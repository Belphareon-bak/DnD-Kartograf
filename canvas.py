"""Plátno se štětcem, nástroji pro výplň a kamerou."""
from collections import deque
from dataclasses import replace
from math import ceil, floor, hypot

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QLinearGradient, QPainter
from PyQt5.QtWidgets import QShortcut, QWidget

from catalog import ASSETS, RELIEF
from document import MAX_LEVEL, Mark, Patch, Tile, TileLayout, cell_name, set_marker
from drawing import CELL, draw_map, fit_map, pen, positions

TOOLS = {
    "brush": ("Štětec", "B", "Maluj tahem. Pravé tlačítko maže v aktivní vrstvě."),
    "rect": ("Obdélník", "U", "Tahem vyznač obdélník pro vybraný podklad."),
    "fill": ("Výplň", "G", "Vyplní souvislou plochu podkladu. Zdi a dveře výplň zastaví."),
    "erase": ("Guma", "E", "Maže prvek pod kurzorem. Shift maže jen podklad."),
    "select": ("Výběr", "V", "Kliknutím vyber prvek a uprav jeho vlastnosti."),
    "hand": ("Ruka", "H", "Posouvej plátno tahem. Kolečkem měníš přiblížení."),
}


class Canvas(QWidget):
    selection_changed = pyqtSignal()
    hover_changed = pyqtSignal(str)
    zoom_changed = pyqtSignal(int)
    followed = pyqtSignal(object)

    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.history = history
        self.project = self.area = None
        self.tool, self.asset, self.turns = "brush", "grass", 0
        self.selection = None
        self.grid = True
        self.step = 36.0
        self.origin = QPointF()
        self._fit = True
        self._pan = self._mouse_start = self._last_draw = self._rect_start = None
        self._stroke = None
        self._button = Qt.NoButton
        self._right = self._space = False
        self._hover = None
        self._height_touched = set()
        self._pending_selection = None
        self._shortcuts = []
        self.setObjectName("mapCanvas")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Plátno mapy")
        self.setCursor(Qt.CrossCursor)

    def sizeHint(self):
        return QSize(750, 600)

    def bind(self, key, callback):
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.setContext(Qt.WidgetShortcut)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)

    def show_area(self, project, area, reset=False):
        self.project, self.area = project, area
        if reset:
            self.selection = None
            self._hover = None
            self.hover_changed.emit("")
            self.fit()
        self.update()

    def set_tool(self, tool):
        self.tool = tool
        self.setCursor(Qt.OpenHandCursor if tool == "hand" else Qt.ArrowCursor if tool == "select" else Qt.CrossCursor)
        self.update()

    def fit(self):
        if not self.area:
            return
        bounds = QRectF(self.rect()).adjusted(55, 30, -31, -31)
        fitted = fit_map(self.area, bounds)
        self.step = min(52.0, max(1, fitted.width() / self.area.cols))
        self.origin = bounds.center() - QPointF(self.area.cols * self.step / 2, self.area.rows * self.step / 2)
        self._fit = True
        self.report_zoom()

    def report_zoom(self):
        self.zoom_changed.emit(round(self.step / 36 * 100))
        self.update()

    def zoom(self, multiplier, anchor=None):
        if not self.area or self._button != Qt.NoButton:
            return
        if anchor is None:
            anchor = QPointF(self.rect().center())
        step = min(160, max(1, self.step * multiplier))
        self.origin = anchor - (anchor - self.origin) * (step / self.step)
        self.step = step
        self._fit = False
        self.report_zoom()

    def resizeEvent(self, event):
        if self._fit:
            self.fit()
        super().resizeEvent(event)

    def map_rect(self):
        return QRectF(self.origin.x(), self.origin.y(), self.area.cols * self.step, self.area.rows * self.step)

    def grid_pos(self, screen):
        return (screen - self.origin) / self.step

    def cell_at(self, point):
        return floor(point.x()), floor(point.y())

    def edge_at(self, point, along=None):
        x, y = self.cell_at(point)
        fx, fy = point.x() - x, point.y() - y
        distance_v, distance_h = min(fx, 1 - fx), min(fy, 1 - fy)
        if (along == "v" and distance_v <= 0.34
                or not (along == "h" and distance_h <= 0.34) and distance_v < distance_h):
            return x + (fx > 0.5), y, "v"
        return x, y + (fy > 0.5), "h"

    def items_in(self, cell):
        return sorted(self.area.markers_in(*cell))

    def picked(self, point, floor_fallback=True):
        x, y = self.cell_at(point)
        edge = self.edge_at(point)
        ex, ey, axis = edge
        gap = abs(point.x() - ex) if axis == "v" else abs(point.y() - ey)
        if edge in self.area.edges and gap < 0.18:
            return "edges", edge
        if not self.area.contains(x, y):
            return None
        items = self.items_in((x, y))
        if items:
            wanted = min(positions((x, y), items),
                         key=lambda row: (point.x() * CELL - row[2].x()) ** 2 + (point.y() * CELL - row[2].y()) ** 2)
            return "markers", wanted[0]
        if floor_fallback and (x, y) in self.area.tiles:
            return "tiles", (x, y)
        if floor_fallback and (x, y) in self.area.heights:
            return "heights", (x, y)
        return None

    def select(self, selection):
        if selection != self.selection:
            self.selection = selection
            self.selection_changed.emit()
        self.update()

    def erase_selection(self):
        if not self.selection:
            return
        layer, key = self.selection
        patch = Patch("Odebrání prvku")
        self.put(patch, layer, key, None)
        self.select(None)
        if patch.edits:
            self.history.push(patch)

    def rotate_selection(self):
        if not self.selection or self.tool != "select":
            return False
        layer, key = self.selection
        current = getattr(self.area, layer).get(key)
        if layer == "heights" or not current or not (ASSETS[current.style].directional
                                                     or isinstance(current, Tile) and current.layout):
            return False
        patch = Patch("Otočení prvku")
        self.put(patch, layer, key, replace(current, turns=(current.turns + 1) % 4))
        self.history.push(patch)
        return True

    def put(self, patch, layer, key, value, now=False):
        if layer == "markers":
            set_marker(patch, self.project, self.area, key, value, now)
        else:
            patch.put(getattr(self.area, layer), key, value, now)

    def paint_height(self, patch, cell, now=False, remove=False):
        if cell in self._height_touched:
            return
        if now:
            self._height_touched.add(cell)
        if self.asset == "slope_ground" and not remove:
            tile = self.area.tiles.get(cell, Tile("scree"))
            layout = tile.layout or TileLayout((tile.style,) * 4, (0, 0, 0, 0), ("",) * 4, -1)
            turn = (self.turns - tile.turns) % 4
            offsets = (1, 1, 0, 0)
            if turn:
                offsets = offsets[-turn:] + offsets[:-turn]
            patch.put(self.area.tiles, cell, replace(tile, layout=replace(layout, offsets=offsets)), now)
            return
        delta = RELIEF.get(self.asset, 0)
        base = self.area.heights.get(cell, 0)
        level = min(MAX_LEVEL, max(-MAX_LEVEL, base + delta)) if delta and not remove else 0
        patch.put(self.area.heights, cell, level or None, now)
        if not level and (not delta or remove):
            tile = self.area.tiles.get(cell)
            if tile and tile.layout and any(tile.layout.offsets):
                patch.put(self.area.tiles, cell, replace(tile, layout=replace(tile.layout, offsets=(0, 0, 0, 0))), now)

    def painted_tile(self, cell):
        turns = self.turns if ASSETS[self.asset].directional else 0
        previous = self.area.tiles.get(cell)
        layout = None
        if previous and previous.layout and any(previous.layout.offsets):
            offsets = previous.oriented(previous.layout.offsets)
            if turns:
                offsets = offsets[turns:] + offsets[:turns]
            layout = TileLayout((self.asset,) * 4, offsets, ("",) * 4, -1)
        return Tile(self.asset, turns, layout)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tone = QLinearGradient(0, 0, self.width(), self.height())
        tone.setColorAt(0, QColor("#2a322e"))
        tone.setColorAt(1, QColor("#242925"))
        painter.fillRect(self.rect(), tone)
        painter.setPen(pen("#3b443e", 1))
        visible = event.rect()
        for x in range(visible.left() // 21 * 21 + 10, visible.right() + 1, 21):
            for y in range(visible.top() // 21 * 21 + 10, visible.bottom() + 1, 21):
                painter.drawPoint(QPointF(x, y))
        if self.area:
            map_box = self.map_rect()
            painter.fillRect(map_box.adjusted(-3, -3, 6, 9), QColor(16, 19, 16, 105))
            draw_map(painter, self.area, map_box, grid=self.grid, clip=QRectF(event.rect()))
            self.draw_guides(painter)
        painter.end()

    def draw_guides(self, painter):
        rect = None
        if self.selection:
            layer, key = self.selection
            if layer == "edges":
                self.outline_edge(painter, key, "#ffe4a2")
            elif layer == "markers":
                for mark_key, _, where, radius in positions(key[:2], self.items_in(key[:2])):
                    if mark_key == key:
                        painter.setPen(pen("#ffe4a2", 2))
                        painter.setBrush(Qt.NoBrush)
                        center = self.origin + where * (self.step / CELL)
                        size = radius * self.step / CELL + 4
                        painter.drawEllipse(center, size, size)
                        break
            else:
                rect = (*key, 1, 1)
            if rect:
                self.highlight(painter, rect, "#f6d79d", fill=False)

        if self._hover is None or self._pan is not None:
            return
        if self._rect_start is not None:
            self.highlight(painter, self.rectangle_range(self.cell_at(self._hover)), "#da9d68")
        elif self.tool in {"brush", "rect", "fill", "erase"}:
            x, y = self.cell_at(self._hover)
            if self.tool == "brush" and ASSETS[self.asset].layer == "edges":
                key = self.edge_at(self._hover)
                if self.area.contains_edge(*key):
                    self.outline_edge(painter, key, "#f4bc80")
            elif self.area.contains(x, y):
                self.highlight(painter, (x, y, 1, 1), "#e3ac76")

    def highlight(self, painter, rect, color, fill=True):
        x, y, w, h = rect
        box = QRectF(self.origin.x() + x * self.step, self.origin.y() + y * self.step, w * self.step, h * self.step)
        painter.setPen(pen(color, 1.8))
        if fill:
            tone = QColor(color)
            tone.setAlpha(44)
            painter.setBrush(tone)
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawRect(box)

    def outline_edge(self, painter, key, color):
        x, y, axis = key
        start = self.origin + QPointF(x * self.step, y * self.step)
        end = start + (QPointF(self.step, 0) if axis == "h" else QPointF(0, self.step))
        painter.setPen(pen(color, 3))
        painter.drawLine(start, end)

    def rectangle_range(self, end):
        x, y = self._rect_start
        tx = min(max(0, end[0]), self.area.cols - 1)
        ty = min(max(0, end[1]), self.area.rows - 1)
        return min(x, tx), min(y, ty), abs(tx - x) + 1, abs(ty - y) + 1

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.18 ** (delta / 120), event.position())
            event.accept()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self.hover_changed.emit("")
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._button != Qt.NoButton:
            self.cancel_gesture()
            event.accept()
            return
        if event.key() == Qt.Key_Space:
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Space:
            self._space = False
            self.set_tool(self.tool)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space = False
        self.cancel_gesture()
        super().focusOutEvent(event)

    def cancel_gesture(self):
        if self._button == Qt.NoButton:
            return
        if self._stroke is not None:
            self._stroke.undo()
        self._button = Qt.NoButton
        self._stroke = self._pan = self._last_draw = self._rect_start = self._pending_selection = None
        self._height_touched.clear()
        self.set_tool(self.tool)

    def mousePressEvent(self, event):
        if (not self.area or self._button != Qt.NoButton
                or event.button() not in (Qt.LeftButton, Qt.RightButton, Qt.MiddleButton)):
            return
        self.setFocus()
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.ControlModifier:
            if self.open_transition(self.grid_pos(event.localPos())):
                return
        self._button = event.button()
        self._height_touched.clear()
        point = self.grid_pos(event.localPos())
        self._hover = point
        self._right = event.button() == Qt.RightButton
        if event.button() == Qt.MiddleButton or self._space or self.tool == "hand":
            self._pan = self.origin - event.localPos()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self.tool == "select" and not self._right:
            self.select(self.picked(point))
            return
        layer = ASSETS[self.asset].layer
        if self.tool == "rect" and not self._right:
            cell = self.cell_at(point)
            if self.area.contains(*cell):
                self._rect_start = cell
        elif self.tool == "fill" and not self._right:
            self.flood(self.cell_at(point))
        else:
            self._stroke = Patch("Mazání" if self.tool == "erase" or self._right else "Kreslení")
            self._last_draw = point
            self._pending_selection = None
            self.draw_at(point, event.modifiers(), stamp=layer == "markers")
        self.update()

    def open_transition(self, point):
        picked = self.picked(point, floor_fallback=False)
        if picked and picked[0] == "markers":
            item = self.area.markers.get(picked[1])
            if item and item.target:
                self.followed.emit(item.target)
                return True
        return False

    def mouseDoubleClickEvent(self, event):
        if self.area and event.button() == Qt.LeftButton and self.tool == "select":
            if self.open_transition(self.grid_pos(event.localPos())):
                self.cancel_gesture()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if not self.area:
            return
        if self._pan is not None:
            self.origin = event.localPos() + self._pan
            self._fit = False
        self._hover = self.grid_pos(event.localPos())
        x, y = self.cell_at(self._hover)
        if self._stroke is not None and self._last_draw is not None:
            self.extend_stroke(self._hover, event.modifiers())
        if self.area.contains(x, y):
            name = cell_name(x, y)
            level = self.area.heights.get((x, y))
            self.hover_changed.emit(f"{name} · {level:+d}" if level else name)
        else:
            self.hover_changed.emit("")
        self.update()

    def extend_stroke(self, point, modifiers):
        if ASSETS[self.asset].layer == "markers" and self.tool != "erase":
            return
        delta = point - self._last_draw
        length = hypot(delta.x(), delta.y())
        if not length:
            return
        dx, dy = abs(delta.x()), abs(delta.y())
        along = "v" if dy > dx * 1.25 else "h" if dx > dy * 1.25 else None
        parts = ceil(length * 3)
        for i in range(1, parts + 1):
            self.draw_at(self._last_draw + delta * (i / parts), modifiers, along=along)
        self._last_draw = point

    def mouseReleaseEvent(self, event):
        if not self.area or event.button() != self._button:
            return
        self._button = Qt.NoButton
        point = self.grid_pos(event.localPos())
        if self._pan is not None:
            position = event.localPos() + self._pan
            if position != self.origin:
                self.origin = position
                self._fit = False
            self._pan = None
            self.set_tool(self.tool)
        elif self._rect_start is not None:
            x, y, w, h = self.rectangle_range(self.cell_at(point))
            self._rect_start = None
            height = ASSETS[self.asset].layer == "heights"
            patch = Patch("Výška obdélníku" if height else "Vyplnění obdélníku")
            for oy in range(y, y + h):
                for ox in range(x, x + w):
                    if height:
                        self.paint_height(patch, (ox, oy))
                    else:
                        patch.put(self.area.tiles, (ox, oy), self.painted_tile((ox, oy)))
            if patch.edits:
                self.history.push(patch)
        elif self._stroke is not None:
            if self._last_draw is not None:
                self.extend_stroke(point, event.modifiers())
            patch, self._stroke = self._stroke, None
            if patch.edits:
                self.history.push(patch)
            if self._pending_selection:
                self.select(self._pending_selection)
        self._height_touched.clear()
        self.update()

    def draw_at(self, point, modifiers, stamp=False, along=None):
        x, y = self.cell_at(point)
        asset = ASSETS[self.asset]
        layer = asset.layer
        if self.tool == "erase" and not modifiers & Qt.ShiftModifier:
            picked = self.picked(point)
            if picked:
                self.put(self._stroke, *picked, None, now=True)
            return
        if self.tool == "erase":
            layer = "tiles"
        if layer == "heights":
            if self.area.contains(x, y):
                self.paint_height(self._stroke, (x, y), now=True, remove=self._right)
            return
        if layer == "edges":
            key = self.edge_at(point, along)
            if not self.area.contains_edge(*key):
                return
        elif not self.area.contains(x, y):
            return
        elif layer == "markers":
            key = (x, y, asset.group)
        else:
            key = (x, y)
        if self._right or self.tool == "erase":
            self.put(self._stroke, layer, key, None, now=True)
        elif layer == "tiles":
            self.put(self._stroke, layer, key, self.painted_tile(key), now=True)
        elif layer == "edges" or stamp:
            existing = getattr(self.area, layer).get(key)
            if not existing or existing.style != self.asset:
                self.put(self._stroke, layer, key, Mark.from_asset(self.asset, self.turns), now=True)
            if stamp:
                self._pending_selection = (layer, key)

    def flood(self, start):
        if not self.area.contains(*start):
            return
        patch = Patch("Výplň plochy")
        tiles = self.area.tiles
        original = tiles.get(start)
        replacement = self.painted_tile(start)
        if original == replacement:
            return
        original_style = (original.style, original.turns if ASSETS[original.style].directional else 0) if original else None
        pending = deque([start])
        seen = {start}
        while pending:
            x, y = pending.popleft()
            patch.put(tiles, (x, y), self.painted_tile((x, y)))
            neighbors = ((x - 1, y, (x, y, "v")), (x + 1, y, (x + 1, y, "v")),
                         (x, y - 1, (x, y, "h")), (x, y + 1, (x, y + 1, "h")))
            for nx, ny, edge in neighbors:
                cell = nx, ny
                if cell in seen or not self.area.contains(*cell) or edge in self.area.edges:
                    continue
                seen.add(cell)
                other = tiles.get(cell)
                style = (other.style, other.turns if ASSETS[other.style].directional else 0) if other else None
                if style == original_style:
                    pending.append(cell)
        if patch.edits:
            self.history.push(patch)
