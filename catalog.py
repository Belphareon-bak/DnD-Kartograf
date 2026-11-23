"""Paleta mapových prvků a výchozí značky."""
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Asset:
    key: str
    name: str
    group: str
    section: str
    layer: str
    color: str
    glyph: str
    cr: str = ""
    dm: bool = False
    directional: bool = False


GROUPS = {
    "outdoor": "Exteriéry",
    "relief": "Reliéf",
    "indoor": "Interiéry",
    "door": "Dveře",
    "link": "Průchody mezi oblastmi",
    "monster": "Monstra",
    "treasure": "Poklady",
    "trap": "Pasti",
    "ore": "Ruda",
    "label": "Popisky a orientace",
}

TERRAINS = (
    ("grass", "Tráva", "outdoor", "Terén", "#78845c", "grass"),
    ("path", "Ušlapaná cesta", "outdoor", "Terén", "#aa895e", "earth"),
    ("forest", "Les", "outdoor", "Terén", "#546b4d", "forest"),
    ("sand", "Písek", "outdoor", "Terén", "#d5bb85", "sand"),
    ("snow", "Sníh", "outdoor", "Terén", "#dce5df", "ice"),
    ("mud", "Bahno", "outdoor", "Terén", "#86765a", "earth"),
    ("mountain", "Horský masiv", "outdoor", "Hory a skály", "#938a7e", "mountain"),
    ("scree", "Horská suť", "outdoor", "Hory a skály", "#a49782", "scree"),
    ("snow_ridge", "Zasněžený hřeben", "outdoor", "Hory a skály", "#caccc2", "mountain"),
    ("rock", "Skála", "outdoor", "Hory a skály", "#8d8779", "rock"),
    ("cave", "Jeskynní podlaha", "outdoor", "Jeskyně a propasti", "#a7977d", "cave"),
    ("chasm", "Propast", "outdoor", "Jeskyně a propasti", "#3a3f47", "chasm"),
    ("lava", "Láva", "outdoor", "Jeskyně a propasti", "#ae6245", "lava"),
    ("river", "Řeka a potok", "outdoor", "Voda", "#527e8b", "water"),
    ("shallows", "Mělčina", "outdoor", "Voda", "#7da59b", "water"),
    ("lake", "Hluboká voda", "outdoor", "Voda", "#3e6173", "water"),
    ("bridge", "Dřevěný most", "outdoor", "Mosty a stavby", "#aa845b", "bridge"),
    ("stone_bridge", "Kamenný most", "outdoor", "Mosty a stavby", "#bab1a0", "stone_bridge"),
    ("paving", "Dlážděná cesta", "outdoor", "Mosty a stavby", "#c3b7a0", "stone"),
    ("roof", "Střecha a zástavba", "outdoor", "Mosty a stavby", "#986d56", "roof"),
    ("flagstone", "Kamenné desky", "indoor", "Podlahy", "#cdc0a8", "stone"),
    ("marble", "Mramor", "indoor", "Podlahy", "#e3d8be", "marble"),
    ("wood", "Dřevěná podlaha", "indoor", "Podlahy", "#b9906d", "wood"),
    ("brick", "Cihlová podlaha", "indoor", "Podlahy", "#a87964", "brick"),
    ("mosaic", "Mozaika", "indoor", "Podlahy", "#8d9b91", "mosaic"),
    ("dirt", "Udusaná hlína", "indoor", "Podlahy", "#a48f6c", "earth"),
    ("stairs", "Schodiště", "indoor", "Konstrukce", "#cabba2", "stairs"),
    ("grate", "Podlahová mříž", "indoor", "Konstrukce", "#838784", "grate"),
    ("solid", "Masivní zdivo", "indoor", "Stěny", "#534b42", "solid"),
)

RELIEF = {
    "raise_ground": 1,
    "lower_ground": -1,
    "level_ground": 0,
}

EDGES = (
    ("wall", "Kamenná stěna", "indoor", "Stěny", "#453b37", "wall", False),
    ("partition", "Dřevěná příčka", "indoor", "Stěny", "#805f45", "wall", False),
    ("low_wall", "Zídka", "indoor", "Stěny", "#7b7160", "low_wall", False),
    ("door", "Dřevěné dveře", "door", "Běžné dveře", "#81553b", "door", False),
    ("iron_door", "Kované dveře", "door", "Běžné dveře", "#4e5959", "door", False),
    ("arch", "Otevřený oblouk", "door", "Běžné dveře", "#82684c", "arch", False),
    ("locked", "Zamčené dveře", "door", "Zabezpečené", "#8e5c39", "locked", False),
    ("secret", "Tajné dveře", "door", "Zabezpečené", "#654b6a", "secret", True),
    ("portcullis", "Padací mříž", "door", "Zabezpečené", "#4e5959", "grate", False),
)

MARKS = (
    ("door_point", "Dveře uvnitř pole", "door", "Podle polohy v NWN", "#81553b", "door", False, False),
    ("locked_point", "Zamčené dveře uvnitř pole", "door", "Podle polohy v NWN", "#8e5c39", "locked", False, False),
    ("exit", "Přechod", "link", "Pěší a svislé", "#317475", "arrow", False, True),
    ("up", "Schody nahoru", "link", "Pěší a svislé", "#317475", "stairs", False, True),
    ("down", "Schody dolů", "link", "Pěší a svislé", "#475f86", "stairs_down", False, True),
    ("portal", "Portál", "link", "Brány a portály", "#655581", "portal", False, True),
    ("secret_link", "Skrytý průchod", "link", "Brány a portály", "#655581", "secret", True, True),
    ("monster", "Obecná značka", "monster", "NWN a vlastní", "#8e4b50", "monster", True, False),
    ("chest", "Truhla", "treasure", "Kořist", "#a36e33", "chest", True, False),
    ("loot", "Hromádka kořisti", "treasure", "Kořist", "#a36e33", "coins", True, False),
    ("relic", "Relikvie", "treasure", "Zvláštní nálezy", "#a36e33", "gem", True, False),
    ("cache", "Skrytá skrýš", "treasure", "Zvláštní nálezy", "#a36e33", "secret", True, False),
    ("plate", "Nášlapná deska", "trap", "Mechanické", "#a35c43", "trap", True, False),
    ("pit", "Propadlo", "trap", "Mechanické", "#a35c43", "pit", True, False),
    ("dart", "Šipky", "trap", "Mechanické", "#a35c43", "arrow", True, True),
    ("rune", "Magická runa", "trap", "Magické", "#914e74", "rune", True, False),
    ("beam", "Výboj", "trap", "Magické", "#914e74", "bolt", True, True),
    ("iron", "Železná ruda", "ore", "Běžná ložiska", "#596975", "ore", True, False),
    ("copper", "Měděná ruda", "ore", "Běžná ložiska", "#9c654a", "ore", True, False),
    ("silver", "Stříbrná ruda", "ore", "Vzácná ložiska", "#6d748a", "ore", True, False),
    ("gold", "Zlatá ruda", "ore", "Vzácná ložiska", "#9f7638", "ore", True, False),
    ("crystal", "Krystaly", "ore", "Vzácná ložiska", "#596d93", "gem", True, False),
    ("label", "Text v mapě", "label", "Text a body", "#4c5241", "text", False, False),
    ("point", "Orientační bod", "label", "Text a body", "#4c6559", "pin", False, False),
)

# Názvy a CR z D&D System Reference Document 5.1, Wizards of the Coast LLC, CC BY 4.0.
# https://media.dndbeyond.com/compendium-images/srd/5.1/SRD_CC_v5.1.pdf
MONSTERS = (
    ("bat", "Netopýr", "Zvířata", "0"),
    ("rat", "Krysa", "Zvířata", "0"),
    ("wolf", "Vlk", "Zvířata", "1/4"),
    ("bear", "Medvěd hnědý", "Zvířata", "1"),
    ("spider", "Obří pavouk", "Zvířata", "1"),
    ("bandit", "Bandita", "Humanoidi", "1/8"),
    ("goblin", "Goblin", "Humanoidi", "1/4"),
    ("orc", "Ork", "Humanoidi", "1/2"),
    ("bugbear", "Bugbear", "Humanoidi", "1"),
    ("ogre", "Zlobr", "Obři", "2"),
    ("troll", "Troll", "Obři", "5"),
    ("hill_giant", "Pahorkový obr", "Obři", "5"),
    ("skeleton", "Kostlivec", "Nemrtví", "1/4"),
    ("zombie", "Zombie", "Nemrtví", "1/4"),
    ("ghoul", "Ghúl", "Nemrtví", "1"),
    ("wight", "Wight", "Nemrtví", "3"),
    ("wraith", "Přízrak", "Nemrtví", "5"),
    ("vampire", "Upír", "Nemrtví", "13"),
    ("magma_mephit", "Magmatický mefit", "Elementálové", "1/2"),
    ("air_elemental", "Vzdušný elementál", "Elementálové", "5"),
    ("fire_elemental", "Ohnivý elementál", "Elementálové", "5"),
    ("imp", "Imp", "Běsi", "1"),
    ("hell_hound", "Pekelný pes", "Běsi", "3"),
    ("erinyes", "Erinyes", "Běsi", "12"),
    ("pseudodragon", "Pseudodrak", "Draci", "1/4"),
    ("young_white", "Mladý bílý drak", "Draci", "6"),
    ("young_red", "Mladý rudý drak", "Draci", "10"),
    ("adult_red", "Dospělý rudý drak", "Draci", "17"),
    ("ancient_red", "Prastarý rudý drak", "Draci", "24"),
)

ASSETS = {}
for key, name, group, section, color, glyph in TERRAINS:
    ASSETS[key] = Asset(
        key, name, group, section, "tiles", color, glyph,
        directional=glyph in {"bridge", "stone_bridge", "stairs", "wood"},
    )
for key, name, color in (
    ("raise_ground", "Zvednout o 1 stupeň", "#777f56"),
    ("lower_ground", "Snížit o 1 stupeň", "#567988"),
    ("level_ground", "Srovnat na nulu", "#80756b"),
    ("slope_ground", "Svah o 1 stupeň", "#956d4f"),
):
    ASSETS[key] = Asset(key, name, "relief", "Výškové stupně", "heights", color, key,
                        directional=key == "slope_ground")
for key, name, group, section, color, glyph, dm in EDGES:
    ASSETS[key] = Asset(key, name, group, section, "edges", color, glyph, dm=dm)
for key, name, group, section, color, glyph, dm, directional in MARKS:
    ASSETS[key] = Asset(key, name, group, section, "markers", color, glyph, dm=dm, directional=directional)
for key, name, section, cr in sorted(MONSTERS, key=lambda row: (row[2], Fraction(row[3]), row[1])):
    key = "srd_" + key
    glyph = {"Nemrtví": "skull", "Draci": "dragon", "Zvířata": "paw",
             "Elementálové": "rune", "Běsi": "dragon"}.get(section, "monster")
    ASSETS[key] = Asset(key, name, "monster", "D&D 5e / " + section, "markers", "#8e4b50", glyph, cr, True)
