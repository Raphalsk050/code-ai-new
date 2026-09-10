from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from code_ai.core.errors import ToolExecutionError

ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"

_RES_STRING_POOL_TYPE = 0x0001
_RES_XML_TYPE = 0x0003
_RES_XML_START_NAMESPACE = 0x0100
_RES_XML_END_NAMESPACE = 0x0101
_RES_XML_START_ELEMENT = 0x0102
_RES_XML_END_ELEMENT = 0x0103
_RES_XML_CDATA = 0x0104
_RES_XML_RESOURCE_MAP = 0x0180

_UTF8_FLAG = 0x0100

# Res_value data types (frameworks/base ResourceTypes.h).
TYPE_NULL = 0x00
TYPE_REFERENCE = 0x01
TYPE_ATTRIBUTE = 0x02
TYPE_STRING = 0x03
TYPE_FLOAT = 0x04
TYPE_DIMENSION = 0x05
TYPE_FRACTION = 0x06
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12

# Attribute names are usually still in the string pool, but AAPT2 is free to
# leave them empty and identify an attribute by resource id alone. These are the
# framework ids this analysis reads; anything unmapped degrades to ``attr_0x...``
# rather than being silently dropped.
ANDROID_ATTRS: dict[int, str] = {
    0x01010000: "theme",
    0x01010001: "label",
    0x01010002: "icon",
    0x01010003: "name",
    0x01010006: "permission",
    0x01010007: "readPermission",
    0x01010008: "writePermission",
    0x01010009: "protectionLevel",
    0x0101000B: "sharedUserId",
    0x0101000C: "taskAffinity",
    0x0101000E: "enabled",
    0x0101000F: "debuggable",
    0x01010010: "exported",
    0x01010011: "process",
    0x01010018: "authorities",
    0x0101001B: "grantUriPermissions",
    0x0101001C: "priority",
    0x0101001D: "launchMode",
    0x01010024: "value",
    0x01010025: "resource",
    0x01010026: "mimeType",
    0x01010027: "scheme",
    0x01010028: "host",
    0x01010029: "port",
    0x0101002A: "path",
    0x0101002B: "pathPrefix",
    0x0101002C: "pathPattern",
    0x0101020C: "minSdkVersion",
    0x0101021B: "versionCode",
    0x0101021C: "versionName",
    0x01010270: "targetSdkVersion",
    0x01010271: "maxSdkVersion",
    0x01010272: "testOnly",
    0x0101027F: "backupAgent",
    0x01010280: "allowBackup",
    0x01010281: "glEsVersion",
    0x0101028E: "required",
    0x01010473: "fullBackupContent",
    0x010104EA: "extractNativeLibs",
    0x010104EC: "usesCleartextTraffic",
    0x01010527: "networkSecurityConfig",
    0x01010572: "compileSdkVersion",
    0x01010573: "compileSdkVersionCodename",
    0x01010603: "requestLegacyExternalStorage",
}

_MAX_ELEMENTS = 200_000


@dataclass(slots=True)
class StringPool:
    """The RES_STRING_POOL every binary resource file starts with."""

    strings: list[str] = field(default_factory=list)

    def get(self, index: int) -> str | None:
        if index < 0 or index >= len(self.strings):
            return None
        return self.strings[index]


@dataclass(slots=True)
class AxmlAttribute:
    namespace: str | None
    name: str
    type: int
    data: int
    raw: str | None = None

    @property
    def value(self) -> Any:
        """The attribute as a Python value, or a marker when it is a reference.

        References are the one type that cannot be resolved here: the target
        lives in ``resources.arsc``. They come back as ``@0x7f0e0001`` so a
        caller holding the resource table can finish the job (see
        :mod:`code_ai.tools.apk.arsc`), and stay readable when it cannot.
        """

        if self.type == TYPE_STRING:
            return self.raw if self.raw is not None else ""
        if self.type == TYPE_INT_BOOLEAN:
            return self.data != 0
        if self.type in {TYPE_INT_DEC, TYPE_INT_HEX}:
            return _signed32(self.data)
        if self.type == TYPE_REFERENCE:
            return f"@0x{self.data:08x}"
        if self.type == TYPE_ATTRIBUTE:
            return f"?0x{self.data:08x}"
        if self.type == TYPE_NULL:
            return None
        if self.type == TYPE_FLOAT:
            return struct.unpack("<f", struct.pack("<I", self.data))[0]
        if self.type in {TYPE_DIMENSION, TYPE_FRACTION}:
            return f"0x{self.data:08x}"
        if 0x1C <= self.type <= 0x1F:
            return f"#{self.data:08x}"
        return _signed32(self.data)

    def as_text(self) -> str:
        value = self.value
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def is_reference(self) -> bool:
        return self.type == TYPE_REFERENCE


@dataclass(slots=True)
class AxmlElement:
    name: str
    namespace: str | None = None
    attributes: list[AxmlAttribute] = field(default_factory=list)
    children: list[AxmlElement] = field(default_factory=list)
    # Character data, which a manifest never uses but a network security config
    # does: its domains are element text, not attributes.
    text: str = ""

    def attr(self, name: str, *, namespace: str | None = ANDROID_NAMESPACE) -> AxmlAttribute | None:
        for attribute in self.attributes:
            if attribute.name != name:
                continue
            if namespace is None or attribute.namespace in {namespace, None}:
                return attribute
        return None

    def text_attr(self, name: str, *, namespace: str | None = ANDROID_NAMESPACE) -> str | None:
        attribute = self.attr(name, namespace=namespace)
        return None if attribute is None else attribute.as_text()

    def iter_children(self, name: str) -> Iterator[AxmlElement]:
        for child in self.children:
            if child.name == name:
                yield child

    def child(self, name: str) -> AxmlElement | None:
        return next(self.iter_children(name), None)

    def walk(self) -> Iterator[AxmlElement]:
        yield self
        for child in self.children:
            yield from child.walk()


def parse_axml(data: bytes) -> AxmlElement:
    """Decode a binary Android XML document into an element tree.

    Raises :class:`ToolExecutionError` with a readable reason when the input is
    not AXML - a plain-text manifest, the protobuf one an .aab carries, or a
    truncated file - because that is something the caller can act on.
    """

    if len(data) < 8:
        raise ToolExecutionError("Binary XML is empty or truncated.")
    magic_type, header_size, _file_size = struct.unpack_from("<HHI", data, 0)
    if magic_type != _RES_XML_TYPE:
        if data.lstrip()[:1] in {b"<", b"\xef"}:
            raise ToolExecutionError("This XML is plain text, not the compiled binary format.")
        raise ToolExecutionError(
            f"Not a binary XML document (chunk type 0x{magic_type:04x}); "
            "an .aab stores its manifest as protobuf, which this tool cannot read."
        )

    pool = StringPool()
    resource_map: list[int] = []
    stack: list[AxmlElement] = []
    root: AxmlElement | None = None
    elements = 0

    offset = max(header_size, 8)
    end = len(data)
    while offset + 8 <= end:
        chunk_type, chunk_header, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_size < 8 or offset + chunk_size > end:
            # A short trailing chunk shows up in mangled files; stop instead of
            # reading past the buffer.
            break
        if chunk_type == _RES_STRING_POOL_TYPE:
            pool = parse_string_pool(data, offset)
        elif chunk_type == _RES_XML_RESOURCE_MAP:
            count = (chunk_size - chunk_header) // 4
            resource_map = list(struct.unpack_from(f"<{count}I", data, offset + chunk_header))
        elif chunk_type == _RES_XML_START_ELEMENT:
            elements += 1
            if elements > _MAX_ELEMENTS:
                raise ToolExecutionError("Binary XML has an implausible number of elements.")
            element = _parse_start_element(data, offset, chunk_header, pool, resource_map)
            if stack:
                stack[-1].children.append(element)
            elif root is None:
                root = element
            stack.append(element)
        elif chunk_type == _RES_XML_END_ELEMENT:
            if stack:
                stack.pop()
        elif chunk_type == _RES_XML_CDATA and stack:
            data_index = struct.unpack_from("<i", data, offset + chunk_header)[0]
            stack[-1].text += pool.get(data_index) or ""
        offset += chunk_size

    if root is None:
        raise ToolExecutionError("Binary XML contains no elements.")
    return root


def _parse_start_element(
    data: bytes,
    offset: int,
    header_size: int,
    pool: StringPool,
    resource_map: list[int],
) -> AxmlElement:
    base = offset + header_size
    namespace_idx, name_idx = struct.unpack_from("<ii", data, base)
    attribute_start, attribute_size, attribute_count = struct.unpack_from("<HHH", data, base + 8)
    element = AxmlElement(name=pool.get(name_idx) or "", namespace=pool.get(namespace_idx))
    if attribute_size < 20:
        return element
    for index in range(attribute_count):
        position = base + attribute_start + index * attribute_size
        if position + 20 > len(data):
            break
        attr_ns_idx, attr_name_idx, raw_idx = struct.unpack_from("<iii", data, position)
        value_type = data[position + 15]
        value_data = struct.unpack_from("<I", data, position + 16)[0]
        name = pool.get(attr_name_idx) or ""
        if not name and 0 <= attr_name_idx < len(resource_map):
            resource_id = resource_map[attr_name_idx]
            name = ANDROID_ATTRS.get(resource_id, f"attr_0x{resource_id:08x}")
        raw = pool.get(raw_idx) if raw_idx >= 0 else None
        if value_type == TYPE_STRING and raw is None:
            raw = pool.get(value_data)
        element.attributes.append(
            AxmlAttribute(
                namespace=pool.get(attr_ns_idx),
                name=name,
                type=value_type,
                data=value_data,
                raw=raw,
            )
        )
    return element


def parse_string_pool(data: bytes, offset: int) -> StringPool:
    _chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
    count, _style_count, flags, strings_start, _styles_start = struct.unpack_from(
        "<IIIII", data, offset + 8
    )
    if count == 0:
        return StringPool()
    offsets_at = offset + header_size
    if offsets_at + count * 4 > len(data):
        raise ToolExecutionError("String pool is truncated.")
    offsets = struct.unpack_from(f"<{count}I", data, offsets_at)
    base = offset + strings_start
    limit = min(offset + chunk_size, len(data))
    utf8 = bool(flags & _UTF8_FLAG)
    read = _read_utf8 if utf8 else _read_utf16
    strings: list[str] = []
    for entry_offset in offsets:
        position = base + entry_offset
        strings.append("" if position >= limit else read(data, position, limit))
    return StringPool(strings=strings)


def _read_utf8(data: bytes, position: int, limit: int) -> str:
    _chars, position = _varint8(data, position)
    length, position = _varint8(data, position)
    return data[position : min(position + length, limit)].decode("utf-8", errors="replace")


def _read_utf16(data: bytes, position: int, limit: int) -> str:
    if position + 2 > limit:
        return ""
    length = struct.unpack_from("<H", data, position)[0]
    position += 2
    if length & 0x8000:
        if position + 2 > limit:
            return ""
        low = struct.unpack_from("<H", data, position)[0]
        position += 2
        length = ((length & 0x7FFF) << 16) | low
    end = min(position + length * 2, limit)
    return data[position:end].decode("utf-16-le", errors="replace")


def _varint8(data: bytes, position: int) -> tuple[int, int]:
    value = data[position]
    position += 1
    if value & 0x80:
        value = ((value & 0x7F) << 8) | data[position]
        position += 1
    return value, position


def _signed32(value: int) -> int:
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value
