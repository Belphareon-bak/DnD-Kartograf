"""Společné zobrazení mapy na plátně, v PDF a v PNG."""
from collections import defaultdict
from functools import lru_cache
from math import ceil, cos, floor, pi, sin, sqrt
from random import Random

from PyQt5.QtCore import QLineF, QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt
from PyQt5.QtGui import (QColor, QFont, QIcon, QImage, QLinearGradient, QPageLayout,
                         QPageSize, QPainter, QPainterPath, QPainterPathStroker, QPdfWriter,
                         QPen, QPixmap, QPolygonF, QRadialGradient, QTransform)

from catalog import ASSETS, GROUPS
from document import UNITS, Mark, atomic_path, cell_name, column_name

CELL = 48
DETAIL_TILE_STEP = 36
PDF_DPI = 180
LEGEND_STEP = 48
TEXELS = 72
PAPER = "#f2ebd9"
INK = "#353e35"
MONO = "IBM Plex Mono"
SANS = "IBM Plex Sans"
SERIF = "DejaVu Serif"

# Tahy ikon v políčku 24 × 24. Stejné značky se používají i při tisku.
GLYPHS = {
    "brush": (((7, 15), (16, 5), (19, 4), (20, 7), (11, 17), (7, 15)),
              ((7, 15), (5, 16), (4, 20), (8, 19), (11, 17))),
    "rect": (((4, 6), (20, 6), (20, 18), (4, 18), (4, 6)),),
    "fill": (((4, 11), (11, 4), (18, 11), (11, 18), (4, 11)),
             ((4, 11), (18, 11)), ((19, 14), (21, 18), (19, 20), (17, 18), (19, 14))),
    "erase": (((4, 14), (13, 5), (20, 12), (13, 19), (9, 19), (4, 14)), ((8, 10), (16, 17)), ((13, 19), (21, 19))),
    "select": (((6, 3), (19, 13), (13, 14), (16, 20), (13, 21), (10, 15), (6, 19), (6, 3)),),
    "hand": (((5, 13), (5, 9), (7, 9), (8, 13), (8, 5), (10, 4), (11, 5), (11, 11), (11, 3),
              (13, 3), (14, 5), (14, 11), (14, 5), (16, 5), (17, 7), (17, 13), (18, 9),
              (20, 9), (20, 15), (16, 21), (10, 21), (5, 13)),),
    "undo": (((10, 5), (5, 10), (10, 15)), ((5, 10), (15, 10), (19, 13), (19, 17), (16, 19), (12, 19))),
    "redo": (((14, 5), (19, 10), (14, 15)), ((19, 10), (9, 10), (5, 13), (5, 17), (8, 19), (12, 19))),
    "plus": (((12, 5), (12, 19)), ((5, 12), (19, 12))),
    "minus": (((5, 12), (19, 12)),),
    "fit": (((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
            ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15))),
    "grid": (((4, 4), (20, 4), (20, 20), (4, 20), (4, 4)), ((4, 12), (20, 12)), ((12, 4), (12, 20))),
    "atlas": (((3, 6), (9, 4), (15, 7), (21, 5), (21, 18), (15, 20), (9, 17), (3, 19), (3, 6)),
              ((9, 4), (9, 17)), ((15, 7), (15, 20))),
    "rotate": (((13, 4), (17, 5), (20, 8), (20, 14), (17, 19), (11, 20), (6, 17), (4, 12), (6, 7), (10, 5)),
               ((7, 3), (11, 5), (8, 9))),
    "save": (((5, 4), (16, 4), (20, 8), (20, 20), (4, 20), (4, 4), (5, 4)),
             ((8, 4), (8, 10), (16, 10), (16, 4)), ((8, 20), (8, 14), (16, 14), (16, 20))),
    "export": (((12, 3), (12, 15)), ((7, 10), (12, 15), (17, 10)), ((4, 15), (4, 20), (20, 20), (20, 15))),
    "chevron": (((7, 10), (12, 15), (17, 10)),),
    "more": (((5, 12), (6, 12)), ((11, 12), (12, 12)), ((17, 12), (18, 12))),
    "arrow": (((12, 19), (12, 5)), ((6, 11), (12, 5), (18, 11))),
    "stairs": (((4, 18), (8, 18), (8, 14), (12, 14), (12, 10), (16, 10), (16, 6), (20, 6)),),
    "stairs_down": (((4, 6), (8, 6), (8, 10), (12, 10), (12, 14), (16, 14), (16, 18), (20, 18)),),
    "portal": (((5, 19), (5, 11), (8, 6), (12, 4), (16, 6), (19, 11), (19, 19), (5, 19)), ((12, 8), (12, 16))),
    "secret": (((8, 8), (9, 5), (14, 5), (16, 8), (15, 11), (12, 13), (12, 15)), ((12, 19), (12.1, 19))),
    "monster": (((4, 8), (7, 5), (8, 9), (16, 9), (17, 5), (20, 8), (19, 16),
                 (15, 19), (9, 19), (5, 16), (4, 8)), ((8, 13), (10, 14)), ((16, 13), (14, 14)), ((10, 17), (14, 17))),
    "skull": (((6, 6), (10, 4), (14, 4), (18, 6), (20, 11), (18, 15), (16, 16), (16, 20),
               (8, 20), (8, 16), (6, 15), (4, 11), (6, 6)), ((8, 11), (10, 11)), ((14, 11), (16, 11)), ((12, 17), (12, 20))),
    "dragon": (((4, 7), (8, 8), (8, 4), (12, 7), (17, 6), (20, 10), (16, 11), (18, 14),
                (13, 15), (12, 19), (8, 20), (9, 15), (4, 12), (4, 7)), ((14, 9), (15, 9))),
    "paw": (((6, 17), (7, 14), (11, 11), (13, 11), (17, 14), (18, 17), (16, 20), (8, 20), (6, 17)),
            ((5, 8), (6, 9)), ((9, 5), (10, 7)), ((14, 5), (14, 7)), ((19, 8), (18, 9))),
    "chest": (((4, 10), (7, 6), (17, 6), (20, 10), (20, 18), (4, 18), (4, 10), (20, 10)), ((12, 10), (12, 14))),
    "coins": (((5, 8), (12, 5), (18, 8), (12, 11), (5, 8)), ((5, 8), (5, 15), (12, 19), (18, 15), (18, 8)),
              ((5, 12), (12, 15), (18, 12))),
    "gem": (((3, 10), (7, 5), (17, 5), (21, 10), (12, 20), (3, 10), (21, 10)),
            ((7, 5), (9, 10), (12, 20), (15, 10), (17, 5))),
    "trap": (((12, 4), (21, 19), (3, 19), (12, 4)), ((12, 9), (12, 13)), ((12, 16), (12.1, 16))),
    "pit": (((4, 5), (20, 5), (20, 19), (4, 19), (4, 5)), ((4, 5), (20, 19)), ((20, 5), (4, 19))),
    "rune": (((12, 4), (20, 12), (12, 20), (4, 12), (12, 4)), ((8, 12), (16, 12)), ((12, 8), (12, 16))),
    "bolt": (((13, 4), (6, 13), (11, 13), (10, 21), (18, 10), (13, 10), (13, 4)),),
    "ore": (((4, 16), (7, 8), (14, 5), (20, 11), (18, 18), (8, 20), (4, 16)), ((7, 8), (11, 14), (20, 11)), ((11, 14), (8, 20))),
    "text": (((5, 6), (19, 6)), ((12, 6), (12, 19)), ((8, 19), (16, 19))),
    "pin": (((12, 20), (5, 11), (6, 7), (9, 4), (15, 4), (18, 7), (19, 11), (12, 20)), ((12, 10), (12.1, 10))),
    "wall": (((4, 7), (20, 7), (20, 17), (4, 17), (4, 7)), ((4, 12), (20, 12)), ((10, 7), (10, 12)), ((15, 12), (15, 17))),
    "low_wall": (((4, 10), (20, 10), (20, 15), (4, 15), (4, 10)), ((9, 10), (9, 15)), ((15, 10), (15, 15))),
    "arch": (((5, 19), (5, 10), (8, 6), (12, 4), (16, 6), (19, 10), (19, 19)),),
    "door": (((6, 20), (6, 5), (18, 5), (18, 20)), ((6, 5), (14, 8), (14, 18), (6, 20)), ((11, 13), (11.1, 13))),
    "locked": (((6, 10), (18, 10), (18, 20), (6, 20), (6, 10)), ((9, 10), (9, 6), (12, 4), (15, 6), (15, 10)), ((12, 14), (12, 16))),
    "grate": (((4, 5), (20, 5), (20, 19), (4, 19), (4, 5)), ((9, 5), (9, 19)), ((15, 5), (15, 19)), ((4, 12), (20, 12))),
    "image": (((3, 5), (21, 5), (21, 19), (3, 19), (3, 5)), ((4, 16), (9, 11), (13, 15), (16, 12), (20, 16)), ((16, 9), (16.1, 9))),
    "raise_ground": (((5, 18), (19, 18)), ((12, 15), (12, 5)), ((7, 10), (12, 5), (17, 10))),
    "lower_ground": (((5, 6), (19, 6)), ((12, 9), (12, 19)), ((7, 14), (12, 19), (17, 14))),
    "level_ground": (((5, 9), (19, 9)), ((5, 15), (19, 15))),
    "slope_ground": (((4, 19), (20, 19)), ((4, 17), (20, 6), (20, 17)), ((8, 6), (14, 6)), ((11, 3), (11, 9))),
}


def font(family, pixels, bold=False):
    result = QFont(family)
    result.setPixelSize(pixels)
    result.setBold(bold)
    return result


def line(painter, a, b):
    painter.drawLine(QPointF(*a), QPointF(*b))


def poly(points):
    return QPolygonF([QPointF(*point) for point in points])


def pen(color, width):
    return QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def glyph(p, key, box, color):
    p.save()
    p.translate(box.topLeft())
    p.scale(box.width() / 24, box.height() / 24)
    p.setPen(pen(color, 1.8))
    for path in GLYPHS.get(key, GLYPHS["pin"]):
        p.drawPolyline(poly(path))
    p.restore()


def compass(p, center, size, turns=0, ink=INK, accent="#a7543a"):
    p.save()
    p.translate(center)
    p.setPen(pen(ink, 1))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(), size * 0.39, size * 0.39)
    p.rotate(turns * 90)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(accent))
    p.drawPolygon(poly(((0, -size * 0.57), (-size * 0.19, size * 0.13), (0, size * 0.02))))
    p.setBrush(QColor(ink))
    p.drawPolygon(poly(((0, -size * 0.57), (size * 0.19, size * 0.13), (0, size * 0.02))))
    p.drawPolygon(poly(((0, size * 0.36), (-size * 0.11, size * 0.12), (size * 0.11, size * 0.12))))
    p.restore()


@lru_cache(maxsize=140)
def texture(key, variant, turns):
    asset = ASSETS[key]
    style = asset.glyph
    rng = Random(f"{key}:{variant}")
    base = QColor(asset.color)
    light = base.lighter(123)
    dark = base.darker(125)
    image = QImage(72, 72, QImage.Format_RGB32)
    image.fill(base)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 0, 72, 72)
    gradient.setColorAt(0, base.lighter(106))
    gradient.setColorAt(1, base.darker(106))
    p.fillRect(image.rect(), gradient)
    if style in {"grass", "forest", "earth", "rock", "cave", "scree", "mountain", "solid", "lava"}:
        for _ in range(13):
            center = QPointF(rng.randrange(74), rng.randrange(74))
            radius = rng.uniform(6, 24)
            shade = QColor(dark if rng.random() < 0.55 else light)
            shade.setAlpha(rng.randrange(38, 95))
            wash = QRadialGradient(center, radius)
            wash.setColorAt(0, shade)
            shade.setAlpha(0)
            wash.setColorAt(1, shade)
            p.setPen(Qt.NoPen)
            p.setBrush(wash)
            p.drawEllipse(center, radius, radius * 0.82)
    for _ in range(58):
        tone = QColor(light if rng.random() > 0.45 else dark)
        tone.setAlpha(rng.randint(18, 90))
        p.setPen(pen(tone, rng.choice((0.65, 0.9, 1.35))))
        p.drawPoint(QPointF(rng.randrange(72), rng.randrange(72)))

    p.setPen(pen(dark, 0.8))
    if style in {"grass", "forest", "sand", "ice"}:
        for _ in range(10):
            x, y = rng.randrange(72), rng.randrange(72)
            p.setPen(pen(dark, 0.65))
            if style == "grass":
                p.drawPolyline(poly(((x - 2, y - 2), (x, y), (x + 1, y - 4))))
            else:
                line(p, (x, y), (x + rng.randrange(2, 8), y - 1))
        if style == "forest":
            trees = ((13, 17), (37, 12), (56, 19), (26, 37), (48, 41), (16, 60), (49, 60))
            for x, y in sorted(trees, key=lambda t: t[1]):
                x, y = x + rng.randint(-6, 6), y + rng.randint(-5, 5)
                radius = rng.uniform(5.5, 12.5)
                shape = tuple((x + cos(a * pi / 4) * radius * rng.uniform(0.8, 1.1),
                               y + sin(a * pi / 4) * radius * rng.uniform(0.8, 1.1)) for a in range(8))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(27, 38, 26, 119))
                p.drawPolygon(poly(tuple((u + 3, v + 3) for u, v in shape)))
                for face in range(8):
                    p.setBrush(base.darker(112 + abs(face - 5) * 10 + rng.randrange(13)))
                    p.drawPolygon(poly(((x - radius * 0.18, y - radius * 0.23), shape[face], shape[(face + 1) % 8])))
                p.setPen(pen(base.lighter(121), 0.7))
                p.drawPolyline(poly((shape[4], shape[5], (x - radius * 0.18, y - radius * 0.23), shape[7])))
    elif style == "water":
        for _ in range(6):
            x, y = rng.randrange(72), rng.randrange(72)
            p.setPen(pen(light, 1.0))
            p.drawPolyline(poly(((x, y), (x + 5, y - 1), (x + 11, y), (x + 15, y))))
            p.setPen(pen(dark, 0.65))
            line(p, (x + 2, y + 3), (x + 9, y + 3))
    elif style in {"rock", "cave", "chasm", "scree", "solid", "mountain", "lava"}:
        if style in {"mountain", "rock", "solid", "lava"}:
            for _ in range(9 if style == "mountain" else 6):
                x, y = rng.uniform(-6, 66), rng.uniform(-6, 70)
                n = rng.uniform(12, 36)
                p.setPen(Qt.NoPen)
                p.setBrush(base.darker(rng.randrange(115, 148)))
                p.drawPolygon(poly(((x, y + n * .15), (x + n * .54, y - n * .4), (x + n, y + n * .24),
                                    (x + n * .57, y + n * .63))))
                p.setBrush(base.lighter(rng.randrange(106, 137)))
                p.drawPolygon(poly(((x, y + n * .15), (x + n * .54, y - n * .4), (x + n * .45, y + n * .34))))
        for _ in range(6):
            x, y = rng.randrange(72), rng.randrange(72)
            p.setPen(pen(light if style == "chasm" else dark, 0.75))
            p.drawPolyline(poly(((x - 6, y + 7), (x, y), (x + 7, y - 1), (x + 15, y - 9))))
            for offset in (0, 4, 7):
                line(p, (x + offset, y + 2), (x + offset - 4, y + 7))
    elif style in {"stone", "brick", "marble", "mosaic", "stone_bridge", "roof"}:
        bands = 4 if style in {"brick", "mosaic"} else 3
        if style == "roof":
            bands = 7
        for row in range(bands):
            y = row * 72 / bands
            h = 72 / bands
            offset = 8 * (row % 2)
            line(p, (0, y), (72, y))
            for x in range(-int(offset), 73, 24):
                tone = QColor(light if rng.random() > 0.4 else dark)
                tone.setAlpha(rng.randrange(15, 56))
                p.fillRect(QRectF(x + 0.7, y + 0.7, 22.8, h - 1.4), tone)
                line(p, (x, y), (x, y + h))
        if style == "marble":
            p.setPen(pen(light, 1.5))
            line(p, (13, 55), (62, 11))
            line(p, (25, 32), (46, 19))
    elif style in {"wood", "bridge", "stairs", "grate"}:
        for at in range(0, 72, 11 if style == "stairs" else 12):
            if style in {"wood", "stairs", "grate"}:
                line(p, (0, at), (72, at))
            if style != "stairs":
                length = 72 if style == "grate" else rng.randrange(19, 69)
                y = rng.randrange(3, 14)
                line(p, (at, 0 if style != "wood" else y), (at, length))
    else:
        for _ in range(7):
            x, y = rng.randrange(72), rng.randrange(72)
            line(p, (x, y), (x + rng.randrange(1, 5), y))

    if style in {"bridge", "stone_bridge"}:
        p.setPen(pen(base.darker(160), 3))
        for y in (8, 64):
            line(p, (0, y), (72, y))
            p.setPen(pen(base.lighter(152), 1))
            line(p, (0, y - 2), (72, y - 2))
            p.setPen(pen(base.darker(160), 3))
    p.end()
    if asset.directional and turns:
        image = image.transformed(QTransform().rotate(turns * 90))
    return image


def corner_path(indices):
    """Malovaná hranice pro přechod dvou povrchů. Konce leží ve středu hrany."""
    path = QPainterPath()
    if len(indices) == 2 and (indices[1] - indices[0]) % 2 == 1:
        corner = indices[0] if indices[1] - indices[0] == 1 else indices[1]
        source = QPainterPath(QPointF(0, 0))
        source.lineTo(TEXELS, 0)
        source.lineTo(TEXELS, TEXELS / 2)
        source.cubicTo(TEXELS * .7, TEXELS * .57, TEXELS * .3, TEXELS * .43, 0, TEXELS / 2)
        source.closeSubpath()
        indices = (corner,)
    else:
        source = QPainterPath(QPointF(0, 0))
        source.lineTo(TEXELS / 2, 0)
        source.cubicTo(TEXELS * .46, TEXELS * .30, TEXELS * .27, TEXELS * .46, 0, TEXELS / 2)
        source.closeSubpath()
    mid = TEXELS / 2
    for corner in indices:
        transform = QTransform().translate(mid, mid).rotate(90 * corner).translate(-mid, -mid)
        path.addPath(transform.map(source))
    return path


@lru_cache(maxsize=384)
def graphics_for_layout(style, corners, crossings, variant, turns):
    image = texture(style, variant, turns if ASSETS[style].directional else 0).copy()
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    for surface in sorted(set(corners) - {style}):
        indices = tuple(i for i, terrain in enumerate(corners) if terrain == surface)
        p.save()
        p.setClipPath(corner_path(indices))
        p.drawImage(QPointF(), texture(surface, variant, 0))
        p.restore()
    mid = TEXELS / 2
    ends = (QPointF(mid, 0), QPointF(TEXELS, mid), QPointF(mid, TEXELS), QPointF(0, mid))
    center = QPointF(mid, mid)
    priorities = ("cave", "flagstone", "path", "paving", "river", "bridge", "stone_bridge", "solid")
    widths = (33, 32, 18, 24, 11, 19, 19, 7)
    for surface, width in zip(priorities, widths):
        sides = [i for i, value in enumerate(crossings) if value == surface]
        if not sides:
            continue
        stroke = QPainterPath()
        if len(sides) == 2:
            stroke.moveTo(ends[sides[0]])
            stroke.quadTo(center, ends[sides[1]])
        else:
            for side in sides:
                stroke.moveTo(center)
                stroke.lineTo(ends[side])
        p.setPen(pen(QColor(39, 31, 26, 105), width + 2.3))
        p.drawPath(stroke)
        mask = QPainterPathStroker()
        mask.setWidth(width)
        mask.setCapStyle(Qt.RoundCap)
        p.save()
        p.setClipPath(mask.createStroke(stroke))
        orientation = int(surface in ("bridge", "stone_bridge") and all(i in (0, 2) for i in sides))
        p.drawImage(QPointF(), texture(surface, variant, orientation))
        p.restore()
    p.end()
    return image


def _relief_face(p, start, end, toward_neighbor, difference_a, difference_b):
    if not (difference_a or difference_b):
        return
    if difference_a * difference_b < 0:
        cross = start + (end - start) * (difference_a / (difference_a - difference_b))
        _relief_face(p, start, cross, toward_neighbor, difference_a, 0)
        _relief_face(p, cross, end, toward_neighbor, 0, difference_b)
        return
    average = (difference_a + difference_b) / 2
    to_low = toward_neighbor if average > 0 else -toward_neighbor
    extent = min(15.5, 4.5 + abs(average) * 3.7)
    lit = to_low.x() + to_low.y() < 0
    first = min(190, 90 + round(abs(average) * 13))
    face = QLinearGradient(start, start + to_low * extent)
    face.setColorAt(0, QColor(35, 26, 23, first))
    face.setColorAt(.48, QColor(64, 46, 38, first // 2))
    face.setColorAt(1, QColor(35, 32, 30, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(face)
    p.drawPolygon(QPolygonF((start, end, end + to_low * extent, start + to_low * extent)))
    p.setPen(pen(QColor(46, 40, 33, 130), .95))
    p.drawLine(start, end)
    p.setPen(pen(QColor(249, 227, 184, 168 if lit else 112), 1.25))
    rim = to_low * 1.45
    p.drawLine(start - rim, end - rim)
    if abs(average) >= 1.5:
        p.setPen(pen(QColor(251, 221, 172, 39), .95))
        p.drawLine(start + to_low * (extent * .49), end + to_low * (extent * .49))


def draw_relief(p, area, visible):
    if not (area.heights or any(tile.layout and any(tile.layout.offsets) for tile in area.tiles.values())):
        return
    left, top = max(0, floor(visible.left() / CELL)), max(0, floor(visible.top() / CELL))
    right = min(area.cols, ceil(visible.right() / CELL))
    bottom = min(area.rows, ceil(visible.bottom() / CELL))
    levels = {(x, y): area.corner_heights(x, y)
              for y in range(top - 1, bottom + 1) for x in range(left - 1, right + 1)}
    for y in range(top, bottom):
        for x in range(left, right):
            nw, ne, se, sw = levels[x, y]
            if not (nw or ne or se or sw):
                continue
            rect = QRectF(x * CELL, y * CELL, CELL, CELL)
            average = (nw + ne + se + sw) / 4
            tint = QColor(228, 206, 154, min(26, round(average * 7))) if average >= 0 else QColor(
                37, 48, 62, min(45, round(-average * 13)))
            p.fillRect(rect, tint)
            dx, dy = ne + se - nw - sw, sw + se - nw - ne
            if dx or dy:
                scale = CELL / (2 * (abs(dx) + abs(dy)))
                offset = QPointF(dx * scale, dy * scale)
                shade = QLinearGradient(rect.center() - offset, rect.center() + offset)
                shade.setColorAt(0, QColor(62, 47, 38, 49))
                shade.setColorAt(1, QColor(245, 222, 179, 62))
                p.fillRect(rect, shade)
    for y in range(top, bottom):
        for x in range(left, right):
            nw, ne, se, sw = levels[x, y]
            a, b = x * CELL, y * CELL
            right_levels = levels[x + 1, y]
            below = levels[x, y + 1]
            _relief_face(p, QPointF(a + CELL, b), QPointF(a + CELL, b + CELL), QPointF(1, 0),
                         ne - right_levels[0], se - right_levels[3])
            _relief_face(p, QPointF(a, b + CELL), QPointF(a + CELL, b + CELL), QPointF(0, 1),
                         sw - below[0], se - below[1])
            if x == 0:
                _relief_face(p, QPointF(a, b), QPointF(a, b + CELL), QPointF(-1, 0), nw, sw)
            if y == 0:
                _relief_face(p, QPointF(a, b), QPointF(a + CELL, b), QPointF(0, -1), nw, ne)


def draw_badge(p, item, where, radius):
    asset = ASSETS[item.style]
    p.save()
    p.translate(where)
    p.setPen(pen("#fff2dc", max(1, radius * 0.14)))
    p.setBrush(QColor(asset.color))
    p.drawEllipse(QPointF(), radius, radius)
    if asset.directional:
        p.rotate(item.turns * 90)
    glyph(p, asset.glyph, QRectF(-radius * 0.88, -radius * 0.88, radius * 1.76, radius * 1.76), "#fff4e3")
    p.restore()


@lru_cache(maxsize=210)
def icon(key, size=22, ink="#b9c2b9"):
    picture = QPixmap(size, size)
    picture.fill(Qt.transparent)
    p = QPainter(picture)
    p.setRenderHint(QPainter.Antialiasing)
    if key == "compass":
        compass(p, QPointF(size / 2, size / 2), size * 0.81, ink=ink, accent="#d59b5d")
    elif key in ASSETS:
        asset = ASSETS[key]
        if asset.layer == "tiles":
            p.drawImage(QRectF(1, 1, size - 2, size - 2), texture(key, 0, 0))
            p.setPen(pen("#c1b593", 0.7))
            p.drawRect(QRectF(1, 1, size - 2, size - 2))
        else:
            draw_badge(p, Mark.from_asset(key), QPointF(size / 2, size / 2), size * 0.45)
    else:
        glyph(p, key, QRectF(0, 0, size, size), ink)
    p.end()
    return QIcon(picture)


def visible_marks(area, player=False):
    grouped = defaultdict(list)
    for key, mark in sorted(area.markers.items()):
        if not (player and mark.hidden):
            grouped[key[:2]].append((key, mark))
    return grouped


def positions(cell, items):
    x, y = cell
    count = len(items)
    columns = ceil(sqrt(count))
    step = CELL / columns
    radius = min(14, step * 0.43)
    return [
        (key, mark, QPointF(x * CELL + (index % columns + 0.5) * step,
                            y * CELL + (index // columns + 0.5) * step), radius)
        for index, (key, mark) in enumerate(items)
    ]


def draw_edge(p, key, item, player=False):
    x, y, axis = key
    asset = ASSETS["wall" if player and item.hidden else item.style]
    p.save()
    p.translate(x * CELL, y * CELL)
    if axis == "v":
        p.rotate(90)
    if asset.group == "indoor":
        p.setPen(pen("#382e29", 7 if asset.glyph == "wall" else 4))
        line(p, (0, 0), (CELL, 0))
        p.setPen(pen(asset.color if asset.glyph == "low_wall" else QColor(asset.color).lighter(137), 3))
        line(p, (0, -0.7), (CELL, -0.7))
    else:
        p.setPen(pen("#f2e6cb", 7))
        line(p, (5, 0), (CELL - 5, 0))
        p.setPen(pen(asset.color, 3))
        line(p, (5, 0), (CELL - 5, 0))
        for post in (5, CELL - 5):
            line(p, (post, -5), (post, 5))
        if asset.glyph != "arch":
            p.setBrush(QColor(asset.color))
            p.setPen(pen("#f5ecd8", 1))
            p.drawEllipse(QPointF(CELL / 2, 0), 7.7, 7.7)
            glyph(p, asset.glyph, QRectF(CELL / 2 - 7, -7, 14, 14), "#fff2d7")
    p.restore()


@lru_cache(maxsize=2)
def reference_image(data):
    return QImage.fromData(data)


def draw_reference(p, ref):
    image = reference_image(ref.data)
    if image.isNull():
        return
    target = QRectF(ref.x * CELL, ref.y * CELL, ref.w * CELL, ref.h * CELL)
    p.save()
    p.setOpacity(ref.opacity)
    p.translate(target.center())
    p.rotate(ref.turns * 90)
    w, h = (target.height(), target.width()) if ref.turns % 2 else (target.width(), target.height())
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(-w / 2, -h / 2, w, h), image)
    p.restore()


def draw_cell_grid(p, bounds, clip, step, *, scale=1.0):
    visible = bounds.intersected(clip)
    if visible.isEmpty():
        return
    stride = max(1, ceil(8 / (step * scale)))
    lines = {False: [], True: []}

    def ticks(origin, left, right, count):
        first = max(1, ceil((left - origin) / (step * stride)))
        end = min(count, floor((right - origin) / (step * stride)))
        for index in range(first, end + 1):
            yield origin + index * stride * step, index % 5 == 0

    cols = ceil(bounds.width() / (step * stride)) - 1
    rows = ceil(bounds.height() / (step * stride)) - 1
    for x, major in ticks(bounds.left(), visible.left(), visible.right(), cols):
        lines[major].append(QLineF(x, visible.top(), x, visible.bottom()))
    for y, major in ticks(bounds.top(), visible.top(), visible.bottom(), rows):
        lines[major].append(QLineF(visible.left(), y, visible.right(), y))
    p.save()
    for major, segments in lines.items():
        if not segments:
            continue
        width = 2.05 if major else 1.6
        p.setPen(pen(QColor(255, 250, 237, 153 if major else 135), (width + 1.25) / scale))
        p.drawLines(segments)
        color = QColor(31, 29, 27, 238 if major else 213)
        p.setPen(pen(color, width / scale))
        p.drawLines(segments)
    p.restore()


@lru_cache(maxsize=768)
def native_tile_image(raw, turns):
    image = QImage.fromData(raw, b"png")
    if turns:
        image = image.transformed(QTransform().rotate(90 * turns))
    return image


@lru_cache(maxsize=128)
def interior_tile_image(raw, turns, empty_color):
    image = native_tile_image(raw, turns).copy()
    if not image.isNull():
        mask = image.createMaskFromColor(QColor(empty_color).rgb(), Qt.MaskOutColor)
        image.setAlphaChannel(mask.convertToFormat(QImage.Format_Grayscale8))
    return image


def interior_contains(area, point, empty_color):
    x, y = floor(point[0]), floor(point[1])
    tile = area.tiles.get((x, y))
    if tile is None:
        return False
    if tile.layout is None:
        return True
    layout = tile.layout
    if layout.minimap:
        image = interior_tile_image(layout.minimap, tile.turns, empty_color)
    else:
        image = native_tile_image(layout.detail, tile.turns)
    return not image.isNull() and image.pixelColor(int((point[0] - x) * image.width()),
                                                  int((point[1] - y) * image.height())).alpha() > 0


def draw_scenery(p, items, visible):
    for item in sorted(items, key=lambda obj: obj.elevation):
        at = QPointF(*item.point) * CELL
        if item.preview:
            x, y, width, height = item.preview.bounds
            rect = QRectF(x * CELL, y * CELL, width * CELL, height * CELL)
            p.save()
            p.translate(at)
            p.rotate(item.angle)
            p.drawImage(rect, native_tile_image(item.preview.data, 0))
            p.restore()
        elif visible.contains(at):
            p.setPen(pen("#594734", .9))
            p.setBrush(QColor("#f1d59d"))
            p.drawEllipse(at, 2.6, 2.6)


def draw_map(p, area, target, grid=True, reference=True, player=False, clip=None, annotations=True, scenery=True,
             transparent_color=None):
    """Target is the bounds of the printable cells. Clip is in device coordinates."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    scale = target.width() / (area.cols * CELL)
    p.translate(target.topLeft())
    p.scale(scale, scale)
    w, h = area.cols * CELL, area.rows * CELL
    transparent = transparent_color is not None
    bounds = QRectF(0, 0, w, h)
    if clip is None:
        visible = bounds
    else:
        visible = QRectF((clip.x() - target.x()) / scale, (clip.y() - target.y()) / scale,
                         clip.width() / scale, clip.height() / scale).adjusted(-CELL, -CELL, CELL, CELL)
    if not transparent:
        p.fillRect(bounds, QColor(PAPER))
    ref = area.reference if reference and area.reference and area.reference.visible else None
    detailed = target.width() / area.cols >= DETAIL_TILE_STEP
    p.save()
    p.setClipRect(bounds, Qt.IntersectClip)
    if ref and not ref.above:
        draw_reference(p, ref)
    tiles = area.tiles.items()
    if clip is not None:
        xs = range(max(0, floor(visible.left() / CELL)), min(area.cols, ceil(visible.right() / CELL)))
        ys = range(max(0, floor(visible.top() / CELL)), min(area.rows, ceil(visible.bottom() / CELL)))
        tiles = (((x, y), tile) for y in ys for x in xs if (tile := area.tiles.get((x, y))) is not None)
    for (x, y), tile in tiles:
        rect = QRectF(x * CELL, y * CELL, CELL, CELL)
        if tile.layout:
            layout = tile.layout
            if layout.detail and (detailed or not layout.minimap):
                detail = native_tile_image(layout.detail, tile.turns)
                if not detail.isNull():
                    if not transparent:
                        p.fillRect(rect, QColor("#050403"))
                    p.drawImage(rect, detail)
                    continue
            if layout.minimap:
                image = (interior_tile_image(layout.minimap, tile.turns, transparent_color)
                         if transparent else native_tile_image(layout.minimap, tile.turns))
                if not image.isNull():
                    p.drawImage(rect, image)
                    continue
            if transparent:
                continue
        asset = ASSETS[tile.style]
        turns = tile.turns if asset.directional else 0
        variant = ((x * 11) ^ (y * 7)) % 7
        if tile.layout and (len(set(tile.layout.corners)) > 1 or any(tile.layout.crossings)):
            source = graphics_for_layout(tile.style, tile.oriented(tile.layout.corners),
                                         tile.oriented(tile.layout.crossings), variant, turns)
        else:
            source = texture(tile.style, variant, turns)
        p.drawImage(rect, source)
    draw_relief(p, area, visible)
    if scenery:
        draw_scenery(p, area.scenery, visible)
    if ref and ref.above:
        draw_reference(p, ref)
    p.restore()

    if grid:
        draw_cell_grid(p, bounds, visible, CELL, scale=scale)
    if not transparent:
        p.setPen(pen("#867e66", 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bounds)

    if grid and annotations:
        p.setPen(QColor("#aea48f") if clip is not None else QColor("#6d6553"))
        p.setFont(font(MONO, 12))
        stride = max(1, ceil(22 / (CELL * scale)))
        for x in range(0, area.cols, stride):
            pos = x * CELL
            if visible.left() - CELL <= pos <= visible.right():
                p.drawText(QRectF(pos, -25, CELL, 21), Qt.AlignCenter, column_name(x))
        for y in range(0, area.rows, stride):
            pos = y * CELL
            if visible.top() - CELL <= pos <= visible.bottom():
                p.drawText(QRectF(-39, pos, 32, CELL), Qt.AlignRight | Qt.AlignVCenter, str(y + 1))

    for key, item in area.edges.items():
        if clip is None or visible.contains(QPointF(key[0] * CELL, key[1] * CELL)):
            draw_edge(p, key, item, player)

    if not annotations:
        p.restore()
        return

    labels = []
    for cell, items in visible_marks(area, player).items():
        if clip is not None and not visible.contains(QPointF(cell[0] * CELL, cell[1] * CELL)):
            continue
        for _, item, where, radius in positions(cell, items):
            if item.style == "label" and item.name:
                labels.append((item.name, where))
            else:
                draw_badge(p, item, where, radius)
    p.setFont(font(SERIF, 13, True))
    for name, where in labels:
        text = p.fontMetrics().elidedText(name, Qt.ElideRight, min(w - 10, 230))
        width = p.fontMetrics().horizontalAdvance(text) + 16
        x = min(max(4, where.x() - width / 2), w - width - 4)
        box = QRectF(x, where.y() - 10, width, 20)
        p.setPen(pen("#9e9376", 0.8))
        p.setBrush(QColor(247, 239, 218, 238))
        p.drawRoundedRect(box, 3, 3)
        p.setPen(QColor(INK))
        p.drawText(box, Qt.AlignCenter, text)
    p.restore()


def fit_map(area, bounds):
    scale = min(bounds.width() / area.cols, bounds.height() / area.rows)
    width, height = area.cols * scale, area.rows * scale
    return QRectF(bounds.center().x() - width / 2, bounds.center().y() - height / 2, width, height)


def legend_rows(project, area, player):
    used = defaultdict(list)
    for layer in (area.edges, area.markers):
        for key, item in sorted(layer.items()):
            asset = ASSETS[item.style]
            if (player and item.hidden) or asset.group in {"indoor", "label"}:
                continue
            name = item.name or asset.name
            target = item.target
            suffix = f"CR {item.cr}" if item.cr else ""
            if item.dc is not None:
                suffix = f"TO / DC {item.dc}"
            if target and target.area in project.areas:
                suffix = f"→ {cell_name(target.x, target.y)}\n{project.areas[target.area].name}"
            if not item.name and not target and not item.note:
                index = (asset.group, item.style, name, suffix)
            else:
                index = (asset.group, item.style, name, suffix, key)
            used[index].append(key)
    rows = []
    for entry, locations in sorted(used.items(), key=lambda entry: (list(GROUPS).index(entry[0][0]), entry[0][2])):
        _, key, name, detail = entry[:4]
        x, y = locations[0][:2]
        where = cell_name(min(x, area.cols - 1), min(y, area.rows - 1))
        if len(locations) > 1:
            name = f"{name} × {len(locations)}"
            where = ""
        combined = f"{where} {detail}" if detail.startswith("→") else " · ".join(v for v in (where, detail) if v)
        rows.append((key, name, combined))
    return rows


def _text(p, text, box, size, face=SANS, color=INK, bold=False, align=Qt.AlignLeft | Qt.AlignVCenter):
    p.setFont(font(face, size, bold))
    p.setPen(QColor(color))
    display = p.fontMetrics().elidedText(text, Qt.ElideRight, max(0, int(box.width())))
    p.drawText(box, align, display)


def sheet(p, project, area, width, height, rows, grid, reference, player, appendix=0):
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(QRectF(0, 0, width, height), QColor("#fbf7ec"))
    edge = 35
    top = 129
    p.setPen(pen("#bcac8d", 1))
    line(p, (edge, 103), (width - edge, 103))
    _text(p, project.title, QRectF(edge, 24, width - 180, 22), 16, MONO, "#786c58")
    title = f"{area.name} · legenda {appendix}" if appendix else area.name
    _text(p, title, QRectF(edge, 49, width - 155, 46), 33, SERIF, bold=True)
    compass(p, QPointF(width - edge - 28, 58), 48, area.north)
    shifts = ((0, -37), (37, 0), (0, 37), (-37, 0))
    dx, dy = shifts[area.north]
    _text(p, "S", QRectF(width - edge - 35 + dx, 50 + dy, 14, 16), 12, MONO, align=Qt.AlignCenter)
    footer = f"{area.cols} × {area.rows} polí  ·  {UNITS[area.unit][0]}  ·  {UNITS[area.unit][1]}"
    lowest, highest = min(0, min(area.heights.values(), default=0)), max(0, max(area.heights.values(), default=0))
    for key, tile in area.tiles.items():
        if tile.layout and any(tile.layout.offsets):
            values = area.corner_heights(*key)
            lowest, highest = min(lowest, *values), max(highest, *values)
    if lowest or highest:
        footer += f"  ·  výškové stupně {lowest:+d} až {highest:+d}"
    _text(p, footer,
          QRectF(edge, height - 53, width - edge * 2, 27), 13, MONO, "#786c58")
    if appendix:
        _legend(p, rows, QRectF(edge, top, width - edge * 2, height - top - 60))
        return
    side = min(240, int(width * 0.30)) if rows else 0
    map_box = QRectF(edge + 34, top + 22, width - 2 * edge - side - 36, height - top - 84)
    draw_map(p, area, fit_map(area, map_box), grid=grid, reference=reference, player=player)
    if rows:
        _legend(p, rows, QRectF(width - edge - side + 17, top, side - 18, height - top - 66))


def _legend(p, rows, box):
    p.setPen(pen("#bcac8d", 1))
    line(p, (box.left(), box.top() + 30), (box.right(), box.top() + 30))
    _text(p, "Legenda", QRectF(box.x(), box.y(), box.width(), 27), 18, SERIF, bold=True)
    y = box.y() + 42
    for key, title, detail in rows:
        draw_badge(p, Mark.from_asset(key), QPointF(box.x() + 12, y + 10), 10)
        _text(p, title, QRectF(box.x() + 28, y - 3, box.width() - 27, 19), 14, bold=True)
        for index, part in enumerate(detail.splitlines()):
            _text(p, part, QRectF(box.x() + 28, y + 14 + 14 * index, box.width() - 27, 16), 12, MONO, "#786c58")
        y += LEGEND_STEP


def area_image_frame(area, pixels, row_count):
    width = max(540, area.cols * pixels + (375 if row_count else 110))
    height = max(310, area.rows * pixels + 219, row_count * LEGEND_STEP + 255)
    return width, height


def bounded_image_size(width, height):
    factor = min(1.0, 12000 / width, 12000 / height, sqrt(32_000_000 / (width * height)))
    return QSize(max(1, int(width * factor)), max(1, int(height * factor)))


def area_image_sheet(p, project, area, width, height, rows, grid, reference, player):
    # Použij shodné rozvržení pro PNG a PDF s automatickou velikostí.
    ratio = max(1.0, width / 1420)
    if height / ratio < 270:
        ratio = height / 270
    if len(rows) <= max(0, int((height / ratio - 235) // LEGEND_STEP)):
        p.scale(ratio, ratio)
        width, height = width / ratio, height / ratio
    sheet(p, project, area, width, height, rows, grid, reference, player)


def write_png(path, size, paint):
    image = QImage(size, QImage.Format_RGB32)
    if image.isNull():
        raise ValueError("Pro vykreslení obrázku není dost paměti.")
    p = QPainter(image)
    try:
        paint(p)
    finally:
        p.end()
    with atomic_path(path) as output:
        if not image.save(str(output), "PNG"):
            raise OSError("PNG se nepodařilo zapsat.")


def export_png(project, area, path, pixels=CELL, grid=True, legend=True, reference=False, player=False):
    rows = legend_rows(project, area, player) if legend else []
    width, height = area_image_frame(area, pixels, len(rows))
    size = bounded_image_size(width, height)

    def paint(p):
        p.scale(size.width() / width, size.height() / height)
        area_image_sheet(p, project, area, width, height, rows, grid, reference, player)

    write_png(path, size, paint)


def pdf_layout(page_size, landscape=False):
    if isinstance(page_size, QSize):
        points = QSizeF(page_size) * (72 / PDF_DPI)
        size = QPageSize(points, QPageSize.Point, "Mapa", QPageSize.ExactMatch)
        return QPageLayout(size, QPageLayout.Portrait, QMarginsF())
    size = QPageSize(QPageSize.A3 if page_size == "A3" else QPageSize.A4)
    direction = QPageLayout.Landscape if landscape else QPageLayout.Portrait
    return QPageLayout(size, direction, QMarginsF())


def pdf_writer(output, title, layout):
    pdf = QPdfWriter(str(output))
    pdf.setTitle(title)
    pdf.setCreator("Kartograf")
    pdf.setResolution(PDF_DPI)
    if not pdf.setPageLayout(layout):
        raise ValueError("Velikost stránky PDF se nepodařilo nastavit.")
    return pdf


def export_pdf(project, areas, path, page_size="A4", landscape=True, grid=True, legend=True,
               reference=False, player=False):
    with atomic_path(path) as output:
        pdf = pdf_writer(output, project.title, pdf_layout(page_size, landscape))
        width, height = (1240, 877) if landscape else (877, 1240)
        capacity = (height - 235) // LEGEND_STEP
        p = QPainter(pdf)
        if not p.isActive():
            raise OSError("PDF se nepodařilo otevřít k zápisu.")
        try:
            for index, area in enumerate(areas):
                if index and not pdf.newPage():
                    raise OSError("PDF nemůže přidat další stránku.")
                rows = legend_rows(project, area, player) if legend else []
                for page, offset in enumerate(range(0, max(1, len(rows)), capacity)):
                    if page and not pdf.newPage():
                        raise OSError("PDF nemůže přidat stránku legendy.")
                    p.save()
                    p.scale(pdf.width() / width, pdf.height() / height)
                    sheet(p, project, area, width, height, rows[offset:offset + capacity],
                          grid, reference, player, appendix=page)
                    p.restore()
        finally:
            p.end()
        del pdf
        if output.stat().st_size < 100:
            raise OSError("PDF se nepodařilo zapsat.")


def atlas_sheet(p, atlas, width, height, grid=False, legend=True, reference=False, player=False):
    x, y, right, bottom = atlas.bounds
    if right <= x or bottom <= y:
        raise ValueError("Mapa světa nemá žádnou oblast.")
    viewport = QRectF(24, 96, width - 48, height - 120)
    # Soubor používá celý obdélník mapy. Kamera slouží jen k prohlížení.
    step = min((viewport.width() - 68) / (right - x), (viewport.height() - 44) / (bottom - y))
    origin = viewport.center() - QPointF((x + right) / 2, (y + bottom) / 2) * step
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(QRectF(0, 0, width, height), QColor("#fbf7ec"))
    _text(p, atlas.project.title, QRectF(26, 19, width - 110, 43), 33, SERIF, bold=True)
    _text(p, atlas.scope_summary, QRectF(27, 64, width - 110, 20), 15, MONO, "#786c58")
    compass(p, QPointF(width - 54, 54), 35)
    _text(p, "S", QRectF(width - 62, 17, 16, 16), 12, MONO, align=Qt.AlignCenter)
    atlas.paint_board(p, viewport, origin, step, grid=grid, labels=legend, reference=reference,
                      entrances=legend and atlas.show_pins, player=player, printing=True)


def atlas_image_size(atlas, pixels):
    x, y, right, bottom = atlas.bounds
    width = max(540, ceil((right - x) * pixels) + 116)
    height = max(310, ceil((bottom - y) * pixels) + 164)
    return bounded_image_size(width, height).expandedTo(QSize(540, 310))


def export_size(project, area, atlas, kind, preset, landscape, legend, player):
    if kind == "pdf" and preset != "auto":
        return pdf_layout(preset, landscape).fullRectPixels(PDF_DPI).size()
    pixels = CELL if preset == "auto" else preset
    if atlas is not None:
        size = atlas_image_size(atlas, pixels)
    else:
        count = len(legend_rows(project, area, player)) if legend else 0
        size = bounded_image_size(*area_image_frame(area, pixels, count))
    return pdf_layout(size).fullRectPixels(PDF_DPI).size() if kind == "pdf" else size


def export_atlas_png(atlas, path, pixels=CELL, **options):
    size = atlas_image_size(atlas, pixels)
    write_png(path, size, lambda p: atlas_sheet(p, atlas, size.width(), size.height(), **options))


def write_pdf_sheet(path, title, layout, width, height, paint):
    with atomic_path(path) as output:
        pdf = pdf_writer(output, title, layout)
        painter = QPainter(pdf)
        if not painter.isActive():
            raise OSError("PDF se nepodařilo otevřít k zápisu.")
        try:
            painter.scale(pdf.width() / width, pdf.height() / height)
            paint(painter)
        finally:
            painter.end()
        del pdf
        if output.stat().st_size < 100:
            raise OSError("PDF se nepodařilo zapsat.")


def export_atlas_pdf(atlas, path, page_size="A4", landscape=False, **options):
    if page_size == "auto":
        size = atlas_image_size(atlas, CELL)
        width, height = size.width(), size.height()
        layout = pdf_layout(size)
    else:
        width, height = (1240, 877) if landscape else (877, 1240)
        layout = pdf_layout(page_size, landscape)
    write_pdf_sheet(path, atlas.project.title, layout, width, height,
                    lambda p: atlas_sheet(p, atlas, width, height, **options))


def export_area_pdf(project, area, path, page_size, landscape, grid=True, legend=True,
                    reference=False, player=False):
    if page_size != "auto":
        export_pdf(project, [area], path, page_size, landscape, grid, legend, reference, player)
        return
    rows = legend_rows(project, area, player) if legend else []
    width, height = area_image_frame(area, CELL, len(rows))
    layout = pdf_layout(bounded_image_size(width, height))
    write_pdf_sheet(path, project.title, layout, width, height,
                    lambda p: area_image_sheet(p, project, area, width, height, rows, grid, reference, player))
