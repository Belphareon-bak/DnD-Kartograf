"""Převod místního modulu NWN1 / NWN:EE na editovatelný projekt Kartografu."""

from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
import math
import os
import re

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage

from atlas_layout import COMPACT_MODULE_AREAS, NORMALS, SEAM_TOLERANCE, arrange, edge_point, nearest_edge, world_areas
from aurora_reader import (ARE, DDS, GIT, IFO, MAX_RESOURCE_BYTES, MDL, NCS, NSS, SET, TGA, TWO_DA, AuroraError,
                           ErfReader, GameResources, Localized, TalkTable, read_gff, text_bytes)
from document import (Area, Mark, MAX_AREAS, MAX_LEVEL, MAX_MINIMAP_SIDE, MAX_SIZE, Patch, Placement, Project, Scenery, Target,
                      Tile, TileLayout, cell_name, marker_group, rotated_xy)
from nwn_minimap import TEXTURE_SAMPLE_SIDE, dds_image, minimap_png, tga_texture
from nwn_objects import NAME, appearance_models, model_preview, tile_preview
from nwn_rules import nwn_rules

UTC = 2027
# Větší moduly sdílejí rozpočet pixelů mezi unikátními modely dlaždic.
MAX_DETAIL_PIXELS = 25_000_000
MAX_TEXTURE_CACHE_BYTES = 48 * 1024 * 1024
TEXTURE_FORMATS = ((DDS, ".dds", dds_image), (TGA, ".tga", tga_texture))
IFO_FIELDS = set("Mod_Name Mod_Version Mod_HakList Mod_Hak Mod_CustomTlk Mod_Area_list Area_Name "
                 "Mod_Entry_Area Mod_Entry_X Mod_Entry_Y".split())
ARE_FIELDS = set("Name Width Height TileSet Tileset Tile_List Tile_Height Tile_ID Tile_Orientation Flags".split())
GIT_FIELDS = set(
    "Tag TemplateResRef LocName LocalizedName FirstName LastName HasMapNote MapNote MapNoteEnabled "
    "LinkedTo LinkedToFlags XPosition YPosition PositionX PositionY X Y Geometry PointX PointY "
    "XOrientation YOrientation ScriptOnEnter VarTable Name Value "
    "Locked Lockable KeyRequired KeyName OpenLockDC TrapFlag TrapDetectDC DisarmDC TrapType "
    "HasInventory ItemList InventoryRes OnOpen OnUsed BaseItem ChallengeRating CreatureList ResRef CR SpawnPointList "
    "Appearance Useable Bearing Z".split()
) | {"Door List", "TriggerList", "Creature List", "Encounter List", "Placeable List", "WaypointList", "List"}
SCRIPT_TOKENS = re.compile(r'//[^\r\n]*|/\*.*?\*/|"(?:[^"\\]|\\.)*"|[A-Za-z_]\w*|\S', re.DOTALL)
SCRIPT_RESREF = re.compile(r'"([A-Za-z0-9_-]{1,16})"')
CORNERS = ("topleft", "topright", "bottomright", "bottomleft")
SIDES = ("top", "right", "bottom", "left")
TILE_LABELS = set((*CORNERS, *SIDES, *(corner + "height" for corner in CORNERS),
                   "interior", "imagemap2d", "model"))

SURFACES = {
    "grass": "grass", "gras": "grass", "forest": "grass", "trees": "forest", "fields": "grass",
    "mud": "mud", "marsh": "mud", "cobble": "paving", "plaza": "paving", "stone": "flagstone",
    "path": "path", "road": "path", "desert": "sand", "sand": "sand", "suelo": "sand",
    "cliff": "mountain", "rock": "rock", "gentlehill": "scree", "wayldhill": "scree", "snow": "snow",
    "water": "river", "agua": "river", "profundo": "lake", "shallows": "shallows",
    "chasm": "chasm", "chasym": "chasm", "pit": "chasm", "lava": "lava",
    "wall": "solid", "cave": "cave", "platform": "wood", "inn": "wood", "kitchen": "wood",
    "livingroom": "wood", "shop": "wood", "library": "wood", "storage": "wood", "rich": "marble",
    "jail": "flagstone", "room": "flagstone", "2x": "flagstone", "carvedfloor": "flagstone", "yellowfloor": "marble",
    "building": "roof", "svirfneblin": "roof", "drow": "roof", "poor": "roof", "keep": "roof",
    "goodcastle": "roof", "evilcastle": "roof", "hut": "roof", "tower": "roof",
}
CROSSERS = {
    "road": "path", "alley": "paving", "tunnel": "path", "stream": "river", "aquaduct": "river",
    "trench": "river", "bridge": "bridge", "dock": "bridge", "raisedbridge": "stone_bridge",
    "puente": "bridge", "ruinas": "paving", "wall": "solid", "fence": "solid",
    "cwall": "solid", "platwall": "solid", "watwall": "solid",
}
CORRIDORS = {"corridor", "door", "doorway", "passage", "tracks"}


@dataclass
class ImportReport:
    tiles: int = 0
    minimap_tiles: int = 0
    detailed_tiles: int = 0
    links: int = 0
    resolved_links: int = 0
    scripted_links: int = 0
    doors: int = 0
    creatures: int = 0
    encounters: int = 0
    traps: int = 0
    containers: int = 0
    scripted_containers: int = 0
    model_objects: int = 0
    usable_objects: int = 0
    missing_haks: list = field(default_factory=list)
    missing_tilesets: set = field(default_factory=set)
    unknown_tiles: set = field(default_factory=set)
    missing_gits: list = field(default_factory=list)
    missing_areas: list = field(default_factory=list)
    unknown_surfaces: set = field(default_factory=set)
    missing_tlk: str = ""

    def text(self, count, visible=None):
        scope = f" · na společné mapě {visible}" if visible is not None else ""
        detail = [
            f"Oblasti {count}{scope}",
            f"Minimapy NWN · {self.minimap_tiles}/{self.tiles} dlaždic",
            f"Průchody {self.resolved_links}/{self.links} přiřazených k cíli",
            f"Dveře {self.doors} · postavy {self.creatures} · střety {self.encounters}",
            f"Pasti {self.traps} · kontejnery s inventářem {self.containers} · se skriptem kořisti {self.scripted_containers}",
        ]
        if self.scripted_links:
            detail.append(f"Průchody z parametrů skriptu · {self.scripted_links}")
        if self.model_objects or self.usable_objects:
            detail.append(f"Půdorysy objektů · {self.model_objects} · bodové značky interakcí · {self.usable_objects}")
        if self.detailed_tiles:
            detail.append(f"Dlaždice vykreslené z modelů · {self.detailed_tiles}")
        if self.missing_haks:
            detail.append("Nedostupné HAKy · " + ", ".join(self.missing_haks))
        if self.missing_tilesets:
            detail.append("Nedostupné tilesety · " + ", ".join(sorted(self.missing_tilesets)))
        if self.missing_tlk:
            detail.append(f"Nedostupná tabulka textů · {self.missing_tlk}")
        if self.unknown_tiles or self.unknown_surfaces:
            detail.append(f"Náhradní podklad · {len(self.unknown_tiles)} typů dlaždic a {len(self.unknown_surfaces)} typů rohů")
        if self.missing_gits:
            detail.append(f"Oblasti bez záznamu objektů · {len(self.missing_gits)}")
        if self.missing_areas:
            detail.append("Chybějící soubory oblastí · " + ", ".join(self.missing_areas))
        return "\n".join(detail)


class ImportCancelled(Exception):
    pass


@dataclass(frozen=True)
class Endpoint:
    area: str
    cell: tuple[int, int]
    tag: str
    link: str = ""
    position: tuple[float, float] | None = None
    edge: int | None = None

    @property
    def target(self):
        return Target(self.area, *self.cell)


@dataclass(frozen=True)
class Passage:
    source: Endpoint
    flag: int
    object_: dict
    trigger: bool
    scripted: bool
    settings: dict


def profile_dir():
    for env in ("NWN_HOME", "NWN_USER_DIRECTORY"):
        if value := os.environ.get(env):
            return Path(value).expanduser()
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "Neverwinter Nights"


def game_folder(preference=None, auto=True):
    home = Path.home()
    roots = [preference]
    if auto:
        roots.extend((
            os.environ.get("KARTOGRAF_NWN_GAME"),
            home / "Games/Neverwinter Nights Enhanced Edition/game",
            home / ".local/share/Steam/steamapps/common/Neverwinter Nights",
            home / ".steam/steam/steamapps/common/Neverwinter Nights",
            home / "GOG Games/Neverwinter Nights Enhanced Edition/game",
        ))
    for root in roots:
        if root:
            root = Path(root).expanduser()
            for game in (root, root / "game", root.parent if root.name.casefold() == "data" else root):
                if any((game / "data").glob("*.key")):
                    return game
    return None


def _named_file(name, extension, directories):
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_. -]+", name) or name in (".", ".."):
        return None
    filename = name if name.casefold().endswith(extension) else name + extension
    for folder in directories:
        target = folder / filename
        if target.is_file():
            return target
        if folder.is_dir():
            found = next((f for f in folder.iterdir() if f.name.casefold() == filename.casefold() and f.is_file()), None)
            if found:
                return found
    return None


class Resources:
    def __init__(self, path, archive, ifo, report, game=None, profile=None):
        folder = path.parent
        profile = Path(profile).expanduser() if profile is not None else profile_dir()
        self.module = archive
        self.transition_scripts = nwn_rules().transition_scripts
        self.extra_assets = {}
        self._override_dirs = (folder, folder / "override", profile / "override")
        for directory in self._override_dirs:
            if directory.is_dir():
                for file in directory.iterdir():
                    if file.suffix.casefold() in {".set", ".tga", ".dds", ".2da", ".mdl"} and file.is_file():
                        self.extra_assets.setdefault(file.name.casefold(), file)
        self._tilesets = {}
        self._minimaps = {}
        self._creatures = {}
        self._loot_scripts = {}
        self._model_names = None
        self._model_previews = {}
        self._texture_colors = {}
        self._texture_images = OrderedDict()
        self._texture_bytes = 0
        self.haks = []
        haks = [entry.get("Mod_Hak") for entry in ifo.get("Mod_HakList", [])]
        haks.append(ifo.get("Mod_Hak"))
        for name in dict.fromkeys(n for n in haks if n):
            found = _named_file(name, ".hak", (folder, folder / "hak", folder / "haky", profile / "hak"))
            if found:
                self.haks.append(ErfReader(found, {SET, TGA, DDS, MDL, TWO_DA, UTC, NSS, NCS}))
            else:
                report.missing_haks.append(name)
        self.base = GameResources(game, {SET, TGA, DDS, MDL, TWO_DA, NSS}) if game else None
        self.custom = self.standard = None
        custom = ifo.get("Mod_CustomTlk")
        if custom:
            tlk = _named_file(custom, ".tlk", (folder, folder / "tlk", profile / "tlk"))
            if tlk:
                self.custom = TalkTable(tlk, "cp1250")
            else:
                report.missing_tlk = custom
        if game:
            for candidate in (game / "lang/en/data/dialog.tlk", game / "data/dialog.tlk", game / "dialog.tlk"):
                if candidate.is_file():
                    self.standard = TalkTable(candidate, "cp1252")
                    break

    def tileset(self, name):
        if name not in self._tilesets:
            raw = self._asset(name, SET, ".set")
            self._tilesets[name] = Tileset(raw) if raw else None
        return self._tilesets[name]

    def _asset(self, name, kind, extension):
        for archive in (self.module, *self.haks):
            if (name, kind) in archive.entries:
                return archive.read(name, kind)
        if file := self.extra_assets.get(name + extension):
            return self.loose_asset(file)
        return self.base.read(name, kind) if self.base else None

    @staticmethod
    def loose_asset(file):
        if file.stat().st_size > MAX_RESOURCE_BYTES:
            raise AuroraError(f"Příliš velký prostředek {file.name}.")
        return file.read_bytes()

    def minimap(self, tile):
        model = tile.get("model", "")
        candidates = dict.fromkeys((tile.get("imagemap2d", ""), f"mi_{model}" if model else ""))
        for label in candidates:
            name = label.removesuffix(".tga")
            if not re.fullmatch(r"[a-z0-9_-]{1,16}", name) or name == "null":
                continue
            if name not in self._minimaps:
                try:
                    data = self._asset(name, TGA, ".tga")
                    self._minimaps[name] = minimap_png(data) if data else b""
                except (OSError, AuroraError):
                    self._minimaps[name] = b""
            if image := self._minimaps[name]:
                return image
        return b""

    def texture_color(self, name):
        if name not in self._texture_colors:
            image = self.texture_image(name)
            color = (QColor("#b9b1a4") if image.isNull() else
                     image.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixelColor(0, 0))
            self._texture_colors[name] = color
        return self._texture_colors[name]

    def texture_sources(self, name):
        # Formáty porovnávej uvnitř stejného zdroje. HAK má přednost před základní hrou.
        for archive in (self.module, *self.haks):
            formats = [(kind, decode) for kind, _, decode in TEXTURE_FORMATS if (name, kind) in archive.entries]
            if formats:
                for kind, decode in formats:
                    yield archive.read(name, kind), decode
                return
        files = [(file, decode) for _, suffix, decode in TEXTURE_FORMATS
                 if (file := self.extra_assets.get(name + suffix))]
        if files:
            directory = min((file.parent for file, _ in files), key=self._override_dirs.index)
            for file, decode in files:
                if file.parent == directory:
                    yield self.loose_asset(file), decode
            return
        if self.base:
            for kind, _, decode in TEXTURE_FORMATS:
                if (name, kind) in self.base.entries:
                    yield self.base.read(name, kind), decode

    def texture_image(self, name):
        if name in self._texture_images:
            self._texture_images.move_to_end(name)
            return self._texture_images[name]
        image = QImage()
        if NAME.fullmatch(name) and name != "null":
            try:
                for raw, decode in self.texture_sources(name):
                    if raw:
                        candidate = decode(raw)
                        if candidate.width() * candidate.height() > image.width() * image.height():
                            image = candidate
                        if min(image.width(), image.height()) >= TEXTURE_SAMPLE_SIDE:
                            break
            except (OSError, AuroraError):
                pass
        while self._texture_images and self._texture_bytes + image.sizeInBytes() > MAX_TEXTURE_CACHE_BYTES:
            _, removed = self._texture_images.popitem(last=False)
            self._texture_bytes -= removed.sizeInBytes()
        self._texture_images[name] = image
        self._texture_bytes += image.sizeInBytes()
        return image

    def tile_model(self, tileset, index):
        data = self.tileset(tileset)
        name = data.entries.get(index, {}).get("model", "") if data else ""
        if NAME.fullmatch(name) and (f"{name}.mdl" in self.extra_assets or any(
                source and (name, MDL) in source.entries for source in (self.module, *self.haks, self.base))):
            return name
        return None

    def tile_detail(self, name, side):
        try:
            raw = self._asset(name, MDL, ".mdl")
            return tile_preview(raw, side, self.texture_color, self.texture_image)
        except (OSError, AuroraError):
            return b""

    def placeable_preview(self, appearance):
        if type(appearance) is not int:
            return None
        if self._model_names is None:
            try:
                self._model_names = appearance_models(self._asset("placeables", TWO_DA, ".2da"))
            except (OSError, ValueError):
                self._model_names = {}
        name = self._model_names.get(appearance)
        if not name:
            return None
        if name not in self._model_previews:
            try:
                raw = self._asset(name, MDL, ".mdl")
                self._model_previews[name] = model_preview(raw, self.texture_color, self.texture_image)
            except (OSError, AuroraError):
                self._model_previews[name] = None
        return self._model_previews[name]

    def creature(self, name):
        if name and name not in self._creatures:
            self._creatures[name] = {}
            for archive in (self.module, *self.haks):
                raw = archive.read(name, UTC)
                if raw:
                    self._creatures[name] = read_gff(raw, GIT_FIELDS)
                    break
        return self._creatures.get(name, {})

    def _script(self, name):
        for archive in (self.module, *self.haks):
            if (name, NSS) in archive.entries:
                return archive.read(name, NSS)
            if (name, NCS) in archive.entries:
                # Kompilovaná náhrada může mít jiné chování než stejnojmenný skript původní hry.
                return None
        return self.base.read(name, NSS) if self.base else None

    def loot_script(self, name):
        if name not in self._loot_scripts:
            pending = [name]
            visited = set()
            found = False
            while pending and not found:
                script = pending.pop()
                if script in visited:
                    continue
                visited.add(script)
                if raw := self._script(script):
                    found, called = loot_calls(text_bytes(raw))
                    pending.extend(called)
            self._loot_scripts[name] = found
        return self._loot_scripts[name]

    def text(self, value):
        if isinstance(value, Localized):
            for raw in value.variants.values():
                if text := text_bytes(raw).strip():
                    return text
            if value.strref == 0xffffffff:
                return ""
            if value.strref >= 0x1000000:
                return self.custom.text(value.strref - 0x1000000).strip() if self.custom else ""
            if self.standard:
                return self.standard.text(value.strref).strip()
        return value.strip() if isinstance(value, str) else ""

    def name(self, object_):
        for label in ("LocName", "LocalizedName"):
            if name := self.text(object_.get(label)):
                return name
        name = " ".join(value for key in ("FirstName", "LastName") if (value := self.text(object_.get(key))))
        return name or object_.get("Tag") or object_.get("TemplateResRef") or ""


class Tileset:
    def __init__(self, raw):
        self.entries = {}
        self.general = {}
        section = None
        for line in text_bytes(raw).splitlines():
            line = line.split(";", 1)[0].strip().casefold()
            if line.startswith("["):
                heading = line.strip("[]").strip()
                if heading == "general":
                    section = self.general
                elif heading.startswith("tile") and heading[4:].isascii() and heading[4:].isdecimal():
                    section = self.entries.setdefault(int(heading[4:]), {})
                else:
                    section = None
            elif "=" in line and section is not None:
                key, value = line.split("=", 1)
                if (key := key.strip()) in TILE_LABELS:
                    section[key] = value.strip()


def _integer(value, default=0):
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return default


def _limited_level(value):
    return max(-MAX_LEVEL, min(MAX_LEVEL, _integer(value)))


def default_surface(flags, tileset, name):
    interior = tileset.general.get("interior") == "1" if tileset else flags & 1
    if name == "tbm01":
        return "mountain"
    if name in {"tdm01", "tcd01", "tdin1", "tw002"} or flags & 2 and flags & 4:
        return "cave"
    if interior:
        return "flagstone"
    if name in {"tts02", "tts01"}:
        return "snow"
    if name == "ttd01":
        return "sand"
    return "grass"


def tile_layout(tileset, name, index, fallback, report, resources):
    entry = tileset.entries.get(index) if tileset else None
    if entry is None:
        if tileset:
            report.unknown_tiles.add((name, index))
        return fallback, TileLayout((fallback,) * 4, (0, 0, 0, 0), ("",) * 4, index)

    def terrain(token):
        stem = token.rstrip("0123456789")
        if stem == "stone" and name == "tbm01":
            return "mountain"
        if stem == "floor":
            return fallback if fallback in {"cave", "flagstone"} else "paving"
        if stem in SURFACES:
            return SURFACES[stem]
        if token:
            report.unknown_surfaces.add((name, token))
        return fallback

    def crosser(token):
        token = token.rstrip("0123456789")
        if token in CORRIDORS:
            return "cave" if fallback == "cave" else "flagstone"
        return CROSSERS.get(token, "")

    corners = tuple(terrain(entry.get(corner, "")) for corner in CORNERS)
    offsets = tuple(_limited_level(entry.get(corner + "height")) for corner in CORNERS)
    crossing = tuple(crosser(entry.get(side, "")) for side in SIDES)
    base = Counter(corners).most_common(1)[0][0]
    return base, TileLayout(corners, offsets, crossing, index, resources.minimap(entry))


def add_tile_details(areas, keys, resources, report, progress):
    models = {(tileset, index): model for tileset, index in keys
              if (model := resources.tile_model(tileset, index))}
    names = dict.fromkeys(models.values())
    if not names:
        return
    side = min(MAX_MINIMAP_SIDE, math.isqrt(MAX_DETAIL_PIXELS // len(names)))
    if side < 64:
        return

    for i, name in enumerate(names):
        if progress and progress(i, len(names), f"Model dlaždice {name}") is False:
            raise ImportCancelled
        names[name] = resources.tile_detail(name, side)
    for area in areas.values():
        for cell, tile in area.tiles.items():
            image = names.get(models.get((area.tileset, tile.layout.index)))
            if image:
                area.tiles[cell] = replace(tile, layout=replace(tile.layout, detail=image))
                report.detailed_tiles += 1
    if progress and progress(len(names), len(names), "") is False:
        raise ImportCancelled


def area_position(area, object_, polygon=False):
    for x_key, y_key in (("X", "Y"), ("XPosition", "YPosition"), ("PositionX", "PositionY")):
        if x_key in object_ and y_key in object_:
            x, y = object_[x_key], object_[y_key]
            if not (isinstance(x, (int, float)) and math.isfinite(x) and isinstance(y, (int, float)) and math.isfinite(y)):
                return None
            if polygon:
                points = [(p.get("PointX"), p.get("PointY")) for p in object_.get("Geometry", [])]
                good = [(u, v) for u, v in points if isinstance(u, (float, int)) and isinstance(v, (float, int))
                        and math.isfinite(u) and math.isfinite(v)]
                if good:
                    x += sum(q[0] for q in good) / len(good)
                    y += sum(q[1] for q in good) / len(good)
            px, py = x / 10, area.rows - y / 10
            if -0.02 <= px <= area.cols + 0.02 and -0.02 <= py <= area.rows + 0.02:
                return px, py
            return None
    return None


def locate(area, position):
    x, y = position
    return min(area.cols - 1, max(0, math.floor(x))), min(area.rows - 1, max(0, math.floor(y)))


def cr_text(value):
    if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0:
        return f"{value:g}"
    return ""


def key_tag(value):
    return value.casefold() if isinstance(value, str) else ""


def note_for(object_, extra=""):
    tag = object_.get("Tag") or ""
    template = object_.get("TemplateResRef") or ""
    labels = [" · ".join(v for v in (f"Tag {tag}" if tag else "", f"Šablona {template}" if template else "") if v)]
    if object_.get("KeyRequired") and (key := object_.get("KeyName")):
        labels.append(f"Klíč {key}")
    if extra:
        labels.append(extra)
    return "\n".join(s for s in labels if s)[:4000]


def marker_slot(markers, cell, group):
    slot = (*cell, group)
    if slot in markers:
        index = 2
        while (*cell, f"{group}#{index}") in markers:
            index += 1
        slot = (*cell, f"{group}#{index}")
    return slot


def add_mark(area, cell, group, mark):
    area.markers[marker_slot(area.markers, cell, group)] = mark


def named_mark(style, name, note="", **changes):
    return replace(Mark.from_asset(style), name=name[:100], note=note[:4000], **changes)


def dc_value(value):
    return value if type(value) is int and 0 <= value <= 999 else None


def add_door(area, pos, obj, name):
    key = None
    x, y = locate(area, pos)
    along_x, along_y = abs(pos[0] - round(pos[0])), abs(pos[1] - round(pos[1]))
    if min(along_x, along_y) <= .26:
        key = (round(pos[0]), y, "v") if along_x < along_y else (x, round(pos[1]), "h")
    locked = bool(obj.get("Locked"))
    dc = dc_value(obj.get("OpenLockDC")) if locked else None
    note = note_for(obj)
    if key and area.contains_edge(*key) and key not in area.edges:
        area.edges[key] = named_mark("locked" if locked else "door", name, note, dc=dc)
    else:
        add_mark(area, (x, y), "door", named_mark("locked_point" if locked else "door_point", name, note, dc=dc))


def add_trap(area, cell, obj, name, report):
    if obj.get("TrapFlag"):
        detect = dc_value(obj.get("TrapDetectDC"))
        text = f"Najití {detect}" if detect is not None else ""
        add_mark(area, cell, "trap", named_mark("plate", name, note_for(obj, text),
                                               dc=dc_value(obj.get("DisarmDC"))))
        report.traps += 1


def script_arguments(tokens, start):
    depth = 0
    args = []
    for index in range(start, len(tokens)):
        token = tokens[index]
        if depth == 0 and token in {",", ")"}:
            args.append(tokens[start:index])
            if token == ")":
                return args
            start = index + 1
        depth += (token == "(") - (token == ")")
    return []


def loot_calls(source):
    """Vyhledej doložitelné plnění inventáře kontejneru ve zdrojovém NWScriptu."""
    tokens = [token for token in SCRIPT_TOKENS.findall(source) if not token.startswith(("//", "/*"))]
    own = {"OBJECT_SELF"}
    for index in range(len(tokens) - 3):
        if tokens[index] == "object" and tokens[index + 2:index + 4] == ["=", "OBJECT_SELF"]:
            own.add(tokens[index + 1])

    called = []
    for index, name in enumerate(tokens[:-1]):
        if tokens[index + 1] != "(":
            continue
        generates = name in {"GetMagic", "GetTreasure", "DTSGenerateTreasureOnContainer"} or (
            name.startswith("Generate") and name.endswith("Treasure")
        )
        if not generates and name not in {"CreateItemOnObject", "ExecuteScript"}:
            continue
        args = script_arguments(tokens, index + 2)
        target = 0 if name == "DTSGenerateTreasureOnContainer" else 1
        if len(args) <= target or len(args[target]) != 1 or args[target][0] not in own:
            continue
        if generates:
            return True, called
        if len(args[0]) == 1 and (literal := SCRIPT_RESREF.fullmatch(args[0][0])):
            if name == "CreateItemOnObject":
                return True, called
            called.append(literal[1].casefold())
    return False, called


def container_script(obj, resources):
    for event in ("OnOpen", "OnUsed"):
        name = key_tag(obj.get(event))
        if name and resources.loot_script(name):
            return name
    return ""


def landing_edge(obj):
    x, y = obj.get("XOrientation"), obj.get("YOrientation")
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
        return None
    # Směr příchodu míří dovnitř. V mapě navíc osa Y roste směrem k jihu.
    return next((i for i, (dx, dy) in enumerate(NORMALS)
                 if math.isclose(-x, dx, abs_tol=.04) and math.isclose(y, dy, abs_tol=.04)), None)


def add_objects(area, git, res, report, target_tags, transitions):
    for field_, kind in (("Door List", 1), ("WaypointList", 2), ("TriggerList", 0)):
        for obj in git.get(field_, []):
            pos = area_position(area, obj, polygon=kind == 0)
            if pos is None:
                continue
            cell = locate(area, pos)
            tag, to, flag = key_tag(obj.get("Tag")), key_tag(obj.get("LinkedTo")), obj.get("LinkedToFlags")
            settings = {}
            scripted = False
            if kind == 0 and key_tag(obj.get("ScriptOnEnter")) in res.transition_scripts:
                settings = {row.get("Name"): row.get("Value") for row in obj.get("VarTable", [])}
                if flag not in (1, 2) and (script_target := key_tag(settings.get("target"))):
                    flag, to, scripted = 2, script_target, True
            endpoint = Endpoint(area.uid, cell, tag, to,
                                (min(area.cols, max(0, pos[0])), min(area.rows, max(0, pos[1]))),
                                landing_edge(obj) if kind == 2 else None)
            if kind and tag:
                target_tags[kind, tag].append(endpoint)
            if kind != 2 and to and flag in (1, 2):
                transitions.append(Passage(endpoint, flag, obj, kind == 0, scripted, settings))
            name = res.name(obj)
            if kind == 1:
                add_door(area, pos, obj, name)
                report.doors += 1
            elif kind == 2 and obj.get("HasMapNote"):
                note = res.text(obj.get("MapNote"))
                if note or name:
                    add_mark(area, cell, "label", named_mark("point", note or name, note_for(obj),
                                                            hidden=not bool(obj.get("MapNoteEnabled"))))
            if kind != 2:
                add_trap(area, cell, obj, name, report)

    for obj in git.get("Creature List", []):
        pos = area_position(area, obj)
        if pos is None:
            continue
        template = res.creature(obj.get("TemplateResRef")) if obj.get("ChallengeRating") is None else {}
        data = template | obj
        add_mark(area, locate(area, pos), "monster",
                 named_mark("monster", res.name(data), note_for(obj), cr=cr_text(data.get("ChallengeRating"))))
        report.creatures += 1

    for obj in git.get("Encounter List", []):
        pos = area_position(area, obj, polygon=True) or area_position(area, obj)
        if pos is None:
            continue
        pool = [f"{pawn.get('ResRef', '')} · CR {cr_text(pawn.get('CR'))}" for pawn in obj.get("CreatureList", [])]
        spawn = [cell_name(*locate(area, where)) for entry in obj.get("SpawnPointList", [])
                 if (where := area_position(area, entry)) is not None]
        lines = ["Střet NWN"]
        if pool:
            lines.append("Zásoba nestvůr · " + ", ".join(pool))
        if spawn:
            lines.append("Místa výskytu · " + ", ".join(dict.fromkeys(spawn)))
        add_mark(area, locate(area, pos), "monster", named_mark("monster", res.name(obj), note_for(obj, "\n".join(lines))))
        report.encounters += 1

    scenery = []
    for obj in git.get("Placeable List", []):
        if (pos := area_position(area, obj)) is None:
            continue
        name = res.name(obj)
        preview = res.placeable_preview(obj.get("Appearance"))
        usable = bool(obj.get("Useable"))
        if preview or (usable and name):
            point = (min(area.cols, max(0, pos[0])), min(area.rows, max(0, pos[1])))
            bearing, height = obj.get("Bearing", 0), obj.get("Z", 0)
            if not isinstance(bearing, (int, float)) or not math.isfinite(bearing):
                bearing = 0
            angle = (-math.degrees(bearing % math.tau)) % 360
            height = height if isinstance(height, (int, float)) and math.isfinite(height) else 0
            height = min(100_000, max(-100_000, height))
            scenery.append(Scenery(name[:100], point, angle, height, usable, preview))
            report.model_objects += bool(preview)
            report.usable_objects += not preview
        if not (obj.get("HasInventory") or obj.get("TrapFlag")):
            continue
        cell = locate(area, pos)
        if obj.get("HasInventory"):
            inventory = obj.get("ItemList") or []
            script = "" if inventory else container_script(obj, res)
        else:
            inventory, script = [], ""
        if inventory or script:
            if inventory:
                items = [item["InventoryRes"] for item in inventory if item.get("InventoryRes")]
                summary = f"Položky inventáře · {len(inventory)}"
                if items:
                    summary += "\n" + ", ".join(items)
                report.containers += 1
            else:
                summary = f"Skript kořisti · {script}"
                report.scripted_containers += 1
            dc = dc_value(obj.get("OpenLockDC")) if obj.get("Locked") else None
            add_mark(area, cell, "treasure", named_mark("chest", name, note_for(obj, summary), dc=dc))
        add_trap(area, cell, obj, name, report)
    area.scenery = tuple(scenery)

    for obj in git.get("List", []):
        if (pos := area_position(area, obj)) is not None:
            add_mark(area, locate(area, pos), "treasure", named_mark("loot", res.name(obj), note_for(obj)))


def direction_for(area, direction_from, target):
    x, y = direction_from
    if target and target.area == area.uid:
        dx, dy = target.x + .5 - x, target.y + .5 - y
        if abs(dx) > abs(dy):
            return 1 if dx > 0 else 3
        return 0 if dy < 0 else 2
    distances = (y, area.cols - x, area.rows - y, x)
    return min(range(4), key=distances.__getitem__)


def resolve_transition(endpoint, flag, targets):
    candidates = targets.get((flag, endpoint.link), ())
    if len(candidates) > 1 and flag == 1 and endpoint.tag:
        reciprocal = [target for target in candidates if target.link == endpoint.tag]
        if reciprocal:
            candidates = reciprocal
    locations = {target.target for target in candidates}
    if len(locations) == 1:
        positions = {candidate.position for candidate in candidates}
        directions = {candidate.edge for candidate in candidates}
        chosen = candidates[0]
        return replace(chosen, position=chosen.position if len(positions) == 1 else None,
                       edge=chosen.edge if len(directions) == 1 else None), 1
    return None, len(locations)


def passage_edges(areas, passage, landing):
    if (not passage.trigger or passage.flag != 2 or not landing or landing.edge is None
            or landing.position is None or passage.source.area == landing.area):
        return None
    source = passage.source
    first, second = areas[source.area], areas[landing.area]
    direction, distance = nearest_edge(first, source.position)
    if distance > .25:
        return None
    zero = Placement(0, 0)
    a = edge_point(first, zero, source.position, direction)
    b = edge_point(second, zero, landing.position, landing.edge)
    if (math.dist(b, landing.position) > .7 or nearest_edge(second, b)[0] != landing.edge):
        return None
    turned = rotated_xy(second.cols, second.rows, b, (direction + 2 - landing.edge) % 4)
    if any(abs((u - v) - round(u - v)) > SEAM_TOLERANCE for u, v in zip(a, turned)):
        return None
    return source.area, landing.area, tuple(round(v, 4) for v in a), tuple(round(v, 4) for v in b)


def import_passages(areas, transitions, targets, resources, report):
    resolved = [resolve_transition(passage.source, passage.flag, targets) for passage in transitions]
    candidates = [passage_edges(areas, passage, landing) for passage, (landing, _) in zip(transitions, resolved)]
    edges = set(candidates)
    for passage, (destination, count), seam in zip(transitions, resolved, candidates):
        source, obj = passage.source, passage.object_
        area = areas[source.area]
        report.links += 1
        target = destination.target if destination else None
        point = source.position
        landing = destination.position if destination else None
        notes = [f"Cílový tag {source.link}"]
        if passage.scripted:
            report.scripted_links += 1
            notes.append(f"Průchod řídí skript {key_tag(obj.get('ScriptOnEnter'))}")
        if target:
            report.resolved_links += 1
        else:
            notes[0] += " · více možných cílů" if count else " · cíl nenalezen"
        if seam:
            first, second, a, b = seam
            if (second, first, b, a) in edges:
                point, landing = a, b
        if requirement := passage.settings.get("require"):
            notes.append(f"Podmínka · {requirement}")
        if minimum := passage.settings.get("minimum_level"):
            notes.append(f"Minimální úroveň · {minimum}")
        turns = direction_for(area, source.position, target)
        mark = named_mark("exit", resources.name(obj), note_for(obj, "\n".join(notes)), turns=turns,
                          target=target, origin_xy=point, arrival_xy=landing,
                          hidden=bool(passage.settings.get("hidden_route")))
        add_mark(area, source.cell, "link", mark)


def import_module(path, game=None, profile=None, progress=None, *, tile_details=True):
    path = Path(path).expanduser().resolve()
    report = ImportReport()
    archive = ErfReader(path, {ARE, SET, TGA, DDS, MDL, TWO_DA, IFO, GIT, UTC, NSS, NCS})
    raw = archive.read("module", IFO)
    if not raw:
        raise AuroraError(f"{path.name} neobsahuje modul NWN s module.ifo.")
    ifo = read_gff(raw, IFO_FIELDS)
    are_refs = [key for key, kind in archive.entries if kind == ARE]
    if not 0 < len(are_refs) <= MAX_AREAS:
        raise AuroraError(f"Editor potřebuje 1 až {MAX_AREAS} oblastí v jednom modulu. Nalezeno {len(are_refs)}.")
    resources = Resources(path, archive, ifo, report, game_folder(game), profile)
    preferred = [key_tag(row.get("Area_Name")) for row in ifo.get("Mod_Area_list", [])]
    known = set(are_refs)
    report.missing_areas = list(dict.fromkeys(ref for ref in preferred if ref and ref not in known))
    ordered = list(dict.fromkeys(ref for ref in [*preferred, *are_refs] if ref in known))
    areas = {}
    targets = defaultdict(list)
    transitions = []
    tile_cache = {}
    for number, ref in enumerate(ordered, 1):
        if progress and progress(number - 1, len(ordered), ref) is False:
            raise ImportCancelled
        try:
            are = read_gff(archive.read(ref, ARE), ARE_FIELDS)
            width, height = are.get("Width"), are.get("Height")
            if not all(type(value) is int and 1 <= value <= MAX_SIZE for value in (width, height)):
                raise AuroraError(f"Neplatné rozměry {width} × {height}.")
            name = resources.text(are.get("Name")) or ref
            tileset_name = key_tag(are.get("Tileset") or are.get("TileSet"))
            flags = are.get("Flags", 0)
            kind = "underground" if flags & 2 else "indoor" if flags & 1 else "outdoor"
            area = Area(name[:100], width, height, uid=ref, source_ref=ref, tileset=tileset_name, kind=kind)
            tileset = resources.tileset(tileset_name)
            if tileset is None:
                report.missing_tilesets.add(tileset_name or "(bez názvu)")
            fallback = default_surface(flags, tileset, tileset_name)
            tiles = are.get("Tile_List", [])
            if len(tiles) != width * height:
                raise AuroraError(f"Rozměr vyžaduje {width * height} dlaždic, v záznamu jich je {len(tiles)}.")
            for i, item in enumerate(tiles):
                index = _integer(item.get("Tile_ID"), -1)
                if not -1 <= index < (1 << 31):
                    raise AuroraError(f"Neplatné číslo dlaždice {index}.")
                turns = (-_integer(item.get("Tile_Orientation"))) % 4
                at = i % width, height - 1 - i // width
                key = (tileset_name, index, fallback)
                if key not in tile_cache:
                    tile_cache[key] = tile_layout(tileset, tileset_name, index, fallback, report, resources)
                style, layout = tile_cache[key]
                area.tiles[at] = Tile(style, turns, layout)
                report.minimap_tiles += bool(layout.minimap)
                if (level := _limited_level(item.get("Tile_Height"))):
                    area.heights[at] = level
            areas[ref] = area
            report.tiles += len(tiles)
            if instance := archive.read(ref, GIT):
                add_objects(area, read_gff(instance, GIT_FIELDS), resources, report, targets, transitions)
            else:
                report.missing_gits.append(ref)
        except (AuroraError, TypeError, KeyError, AttributeError) as error:
            raise AuroraError(f"Oblast {ref} · {error}") from error

    import_passages(areas, transitions, targets, resources, report)

    active = key_tag(ifo.get("Mod_Entry_Area"))
    if active not in areas:
        active = next(iter(areas))
    elif (entry := area_position(areas[active], {"X": ifo.get("Mod_Entry_X"), "Y": ifo.get("Mod_Entry_Y")})) is not None:
        add_mark(areas[active], locate(areas[active], entry), "label", named_mark("point", "Vstup modulu"))
    title = resources.text(ifo.get("Mod_Name")) or path.stem
    if progress and progress(len(ordered), len(ordered), "") is False:
        raise ImportCancelled
    if tile_details:
        add_tile_details(areas, dict.fromkeys((tileset, index) for tileset, index, _ in tile_cache),
                         resources, report, progress)
    version = ifo.get("Mod_Version")
    if type(version) is not int or not 0 <= version <= 0xffffffff:
        version = None
    project = Project(title[:100], areas, active, nwn_source=(path.name, version))
    project.atlas = arrange(project, world_areas(project, include_hidden=True))
    project.atlas = arrange(project, project.areas, place_nearby=len(areas) <= COMPACT_MODULE_AREAS)
    return project, report


def native_grid_rotation(area, source):
    if area.tileset != source.tileset:
        return None
    # Sever lze v editoru změnit samostatně. Otočení půdorysu ověř podle nativních dlaždic.
    native = [(cell, tile) for cell, tile in area.tiles.items() if tile.layout and tile.layout.index >= 0]
    if not native:
        return None
    for turns in range(4):
        size = (source.rows, source.cols) if turns % 2 else (source.cols, source.rows)
        if (area.cols, area.rows) != size:
            continue
        for (x, y), tile in native:
            px, py = rotated_xy(area.cols, area.rows, (x + .5, y + .5), -turns)
            original = source.tiles.get((math.floor(px), math.floor(py)))
            if (not original or not original.layout or original.layout.index != tile.layout.index
                    or (original.turns + turns) % 4 != tile.turns):
                break
        else:
            return turns
    return None


def treasure_identity(mark):
    provenance = mark.note.partition("\n")[0]
    return mark.style, provenance if provenance.startswith(("Tag ", "Šablona ")) else mark.name


def merge_nwn_treasures(project, imported):
    """Připrav jednu vratnou změnu značek pro oblasti ze stejného půdorysu NWN."""
    patch = Patch("Doplnění pokladů z modulu NWN")
    matched = 0
    for area in project.areas.values():
        source = imported.areas.get(area.source_ref)
        if source is None or (turns := native_grid_rotation(area, source)) is None:
            continue
        matched += 1
        existing = Counter((key[:2], treasure_identity(mark)) for key, mark in area.markers.items()
                           if marker_group(key[2]) == "treasure")
        occupied = set(area.markers)
        for (x, y, slot), mark in source.markers.items():
            if marker_group(slot) != "treasure":
                continue
            px, py = rotated_xy(source.cols, source.rows, (x + .5, y + .5), turns)
            cell = math.floor(px), math.floor(py)
            identity = cell, treasure_identity(mark)
            if existing[identity]:
                existing[identity] -= 1
            else:
                key = marker_slot(occupied, cell, "treasure")
                occupied.add(key)
                patch.put(area.markers, key, mark)
    return patch, matched


def merge_nwn_tile_images(project, imported):
    """Aktualizuj grafiku ověřených typů dlaždic v jedné vratné změně."""
    patch = Patch("Aktualizace podkladů dlaždic z modulu NWN")
    assets = {}
    matched = 0
    for area in project.areas.values():
        source = imported.areas.get(area.source_ref)
        if source is None or native_grid_rotation(area, source) is None:
            continue
        matched += 1
        for tile in source.tiles.values():
            if tile.layout and tile.layout.index >= 0:
                assets[source.tileset, tile.layout.index] = tile.layout

    for area in project.areas.values():
        for cell, tile in area.tiles.items():
            layout = tile.layout
            candidate = assets.get((area.tileset, layout.index)) if layout else None
            if candidate:
                refreshed = replace(layout, minimap=candidate.minimap or layout.minimap,
                                    detail=candidate.detail or layout.detail)
                patch.put(area.tiles, cell, replace(tile, layout=refreshed))
    return patch, matched
