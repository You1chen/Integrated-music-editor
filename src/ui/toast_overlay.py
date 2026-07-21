"""Toast overlay — notification queue (replaces toast.tsx)."""

from __future__ import annotations

from PyQt6.QtCore import (
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ToastWidget(QFrame):
    """Single toast notification."""

    def __init__(self, toast_type: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(f"toast{toast_type.capitalize()}")

        # Type-specific styling
        colors = {
            "info": ("#91d2fa", "#1b95e0"),
            "success": ("#19cf86", "#0d6b45"),
            "warning": ("#fab81e", "#b8860b"),
        }
        bg, border = colors.get(toast_type, colors["info"])

        self.setStyleSheet(
            f"background-color: #222; border-left: 4px solid {border};"
            f" border-radius: 4px; padding: 8px 12px; margin: 4px;"
        )
        self.setFixedWidth(300)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)

        # Icon
        icons = {"info": "ℹ", "success": "✓", "warning": "⚠"}
        icon = QLabel(icons.get(toast_type, "ℹ"))
        icon.setStyleSheet(f"color: {bg}; font-size: 16px; font-weight: bold;")
        layout.addWidget(icon)

        # Text
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: #eee;")
        layout.addWidget(label, stretch=1)

        # Close button
        close_btn = QLabel("✕")
        close_btn.setStyleSheet("color: #888;")
        close_btn.mousePressEvent = lambda ev: self.hide()
        layout.addWidget(close_btn)

class ToastOverlay(QWidget):
    """Overlay that shows a queue of toast notifications.

    Hidden when empty, shown only when at least one toast is active.
    This avoids a black rectangle artifact on Windows where child-widget
    transparency attributes have no effect.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addStretch()

        self._toasts: list[ToastWidget] = []
        self.hide()  # start invisible — no toasts yet

    def show_toast(self, toast_type: str, text: str) -> None:
        """Add a toast notification to the queue."""
        toast = ToastWidget(toast_type, text, self)
        # Insert at the top of the layout
        layout = self.layout()
        if layout:
            layout.insertWidget(0, toast)
        toast.show()

        self._toasts.append(toast)

        # Auto-dismiss after 3 seconds
        QTimer.singleShot(3000, lambda: self._dismiss(toast))

        self.show()  # make overlay visible
        self._reposition()

    def _dismiss(self, toast: ToastWidget) -> None:
        """Hide and remove a toast. Hide overlay if it was the last one."""
        toast.hide()
        toast.deleteLater()
        if toast in self._toasts:
            self._toasts.remove(toast)
        if not self._toasts:
            self.hide()  # nothing left → disappear completely

    def _reposition(self) -> None:
        """Position at top-right of parent window."""
        parent = self.parentWidget()
        if parent:
            pw = parent.width()
            self.move(pw - self.width() - 16, 52)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition()
