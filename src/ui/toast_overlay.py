"""Toast overlay — notification queue (replaces toast.tsx).

Rendered as a top-level frameless window that stays above all other
windows, including modal dialogs.  Repositions automatically when
the main window moves or resizes.

Each toast is a plain text card on a solid colour:
  success → white,  warning (failure) → red,  info (hint) → yellow.
No icon, no close button, no extra decoration.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ToastWidget(QFrame):
    """Single toast notification — just text on a solid colour card."""

    def __init__(self, toast_type: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(f"toast{toast_type.capitalize()}")

        # (background, text) per type: success=white, warning=red, info=yellow.
        palettes = {
            "success": ("#ffffff", "#1a1d23"),
            "warning": ("#e74c3c", "#ffffff"),
            "info": ("#f5c518", "#1a1d23"),
        }
        bg, fg = palettes.get(toast_type, palettes["info"])
        # White needs a hairline border to stay visible over light content;
        # red / yellow pop on their own in both themes.
        border = "rgba(0, 0, 0, 0.15)" if toast_type == "success" else "none"

        self.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {bg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 8px;"
            f"}}"
        )
        self.setFixedWidth(300)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {fg};")
        layout.addWidget(label)


class ToastOverlay(QWidget):
    """Overlay that shows a queue of toast notifications.

    A top-level frameless tool window that stays on top of everything
    (including modal dialogs).  Follows the main window position.
    """

    def __init__(self, main_window: QWidget) -> None:
        super().__init__(None)  # top-level — no parent
        self._main = main_window

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addStretch()

        self._toasts: list[ToastWidget] = []

        # Track main window moves
        main_window.installEventFilter(self)
        self.hide()

    # ── Tracking main window ────────────────────────────────

    def eventFilter(self, obj, event):
        """Follow the main window when it moves or resizes."""
        from PyQt6.QtCore import QEvent
        if obj is self._main and event.type() in (
            QEvent.Type.Move, QEvent.Type.Resize,
        ):
            self._reposition()
        return super().eventFilter(obj, event)

    # ── Toast API ───────────────────────────────────────────

    def show_toast(self, toast_type: str, text: str) -> None:
        """Add a toast notification to the queue."""
        toast = ToastWidget(toast_type, text, self)
        layout = self.layout()
        if layout:
            layout.insertWidget(0, toast)
        toast.show()

        self._toasts.append(toast)

        QTimer.singleShot(3000, lambda: self._dismiss(toast))

        self._reposition()
        self.show()

    def _dismiss(self, toast: ToastWidget) -> None:
        """Hide and remove a toast. Hide overlay if it was the last one."""
        toast.hide()
        toast.deleteLater()
        if toast in self._toasts:
            self._toasts.remove(toast)
        if not self._toasts:
            self.hide()

    # ── Positioning ─────────────────────────────────────────

    def _reposition(self) -> None:
        """Position at top-right of the main window."""
        if self._main:
            pt = self._main.mapToGlobal(self._main.rect().topRight())
            self.move(pt.x() - self.width() - 16, pt.y() + 52)
