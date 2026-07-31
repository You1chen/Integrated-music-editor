"""Audio manager — wraps QMediaPlayer, replaces audiomodule.ts + audioRef.

Provides property accessors matching the web app's audioRef API and
pyqtSignals replacing the PubSub pattern.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import IntEnum, auto
from typing import Any, Optional

from PyQt6.QtCore import (
    QObject,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

from .lrc_parser import guard


class AudioState(IntEnum):
    """Types of audio state changes (mirrors web AudioActionType)."""
    PAUSE_CHANGED = 0
    DURATION_LOADED = 1
    RATE_CHANGED = 2


@dataclass
class AudioStateData:
    """Payload for audio state change signals."""
    type: AudioState
    payload: Any


class AudioManager(QObject):
    """QMediaPlayer wrapper providing the same API as the web audioRef.

    Signals replace the PubSub pattern (currentTimePubSub, audioStatePubSub).
    """

    current_time_changed = pyqtSignal(float)
    state_changed = pyqtSignal(AudioStateData)
    error_occurred = pyqtSignal(str)
    duration_changed = pyqtSignal(float)
    meta_data_changed = pyqtSignal()

    _MS_TO_SEC = 0.001
    _TIMER_INTERVAL = 16  # ~60fps, matches requestAnimationFrame

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer()
        self._output = QAudioOutput()
        self._player.setAudioOutput(self._output)

        # Timer for ~60fps current time updates during playback
        self._timer = QTimer()
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._emit_current_time)

        # Connect QMediaPlayer signals
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_error)
        self._player.metaDataChanged.connect(self.meta_data_changed.emit)

    # ── Property Accessors (match audioRef API) ────────────────

    @property
    def src(self) -> str:
        """Get the current audio source URL."""
        return self._player.source().toString()

    @property
    def local_path(self) -> str:
        """Get the current audio source as a local file path (empty if
        no audio is loaded or the source is not a local file)."""
        src = self.src
        if not src:
            return ""
        path = QUrl(src).toLocalFile()
        return path if path and os.path.isfile(path) else ""

    @property
    def duration(self) -> float:
        """Get audio duration in seconds."""
        return self._player.duration() * self._MS_TO_SEC

    @property
    def paused(self) -> bool:
        """Check if audio is paused or stopped."""
        return self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState

    @property
    def playback_rate(self) -> float:
        """Get playback rate (linear scale)."""
        return self._player.playbackRate()

    @playback_rate.setter
    def playback_rate(self, rate: float) -> None:
        """Set playback rate (linear scale)."""
        self._player.setPlaybackRate(rate)
        self.state_changed.emit(AudioStateData(
            type=AudioState.RATE_CHANGED,
            payload=rate,
        ))

    @property
    def current_time(self) -> float:
        """Get current playback position in seconds."""
        return self._player.position() * self._MS_TO_SEC

    @current_time.setter
    def current_time(self, time: float) -> None:
        """Seek to position in seconds."""
        if self._player.duration() > 0:
            self._player.setPosition(int(time * 1000))

    # ── Playback Control ───────────────────────────────────────

    def toggle(self) -> None:
        """Toggle between play and pause."""
        if self._player.duration() > 0:
            if self.paused:
                self._player.play()
            else:
                self._player.pause()

    def step(self, modifiers: Any, offset: float, target: Optional[float] = None) -> float:
        """Adjust playback position with modifier support.

        Ports audioRef.step() — supports Alt (×0.2) and Shift (×0.5) modifiers.

        Args:
            modifiers: Qt.KeyboardModifier flags or a dict with altKey/shiftKey.
            offset: Base offset amount (e.g., -5 or +5).
            target: Target position; defaults to current time.

        Returns:
            New current time after the step.
        """
        if target is None:
            target = self.current_time

        # Handle both Qt modifiers and dict-like modifiers
        alt = False
        shift = False
        if isinstance(modifiers, dict):
            alt = bool(modifiers.get('altKey', False))
            shift = bool(modifiers.get('shiftKey', False))
        else:
            # PyQt6 Qt.KeyboardModifier flags — supports & directly
            alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        if alt:
            offset *= 0.2
        if shift:
            offset *= 0.5

        new_time = guard(offset + target, 0, self.duration)
        self.current_time = new_time
        return new_time

    def set_source(self, url: str) -> None:
        """Set the audio source URL."""
        qurl = QUrl(url)
        self._player.setSource(qurl)

    @property
    def cover_image(self):
        """Try to extract embedded cover art from audio metadata.

        Returns a QPixmap on success, or None when no cover is embedded.
        Safe to call from any thread — catches all exceptions internally.
        """
        try:
            from PyQt6.QtMultimedia import QMediaMetaData
            meta = self._player.metaData()
            for key in (QMediaMetaData.Key.CoverArtImage,
                        QMediaMetaData.Key.ThumbnailImage):
                variant = meta.value(key)
                if variant.isValid():
                    from PyQt6.QtGui import QImage, QPixmap
                    img = variant.value(QImage)
                    if img and not img.isNull():
                        return QPixmap.fromImage(img)
        except Exception:
            pass
        return None

    # ── Internal Signal Handlers ───────────────────────────────

    def _emit_current_time(self) -> None:
        self.current_time_changed.emit(self.current_time)

    def _on_position_changed(self, _position: int) -> None:
        # Position changes handled by timer during playback;
        # emit once when paused (e.g., user seeking)
        if self.paused:
            self.current_time_changed.emit(self.current_time)

    def _on_duration_changed(self, duration_ms: int) -> None:
        duration = duration_ms * self._MS_TO_SEC
        self.duration_changed.emit(duration)
        self.state_changed.emit(AudioStateData(
            type=AudioState.DURATION_LOADED,
            payload=duration,
        ))

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._timer.start(self._TIMER_INTERVAL)
            self.state_changed.emit(AudioStateData(
                type=AudioState.PAUSE_CHANGED,
                payload=False,
            ))
        else:
            self._timer.stop()
            self.state_changed.emit(AudioStateData(
                type=AudioState.PAUSE_CHANGED,
                payload=True,
            ))

    def _on_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        # Map error codes to user-friendly messages (matching web app)
        error_messages = {
            QMediaPlayer.Error.ResourceError: "音频资源错误",
            QMediaPlayer.Error.FormatError: "不支持的音频格式",
            QMediaPlayer.Error.NetworkError: "音频网络错误",
            QMediaPlayer.Error.AccessDeniedError: "音频访问被拒绝",
        }
        msg = error_messages.get(error, error_string or "未知音频错误")
        self.error_occurred.emit(msg)
