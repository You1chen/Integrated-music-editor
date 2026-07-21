"""Footer bar — audio player container with drag-and-drop support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QWidget,
)

from ..core.audio_manager import AudioStateData
from ..core.constants import PageRoute

if TYPE_CHECKING:
    from .main_window import MainWindow
    from .audio_controls import AudioControls


class FooterBar(QWidget):
    """Bottom bar hosting the audio player controls.

    Also provides drag-and-drop support for audio files.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self.setObjectName("footerBar")
        self.setFixedHeight(80)
        self.setAcceptDrops(True)

        self._main_window = main_window

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # AudioControls will be set after creation (circular dependency)
        self.audio_controls: "AudioControls | None" = None

        # Placeholder label until audio controls are set
        from PyQt6.QtWidgets import QLabel
        self._placeholder = QLabel("未加载音频")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._placeholder)

    def set_audio_controls(self, controls: "AudioControls") -> None:
        """Set the audio controls widget."""
        layout = self.layout()
        if layout:
            # Remove placeholder
            if self._placeholder:
                layout.removeWidget(self._placeholder)
                self._placeholder.hide()
                self._placeholder.deleteLater()
                self._placeholder = None
            layout.addWidget(controls)
        self.audio_controls = controls

    def update_audio_state(self, data: AudioStateData) -> None:
        """Forward audio state changes to the audio controls."""
        if self.audio_controls:
            self.audio_controls.update_state(data)

    # ── Drag and Drop ──────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if file_path:
                self._handle_file_drop(file_path)

    def _handle_file_drop(self, file_path: str) -> None:
        """Handle a file dropped onto the footer.

        Audio files → load into player.
        Text/LRC files → parse as lyrics (switch to editor).
        """
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        audio_exts = {"mp3", "flac", "wav", "ogg", "m4a", "aac", "wma", "opus"}
        text_exts = {"txt", "lrc"}

        if ext in audio_exts:
            # Load audio
            if self._main_window.config.get_remember_last_mp3():
                self._main_window.config.set_last_mp3_path(file_path)
            self._main_window.audio_manager.set_source(
                QUrl.fromLocalFile(file_path).toString()
            )
            self._main_window.config.set_audio_src(
                QUrl.fromLocalFile(file_path).toString()
            )
        elif ext in text_exts:
            # Load lyrics text
            if self._main_window.config.get_remember_last_lrc():
                self._main_window.config.set_last_lrc_path(file_path)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._main_window.lrc_state.parse(
                    text,
                    self._main_window.trim_options,
                )
                self._main_window.content_stack.set_page(PageRoute.EDITOR)
            except Exception as e:
                self._main_window.toast_overlay.show_toast(
                    "warning", f"加载文件失败：{e}"
                )
