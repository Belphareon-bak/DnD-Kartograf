"""Statická geometrie binárních modelů NWN podle formátu NWN1MDL."""
from math import isfinite
from struct import Struct


# Struktury a druhy ovladačů popisuje xoreos-docs/templates/NWN1MDL.bt.
FILE_HEADER = Struct("<3I")
ARRAY = Struct("<3I")
WORD = Struct("<I")
CONTROLLER = Struct("<IHHHBB")
VERTEX = Struct("<3f")
UV = Struct("<2f")
FACE = Struct("<26x3H")
VERTEX_INFO = Struct("<iHHi")
NODE_SIZE = 112
MESH_SIZE = 512
MAX_NODES = 4096
MAX_ELEMENTS = 48_000
HAS_LIGHT, HAS_EMITTER, HAS_REFERENCE, HAS_MESH, HAS_AABB = 2, 4, 16, 32, 512
TRANSFORMS = {8: ("position", 3), 20: ("quaternion", 4), 36: ("scale", 1), 128: ("alpha", 1)}


def span(data, offset, length):
    if not 0 <= offset <= len(data) - length:
        raise ValueError("Část modelu NWN přesahuje soubor.")
    return data[offset:offset + length]


def records(data, offset, count, item):
    if not 0 <= count <= MAX_ELEMENTS:
        raise ValueError("Příliš velká tabulka modelu NWN.")
    if not count:
        return iter(())
    return item.iter_unpack(span(data, offset, count * item.size))


def coordinates(data, offset, count, item):
    values = tuple(records(data, offset, count, item))
    if not all(isfinite(value) and abs(value) <= 2500 for point in values for value in point):
        raise ValueError("Neplatné souřadnice modelu NWN.")
    return values


def node_controllers(core, node):
    keys_at, key_count, _ = ARRAY.unpack_from(core, node + 84)
    data_at, data_count, _ = ARRAY.unpack_from(core, node + 96)
    props = {}
    for kind, rows, _, start, columns, _ in records(core, keys_at, key_count, CONTROLLER):
        spec = TRANSFORMS.get(kind)
        if spec is None or not 0 < rows < 0xffff:
            continue
        name, size = spec
        if columns & 15 != size or start + size > data_count:
            continue
        component = Struct(f"<{size}f")
        props[name], = coordinates(core, data_at + start * 4, 1, component)
    return props


def mesh_geometry(core, raw, mesh, props):
    span(core, mesh, MESH_SIZE)
    if WORD.unpack_from(core, mesh + 108)[0] != 1:
        return (), (), ()
    faces_at, face_count, _ = ARRAY.unpack_from(core, mesh + 8)
    vertices_at, count, _, uv_at = VERTEX_INFO.unpack_from(core, mesh + 444)
    if not count or not face_count:
        return (), (), ()
    bitmap = bytes(span(core, mesh + 120, 64)).split(b"\0", 1)[0].decode("ascii", errors="ignore").lower()
    props["bitmap"] = (bitmap or "null",)
    props["diffuse"], = coordinates(core, mesh + 60, 1, VERTEX)
    vertices = coordinates(raw, vertices_at, count, VERTEX)
    uv = tuple(records(raw, uv_at, count, UV)) if uv_at != -1 else ()
    # U kompilovaného modelu mají vrcholy i jejich UV společný index.
    faces = tuple((*indices, 0, *indices) for indices in records(core, faces_at, face_count, FACE))
    return vertices, uv, faces


def binary_nodes(data):
    kind, core_size, raw_size = FILE_HEADER.unpack_from(data)
    if kind or core_size < 112:
        raise ValueError("Neplatná hlavička binárního modelu NWN.")
    data = memoryview(data)
    core = span(data, FILE_HEADER.size, core_size)
    raw = span(data, FILE_HEADER.size + core_size, raw_size)
    root, declared_nodes = Struct("<2I").unpack_from(core, 72)
    if not 0 < declared_nodes <= MAX_NODES:
        raise ValueError("Neplatný počet uzlů modelu NWN.")
    pending = [(root, None)]
    nodes = {}
    while pending:
        offset, parent = pending.pop()
        if offset in nodes or len(nodes) >= MAX_NODES:
            raise ValueError("Neplatný strom modelu NWN.")
        span(core, offset, NODE_SIZE)
        flags, = WORD.unpack_from(core, offset + 108)
        children_at, child_count, _ = ARRAY.unpack_from(core, offset + 72)
        if child_count > MAX_NODES:
            raise ValueError("Příliš mnoho potomků uzlu modelu NWN.")
        props = node_controllers(core, offset)
        if parent is not None:
            props["parent"] = (parent,)
        vertices, uv, faces = (), (), ()
        if flags & HAS_MESH and not flags & HAS_AABB:
            mesh = offset + NODE_SIZE
            mesh += sum(length for kind, length in ((HAS_LIGHT, 92), (HAS_EMITTER, 216), (HAS_REFERENCE, 68))
                        if flags & kind)
            vertices, uv, faces = mesh_geometry(core, raw, mesh, props)
        nodes[offset] = props, vertices, uv, faces
        children = [child for child, in records(core, children_at, child_count, WORD)]
        pending.extend((child, offset) for child in reversed(children))
    return nodes
