"""Minimapa jedné dlaždice z prostředku ImageMap2D."""
from struct import Struct

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt5.QtGui import QImage

from document import MAX_MINIMAP_SIDE

try:
    from PIL import Image
except ImportError:
    Image = None

TGA_HEADER = Struct("<BBBHHBHHHHBB")
DDS_HEADER = Struct("<4I")
DDS_COLORS = Struct("<HHI")
MAX_TEXTURE_SIDE = 4096
TEXTURE_SAMPLE_SIDE = 256
BLOCK_BYTES = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16}
BCN_CODEC = {b"DXT1": 1, b"DXT3": 2, b"DXT5": 3}


def unpack_rle(data, count, pixel_bytes):
    pixels = bytearray()
    at = 0
    while count:
        if at >= len(data):
            return None
        packet = data[at]
        length = (packet & 127) + 1
        repeat = bool(packet & 128)
        size = pixel_bytes * (1 if repeat else length)
        at += 1
        if length > count or at + size > len(data):
            return None
        group = data[at:at + size]
        pixels.extend(group * length if repeat else group)
        at += size
        count -= length
    return bytes(pixels)


def tga_image(raw, max_side=MAX_MINIMAP_SIDE):
    if len(raw) < TGA_HEADER.size:
        return QImage()
    comment, colormap, kind, _, _, _, _, _, width, height, depth, origin = TGA_HEADER.unpack_from(raw)
    if (colormap or kind not in (2, 10) or depth not in (24, 32)
            or not (0 < width <= max_side and 0 < height <= max_side)):
        return QImage()
    stride = width * (depth // 8)
    offset = TGA_HEADER.size + comment
    if kind == 10:
        pixels = unpack_rle(raw[offset:], width * height, depth // 8)
    else:
        pixels = raw[offset:offset + stride * height]
    if not pixels or len(pixels) != stride * height:
        return QImage()
    if depth == 24:
        image = QImage(pixels, width, height, stride, QImage.Format_BGR888)
    else:
        image = QImage(pixels, width, height, stride, QImage.Format_RGBA8888).rgbSwapped()
        if not origin & 15:
            image = image.convertToFormat(QImage.Format_RGB32)
    return image.mirrored(bool(origin & 16), (origin & 32) == 0)


def rgb565(value):
    red, green, blue = value >> 11, (value >> 5) & 63, value & 31
    return (red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)


def dds_block(data, offset, codec, opaque):
    color_at = offset if codec == b"DXT1" else offset + 8
    first, second, indices = DDS_COLORS.unpack_from(data, color_at)
    a, b = rgb565(first), rgb565(second)
    four_colors = first > second or codec != b"DXT1"
    if four_colors:
        colors = (a, b, tuple((2 * u + v) // 3 for u, v in zip(a, b)),
                  tuple((u + 2 * v) // 3 for u, v in zip(a, b)))
    else:
        colors = (a, b, tuple((u + v) // 2 for u, v in zip(a, b)), (0, 0, 0))

    if codec == b"DXT1":
        palette = tuple(bytes((*rgb, 255 if index < 3 or four_colors or opaque else 0))
                        for index, rgb in enumerate(colors))
        return b"".join(palette[(indices >> pixel) & 3] for pixel in range(0, 32, 2))

    if codec == b"DXT3":
        alpha_bits = int.from_bytes(data[offset:offset + 8], "little")
        alpha = tuple(((alpha_bits >> bit) & 15) * 17 for bit in range(0, 64, 4))
    else:
        first_alpha, second_alpha = data[offset], data[offset + 1]
        steps = 7 if first_alpha > second_alpha else 5
        alphas = (first_alpha, second_alpha, *(
            ((steps - index) * first_alpha + index * second_alpha) // steps for index in range(1, steps)))
        if steps == 5:
            alphas += (0, 255)
        alpha_bits = int.from_bytes(data[offset + 2:offset + 8], "little")
        alpha = tuple(alphas[(alpha_bits >> bit) & 7] for bit in range(0, 48, 3))
    return b"".join(bytes((*colors[(indices >> (2 * pixel)) & 3], value)) for pixel, value in enumerate(alpha))


def dds_image(data):
    """Čte komprimované textury DirectDraw i hlavičku BioWare v NWN."""
    if len(data) < 20:
        return QImage()
    opaque = False
    bioware = not data.startswith(b"DDS ")
    if not bioware:
        if len(data) < 128 or data[4:8] != b"|\0\0\0":
            return QImage()
        _, _, height, width = DDS_HEADER.unpack_from(data, 4)
        codec = data[84:88]
        offset = 128
    else:
        width, height, channels, length = DDS_HEADER.unpack_from(data)
        codec = {3: b"DXT1", 4: b"DXT5"}.get(channels)
        opaque = channels == 3
        offset = 20

    if codec not in BLOCK_BYTES or not (0 < width <= MAX_TEXTURE_SIDE and 0 < height <= MAX_TEXTURE_SIDE):
        return QImage()
    block_size = BLOCK_BYTES[codec]

    def level_size(w, h):
        return ((w + 3) // 4) * ((h + 3) // 4) * block_size

    if bioware and length != level_size(width, height):
        return QImage()
    while max(width, height) > TEXTURE_SAMPLE_SIDE:
        next_at = offset + level_size(width, height)
        half_width, half_height = max(width // 2, 1), max(height // 2, 1)
        if next_at + level_size(half_width, half_height) > len(data):
            break
        offset, width, height = next_at, half_width, half_height
    if offset + level_size(width, height) > len(data):
        return QImage()

    # Volitelný nativní dekodér urychlí zpracování velkých sad textur.
    if Image is not None:
        try:
            decoded = Image.frombytes("RGBA", (width, height), data[offset:offset + level_size(width, height)],
                                      "bcn", (BCN_CODEC[codec], codec.decode("ascii")))
        except (OSError, ValueError):
            pass
        else:
            if opaque:
                image = QImage(decoded.convert("RGB").tobytes(), width, height, width * 3, QImage.Format_RGB888)
            else:
                image = QImage(decoded.tobytes(), width, height, width * 4, QImage.Format_RGBA8888)
            return texture_sample(image.mirrored(False, True) if bioware else image)
    stride = width * 4
    pixels = bytearray(stride * height)
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            block = dds_block(data, offset, codec, opaque)
            offset += block_size
            line_bytes = min(width - x, 4) * 4
            for row in range(min(height - y, 4)):
                start = (y + row) * stride + x * 4
                pixels[start:start + line_bytes] = block[row * 16:row * 16 + line_bytes]
    image = QImage(bytes(pixels), width, height, stride, QImage.Format_RGBA8888)
    # Textury BioWare mají vůči TGA stejných herních materiálů řádky odspodu.
    return texture_sample(image.mirrored(False, True) if bioware else image)


def texture_sample(image):
    if max(image.width(), image.height()) > TEXTURE_SAMPLE_SIDE:
        return image.scaled(TEXTURE_SAMPLE_SIDE, TEXTURE_SAMPLE_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image.copy()


def tga_texture(data):
    return texture_sample(tga_image(data, MAX_TEXTURE_SIDE))


def image_png(image):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    return bytes(data) if not image.isNull() and image.save(buffer, "PNG") else b""


def minimap_png(raw):
    return image_png(tga_image(raw))
