"""Local cover sampling and paint helpers shared by the home and share cards."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter


@dataclass(frozen=True)
class CardPalette:
    background: QColor
    text: QColor
    secondary: QColor
    accent: QColor


def mix(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(*(x + (y - x) * amount for x, y in zip(a.getRgbF(), b.getRgbF())))


def _luminance(color: QColor) -> float:
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
              for x in color.getRgbF()[:3]]
    return sum(x * weight for x, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _contrast(a: QColor, b: QColor) -> float:
    dark, light = sorted((_luminance(a), _luminance(b)))
    return (light + 0.05) / (dark + 0.05)


def _read_pixels(image: QImage) -> np.ndarray:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = image.constBits().asstring(image.sizeInBytes())
    return np.frombuffer(pixels, np.uint8).reshape(image.height(), image.width(), 4)


def _cover_colors(cover: QImage, fallback: QColor) -> tuple[QColor, QColor]:
    if cover.isNull():
        return QColor(fallback), QColor(fallback)
    sample = cover.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    pixels = _read_pixels(sample).reshape(-1, 4)
    rgb = pixels[pixels[:, 3] >= 128, :3]
    if not len(rgb):
        return QColor(fallback), QColor(fallback)
    # Rank coarse RGB bins by area and keep actual sampled colors as representatives.
    bins = (rgb.astype(np.int32) // 32) @ np.array([64, 8, 1])
    counts = np.bincount(bins, minlength=512)
    winners = np.argsort(counts)[-6:][::-1]
    swatches = [(QColor(*map(int, rgb[bins == i].mean(axis=0))), counts[i])
                for i in winners if counts[i]]
    dominant = swatches[0][0]
    accent, _ = max(swatches, key=lambda entry: entry[0].hsvSaturationF() *
                    (0.4 + 0.6 * entry[1] / swatches[0][1]))
    return dominant, accent


def card_palette(cover: QImage, fallback: QColor, *, style: str = "faithful",
                 tone: str = "auto") -> CardPalette:
    dominant, accent = _cover_colors(cover, fallback)
    dark = tone == "dark" or (tone == "auto" and _luminance(dominant) < 0.36)
    base = QColor("#171820" if dark else "#fffdfa")
    foreground = QColor("#ffffff" if dark else "#14141a")
    if style == "mono":
        dominant = base
    elif style == "soft":
        accent = mix(accent, base, 0.4)
        dominant = mix(dominant, base, 0.4)
    elif style == "vivid" and accent.hsvSaturationF() > 0.03:
        accent = QColor.fromHsvF(accent.hsvHueF(), 0.88, 0.91)
        dominant = accent
    background = mix(base, dominant, 0.25 if dark else 0.13)
    secondary = mix(foreground, background, 0.25)
    while _contrast(accent, background) < 4.5:
        accent = mix(accent, foreground, 0.14)
    return CardPalette(background, foreground, secondary, accent)


def draw_cover(painter: QPainter, rect: QRectF, image: QImage, focus: float = 0.5) -> None:
    """Fill the destination without stretching, with adjustable horizontal framing."""
    if image.isNull() or rect.isEmpty():
        return
    scale = max(rect.width() / image.width(), rect.height() / image.height())
    width, height = rect.width() / scale, rect.height() / scale
    src = QRectF((image.width() - width) * max(0.0, min(1.0, focus)),
                 (image.height() - height) / 2, width, height)
    painter.drawImage(rect, image, src)


def soft_cover(cover: QImage, strength: int = 1) -> QImage:
    if cover.isNull():
        return QImage()
    sample = cover.scaled(144, 144, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    pixels = _read_pixels(sample).astype(np.float32)
    sigma = {1: 4.0, 2: 7.0, 3: 11.0}.get(strength, 4.0)
    radius = round(sigma * 2.5)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    for axis in (0, 1):
        pixels = np.apply_along_axis(
            lambda line: np.convolve(np.pad(line, radius, mode="edge"), kernel, mode="valid"),
            axis, pixels,
        )
    data = np.ascontiguousarray(pixels.clip(0, 255), dtype=np.uint8)
    return QImage(data.data, sample.width(), sample.height(), sample.width() * 4,
                  QImage.Format.Format_RGBA8888).copy()
