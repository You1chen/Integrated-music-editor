"""Cursor label — live timestamp display on the selected lyric line (replaces curser.tsx)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel

from ..core.audio_manager import AudioState, AudioStateData
from ..core.lrc_parser import Fixed, convert_time_to_tag

if TYPE_CHECKING:
    from .main_window import MainWindow


class CursorLabel(QLabel):
    """Shows the current audio time as a formatted timestamp.

    Follows the Nyquist–Shannon sampling theorem logic from curser.tsx:
    - When paused or high precision: updates on every time change
    - When playing: polls at 2*B Hz where B = [1, 10, 100, 1000][fixed] * rate
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window
        self._fixed: Fixed = 3
        self._paused = True
        self._rate = 1.0
        self._time = 0.0

        # Timer for polling (when not using signal-based updates)
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.timeout.connect(self._poll_time)

        # Connect audio signals
        self._mw.audio_manager.current_time_changed.connect(self._on_time_changed)
        self._mw.audio_manager.state_changed.connect(self._on_state_changed)

        self.setStyleSheet("font-family: monospace; font-size: 14px;")

        self._update_display()

    def set_fixed(self, fixed: Fixed) -> None:
        """Update the timestamp precision."""
        self._fixed = fixed
        self._update_display()
        self._update_timer_strategy()

    def _on_time_changed(self, time: float) -> None:
        self._time = time
        self._update_display()

    def _on_state_changed(self, data: AudioStateData) -> None:
        if data.type == AudioState.PAUSE_CHANGED:
            self._paused = data.payload
            self._update_timer_strategy()
        elif data.type == AudioState.RATE_CHANGED:
            self._rate = data.payload
            self._update_timer_strategy()

    def _poll_time(self) -> None:
        self._time = self._mw.audio_manager.current_time
        self._update_display()

    def _update_timer_strategy(self) -> None:
        """Determine whether to use signal or timer for updates.

        Ports the Nyquist–Shannon sampling logic from curser.tsx.
        """
        B = [1, 10, 100, 1000][self._fixed] * self._rate

        if self._paused or 2 * B > 60:
            # Use signal-based (already connected)
            self._poll_timer.stop()
        else:
            # Use timer polling at 2*B Hz
            interval = int(1000 / (2 * B))
            self._poll_timer.start(interval)

    def _update_display(self) -> None:
        self.setText(convert_time_to_tag(self._time, self._fixed) + " ▶")
