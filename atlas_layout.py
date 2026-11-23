"""Rozložení exteriérů na společném plátně a souřadnice průchodů."""
from collections import defaultdict
from dataclasses import dataclass
from math import dist, sqrt

from catalog import ASSETS
from document import Placement, Target, marker_group, rotated_xy
from nwn_rules import nwn_rules

NORMALS = ((0, -1), (1, 0), (0, 1), (-1, 0))
SEAM_GAP = .24
SEAM_TOLERANCE = .02
COMPACT_MODULE_AREAS = 24


@dataclass(frozen=True)
class Route:
    area: str
    point: tuple[float, float]
    target: Target
    landing: tuple[float, float]
    hidden: bool = False

    def reversed(self):
        return Route(self.target.area, self.landing, Target(self.area, 0, 0), self.point, self.hidden)


@dataclass(frozen=True)
class InteriorLayer:
    host: str
    placement: Placement
    hidden: bool
    empty_color: str


def nwn_reference(project):
    rules = nwn_rules()
    if rules.placements and all(uid in project.areas and project.areas[uid].source_ref == uid
                                for uid in rules.placements):
        return rules
    return None


def area_kind(area, original_surface=None):
    if area.kind != "auto":
        return area.kind
    if original_surface is not None and area.source_ref:
        return "outdoor" if area.source_ref in original_surface else "indoor"
    outdoor = sum(ASSETS[tile.style].group == "outdoor" for tile in area.tiles.values())
    return "outdoor" if outdoor * 2 >= len(area.tiles) else "indoor"


def exterior_areas(project):
    known = nwn_reference(project)
    surface = known.surface if known and known.surface else None
    return {uid: area for uid, area in project.areas.items() if area_kind(area, surface) == "outdoor"}


def area_routes(project, area):
    for (x, y, slot), mark in area.markers.items():
        target = mark.target
        if marker_group(slot) != "link" or not target or target.area not in project.areas or target.area == area.uid:
            continue
        yield Route(area.uid, mark.origin_xy or (x + .5, y + .5), target,
                    mark.arrival_xy or (target.x + .5, target.y + .5), mark.hidden)


def world_areas(project, exteriors=None, *, include_hidden=False):
    exteriors = exterior_areas(project) if exteriors is None else exteriors
    if not exteriors or len(exteriors) == len(project.areas):
        return exteriors
    neighbors = defaultdict(set)
    for area in project.areas.values():
        for route in area_routes(project, area):
            if (include_hidden or not route.hidden) and exact_seam(project.areas, route):
                neighbors[area.uid].add(route.target.area)
                neighbors[route.target.area].add(area.uid)
    for uid, layer in interior_layers(project.areas).items():
        if include_hidden or not layer.hidden:
            neighbors[layer.host].add(uid)
    visible = set(exteriors)
    pending = list(exteriors)
    while pending:
        added = neighbors[pending.pop()] - visible
        visible.update(added)
        pending.extend(added)
    return {uid: area for uid, area in project.areas.items() if uid in visible}


def interiors_reaching_treasure(project, exteriors, treasures, player):
    """Najdi, ze kterých interiérů vede cesta k pokladu uvnitř."""
    incoming = defaultdict(set)
    reachable = set()
    for uid, area in project.areas.items():
        if uid in exteriors:
            continue
        if any(not player or not mark.hidden for _, _, mark in treasures[uid]):
            reachable.add(uid)
        for route in area_routes(project, area):
            if route.target.area not in exteriors and not (player and route.hidden):
                incoming[route.target.area].add(uid)

    pending = list(reachable)
    while pending:
        upstream = incoming[pending.pop()] - reachable
        reachable.update(upstream)
        pending.extend(upstream)
    return reachable


def size(area, turns):
    return (area.rows, area.cols) if turns % 2 else (area.cols, area.rows)


def box(area, placement):
    width, height = size(area, placement.turns)
    return placement.x, placement.y, placement.x + width, placement.y + height


def bounds(areas, positions):
    rects = [box(areas[uid], p) for uid, p in positions.items()]
    return tuple(f(r[i] for r in rects) for i, f in enumerate((min, min, max, max))) if rects else (0, 0, 0, 0)


def intersects(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) - 1e-6 and max(a[1], b[1]) < min(a[3], b[3]) - 1e-6


def world_point(area, placement, point):
    x, y = rotated_xy(area.cols, area.rows, point, placement.turns)
    return placement.x + x, placement.y + y


def interior_layers(areas):
    """Umístí překryv podle společné polohy dvou nativních spouští."""
    rule = nwn_rules().overlay
    if rule is None:
        return {}
    host, room = areas.get(rule.host), areas.get(rule.area)
    if (not host or not room or host.source_ref != host.uid or room.source_ref != room.uid
            or host.unit != room.unit or host.tileset != rule.tileset or room.tileset != rule.tileset):
        return {}

    def shared_trigger(area, target, tag):
        return next((mark for mark in area.markers.values()
                     if mark.target and mark.target.area == target.uid and mark.origin_xy is not None
                     and mark.note.partition("\n")[0].partition(" · ")[0] == f"Tag {tag}"), None)

    entry = shared_trigger(host, room, rule.entry_tag)
    exit_ = shared_trigger(room, host, rule.exit_tag)
    if not entry or not exit_:
        return {}

    matches = []
    for turns in range(4):
        x, y = rotated_xy(room.cols, room.rows, exit_.origin_xy, turns)
        dx, dy = entry.origin_xy[0] - x, entry.origin_xy[1] - y
        if any(abs(value - round(value)) > SEAM_TOLERANCE for value in (dx, dy)):
            continue
        placement = Placement(round(dx), round(dy), turns)
        left, top, right, bottom = box(room, placement)
        if 0 <= left < right <= host.cols and 0 <= top < bottom <= host.rows:
            matches.append(placement)
    return {room.uid: InteriorLayer(host.uid, matches[0], entry.hidden, rule.empty_color)} if len(matches) == 1 else {}


def add_interior_layers(areas, positions, layers):
    for uid, layer in layers.items():
        host_area, host = areas[layer.host], positions[layer.host]
        left, top, right, bottom = box(areas[uid], layer.placement)
        corners = (world_point(host_area, host, point) for point in
                   ((left, top), (left, bottom), (right, top), (right, bottom)))
        xs, ys = zip(*corners)
        positions[uid] = Placement(min(xs), min(ys), (host.turns + layer.placement.turns) % 4)
    return positions


def nearest_edge(area, position):
    x, y = position
    distances = y, area.cols - x, area.rows - y, x
    side = min(range(4), key=distances.__getitem__)
    return side, distances[side]


def exact_seam(areas, route):
    """Průchod se souřadnicemi prahů přímo na hranách obou půdorysů."""
    return (areas[route.area].unit == areas[route.target.area].unit
            and abs(nearest_edge(areas[route.area], route.point)[1]) < 1e-6
            and abs(nearest_edge(areas[route.target.area], route.landing)[1]) < 1e-6)


def edge_point(area, placement, local, side):
    x, y = rotated_xy(area.cols, area.rows, local, placement.turns)
    width, height = size(area, placement.turns)
    px, py = ((x, 0), (width, y), (x, height), (0, y))[(side + placement.turns) % 4]
    return placement.x + px, placement.y + py


def matching_edges(areas, positions, route):
    a, b = route.area, route.target.area
    side_a, distance_a = nearest_edge(areas[a], route.point)
    side_b, distance_b = nearest_edge(areas[b], route.landing)
    if max(distance_a, distance_b) > 2.2:
        return None
    direction = (side_a + positions[a].turns) % 4
    if (side_b + positions[b].turns - direction) % 4 != 2:
        return None
    p = edge_point(areas[a], positions[a], route.point, side_a)
    q = edge_point(areas[b], positions[b], route.landing, side_b)
    dx, dy = NORMALS[direction]
    difference = q[0] - p[0], q[1] - p[1]
    separation = difference[0] * dx + difference[1] * dy
    offset = abs(difference[0] * dy - difference[1] * dx)
    if exact_seam(areas, route):
        return (p, q) if abs(separation) <= SEAM_TOLERANCE and offset <= SEAM_TOLERANCE else None
    if -.02 <= separation <= .64 and offset <= 1.6:
        return p, q
    return None


def translated(positions, dx, dy):
    return {uid: Placement(p.x + dx, p.y + dy, p.turns) for uid, p in positions.items()}


def merge(areas, stationary, moving, route):
    source, target = route.area, route.target.area
    first = stationary[source]
    from_side, _ = nearest_edge(areas[source], route.point)
    to_side, _ = nearest_edge(areas[target], route.landing)
    direction = (from_side + first.turns) % 4
    rotation = (direction + 2 - to_side - moving[target].turns) % 4
    # Apply one common rotation to the moving group, then shift its far edge to meet the first group.
    spun = {}
    for uid, placement in moving.items():
        x, y, right, bottom = box(areas[uid], placement)
        points = [rotated_xy(0, 0, pt, rotation) for pt in ((x, y), (x, bottom), (right, y), (right, bottom))]
        spun[uid] = Placement(min(pt[0] for pt in points), min(pt[1] for pt in points), (placement.turns + rotation) % 4)
    p = edge_point(areas[source], first, route.point, from_side)
    q = edge_point(areas[target], spun[target], route.landing, to_side)
    dx, dy = p[0] - q[0], p[1] - q[1]
    if exact_seam(areas, route):
        # Souřadnice z GFF jsou float32. Zachovej celočíselnou mřížku při malé odchylce.
        dx, dy = (round(value) if abs(value - round(value)) <= SEAM_TOLERANCE else value for value in (dx, dy))
    else:
        nx, ny = NORMALS[direction]
        dx, dy = dx + nx * SEAM_GAP, dy + ny * SEAM_GAP
    moved = translated(spun, dx, dy)
    fixed_boxes = [box(areas[uid], placement) for uid, placement in stationary.items()]
    for uid, placement in moved.items():
        if any(intersects(box(areas[uid], placement), other) for other in fixed_boxes):
            return None
    return stationary | moved


def starting_placements(project, areas):
    if project.atlas:
        return {uid: placement for uid, placement in project.atlas.items() if uid in areas}
    known = nwn_reference(project)
    result = {}
    if known:
        for uid, (x, y, turns, width, height) in known.placements.items():
            area = areas.get(uid)
            if area and size(area, -area.north) == (width, height):
                result[uid] = Placement(x, y, (turns - area.north) % 4)
    return result


def suitable_routes(project, areas):
    land = [route for area in areas.values() for route in area_routes(project, area)
            if route.target.area in areas and area.unit == areas[route.target.area].unit
            and (not route.hidden or exact_seam(areas, route))]
    groups = {}
    for route in land:
        groups.setdefault((route.area, route.target.area), []).append(route)

    def score(route):
        reverse = groups.get((route.target.area, route.area), ())
        reciprocal = any(dist(route.point, other.landing) + dist(route.landing, other.point) <= 5.5
                         for other in reverse)
        _, first = nearest_edge(areas[route.area], route.point)
        _, last = nearest_edge(areas[route.target.area], route.landing)
        return (4 if reciprocal else 0) - first - last

    return sorted((route for route in land
                   if max(nearest_edge(areas[route.area], route.point)[1],
                          nearest_edge(areas[route.target.area], route.landing)[1]) <= 2.15),
                  key=lambda route: (exact_seam(areas, route), score(route)), reverse=True)


def place_near_route(areas, fixed, moving, routes):
    """Přesuň samostatný půdorys blíž ke vstupu, pokud se vejde do volného místa na mapě."""
    left, top, right, bottom = bounds(areas, fixed)
    branch_left, branch_top, branch_right, branch_bottom = bounds(areas, moving)
    stationary = [box(areas[uid], placement) for uid, placement in fixed.items()]
    choices = []
    for route in routes:
        if route.area in moving and route.target.area in fixed:
            route = route.reversed()
        source, target = route.area, route.target.area
        if source not in fixed or target not in moving or exact_seam(areas, route):
            continue
        x, y, end_x, end_y = box(areas[source], fixed[source])
        px, py = world_point(areas[source], fixed[source], route.point)
        qx, qy = world_point(areas[target], moving[target], route.landing)
        align_x, align_y = round(px - qx), round(py - qy)
        for dx, dy in ((align_x, y - branch_bottom - 1), (align_x, end_y - branch_top + 1),
                       (x - branch_right - 1, align_y), (end_x - branch_left + 1, align_y)):
            if not (left <= branch_left + dx <= branch_right + dx <= right
                    and top <= branch_top + dy <= branch_bottom + dy <= bottom):
                continue
            shifted = translated(moving, dx, dy)
            if all(not intersects(box(areas[uid], placement), other)
                   for uid, placement in shifted.items() for other in stationary):
                choices.append((dist((px, py), (qx + dx, qy + dy)), dx, dy))
    if not choices:
        return None
    _, dx, dy = min(choices)
    return translated(moving, dx, dy)


def arrange(project, areas=None, *, place_nearby=False):
    areas = exterior_areas(project) if areas is None else areas
    layers = interior_layers(areas)
    main = {uid: area for uid, area in areas.items() if uid not in layers} if layers else areas
    return add_interior_layers(areas, arrange_areas(project, main, place_nearby), layers)


def arrange_areas(project, areas, place_nearby):
    if not areas:
        return {}
    fixed = starting_placements(project, areas)
    leader = min(fixed) if fixed else None
    groups = {leader: fixed} if fixed else {}
    for uid, area in areas.items():
        if uid not in fixed:
            groups[uid] = {uid: Placement(0, 0, -area.north % 4)}
    owner = {uid: group for group, positions in groups.items() for uid in positions}

    for route in suitable_routes(project, areas):
        a, b = owner[route.area], owner[route.target.area]
        if a == b:
            continue
        if b == leader:
            a, b, route = b, a, route.reversed()
        joined = merge(areas, groups[a], groups[b], route)
        if joined is not None:
            groups[a] = joined
            del groups[b]
            for uid in joined:
                owner[uid] = a

    groups_by_size = sorted(groups, key=lambda g: (g == leader, len(groups[g]), project.active in groups[g]), reverse=True)
    base = groups[groups_by_size[0]]
    left, top, right, bottom = bounds(areas, base)
    result = dict(base)
    if not fixed:
        result = translated(base, -left, -top)
        right, bottom, left = right - left, bottom - top, 0
    loose = [groups[g] for g in groups_by_size[1:]]
    if not loose:
        return result
    if place_nearby:
        routes = [route for area in areas.values() for route in area_routes(project, area)
                  if route.target.area in areas and area.unit == areas[route.target.area].unit]
        remaining = []
        for branch in loose:
            nearby = place_near_route(areas, result, branch, routes)
            if nearby is None:
                remaining.append(branch)
            else:
                result.update(nearby)
        loose = remaining
    span = max(45, right - left, sqrt(sum(area.cols * area.rows for area in areas.values())) * 1.22)
    x, y, shelf = left, bottom + 7, 0
    for branch in loose:
        l, t, r, b = bounds(areas, branch)
        width, height = r - l, b - t
        if x > left and x + width > left + span:
            x, y, shelf = left, y + shelf + 5, 0
        result.update(translated(branch, x - l, y - t))
        x, shelf = x + width + 5, max(shelf, height)
    return result
