"""Audio controls — custom player bar (replaces audio.tsx).

Play/pause, ±5s skip, timeline slider, rate slider (log scale),
time display, and waveform.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QActionGroup
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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── Playback Mode Button (replaces 加载音频) ────
        self._mode_btn = QPushButton(PLAY_MODE_LABELS[PlayMode.SINGLE])
        self._mode_btn.setObjectName("audioButton")
        self._mode_btn.setToolTip("选择播放模式")
        self._mode_btn.setFixedHeight(32)
        self._mode_btn.clicked.connect(self._on_mode_clicked)
        layout.addWidget(self._mode_btn)

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
        self._rate_btn = _RateButton("×1.00")
        self._rate_btn.setObjectName("audioButton")
        self._rate_btn.setToolTip("单击重置为 1.00 · 双击输入/调整播放速度")
        self._rate_btn.setFixedWidth(60)
        self._rate_btn.clicked.connect(self._on_rate_clicked)
        self._rate_btn.double_clicked.connect(self._open_rate_dialog)
        layout.addWidget(self._rate_btn)

        # ── Rate Slider (log scale) ─────────────────────
        self._rate_slider = _RateSlider(Qt.Orientation.Horizontal)
        self._rate_slider.setRange(-100, 100)  # maps to ln(rate), -1 to 1
        self._rate_slider.setValue(0)
        self._rate_slider.setFixedWidth(100)
        self._rate_slider.valueChanged.connect(self._on_rate_changed)
        self._rate_slider.double_clicked.connect(self._open_rate_dialog)
        layout.addWidget(self._rate_slider)

        # ── Playlist Button (far right) ─────────────────
        self._playlist_btn = QPushButton("☰")
        self._playlist_btn.setObjectName("audioButton")
        self._playlist_btn.setToolTip("播放列表")
        self._playlist_btn.setFixedSize(36, 32)
        self._playlist_btn.clicked.connect(self._on_open_playlist)
        layout.addWidget(self._playlist_btn)

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

        # Single-shot timer to defer the rate reset so a double-click
        # (which also fires `clicked`) opens the dialog instead of resetting.
        self._rate_reset_timer = QTimer(self)
        self._rate_reset_timer.setSingleShot(True)
        self._rate_reset_timer.timeout.connect(self._on_rate_reset)

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

    def _on_open_playlist(self) -> None:
        self._mw.open_playlist_panel()

    def _on_mode_clicked(self) -> None:
        """Open a menu so the user picks a playback mode freely."""
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)

        current = self._mw.playlist.mode
        for mode in PLAY_MODE_ORDER:
            action = menu.addAction(PLAY_MODE_LABELS[mode])
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

    def update_mode_label(self, mode: PlayMode) -> None:
        """Refresh the mode button text after the mode changes."""
        self._mode_btn.setText(PLAY_MODE_LABELS.get(mode, PLAY_MODE_LABELS[PlayMode.SINGLE]))

    def set_mode_lock(self, locked: bool) -> None:
        """Disable mode switching while lyrics are being edited."""
        self._mode_btn.setEnabled(not locked)

    def _on_replay(self) -> None:
        self._mw.audio_manager.step({}, -5)

    def _on_forward(self) -> None:
        self._mw.audio_manager.step({}, 5)

    def _on_play_pause(self) -> None:
        self._mw.audio_manager.toggle()

    def set_rate(self, rate: float) -> None:
        """Apply a playback rate and persist it if the config asks to."""
        self._mw.audio_manager.playback_rate = rate
        if self._mw.config.get_remember_playback_rate():
            self._mw.config.set_last_playback_rate(rate)

    def _open_rate_dialog(self) -> None:
        """Open the rate-adjust dialog (double-click on display/slider)."""
        self._rate_reset_timer.stop()  # a double-click shouldn't also reset
        dialog = _RateAdjustDialog(self)
        dialog.exec()

    def _on_rate_clicked(self) -> None:
        """Single click on the display → reset, deferred to disambiguate
        from a double-click."""
        from PyQt6.QtWidgets import QApplication
        self._rate_reset_timer.start(QApplication.doubleClickInterval())

    def _on_rate_reset(self) -> None:
        """Reset playback rate to 1.0 (single-click on the display)."""
        self.set_rate(1.0)

    def _on_rate_changed(self, value: int) -> None:
        self.set_rate(slider_to_rate(value))

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


# ── Rate adjust dialog & slider support ──────────────────────────


class _RateSlider(QSlider):
    """QSlider that reports double-clicks so the rate dialog can open."""

    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.double_clicked.emit()
        event.accept()


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
        border: 2px solid #ffffff66;
    }}
    """
