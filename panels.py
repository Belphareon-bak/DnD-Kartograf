"""Strom palety, formulář prvku a dialogy editoru."""
from dataclasses import replace
from fractions import Fraction
from html import escape
import unicodedata

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSlider, QSpinBox,
                             QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from catalog import ASSETS, GROUPS
from document import AREA_KINDS, MAX_SIZE, MIN_SIZE, UNITS, Target, cell_name
from drawing import PDF_DPI, icon
from nwn_bestiary import CreatureCard, EncounterCard

DIRECTIONS = ("↑ Nahoru", "→ Doprava", "↓ Dolů", "← Doleva")
GROUP_ICON = dict(outdoor="grass", relief="raise_ground", indoor="flagstone", door="door", link="exit", monster="monster",
                  treasure="chest", trap="plate", ore="crystal", label="point")
ASSET_ROLE = Qt.UserRole + 1


def heading(text):
    label = QLabel(text)
    label.setProperty("role", "section")
    return label


def subtle(text=""):
    label = QLabel(text)
    label.setProperty("role", "muted")
    label.setWordWrap(True)
    return label


def labeled_button(text, name, image=None, primary=False):
    button = QPushButton(text)
    button.setObjectName(name)
    if image:
        button.setIcon(icon(image, ink="#263229" if primary else "#c1c9bc"))
    if primary:
        button.setProperty("primary", True)
    return button


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


class Palette(QFrame):
    asset_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("palette")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(11)
        layout.addWidget(heading("PALETA"))
        self.search = QLineEdit()
        self.search.setObjectName("paletteSearch")
        self.search.setPlaceholderText("Hledat prvek…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Hledat v paletě")
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setObjectName("assetTree")
        self.tree.setAccessibleName("Strom kategorií mapových prvků")
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.setIconSize(QSize(17, 17))
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setTextElideMode(Qt.ElideRight)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.tree, 1)
        self.empty = subtle("Žádný prvek neodpovídá hledání.")
        self.empty.hide()
        layout.addWidget(self.empty)

        current = QFrame()
        current.setObjectName("activeAsset")
        row = QHBoxLayout(current)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(10)
        self.swatch = QLabel()
        self.swatch.setFixedSize(38, 38)
        row.addWidget(self.swatch)
        names = QVBoxLayout()
        names.setSpacing(1)
        self.name = QLabel()
        self.name.setObjectName("assetName")
        self.name.setWordWrap(True)
        self.description = subtle()
        names.addWidget(self.name)
        names.addWidget(self.description)
        row.addLayout(names, 1)
        layout.addWidget(current)

        self.nodes = {}
        self.groups = {}
        self.sections = {}
        for group, name in GROUPS.items():
            node = QTreeWidgetItem(self.tree, [name])
            node.setIcon(0, icon(GROUP_ICON[group]))
            bold = node.font(0)
            bold.setBold(True)
            node.setFont(0, bold)
            self.groups[group] = node
        for key, asset in ASSETS.items():
            branch = self.groups[asset.group]
            for depth, name in enumerate(asset.section.split(" / ")):
                prefix = tuple(asset.section.split(" / ")[:depth + 1])
                identity = (asset.group, prefix)
                node = self.sections.get(identity)
                if node is None:
                    node = QTreeWidgetItem(branch, [name])
                    self.sections[identity] = node
                branch = node
            mark = f"  ·  CR {asset.cr}" if asset.cr else ""
            node = QTreeWidgetItem(branch, [asset.name + mark])
            node.setIcon(0, icon(key))
            node.setData(0, ASSET_ROLE, key)
            node.setToolTip(0, asset.name + mark)
            self.nodes[key] = node
        for node in (*self.groups.values(), *self.sections.values()):
            node.setFlags(node.flags() & ~Qt.ItemIsSelectable)
        self._expansion = None
        self.search.textChanged.connect(self.filter)
        self.tree.currentItemChanged.connect(self.select_item)
        self.tree.itemClicked.connect(self.toggle_branch)
        self.tree.setExpandsOnDoubleClick(False)
        self.show_asset("grass", reveal=True)

    def toggle_branch(self, item, column):
        if not item.data(column, ASSET_ROLE):
            item.setExpanded(not item.isExpanded())

    def show_asset(self, key, reveal=False):
        if reveal:
            node = self.nodes[key]
            parent = node.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(node)
        asset = ASSETS[key]
        self.name.setText(asset.name)
        self.swatch.setPixmap(icon(key, 38).pixmap(38, 38))
        context = {"tiles": "Podklad", "heights": "Výškový stupeň", "edges": "Na hraně pole", "markers": "Značka v poli"}[asset.layer]
        self.description.setText(f"CR {asset.cr} · D&D 5e" if asset.cr else context)

    def select_item(self, item, previous):
        key = item.data(0, ASSET_ROLE) if item else None
        if key:
            self.show_asset(key)
            self.asset_selected.emit(key)

    def filter(self, text):
        words = normalized(text).split()
        folders = (*self.groups.values(), *self.sections.values())
        if words and self._expansion is None:
            self._expansion = [node.isExpanded() for node in folders]
        for key, item in self.nodes.items():
            asset = ASSETS[key]
            haystack = normalized(" ".join((asset.name, asset.section, GROUPS[asset.group], asset.cr, key)))
            item.setHidden(not all(word in haystack for word in words))
        for item in reversed(folders):
            item.setHidden(all(item.child(i).isHidden() for i in range(item.childCount())))
            if words and not item.isHidden():
                item.setExpanded(True)
        if not words and self._expansion is not None:
            for item, expanded in zip(folders, self._expansion):
                item.setExpanded(expanded)
            self._expansion = None
        self.empty.setVisible(all(item.isHidden() for item in self.groups.values()))


def dialog_layout(dialog, ok_text):
    dialog.setMinimumWidth(435)
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(15)
    form = QFormLayout()
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(9)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText(ok_text)
    buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
    buttons.button(QDialogButtonBox.Cancel).setText("Zrušit")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return form


def text_input(value="", limit=100):
    field = QLineEdit(value)
    field.setMaxLength(limit)
    return field


def space_spin(value, lower, upper):
    spin = QSpinBox()
    spin.setRange(lower, upper)
    spin.setValue(value)
    return spin


def direction_field(turns):
    field = QComboBox()
    field.addItems(DIRECTIONS)
    field.setCurrentIndex(turns)
    return field


class StartupDialog(QDialog):
    def __init__(self, parent, restore_session):
        super().__init__(parent)
        self.setWindowTitle("Nastavení")
        form = dialog_layout(self, "Uložit")
        self.restore_session = QCheckBox("Otevřít poslední rozpracovanou relaci")
        self.restore_session.setObjectName("restoreLastSession")
        self.restore_session.setChecked(restore_session)
        form.addRow(self.restore_session)
        form.addRow(subtle("Při spuštění se načte poslední projekt ze souboru a jeho aktivní oblast."))


class AreaDialog(QDialog):
    def __init__(self, parent, title, area=None, project=False, unit="nwn"):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = dialog_layout(self, "Použít" if area else "Vytvořit")
        self.project_name = None
        if project:
            self.project_name = text_input("Nový projekt")
            form.addRow("Název projektu", self.project_name)
            self.project_name.selectAll()
        self.name = text_input(area.name if area else ("První oblast" if project else "Nová oblast"))
        form.addRow("Název oblasti", self.name)
        self.cols = space_spin(area.cols if area else 24, MIN_SIZE, MAX_SIZE)
        self.rows = space_spin(area.rows if area else 18, MIN_SIZE, MAX_SIZE)
        form.addRow("Šířka v polích", self.cols)
        form.addRow("Výška v polích", self.rows)
        self.unit = QComboBox()
        for key, (caption, details) in UNITS.items():
            self.unit.addItem(f"{caption} · {details}", key)
        self.unit.setCurrentIndex(self.unit.findData(area.unit if area else unit))
        form.addRow("Měřítko", self.unit)
        self.north = direction_field(area.north if area else 0)
        form.addRow("Sever na mapě", self.north)
        self.kind = QComboBox()
        for key, label in AREA_KINDS.items():
            self.kind.addItem(label, key)
        self.kind.setCurrentIndex(self.kind.findData(area.kind if area else "auto"))
        form.addRow("Typ oblasti", self.kind)
        if not project:
            self.name.selectAll()
            self.name.setFocus()

    def accept(self):
        if not self.name.text().strip() or (self.project_name and not self.project_name.text().strip()):
            QMessageBox.information(self, "Název", "Zadej název projektu i oblasti." if self.project_name else "Zadej název oblasti.")
            return
        super().accept()

    def values(self):
        return dict(name=self.name.text().strip(), cols=self.cols.value(), rows=self.rows.value(),
                    unit=self.unit.currentData(), north=self.north.currentIndex(), kind=self.kind.currentData())


class ReferenceDialog(QDialog):
    def __init__(self, parent, reference):
        super().__init__(parent)
        self.reference = reference
        self.setWindowTitle("Nastavení předlohy")
        form = dialog_layout(self, "Použít")
        self.dimensions = []
        for value, title, minimum in zip(
            (reference.x, reference.y, reference.w, reference.h),
            ("Posun doprava v polích", "Posun dolů v polích", "Šířka v polích", "Výška v polích"),
            (-MAX_SIZE * 2, -MAX_SIZE * 2, 0.25, 0.25),
        ):
            field = QDoubleSpinBox()
            field.setRange(min(minimum, value), max(MAX_SIZE * 4, value))
            field.setDecimals(2)
            field.setSingleStep(0.25)
            field.setValue(value)
            self.dimensions.append(field)
            form.addRow(title, field)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(round(reference.opacity * 100))
        row = QHBoxLayout()
        percentage = QLabel(f"{self.opacity.value()} %")
        percentage.setMinimumWidth(45)
        self.opacity.valueChanged.connect(lambda value: percentage.setText(f"{value} %"))
        row.addWidget(self.opacity, 1)
        row.addWidget(percentage)
        form.addRow("Viditelnost předlohy", row)
        self.above = QCheckBox("Zobrazit nad podkladem")
        self.above.setChecked(reference.above)
        form.addRow(self.above)
        self.visible = QCheckBox("Zobrazit předlohu")
        self.visible.setChecked(reference.visible)
        form.addRow(self.visible)

    def values(self):
        x, y, w, h = (item.value() for item in self.dimensions)
        return replace(self.reference, x=x, y=y, w=w, h=h, opacity=self.opacity.value() / 100,
                       above=self.above.isChecked(), visible=self.visible.isChecked())


class ExportDialog(QDialog):
    def __init__(self, parent, kind, map_name, landscape, has_reference, dimensions, *, world=False, grid=True):
        super().__init__(parent)
        self.setObjectName("exportDialog")
        self.setWindowTitle("Export mapy")
        form = dialog_layout(self, "Vybrat soubor…")
        self.kind = QComboBox()
        self.kind.addItem("PDF – tiskový dokument", "pdf")
        self.kind.addItem("PNG – obrázek", "png")
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        form.addRow("Formát", self.kind)
        scope = QLabel(map_name)
        scope.setObjectName("exportScope")
        scope.setWordWrap(True)
        scope.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        form.addRow("Mapa", scope)
        self.size_preset = QComboBox()
        self.size_preset.setObjectName("exportSize")
        self.size_preset.setAccessibleName("Velikost")
        size_note = subtle()
        size_note.setObjectName("exportDimensions")
        size_note.setWordWrap(False)
        size_field = QHBoxLayout()
        size_field.setSpacing(10)
        size_field.addWidget(self.size_preset, 1)
        size_field.addWidget(size_note)
        form.addRow("Velikost", size_field)
        self.orientation = QComboBox()
        self.orientation.addItem("Na šířku", True)
        self.orientation.addItem("Na výšku", False)
        self.orientation.setCurrentIndex(0 if landscape else 1)
        form.addRow("Orientace", self.orientation)
        self.audience = QComboBox()
        self.audience.addItem("Pro vypravěče / DM", False)
        self.audience.addItem("Pro hráče · skrýt tajné značky", True)
        form.addRow("Varianta mapy", self.audience)
        self.grid = QCheckBox("Čtvercová mřížka" if world else "Čtvercová mřížka a souřadnice")
        self.grid.setChecked(grid)
        self.legend = QCheckBox("Názvy oblastí a značky vstupů" if world else "Legenda prvků a průchodů")
        self.legend.setChecked(True)
        self.reference = QCheckBox("Zahrnout viditelnou předlohu")
        self.reference.setEnabled(has_reference)
        for field in (self.grid, self.legend, self.reference):
            form.addRow(field)

        def update_dimensions():
            kind = self.kind.currentData()
            preset = self.size_preset.currentData()
            fixed_paper = kind == "pdf" and preset != "auto"
            self.orientation.setVisible(fixed_paper)
            form.labelForField(self.orientation).setVisible(fixed_paper)
            size = dimensions(kind, preset, self.orientation.currentData(),
                              self.legend.isChecked(), self.audience.currentData())
            size_note.setText(f"{size.width():,} × {size.height():,} px".replace(",", "\u00a0"))
            size_note.setToolTip(f"Přepočet velikosti stránky při {PDF_DPI} dpi." if kind == "pdf" else "")
            self.adjustSize()

        def update_presets():
            self.size_preset.blockSignals(True)
            self.size_preset.clear()
            self.size_preset.addItem("Výchozí", "auto")
            if self.kind.currentData() == "pdf":
                for page in ("A4", "A3"):
                    self.size_preset.addItem(page, page)
            else:
                for pixels in (24, 48, 96):
                    self.size_preset.addItem(f"{pixels} px na pole", pixels)
            self.size_preset.blockSignals(False)
            update_dimensions()

        self.kind.currentIndexChanged.connect(update_presets)
        self.size_preset.currentIndexChanged.connect(update_dimensions)
        self.orientation.currentIndexChanged.connect(update_dimensions)
        self.legend.toggled.connect(update_dimensions)
        self.audience.currentIndexChanged.connect(update_dimensions)
        update_presets()

    def options(self):
        return dict(grid=self.grid.isChecked(), legend=self.legend.isChecked(),
                    reference=self.reference.isChecked(), player=self.audience.currentData())


def detail_label(text, name=""):
    label = QLabel()
    label.setObjectName(name)
    label.setTextFormat(Qt.PlainText)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    label.setText(text)
    return label


class CreatureDialog(QDialog):
    def __init__(self, card, encounter_cr="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{card.name} — karta nestvůry")
        self.resize(640, 660)
        self.setMinimumSize(375, 380)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setObjectName("creatureCard")
        browser.setOpenLinks(False)
        browser.setHtml(self.stat_block(card, encounter_cr))
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Zavřít")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def stat_block(card, encounter_cr):
        source = {"UTC": "Šablona NWN", "GIT": "Instanční údaje NWN"}.get(card.source, "Seznam nestvůr střetu NWN")
        metadata = " · ".join(v for v in (source, card.resref) if v)
        cr = []
        if encounter_cr:
            cr.append(f"CR ve střetu {encounter_cr}")
        if card.cr and card.cr != encounter_cr:
            cr.append(f"CR v datech {card.cr}")
        parts = [
            '<body style="color:#e5e6db;background-color:#202622;">',
            f'<h1 style="color:#f4dfb1;font-family:serif;">{escape(card.name)}</h1>',
            f'<p style="color:#adb8aa;">{escape(metadata)}</p>',
        ]
        if cr:
            parts.append(f'<p style="color:#f4dfb1;"><b>{escape(" · ".join(cr))}</b></p>')
        if any(score is not None for score in card.abilities):
            cells = "".join(
                f'<td width="16%" align="center"><b>{label}</b><br/>{score if score is not None else "—"}</td>'
                for label, score in zip(("SÍL", "OBR", "ODL", "INT", "MDR", "CHA"), card.abilities))
            parts.append(f'<table width="100%" cellpadding="8" bgcolor="#303a30"><tr>{cells}</tr></table>')
        for label, value in card.stats:
            parts.append(f'<p><b style="color:#f4dfb1;">{escape(label)}</b><br/>{escape(value)}</p>')
        if card.description:
            description = escape(card.description).replace("\n", "<br/>")
            parts.append(f'<h3>Popis ve hře</h3><p>{description}</p>')
        if not card.source:
            parts.append("<p>Podrobná šablona UTC je nedostupná.</p>")
        parts.append("</body>")
        return "".join(parts)


class MarkSummary(QFrame):
    go_to = pyqtSignal(object)

    def __init__(self, project, area, key, item, *, browsing):
        super().__init__()
        self.setObjectName("markSummary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 10)
        layout.setSpacing(6)
        asset = ASSETS[item.style]
        notes = item.note.splitlines()
        card = item.nwn_detail
        encounter = isinstance(card, EncounterCard) or "Střet NWN" in notes
        kind = {"link": "Přechod", "monster": "Střet NWN" if encounter else "Nestvůra"}.get(asset.group, GROUPS[asset.group])
        coordinate = cell_name(min(key[0], area.cols - 1), min(key[1], area.rows - 1))
        layout.addWidget(subtle(f"{kind} · {coordinate}"))
        layout.addWidget(detail_label(item.name or (kind if asset.group == "monster" else asset.name), "objectTitle"))
        if asset.group == "link":
            self.add_destination(layout, project, item, notes, browsing)
        if isinstance(card, EncounterCard):
            self.add_encounter(layout, card)
        elif asset.group == "monster":
            self.add_monster(layout, item, notes)
        if browsing and kind not in {"Přechod", "Střet NWN"}:
            if item.dc is not None:
                layout.addWidget(detail_label(f"TO / DC {item.dc}"))
            if item.note:
                layout.addWidget(detail_label(item.note))

    def add_monster(self, layout, item, notes):
        card = item.nwn_detail
        cr = item.cr or (card.cr if card else "")
        if cr:
            layout.addWidget(detail_label(f"CR {cr}", "markChallenge"))
        if card:
            button = labeled_button("Karta nestvůry", "openCreatureCard", "monster")
            button.clicked.connect(lambda: CreatureDialog(card, parent=self).exec_())
            layout.addWidget(button)
        else:
            for line in notes:
                title, _, value = line.partition(" · ")
                if title == "Zásoba nestvůr" and value:
                    layout.addWidget(heading("MOŽNÉ NESTVŮRY"))
                    layout.addWidget(detail_label(value.replace(", ", "\n"), "legacyEncounterPool"))

    def add_encounter(self, layout, card):
        facts = []
        if card.difficulty:
            facts.append(card.difficulty)
        if card.recommended is not None:
            facts.append(f"Doporučený počet {card.recommended}")
        if card.maximum is not None:
            facts.append(f"Nejvýše {card.maximum} najednou")
        if card.active is False:
            facts.append("Neaktivní střet")
        if card.spawn_points:
            facts.append(f"Body výskytu {card.spawn_points}")
        if facts:
            layout.addWidget(detail_label(" · ".join(facts), "encounterFacts"))
        layout.addWidget(heading("MOŽNÉ NESTVŮRY"))
        if not card.entries:
            layout.addWidget(subtle("Seznam v modulu je prázdný."))
        for entry in card.entries:
            creature = entry.creature or CreatureCard(entry.name, entry.resref)
            label = QLabel(f'<a href="card" style="color:#f4dfb1;">{escape(entry.name)}</a>')
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setObjectName("encounterCreature")
            label.setTextInteractionFlags(Qt.TextBrowserInteraction)
            label.setOpenExternalLinks(False)
            label.setToolTip(entry.resref)
            label.linkActivated.connect(lambda _, c=creature, cr=entry.cr: CreatureDialog(c, cr, self).exec_())
            layout.addWidget(label)
            cr = entry.cr or creature.cr
            details = ([f"CR {cr}"] if cr else []) + (["Nejvýše jedna této šablony"] if entry.single_spawn else [])
            if details:
                layout.addWidget(subtle(" · ".join(details)))

    def add_destination(self, layout, project, item, notes, browsing):
        target = item.target
        area = project.areas.get(target.area) if target else None
        if area:
            layout.addWidget(heading("CÍLOVÁ OBLAST"))
            layout.addWidget(detail_label(area.name, "transitionDestination"))
            layout.addWidget(subtle(f"Cílové pole {cell_name(target.x, target.y)}"))
            if browsing:
                jump = labeled_button("Přejít na cíl", "jumpSummary", "exit")
                jump.clicked.connect(lambda: self.go_to.emit(target))
                layout.addWidget(jump)
        else:
            tag = next((line for line in notes if line.startswith("Cílový tag ")), "")
            layout.addWidget(detail_label("Cíl se nepodařilo dohledat" if tag else "Cíl není nastavený", "transitionDestination"))
            if tag:
                layout.addWidget(detail_label(tag))


class MarkForm(QWidget):
    applied = pyqtSignal(object, bool)
    go_to = pyqtSignal(object)

    def __init__(self, project, area, key, item):
        super().__init__()
        self.project, self.area, self.key, self.item = project, area, key, item
        self.asset = ASSETS[item.style]
        self.setObjectName("markForm")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.name = text_input(item.name)
        self.name.setObjectName("markName")
        self.name.setPlaceholderText(self.asset.name)
        layout.addWidget(QLabel("Název"))
        layout.addWidget(self.name)

        self.orientation = None
        if self.asset.directional:
            self.orientation = direction_field(item.turns)
            self.orientation.setObjectName("markDirection")
            layout.addWidget(QLabel("Směr značky"))
            layout.addWidget(self.orientation)

        self.cr = self.dc = None
        if self.asset.group == "monster":
            self.cr = text_input(item.cr, 16)
            self.cr.setPlaceholderText("Např. 7 nebo 1/4")
            layout.addWidget(QLabel("CR · obtížnost střetu"))
            layout.addWidget(self.cr)
        if self.asset.group in {"trap", "door", "treasure"}:
            self.dc = space_spin(item.dc if item.dc is not None else -1, -1, 999)
            self.dc.setSpecialValueText("Bez hodnoty")
            layout.addWidget(QLabel("TO / DC"))
            layout.addWidget(self.dc)

        self.destination = None
        if self.asset.group == "link":
            self._link_fields(layout)

        self.note = QPlainTextEdit(item.note)
        self.note.setObjectName("markNote")
        self.note.setPlaceholderText("Poznámka k prvku")
        self.note.setFixedHeight(63)
        layout.addWidget(QLabel("Poznámka"))
        layout.addWidget(self.note)
        self.hidden = QCheckBox("Skrýt v exportu pro hráče")
        self.hidden.setChecked(item.hidden)
        layout.addWidget(self.hidden)
        self.apply_button = labeled_button("Použít změny", "applyMark", primary=True)
        self.apply_button.clicked.connect(self.apply)
        layout.addWidget(self.apply_button)

    def _link_fields(self, layout):
        self.destination = QComboBox()
        self.destination.setObjectName("linkArea")
        self.destination.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.destination.setMinimumContentsLength(9)
        self.destination.addItem("Bez cíle", None)
        for uid, area in self.project.areas.items():
            self.destination.addItem(area.name, uid)
        self.destination.setCurrentIndex(max(0, self.destination.findData(self.item.target.area if self.item.target else None)))
        layout.addWidget(QLabel("Cílová oblast"))
        layout.addWidget(self.destination)
        self.col = space_spin(1, 1, MAX_SIZE)
        self.row = space_spin(1, 1, MAX_SIZE)
        self.col.setObjectName("linkColumn")
        self.row.setObjectName("linkRow")
        self.col.setAccessibleName("Sloupec cílového pole")
        self.row.setAccessibleName("Řádek cílového pole")
        coords = QHBoxLayout()
        coords.setSpacing(7)
        coords.addWidget(self.col, 1)
        coords.addWidget(self.row, 1)
        layout.addLayout(coords)
        self.info = subtle()
        self.info.setObjectName("linkCoordinate")
        layout.addWidget(self.info)
        self.reciprocal = QCheckBox("Přidat zpětný průchod")
        self.reciprocal.setObjectName("reciprocalLink")
        self._pair_manual = False
        self.reciprocal.clicked.connect(self.remember_pair_choice)
        layout.addWidget(self.reciprocal)
        self.jump = labeled_button("Přejít na cíl", "jumpLink")
        self.jump.clicked.connect(lambda: self.go_to.emit(self.item.target))
        layout.addWidget(self.jump)
        self.jump.setVisible(self.item.target is not None)
        self.col.valueChanged.connect(self.show_coordinate)
        self.row.valueChanged.connect(self.show_coordinate)
        self.destination.currentIndexChanged.connect(self.update_destination)
        if self.orientation:
            self.orientation.currentIndexChanged.connect(self.direction_changed)
        self.update_destination()

    def direction_changed(self):
        if self.item.target is None:
            self.update_destination()

    def remember_pair_choice(self):
        self._pair_manual = True

    def update_destination(self):
        uid = self.destination.currentData()
        area = self.project.areas.get(uid)
        for field in (self.col, self.row, self.reciprocal):
            field.setEnabled(area is not None)
        if area is not None:
            self.col.setMaximum(area.cols)
            self.row.setMaximum(area.rows)
            target = self.item.target
            if target and target.area == uid:
                self.col.setValue(target.x + 1)
                self.row.setValue(target.y + 1)
            else:
                relative = (area.north - self.area.north) % 4
                direction = (self.orientation.currentIndex() + relative) % 4
                x, y = self.key[:2]
                if relative == 1:
                    x, y = self.area.rows - 1 - y, x
                elif relative == 2:
                    x, y = self.area.cols - 1 - x, self.area.rows - 1 - y
                elif relative == 3:
                    x, y = y, self.area.cols - 1 - x
                self.col.setValue({1: 1, 3: area.cols}.get(direction, min(x + 1, area.cols)))
                self.row.setValue({0: area.rows, 2: 1}.get(direction, min(y + 1, area.rows)))
        self.show_coordinate()

    def show_coordinate(self):
        target = self.target()
        area = self.project.areas.get(target.area) if target else None
        self.info.setText(f"Pole {cell_name(target.x, target.y)} · sloupec / řádek" if target else "Zvol oblast a cílové pole.")
        here = Target(self.area.uid, *self.key[:2])
        paired = target and any(mark.target == here for _, mark in area.markers_in(target.x, target.y, "link"))
        if not self._pair_manual:
            self.reciprocal.setChecked(bool(paired))

    def target(self):
        uid = self.destination.currentData()
        return Target(uid, self.col.value() - 1, self.row.value() - 1) if uid else None

    def apply(self):
        cr = self.cr.text().strip().replace(",", ".") if self.cr else ""
        try:
            if cr and not 0 <= Fraction(cr) <= 999:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            QMessageBox.information(self, "CR", "Zadej nezáporné číslo nebo zlomek, například 7 nebo 1/4.")
            return
        target = self.target() if self.destination else None
        item = replace(self.item, name=self.name.text().strip(), note=self.note.toPlainText().strip()[:4000],
                       cr=cr, dc=self.dc.value() if self.dc and self.dc.value() >= 0 else None,
                       hidden=self.hidden.isChecked(),
                       target=target, arrival_xy=self.item.arrival_xy if target == self.item.target else None,
                       turns=self.orientation.currentIndex() if self.orientation else 0)
        self.applied.emit(item, bool(self.destination and self.reciprocal.isChecked()))
