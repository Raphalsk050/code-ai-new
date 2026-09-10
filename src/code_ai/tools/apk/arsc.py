from __future__ import annotations

import struct
from dataclasses import dataclass, field

from code_ai.tools.apk.axml import (
    TYPE_INT_BOOLEAN,
    TYPE_INT_DEC,
    TYPE_INT_HEX,
    TYPE_REFERENCE,
    TYPE_STRING,
    StringPool,
    parse_string_pool,
)

_RES_TABLE_TYPE = 0x0002
_RES_TABLE_PACKAGE_TYPE = 0x0200
_RES_TABLE_TYPE_TYPE = 0x0201

_ENTRY_FLAG_COMPLEX = 0x0001
_ENTRY_FLAG_COMPACT = 0x0008
_TYPE_FLAG_SPARSE = 0x01
_TYPE_FLAG_OFFSET16 = 0x02

# A table is a map of ids to values across every configuration the app ships
# (each locale, density, orientation). Only one value per id is ever reported,
# so this ceiling exists purely to keep a pathological table from being walked
# forever.
_MAX_ENTRIES = 500_000
_MAX_REFERENCE_DEPTH = 4


@dataclass(slots=True)
class ResourceValue:
    type: int
    data: int
    default_config: bool


@dataclass(slots=True)
class ResourceTable:
    """Just enough of ``resources.arsc`` to turn ``@0x7f...`` into a value.

    A manifest references most of its interesting strings rather than inlining
    them: the app label, and the network-security config whose own file path is
    a resource string. Resolving those is the difference between reporting
    ``label: @0x7f110002`` and reporting the app's actual name.
    """

    package_name: str | None = None
    package_id: int | None = None
    strings: StringPool = field(default_factory=StringPool)
    values: dict[int, list[ResourceValue]] = field(default_factory=dict)

    def resolve(self, resource_id: int, *, _depth: int = 0) -> str | None:
        """Return the resource's value as text, following reference chains."""

        entries = self.values.get(resource_id)
        if not entries:
            return None
        entry = next((item for item in entries if item.default_config), entries[0])
        if entry.type == TYPE_STRING:
            return self.strings.get(entry.data)
        if entry.type == TYPE_REFERENCE:
            if _depth >= _MAX_REFERENCE_DEPTH or entry.data == resource_id or entry.data == 0:
                return None
            return self.resolve(entry.data, _depth=_depth + 1)
        if entry.type == TYPE_INT_BOOLEAN:
            return "true" if entry.data else "false"
        if entry.type in {TYPE_INT_DEC, TYPE_INT_HEX}:
            return str(entry.data)
        return None


def parse_resource_table(data: bytes) -> ResourceTable:
    """Parse ``resources.arsc``. Returns an empty table for anything unreadable.

    Resource resolution is a nicety layered on top of the manifest analysis, so
    a table this parser cannot follow degrades to unresolved ``@0x...`` markers
    instead of failing the whole report.
    """

    table = ResourceTable()
    if len(data) < 12:
        return table
    chunk_type, header_size, _size = struct.unpack_from("<HHI", data, 0)
    if chunk_type != _RES_TABLE_TYPE:
        return table

    offset = max(header_size, 12)
    end = len(data)
    while offset + 8 <= end:
        inner_type, inner_header, inner_size = struct.unpack_from("<HHI", data, offset)
        if inner_size < 8 or offset + inner_size > end:
            break
        if inner_type == 0x0001 and not table.strings.strings:
            table.strings = parse_string_pool(data, offset)
        elif inner_type == _RES_TABLE_PACKAGE_TYPE:
            _parse_package(data, offset, inner_header, inner_size, table)
        offset += inner_size
    return table


def _parse_package(
    data: bytes, offset: int, header_size: int, chunk_size: int, table: ResourceTable
) -> None:
    package_id = struct.unpack_from("<I", data, offset + 8)[0]
    raw_name = data[offset + 12 : offset + 12 + 256]
    name = raw_name.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
    if table.package_id is None:
        table.package_id = package_id
        table.package_name = name or None

    # The package header is followed by its type-name and key-name pools and
    # then the type chunks. Walking chunk by chunk steps over the pools without
    # having to trust the offsets in the header.
    inner = offset + max(header_size, 8)
    end = min(offset + chunk_size, len(data))
    while inner + 8 <= end:
        inner_type, _inner_header, inner_size = struct.unpack_from("<HHI", data, inner)
        if inner_size < 8 or inner + inner_size > end:
            break
        if inner_type == _RES_TABLE_TYPE_TYPE:
            _parse_type(data, inner, _inner_header, inner_size, package_id, table)
        inner += inner_size


def _parse_type(
    data: bytes,
    offset: int,
    header_size: int,
    chunk_size: int,
    package_id: int,
    table: ResourceTable,
) -> None:
    if len(table.values) > _MAX_ENTRIES:
        return
    type_id = data[offset + 8]
    flags = data[offset + 9]
    entry_count, entries_start = struct.unpack_from("<II", data, offset + 12)
    if type_id == 0 or entry_count == 0:
        return
    config_size = struct.unpack_from("<I", data, offset + 20)[0]
    config_end = min(offset + 20 + max(config_size, 4), len(data))
    default_config = not any(data[offset + 24 : config_end])

    offsets_at = offset + header_size
    entries_at = offset + entries_start
    chunk_end = min(offset + chunk_size, len(data))
    base_id = (package_id << 24) | (type_id << 16)

    for index, entry_index, entry_offset in _iter_entry_offsets(
        data, offsets_at, entry_count, flags, chunk_end
    ):
        del index
        position = entries_at + entry_offset
        if position + 8 > chunk_end:
            continue
        value = _parse_entry(data, position, chunk_end, default_config)
        if value is None:
            continue
        table.values.setdefault(base_id | entry_index, []).append(value)


def _iter_entry_offsets(
    data: bytes, offsets_at: int, entry_count: int, flags: int, limit: int
) -> list[tuple[int, int, int]]:
    """Yield ``(slot, entry index, byte offset)`` for each populated entry.

    Three layouts exist in the wild: the classic 32-bit offset array, the
    16-bit one (``FLAG_OFFSET16``), and the sparse pairs newer aapt2 emits for
    types where most ids are absent.
    """

    result: list[tuple[int, int, int]] = []
    if flags & _TYPE_FLAG_SPARSE:
        for slot in range(entry_count):
            position = offsets_at + slot * 4
            if position + 4 > limit:
                break
            entry_index, packed = struct.unpack_from("<HH", data, position)
            result.append((slot, entry_index, packed * 4))
        return result
    if flags & _TYPE_FLAG_OFFSET16:
        for slot in range(entry_count):
            position = offsets_at + slot * 2
            if position + 2 > limit:
                break
            packed = struct.unpack_from("<H", data, position)[0]
            if packed != 0xFFFF:
                result.append((slot, slot, packed * 4))
        return result
    for slot in range(entry_count):
        position = offsets_at + slot * 4
        if position + 4 > limit:
            break
        packed = struct.unpack_from("<I", data, position)[0]
        if packed != 0xFFFFFFFF:
            result.append((slot, slot, packed))
    return result


def _parse_entry(
    data: bytes, position: int, limit: int, default_config: bool
) -> ResourceValue | None:
    entry_size, entry_flags = struct.unpack_from("<HH", data, position)
    if entry_flags & _ENTRY_FLAG_COMPACT:
        # Android 14's compact entry packs the value into the entry header:
        # the size field holds the key index and the type rides in flags>>8.
        value_type = (entry_flags >> 8) & 0xFF
        if position + 8 > limit:
            return None
        value_data = struct.unpack_from("<I", data, position + 4)[0]
        return ResourceValue(type=value_type, data=value_data, default_config=default_config)
    if entry_flags & _ENTRY_FLAG_COMPLEX:
        # A bag (style, array, plural). Nothing here needs one.
        return None
    value_at = position + max(entry_size, 8)
    if value_at + 8 > limit:
        return None
    value_type = data[value_at + 3]
    value_data = struct.unpack_from("<I", data, value_at + 4)[0]
    return ResourceValue(type=value_type, data=value_data, default_config=default_config)


def parse_resource_id(marker: str) -> int | None:
    """Turn an unresolved ``@0x7f110002`` marker back into its numeric id."""

    if not marker.startswith("@0x"):
        return None
    try:
        return int(marker[3:], 16)
    except ValueError:
        return None
