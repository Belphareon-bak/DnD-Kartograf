"""Čtecí část formátů Aurora pro import map. Archiv ani herní data se nemění."""

from dataclasses import dataclass
from pathlib import Path
import re
import struct


TGA, NSS, NCS, ARE, SET, IFO, GIT = 3, 2009, 2010, 2012, 2013, 2014, 2023
UTC, UTE = 2027, 2040
MDL, TWO_DA, DDS = 2002, 2017, 2033
MAX_RESOURCE_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 1_000_000
ERF_HEADER = struct.Struct("<4s4s9I")
ERF_KEY = struct.Struct("<16sIHH")
GFF_TRIPLE = struct.Struct("<III")
U32 = struct.Struct("<I")
TABLE_TOKEN = re.compile(r'"([^"]*)"|(\S+)')


class AuroraError(ValueError):
    pass


def read_span(stream, start, size):
    stream.seek(start)
    data = stream.read(size)
    if len(data) != size:
        raise AuroraError("Herní soubor je neúplný.")
    return data


def resref_bytes(data):
    return data.split(b"\0", 1)[0].decode("ascii").casefold()


def text_bytes(data, fallback="cp1250"):
    raw = data.rstrip(b"\0")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(fallback, errors="replace")


class ErfReader:
    """Čte adresář ERF V1.0. Obsah jednotlivých prostředků načítá až při použití."""

    def __init__(self, path, types):
        self.path = Path(path)
        size = self.path.stat().st_size
        with self.path.open("rb") as stream:
            header = ERF_HEADER.unpack(read_span(stream, 0, ERF_HEADER.size))
            kind, version, _, _, count, _, key_offset, resource_offset, *_ = header
            if kind not in {b"MOD ", b"HAK ", b"ERF "} or version != b"V1.0":
                raise AuroraError(f"{self.path.name} nemá podporovaný formát NWN ERF V1.0.")
            if count > MAX_DIRECTORY_ENTRIES or not (160 <= key_offset <= size - count * ERF_KEY.size
                                                      and 160 <= resource_offset <= size - count * 8):
                raise AuroraError(f"Poškozený adresář archivu {self.path.name}.")
            keys = read_span(stream, key_offset, count * ERF_KEY.size)
            entries = read_span(stream, resource_offset, count * 8)
        self.entries = {}
        for label, index, resource_type, _ in ERF_KEY.iter_unpack(keys):
            if resource_type not in types:
                continue
            if index >= count:
                raise AuroraError(f"Neplatný index archivu {self.path.name}.")
            start, length = struct.unpack_from("<II", entries, index * 8)
            if not 160 <= start <= size - length:
                raise AuroraError(f"Poškozený prostředek v archivu {self.path.name}.")
            self.entries[resref_bytes(label), resource_type] = (start, length)

    def read(self, name, kind):
        entry = self.entries.get((name.casefold(), kind))
        if entry is None:
            return None
        at, size = entry
        if size > MAX_RESOURCE_BYTES:
            raise AuroraError(f"Příliš velký prostředek {name} v {self.path.name}.")
        with self.path.open("rb") as stream:
            return read_span(stream, at, size)


@dataclass(frozen=True)
class Localized:
    strref: int
    variants: dict


class GffReader:
    """Dekóduje použité štítky GFF V3.2 se stejným výběrem v podstrukturách."""

    PRIMITIVES = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 8: "<f"}
    EXTENDED = {6: "<Q", 7: "<q", 9: "<d"}

    def __init__(self, raw, fields=None):
        self.raw = raw
        self.wanted = fields
        if len(raw) < 56 or raw[4:8] != b"V3.2":
            raise AuroraError("Záznam nesplňuje formát Aurora GFF V3.2.")
        st, sn, fd, fn, lb, ln, data, dn, idx, ix, lists, li = struct.unpack_from("<12I", raw, 8)
        self.structs = self.section(st, sn * 12)
        self.fields = self.section(fd, fn * 12)
        label_data = self.section(lb, ln * 16)
        self.labels = [bytes(label_data[i:i + 16]).split(b"\0", 1)[0].decode("ascii")
                       for i in range(0, len(label_data), 16)]
        self.data = self.section(data, dn)
        self.indices = self.section(idx, ix)
        self.lists = self.section(lists, li)
        self._records = {}
        self._path = set()

    def section(self, start, count):
        if not 0 <= start <= len(self.raw) - count:
            raise AuroraError("Tabulka GFF přesahuje konec záznamu.")
        return memoryview(self.raw)[start:start + count]

    @staticmethod
    def checked_slice(data, offset, length):
        if not 0 <= offset <= len(data) - length:
            raise AuroraError("Pole GFF přesahuje konec tabulky.")
        return data[offset:offset + length]

    def decode(self):
        try:
            return self.record(0)
        except (IndexError, struct.error) as error:
            raise AuroraError("Poškozený obsah záznamu GFF.") from error

    def record(self, index):
        if index in self._records:
            return self._records[index]
        if index in self._path or len(self._path) >= 32:
            raise AuroraError("Neplatné vnoření záznamu GFF.")
        _, source, count = GFF_TRIPLE.unpack_from(self.structs, index * 12)
        self._path.add(index)
        if count == 1:
            indices = (source,)
        elif count:
            indices = (row[0] for row in U32.iter_unpack(self.checked_slice(self.indices, source, count * 4)))
        else:
            indices = ()
        values = {}
        for field in indices:
            kind, label, value = GFF_TRIPLE.unpack_from(self.fields, field * 12)
            name = self.labels[label]
            if self.wanted is None or name in self.wanted:
                values[name] = self.value(kind, value)
        self._path.remove(index)
        self._records[index] = values
        return values

    def value(self, kind, offset):
        primitive = self.PRIMITIVES.get(kind)
        if primitive:
            return struct.unpack_from(primitive, U32.pack(offset))[0]
        extended = self.EXTENDED.get(kind)
        if extended:
            return struct.unpack_from(extended, self.data, offset)[0]
        if kind == 14:
            return self.record(offset)
        if kind == 15:
            count = U32.unpack_from(self.lists, offset)[0]
            entries = self.checked_slice(self.lists, offset + 4, count * 4)
            return [self.record(row[0]) for row in U32.iter_unpack(entries)]
        if kind == 12:
            length, strref, count = GFF_TRIPLE.unpack_from(self.data, offset)
            body = self.checked_slice(self.data, offset + 4, length)
            position = 8
            variants = {}
            for _ in range(count):
                code, chars = struct.unpack_from("<II", body, position)
                position += 8
                variants[code] = bytes(self.checked_slice(body, position, chars))
                position += chars
            return Localized(strref, variants)
        if kind in (10, 11, 13):
            prefix = 1 if kind == 11 else 4
            length = self.data[offset] if prefix == 1 else U32.unpack_from(self.data, offset)[0]
            raw = bytes(self.checked_slice(self.data, offset + prefix, length))
            return raw if kind == 13 else text_bytes(raw)
        return None


def read_gff(raw, fields=None):
    return GffReader(raw, fields).decode()


def table_columns(raw, *columns):
    if not raw or not raw.startswith(b"2DA "):
        return {}
    result = {}
    positions = None
    for line in text_bytes(raw).splitlines()[1:]:
        if not line.strip() or line.lstrip().casefold().startswith("default:"):
            continue
        fields = [b or a for a, b in TABLE_TOKEN.findall(line)]
        if positions is None:
            names = {field.casefold(): index for index, field in enumerate(fields, 1)}
            positions = tuple(names.get(label.casefold(), -1) for label in columns)
        elif fields and fields[0].isascii() and fields[0].isdecimal() and len(fields[0]) <= 9:
            result[int(fields[0])] = tuple(fields[index] if 0 <= index < len(fields) else "" for index in positions)
    return result


class TalkTable:
    def __init__(self, path, encoding):
        self.path, self.encoding = path, encoding
        with path.open("rb") as stream:
            magic, _, _, self.count, self.start = struct.unpack("<4s4sIII", read_span(stream, 0, 20))
        if magic != b"TLK " or not 20 + self.count * 40 <= self.start <= path.stat().st_size:
            raise AuroraError(f"Neplatná tabulka textů {path.name}.")

    def text(self, index):
        if not 0 <= index < self.count:
            return ""
        with self.path.open("rb") as stream:
            offset, length = struct.unpack("<II", read_span(stream, 20 + index * 40 + 28, 8))
            if length > MAX_RESOURCE_BYTES or self.start + offset + length > self.path.stat().st_size:
                raise AuroraError(f"Neplatný text v {self.path.name}.")
            return text_bytes(read_span(stream, self.start + offset, length), self.encoding)


class GameResources:
    def __init__(self, root, types):
        self.entries = {}
        self._offsets = {}
        for database in sorted((root / "data").glob("*.key")):
            raw = database.read_bytes()
            if raw[:8] != b"KEY V1  ":
                continue
            if len(raw) < 24:
                raise AuroraError(f"Neúplný index {database.name}.")
            count_bif, count_key, bif_at, key_at = struct.unpack_from("<4I", raw, 8)
            if not (count_bif <= MAX_DIRECTORY_ENTRIES and count_key <= MAX_DIRECTORY_ENTRIES
                    and 0 <= bif_at <= len(raw) - count_bif * 12 and 0 <= key_at <= len(raw) - count_key * 22):
                raise AuroraError(f"Neplatný index {database.name}.")
            archives = []
            for i in range(count_bif):
                _, offset, size, _ = struct.unpack_from("<IIHH", raw, bif_at + i * 12)
                name = bytes(GffReader.checked_slice(raw, offset, size)).rstrip(b"\0").decode("ascii")
                relative = Path(name.replace("\\", "/").lower())
                archives.append(root / relative if not relative.is_absolute() and ".." not in relative.parts else None)
            for i in range(count_key):
                label, kind, ident = struct.unpack_from("<16sHI", raw, key_at + i * 22)
                if kind in types and (part := ident >> 20) < len(archives) and archives[part] is not None:
                    self.entries[resref_bytes(label), kind] = (archives[part], ident & 0xfffff)

    def read(self, name, resource_type):
        entry = self.entries.get((name.casefold(), resource_type))
        if not entry:
            return None
        path, index = entry
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            if path not in self._offsets:
                magic, version, count, _, offset = struct.unpack("<4s4sIII", read_span(stream, 0, 20))
                if (magic, version) != (b"BIFF", b"V1  "):
                    raise AuroraError(f"Neplatný archiv {path.name}.")
                self._offsets[path] = (count, offset)
            count, locations = self._offsets[path]
            if index >= count:
                raise AuroraError(f"Chybějící prostředek {name} v {path.name}.")
            _, offset, length, kind = struct.unpack("<4I", read_span(stream, locations + index * 16, 16))
            if kind != resource_type or length > MAX_RESOURCE_BYTES:
                raise AuroraError(f"Neplatný prostředek {name}.")
            return read_span(stream, offset, length)
