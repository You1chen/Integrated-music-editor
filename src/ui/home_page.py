"""Music-player home page — cover art + scrolling lyrics axis.

Replaces the old help/intro page.  The old content now lives in a one-shot
WelcomeDialog that pops up on launch (can be disabled in preferences).

Layout (QHBoxLayout):
  Left  — cover art (clickable QPushButton, square, scales with height)
  Right — LyricAxisWidget (fills remaining width, scrolls with audio)

Cover behaviour mirrors MetaEditorPage:
  - Reads embedded cover from the audio file via mutagen (ID3 APIC / FLAC pictures).
  - Click to browse → crop → display a custom cover image.
  - Falls back to placeholder text when no cover is available.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import mutagen
from mutagen.id3 import ID3

from .lyric_axis_widget import LyricAxisWidget

if TYPE_CHECKING:
    from .main_window import MainWindow


class HomePage(QWidget):
    """Music-player landing page.

    ┌──────────────────────────────────────────┐
    │  ┌──────────┐  ┌──────────────────────┐  │
    │  │  cover   │  │   LyricAxisWidget    │  │
    │  │  button  │  │   (stretch)          │  │
    │  └──────────┘  └──────────────────────┘  │
    └──────────────────────────────────────────┘
    """

    # ── Layout tweaks ────────────────────────────────────────────
    COVER_MIN = 160          # minimum cover width/height in px
    COVER_MAX = 420          # maximum cover width/height in px
    COVER_RADIUS = 12        # border-radius for cover (px, QSS)
    # ──────────────────────────────────────────────────────────────

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        # ── Cover data (mirrors MetaEditorPage) ──
        self._cover_data: bytes | None = None   # raw image bytes
        self._cover_mime: str = ""              # e.g. "image/jpeg"

        # ── Outer container (provides padding) ──
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Inner row: cover | lyrics ──
        row = QHBoxLayout()
        row.setContentsMargins(24, 20, 24, 20)
        row.setSpacing(28)
        outer.addLayout(row, stretch=1)

        # ── Left: cover button ───────────────────────────────
        # QPushButton（非 QLabel）— 点击可导入封面，行为和 MetaEditorPage 一致。
        cover_wrap = QVBoxLayout()
        cover_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addLayout(cover_wrap)

        self._cover_btn = QPushButton("无封面")
        self._cover_btn.setObjectName("homeCover")
        self._cover_btn.setFlat(True)
        self._cover_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cover_btn.setMinimumSize(self.COVER_MIN, self.COVER_MIN)
        self._cover_btn.setMaximumSize(self.COVER_MAX, self.COVER_MAX)
        self._cover_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._cover_btn.setStyleSheet(f"""
            QPushButton#homeCover {{
                border: none;
                border-radius: {self.COVER_RADIUS}px;
                background-color: transparent;
                font-size: 13px;
                color: #888888;
            }}
            QPushButton#homeCover:hover {{
                background-color: rgba(255,255,255,8);
            }}
        """)
        self._cover_btn.clicked.connect(self._on_browse_cover)
        cover_wrap.addWidget(self._cover_btn)

        # ── Right: lyrics axis ───────────────────────────────
        self._lyric_axis = LyricAxisWidget(main_window)
        row.addWidget(self._lyric_axis, stretch=1)

        # ── Listen: audio changes → reload embedded cover ────
        self._mw.audio_manager.duration_changed.connect(self._load_cover)

        # Initial load
        self._load_cover()

    # ── Audio path helper ────────────────────────────────────────

    # ── Cover: load from audio file ──────────────────────────────

    def _load_cover(self) -> None:
        """Read embedded cover art from the audio file using mutagen."""
        path = self._mw.audio_manager.local_path
        if not path or not os.path.isfile(path):
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

        # ID3 (MP3)
        if isinstance(tags, ID3):
            apic = tags.getall("APIC")
            if apic:
                self._cover_data = apic[0].data
                self._cover_mime = apic[0].mime
                self._update_cover_icon()
                return

        # FLAC / Ogg pictures
        pics = getattr(audio, "pictures", None)
        if pics:
            self._cover_data = pics[0].data
            self._cover_mime = pics[0].mime
            self._update_cover_icon()
            return

        self._show_placeholder()

    # ── Cover: browse & crop ─────────────────────────────────────

    def _on_browse_cover(self) -> None:
        """Browse for a cover image file, then open crop dialog."""
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

        # Open crop dialog (same as MetaEditorPage)
        from .meta_editor_page import CoverCropDialog
        crop_dialog = CoverCropDialog(file_path, self)
        if crop_dialog.exec() != CoverCropDialog.DialogCode.Accepted:
            return

        self._cover_data, self._cover_mime = crop_dialog.get_result()
        if not self._cover_data:
            self._show_placeholder()
            return

        self._update_cover_icon()

    # ── Cover: icon rendering ────────────────────────────────────

    def _update_cover_icon(self) -> None:
        """Update the button icon from ``_cover_data`` bytes."""
        if not self._cover_data:
            self._show_placeholder()
            return

        pix = QPixmap()
        if not pix.loadFromData(self._cover_data):
            self._show_placeholder()
            return

        size = self._cover_btn.width()
        if size <= 0:
            size = self.COVER_MIN

        # Square images → stretch to fill; rectangular → fit keeping ratio
        mode = (
            Qt.AspectRatioMode.IgnoreAspectRatio
            if pix.width() == pix.height()
            else Qt.AspectRatioMode.KeepAspectRatio
        )
        scaled = pix.scaled(
            size, size, mode, Qt.TransformationMode.SmoothTransformation
        )
        self._cover_btn.setIcon(QIcon(scaled))
        self._cover_btn.setIconSize(self._cover_btn.size())
        self._cover_btn.setText("")

    def _show_placeholder(self) -> None:
        """Reset cover to placeholder state."""
        self._cover_data = None
        self._cover_mime = ""
        self._cover_btn.setIcon(QIcon())
        self._cover_btn.setText("无封面")

    # ── Resize ───────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        """Keep cover square and re-scale icon on resize."""
        super().resizeEvent(event)
        # Enforce square: side = min(available height, available width)
        # clamped to [COVER_MIN, COVER_MAX].
        avail_h = self.height() - 40         # 20px top + bottom margin
        avail_w = self.width() - 24 * 2 - 28  # horizontal padding + gap
        side = max(self.COVER_MIN, min(self.COVER_MAX, avail_h, avail_w))
        self._cover_btn.setFixedSize(side, side)

        if self._cover_data:
            self._update_cover_icon()
