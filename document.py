"""Datový model map, otočení, historie změn a projektový soubor Kartografu."""
from contextlib import contextmanager
from base64 import b64decode, b64encode
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
import json
import math
import os
import tempfile
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PyQt5.QtGui import QImageReader
from PyQt5.QtWidgets import QUndoCommand

from catalog import ASSETS
from nwn_bestiary import ABILITY_FIELDS, CreatureCard, EncounterCard, EncounterEntry

MIN_SIZE, MAX_SIZE, MAX_AREAS = 1, 256, 512
MAX_LEVEL = 9999
MAX_REFERENCE_SIDE = 4096
MAX_REFERENCE_BYTES = 72 * 1024 * 1024
MAX_PROJECT_BYTES = 256 * 1024 * 1024
MAX_MINIMAP_SIDE = 256
MAX_MINIMAP_BYTES = MAX_MINIMAP_SIDE ** 2 * 4 + 16384
UNITS = {"nwn": ("NWN:EE", "1 pole = 1 dlaždice Toolsetu"),
         "dnd": ("D&D", "1 pole = 5 stop")}
AREA_KINDS = {"auto": "Podle terénu", "outdoor": "Exteriér", "indoor": "Interiér", "underground": "Podzemí"}
ATLAS_LIMIT = MAX_AREAS * (MAX_SIZE + 16)


@dataclass(frozen=True)
class TileLayout:
    """Rohy v pořadí SZ, SV, JV, JZ a křížení od severní hrany po směru hodin."""

    corners: tuple[str, str, str, str]
    offsets: tuple[int, int, int, int]
    crossings: tuple[str, str, str, str]
    index: int
    minimap: bytes = b""
    detail: bytes = b""


@dataclass(frozen=True)
class Tile:
    style: str
    turns: int = 0
    layout: TileLayout | None = None

    def oriented(self, values):
        return values[-self.turns:] + values[:-self.turns] if self.turns else values


@dataclass(frozen=True)
class Target:
    area: str
    x: int
    y: int


@dataclass(frozen=True)
class Mark:
    style: str
    turns: int = 0
    name: str = ""
    note: str = ""
    cr: str = ""
    dc: int | None = None
    hidden: bool = False
    target: Target | None = None
    origin_xy: tuple[float, float] | None = None
    arrival_xy: tuple[float, float] | None = None
    nwn_detail: CreatureCard | EncounterCard | None = None

    @classmethod
    def from_asset(cls, key, turns=0):
        asset = ASSETS[key]
        return cls(key, turns, cr=asset.cr, hidden=asset.dm)


@dataclass(frozen=True)
class Reference:
    data: bytes
    x: float
    y: float
    w: float
    h: float
    opacity: float = 0.40
    turns: int = 0
    visible: bool = True
    above: bool = True


@dataclass(frozen=True)
class ObjectPreview:
    bounds: tuple[float, float, float, float]
    data: bytes


@dataclass(frozen=True)
class Scenery:
    name: str
    point: tuple[float, float]
    angle: float
    elevation: float
    usable: bool
    preview: ObjectPreview | None


@dataclass
class Area:
    name: str
    cols: int
    rows: int
    unit: str = "nwn"
    uid: str = field(default_factory=lambda: uuid4().hex)
    north: int = 0
    tiles: dict = field(default_factory=dict)
    edges: dict = field(default_factory=dict)
    markers: dict = field(default_factory=dict)
    reference: Reference | None = None
    heights: dict = field(default_factory=dict)
    source_ref: str = ""
    tileset: str = ""
    kind: str = "auto"
    scenery: tuple[Scenery, ...] = ()

    def contains(self, x, y):
        return 0 <= x < self.cols and 0 <= y < self.rows

    def contains_edge(self, x, y, axis):
        return ((axis == "h" and 0 <= x < self.cols and 0 <= y <= self.rows)
                or (axis == "v" and 0 <= x <= self.cols and 0 <= y < self.rows))

    def corner_heights(self, x, y):
        level = self.heights.get((x, y), 0)
        tile = self.tiles.get((x, y))
        if tile and tile.layout and any(tile.layout.offsets):
            return tuple(level + offset for offset in tile.oriented(tile.layout.offsets))
        return (level, level, level, level)

    def markers_in(self, x, y, group=None):
        for key, mark in self.markers.items():
            if key[:2] == (x, y) and (group is None or marker_group(key[2]) == group):
                yield key, mark


@dataclass
class Placement:
    x: float
    y: float
    turns: int = 0


@dataclass
class Project:
    title: str
    areas: dict
    active: str
    atlas: dict[str, Placement] = field(default_factory=dict)
    nwn_source: tuple[str, int | None] | None = None

    @classmethod
    def single(cls, title, area):
        return cls(title, {area.uid: area}, area.uid)


def column_name(x):
    letters = ""
    while x >= 0:
        x, remainder = divmod(x, 26)
        letters = chr(65 + remainder) + letters
        x -= 1
    return letters


def cell_name(x, y):
    return f"{column_name(x)}{y + 1}"


def marker_group(slot):
    return slot.partition("#")[0]


def rotated_xy(width, height, point, turns):
    x, y = point
    return ((x, y), (height - y, x), (width - x, height - y), (y, width - x))[turns % 4]


def rotated_scenery(area, turns):
    return tuple(replace(item, point=rotated_xy(area.cols, area.rows, item.point, turns),
                         angle=(item.angle + turns * 90) % 360) for item in area.scenery)


class Patch(QUndoCommand):
    """Jedna změna i pro celý tah štětce nebo pro obě strany průchodu."""

    def __init__(self, title):
        super().__init__(title)
        self.edits = {}

    @staticmethod
    def _set(mapping, key, value):
        if value is None:
            mapping.pop(key, None)
        else:
            mapping[key] = value

    def put(self, mapping, key, value, now=False):
        identity = (id(mapping), key)
        previous = self.edits.get(identity)
        before = previous[2] if previous else mapping.get(key)
        if before == value:
            self.edits.pop(identity, None)
        else:
            self.edits[identity] = (mapping, key, before, value)
        if now:
            self._set(mapping, key, value)

    def redo(self):
        for mapping, key, _, value in self.edits.values():
            self._set(mapping, key, value)

    def undo(self):
        for mapping, key, value, _ in reversed(self.edits.values()):
            self._set(mapping, key, value)


class ProjectEdit(QUndoCommand):
    def __init__(self, project, attr, value, title):
        super().__init__(title)
        self.project, self.attr = project, attr
        self.before, self.after = getattr(project, attr), value

    def redo(self):
        setattr(self.project, self.attr, self.after)

    def undo(self):
        setattr(self.project, self.attr, self.before)


def rotate_area(project, uid, direction):
    """Otočí rastr, přepočítá hrany i všechna příchozí spojení."""
    area = project.areas[uid]
    if direction == 1:
        def cell(x, y):
            return area.rows - 1 - y, x

        def edge(x, y, axis):
            if axis == "h":
                return area.rows - y, x, "v"
            return area.rows - 1 - y, x, "h"
    else:
        def cell(x, y):
            return y, area.cols - 1 - x

        def edge(x, y, axis):
            if axis == "h":
                return y, area.cols - 1 - x, "v"
            return y, area.cols - x, "h"

    def turn(item):
        if ASSETS[item.style].directional or isinstance(item, Tile) and item.layout:
            return replace(item, turns=(item.turns + direction) % 4)
        return item

    def relink(item):
        target = item.target
        if target and target.area == uid:
            position = rotated_xy(area.cols, area.rows, item.arrival_xy, direction) if item.arrival_xy else None
            return replace(item, target=Target(uid, *cell(target.x, target.y)), arrival_xy=position)
        return item

    def relocate(item):
        if item.origin_xy:
            return replace(item, origin_xy=rotated_xy(area.cols, area.rows, item.origin_xy, direction))
        return item

    ref = area.reference
    if ref:
        x, y = (area.rows - ref.y - ref.h, ref.x) if direction == 1 else (ref.y, area.cols - ref.x - ref.w)
        ref = replace(ref, x=x, y=y, w=ref.h, h=ref.w, turns=(ref.turns + direction) % 4)

    changed = replace(
        area, cols=area.rows, rows=area.cols, north=(area.north + direction) % 4,
        tiles={cell(*key): turn(tile) for key, tile in area.tiles.items()},
        heights={cell(*key): value for key, value in area.heights.items()},
        edges={edge(*key): item for key, item in area.edges.items()},
        markers={(*cell(x, y), group): relink(relocate(turn(item)))
                 for (x, y, group), item in area.markers.items()},
        reference=ref,
        scenery=rotated_scenery(area, direction),
    )
    patch = Patch("Otočení oblasti")
    patch.put(project.areas, uid, changed)
    if placement := project.atlas.get(uid):
        patch.put(project.atlas, uid, replace(placement, turns=(placement.turns - direction) % 4))
    for other in project.areas.values():
        if other.uid != uid:
            for key, item in other.markers.items():
                if item.target and item.target.area == uid:
                    patch.put(other.markers, key, relink(item))
    return patch


def resize_allowed(project, area, cols, rows):
    dims = Area(area.name, cols, rows, area.unit)
    if (any(not dims.contains(*key) for key in area.tiles)
            or any(not dims.contains(*key) for key in area.heights)
            or any(not dims.contains(*key[:2]) for key in area.markers)
            or any(item.point[0] > cols or item.point[1] > rows for item in area.scenery)
            or any(not dims.contains_edge(*key) for key in area.edges)):
        return False
    return all(
        not item.target or item.target.area != area.uid or dims.contains(item.target.x, item.target.y)
        for origin in project.areas.values() for item in origin.markers.values()
    )


def delete_area(project, uid):
    keep = {}
    for key, area in project.areas.items():
        if key != uid:
            markers = {k: (replace(item, target=None, arrival_xy=None) if item.target and item.target.area == uid else item)
                       for k, item in area.markers.items()}
            keep[key] = replace(area, markers=markers) if markers != area.markers else area
    return ProjectEdit(project, "areas", keep, "Odebrání oblasti")


def set_marker(patch, project, area, key, value, now=False):
    """Při přepsání průchodu uvolní i jeho původní zpětné spojení."""
    previous = area.markers.get(key)
    if previous and previous.target and (value is None or value.target != previous.target):
        target = previous.target
        peer = project.areas.get(target.area)
        if peer:
            here = Target(area.uid, *key[:2])
            partners = [(k, m) for k, m in peer.markers_in(target.x, target.y, "link")
                        if m.target == here and (peer is not area or k != key)]
            siblings = any(k != key and m.target == target for k, m in area.markers_in(*key[:2], "link"))
            if len(partners) == 1 and not siblings:
                peer_key, partner = partners[0]
                patch.put(peer.markers, peer_key, replace(partner, target=None, arrival_xy=None), now)
    patch.put(area.markers, key, value, now)


@contextmanager
def atomic_path(path):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=f".{path.stem[:36]}-", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        yield Path(temp)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def import_reference(path, area):
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.isEmpty():
        raise ValueError("Soubor neobsahuje čitelný obrázek.")
    if max(size.width(), size.height()) > MAX_REFERENCE_SIDE:
        size.scale(QSize(MAX_REFERENCE_SIDE, MAX_REFERENCE_SIDE), Qt.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    if image.isNull() or max(image.width(), image.height()) > MAX_REFERENCE_SIDE:
        raise ValueError("Předlohu se nepodařilo načíst v podporované velikosti.")
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    extension = "PNG" if image.hasAlphaChannel() else "JPEG"
    if not image.save(buffer, extension, 91):
        raise ValueError("Předlohu se nepodařilo vložit do projektu.")
    return Reference(bytes(data), 0, 0, area.cols, area.rows)


def _encode_items(mapping):
    output = []
    for coords, item in sorted(mapping.items()):
        entry = [*coords, item.style, item.turns]
        if isinstance(item, Tile) and item.layout:
            layout = item.layout
            entry.append([layout.corners, layout.offsets, layout.crossings, layout.index])
        if isinstance(item, Mark):
            target = [item.target.area, item.target.x, item.target.y] if item.target else None
            fields = [item.name, item.note, item.cr, item.dc, item.hidden, target]
            if item.origin_xy or item.arrival_xy:
                fields.extend((item.origin_xy, item.arrival_xy))
            entry.append(fields)
        output.append(entry)
    return output


def save_project(project, path):
    header = {"format": "kartograf", "version": 3, "title": project.title, "active": project.active, "areas": []}
    if project.nwn_source:
        header["nwn_source"] = project.nwn_source
    if project.atlas:
        header["atlas"] = [[uid, p.x, p.y, p.turns] for uid, p in project.atlas.items() if uid in project.areas]
    minimaps = {(area.tileset, tile.layout.index): tile.layout.minimap
                for area in project.areas.values() for tile in area.tiles.values()
                if tile.layout and tile.layout.minimap}
    if minimaps:
        header["minimaps"] = [[tileset, index, b64encode(raw).decode("ascii")]
                              for (tileset, index), raw in sorted(minimaps.items())]
    details = {(area.tileset, tile.layout.index): tile.layout.detail
               for area in project.areas.values() for tile in area.tiles.values()
               if tile.layout and tile.layout.detail}
    if details:
        header["tile_details"] = [[tileset, index, b64encode(raw).decode("ascii")]
                                  for (tileset, index), raw in sorted(details.items())]
    models = {}
    reference_bytes = sum(len(a.reference.data) for a in project.areas.values() if a.reference)
    expanded = reference_bytes
    if expanded > MAX_PROJECT_BYTES:
        raise ValueError("Součet předloh v projektu překročil 256 MiB.")
    with atomic_path(path) as output, ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=4) as archive:
        for i, area in enumerate(project.areas.values()):
            record = dict(uid=area.uid, name=area.name, cols=area.cols, rows=area.rows, unit=area.unit, north=area.north)
            if area.kind != "auto":
                record["kind"] = area.kind
            for layer in ("tiles", "edges", "markers"):
                record[layer] = _encode_items(getattr(area, layer))
            bestiary = [[*key, asdict(item.nwn_detail)] for key, item in sorted(area.markers.items())
                        if item.nwn_detail is not None]
            if bestiary:
                record["nwn_bestiary"] = bestiary
            if area.heights:
                record["heights"] = [[x, y, level] for (x, y), level in sorted(area.heights.items())]
            if area.source_ref or area.tileset:
                record["nwn"] = [area.source_ref, area.tileset]
            if area.scenery:
                record["scenery"] = [
                    [item.name, item.point, item.angle, item.elevation, item.usable,
                     models.setdefault(item.preview, len(models)) if item.preview else None]
                    for item in area.scenery
                ]
            ref = area.reference
            if ref:
                record["reference"] = [i, ref.x, ref.y, ref.w, ref.h, ref.opacity, ref.turns, ref.visible, ref.above]
                archive.writestr(f"references/{i}", ref.data)
            header["areas"].append(record)
        if models:
            header["model_previews"] = [[item.bounds, b64encode(item.data).decode("ascii")] for item in models]
        manifest = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if expanded + len(manifest) > MAX_PROJECT_BYTES:
            raise ValueError("Projekt překročil 256 MiB.")
        archive.writestr("project.json", manifest)


def _require(condition):
    if not condition:
        raise ValueError("Projekt obsahuje neplatná nebo nepodporovaná data.")


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def _string(value, length):
    return isinstance(value, str) and len(value) <= length


def _number(value, size):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= size


def _point(value):
    if value is None:
        return None
    _require(len(value) == 2 and all(_number(v, MAX_SIZE) and v >= 0 for v in value))
    return tuple(value)


def _read_items(area, layer, entries, minimaps, details):
    mapping = getattr(area, layer)
    for entry in entries:
        x, y = entry[:2]
        _require(_integer(x, 0, MAX_SIZE) and _integer(y, 0, MAX_SIZE))
        if layer == "tiles":
            style, turns, *layout = entry[2:]
            location = (x, y)
        else:
            flag, style, turns, props = entry[2:]
            location = (x, y, flag)
        _require(style in ASSETS and ASSETS[style].layer == layer and _integer(turns, 0, 3))
        _require(location not in mapping)
        if layer == "edges":
            _require(area.contains_edge(*location))
        else:
            _require(area.contains(x, y))
        if layer == "tiles":
            _require(len(layout) <= 1)
            native = _read_layout(layout[0], area.tileset, minimaps, details) if layout else None
            mapping[location] = Tile(style, turns, native)
            continue
        if layer == "markers":
            _require(isinstance(flag, str) and marker_group(flag) == ASSETS[style].group)
            suffix = flag.partition("#")[2]
            _require("#" not in flag or suffix.isascii() and suffix.isdecimal() and 2 <= int(suffix) <= 65536)
        name, note, cr, dc, hidden, target, *coordinates = props
        _require(_string(name, 100) and _string(note, 4000) and _string(cr, 16)
                 and (dc is None or _integer(dc, 0, 999)) and type(hidden) is bool)
        if target is not None:
            uid, tx, ty = target
            _require(layer == "markers" and marker_group(flag) == "link" and _string(uid, 64)
                     and _integer(tx, 0, MAX_SIZE) and _integer(ty, 0, MAX_SIZE))
            target = Target(uid, tx, ty)
        origin = arrival = None
        if coordinates:
            _require(len(coordinates) == 2 and layer == "markers" and marker_group(flag) == "link")
            origin, arrival = map(_point, coordinates)
            _require(not origin or origin[0] <= area.cols and origin[1] <= area.rows)
            _require(not arrival or target is not None)
        mapping[location] = Mark(style, turns, name, note, cr, dc, hidden, target, origin, arrival)


def _read_creature_card(data):
    card = CreatureCard(**data)
    _require(_string(card.name, 100) and _string(card.resref, 64) and _string(card.cr, 16)
             and card.source in {"GIT", "UTC"} and _string(card.description, 8000))
    _require(len(card.abilities) == len(ABILITY_FIELDS)
             and all(score is None or _integer(score, 0, 255) for score in card.abilities))
    _require(len(card.stats) <= 24 and all(_string(label, 100) and _string(value, 4000)
                                           for label, value in card.stats))
    return replace(card, abilities=tuple(card.abilities), stats=tuple(tuple(row) for row in card.stats))


def _read_encounter_card(data):
    card = EncounterCard(**data)
    _require(_string(card.difficulty, 160) and (card.active is None or type(card.active) is bool)
             and _integer(card.spawn_points, 0, 2 ** 31 - 1)
             and all(value is None or _integer(value, 0, 65535) for value in (card.recommended, card.maximum)))
    entries = []
    for raw in card.entries:
        entry = EncounterEntry(**raw)
        _require(_string(entry.resref, 64) and _string(entry.cr, 16) and type(entry.single_spawn) is bool)
        entries.append(replace(entry, creature=_read_creature_card(entry.creature) if entry.creature is not None else None))
    return replace(card, entries=tuple(entries))


def _read_bestiary(area, records):
    for x, y, flag, raw in records:
        _require(_integer(x, 0, MAX_SIZE) and _integer(y, 0, MAX_SIZE) and isinstance(flag, str))
        key = x, y, flag
        item = area.markers.get(key)
        _require(item is not None and item.nwn_detail is None and marker_group(flag) == "monster")
        card = _read_encounter_card(raw) if "entries" in raw else _read_creature_card(raw)
        area.markers[key] = replace(item, nwn_detail=card)


def _read_layout(values, tileset, minimaps, details):
    corners, offsets, crossings, index = values
    _require(len(corners) == len(offsets) == len(crossings) == 4 and _integer(index, -1, 2**31 - 1))
    _require(all(v in ASSETS and ASSETS[v].layer == "tiles" for v in corners))
    _require(all(not v or v in ASSETS and ASSETS[v].layer == "tiles" for v in crossings))
    _require(all(_integer(v, -MAX_LEVEL, MAX_LEVEL) for v in offsets))
    key = tileset, index
    return TileLayout(tuple(corners), tuple(offsets), tuple(crossings), index, minimaps.get(key, b""), details.get(key, b""))


def _read_tile_images(rows):
    images = {}
    for tileset, index, encoded in rows:
        _require(_string(tileset, 64) and _integer(index, -1, 2**31 - 1))
        key = tileset, index
        _require(key not in images)
        images[key] = _read_tile_png(encoded)
    return images


def _read_tile_png(encoded):
    _require(_string(encoded, ((MAX_MINIMAP_BYTES + 2) // 3) * 4))
    raw = b64decode(encoded, validate=True)
    _require(len(raw) <= MAX_MINIMAP_BYTES)
    buffer = QBuffer()
    buffer.setData(raw)
    buffer.open(QIODevice.ReadOnly)
    size = QImageReader(buffer, b"png").size()
    _require(0 < size.width() <= MAX_MINIMAP_SIDE and 0 < size.height() <= MAX_MINIMAP_SIDE)
    return raw


def _read_model_previews(rows):
    models = []
    for rect, image in rows:
        _require(len(rect) == 4 and all(_number(value, MAX_SIZE) for value in rect))
        _require(rect[2] > 0 and rect[3] > 0)
        models.append(ObjectPreview(tuple(rect), _read_tile_png(image)))
    return models


def _read_scenery(area, records, models):
    items = []
    for name, point, angle, elevation, usable, model in records:
        point = _point(point)
        _require(point and point[0] <= area.cols and point[1] <= area.rows
                 and _string(name, 100) and _number(angle, 360) and angle >= 0
                 and _number(elevation, 100_000) and type(usable) is bool)
        _require(model is None or _integer(model, 0, len(models) - 1))
        items.append(Scenery(name, point, angle, elevation, usable,
                             models[model] if model is not None else None))
    return tuple(items)


def _read_reference(archive, data, index):
    number, x, y, w, h, opacity, turns, visible, above = data
    _require(number == index and all(type(n) in (int, float) and math.isfinite(n) and abs(n) <= 1_000_000
                                    for n in (x, y, w, h, opacity)))
    _require(0.25 <= w <= MAX_SIZE * 4 and 0.25 <= h <= MAX_SIZE * 4 and 0 <= opacity <= 1 and _integer(turns, 0, 3)
             and type(visible) is bool and type(above) is bool)
    info = archive.getinfo(f"references/{index}")
    _require(info.file_size <= MAX_REFERENCE_BYTES)
    raw = archive.read(info)
    buffer = QBuffer()
    buffer.setData(raw)
    buffer.open(QIODevice.ReadOnly)
    size = QImageReader(buffer).size()
    _require(size.isValid() and not size.isEmpty() and max(size.width(), size.height()) <= MAX_REFERENCE_SIDE)
    return Reference(raw, x, y, w, h, opacity, turns, visible, above)


def load_project(path):
    try:
        with ZipFile(path) as archive:
            contents = archive.infolist()
            _require(len(contents) <= MAX_AREAS + 1 and sum(info.file_size for info in contents) <= MAX_PROJECT_BYTES)
            data = json.loads(archive.read("project.json"))
            _require(data["format"] == "kartograf" and data["version"] in (1, 2, 3))
            _require(_string(data["title"], 100) and 1 <= len(data["areas"]) <= MAX_AREAS)
            minimaps = _read_tile_images(data.get("minimaps", []))
            details = _read_tile_images(data.get("tile_details", []))
            models = _read_model_previews(data.get("model_previews", []))
            areas = {}
            for index, record in enumerate(data["areas"]):
                uid, name, cols, rows, unit, north = (record[k] for k in ("uid", "name", "cols", "rows", "unit", "north"))
                _require(_string(uid, 64) and uid not in areas and _string(name, 100)
                         and _integer(cols, MIN_SIZE, MAX_SIZE) and _integer(rows, MIN_SIZE, MAX_SIZE)
                         and unit in UNITS and _integer(north, 0, 3))
                area = Area(name, cols, rows, unit, uid, north)
                area.kind = record.get("kind", "auto")
                _require(area.kind in AREA_KINDS)
                area.source_ref, area.tileset = record.get("nwn", ("", ""))
                _require(_string(area.source_ref, 64) and _string(area.tileset, 64))
                for x, y, level in record.get("heights", []):
                    _require(_integer(x, 0, MAX_SIZE) and _integer(y, 0, MAX_SIZE)
                             and area.contains(x, y) and _integer(level, -MAX_LEVEL, MAX_LEVEL))
                    _require((x, y) not in area.heights)
                    area.heights[x, y] = level
                for layer in ("tiles", "edges", "markers"):
                    _read_items(area, layer, record[layer], minimaps, details)
                _read_bestiary(area, record.get("nwn_bestiary", []))
                area.scenery = _read_scenery(area, record.get("scenery", []), models)
                if "reference" in record:
                    area.reference = _read_reference(archive, record["reference"], index)
                areas[uid] = area
            for area in areas.values():
                for item in area.markers.values():
                    target = item.target
                    if target:
                        _require(target.area in areas and areas[target.area].contains(target.x, target.y))
                        if item.arrival_xy:
                            other = areas[target.area]
                            _require(item.arrival_xy[0] <= other.cols and item.arrival_xy[1] <= other.rows)
            atlas = {}
            for uid, x, y, turns in data.get("atlas", []):
                _require(uid in areas and uid not in atlas and _number(x, ATLAS_LIMIT) and _number(y, ATLAS_LIMIT)
                         and _integer(turns, 0, 3))
                atlas[uid] = Placement(x, y, turns)
            source = None
            if "nwn_source" in data:
                filename, version = data["nwn_source"]
                _require(_string(filename, 256) and (version is None or _integer(version, 0, 0xffffffff)))
                source = filename, version
            active = data["active"]
            return Project(data["title"], areas, active if active in areas else next(iter(areas)), atlas, source)
    except (BadZipFile, KeyError, TypeError, OverflowError, AttributeError, RuntimeError) as error:
        raise ValueError("Soubor nelze otevřít jako projekt Kartografu.") from error
