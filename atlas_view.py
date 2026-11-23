"""Společná mapa tvořená přímo z půdorysů projektu."""
from collections import OrderedDict
from dataclasses import dataclass, replace
from math import dist, floor, hypot

from PyQt5.QtCore import QLineF, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QKeySequence, QLinearGradient, QPainter, QTransform
from PyQt5.QtWidgets import QMenu, QShortcut, QWidget

from atlas_layout import (COMPACT_MODULE_AREAS, NORMALS, add_interior_layers, arrange, area_routes, bounds, box,
                          exact_seam, exterior_areas, interior_layers, interiors_reaching_treasure, matching_edges,
                          nearest_edge, world_areas, world_point)
from document import ATLAS_LIMIT, cell_name, marker_group, rotated_xy
from drawing import DETAIL_TILE_STEP, MONO, SANS, draw_badge, draw_cell_grid, draw_map, font, glyph, interior_contains, pen

TERRAIN_CACHE_BYTES = 20 * 1024 * 1024
MAX_ATLAS_STEP = 160
TREASURE_RING = "#ffdb73"
SCENERY_MIN_STEP = 7


@dataclass
class Pin:
    center: QPointF
    routes: list
    entrance: bool
    radius: float


class AtlasView(QWidget):
    picked_area = pyqtSignal(str)
    opened_area = pyqtSignal(str)
    followed = pyqtSignal(object)
    placed = pyqtSignal(str, object)
    zoom_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("atlasCanvas")
        self.setAccessibleName("Mapa světa")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.OpenHandCursor)
        self.project = None
        self.exteriors = {}
        self.world = {}
        self.areas, self.positions, self.routes = {}, {}, {}
        self.layers = {}
        self.treasures = {}
        self.treasure_interiors = {False: set(), True: set()}
        self.bounds = (0, 0, 0, 0)
        self.center = QPointF()
        self.step = 4.0
        self.fitted = True
        self.grid = False
        self.show_pins = True
        self.show_routes = False
        self.show_treasures = False
        self.show_scenery = True
        self.show_all_areas = False
        self.arranging = False
        self.selected = ""
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._pins = []
        self._gesture = None
        self._moving = None
        self.bind("F", self.fit)
        self.bind("Return", self.open_selected)

    def sizeHint(self):
        return QSize(850, 625)

    def bind(self, key, action):
        shortcut = QShortcut(QKeySequence(key), self)
        shortcut.setContext(Qt.WidgetShortcut)
        shortcut.activated.connect(action)

    def clear_project(self):
        self.project = None
        self.areas, self.positions, self.routes = {}, {}, {}
        self.layers = {}
        self.exteriors, self.world, self.treasures = {}, {}, {}
        self.treasure_interiors = {False: set(), True: set()}
        self.bounds = (0, 0, 0, 0)
        self.selected = ""
        self._cache.clear()
        self._cache_bytes = 0
        self._pins.clear()
        self.update()

    def set_project(self, project, reset=False, redraw=True, preferred=None):
        new_project = self.project is not project
        self.project = project
        self.exteriors = exterior_areas(project)
        if new_project or reset:
            compact_import = (len(project.areas) <= COMPACT_MODULE_AREAS
                              and all(area.source_ref for area in project.areas.values()))
            self.show_all_areas = compact_import
            if compact_import:
                self.grid = True
        if not self.exteriors:
            self.show_all_areas = True
        self.world = world_areas(project, self.exteriors)
        self.areas = project.areas if self.show_all_areas else self.world
        self.layers = interior_layers(self.areas)
        self.positions = arrange(project, self.areas)
        self.routes = {uid: tuple(area_routes(project, area)) for uid, area in self.areas.items()}
        self.treasures = {
            uid: tuple((x, y, mark) for (x, y, slot), mark in area.markers.items()
                       if marker_group(slot) == "treasure")
            for uid, area in project.areas.items()
        }
        self.treasure_interiors = {
            player: interiors_reaching_treasure(project, self.areas, self.treasures, player)
            for player in (False, True)
        }
        self.bounds = bounds(self.areas, self.positions)
        if reset or self.selected not in self.positions:
            self.selected = preferred if preferred in self.positions else ""
        if redraw:
            self._cache.clear()
            self._cache_bytes = 0
        self._pins.clear()
        if reset or self.fitted:
            self.fit()
        self.update()

    @property
    def scope_summary(self):
        if self.show_all_areas:
            return f"Všechny oblasti · {len(self.positions)}"
        if len(self.positions) > len(self.exteriors):
            return f"Mapa světa · {len(self.positions)} z {len(self.project.areas)} oblastí"
        return f"Mapa světa · {len(self.positions)} venkovních oblastí"

    def configure_arranging(self, enabled):
        self.arranging = enabled
        self.cancel_gesture()
        self.update()

    def cancel_gesture(self):
        self._gesture, self._moving = None, None
        self.setCursor(Qt.OpenHandCursor)

    def choose(self, uid):
        if uid in self.areas:
            self.selected = uid
            self.update()

    def open_selected(self):
        if self.selected in self.areas:
            self.opened_area.emit(self.selected)

    def rotate_selected(self):
        if self.arranging and self.selected in self.positions:
            uid = self.host_area(self.selected)
            old = self.positions[uid]
            x, y, right, bottom = box(self.areas[uid], old)
            dx = ((right - x) - (bottom - y)) / 2
            self.placed.emit(uid, replace(old, x=old.x + dx, y=old.y - dx, turns=(old.turns + 1) % 4))

    def report_zoom(self):
        self.zoom_changed.emit(round(self.step / 36 * 100))
        self.update()

    def fit(self):
        x, y, right, bottom = self.bounds
        self.center = QPointF((x + right) / 2, (y + bottom) / 2)
        self.step = min(42., max(0.002, min(max(1, self.width() - 64) / max(1, right - x),
                                          max(1, self.height() - 58) / max(1, bottom - y))))
        self.fitted = True
        self.report_zoom()

    def focus_on(self, uid):
        if uid not in self.positions:
            return
        self.choose(uid)
        x, y, right, bottom = box(self.areas[uid], self.positions[uid])
        self.center = QPointF((x + right) / 2, (y + bottom) / 2)
        self.step = min(76., max(3., min(self.width() * .62 / (right - x), self.height() * .58 / (bottom - y))))
        self.fitted = False
        self.report_zoom()

    def view_center(self):
        return QPointF(self.width() / 2, self.height() / 2)

    def in_world(self, position):
        return self.center + (position - self.view_center()) / self.step

    def zoom(self, ratio, anchor=None):
        anchor = self.view_center() if anchor is None else anchor
        pin = self.in_world(anchor)
        self.step = min(MAX_ATLAS_STEP, max(0.002, self.step * ratio))
        self.center = pin - (anchor - self.view_center()) / self.step
        self.fitted = False
        self.report_zoom()

    def resizeEvent(self, event):
        if self.fitted:
            self.fit()
        super().resizeEvent(event)

    def wheelEvent(self, event):
        if delta := event.angleDelta().y():
            self.zoom(1.20 ** (delta / 120), event.position())
            event.accept()

    def preview(self, uid, step, reference=True, player=False):
        sample = next((size for size in (9, 16, 32, 64, 128) if step <= size), MAX_ATLAS_STEP)
        area = self.areas[uid]
        sample = min(sample, max(1, 1350 // max(area.cols, area.rows)))
        scenery = self.show_scenery and step >= SCENERY_MIN_STEP
        layer = self.layers.get(uid)
        empty_color = layer.empty_color if layer else None
        key = (uid, sample, reference, player, scenery, empty_color)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        image = QImage(area.cols * sample, area.rows * sample, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        p = QPainter(image)
        try:
            draw_map(p, area, QRectF(image.rect()), grid=False, reference=reference,
                     player=player, annotations=False, scenery=scenery, transparent_color=empty_color)
        finally:
            p.end()
        while self._cache and self._cache_bytes + image.sizeInBytes() > TERRAIN_CACHE_BYTES:
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes -= removed.sizeInBytes()
        self._cache[key] = image
        self._cache_bytes += image.sizeInBytes()
        return image

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            visible = QRectF(self.rect())
            origin = self.view_center() - self.center * self.step
            self._pins = self.paint_board(painter, visible, origin, self.step, grid=self.grid,
                                          entrances=self.show_pins)
        finally:
            painter.end()

    def host_area(self, uid):
        return self.layers[uid].host if uid in self.layers else uid

    def area_groups(self, positions, selected):
        active_host = self.host_area(selected)
        order = [uid for uid in positions if uid not in self.layers and uid != active_host]
        if active_host in positions:
            order.append(active_host)
        return [(uid, *(child for child, layer in self.layers.items() if layer.host == uid)) for uid in order]

    def paint_area(self, p, viewport, rect, placement, uid, step, reference, player):
        area = self.areas[uid]
        layer = self.layers.get(uid)
        if not layer:
            p.fillRect(rect.adjusted(-1, -1, 2, 2), QColor(16, 26, 23, 140))
        target = QRectF(0, 0, area.cols * step, area.rows * step)
        transform = self.area_transform(rect, placement.turns)
        p.save()
        p.setWorldTransform(transform, True)
        if step >= DETAIL_TILE_STEP:
            # Kresli ve zvětšeném výřezu z původních dlaždic a objekty přímo v měřítku pohledu.
            clip = transform.inverted()[0].mapRect(viewport)
            draw_map(p, area, target, grid=False, reference=reference, player=player, clip=clip,
                     annotations=False, scenery=self.show_scenery, transparent_color=layer.empty_color if layer else None)
        else:
            p.drawImage(target, self.preview(uid, step, reference, player))
        p.restore()

    def paint_board(self, painter, viewport, origin, step, *, grid=False, labels=True, reference=True,
                    entrances=True, player=False, printing=False):
        """Sdílené vykreslení plátna a tiskového/exportního pohledu."""
        p = painter
        p.save()
        p.setClipRect(viewport)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        wash = QLinearGradient(viewport.topLeft(), viewport.bottomRight())
        wash.setColorAt(0, QColor("#364c47"))
        wash.setColorAt(1, QColor("#263a41"))
        p.fillRect(viewport, wash)

        def screen(x, y):
            return origin + QPointF(x, y) * step

        if grid:
            delta = step * max(1, round(36 / step))
            p.setPen(pen(QColor(199, 216, 191, 45), .65))
            start = origin.x() + floor((viewport.left() - origin.x()) / delta) * delta
            while start < viewport.right():
                p.drawLine(QPointF(start, viewport.top()), QPointF(start, viewport.bottom()))
                start += delta
            start = origin.y() + floor((viewport.top() - origin.y()) / delta) * delta
            while start < viewport.bottom():
                p.drawLine(QPointF(viewport.left(), start), QPointF(viewport.right(), start))
                start += delta

        positions = self.positions
        if self._moving and not printing:
            uid, placement = self._moving
            positions = add_interior_layers(self.areas, positions | {uid: placement}, self.layers)
        rectangles = {}
        selected = "" if printing else self.selected
        for group in self.area_groups(positions, selected):
            for uid in group:
                place = positions[uid]
                x, y, right, bottom = box(self.areas[uid], place)
                rect = QRectF(screen(x, y), screen(right, bottom))
                if not rect.intersects(viewport):
                    continue
                rectangles[uid] = rect
                if rect.width() < 1 or rect.height() < 1:
                    if uid not in self.layers:
                        p.fillRect(rect.adjusted(-1, -1, 1, 1), QColor("#b5a784"))
                else:
                    self.paint_area(p, viewport, rect, place, uid, step, reference, player)
            host = group[0]
            rect = rectangles.get(host)
            if rect is None or rect.width() < 1 or rect.height() < 1:
                continue
            if grid:
                draw_cell_grid(p, rect, viewport, step)
            if host == selected:
                p.setPen(pen("#ffe2a1", 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect)

        treasure_interiors = self.treasure_interiors[player] if self.show_treasures else set()
        connections = set()
        thresholds = {}
        pins = []
        for uid, routes in self.routes.items():
            if uid not in positions:
                continue
            if uid not in rectangles and not self.show_routes:
                continue
            place = positions[uid]
            candidates = []
            for route in routes:
                if route.hidden and player:
                    continue
                destination = route.target.area
                start = screen(*world_point(self.areas[uid], place, route.point))
                if destination in positions:
                    related = selected in (uid, destination) or self.show_routes
                    edges = matching_edges(self.areas, positions, route)
                    if edges:
                        ends = tuple(sorted((tuple(round(v, 2) for v in xy) for xy in edges)))
                        if exact_seam(self.areas, route):
                            if ends in thresholds:
                                thresholds[ends].routes.append(route)
                            else:
                                center = (screen(*edges[0]) + screen(*edges[1])) / 2
                                self.paint_threshold(p, route, place, center, step)
                                thresholds[ends] = Pin(center, [route], False, max(6., min(step * .28, 9.)))
                        elif ends not in connections and uid in rectangles:
                            p.setPen(pen("#d9c48e", min(2.5, max(1., .2 * step))))
                            p.drawLine(screen(*edges[0]), screen(*edges[1]))
                            connections.add(ends)
                        continue
                    end = screen(*world_point(self.areas[destination], positions[destination], route.landing))
                    if related:
                        line_pen = pen(QColor(170, 218, 215, 132), 1.25)
                        line_pen.setStyle(Qt.DashLine)
                        p.setPen(line_pen)
                        p.drawLine(start, end)
                    if entrances and (related or step >= 7) and viewport.contains(start):
                        pins.append(Pin(start, [route], False, 8))
                elif (entrances or destination in treasure_interiors) and viewport.contains(start):
                    candidates.append((start, route))
            if candidates and step < 7:
                center = sum((position for position, _ in candidates), QPointF()) / len(candidates)
                pins.append(Pin(center, [route for _, route in candidates], True, 10 if uid == selected else 7))
            else:
                pins.extend(Pin(point, [route], True, 9) for point, route in candidates)

        if labels:
            occupied = self.paint_labels(p, rectangles, selected)
            if self.show_scenery and step >= 14:
                self.paint_map_notes(p, viewport, rectangles, positions, origin, step, occupied,
                                     [pin.center for pin in pins] + [pin.center for pin in thresholds.values()])
        if self.show_treasures:
            self.paint_treasures(p, viewport, rectangles, positions, origin, step, player)
        painted = self.paint_pins(p, pins, step, treasure_interiors)
        p.restore()
        return painted + list(thresholds.values()) if entrances else painted

    @staticmethod
    def area_transform(rect, turns):
        corner = (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())[turns]
        return QTransform().translate(corner.x(), corner.y()).rotate(turns * 90)

    def paint_threshold(self, painter, route, placement, center, step):
        side, _ = nearest_edge(self.areas[route.area], route.point)
        dx, dy = NORMALS[(side + placement.turns) % 4]
        across = QPointF(-dy, dx)
        tangent = across * max(2., min(step * .24, 17.))
        width = min(3., max(1.4, step * .11))
        first, second = center - tangent, center + tangent
        color = "#29755f" if route.hidden else "#1c5972"
        painter.setPen(pen("#f4e8cf", width + 1.6))
        painter.drawLine(first, second)
        painter.setPen(pen(color, width))
        painter.drawLine(first, second)

    def paint_treasures(self, p, viewport, rectangles, placements, origin, step, player):
        radius = min(9., max(5., step * .26))
        spacing = 2 * radius + 1
        occupied = set()
        for uid in reversed(rectangles):
            for x, y, mark in self.treasures[uid]:
                if player and mark.hidden:
                    continue
                point = QPointF(*world_point(self.areas[uid], placements[uid], (x + .5, y + .5))) * step
                center = origin + point
                bucket = floor(point.x() / spacing), floor(point.y() / spacing)
                if bucket not in occupied and viewport.contains(center):
                    draw_badge(p, mark, center, radius)
                    occupied.add(bucket)

    def paint_labels(self, p, rectangles, selected_uid):
        claims = []
        priority = sorted(rectangles, key=lambda uid: (uid != selected_uid, -rectangles[uid].width()))
        p.setFont(font(SANS, 12, True))
        fm = p.fontMetrics()
        for uid in priority:
            rect = rectangles[uid]
            selected = uid == selected_uid
            if not selected and (rect.height() < 19 or max(rect.width(), rect.height()) < 54):
                continue
            text = fm.elidedText(self.areas[uid].name, Qt.ElideRight, int(max(117, min(rect.width() - 5, 206))))
            width = fm.horizontalAdvance(text) + 12
            panel = QRectF(rect.center().x() - width / 2, rect.top() + 3, width, 19)
            if any(panel.intersects(other) for other in claims):
                continue
            claims.append(panel)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(25, 34, 28, 220))
            p.drawRoundedRect(panel, 4, 4)
            p.setPen(QColor("#f9e3bc" if selected else "#f8f0db"))
            p.drawText(panel, Qt.AlignCenter, text)
        return claims

    def paint_map_notes(self, p, viewport, rectangles, placements, origin, step, occupied, links):
        p.setFont(font(SANS, 11))
        metrics = p.fontMetrics()
        limit = 1 if step < 28 else 3
        for uid in rectangles:
            area = self.areas[uid]
            drawn = 0
            for (x, y, slot), note in area.markers.items():
                if marker_group(slot) != "label" or note.style != "point" or note.hidden or not note.name:
                    continue
                center = origin + QPointF(*world_point(area, placements[uid], (x + .5, y + .5))) * step
                if any(QLineF(center, point).length() < 13 for point in links):
                    continue
                title = metrics.elidedText(note.name, Qt.ElideRight, 127)
                width = metrics.horizontalAdvance(title) + 10
                right = QRectF(center.x() + 7, center.y() + 3, width, 16)
                left = right.translated(-width - 14, 0)
                panel = next((candidate for candidate in (right, left)
                              if viewport.contains(candidate) and not any(candidate.intersects(other)
                                                                          for other in occupied)), None)
                if panel is None:
                    continue
                occupied.append(panel)
                p.setPen(pen("#233534", 1.15))
                p.setBrush(QColor("#96d1c6"))
                p.drawEllipse(center, 3, 3)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(32, 53, 51, 232))
                p.drawRoundedRect(panel, 3, 3)
                p.setPen(QColor("#d5efe8"))
                p.drawText(panel, Qt.AlignCenter, title)
                drawn += 1
                if drawn >= limit:
                    break

    def paint_pins(self, p, pins, step, treasure_interiors):
        groups = []
        # Groups are recomputed in screen units so each visible entrance has a usable hit area.
        for pin in sorted(pins, key=lambda candidate: not candidate.entrance):
            neighbor = next((other for other in groups if QLineF(pin.center, other.center).length()
                             < pin.radius + other.radius + (1 if step < 7 else 3)), None)
            if neighbor:
                neighbor.routes.extend(pin.routes)
                neighbor.radius = max(neighbor.radius, pin.radius)
            else:
                groups.append(pin)
        p.setFont(font(MONO, 11, True))
        for pin in groups:
            if pin.entrance and any(route.target.area in treasure_interiors for route in pin.routes):
                p.setBrush(Qt.NoBrush)
                ring = pin.radius + 3
                p.setPen(pen("#352b22", 4))
                p.drawEllipse(pin.center, ring, ring)
                p.setPen(pen(TREASURE_RING, 2))
                p.drawEllipse(pin.center, ring, ring)
            colors = ("#f1c990", "#a86e4b") if pin.entrance else ("#b7e8e1", "#427b7b")
            p.setBrush(QColor(colors[1]))
            p.setPen(pen(colors[0], 1.1))
            p.drawEllipse(pin.center, pin.radius, pin.radius)
            destinations = {route.target.area for route in pin.routes}
            shared_entry = all(self.host_area(route.area) == self.host_area(route.target.area)
                               for route in pin.routes)
            if len(destinations) > 1 and not shared_entry:
                p.setPen(QColor("#fff5e2"))
                p.drawText(QRectF(pin.center.x() - 12, pin.center.y() - 7, 24, 14),
                           Qt.AlignCenter, str(len(destinations)))
            else:
                scale = pin.radius * .71
                glyph(p, "door" if pin.entrance else "portal",
                      QRectF(pin.center.x() - scale, pin.center.y() - scale, 2 * scale, 2 * scale), "#fff6df")
        return groups

    def pin_at(self, position):
        return next((pin for pin in reversed(self._pins)
                     if QLineF(position, pin.center).length() <= pin.radius + 3), None)

    def destination_label(self, uid):
        name = self.project.areas[uid].name
        if self.show_treasures and uid in self.treasure_interiors[False]:
            return f"{name} · poklad uvnitř"
        return name

    def area_at(self, position):
        point = self.in_world(position)
        for group in reversed(self.area_groups(self.positions, self.selected)):
            for uid in reversed(group):
                area, placement = self.areas[uid], self.positions[uid]
                x, y, right, bottom = box(area, placement)
                if x <= point.x() <= right and y <= point.y() <= bottom:
                    layer = self.layers.get(uid)
                    if layer is None or interior_contains(area, self.local_point(uid, point), layer.empty_color):
                        return uid
        return ""

    def local_point(self, uid, point):
        place = self.positions[uid]
        x, y, right, bottom = box(self.areas[uid], place)
        return rotated_xy(right - x, bottom - y, (point.x() - x, point.y() - y), -place.turns)

    def follow_pin(self, pin, position):
        routes = {}
        for route in pin.routes:
            routes.setdefault(route.target, route)
        if len(routes) == 1:
            self.followed.emit(next(iter(routes)))
            return
        ordered = sorted(routes, key=lambda to: (self.project.areas[to.area].name.casefold(), to.x, to.y))
        menu = QMenu(self)
        for to in ordered:
            name = self.destination_label(to.area)
            action = menu.addAction(f"{name} · {cell_name(to.x, to.y)}")
            action.triggered.connect(lambda checked, target=to: self.followed.emit(target))
        menu.exec_(self.mapToGlobal(position))

    def mousePressEvent(self, event):
        if not self.positions or self._gesture or event.button() not in (Qt.LeftButton, Qt.MiddleButton):
            return
        position = event.localPos()
        self.setFocus()
        uid = self.host_area(self.area_at(position)) if self.arranging and event.button() == Qt.LeftButton else ""
        self._gesture = (event.button(), position, QPointF(self.center), uid)
        self.setCursor(Qt.ClosedHandCursor)

    def drag_to(self, position):
        _, start, center, uid = self._gesture
        delta = position - start
        moved = hypot(delta.x(), delta.y()) >= 4
        if moved:
            shift = delta / self.step
            if uid:
                item = self.positions[uid]
                x = min(ATLAS_LIMIT, max(-ATLAS_LIMIT, item.x + shift.x()))
                y = min(ATLAS_LIMIT, max(-ATLAS_LIMIT, item.y + shift.y()))
                self._moving = uid, replace(item, x=round(x, 3), y=round(y, 3))
            else:
                self.center = center - shift
                self.fitted = False
            self.update()
        return moved

    def mouseMoveEvent(self, event):
        position = event.localPos()
        if self._gesture:
            self.drag_to(position)
            return
        pin = self.pin_at(position) if not self.arranging else None
        uid = self.area_at(position)
        self.setCursor(Qt.PointingHandCursor if pin else Qt.SizeAllCursor if uid and self.arranging else Qt.OpenHandCursor)
        if pin:
            names = sorted({self.destination_label(r.target.area) for r in pin.routes})
            self.setToolTip("\n".join(names[:12]) + (f"\n+ {len(names) - 12} dalších" if len(names) > 12 else ""))
        else:
            self.setToolTip(self.object_tooltip(uid, position) if uid else "")

    def object_tooltip(self, uid, position):
        area = self.areas[uid]
        if not self.show_scenery or self.arranging or self.step < 15:
            return area.name
        local = self.local_point(uid, self.in_world(position))
        nearest = min((item for item in area.scenery if item.name),
                      key=lambda item: (dist(local, item.point), not item.usable), default=None)
        if nearest and dist(local, nearest.point) <= .27:
            return f"{nearest.name}\n{area.name}"
        return area.name

    def mouseReleaseEvent(self, event):
        if not self._gesture or event.button() != self._gesture[0]:
            return
        moved = self.drag_to(event.localPos())
        destination = self._moving if moved else None
        self.cancel_gesture()
        if destination:
            self.placed.emit(*destination)
        elif not moved and event.button() == Qt.LeftButton:
            pin = self.pin_at(event.localPos()) if not self.arranging else None
            if pin:
                self.follow_pin(pin, event.pos())
            elif uid := self.area_at(event.localPos()):
                self.picked_area.emit(uid)
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not self.arranging:
            if uid := self.area_at(event.localPos()):
                self.cancel_gesture()
                self.opened_area.emit(uid)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._gesture:
            self.cancel_gesture()
            self.update()
            event.accept()
        else:
            super().keyPressEvent(event)
