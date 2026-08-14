"""Content stack — QStackedWidget page router (replaces hash-based routing)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QStackedWidget,
    QWidget,
)

from ..core.constants import PageRoute, ThemeMode

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Default Theme Colors (mirrors usePref.ts themeColor) ───

THEME_COLORS = {
    "orange": "#ff691f",
    "yellow": "#fab81e",
    "lime": "#7fdbb6",
    "green": "#19cf86",
    "blue": "#91d2fa",
    "navy": "#1b95e0",
    "grey": "#abb8c2",
    "red": "#e81c4f",
    "pink": "#f58ea8",
    "purple": "#c877fe",
}

DEFAULT_THEME_COLOR = "#f58ea8"  # pink

# ── Shared theme state ─────────────────────────────────────

_CURRENT_DARK: bool = False
_CURRENT_BG: str = "#f1f3f4"
_CURRENT_FG: str = "#111111"
_CURRENT_THEME_COLOR: str = DEFAULT_THEME_COLOR


def is_dark_theme() -> bool:
    """Return whether the current effective theme is dark.

    Use this from any widget that needs to pick adaptive colors.
    It is updated every time ``apply_theme()`` is called.
    """
    return _CURRENT_DARK


def get_theme_colors() -> tuple[str, str, str, bool]:
    """Return current theme colors as (bg, fg, theme_color, dark)."""
    return _CURRENT_BG, _CURRENT_FG, _CURRENT_THEME_COLOR, _CURRENT_DARK


def apply_theme(prefs: dict) -> None:
    """Apply theme (color + dark/light mode) to the entire application."""
    app = QApplication.instance()
    if app is None:
        return

    theme_color = prefs.get("themeColor", DEFAULT_THEME_COLOR)
    theme_mode = prefs.get("themeMode", ThemeMode.AUTO)

    # Parse theme color to RGB
    hex_color = theme_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Determine dark/light
    system_dark = False
    try:
        from PyQt6.QtCore import Qt as QtCore
        scheme = app.styleHints().colorScheme()
        # Qt.ColorScheme.Dark = 1 (added in PyQt6 6.5)
        system_dark = (int(scheme) == 1)
    except (AttributeError, ImportError, TypeError):
        pass

    dark = (
        theme_mode == ThemeMode.DARK or
        (theme_mode == ThemeMode.AUTO and system_dark)
    )

    # Build QSS stylesheet
    bg = "#111111" if dark else "#f1f3f4"
    fg = "#eeeeee" if dark else "#111111"
    semi_bg = "#202020cc" if dark else "#e0e0e0cc"
    contrast_text = "#111111" if _is_light_color(r, g, b) else "#eeeeee"

    # Store for use by other modules
    global _CURRENT_DARK, _CURRENT_BG, _CURRENT_FG, _CURRENT_THEME_COLOR
    _CURRENT_DARK = dark
    _CURRENT_BG = bg
    _CURRENT_FG = fg
    _CURRENT_THEME_COLOR = theme_color

    border_radius = "8px"

    qss = f"""
    /* Global */
    QWidget {{
        color: {fg};
        background-color: {bg};
        font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
        font-size: 14px;
    }}

    /* Header */
    #headerBar {{
        background-color: {bg};
        border-bottom: 2px solid {theme_color};
        padding: 4px 8px;
    }}
    #appTitle {{
        font-size: 18px;
        font-weight: bold;
        color: {theme_color};
    }}
    #navTab {{
        padding: 6px 14px;
        border: none;
        border-radius: {border_radius};
        background: transparent;
        color: {fg};
        font-size: 14px;
    }}
    #navTab:hover {{
        background-color: {theme_color};
        color: {contrast_text};
    }}
    #navTab:checked {{
        background-color: {theme_color};
        color: {"#111111" if _is_light_color(r, g, b) else "#eeeeee"};
    }}

    /* Footer */
    #footerBar {{
        background-color: {semi_bg};
        border-top: 1px solid {theme_color};
        padding: 4px 8px;
    }}

    /* Audio Controls */
    #audioButton {{
        border: none;
        background: transparent;
        color: {fg};
        padding: 4px 8px;
        border-radius: {border_radius};
    }}
    #audioButton:hover {{
        background-color: {theme_color};
        color: {contrast_text};
    }}
    #audioButton:disabled {{
        opacity: 0.5;
    }}

    /* ── Buttons (generic) ─────────────────────────── */
    QPushButton {{
        border: 1px solid {fg}33;
        border-radius: {border_radius};
        padding: 6px 14px;
        background: transparent;
        color: {fg};
    }}
    QPushButton:hover {{
        background-color: {theme_color};
        color: {contrast_text};
        border-color: {theme_color};
    }}
    QPushButton:pressed {{
        background-color: {theme_color};
        color: {contrast_text};
    }}
    QPushButton:checked {{
        background-color: {theme_color};
        color: {"#111111" if _is_light_color(r, g, b) else "#eeeeee"};
        border-color: {theme_color};
    }}

    /* ── Sliders ───────────────────────────────────── */
    QSlider::groove:horizontal {{
        height: 4px;
        background: {fg};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 16px;
        height: 16px;
        margin: -6px 0;
        border-radius: 8px;
        background: {theme_color};
    }}
    QSlider::handle:horizontal:hover {{
        background: {theme_color};
        border: 2px solid #ffffff66;
    }}
    QSlider::sub-page:horizontal {{
        background: {theme_color};
        border-radius: 2px;
    }}

    /* ── ComboBox ──────────────────────────────────── */
    QComboBox {{
        border: 1px solid {fg}33;
        border-radius: {border_radius};
        padding: 4px 10px;
        background: transparent;
    }}
    QComboBox:hover {{
        border-color: {theme_color};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {bg};
        color: {fg};
        border: 1px solid {fg}33;
        border-radius: 4px;
        selection-background-color: {theme_color};
        selection-color: {contrast_text};
    }}
    QComboBox QAbstractItemView::item:hover {{
        background-color: {theme_color};
        color: {contrast_text};
    }}

    /* ── SpinBox ───────────────────────────────────── */
    QSpinBox {{
        border: 1px solid {fg}33;
        border-radius: {border_radius};
        padding: 4px 8px;
        background: transparent;
    }}
    QSpinBox:hover {{
        border-color: {theme_color};
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background-color: {theme_color};
    }}

    /* ── LineEdit ──────────────────────────────────── */
    QLineEdit {{
        border: 1px solid {fg}33;
        border-radius: {border_radius};
        padding: 4px 8px;
        background: transparent;
    }}
    QLineEdit:hover {{
        border-color: {theme_color};
    }}
    QLineEdit:focus {{
        border-color: {theme_color};
    }}

    /* ── Tab Widget ────────────────────────────────── */
    QTabBar::tab {{
        border: 1px solid transparent;
        border-radius: {border_radius};
        padding: 6px 16px;
        margin: 2px;
    }}
    QTabBar::tab:hover {{
        background-color: {theme_color};
        color: {contrast_text};
        border-color: {theme_color};
    }}
    QTabBar::tab:selected {{
        background-color: {theme_color};
        color: {contrast_text};
        border-color: {theme_color};
    }}

    /* ── Menu ─────────────────────────────────────── */
    QMenu {{
        background: {bg};
        color: {fg};
        border: 1px solid {fg}33;
        border-radius: 4px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px;
        border-radius: 3px;
    }}
    QMenu::item:selected {{
        background: {theme_color};
        color: {contrast_text};
    }}
    QMenu::separator {{
        height: 1px;
        background: {fg}22;
        margin: 4px 8px;
    }}

    /* ── GroupBox ──────────────────────────────────── */
    QGroupBox {{
        border: 1px solid {fg}22;
        border-radius: {border_radius};
        margin-top: 12px;
        padding-top: 16px;
        font-weight: bold;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
    }}

    /* ── Collapsible header ───────────────────────── */
    #collapsibleHeader {{
        text-align: left;
        font-size: 13px;
        font-weight: bold;
        color: {fg};
        background: {semi_bg};
        border: 1px solid {fg}22;
        border-radius: {border_radius};
        padding: 6px 10px;
    }}
    #collapsibleHeader:hover {{
        background: {theme_color};
        color: {contrast_text};
        border-color: {theme_color};
    }}

    /* Home page sync button */
    #homeSyncButton {{
        text-align: left;
        padding: 8px 16px;
        border-radius: {border_radius};
    }}

    /* Editor */
    #editorArea {{
        border: 1px solid {theme_color};
        border-radius: {border_radius};
        background: transparent;
        font-size: 15px;
        padding: 8px;
    }}

    /* Synchronizer */
    #lyricList {{
        background: transparent;
        border: none;
        outline: none;
        font-size: 16px;
    }}
    #lyricList::item {{
        padding: 8px 8px 8px 100px;
        border-top: 2px solid transparent;
        border-bottom: 2px solid transparent;
    }}
    #lyricList::item:nth-child(even) {{
        background-color: {"#00000022" if dark else "#ffffff22"};
    }}
    #lyricList::item:nth-child(odd) {{
        background-color: {"#ffffff22" if dark else "#00000022"};
    }}
    #lyricList::item:selected {{
        background-color: {theme_color};
        border-color: {theme_color};
        border-top: 2px solid {theme_color};
        border-bottom: 2px solid {theme_color};
    }}

    /* Toast */
    #toastInfo {{
        border-left: 4px solid #91d2fa;
    }}
    #toastSuccess {{
        border-left: 4px solid #19cf86;
    }}
    #toastWarning {{
        border-left: 4px solid #fab81e;
    }}

    /* Preferences */
    #prefList::item {{
        padding: 8px 16px;
    }}

    /* Checkboxes — ensure indicator (box) is always visible */
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid {fg};
        border-radius: 3px;
        background: transparent;
    }}
    QCheckBox::indicator:checked {{
        background-color: {theme_color};
        border-color: {theme_color};
    }}
    QCheckBox::indicator:hover {{
        border-color: {theme_color};
    }}

    /* Scroll bars */
    QScrollBar:vertical {{
        width: 8px;
        background: transparent;
    }}
    QScrollBar::handle:vertical {{
        background: {theme_color};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    """

    app.setStyleSheet(qss)

    # Also set palette for native dialogs etc.
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(fg))
    palette.setColor(QPalette.ColorRole.Base, QColor(bg))
    palette.setColor(QPalette.ColorRole.Text, QColor(fg))
    palette.setColor(QPalette.ColorRole.Button, QColor(bg))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(fg))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(theme_color))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor("#111111" if _is_light_color(r, g, b) else "#eeeeee"),
    )
    app.setPalette(palette)


def _is_light_color(r: int, g: int, b: int) -> bool:
    """Determine if a color is light (for contrast choice).

    Ports the WCAG luminance check from content.tsx.
    """
    # luminance
    def lum(v: int) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    l = 0.2126 * lum(r) + 0.7152 * lum(g) + 0.0722 * lum(b)
    con = l + 0.05
    return con * con > 0.0525


class ContentStack(QStackedWidget):
    """Page router using QStackedWidget.

    Manages the 5 pages and their visibility.
    """

    sync_page_active_changed = pyqtSignal(bool)

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._main_window = main_window

        # Pages will be set from outside after construction
        self._pages: dict[int, QWidget] = {}

    def register_page(self, route: int, widget: QWidget) -> None:
        """Add a page widget to the stack."""
        idx = self.addWidget(widget)
        self._pages[route] = widget

    def set_page(self, route: int) -> None:
        """Switch to the specified page."""
        widget = self._pages.get(route)
        if widget is not None:
            prev_was_sync = self.currentIndex() == PageRoute.SYNCHRONIZER
            self.setCurrentWidget(widget)

            # Update header selection
            self._main_window.header_bar.set_active(route)

            # Emit sync page state changes
            is_sync = route == PageRoute.SYNCHRONIZER
            if prev_was_sync != is_sync:
                self.sync_page_active_changed.emit(is_sync)
