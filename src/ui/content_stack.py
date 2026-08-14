"""Content stack — QStackedWidget page router (replaces hash-based routing)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QObject, pyqtSignal
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
_CURRENT_BG: str = "#f5f6f8"
_CURRENT_FG: str = "#1a1d23"
_CURRENT_THEME_COLOR: str = DEFAULT_THEME_COLOR


class _ThemeEvents(QObject):
    """Broadcasts theme rebuilds so widgets with cached inline styles
    (computed once from ``get_theme_colors()``) can re-polish themselves."""

    changed = pyqtSignal()


theme_events = _ThemeEvents()


def is_dark_theme() -> bool:
    """Return whether the current effective theme is dark.

    Use this from any widget that needs to pick adaptive colors.
    It is updated every time ``apply_theme()`` is called.
    """
    return _CURRENT_DARK


def get_theme_colors() -> tuple[str, str, str, bool]:
    """Return current theme colors as (bg, fg, theme_color, dark)."""
    return _CURRENT_BG, _CURRENT_FG, _CURRENT_THEME_COLOR, _CURRENT_DARK


# ── Color helpers ─────────────────────────────────────────

def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Parse a ``#rrggbb`` color into an (r, g, b) tuple."""
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgba(color: str, alpha: float) -> str:
    """Return ``rgba(r, g, b, a)`` for a ``#rrggbb`` color.

    Qt's QSS parser interprets 8-digit hex as ``#AARRGGBB`` (alpha first),
    so ``"#f58ea8" + "33"`` would NOT mean "pink at 20% opacity" — it would
    be parsed as alpha=0xf5 with a greenish RGB.  Always spell transparency
    out as ``rgba()`` to avoid that trap.
    """
    r, g, b = _hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _blend(hex_a: str, hex_b: str, weight: float) -> str:
    """Mix two ``#rrggbb`` colors; *weight* 1.0 → pure *hex_a*."""
    ra, ga, ba = _hex_to_rgb(hex_a)
    rb, gb, bb = _hex_to_rgb(hex_b)
    return "#{:02x}{:02x}{:02x}".format(
        int(ra * weight + rb * (1 - weight)),
        int(ga * weight + gb * (1 - weight)),
        int(ba * weight + bb * (1 - weight)),
    )


def apply_theme(prefs: dict) -> None:
    """Apply theme (color + dark/light mode) to the entire application."""
    app = QApplication.instance()
    if app is None:
        return

    theme_color = prefs.get("themeColor", DEFAULT_THEME_COLOR)
    theme_mode = prefs.get("themeMode", ThemeMode.AUTO)

    # Parse theme color to RGB
    r, g, b = _hex_to_rgb(theme_color)

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

    # ── Palette (design tokens) ─────────────────────────────
    if dark:
        bg = "#121417"
        surface = "#1a1e24"
        fg = "#e7e9ec"
    else:
        bg = "#f5f6f8"
        surface = "#ffffff"
        fg = "#1a1d23"

    accent = theme_color
    accent_contrast = "#111111" if _is_light_color(r, g, b) else "#eeeeee"
    accent_soft = _rgba(accent, 0.16)       # subtle hover fill
    accent_soft_strong = _rgba(accent, 0.30)
    border = _rgba(fg, 0.12)                # hairline
    border_strong = _rgba(fg, 0.22)         # visible border
    muted = _blend(fg, bg, 0.55)            # secondary text
    faint = _blend(fg, bg, 0.34)            # placeholder/hint text

    # Store for use by other modules
    global _CURRENT_DARK, _CURRENT_BG, _CURRENT_FG, _CURRENT_THEME_COLOR
    _CURRENT_DARK = dark
    _CURRENT_BG = bg
    _CURRENT_FG = fg
    _CURRENT_THEME_COLOR = theme_color

    radius = "9px"
    radius_sm = "6px"

    qss = f"""
    /* ── Global ─────────────────────────────────────── */
    QWidget {{
        color: {fg};
        background-color: {bg};
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
        font-size: 14px;
    }}
    QLabel {{ background: transparent; }}

    QToolTip {{
        background-color: {surface};
        color: {fg};
        border: 1px solid {border_strong};
        border-radius: {radius_sm};
        padding: 5px 9px;
    }}

    /* ── Header ────────────────────────────────────── */
    #headerBar {{
        background-color: {surface};
        border-bottom: 1px solid {border};
        padding: 4px 10px;
    }}
    #appTitle {{
        font-size: 17px;
        font-weight: 700;
        color: {accent};
        padding: 0 8px;
    }}
    #navTab {{
        padding: 6px 14px;
        border: none;
        border-radius: {radius};
        background: transparent;
        color: {muted};
        font-size: 14px;
    }}
    #navTab:hover {{
        background-color: {accent_soft};
        color: {fg};
    }}
    #navTab:checked {{
        background-color: {accent};
        color: {accent_contrast};
        font-weight: 600;
    }}

    /* ── Footer ────────────────────────────────────── */
    #footerBar {{
        background-color: {surface};
        border-top: 1px solid {border};
        padding: 4px 10px;
    }}

    /* ── Audio Controls ────────────────────────────── */
    #audioButton {{
        border: none;
        background: transparent;
        color: {fg};
        padding: 4px 8px;
        border-radius: {radius};
        font-size: 15px;
    }}
    #audioButton:hover {{
        background-color: {accent_soft};
        color: {fg};
    }}
    #audioButton:pressed {{
        background-color: {accent};
        color: {accent_contrast};
    }}
    #audioButton:disabled {{
        color: {faint};
    }}

    /* ── Buttons (generic) ─────────────────────────── */
    QPushButton {{
        border: 1px solid {border_strong};
        border-radius: {radius};
        padding: 6px 14px;
        background-color: {surface};
        color: {fg};
    }}
    QPushButton:hover {{
        background-color: {accent};
        color: {accent_contrast};
        border-color: {accent};
    }}
    QPushButton:pressed {{
        background-color: {accent};
        color: {accent_contrast};
        border-color: {accent};
    }}
    QPushButton:checked {{
        background-color: {accent};
        color: {accent_contrast};
        border-color: {accent};
        font-weight: 600;
    }}
    QPushButton:disabled {{
        color: {faint};
        border-color: {border};
        background-color: transparent;
    }}
    QPushButton:default {{
        border: 2px solid {accent};
        font-weight: 600;
    }}

    /* ── Inputs ────────────────────────────────────── */
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox,
    QSpinBox, QDoubleSpinBox {{
        border: 1px solid {border_strong};
        border-radius: {radius};
        padding: 5px 10px;
        background-color: {surface};
        color: {fg};
        selection-background-color: {accent};
        selection-color: {accent_contrast};
    }}
    QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover,
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
        border-color: {accent};
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {accent};
    }}
    QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
        color: {faint};
        border-color: {border};
    }}

    /* ── Sliders ───────────────────────────────────── */
    QSlider::groove:horizontal {{
        height: 5px;
        background: {border};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {accent};
        border-radius: 2px;
    }}
    QSlider::add-page:horizontal {{
        background: {border};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
        background: {accent};
        border: 2px solid {surface};
    }}
    QSlider::handle:horizontal:hover {{
        background: {accent};
        border: 2px solid {accent_contrast};
    }}
    QSlider::handle:horizontal:pressed {{
        background: {accent};
        border: 2px solid {accent_contrast};
    }}

    /* ── ComboBox drop-down ────────────────────────── */
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {surface};
        color: {fg};
        border: 1px solid {border_strong};
        border-radius: {radius_sm};
        padding: 4px;
        selection-background-color: {accent};
        selection-color: {accent_contrast};
        outline: none;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 6px 10px;
        border-radius: {radius_sm};
    }}
    QComboBox QAbstractItemView::item:hover {{
        background-color: {accent_soft};
        color: {fg};
    }}

    /* ── SpinBox buttons ───────────────────────────── */
    QSpinBox::up-button, QDoubleSpinBox::up-button,
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 18px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: {accent_soft};
    }}

    /* ── Tab Widget ────────────────────────────────── */
    QTabWidget::pane {{
        border: 1px solid {border};
        border-radius: {radius};
        top: -1px;
    }}
    QTabBar::tab {{
        border: 1px solid transparent;
        border-radius: {radius};
        padding: 6px 16px;
        margin: 2px;
        color: {muted};
    }}
    QTabBar::tab:hover {{
        background-color: {accent_soft};
        color: {fg};
    }}
    QTabBar::tab:selected {{
        background-color: {accent};
        color: {accent_contrast};
    }}

    /* ── Menu ──────────────────────────────────────── */
    QMenu {{
        background: {surface};
        color: {fg};
        border: 1px solid {border_strong};
        border-radius: {radius_sm};
        padding: 4px;
    }}
    QMenu::item {{
        padding: 7px 22px;
        border-radius: {radius_sm};
    }}
    QMenu::item:selected {{
        background: {accent};
        color: {accent_contrast};
    }}
    QMenu::item:disabled {{
        color: {faint};
    }}
    QMenu::separator {{
        height: 1px;
        background: {border};
        margin: 4px 8px;
    }}

    /* ── GroupBox ──────────────────────────────────── */
    QGroupBox {{
        border: 1px solid {border};
        border-radius: {radius};
        margin-top: 12px;
        padding-top: 16px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: {muted};
    }}

    /* ── Collapsible header ────────────────────────── */
    #collapsibleHeader {{
        text-align: left;
        font-size: 13px;
        font-weight: 600;
        color: {fg};
        background: {surface};
        border: 1px solid {border};
        border-radius: {radius};
        padding: 8px 12px;
    }}
    #collapsibleHeader:hover {{
        background: {accent_soft};
        color: {fg};
        border-color: {accent};
    }}

    /* ── Checkboxes / Radios ───────────────────────── */
    QCheckBox {{ spacing: 8px; }}
    QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid {border_strong};
        border-radius: 5px;
        background: {surface};
    }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
        border-color: {accent};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background-color: {accent};
        border-color: {accent};
    }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        border-color: {border};
        background: transparent;
    }}

    /* ── Scroll bars ───────────────────────────────── */
    QScrollBar:vertical {{
        width: 10px;
        background: transparent;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {border_strong};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {accent};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        height: 10px;
        background: transparent;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {border_strong};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {accent};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    /* ── Scroll area / editor ──────────────────────── */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    #editorArea {{
        border: 1px solid {accent};
        border-radius: {radius};
        background: transparent;
        font-size: 14px;
        padding: 8px;
    }}

    /* ── Synchronizer ──────────────────────────────── */
    #lyricList {{
        background: transparent;
        border: none;
    }}
    """

    app.setStyleSheet(qss)

    # Also set palette for native dialogs etc.
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(fg))
    palette.setColor(QPalette.ColorRole.Base, QColor(surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(bg))
    palette.setColor(QPalette.ColorRole.Text, QColor(fg))
    palette.setColor(QPalette.ColorRole.Button, QColor(surface))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(fg))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(accent))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor("#111111" if _is_light_color(r, g, b) else "#eeeeee"),
    )
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(faint))
    app.setPalette(palette)

    # Let widgets that cache inline styles re-read the new theme colors.
    theme_events.changed.emit()


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
