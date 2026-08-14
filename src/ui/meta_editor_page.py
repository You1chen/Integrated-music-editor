"""Meta information editor page — edit audio file metadata (ID3 / Vorbis tags).

Completely independent from the LRC lyrics editor.  Reads and writes tags
directly in the loaded audio file using :mod:`mutagen`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import QRect, Qt, QUrl
from PyQt6.QtGui import (
    QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import mutagen
from mutagen.id3 import (
    APIC, COMM, ID3, TALB, TCOM, TCON, TDRC, TEXT, TIT2, TPE1, TPE2, TYER,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Field definitions ───────────────────────────────────────────

# (field_key, label, placeholder)
# Standard LRC-compatible fields that overlap with ID3
_TEXT_FIELDS: list[tuple[str, str, str]] = [
    ("title",       "歌名",       "歌曲名称"),
    ("artist",      "歌手",       "演唱者"),
    ("album",       "专辑",       "所属专辑"),
    ("albumartist", "专辑歌手",   "专辑的表演者"),
    ("lyricist",    "词作者",     "作词人"),
    ("composer",    "作曲者",     "作曲人"),
    ("year",        "年份",       "发行年份，如 2024"),
    ("genre",       "流派",       "音乐风格"),
    ("comment",     "备注",       "附加说明"),
]


# ── Crop modes ──────────────────────────────────────────────────

CROP_RECT = "rect"
CROP_SQUARE = "square"
CROP_CIRCLE = "circle"


class CoverCropDialog(QDialog):
    """Dialog for cropping a cover image before embedding.

    Supports three modes:
    - **矩形** (rect):  free-form rectangle
    - **方形** (square): constrained 1∶1 square
    - **圆形** (circle): square crop with a circular alpha mask
    """

    def __init__(
        self, image_path: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("裁剪封面图片")
        self.setMinimumSize(520, 440)
        self.resize(600, 520)

        self._image = QImage(image_path)
        if self._image.isNull():
            QMessageBox.warning(self, "错误", "无法加载图片文件")
            self.reject()
            return

        self._crop_mode = CROP_RECT
        self._result_data: bytes | None = None
        self._result_mime = "image/png"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Crop preview ──
        self._preview = _CropPreview(self._image)
        self._preview.setMinimumHeight(260)
        layout.addWidget(self._preview, stretch=1)

        # ── Mode selector ──
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(16)

        self._mode_group = QButtonGroup(self)

        self._rect_radio = QRadioButton("矩形")
        self._square_radio = QRadioButton("方形")
        self._circle_radio = QRadioButton("圆形")

        self._mode_group.addButton(self._rect_radio, 0)
        self._mode_group.addButton(self._square_radio, 1)
        self._mode_group.addButton(self._circle_radio, 2)

        self._rect_radio.setChecked(True)
        self._mode_group.idClicked.connect(self._on_mode_changed)

        mode_layout.addWidget(QLabel("裁剪模式："))
        mode_layout.addWidget(self._rect_radio)
        mode_layout.addWidget(self._square_radio)
        mode_layout.addWidget(self._circle_radio)
        mode_layout.addStretch()

        layout.addLayout(mode_layout)

        # ── Buttons ──
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_mode_changed(self, idx: int) -> None:
        modes = {0: CROP_RECT, 1: CROP_SQUARE, 2: CROP_CIRCLE}
        self._crop_mode = modes.get(idx, CROP_RECT)
        self._preview.set_crop_mode(self._crop_mode)

    def _on_accept(self) -> None:
        """Crop the image according to the current selection and mode."""
        import tempfile

        crop = self._preview.get_image_crop_rect()
        if crop is None:
            self.accept()
            return

        x, y, w, h = crop
        if w <= 0 or h <= 0:
            self.accept()
            return

        if self._crop_mode in (CROP_SQUARE, CROP_CIRCLE):
            # Force square: take the shorter side, center the crop
            size = min(w, h)
            cx = x + (w - size) // 2
            cy = y + (h - size) // 2
            cropped = self._image.copy(cx, cy, size, size)
        else:
            cropped = self._image.copy(x, y, w, h)

        if self._crop_mode == CROP_CIRCLE:
            # Apply circular alpha mask
            s = cropped.width()
            result = QImage(s, s, QImage.Format.Format_ARGB32)
            result.fill(Qt.GlobalColor.transparent)

            painter = QPainter(result)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, s, s)
            painter.setClipPath(path)
            painter.drawImage(0, 0, cropped)
            painter.end()

            cropped = result

        # Save as PNG bytes via temp file (supports transparency)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            cropped.save(tmp.name, "PNG")
            with open(tmp.name, "rb") as f:
                self._result_data = f.read()
            self._result_mime = "image/png"
        finally:
            os.unlink(tmp.name)
        self.accept()

    def get_result(self) -> tuple[bytes, str]:
        """Return (image_bytes, mime_type) after the dialog is accepted."""
        return self._result_data or b"", self._result_mime


class _CropPreview(QWidget):
    """Interactive image crop preview widget.

    Displays a scaled image and lets the user drag to define a crop region.
    Supports rectangle, square (1∶1 constrained), and circle modes.
    """

    _OVERLAY = QColor(0, 0, 0, 140)       # semi-transparent mask
    _BORDER = QColor(255, 255, 255, 220)   # crop border
    _HANDLE_COLOR = QColor(255, 255, 255)

    def __init__(self, image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = image
        self._crop_mode = CROP_RECT
        self._crop_rect: QRect | None = None   # in widget coords
        self._dragging = False
        self._drag_mode = ""                   # "draw" | "move" | "resize"
        self._drag_corner = ""                 # "tl" | "tr" | "bl" | "br"
        self._drag_anchor: QRect | None = None
        self._drag_origin: None = None  # QPoint at drag start
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_crop_mode(self, mode: str) -> None:
        self._crop_mode = mode
        self.update()

    def get_image_crop_rect(self) -> tuple[int, int, int, int] | None:
        """Convert widget-space crop rect to image-space (x, y, w, h)."""
        if self._crop_rect is None:
            return None
        img_rect = self._image_display_rect()
        if img_rect.width() <= 0 or img_rect.height() <= 0:
            return None
        sx = self._image.width() / img_rect.width()
        sy = self._image.height() / img_rect.height()
        r = self._crop_rect
        return (
            int((r.x() - img_rect.x()) * sx),
            int((r.y() - img_rect.y()) * sy),
            int(r.width() * sx),
            int(r.height() * sy),
        )

    # ── geometry ─────────────────────────────────────────────

    def _image_display_rect(self) -> QRect:
        """Return the rectangle where the image is drawn (centered, keeping AR)."""
        if self._image.isNull():
            return QRect()
        iw, ih = self._image.width(), self._image.height()
        ww, wh = self.width(), self.height()
        if ww <= 0 or wh <= 0:
            return QRect()
        scale = min(ww / iw, wh / ih)
        dw, dh = int(iw * scale), int(ih * scale)
        dx = (ww - dw) // 2
        dy = (wh - dh) // 2
        return QRect(dx, dy, dw, dh)

    # ── paint ───────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Draw image centered
        img_rect = self._image_display_rect()
        if img_rect.isValid():
            painter.drawImage(img_rect, self._image)

        if self._crop_rect is not None and self._crop_rect.isValid():
            r = self._crop_rect.normalized()

            # 2. Dark overlay — four rectangles around the crop
            painter.setBrush(self._OVERLAY)
            painter.setPen(Qt.PenStyle.NoPen)
            # top
            painter.drawRect(0, 0, self.width(), r.top())
            # bottom
            painter.drawRect(
                0, r.bottom() + 1, self.width(),
                self.height() - r.bottom() - 1,
            )
            # left
            painter.drawRect(0, r.top(), r.left(), r.height())
            # right
            painter.drawRect(
                r.right() + 1, r.top(),
                self.width() - r.right() - 1, r.height(),
            )

            # 3. Crop border
            pen = QPen(self._BORDER, 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            if self._crop_mode == CROP_CIRCLE:
                painter.drawEllipse(r)
            else:
                painter.drawRect(r)

            # 4. Corner handles
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._HANDLE_COLOR)
            hs = 7
            corners = [
                (r.left(), r.top()), (r.right(), r.top()),
                (r.left(), r.bottom()), (r.right(), r.bottom()),
            ]
            for cx, cy in corners:
                painter.drawRect(cx - hs // 2, cy - hs // 2, hs, hs)

    # ── mouse ────────────────────────────────────────────────

    _HANDLE_HIT = 10   # corner hit-test radius

    def _hit_corner(self, pos, r: QRect) -> str:
        """Return corner name ('tl','tr','bl','br') if *pos* is near one."""
        rn = r.normalized()
        for name, (cx, cy) in [
            ("tl", (rn.left(), rn.top())),
            ("tr", (rn.right(), rn.top())),
            ("bl", (rn.left(), rn.bottom())),
            ("br", (rn.right(), rn.bottom())),
        ]:
            if abs(pos.x() - cx) <= self._HANDLE_HIT and abs(pos.y() - cy) <= self._HANDLE_HIT:
                return name
        return ""

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()

        if self._crop_rect is not None and self._crop_rect.isValid():
            corner = self._hit_corner(pos, self._crop_rect)
            if corner:
                # Resize from corner
                self._drag_mode = "resize"
                self._drag_corner = corner
                self._drag_anchor = QRect(self._crop_rect)
                self._drag_origin = pos
                self._dragging = True
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                return
            if self._crop_rect.normalized().contains(pos):
                # Move the existing selection
                self._drag_mode = "move"
                self._drag_anchor = QRect(self._crop_rect)
                self._drag_origin = pos
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                return

        # Draw new selection
        self._drag_mode = "draw"
        self._crop_rect = QRect(pos, pos)
        self._dragging = True
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()

        if not self._dragging:
            # Update cursor based on position
            if self._crop_rect is not None and self._crop_rect.isValid():
                if self._hit_corner(pos, self._crop_rect):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif self._crop_rect.normalized().contains(pos):
                    self.setCursor(Qt.CursorShape.OpenHandCursor)
                else:
                    self.setCursor(Qt.CursorShape.CrossCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return

        img_rect = self._image_display_rect()

        if self._drag_mode == "draw":
            start = self._crop_rect.topLeft()
            end = pos
            end.setX(max(img_rect.left(), min(img_rect.right(), end.x())))
            end.setY(max(img_rect.top(), min(img_rect.bottom(), end.y())))
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            if self._crop_mode in (CROP_SQUARE, CROP_CIRCLE):
                size = min(abs(dx), abs(dy))
                dx = size if dx >= 0 else -size
                dy = size if dy >= 0 else -size
            self._crop_rect = QRect(start.x(), start.y(), dx, dy)

        elif self._drag_mode == "move":
            delta = pos - self._drag_origin
            r = self._drag_anchor.normalized()
            new_x = r.x() + delta.x()
            new_y = r.y() + delta.y()
            # Clamp to image bounds
            new_x = max(img_rect.left(), min(img_rect.right() - r.width(), new_x))
            new_y = max(img_rect.top(), min(img_rect.bottom() - r.height(), new_y))
            self._crop_rect = QRect(new_x, new_y, r.width(), r.height())

        elif self._drag_mode == "resize":
            r = self._drag_anchor.normalized()
            delta = pos - self._drag_origin
            corner = self._drag_corner

            new_rect = QRect(r)

            if "l" in corner:
                new_rect.setLeft(
                    max(img_rect.left(),
                        min(r.right() - 10, r.left() + delta.x()))
                )
            else:
                new_rect.setRight(
                    min(img_rect.right(),
                        max(r.left() + 10, r.right() + delta.x()))
                )

            if "t" in corner:
                new_rect.setTop(
                    max(img_rect.top(),
                        min(r.bottom() - 10, r.top() + delta.y()))
                )
            else:
                new_rect.setBottom(
                    min(img_rect.bottom(),
                        max(r.top() + 10, r.bottom() + delta.y()))
                )

            # Constrain to square / circle
            if self._crop_mode in (CROP_SQUARE, CROP_CIRCLE):
                rn = new_rect.normalized()
                size = min(rn.width(), rn.height())
                if "l" in corner:
                    new_rect.setLeft(new_rect.right() - size)
                else:
                    new_rect.setRight(new_rect.left() + size)
                if "t" in corner:
                    new_rect.setTop(new_rect.bottom() - size)
                else:
                    new_rect.setBottom(new_rect.top() + size)

            self._crop_rect = new_rect

        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = False
        self._drag_mode = ""
        pos = event.position().toPoint()
        if self._crop_rect is not None:
            r = self._crop_rect.normalized()
            if r.width() < 10 or r.height() < 10:
                self._crop_rect = None
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._hit_corner(pos, self._crop_rect):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif r.contains(pos):
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()


class MetaEditorPage(QScrollArea):
    """Scrollable audio-metadata editor page.

    Reads existing ID3 / Vorbis tags from the currently-loaded audio file
    and lets the user edit them.  All changes are written back to the
    audio file when the user clicks **保存到音频文件**.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(12)

        # ── Prompt when no audio is loaded ──────────────────
        self._no_audio_label = QLabel(
            "请先载入音频文件\n\n"
            "在「歌单」页点击歌曲，或把音频文件直接拖到窗口底部，\n"
            "即可在此编辑歌曲的元信息。"
        )
        self._no_audio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_audio_label.setWordWrap(True)
        self._no_audio_label.setStyleSheet(
            "font-size: 15px; color: palette(mid); padding: 40px;"
        )
        layout.addWidget(self._no_audio_label)

        # ── Editor widget (hidden until audio is loaded) ────
        self._editor = QWidget()
        editor_layout = QVBoxLayout(self._editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(12)

        # ── Main left-right split ──
        main_split = QHBoxLayout()
        main_split.setSpacing(12)

        # ==== Left side: cover + save button ====
        left_layout = QVBoxLayout()
        left_layout.setSpacing(12)

        # ---- Cover art ----
        cover_group = QGroupBox("封面图片")
        cover_vlayout = QVBoxLayout(cover_group)
        cover_vlayout.setSpacing(6)

        picsize = 180

        self._cover_thumbnail = QPushButton("无封面")
        self._cover_thumbnail.setFixedSize(picsize, picsize)
        self._cover_thumbnail.setIconSize(self._cover_thumbnail.size())
        self._cover_thumbnail.setFlat(True)
        self._cover_thumbnail.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cover_thumbnail.setStyleSheet(
            "QPushButton {"
            "  border: none; border-radius: 4px;"
            "  background: transparent; font-size: 12px;"
            "}"
            "QPushButton:hover {"
            "  border: 2px solid palette(highlight);"
            "}"
        )
        self._cover_thumbnail.clicked.connect(self._on_browse_cover)
        cover_vlayout.addWidget(self._cover_thumbnail)

        self._cover_info = QLabel("")
        self._cover_info.setStyleSheet("font-size: 11px; color: gray;")
        self._cover_info.setWordWrap(True)
        self._cover_info.setMaximumWidth(picsize)
        cover_vlayout.addWidget(self._cover_info)

        browse_cover_btn = QPushButton("浏览...")
        browse_cover_btn.clicked.connect(self._on_browse_cover)
        cover_vlayout.addWidget(browse_cover_btn)

        clear_cover_btn = QPushButton("清除")
        clear_cover_btn.clicked.connect(self._on_clear_cover)
        cover_vlayout.addWidget(clear_cover_btn)

        left_layout.addWidget(cover_group)

        # ---- Save button (unnamed container) ----
        save_container = QWidget()
        save_vlayout = QVBoxLayout(save_container)
        save_vlayout.setContentsMargins(0, 0, 0, 0)
        self._save_btn = QPushButton("保存到音频文件")
        self._save_btn.setStyleSheet(
            "font-size: 15px; font-weight: bold; padding: 8px 24px;"
        )
        self._save_btn.clicked.connect(self._on_save)
        save_vlayout.addWidget(self._save_btn)
        left_layout.addWidget(save_container)

        left_layout.addStretch()
        main_split.addLayout(left_layout)

        # ==== Right side: text fields ====
        right_layout = QVBoxLayout()
        right_layout.setSpacing(12)

        # ---- Standard text fields ----
        text_group = QGroupBox("基本信息")
        text_form = QFormLayout(text_group)
        text_form.setSpacing(6)

        # ── Filename editor ──
        self._filename_input = QLineEdit()
        self._filename_input.setPlaceholderText("重命名音频文件（不含扩展名）")
        text_form.addRow("文件名:", self._filename_input)

        self._inputs: dict[str, QLineEdit] = {}
        for key, label, placeholder in _TEXT_FIELDS:
            inp = QLineEdit()
            inp.setPlaceholderText(placeholder)
            text_form.addRow(f"{label}:", inp)
            self._inputs[key] = inp

        right_layout.addWidget(text_group)
        right_layout.addStretch()
        main_split.addLayout(right_layout, stretch=1)

        editor_layout.addLayout(main_split)
        editor_layout.addStretch()

        layout.addWidget(self._editor)

        # ── Track audio changes ─────────────────────────────
        self._mw.audio_manager.state_changed.connect(self._on_audio_state_changed)
        self._last_audio_path: str = ""

        # Initial refresh
        self._refresh()

    # ── Visibility ──────────────────────────────────────────────

    def showEvent(self, event) -> None:
        """Refresh form when the page becomes visible."""
        super().showEvent(event)
        self._refresh()

    # ── Core logic ──────────────────────────────────────────────

    def _refresh(self) -> None:
        """Re-read audio tags and populate the form."""
        path = self._mw.audio_manager.local_path
        if not path or not os.path.isfile(path):
            self._editor.hide()
            self._no_audio_label.show()
            self._last_audio_path = ""
            return

        self._editor.show()
        self._no_audio_label.hide()

        # Only reload if the audio file changed
        if path == self._last_audio_path:
            return
        self._last_audio_path = path

        # ── Populate filename (stem only, no extension) ──
        stem, _ = os.path.splitext(os.path.basename(path))
        self._filename_input.setText(stem)

        self._load_from_audio(path)

    def _load_from_audio(self, path: str) -> None:
        """Read metadata from *path* using mutagen and fill the form."""
        try:
            audio = mutagen.File(path)
        except Exception:
            print("无法读取音频文件元信息")
            return

        if audio is None:
            # mutagen doesn't know this format
            self._clear_form()
            return

        tags = getattr(audio, "tags", None)

        # ── ID3 (MP3) ──────────────────────────────────────
        if isinstance(tags, ID3):
            self._inputs["title"].setText(
                _id3_text(tags, TIT2)
            )
            self._inputs["artist"].setText(
                _id3_text(tags, TPE1)
            )
            self._inputs["album"].setText(
                _id3_text(tags, TALB)
            )
            self._inputs["albumartist"].setText(
                _id3_text(tags, TPE2)
            )
            self._inputs["lyricist"].setText(
                _id3_text(tags, TEXT)
            )
            self._inputs["composer"].setText(
                _id3_text(tags, TCOM)
            )
            self._inputs["year"].setText(
                _id3_text(tags, TDRC) or _id3_text(tags, TYER)
            )
            self._inputs["genre"].setText(
                _id3_text(tags, TCON)
            )
            self._inputs["comment"].setText(
                _id3_comment(tags)
            )

            # Cover art
            apic = tags.getall("APIC")
            if apic:
                self._cover_data = apic[0].data
                self._cover_mime = apic[0].mime
                self._show_cover_from_data(self._cover_data)
                desc = apic[0].desc or ""
                info = f"{apic[0].mime}"
                if desc:
                    info += f"\n{desc}"
                self._cover_info.setText(info)
            else:
                self._cover_data = None
                self._cover_mime = ""
                self._cover_thumbnail.setIcon(QIcon())
                self._cover_thumbnail.setText("无封面")
                self._cover_info.setText("")

        # ── VorbisComments (FLAC / Ogg) ────────────────────
        elif tags is not None:
            self._inputs["title"].setText(
                _vc_text(tags, "title")
            )
            self._inputs["artist"].setText(
                _vc_text(tags, "artist")
            )
            self._inputs["album"].setText(
                _vc_text(tags, "album")
            )
            self._inputs["albumartist"].setText(
                _vc_text(tags, "albumartist")
            )
            self._inputs["lyricist"].setText(
                _vc_text(tags, "lyricist")
            )
            self._inputs["composer"].setText(
                _vc_text(tags, "composer")
            )
            self._inputs["year"].setText(
                _vc_text(tags, "date")
            )
            self._inputs["genre"].setText(
                _vc_text(tags, "genre")
            )
            self._inputs["comment"].setText(
                _vc_text(tags, "comment") or _vc_text(tags, "description")
            )

            # Cover art (FLAC pictures)
            pics = getattr(audio, "pictures", None)
            if pics:
                self._cover_data = pics[0].data
                self._cover_mime = pics[0].mime
                self._show_cover_from_data(self._cover_data)
                desc = pics[0].desc or ""
                info = f"{pics[0].mime}  {pics[0].type}"
                if desc:
                    info += f"\n{desc}"
                self._cover_info.setText(info)
            else:
                self._cover_data = None
                self._cover_mime = ""
                self._cover_thumbnail.setIcon(QIcon())
                self._cover_thumbnail.setText("无封面")
                self._cover_info.setText("")

        else:
            self._clear_form()

    def _save_to_audio(self, path: str) -> None:
        """Write metadata back to *path* using mutagen."""
        try:
            audio = mutagen.File(path)
        except Exception:
            print("无法写入音频文件")
            return

        if audio is None:
            QMessageBox.warning(self, "错误", "不支持的音频格式，无法写入元信息")
            return

        # Ensure tags exist (files without any existing tags)
        tags = getattr(audio, "tags", None)
        if tags is None:
            try:
                audio.add_tags()
                tags = audio.tags
            except Exception:
                QMessageBox.warning(self, "错误", "无法为此文件创建标签")
                return

        # ── ID3 (MP3) ──────────────────────────────────────
        if isinstance(tags, ID3):
            _set_id3_text(tags, TIT2, self._inputs["title"].text())
            _set_id3_text(tags, TPE1, self._inputs["artist"].text())
            _set_id3_text(tags, TALB, self._inputs["album"].text())
            _set_id3_text(tags, TPE2, self._inputs["albumartist"].text())
            _set_id3_text(tags, TEXT, self._inputs["lyricist"].text())
            _set_id3_text(tags, TCOM, self._inputs["composer"].text())

            year = self._inputs["year"].text().strip()
            tags.delall("TDRC")
            tags.delall("TYER")
            if year:
                try:
                    tags.add(TDRC(encoding=3, text=year))
                except Exception:
                    tags.add(TYER(encoding=3, text=year))

            _set_id3_text(tags, TCON, self._inputs["genre"].text())

            comment = self._inputs["comment"].text().strip()
            tags.delall("COMM")
            if comment:
                tags.add(
                    COMM(encoding=3, lang="zho", desc="", text=comment)
                )

            # Cover art
            tags.delall("APIC")
            if self._cover_data:
                tags.add(
                    APIC(
                        encoding=3,
                        mime=self._cover_mime,
                        type=3,  # Cover (front)
                        desc="cover",
                        data=self._cover_data,
                    )
                )

            audio.save()
            print("元信息已保存到音频文件")
            return

        # ── VorbisComments (FLAC / Ogg) ────────────────────
        if hasattr(tags, "get"):
            _set_vc_text(tags, "title", self._inputs["title"].text())
            _set_vc_text(tags, "artist", self._inputs["artist"].text())
            _set_vc_text(tags, "album", self._inputs["album"].text())
            _set_vc_text(tags, "albumartist", self._inputs["albumartist"].text())
            _set_vc_text(tags, "lyricist", self._inputs["lyricist"].text())
            _set_vc_text(tags, "composer", self._inputs["composer"].text())
            _set_vc_text(tags, "date", self._inputs["year"].text())
            _set_vc_text(tags, "genre", self._inputs["genre"].text())
            _set_vc_text(tags, "comment", self._inputs["comment"].text())

            # Cover art for FLAC
            if hasattr(audio, "clear_pictures") and hasattr(audio, "add_picture"):
                audio.clear_pictures()
                if self._cover_data:
                    from mutagen.flac import Picture
                    pic = Picture()
                    pic.type = 3  # Cover (front)
                    pic.mime = self._cover_mime
                    pic.desc = "cover"
                    pic.data = self._cover_data
                    audio.add_picture(pic)

            audio.save()
            print("元信息已保存到音频文件")
            return

        QMessageBox.warning(self, "错误", "不支持的音频格式，无法写入元信息")

    def _clear_form(self) -> None:
        """Reset all inputs to empty."""
        for inp in self._inputs.values():
            inp.clear()
        self._cover_thumbnail.setIcon(QIcon())
        self._cover_thumbnail.setText("无封面")
        self._cover_info.setText("")
        self._cover_data = None
        self._cover_mime = ""

    # ── Cover art helpers ────────────────────────────────────────

    def _set_cover_button_icon(self, data: bytes | None) -> None:
        """Update the cover thumbnail button with the given image data."""
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                # Square → stretch to fill; rectangle → fit keeping ratio
                mode = (
                    Qt.AspectRatioMode.IgnoreAspectRatio
                    if pixmap.width() == pixmap.height()
                    else Qt.AspectRatioMode.KeepAspectRatio
                )
                pixmap = pixmap.scaled(
                    140, 140,
                    mode,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._cover_thumbnail.setIcon(QIcon(pixmap))
                self._cover_thumbnail.setText("")
                return
        self._cover_thumbnail.setIcon(QIcon())
        self._cover_thumbnail.setText("无封面")

    def _show_cover_from_data(self, data: bytes) -> None:
        """Display cover art from raw image bytes."""
        self._set_cover_button_icon(data)

    def _on_browse_cover(self) -> None:
        """Browse for a cover art image file, then open crop dialog."""
        default_dir = self._mw.config.get_default_cover_browse_dir()
        if not default_dir or not os.path.exists(default_dir):
            default_dir = self._mw.config.get_default_browse_dir()

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择封面图片",
            default_dir,
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.gif *.webp);;所有文件 (*)",
        )
        if not file_path:
            return

        # Open crop dialog
        crop_dialog = CoverCropDialog(file_path, self)
        if crop_dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._cover_data, self._cover_mime = crop_dialog.get_result()
        if not self._cover_data:
            QMessageBox.warning(self, "错误", "裁剪图片失败")
            return

        self._show_cover_from_data(self._cover_data)
        size_kb = len(self._cover_data) // 1024
        self._cover_info.setText(
            f"{os.path.basename(file_path)}\n{size_kb} KB\n(已裁剪)"
        )

    def _on_clear_cover(self) -> None:
        """Remove the cover art."""
        self._cover_data = None
        self._cover_mime = ""
        self._cover_thumbnail.setIcon(QIcon())
        self._cover_thumbnail.setText("无封面")
        self._cover_info.setText("")

    # ── Slots ────────────────────────────────────────────────────

    def _on_audio_state_changed(self, data) -> None:
        """Refresh when audio source changes (e.g. new file loaded)."""
        # The DURATION_LOADED state indicates a new file is ready
        from ..core.audio_manager import AudioState
        if data.type == AudioState.DURATION_LOADED:
            self._last_audio_path = ""  # force re-read
            self._refresh()

    def _notify_playlist(self, old_path: str, new_path: str) -> None:
        """Tell the playlist page to refresh a song's metadata."""
        try:
            from ..core.constants import PageRoute
            playlist_page = self._mw.content_stack._pages.get(PageRoute.PLAYLIST)
            if playlist_page is not None and hasattr(playlist_page, "refresh_song"):
                playlist_page.refresh_song(old_path, new_path)
        except Exception:
            pass  # Best-effort, don't break save on playlist errors

    def _on_save(self) -> None:
        """Write all current form values to the audio file,
        and rename the file if the filename was changed."""
        path = self._mw.audio_manager.local_path
        if not path:
            print("请先载入音频文件")
            return

        # ── Validate filename before saving ──
        new_stem = self._filename_input.text().strip()
        if not new_stem:
            print("文件名不能为空")
            return

        # ── 1. Save metadata tags ──
        self._save_to_audio(path)

        # ── 2. Rename file if filename changed ──
        old_dir = os.path.dirname(path)
        _stem, ext = os.path.splitext(os.path.basename(path))
        if new_stem == _stem:
            # Tags saved, no rename — still refresh playlist
            self._notify_playlist(path, "")
            return

        new_path = os.path.join(old_dir, new_stem + ext)
        if os.path.normpath(new_path) == os.path.normpath(path):
            return  # same file (case-only change on Windows)

        if os.path.exists(new_path):
            print(f"目标文件已存在：{new_stem}{ext}")
            return

        # ── Release file lock before renaming ──
        self._mw.audio_manager._player.stop()
        self._mw.audio_manager._player.setSource(QUrl())

        try:
            os.rename(path, new_path)
        except OSError as e:
            print(f"重命名失败：{e}")
            # Re-load original file
            self._mw.audio_manager.set_source(
                QUrl.fromLocalFile(path).toString()
            )
            return

        # ── Also rename matching .lrc file if it exists ──
        old_lrc = os.path.splitext(path)[0] + ".lrc"
        if os.path.isfile(old_lrc):
            new_lrc = os.path.join(old_dir, new_stem + ".lrc")
            try:
                os.rename(old_lrc, new_lrc)
            except OSError:
                pass  # LRC rename is best-effort

        # ── 3. Reload audio from new path ──
        url = QUrl.fromLocalFile(new_path).toString()
        self._mw.audio_manager.set_source(url)
        self._mw.config.remember_mp3_path(new_path)
        self._last_audio_path = new_path

        # ── 4. Refresh playlist entry ──
        self._notify_playlist(path, new_path)

        print(f"已重命名为：{new_stem}{ext}")


# ── mutagen helper functions ─────────────────────────────────────

def _id3_text(tags: ID3, frame_cls) -> str:
    """Get the first text value from an ID3 text frame, or ''."""
    frame = tags.get(frame_cls.__name__)  # e.g. "TIT2"
    if frame is None:
        return ""
    return str(frame.text[0]) if frame.text else ""


def _id3_comment(tags: ID3) -> str:
    """Get the first comment text, or ''."""
    for key in tags:
        if key.startswith("COMM"):
            frame = tags[key]
            return str(frame.text[0]) if frame.text else ""
    return ""


def _set_id3_text(tags: ID3, frame_cls, value: str) -> None:
    """Set or delete an ID3 text frame."""
    value = value.strip()
    tags.delall(frame_cls.__name__)
    if value:
        tags.add(frame_cls(encoding=3, text=value))


def _vc_text(tags, key: str) -> str:
    """Get the first VorbisComment value for *key*, or ''."""
    try:
        vals = tags.get(key)
        return vals[0] if vals else ""
    except Exception:
        return ""


def _set_vc_text(tags, key: str, value: str) -> None:
    """Set or delete a VorbisComment field."""
    value = value.strip()
    if key in tags:
        del tags[key]
    if value:
        tags[key] = value
