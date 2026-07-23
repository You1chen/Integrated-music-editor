"""Scrollable lyrics axis — interactive, with display-mode toggle.

Each lyric line is a QPushButton subclass (like the timestamp button in
``synchronizer/_lyric_row.py``) so click and hover work reliably out of
the box.  A floating mode-toggle button in the top-right corner cycles
through 原文 → 翻译 → 双语 → 原文 …
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .content_stack import get_theme_colors

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Visual tweaks ──────────────────────────────────────────────────
SPACING = 4                 # px between items in the layout
FONT_ACTIVE_SIZE = 16       # current-line font size
FONT_NORMAL_SIZE = 13       # other lines
# ────────────────────────────────────────────────────────────────────


def _blend(hex_a: str, hex_b: str, weight: float) -> str:
    """Mix two hex colours.  *weight* 1.0 → pure *hex_a*, 0.0 → pure *hex_b*."""
    hex_a, hex_b = hex_a.lstrip("#"), hex_b.lstrip("#")
    ra, ga, ba = int(hex_a[0:2], 16), int(hex_a[2:4], 16), int(hex_a[4:6], 16)
    rb, gb, bb = int(hex_b[0:2], 16), int(hex_b[2:4], 16), int(hex_b[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(
        int(ra * weight + rb * (1 - weight)),
        int(ga * weight + gb * (1 - weight)),
        int(ba * weight + bb * (1 - weight)),
    )


class LyricItemWidget(QPushButton):
    """One lyric line — inherits QPushButton for reliable click + hover.

    Same pattern as ``_LyricRow._time_btn`` in the synchronizer.
    """

    timestamp_clicked = pyqtSignal(float)

    def __init__(
        self,
        timestamp: float | None,
        text: str,
        translation: str,
        mode: str = "original",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._timestamp = timestamp
        self._text = text or ""
        self._translation = translation or ""

        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Let the internal QVBoxLayout determine this button's height
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        # ── Theme-aware colours ───────────────────────────────
        _bg, _fg, _theme, _dark = get_theme_colors()
        # Hover: use theme colour at low opacity over bg
        hover_border = _blend(_theme, _bg, 0.22)
        hover_bg = _blend(_theme, _bg, 0.08)

        # ── Click → emit timestamp (native QPushButton.clicked) ──
        self.clicked.connect(self._emit_timestamp)

        # ── Hover: subtle border + highlight ────────────────────
        self.setStyleSheet(f"""
            LyricItemWidget {{
                border: 1px solid transparent;
                border-radius: 8px;
                text-align: left;
            }}
            LyricItemWidget:hover {{
                border: 1px solid {hover_border};
                background-color: {hover_bg};
            }}
        """)

        # ── Child labels ────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(1)

        self._orig_lbl = QLabel(self._text)
        self._orig_lbl.setWordWrap(True)
        self._orig_lbl.setFont(QFont("Microsoft YaHei", FONT_NORMAL_SIZE))
        self._orig_lbl.setStyleSheet(
            f"color: {_blend(_fg, _bg, 0.60)}; background: transparent;"
        )

        self._trans_lbl = QLabel(self._translation)
        self._trans_lbl.setWordWrap(True)
        self._trans_lbl.setFont(QFont("Microsoft YaHei", FONT_NORMAL_SIZE - 1))
        self._trans_lbl.setStyleSheet(
            f"color: {_blend(_fg, _bg, 0.38)}; background: transparent;"
        )

        layout.addWidget(self._orig_lbl)
        layout.addWidget(self._trans_lbl)

        self.set_mode(mode)

    # ── Internal ──────────────────────────────────────────────────

    def _emit_timestamp(self) -> None:
        if self._timestamp is not None:
            self.timestamp_clicked.emit(self._timestamp)

    # ── Size (let the internal layout decide) ──────────────────

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self.layout().minimumSize()

    # ── Public API ────────────────────────────────────────────────

    @property
    def timestamp(self) -> float | None:
        return self._timestamp

    def set_mode(self, mode: str) -> None:
        fallback = self._text
        if mode == "original":
            self._orig_lbl.show()
            self._trans_lbl.hide()
        elif mode == "translation":
            self._orig_lbl.hide()
            self._trans_lbl.setText(self._translation or fallback)
            self._trans_lbl.show()
        else:  # "bilingual"
            self._orig_lbl.show()
            self._trans_lbl.setText(self._translation or fallback)
            self._trans_lbl.show()

    def set_active(self, active: bool) -> None:
        # Always read current theme — user may have switched theme at runtime
        _bg, _fg, theme, _dark = get_theme_colors()
        if active:
            self._orig_lbl.setStyleSheet(
                f"color: {theme}; font-weight: bold;"
                f" font-size: {FONT_ACTIVE_SIZE}px; background: transparent;"
            )
            self._trans_lbl.setStyleSheet(
                f"color: {theme};"
                f" font-size: {FONT_ACTIVE_SIZE - 1}px; background: transparent;"
            )
        else:
            self._orig_lbl.setStyleSheet(
                f"color: {_blend(_fg, _bg, 0.60)}; font-weight: normal;"
                f" font-size: {FONT_NORMAL_SIZE}px; background: transparent;"
            )
            self._trans_lbl.setStyleSheet(
                f"color: {_blend(_fg, _bg, 0.38)}; font-weight: normal;"
                f" font-size: {FONT_NORMAL_SIZE - 1}px; background: transparent;"
            )


class LyricAxisWidget(QScrollArea):
    """Interactive, scrollable lyrics axis.

    Playback is the master — lyrics follow audio, never the reverse.
      - Audio hits a timestamp → matching item snaps to viewport centre.
      - Click a lyric → seek audio to that timestamp.
      - Scroll freely — scrolling never seeks; the next timestamp hit
        will pull the axis back to centre.
    """

    MODES = ["original", "translation", "bilingual"]
    MODE_LABELS = {"original": "原文", "translation": "翻译", "bilingual": "双语"}

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(parent=None)
        self._mw = main_window

        self._mode: str = "original"
        self._items: List[LyricItemWidget] = []
        self._current_idx: int = -1
        self._programmatic: bool = False
        self._user_set_mode: bool = False

        # ── Scroll area ──────────────────────────────────────
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)

        # Container
        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(SPACING)

        self._top_spacer = QWidget()
        self._layout.addWidget(self._top_spacer)

        self._empty_label = QLabel(
            "此音乐暂无 LRC\n\n"
            "你要去「歌词制作」页面创建吗？"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        _b, _f, _t, _d = get_theme_colors()
        self._empty_label.setStyleSheet(
            f"color: {_blend(_f, _b, 0.38)}; font-size: 15px; padding: 40px;"
        )
        self._layout.addWidget(self._empty_label)
        self._empty_label.hide()

        self._bottom_spacer = QWidget()
        self._layout.addWidget(self._bottom_spacer)

        self.setWidget(container)

        # ── Mode toggle button (floating) ────────────────────
        self._mode_btn = QPushButton(self.MODE_LABELS[self._mode], self)
        self._mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _b2, _f2, _t2, _d2 = get_theme_colors()
        btn_bg = _blend(_f2, _b2, 0.12)
        btn_bg_hover = _blend(_f2, _b2, 0.20)
        btn_border = _blend(_f2, _b2, 0.30)
        self._mode_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: {btn_bg}; color: {_blend(_f2, _b2, 0.70)};"
            f"  border: 1px solid {btn_border}; border-radius: 6px;"
            f"  padding: 3px 10px; font-size: 12px;"
            f"}}"
            f"QPushButton:hover {{ background: {btn_bg_hover}; }}"
        )
        self._mode_btn.clicked.connect(self._cycle_mode)

        self._mw.lrc_state.state_changed.connect(self._rebuild)
        self._mw.audio_manager.current_time_changed.connect(self._on_time)

        self._rebuild()

    # ── Public ─────────────────────────────────────────────────

    def has_lyrics(self) -> bool:
        return len(self._items) > 0

    # ── Rebuild ────────────────────────────────────────────────

    def _rebuild(self) -> None:
        self._programmatic = True
        try:
            for item in self._items:
                item.timestamp_clicked.disconnect()
                self._layout.removeWidget(item)
                item.deleteLater()
            self._items.clear()
            self._current_idx = -1

            for line in self._mw.lrc_state.lyric:
                if line.time is None:
                    continue
                item = LyricItemWidget(
                    timestamp=line.time,
                    text=line.text,
                    translation=line.translation,
                    mode=self._mode,
                )
                item.timestamp_clicked.connect(self._on_item_clicked)
                self._items.append(item)
                self._layout.insertWidget(
                    self._layout.count() - 1, item,
                )

            if not self._items:
                self._empty_label.show()
                self._mode_btn.hide()
                return

            self._empty_label.hide()
            self._mode_btn.show()

            has_trans = any(
                item._translation for item in self._items
            )
            if not has_trans:
                # No translations — always stay on original
                self._mode = "original"
                self._mode_btn.setText(self.MODE_LABELS[self._mode])
            elif not self._user_set_mode and self._mode == "original":
                # Translations exist and user hasn't toggled — auto bilingual
                self._mode = "bilingual"
                self._mode_btn.setText(self.MODE_LABELS[self._mode])
            for item in self._items:
                item.set_mode(self._mode)

            self._update_spacers()
            # Let the layout settle before the first scroll
            self.widget().updateGeometry()
        finally:
            self._programmatic = False

        # Defer so item heights are final when _scroll_to_item runs
        QTimer.singleShot(0, lambda: self._on_time(
            self._mw.audio_manager.current_time
        ))

    # ── Display mode ───────────────────────────────────────────

    def _cycle_mode(self) -> None:
        """Cycle: original → translation → bilingual → original.

        When no lyric has a translation the toggle is a no-op
        (switching to a translation view with nothing to show is
        confusing to the user).
        """
        has_trans = any(item._translation for item in self._items)
        if not has_trans:
            return  # nothing to translate — stay on original

        self._user_set_mode = True
        self._programmatic = True
        try:
            idx = self.MODES.index(self._mode)
            self._mode = self.MODES[(idx + 1) % len(self.MODES)]
            self._mode_btn.setText(self.MODE_LABELS[self._mode])
            for item in self._items:
                item.set_mode(self._mode)
            self._update_spacers()
            self.widget().updateGeometry()
        finally:
            self._programmatic = False
        QTimer.singleShot(0, lambda: self._on_time(
            self._mw.audio_manager.current_time
        ))

    # ── Snap-to-centre ─────────────────────────────────────────

    def _on_time(self, audio_time: float) -> None:
        idx = self._find_index(audio_time)
        if idx < 0 or idx >= len(self._items):
            return
        if idx == self._current_idx:
            return

        if 0 <= self._current_idx < len(self._items):
            self._items[self._current_idx].set_active(False)
        self._current_idx = idx
        self._items[idx].set_active(True)

        self._scroll_to_item(idx)

    def _find_index(self, audio_time: float) -> int:
        best = -1
        for i, item in enumerate(self._items):
            ts = item.timestamp
            if ts is not None and ts <= audio_time:
                best = i
        return best

    def _scroll_to_item(self, idx: int) -> None:
        item = self._items[idx]
        # item.pos() is already in container coords (parent of item == container).
        # mapTo(QPoint(0,0)) gives us the same result but is explicit.
        item_y = item.mapTo(self.widget(), QPoint(0, 0)).y()
        item_h = item.height()
        view_h = self.viewport().height()
        if view_h <= 0:
            return

        # Scroll so the item's vertical centre lands at the viewport centre
        target = item_y + item_h // 2 - view_h // 2
        vbar = self.verticalScrollBar()
        target = max(vbar.minimum(), min(vbar.maximum(), target))

        self._programmatic = True
        vbar.setValue(target)
        self._programmatic = False

    # ── User interaction ───────────────────────────────────────

    def _on_item_clicked(self, timestamp: float) -> None:
        """User clicked a lyric line → seek audio (the ONLY path that seeks)."""
        self._mw.audio_manager.current_time = timestamp

    # ── Spacers ────────────────────────────────────────────────

    def _update_spacers(self) -> None:
        vh = self.viewport().height()
        half = max(0, vh // 2)
        self._top_spacer.setFixedHeight(half)
        self._bottom_spacer.setFixedHeight(half)

    # ── Resize ─────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._programmatic = True
        try:
            self._update_spacers()
            btn_w = self._mode_btn.sizeHint().width()
            btn_h = self._mode_btn.sizeHint().height()
            self._mode_btn.setGeometry(
                self.viewport().width() - btn_w - 8, 8, btn_w, btn_h,
            )
            if 0 <= self._current_idx < len(self._items):
                self._scroll_to_item(self._current_idx)
        finally:
            self._programmatic = False
