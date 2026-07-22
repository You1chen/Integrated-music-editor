"""LRC Maker — Desktop lyrics editor and synchronization tool.

Entry point for the PyQt6 application. Run with:
    python main.py
"""

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
from src.ui.audio_controls import AudioControls


def main() -> None:
    # High DPI support (Windows)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("集成歌曲编辑器")
    app.setOrganizationName("lrc-maker")
    app.setApplicationVersion("6.0.0")

    # Fusion style ensures QSS-compatible rendering for all widgets
    # (native Windows menus/dropdowns would ignore stylesheets otherwise)
    app.setStyle("Fusion")

    # ── Build Main Window ──────────────────────────────
    window = MainWindow()
    window.resize(1100, 700)
    window.setMinimumSize(800, 500)

    # ── Create Pages ────────────────────────────────────
    # Home page (route 0)
    home = HomePage(window)
    window.content_stack.register_page(PageRoute.HOME, home)

    # Editor page (route 1) — accessed via drag-and-drop of .lrc/.txt files
    editor = EditorPage(window)
    window.content_stack.register_page(PageRoute.EDITOR, editor)

    # Synchronizer page (route 2)
    sync = SynchronizerPage(window)
    window.content_stack.register_page(PageRoute.SYNCHRONIZER, sync)

    # Preferences page (route 3)
    prefs_page = PreferencesPage(window)
    window.content_stack.register_page(PageRoute.PREFERENCES, prefs_page)

    # Meta editor page (route 4) — edit audio file metadata (ID3 tags)
    meta_editor = MetaEditorPage(window)
    window.content_stack.register_page(PageRoute.META_EDITOR, meta_editor)

    # ── Create Audio Controls and wire into footer ──────
    audio_controls = AudioControls(window)
    window.footer_bar.set_audio_controls(audio_controls)

    # Restore last playback rate (must be after audio_controls is wired to receive signals)
    if window.config.get_remember_playback_rate():
        last_rate = window.config.get_last_playback_rate()
        window.audio_manager.playback_rate = last_rate

    # ── Apply initial theme from saved preferences ──────
    saved_prefs = window.config.get_preferences()
    apply_theme(saved_prefs)

    # ── Apply waveform preference to audio controls ─────
    audio_controls.set_waveform_visible(saved_prefs.get("showWaveform", True))
    audio_controls.set_fixed(saved_prefs.get("fixed", 3))

    # Connect audio current-time changes → audio controls display
    # (so that seeking while paused — e.g. clicking a timestamp —
    #  updates the progress bar and time label immediately)
    window.audio_manager.current_time_changed.connect(
        audio_controls.on_current_time_changed
    )

    # Connect preference updates → audio controls
    window.lrc_state.state_changed.connect(
        lambda: audio_controls.set_fixed(
            window.config.get_preferences().get("fixed", 3)
        )
    )

    # Show window
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
