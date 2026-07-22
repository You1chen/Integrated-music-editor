"""Auto-resizing lyric input widget for adding new lyrics.

Enter       → emits submit_requested (submit and clear)
Shift+Enter → inserts a literal newline
Ctrl+Enter  → inserts a literal newline
Ctrl+Z      → local text-undo when input has text;
              propagates to parent (lyric undo) when input is empty

Height grows with line count via sizeHint override.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QPlainTextEdit, QWidget


class _LyricInput(QPlainTextEdit):
    """Auto-resizing input for adding new lyrics."""

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
