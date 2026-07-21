"""Audio controls — custom player bar (replaces audio.tsx).

Play/pause, ±5s skip, timeline slider, rate slider (log scale),
time display, and waveform.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from ..core.audio_manager import AudioState, AudioStateData
from ..core.lrc_parser import Fixed, convert_time_to_tag

if TYPE_CHECKING:
    from .main_window import MainWindow


class AudioControls(QWidget):
    """Custom audio player bar with all controls."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── Load Audio Button ───────────────────────────
        self._load_btn = QPushButton("🎵")
        self._load_btn.setObjectName("audioButton")
        self._load_btn.setToolTip("加载音频")
        self._load_btn.setFixedSize(36, 32)
        self._load_btn.clicked.connect(self._on_load_audio)
        layout.addWidget(self._load_btn)

        # ── Replay 5s ───────────────────────────────────
        self._replay_btn = QPushButton("⏮")
        self._replay_btn.setObjectName("audioButton")
        self._replay_btn.setToolTip("后退 5 秒")
        self._replay_btn.setFixedSize(36, 32)
        self._replay_btn.clicked.connect(self._on_replay)
        layout.addWidget(self._replay_btn)

        # ── Play/Pause ──────────────────────────────────
        self._play_btn = QPushButton("▶")
        self._play_btn.setObjectName("audioButton")
        self._play_btn.setToolTip("播放")
        self._play_btn.setFixedSize(36, 32)
        self._play_btn.clicked.connect(self._on_play_pause)
        layout.addWidget(self._play_btn)

        # ── Forward 5s ──────────────────────────────────
        self._forward_btn = QPushButton("⏭")
        self._forward_btn.setObjectName("audioButton")
        self._forward_btn.setToolTip("前进 5 秒")
        self._forward_btn.setFixedSize(36, 32)
        self._forward_btn.clicked.connect(self._on_forward)
        layout.addWidget(self._forward_btn)

        # ── Time Display ────────────────────────────────
        self._time_label = QLabel("00:00.000 / 00:00.000")
        self._time_label.setStyleSheet("font-family: monospace; font-size: 13px; padding: 0 6px;")
        layout.addWidget(self._time_label)

        # ── Timeline Slider ─────────────────────────────
        self._timeline = QSlider(Qt.Orientation.Horizontal)
        self._timeline.setRange(0, 0)
        self._timeline.setSingleStep(1000)  # ms
        self._timeline.sliderPressed.connect(self._on_slider_pressed)
        self._timeline.sliderReleased.connect(self._on_slider_released)
        self._timeline.sliderMoved.connect(self._on_slider_moved)
        layout.addWidget(self._timeline, stretch=2)

        # ── Waveform (hidden by default) ────────────────
        from .waveform_widget import WaveformWidget
        self._waveform = WaveformWidget(main_window)
        self._waveform.hide()
        layout.addWidget(self._waveform, stretch=3)

        # ── Rate Display ────────────────────────────────
        self._rate_btn = QPushButton("×1.00")
        self._rate_btn.setObjectName("audioButton")
        self._rate_btn.setToolTip("重置播放速度")
        self._rate_btn.setFixedWidth(60)
        self._rate_btn.clicked.connect(self._on_rate_reset)
        layout.addWidget(self._rate_btn)

        # ── Rate Slider (log scale) ─────────────────────
        self._rate_slider = QSlider(Qt.Orientation.Horizontal)
        self._rate_slider.setRange(-100, 100)  # maps to ln(rate), -1 to 1
        self._rate_slider.setValue(0)
        self._rate_slider.setFixedWidth(100)
        self._rate_slider.valueChanged.connect(self._on_rate_changed)
        layout.addWidget(self._rate_slider)

        # ── State ───────────────────────────────────────
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
            # Update slider without triggering signal
            log_rate = math.log(self._rate)
            slider_val = int(log_rate * 100)
            self._rate_slider.blockSignals(True)
            self._rate_slider.setValue(max(-100, min(100, slider_val)))
            self._rate_slider.blockSignals(False)

    def set_waveform_visible(self, visible: bool) -> None:
        """Show/hide waveform widget."""
        self._waveform_visible = visible
        self._show_waveform(visible)

    def set_fixed(self, fixed: Fixed) -> None:
        """Update timestamp precision for time display."""
        self._fixed = fixed
        self._update_time_display()

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

    # ── Internal Handlers ───────────────────────────────────

    def _on_load_audio(self) -> None:
        from .load_audio_dialog import LoadAudioDialog
        dialog = LoadAudioDialog(self._mw)
        dialog.exec()

    def _on_replay(self) -> None:
        self._mw.audio_manager.step({}, -5)

    def _on_forward(self) -> None:
        self._mw.audio_manager.step({}, 5)

    def _on_play_pause(self) -> None:
        self._mw.audio_manager.toggle()

    def _on_rate_reset(self) -> None:
        self._mw.audio_manager.playback_rate = 1.0
        if self._mw.config.get_remember_playback_rate():
            self._mw.config.set_last_playback_rate(1.0)

    def _on_rate_changed(self, value: int) -> None:
        log_rate = value / 100.0  # -1.0 to 1.0
        rate = math.exp(log_rate)
        self._mw.audio_manager.playback_rate = rate
        if self._mw.config.get_remember_playback_rate():
            self._mw.config.set_last_playback_rate(rate)

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
        """Update the time label."""
        cur = convert_time_to_tag(self._current_time, self._fixed, False)
        dur = (
            convert_time_to_tag(self._duration, self._fixed, False)
            if self._duration > 0
            else "00:00.000"
        )
        self._time_label.setText(f"{cur} / {dur}")

    def _show_waveform(self, visible: bool) -> None:
        """Toggle timeline vs waveform display."""
        if visible and self._duration > 0:
            self._timeline.hide()
            self._waveform.show()
        else:
            self._waveform.hide()
            self._timeline.show()
