#!/usr/bin/python3 -B
"""Kartograf, místní editor 2D map pro D&D a NWN:EE."""
from dataclasses import replace
from functools import partial
from pathlib import Path
import sys

from PyQt5.QtCore import QLockFile, QPointF, QSettings, QSignalBlocker, QSize, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QKeySequence, QPainter, QPalette, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QButtonGroup, QDialog, QFileDialog, QFrame,
                             QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressDialog, QScrollArea,
                             QSizePolicy, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QUndoStack,
                             QVBoxLayout, QWidget)

from atlas_layout import add_interior_layers
from atlas_view import AtlasView
from canvas import Canvas, TOOLS
from catalog import ASSETS
from document import (AREA_KINDS, Area, MAX_AREAS, MAX_LEVEL, Mark, Patch, Project, ProjectEdit, Target,
                      UNITS, cell_name, delete_area, import_reference, load_project,
                      resize_allowed, rotate_area, save_project, set_marker)
from drawing import (CELL, SANS, compass, export_area_pdf, export_atlas_pdf, export_atlas_png,
                     export_png, export_size, font, icon)
from example import mountain_project
from nwn_import import ImportCancelled, game_folder, import_module, merge_nwn_graphics, merge_nwn_treasures, profile_dir
from panels import (AreaDialog, DIRECTIONS, ExportDialog, MarkForm, MarkSummary, Palette, ReferenceDialog,
                    StartupDialog, heading, labeled_button, normalized, space_spin, subtle)

BASEDIR = Path(__file__).resolve().parent
APP_ICON = BASEDIR / "kartograf.png"
PROJECT_FILTER = "Projekty Kartografu (*.kartograf)"
MODULE_FILTER = "Moduly Neverwinter Nights (*.mod)"
OPEN_FILTER = "Projekty a moduly (*.kartograf *.mod)"
AREA_ID = Qt.UserRole + 1
ENTRY_DETAILS = Qt.UserRole + 2
RECENT_LIMIT = 12


class ElidedLabel(QLabel):
    def __init__(self, value=""):
        super().__init__(value)
        self.setMinimumWidth(12)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.WindowText))
        p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.width()))
        p.end()


class EntryListDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, 53)

    def paint(self, p, option, index):
        rect = QRectF(option.rect).adjusted(0, 2, -1, -2)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        selected = option.state & QStyle.State_Selected
        if selected or option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#374738" if selected else "#2f3b31"))
            p.drawRoundedRect(rect, 6, 6)
        if selected:
            p.setBrush(QColor("#d8ad72"))
            p.drawRoundedRect(QRectF(rect.left() + 1, rect.top() + 7, 3, rect.height() - 14), 1.5, 1.5)
        x, y = rect.x() + 30, rect.y()
        icon("compass", 18, "#d7b679" if selected else "#94a597").paint(
            p, int(rect.x()) + 7, int(y) + 13, 16, 19)
        p.setFont(font(SANS, 14, True))
        p.setPen(QColor("#faeed4" if selected else "#e0e6da"))
        title = p.fontMetrics().elidedText(index.data(Qt.DisplayRole), Qt.ElideRight, int(rect.width()) - 37)
        p.drawText(QRectF(x, y + 4, rect.width() - 35, 21), Qt.AlignVCenter, title)
        p.setFont(font(SANS, 12))
        p.setPen(QColor("#b3c2b3"))
        details = p.fontMetrics().elidedText(index.data(ENTRY_DETAILS), Qt.ElideRight, int(rect.width()) - 37)
        p.drawText(QRectF(x, y + 25, rect.width() - 35, 19), Qt.AlignVCenter, details)
        p.restore()


def frame(name, layout_class, margins=(0, 0, 0, 0), spacing=0):
    widget = QFrame()
    widget.setObjectName(name)
    layout = layout_class(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return widget, layout


def divider(vertical=False):
    widget = QFrame()
    widget.setProperty("role", "separator")
    if vertical:
        widget.setFixedWidth(1)
    else:
        widget.setFixedHeight(1)
    return widget


def icon_button(image, label, name, callback=None):
    button = labeled_button("", name, image)
    button.setProperty("role", "icon")
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.setFixedSize(33, 32)
    if callback:
        button.clicked.connect(callback)
    return button


def file_stamp(path):
    info = path.stat()
    return info.st_ino, info.st_mtime_ns, info.st_size


def module_label(version):
    return f"Modul NWN V{version}" if version is not None else "Modul NWN"


class Editor(QMainWindow):
    def __init__(self, initial=None, demo=False):
        super().__init__()
        self.settings = QSettings("Kartograf", "Kartograf")
        self.project = None
        self.path = self.stamp = self.module_path = None
        self._area_signature = None
        self.project_actions = []
        self.mark_form = None
        self._hint_timer = QTimer(self, singleShot=True)
        self._hint_timer.timeout.connect(self.default_hint)
        self.history = QUndoStack(self)
        self.history.setUndoLimit(100)
        self.canvas = Canvas(self.history)
        self.atlas = AtlasView()
        self.atlas_mode = False
        self.setMinimumSize(955, 610)
        self.setObjectName("kartografWindow")
        self.setWindowIcon(QIcon(str(APP_ICON)) if APP_ICON.exists() else icon("compass", 36))
        self._build()
        self.history.indexChanged.connect(self.refresh)
        self.history.cleanChanged.connect(self.update_chrome)
        self.canvas.selection_changed.connect(self.refresh_inspector)
        self.canvas.hover_changed.connect(lambda label: self.coordinates.setText(label or "—"))
        self.canvas.zoom_changed.connect(lambda level: self.update_zoom(level, False))
        self.canvas.followed.connect(self.jump)
        self.atlas.zoom_changed.connect(lambda level: self.update_zoom(level, True))
        self.atlas.picked_area.connect(self.pick_on_atlas)
        self.atlas.opened_area.connect(self.open_from_atlas)
        self.atlas.followed.connect(self.jump)
        self.atlas.placed.connect(self.place_on_atlas)
        self.palette.asset_selected.connect(self.select_asset)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1500, screen.width() - 32), min(910, screen.height() - 48))
        geometry = self.settings.value("window")
        if geometry:
            self.restoreGeometry(geometry)
        self.show_home()
        if initial:
            self.open_path(Path(initial))
        elif demo:
            self.replace_project(mountain_project())
        elif self.settings.value("restoreLastSession", False, type=bool):
            self.restore_session()

    @property
    def area(self):
        return self.project.areas[self.project.active]

    @property
    def session_path(self):
        return self.path or self.module_path

    @property
    def map_view(self):
        return self.atlas if self.atlas_mode else self.canvas

    def action(self, menu, title, callback, shortcut=None, *, project_only=False):
        action = QAction(title, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        if menu is not None:
            menu.addAction(action)
        self.addAction(action)
        if project_only:
            self.project_actions.append(action)
        return action

    def _build(self):
        container = QWidget()
        application = QVBoxLayout(container)
        application.setContentsMargins(0, 0, 0, 0)
        application.setSpacing(0)
        self.setCentralWidget(container)

        header, row = frame("desktopHeader", QHBoxLayout, (15, 10, 15, 10), 11)
        header.setFixedHeight(62)
        logo, badge = frame("brandIcon", QHBoxLayout, (0, 0, 0, 0))
        logo.setFixedSize(37, 37)
        brand_mark = QLabel()
        brand_mark.setAlignment(Qt.AlignCenter)
        brand_mark.setPixmap(icon("compass", 27, "#e8dbb9").pixmap(27, 27))
        badge.addWidget(brand_mark)
        brand = QLabel("Kartograf")
        brand.setObjectName("brandName")
        row.addWidget(logo)
        row.addWidget(brand)
        row.addSpacing(5)
        file_button = labeled_button("Soubor", "fileMenu")
        file_button.setMinimumWidth(88)
        self.file_menu = QMenu(file_button)
        file_button.setMenu(self.file_menu)
        row.addWidget(file_button)
        row.addSpacing(4)
        self.project_separator = divider(True)
        row.addWidget(self.project_separator)
        self.project_label = ElidedLabel()
        self.project_label.setObjectName("projectName")
        row.addWidget(self.project_label, 1)
        self.save_state = QLabel()
        self.save_state.setObjectName("saveState")
        row.addWidget(self.save_state)
        self.save_button = labeled_button("Uložit", "saveProject", "save")
        self.save_button.clicked.connect(self.save)
        self.save_button.setToolTip("Uložit projekt (Ctrl+S)")
        self.export_button = labeled_button("Exportovat", "exportMap", "export", primary=True)
        self.export_button.clicked.connect(self.export)
        self.export_button.setToolTip("Export mapy (Ctrl+E)")
        self.startup_settings_button = labeled_button("Nastavení", "homeSettings")
        self.startup_settings_button.clicked.connect(self.show_settings)
        row.addWidget(self.startup_settings_button)
        row.addWidget(self.save_button)
        row.addWidget(self.export_button)
        application.addWidget(header)
        self._file_actions()

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.palette = Palette()
        self.palette.setMinimumWidth(198)
        self.palette.setMaximumWidth(360)
        self.splitter.addWidget(self.palette)
        self.splitter.addWidget(self._workspace())
        self.splitter.addWidget(self._right_panel())
        self.splitter.setSizes([244, 945, 256])
        self.splitter.splitterMoved.connect(self.adapt_toolbar)
        for index, weight in enumerate((0, 1, 0)):
            self.splitter.setStretchFactor(index, weight)
        self.pages = QStackedWidget()
        self.home = self._home()
        self.pages.addWidget(self.home)
        self.pages.addWidget(self.splitter)
        application.addWidget(self.pages, 1)

        status, strip = frame("status", QHBoxLayout, (14, 3, 14, 3), 10)
        status.setFixedHeight(31)
        self.status_help = ElidedLabel()
        self.status_help.setObjectName("statusHelp")
        strip.addWidget(self.status_help, 1)
        self.coordinates = QLabel("—")
        self.coordinates.setMinimumWidth(74)
        self.coordinates.setToolTip("Políčko pod kurzorem")
        self.coordinates.setObjectName("coordinates")
        strip.addWidget(self.coordinates)
        self.status_separator = divider(True)
        strip.addWidget(self.status_separator)
        self.status_dimensions = QLabel()
        self.status_dimensions.setObjectName("coordinates")
        strip.addWidget(self.status_dimensions)
        application.addWidget(status)

    def _home(self):
        home, outer = frame("home", QHBoxLayout, (28, 38, 28, 26))
        content, column = frame("homeContent", QVBoxLayout, spacing=16)
        content.setMaximumWidth(820)
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        title = QLabel("Projekty")
        title.setObjectName("homeTitle")
        column.addWidget(title)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        new = labeled_button("Nový projekt…", "homeNew", "plus", primary=True)
        new.clicked.connect(self.new_project)
        self.home_open = labeled_button("Otevřít soubor…", "homeOpen")
        self.home_open.clicked.connect(self.browse_project)
        module = labeled_button("Importovat modul NWN…", "homeImport")
        module.clicked.connect(self.choose_module)
        for button in (new, self.home_open, module):
            buttons.addWidget(button)
        buttons.addStretch(1)
        column.addLayout(buttons)
        column.addSpacing(8)
        column.addWidget(heading("NAPOSLEDY OTEVŘENÉ"))

        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentProjects")
        self.recent_list.setAccessibleName("Naposledy otevřené projekty a moduly")
        self.recent_list.setItemDelegate(EntryListDelegate(self.recent_list))
        self.recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent_list.setMouseTracking(True)
        self.recent_list.itemClicked.connect(self.open_recent)
        self.recent_list.itemActivated.connect(self.open_recent)
        column.addWidget(self.recent_list)
        self.recent_empty = subtle("Zatím tu nejsou žádné otevřené projekty ani moduly.")
        column.addWidget(self.recent_empty)
        column.addStretch(1)
        outer.addStretch(1)
        outer.addWidget(content, 3)
        outer.addStretch(1)
        return home

    def _file_actions(self):
        menu = self.file_menu
        self.action(menu, "Nový projekt…", self.new_project, "Ctrl+N")
        self.action(menu, "Otevřít soubor…", self.browse_project, "Ctrl+O")
        self.action(menu, "Importovat modul NWN…", self.choose_module, "Ctrl+I")
        self.action(menu, "Aktualizovat dlaždice a vybavení z modulu NWN…", self.choose_graphics_module, project_only=True)
        self.action(menu, "Doplnit poklady z modulu NWN…", self.choose_treasure_module, project_only=True)
        self.action(menu, "Složka dat NWN…", self.choose_nwn_game)
        menu.addSeparator()
        self.action(menu, "Uložit", self.save, "Ctrl+S", project_only=True)
        self.action(menu, "Uložit jako…", lambda: self.save_as(), "Ctrl+Shift+S", project_only=True)
        self.action(menu, "Přejmenovat projekt…", self.rename_project, project_only=True)
        self.action(menu, "Zavřít projekt", self.close_project, "Ctrl+W", project_only=True)
        menu.addSeparator()
        self.action(menu, "Exportovat PDF / PNG…", self.export, "Ctrl+E", project_only=True)
        menu.addSeparator()
        self.action(menu, "Ukázka horského průsmyku", self.open_mountains)
        menu.addSeparator()
        self.action(menu, "Nastavení…", self.show_settings)
        self.action(menu, "Klávesové zkratky", self.shortcuts, "F1")
        self.action(menu, "Ukončit", self.close, "Ctrl+Q")

    def update_project_controls(self):
        active = self.project is not None
        self.startup_settings_button.setVisible(not active)
        for widget in (self.project_separator, self.save_state, self.save_button,
                       self.export_button, self.coordinates, self.status_dimensions, self.status_separator):
            widget.setVisible(active)
        for action in self.project_actions:
            action.setEnabled(active)

    def show_home(self):
        self.pages.setCurrentWidget(self.home)
        self.update_project_controls()
        self.refresh_recents()
        self.update_chrome()
        self.default_hint()
        (self.recent_list if self.recent_list.count() else self.home_open).setFocus()

    def recent_projects(self):
        projects = {}
        stored = self.settings.value("recentProjects", [])
        if isinstance(stored, list):
            for entry in stored:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"]:
                    path, title, version = entry["path"], entry.get("title"), entry.get("version")
                    projects.setdefault(path, (title if isinstance(title, str) and title else Path(path).stem,
                                               version if type(version) is int else None))
        # Starší vydání ukládala jen cestu k jedinému projektu.
        previous = self.settings.value("project", "", type=str)
        if previous:
            projects.setdefault(previous, (Path(previous).stem, None))
        return projects

    def refresh_recents(self):
        self.recent_list.clear()
        for filename, (title, version) in self.recent_projects().items():
            path = Path(filename)
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, filename)
            kind = module_label(version) if path.suffix.casefold() == ".mod" else "Uložený projekt"
            item.setData(ENTRY_DETAILS, f"{kind} · {path.name}")
            item.setToolTip(filename)
            self.recent_list.addItem(item)
        count = self.recent_list.count()
        self.recent_list.setVisible(bool(count))
        self.recent_list.setMaximumHeight(min(count, 6) * 53 + 3)
        self.recent_empty.setVisible(not count)

    def open_recent(self, item):
        if self.project is None and not self.open_path(Path(item.data(Qt.UserRole))):
            self.refresh_recents()

    def remember_project(self):
        self.settings.sync()
        filename = str(self.session_path)
        projects = self.recent_projects()
        projects.pop(filename, None)
        source = self.project.nwn_source
        version = source[1] if source and self.path is None else None
        recent = [(filename, (self.project.title, version)), *projects.items()]
        self.settings.setValue("recentProjects", [
            {"path": path, "title": title, "version": version}
            for path, (title, version) in recent[:RECENT_LIMIT]
        ])
        self.remember_session()

    def remember_session(self):
        if self.session_path is None:
            return
        filename = str(self.session_path)
        self.settings.setValue("project", filename)
        self.settings.setValue("sessionView", {
            "path": filename, "area": self.project.active,
            "view": "atlas" if self.atlas_mode else "detail",
            "allAreas": self.atlas.show_all_areas, "selection": self.atlas.selected,
        })

    def restore_session(self):
        filename = self.settings.value("project", "", type=str)
        view = self.settings.value("sessionView")
        if not filename or not self.open_path(Path(filename), restoring=True):
            return
        if not isinstance(view, dict) or view.get("path") != filename:
            return
        uid = view.get("area")
        if isinstance(uid, str) and uid in self.project.areas:
            self.change_area(uid)
        if view.get("view") == "detail":
            self.open_from_atlas(self.project.active)
        elif view.get("view") == "atlas":
            self.show_atlas()
            all_areas = view.get("allAreas")
            if self.atlas_mode and type(all_areas) is bool:
                self.set_atlas_all_areas(all_areas)
            selected = view.get("selection")
            if self.atlas_mode and isinstance(selected, str):
                self.pick_on_atlas(selected)
        self.remember_session()

    def show_settings(self):
        dialog = StartupDialog(self, self.settings.value("restoreLastSession", False, type=bool))
        if dialog.exec_() == QDialog.Accepted:
            self.settings.setValue("restoreLastSession", dialog.restore_session.isChecked())
            self.settings.sync()

    def _workspace(self):
        workspace, column = frame("workspace", QVBoxLayout)
        workspace.setMinimumWidth(370)
        toolbar, controls = frame("toolStrip", QHBoxLayout, (12, 6, 12, 6), 4)
        toolbar.setFixedHeight(47)
        tabs = QButtonGroup(self)
        self.atlas_button = labeled_button("Mapa světa", "showAtlas", "atlas")
        self.detail_button = labeled_button("Detail oblasti", "showDetail")
        for button in (self.atlas_button, self.detail_button):
            button.setCheckable(True)
            button.setProperty("role", "viewtab")
            tabs.addButton(button)
            controls.addWidget(button)
        self.atlas_button.setToolTip("Společná mapa oblastí (Alt+M)")
        self.detail_button.setChecked(True)
        self.atlas_button.clicked.connect(self.show_atlas)
        self.detail_button.clicked.connect(lambda: self.open_from_atlas())
        self.action(None, "Mapa světa", self.show_atlas, "Alt+M", project_only=True)
        self.action(None, "Zpět na mapu světa", self.show_atlas, "Alt+Left", project_only=True)
        controls.addWidget(divider(True))
        self.detail_tools, tool_row = frame("detailTools", QHBoxLayout, spacing=4)
        self.atlas_tools, atlas_row = frame("atlasTools", QHBoxLayout, spacing=4)
        controls.addWidget(self.detail_tools)
        controls.addWidget(self.atlas_tools)
        self.atlas_tools.hide()
        self.arrange_button = self.atlas_option(atlas_row, "Uspořádat", "arrangeAtlas",
                                               "Přetáhni oblast nebo ji otoč klávesou R", self.arrange_atlas)
        self.atlas_option(atlas_row, "Vstupy", "atlasEntrances", "Zobrazit klikatelné vstupy",
                          self.set_atlas_pins, True)
        self.atlas_option(atlas_row, "Spojení", "atlasLinks", "Zobrazit přechody mezi vzdálenými oblastmi",
                          self.set_atlas_routes)
        self.atlas_option(atlas_row, "Poklady", "atlasTreasures", "Poklady venku a zlaté kroužky u vstupů",
                          self.set_atlas_treasures)
        self.scenery_button = self.atlas_option(atlas_row, "Vybavení", "atlasScenery",
                                                "Půdorysy předmětů z modulu při čitelném přiblížení",
                                                self.set_atlas_scenery, checked=True)
        self.atlas.bind("R", self.atlas.rotate_selected)
        self.tool_buttons = {}
        group = QButtonGroup(self)
        for key, (name, shortcut, help_text) in TOOLS.items():
            button = labeled_button(name, "tool_" + key, key)
            button.setProperty("role", "tool")
            button.setCheckable(True)
            button.setToolTip(f"{name} ({shortcut})\n{help_text}")
            button.setAccessibleName(name)
            group.addButton(button)
            button.clicked.connect(lambda checked, tool=key: self.select_tool(tool))
            self.canvas.bind(shortcut, lambda tool=key: self.select_tool(tool))
            tool_row.addWidget(button)
            self.tool_buttons[key] = button
        self.tool_buttons["brush"].setChecked(True)
        controls.addStretch(1)
        back = icon_button("undo", "Zpět (Ctrl+Z)", "undo", self.undo)
        forward = icon_button("redo", "Vpřed (Ctrl+Shift+Z)", "redo", self.redo)
        back.setEnabled(False)
        forward.setEnabled(False)
        self.history.canUndoChanged.connect(back.setEnabled)
        self.history.canRedoChanged.connect(forward.setEnabled)
        controls.addWidget(back)
        controls.addWidget(forward)
        controls.addWidget(divider(True))
        self.rotate_button = icon_button("rotate", "Otočit vybraný prvek nebo oblast (R)", "rotateBrush", self.rotate_current)
        controls.addWidget(self.rotate_button)
        self.canvas.bind("R", self.rotate_brush)
        self.canvas.bind("Delete", self.canvas.erase_selection)
        self.canvas.bind("F", self.canvas.fit)
        self.action(None, "Zpět", self.undo, "Ctrl+Z", project_only=True)
        self.action(None, "Vpřed", self.redo, "Ctrl+Shift+Z", project_only=True)
        self.action(None, "Vpřed", self.redo, "Ctrl+Y", project_only=True)
        column.addWidget(toolbar)

        strip, current = frame("mapTitleBar", QHBoxLayout, (16, 6, 12, 6), 5)
        strip.setFixedHeight(66)
        names = QVBoxLayout()
        names.setSpacing(1)
        self.area_title = ElidedLabel()
        self.area_title.setObjectName("areaTitle")
        self.area_details = ElidedLabel()
        self.area_details.setObjectName("areaDetails")
        names.addWidget(self.area_title)
        names.addWidget(self.area_details)
        current.addLayout(names, 1)
        self.all_areas_button = self.atlas_option(current, "Všechny oblasti", "atlasAllAreas",
                                                  "Přidat skryté a samostatné půdorysy na plátno",
                                                  self.set_atlas_all_areas)
        self.compass_label = QLabel()
        self.compass_label.setFixedWidth(39)
        self.compass_label.setAlignment(Qt.AlignCenter)
        current.addWidget(self.compass_label)
        current.addWidget(divider(True))
        current.addWidget(icon_button("minus", "Oddálit", "zoomOut", lambda: self.map_view.zoom(1 / 1.2)))
        self.zoom_label = QLabel("100 %")
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setFixedWidth(51)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        current.addWidget(self.zoom_label)
        current.addWidget(icon_button("plus", "Přiblížit", "zoomIn", lambda: self.map_view.zoom(1.2)))
        current.addWidget(icon_button("fit", "Přizpůsobit mapu oknu (F)", "fitMap", lambda: self.map_view.fit()))
        self.grid_button = icon_button("grid", "Zobrazit čtvercovou mřížku", "toggleGrid", self.toggle_grid)
        self.grid_button.setCheckable(True)
        self.grid_button.setChecked(True)
        current.addWidget(self.grid_button)
        column.addWidget(strip)
        self.maps = QStackedWidget()
        self.maps.addWidget(self.canvas)
        self.maps.addWidget(self.atlas)
        column.addWidget(self.maps, 1)
        return workspace

    def atlas_option(self, layout, name, role, tip, callback, checked=False):
        button = labeled_button(name, role)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setProperty("role", "tool")
        button.setToolTip(tip)
        button.toggled.connect(callback)
        layout.addWidget(button)
        return button

    def _right_panel(self):
        panel, column = frame("areaPanel", QVBoxLayout, (13, 13, 13, 10), 8)
        panel.setMinimumWidth(228)
        panel.setMaximumWidth(375)
        top = QHBoxLayout()
        top.setSpacing(5)
        self.area_heading = heading("OBLASTI")
        top.addWidget(self.area_heading, 1)
        top.addWidget(icon_button("plus", "Přidat oblast (Ctrl+Shift+N)", "addArea", self.new_area))
        options = icon_button("more", "Možnosti aktivní oblasti", "areaMenu")
        self.area_options = options
        self.area_menu = QMenu(options)
        options.setMenu(self.area_menu)
        top.addWidget(options)
        column.addLayout(top)
        self.area_search = QLineEdit()
        self.area_search.setObjectName("areaSearch")
        self.area_search.setPlaceholderText("Hledat oblast…")
        self.area_search.setClearButtonEnabled(True)
        self.area_search.textChanged.connect(self.filter_areas)
        column.addWidget(self.area_search)

        self.area_list = QListWidget()
        self.area_list.setObjectName("areaList")
        self.area_list.setAccessibleName("Oblasti projektu")
        self.area_list.setItemDelegate(EntryListDelegate(self.area_list))
        self.area_list.setMaximumHeight(223)
        self.area_list.setMinimumHeight(61)
        self.area_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area_list.setMouseTracking(True)
        self.area_list.currentItemChanged.connect(self.area_selected)
        column.addWidget(self.area_list)
        column.addWidget(divider())
        column.addWidget(heading("VLASTNOSTI"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setAccessibleName("Vlastnosti prvku nebo oblasti")
        self.inspector_scroll = scroll
        self.inspector = QWidget()
        self.inspector_body = QVBoxLayout(self.inspector)
        self.inspector_body.setContentsMargins(0, 1, 3, 1)
        self.inspector_body.setSpacing(8)
        self.inspector_body.setSizeConstraint(QLayout.SetMinimumSize)
        scroll.setWidget(self.inspector)
        column.addWidget(scroll, 1)

        menu = self.area_menu
        self.action(menu, "Přidat oblast…", self.new_area, "Ctrl+Shift+N", project_only=True)
        self.action(menu, "Název, rozměry a měřítko…", self.edit_area)
        menu.addSeparator()
        self.action(menu, "Otočit o 90° doprava", lambda: self.rotate(1))
        self.action(menu, "Otočit o 90° doleva", lambda: self.rotate(-1))
        menu.addSeparator()
        self.action(menu, "Vložit obrázkovou předlohu…", self.choose_reference)
        self.reference_edit = self.action(menu, "Nastavit předlohu…", self.adjust_reference)
        self.reference_toggle = self.action(menu, "Skrýt / zobrazit předlohu", self.toggle_reference)
        self.reference_remove = self.action(menu, "Odebrat předlohu", self.remove_reference)
        menu.addSeparator()
        self.remove_action = self.action(menu, "Odebrat oblast…", self.remove_area)
        return panel

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "tool_buttons"):
            QTimer.singleShot(0, self.adapt_toolbar)

    def adapt_toolbar(self):
        compact = self.canvas.width() < 850
        for key, button in self.tool_buttons.items():
            button.setText("" if compact else TOOLS[key][0])

    def undo(self):
        self.canvas.cancel_gesture()
        self.atlas.cancel_gesture()
        self.history.undo()

    def redo(self):
        self.canvas.cancel_gesture()
        self.atlas.cancel_gesture()
        self.history.redo()

    def refresh(self):
        if self.project is None:
            return
        if self.project.active not in self.project.areas:
            self.project.active = next(iter(self.project.areas))
        area = self.area
        old = self.canvas.area
        reset = old is None or old.uid != area.uid or (old.cols, old.rows, old.north) != (area.cols, area.rows, area.north)
        self.canvas.show_area(self.project, area, reset)
        if self.canvas.selection:
            layer, key = self.canvas.selection
            if key not in getattr(area, layer):
                self.canvas.selection = None
        if self.atlas_mode:
            self.atlas.set_project(self.project, preferred=self.project.active)
            if not self.atlas.positions:
                self.display_map(False)
        self.refresh_areas()
        self.refresh_inspector()
        self.update_chrome()

    def update_zoom(self, level, world):
        if world == self.atlas_mode:
            self.zoom_label.setText(f"{max(1, level)} %")

    def display_map(self, world):
        self.atlas_mode = world
        self.maps.setCurrentWidget(self.map_view)
        self.palette.setVisible(not world)
        self.detail_tools.setVisible(not world)
        self.atlas_tools.setVisible(world)
        self.all_areas_button.setVisible(world)
        (self.atlas_button if world else self.detail_button).setChecked(True)
        self.grid_button.setChecked(self.map_view.grid)
        self.update_zoom(round(self.map_view.step / 36 * 100), world)
        self.map_view.setFocus()
        self.adapt_toolbar()

    def show_atlas(self):
        if self.project is None:
            return
        if self.atlas_mode:
            self.atlas.setFocus()
            return
        self.canvas.cancel_gesture()
        self.atlas.set_project(self.project, self.atlas.project is not self.project, preferred=self.project.active)
        if not self.atlas.positions:
            self.display_map(False)
            return
        self.display_map(True)
        self.refresh_areas()
        self.refresh_inspector()
        self.update_chrome()
        self.default_hint()

    def pick_on_atlas(self, uid):
        self.atlas.choose(uid)
        self.refresh_areas()
        self.refresh_inspector()

    def open_from_atlas(self, uid=None):
        if not self.atlas_mode:
            return
        chosen = uid or self.atlas.selected or self.project.active
        self.display_map(False)
        self.change_area(chosen)
        self.refresh()
        self.default_hint()

    def arrange_atlas(self, value):
        self.atlas.configure_arranging(value)
        self.atlas.setFocus()
        self.default_hint()
        self.update_chrome()

    def set_atlas_pins(self, value):
        self.atlas.show_pins = value
        self.atlas.update()

    def set_atlas_routes(self, value):
        self.atlas.show_routes = value
        self.atlas.update()

    def set_atlas_treasures(self, value):
        self.atlas.show_treasures = value
        self.atlas.update()

    def set_atlas_scenery(self, value):
        self.atlas.show_scenery = value
        self.atlas.update()

    def set_atlas_all_areas(self, value):
        self.atlas.cancel_gesture()
        self.atlas.show_all_areas = value
        self.atlas.fitted = True
        self.atlas.set_project(self.project, preferred=self.project.active)
        self.refresh_areas()
        self.refresh_inspector()
        self.update_chrome()
        self.atlas.setFocus()

    def place_on_atlas(self, uid, placement):
        if uid not in self.atlas.positions or placement == self.atlas.positions[uid]:
            return
        self.atlas.choose(uid)
        self.atlas.fitted = False
        positions = self.project.atlas | self.atlas.positions | {uid: placement}
        add_interior_layers(self.atlas.areas, positions, self.atlas.layers)
        self.history.push(ProjectEdit(self.project, "atlas", positions, "Rozložení mapy světa"))

    def refresh_areas(self):
        data = tuple((a.uid, a.name, a.cols, a.rows, a.unit) for a in self.project.areas.values())
        self.area_list.blockSignals(True)
        if self._area_signature != data:
            self._area_signature = data
            self.area_heading.setText(f"OBLASTI · {len(data)}")
            self.area_list.clear()
            for uid, name, cols, rows, unit in data:
                item = QListWidgetItem(name)
                item.setData(AREA_ID, uid)
                item.setData(ENTRY_DETAILS, f"{cols} × {rows}  ·  {UNITS[unit][0]}")
                ref = self.project.areas[uid].source_ref
                item.setToolTip(f"{name}\n{ref}" if ref else name)
                self.area_list.addItem(item)
            self.area_list.setFixedHeight(min(215, len(data) * 53 + 1))
        self.filter_areas(self.area_search.text())
        selected = self.atlas.selected if self.atlas_mode else self.project.active
        for i in range(self.area_list.count()):
            item = self.area_list.item(i)
            if item.data(AREA_ID) == selected:
                self.area_list.setCurrentItem(item)
                if self.atlas_mode and not item.isHidden():
                    self.area_list.scrollToItem(item)
                break
        else:
            self.area_list.clearSelection()
        self.area_list.blockSignals(False)

    def filter_areas(self, text):
        if self.project is None:
            return
        filter_text = normalized(text.strip())
        for i in range(self.area_list.count()):
            item = self.area_list.item(i)
            uid = item.data(AREA_ID)
            area = self.project.areas.get(uid)
            matches = area and (not filter_text or filter_text in normalized(f"{area.name} {area.source_ref}"))
            item.setHidden(not matches)

    def area_selected(self, item, previous):
        if item and self.project:
            uid = item.data(AREA_ID)
            if self.atlas_mode:
                if uid in self.atlas.areas:
                    self.focus_atlas_area(uid)
                else:
                    self.open_from_atlas(uid)
            else:
                self.change_area(uid)

    def focus_atlas_area(self, uid):
        self.atlas.focus_on(uid)
        self.refresh_inspector()
        self.atlas.setFocus()

    def change_area(self, uid):
        self.canvas.cancel_gesture()
        if uid in self.project.areas and uid != self.project.active:
            self.project.active = uid
            self.refresh()
        self.canvas.setFocus()

    def jump(self, to):
        if not to or to.area not in self.project.areas:
            return
        if self.atlas_mode:
            if to.area in self.atlas.areas:
                self.atlas.focus_on(to.area)
                self.refresh_areas()
                self.refresh_inspector()
                self.message(f"Cíl · {self.project.areas[to.area].name} · {cell_name(to.x, to.y)}")
                return
            self.display_map(False)
        if self.area_search.text():
            self.area_search.clear()
        self.change_area(to.area)
        alternatives = sorted(self.area.markers_in(to.x, to.y, "link"))
        self.canvas.select(("markers", alternatives[0][0]) if alternatives else ("tiles", (to.x, to.y)))
        self.update_chrome()
        self.refresh_areas()
        self.message(f"Cíl · {self.area.name} · {cell_name(to.x, to.y)}")

    def update_chrome(self):
        if self.project is None:
            self.project_label.clear()
            self.project_label.setToolTip("")
            self.setWindowTitle("Kartograf")
            return
        dirty = not self.history.isClean()
        title = self.project.title
        details = [f"Projekt · {self.path}"] if self.path else []
        if self.project.nwn_source:
            filename, version = self.project.nwn_source
            title += f" · {module_label(version)}"
            details.append(f"{module_label(version)} · {self.module_path or filename}")
        elif self.path:
            title += " · Uložený projekt"
        self.project_label.setText(title)
        self.project_label.setToolTip("\n".join(details) or self.project.title)
        self.save_state.setText("Neuložené změny" if dirty else "Uloženo" if self.path else "")
        self.setWindowTitle(f"{self.project.title}{' *' if dirty else ''} — Kartograf")
        area = self.area
        if self.atlas_mode:
            exterior = len(self.atlas.exteriors)
            self.area_title.setText(self.project.title)
            self.area_title.setToolTip(self.project.title)
            self.area_details.setText(self.atlas.scope_summary)
            self.scenery_button.setVisible(any(area.scenery for area in self.atlas.areas.values()))
            self.status_dimensions.setText(f"{exterior} exteriérů · {len(self.project.areas) - exterior} uvnitř a pod zemí")
            with QSignalBlocker(self.all_areas_button):
                self.all_areas_button.setChecked(self.atlas.show_all_areas)
            self.all_areas_button.setEnabled(0 < len(self.atlas.world) < len(self.project.areas))
            self.coordinates.setText("")
            north = 0
        else:
            self.area_title.setText(area.name)
            self.area_title.setToolTip(area.name)
            self.area_details.setText(f"{UNITS[area.unit][0]} · {UNITS[area.unit][1]}")
            self.status_dimensions.setText(f"{area.cols} × {area.rows} polí")
            north = area.north
        self.compass_label.setToolTip("Sever · " + DIRECTIONS[north])
        picture = QPixmap(30, 30)
        picture.fill(Qt.transparent)
        p = QPainter(picture)
        p.setRenderHint(QPainter.Antialiasing)
        compass(p, QPointF(15, 15), 21, north, "#c6ccbd", "#d5b07b")
        p.end()
        self.compass_label.setPixmap(picture)
        for action in (self.reference_edit, self.reference_toggle, self.reference_remove):
            action.setEnabled(area.reference is not None)
        self.remove_action.setEnabled(len(self.project.areas) > 1)
        self.area_options.setEnabled(not self.atlas_mode)
        self.rotate_button.setEnabled(not self.atlas_mode or self.atlas.arranging)

    def refresh_inspector(self):
        self.mark_form = None
        while self.inspector_body.count():
            child = self.inspector_body.takeAt(0).widget()
            if child:
                child.hide()
                child.deleteLater()
        self.inspector_scroll.verticalScrollBar().setValue(0)
        if self.project is None:
            return
        if self.atlas_mode:
            self.atlas_inspector()
            return
        selected = self.canvas.selection
        if selected:
            layer, key = selected
            item = getattr(self.area, layer).get(key)
            if item:
                if layer in {"tiles", "heights"}:
                    self.tile_inspector(key)
                    return
                browsing = self.canvas.tool == "hand"
                summary = MarkSummary(self.project, self.area, key, item, browsing=browsing)
                summary.go_to.connect(self.jump)
                self.inspector_body.addWidget(summary)
                if not browsing:
                    form = MarkForm(self.project, self.area, key, item)
                    form.applied.connect(lambda value, pair: self.apply_mark(layer, key, value, pair))
                    form.go_to.connect(self.jump)
                    self.inspector_body.addWidget(form)
                    self.mark_form = form
                self.inspector_body.addStretch(1)
                return

        info = ElidedLabel(self.area.name)
        info.setObjectName("objectTitle")
        info.setToolTip(self.area.name)
        self.inspector_body.addWidget(info)
        self.inspector_body.addWidget(subtle(f"{self.area.cols} × {self.area.rows} polí · {UNITS[self.area.unit][0]}"))
        if self.area.source_ref:
            self.inspector_body.addWidget(subtle(f"NWN · {self.area.source_ref} · {self.area.tileset or 'bez sady'}"))
        self.inspector_body.addWidget(divider())
        links = [(key, item) for key, item in sorted(self.area.markers.items()) if item.target]
        if links:
            self.inspector_body.addWidget(heading("PŘECHODY"))
            for key, item in links:
                dest = self.project.areas[item.target.area]
                text = f"{cell_name(*key[:2])}  →  {dest.name}"
                button = labeled_button(text, "areaTransition", item.style)
                button.setProperty("role", "link")
                button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                button.setToolTip(f"{text} · {cell_name(item.target.x, item.target.y)}")
                button.clicked.connect(lambda checked, to=item.target: self.jump(to))
                self.inspector_body.addWidget(button)
        else:
            self.inspector_body.addWidget(subtle("Klikni na značku v mapě. Výběr umožní její úpravu."))
        if self.area.reference:
            button = labeled_button("Nastavit předlohu…", "referenceSettings", "image")
            button.clicked.connect(self.adjust_reference)
            self.inspector_body.addWidget(button)
        self.inspector_body.addStretch(1)

    def atlas_inspector(self):
        uid = self.atlas.selected
        if uid not in self.atlas.areas:
            self.inspector_body.addWidget(subtle("Vyber oblast na mapě nebo v seznamu."))
        else:
            area = self.atlas.areas[uid]
            caption = QLabel(area.name)
            caption.setObjectName("objectTitle")
            caption.setWordWrap(True)
            self.inspector_body.addWidget(caption)
            kind = area.kind
            if kind == "auto":
                kind = "outdoor" if uid in self.atlas.exteriors else "indoor"
            self.inspector_body.addWidget(subtle(f"{AREA_KINDS[kind]} · {area.cols} × {area.rows} polí"))
            actions, row = frame("atlasInspectorActions", QHBoxLayout, spacing=5)
            zoom = labeled_button("Přiblížit", "focusAtlasArea", "plus")
            zoom.clicked.connect(lambda: self.focus_atlas_area(uid))
            row.addWidget(zoom, 1)
            button = labeled_button("Půdorys", "openAtlasArea", "grid")
            button.clicked.connect(lambda: self.open_from_atlas(uid))
            row.addWidget(button, 1)
            self.inspector_body.addWidget(actions)
            routes = sorted({r.target for r in self.atlas.routes[uid]},
                            key=lambda t: (self.project.areas[t.area].name.casefold(), t.x, t.y))
            for on_map, label in ((False, "VSTUPY"), (True, "NAVAZUJÍCÍ OBLASTI")):
                group = [to for to in routes if (to.area in self.atlas.areas) == on_map]
                if not group:
                    continue
                self.inspector_body.addWidget(divider())
                self.inspector_body.addWidget(heading(label))
                for to in group:
                    name = self.project.areas[to.area].name
                    link = labeled_button(name, "atlasDestination", "portal" if on_map else "door")
                    link.setProperty("role", "link")
                    link.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                    link.setToolTip(f"{name} · {cell_name(to.x, to.y)}")
                    link.clicked.connect(lambda checked, target=to: self.jump(target))
                    self.inspector_body.addWidget(link)
        self.inspector_body.addStretch(1)

    def tile_inspector(self, key):
        tile = self.area.tiles.get(key)
        caption = ASSETS[tile.style].name if tile else "Výška pole"
        title = QLabel(caption)
        title.setObjectName("objectTitle")
        self.inspector_body.addWidget(title)
        self.inspector_body.addWidget(subtle(cell_name(*key)))
        if tile and tile.layout:
            if tile.layout.index >= 0:
                self.inspector_body.addWidget(subtle(f"Dlaždice NWN {tile.layout.index}"))
            levels = self.area.corner_heights(*key)
            if len(set(levels)) > 1:
                self.inspector_body.addWidget(subtle(f"Rohy SZ/SV/JV/JZ · {' / '.join(map(str, levels))}"))
        field = space_spin(self.area.heights.get(key, 0), -MAX_LEVEL, MAX_LEVEL)
        field.setAccessibleName("Výškový stupeň")
        self.inspector_body.addWidget(QLabel("Výškový stupeň"))
        self.inspector_body.addWidget(field)
        apply = labeled_button("Nastavit výšku", "tileHeight")
        apply.clicked.connect(lambda: self.set_height(key, field.value()))
        self.inspector_body.addWidget(apply)
        if tile and (ASSETS[tile.style].directional or tile.layout):
            turn = labeled_button("Otočit dlaždici", "turnTile", "rotate")
            turn.clicked.connect(self.canvas.rotate_selection)
            self.inspector_body.addWidget(turn)
        self.inspector_body.addStretch(1)

    def set_height(self, key, value):
        patch = Patch("Výška pole")
        patch.put(self.area.heights, key, value or None)
        if patch.edits:
            self.history.push(patch)

    def select_tool(self, tool):
        layer = ASSETS[self.canvas.asset].layer
        if (tool == "rect" and layer not in {"tiles", "heights"}) or (tool == "fill" and layer != "tiles"):
            return
        self.canvas.cancel_gesture()
        self.tool_buttons[tool].setChecked(True)
        self.canvas.set_tool(tool)
        self.canvas.setFocus()
        if self.canvas.selection and not self.atlas_mode:
            self.refresh_inspector()
        self.default_hint()

    def select_asset(self, key):
        self.canvas.asset, self.canvas.turns = key, 0
        layer = ASSETS[key].layer
        self.tool_buttons["rect"].setEnabled(layer in {"tiles", "heights"})
        self.tool_buttons["fill"].setEnabled(layer == "tiles")
        if not ((layer in {"tiles", "heights"} and self.canvas.tool == "rect")
                or (layer == "tiles" and self.canvas.tool == "fill")):
            self.select_tool("brush")
        self.canvas.select(None)
        self.canvas.setFocus()
        self.default_hint()

    def rotate_brush(self):
        if not self.canvas.rotate_selection():
            asset = ASSETS[self.canvas.asset]
            if asset.directional:
                self.canvas.turns = (self.canvas.turns + 1) % 4
                self.canvas.update()
                self.message(f"{asset.name} · {DIRECTIONS[self.canvas.turns]}")
            elif asset.layer == "edges":
                self.message("Stěny a dveře se přichytávají k nejbližší hraně čtverce.")
            else:
                self.message(f"{asset.name} nemá směr.")

    def rotate_current(self):
        if self.atlas_mode:
            self.atlas.rotate_selected()
        else:
            self.rotate_brush()

    def toggle_grid(self, value):
        self.map_view.grid = value
        self.map_view.update()

    def default_hint(self):
        if self.project is None:
            self.status_help.clear()
            return
        if self.atlas_mode:
            text = ("Přetáhni oblast a klávesou R ji otoč. Prostřední tlačítko posouvá celou mapu."
                    if self.atlas.arranging else
                    "Tahem posuneš mapu. Kolečko přiblíží, vstup otevře interiér a dvojklik otevře půdorys.")
            self.status_help.setText(text)
            return
        tool = self.canvas.tool
        asset = ASSETS[self.canvas.asset]
        text = TOOLS[tool][2]
        if tool == "brush":
            if asset.layer == "edges":
                text = "Polož prvek na hranu pole. Tahem pokračuj po dalších hranách."
            elif asset.layer == "markers":
                text = "Kliknutím polož značku. Podrobnosti upravíš vpravo."
            elif asset.layer == "heights":
                if self.canvas.asset == "slope_ground":
                    text = "Vyšší hrana svahu míří ve směru štětce. Na prázdném poli se použije suť."
                else:
                    text = "Klikni nebo táhni přes pole. Společná hrana stejné výšky zůstane bez stínu."
            if asset.directional:
                text += f"  ·  R otočí značku {DIRECTIONS[self.canvas.turns].split()[0]}"
        self.status_help.setText(text)

    def message(self, text):
        self.status_help.setText(text)
        self._hint_timer.start(4400)

    def apply_mark(self, layer, key, value, reciprocal):
        area = self.area
        patch = Patch("Vlastnosti prvku")
        if layer == "edges":
            patch.put(area.edges, key, value)
        elif layer == "markers":
            target = value.target
            here = Target(area.uid, *key[:2])
            previous = area.markers[key]
            if target == here and target != previous.target:
                QMessageBox.information(self, "Cíl průchodu", "Zvol jiné cílové pole.")
                return
            if target and target != here:
                destination = self.project.areas[target.area]
                candidates = list(destination.markers_in(target.x, target.y, "link"))
                found = [pair for pair in candidates if pair[1].target == here]
                if not found:
                    found = [pair for pair in candidates if pair[1].target is None]
                other_key, partner = found[0] if len(found) == 1 else ((target.x, target.y, "link"), None)
                existing_pair = target == previous.target and any(mark.target == here for _, mark in candidates)
                if reciprocal and (partner is not None or not existing_pair):
                    if partner is None and candidates:
                        QMessageBox.information(self, "Zpětný průchod", "Cílové pole už obsahuje průchod s jiným cílem.")
                        return
                    key_back = {"up": "down", "down": "up"}.get(value.style, value.style)
                    back = partner or Mark.from_asset(key_back)
                    turns = (value.turns - area.north + destination.north + 2) % 4
                    patch.put(destination.markers, other_key,
                              replace(back, target=here, turns=turns, arrival_xy=value.origin_xy))
                elif not reciprocal and partner and partner.target == here:
                    patch.put(destination.markers, other_key, replace(partner, target=None, arrival_xy=None))
            set_marker(patch, self.project, area, key, value)
        if patch.edits:
            self.history.push(patch)
            self.message("Vlastnosti prvku upraveny.")

    def replace_area(self, area, title):
        patch = Patch(title)
        patch.put(self.project.areas, area.uid, area)
        if patch.edits:
            self.history.push(patch)

    def new_area(self):
        if len(self.project.areas) >= MAX_AREAS:
            QMessageBox.information(self, "Oblasti", f"Projekt pojme nejvýše {MAX_AREAS} oblastí.")
            return
        dialog = AreaDialog(self, "Přidat oblast", unit=self.area.unit)
        if dialog.exec_() == QDialog.Accepted:
            area = Area(**dialog.values())
            self.replace_area(area, "Přidání oblasti")
            self.area_search.clear()
            if self.atlas_mode:
                self.open_from_atlas(area.uid)
            else:
                self.change_area(area.uid)

    def edit_area(self):
        dialog = AreaDialog(self, "Nastavení oblasti", self.area)
        if dialog.exec_() == QDialog.Accepted:
            values = dialog.values()
            if not resize_allowed(self.project, self.area, values["cols"], values["rows"]):
                QMessageBox.information(self, "Rozměry oblasti",
                                        "Do zvoleného rastru se nevejdou některé prvky nebo cíle průchodů.")
                return
            self.replace_area(replace(self.area, **values), "Nastavení oblasti")

    def rotate(self, direction):
        self.canvas.cancel_gesture()
        self.canvas.selection = None
        self.history.push(rotate_area(self.project, self.area.uid, direction))
        self.message("Oblast otočena. Souřadnice propojených průchodů jsou přepočítané.")

    def remove_area(self):
        if len(self.project.areas) < 2:
            return
        if QMessageBox.question(self, "Odebrat oblast",
                                f"Odebrat oblast „{self.area.name}“ a odpojit její průchody?",
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            self.history.push(delete_area(self.project, self.area.uid))

    def choose_reference(self):
        file = self.choose_file("Vložit předlohu", "Obrázky (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if file is None:
            return
        try:
            ref = import_reference(file, self.area)
            self.replace_area(replace(self.area, reference=ref), "Vložení předlohy")
            self.message("Předloha je vložená do projektu. V nabídce oblasti lze upravit její polohu a průhlednost.")
        except (ValueError, OSError) as error:
            self.error("Vložit předlohu", error)

    def adjust_reference(self):
        if self.area.reference:
            dialog = ReferenceDialog(self, self.area.reference)
            if dialog.exec_() == QDialog.Accepted:
                self.replace_area(replace(self.area, reference=dialog.values()), "Nastavení předlohy")

    def toggle_reference(self):
        if self.area.reference:
            ref = replace(self.area.reference, visible=not self.area.reference.visible)
            self.replace_area(replace(self.area, reference=ref), "Viditelnost předlohy")

    def remove_reference(self):
        if self.area.reference:
            self.replace_area(replace(self.area, reference=None), "Odebrání předlohy")

    def unsaved_ok(self):
        self.canvas.cancel_gesture()
        self.atlas.cancel_gesture()
        if self.history.isClean():
            return True
        box = QMessageBox(QMessageBox.Warning, "Neuložené změny",
                          "Projekt obsahuje neuložené změny.", parent=self)
        save = box.addButton("Uložit", QMessageBox.AcceptRole)
        discard = box.addButton("Zahodit změny", QMessageBox.DestructiveRole)
        cancel = box.addButton("Zrušit", QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec_()
        return box.clickedButton() == discard or (box.clickedButton() == save and self.save())

    def replace_project(self, project, path=None, stamp=None, *, module_path=None):
        self.remember_session()
        self.project, self.path, self.stamp = project, path, stamp
        self.module_path = module_path
        self._area_signature = None
        self.canvas.area = None
        self.atlas.project = None
        self.atlas.show_all_areas = False
        self.arrange_button.setChecked(False)
        if project is None:
            self.atlas_mode = False
            self.canvas.show_area(None, None, reset=True)
            self.atlas.clear_project()
            self.area_list.clear()
            self.refresh_inspector()
            self.history.clear()
            self.show_home()
            return
        self.pages.setCurrentWidget(self.splitter)
        self.update_project_controls()
        self.display_map(False)
        self.area_search.clear()
        self.history.clear()
        self.refresh()
        if len(project.areas) > 1 and (project.atlas or any(a.source_ref for a in project.areas.values())):
            self.show_atlas()
        else:
            self.canvas.setFocus()
        if self.session_path:
            self.remember_project()
        self.default_hint()

    def close_project(self):
        if self.project is not None and self.unsaved_ok():
            self.replace_project(None)

    def new_project(self):
        if not self.unsaved_ok():
            return
        dialog = AreaDialog(self, "Nový projekt", project=True, unit=self.area.unit if self.project else "nwn")
        if dialog.exec_() == QDialog.Accepted:
            area = Area(**dialog.values())
            self.replace_project(Project.single(dialog.project_name.text().strip(), area))

    def open_mountains(self):
        if self.unsaved_ok():
            self.replace_project(mountain_project())

    def choose_nwn_game(self):
        current = game_folder(self.settings.value("nwnGame"))
        selected = QFileDialog.getExistingDirectory(self, "Složka dat Neverwinter Nights", str(current or Path.home()))
        if selected:
            resolved = game_folder(selected, auto=False)
            if resolved:
                self.settings.setValue("nwnGame", str(resolved))
                self.message(f"Data NWN · {resolved}")
            else:
                self.error("Složka dat Neverwinter Nights", "Zvol instalaci, která obsahuje složku data se soubory KEY.")

    def module_directory(self):
        remembered = self.settings.value("nwnModuleDirectory", "", type=str)
        if remembered and Path(remembered).is_dir():
            return remembered
        modules = profile_dir() / "modules"
        return str(modules if modules.is_dir() else Path.home())

    def choose_module(self):
        path = self.choose_file("Importovat modul NWN", MODULE_FILTER, self.module_directory())
        if path and self.unsaved_ok():
            self.open_path(path)

    def read_nwn_module(self, path, title, *, restoring=False, tile_details=True):
        progress = QProgressDialog("Čtu modul NWN…", "Zrušit", 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(200)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        def step(count, total, ref):
            if progress.maximum() != total:
                progress.setRange(0, total)
            if ref:
                progress.setLabelText(f"{count + 1} z {total} · {ref}")
            progress.setValue(count)
            return not progress.wasCanceled()

        try:
            return import_module(path, game=self.settings.value("nwnGame"), progress=step, tile_details=tile_details)
        except ImportCancelled:
            return None
        except (OSError, ValueError) as error:
            progress.hide()
            if not restoring:
                self.error(title, error)
            return None
        finally:
            progress.hide()
            progress.deleteLater()

    def import_path(self, path, *, restoring=False):
        imported = self.read_nwn_module(path, "Importovat modul NWN", restoring=restoring)
        if imported is None:
            if restoring:
                self.message("Poslední rozpracovanou relaci se nepodařilo otevřít.")
            return False
        project, report = imported
        self.replace_project(project, module_path=path)
        if not restoring:
            self.history.resetClean()
        self.settings.setValue("nwnModuleDirectory", str(path.parent))
        source = f"{module_label(project.nwn_source[1])} · {path.name}"
        self.message(f"Importováno · {len(project.areas)} oblastí · {source}")
        if not restoring and self.isVisible():
            visible = len(self.atlas.positions) if self.atlas_mode else 1
            self.show_import_report(f"{source}\n\n{report.text(len(project.areas), visible)}")
        return True

    def choose_treasure_module(self):
        self.update_from_module("Doplnit poklady z modulu NWN", merge_nwn_treasures,
                                "Poklady · {} nových značek v {} porovnaných oblastech", tile_details=False)

    def choose_graphics_module(self):
        self.update_from_module("Aktualizovat dlaždice a vybavení z modulu NWN", merge_nwn_graphics,
                                "Grafika z NWN · {} změn · porovnáno {} oblastí", tile_details=True)

    def update_from_module(self, title, merge, summary, *, tile_details):
        initial = str(self.session_path.parent) if self.session_path else self.module_directory()
        path = self.choose_file(title, MODULE_FILTER, initial)
        if path is None or (imported := self.read_nwn_module(path, title, tile_details=tile_details)) is None:
            return
        patch, matched = merge(self.project, imported[0])
        if not matched:
            self.error(title, "Vyber zdrojový modul, jehož oblasti odpovídají otevřené mapě.")
            return
        if patch.edits:
            self.canvas.cancel_gesture()
            self.atlas.cancel_gesture()
            self.history.push(patch)
        self.message(summary.format(len(patch.edits), matched))

    def show_import_report(self, text):
        QMessageBox.information(self, "Modul NWN načten", text)

    def browse_project(self):
        file = self.choose_file("Otevřít soubor", OPEN_FILTER, str(self.session_path.parent) if self.session_path else "")
        if file is not None and self.unsaved_ok():
            self.open_path(file)

    def open_path(self, path, *, restoring=False):
        try:
            path = path.expanduser().resolve()
            if path.suffix.casefold() == ".mod":
                return self.import_path(path, restoring=restoring)
            before = file_stamp(path)
            project = load_project(path)
            if file_stamp(path) != before:
                raise ValueError("Soubor se během načítání změnil. Otevři ho znovu.")
            self.replace_project(project, path, before)
            self.message(f"Otevřeno · {path.name}")
            return True
        except (OSError, ValueError, RuntimeError) as error:
            if restoring:
                self.message("Poslední rozpracovanou relaci se nepodařilo otevřít.")
            else:
                self.error("Otevřít projekt", error)
            return False

    def rename_project(self):
        name, accepted = QInputDialog.getText(self, "Přejmenovat projekt", "Název projektu", text=self.project.title)
        if accepted and name.strip() and self.project.title != name.strip()[:100]:
            self.history.push(ProjectEdit(self.project, "title", name.strip()[:100], "Název projektu"))

    def save(self):
        self.canvas.cancel_gesture()
        self.atlas.cancel_gesture()
        return self.save_as() if self.path is None else self.write_project(self.path)

    def save_as(self):
        default = str(self.path or (Path.home() / (self.project.title.replace("/", "–") + ".kartograf")))
        path = self.choose_file("Uložit projekt", PROJECT_FILTER, default, "kartograf")
        return self.write_project(path) if path else False

    def write_project(self, path):
        path = path.resolve()
        if path.suffix.casefold() == ".mod":
            self.error("Uložit projekt", "Pro projekt použij příponu .kartograf.")
            return False
        lock = QLockFile(str(path) + ".lock")
        if not lock.tryLock(0):
            self.error("Uložit projekt", "Soubor je dočasně uzamčený nebo do složky nelze zapisovat.")
            return False
        try:
            if self.path == path and (not path.exists() or file_stamp(path) != self.stamp):
                raise ValueError("Soubor se mezitím změnil. Použij Uložit jako a zvol jiný soubor.")
            save_project(self.project, path)
            self.path, self.stamp = path, file_stamp(path)
            self.remember_project()
            self.history.setClean()
            self.update_chrome()
            self.message(f"Uloženo · {path.name}")
            return True
        except (OSError, ValueError) as error:
            self.error("Uložit projekt", error)
            return False
        finally:
            lock.unlock()

    def choose_file(self, title, name_filter, suggestion="", suffix=None):
        dialog = QFileDialog(self, title, suggestion or str(self.path.parent if self.path else Path.home()), name_filter)
        if suffix:
            dialog.setAcceptMode(QFileDialog.AcceptSave)
            dialog.setDefaultSuffix(suffix)
            dialog.setFileMode(QFileDialog.AnyFile)
        else:
            dialog.setFileMode(QFileDialog.ExistingFile)
        return Path(dialog.selectedFiles()[0]) if dialog.exec_() == QDialog.Accepted else None

    def export(self):
        self.canvas.cancel_gesture()
        self.atlas.cancel_gesture()
        world = self.atlas_mode
        area = self.area
        if world:
            left, top, right, bottom = self.atlas.bounds
            landscape = right - left >= bottom - top
            scope = self.atlas.scope_summary
            suffix = "všechny oblasti" if self.atlas.show_all_areas else "mapa světa"
            name = f"{self.project.title} – {suffix}"
            layers = self.atlas.areas.values()
        else:
            landscape = area.cols >= area.rows
            scope = name = area.name
            layers = (area,)
        stored = self.settings.value("exportFormat", "pdf")
        dimensions = partial(export_size, self.project, area, self.atlas if world else None)
        dialog = ExportDialog(self, stored, scope, landscape, any(a.reference and a.reference.visible for a in layers),
                              dimensions, world=world, grid=self.map_view.grid)
        if dialog.exec_() != QDialog.Accepted:
            return
        kind = dialog.kind.currentData()
        default_dir = Path(self.settings.value("exportDirectory", str(self.path.parent if self.path else Path.home())))
        name = name.replace("/", "–")
        path = self.choose_file("Exportovat mapu", f"{kind.upper()} (*.{kind})", str(default_dir / f"{name}.{kind}"), kind)
        if path is None:
            return
        preset = dialog.size_preset.currentData()
        options = dialog.options()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if kind == "pdf":
                landscape = dialog.orientation.currentData()
                if world:
                    export_atlas_pdf(self.atlas, path, preset, landscape, **options)
                else:
                    export_area_pdf(self.project, area, path, preset, landscape, **options)
            else:
                pixels = CELL if preset == "auto" else preset
                if world:
                    export_atlas_png(self.atlas, path, pixels=pixels, **options)
                else:
                    export_png(self.project, area, path, pixels=pixels, **options)
        except (OSError, ValueError) as error:
            QApplication.restoreOverrideCursor()
            self.error("Exportovat mapu", error)
            return
        except Exception:
            QApplication.restoreOverrideCursor()
            raise
        QApplication.restoreOverrideCursor()
        self.settings.setValue("exportFormat", kind)
        self.settings.setValue("exportDirectory", str(path.parent))
        self.message(f"Exportováno · {path.name}")

    def shortcuts(self):
        tools = "\n".join(f"{key}  {title}" for title, key, _ in TOOLS.values())
        QMessageBox.information(self, "Ovládání Kartografu",
                                f"{tools}\nR  Otočit prvek\nF  Přizpůsobit mapu\nDelete  Odebrat vybraný prvek\n\n"
                                "Mezerník nebo prostřední tlačítko + tah  Posun plátna\n"
                                "Kolečko  Přiblížení\nCtrl+Z / Ctrl+Shift+Z  Zpět / vpřed\n"
                                "Alt+M nebo Alt+←  Mapa světa\nCtrl+klik na značku průchodu  Přejít na cíl\n"
                                "Ctrl+I  Importovat modul NWN\nCtrl+S  Uložit projekt\nCtrl+E  Exportovat mapu")

    def error(self, title, error):
        QMessageBox.warning(self, title, str(error))

    def closeEvent(self, event):
        if self.unsaved_ok():
            self.remember_session()
            self.settings.setValue("window", self.saveGeometry())
            self.settings.sync()
            event.accept()
        else:
            event.ignore()


def app_palette():
    palette = QPalette()
    for role, color in ((QPalette.Window, "#202622"), (QPalette.WindowText, "#e5e6db"),
                        (QPalette.Base, "#171e19"), (QPalette.Text, "#e5e6db"),
                        (QPalette.Button, "#2c352d"), (QPalette.ButtonText, "#e5e6db"),
                        (QPalette.Highlight, "#b79b69"), (QPalette.HighlightedText, "#161f17"),
                        (QPalette.Mid, "#536552"), (QPalette.Shadow, "#121a13")):
        palette.setColor(role, QColor(color))
    return palette


def make_application():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Kartograf")
    app.setDesktopFileName("kartograf")
    app.setStyle("Fusion")
    app.setPalette(app_palette())
    app.setStyleSheet((BASEDIR / "theme.qss").read_text(encoding="utf-8"))
    return app


def main():
    args = sys.argv[1:]
    if len(args) > 1 or (args and args[0] in ("-h", "--help")):
        print("Použití: kartograf.py [PROJEKT.kartograf | MODUL.mod | --demo]")
        return int(len(args) > 1)
    app = make_application()
    editor = Editor(args[0] if args and args[0] != "--demo" else None, demo=args == ["--demo"])
    editor.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
