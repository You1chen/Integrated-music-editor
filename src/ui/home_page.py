"""Cover-driven lyrics card for the music-player home page."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import mutagen
from mutagen.id3 import APIC, ID3

from .content_stack import get_theme_colors, theme_events
from .lyric_axis_widget import LyricAxisWidget
from .lyric_card_style import card_palette, draw_cover, mix, soft_cover
from .song_info_dialog import _read_tags

if TYPE_CHECKING:
    from .main_window import MainWindow


class _CardSurface(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.art = QImage()
        self.base = QColor("#171820")
        self.accent = QColor("#e3c7e9")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        outline = QPainterPath()
        outline.addRoundedRect(bounds, 22, 22)
        painter.setClipPath(outline)
        painter.fillRect(bounds, self.base)
        draw_cover(painter, bounds, self.art)
        veil = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        start = mix(self.base, self.accent, 0.10)
        start.setAlpha(218)
        end = QColor(self.base)
        end.setAlpha(247)
        veil.setColorAt(0.0, start)
        veil.setColorAt(0.70, end)
        painter.fillRect(bounds, veil)


class HomePage(QWidget):
    """Keep the current track and the live lyrics together on one card."""

    _STYLES = {"faithful": "忠实", "soft": "柔和", "vivid": "鲜明", "mono": "单色"}
    _TONES = {"auto": "自动", "light": "浅色", "dark": "深色"}

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window
        self._options = self._mw.config.get_home_card_options()
        self._cover_data: bytes | None = None
        self._cover_mime = ""
        self._cover_image = QImage()
        self._audio_tags: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        self._surface = _CardSurface()
        outer.addWidget(self._surface)
        content = QVBoxLayout(self._surface)
        content.setContentsMargins(22, 14, 22, 20)
        content.setSpacing(10)

        actions = QHBoxLayout()
        actions.addStretch()
        self._mode_btn = QPushButton()
        self._mode_btn.setToolTip("切换原文、译文与双语")
        self._appearance_btn = QPushButton("卡片外观")
        self._appearance_btn.clicked.connect(self._open_appearance)
        self._share_btn = QPushButton("分享歌词")
        self._share_btn.clicked.connect(self._open_share)
        self._action_buttons = (self._mode_btn, self._appearance_btn, self._share_btn)
        for button in self._action_buttons:
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        content.addLayout(actions)

        row = QHBoxLayout()
        row.setSpacing(26)
        content.addLayout(row, stretch=1)
        self._card_row = row
        track = QWidget()
        track.setObjectName("homeTrack")
        track.setStyleSheet("#homeTrack { background: transparent; }")
        track_layout = QVBoxLayout(track)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(8)
        track_layout.addStretch()
        self._cover_btn = QPushButton("选择封面")
        self._cover_btn.setObjectName("homeCover")
        self._cover_btn.setToolTip("选择或更换封面")
        self._cover_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cover_btn.clicked.connect(self._on_browse_cover)
        track_layout.addWidget(self._cover_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._title = QLabel()
        self._subtitle = QLabel()
        for label in (self._title, self._subtitle):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            track_layout.addWidget(label)
        track_layout.addStretch()
        self._track = track
        row.addWidget(track)

        self._lyric_axis = LyricAxisWidget(main_window, card_palette(QImage(), QColor("#b38cc4")),
                                           self._options.get("mode", "bilingual"))
        row.addWidget(self._lyric_axis, stretch=1)
        self._mode_btn.clicked.connect(self._cycle_mode)
        self._lyric_axis.mode_changed.connect(self._sync_actions)
        self._mw.lrc_state.document_changed.connect(self._refresh_song_info)
        self._mw.audio_manager.media_loaded.connect(self._load_cover)
        self._mw.audio_manager.source_changed.connect(self._source_changed)
        theme_events.changed.connect(self._restyle_card)
        self._load_cover()

    def set_lyric_axis_visible(self, visible: bool) -> None:
        self._lyric_axis.setVisible(visible)
        self._mode_btn.setVisible(visible)
        self._card_row.setStretch(0, int(not visible))

    def _cycle_mode(self) -> None:
        self._lyric_axis.cycle_mode()
        self._save_option("mode", self._lyric_axis.mode)

    def _save_option(self, key: str, value: str) -> None:
        self._options[key] = value
        self._mw.config.set_home_card_options(self._options)

    def _sync_actions(self) -> None:
        axis = self._lyric_axis
        mode = axis.mode if axis.has_translation else "original"
        self._mode_btn.setText(axis.MODE_LABELS[mode])
        self._mode_btn.setEnabled(axis.has_translation)
        self._share_btn.setEnabled(any(line.text.strip() or line.translation.strip()
                                       for line in self._mw.lrc_state.lyric))

    def _open_appearance(self) -> None:
        menu = QMenu(self)
        for key, title, values in (("style", "配色", self._STYLES), ("tone", "明暗", self._TONES)):
            group = menu.addMenu(title)
            for value, label in values.items():
                action = group.addAction(label)
                action.setCheckable(True)
                action.setChecked(value == self._options.get(key, next(iter(values))))
                action.setData((key, value))
        action = menu.exec(self._appearance_btn.mapToGlobal(self._appearance_btn.rect().bottomLeft()))
        if action is not None and action.data():
            self._save_option(*action.data())
            self._restyle_card()

    def _song_info(self) -> tuple[str, str, str]:
        info = self._mw.lrc_state.info
        audio_path = self._mw.audio_manager.local_path
        title = info.get("ti") or self._audio_tags.get("title")
        title = title or os.path.splitext(os.path.basename(audio_path))[0] or "未加载歌曲"
        return title, info.get("ar") or self._audio_tags.get("artist", ""), info.get("al") or self._audio_tags.get("album", "")

    def _refresh_song_info(self) -> None:
        title, artist, album = self._song_info()
        for label, text in ((self._title, title), (self._subtitle, " · ".join(filter(None, (artist, album))))):
            label.setToolTip(text)
            label.setText(QFontMetrics(label.font()).elidedText(text, Qt.TextElideMode.ElideRight,
                                                               self._track.width()))
        self._sync_actions()

    def _open_share(self) -> None:
        from .lyric_share_dialog import LyricShareDialog

        title, artist, album = self._song_info()
        audio = self._mw.audio_manager
        lines = list(self._mw.lrc_state.lyric)
        index = max((i for i, line in enumerate(lines) if line.time is not None and line.time <= audio.current_time),
                    key=lambda i: (lines[i].time, i), default=self._mw.lrc_state.select_index)
        dialog = LyricShareDialog(title=title, artist=artist, album=album, cover=self._cover_image,
                                  lines=lines, current_index=index,
                                  progress=audio.current_time / audio.duration if audio.duration else 0,
                                  config=self._mw.config, parent=self)
        dialog.exec()
        dialog.deleteLater()

    def _source_changed(self, url: str) -> None:
        if url != self._mw.audio_manager.src:
            self._audio_tags = {}
            self._show_placeholder()

    def _restyle_card(self) -> None:
        _bg, _fg, theme, dark = get_theme_colors()
        tone = self._options.get("tone", "auto")
        if self._cover_image.isNull() and tone == "auto":
            tone = "dark" if dark else "light"
        style = self._options.get("style", "faithful")
        palette = card_palette(self._cover_image, QColor(theme), style=style, tone=tone)
        self._surface.base = palette.background
        self._surface.accent = palette.accent
        self._surface.update()
        self._lyric_axis.set_card_palette(palette)
        text, accent = palette.text.name(), palette.accent.name()
        border = mix(palette.background, palette.text, 0.20).name()
        hover = QColor(palette.accent)
        hover.setAlpha(46)
        hover_css = f"rgba({hover.red()},{hover.green()},{hover.blue()},0.18)"
        # Controls stay transparent so only the card surface provides a background;
        # a second solid fill would visibly disagree with the card gradient.
        self._cover_btn.setStyleSheet(f"""
            QPushButton#homeCover {{ border: 1px solid {border}; border-radius: 13px;
                background: transparent; color: {text}; }}
            QPushButton#homeCover:hover {{ border-color: {accent}; background: {hover_css}; }}
        """)
        self._title.setStyleSheet(f"color: {text}; font-size: 22px; font-weight: 700; background: transparent;")
        self._subtitle.setStyleSheet(f"color: {palette.secondary.name()}; font-size: 13px; background: transparent;")
        for button in self._action_buttons:
            button.setStyleSheet(f"""
                QPushButton {{ border: 1px solid {border}; border-radius: 13px;
                    padding: 5px 12px; color: {text}; background: transparent; }}
                QPushButton:hover {{ border-color: {accent}; background: {hover_css}; }}
                QPushButton:disabled {{ color: {palette.secondary.name()}; }}
            """)
        self._refresh_song_info()

    def _load_cover(self) -> None:
        """Read embedded cover art from the audio file using mutagen."""
        path = self._mw.audio_manager.local_path
        self._audio_tags = _read_tags(path) if path else {}
        if not path:
            self._show_placeholder()
            return

        try:
            audio = mutagen.File(path)
        except Exception:
            self._show_placeholder()
            return

        if audio is None:
            self._show_placeholder()
            return

        tags = getattr(audio, "tags", None)

        if isinstance(tags, ID3):
            apic = tags.getall("APIC")
            if apic:
                self._cover_data = apic[0].data
                self._cover_mime = apic[0].mime
                self._update_cover_icon()
                return

        pics = getattr(audio, "pictures", None)
        if pics:
            self._cover_data = pics[0].data
            self._cover_mime = pics[0].mime
            self._update_cover_icon()
            return

        self._show_placeholder()

    def _on_browse_cover(self) -> None:
        """Browse for a cover image file, then open the crop dialog."""
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

        from .meta_editor_page import CoverCropDialog
        crop_dialog = CoverCropDialog(file_path, self)
        if crop_dialog.exec() != CoverCropDialog.DialogCode.Accepted:
            return

        self._cover_data, self._cover_mime = crop_dialog.get_result()
        if not self._cover_data:
            self._show_placeholder()
            return

        self._update_cover_icon()

        reply = QMessageBox.question(
            self,
            "保存封面",
            "是否将封面保存到音频文件？\n"
            "（若不保存，封面仅在本页显示，重新加载音频后会消失）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._save_cover_to_audio()

    def _save_cover_to_audio(self) -> None:
        """Write the in-memory cover to the audio file, keeping other tags."""
        path = self._mw.audio_manager.local_path
        if not path or not os.path.isfile(path):
            print("无法保存封面：音频文件不存在")
            return

        try:
            audio = mutagen.File(path)
        except Exception:
            print("无法写入音频文件")
            return

        if audio is None:
            print("不支持的音频格式，无法写入封面")
            return

        try:
            tags = getattr(audio, "tags", None)
            if tags is None:
                try:
                    audio.add_tags()
                    tags = audio.tags
                except Exception:
                    print("无法为此文件创建标签")
                    return

            if isinstance(tags, ID3):
                tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime=self._cover_mime,
                        type=3,
                        desc="cover",
                        data=self._cover_data,
                    )
                )
                audio.save()
                self._after_cover_saved()
                return

            if hasattr(audio, "clear_pictures") and hasattr(audio, "add_picture"):
                from mutagen.flac import Picture
                pic = Picture()
                pic.type = 3
                pic.mime = self._cover_mime
                pic.desc = "cover"
                pic.data = self._cover_data
                audio.clear_pictures()
                audio.add_picture(pic)
                audio.save()
                self._after_cover_saved()
                return

            print("该格式不支持写入封面")
        except Exception:
            print("保存封面失败")

    def _after_cover_saved(self) -> None:
        """Report success and force the meta editor to re-read the file."""
        print("封面已保存到音频文件")

        try:
            from ..core.constants import PageRoute
            meta_page = self._mw.content_stack._pages.get(PageRoute.META_EDITOR)
            if meta_page is not None and hasattr(meta_page, "_last_audio_path"):
                meta_page._last_audio_path = ""
        except Exception:
            pass

        self._notify_playlist()

    def _notify_playlist(self) -> None:
        """Refresh the current song entry in the playlist."""
        try:
            from ..core.constants import PageRoute
            playlist_page = self._mw.content_stack._pages.get(PageRoute.PLAYLIST)
            if playlist_page is not None and hasattr(playlist_page, "refresh_song"):
                path = self._mw.audio_manager.local_path
                if path:
                    playlist_page.refresh_song(path)
        except Exception:
            pass

    def _update_cover_icon(self) -> None:
        """Update the button icon from ``_cover_data`` bytes."""
        if not self._cover_data:
            self._show_placeholder()
            return

        self._cover_image = QImage.fromData(self._cover_data)
        if self._cover_image.isNull():
            self._show_placeholder()
            return
        self._surface.art = soft_cover(self._cover_image, 2)
        self._paint_cover_icon()
        self._restyle_card()

    def _paint_cover_icon(self) -> None:
        if self._cover_image.isNull():
            return
        size = self._cover_btn.size()
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(size * ratio)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        bounds = QRectF(0, 0, size.width(), size.height())
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outline = QPainterPath()
        outline.addRoundedRect(bounds, 13, 13)
        painter.setClipPath(outline)
        draw_cover(painter, bounds, self._cover_image)
        painter.end()
        self._cover_btn.setIcon(QIcon(pixmap))
        self._cover_btn.setIconSize(size)
        self._cover_btn.setText("")

    def _show_placeholder(self) -> None:
        """Reset cover to placeholder state."""
        self._cover_data = None
        self._cover_mime = ""
        self._cover_image = QImage()
        self._surface.art = QImage()
        self._cover_btn.setIcon(QIcon())
        self._cover_btn.setText("选择封面")
        self._restyle_card()

    def resizeEvent(self, event) -> None:
        """Keep enough room for the lyric column on a narrow window."""
        super().resizeEvent(event)
        width = max(136, min(390, int(self.width() * 0.36)))
        side = max(112, min(width, self.height() - 142))
        self._track.setFixedWidth(width)
        self._cover_btn.setFixedSize(side, side)
        self._paint_cover_icon()
        self._refresh_song_info()
