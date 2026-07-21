"""Main application window — QMainWindow with header + content stack + footer."""

from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.audio_manager import AudioManager, AudioState, AudioStateData
from ..core.config_manager import ConfigManager
from ..core.constants import InputAction, PageRoute, SyncMode
from ..core.keybinding import KeyBindingManager
from ..core.lrc_parser import FormatOptions, Fixed, TrimOptions
from ..core.lrc_state import LrcStateManager
from .content_stack import ContentStack
from .footer_bar import FooterBar
from .header_bar import HeaderBar
from .toast_overlay import ToastOverlay


class MainWindow(QMainWindow):
    """Top-level application window.

    Orchestrates all shared state and connects signals between components.
    """

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("集成歌曲编辑器")
        self.setMinimumSize(800, 500)

        # ── Shared State Objects ────────────────────────────
        self.config = ConfigManager()
        self.lrc_state = LrcStateManager(self)
        self.audio_manager = AudioManager(self)
        self.keybinding_manager = KeyBindingManager(
            user_overrides=self.config.get_keybindings()
        )

        # Guard against _save_state re-creating draft during close
        self._closing = False

        # Load saved preferences
        prefs = self.config.get_preferences()

        # Trim options for parsing
        space_start = prefs.get("spaceStart", 1)
        space_end = prefs.get("spaceEnd", 0)
        self._trim_options = TrimOptions(
            trim_start=space_start >= 0,
            trim_end=space_end >= 0,
        )

        # Format options for stringify
        self._format_options = FormatOptions(
            space_start=space_start,
            space_end=space_end,
            fixed=prefs.get("fixed", 3),
            end_of_line="\r\n",
        )
        self.lrc_state.update_format_options(self._format_options)

        # ── Build UI ────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self.header_bar = HeaderBar()
        layout.addWidget(self.header_bar)

        # Content stack (pages)
        self.content_stack = ContentStack(self)
        layout.addWidget(self.content_stack, stretch=1)

        # Footer (audio controls)
        self.footer_bar = FooterBar(self)
        layout.addWidget(self.footer_bar)

        # Toast overlay (positioned absolutely at top-right)
        self.toast_overlay = ToastOverlay(self)
        self.toast_overlay.setFixedWidth(320)

        # ── Conect Signals ──────────────────────────────────
        self._connect_signals()

        # ── Load saved draft ────────────────────────────────
        if self.config.get_remember_draft():
            saved_lyric = self.config.get_lyric()
            if saved_lyric:
                self.lrc_state.init_from_text(
                    text=saved_lyric,
                    options=self._trim_options,
                    select=self.config.get_select_index(),
                )

        # Restore audio source: last path takes priority (if remember enabled)
        if self.config.get_remember_last_mp3():
            last_mp3 = self.config.get_last_mp3_path()
            if last_mp3:
                import os
                if os.path.exists(last_mp3):
                    url = QUrl.fromLocalFile(last_mp3).toString()
                    self.config.set_audio_src(url)
                    self.audio_manager.set_source(url)
        if not self.audio_manager.src:
            saved_src = self.config.get_audio_src()
            if saved_src:
                self.audio_manager.set_source(saved_src)

        # Restore last LRC file
        if self.config.get_remember_last_lrc():
            last_lrc = self.config.get_last_lrc_path()
            if last_lrc:
                import os
                if os.path.exists(last_lrc):
                    try:
                        with open(last_lrc, "r", encoding="utf-8") as f:
                            text = f.read()
                        self.lrc_state.init_from_text(
                            text=text,
                            options=self._trim_options,
                            select=self.config.get_select_index(),
                        )
                    except Exception:
                        pass  # Silently fail if file can't be loaded

        # Show home page by default
        self.content_stack.set_page(PageRoute.HOME)

        # ── Install app-wide event filter for keyboard shortcuts ──
        # Must be on QApplication (not self) so that key events are
        # intercepted BEFORE they reach the focused child widget.
        # This way Space → timestamp always works when a lyric is
        # selected, even if focus is on the play button or elsewhere.
        from PyQt6.QtWidgets import QApplication as QA
        app_instance = QA.instance()
        if app_instance:
            app_instance.installEventFilter(self)

    # ── Event Filter (global keyboard) ──────────────────────

    def eventFilter(self, obj, event):
        """App-wide event filter: routes keyboard events.

        When a lyric line is selected on the synchronizer page:
        - Space (SYNC) always timestamps — never toggles play/pause
        - Audio shortcuts (seek, rate, toggle) are blocked entirely
        Other key events fall through to handle_global_key.

        Keyboard shortcuts are suppressed when a text-input widget has
        focus so that dialogs (like pattern-match / AI assist) receive
        normal text input.
        """
        from PyQt6.QtCore import QEvent
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        # ── Don't steal keys from text-input widgets ──
        from PyQt6.QtWidgets import QApplication
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None and isinstance(
            focus_widget, (QLineEdit, QPlainTextEdit, QTextEdit)
        ):
            return super().eventFilter(obj, event)

        # ── Intercept when a lyric is selected on the sync page ──
        sync_page = self.content_stack._pages.get(PageRoute.SYNCHRONIZER)
        if (
            sync_page is not None
            and self.content_stack.currentIndex() == PageRoute.SYNCHRONIZER
            and self.lrc_state.select_index != -1
        ):
            action = self.keybinding_manager.get_matched_action(event)

            # Audio shortcuts must NOT fire when a lyric is selected
            if action in (
                InputAction.SEEK_BACKWARD,
                InputAction.SEEK_FORWARD,
                InputAction.RESET_RATE,
                InputAction.TOGGLE_PLAY,
                InputAction.INCREASE_RATE,
                InputAction.DECREASE_RATE,
            ):
                return True  # Swallow the event

            # Space → always timestamp, regardless of which widget has focus
            if action == InputAction.SYNC:
                sync_page._on_sync()
                return True

        # Fall through to normal global-key handling
        if self.handle_global_key(event):
            return True
        return super().eventFilter(obj, event)

    # ── Close Event (draft warning) ─────────────────────────

    def closeEvent(self, event) -> None:
        """Intercept window close to clean up all session drafts."""
        if len(self.lrc_state.lyric) > 0:
            # Stop audio timer to prevent it from re-creating drafts
            self.audio_manager._timer.stop()
            # Block _save_state, then clear UI
            self._closing = True
            self.lrc_state.init_from_text("", self._trim_options)

        # Delete every draft this session ever touched
        self.config.cleanup_session_drafts()
        self._closing = True
        super().closeEvent(event)

    # ── App-level keyboard handler ──────────────────────────

    def handle_global_key(self, event: QKeyEvent) -> bool:
        """Handle keyboard events globally.

        Returns True if the event was handled.
        """
        action = self.keybinding_manager.get_matched_action(event)

        if action is None:
            return False

        # Audio source must be set for audio actions
        if action in (
            InputAction.SEEK_BACKWARD,
            InputAction.SEEK_FORWARD,
            InputAction.RESET_RATE,
            InputAction.INCREASE_RATE,
            InputAction.DECREASE_RATE,
            InputAction.TOGGLE_PLAY,
        ):
            if not self.audio_manager.src:
                return False

        # Rate uses log scale from web app: playbackRate ∈ [1/e, e]
        # rate_slider_value = ln(playbackRate)
        # playbackRate = exp(rate_slider_value)
        import math

        rate = self.audio_manager.playback_rate

        if action == InputAction.SEEK_BACKWARD:
            self.audio_manager.step(event.modifiers(), -5)
            return True
        elif action == InputAction.SEEK_FORWARD:
            self.audio_manager.step(event.modifiers(), 5)
            return True
        elif action == InputAction.RESET_RATE:
            self.audio_manager.playback_rate = 1.0
            if self.config.get_remember_playback_rate():
                self.config.set_last_playback_rate(1.0)
            return True
        elif action == InputAction.INCREASE_RATE:
            log_rate = math.log(rate)
            new_rate = math.exp(min(log_rate + 0.2, 1.0))
            self.audio_manager.playback_rate = new_rate
            if self.config.get_remember_playback_rate():
                self.config.set_last_playback_rate(new_rate)
            return True
        elif action == InputAction.DECREASE_RATE:
            log_rate = math.log(rate) if rate > 0 else 0
            new_rate = math.exp(max(log_rate - 0.2, -1.0))
            self.audio_manager.playback_rate = new_rate
            if self.config.get_remember_playback_rate():
                self.config.set_last_playback_rate(new_rate)
            return True
        elif action == InputAction.TOGGLE_PLAY:
            self.audio_manager.toggle()
            return True
        elif action == InputAction.SHOW_HELP:
            self.content_stack.set_page(PageRoute.HOME)
            return True
        elif action == InputAction.UNDO:
            self.lrc_state.undo()
            return True
        elif action == InputAction.REDO:
            self.lrc_state.redo()
            return True

        return False

    # ── Signal Wiring ───────────────────────────────────────

    def _connect_signals(self) -> None:
        # Header navigation
        self.header_bar.page_requested.connect(self.content_stack.set_page)

        # Audio manager -> footer + lrc_state + toast
        self.audio_manager.state_changed.connect(self._on_audio_state_changed)
        self.audio_manager.error_occurred.connect(self._on_audio_error)
        self.audio_manager.duration_changed.connect(self._on_duration_loaded)

        # LRC state changes -> save
        self.lrc_state.state_changed.connect(self._save_state)

        # Content stack notifies when synchronizer page is shown/hidden
        self.content_stack.sync_page_active_changed.connect(self._on_sync_page_changed)

    def _on_audio_state_changed(self, data: AudioStateData) -> None:
        self.footer_bar.update_audio_state(data)

    def _on_audio_error(self, message: str) -> None:
        self.toast_overlay.show_toast("warning", message)

    def _on_duration_loaded(self, duration: float) -> None:
        try:
            from ..core.lrc_parser import convert_time_to_tag
            self.lrc_state.set_info(
                "length",
                convert_time_to_tag(duration, self._format_options.fixed, False),
            )
            self.toast_overlay.show_toast("success", "音频已载入")
        except Exception:
            pass  # Prevent crash during audio metadata update

    def _on_sync_page_changed(self, active: bool) -> None:
        if active:
            # Connect audio time -> lrc refresh
            self.audio_manager.current_time_changed.connect(
                self.lrc_state.refresh
            )
        else:
            # Disconnect when leaving sync page
            try:
                self.audio_manager.current_time_changed.disconnect(
                    self.lrc_state.refresh
                )
            except TypeError:
                pass  # Not connected

    def _save_state(self) -> None:
        """Persist state to config when it changes."""
        if self._closing:
            return
        if self.config.get_remember_draft():
            text = self.lrc_state.stringify(self._format_options)
            self.config.set_lyric(text)
            self.config.set_select_index(self.lrc_state.select_index)

    # ── Public Helpers ──────────────────────────────────────

    def update_preferences(self, prefs: dict) -> None:
        """Apply preference changes to all components."""
        space_start = prefs.get("spaceStart", 1)
        space_end = prefs.get("spaceEnd", 0)
        self._trim_options = TrimOptions(
            trim_start=space_start >= 0,
            trim_end=space_end >= 0,
        )
        self._format_options = FormatOptions(
            space_start=space_start,
            space_end=space_end,
            fixed=prefs.get("fixed", 3),
            end_of_line="\r\n",
        )
        self.lrc_state.update_format_options(self._format_options)
        self.config.set_preferences(prefs)

        # Re-apply theme
        from .content_stack import apply_theme
        apply_theme(prefs)

    @property
    def format_options(self) -> FormatOptions:
        return self._format_options

    @property
    def trim_options(self) -> TrimOptions:
        return self._trim_options
