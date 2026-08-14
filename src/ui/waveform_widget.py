"""Waveform widget — custom QPainter audio waveform visualization (replaces wavesurfer.js).

Draws waveform from decoded audio samples using numpy + soundfile.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QWidget

from .content_stack import get_theme_colors

if TYPE_CHECKING:
    from .main_window import MainWindow


class WaveformWidget(QWidget):
    """Custom-painted audio waveform.

    Reads audio data using soundfile, downsamples to widget width,
    and draws a filled waveform with progress overlay.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        self._samples: Optional[np.ndarray] = None  # Downsampled waveform data
        self._duration: float = 0.0
        self._value: float = 0.0  # Current time in seconds
        self._theme_color = QColor("#f58ea8")
        self._loaded_path: str = ""

        self.setMinimumHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # Listen for audio changes
        self._mw.audio_manager.duration_changed.connect(self._on_audio_loaded)
        self._mw.audio_manager.current_time_changed.connect(self._on_time_changed)

    def _on_audio_loaded(self, duration: float) -> None:
        """Load and decode audio for waveform display."""
        src = self._mw.audio_manager.src
        if not src:
            return

        # Only process local files
        local_path = QUrl(src).toLocalFile()
        if not local_path or local_path == self._loaded_path:
            self._duration = duration
            self.update()
            return

        try:
            import soundfile as sf
            data, samplerate = sf.read(local_path, always_2d=True)
            # Convert to mono by averaging channels
            if data.ndim > 1 and data.shape[1] > 1:
                mono = np.mean(data, axis=1)
            else:
                mono = data.flatten()

            # Downsample to widget width
            target_size = max(self.width(), 600)
            if len(mono) > target_size:
                # Take the max absolute value in each chunk (peak envelope)
                n_chunks = len(mono) // target_size
                reshaped = mono[:n_chunks * target_size].reshape(target_size, n_chunks)
                self._samples = np.max(np.abs(reshaped), axis=1)
            else:
                self._samples = np.abs(mono)

            # Normalize
            max_val = np.max(self._samples)
            if max_val > 0:
                self._samples = self._samples / max_val

            self._loaded_path = local_path
            self._duration = duration
            self.update()
        except Exception:
            self._samples = None
            self.update()

    def _on_time_changed(self, time: float) -> None:
        """Update cursor position."""
        self._value = time
        self.update()

    def set_value(self, time: float) -> None:
        """Set current time externally (e.g., from slider)."""
        self._value = time
        self.update()

    def set_theme_color(self, color: QColor) -> None:
        """Update the waveform progress color."""
        self._theme_color = color
        self.update()

    # ── Painting ───────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        mid = h // 2

        # Always read the live theme so the waveform follows the user's
        # theme color (and light/dark mode) at runtime.
        _bg, _fg, theme_hex, _dark = get_theme_colors()
        theme = QColor(theme_hex)

        if self._samples is None or len(self._samples) == 0:
            # Draw flat line
            painter.setPen(QPen(QColor(_fg), 1))
            painter.drawLine(0, mid, w, mid)
            painter.end()
            return

        # Build waveform polygon
        num_samples = len(self._samples)
        x_scale = w / num_samples

        # Top half path
        top_path = QPainterPath()
        top_path.moveTo(0, mid)
        for i, val in enumerate(self._samples):
            x = i * x_scale
            y = mid - val * (mid - 2)
            top_path.lineTo(x, y)
        top_path.lineTo(w, mid)

        # Bottom half path (mirror)
        bot_path = QPainterPath()
        bot_path.moveTo(0, mid)
        for i, val in enumerate(self._samples):
            x = i * x_scale
            y = mid + val * (mid - 2)
            bot_path.lineTo(x, y)
        bot_path.lineTo(w, mid)

        # Draw background waveform (unplayed → muted foreground)
        painter.setPen(Qt.PenStyle.NoPen)
        unplayed = QColor(_fg)
        unplayed.setAlpha(80)
        painter.setBrush(unplayed)
        painter.drawPath(top_path)
        painter.drawPath(bot_path)

        # Draw progress overlay (played → theme color)
        if self._duration > 0:
            progress_x = int((self._value / self._duration) * w) if self._duration > 0 else 0

            painter.save()
            painter.setClipRect(0, 0, progress_x, h)

            played = QColor(theme)
            played.setAlpha(150)
            painter.setBrush(played)
            painter.drawPath(top_path)
            painter.drawPath(bot_path)

            painter.restore()

            # Draw cursor line
            painter.setPen(QPen(theme, 2))
            painter.drawLine(progress_x, 0, progress_x, h)

        painter.end()

    # ── Mouse Interaction ──────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._seek(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._seek(event.position().x())

    def _seek(self, x: float) -> None:
        """Seek to position based on click x-coordinate."""
        if self._duration > 0:
            ratio = max(0, min(1, x / self.width()))
            time = ratio * self._duration
            self._mw.audio_manager.current_time = time

    def resizeEvent(self, event) -> None:
        """Re-decode audio when resized to match new width."""
        super().resizeEvent(event)
        if self._loaded_path:
            # Force re-load to re-downsample
            self._loaded_path = ""
            self._on_audio_loaded(self._duration)
