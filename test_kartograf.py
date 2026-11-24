"""Regresní testy projektu, kreslení, dialogů a tiskových výstupů."""
from dataclasses import replace
from fractions import Fraction
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSettings, Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPainter, QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QLabel, QPushButton

from atlas_layout import arrange, area_routes, bounds, matching_edges, world_point
from catalog import ASSETS, GROUPS
from document import (Area, Mark, Patch, Placement, Project, Target, Tile, TileLayout, cell_name, delete_area,
                      import_reference, load_project, resize_allowed, rotate_area, save_project,
                      set_marker)
from drawing import PDF_DPI, draw_map, export_pdf, export_png, legend_rows
from kartograf import ENTRY_DETAILS, Editor, make_application
from nwn_minimap import image_png
from panels import AreaDialog, ExportDialog, StartupDialog
from test_nwn_import import module_fixture


def connected_project():
    one = Area("Horská římsa", 16, 12)
    two = Area("Dvůr", 14, 10)
    return Project("Rozpracovaná výprava", {a.uid: a for a in (one, two)}, one.uid), one, two


def export_fixture():
    yard = Area("Nádvoří", 24, 17)
    store = Area("Krytý sklad", 24, 17)
    for area, ground in ((yard, "grass"), (store, "flagstone")):
        area.tiles = {(x, y): Tile(ground) for y in range(area.rows) for x in range(area.cols)}
    yard.markers[2, 5, "monster"] = replace(Mark.from_asset("monster"), name="Strážný přízrak")
    yard.markers[3, 5, "treasure"] = replace(Mark.from_asset("chest"), name="Truhla se zásobami")
    yard.markers[23, 6, "link"] = replace(Mark.from_asset("exit"), target=Target(store.uid, 0, 6))
    return Project("Pohraniční hláska", {yard.uid: yard, store.uid: store}, yard.uid)


class ModelTest(unittest.TestCase):
    def test_compact_import_places_a_separate_room_near_its_portal(self):
        halls = Area("Chodby", 7, 6, uid="halls")
        upper = Area("Horní sál", 4, 8, uid="upper")
        service = Area("Servis", 6, 4, uid="service")
        halls.markers[4, 3, "link"] = Mark("exit", target=Target(service.uid, 4, 0),
                                          origin_xy=(4.25, 3.2), arrival_xy=(4.25, .8))
        areas = {a.uid: a for a in (halls, upper, service)}
        fixed = {halls.uid: Placement(4, 8), upper.uid: Placement(0, 0)}
        project = Project("Chodby", areas, halls.uid, fixed)
        result = arrange(project, areas, place_nearby=True)
        self.assertEqual(result, fixed | {service.uid: Placement(4, 3)})
        self.assertEqual(bounds(areas, result), bounds(areas, fixed))
        portal = next(area_routes(project, halls))
        self.assertIsNone(matching_edges(areas, result, portal))

    def test_catalog_and_numeric_cr_order(self):
        sections = {}
        self.assertGreaterEqual(len(ASSETS), 80)
        for asset in ASSETS.values():
            if asset.cr:
                sections.setdefault(asset.section, []).append(Fraction(asset.cr))
        self.assertEqual(sum(map(len, sections.values())), 29)
        self.assertEqual([keys for keys in sections.values() if keys != sorted(keys)], [])
        self.assertTrue(ASSETS["monster"].dm)
        self.assertEqual(ASSETS["grass"].layer, "tiles")
        self.assertEqual(ASSETS["door"].layer, "edges")
        self.assertEqual(cell_name(255, 255), "IV256")

    def test_rotation_restores_all_layers_and_incoming_targets(self):
        project, a, b = connected_project()
        a.tiles[0, 1] = Tile("wood", 2)
        a.tiles[2, 3] = Tile("grass")
        a.edges[16, 8, "v"] = Mark.from_asset("secret")
        a.edges[3, 0, "h"] = Mark.from_asset("wall")
        a.markers[10, 9, "link"] = replace(Mark.from_asset("up"), target=Target(b.uid, 1, 4))
        b.markers[1, 4, "link"] = replace(Mark.from_asset("down", 2), target=Target(a.uid, 10, 9))
        turn = rotate_area(project, a.uid, 1)
        turn.redo()
        rotated = project.areas[a.uid]
        self.assertEqual((rotated.cols, rotated.rows, rotated.north), (12, 16, 1))
        self.assertEqual(rotated.tiles[10, 0], Tile("wood", 3))
        self.assertEqual(rotated.tiles[8, 2], Tile("grass"))
        self.assertIn((3, 16, "h"), rotated.edges)
        self.assertIn((12, 3, "v"), rotated.edges)
        self.assertEqual(rotated.markers[2, 10, "link"].target, Target(b.uid, 1, 4))
        self.assertEqual(b.markers[1, 4, "link"].target, Target(a.uid, 2, 10))
        for _ in range(3):
            rotate_area(project, a.uid, 1).redo()
        self.assertEqual(project.areas[a.uid], a)
        self.assertEqual(b.markers[1, 4, "link"].target, Target(a.uid, 10, 9))
        turn.redo()
        turn.undo()
        self.assertEqual(project.areas[a.uid], a)
        self.assertEqual(b.markers[1, 4, "link"].target, Target(a.uid, 10, 9))

    def test_remove_and_resize_preserve_graph(self):
        project, a, b = connected_project()
        mark = replace(Mark.from_asset("exit"), target=Target(b.uid, 13, 9))
        a.markers[0, 0, "link"] = mark
        self.assertFalse(resize_allowed(project, b, 13, 10))
        self.assertTrue(resize_allowed(project, b, 14, 10))
        remove = delete_area(project, b.uid)
        remove.redo()
        self.assertNotIn(b.uid, project.areas)
        self.assertIsNone(project.areas[a.uid].markers[0, 0, "link"].target)
        remove.undo()
        self.assertEqual(list(project.areas), [a.uid, b.uid])
        self.assertEqual(project.areas[a.uid].markers[0, 0, "link"], mark)

    def test_erasing_pair_is_atomic_with_undo(self):
        project, a, b = connected_project()
        mark_a = replace(Mark.from_asset("exit"), target=Target(b.uid, 0, 1))
        mark_b = replace(Mark.from_asset("exit"), target=Target(a.uid, 15, 1))
        a.markers[15, 1, "link"] = mark_a
        b.markers[0, 1, "link"] = mark_b
        patch = Patch("Guma")
        set_marker(patch, project, a, (15, 1, "link"), None, now=True)
        self.assertNotIn((15, 1, "link"), a.markers)
        self.assertIsNone(b.markers[0, 1, "link"].target)
        patch.undo()
        self.assertEqual(a.markers[15, 1, "link"], mark_a)
        self.assertEqual(b.markers[0, 1, "link"], mark_b)

    def test_project_rejects_broken_files_and_save_is_atomic(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / "projekt.kartograf"
            project = export_fixture()
            save_project(project, path)
            expected = path.read_bytes()
            with patch.object(ZipFile, "writestr", side_effect=OSError("Test přerušeného zápisu")):
                with self.assertRaises(OSError):
                    save_project(project, path)
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(load_project(path), project)
            bad = Path(base) / "broken.kartograf"
            bad.write_bytes(b"wrong-format")
            with self.assertRaises(ValueError):
                load_project(bad)
            with ZipFile(bad, "w") as archive:
                archive.writestr("project.json", '{"format":"kartograf","version":999}')
            with self.assertRaises(ValueError):
                load_project(bad)

    def test_existing_v1_project_is_editable(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / "v1.kartograf"
            document = {"format": "kartograf", "version": 1, "title": "Původní", "active": "r",
                        "areas": [{"uid": "r", "name": "Starší mapa", "cols": 8, "rows": 8, "unit": "nwn", "north": 0,
                                   "tiles": [[2, 3, "grass", 0]], "edges": [], "markers": []}]}
            with ZipFile(path, "w") as archive:
                archive.writestr("project.json", json.dumps(document))
            old = load_project(path)
            self.assertEqual(old.areas["r"].tiles[2, 3], Tile("grass"))
            old.areas["r"].heights[2, 3] = -1
            save_project(old, path)
            self.assertEqual(load_project(path), old)

    def test_reference_embedded_rotated_and_reopened(self):
        with tempfile.TemporaryDirectory() as base:
            base = Path(base)
            source = base / "výřez.png"
            image = QImage(81, 43, QImage.Format_ARGB32)
            image.fill(QColor("#546a7e"))
            self.assertTrue(image.save(str(source)))
            project, area, _ = connected_project()
            area.reference = replace(import_reference(source, area), x=1.5, y=2.5, w=6.0, h=4.0, opacity=0.67)
            rotate_area(project, area.uid, 1).redo()
            ref = project.areas[area.uid].reference
            self.assertEqual((ref.x, ref.y, ref.w, ref.h, ref.turns), (5.5, 1.5, 4.0, 6.0, 1))
            path = base / "předloha.kartograf"
            save_project(project, path)
            source.unlink()
            reopened = load_project(path)
            self.assertEqual(reopened, project)
            self.assertEqual(QImage.fromData(reopened.areas[area.uid].reference.data).pixelColor(10, 10),
                             image.pixelColor(10, 10))


class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, cls.config.name)
        settings_file = Path(QSettings("Kartograf", "Kartograf").fileName())
        if not settings_file.is_relative_to(cls.config.name):
            raise RuntimeError(f"Testovací nastavení nesmí zapisovat do {settings_file}")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = make_application()

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.config.cleanup()

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(dir=self.config.name))
        self.window = Editor(demo=True)
        self.window.history.setClean()
        self.window.resize(1420, 850)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        project, self.a, self.b = connected_project()
        self.window.replace_project(project)
        self.app.processEvents()
        self.canvas = self.window.canvas

    def tearDown(self):
        self.window.history.setClean()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def at(self, x, y):
        return (self.canvas.origin + QPointF(x * self.canvas.step, y * self.canvas.step)).toPoint()

    def click(self, x, y, button=Qt.LeftButton, modifiers=Qt.NoModifier):
        QTest.mouseClick(self.canvas, button, modifiers, self.at(x, y))
        self.app.processEvents()

    def draw(self, x1, y1, x2, y2):
        QTest.mousePress(self.canvas, Qt.LeftButton, pos=self.at(x1, y1))
        QTest.mouseMove(self.canvas, self.at(x2, y2))
        QTest.mouseRelease(self.canvas, Qt.LeftButton, pos=self.at(x2, y2))
        self.app.processEvents()

    def modal(self, action, expected, fill):
        errors = []

        def accept():
            dialog = QApplication.activeModalWidget()
            try:
                self.assertIsInstance(dialog, expected)
                fill(dialog)
                QTest.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok), Qt.LeftButton)
            except Exception as error:
                errors.append(error)
                if dialog is not None:
                    dialog.reject()

        QTimer.singleShot(10, accept)
        action()
        if errors:
            raise errors[0]
        self.app.processEvents()

    def startup_window(self):
        window = Editor()
        window.resize(1240, 810)
        window.show()
        window.activateWindow()
        self.app.processEvents()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        return window

    def reset_startup_settings(self):
        for key in ("project", "recentProjects", "sessionView", "restoreLastSession"):
            self.window.settings.remove(key)
        self.window.settings.sync()

    def test_start_opens_home_and_recent_files_follow_successful_opens_and_saves(self):
        self.reset_startup_settings()
        former = self.base / "Původní mapa.kartograf"
        saved = self.base / "Další mapa.kartograf"
        save_project(Project.single("Původní mapa", Area("Vstup", 8, 6)), former)
        self.window.settings.setValue("project", str(former))
        self.window.settings.sync()

        with patch("kartograf.mountain_project", side_effect=AssertionError("Ukázka se nesmí vytvořit")):
            start = self.startup_window()
        self.assertIsNone(start.project)
        self.assertIsNone(start.canvas.area)
        self.assertIs(start.pages.currentWidget(), start.home)
        self.assertFalse(start.splitter.isVisible())
        self.assertFalse(start.export_button.isVisible())
        self.assertTrue(all(not action.isEnabled() for action in start.project_actions))

        recent = start.recent_list
        item = recent.item(0)
        self.assertEqual(item.data(Qt.UserRole), str(former))
        QTest.mouseClick(recent.viewport(), Qt.LeftButton, pos=recent.visualItemRect(item).center())
        self.assertEqual(start.path, former)
        self.assertIs(start.pages.currentWidget(), start.splitter)
        self.assertTrue(start.write_project(saved))
        start.close_project()
        self.assertIsNone(start.project)
        self.assertIs(start.pages.currentWidget(), start.home)
        self.assertEqual([recent.item(i).data(Qt.UserRole) for i in range(recent.count())], [str(saved), str(former)])

        recent.setCurrentRow(1)
        recent.setFocus()
        QTest.keyClick(recent, Qt.Key_Return)
        self.assertEqual(start.path, former)
        start.close()
        resumed = self.startup_window()
        self.assertIsNone(resumed.project)
        self.assertEqual([resumed.recent_list.item(i).data(Qt.UserRole) for i in range(2)],
                         [str(former), str(saved)])

    def test_startup_setting_restores_the_saved_project_and_active_area(self):
        self.reset_startup_settings()
        project, a, b = connected_project()
        project.atlas = {a.uid: Placement(0, 0), b.uid: Placement(16, 0)}
        path = self.base / "relace.kartograf"
        save_project(project, path)
        self.assertTrue(self.window.open_path(path))
        self.assertTrue(self.window.atlas_mode)
        self.window.open_from_atlas(b.uid)

        def enable_restore(dialog):
            self.assertFalse(dialog.restore_session.isChecked())
            QTest.mouseClick(dialog.restore_session, Qt.LeftButton)
            self.assertTrue(dialog.restore_session.isChecked())

        self.modal(self.window.show_settings, StartupDialog, enable_restore)
        self.window.close()
        start = self.startup_window()
        self.assertEqual(start.path, path)
        self.assertFalse(start.atlas_mode)
        self.assertEqual(start.area.uid, b.uid)
        self.assertTrue(start.history.isClean())
        start.show_atlas()
        start.close()
        restored = self.startup_window()
        self.assertTrue(restored.atlas_mode)
        self.assertEqual(restored.area.uid, b.uid)

    def test_unavailable_last_session_leaves_a_working_home_page(self):
        self.reset_startup_settings()
        self.window.settings.setValue("restoreLastSession", True)
        self.window.settings.setValue("project", str(self.base / "odstraněná-mapa.kartograf"))
        self.window.settings.sync()
        start = self.startup_window()
        self.assertIsNone(start.project)
        self.assertIs(start.pages.currentWidget(), start.home)
        self.assertEqual(start.recent_list.count(), 0)
        self.assertTrue(start.recent_empty.isVisible())
        self.assertIn("nepodařilo", start.status_help.text())

    def test_module_open_and_restore_do_not_reuse_an_older_project(self):
        self.reset_startup_settings()
        former = self.base / "Starší verze.kartograf"
        save_project(Project.single("Zkušební modul", Area("Starý půdorys", 8, 6)), former)
        mod = module_fixture(self.base / "Aktuální.MOD", version=26)
        self.assertTrue(self.window.open_path(former))
        with patch.object(self.window, "choose_file", return_value=mod) as picker, \
                patch.object(self.window, "show_import_report") as report:
            self.window.browse_project()
        self.assertIn("*.kartograf *.mod", picker.call_args.args[1])
        self.assertIn("Modul NWN V26", report.call_args.args[0])
        self.assertIsNone(self.window.path)
        self.assertEqual(self.window.module_path, mod)
        self.assertEqual(self.window.project.nwn_source, (mod.name, 26))
        self.assertEqual(set(self.window.project.areas), {"ridge", "workshop"})
        self.assertIn("Modul NWN V26", self.window.project_label.text())
        source_bytes = mod.read_bytes()
        with patch.object(self.window, "error") as error:
            self.assertFalse(self.window.write_project(mod))
        error.assert_called_once()
        self.assertEqual(mod.read_bytes(), source_bytes)
        self.assertEqual(len(load_project(former).areas), 1)
        self.window.history.setClean()
        self.window.close_project()

        recent = self.window.recent_list
        self.assertEqual(recent.count(), 2)
        self.assertIn("Modul NWN V26", recent.item(0).data(ENTRY_DETAILS))
        self.assertIn("Uložený projekt", recent.item(1).data(ENTRY_DETAILS))
        with patch.object(self.window, "show_import_report"):
            self.window.open_recent(recent.item(0))
        self.assertEqual(self.window.module_path, mod)
        self.window.open_from_atlas("workshop")
        self.window.history.setClean()
        self.window.settings.setValue("restoreLastSession", True)
        self.window.close()

        module_fixture(mod, version=27)
        start = self.startup_window()
        self.assertEqual(start.module_path, mod)
        self.assertEqual(start.project.nwn_source, (mod.name, 27))
        self.assertEqual(start.area.uid, "workshop")
        self.assertFalse(start.atlas_mode)
        self.assertTrue(start.history.isClean())

    def test_palette_tree_search_and_asset_activation(self):
        palette = self.window.palette
        tree = palette.tree
        self.assertEqual([tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())], list(GROUPS.values()))
        palette.search.setText("zlate loz")
        self.assertTrue(palette.empty.isVisible())
        palette.search.setText("medena")
        item = palette.nodes["copper"]
        self.assertFalse(item.isHidden())
        tree.scrollToItem(item)
        self.app.processEvents()
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(item).center())
        self.click(2.5, 2.5)
        self.assertEqual(self.a.markers[2, 2, "ore"].style, "copper")

    def test_ground_drag_undo_redo_and_cancel(self):
        self.draw(1.5, 2.5, 9.5, 2.5)
        self.assertEqual(set(self.a.tiles), {(x, 2) for x in range(1, 10)})
        self.assertEqual(self.window.history.count(), 1)
        QTest.keyClick(self.canvas, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(self.a.tiles, {})
        QTest.keyClick(self.canvas, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(len(self.a.tiles), 9)
        QTest.mousePress(self.canvas, Qt.LeftButton, pos=self.at(1.5, 5.5))
        QTest.keyClick(self.canvas, Qt.Key_Escape)
        QTest.mouseRelease(self.canvas, Qt.LeftButton, pos=self.at(1.5, 5.5))
        self.assertNotIn((1, 5), self.a.tiles)
        self.click(2.5, 2.5, Qt.RightButton)
        self.assertNotIn((2, 2), self.a.tiles)

    def test_wheel_zoom_pan_fit_and_grid(self):
        target = self.at(4.5, 4.5)
        grid_point = self.canvas.grid_pos(QPointF(target))
        before = self.canvas.step
        wheel = QWheelEvent(QPointF(target), QPointF(self.canvas.mapToGlobal(target)), QPoint(), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        self.app.sendEvent(self.canvas, wheel)
        self.assertGreater(self.canvas.step, before)
        self.assertLess((self.canvas.grid_pos(QPointF(target)) - grid_point).manhattanLength(), 1e-5)
        origin = self.canvas.origin
        QTest.mousePress(self.canvas, Qt.MiddleButton, pos=target)
        QTest.mouseMove(self.canvas, target + QPoint(40, 25))
        QTest.mouseRelease(self.canvas, Qt.MiddleButton, pos=target + QPoint(40, 25))
        self.assertNotEqual(self.canvas.origin, origin)
        self.window.grid_button.click()
        self.assertFalse(self.canvas.grid)
        self.canvas.setFocus()
        QTest.keyClick(self.canvas, Qt.Key_F)
        self.assertEqual(self.canvas.step, before)

    def test_fill_honors_snapped_walls_and_rectangle(self):
        self.window.select_asset("wall")
        self.draw(6.01, 0.5, 6.01, 11.5)
        self.assertEqual(set(self.a.edges), {(6, y, "v") for y in range(12)})
        self.window.select_asset("flagstone")
        self.window.select_tool("fill")
        self.click(3.5, 5.5)
        self.assertEqual(len(self.a.tiles), 6 * 12)
        self.assertTrue(all(x < 6 for x, _ in self.a.tiles))
        self.window.select_asset("wood")
        self.window.select_tool("rect")
        self.draw(7.5, 3.5, 10.5, 5.5)
        self.assertEqual(sum(tile.style == "wood" for tile in self.a.tiles.values()), 12)
        self.window.undo()
        self.assertEqual(len(self.a.tiles), 72)

    def test_relief_tools_keep_ground_and_apply_one_step_per_stroke(self):
        for col in range(3, 7):
            self.a.tiles[col, 2] = Tile("rock")
        self.window.select_asset("raise_ground")
        self.draw(3.5, 2.5, 6.5, 2.5)
        self.assertEqual(self.a.heights, {(col, 2): 1 for col in range(3, 7)})
        self.assertEqual(set(self.a.tiles.values()), {Tile("rock")})
        self.window.select_asset("lower_ground")
        self.window.select_tool("rect")
        self.draw(4.5, 5.5, 6.5, 6.5)
        self.assertEqual({p for p, h in self.a.heights.items() if h == -1},
                         {(x, y) for x in range(4, 7) for y in (5, 6)})
        self.window.undo()
        self.assertTrue(all(h > 0 for h in self.a.heights.values()))
        self.window.redo()
        self.assertEqual(self.a.heights[6, 6], -1)
        self.window.select_asset("slope_ground")
        self.window.select_tool("brush")
        self.window.rotate_brush()
        self.click(4.5, 2.5)
        self.assertEqual(self.a.corner_heights(4, 2), (1, 2, 2, 1))
        self.window.select_asset("mountain")
        self.click(4.5, 2.5)
        self.assertEqual(self.a.corner_heights(4, 2), (1, 2, 2, 1))
        self.window.select_asset("level_ground")
        self.click(4.5, 2.5)
        self.assertEqual(self.a.corner_heights(4, 2), (0, 0, 0, 0))
        self.assertEqual(self.a.tiles[4, 2].style, "mountain")

    def test_import_action_loads_native_areas_and_saves_project(self):
        mod = module_fixture(self.base / "gui.mod", indoor=True, scenery=True)
        with patch.object(self.window, "choose_file", return_value=mod), patch.object(self.window, "show_import_report") as report:
            QTest.keyClick(self.canvas, Qt.Key_I, Qt.ControlModifier)
        self.assertEqual(self.window.project.title, "Zkušební modul")
        self.assertEqual(self.window.area.source_ref, "ridge")
        self.assertFalse(self.window.history.isClean())
        self.assertIn("1/2", report.call_args.args[0])
        self.assertIn("Oblasti 2 · na společné mapě 2", report.call_args.args[0])
        self.assertEqual(set(self.window.atlas.positions), {"ridge", "workshop"})
        self.assertEqual(set(self.window.atlas.world), {"ridge"})
        self.assertTrue(self.window.scenery_button.isChecked())
        atlas = self.window.atlas
        decorated = atlas.preview("workshop", 35)
        QTest.mouseClick(self.window.scenery_button, Qt.LeftButton)
        self.assertNotEqual(atlas.preview("workshop", 35), decorated)
        self.window.area_search.setText("workshop")
        self.assertFalse(self.window.area_list.item(1).isHidden())
        self.assertTrue(self.window.write_project(self.base / "gui.kartograf"))
        self.assertEqual(load_project(self.base / "gui.kartograf"), self.window.project)

    def test_configured_overlay_keeps_a_clickable_shared_entry(self):
        rules = self.base / "rules.json"
        empty_color = "#332015"
        rules.write_text(json.dumps({"overlay": {
            "host": "hall", "area": "store", "tileset": "testset",
            "entry_tag": "to_store", "exit_tag": "to_hall", "empty_color": empty_color,
        }}), encoding="utf-8")
        environment = patch.dict(os.environ, {"KARTOGRAF_NWN_RULES": str(rules)})
        environment.start()
        self.addCleanup(environment.stop)

        halls = Area("Sál", 8, 6, uid="hall", source_ref="hall", tileset="testset")
        service = Area("Sklad", 5, 4, uid="store", source_ref="store", tileset="testset")
        halls.markers[3, 3, "link"] = Mark("exit", note="Tag to_store", target=Target(service.uid, 3, 0),
                                          origin_xy=(3.27, 3.14), arrival_xy=(3.27, .85))
        service.markers[3, 1, "link"] = Mark("exit", note="Tag to_hall", target=Target(halls.uid, 3, 3),
                                            origin_xy=(3.27, 1.14), arrival_xy=(3.27, 3.31))
        base_color, room_color = QColor("#7694a7"), QColor("#ac8563")
        walls = QImage(8, 8, QImage.Format_RGB32)
        walls.fill(base_color)
        wing = QImage(8, 8, QImage.Format_RGB32)
        wing.fill(QColor(empty_color))
        painter = QPainter(wing)
        painter.fillRect(0, 0, 4, 8, room_color)
        painter.end()
        layout = TileLayout(("flagstone",) * 4, (0,) * 4, ("",) * 4, 1, image_png(walls))
        halls.tiles[1, 3] = Tile("flagstone", layout=layout)
        service.tiles[1, 1] = Tile("flagstone", layout=replace(layout, index=2, minimap=image_png(wing)))
        areas = {a.uid: a for a in (halls, service)}
        project = Project("Sál se skladem", areas, halls.uid,
                          {halls.uid: Placement(0, 0), service.uid: Placement(0, -5)})
        self.window.replace_project(project)
        atlas = self.window.atlas
        self.assertEqual(atlas.positions[service.uid], Placement(0, 2))

        def click_position(x, y):
            return atlas.view_center() + (QPointF(x, y) - atlas.center) * atlas.step

        self.assertEqual(atlas.area_at(click_position(1.25, 3.25)), service.uid)
        self.assertEqual(atlas.area_at(click_position(1.75, 3.25)), halls.uid)
        atlas.zoom(100)
        self.assertGreater(atlas.step, 64)
        for step in (16, 96):
            with self.subTest(step=step):
                image = QImage(halls.cols * step, halls.rows * step, QImage.Format_RGB32)
                p = QPainter(image)
                try:
                    pins = atlas.paint_board(p, QRectF(image.rect()), QPointF(), step, labels=False)
                finally:
                    p.end()
                self.assertEqual(image.pixelColor(round(1.25 * step), round(3.25 * step)), room_color)
                self.assertEqual(image.pixelColor(round(1.75 * step), round(3.25 * step)), base_color)
                self.assertEqual(len(pins), 1)
                self.assertEqual({r.target.area for r in pins[0].routes}, set(areas))
                self.assertEqual(pins[0].center, QPointF(3.27 * step, 3.14 * step))

        self.window.place_on_atlas(halls.uid, Placement(2, 1, 1))
        self.assertEqual(atlas.positions[service.uid], Placement(2, 1, 1))
        self.assertEqual(project.atlas[service.uid], atlas.positions[service.uid])
        self.window.history.undo()
        self.assertEqual(atlas.positions[service.uid], Placement(0, 2))
        QTest.mouseDClick(atlas, Qt.LeftButton, pos=click_position(1.25, 3.25).toPoint())
        self.assertEqual(self.window.area.uid, service.uid)

    def test_layers_and_selected_door_to(self):
        self.click(4.5, 4.5)
        for key in ("locked", "monster", "chest", "plate", "iron"):
            self.window.select_asset(key)
            self.click(4.48, 4.02 if key == "locked" else 4.5)
        self.assertEqual(len(self.a.tiles), 1)
        self.assertEqual(len(self.a.edges), 1)
        self.assertEqual(len(self.a.markers), 4)
        self.window.select_tool("select")
        self.click(4.5, 4.01)
        form = self.window.mark_form
        form.dc.setValue(32)
        form.name.setText("Dveře do skladu")
        QTest.mouseClick(form.apply_button, Qt.LeftButton)
        self.assertEqual(self.a.edges[4, 4, "h"].dc, 32)
        self.assertTrue(any("TO / DC 32" in detail for _, _, detail in legend_rows(self.window.project, self.a, False)))
        self.window.undo()
        self.assertIsNone(self.a.edges[4, 4, "h"].dc)

    def test_new_project_and_area_use_selected_grid_and_search(self):
        def new_values(dialog):
            dialog.project_name.setText("D&D údolí")
            dialog.name.setText("Strážnice")
            dialog.cols.setValue(18)
            dialog.rows.setValue(11)
            dialog.unit.setCurrentIndex(dialog.unit.findData("dnd"))

        self.modal(lambda: QTest.keyClick(self.canvas, Qt.Key_N, Qt.ControlModifier), AreaDialog, new_values)
        self.assertEqual((self.window.project.title, self.window.area.name), ("D&D údolí", "Strážnice"))
        self.assertEqual((self.window.area.cols, self.window.area.rows, self.window.area.unit), (18, 11, "dnd"))
        self.modal(lambda: self.window.findChild(type(self.window.save_button), "addArea").click(),
                   AreaDialog, lambda d: d.name.setText("Údolí"))
        self.assertEqual(len(self.window.project.areas), 2)
        self.window.area_search.setText("straznice")
        self.assertFalse(self.window.area_list.item(0).isHidden())
        self.assertTrue(self.window.area_list.item(1).isHidden())

    def test_hand_click_inspects_each_marker_and_drag_pans(self):
        landing = Target(self.b.uid, 1, 3)
        self.b.name = "Dlouhý název cílové oblasti za podzemním jezerem"
        self.a.markers[2, 2, "link"] = replace(Mark.from_asset("exit"), target=landing)
        self.a.markers[2, 2, "monster"] = replace(Mark.from_asset("monster"), name="Hlídka",
                                                   note="Střet NWN\nZásoba nestvůr · crawler · CR 3")
        self.window.select_tool("hand")
        before = self.canvas.origin
        self.click(2.75, 2.25)
        self.assertEqual(self.canvas.selection, ("markers", (2, 2, "monster")))
        self.assertIsNone(self.window.mark_form)
        self.assertEqual(self.window.inspector.findChild(QLabel, "legacyEncounterPool").text(), "crawler · CR 3")
        self.assertEqual(self.canvas.origin, before)
        QTest.keyClick(self.canvas, Qt.Key_Delete)
        self.assertIn((2, 2, "monster"), self.a.markers)
        start = self.at(2.25, 2.25)
        QTest.mousePress(self.canvas, Qt.LeftButton, pos=start)
        QTest.mouseRelease(self.canvas, Qt.LeftButton, pos=start + QPoint(45, 20))
        self.assertEqual(self.canvas.selection, ("markers", (2, 2, "monster")))
        self.assertEqual(self.canvas.origin, before + QPointF(45, 20))
        self.click(2.25, 2.25)
        summary = self.window.inspector_body.itemAt(0).widget()
        self.assertEqual(summary.findChild(QLabel, "transitionDestination").text(), self.b.name)
        self.assertLessEqual(summary.width(), self.window.inspector_scroll.viewport().width())
        QTest.mouseClick(summary.findChild(QPushButton, "jumpSummary"), Qt.LeftButton)
        self.assertEqual(self.window.project.active, self.b.uid)
        self.assertTrue(self.window.history.isClean())

    def test_two_way_link_jump_rotate_and_erase(self):
        self.window.select_asset("exit")
        self.window.rotate_brush()
        self.click(15.5, 6.5)
        form = self.window.mark_form
        form.destination.setCurrentIndex(form.destination.findData(self.b.uid))
        form.reciprocal.setChecked(True)
        form.apply_button.click()
        self.assertEqual(self.a.markers[15, 6, "link"].target, Target(self.b.uid, 0, 6))
        self.assertEqual(self.b.markers[0, 6, "link"].turns, 3)
        self.window.mark_form.jump.click()
        self.assertEqual(self.window.area.uid, self.b.uid)
        self.window.rotate(1)
        self.assertEqual(self.a.markers[15, 6, "link"].target, Target(self.b.uid, 3, 0))
        self.window.select_tool("select")
        self.click(3.5, 0.5)
        QTest.keyClick(self.canvas, Qt.Key_Delete)
        self.assertIsNone(self.a.markers[15, 6, "link"].target)
        self.window.undo()
        self.assertEqual(self.a.markers[15, 6, "link"].target, Target(self.b.uid, 3, 0))

    def test_save_reopen_and_reject_external_edit(self):
        self.click(2.5, 3.5)
        path = self.base / "kresba.kartograf"
        with patch.object(self.window, "choose_file", return_value=path):
            QTest.mouseClick(self.window.save_button, Qt.LeftButton)
        self.assertTrue(self.window.history.isClean())
        self.assertEqual(load_project(path), self.window.project)
        self.assertTrue(self.window.open_path(path))
        self.assertEqual(self.window.area.tiles[2, 3], Tile("grass"))
        changed = load_project(path)
        changed.title = "Změněno v jiném okně"
        save_project(changed, path)
        self.click(3.5, 3.5)
        with patch.object(self.window, "error") as warning:
            self.assertFalse(self.window.save())
        warning.assert_called_once()
        self.assertEqual(load_project(path).title, "Změněno v jiném okně")

    def export_view(self, name, scope, preset=None, configure=None):
        output = self.base / name
        dimensions = []

        def options(dialog):
            self.assertEqual(dialog.findChild(QLabel, "exportScope").text(), scope)
            dialog.kind.setCurrentIndex(dialog.kind.findData(output.suffix[1:]))
            self.assertEqual(dialog.size_preset.currentText(), "Výchozí")
            if preset is not None:
                index = dialog.size_preset.findData(preset)
                self.assertGreaterEqual(index, 0)
                dialog.size_preset.setCurrentIndex(index)
            if configure:
                configure(dialog)
            label = dialog.findChild(QLabel, "exportDimensions").text()
            self.assertTrue(label.endswith(" px"))
            dimensions.extend(int(part.replace("\u00a0", "")) for part in label[:-3].split(" × "))

        with patch.object(self.window, "choose_file", return_value=output):
            self.modal(lambda: QTest.mouseClick(self.window.export_button, Qt.LeftButton), ExportDialog, options)
        self.assertTrue(output.is_file())
        if output.suffix == ".png":
            image = QImage(str(output))
            self.assertEqual(dimensions, [image.width(), image.height()])
        elif shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(output)], text=True)
            dimensions_pt = next(line.split()[2:5:2] for line in info.splitlines() if line.startswith("Page size:"))
            self.assertEqual(dimensions, [int(float(points) * PDF_DPI / 72 + .5) for points in dimensions_pt])
        return output

    def test_both_export_dialog_paths(self):
        project = export_fixture()
        self.window.replace_project(project)
        area = list(project.areas.values())[1]
        self.window.change_area(area.uid)
        original = self.export_view("oblast.png", area.name)
        self.canvas.zoom(12)
        self.canvas.origin = QPointF(-500, -275)
        zoomed = self.export_view("oblast-priblizena.png", area.name)
        self.assertEqual(zoomed.read_bytes(), original.read_bytes())
        image = QImage(str(zoomed))
        self.assertGreater(image.width(), 1200)
        self.assertGreater(image.height(), 900)

        out = self.export_view("oblast.pdf", area.name)
        self.assertTrue(out.read_bytes().startswith(b"%PDF-"))
        if shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(out)], text=True)
            self.assertRegex(info, r"(?m)^Pages:\s+1$")
        if shutil.which("pdftotext"):
            text = subprocess.check_output(["pdftotext", "-layout", str(out), "-"], text=True)
            self.assertIn(area.name, [line.strip() for line in text.splitlines()[:7]])

    def test_grid_is_visible_over_textured_tiles_at_common_zooms(self):
        area = Area("Podlaha", 3, 2)
        area.tiles = {(x, y): Tile("flagstone" if y == 0 else "grass")
                      for x in range(area.cols) for y in range(area.rows)}

        def render(step, grid):
            image = QImage(area.cols * step, area.rows * step, QImage.Format_RGB32)
            painter = QPainter(image)
            try:
                draw_map(painter, area, QRectF(image.rect()), grid=grid, annotations=False)
            finally:
                painter.end()
            return image

        for step in (18, 44):
            plain, gridded = render(step, False), render(step, True)
            for y in (step // 2, step * 3 // 2):
                with self.subTest(step=step, row=y // step):
                    contrast = max(
                        plain.pixelColor(x, y).value() - gridded.pixelColor(x, y).value()
                        for x in (step - 1, step)
                    )
                    self.assertGreater(contrast, 34)
                    self.assertEqual(plain.pixelColor(step // 2, y), gridded.pixelColor(step // 2, y))

    def test_old_import_uses_its_game_minimaps_and_saved_positions(self):
        project, yard, room = connected_project()
        yard.source_ref, yard.kind = "yard", "outdoor"
        room.source_ref, room.kind = "room", "indoor"
        room.tileset = "scene"
        project.atlas = {yard.uid: Placement(0, 0), room.uid: Placement(20, 4, 1)}
        color = QColor("#6a8271")
        image = QImage(16, 16, QImage.Format_RGB32)
        image.fill(color)
        sprite = self.base / "tile.png"
        self.assertTrue(image.save(str(sprite)))
        room.tiles[6, 5] = Tile("flagstone", 1, TileLayout(("flagstone",) * 4, (0,) * 4, ("",) * 4,
                                                          3, sprite.read_bytes()))

        project_path = self.base / "původní-import.kartograf"
        save_project(project, project_path)
        with ZipFile(project_path) as archive:
            manifest = json.loads(archive.read("project.json"))
        manifest["schematic"] = "24"
        manifest["areas"][1]["plan_turns"] = 1
        with ZipFile(project_path, "w") as archive:
            archive.writestr("project.json", json.dumps(manifest))
        self.assertTrue(self.window.open_path(project_path))
        self.app.processEvents()
        atlas = self.window.atlas
        self.assertTrue(self.window.atlas_mode)
        self.assertEqual(self.window.area_details.text(), "Všechny oblasti · 2")
        self.assertTrue(self.window.all_areas_button.isChecked())
        self.assertEqual(atlas.positions, project.atlas)
        x, y = world_point(room, atlas.positions[room.uid], (6.5, 5.5))
        point = (atlas.view_center() + (QPointF(x, y) - atlas.center) * atlas.step).toPoint()
        self.assertEqual(atlas.grab().toImage().pixelColor(point), color)
        self.assertEqual(load_project(project_path), project)

    def test_automatic_size_reacts_to_legend_and_fits_it_on_one_page(self):
        area = Area("Věž", 5, 12)
        self.window.replace_project(Project.single("Zkouška velikosti", area))
        for index in range(25):
            area.markers[index % 5, index // 5, "monster"] = replace(Mark.from_asset("monster"),
                                                                      name=f"Stráž {index:02}")

        def configure(dialog):
            label = dialog.findChild(QLabel, "exportDimensions")
            before = label.text()
            self.assertFalse(dialog.orientation.isVisible())
            dialog.legend.setChecked(False)
            self.assertNotEqual(label.text(), before)
            fixed = dialog.size_preset.findData("A3")
            dialog.size_preset.setCurrentIndex(fixed)
            self.assertTrue(dialog.orientation.isVisible())
            paper = label.text()
            dialog.orientation.setCurrentIndex(1 - dialog.orientation.currentIndex())
            self.assertNotEqual(label.text(), paper)
            dialog.size_preset.setCurrentIndex(0)
            dialog.legend.setChecked(True)
            self.assertFalse(dialog.orientation.isVisible())
            self.assertEqual(label.text(), before)

        output = self.export_view("vez.pdf", area.name, configure=configure)
        if shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(output)], text=True)
            self.assertRegex(info, r"(?m)^Pages:\s+1$")
        if shutil.which("pdftotext"):
            report = subprocess.check_output(["pdftotext", "-layout", str(output), "-"], text=True)
            for index in range(25):
                self.assertIn(f"Stráž {index:02}", report)

    def test_world_export_uses_full_unsaved_layout_in_both_formats(self):
        north = Area("Severní povrch", 12, 8, kind="outdoor")
        south = Area("Jižní povrch", 10, 7, kind="outdoor")
        cellar = Area("Hluboký sklep", 5, 5, kind="underground")
        for area, style in ((north, "forest"), (south, "lava"), (cellar, "flagstone")):
            area.tiles = {(x, y): Tile(style) for x in range(area.cols) for y in range(area.rows)}
        project = Project("Zkouška výřezu", {a.uid: a for a in (north, south, cellar)}, cellar.uid,
                          {north.uid: Placement(0, 0), south.uid: Placement(18, 1)})
        self.window.replace_project(project)
        self.assertTrue(self.window.atlas_mode)
        scope = "Mapa světa · 2 venkovních oblastí"
        original = QImage(str(self.export_view("mapa-puvodni.png", scope, 24)))

        self.window.place_on_atlas(south.uid, Placement(-60, 12, 1))
        self.window.atlas.focus_on(north.uid)
        self.window.atlas.zoom(6)
        png = self.export_view("mapa.png", scope, 24)
        pdf = self.export_view("mapa.pdf", scope)
        self.assertFalse(self.window.history.isClean())
        self.assertIsNone(self.window.path)
        image = QImage(str(png))
        self.assertGreater(image.width(), original.width() + 800)
        # Láva z oblasti mimo přiblížený pohled leží na novém levém okraji.
        red_columns = []
        for y in range(110, image.height() - 28, 17):
            for x in range(30, image.width() - 28, 17):
                pixel = image.pixelColor(x, y)
                if pixel.red() > 118 and pixel.red() > pixel.green() + 42:
                    red_columns.append(x)
        self.assertTrue(red_columns)
        self.assertLess(max(red_columns), image.width() // 3)

        self.window.atlas.fit()
        fitted_png = self.export_view("mapa-cela.png", scope, 24)
        fitted_pdf = self.export_view("mapa-cela.pdf", scope)
        self.assertEqual(fitted_png.read_bytes(), png.read_bytes())
        if shutil.which("pdftotext"):
            sheet = subprocess.check_output(["pdftotext", "-layout", str(pdf), "-"], text=True)
            self.assertIn("Mapa světa", sheet)
            self.assertIn(north.name, sheet)
            self.assertIn(south.name, sheet)
            self.assertNotIn(cellar.name, sheet)
        if shutil.which("pdftoppm"):
            options = ["pdftoppm", "-singlefile", "-r", "55"]
            self.assertEqual(subprocess.check_output(options + [str(pdf)]),
                             subprocess.check_output(options + [str(fitted_pdf)]))

    def test_world_all_areas_includes_unlinked_interiors_in_list_map_and_export(self):
        yard = Area("Dvůr", 10, 7, kind="outdoor")
        ridge = Area("Římsa", 8, 6, kind="outdoor")
        study = Area("Skrytá pracovna", 12, 5, kind="underground")
        placements = {yard.uid: Placement(0, 0), ridge.uid: Placement(12, 0)}
        project = Project("Zkouška celé mapy", {area.uid: area for area in (yard, ridge, study)},
                          yard.uid, placements.copy())
        self.window.replace_project(project)
        self.app.processEvents()
        atlas = self.window.atlas
        self.assertEqual(set(atlas.positions), set(placements))
        sidebar = self.window.area_list
        item = sidebar.findItems(study.name, Qt.MatchExactly)[0]
        self.assertFalse(item.isHidden())
        QTest.mouseClick(sidebar.viewport(), Qt.LeftButton, pos=sidebar.visualItemRect(item).center())
        self.assertFalse(self.window.atlas_mode)
        self.assertEqual(self.window.area.uid, study.uid)
        self.window.show_atlas()
        self.app.processEvents()

        atlas.zoom(2)
        QTest.mouseClick(self.window.all_areas_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(set(atlas.positions), set(project.areas))
        self.assertTrue(atlas.fitted)
        self.assertEqual(project.atlas, placements)
        for limits in (atlas.bounds[:2], atlas.bounds[2:]):
            point = atlas.view_center() + (QPointF(*limits) - atlas.center) * atlas.step
            self.assertTrue(atlas.rect().contains(point.toPoint()))

        scope = "Všechny oblasti · 3"
        pdf = self.export_view("vsechny-oblasti.pdf", scope)
        self.export_view("vsechny-oblasti.png", scope)
        if shutil.which("pdftotext"):
            content = subprocess.check_output(["pdftotext", str(pdf), "-"], text=True)
            self.assertIn(study.name, content)
        QTest.mouseClick(self.window.all_areas_button, Qt.LeftButton)
        self.assertEqual(set(atlas.positions), set(placements))
        self.assertFalse(item.isHidden())
        self.assertTrue(self.window.history.isClean())

    def test_world_opens_for_an_import_with_only_interiors(self):
        first = Area("Vchod do dolu", 5, 5, kind="indoor", source_ref="mine_entry")
        second = Area("Hluboká šachta", 4, 7, kind="underground", source_ref="mine_deep")
        project = Project("Důl", {area.uid: area for area in (first, second)}, first.uid)
        self.window.replace_project(project)
        self.assertTrue(self.window.atlas_mode)
        self.assertEqual(set(self.window.atlas.positions), set(project.areas))
        self.assertTrue(self.window.all_areas_button.isChecked())

    def test_world_treasures_mark_surface_and_reachable_interiors(self):
        surface = Area("Náměstí", 16, 12, kind="outdoor")
        entry = Area("Vstupní chodba", 5, 5, kind="indoor")
        vault = Area("Sklepní pokladnice", 5, 5, kind="underground")
        empty = Area("Prázdný dům", 5, 5, kind="indoor")
        surface.markers[9, 6, "treasure#2"] = Mark.from_asset("chest")
        for point, origin, target, hidden in (
            ((1, 2), surface, entry, False),
            ((13, 9), surface, empty, False),
            ((2, 2), entry, vault, True),
            ((2, 2), vault, entry, False),
            ((2, 2), empty, surface, False),
        ):
            origin.markers[(*point, "link")] = replace(Mark.from_asset("exit"),
                                                        target=Target(target.uid, 0, 0), hidden=hidden)
        vault.markers[3, 3, "treasure"] = replace(Mark.from_asset("relic"), hidden=False)
        project = Project("Poklady", {a.uid: a for a in (surface, entry, vault, empty)}, surface.uid,
                          {surface.uid: Placement(0, 0, 1)})
        self.window.replace_project(project)
        self.window.show_atlas()
        atlas = self.window.atlas
        atlas.focus_on(surface.uid)
        self.app.processEvents()
        treasures = self.window.findChild(QPushButton, "atlasTreasures")
        entrances = self.window.findChild(QPushButton, "atlasEntrances")
        self.assertTrue(treasures.isVisible())
        self.assertFalse(treasures.isChecked())

        coordinates = {r.target.area: pin.center.toPoint() for pin in atlas._pins for r in pin.routes}
        world = world_point(surface, atlas.positions[surface.uid], (9.5, 6.5))
        chest = (atlas.view_center() + (QPointF(*world) - atlas.center) * atlas.step).toPoint()

        def crop(image, point):
            return image.copy(QRect(point.x() - 15, point.y() - 15, 31, 31))

        off = atlas.grab().toImage()
        QTest.mouseClick(treasures, Qt.LeftButton)
        self.app.processEvents()
        on = atlas.grab().toImage()
        self.assertNotEqual(crop(off, chest), crop(on, chest))
        self.assertNotEqual(crop(off, coordinates[entry.uid]), crop(on, coordinates[entry.uid]))
        self.assertEqual(crop(off, coordinates[empty.uid]), crop(on, coordinates[empty.uid]))

        QTest.mouseClick(entrances, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual({r.target.area for pin in atlas._pins for r in pin.routes}, {entry.uid})
        QTest.mouseClick(atlas, Qt.LeftButton, pos=coordinates[entry.uid])
        self.assertFalse(self.window.atlas_mode)
        self.assertEqual(self.window.area.uid, entry.uid)
        self.window.show_atlas()
        scope = "Mapa světa · 1 venkovních oblastí"

        def player(dialog):
            dialog.audience.setCurrentIndex(dialog.audience.findData(True))

        dm = self.export_view("dm-poklady.png", scope)
        public = self.export_view("hraci-poklady.png", scope, configure=player)
        QTest.mouseClick(treasures, Qt.LeftButton)
        dm_disabled = self.export_view("dm-vypnuto.png", scope)
        public_disabled = self.export_view("hraci-vypnuto.png", scope, configure=player)
        self.assertNotEqual(dm.read_bytes(), dm_disabled.read_bytes())
        self.assertEqual(public.read_bytes(), public_disabled.read_bytes())
        self.assertTrue(self.window.history.isClean())

    def test_player_export_hides_actual_traits(self):
        project = export_fixture()
        area = project.areas[project.active]
        gm = self.base / "gm.pdf"
        player = self.base / "player.pdf"
        export_pdf(project, [area], gm)
        export_pdf(project, [area], player, page_size="A3", landscape=False, player=True)
        gm_rows = [name for _, name, _ in legend_rows(project, area, False)]
        player_rows = [name for _, name, _ in legend_rows(project, area, True)]
        self.assertIn("Strážný přízrak", gm_rows)
        self.assertNotIn("Strážný přízrak", player_rows)
        self.assertIn("Přechod", player_rows)
        if shutil.which("pdftotext"):
            text = subprocess.check_output(["pdftotext", "-layout", str(player), "-"], text=True)
            self.assertNotIn("Truhla se zásobami", text)
            self.assertIn("Krytý sklad", text)
        if shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(player)], text=True)
            self.assertIn("(A3)", info)
        gm_png = self.base / "gm.png"
        player_png = self.base / "player.png"
        export_png(project, area, gm_png, pixels=24, legend=False)
        export_png(project, area, player_png, pixels=24, legend=False, player=True)
        first, second = QImage(str(gm_png)), QImage(str(player_png))
        self.assertEqual(first.size(), second.size())
        self.assertNotEqual(first, second)

    def test_export_many_areas_and_legend_appendix(self):
        areas = [Area(f"Oblast {i}", 5, 5) for i in range(1, 323)]
        project = Project("Rozsáhlý atlas", {a.uid: a for a in areas}, areas[0].uid)
        tilemap_path = self.base / "velká-mapa.kartograf"
        save_project(project, tilemap_path)
        self.assertEqual(len(load_project(tilemap_path).areas), 322)
        for i in range(25):
            areas[0].markers[i % 5, i // 5, "monster"] = replace(Mark.from_asset("monster"), name=f"Stráž {i}")
        out = self.base / "velká-mapa.pdf"
        export_pdf(project, areas, out)
        self.assertTrue(out.read_bytes().startswith(b"%PDF-"))
        if shutil.which("pdfinfo"):
            info = subprocess.check_output(["pdfinfo", str(out)], text=True)
            self.assertRegex(info, r"(?m)^Pages:\s+323$")


if __name__ == "__main__":
    unittest.main()
