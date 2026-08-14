"""Toast overlay — notification queue (replaces toast.tsx).

Rendered as a top-level frameless window that stays above all other
windows, including modal dialogs.  Repositions automatically when
the main window moves or resizes.
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

from .content_stack import get_theme_colors


class ToastWidget(QFrame):
    """Single toast notification."""

    def __init__(self, toast_type: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(f"toast{toast_type.capitalize()}")

        colors = {
            "info": ("#91d2fa", "#1b95e0"),
            "success": ("#19cf86", "#0d6b45"),
            "warning": ("#fab81e", "#b8860b"),
        }
        icon_color, bar_color = colors.get(toast_type, colors["info"])

        _bg, fg, _theme, dark = get_theme_colors()
        surface = "#1a1e24" if dark else "#ffffff"
        muted = "#9aa1ab" if dark else "#6b7280"

        self.setStyleSheet(
            f"background-color: {surface};"
            f" border: 1px solid {bar_color};"
            f" border-left: 4px solid {bar_color};"
            f" border-radius: 8px;"
        )
        self.setFixedWidth(300)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        icons = {"info": "ℹ", "success": "✓", "warning": "⚠"}
        icon = QLabel(icons.get(toast_type, "ℹ"))
        icon.setStyleSheet(f"color: {icon_color}; font-size: 16px; font-weight: bold;")
        layout.addWidget(icon)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {fg};")
        layout.addWidget(label, stretch=1)

        close_btn = QLabel("✕")
        close_btn.setStyleSheet(f"color: {muted};")
        close_btn.mousePressEvent = lambda ev: self.hide()
        layout.addWidget(close_btn)


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
