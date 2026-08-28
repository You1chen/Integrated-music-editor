"""LRC Maker 应用图标生成器（极简风：渐变圆角方块 + 白色连音符）。

用法:
    python png/make_icon.py

生成的图标放在 png/ 目录下：
    icon.png   1024×1024 PNG 主图
    icon.ico   Windows 多尺寸 ICO（16/24/32/48/64/128/256），供 PyInstaller 打包用

打包接线（未来打包时改一行）:
    lrc-maker.spec 的 EXE(...) 里 icon=None 改为 icon="png/icon.ico"
"""

import struct
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QImageWriter,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QTransform,
)
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QGraphicsPathItem, QGraphicsScene

SIZE = 1024

APP = QApplication.instance() or QApplication([])

# ---------------------------------------------------------------- 背景

BG_PATH = QPainterPath()
BG_PATH.addRoundedRect(QRectF(0, 0, SIZE, SIZE), 232, 232)

# 渐变：靛蓝 → 紫罗兰
GRAD = QLinearGradient(QPointF(0, 0), QPointF(SIZE, SIZE))
GRAD.setColorAt(0.0, QColor("#4F63EE"))
GRAD.setColorAt(0.5, QColor("#6C4DF2"))
GRAD.setColorAt(1.0, QColor("#9340F9"))


def _ellipse(tx: float, ty: float, rx: float, ry: float, angle: float) -> QPainterPath:
    """以 (tx, ty) 为中心的旋转椭圆路径。"""
    p = QPainterPath()
    p.addEllipse(QRectF(-rx, -ry, 2 * rx, 2 * ry))
    return QTransform().translate(tx, ty).rotate(angle).map(p)


def build_glyph_path() -> QPainterPath:
    """白色连音符（♫）：两个音符头 + 两根符干 + 一道符杠。"""
    path = QPainterPath()

    # 音符头（微倾斜的椭圆）
    path.addPath(_ellipse(446, 668, 90, 78, -18))   # 左头
    path.addPath(_ellipse(640, 668, 90, 78, -18))   # 右头

    # 符干 -- 顶端藏进符杠内，避免圆头露出“小耳朵”
    path.addRoundedRect(QRectF(498, 282, 36, 394), 17, 17)
    path.addRoundedRect(QRectF(690, 282, 36, 394), 17, 17)

    # 符杠（向右上方微翘的平行四边形）
    beam = QPolygonF(
        [
            QPointF(492, 302),
            QPointF(492, 262),
            QPointF(734, 248),
            QPointF(734, 288),
        ]
    )
    path.addPolygon(beam)
    # 整组水平居中（画布中心 512），并弥补下方投影带来的视觉重心上移
    return QTransform().translate(-31, 10).map(path)


GLYPH = build_glyph_path()


# ---------------------------------------------------------------- 渲染

def render_master() -> QImage:
    master = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    master.fill(Qt.GlobalColor.transparent)

    painter = QPainter(master)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 1) 渐变圆角背景
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(BG_PATH, GRAD)

    # 2) 左上角柔光（提亮顶部，增加体积感）
    painter.setClipPath(BG_PATH)
    glow = QRadialGradient(QPointF(200, 130), 950)
    glow.setColorAt(0.0, QColor(255, 255, 255, 80))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(master.rect(), glow)
    painter.setClipping(False)

    # 3) 音符的柔和投影（先渲染投影层，再叠白色音符）
    painter.drawImage(0, 0, _shadow_layer())
    painter.fillPath(GLYPH, QColor("#FFFFFF"))
    painter.end()
    return master


def _shadow_layer() -> QImage:
    """用 QGraphicsDropShadowEffect 给音符做柔和投影（QPainter 本身不提供模糊）。"""
    base = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    base.fill(Qt.GlobalColor.transparent)
    p = QPainter(base)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.fillPath(GLYPH, QColor("#FFFFFF"))
    p.end()

    effect = QGraphicsDropShadowEffect()
    effect.setOffset(0, 24)
    effect.setBlurRadius(40)
    effect.setColor(QColor(20, 12, 60, 120))

    scene = QGraphicsScene()
    item = scene.addPixmap(QPixmap.fromImage(base))
    item.setGraphicsEffect(effect)

    out = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    scene.setSceneRect(QRectF(0, 0, SIZE, SIZE))
    scene.render(QPainter(out), source=QRectF(0, 0, SIZE, SIZE))
    return out


def scale_to(master: QImage, size: int) -> QImage:
    return master.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def png_bytes(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    QImageWriter(buf, b"png").write(img)
    return bytes(buf.data())


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256), master: QImage | None = None) -> None:
    """纯 Python 打包 ICO：ICO 支持直接内嵌 PNG 数据（Vista+）。"""
    if master is None:
        master = render_master()
    entries = [(s, png_bytes(scale_to(master, s))) for s in sizes]
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(entries)))
        offset = 6 + 16 * len(entries)
        for s, data in entries:
            b = 0 if s >= 256 else s  # 256 用 0 表示
            f.write(struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset))
            offset += len(data)
        for _, data in entries:
            f.write(data)


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    master = render_master()

    png_path = out_dir / "icon.png"
    master.save(str(png_path), "PNG")
    print(f"OK  {png_path}  ({SIZE}x{SIZE})")

    ico_path = out_dir / "icon.ico"
    write_ico(ico_path, master=master)
    print(f"OK  {ico_path}")


if __name__ == "__main__":
    main()
