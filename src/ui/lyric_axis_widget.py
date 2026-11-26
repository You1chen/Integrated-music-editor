"""Playback-driven bilingual typography on the home card."""

from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .lyric_card_style import CardPalette, mix

if TYPE_CHECKING:
    from .main_window import MainWindow


class _LyricRow(QPushButton):
    def __init__(self, text: str, translation: str) -> None:
        super().__init__()
        self.setObjectName("cardLyricRow")
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text or translation)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(4)
        self.original = self._label(text)
        self.translation = self._label(translation)
        layout.addWidget(self.original)
        layout.addWidget(self.translation)

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return label

    def set_mode(self, mode: str) -> None:
        has_translation = bool(self.translation.text())
        self.original.setVisible(mode != "translation" or not has_translation)
        self.translation.setVisible(mode != "original" and has_translation)

    def restyle(self, palette: CardPalette, active: bool) -> None:
        original = palette.text if active else mix(palette.text, palette.background, 0.30)
        translation = palette.secondary if active else mix(palette.secondary, palette.background, 0.20)
        self.original.setStyleSheet(
            f"color: {original.name()}; background: transparent; font-size: 22px; font-weight: 600;"
        )
        self.translation.setStyleSheet(
            f"color: {translation.name()}; background: transparent; font-size: 15px; font-weight: 400;"
        )
        tint = mix(palette.background, palette.accent, 0.24).name()
        self.setStyleSheet(
            "QPushButton#cardLyricRow { border: 1px solid transparent;"
            " border-radius: 10px; background: transparent; }"
            f"QPushButton#cardLyricRow:hover {{ border-color: {tint}; }}"
            + (f"QPushButton#cardLyricRow {{ border-left: 3px solid {palette.accent.name()}; }}" if active else "")
        )


class LyricAxisWidget(QScrollArea):
    """Scroll without seeking. Clicking a lyric seeks to its timestamp."""

    mode_changed = pyqtSignal()
    MODES = ("original", "translation", "bilingual")
    MODE_LABELS = {"original": "原文", "translation": "翻译", "bilingual": "双语"}

    def __init__(self, main_window: MainWindow, palette: CardPalette, mode: str = "bilingual") -> None:
        super().__init__()
        self._mw = main_window
        self._palette = palette
        self.mode = mode if mode in self.MODES else "bilingual"
        self.has_translation = False
        self._items: list[_LyricRow] = []
        self._timeline: list[tuple[float, int]] = []
        self._current_idx = -1

        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("cardLyrics")
        self.viewport().setAutoFillBackground(False)
        body = QWidget()
        body.setObjectName("cardLyricsBody")
        self.setStyleSheet("#cardLyrics, #cardLyricsBody { background: transparent; border: none; }")
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._top = QWidget()
        self._bottom = QWidget()
        self._empty = QLabel("暂无同步歌词")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._top)
        self._layout.addWidget(self._empty, stretch=1)
        self._layout.addWidget(self._bottom)
        self.setWidget(body)
        body.setAutoFillBackground(False)
        self._scroll = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._scroll.setDuration(210)
        self._scroll.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._align = QTimer(self)
        self._align.setSingleShot(True)
        self._align.timeout.connect(self._align_to_current)
        self._follow = QTimer(self)
        self._follow.setSingleShot(True)
        self._follow.timeout.connect(lambda: self._scroll_to_current(True))
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
        self._mw.lrc_state.document_changed.connect(self._rebuild)
        self._mw.audio_manager.current_time_changed.connect(self._on_time)
        self._rebuild()

    def set_card_palette(self, palette: CardPalette) -> None:
        self._palette = palette
        self._empty.setStyleSheet(f"color: {palette.secondary.name()}; background: transparent; font-size: 16px;")
        for index, row in enumerate(self._items):
            row.restyle(palette, index == self._current_idx)

    def _rebuild(self) -> None:
        self._scroll.stop()
        for item in self._items:
            self._layout.removeWidget(item)
            item.hide()
            item.deleteLater()
        self._items.clear()
        self._timeline.clear()
        self._current_idx = -1
        for line in self._mw.lrc_state.lyric:
            if line.time is None:
                continue
            row = _LyricRow(line.text, line.translation)
            row.clicked.connect(lambda checked=False, ts=line.time: self._seek(ts))
            self._timeline.append((line.time, len(self._items)))
            self._items.append(row)
            self._layout.insertWidget(self._layout.count() - 1, row)
        self._timeline.sort()
        self.has_translation = any(row.translation.text() for row in self._items)
        self._empty.setVisible(not self._items)
        self._update_spacers()
        self._apply_mode()
        self.set_card_palette(self._palette)
        self._align.start(0)

    def _seek(self, timestamp: float) -> None:
        self._mw.audio_manager.current_time = timestamp
        self._align.start(0)

    def cycle_mode(self) -> None:
        if not self.has_translation:
            return
        self.mode = self.MODES[(self.MODES.index(self.mode) + 1) % len(self.MODES)]
        self._apply_mode()
        self._align.start(0)

    def _apply_mode(self) -> None:
        for row in self._items:
            row.set_mode(self.mode)
        self.mode_changed.emit()

    def _index_at(self, seconds: float) -> int:
        slot = bisect_right(self._timeline, (seconds, len(self._items))) - 1
        return self._timeline[slot][1] if slot >= 0 else -1

    def _on_time(self, seconds: float, *, animate: bool = True) -> None:
        index = self._index_at(seconds)
        if index == self._current_idx:
            return
        if self._current_idx >= 0:
            self._items[self._current_idx].restyle(self._palette, False)
        self._current_idx = index
        if index >= 0:
            self._items[index].restyle(self._palette, True)
        if animate:
            self._follow.start(0)
        else:
            self._scroll_to_current(False)

    def _on_range_changed(self, _minimum: int, _maximum: int) -> None:
        # Newly inserted rows can receive their final geometry after the first timer.
        self._align.start(0)

    def _align_to_current(self) -> None:
        self._on_time(self._mw.audio_manager.current_time, animate=False)
        self._scroll_to_current(False)

    def _scroll_to_current(self, animate: bool) -> None:
        self._layout.activate()
        bar = self.verticalScrollBar()
        target = 0
        if self._items:
            row = self._items[max(0, self._current_idx)]
            target = max(0, min(bar.maximum(), row.y() + (row.height() - self.viewport().height()) // 2))
        self._scroll.stop()
        if animate and bar.value() != target:
            self._scroll.setStartValue(bar.value())
            self._scroll.setEndValue(target)
            self._scroll.start()
        else:
            bar.setValue(target)

    def wheelEvent(self, event) -> None:
        self._scroll.stop()
        super().wheelEvent(event)

    def _update_spacers(self) -> None:
        gutter = self.viewport().height() // 2 if self._items else 0
        self._top.setFixedHeight(gutter)
        self._bottom.setFixedHeight(gutter)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_spacers()
        self._align.start(0)
