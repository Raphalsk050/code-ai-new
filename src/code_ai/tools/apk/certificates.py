from __future__ import annotations

import hashlib
from dataclasses import dataclass

# A signing certificate is the one part of an APK worth reading byte by byte:
# the fingerprint decides whether an update can ever be installed over the
# release build, and the algorithm and key size decide whether the signature
# still means anything. Rather than pull in a crypto dependency for it, this
# module walks just enough DER to answer those questions.

_TAG_INTEGER = 0x02
_TAG_BIT_STRING = 0x03
_TAG_OID = 0x06
_TAG_SEQUENCE = 0x30
_TAG_SET = 0x31
_TAG_UTC_TIME = 0x17
_TAG_GENERALIZED_TIME = 0x18
_CONTEXT_0 = 0xA0

_OID_COMMON_NAME = "2.5.4.3"
_OID_ORGANIZATION = "2.5.4.10"

_SIGNATURE_ALGORITHMS: dict[str, str] = {
    "1.2.840.113549.1.1.5": "SHA1withRSA",
    "1.2.840.113549.1.1.11": "SHA256withRSA",
    "1.2.840.113549.1.1.12": "SHA384withRSA",
    "1.2.840.113549.1.1.13": "SHA512withRSA",
    "1.2.840.113549.1.1.4": "MD5withRSA",
    "1.2.840.10040.4.3": "SHA1withDSA",
    "2.16.840.1.101.3.4.3.2": "SHA256withDSA",
    "1.2.840.10045.4.1": "SHA1withECDSA",
    "1.2.840.10045.4.3.2": "SHA256withECDSA",
    "1.2.840.10045.4.3.3": "SHA384withECDSA",
    "1.2.840.10045.4.3.4": "SHA512withECDSA",
}

_KEY_ALGORITHMS: dict[str, str] = {
    "1.2.840.113549.1.1.1": "RSA",
    "1.2.840.10040.4.1": "DSA",
    "1.2.840.10045.2.1": "EC",
}

_WEAK_ALGORITHMS = frozenset({"SHA1withRSA", "MD5withRSA", "SHA1withDSA", "SHA1withECDSA"})

_OID_SIGNED_DATA = "1.2.840.113549.1.7.2"


@dataclass(slots=True)
class CertificateInfo:
    subject: str | None
    issuer: str | None
    serial: str | None
    not_before: str | None
    not_after: str | None
    signature_algorithm: str | None
    key_algorithm: str | None
    key_bits: int | None
    sha256: str
    sha1: str

    @property
    def self_signed(self) -> bool:
        return bool(self.subject) and self.subject == self.issuer

    @property
    def debug_certificate(self) -> bool:
        """True for the certificate the Android SDK generates for debug builds."""

        haystack = f"{self.subject or ''} {self.issuer or ''}".lower()
        return "android debug" in haystack

    @property
    def weak_algorithm(self) -> bool:
        return (self.signature_algorithm or "") in _WEAK_ALGORITHMS

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "serial": self.serial,
            "valid_from": self.not_before,
            "valid_until": self.not_after,
            "signature_algorithm": self.signature_algorithm,
            "key_algorithm": self.key_algorithm,
            "key_bits": self.key_bits,
            "sha256": self.sha256,
            "sha1": self.sha1,
            "self_signed": self.self_signed,
            "debug_certificate": self.debug_certificate,
        }


def parse_certificate(der: bytes) -> CertificateInfo:
    """Read an X.509 certificate. Unreadable fields come back as ``None``."""

    info = CertificateInfo(
        subject=None,
        issuer=None,
        serial=None,
        not_before=None,
        not_after=None,
        signature_algorithm=None,
        key_algorithm=None,
        key_bits=None,
        sha256=hashlib.sha256(der).hexdigest(),
        sha1=hashlib.sha1(der).hexdigest(),
    )
    try:
        certificate = _children(der, *_content(der, 0))
        if not certificate:
            return info
        tbs_tag, tbs_start, tbs_end = certificate[0]
        if tbs_tag != _TAG_SEQUENCE:
            return info
        if len(certificate) > 1:
            algorithm = _children(der, certificate[1][1], certificate[1][2])
            if algorithm and algorithm[0][0] == _TAG_OID:
                oid = _oid(der, algorithm[0][1], algorithm[0][2])
                info.signature_algorithm = _SIGNATURE_ALGORITHMS.get(oid, oid)

        fields = _children(der, tbs_start, tbs_end)
        if fields and fields[0][0] == _CONTEXT_0:
            fields = fields[1:]
        if fields and fields[0][0] == _TAG_INTEGER:
            info.serial = _integer(der, fields[0][1], fields[0][2])
        if len(fields) > 3:
            info.issuer = _name(der, fields[2][1], fields[2][2])
            validity = _children(der, fields[3][1], fields[3][2])
            if len(validity) > 1:
                info.not_before = _time(der, validity[0])
                info.not_after = _time(der, validity[1])
        if len(fields) > 4:
            info.subject = _name(der, fields[4][1], fields[4][2])
        if len(fields) > 5:
            _read_public_key(der, fields[5], info)
    except (IndexError, ValueError):
        # A malformed certificate still has a usable fingerprint, which is what
        # most of the analysis needs; report what parsed and move on.
        return info
    return info


def extract_certificates(pkcs7: bytes) -> list[bytes]:
    """Pull the X.509 certificates out of a v1 ``META-INF/*.RSA`` block.

    The file is a PKCS#7 SignedData; its certificates live in the ``[0]``
    implicit set inside. Anything that does not parse yields an empty list, and
    the caller falls back to reporting the signature file alone.
    """

    try:
        content_info = _children(pkcs7, *_content(pkcs7, 0))
        if len(content_info) < 2 or content_info[0][0] != _TAG_OID:
            return []
        if _oid(pkcs7, content_info[0][1], content_info[0][2]) != _OID_SIGNED_DATA:
            return []
        signed_data_holder = _children(pkcs7, content_info[1][1], content_info[1][2])
        if not signed_data_holder:
            return []
        signed_data = _children(pkcs7, signed_data_holder[0][1], signed_data_holder[0][2])
        for tag, start, end in signed_data:
            if tag != _CONTEXT_0:
                continue
            certificates: list[bytes] = []
            position = start
            while position + 2 <= end:
                child_tag, _child_start, child_end, next_position = _tlv(pkcs7, position)
                if child_end > end:
                    break
                if child_tag == _TAG_SEQUENCE:
                    certificates.append(pkcs7[position:child_end])
                position = next_position
            return certificates
    except (IndexError, ValueError):
        return []
    return []


def _read_public_key(data: bytes, field: tuple[int, int, int], info: CertificateInfo) -> None:
    tag, start, end = field
    if tag != _TAG_SEQUENCE:
        return
    parts = _children(data, start, end)
    if not parts:
        return
    algorithm = _children(data, parts[0][1], parts[0][2])
    if algorithm and algorithm[0][0] == _TAG_OID:
        oid = _oid(data, algorithm[0][1], algorithm[0][2])
        info.key_algorithm = _KEY_ALGORITHMS.get(oid, oid)
    if len(parts) < 2 or parts[1][0] != _TAG_BIT_STRING:
        return
    # Skip the "unused bits" byte that opens every BIT STRING.
    key_start = parts[1][1] + 1
    key_end = parts[1][2]
    if info.key_algorithm == "RSA":
        key = _children(data, *_content(data, key_start))
        if key and key[0][0] == _TAG_INTEGER:
            modulus = data[key[0][1] : key[0][2]].lstrip(b"\x00")
            info.key_bits = len(modulus) * 8
    elif info.key_algorithm == "EC":
        # An uncompressed EC point is 1 + 2 * field size bytes.
        info.key_bits = max(0, (key_end - key_start - 1) // 2) * 8


def _content(data: bytes, position: int) -> tuple[int, int]:
    _tag, start, end, _next = _tlv(data, position)
    return start, end


def _tlv(data: bytes, position: int) -> tuple[int, int, int, int]:
    tag = data[position]
    length_byte = data[position + 1]
    position += 2
    if length_byte & 0x80:
        count = length_byte & 0x7F
        if count == 0 or count > 4:
            raise ValueError("Unsupported DER length encoding.")
        length = int.from_bytes(data[position : position + count], "big")
        position += count
    else:
        length = length_byte
    end = position + length
    if end > len(data):
        raise ValueError("DER value runs past the end of the buffer.")
    return tag, position, end, end


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int, int]]:
    result: list[tuple[int, int, int]] = []
    position = start
    while position + 2 <= end:
        tag, content_start, content_end, position = _tlv(data, position)
        if content_end > end:
            break
        result.append((tag, content_start, content_end))
    return result


def _oid(data: bytes, start: int, end: int) -> str:
    raw = data[start:end]
    if not raw:
        return ""
    parts = [str(raw[0] // 40), str(raw[0] % 40)]
    value = 0
    for byte in raw[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(value))
            value = 0
    return ".".join(parts)


def _integer(data: bytes, start: int, end: int) -> str:
    return "0x" + (data[start:end].hex() or "00")


def _time(data: bytes, field: tuple[int, int, int]) -> str | None:
    tag, start, end = field
    raw = data[start:end].decode("ascii", errors="replace").rstrip("Z")
    if tag == _TAG_UTC_TIME and len(raw) >= 12:
        century = "19" if int(raw[:2]) >= 50 else "20"
        raw = century + raw
    elif tag != _TAG_GENERALIZED_TIME:
        return None
    if len(raw) < 14:
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T{raw[8:10]}:{raw[10:12]}:{raw[12:14]}Z"


def _name(data: bytes, start: int, end: int) -> str | None:
    """Render a Distinguished Name as ``CN=..., O=...`` for the parts we read."""

    common_names: list[str] = []
    organizations: list[str] = []
    for tag, rdn_start, rdn_end in _children(data, start, end):
        if tag != _TAG_SET:
            continue
        for pair_tag, pair_start, pair_end in _children(data, rdn_start, rdn_end):
            if pair_tag != _TAG_SEQUENCE:
                continue
            parts = _children(data, pair_start, pair_end)
            if len(parts) < 2 or parts[0][0] != _TAG_OID:
                continue
            oid = _oid(data, parts[0][1], parts[0][2])
            text = data[parts[1][1] : parts[1][2]].decode("utf-8", errors="replace")
            if oid == _OID_COMMON_NAME:
                common_names.append(text)
            elif oid == _OID_ORGANIZATION:
                organizations.append(text)
    fragments = [f"CN={value}" for value in common_names]
    fragments += [f"O={value}" for value in organizations]
    return ", ".join(fragments) or None
