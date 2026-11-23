"""Ukázkový horský průsmyk vytvořený z editorových dlaždic."""
from dataclasses import replace

from catalog import ASSETS
from document import Area, Mark, Project, Tile, TileLayout


def marker(area, x, y, style, **props):
    area.markers[x, y, ASSETS[style].group] = replace(Mark.from_asset(style), **props)


def mountain_project():
    area = Area("Horský průsmyk", 26, 17)
    trail = (9, 9, 9, 9, 9, 9, 8, 8, 8, 8, 7, 7, 7, 7, 7, 7, 8, 8, 8, 9, 9, 9, 9, 10, 10, 10)
    north = (3, 4, 4, 5, 5, 6, 5, 5, 4, 4, 5, 5, 5, 4, 4, 5, 6, 6, 6, 5, 5, 6, 6, 5, 4, 3)
    south = (14, 14, 14, 15, 15, 15, 14, 13, 13, 13, 12, 12, 11, 11, 12, 12, 12, 13, 13,
             14, 14, 14, 15, 14, 14, 13)
    for y in range(area.rows):
        for x in range(area.cols):
            style = "grass"
            height = 0
            if y <= north[x]:
                if y <= north[x] - 3:
                    style, height = "snow_ridge", 4
                else:
                    style, height = "mountain", 3
            elif y == north[x] + 1:
                style, height = "scree", 2
            elif y >= south[x] and x >= 9:
                style, height = ("mountain", 3) if y >= south[x] + 2 else ("rock", 2)
            elif y >= 12 and x < 9:
                channel = (13, 13, 14, 14, 15, 15, 16, 16, 16)[x]
                if abs(y - channel) < 2:
                    style, height = ("river", -2) if y == channel else ("scree", -1)
            elif x < 6 or (x >= 21 and y > 7):
                if (x * 2 + y) % 7 in (0, 1, 2):
                    style = "forest"
            area.tiles[x, y] = Tile(style)
            if height:
                area.heights[x, y] = height

    ramps = {4: (0, (0, 1, 1, 0)), 9: (1, (0, 1, 1, 0)),
             16: (1, (1, 0, 0, 1)), 21: (0, (1, 0, 0, 1))}
    for x, y in enumerate(trail):
        level = 2 if 10 <= x <= 15 else 1 if 5 <= x <= 20 else 0
        style = "paving" if 10 <= x <= 15 else "path"
        start = min(y, trail[x - 1]) if x else y
        stop = max(y, trail[x - 1]) if x else y
        for row in range(start, stop + 2):
            key = (x, row)
            tile = Tile(style)
            base = level
            if x in ramps:
                base, offsets = ramps[x]
                tile = Tile(style, layout=TileLayout((style,) * 4, offsets, ("",) * 4, -1))
            area.tiles[key] = tile
            if base:
                area.heights[key] = base
            else:
                area.heights.pop(key, None)

    marker(area, 12, 2, "label", name="Severní hřeben")
    marker(area, 12, 8, "label", name="Kamenné sedlo")
    marker(area, 5, 12, "label", name="Údolí potoka")
    marker(area, 19, 15, "label", name="Jižní sráz")
    return Project.single("Horský průsmyk", area)
