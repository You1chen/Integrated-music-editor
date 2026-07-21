"""Load Audio dialog — file picker + URL input (replaces loadaudio.tsx)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


class LoadAudioDialog(QDialog):
    """Modal dialog for loading audio: local file or URL."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._mw = main_window

        self.setWindowTitle("加载音频")
        self.setMinimumWidth(450)

        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── File Tab ────────────────────────────────────
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        file_layout.setContentsMargins(20, 20, 20, 20)

        file_label = QLabel("点击这里或直接拖放音频到这个页面")
        file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        file_label.setStyleSheet("font-size: 15px; padding: 20px;")
        file_layout.addWidget(file_label)

        file_btn = QPushButton("📁 文件")
        file_btn.setObjectName("loadAudioFileBtn")
        file_btn.clicked.connect(self._on_file_pick)
        file_layout.addWidget(file_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        file_layout.addStretch()
        tabs.addTab(file_tab, "文件")

        # ── URL Tab ─────────────────────────────────────
        url_tab = QWidget()
        url_layout = QVBoxLayout(url_tab)
        url_layout.setContentsMargins(20, 20, 20, 20)

        url_label = QLabel("输入音频链接：")
        url_layout.addWidget(url_label)

        url_form = QHBoxLayout()
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://example.com/audio.mp3")
        url_form.addWidget(self._url_input)

        url_submit = QPushButton("加载")
        url_submit.clicked.connect(self._on_url_submit)
        url_form.addWidget(url_submit)

        url_layout.addLayout(url_form)
        url_layout.addStretch()
        tabs.addTab(url_tab, "外链")

    def _on_file_pick(self) -> None:
        # Determine initial directory: last path > default browse dir > home
        default_dir = self._mw.config.get_default_browse_dir()
        last_path = self._mw.config.get_last_mp3_path()
        if last_path and os.path.exists(os.path.dirname(last_path)):
            start_dir = os.path.dirname(last_path)
        elif default_dir and os.path.exists(default_dir):
            start_dir = default_dir
        else:
            start_dir = ""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开音频文件",
            start_dir,
            "音频文件 (*.mp3 *.flac *.wav *.ogg *.m4a *.aac *.wma *.opus);;所有文件 (*)",
        )
        if file_path:
            # Save last path if preference is enabled
            if self._mw.config.get_remember_last_mp3():
                self._mw.config.set_last_mp3_path(file_path)

            url = QUrl.fromLocalFile(file_path).toString()
            self._mw.config.set_audio_src(url)
            self.accept()
            # Defer audio loading to avoid crash when dialog is still active
            QTimer.singleShot(100, lambda: self._mw.audio_manager.set_source(url))

    def _on_url_submit(self) -> None:
        url = self._url_input.text().strip()
        if url:
            # Handle Netease music URLs (port of nec() function)
            if "music.163.com" in url:
                import re
                match = re.search(r"\d{4,}", url)
                if match:
                    url = f"https://music.163.com/song/media/outer/url?id={match.group(0)}.mp3"

            self._mw.config.set_audio_src(url)
            self.accept()
            # Defer audio loading to avoid crash when dialog is still active
            QTimer.singleShot(100, lambda: self._mw.audio_manager.set_source(url))
