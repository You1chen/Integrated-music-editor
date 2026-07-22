"""Shared helper functions for the synchronizer sub-package."""

from __future__ import annotations


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
