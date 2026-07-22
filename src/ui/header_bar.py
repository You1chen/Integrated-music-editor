"""Header navigation bar with page tabs."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from ..core.constants import PageRoute


class HeaderBar(QWidget):
    """Top navigation bar with app title and tab buttons.

    Tabs: Home | Editor | Synchronizer | Preferences
    """

    page_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(4)

        # App title
        self.title_label = QLabel("集成歌曲编辑器")
        self.title_label.setObjectName("appTitle")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # Nav tabs
        self._buttons: dict[int, QPushButton] = {}

        tabs = [
            (PageRoute.HOME, "主页"),
            (PageRoute.SYNCHRONIZER, "歌词制作"),
            (PageRoute.META_EDITOR, "编辑元信息"),
            (PageRoute.PREFERENCES, "设置"),
        ]

        for route, label in tabs:
            btn = QPushButton(label)
            btn.setObjectName("navTab")
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.clicked.connect(lambda checked, r=route: self._on_tab_clicked(r))
            layout.addWidget(btn)
            self._buttons[route] = btn

        # Default selection
        self._buttons[PageRoute.HOME].setChecked(True)
        self._active_route = PageRoute.HOME

    def _on_tab_clicked(self, route: int) -> None:
        # Uncheck all other buttons
        for r, btn in self._buttons.items():
            btn.setChecked(r == route)
        self._active_route = route
        self.page_requested.emit(route)

    def set_active(self, route: int) -> None:
        """Programmatically set the active tab."""
        for r, btn in self._buttons.items():
            btn.setChecked(r == route)
        self._active_route = route

