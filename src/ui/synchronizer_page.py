"""Synchronizer page — the core lyrics timing tool (replaces synchronizer.tsx).

Displays lyrics lines with clickable timestamp buttons,
lets the user insert/remove timestamps while audio plays, using keyboard shortcuts.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.request
from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QKeyEvent, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import InputAction, SyncMode
from ..core.lrc_parser import (
    Fixed,
    LyricLine,
    convert_time_to_tag,
    format_text,
)
from .content_stack import is_dark_theme, get_theme_colors

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Helpers ────────────────────────────────────────────────────────────

def _contrast_for_theme(theme_color: str) -> str:
    """Return black or white text color that contrasts with the given theme color.

    Uses WCAG luminance check (same algorithm as content_stack._is_light_color).
    """
    hex_color = theme_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    def lum(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    l = 0.2126 * lum(r) + 0.7152 * lum(g) + 0.0722 * lum(b)
    con = l + 0.05
    return "#111111" if con * con > 0.0525 else "#eeeeee"


# ── Lyric Input Widget ──────────────────────────────────────────────

class _LyricInput(QPlainTextEdit):
    """Auto-resizing input for adding new lyrics.

    Enter       → emits submit_requested (submit and clear)
    Shift+Enter → inserts a literal newline
    Ctrl+Enter  → inserts a literal newline
    Ctrl+Z      → local text-undo when input has text;
                  propagates to parent (lyric undo) when input is empty

    Height grows with line count via sizeHint override.
    """

    submit_requested = pyqtSignal()

    _MAX_VISIBLE_LINES = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setPlaceholderText("输入歌词，Enter 提交…")
        # Never show internal scrollbars — height grows to fit content
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Auto-resize when content changes
        self.textChanged.connect(self._on_content_changed)
        # Defer initial sizing until widget is polished
        QTimer.singleShot(0, self._apply_height)

    # ── Height calculation ───────────────────────────────────

    def _calc_height(self) -> int:
        """Return the ideal height (px) for the current content.

        Uses QTextDocument.adjustSize() for accurate layout measurement
        rather than estimating from font metrics alone.
        """
        doc = self.document()
        doc.adjustSize()  # force layout so size() is accurate
        doc_h = doc.size().height()  # actual rendered content height

        fm = self.fontMetrics()
        line_h = fm.lineSpacing()
        frame_w = self.frameWidth()

        # Fallback: use line-count estimation when doc layout returns 0
        if doc_h <= 0:
            text = self.toPlainText()
            n = text.count("\n") + 1 if text.strip() else 1
            doc_margin = doc.documentMargin()
            doc_h = n * line_h + 2 * doc_margin

        # Content + padding (0.5 * line_h) + frame border
        target = int(doc_h + 0.5 * line_h + 2 * frame_w)

        min_h = int(1.5 * line_h) + 2 * frame_w
        max_h = int((self._MAX_VISIBLE_LINES + 0.5) * line_h) + 2 * frame_w
        return max(min_h, min(target, max_h))

    def _apply_height(self) -> None:
        """Set height constraints and notify parent layout."""
        h = self._calc_height()
        self.setMinimumHeight(h)
        self.setMaximumHeight(h)
        self.updateGeometry()

    def _on_content_changed(self) -> None:
        self._apply_height()

    # ── Override size hints so the parent layout sees our real height ──

    def sizeHint(self):
        base = super().sizeHint()
        return QSize(base.width(), self._calc_height())

    def minimumSizeHint(self):
        base = super().minimumSizeHint()
        return QSize(base.width(), self._calc_height())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        mods = event.modifiers()
        key = event.key()

        # Ctrl+Z → local undo when there's text; otherwise let parent handle lyric undo
        if mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Z:
            if not self.toPlainText().strip():
                event.ignore()  # propagate to parent for lyric undo
                return
            super().keyPressEvent(event)
            return

        # Enter / Return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                # Ctrl+Enter / Shift+Enter → insert newline
                super().keyPressEvent(event)
            else:
                # Plain Enter → submit
                self.submit_requested.emit()
            return

        super().keyPressEvent(event)


# ── Row Widget ──────────────────────────────────────────────────────

class _LyricRow(QFrame):
    """One row in the synchronizer: timestamp button + text display.

    Three display modes (managed via QStackedWidget):
    - View  (0): QLabel — default read-only display
    - Edit  (1): QLineEdit — inline text editing
    - Split (2): QTextEdit — inline split with blinking // markers

    Signals
    ------
    seek_requested(float)       — user clicked timestamp → seek audio
    edit_requested(int)         — user Ctrl+clicked timestamp → edit dialog
    row_clicked(int)            — user clicked the text area → select this line
    edit_lyric_requested(int)   — context menu "编辑" → triggers enter_edit_mode
    split_lyric_requested(int)  — context menu "拆分" → triggers enter_split_mode
    lyric_text_changed(int,str) — edit confirmed → update state
    lyric_split_done(int,str,list) — split confirmed (index, cleaned_text, positions)
    """

    # View-mode signals
    seek_requested = pyqtSignal(float)
    edit_requested = pyqtSignal(int)
    row_clicked = pyqtSignal(int)

    # Context menu → inline mode triggers
    edit_lyric_requested = pyqtSignal(int)
    split_lyric_requested = pyqtSignal(int)
    append_requested = pyqtSignal(int)

    # Result signals (emitted when inline editing/splitting is done)
    lyric_text_changed = pyqtSignal(int, str)
    lyric_split_done = pyqtSignal(int, str, list)

    def __init__(
        self,
        index: int,
        line: LyricLine,
        fixed: Fixed,
        space_start: int,
        space_end: int,
        theme_color: str,
        is_dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._line = line
        self._fixed = fixed
        self._space_start = space_start
        self._space_end = space_end
        self._theme_color = theme_color
        self._is_dark = is_dark
        self._selected = False
        self._at_current = False

        self.setFixedHeight(36)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 8, 2)
        layout.setSpacing(6)

        # ── Timestamp button ──────────────────────────
        self._time_btn = QPushButton()
        self._time_btn.setFixedWidth(105)
        self._time_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._time_btn.clicked.connect(self._on_time_clicked)
        layout.addWidget(self._time_btn)

        # ── Display stack (3 modes) ───────────────────
        self._display_stack = QStackedWidget()

        # [0] View: QLabel
        text = format_text(line.text, space_start, space_end)
        self._text_label = QLabel(text)
        self._text_label.setTextFormat(Qt.TextFormat.PlainText)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._display_stack.addWidget(self._text_label)  # index 0

        # [1] Edit: QLineEdit
        self._edit_input = QLineEdit()
        self._edit_input.returnPressed.connect(self._on_edit_confirm)
        self._edit_input.installEventFilter(self)
        self._display_stack.addWidget(self._edit_input)  # index 1

        # [2] Split: QTextEdit
        self._split_input = QTextEdit()
        self._split_input.setFixedHeight(44)
        self._split_input.setTabChangesFocus(True)
        self._split_input.textChanged.connect(self._on_split_text_changed)
        self._split_input.installEventFilter(self)
        self._display_stack.addWidget(self._split_input)  # index 2

        layout.addWidget(self._display_stack, stretch=1)

        # Split blink
        self._split_blink_timer = QTimer(self)
        self._split_blink_timer.timeout.connect(self._toggle_split_blink)
        self._split_blink_on = True

        # Initial render
        self._restyle()

    # ── Public API ────────────────────────────────────────

    @property
    def lyric_index(self) -> int:
        return self._index

    @property
    def is_editing(self) -> bool:
        """True when the row is in edit or split mode."""
        return self._display_stack.currentIndex() != 0

    def update_state(
        self,
        line: LyricLine,
        selected: bool,
        at_current: bool,
        fixed: Fixed,
        space_start: int,
        space_end: int,
        theme_color: str,
        is_dark: bool,
    ) -> None:
        """Update all display state at once (skips text if user is editing)."""
        self._line = line
        self._selected = selected
        self._at_current = at_current
        self._fixed = fixed
        self._space_start = space_start
        self._space_end = space_end
        self._theme_color = theme_color
        self._is_dark = is_dark

        # Update timestamp button
        if line.time is not None:
            tag = convert_time_to_tag(line.time, fixed)
            self._time_btn.setText(tag)
        else:
            self._time_btn.setText("[--:--.---]")

        # Only update text label when in view mode (not while user is editing)
        if self._display_stack.currentIndex() == 0:
            text = format_text(line.text, space_start, space_end)
            self._text_label.setText(text)

        self._restyle()

    # ── Mode: Enter / Exit ────────────────────────────────

    def enter_edit_mode(self) -> None:
        """Switch to inline edit mode."""
        self._edit_input.setText(self._line.text)
        self._display_stack.setCurrentIndex(1)
        self._edit_input.setFocus()
        self._edit_input.selectAll()

    def _exit_edit_mode(self, save: bool = True) -> None:
        """Leave edit mode, optionally saving changes."""
        if save:
            new_text = self._edit_input.text()
            if new_text != self._line.text:
                self.lyric_text_changed.emit(self._index, new_text)
        self._display_stack.setCurrentIndex(0)

    def enter_split_mode(self) -> None:
        """Switch to inline split mode (expands row for QTextEdit)."""
        self.setFixedHeight(58)
        self._split_input.setPlainText(self._line.text)
        self._display_stack.setCurrentIndex(2)
        self._split_input.setFocus()
        # Select all for convenience
        cursor = self._split_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        self._split_input.setTextCursor(cursor)
        self._split_blink_on = True
        self._split_blink_timer.start(500)
        self._apply_split_highlights()

    def _exit_split_mode(self) -> None:
        """Leave split mode and restore compact height."""
        self._split_blink_timer.stop()
        self._split_input.setExtraSelections([])
        self.setFixedHeight(36)
        self._display_stack.setCurrentIndex(0)

    # ── Edit Mode Handlers ────────────────────────────────

    def _on_edit_confirm(self) -> None:
        """Enter pressed in edit input."""
        self._exit_edit_mode(save=True)

    # ── Split Mode Handlers ───────────────────────────────

    def _on_split_text_changed(self) -> None:
        self._apply_split_highlights()

    def _toggle_split_blink(self) -> None:
        self._split_blink_on = not self._split_blink_on
        self._apply_split_highlights()

    @staticmethod
    def _find_markers(text: str) -> list[int]:
        """Return start positions of all '//' in *text*."""
        positions: list[int] = []
        idx = 0
        while True:
            idx = text.find("//", idx)
            if idx == -1:
                break
            positions.append(idx)
            idx += 2
        return positions

    def _apply_split_highlights(self) -> None:
        """Highlight '//' markers with blinking ExtraSelections."""
        if not self._split_blink_on:
            self._split_input.setExtraSelections([])
            return

        text = self._split_input.toPlainText()
        markers = self._find_markers(text)
        if not markers:
            self._split_input.setExtraSelections([])
            return

        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#ffeb3b"))
        fmt.setForeground(QColor("#f44336"))
        fmt.setFontWeight(QFont.Weight.Bold)

        extra_selections: list[QTextEdit.ExtraSelection] = []
        for pos in markers:
            sel = QTextEdit.ExtraSelection()
            sel.format = fmt
            sel.cursor = QTextCursor(self._split_input.document())
            sel.cursor.setPosition(pos)
            sel.cursor.setPosition(pos + 2, QTextCursor.MoveMode.KeepAnchor)
            extra_selections.append(sel)

        self._split_input.setExtraSelections(extra_selections)

    def _do_cursor_split(self) -> None:
        """Ctrl+Enter: split at current cursor position.

        Invalid cursor (at start/end) → silently exit split mode (no-op).
        """
        cursor = self._split_input.textCursor()
        pos = cursor.position()
        text = self._split_input.toPlainText()
        if 0 < pos < len(text):
            self.lyric_split_done.emit(self._index, text, [pos])
        self._exit_split_mode()

    def _do_marker_split(self) -> None:
        """Enter: split at all '//' marker positions.

        '//' markers are REMOVED entirely (not converted to '/').
        No valid markers → silently exit split mode (no-op).
        """
        text = self._split_input.toPlainText()
        markers = self._find_markers(text)

        # Filter: ignore markers at very start or end of text
        valid_markers = [p for p in markers if 0 < p < len(text) - 1]
        if not valid_markers:
            self._exit_split_mode()
            return

        # Remove "//" entirely from right to left
        cleaned = text
        for pos in reversed(markers):
            cleaned = cleaned[:pos] + cleaned[pos + 2:]

        # Compute split positions in cleaned text
        # Each removed "//" shifts subsequent positions left by 2
        split_positions: list[int] = []
        for i, pos in enumerate(markers):
            adjusted = pos - 2 * i
            if 0 < adjusted < len(cleaned):
                split_positions.append(adjusted)

        if not split_positions:
            self._exit_split_mode()
            return

        self.lyric_split_done.emit(self._index, cleaned, split_positions)
        self._exit_split_mode()

    # ── Event Filter (keyboard for editors) ────────────────

    def eventFilter(self, obj, event):
        """Intercept Escape / Enter in edit and split inputs."""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            if obj == self._edit_input:
                if event.key() == Qt.Key.Key_Escape:
                    self._exit_edit_mode(save=False)
                    return True
            elif obj == self._split_input:
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    self._exit_split_mode()
                    return True
                elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                        self._do_cursor_split()
                    else:
                        self._do_marker_split()
                    return True
        return super().eventFilter(obj, event)

    # ── Internal ──────────────────────────────────────────

    def _on_time_clicked(self) -> None:
        from PyQt6.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self.edit_requested.emit(self._index)
        elif self._line.time is not None:
            self.seek_requested.emit(self._line.time)

    def _restyle(self) -> None:
        """Apply QSS styling based on current state."""
        theme = self._theme_color
        bg = "transparent"
        border = "none"
        fg = "#eeeeee" if self._is_dark else "#111111"

        if self._selected and self._display_stack.currentIndex() == 0:
            bg = f"{theme}"
            border = f"1px solid {theme}"
        elif self._at_current and self._display_stack.currentIndex() == 0:
            bg = f"{theme}"

        self.setStyleSheet(
            f"_LyricRow {{ background-color: {bg}; border: {border}; border-radius: 4px; }}"
        )

        # Timestamp button style
        contrast = _contrast_for_theme(theme)
        if self._line.time is not None:
            # When selected, the row bg is theme-colored, so the button
            # (which is transparent) needs contrast text to be readable.
            btn_bg = f"{theme}" if not self._selected else "transparent"
            btn_text = contrast
            btn_style = (
                f"QPushButton {{"
                f"  background-color: {btn_bg};"
                f"  color: {btn_text};"
                f"  border: 1px solid {theme};"
                f"  border-radius: 4px;"
                f"  font-family: monospace;"
                f"  font-size: 12px;"
                f"  padding: 2px 4px;"
                f"}}"
                f"QPushButton:hover {{"
                f"  background-color: {theme};"
                f"  color: {contrast};"
                f"  border-color: {theme};"
                f"}}"
            )
        else:
            btn_style = (
                f"QPushButton {{"
                f"  background-color: transparent;"
                f"  color: {fg}44;"
                f"  border: 1px dashed {fg}22;"
                f"  border-radius: 4px;"
                f"  font-family: monospace;"
                f"  font-size: 12px;"
                f"  padding: 2px 4px;"
                f"}}"
                f"QPushButton:hover {{"
                f"  border-color: {theme};"
                f"  color: {theme};"
                f"}}"
            )
        self._time_btn.setStyleSheet(btn_style)

        # Text label style (only applies to view mode)
        self._text_label.setStyleSheet(
            f"QLabel {{ color: {fg}; font-size: 14px; background: transparent; border: none; }}"
        )

        # Edit input style
        self._edit_input.setStyleSheet(
            f"QLineEdit {{"
            f"  color: {fg}; font-size: 14px; background: transparent;"
            f"  border: 1px solid {theme}; border-radius: 3px; padding: 2px 6px;"
            f"}}"
        )

        # Split input style
        self._split_input.setStyleSheet(
            f"QTextEdit {{"
            f"  color: {fg}; font-size: 13px; background: transparent;"
            f"  border: 1px solid {theme}; border-radius: 3px; padding: 2px 6px;"
            f"}}"
        )

    def mousePressEvent(self, event) -> None:
        """Clicking on the text area (not the button) selects the row.
        Ctrl+click on text area appends an empty line below.
        """
        if not self._time_btn.geometry().contains(event.pos()):
            from PyQt6.QtWidgets import QApplication
            modifiers = QApplication.keyboardModifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                self.append_requested.emit(self._index)
            else:
                self.row_clicked.emit(self._index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click on the text area enters edit mode."""
        if not self._time_btn.geometry().contains(event.pos()):
            self.edit_lyric_requested.emit(self._index)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Right-click context menu: edit / split / append."""
        from PyQt6.QtWidgets import QMenu

        # Auto-select this row on right-click
        self.row_clicked.emit(self._index)

        menu = QMenu(self)

        edit_action = menu.addAction("✏️ 编辑")
        split_action = menu.addAction("✂️ 拆分")
        append_action = menu.addAction("📝 追加")

        edit_action.triggered.connect(
            lambda: self.edit_lyric_requested.emit(self._index)
        )
        split_action.triggered.connect(
            lambda: self.split_lyric_requested.emit(self._index)
        )
        append_action.triggered.connect(
            lambda: self.append_requested.emit(self._index)
        )

        menu.exec(event.globalPos())


# ── Translation Row Widget ───────────────────────────────────────────

class _TranslationRow(QFrame):
    """A translation editing row below a lyric row.

    Has an empty 105px placeholder (matching timestamp button width)
    and a QLineEdit for editing the translation text.

    Signals
    ------
    translation_changed(int, str) — index + new text (per-keystroke)
    translation_finished(int)      — user finished editing (Enter / focus loss)
    row_clicked(int)               — user clicked empty area → select parent line
    """

    translation_changed = pyqtSignal(int, str)
    translation_finished = pyqtSignal(int)
    row_clicked = pyqtSignal(int)

    def __init__(
        self,
        index: int,
        line: LyricLine,
        theme_color: str,
        is_dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._line = line
        self._theme_color = theme_color
        self._is_dark = is_dark

        self.setFixedHeight(34)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 8, 0)
        layout.setSpacing(6)

        # ── 105px empty placeholder (aligns with timestamp button) ──
        self._empty_placeholder = QLabel()
        self._empty_placeholder.setFixedWidth(105)
        layout.addWidget(self._empty_placeholder)

        # ── Editable translation field ──
        self._trans_edit = QLineEdit()
        self._trans_edit.setPlaceholderText("输入翻译…")
        self._trans_edit.setText(line.translation)
        self._trans_edit.textChanged.connect(self._on_text_changed)
        self._trans_edit.editingFinished.connect(self._on_editing_finished)
        layout.addWidget(self._trans_edit, stretch=1)

        self._restyle()

    @property
    def lyric_index(self) -> int:
        return self._index

    def update_state(self, line: LyricLine, theme_color: str, is_dark: bool) -> None:
        """Update translation text from state (skips if user is editing)."""
        self._line = line
        self._theme_color = theme_color
        self._is_dark = is_dark
        if not self._trans_edit.hasFocus():
            self._trans_edit.setText(line.translation)
        self._restyle()

    def _on_text_changed(self, text: str) -> None:
        self.translation_changed.emit(self._index, text)

    def _on_editing_finished(self) -> None:
        """User pressed Enter or left the field — push undo snapshot."""
        self.translation_finished.emit(self._index)

    def _restyle(self) -> None:
        fg = "#eeeeee" if self._is_dark else "#111111"
        theme = self._theme_color

        self.setStyleSheet(
            f"_TranslationRow {{"
            f"  background-color: transparent;"
            f"  border-left: 3px solid {theme};"
            f"}}"
        )

        self._trans_edit.setStyleSheet(
            f"QLineEdit {{"
            f"  color: {theme};"
            f"  background: transparent;"
            f"  border: none;"
            f"  border-bottom: 1px dashed {fg}22;"
            f"  font-size: 13px;"
            f"  font-style: italic;"
            f"  padding: 2px 4px;"
            f"}}"
            f"QLineEdit:hover {{"
            f"  border-bottom-color: {theme};"
            f"}}"
            f"QLineEdit:focus {{"
            f"  border-bottom-color: {theme};"
            f"  border-bottom-width: 2px;"
            f"}}"
        )

    def mousePressEvent(self, event) -> None:
        """Clicking empty area selects the parent lyric row."""
        if not self._trans_edit.geometry().contains(event.pos()):
            self.row_clicked.emit(self._index)
        super().mousePressEvent(event)


# ── Synchronizer Page ────────────────────────────────────────────────


class SynchronizerPage(QWidget):
    """Core timing tool: shows lyric lines with timestamp buttons,
    handles keyboard input for timestamps.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ───────────────────────────────────
        self._toolbar = self._create_toolbar()
        layout.addLayout(self._toolbar)

        # ── Lyric input box (top of lyrics list) ──────
        self._lyric_input = _LyricInput(self)
        self._lyric_input.submit_requested.connect(self._on_lyric_input_submit)
        self._lyric_input.hide()
        layout.addWidget(self._lyric_input)

        # ── Scroll area for lyric rows ────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._rows_container = QWidget()
        self._rows_container.setObjectName("lyricList")
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(1)
        self._rows_layout.addStretch()

        self._scroll.setWidget(self._rows_container)
        layout.addWidget(self._scroll, stretch=1)

        # ── Detect background clicks for deselection ──
        self._scroll.viewport().installEventFilter(self)

        # ── Space Button (optional, absolute-positioned) ─
        self._space_btn: QPushButton | None = None

        # ── Row widgets cache ─────────────────────────
        self._rows: list[_LyricRow] = []
        self._trans_rows: list[_TranslationRow] = []

        # ── State ─────────────────────────────────────
        self._suppress_refresh = False
        self._translation_mode = False
        self._append_target_index: int | None = None  # row index for append-after mode

        # Connect state changes
        self._mw.lrc_state.state_changed.connect(self._refresh_rows)

        # Initial render
        self._rebuild_all()

    # ── Toolbar ─────────────────────────────────────────────

    def _create_toolbar(self) -> QHBoxLayout:
        """Build the top toolbar with action buttons and mode toggle."""
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(6)

        # Translate toggle button
        self._btn_translate = QPushButton("翻译")
        self._btn_translate.setToolTip("切换翻译编辑模式")
        self._btn_translate.setCheckable(True)
        self._btn_translate.setChecked(False)
        self._btn_translate.clicked.connect(self._on_translate_toggle)
        toolbar.addWidget(self._btn_translate)

        # Pattern match button (only visible in translation mode)
        self._btn_pattern_match = QPushButton("模式匹配")
        self._btn_pattern_match.setToolTip("从粘贴的翻译文本中匹配翻译")
        self._btn_pattern_match.clicked.connect(self._on_pattern_match)
        self._btn_pattern_match.hide()
        toolbar.addWidget(self._btn_pattern_match)

        # Import button
        self._btn_import = QPushButton("导入")
        self._btn_import.setToolTip("导入 LRC 文件")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        # Export button
        self._btn_export = QPushButton("导出")
        self._btn_export.setToolTip("导出 LRC 文件")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        # Edit text button
        self._btn_edit = QPushButton("编辑")
        self._btn_edit.setToolTip("直接编辑歌词文本")
        self._btn_edit.clicked.connect(self._on_edit_text)
        toolbar.addWidget(self._btn_edit)

        # Save button — overwrite source file
        self._btn_save = QPushButton("保存")
        self._btn_save.setToolTip("保存并覆写源 LRC 文件")
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        # Preview button — read-only output preview
        self._btn_preview = QPushButton("预览")
        self._btn_preview.setToolTip("预览 LRC 输出效果")
        self._btn_preview.clicked.connect(self._on_preview)
        toolbar.addWidget(self._btn_preview)

        toolbar.addStretch()

        return toolbar

    # ── Public API ──────────────────────────────────────────

    def set_space_button_visible(self, visible: bool) -> None:
        """Show/hide the on-screen space button (from preferences)."""
        if visible:
            if self._space_btn is None:
                self._space_btn = QPushButton("空格", self)
                self._space_btn.setFixedSize(100, 100)
                self._space_btn.clicked.connect(self._on_sync)
                self._reposition_space_button()
            self._restyle_space_button()
            self._space_btn.show()
        else:
            if self._space_btn:
                self._space_btn.hide()

    def _restyle_space_button(self) -> None:
        """Apply theme-aware styling to the on-screen space button."""
        if self._space_btn is None:
            return
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        contrast = _contrast_for_theme(theme_color)
        self._space_btn.setStyleSheet(
            f"QPushButton {{"
            f"  color: {contrast}; background-color: {theme_color};"
            f"  border: none; border-radius: 50px;"
            f"  font-size: 14px; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  border: 2px solid {contrast};"
            f"}}"
        )

    # ── Toolbar Handlers ────────────────────────────────────

    def _on_translate_toggle(self) -> None:
        """Toggle translation editing mode on/off."""
        self._translation_mode = self._btn_translate.isChecked()
        self._btn_pattern_match.setVisible(self._translation_mode)
        self._rebuild_all()

    def _on_translation_changed(self, index: int, text: str) -> None:
        """Live update translation text (no undo push — per-keystroke)."""
        state = self._mw.lrc_state
        if 0 <= index < len(state.lyric):
            state.lyric[index].translation = text
        # Emit state_changed so draft is auto-saved and UI stays fresh
        state.state_changed.emit()

    def _on_translation_finished(self, index: int) -> None:
        """User finished editing (Enter / focus loss) — push one undo snapshot."""
        self._mw.lrc_state._push_undo()

    def _on_ai_assist(
        self, target_text_edit: QPlainTextEdit | None = None
    ) -> None:
        """Open the AI assist dialog with two options for translation help.

        When *target_text_edit* is provided, API auto results fill that
        widget directly instead of opening a new pattern-match dialog.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("AI 辅助翻译")
        dialog.resize(500, 400)
        dialog.setMinimumSize(420, 320)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(16, 16, 16, 16)
        dlg_layout.setSpacing(12)

        # Title
        title = QLabel("AI 辅助翻译")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dlg_layout.addWidget(title)

        dlg_layout.addSpacing(4)

        # Stacked widget for pages
        stack = QStackedWidget()
        dlg_layout.addWidget(stack, stretch=1)

        # ── Page 0: Two option buttons ──
        options_page = QWidget()
        options_layout = QVBoxLayout(options_page)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(12)
        options_layout.addStretch()

        btn_chat = QPushButton("模型聊天网站")
        btn_chat.setStyleSheet(
            "QPushButton {"
            "  font-size: 15px; padding: 16px; border: 2px solid #aaa;"
            "  border-radius: 8px; text-align: left;"
            "}"
            "QPushButton:hover {"
            "  border-color: #58a6ff; background-color: rgba(88,166,255,0.1);"
            "}"
        )
        btn_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_chat.clicked.connect(lambda: stack.setCurrentIndex(1))
        options_layout.addWidget(btn_chat)

        btn_api = QPushButton("API 自动")
        btn_api.setStyleSheet(
            "QPushButton {"
            "  font-size: 15px; padding: 16px; border: 2px solid #aaa;"
            "  border-radius: 8px; text-align: left;"
            "}"
            "QPushButton:hover {"
            "  border-color: #58a6ff; background-color: rgba(88,166,255,0.1);"
            "}"
        )
        btn_api.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_api.clicked.connect(lambda: stack.setCurrentIndex(2))
        options_layout.addWidget(btn_api)

        options_layout.addStretch()
        stack.addWidget(options_page)  # index 0

        # ── Page 1: Model chat website ──
        chat_page = QWidget()
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(12)

        btn_back = QPushButton("← 返回")
        btn_back.setFlat(True)
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.clicked.connect(lambda: stack.setCurrentIndex(0))
        chat_layout.addWidget(btn_back)

        chat_layout.addSpacing(4)

        btn_copy_prompt = QPushButton("📋  生成并复制提示词")
        btn_copy_prompt.setStyleSheet(
            "QPushButton {"
            "  font-size: 14px; padding: 12px; border: 2px solid #58a6ff;"
            "  border-radius: 8px; color: #58a6ff; font-weight: bold;"
            "}"
            "QPushButton:hover { background-color: rgba(88,166,255,0.15); }"
        )
        btn_copy_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_copy_prompt.clicked.connect(lambda: self._generate_and_copy_prompt(dialog))
        chat_layout.addWidget(btn_copy_prompt)

        # Separator
        sep = QLabel("— AI 聊天网站 —")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep.setStyleSheet("color: #888; font-size: 12px; margin-top: 8px;")
        chat_layout.addWidget(sep)

        # Links section
        links_widget = QWidget()
        links_layout = QVBoxLayout(links_widget)
        links_layout.setContentsMargins(8, 0, 8, 0)
        links_layout.setSpacing(8)

        deepseek_link = QPushButton("🔗  DeepSeek Chat → chat.deepseek.com")
        deepseek_link.setFlat(True)
        deepseek_link.setCursor(Qt.CursorShape.PointingHandCursor)
        deepseek_link.setStyleSheet(
            "QPushButton {"
            "  font-size: 13px; padding: 8px; color: #58a6ff; text-align: left;"
            "}"
            "QPushButton:hover { text-decoration: underline; color: #79c0ff; }"
        )
        deepseek_link.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://chat.deepseek.com/"))
        )
        links_layout.addWidget(deepseek_link)

        kimi_link = QPushButton("🔗  Kimi Chat → kimi.com")
        kimi_link.setFlat(True)
        kimi_link.setCursor(Qt.CursorShape.PointingHandCursor)
        kimi_link.setStyleSheet(
            "QPushButton {"
            "  font-size: 13px; padding: 8px; color: #58a6ff; text-align: left;"
            "}"
            "QPushButton:hover { text-decoration: underline; color: #79c0ff; }"
        )
        kimi_link.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://www.kimi.com/"))
        )
        links_layout.addWidget(kimi_link)

        chat_layout.addWidget(links_widget)
        chat_layout.addStretch()
        stack.addWidget(chat_page)  # index 1

        # ── Page 2: API auto ──
        api_page = QWidget()
        api_layout = QVBoxLayout(api_page)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.setSpacing(12)

        btn_back2 = QPushButton("← 返回")
        btn_back2.setFlat(True)
        btn_back2.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back2.clicked.connect(lambda: stack.setCurrentIndex(0))
        api_layout.addWidget(btn_back2)

        # Sub-stack: model list (0) vs config form (1)
        api_substack = QStackedWidget()
        api_layout.addWidget(api_substack, stretch=1)
        api_layout.addStretch()

        # ── Helper: do the actual translation ──
        def _do_translate(cfg: dict) -> None:
            """Build prompt, call AI API in background, show result in a popup.

            The API auto step only does: prompt → API call → result dialog.
            It does NOT touch pattern matching — the user decides whether to
            copy the result or fill it into the pattern-match text box.
            """
            result = self._build_prompt_text()
            if result is None:
                QMessageBox.warning(
                    dialog, "无法翻译",
                    "没有可用的歌词正文（需要带时间戳的歌词行）。"
                )
                return

            prompt, line_count = result
            api_url = cfg["url"]
            api_key = cfg["api_key"]
            model = cfg["model"]

            # ── Progress dialog ──
            progress = QDialog(dialog)
            progress.setWindowTitle("API 自动翻译")
            progress.setFixedSize(380, 110)
            progress.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.CustomizeWindowHint
                | Qt.WindowType.WindowTitleHint
            )
            p_layout = QVBoxLayout(progress)
            p_layout.setContentsMargins(20, 14, 20, 14)
            p_layout.setSpacing(10)

            p_label = QLabel(f"正在调用 AI 翻译（{line_count} 行歌词）…")
            p_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            p_label.setStyleSheet("font-size: 13px;")
            p_layout.addWidget(p_label)

            dots_label = QLabel()
            dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dots_label.setTextFormat(Qt.TextFormat.RichText)
            p_layout.addWidget(dots_label)

            _dot_frame2 = [0]

            def _anim_dots2() -> None:
                parts: list[str] = []
                for j in range(3):
                    if j == _dot_frame2[0]:
                        parts.append("<span style='font-size:150%;color:#ddd'>●</span>")
                    else:
                        parts.append("<span style='font-size:100%;color:#666'>●</span>")
                dots_label.setText(" ".join(parts))
                _dot_frame2[0] = (_dot_frame2[0] + 1) % 3

            dots_timer2 = QTimer(progress)
            dots_timer2.timeout.connect(_anim_dots2)
            dots_timer2.start(280)
            _anim_dots2()
            progress.show()

            _api_result: list[str | None] = [None]
            _api_error: list[str | None] = [None]
            _api_done = [False]

            def _call_api() -> None:
                try:
                    body = json.dumps({
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                    }).encode("utf-8")

                    req = urllib.request.Request(api_url, data=body, headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    })

                    with urllib.request.urlopen(req, timeout=180) as resp:
                        data = json.loads(resp.read().decode("utf-8"))

                    content = data["choices"][0]["message"]["content"]
                    _api_result[0] = content
                except Exception as e:
                    _api_error[0] = str(e)
                finally:
                    _api_done[0] = True

            threading.Thread(target=_call_api, daemon=True).start()

            def _poll_api() -> None:
                if not _api_done[0]:
                    return
                _poll_timer.stop()
                dots_timer2.stop()
                progress.accept()
                progress.deleteLater()

                if _api_error[0]:
                    err_msg = _api_error[0]
                    # Build a helpful diagnostic message
                    masked_key = api_key[:8] + "****" + api_key[-4:] if len(api_key) > 12 else "****"
                    detail = (
                        f"错误：{err_msg}\n\n"
                        f"当前配置：\n"
                        f"  名称：{cfg.get('name', '?')}\n"
                        f"  URL ：{api_url}\n"
                        f"  Key ：{masked_key}\n"
                        f"  Model：{model}\n\n"
                    )
                    if "401" in err_msg or "Unauthorized" in err_msg:
                        detail += (
                            "401 表示 API Key 无效或未授权。请检查：\n"
                            "  ● Key 是否已过期或被删除\n"
                            "  ● Key 是否有该模型的调用权限\n"
                            "  ● URL 是否与 Key 所属平台一致"
                        )
                    elif "404" in err_msg or "Not Found" in err_msg:
                        detail += (
                            "404 表示端点或模型不存在。请检查：\n"
                            "  ● API URL 是否正确\n"
                            "  ● Model 名称是否拼写正确"
                        )
                    else:
                        detail += "请检查网络连接、URL 和 Key 是否正确。"
                    QMessageBox.critical(dialog, "API 调用失败", detail)
                    return

                response_text = _api_result[0]
                if not response_text:
                    QMessageBox.warning(
                        dialog, "AI 返回空内容",
                        "API 调用成功但未返回任何翻译文本。"
                    )
                    return

                # ── Save to txt next to the LRC source file ──
                ok, out_path = _save_translation_txt(response_text)
                if not ok:
                    QMessageBox.critical(
                        dialog, "文件写入失败",
                        f"无法写入翻译文件：\n{out_path}"
                    )
                    return

                # ── "翻译成功" confirm dialog ──
                _show_done(out_path)

            def _save_translation_txt(text: str) -> "tuple[bool, str]":
                """Write the AI translation to {stem}_translation.txt
                next to the current LRC source file.
                Returns (success, path)."""
                lrc_path = self._mw.config.get_last_lrc_path()
                if lrc_path:
                    stem = os.path.splitext(os.path.basename(lrc_path))[0]
                    out_dir = os.path.dirname(lrc_path)
                else:
                    stem = "translation"
                    out_dir = os.path.expanduser("~")
                out_path = os.path.join(out_dir, f"{stem}_translation.txt")
                try:
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    # Verify it actually exists on disk
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        return True, out_path
                    else:
                        return False, out_path
                except OSError:
                    return False, out_path

            def _show_done(txt_path: str) -> None:
                """Simple confirm: 翻译成功 → 查看 / 取消."""
                done = QDialog(dialog)
                done.setWindowTitle("翻译成功")
                done.setFixedSize(420, 140)
                done.setWindowFlags(
                    Qt.WindowType.Dialog
                    | Qt.WindowType.CustomizeWindowHint
                    | Qt.WindowType.WindowTitleHint
                )
                d_layout = QVBoxLayout(done)
                d_layout.setContentsMargins(20, 16, 20, 16)
                d_layout.setSpacing(12)

                msg = QLabel(f"翻译完成，已保存至：\n{txt_path}")
                msg.setWordWrap(True)
                msg.setStyleSheet("font-size: 13px;")
                d_layout.addWidget(msg)

                d_btns = QHBoxLayout()
                d_btns.setSpacing(8)
                d_btns.addStretch()

                btn_view = QPushButton("查看")
                btn_view.setStyleSheet(
                    "QPushButton {"
                    "  font-weight: bold; color: #58a6ff;"
                    "  border: 2px solid #58a6ff;"
                    "  padding: 6px 20px; border-radius: 4px;"
                    "}"
                    "QPushButton:hover {"
                    "  background-color: rgba(88,166,255,0.15);"
                    "}"
                )
                btn_view.clicked.connect(
                    lambda: (
                        done.accept(),
                        QTimer.singleShot(
                            50,
                            lambda: _show_result(_api_result[0] or ""),
                        ),
                    )
                )
                d_btns.addWidget(btn_view)

                btn_cancel = QPushButton("取消")
                btn_cancel.clicked.connect(done.reject)
                d_btns.addWidget(btn_cancel)

                d_layout.addLayout(d_btns)
                done.exec()

            def _show_result(text: str) -> None:
                """Editable dialog showing the full AI translation,
                same style as the '编辑歌词文本' dialog."""
                rd = QDialog()
                rd.setWindowTitle("翻译结果 — " + cfg.get("name", "API"))
                rd.resize(700, 500)
                rd.setMinimumSize(500, 350)

                rd_layout = QVBoxLayout(rd)
                rd_layout.setContentsMargins(12, 12, 12, 12)
                rd_layout.setSpacing(8)

                rd_edit = QPlainTextEdit()
                rd_edit.setPlainText(text)
                rd_edit.setFont(QFont("Consolas", 13))
                rd_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
                rd_layout.addWidget(rd_edit, stretch=1)

                rd_btns = QHBoxLayout()
                rd_btns.setSpacing(8)

                btn_copy = QPushButton("复制")
                btn_copy.clicked.connect(
                    lambda: (
                        QApplication.clipboard().setText(rd_edit.toPlainText()),
                        QMessageBox.information(
                            rd, "已复制", "翻译结果已复制到剪贴板。"
                        ),
                    )
                )
                rd_btns.addWidget(btn_copy)

                btn_fill = QPushButton("填入模式匹配")
                btn_fill.setStyleSheet(
                    "QPushButton {"
                    "  font-weight: bold; color: #58a6ff;"
                    "  border: 2px solid #58a6ff;"
                    "  padding: 6px 16px; border-radius: 4px;"
                    "}"
                    "QPushButton:hover {"
                    "  background-color: rgba(88,166,255,0.15);"
                    "}"
                )
                btn_fill.clicked.connect(
                    lambda: _fill_pattern_match(rd, rd_edit.toPlainText())
                )
                rd_btns.addWidget(btn_fill)

                rd_btns.addStretch()
                btn_close = QPushButton("关闭")
                btn_close.clicked.connect(rd.accept)
                rd_btns.addWidget(btn_close)

                rd_layout.addLayout(rd_btns)
                rd.exec()

            def _fill_pattern_match(
                result_dialog: QDialog, text: str
            ) -> None:
                """Save edits back to txt, close dialogs, fill pattern match."""
                # Save any user edits back to the txt file
                try:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(text)
                except OSError:
                    pass

                result_dialog.accept()  # close result dialog
                dialog.accept()         # close AI assist dialog

                if target_text_edit is not None:
                    target_text_edit.setPlainText(text)
                    QMessageBox.information(
                        None, "已填入",
                        "翻译结果已填入模式匹配输入框，请检查后点击「匹配」。"
                    )
                else:
                    QTimer.singleShot(
                        100,
                        lambda: self._on_pattern_match(initial_text=text),
                    )

            _poll_timer = QTimer(progress)
            _poll_timer.timeout.connect(_poll_api)
            _poll_timer.start(200)

        # ── Build the model-list page ──
        def _build_model_list() -> None:
            """Rebuild the model-list page from saved configs."""
            # Clear existing widget from the sub-stack index 0
            old = api_substack.widget(0)
            if old is not None:
                api_substack.removeWidget(old)
                old.deleteLater()

            configs = self._mw.config.get_api_configs()

            list_page = QWidget()
            list_layout = QVBoxLayout(list_page)
            list_layout.setContentsMargins(4, 8, 4, 0)
            list_layout.setSpacing(8)

            # "Add new model" button
            btn_add = QPushButton("加入新模型")
            btn_add.setStyleSheet(
                "QPushButton {"
                "  font-size: 14px; padding: 10px; border: 2px dashed #aaa;"
                "  border-radius: 6px; color: #aaa;"
                "}"
                "QPushButton:hover {"
                "  border-color: #58a6ff; color: #58a6ff;"
                "}"
            )
            btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_add.clicked.connect(lambda: api_substack.setCurrentIndex(1))
            list_layout.addWidget(btn_add)

            if configs:
                list_layout.addSpacing(4)

            # Scroll area for the model rows
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll_widget = QWidget()
            scroll_layout = QVBoxLayout(scroll_widget)
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            scroll_layout.setSpacing(6)

            for i, cfg in enumerate(configs):
                row = QFrame()
                row.setStyleSheet(
                    "QFrame {"
                    "  border: 1px solid #444; border-radius: 6px;"
                    "  padding: 8px; background: rgba(128,128,128,0.05);"
                    "}"
                    "QFrame:hover { border-color: #666; }"
                )
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(12, 8, 12, 8)
                row_layout.setSpacing(10)

                # Model info
                info_label = QLabel(f"{cfg['name']}\n"
                                    f"<span style='font-size:11px;color:#888;'>"
                                    f"{cfg['model']}</span>")
                info_label.setTextFormat(Qt.TextFormat.RichText)
                info_label.setStyleSheet("font-size: 14px; border: none;")
                row_layout.addWidget(info_label, stretch=1)

                # Translate button
                btn_translate = QPushButton("翻译")
                btn_translate.setFixedSize(70, 36)
                btn_translate.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_translate.setStyleSheet(
                    "QPushButton {"
                    "  font-size: 13px; border: 2px solid #58a6ff;"
                    "  border-radius: 4px; color: #58a6ff; font-weight: bold;"
                    "}"
                    "QPushButton:hover {"
                    "  background-color: rgba(88,166,255,0.15);"
                    "}"
                )
                btn_translate.clicked.connect(
                    lambda checked, c=cfg: _do_translate(c)
                )
                row_layout.addWidget(btn_translate)

                # Test button
                btn_test = QPushButton("测")
                btn_test.setFixedSize(28, 28)
                btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_test.setToolTip("测试连接")
                btn_test.setStyleSheet(
                    "QPushButton {"
                    "  font-size: 12px; border: 1px solid #666;"
                    "  border-radius: 4px; color: #aaa;"
                    "}"
                    "QPushButton:hover {"
                    "  border-color: #3fb950; color: #3fb950;"
                    "}"
                )

                def _make_test_handler(
                    btn: QPushButton, c: dict
                ):
                    def _handler() -> None:
                        btn.setEnabled(False)
                        btn.setText("…")
                        def _work() -> None:
                            try:
                                body = json.dumps({
                                    "model": c["model"],
                                    "messages": [{"role": "user", "content": "Hi"}],
                                    "max_tokens": 5,
                                }).encode("utf-8")
                                req = urllib.request.Request(
                                    c["url"], data=body,
                                    headers={
                                        "Content-Type": "application/json",
                                        "Authorization": f"Bearer {c['api_key']}",
                                    },
                                )
                                with urllib.request.urlopen(req, timeout=10) as resp:
                                    json.loads(resp.read().decode("utf-8"))
                                QTimer.singleShot(0, lambda: _done(True, "✓"))
                            except Exception as e:
                                QTimer.singleShot(0, lambda: _done(False, str(e)))
                        def _done(ok: bool, msg: str) -> None:
                            btn.setEnabled(True)
                            if ok:
                                btn.setText("✓")
                                btn.setStyleSheet(
                                    btn.styleSheet().replace("color: #aaa", "color: #3fb950")
                                )
                                btn.setToolTip("连接成功")
                            else:
                                btn.setText("✗")
                                btn.setStyleSheet(
                                    btn.styleSheet().replace("color: #aaa", "color: #f85149")
                                )
                                btn.setToolTip(f"连接失败：{msg}")
                        threading.Thread(target=_work, daemon=True).start()
                    return _handler

                btn_test.clicked.connect(_make_test_handler(btn_test, cfg))
                row_layout.addWidget(btn_test)

                # Delete button
                btn_del = QPushButton("✕")
                btn_del.setFixedSize(28, 28)
                btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_del.setStyleSheet(
                    "QPushButton {"
                    "  font-size: 14px; border: 1px solid transparent;"
                    "  border-radius: 4px; color: #888;"
                    "}"
                    "QPushButton:hover {"
                    "  border-color: #f85149; color: #f85149;"
                    "}"
                )
                btn_del.setToolTip("删除此配置")
                btn_del.clicked.connect(
                    lambda checked, idx=i: (
                        self._mw.config.remove_api_config(idx),
                        _build_model_list(),
                    )
                )
                row_layout.addWidget(btn_del)

                scroll_layout.addWidget(row)

            scroll_layout.addStretch()
            scroll.setWidget(scroll_widget)

            if configs:
                list_layout.addWidget(scroll, stretch=1)
            else:
                empty_hint = QLabel("暂无已保存的模型，请点击上方按钮添加")
                empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
                empty_hint.setStyleSheet("font-size: 13px; color: #888;")
                list_layout.addWidget(empty_hint, stretch=1)

            api_substack.insertWidget(0, list_page)
            api_substack.setCurrentIndex(0)

        # ── Build the config-form page ──
        def _build_config_form(edit_index: int | None = None) -> None:
            """Build the config-form page.

            When *edit_index* is None we are adding a new config;
            otherwise we are editing an existing one (index into configs list).
            """
            old = api_substack.widget(1)
            if old is not None:
                api_substack.removeWidget(old)
                old.deleteLater()

            form_page = QWidget()
            outer = QVBoxLayout(form_page)
            outer.setContentsMargins(4, 8, 4, 0)
            outer.setSpacing(10)

            title_text = "编辑模型配置" if edit_index is not None else "配置新的 API 模型"
            form_title = QLabel(title_text)
            form_title.setStyleSheet("font-size: 15px; font-weight: bold;")
            outer.addWidget(form_title)

            # ── Shared input config ──
            INPUT_H = 40
            LABEL_W = 80  # px — label 固定宽度

            def _row(label_text: str, input_widget: QLineEdit) -> QHBoxLayout:
                """Create a row: [label | input] in an HBox, same height, same row."""
                lbl = QLabel(label_text)
                lbl.setFixedSize(LABEL_W, INPUT_H)
                lbl.setStyleSheet("font-size: 13px;")
                lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row = QHBoxLayout()
                row.setSpacing(8)
                row.addWidget(lbl)
                row.addWidget(input_widget, stretch=1)
                return row

            name_input = QLineEdit()
            name_input.setPlaceholderText("例如：我的 DeepSeek")
            name_input.setFont(QFont("Microsoft YaHei", 11))
            name_input.setFixedHeight(INPUT_H)
            outer.addLayout(_row("自定义名称", name_input))

            url_input = QLineEdit()
            url_input.setPlaceholderText("https://api.deepseek.com/v1/chat/completions")
            url_input.setFont(QFont("Consolas", 11))
            url_input.setFixedHeight(INPUT_H)
            outer.addLayout(_row("API URL", url_input))

            key_input = QLineEdit()
            key_input.setEchoMode(QLineEdit.EchoMode.Password)
            key_input.setPlaceholderText("sk-…")
            key_input.setFont(QFont("Consolas", 11))
            key_input.setFixedHeight(INPUT_H)
            outer.addLayout(_row("API Key", key_input))

            model_input = QLineEdit()
            model_input.setPlaceholderText("deepseek-chat")
            model_input.setFont(QFont("Consolas", 11))
            model_input.setFixedHeight(INPUT_H)
            outer.addLayout(_row("Model", model_input))

            # Pre-fill if editing existing config
            if edit_index is not None:
                configs = self._mw.config.get_api_configs()
                if 0 <= edit_index < len(configs):
                    cfg = configs[edit_index]
                    name_input.setText(cfg.get("name", ""))
                    url_input.setText(cfg.get("url", ""))
                    key_input.setText(cfg.get("api_key", ""))
                    model_input.setText(cfg.get("model", ""))

            outer.addStretch()

            # ── Feedback label (inline, replaces toast) ──
            feedback = QLabel()
            feedback.setStyleSheet("font-size: 12px; padding: 4px;")
            feedback.setWordWrap(True)
            feedback.hide()
            outer.addWidget(feedback)

            def _show_feedback(text: str, is_error: bool = False) -> None:
                color = "#f85149" if is_error else "#3fb950"
                feedback.setText(f"<span style='color:{color}'>{text}</span>")
                feedback.show()

            # ── Button row ──
            btn_row = QHBoxLayout()
            btn_row.setSpacing(8)

            btn_row.addStretch()

            btn_cancel = QPushButton("取消")
            btn_cancel.setStyleSheet(
                "QPushButton {"
                "  font-size: 13px; padding: 8px 16px; border: 1px solid #aaa;"
                "  border-radius: 4px;"
                "}"
                "QPushButton:hover { border-color: #f85149; color: #f85149; }"
            )
            btn_cancel.clicked.connect(lambda: api_substack.setCurrentIndex(0))
            btn_row.addWidget(btn_cancel)

            btn_test_save = QPushButton("测试并保存")
            btn_test_save.setStyleSheet(
                "QPushButton {"
                "  font-size: 13px; padding: 8px 16px; border: 2px solid #58a6ff;"
                "  border-radius: 4px; color: #58a6ff; font-weight: bold;"
                "}"
                "QPushButton:hover { background-color: rgba(88,166,255,0.15); }"
                "QPushButton:disabled {"
                "  border-color: #555; color: #666;"
                "}"
            )
            btn_row.addWidget(btn_test_save)

            outer.addLayout(btn_row)

            # ── Test → Save (test success required to save) ──
            def _test_and_save() -> None:
                name = name_input.text().strip()
                u = url_input.text().strip()
                k = key_input.text().strip()
                m = model_input.text().strip()
                if not name or not u or not k or not m:
                    _show_feedback("请填写完整的名称、URL、Key 和 Model", True)
                    return

                btn_test_save.setEnabled(False)
                btn_test_save.setText("测试中…")
                feedback.hide()

                _test_done = [False]

                def _do_test() -> None:
                    try:
                        body = json.dumps({
                            "model": m or "default",
                            "messages": [{"role": "user", "content": "Hi"}],
                            "max_tokens": 5,
                        }).encode("utf-8")
                        req = urllib.request.Request(u, data=body, headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {k}",
                        })
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            json.loads(resp.read().decode("utf-8"))
                        if not _test_done[0]:
                            _test_done[0] = True
                            QTimer.singleShot(0, lambda: _on_test_result(True, "连接成功 ✓"))
                    except Exception as e:
                        if not _test_done[0]:
                            _test_done[0] = True
                            QTimer.singleShot(0, lambda: _on_test_result(False, str(e)))

                def _on_test_result(ok: bool, msg: str) -> None:
                    _watchdog.stop()
                    btn_test_save.setEnabled(True)
                    btn_test_save.setText("测试并保存")
                    if ok:
                        # Test passed → save with the exact values that were tested
                        if edit_index is not None:
                            configs = self._mw.config.get_api_configs()
                            if 0 <= edit_index < len(configs):
                                configs[edit_index] = {
                                    "name": name, "url": u,
                                    "api_key": k, "model": m,
                                }
                                raw_cfg = self._mw.config._load_config()
                                raw_cfg["apiConfigs"] = []
                                self._mw.config._save_config()
                                for c in configs:
                                    self._mw.config.add_api_config(
                                        c["name"], c["url"],
                                        c["api_key"], c["model"],
                                    )
                        else:
                            self._mw.config.add_api_config(name, u, k, m)
                        _show_feedback("连接成功，配置已加密保存 ✓")
                        QTimer.singleShot(800, _build_model_list)
                    else:
                        _show_feedback(f"连接失败，未保存：{msg}", True)

                threading.Thread(target=_do_test, daemon=True).start()

                # Watchdog: force-reset after 15 s no matter what
                _watchdog = QTimer(form_page)
                _watchdog.setSingleShot(True)
                _watchdog.timeout.connect(
                    lambda: (
                        _on_test_result(False, "连接超时（15 秒无响应）")
                        if not _test_done[0]
                        else None
                    )
                )
                _watchdog.start(15000)

            btn_test_save.clicked.connect(_test_and_save)

            api_substack.insertWidget(1, form_page)

        # ── Initial state ──
        if self._mw.config.has_api_configs():
            _build_model_list()
            _build_config_form()  # prepare the form for later use
        else:
            _build_model_list()  # empty list with "add" button
            _build_config_form()
            api_substack.setCurrentIndex(1)  # auto-enter config form

        stack.addWidget(api_page)  # index 2

        dialog.exec()

    def _build_prompt_text(self) -> tuple[str, int] | None:
        """Build the AI translation prompt from current LRC lyrics.

        Returns ``(prompt_text, line_count)`` or ``None`` if no usable lyrics.
        """
        state = self._mw.lrc_state
        prefs = self._mw.config.get_preferences()
        fixed: Fixed = prefs.get("fixed", 3)

        lines: list[str] = []
        for ln in state.lyric:
            if ln.time is None:
                continue
            if not ln.text.strip():
                continue
            tag = convert_time_to_tag(ln.time, fixed)
            lines.append(f"{tag}{ln.text}")

        if not lines:
            return None

        lyrics_text = "\n".join(lines)
        prompt = (
            f"{lyrics_text}\n\n"
            "请帮我翻译歌词，翻译给出和原文相同的时间戳，"
            "无时间戳的不必翻译，不必给出歌曲原文，要求翻译符合全文逻辑"
        )
        return prompt, len(lines)

    def _generate_and_copy_prompt(self, parent_dialog: QDialog) -> None:
        """Generate the AI translation prompt from current LRC lyrics and copy to clipboard."""
        result = self._build_prompt_text()
        if result is None:
            self._mw.toast_overlay.show_toast(
                "warning", "没有可用的歌词正文（需要带时间戳的歌词行）"
            )
            return

        prompt, line_count = result
        QApplication.clipboard().setText(prompt)
        self._mw.toast_overlay.show_toast(
            "success", f"提示词已复制到剪贴板（共 {line_count} 行歌词）"
        )

    def _on_pattern_match(self, initial_text: str = "") -> None:
        """Open a dialog where user pastes LRC text containing translations.

        When *initial_text* is provided, the text area is pre-filled with it
        (used by AI auto-translate to feed the API response into matching).
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("模式匹配 - 匹配翻译")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

        # Instructions
        instr_label = QLabel(
            "粘贴包含翻译的 LRC 文本，支持两种格式：\n"
            "  ●  [时间戳]歌词 + [相同时间戳]翻译（成对识别）\n"
            "  ●  [时间戳]翻译文本（直接作为翻译）\n"
        )
        instr_label.setWordWrap(True)
        instr_label.setStyleSheet("font-size: 12px; color: #888; padding-bottom: 4px;")
        dlg_layout.addWidget(instr_label)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("在此粘贴 LRC 文本…")
        text_edit.setFont(QFont("Consolas", 13))
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        if initial_text:
            text_edit.setPlainText(initial_text)
        dlg_layout.addWidget(text_edit, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_ai_assist = QPushButton("AI辅助")
        btn_ai_assist.setToolTip("通过 AI 聊天网站或 API 自动生成翻译")
        btn_ai_assist.clicked.connect(lambda: self._on_ai_assist(target_text_edit=text_edit))
        btn_ai_assist.setStyleSheet(
            "QPushButton {"
            "  font-size: 13px; padding: 6px 14px; border: 1px solid #aaa;"
            "  border-radius: 4px;"
            "}"
            "QPushButton:hover { border-color: #58a6ff; color: #58a6ff; }"
        )
        btn_layout.addWidget(btn_ai_assist)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_match = QPushButton("匹配")
        btn_match.clicked.connect(dialog.accept)
        btn_match.setDefault(True)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_match)
        dlg_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            input_text = text_edit.toPlainText().strip()
            if not input_text:
                self._mw.toast_overlay.show_toast("warning", "未输入任何文本")
                return
            # Defer so the modal dialog fully unwinds before we start
            QTimer.singleShot(0, lambda: self._perform_pattern_matching(input_text))

    def _perform_pattern_matching(self, input_text: str) -> None:
        """Match translations in a background thread, then fill them one by one.

        - Background thread: pure regex matching (never touches Qt / UI)
        - Main thread QTimer poll: check if matching is done
        - Main thread QTimer fill: apply one translation at a time,
          updating the visible _TranslationRow directly → line-by-line effect
        """
        import threading
        from collections import defaultdict

        state = self._mw.lrc_state
        prefs = self._mw.config.get_preferences()
        fixed: Fixed = prefs.get("fixed", 3)

        # Snapshot lyric data for the worker thread (plain Python, no Qt)
        lyric_snapshot: list[tuple[float | None, str, str]] = [
            (ln.time, ln.text, ln.translation) for ln in state.lyric
        ]

        # ── Progress dialog with animated dots ────────────────
        progress = QDialog(self)
        progress.setWindowTitle("模式匹配")
        progress.setFixedSize(300, 110)
        progress.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        p_layout = QVBoxLayout(progress)
        p_layout.setContentsMargins(20, 14, 20, 14)
        p_layout.setSpacing(6)

        p_label = QLabel("正在匹配翻译…")
        p_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p_label.setStyleSheet("font-size: 13px;")
        p_layout.addWidget(p_label)

        # Animated dots
        dots_label = QLabel()
        dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dots_label.setTextFormat(Qt.TextFormat.RichText)
        p_layout.addWidget(dots_label)

        _dot_frame = [0]

        def _anim_dots() -> None:
            parts: list[str] = []
            for j in range(3):
                if j == _dot_frame[0]:
                    parts.append("<span style='font-size:150%;color:#ddd'>●</span>")
                else:
                    parts.append("<span style='font-size:100%;color:#666'>●</span>")
            dots_label.setText(" ".join(parts))
            _dot_frame[0] = (_dot_frame[0] + 1) % 3

        dots_timer = QTimer(progress)
        dots_timer.timeout.connect(_anim_dots)
        dots_timer.start(280)
        _anim_dots()

        progress.show()

        # ── Background matching (plain Python thread, no Qt) ──
        _matches: list[tuple[int, str]] = []
        _done = [False]

        def _match_work() -> None:
            input_lines = [
                ln for ln in re.split(r"\r\n|\n|\r", input_text) if ln
            ]
            ts_groups: dict[str, list[str]] = defaultdict(list)
            for line in input_lines:
                m = re.match(
                    r"^(\[\s*\d{1,3}:\d{1,2}(?:[:.]\d{1,3})?\s*])(.*)", line
                )
                if m:
                    ts_groups[m.group(1)].append(m.group(2))

            for i, (time_val, lyric_text, translation) in enumerate(lyric_snapshot):
                if translation:
                    continue
                if time_val is None:
                    continue
                our_tag = convert_time_to_tag(time_val, fixed)
                texts = ts_groups.get(our_tag)
                if not texts:
                    continue
                result: str | None = None
                if len(texts) == 2:
                    our = lyric_text.strip()
                    t0 = texts[0].strip()
                    t1 = texts[1].strip()
                    if t0 == our and t1 != our:
                        result = t1
                    elif t1 == our and t0 != our:
                        result = t0
                elif len(texts) == 1:
                    t = texts[0].strip()
                    if t and t != lyric_text.strip():
                        result = t
                if result:
                    _matches.append((i, result))
            _done[0] = True

        threading.Thread(target=_match_work, daemon=True).start()

        # ── Poll timer: wait for matching, then fill one by one ─
        def _poll() -> None:
            if not _done[0]:
                return
            _poll_timer.stop()

            if not _matches:
                dots_timer.stop()
                progress.accept()
                progress.deleteLater()
                self._mw.toast_overlay.show_toast(
                    "warning", "未找到匹配的翻译文本"
                )
                return

            # Fill one by one — animation keeps playing, rows update visibly
            theme_color = prefs.get("themeColor", "#f58ea8")
            is_dark = is_dark_theme()
            state._push_undo()
            _queue = list(_matches)
            _count = [0]

            def _fill_one() -> None:
                if not _queue:
                    _fill_timer.stop()
                    dots_timer.stop()
                    progress.accept()
                    progress.deleteLater()
                    state.state_changed.emit()
                    self._mw.toast_overlay.show_toast(
                        "success", f"成功匹配 {_count[0]} 条翻译"
                    )
                    return

                idx, text = _queue.pop(0)
                if 0 <= idx < len(state.lyric):
                    state.lyric[idx] = LyricLine(
                        time=state.lyric[idx].time,
                        text=state.lyric[idx].text,
                        translation=text,
                    )
                    _count[0] += 1
                    if idx < len(self._trans_rows):
                        self._trans_rows[idx].update_state(
                            line=state.lyric[idx],
                            theme_color=theme_color,
                            is_dark=is_dark,
                        )

            _fill_timer = QTimer(self)
            _fill_timer.timeout.connect(_fill_one)
            _fill_timer.start(0)

        _poll_timer = QTimer(self)
        _poll_timer.timeout.connect(_poll)
        _poll_timer.start(30)

    def _on_import(self) -> None:
        """Import LRC file: clear draft → smart import → file browser."""
        state = self._mw.lrc_state

        # Stop audio timer during the entire import flow.  Otherwise
        # refresh() → state_changed → _save_state() would re-create the
        # draft file between delete_draft() and the user picking a new
        # LRC (the smart-import and file-browser dialogs are modal).
        timer_was_active = self._mw.audio_manager._timer.isActive()
        self._mw.audio_manager._timer.stop()

        try:
            if len(state.lyric) > 0:
                # Clear UI first, then delete draft file
                state.init_from_text("", self._mw.trim_options)
                self._mw.config.delete_draft()

            # Smart import (when audio is loaded)
            audio = self._mw.audio_manager
            if audio.src and audio.duration > 0 and self._mw.config.get_enable_smart_import():
                reply = QMessageBox.question(
                    self,
                    "智能查找",
                    "你是否要寻找当前歌曲的歌词",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._do_smart_import()
                    return

            # Fallback to file browser
            self._file_browser_import()
        finally:
            if timer_was_active:
                self._mw.audio_manager._timer.start(
                    self._mw.audio_manager._TIMER_INTERVAL
                )

    def _file_browser_import(self) -> None:
        """Open a file dialog for the user to pick an LRC file manually."""
        default_dir = self._mw.config.get_default_browse_dir()
        last_path = self._mw.config.get_last_lrc_path()
        if last_path and os.path.exists(os.path.dirname(last_path)):
            start_dir = os.path.dirname(last_path)
        elif default_dir and os.path.exists(default_dir):
            start_dir = default_dir
        else:
            start_dir = ""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入歌词",
            start_dir,
            "歌词文件 (*.lrc *.txt);;所有文件 (*)",
        )
        if file_path:
            if self._mw.config.get_remember_last_lrc():
                self._mw.config.set_last_lrc_path(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._mw.lrc_state.init_from_text(text, self._mw.trim_options)
                self._mw.toast_overlay.show_toast("success", "歌词已导入")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"导入失败：{e}")

    def _do_smart_import(self) -> None:
        """Look for ``{audio_stem}.lrc`` next to the MP3 and load it.

        Since drafts are now always deleted before import, we only need to
        check for the matching LRC file.  Falls back to asking the user
        whether to create a new empty draft.
        """
        mp3_path = self._mw.config.get_last_mp3_path()
        if not mp3_path:
            self._mw.toast_overlay.show_toast("warning", "未找到音频文件路径")
            self._file_browser_import()
            return

        audio_dir = os.path.dirname(mp3_path)
        stem = os.path.splitext(os.path.basename(mp3_path))[0]
        lrc_path = os.path.join(audio_dir, f"{stem}.lrc")

        # Same-name LRC next to MP3
        if os.path.exists(lrc_path):
            if lrc_path == self._mw.config.get_last_lrc_path():
                return
            try:
                with open(lrc_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._mw.lrc_state.init_from_text(text, self._mw.trim_options)
                if self._mw.config.get_remember_last_lrc():
                    self._mw.config.set_last_lrc_path(lrc_path)
                self._mw.toast_overlay.show_toast("success", "已加载同名歌词文件")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载歌词文件失败：{e}")
        else:
            # Ask to create new draft
            reply = QMessageBox.question(
                self,
                "新建草稿",
                "未找到歌词文件，是否新建草稿？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._mw.lrc_state.init_from_text("", self._mw.trim_options)
                self._mw.toast_overlay.show_toast("success", "已创建新草稿")
            else:
                self._file_browser_import()

    def _on_export(self) -> None:
        """Export current LRC state to a file chosen by user."""
        info = self._mw.lrc_state.info
        parts = []
        for key in ("ti", "ar"):
            v = info.get(key)
            if v:
                parts.append(v)
        if not parts:
            al = info.get("al", "")
            if al:
                parts.append(al)
            else:
                # Fall back to current audio filename
                mp3_path = self._mw.config.get_last_mp3_path()
                if mp3_path:
                    parts.append(os.path.splitext(os.path.basename(mp3_path))[0])
                else:
                    parts.append("lyrics")
        filename = re.sub(r'[<>:"/\\|?*]', "_", " - ".join(parts)).strip() + ".lrc"

        # Determine initial directory
        default_dir = self._mw.config.get_default_browse_dir()
        last_path = self._mw.config.get_last_lrc_path()
        if last_path and os.path.exists(os.path.dirname(last_path)):
            start_dir = os.path.join(os.path.dirname(last_path), filename)
        elif default_dir and os.path.exists(default_dir):
            start_dir = os.path.join(default_dir, filename)
        else:
            start_dir = filename

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出歌词", start_dir,
            "LRC 文件 (*.lrc);;所有文件 (*)",
        )
        if file_path:
            text = self._mw.lrc_state.stringify(self._mw.format_options)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
            self._mw.toast_overlay.show_toast("success", "歌词已导出")

    def _on_edit_text(self) -> None:
        """Open a dialog to directly edit the LRC text."""
        current_text = self._mw.lrc_state.stringify(self._mw.format_options)

        dialog = QDialog(self)
        dialog.setWindowTitle("编辑歌词文本")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

        text_edit = QPlainTextEdit()
        text_edit.setPlainText(current_text)
        text_edit.setFont(QFont("Consolas", 13))
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        dlg_layout.addWidget(text_edit, stretch=1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(dialog.accept)
        btn_save.setDefault(True)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_save)
        dlg_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = text_edit.toPlainText()
            self._mw.lrc_state.init_from_text(new_text, self._mw.trim_options)
            self._mw.toast_overlay.show_toast("success", "歌词已更新")

    def _on_preview(self) -> None:
        """Show a read-only preview of the LRC output."""
        text = self._mw.lrc_state.stringify(self._mw.format_options)

        dialog = QDialog(self)
        dialog.setWindowTitle("预览 LRC")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

        text_edit = QPlainTextEdit()
        text_edit.setPlainText(text)
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 13))
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        dlg_layout.addWidget(text_edit, stretch=1)

        btn_layout = QHBoxLayout()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        dlg_layout.addLayout(btn_layout)

        dialog.exec()

    def _on_save(self) -> None:
        """Save current state by overwriting the source LRC file."""
        last_path = self._mw.config.get_last_lrc_path()
        if not last_path or not os.path.exists(last_path):
            self._mw.toast_overlay.show_toast(
                "warning", "未找到源文件，请先导入歌词文件"
            )
            return

        if self._mw.config.get_show_save_warning():
            self._show_save_warning_dialog(last_path)
        else:
            self._do_save(last_path)

    def _do_save(self, path: str) -> None:
        """Actually write the LRC string to the given path."""
        text = self._mw.lrc_state.stringify(self._mw.format_options)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._mw.config.delete_draft()
            self._mw.toast_overlay.show_toast("success", "歌词已保存")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败：{e}")

    def _show_save_warning_dialog(self, path: str) -> None:
        """Show the overwrite warning dialog with preview/cancel options."""
        dialog = QDialog(self)
        dialog.setWindowTitle("保存确认")
        dialog.setMinimumWidth(420)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(20, 20, 20, 20)
        dlg_layout.setSpacing(16)

        # Warning icon + message
        msg_label = QLabel(
            "\"保存\"会覆写你的源文件，\n此操作不可撤销，是否预览覆写效果？"
        )
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 14px;")
        dlg_layout.addWidget(msg_label)

        # "Never show again" checkbox
        self._save_warning_cb = QCheckBox("不再显示此警告")
        dlg_layout.addWidget(self._save_warning_cb)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)

        btn_preview = QPushButton("预览")
        btn_preview.clicked.connect(lambda: self._on_save_preview(dialog))

        btn_layout.addStretch()
        btn_layout.addWidget(btn_preview)
        btn_layout.addWidget(btn_cancel)
        dlg_layout.addLayout(btn_layout)

        result = dialog.exec()

        # Persist "never show" preference
        if self._save_warning_cb.isChecked():
            prefs = self._mw.config.get_preferences()
            prefs["showSaveWarning"] = False
            self._mw.update_preferences(prefs)

        # If user closed via X or cancel, do nothing
        if result != QDialog.DialogCode.Accepted:
            return

        # User confirmed save (after preview)
        self._do_save(path)

    def _on_save_preview(self, warning_dialog: QDialog) -> None:
        """Preview then confirm save flow from the warning dialog."""
        # Show preview first
        self._on_preview()

        # After preview closes, ask for final confirmation
        confirm = QDialog(warning_dialog)
        confirm.setWindowTitle("确认覆写")
        confirm.setMinimumWidth(360)

        cnf_layout = QVBoxLayout(confirm)
        cnf_layout.setContentsMargins(20, 20, 20, 20)
        cnf_layout.setSpacing(14)

        cnf_label = QLabel("确认覆写源文件？")
        cnf_label.setStyleSheet("font-size: 14px;")
        cnf_layout.addWidget(cnf_label)

        cnf_btn_layout = QHBoxLayout()
        cnf_btn_layout.setSpacing(8)
        cnf_btn_cancel = QPushButton("取消")
        cnf_btn_cancel.clicked.connect(confirm.reject)
        cnf_btn_confirm = QPushButton("确认")
        cnf_btn_confirm.clicked.connect(confirm.accept)
        cnf_btn_confirm.setDefault(True)
        cnf_btn_layout.addStretch()
        cnf_btn_layout.addWidget(cnf_btn_cancel)
        cnf_btn_layout.addWidget(cnf_btn_confirm)
        cnf_layout.addLayout(cnf_btn_layout)

        if confirm.exec() == QDialog.DialogCode.Accepted:
            # Close the warning dialog with Accepted to trigger save
            warning_dialog.accept()

    # ── Keyboard Handler ────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts on this page.

        Ports the onKeydown handler from synchronizer.tsx.

        Note: when a lyric line is selected, the app-wide event filter
        in MainWindow already intercepts Space (→ timestamp) and blocks
        audio shortcuts — so those cases never reach this method.
        """
        action = self._mw.keybinding_manager.get_matched_action(event)

        state = self._mw.lrc_state
        audio = self._mw.audio_manager

        if action == InputAction.SYNC:
            # Space: toggle play/pause only when no line is selected
            if state.select_index == -1:
                event.accept()
                audio.toggle()
                return
            # Line is selected → timestamp it
            if audio.duration:
                event.accept()
                seek_time = audio.current_time  # capture before advancing
                state.next_(seek_time)
                self._append_target_index = state.select_index
                self._scroll_to_row(state.select_index)
                # Auto-seek verify: jump back to the new timestamp after a delay
                prefs = self._mw.config.get_preferences()
                if prefs.get("autoSeekVerify", False):
                    delay_ms = int(prefs.get("autoSeekDelay", 1.0) * 1000)

                    def _seek_back() -> None:
                        was_paused = audio.paused
                        audio.current_time = seek_time
                        # QMediaPlayer.setPosition may pause on some backends
                        if not was_paused and audio.paused:
                            audio.toggle()

                    QTimer.singleShot(delay_ms, _seek_back)
                return

        elif action == InputAction.DELETE_TIME:
            event.accept()
            state.delete_time()
            return

        elif action == InputAction.RESET_OFFSET:
            if audio.duration:
                line = state.lyric[state.select_index] if 0 <= state.select_index < len(state.lyric) else None
                if line and line.time is not None:
                    event.accept()
                    audio.step(event.modifiers(), 0, line.time)
            return

        elif action == InputAction.DECREASE_OFFSET:
            if audio.duration:
                line = state.lyric[state.select_index] if 0 <= state.select_index < len(state.lyric) else None
                if line and line.time is not None:
                    event.accept()
                    audio.step(event.modifiers(), -0.5, line.time)
            return

        elif action == InputAction.INCREASE_OFFSET:
            if audio.duration:
                line = state.lyric[state.select_index] if 0 <= state.select_index < len(state.lyric) else None
                if line and line.time is not None:
                    event.accept()
                    audio.step(event.modifiers(), 0.5, line.time)
            return

        elif action == InputAction.PREV_LINE:
            event.accept()
            state.select(lambda i: i - 1)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.NEXT_LINE:
            event.accept()
            state.select(lambda i: i + 1)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.FIRST_LINE:
            event.accept()
            state.select(lambda _: 0)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.LAST_LINE:
            event.accept()
            state.select(lambda _: float("inf"))
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.PAGE_UP:
            event.accept()
            state.select(lambda i: i - 10)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.PAGE_DOWN:
            event.accept()
            state.select(lambda i: i + 10)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.UNDO:
            event.accept()
            state.undo()
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.REDO:
            event.accept()
            state.redo()
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.COPY_LINE:
            if 0 <= state.select_index < len(state.lyric):
                event.accept()
                state.copy_line(state.select_index)
                self._append_target_index = state.select_index
                self._scroll_to_row(state.select_index)
                self._mw.toast_overlay.show_toast(
                    "success", f"已复制第 {state.select_index} 行歌词"
                )
            return

        elif action == InputAction.SPLIT_LYRIC:
            if 0 <= state.select_index < len(state.lyric):
                event.accept()
                self._on_split_lyric(state.select_index)
            return

        elif action == InputAction.SAVE:
            event.accept()
            self._on_save()
            return

        elif action == InputAction.EXPORT:
            event.accept()
            self._on_export()
            return

        elif action == InputAction.TRANSLATE:
            event.accept()
            self._btn_translate.setChecked(not self._btn_translate.isChecked())
            self._on_translate_toggle()
            return

        elif action == InputAction.PREVIEW:
            event.accept()
            self._on_preview()
            return

        elif action == InputAction.LOAD_AUDIO:
            event.accept()
            self._mw.footer_bar.audio_controls._on_load_audio()
            return

        # Esc → deselect current row
        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            state.deselect()
            self._append_target_index = None
            event.accept()
            return

        # Forward audio shortcuts only when no lyric line is selected
        if state.select_index == -1:
            if self._mw.handle_global_key(event):
                return

        super().keyPressEvent(event)

    # ── Event Filter (viewport background clicks) ────────────

    def eventFilter(self, obj, event):
        """Detect clicks on empty space of the scroll area → deselect row."""
        from PyQt6.QtCore import QEvent
        if obj == self._scroll.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            pos = event.position().toPoint()
            child = self._scroll.viewport().childAt(pos)
            # Walk up parent chain — if click is inside a row, let it handle it
            while child is not None:
                if isinstance(child, (_LyricRow, _TranslationRow)):
                    return False
                child = child.parentWidget()
            # Click on empty space → deselect
            self._mw.lrc_state.deselect()
            self._append_target_index = None
            return False
        return super().eventFilter(obj, event)

    # ── Internal: Row Management ────────────────────────────

    def _rebuild_all(self) -> None:
        """Full rebuild: clear and recreate all rows."""
        self._suppress_refresh = True

        # Remove existing lyric rows
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        # Remove existing translation rows
        for row in self._trans_rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._trans_rows.clear()

        state = self._mw.lrc_state
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()

        # Remove stretch (last item)
        if self._rows_layout.count() > 0:
            self._rows_layout.takeAt(self._rows_layout.count() - 1)

        for i, line in enumerate(state.lyric):
            # Create lyric row (always present)
            row = _LyricRow(
                index=i,
                line=line,
                fixed=prefs.get("fixed", 3),
                space_start=prefs.get("spaceStart", 1),
                space_end=prefs.get("spaceEnd", 0),
                theme_color=theme_color,
                is_dark=is_dark,
                parent=self._rows_container,
            )
            row.seek_requested.connect(self._on_seek)
            row.edit_requested.connect(self._on_edit_timestamp)
            row.edit_lyric_requested.connect(self._on_edit_lyric)
            row.split_lyric_requested.connect(self._on_split_lyric)
            row.lyric_text_changed.connect(self._on_lyric_text_changed)
            row.lyric_split_done.connect(self._on_lyric_split_done)
            row.append_requested.connect(self._on_append_lyric)
            row.row_clicked.connect(self._on_row_clicked)
            self._rows_layout.addWidget(row)
            self._rows.append(row)

            # If translation mode is active, insert a translation row below
            if self._translation_mode:
                trans_row = _TranslationRow(
                    index=i,
                    line=line,
                    theme_color=theme_color,
                    is_dark=is_dark,
                    parent=self._rows_container,
                )
                trans_row.translation_changed.connect(self._on_translation_changed)
                trans_row.translation_finished.connect(self._on_translation_finished)
                trans_row.row_clicked.connect(self._on_row_clicked)
                self._rows_layout.addWidget(trans_row)
                self._trans_rows.append(trans_row)

        # Re-add stretch
        self._rows_layout.addStretch()

        self._suppress_refresh = False
        self._refresh_rows()

        # Also update input box visibility & styling on full rebuild
        self._update_input_visibility()
        self._restyle_input()

    def _refresh_rows(self) -> None:
        """Update all rows from current state (no rebuild unless count changed)."""
        if self._suppress_refresh:
            return

        state = self._mw.lrc_state
        prefs = self._mw.config.get_preferences()
        fixed: Fixed = prefs.get("fixed", 3)
        space_start = prefs.get("spaceStart", 1)
        space_end = prefs.get("spaceEnd", 0)
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()

        # Update input box visibility
        self._update_input_visibility()
        self._restyle_input()
        self._restyle_space_button()

        # Rebuild if count changed
        if len(self._rows) != len(state.lyric):
            self._rebuild_all()
            return

        sync_mode = self._mw.config.get_sync_mode()

        for i, row in enumerate(self._rows):
            line = state.lyric[i]
            selected = (i == state.select_index)
            at_current = (
                sync_mode == SyncMode.HIGHLIGHT and i == state.current_index
            )
            row.update_state(
                line=line,
                selected=selected,
                at_current=at_current,
                fixed=fixed,
                space_start=space_start,
                space_end=space_end,
                theme_color=theme_color,
                is_dark=is_dark,
            )

        # Update translation rows if active
        if self._translation_mode:
            for i, trans_row in enumerate(self._trans_rows):
                if i < len(state.lyric):
                    trans_row.update_state(
                        line=state.lyric[i],
                        theme_color=theme_color,
                        is_dark=is_dark,
                    )

    # ── Internal: Signal Handlers ──────────────────────────

    def _on_sync(self) -> None:
        """Called by on-screen space button."""
        audio = self._mw.audio_manager
        if audio.duration:
            seek_time = audio.current_time
            self._mw.lrc_state.next_(seek_time)
            self._append_target_index = self._mw.lrc_state.select_index
            self._scroll_to_row(self._mw.lrc_state.select_index)
            # Auto-seek verify
            prefs = self._mw.config.get_preferences()
            if prefs.get("autoSeekVerify", False):
                delay_ms = int(prefs.get("autoSeekDelay", 1.0) * 1000)

                def _seek_back() -> None:
                    was_paused = audio.paused
                    audio.current_time = seek_time
                    if not was_paused and audio.paused:
                        audio.toggle()

                QTimer.singleShot(delay_ms, _seek_back)

    def _on_seek(self, time: float) -> None:
        """Seek audio to a specific time (timestamp button clicked)."""
        audio = self._mw.audio_manager
        if audio.duration > 0:
            audio.current_time = time

    def _on_edit_timestamp(self, index: int) -> None:
        """Open a dialog to manually edit a timestamp (Ctrl+click)."""
        line = self._mw.lrc_state.lyric[index]
        prefs = self._mw.config.get_preferences()
        current_tag = convert_time_to_tag(line.time, prefs.get("fixed", 3)) if line.time is not None else ""
        new_tag, ok = QInputDialog.getText(
            self,
            "编辑时间戳",
            f"输入时间戳 (mm:ss.xxx)：",
            text=current_tag,
        )
        if ok and new_tag.strip():
            self._parse_and_set_time(index, new_tag.strip())

    def _parse_and_set_time(self, index: int, tag: str) -> None:
        """Parse a user-entered timestamp string and set it on the line."""
        match = re.match(r"^\[?\s*(\d{1,3}):(\d{1,2}(?:[:.]\d{1,3})?)\s*]?$", tag)
        if not match:
            return
        mm = int(match.group(1))
        ss = float(match.group(2).replace(":", "."))
        time_val = mm * 60 + ss

        # Select the line first, then set time
        self._mw.lrc_state.select(lambda _: index)
        self._mw.lrc_state.set_time(time_val)

    def _on_row_clicked(self, index: int) -> None:
        """User clicked the text area of a row → select it and set append target."""
        self._mw.lrc_state.select(lambda _: index)
        self._append_target_index = index
        # Steal focus back so that keyboard shortcuts (Space, etc.)
        # are handled by SynchronizerPage.keyPressEvent rather than
        # being swallowed by whatever child widget currently has focus.
        self.setFocus()

    def _on_edit_lyric(self, index: int) -> None:
        """Enter inline edit mode on the specified row."""
        if 0 <= index < len(self._rows):
            self._rows[index].enter_edit_mode()

    def _on_split_lyric(self, index: int) -> None:
        """Enter inline split mode on the specified row."""
        if 0 <= index < len(self._rows):
            self._rows[index].enter_split_mode()

    def _on_lyric_text_changed(self, index: int, new_text: str) -> None:
        """Inline edit confirmed — update state."""
        state = self._mw.lrc_state
        if 0 <= index < len(state.lyric):
            if new_text != state.lyric[index].text:
                state.set_text(index, new_text)
                self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行歌词已更新")

    def _on_lyric_split_done(self, index: int, cleaned_text: str, positions: list) -> None:
        """Inline split confirmed — update state with split."""
        state = self._mw.lrc_state
        if not (0 <= index < len(state.lyric)):
            return

        state.set_text(index, cleaned_text)
        state.split_line(index, positions)

        count = len(positions) + 1
        if count == 2:
            self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行已一分为二")
        else:
            self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行已分裂为 {count} 行")

    def _on_append_lyric(self, index: int) -> None:
        """Append a new empty line after the selected row (same timestamp)."""
        self._mw.lrc_state.append_line(index)
        self._mw.toast_overlay.show_toast("success", f"已在第 {index + 1} 行后追加新行")

    # ── Lyric Input Box ────────────────────────────────────

    def _on_lyric_input_submit(self) -> None:
        """Handle Enter in the lyric input box.

        Splits input by newlines, filters empty lines, then either:
        - Inserts at top (timestamp 0.0) when no row has been clicked
        - Appends after the clicked row (same timestamp)
        """
        raw = self._lyric_input.toPlainText()
        # Split by newlines, strip each line, filter empty
        lines = [ln.strip() for ln in raw.split("\n")]
        lines = [ln for ln in lines if ln]

        if not lines:
            return

        state = self._mw.lrc_state

        # Determine insert position and timestamp
        if self._append_target_index is not None and 0 <= self._append_target_index < len(state.lyric):
            # Append after the selected row, with that row's timestamp
            after_index = self._append_target_index
            ref_time = state.lyric[self._append_target_index].time
        else:
            # Insert at top, timestamp 0
            after_index = -1
            ref_time = 0.0

        state.insert_lines(after_index, lines, ref_time)

        # Clear input and reset append target
        self._lyric_input.clear()
        self._append_target_index = None

        # Scroll to the last inserted line
        self._scroll_to_row(state.select_index)

        count = len(lines)
        if count == 1:
            self._mw.toast_overlay.show_toast("success", "已添加歌词")
        else:
            self._mw.toast_overlay.show_toast("success", f"已添加 {count} 行歌词")

    def _update_input_visibility(self) -> None:
        """Show or hide the lyric input box.

        Visible only when a song is active AND translation mode is off.
        """
        state = self._mw.lrc_state
        audio = self._mw.audio_manager
        has_song = len(state.lyric) > 0 or bool(audio.src)
        self._lyric_input.setVisible(has_song and not self._translation_mode)

    def _restyle_input(self) -> None:
        """Apply theme styling to the lyric input box."""
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()
        fg = "#eeeeee" if is_dark else "#111111"

        self._lyric_input.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  color: {fg};"
            f"  font-size: 14px;"
            f"  background-color: {theme_color}11;"
            f"  border: 1px solid {theme_color};"
            f"  border-radius: 4px;"
            f"  padding: 4px 8px;"
            f"  margin: 2px 4px;"
            f"}}"
            f"QPlainTextEdit:focus {{"
            f"  border-color: {theme_color};"
            f"  background-color: {theme_color}22;"
            f"}}"
        )

    # ── Internal: Scrolling ─────────────────────────────────

    def _scroll_to_row(self, index: int) -> None:
        """Scroll so the given row is visible."""
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            self._scroll.ensureWidgetVisible(row, 0, 40)

    # ── Space Button ────────────────────────────────────────

    def _reposition_space_button(self) -> None:
        """Reposition the space button to bottom-right of this widget."""
        if self._space_btn:
            w = self.width()
            h = self.height()
            self._space_btn.move(w - 130, h - 130)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_space_button()

    def showEvent(self, event) -> None:
        """Called when this page becomes visible."""
        super().showEvent(event)
        self._refresh_rows()
        self.setFocus()
        # Apply space button preference
        prefs = self._mw.config.get_preferences()
        self.set_space_button_visible(prefs.get("screenButton", False))
