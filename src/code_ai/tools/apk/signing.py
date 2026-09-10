from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from code_ai.tools.apk.certificates import CertificateInfo, extract_certificates, parse_certificate

_SIGNING_BLOCK_MAGIC = b"APK Sig Block 42"
_EOCD_MAGIC = b"PK\x05\x06"
_ZIP64_LOCATOR_MAGIC = b"PK\x06\x07"

_BLOCK_V2 = 0x7109871A
_BLOCK_V3 = 0xF05368C0
_BLOCK_V31 = 0x1B93AD61

_BLOCK_NAMES: dict[int, str] = {
    _BLOCK_V2: "v2",
    _BLOCK_V3: "v3",
    _BLOCK_V31: "v3.1",
    0x2146444E: "source stamp v1",
    0x6DFF800D: "source stamp v2",
    0x42726577: "padding",
    0x504B4453: "dependency info",
}

_V1_SIGNATURE_SUFFIXES = (".rsa", ".dsa", ".ec")
# The signing block is normally tens of kilobytes; this ceiling stops a crafted
# size field from asking for a gigabyte read.
_MAX_BLOCK_BYTES = 32 * 1024 * 1024


@dataclass(slots=True)
class SigningInfo:
    """Which signature schemes an APK carries, and who signed it.

    Presence only: this reads the schemes and certificates without verifying a
    single digest, so it answers "who signed this and how" but never "is this
    signature valid". Say so wherever the result is presented.
    """

    schemes: list[str] = field(default_factory=list)
    v1_files: list[str] = field(default_factory=list)
    extra_blocks: list[str] = field(default_factory=list)
    certificates: list[CertificateInfo] = field(default_factory=list)
    signer_count: int = 0
    note: str | None = None

    @property
    def signed(self) -> bool:
        return bool(self.schemes)

    def to_dict(self) -> dict[str, object]:
        return {
            "signed": self.signed,
            "schemes": list(self.schemes),
            "v1_signature_files": list(self.v1_files),
            "extra_blocks": list(self.extra_blocks),
            "signer_count": self.signer_count,
            "certificates": [certificate.to_dict() for certificate in self.certificates],
            "verified": False,
            "note": self.note
            or "Schemes and certificates are read, not cryptographically verified.",
        }


def analyze_signing(path: Path, archive: zipfile.ZipFile) -> SigningInfo:
    info = SigningInfo()
    seen_fingerprints: set[str] = set()

    for name in archive.namelist():
        lowered = name.lower()
        if not lowered.startswith("meta-inf/"):
            continue
        if lowered.endswith(_V1_SIGNATURE_SUFFIXES):
            info.v1_files.append(name)
    if info.v1_files:
        info.schemes.append("v1")
        for name in info.v1_files:
            try:
                blob = archive.read(name)
            except (KeyError, OSError, zipfile.BadZipFile):
                continue
            for der in extract_certificates(blob):
                _add_certificate(info, der, seen_fingerprints)

    try:
        pairs = _read_signing_block(path)
    except OSError:
        pairs = {}
    for block_id, value in pairs.items():
        name = _BLOCK_NAMES.get(block_id)
        if block_id in {_BLOCK_V2, _BLOCK_V3, _BLOCK_V31}:
            info.schemes.append(name or f"0x{block_id:08x}")
            signers = _signer_certificates(value)
            info.signer_count = max(info.signer_count, len(signers))
            for der in signers:
                _add_certificate(info, der, seen_fingerprints)
        elif name is not None and name != "padding":
            info.extra_blocks.append(name)
        elif name is None:
            info.extra_blocks.append(f"0x{block_id:08x}")

    info.schemes.sort()
    if info.v1_files and info.signer_count == 0:
        info.signer_count = max(info.signer_count, len(info.certificates))
    if not info.schemes:
        info.note = "No v1/v2/v3 signature found; this APK cannot be installed as-is."
    return info


def _add_certificate(info: SigningInfo, der: bytes, seen: set[str]) -> None:
    certificate = parse_certificate(der)
    if certificate.sha256 in seen:
        return
    seen.add(certificate.sha256)
    info.certificates.append(certificate)


def _read_signing_block(path: Path) -> dict[int, bytes]:
    """Return the APK Signing Block's id-value pairs, or ``{}`` when absent.

    Only the tail of the file is read: the block sits immediately before the
    central directory, and v1-only APKs have nothing there at all.
    """

    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        tail_size = min(size, 65_557)
        handle.seek(size - tail_size)
        tail = handle.read(tail_size)
        eocd = tail.rfind(_EOCD_MAGIC)
        if eocd == -1:
            return {}
        eocd_at = size - tail_size + eocd
        if eocd + 20 > len(tail):
            return {}
        cd_offset = struct.unpack_from("<I", tail, eocd + 16)[0]
        if cd_offset == 0xFFFFFFFF:
            cd_offset = _zip64_central_directory(handle, tail, eocd, size - tail_size)
            if cd_offset is None:
                return {}
        if cd_offset < 32 or cd_offset > eocd_at:
            return {}

        handle.seek(cd_offset - 24)
        footer = handle.read(24)
        if len(footer) < 24 or footer[8:24] != _SIGNING_BLOCK_MAGIC:
            return {}
        block_size = struct.unpack_from("<Q", footer, 0)[0]
        if block_size < 24 or block_size > _MAX_BLOCK_BYTES:
            return {}
        block_start = cd_offset - block_size - 8
        if block_start < 0:
            return {}
        handle.seek(block_start)
        block = handle.read(block_size + 8)

    if len(block) < 24 or struct.unpack_from("<Q", block, 0)[0] != block_size:
        return {}
    pairs: dict[int, bytes] = {}
    position = 8
    end = len(block) - 24
    while position + 12 <= end:
        pair_size = struct.unpack_from("<Q", block, position)[0]
        if pair_size < 4 or position + 8 + pair_size > len(block):
            break
        block_id = struct.unpack_from("<I", block, position + 8)[0]
        pairs.setdefault(block_id, block[position + 12 : position + 8 + pair_size])
        position += 8 + pair_size
    return pairs


def _zip64_central_directory(handle, tail: bytes, eocd: int, tail_base: int) -> int | None:
    locator = tail.rfind(_ZIP64_LOCATOR_MAGIC, 0, eocd)
    if locator == -1 or locator + 16 > len(tail):
        return None
    record_offset = struct.unpack_from("<Q", tail, locator + 8)[0]
    del tail_base
    handle.seek(record_offset)
    record = handle.read(56)
    if len(record) < 56 or record[:4] != b"PK\x06\x06":
        return None
    return struct.unpack_from("<Q", record, 48)[0]


def _signer_certificates(block: bytes) -> list[bytes]:
    """Extract each signer's certificate chain from a v2/v3 signing block.

    Layout (all lengths are little-endian uint32): a length-prefixed sequence of
    signers; each signer opens with its length-prefixed signed data; the signed
    data opens with digests, then the certificate sequence. v3 adds SDK-range
    fields after those, so reading only the first two is version-agnostic.
    """

    certificates: list[bytes] = []
    signers, _ = _read_prefixed(block, 0)
    if signers is None:
        return certificates
    position = 0
    while position < len(signers):
        signer, position = _read_prefixed(signers, position)
        if signer is None:
            break
        signed_data, _ = _read_prefixed(signer, 0)
        if signed_data is None:
            continue
        _digests, after_digests = _read_prefixed(signed_data, 0)
        certificate_block, _ = _read_prefixed(signed_data, after_digests)
        if certificate_block is None:
            continue
        inner = 0
        while inner < len(certificate_block):
            der, inner = _read_prefixed(certificate_block, inner)
            if der is None:
                break
            certificates.append(der)
    return certificates


def _read_prefixed(data: bytes, position: int) -> tuple[bytes | None, int]:
    if position + 4 > len(data):
        return None, len(data)
    length = struct.unpack_from("<I", data, position)[0]
    start = position + 4
    end = start + length
    if end > len(data):
        return None, len(data)
    return data[start:end], end
