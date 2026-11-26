"""Compose and export lyric cards locally with Qt."""

from __future__ import annotations

import math
import re

from PyQt6.QtCore import QDate, QPointF, QRectF, QSaveFile, QSize, Qt, QTimer
from PyQt6.QtGui import (
    QColor, QFontMetrics, QImage, QImageWriter, QLinearGradient, QPainter,
    QPainterPath, QPalette, QPixmap, QTextLayout, QTextOption,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from ..core.lrc_parser import LyricLine
from .lyric_card_style import card_palette, draw_cover, mix, soft_cover


_WIDTH = 540
_MAX_HEIGHT = 1080
_TEMPLATES = (("compact", "紧凑"), ("classic", "经典"), ("immersive", "沉浸"),
              ("depth", "景深"), ("space", "留白"))


class LyricShareDialog(QDialog):
    """Share a selection of lyrics. The playback index is zero-based and progress is 0–1."""

    def __init__(self, *, title: str, artist: str, album: str, cover: QImage,
                 lines: list[LyricLine], current_index: int, progress: float,
                 config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("分享歌词卡片")
        self._title = title.strip() or "未命名歌曲"
        self._artist = artist.strip()
        self._album = album.strip()
        self._cover = QImage(cover) if cover is not None else QImage()
        self._progress = min(1.0, max(0.0, progress)) if math.isfinite(progress) else 0.0
        self._rows = [(i, line.text.strip(), line.translation.strip())
                      for i, line in enumerate(lines) if line.text.strip() or line.translation.strip()]
        self._initial = min(range(len(self._rows)),
                            key=lambda row: abs(self._rows[row][0] - current_index), default=0)
        self._focus_row = -1
        self._preview_image = QImage()
        self._blur_cache: dict[int, QImage] = {}
        self._palette_cache = {}
        self._config = config
        self._settings = self._read_settings()
        self._build_ui()
        self._loaded_options = self._options()
        self._reset_selection()
        self._timer.stop()
        self._refresh()
        self.resize(900, 650)

    def _read_settings(self) -> dict:
        return self._config.get_lyric_share_options()

    def _build_ui(self):
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        self._preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self._preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._preview.setMinimumSize(230, 300)
        self._preview.setAutoFillBackground(True)
        self._preview.setBackgroundRole(QPalette.ColorRole.AlternateBase)
        self._preview.setAccessibleName("歌词卡片预览")
        body.addWidget(self._preview, 3)

        panel = QWidget()
        panel.setMinimumWidth(300)
        controls = QVBoxLayout(panel)
        controls.setContentsMargins(8, 0, 0, 0)
        body.addWidget(panel, 2)
        controls.addWidget(QLabel("勾选要分享的歌词，可设置一条重点句。"))
        self._list = QListWidget()
        self._list.setAccessibleName("歌词选段")
        for source_index, text, translation in self._rows:
            label = " ".join((text or translation).split())
            item = QListWidgetItem(f"{source_index + 1:02d}  {label}")
            item.setToolTip("\n".join(filter(None, (text, translation))))
            item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)
        controls.addWidget(self._list, 1)
        select_tools = QHBoxLayout()
        controls.addLayout(select_tools)
        for name, action in (("恢复选段", self._reset_selection), ("清空", self._clear_selection),
                             ("设为／取消重点", self._toggle_focus)):
            button = QPushButton(name)
            button.clicked.connect(action)
            select_tools.addWidget(button)
        self._list.itemDoubleClicked.connect(self._toggle_focus)

        form = QFormLayout()
        controls.addLayout(form)
        self._template = self._choice(form, "模板", "template", _TEMPLATES)
        self._style = self._choice(form, "配色", "style", (("faithful", "忠实"), ("soft", "柔和"),
                                                        ("vivid", "鲜明"), ("mono", "单色")))
        self._tone = self._choice(form, "明暗", "tone", (("auto", "自动"), ("light", "浅色"), ("dark", "深色")))
        self._blur = self._choice(form, "模糊", "blur", ((2, "适中"), (1, "轻"), (3, "重")))
        self._crop = QSlider(Qt.Orientation.Horizontal)
        self._crop.setRange(0, 100)
        position = self._settings.get("crop", 50)
        self._crop.setValue(position if type(position) is int else 50)
        self._crop.valueChanged.connect(self._queue_preview)
        form.addRow("横向取景", self._crop)
        self._form = form
        self._width = self._choice(form, "输出宽度", "width", ((1080, "1080 px"), (2160, "2160 px")))

        extras = QHBoxLayout()
        controls.addLayout(extras)
        self._translation = self._check(extras, "译文", "translation", True)
        self._translation.setEnabled(any(translation for _, _, translation in self._rows))
        self._show_album = self._check(extras, "专辑", "album")
        self._show_album.setEnabled(bool(self._album))
        self._date = self._check(extras, "日期", "date")
        self._show_progress = self._check(extras, "进度条", "progress")
        self._caption = QLineEdit(placeholderText="角落小字（可选）")
        self._caption.setAccessibleName("角落小字")
        self._caption.setMaxLength(80)
        caption = self._settings.get("caption", "")
        self._caption.setText(caption if isinstance(caption, str) else "")
        self._caption.textChanged.connect(self._queue_preview)
        controls.addWidget(self._caption)

        actions = QHBoxLayout()
        root.addLayout(actions)
        self._status = QLabel()
        self._status.setWordWrap(True)
        actions.addWidget(self._status, 1)
        self._copy = QPushButton("复制图片")
        self._copy.clicked.connect(self._copy_image)
        actions.addWidget(self._copy)
        self._save = QPushButton("保存 PNG…")
        self._save.clicked.connect(self._save_image)
        actions.addWidget(self._save)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._timer = QTimer(self, singleShot=True, interval=25)
        self._timer.timeout.connect(self._refresh)
        self._list.itemChanged.connect(self._selection_changed)

    def _choice(self, form, label, key, choices):
        combo = QComboBox()
        for value, text in choices:
            combo.addItem(text, value)
        combo.setCurrentIndex(max(0, combo.findData(self._settings.get(key))))
        combo.currentIndexChanged.connect(self._queue_preview)
        form.addRow(label, combo)
        return combo

    def _check(self, layout, label, key, default=False):
        check = QCheckBox(label)
        check.setChecked(self._settings.get(key, default) is True)
        check.toggled.connect(self._queue_preview)
        layout.addWidget(check)
        return check

    def _options(self) -> dict:
        return {
            "template": self._template.currentData(), "style": self._style.currentData(),
            "tone": self._tone.currentData(), "width": self._width.currentData(),
            "blur": self._blur.currentData(), "crop": self._crop.value(),
            "translation": self._translation.isChecked(), "album": self._show_album.isChecked(),
            "date": self._date.isChecked(), "progress": self._show_progress.isChecked(),
            "caption": self._caption.text().strip(),
        }

    def done(self, result):
        options = self._options()
        if options != self._loaded_options:
            try:
                self._config.set_lyric_share_options({**self._read_settings(), **options})
                self._loaded_options = options
            except OSError:
                QMessageBox.warning(self, "分享设置", "无法保存歌词分享设置。")
        super().done(result)

    def _clear_selection(self):
        for row in range(self._list.count()):
            self._list.item(row).setCheckState(Qt.CheckState.Unchecked)
        self._set_focus_row(-1)

    def _reset_selection(self):
        self._clear_selection()
        start = max(0, min(self._initial - 1, len(self._rows) - 4))
        for row in range(start, min(len(self._rows), start + 4)):
            self._list.item(row).setCheckState(Qt.CheckState.Checked)
        if self._rows:
            self._list.setCurrentRow(self._initial)
            self._list.scrollToItem(self._list.item(self._initial))

    def _toggle_focus(self, *_):
        row = self._list.currentRow()
        if row >= 0:
            if row != self._focus_row:
                self._list.item(row).setCheckState(Qt.CheckState.Checked)
                self._set_focus_row(row)
            else:
                self._set_focus_row(-1)

    def _set_focus_row(self, row):
        previous = self._focus_row
        self._focus_row = row
        if previous >= 0:
            old = self._list.item(previous)
            old.setText(old.text().removeprefix("★ "))
        if row >= 0:
            item = self._list.item(row)
            item.setText("★ " + item.text())
        self._queue_preview()

    def _selection_changed(self, item):
        if self._focus_row >= 0 and item is self._list.item(self._focus_row):
            if item.checkState() != Qt.CheckState.Checked:
                self._set_focus_row(-1)
        self._queue_preview()

    def _queue_preview(self, *_):
        self._timer.start()

    def _selected(self):
        return [(text, translation, row == self._focus_row)
                for row, (_, text, translation) in enumerate(self._rows)
                if self._list.item(row).checkState() == Qt.CheckState.Checked]

    def _refresh(self):
        template = self._template.currentData()
        self._form.setRowVisible(self._blur, template == "immersive" and not self._cover.isNull())
        self._form.setRowVisible(self._crop, template == "depth" and not self._cover.isNull())
        selected = self._selected()
        message = ""
        self._preview_image = QImage()
        if not selected:
            message = "请选择要分享的歌词。" if self._rows else "没有可分享的歌词。"
        else:
            self._preview_image = self._render(_WIDTH, selected)
            if self._preview_image.isNull():
                message = "所选歌词太长，请缩短选段。"
        ready = not self._preview_image.isNull()
        self._save.setEnabled(ready)
        self._copy.setEnabled(ready)
        if ready:
            width = self._width.currentData()
            height = self._preview_image.height() * width // _WIDTH
            self._status.setText(f"已选 {len(selected)} 行  ·  {width} × {height} px")
        else:
            self._status.setText(message)
        self._show_preview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_preview()

    def _show_preview(self):
        if self._preview_image.isNull():
            self._preview.setText(self._status.text())
            return
        ratio = self._preview.devicePixelRatioF()
        area = self._preview.contentsRect().size() - QSize(24, 24)
        if area.isEmpty():
            return
        pixmap = QPixmap.fromImage(self._preview_image).scaled(
            area * ratio, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        self._preview.setPixmap(pixmap)

    def _font(self, size, bold=False):
        font = self.font()
        font.setPixelSize(size)
        font.setBold(bold)
        return font

    def _lyric_layout(self, selected, width, available, with_translation):
        wrap = QTextOption()
        wrap.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        for size in (26, 23, 20, 17, 15):
            runs, y = [], 0.0
            for text, translation, focus in selected:
                entries = [(text or translation, self._font(size, focus), "accent" if focus else "text")]
                if text and translation and with_translation:
                    entries.append((translation, self._font(round(size * .71)), "secondary"))
                for value, font, color in entries:
                    for paragraph in value.splitlines():
                        layout = QTextLayout(paragraph, font)
                        layout.setTextOption(wrap)
                        layout.beginLayout()
                        while (line := layout.createLine()).isValid():
                            line.setLineWidth(width)
                            line.setPosition(QPointF(0, y))
                            y += line.height() + 3
                            if y > available:
                                break
                        layout.endLayout()
                        runs.append((layout, color))
                        if y > available:
                            break
                    if y > available:
                        break
                if y > available:
                    break
                y += size * .53
            if y <= available:
                return runs, math.ceil(y)
        return None, 0

    def _render(self, width, selected):
        options = self._options()
        template = options["template"]
        art = not self._cover.isNull()
        margin = 50 if template == "space" else 40
        lyric_width = _WIDTH - 2 * margin - (72 if template == "space" else 0)
        lyric_y = {"compact": 160, "classic": 365 if art else 175,
                   "immersive": 345 if art else 175, "depth": 342 if art else 175,
                   "space": 170}[template]
        note = "   ·   ".join(filter(None, (QDate.currentDate().toString("yyyy.MM.dd") if options["date"] else "",
                                             options["caption"])))
        footer = 42 + (36 if note else 0) + (28 if options["progress"] else 0)
        runs, extent = self._lyric_layout(selected, lyric_width, _MAX_HEIGHT - lyric_y - footer,
                                         options["translation"])
        if runs is None:
            return QImage()
        minimum = {"compact": 485, "classic": 620 if art else 500, "immersive": 600,
                   "depth": 616, "space": 570}[template]
        height = max(minimum, lyric_y + extent + footer)
        scale = width / _WIDTH
        image = QImage(width, round(height * scale), QImage.Format.Format_RGB32)
        if image.isNull():
            return image
        key = options["style"], options["tone"]
        if key not in self._palette_cache:
            self._palette_cache[key] = card_palette(
                self._cover, self.palette().color(QPalette.ColorRole.Highlight), style=key[0], tone=key[1])
        palette = self._palette_cache[key]
        image.fill(palette.background)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                               | QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(scale, scale)
        try:
            if art and template in ("immersive", "depth"):
                strength = options["blur"] if template == "immersive" else 3
                if strength not in self._blur_cache:
                    self._blur_cache[strength] = soft_cover(self._cover, strength)
                frame = QRectF(0, 0, _WIDTH, height)
                focus = options["crop"] / 100 if template == "depth" else .5
                draw_cover(painter, frame, self._blur_cache[strength], focus)
                if template == "depth":
                    painter.save()
                    painter.setClipRect(QRectF(0, 0, _WIDTH, 285))
                    draw_cover(painter, frame, self._cover, focus)
                    painter.restore()
                gradient = QLinearGradient(0, 0, 0, lyric_y - 108)
                tint = QColor(palette.background)
                tint.setAlpha(80 if template == "immersive" else 0)
                gradient.setColorAt(0, tint)
                gradient.setColorAt(.46, tint)
                scrim = QColor(palette.background)
                scrim.setAlpha(247)
                gradient.setColorAt(1, scrim)
                painter.fillRect(frame, gradient)

            self._draw_header(painter, template, margin, lyric_y, palette, options["album"])
            painter.fillRect(QRectF(margin, lyric_y - 19, 31, 3), palette.accent)
            for layout, color in runs:
                painter.setPen(getattr(palette, color))
                layout.draw(painter, QPointF(margin, lyric_y))

            if note:
                self._draw_label(painter, note, margin, height - (68 if options["progress"] else 53),
                                 _WIDTH - 2 * margin, 14, palette.secondary)
            if options["progress"]:
                track = mix(palette.background, palette.secondary, .25)
                rect = QRectF(margin, height - 29, _WIDTH - 2 * margin, 3)
                painter.fillRect(rect, track)
                rect.setWidth(rect.width() * self._progress)
                painter.fillRect(rect, palette.accent)
        finally:
            painter.end()
        return image

    def _draw_header(self, painter, template, margin, lyric_y, palette, show_album):
        art = not self._cover.isNull()
        details = "  /  ".join(filter(None, (self._artist, self._album if show_album else "")))
        width = _WIDTH - 2 * margin
        title_x, title_y = margin, lyric_y - 105
        if template == "space":
            if art:
                self._draw_thumbnail(painter, QRectF(margin, 42, 62, 62))
            title_y = 91 if art else 73
            self._draw_vertical_title(painter, palette.text)
            self._draw_label(painter, details, margin + (80 if art else 0), title_y,
                             width - (150 if art else 75), 16, palette.secondary)
            return
        if art and template == "compact":
            self._draw_thumbnail(painter, QRectF(margin, 38, 78, 78))
            title_x += 96
            width -= 96
            title_y = 49
        elif art and template in ("classic", "immersive"):
            if template == "classic":
                rect = QRectF((_WIDTH - 224) / 2, 29, 224, 224)
            else:
                fit = self._cover.size().scaled(QSize(width, 201), Qt.AspectRatioMode.KeepAspectRatio)
                rect = QRectF((_WIDTH - fit.width()) / 2, 29, fit.width(), fit.height())
            self._draw_thumbnail(painter, rect)
        centered = template in ("classic", "immersive")
        self._draw_label(painter, self._title, title_x, title_y, width, 29, palette.text, True, centered)
        self._draw_label(painter, details, title_x, title_y + 38, width, 16, palette.secondary, center=centered)

    def _draw_thumbnail(self, painter, rect):
        painter.save()
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setClipPath(path)
        draw_cover(painter, rect, self._cover)
        painter.restore()

    def _draw_vertical_title(self, painter, color):
        axis = _WIDTH - 66
        if any("\u3000" <= char <= "\u9fff" for char in self._title):
            chars = list(self._title[:12])
            if len(self._title) > 12:
                chars[-1] = "…"
            for index, char in enumerate(chars):
                self._draw_label(painter, char, axis - 19, 50 + index * 31, 38, 24, color, True, True)
        else:
            painter.save()
            painter.translate(axis + 15, 49)
            painter.rotate(90)
            self._draw_label(painter, self._title, 0, 0, 374, 24, color, True)
            painter.restore()

    def _draw_label(self, painter, text, x, y, width, size, color, bold=False, center=False):
        painter.setFont(self._font(size, bold))
        painter.setPen(color)
        metrics = QFontMetrics(painter.font())
        elided = metrics.elidedText(" ".join(text.split()), Qt.TextElideMode.ElideRight, int(width))
        align = Qt.AlignmentFlag.AlignHCenter if center else Qt.AlignmentFlag.AlignLeft
        painter.drawText(QRectF(x, y, width, metrics.height() + 2), int(align), elided)

    def _export_image(self):
        if self._timer.isActive():
            self._timer.stop()
            self._refresh()
        if self._preview_image.isNull():
            return QImage()
        image = self._render(self._width.currentData(), self._selected())
        if image.isNull():
            self._status.setText("无法生成图片。")
        return image

    def _copy_image(self):
        image = self._export_image()
        if not image.isNull():
            QApplication.clipboard().setImage(image)
            self._status.setText("已复制图片。")

    def _save_image(self):
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self._title)[:100].rstrip(". ")
        path, _ = QFileDialog.getSaveFileName(self, "保存歌词分享卡片", f"{name or '歌词'}-分享.png", "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        image = self._export_image()
        if image.isNull():
            return
        output = QSaveFile(path)
        if output.open(QSaveFile.OpenModeFlag.WriteOnly):
            writer = QImageWriter(output, b"png")
            if writer.write(image) and output.commit():
                self._status.setText("已保存 PNG。")
                return
            output.cancelWriting()
        QMessageBox.warning(self, "无法保存图片", "请确认目标位置可写且有足够的可用空间。")
