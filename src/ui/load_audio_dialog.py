"""Load Audio dialog — local file picker."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


class LoadAudioDialog(QDialog):
    """Modal dialog for loading a local audio file."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._mw = main_window

        self.setWindowTitle("加载音频")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        label = QLabel("选择音频文件，或直接拖放到窗口底部")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-size: 15px; padding: 20px;")
        layout.addWidget(label)

        file_btn = QPushButton("📁 选择文件")
        file_btn.setObjectName("loadAudioFileBtn")
        file_btn.clicked.connect(self._on_file_pick)
        layout.addWidget(file_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

    def _on_file_pick(self) -> None:
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
            self._mw.config.remember_mp3_path(file_path)

            url = QUrl.fromLocalFile(file_path).toString()
            self._mw.config.set_audio_src(url)
            self.accept()
            QTimer.singleShot(100, lambda: self._mw.audio_manager.set_source(url))
