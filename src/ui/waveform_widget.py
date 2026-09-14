"""Custom QPainter audio waveform widget with background envelope decoding."""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Optional

import numpy as np

from PyQt6.QtCore import (
    QMutex,
    QThread,
    Qt,
    QUrl,
    QWaitCondition,
    pyqtSignal,
)
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

WAVEFORM_RES = 1200


class _WaveformDecoder(QThread):
    """Decode audio files into peak envelopes off the GUI thread."""

    decoded = pyqtSignal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pending_path: str | None = None
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._stopping = False

    def request(self, path: str) -> None:
        """Queue *path* for decoding, replacing any still-pending request."""
        self._mutex.lock()
        self._pending_path = path
        self._wake.wakeOne()
        self._mutex.unlock()

    def stop_and_wait(self) -> None:
        """Stop the thread, letting an in-flight decode finish."""
        self._mutex.lock()
        self._stopping = True
        self._wake.wakeAll()
        self._mutex.unlock()
        self.wait(2000)

    def run(self) -> None:
        while True:
            self._mutex.lock()
            if self._stopping:
                self._mutex.unlock()
                return
            path = self._pending_path
            self._pending_path = None
            if path is None:
                self._wake.wait(self._mutex)
                if self._stopping:
                    self._mutex.unlock()
                    return
                path = self._pending_path
                self._pending_path = None
                if path is None:
                    self._mutex.unlock()
                    continue
            self._mutex.unlock()

            samples = self._decode_file(path)
            self.decoded.emit(path, samples)

    @staticmethod
    def _decode_file(local_path: str):
        """Read *local_path* into a normalized peak envelope, or None if it fails."""
        try:
            import soundfile as sf

            data, _samplerate = sf.read(local_path, always_2d=True, dtype="int16")
            if data.ndim > 1 and data.shape[1] > 1:
                mono = np.mean(data, axis=1)
            else:
                mono = data.flatten()

            target_size = WAVEFORM_RES
            if len(mono) > target_size:
                n_chunks = len(mono) // target_size
                reshaped = mono[:n_chunks * target_size].reshape(target_size, n_chunks)
                samples = np.max(np.abs(reshaped), axis=1)
            else:
                samples = np.abs(mono)

            max_val = np.max(samples)
            if max_val > 0:
                samples = samples / max_val
            return samples
        except Exception:
            return None


class WaveformWidget(QWidget):
    """Custom-painted audio waveform with progress overlay."""

    _CACHE_MAX = 6

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        self._samples: Optional[np.ndarray] = None
        self._duration: float = 0.0
        self._value: float = 0.0
        self._theme_color = QColor("#f58ea8")
        self._loaded_path: str = ""
        self._cache: "OrderedDict[str, np.ndarray]" = OrderedDict()

        self.setMinimumHeight(40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._decoder = _WaveformDecoder(self)
        self._decoder.decoded.connect(self._on_decoded)
        self._decoder.start()

        self._mw.audio_manager.duration_changed.connect(self._on_audio_loaded)
        self._mw.audio_manager.current_time_changed.connect(self._on_time_changed)

    def shutdown(self) -> None:
        """Stop the decoder thread so the app can exit cleanly."""
        self._decoder.stop_and_wait()

    def _cache_get(self, path: str):
        samples = self._cache.get(path)
        if samples is not None:
            self._cache.move_to_end(path)
        return samples

    def _cache_put(self, path: str, samples: np.ndarray) -> None:
        self._cache[path] = samples
        self._cache.move_to_end(path)
        while len(self._cache) > self._CACHE_MAX:
            self._cache.popitem(last=False)

    def _on_audio_loaded(self, duration: float) -> None:
        """Load the source's envelope from cache or request a background decode."""
        src = self._mw.audio_manager.src
        if not src:
            return

        local_path = QUrl(src).toLocalFile()
        if not local_path:
            return

        self._duration = duration
        if local_path == self._loaded_path:
            self.update()
            return

        cached = self._cache_get(local_path)
        if cached is not None:
            self._samples = cached
            self._loaded_path = local_path
            self.update()
            return

        self._decoder.request(local_path)

    def _on_decoded(self, local_path: str, samples) -> None:
        """Apply the decoded envelope from the worker thread, if it is current."""
        if samples is not None:
            self._cache_put(local_path, samples)
        if QUrl(self._mw.audio_manager.src).toLocalFile() != local_path:
            return
        self._samples = samples if samples is not None else None
        self._loaded_path = local_path
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

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        mid = h // 2

        _bg, _fg, theme_hex, _dark = get_theme_colors()
        theme = QColor(theme_hex)

        if self._samples is None or len(self._samples) == 0:
            painter.setPen(QPen(QColor(_fg), 1))
            painter.drawLine(0, mid, w, mid)
            painter.end()
            return

        num_samples = len(self._samples)
        x_scale = w / num_samples

        top_path = QPainterPath()
        top_path.moveTo(0, mid)
        for i, val in enumerate(self._samples):
            x = i * x_scale
            y = mid - val * (mid - 2)
            top_path.lineTo(x, y)
        top_path.lineTo(w, mid)

        bot_path = QPainterPath()
        bot_path.moveTo(0, mid)
        for i, val in enumerate(self._samples):
            x = i * x_scale
            y = mid + val * (mid - 2)
            bot_path.lineTo(x, y)
        bot_path.lineTo(w, mid)

        painter.setPen(Qt.PenStyle.NoPen)
        unplayed = QColor(_fg)
        unplayed.setAlpha(80)
        painter.setBrush(unplayed)
        painter.drawPath(top_path)
        painter.drawPath(bot_path)

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

            painter.setPen(QPen(theme, 2))
            painter.drawLine(progress_x, 0, progress_x, h)

        painter.end()

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
        """Repaint on resize; the fixed-resolution envelope needs no re-decode."""
        super().resizeEvent(event)
        self.update()
