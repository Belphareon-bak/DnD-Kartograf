"""Volitelná místní pravidla pro vlastní moduly NWN."""
from dataclasses import dataclass
from functools import lru_cache
import json
from math import isfinite
import os
from pathlib import Path
import re

from document import ATLAS_LIMIT, MAX_SIZE


RESREF = re.compile(r"[a-z0-9_]+")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")
RULES_LIMIT = 1024 * 1024


def identifier(value, size=64):
    return isinstance(value, str) and len(value) <= size and bool(RESREF.fullmatch(value))


@dataclass(frozen=True)
class NwnOverlay:
    host: str
    area: str
    tileset: str
    entry_tag: str
    exit_tag: str
    empty_color: str


@dataclass(frozen=True)
class NwnRules:
    surface: frozenset
    placements: dict
    overlay: NwnOverlay | None
    transition_scripts: frozenset


EMPTY_RULES = NwnRules(frozenset(), {}, None, frozenset())


def names(raw, size=64):
    if not isinstance(raw, list) or any(not identifier(value, size) for value in raw):
        raise ValueError("Neplatné identifikátory v pravidlech NWN.")
    return frozenset(raw)


def placement(raw):
    x, y, turns, width, height = raw
    if (any(type(value) not in (int, float) or not isfinite(value) or abs(value) > ATLAS_LIMIT for value in (x, y))
            or type(turns) is not int or not 0 <= turns < 4
            or any(type(value) is not int or not 1 <= value <= MAX_SIZE for value in (width, height))):
        raise ValueError("Neplatné rozložení v pravidlech NWN.")
    return x, y, turns, width, height


def overlay(raw):
    if raw is None:
        return None
    rule = NwnOverlay(**raw)
    if (any(not identifier(value) for value in (rule.host, rule.area, rule.tileset, rule.entry_tag, rule.exit_tag))
            or rule.host == rule.area or not isinstance(rule.empty_color, str) or not COLOR.fullmatch(rule.empty_color)):
        raise ValueError("Neplatný společný půdorys v pravidlech NWN.")
    return rule


@lru_cache(maxsize=4)
def rules_at(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(RULES_LIMIT + 1)
        if len(raw) > RULES_LIMIT:
            return EMPTY_RULES
        data = json.loads(raw)
        placements = data.get("placements", {})
        if any(not identifier(uid) for uid in placements):
            return EMPTY_RULES
        return NwnRules(names(data.get("surface", [])),
                        {uid: placement(values) for uid, values in placements.items()},
                        overlay(data.get("overlay")), names(data.get("transition_scripts", []), 16))
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return EMPTY_RULES


def nwn_rules():
    path = os.environ.get("KARTOGRAF_NWN_RULES")
    return rules_at(Path(path).expanduser()) if path else EMPTY_RULES
