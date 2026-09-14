"""LRC Maker — desktop lyrics editor and synchronization tool."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.ui.main_window import MainWindow
from src.core.constants import PageRoute
from src.ui.content_stack import apply_theme
from src.ui.home_page import HomePage
from src.ui.editor_page import EditorPage
from src.ui.synchronizer_page import SynchronizerPage
from src.ui.preferences_page import PreferencesPage
from src.ui.meta_editor_page import MetaEditorPage
from src.ui.playlist_page import PlaylistPage
from src.ui.audio_controls import AudioControls


def main() -> None:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("集成歌曲编辑器")
    app.setOrganizationName("lrc-maker")
    app.setApplicationVersion("6.0.0")

    app.setStyle("Fusion")

    window = MainWindow()
    window.resize(1100, 700)
    window.setMinimumSize(800, 500)

    home = HomePage(window)
    window.content_stack.register_page(PageRoute.HOME, home)

    editor = EditorPage(window)
    window.content_stack.register_page(PageRoute.EDITOR, editor)

    sync = SynchronizerPage(window)
    window.content_stack.register_page(PageRoute.SYNCHRONIZER, sync)

    prefs_page = PreferencesPage(window)
    window.content_stack.register_page(PageRoute.PREFERENCES, prefs_page)

    meta_editor = MetaEditorPage(window)
    window.content_stack.register_page(PageRoute.META_EDITOR, meta_editor)

    playlist = PlaylistPage(window)
    window.content_stack.register_page(PageRoute.PLAYLIST, playlist)
    window.liked_changed.connect(playlist.sync_liked)

    audio_controls = AudioControls(window)
    window.footer_bar.set_audio_controls(audio_controls)

    if window.config.get_remember_playback_rate():
        last_rate = window.config.get_last_playback_rate()
        window.audio_manager.playback_rate = last_rate

    saved_volume = window.config.get_last_volume()
    window.audio_manager.volume = saved_volume["volume"]
    window.audio_manager.muted = saved_volume["muted"]
    audio_controls.refresh_volume_icon()

    saved_prefs = window.config.get_preferences()
    apply_theme(saved_prefs)

    audio_controls.set_waveform_visible(saved_prefs.get("showWaveform", True))
    audio_controls.set_fixed(saved_prefs.get("fixed", 3))

    window.audio_manager.current_time_changed.connect(
        audio_controls.on_current_time_changed
    )

    window.lrc_state.state_changed.connect(
        lambda: audio_controls.set_fixed(
            window.config.get_preferences().get("fixed", 3)
        )
    )

    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
