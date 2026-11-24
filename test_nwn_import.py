"""Malý modul s výškami a GIT objekty pro reprodukovatelné zkoušky importu."""

from pathlib import Path
import os
import struct
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QColor, QImage, QPainter

from atlas_layout import area_routes, matching_edges, world_areas
from aurora_reader import AuroraError, ErfReader, read_gff
from document import Area, Target, load_project, rotate_area, save_project
from drawing import CELL, draw_map, draw_relief
from example import mountain_project
from nwn_bestiary import CreatureCard, EncounterCard
from nwn_import import ImportCancelled, import_module, merge_nwn_graphics, merge_nwn_monster_details, merge_nwn_treasures
from nwn_minimap import dds_image


def gff(kind, tree):
    """Minimální zapisovač hodnot použitých v testovacím modulu."""
    labels, structs, fields = [], [], []
    data, indices, lists = bytearray(), bytearray(), bytearray()

    def add_field(name, value):
        if name not in labels:
            labels.append(name)
        if isinstance(value, str):
            encoded = value.encode("cp1250")
            code, raw = 10, len(data)
            data.extend(struct.pack("<I", len(encoded)) + encoded)
        elif isinstance(value, list):
            children = [add_struct(child) for child in value]
            code, raw = 15, len(lists)
            lists.extend(struct.pack("<" + "I" * (len(children) + 1), len(children), *children))
        elif isinstance(value, float):
            code, raw = 8, struct.unpack("<I", struct.pack("<f", value))[0]
        else:
            code, raw = 5, value & 0xffffffff
        fields.append((code, labels.index(name), raw))
        return len(fields) - 1

    def add_struct(branch):
        index = len(structs)
        structs.append(None)
        ids = [add_field(name, value) for name, value in branch.items()]
        if len(ids) == 1:
            location = ids[0]
        else:
            location = len(indices)
            indices.extend(struct.pack("<" + "I" * len(ids), *ids))
        structs[index] = (0xffffffff, location, len(ids))
        return index

    add_struct(tree)
    blocks = [
        (b"".join(struct.pack("<III", *row) for row in structs), len(structs)),
        (b"".join(struct.pack("<III", *row) for row in fields), len(fields)),
        (b"".join(label.encode("ascii").ljust(16, b"\0") for label in labels), len(labels)),
        (data, len(data)), (indices, len(indices)), (lists, len(lists)),
    ]
    header = bytearray(kind.ljust(4, b" ") + b"V3.2")
    offset = 56
    for block, total in blocks:
        header.extend(struct.pack("<II", offset, total))
        offset += len(block)
    return bytes(header) + b"".join(block for block, _ in blocks)


TILESET = b"""[GENERAL]
Interior=0
[TILE0]
ImageMap2D=mi_terrain
TopLeft=Grass
TopRight=Grass
BottomRight=Grass
BottomLeft=Grass
Left=Road
Right=Road
[TILE1]
Model=ledge
TopLeft=Cliff
TopRight=Grass
BottomRight=Grass
BottomLeft=Cliff
TopLeftHeight=1
BottomLeftHeight=1
Top=Road
Bottom=Road
"""


def module_fixture(path, include_tileset=True, scripted=False, shared_passage=False,
                   *, hak="", version=1, indoor=False, scenery=False, stand_bitmap="NULL"):
    def t(index, height=0, orientation=0):
        return dict(Tile_ID=index, Tile_Height=height, Tile_Orientation=orientation)

    def waypoint(tag, x, y):
        return dict(Tag=tag, XPosition=x, YPosition=y)

    ridge_gates = [dict(Tag="plate", LocalizedName="Spoušť", XPosition=5.0, YPosition=15.0,
                        TrapFlag=1, TrapDetectDC=22, DisarmDC=30, LinkedTo="duplicate", LinkedToFlags=2)]
    ridge_waypoints = []
    workshop = {"WaypointList": [
        waypoint("entry", 25.0, 1.0), waypoint("duplicate", 5.0, 5.0), waypoint("duplicate", 15.0, 5.0),
    ]}
    if shared_passage:
        ridge_gates.append(dict(Tag="lab_in", XPosition=18.45, YPosition=5.0, LinkedToFlags=0,
                                ScriptOnEnter="map_travel", VarTable=[dict(Name="target", Value="lab_landing"),
                                                                    dict(Name="require", Value="optics")]))
        ridge_waypoints.append(dict(waypoint("ridge_landing", 15.0, 5.0), XOrientation=-1.0, YOrientation=0.0))
        workshop["WaypointList"].append(dict(waypoint("lab_landing", 5.0, 5.0), XOrientation=1.0, YOrientation=0.0))
        workshop["TriggerList"] = [dict(Tag="lab_out", XPosition=1.55, YPosition=5.0,
                                        LinkedTo="ridge_landing", LinkedToFlags=2)]
    if scripted:
        workshop["Placeable List"] = [
            dict(Tag="reward", LocName="Poklad", X=25.0, Y=5.0, HasInventory=1, Locked=1,
                 OpenLockDC=18, ItemList=[], OnOpen="loot_wrapper"),
            dict(Tag="empty", LocName="Prázdná truhla", X=15.0, Y=5.0, HasInventory=1, OnOpen="empty_chest"),
        ]
    if scenery:
        workshop.setdefault("Placeable List", []).extend((
            dict(Tag="stand", LocName="Pult optika", Appearance=21, X=14.5, Y=5.0,
                 Z=1.5, Bearing=1.57079632679),
            dict(Tag="switch", LocName="Páka", Appearance=22, X=7.0, Y=3.2, Useable=1),
        ))

    ifo = dict(Mod_Name="Zkušební modul", Mod_Version=version, Mod_Entry_Area="ridge",
               Mod_Entry_X=5.0, Mod_Entry_Y=15.0,
               Mod_Area_list=[dict(Area_Name="ridge"), dict(Area_Name="workshop")])
    if hak:
        ifo["Mod_HakList"] = [dict(Mod_Hak=hak)]
    resources = [
        ("module", 2014, gff(b"IFO", ifo)),
        ("ridge", 2012, gff(b"ARE", dict(Name="Římsa", TileSet="kartotst", Width=2, Height=2, Flags=4,
                                         Tile_List=[t(0), t(1, 2, 1), t(0, 3), t(0)]))),
        ("workshop", 2012, gff(b"ARE", dict(Name="Dílna", TileSet="kartotst", Width=3, Height=1,
                                            Flags=1 if shared_passage or indoor else 4,
                                            Tile_List=[t(0), t(0), t(0)]))),
        ("ridge", 2023, gff(b"GIT", {
            "Door List": [dict(Tag="ridge_door", LocName="Dveře na římsu", X=10.0, Y=15.0, Locked=1,
                               OpenLockDC=32, LinkedTo="entry", LinkedToFlags=2)],
            "Creature List": [dict(Tag="guard", FirstName="Strážce", ChallengeRating=11.0,
                                   XPosition=5.0, YPosition=5.0)],
            "TriggerList": ridge_gates,
            "WaypointList": ridge_waypoints,
            "Placeable List": [dict(Tag="chest", LocName="Truhla", X=4.0, Y=4.0,
                                    HasInventory=1, ItemList=[dict(InventoryRes="treasure")])],
        })),
        ("workshop", 2023, gff(b"GIT", workshop)),
    ]
    if scripted:
        resources.extend((
            ("loot_wrapper", 2009, b'void main() { ExecuteScript("fill_reward", OBJECT_SELF); }'),
            ("fill_reward", 2009, b'void main() { GetMagic("Weapon45", OBJECT_SELF, 100, 0); }'),
            ("empty_chest", 2009, b'''void main() {
                // GetMagic("Weapon45", OBJECT_SELF, 100, 0);
                string message = "GetMagic(1, OBJECT_SELF, 100, 0)";
                CreateItemOnObject("gift", GetLastOpenedBy());
            }'''),
        ))
    if include_tileset:
        resources.append(("kartotst", 2013, TILESET))
    if scenery:
        resources.extend((
            ("placeables", 2017, b'2DA V2.0\n\nLabel StrRef ModelName\n21 "Optics table" **** stand_mdl\n'
                                 b'22 Switch **** switch_mdl\n'),
            ("stand_mdl", 2002, f"""newmodel stand_mdl
beginmodelgeom stand_mdl
node dummy stand_mdl
  parent NULL
  position 2 1 0
endnode
node trimesh frame
  parent stand_mdl
  render 1
  bitmap {stand_bitmap}
  verts 3
    0 0 0
    3 0 0
    0 1 0
  tverts 3
    0 0 0
    1 0 0
    0 1 0
  faces 1
    0 1 2 0 0 1 2 0
endnode
endmodelgeom stand_mdl
""".encode("ascii")),
            ("switch_mdl", 2002, b"\0compiled"),
        ))
    return write_erf(path, resources)


def write_erf(path, resources, magic=b"MOD "):
    count = len(resources)
    offset = 160 + count * 32
    keys, index, contents = [], [], []
    for i, (label, kind, data) in enumerate(resources):
        keys.append(struct.pack("<16sIHH", label.encode("ascii"), i, kind, 0))
        index.append(struct.pack("<II", offset, len(data)))
        contents.append(data)
        offset += len(data)
    header = struct.pack("<4s4s9I116x", magic, b"V1.0", 0, 0, count, 160, 160, 160 + count * 24, 0, 0, 0xffffffff)
    path.write_bytes(header + b"".join(keys + index + contents))
    return path


def bestiary_fixture(path):
    base = module_fixture(path, hak="bestiary")
    archive = ErfReader(base, {2012, 2013, 2014, 2023})
    git = read_gff(archive.read("ridge", 2023))
    git["Creature List"][0].update(TemplateResRef="cliff_creature", MaxHitPoints=37)
    git["Encounter List"] = [dict(TemplateResRef="cliff_patrol", LocalizedName="Hlídka útesu",
                                  Tag="patrol", XPosition=5.0, YPosition=15.0, RecCreatures=2)]
    resources = [(name, kind, gff(b"GIT", git) if (name, kind) == ("ridge", 2023) else archive.read(name, kind))
                 for name, kind in archive.entries]
    write_erf(path, resources)
    write_erf(path.with_name("bestiary.hak"), [
        ("cliff_creature", 2027, gff(b"UTC", dict(FirstName="Útesový tvor", Description="Stráží horský brod.",
                  ChallengeRating=3.0, CRAdjust=1, MaxHitPoints=32, HitPoints=25, NaturalAC=4,
                  Str=17, Dex=12, Con=16, Int=8, Wis=11, Cha=7, Race=81, fortbonus=2,
                  ClassList=[dict(Class=5, ClassLevel=3, KnownList1=[dict(Spell=7)])],
                  FeatList=[dict(Feat=2)], SkillList=[dict(Rank=3)]))),
        ("cliff_patrol", 2040, gff(b"UTE", dict(Active=1, MaxCreatures=4, DifficultyIndex=2, CreatureList=[
            dict(ResRef="cliff_creature", CR=4.0, SingleSpawn=1),
            dict(ResRef="missing_creature", CR=0.5, SingleSpawn=0),
        ]))),
        ("racialtypes", 2017, b'2DA V2.0\n\nLabel Name\n81 "Mountain folk" ****\n'),
        ("classes", 2017, b'2DA V2.0\n\nLabel Name\n5 Guardian ****\n'),
        ("spells", 2017, b'2DA V2.0\n\nLabel Name\n7 Mists ****\n'),
        ("feat", 2017, b'2DA V2.0\n\nLabel Feat\n2 Bravery ****\n'),
        ("skills", 2017, b'2DA V2.0\n\nLabel Name\n0 Perception ****\n'),
        ("encdifficulty", 2017, b'2DA V2.0\n\nLabel StrRef\n2 Standard ****\n'),
    ], b"HAK ")
    return path


class NwnImportTest(unittest.TestCase):
    def test_bestiary_uses_gff_instance_and_template_data_and_refreshes_a_saved_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = bestiary_fixture(root / "patrol.mod")
            imported, report = import_module(source, profile=root, tile_details=False)
            self.assertEqual((report.creatures, report.encounters), (1, 1))
            area = imported.areas["ridge"]
            creature = next(mark.nwn_detail for mark in area.markers.values() if isinstance(mark.nwn_detail, CreatureCard))
            encounter = next(mark.nwn_detail for mark in area.markers.values() if isinstance(mark.nwn_detail, EncounterCard))
            self.assertEqual((creature.name, creature.source, creature.cr), ("Strážce", "GIT", "11"))
            self.assertEqual(dict(creature.stats)["Maximální životy"], "37")
            self.assertEqual((encounter.difficulty, encounter.recommended, encounter.maximum), ("Standard", 2, 4))
            entry, missing = encounter.entries
            self.assertEqual((entry.name, entry.cr, entry.single_spawn), ("Útesový tvor", "4", True))
            self.assertEqual((entry.creature.source, entry.creature.cr), ("UTC", "3"))
            self.assertEqual(entry.creature.abilities, (17, 12, 16, 8, 11, 7))
            facts = dict(entry.creature.stats)
            for label, value in (("Maximální životy", "32"), ("Přirozený bonus k OČ", "+4"),
                                 ("Povolání a úrovně", "Guardian 3"), ("Známá kouzla", "Mists"),
                                 ("Rasa", "Mountain folk"), ("Odbornosti", "Bravery"), ("Body dovedností", "Perception 3")):
                self.assertEqual(facts[label], value)
            self.assertEqual((missing.name, missing.cr, missing.creature), ("missing_creature", "0.5", None))

            legacy = deepcopy(imported)
            for key, mark in legacy.areas["ridge"].markers.items():
                if mark.nwn_detail is not None:
                    legacy.areas["ridge"].markers[key] = replace(mark, name="Pojmenováno v mapě", nwn_detail=None)
            rotate_area(legacy, "ridge", 1).redo()
            path = root / "legacy.kartograf"
            save_project(legacy, path)
            project = load_project(path)
            update, matched = merge_nwn_monster_details(project, imported)
            self.assertEqual((matched, len(update.edits)), (2, 2))
            update.redo()
            self.assertEqual(project.atlas, legacy.atlas)
            for mark in project.areas["ridge"].markers.values():
                if mark.nwn_detail:
                    self.assertEqual(mark.name, "Pojmenováno v mapě")
            save_project(project, path)
            self.assertEqual(load_project(path), project)
            update.undo()
            self.assertEqual(project, legacy)

    def test_compiled_tile_uses_dds_and_can_refresh_a_rotated_existing_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = module_fixture(root / "terrain.mod", include_tileset=False, hak="compiled")
            tileset = TILESET.replace(b"[TILE0]\n", b"[TILE0]\nModel=terrain\n")
            core = bytearray(800)
            node, mesh, faces = 112, 224, 736
            struct.pack_into("<2I", core, 72, node, 1)
            struct.pack_into("<I", core, node + 108, 0x21)
            struct.pack_into("<3I", core, mesh + 8, faces, 2, 2)
            struct.pack_into("<3f", core, mesh + 60, 1, 1, 1)
            struct.pack_into("<I", core, mesh + 108, 1)
            core[mesh + 120:mesh + 126] = b"paving"
            struct.pack_into("<iHHi", core, mesh + 444, 0, 4, 1, 48)
            struct.pack_into("<3H", core, faces + 26, 0, 1, 2)
            struct.pack_into("<3H", core, faces + 58, 1, 3, 2)
            vertices = struct.pack("<12f", -5, 5, 0, 5, 5, 0, -5, -5, 0, 5, -5, 0)
            uv = struct.pack("<8f", 0, 1, 1, 1, 0, 0, 1, 0)
            model = struct.pack("<3I", 0, len(core), len(vertices + uv)) + core + vertices + uv
            dds = struct.pack("<4IfHHI", 4, 4, 3, 8, 1.0, 0xf800, 0x001f, 0x50505050)
            header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 2, 24, 32)
            tga = header + bytes((0, 255, 0)) * 4
            write_erf(root / "compiled.hak", [
                ("kartotst", 2013, tileset), ("terrain", 2002, model), ("paving", 2033, dds),
                ("paving", 3, tga), ("mi_terrain", 3, tga),
            ], b"HAK ")
            project, report = import_module(module, profile=root, tile_details=False)
            self.assertEqual(report.detailed_tiles, 0)
            rotate_area(project, "workshop", 1).redo()
            previous = deepcopy(project)
            imported, report = import_module(module, profile=root)
            self.assertEqual(report.detailed_tiles, 6)
            image = QImage.fromData(imported.areas["workshop"].tiles[0, 0].layout.detail)
            self.assertEqual((image.width(), image.height()), (256, 256))
            self.assertEqual(image.pixelColor(32, 128), QColor("red"))
            self.assertEqual(image.pixelColor(216, 128), QColor("blue"))
            update, matched = merge_nwn_graphics(project, imported)
            self.assertEqual((matched, len(update.edits)), (2, 6))
            update.redo()
            self.assertEqual(project.atlas, previous.atlas)
            self.assertEqual(project.areas["workshop"].markers, previous.areas["workshop"].markers)
            rotated = project.areas["workshop"].tiles[0, 0]
            self.assertEqual(rotated.turns, 1)
            self.assertEqual(rotated.layout.detail, imported.areas["workshop"].tiles[0, 0].layout.detail)
            self.assertFalse(merge_nwn_graphics(project, imported)[0].edits)
            saved = root / "compiled.kartograf"
            save_project(project, saved)
            self.assertEqual(load_project(saved), project)
            update.undo()
            self.assertEqual(project, previous)

    def test_bioware_dds_keeps_texture_alpha_with_or_without_acceleration(self):
        alpha = sum(1 << (3 * pixel) for pixel in range(16) if pixel < 8)
        header = struct.pack("<4If", 4, 4, 4, 16, 1)
        pixels = bytes((255, 0)) + alpha.to_bytes(6, "little") + struct.pack("<HHI", 0xf800, 0, 0)
        images = [dds_image(header + pixels)]
        with patch("nwn_minimap.Image", None):
            images.append(dds_image(header + pixels))
            self.assertTrue(dds_image(header + pixels[:-1]).isNull())
        for image in images:
            self.assertEqual(image.pixelColor(0, 0), QColor("red"))
            self.assertEqual(image.pixelColor(0, 3).alpha(), 0)

    def test_custom_tile_uses_game_texture_at_high_zoom_and_survives_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = module_fixture(root / "terrain.mod", include_tileset=False, hak="detail")
            header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 8, 8, 24, 32)
            texture = (bytes((0, 255, 0)) * 4 + bytes((255, 0, 0)) * 4) * 8
            model = b"""newmodel terrain
beginmodelgeom terrain
node dummy floor
  parent NULL
endnode
node trimesh floor
  parent floor
  bitmap paving
  diffuse 1 1
  verts 4
    -5 5 0

    5 5 0
    -5 -5 0
    5 -5 0
  tverts 4
    0 1 0
    1 1 0
    0 0 0
    1 0 0
  faces 2
    0 1 2 0 0 1 2 0
    1 3 2 0 1 3 2 0
endnode
endmodelgeom terrain
"""
            tileset = TILESET.replace(b"[TILE0]\n", b"[TILE0]\nModel=terrain\n")
            write_erf(root / "detail.hak", [("kartotst", 2013, tileset), ("terrain", 2002, model),
                                            ("paving", 3, header + texture),
                                            ("mi_terrain", 3, header + bytes((0, 0, 255)) * 64)], b"HAK ")
            project, report = import_module(module, profile=root)
            self.assertEqual(report.detailed_tiles, 6)
            saved = root / "terrain.kartograf"
            save_project(project, saved)
            self.assertEqual(load_project(saved), project)
            tile = project.areas["workshop"].tiles[0, 0]
            self.assertEqual(QImage.fromData(tile.layout.detail).width(), 256)
            area = Area("Dlaždice", 1, 1, tiles={(0, 0): tile})
            for step, color in ((16, QColor("red")), (160, QColor("lime"))):
                image = QImage(step, step, QImage.Format_RGB32)
                painter = QPainter(image)
                try:
                    draw_map(painter, area, QRectF(image.rect()), grid=False, annotations=False)
                finally:
                    painter.end()
                self.assertEqual(image.pixelColor(step // 4, step // 3), color)
                if step == 160:
                    self.assertEqual(image.pixelColor(step * 3 // 4, step // 3), QColor("blue"))

    def test_import_projects_native_objects_and_rotates_their_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = module_fixture(root / "scenery.mod", scenery=True)
            project, report = import_module(source, profile=root)
            self.assertEqual((report.model_objects, report.usable_objects), (1, 1))
            stand, switch = project.areas["workshop"].scenery
            self.assertEqual((stand.name, stand.point, stand.elevation), ("Pult optika", (1.45, .5), 1.5))
            self.assertAlmostEqual(stand.angle, 270, places=4)
            self.assertEqual(stand.preview.bounds, (.2, -.2, .3, .1))
            photo = QImage.fromData(stand.preview.data)
            self.assertGreater(photo.pixelColor(photo.width() // 5, photo.height() * 3 // 4).alpha(), 0)
            self.assertEqual(photo.pixelColor(photo.width() - 1, 0).alpha(), 0)
            self.assertIsNone(switch.preview)
            self.assertTrue(switch.usable)
            rotation = rotate_area(project, "workshop", 1)
            rotation.redo()
            rotated = project.areas["workshop"].scenery[0]
            self.assertEqual(rotated.point, (.5, 1.45))
            self.assertAlmostEqual((rotated.angle + 180) % 360 - 180, 0, places=4)
            save_project(project, root / "scenery.kartograf")
            self.assertEqual(load_project(root / "scenery.kartograf"), project)
            rotation.undo()
            self.assertEqual(project.areas["workshop"].scenery, (stand, switch))

    def test_graphics_refresh_repairs_saved_object_textures_after_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = module_fixture(root / "scenery.mod", scenery=True)
            project, _ = import_module(module, profile=root)
            rotate_area(project, "workshop", 1).redo()
            saved = root / "legacy.kartograf"
            save_project(project, saved)
            project = load_project(saved)
            before = deepcopy(project)
            area = project.areas["workshop"]
            stand, switch = area.scenery
            image = QImage.fromData(stand.preview.data)
            point = image.width() // 5, image.height() * 3 // 4
            self.assertEqual(image.pixelColor(*point), QColor("#b9b1a4"))

            module_fixture(module, scenery=True, stand_bitmap="stand_tex")
            missing, _ = import_module(module, profile=root)
            self.assertEqual(len(missing.areas["workshop"].scenery), 1)
            self.assertFalse(merge_nwn_graphics(project, missing)[0].edits)

            (root / "stand_tex.dds").write_bytes(struct.pack("<4IfHHI", 4, 4, 3, 8, 1, 0x07e0, 0, 0))
            imported, _ = import_module(module, profile=root)
            preview = imported.areas["workshop"].scenery[0].preview
            self.assertEqual(QImage.fromData(preview.data).pixelColor(*point), QColor("lime"))
            update, matched = merge_nwn_graphics(project, imported)
            self.assertEqual((matched, len(update.edits)), (2, 1))
            update.redo()
            self.assertEqual(project.areas["workshop"], replace(area, scenery=(replace(stand, preview=preview), switch)))
            self.assertEqual(project.atlas, before.atlas)
            self.assertFalse(merge_nwn_graphics(project, imported)[0].edits)
            save_project(project, root / "refreshed.kartograf")
            self.assertEqual(load_project(root / "refreshed.kartograf"), project)
            update.undo()
            self.assertEqual(project, before)

    def test_hak_minimaps_render_with_native_rotation_and_survive_saving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = module_fixture(root / "test.mod", hak="scenery")
            (root / "haky").mkdir()
            base = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 2, 24, 32)
            art = bytes((63, 121, 13)) * 4
            rle = struct.pack("<BBBHHBHHHHBB", 0, 0, 10, 0, 0, 0, 0, 0, 2, 2, 24, 0)
            # Modrá spodní řada, nahoře červená a zelená.
            pixels = bytes((129, 255, 0, 0, 1, 0, 0, 255, 0, 255, 0))
            write_erf(root / "haky" / "scenery.hak",
                      [("mi_terrain", 3, base + art), ("mi_ledge", 3, rle + pixels)], b"HAK ")
            project, report = import_module(module, profile=root)
            self.assertEqual((report.minimap_tiles, report.tiles, report.missing_haks), (7, 7, []))
            save_project(project, root / "import.kartograf")
            reopened = load_project(root / "import.kartograf")
            self.assertEqual(reopened, project)
            tile = reopened.areas["ridge"].tiles[1, 1]
            area = Area("Otočená dlaždice", 1, 1)
            area.tiles[0, 0] = replace(tile, layout=replace(tile.layout, offsets=(0, 0, 0, 0)))
            image = QImage(CELL, CELL, QImage.Format_RGB32)
            painter = QPainter(image)
            try:
                draw_map(painter, area, QRectF(image.rect()), grid=False, annotations=False)
            finally:
                painter.end()
            top_left = image.pixelColor(CELL // 4, CELL // 4)
            bottom_left = image.pixelColor(CELL // 4, 3 * CELL // 4)
            self.assertGreater(top_left.green(), 230)
            self.assertLess(max(top_left.red(), top_left.blue()), 20)
            self.assertGreater(bottom_left.red(), 230)
            self.assertLess(max(bottom_left.green(), bottom_left.blue()), 20)

    def test_import_uses_the_same_game_geometry_after_a_version_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = module_fixture(root / "old.mod")
            repacked = module_fixture(root / "repacked.mod", version=15)
            self.assertNotEqual(source.read_bytes(), repacked.read_bytes())
            baseline, report = import_module(source, profile=root)
            project, second = import_module(repacked, profile=root)
            self.assertEqual(baseline.nwn_source, (source.name, 1))
            self.assertEqual(project.nwn_source, (repacked.name, 15))
            self.assertEqual(replace(project, nwn_source=baseline.nwn_source), baseline)
            self.assertEqual(second, report)

    def test_scripted_shared_edge_aligns_interior_and_survives_saving(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = module_fixture(root / "spolecny-prah.mod", shared_passage=True)
            rules = root / "rules.json"
            rules.write_text('{"transition_scripts":["map_travel"]}', encoding="utf-8")
            with patch.dict(os.environ, {"KARTOGRAF_NWN_RULES": str(rules)}):
                project, report = import_module(module, profile=root)
            self.assertEqual(report.scripted_links, 1)
            self.assertEqual(set(world_areas(project)), {"ridge", "workshop"})
            position = project.atlas["workshop"]
            self.assertEqual((position.x, position.y, position.turns), (2, 1, 0))
            path = root / "spolecny-prah.kartograf"
            save_project(project, path)
            project = load_project(path)
            mark = next(mark for mark in project.areas["ridge"].markers.values() if "Tag lab_in" in mark.note)
            self.assertEqual(mark.target, Target("workshop", 0, 0))
            self.assertEqual((mark.origin_xy, mark.arrival_xy), ((2, 1.5), (0, .5)))
            self.assertIn("Podmínka · optics", mark.note)
            self.assertFalse(mark.hidden)
            route = next(r for r in area_routes(project, project.areas["ridge"]) if r.point == mark.origin_xy)
            self.assertEqual(matching_edges(project.areas, project.atlas, route), ((2, 1.5), (2, 1.5)))

    def test_scripted_reward_and_refresh_of_rotated_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "odmena.mod"
            module_fixture(path, scripted=True)
            imported, report = import_module(path, profile=root)
            workshop = imported.areas["workshop"]
            mark = workshop.markers[2, 0, "treasure"]
            self.assertEqual((mark.name, mark.dc, mark.hidden), ("Poklad", 18, True))
            self.assertIn("Skript kořisti · loot_wrapper", mark.note)
            self.assertNotIn((1, 0, "treasure"), workshop.markers)
            self.assertEqual((report.containers, report.scripted_containers), (1, 1))

            old = deepcopy(imported)
            del old.areas["workshop"].markers[2, 0, "treasure"]
            rotate_area(old, "workshop", 1).redo()
            before = deepcopy(old)
            patch, matched = merge_nwn_treasures(old, imported)
            self.assertEqual((matched, len(patch.edits)), (2, 1))
            patch.redo()
            self.assertEqual(old.areas["workshop"].markers[0, 2, "treasure"], mark)
            self.assertEqual(old.atlas, before.atlas)
            self.assertFalse(merge_nwn_treasures(old, imported)[0].edits)
            patch.undo()
            self.assertEqual(old, before)

    def test_native_positions_heights_tiles_and_instance_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = module_fixture(base / "test.mod")
            unchanged = source.read_bytes()
            project, report = import_module(source, profile=base)
            self.assertEqual(source.read_bytes(), unchanged)
            self.assertEqual((project.title, project.active), ("Zkušební modul", "ridge"))
            self.assertEqual([r.name for r in project.areas.values()], ["Římsa", "Dílna"])
            ridge = project.areas["ridge"]
            self.assertEqual((ridge.cols, ridge.rows, ridge.tileset), (2, 2, "kartotst"))
            self.assertEqual(ridge.heights, {(1, 1): 2, (0, 0): 3})
            self.assertEqual(ridge.tiles[1, 1].turns, 3)
            self.assertEqual(ridge.corner_heights(1, 1), (2, 2, 3, 3))
            self.assertEqual(ridge.tiles[1, 1].layout.corners[0], "mountain")
            self.assertEqual(ridge.tiles[1, 1].oriented(ridge.tiles[1, 1].layout.crossings), ("", "path", "", "path"))
            self.assertEqual(ridge.edges[1, 0, "v"].dc, 32)
            self.assertEqual(ridge.markers[1, 0, "link"].target, Target("workshop", 2, 0))
            self.assertIsNone(ridge.markers[0, 0, "link"].target)
            self.assertIn("více možných cílů", ridge.markers[0, 0, "link"].note)
            self.assertEqual(ridge.markers[0, 1, "monster"].cr, "11")
            self.assertEqual(ridge.markers[0, 0, "trap"].dc, 30)
            self.assertIn("treasure", ridge.markers[0, 1, "treasure"].note)
            self.assertEqual((report.tiles, report.links, report.resolved_links), (7, 2, 1))
            self.assertFalse(report.missing_tilesets)
            saved = base / "map.kartograf"
            save_project(project, saved)
            self.assertEqual(load_project(saved), project)

    def test_missing_tileset_preserves_dimensions_and_elevation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = module_fixture(base / "test.mod", include_tileset=False)
            project, report = import_module(source, profile=base)
            area = project.areas["ridge"]
            self.assertEqual(report.missing_tilesets, {"kartotst"})
            self.assertEqual(area.heights[1, 1], 2)
            self.assertEqual(area.tiles[1, 1].layout.index, 1)
            self.assertEqual((area.cols, area.rows), (2, 2))

    def test_cancel_before_import_and_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = module_fixture(base / "test.mod")
            with self.assertRaises(ImportCancelled):
                import_module(source, profile=base, progress=lambda *args: False)
            source.write_bytes(b"unsupported")
            with self.assertRaises(AuroraError):
                import_module(source, profile=base)


class ReliefTest(unittest.TestCase):
    def test_equal_height_seam_clean_and_shadow_on_lower_side(self):
        area = Area("Výšky", 6, 3)
        area.heights = {(1, 1): 2, (2, 1): 2, (4, 1): -1, (5, 1): -1}
        image = QImage(area.cols * CELL, area.rows * CELL, QImage.Format_RGB32)
        base = QColor("#a29485")
        image.fill(base)
        painter = QPainter(image)
        try:
            draw_relief(painter, area, QRectF(image.rect()))
        finally:
            painter.end()
        y = CELL + CELL // 2
        self.assertEqual(image.pixelColor(2 * CELL, y), image.pixelColor(2 * CELL + 15, y))
        self.assertEqual(image.pixelColor(5 * CELL, y), image.pixelColor(5 * CELL + 15, y))
        self.assertLess(image.pixelColor(CELL - 2, y).value(), base.value())
        self.assertGreater(image.pixelColor(CELL + 1, y).value(), base.value())
        self.assertLess(image.pixelColor(4 * CELL + 2, y).value(), image.pixelColor(4 * CELL + 18, y).value())
        self.assertGreater(image.pixelColor(4 * CELL - 2, y).value(), base.value())

    def test_pass_connects_all_26_columns_and_crosses_ramps(self):
        sample = mountain_project()
        area = sample.areas[sample.active]
        paths = {pos for pos, tile in area.tiles.items() if tile.style in {"path", "paving"}}
        found = {min(paths)}
        while True:
            new = found | {(x + dx, y + dy) for x, y in found for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                           if (x + dx, y + dy) in paths}
            if new == found:
                break
            found = new
        self.assertEqual(found, paths)
        self.assertEqual({x for x, _ in found}, set(range(26)))
        for x, y in paths:
            if (x + 1, y) in paths:
                a, b = area.corner_heights(x, y), area.corner_heights(x + 1, y)
                self.assertEqual((a[1], a[2]), (b[0], b[3]), (x, y))
        self.assertEqual((min(area.heights.values()), max(area.heights.values())), (-2, 4))
