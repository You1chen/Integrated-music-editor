"""Audio controls — custom player bar (three-zone layout).

Left   — cover placeholder + song info (title/subtitle) + like/comment/more.
Middle — transport row (mode · prev · big circular play · next · volume)
         + progress row (current time · timeline/waveform · rate chip · total).
Right  — lyrics-display toggle · playlist icon.

The same widget is used in the main footer and inside the expanded lyric
editor; shared audio/playlist state is wired internally so both instances
behave identically.
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core.audio_manager import AudioState, AudioStateData
from ..core.constants import PLAY_MODE_LABELS, PLAY_MODE_ORDER, PlayMode
from ..core.lrc_parser import Fixed, convert_time_to_tag, guard
from .content_stack import get_theme_colors

if TYPE_CHECKING:
    from PyQt6.QtGui import QMouseEvent

    from .main_window import MainWindow


# ── Playback-mode icons (shown on the mode button + its menu) ──
MODE_ICONS = {
    PlayMode.SINGLE: "▶",       # 单次播放 — play once
    PlayMode.SEQUENTIAL: "▶▶",  # 顺序播放 — play through in order
    PlayMode.LOOP: "↻",         # 循环播放 — repeat all
    PlayMode.SINGLE_LOOP: "↻¹", # 单曲循环 — repeat one
    PlayMode.SHUFFLE: "⇄",      # 随机播放 — shuffle
}


def _mode_icon_text(mode: PlayMode) -> str:
    """Return the icon glyph for a playback mode."""
    return MODE_ICONS.get(mode, "▶")


# ── Playback rate: the log-scale slider maps [-100, 100] → ln(rate) ∈ [-1, 1] ──
RATE_MIN = math.exp(-1.0)
RATE_MAX = math.exp(1.0)


def rate_to_slider(rate: float) -> int:
    """Map a linear rate to the slider integer (ln(rate) × 100)."""
    return int(round(math.log(rate) * 100.0))


def slider_to_rate(value: int) -> float:
    """Map a slider integer back to a linear rate."""
    return math.exp(value / 100.0)


class AudioControls(QWidget):
    """Custom audio player bar with all controls."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        self._build_left(layout)
        self._build_middle(layout)
        self._build_right(layout)

        # ── State ───────────────────────────────────────────
        self._duration = 0.0
        self._current_time = 0.0
        self._seeking = False
        self._rate = 1.0
        self._fixed: Fixed = 3
        self._paused = True
        self._waveform_visible = False

        # Timer for periodic UI refresh during playback
        self._ui_timer = QTimer(self)
        self._ui_timer.setInterval(50)  # 20fps for UI
        self._ui_timer.timeout.connect(self._update_display)

        # Single-shot timer to defer the rate reset so a double-click
        # (which also fires `clicked`) opens the dialog instead of resetting.
        self._rate_reset_timer = QTimer(self)
        self._rate_reset_timer.setSingleShot(True)
        self._rate_reset_timer.timeout.connect(self._on_rate_reset)

        # ── Internal wiring (receiver = self, auto-disconnects on destroy) ──
        # New audio loaded / metadata refreshed → cover + info + like state.
        self._mw.audio_manager.meta_data_changed.connect(self._refresh_cover)
        self._mw.audio_manager.duration_changed.connect(self._on_audio_reloaded)
        # Queue moves to another song (prev/next/click) → refresh text + like.
        self._mw.playlist.current_changed.connect(self._refresh_song_info)
        self._mw.playlist.current_changed.connect(self._refresh_like_state)
        self._mw.playlist.queue_changed.connect(self._refresh_like_state)
        # Like toggled in any instance (footer / expanded editor) → re-read.
        self._mw.liked_changed.connect(self._on_liked_changed)

        # ── Initial reflect (audio may already be loaded) ───
        self._duration = self._mw.audio_manager.duration
        self._rate = self._mw.audio_manager.playback_rate
        self._rate_btn.setText(f"×{self._rate:.2f}")
        self._update_time_display()
        self._refresh_cover()
        self._refresh_song_info()
        self._refresh_like_state()
        self._refresh_volume_icon()
        self._sync_lyric_toggle(self._mw.lyric_axis_visible())

    # ── Layout construction ─────────────────────────────────

    def _build_left(self, layout: QHBoxLayout) -> None:
        """Left zone: cover + (title / subtitle / status icons)."""
        left = QHBoxLayout()
        left.setSpacing(8)
        left.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Cover placeholder — a square frame; the embedded cover fills it
        # when available (image content is best-effort).
        self._cover = QLabel()
        self._cover.setObjectName("footerCover")
        self._cover.setFixedSize(52, 52)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self._cover)

        # Text column: two lines + a row of status icons below, vertically
        # centred so extra footer height is absorbed symmetrically.
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.addStretch(1)

        self._title_label = QLabel("未加载音频")
        self._title_label.setObjectName("songTitle")
        self._title_label.setFixedWidth(160)
        text_col.addWidget(self._title_label)

        self._subtitle_label = QLabel("")
        self._subtitle_label.setObjectName("songSubtitle")
        self._subtitle_label.setFixedWidth(160)
        text_col.addWidget(self._subtitle_label)

        icons = QHBoxLayout()
        icons.setSpacing(4)
        self._like_btn = QPushButton("♡")
        self._like_btn.setObjectName("statusBtn")
        self._like_btn.setCheckable(True)
        self._like_btn.setToolTip("喜欢")
        self._like_btn.setFixedSize(28, 24)
        self._like_btn.clicked.connect(self._on_like_clicked)
        icons.addWidget(self._like_btn)

        self._comment_label = QLabel("💬 0")
        self._comment_label.setObjectName("statusBtn")
        self._comment_label.setToolTip("评论（互动量，当前无数据源）")
        self._comment_label.setFixedWidth(52)
        icons.addWidget(self._comment_label)

        self._more_btn = QPushButton("⋯")
        self._more_btn.setObjectName("statusBtn")
        self._more_btn.setToolTip("更多操作")
        self._more_btn.setFixedSize(28, 24)
        self._more_btn.clicked.connect(self._on_more_clicked)
        icons.addWidget(self._more_btn)
        icons.addStretch()
        text_col.addLayout(icons)
        text_col.addStretch(1)

        left.addLayout(text_col)
        layout.addLayout(left)

    def _build_middle(self, layout: QHBoxLayout) -> None:
        """Middle zone: transport row (top) + progress row (bottom)."""
        middle = QVBoxLayout()
        middle.setSpacing(3)
        middle.addStretch(1)

        # ── Top: five transport icons, equally spaced ──
        top = QHBoxLayout()
        top.setSpacing(14)
        top.addStretch()

        self._mode_btn = QPushButton(_mode_icon_text(self._mw.playlist.mode))
        self._mode_btn.setObjectName("audioButton")
        self._mode_btn.setToolTip("播放模式")
        self._mode_btn.setFixedSize(34, 34)
        self._mode_btn.clicked.connect(self._on_mode_clicked)
        top.addWidget(self._mode_btn)

        self._replay_btn = QPushButton("⏮")
        self._replay_btn.setObjectName("audioButton")
        self._replay_btn.setToolTip("上一首")
        self._replay_btn.setFixedSize(34, 34)
        self._replay_btn.clicked.connect(self._on_prev)
        top.addWidget(self._replay_btn)

        # The big circular play key — visual anchor of the bar.
        self._play_btn = QPushButton("▶")
        self._play_btn.setObjectName("bigPlayBtn")
        self._play_btn.setToolTip("播放")
        self._play_btn.setFixedSize(46, 46)
        self._play_btn.clicked.connect(self._on_play_pause)
        top.addWidget(self._play_btn)

        self._forward_btn = QPushButton("⏭")
        self._forward_btn.setObjectName("audioButton")
        self._forward_btn.setToolTip("下一首")
        self._forward_btn.setFixedSize(34, 34)
        self._forward_btn.clicked.connect(self._on_next)
        top.addWidget(self._forward_btn)

        self._volume_btn = QPushButton("🔊")
        self._volume_btn.setObjectName("audioButton")
        self._volume_btn.setToolTip("音量")
        self._volume_btn.setFixedSize(34, 34)
        self._volume_btn.clicked.connect(self._on_volume_clicked)
        top.addWidget(self._volume_btn)

        top.addStretch()
        middle.addLayout(top)

        # ── Bottom: current time · timeline/waveform · rate · total ──
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        self._time_label = QLabel("00:00.000")
        self._time_label.setStyleSheet(
            "font-family: monospace; font-size: 12px; padding: 0 2px;"
        )
        bottom.addWidget(self._time_label)

        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setRange(0, 0)
        self._timeline.setSingleStep(1000)  # ms
        self._timeline.sliderPressed.connect(self._on_slider_pressed)
        self._timeline.sliderReleased.connect(self._on_slider_released)
        self._timeline.sliderMoved.connect(self._on_slider_moved)
        bottom.addWidget(self._timeline, stretch=1)

        from .waveform_widget import WaveformWidget
        self._waveform = WaveformWidget(self._mw)
        self._waveform.hide()
        bottom.addWidget(self._waveform, stretch=1)

        self._rate_btn = _RateButton("×1.00")
        self._rate_btn.setObjectName("audioButton")
        self._rate_btn.setToolTip("单击重置为 1.00 · 双击输入/调整播放速度")
        self._rate_btn.setFixedWidth(64)
        self._rate_btn.clicked.connect(self._on_rate_clicked)
        self._rate_btn.double_clicked.connect(self._open_rate_dialog)
        bottom.addWidget(self._rate_btn)

        self._duration_label = QLabel("00:00.000")
        self._duration_label.setStyleSheet(
            "font-family: monospace; font-size: 12px; padding: 0 2px;"
        )
        bottom.addWidget(self._duration_label)

        middle.addLayout(bottom)
        middle.addStretch(1)
        layout.addLayout(middle, stretch=1)

    def _build_right(self, layout: QHBoxLayout) -> None:
        """Right zone: mode/quality · lyrics toggle · playlist."""
        right = QHBoxLayout()
        right.setSpacing(8)
        right.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # ── Lyrics-display toggle (bound to the home lyric axis) ──
        self._lyric_btn = QPushButton()
        self._lyric_btn.setObjectName("lyricToggleBtn")
        self._lyric_btn.setCheckable(True)
        self._lyric_btn.setToolTip("显示/隐藏歌词轴")
        self._lyric_btn.setFixedHeight(30)
        self._lyric_btn.clicked.connect(self._on_lyric_toggle_clicked)
        right.addWidget(self._lyric_btn)

        # ── Playlist / queue ─────────────────────────────────
        self._playlist_btn = QPushButton("☰")
        self._playlist_btn.setObjectName("audioButton")
        self._playlist_btn.setToolTip("播放列表")
        self._playlist_btn.setFixedSize(34, 30)
        self._playlist_btn.clicked.connect(self._on_open_playlist)
        right.addWidget(self._playlist_btn)

        layout.addLayout(right)

    # ── Public API ──────────────────────────────────────────

    def update_state(self, data: AudioStateData) -> None:
        """Handle audio state changes from AudioManager."""
        if data.type == AudioState.PAUSE_CHANGED:
            self._paused = data.payload
            if self._paused:
                self._ui_timer.stop()
                self._play_btn.setText("▶")
            else:
                self._ui_timer.start()
                self._play_btn.setText("⏸")
            self._update_time_display()

        elif data.type == AudioState.DURATION_LOADED:
            self._duration = data.payload
            self._timeline.setRange(0, int(data.payload * 1000))
            self._update_time_display()
            # Apply waveform preference
            self._show_waveform(self._waveform_visible)

        elif data.type == AudioState.RATE_CHANGED:
            self._rate = data.payload
            self._rate_btn.setText(f"×{self._rate:.2f}")

    def set_waveform_visible(self, visible: bool) -> None:
        """Show/hide waveform widget."""
        self._waveform_visible = visible
        self._show_waveform(visible)

    def set_fixed(self, fixed: Fixed) -> None:
        """Update timestamp precision for time display."""
        self._fixed = fixed
        self._update_time_display()

    def refresh_fixed(self) -> None:
        """Re-read the timestamp precision from preferences.

        Bound-method slot for ``lrc_state.state_changed``: using a bound
        method (rather than a lambda) lets Qt auto-disconnect the signal
        when this controls widget is destroyed — e.g. the second instance
        inside the expanded lyric editor when its dialog closes.
        """
        self.set_fixed(self._mw.config.get_preferences().get("fixed", 3))

    def on_current_time_changed(self, time: float) -> None:
        """Handle current-time changes for seek-when-paused display updates.

        During playback the internal UI timer drives the display;
        this handler only fires when paused (e.g. user clicks a
        timestamp button or drags the timeline while paused).
        """
        if not self._paused:
            return
        self._current_time = time
        self._update_time_display()
        self._timeline.blockSignals(True)
        self._timeline.setValue(int(time * 1000))
        self._timeline.blockSignals(False)

    def update_mode_label(self, mode: PlayMode) -> None:
        """Refresh the mode icon button + its tooltip."""
        label = PLAY_MODE_LABELS.get(mode, PLAY_MODE_LABELS[PlayMode.SINGLE])
        self._mode_btn.setText(_mode_icon_text(mode))
        self._mode_btn.setToolTip(f"播放模式：{label}")

    def set_mode_lock(self, locked: bool) -> None:
        """Disable mode switching while lyrics are being edited."""
        self._mode_btn.setEnabled(not locked)

    def set_rate(self, rate: float) -> None:
        """Apply a playback rate and persist it if the config asks to."""
        self._mw.audio_manager.playback_rate = rate
        if self._mw.config.get_remember_playback_rate():
            self._mw.config.set_last_playback_rate(rate)

    # ── Left-zone handlers ──────────────────────────────────

    def _on_like_clicked(self) -> None:
        path = self._mw.audio_manager.local_path
        if not path:
            self._refresh_like_state()
            return
        cache = self._mw.config.get_playlist_cache()
        in_cache = any(
            s.get("path") == path for s in cache.get("songs", [])
        )
        if not in_cache:
            self._refresh_like_state()  # revert the visual toggle
            self._mw.toast_overlay.show_toast(
                "info", "仅在歌单中的歌曲可收藏"
            )
            return
        liked = self._mw.config.toggle_playlist_like(path)
        self._mw.liked_changed.emit(path, liked)
        self._refresh_like_state()

    def _on_liked_changed(self, path: str, _liked: bool) -> None:
        """Another instance toggled a like — re-read if it was this song."""
        if path == self._mw.audio_manager.local_path:
            self._refresh_like_state()

    def _on_more_clicked(self) -> None:
        from .song_info_dialog import SongInfoDialog
        song = self._mw.playlist.current_song
        path = self._mw.audio_manager.local_path
        if song is None and path:
            song = {
                "path": path,
                "title": os.path.splitext(os.path.basename(path))[0],
                "artist": "",
                "duration": self._duration,
            }
        if song and song.get("path"):
            SongInfoDialog(self._mw, song, self).exec()

    def _refresh_song_info(self, *_args) -> None:
        """Fill title/subtitle from the queue entry or the audio file."""
        song = self._mw.playlist.current_song
        path = self._mw.audio_manager.local_path
        if song:
            title = song.get("title") or os.path.splitext(
                os.path.basename(song["path"])
            )[0]
            subtitle = song.get("artist", "") or ""
        elif path:
            title = os.path.splitext(os.path.basename(path))[0]
            subtitle = ""
        else:
            title = "未加载音频"
            subtitle = ""
        self._set_elided(self._title_label, title)
        self._set_elided(self._subtitle_label, subtitle)
        self._more_btn.setEnabled(bool(path))

    def _refresh_like_state(self, *_args) -> None:
        """Sync the like button with the playlist-cache liked flag."""
        path = self._mw.audio_manager.local_path
        self._like_btn.setEnabled(bool(path))
        liked = False
        if path:
            cache = self._mw.config.get_playlist_cache()
            for s in cache.get("songs", []):
                if s.get("path") == path:
                    liked = bool(s.get("liked", False))
                    break
        self._like_btn.setChecked(liked)
        self._like_btn.setText("❤" if liked else "♡")

    def _refresh_cover(self, *_args) -> None:
        """Fill the cover frame with the embedded cover (or leave it empty)."""
        pixmap = self._mw.audio_manager.cover_image
        if pixmap is not None:
            scaled = pixmap.scaled(
                52, 52,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._cover.setPixmap(scaled)
        else:
            self._cover.setPixmap(QPixmap())

    def _on_audio_reloaded(self, _duration: float) -> None:
        """A new audio file was loaded — refresh everything audio-derived."""
        self._refresh_cover()
        self._refresh_song_info()
        self._refresh_like_state()
        self._refresh_volume_icon()

    # ── Middle-zone handlers ────────────────────────────────

    def _on_prev(self) -> None:
        self._mw.playlist.prev()

    def _on_next(self) -> None:
        self._mw.playlist.next()

    def _on_play_pause(self) -> None:
        self._mw.audio_manager.toggle()

    def _on_volume_clicked(self) -> None:
        popup = getattr(self, "_volume_popup", None)
        if popup is None:
            popup = _VolumePopup(self)
            self._volume_popup = popup
        if popup.isVisible():
            popup.close()
        else:
            popup._refresh_from_audio()
            popup._position_near(self._volume_btn)
            popup.show()

    def _refresh_volume_icon(self) -> None:
        muted = (
            self._mw.audio_manager.muted
            or self._mw.audio_manager.volume <= 0.001
        )
        self._volume_btn.setText("🔇" if muted else "🔊")

    # ── Right-zone handlers ─────────────────────────────────

    def _on_lyric_toggle_clicked(self) -> None:
        self._sync_lyric_toggle(self._mw.toggle_lyric_axis())

    def _sync_lyric_toggle(self, visible: bool) -> None:
        """Force the button's checked state to match the real axis state."""
        self._lyric_btn.blockSignals(True)
        self._lyric_btn.setChecked(visible)
        self._lyric_btn.blockSignals(False)
        self._lyric_btn.setText("歌词 开" if visible else "歌词 关")

    def _on_open_playlist(self) -> None:
        self._mw.open_playlist_panel()

    def _on_mode_clicked(self) -> None:
        """Open a menu (icon + label per mode) so the user picks freely."""
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)

        current = self._mw.playlist.mode
        for mode in PLAY_MODE_ORDER:
            action = menu.addAction(
                f"{_mode_icon_text(mode)}  {PLAY_MODE_LABELS[mode]}"
            )
            action.setCheckable(True)
            action.setChecked(mode == current)
            action.triggered.connect(
                lambda checked=False, m=mode: self._on_mode_selected(m)
            )
            group.addAction(action)

        menu.exec(self._mode_btn.mapToGlobal(
            self._mode_btn.rect().bottomLeft()
        ))

    def _on_mode_selected(self, mode: PlayMode) -> None:
        self._mw.set_play_mode(mode)

    # ── Rate ────────────────────────────────────────────────

    def _open_rate_dialog(self) -> None:
        """Open the rate-adjust dialog (double-click on the chip)."""
        self._rate_reset_timer.stop()  # a double-click shouldn't also reset
        dialog = _RateAdjustDialog(self)
        dialog.exec()

    def _on_rate_clicked(self) -> None:
        """Single click on the chip → reset, deferred to disambiguate
        from a double-click."""
        from PyQt6.QtWidgets import QApplication
        self._rate_reset_timer.start(QApplication.doubleClickInterval())

    def _on_rate_reset(self) -> None:
        """Reset playback rate to 1.0 (single-click on the chip)."""
        self.set_rate(1.0)

    # ── Timeline ────────────────────────────────────────────

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._seeking = False
        time = self._timeline.value() / 1000.0
        self._mw.audio_manager.current_time = time

    def _on_slider_moved(self, value: int) -> None:
        time = value / 1000.0
        self._current_time = time
        self._update_time_display()
        if self._waveform_visible:
            self._waveform.set_value(time)

    def _update_display(self) -> None:
        """Periodic update during playback."""
        if not self._seeking:
            self._current_time = self._mw.audio_manager.current_time
        self._update_time_display()

        if not self._seeking:
            self._timeline.blockSignals(True)
            self._timeline.setValue(int(self._current_time * 1000))
            self._timeline.blockSignals(False)

    def _update_time_display(self) -> None:
        """Update the current + total time labels."""
        cur = convert_time_to_tag(self._current_time, self._fixed, False)
        dur = (
            convert_time_to_tag(self._duration, self._fixed, False)
            if self._duration > 0
            else "00:00.000"
        )
        self._time_label.setText(cur)
        self._duration_label.setText(dur)

    def _show_waveform(self, visible: bool) -> None:
        """Toggle timeline vs waveform display."""
        if visible and self._duration > 0:
            self._timeline.hide()
            self._waveform.show()
        else:
            self._waveform.hide()
            self._timeline.show()

    # ── Helpers ─────────────────────────────────────────────

    def _set_elided(self, label: QLabel, text: str) -> None:
        """Set a label's text, elided to its fixed width."""
        fm = label.fontMetrics()
        elided = fm.elidedText(
            text or "", Qt.TextElideMode.ElideRight, label.width() - 2
        )
        label.setText(elided)


# ── Volume popup ──────────────────────────────────────────


class _VolumePopup(QDialog):
    """Frameless popup for live volume control (slider + mute toggle).

    One instance per ``AudioControls`` is created lazily and reused; it is
    re-positioned and re-synced to the audio state before every show.
    """

    def __init__(self, controls: "AudioControls") -> None:
        super().__init__(
            controls,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self._ac = controls
        self._mw = controls._mw
        self.setObjectName("volumePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self._mute_btn = QPushButton()
        self._mute_btn.setObjectName("audioButton")
        self._mute_btn.setToolTip("静音 / 取消静音")
        self._mute_btn.clicked.connect(self._on_mute_toggled)
        layout.addWidget(self._mute_btn)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setMinimumWidth(150)
        self._slider.setStyleSheet(_volume_slider_qss())
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider)

        self._value_label = QLabel()
        self._value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value_label.setStyleSheet(
            "font-family: monospace; font-size: 12px; color: gray;"
        )
        layout.addWidget(self._value_label)

        self._refresh_from_audio()

    def _refresh_from_audio(self) -> None:
        """Sync slider + mute label from the current audio state."""
        self._slider.blockSignals(True)
        self._slider.setValue(int(round(self._mw.audio_manager.volume * 100)))
        self._slider.blockSignals(False)
        self._refresh_mute_btn()
        self._refresh_value_label()

    def _position_near(self, anchor: QWidget) -> None:
        """Place just above *anchor*, clamped inside the screen geometry."""
        self.adjustSize()
        from PyQt6.QtWidgets import QApplication
        screen = (
            QApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
            or QApplication.primaryScreen()
        )
        geo = screen.availableGeometry()
        g = anchor.mapToGlobal(anchor.rect().bottomLeft())
        x = max(geo.left() + 4, min(g.x(), geo.right() - self.width() - 4))
        y = max(geo.top() + 4, min(g.y() - self.height() - 6, geo.bottom() - self.height() - 4))
        self.move(x, y)

    def _on_mute_toggled(self) -> None:
        self._mw.audio_manager.muted = not self._mw.audio_manager.muted
        self._refresh_mute_btn()
        self._ac._refresh_volume_icon()

    def _on_slider_changed(self, value: int) -> None:
        self._mw.audio_manager.volume = value / 100.0
        if self._mw.audio_manager.muted and value > 0:
            self._mw.audio_manager.muted = False
            self._refresh_mute_btn()
        self._refresh_value_label()
        self._ac._refresh_volume_icon()

    def _refresh_mute_btn(self) -> None:
        muted = self._mw.audio_manager.muted
        self._mute_btn.setText("🔇 静音" if muted else "🔊 未静音")

    def _refresh_value_label(self) -> None:
        self._value_label.setText(f"{self._slider.value()}%")


# ── Rate adjust dialog & slider support ──────────────────────────


class _RateButton(QPushButton):
    """Rate display button that reports double-clicks for the adjust dialog."""

    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.double_clicked.emit()
        event.accept()


class _RateAdjustDialog(QDialog):
    """Popup for comfortable playback-rate tuning.

    A lengthened slider (bigger handle) for smooth dragging, plus a
    precise text input with 0.01 steps. Changes apply live; Cancel
    restores the rate the dialog was opened with.
    """

    def __init__(self, audio_controls: "AudioControls") -> None:
        super().__init__(audio_controls._mw)
        self._ac = audio_controls
        self._orig_rate = audio_controls._rate

        self.setWindowTitle("调整播放速度")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # ── Lengthened slider (same log mapping as the footer slider) ──
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(-100, 100)
        self._slider.setValue(rate_to_slider(self._orig_rate))
        self._slider.setMinimumHeight(56)
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.setStyleSheet(_big_slider_qss())
        layout.addWidget(self._slider)

        # ── Precise value row ──
        row = QHBoxLayout()
        row.setSpacing(8)
        self._value_label = QLabel(f"×{self._orig_rate:.2f}")
        self._value_label.setStyleSheet(
            "font-family: monospace; font-size: 16px; font-weight: bold;"
        )
        self._edit = QLineEdit(f"{self._orig_rate:.2f}")
        self._edit.setFixedWidth(72)
        self._edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._edit.setToolTip(
            f"输入播放速度，精度 0.01（范围 {RATE_MIN:.2f} ~ {RATE_MAX:.2f}）"
        )
        self._edit.returnPressed.connect(self._on_edit_commit)
        hint = QLabel(f"范围 {RATE_MIN:.2f} ~ {RATE_MAX:.2f} · 精度 0.01")
        hint.setStyleSheet("font-size: 12px; color: gray;")
        row.addWidget(self._value_label)
        row.addWidget(self._edit)
        row.addWidget(hint)
        row.addStretch()
        layout.addLayout(row)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        reset_btn = QPushButton("重置为 1.00")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("确认")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self._edit.setFocus()
        self._edit.selectAll()

    # ── Handlers ──────────────────────────────────────────

    def _on_slider_changed(self, value: int) -> None:
        rate = slider_to_rate(value)
        self._ac.set_rate(rate)
        self._value_label.setText(f"×{rate:.2f}")
        self._edit.setText(f"{rate:.2f}")

    def _on_edit_commit(self) -> None:
        self._apply_from_edit()
        self.accept()

    def _on_ok(self) -> None:
        self._apply_from_edit()
        self.accept()

    def _on_reset(self) -> None:
        self._ac.set_rate(1.0)
        self._sync_to(1.0)

    def _apply_from_edit(self) -> None:
        try:
            rate = float(self._edit.text().strip())
        except ValueError:
            self._edit.setText(f"{self._ac._rate:.2f}")
            return
        rate = round(guard(rate, RATE_MIN, RATE_MAX), 2)
        self._ac.set_rate(rate)
        self._sync_to(rate)

    def _sync_to(self, rate: float) -> None:
        self._value_label.setText(f"×{rate:.2f}")
        self._edit.setText(f"{rate:.2f}")
        self._slider.blockSignals(True)
        self._slider.setValue(rate_to_slider(rate))
        self._slider.blockSignals(False)

    def reject(self) -> None:
        """Cancel: restore the rate the dialog was opened with."""
        self._ac.set_rate(self._orig_rate)
        super().reject()


def _volume_slider_qss() -> str:
    """QSS for the compact volume-popup slider (slim groove + small handle)."""
    _bg, fg, theme, _dark = get_theme_colors()
    return f"""
    QSlider::groove:horizontal {{
        height: 4px;
        background: {fg};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {theme};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
        background: {theme};
        border: 2px solid rgba(255, 255, 255, 0.4);
    }}
    """


def _big_slider_qss() -> str:
    """QSS for the enlarged slider: taller groove + bigger handle."""
    _bg, fg, theme, _dark = get_theme_colors()
    return f"""
    QSlider::groove:horizontal {{
        height: 8px;
        background: {fg};
        border-radius: 4px;
    }}
    QSlider::sub-page:horizontal {{
        background: {theme};
        border-radius: 4px;
    }}
    QSlider::handle:horizontal {{
        width: 28px;
        height: 28px;
        margin: -10px 0;
        border-radius: 14px;
        background: {theme};
    }}
    QSlider::handle:horizontal:hover {{
        background: {theme};
        border: 2px solid rgba(255, 255, 255, 0.4);
    }}
    """
