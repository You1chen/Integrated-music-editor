"""A translation editing row below a lyric row.

Has an empty 105px placeholder (matching timestamp button width)
and a QLineEdit for editing the translation text.

Signals
------
translation_changed(int, str) — index + new text (per-keystroke)
translation_finished(int)      — user finished editing (Enter / focus loss)
row_clicked(int)               — user clicked empty area → select parent line
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from ...core.lrc_parser import LyricLine
from ._helpers import _rgba


class _TranslationRow(QFrame):
    """A translation editing row below a lyric row."""

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
        self._multi_selected = False

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

    def update_state(
        self, line: LyricLine, theme_color: str, is_dark: bool,
        multi_selected: bool = False,
    ) -> None:
        """Update translation text from state (skips if user is editing)."""
        self._line = line
        self._theme_color = theme_color
        self._is_dark = is_dark
        self._multi_selected = multi_selected
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

        border_style = f"3px solid {theme}"
        self.setStyleSheet(
            f"_TranslationRow {{"
            f"  background-color: transparent;"
            f"  border-left: {border_style};"
            f"}}"
        )

        self._trans_edit.setStyleSheet(
            f"QLineEdit {{"
            f"  color: {theme};"
            f"  background: transparent;"
            f"  border: none;"
            f"  border-bottom: 1px dashed {_rgba(fg, 0.13)};"
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
