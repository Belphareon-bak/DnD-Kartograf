"""Údaje čtené z herních šablon UTC a z instancí GIT."""
from collections import Counter
from dataclasses import dataclass


ABILITY_FIELDS = ("Str", "Dex", "Con", "Int", "Wis", "Cha")
SPELL_LISTS = tuple(f"{kind}List{level}" for kind in ("Memorized", "Known") for level in range(10))
CREATURE_FIELDS = set(ABILITY_FIELDS + SPELL_LISTS) | set(
    "FirstName LastName Tag TemplateResRef ChallengeRating CRAdjust Race Subrace Description HitPoints MaxHitPoints "
    "NaturalAC fortbonus refbonus willbonus ClassList Class ClassLevel FeatList Feat SkillList Rank "
    "Spell SpecAbilityList SpellCasterLevel".split())
ENCOUNTER_FIELDS = set("LocalizedName Tag CreatureList ResRef CR SingleSpawn RecCreatures MaxCreatures "
                       "DifficultyIndex Active SpawnPointList TemplateResRef".split())


@dataclass(frozen=True)
class CreatureCard:
    name: str
    resref: str = ""
    cr: str = ""
    source: str = ""
    abilities: tuple[int | None, ...] = (None,) * len(ABILITY_FIELDS)
    stats: tuple[tuple[str, str], ...] = ()
    description: str = ""


@dataclass(frozen=True)
class EncounterEntry:
    resref: str
    cr: str
    single_spawn: bool
    creature: CreatureCard | None

    @property
    def name(self):
        return self.creature.name if self.creature else self.resref


@dataclass(frozen=True)
class EncounterCard:
    entries: tuple[EncounterEntry, ...]
    difficulty: str = ""
    recommended: int | None = None
    maximum: int | None = None
    active: bool | None = None
    spawn_points: int = 0


def stat_int(value, low=0, high=65535):
    return value if type(value) is int and low <= value <= high else None


def gff_rows(data, field):
    rows = data.get(field)
    return rows if isinstance(rows, list) else ()


def make_creature_card(resources, data, resref, cr, source):
    stats = []

    def add(label, value):
        if value is not None and value != "":
            stats.append((label, str(value)[:4000]))

    def name(table, index, column="Name"):
        return resources.table_name(table, index, column)

    race = name("racialtypes", data.get("Race"))
    subrace = data.get("Subrace")
    add("Rasa", " · ".join(v for v in (race, subrace) if isinstance(v, str) and v))
    class_rows = gff_rows(data, "ClassList")
    classes = [f"{name('classes', row['Class'])} {level}" for row in class_rows
               if stat_int(row.get("Class")) is not None and (level := stat_int(row.get("ClassLevel"), 1))]
    add("Povolání a úrovně", " · ".join(classes))
    hp, base_hp = (stat_int(data.get(field)) for field in ("MaxHitPoints", "HitPoints"))
    add("Maximální životy", hp)
    if base_hp != hp:
        add("Základní životy", base_hp)
    if (armor := stat_int(data.get("NaturalAC"))) is not None:
        add("Přirozený bonus k OČ", f"{armor:+d}")
    if adjustment := stat_int(data.get("CRAdjust"), -32768):
        add("Úprava CR", f"{adjustment:+d}")
    saves = [f"{label} {bonus:+d}" for label, field in (("Odolnost", "fortbonus"),
             ("Reflex", "refbonus"), ("Vůle", "willbonus"))
             if (bonus := stat_int(data.get(field), -32768, 32767)) is not None]
    add("Bonusy k záchranám", " · ".join(saves))
    skills = [f"{name('skills', index)} {rank}" for index, row in enumerate(gff_rows(data, "SkillList"))
              if (rank := stat_int(row.get("Rank"), 1))]
    add("Body dovedností", ", ".join(skills))
    feats = dict.fromkeys(name("feat", row.get("Feat"), "Feat") for row in gff_rows(data, "FeatList"))
    add("Odbornosti", ", ".join(filter(None, feats)))
    for kind, label in (("Memorized", "Připravená kouzla"), ("Known", "Známá kouzla")):
        spells = Counter(name("spells", row.get("Spell")) for cls in class_rows
                         for level in range(10) for row in gff_rows(cls, f"{kind}List{level}"))
        spells.pop("", None)
        add(label, ", ".join(f"{spell} ×{count}" if count > 1 else spell for spell, count in spells.items()))
    specials = dict.fromkeys(name("spells", row.get("Spell")) for row in gff_rows(data, "SpecAbilityList"))
    add("Zvláštní schopnosti", ", ".join(filter(None, specials)))
    return CreatureCard((resources.name(data) or resref or "Nestvůra")[:100], resref[:64], cr, source,
                        tuple(stat_int(data.get(field), 0, 255) for field in ABILITY_FIELDS),
                        tuple(stats), resources.text(data.get("Description"))[:8000])
