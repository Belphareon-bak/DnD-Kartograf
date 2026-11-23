"""Pohled shora z geometrie a textur textových modelů Aurora."""
from dataclasses import dataclass
from math import ceil, degrees, isfinite
import re
from struct import error as StructError
from sys import byteorder

from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QBrush, QColor, QImage, QMatrix4x4, QPainter, QPolygonF, QQuaternion, QTransform, QVector3D

from aurora_reader import text_bytes
from document import MAX_MINIMAP_SIDE, MAX_SIZE, ObjectPreview
from nwn_mdl import binary_nodes
from nwn_minimap import image_png

NAME = re.compile(r"[a-z0-9_-]{1,16}")
TABLE_TOKEN = re.compile(r'"([^"]*)"|(\S+)')
MAX_MODEL_BYTES = 4 * 1024 * 1024
MAX_VERTICES = 48_000


@dataclass(frozen=True)
class ProjectedFace:
    height: float
    points: tuple
    color: QColor
    material: str
    uv: tuple
    alpha: float


def appearance_models(raw):
    if not raw or not raw.startswith(b"2DA "):
        return {}
    column = None
    result = {}
    for line in text_bytes(raw).splitlines()[1:]:
        fields = [(a or b).lower() for a, b in TABLE_TOKEN.findall(line)]
        if not fields:
            continue
        if column is None:
            if "modelname" in fields:
                column = fields.index("modelname") + 1
        elif len(fields) > column and fields[0].isdigit() and NAME.fullmatch(fields[column]):
            result[int(fields[0])] = fields[column]
    return result


def components(words, length):
    values = tuple(map(float, words[:length]))
    if len(values) != length or not all(isfinite(v) and abs(v) <= 2500 for v in values):
        raise ValueError("Neplatný model NWN.")
    return values


def model_nodes(raw):
    source = filter(None, (line.partition("#")[0].lower().split()
                           for line in raw.decode("ascii", errors="replace").splitlines()))
    nodes = {}
    for words in source:
        if words[0] == "beginmodelgeom":
            break
    for words in source:
        if words[0] == "endmodelgeom":
            break
        if words[0] != "node" or len(words) != 3:
            continue
        _, kind, name = words
        properties = {}
        vertices, uv, faces = (), (), ()
        for row in source:
            key, *values = row
            if key == "endnode":
                break
            if key in {"verts", "tverts", "faces"}:
                count = int(values[0])
                if not 0 <= count <= MAX_VERTICES:
                    raise ValueError("Model NWN má příliš mnoho vrcholů.")
                records = [next(source) for _ in range(count)]
                if kind == "trimesh":
                    if key == "verts":
                        vertices = tuple(components(record, 3) for record in records)
                    elif key == "tverts":
                        uv = tuple(tuple(map(float, record[:2])) for record in records)
                    else:
                        faces = tuple(tuple(map(int, record[:7])) for record in records)
            elif key in {"parent", "position", "orientation", "scale", "render", "bitmap", "diffuse", "alpha"}:
                properties[key] = values
        # Potomek může mít stejné jméno jako pomocný uzel. Vazby rodičů používají první výskyt.
        node = name if name not in nodes else (name, len(nodes))
        nodes[node] = properties, vertices, uv, faces
    return nodes


def geometry(raw, texture_color):
    nodes = binary_nodes(raw) if raw.startswith(b"\0" * 4) else model_nodes(raw)
    transforms = {}

    def transform(name, depth=0):
        if name in transforms:
            return transforms[name]
        if name not in nodes:
            return QMatrix4x4()
        if depth >= 32:
            raise ValueError("Příliš hluboká hierarchie modelu NWN.")
        props, _, _, _ = nodes[name]
        matrix = QMatrix4x4(transform(props.get("parent", ["null"])[0], depth + 1))
        matrix.translate(*components(props.get("position", (0, 0, 0)), 3))
        if quaternion := props.get("quaternion"):
            x, y, z, w = quaternion
            matrix.rotate(QQuaternion(w, x, y, z).normalized())
        elif orientation := props.get("orientation"):
            x, y, z, angle = components(orientation, 4)
            if angle:
                matrix.rotate(degrees(angle), x, y, z)
        if "scale" in props:
            scale, = components(props["scale"], 1)
            matrix.scale(scale, scale, scale)
        transforms[name] = matrix
        return matrix

    triangles = []
    for name, (props, vertices, uv, faces) in nodes.items():
        if not faces or props.get("render") == ["0"]:
            continue
        alpha, = components(props.get("alpha", (1,)), 1)
        alpha = max(0, min(1, alpha))
        material = props.get("bitmap", ["null"])[0]
        texture = texture_color(material)
        opacity = round(texture.alpha() * alpha)
        if alpha <= 0:
            continue
        diffuse = props.get("diffuse", ())
        tint = components((*diffuse, 1, 1, 1), 3)
        rgb = [round(channel * (.65 + .35 * max(0, min(1, value))))
               for channel, value in zip(texture.getRgb()[:3], tint)]
        color = QColor(*rgb, opacity)
        matrix = transform(name)
        if not matrix.isIdentity():
            mapped = (matrix.map(QVector3D(*point)) for point in vertices)
            vertices = [(point.x(), point.y(), point.z()) for point in mapped]
        for face in faces:
            indices = face[:3]
            if not all(0 <= index < len(vertices) for index in indices):
                raise ValueError("Model NWN odkazuje na chybějící vrchol.")
            a, b, c = (vertices[index] for index in indices)
            area = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
            if abs(area) > .0001:
                corners = face[4:7]
                coords = tuple(uv[index] for index in corners) if len(corners) == 3 and all(
                    0 <= index < len(uv) for index in corners) else ()
                if any(len(point) != 2 or any(not isfinite(value) or abs(value) > 1e6 for value in point)
                       for point in coords):
                    coords = ()
                triangles.append(ProjectedFace(a[2] + b[2] + c[2], tuple((x, -y) for x, y, _ in (a, b, c)),
                                               color, material, coords, alpha))
    return triangles


def model_faces(raw, texture_color, *, compiled=False):
    if not raw or len(raw) > MAX_MODEL_BYTES:
        return ()
    if b"\0" in raw[:160] and not (compiled and raw.startswith(b"\0" * 4)):
        return ()
    try:
        return geometry(raw, texture_color)
    except (ValueError, IndexError, StopIteration, OverflowError, StructError):
        return ()


def texture_brush(image, uv, points):
    u, v = (uv[0][0] % 1) * image.width(), (-uv[0][1] % 1) * image.height()
    du1, dv1 = (uv[1][0] - uv[0][0]) * image.width(), (uv[0][1] - uv[1][1]) * image.height()
    du2, dv2 = (uv[2][0] - uv[0][0]) * image.width(), (uv[0][1] - uv[2][1]) * image.height()
    determinant = du1 * dv2 - du2 * dv1
    if abs(determinant) < 1e-6:
        return None
    first, second = points[1] - points[0], points[2] - points[0]
    across = (first * dv2 - second * dv1) / determinant
    down = (second * du1 - first * du2) / determinant
    origin = points[0] - across * u - down * v
    brush = QBrush(image)
    brush.setTransform(QTransform(across.x(), across.y(), down.x(), down.y(), origin.x(), origin.y()))
    return brush


def render_faces(faces, left, top, width, height, scale, texture_image):
    image = QImage(min(MAX_MINIMAP_SIDE, ceil(width * scale)), min(MAX_MINIMAP_SIDE, ceil(height * scale)),
                   QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.setPen(Qt.NoPen)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for face in sorted(faces, key=lambda item: item.height):
            points = [QPointF((x - left) * scale, (y - top) * scale) for x, y in face.points]
            texture = texture_image(face.material) if texture_image and face.uv else None
            brush = texture_brush(texture, face.uv, points) if texture and not texture.isNull() else None
            painter.setOpacity(face.alpha if brush else 1)
            painter.setBrush(brush or QBrush(face.color))
            painter.drawPolygon(QPolygonF(points))
    finally:
        painter.end()
    return image


def tile_preview(raw, side, texture_color, texture_image):
    faces = model_faces(raw, texture_color, compiled=True)
    if not faces:
        return b""
    image = render_faces(faces, -5, -5, 10, 10, side / 10, texture_image)
    alpha = 3 if byteorder == "little" else 0
    if not any(image.constBits().asstring(image.sizeInBytes())[alpha::4]):
        return b""
    return image_png(image)


def model_preview(raw, texture_color, texture_image=None):
    faces = model_faces(raw, texture_color)
    if not faces:
        return None
    materials = {face.material for face in faces}
    if texture_image and "null" not in materials and all(texture_image(name).isNull() for name in materials):
        return None
    xs, ys = zip(*(point for face in faces for point in face.points))
    left, top = min(xs), min(ys)
    width, height = max(xs) - left, max(ys) - top
    if not (0 < width <= 200 and 0 < height <= 200
            and abs(left) <= MAX_SIZE * 10 and abs(top) <= MAX_SIZE * 10):
        return None
    scale = min(MAX_MINIMAP_SIDE / max(width, height), 32)
    image = render_faces(faces, left, top, width, height, scale, texture_image)
    return ObjectPreview(tuple(value / 10 for value in (left, top, width, height)), image_png(image))
