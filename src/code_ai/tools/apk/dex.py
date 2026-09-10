from __future__ import annotations

import struct
from dataclasses import dataclass

_DEX_MAGIC = b"dex\n"
_HEADER_BYTES = 112

# The 64K limit that forces multidex is per-dex, on the method *id* table, and
# it is the number teams actually watch. Anything close to it is worth saying
# out loud before a build starts failing.
DEX_METHOD_LIMIT = 65_536


@dataclass(slots=True)
class DexInfo:
    name: str
    version: str
    size: int
    string_ids: int
    type_ids: int
    proto_ids: int
    field_ids: int
    method_ids: int
    class_defs: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "size_bytes": self.size,
            "classes": self.class_defs,
            "methods": self.method_ids,
            "fields": self.field_ids,
            "strings": self.string_ids,
            "method_headroom": DEX_METHOD_LIMIT - self.method_ids,
        }


def parse_dex_header(header: bytes, *, name: str, size: int) -> DexInfo | None:
    """Read a DEX header. Returns ``None`` when the bytes are not a DEX file.

    Only the fixed 112-byte header is needed for the counts, so callers can
    read that prefix instead of inflating a 20 MB classes.dex.
    """

    if len(header) < _HEADER_BYTES or not header.startswith(_DEX_MAGIC):
        return None
    version = header[4:7].decode("ascii", errors="replace")
    (
        string_ids,
        _string_off,
        type_ids,
        _type_off,
        proto_ids,
        _proto_off,
        field_ids,
        _field_off,
        method_ids,
        _method_off,
        class_defs,
        _class_off,
    ) = struct.unpack_from("<12I", header, 56)
    return DexInfo(
        name=name,
        version=version,
        size=size,
        string_ids=string_ids,
        type_ids=type_ids,
        proto_ids=proto_ids,
        field_ids=field_ids,
        method_ids=method_ids,
        class_defs=class_defs,
    )


def dex_min_api(version: str) -> int | None:
    """Map a DEX format version to the minimum API level that can load it."""

    return {"035": 1, "037": 24, "038": 26, "039": 28, "040": 30}.get(version)
