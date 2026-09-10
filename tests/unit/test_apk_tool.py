"""Tests for the APK analysis tool.

There is no sample APK checked in, so the fixtures here build one: the helpers
below encode a binary AndroidManifest.xml, a resources.arsc, DEX headers, an
X.509 certificate and an APK Signing Block by hand. That keeps the tests honest
about the formats the parsers claim to read, instead of asserting against a
recorded blob nobody can regenerate.
"""

from __future__ import annotations

import asyncio
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.apk import AnalyzeApkTool
from code_ai.tools.apk.axml import ANDROID_ATTRS, ANDROID_NAMESPACE
from code_ai.tools.base import ToolCapability, ToolContext
from code_ai.util.paths import WorkspacePolicy

ATTR_IDS: dict[str, int] = {name: value for value, name in ANDROID_ATTRS.items()}

_TYPE_REFERENCE = 0x01
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_BOOLEAN = 0x12


class Ref(int):
    """Marks an attribute value as a resource reference rather than an int."""


@dataclass
class Elem:
    name: str
    attrs: dict[str, object] = field(default_factory=dict)
    children: list[Elem] = field(default_factory=list)
    text: str = ""


# --------------------------------------------------------------------------
# Binary XML encoding
# --------------------------------------------------------------------------


def _len8(value: int) -> bytes:
    return bytes([value]) if value < 0x80 else bytes([(value >> 8) | 0x80, value & 0xFF])


def encode_string_pool(strings: list[str], *, utf8: bool = False) -> bytes:
    data = bytearray()
    offsets: list[int] = []
    for text in strings:
        offsets.append(len(data))
        if utf8:
            encoded = text.encode("utf-8")
            data += _len8(len(text)) + _len8(len(encoded)) + encoded + b"\x00"
        else:
            data += struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\x00\x00"
    while len(data) % 4:
        data += b"\x00"
    strings_start = 28 + 4 * len(strings)
    size = strings_start + len(data)
    flags = 0x100 if utf8 else 0
    header = struct.pack("<HHIIIIII", 1, 28, size, len(strings), 0, flags, strings_start, 0)
    return header + struct.pack(f"<{len(strings)}I", *offsets) + bytes(data)


def encode_axml(root: Elem, *, utf8: bool = False, names_via_resource_map: bool = False) -> bytes:
    slots: list[str] = []
    slot_index: dict[str, int] = {}

    def collect(element: Elem) -> None:
        for key in element.attrs:
            name = key.lstrip("!")
            if name not in slot_index:
                slot_index[name] = len(slots)
                slots.append(name)
        for child in element.children:
            collect(child)

    collect(root)
    pool: list[str] = ["" if names_via_resource_map else name for name in slots]
    index: dict[str, int] = (
        {} if names_via_resource_map else {name: position for position, name in enumerate(slots)}
    )

    def intern(text: str) -> int:
        if text in index:
            return index[text]
        index[text] = len(pool)
        pool.append(text)
        return index[text]

    uri = intern(ANDROID_NAMESPACE)
    prefix = intern("android")

    def attribute(key: str, value: object) -> bytes:
        namespace = -1 if key.startswith("!") else uri
        name_index = slot_index[key.lstrip("!")]
        raw = -1
        if isinstance(value, Ref):
            value_type, data = _TYPE_REFERENCE, int(value)
        elif isinstance(value, bool):
            value_type, data = _TYPE_INT_BOOLEAN, (0xFFFFFFFF if value else 0)
        elif isinstance(value, int):
            value_type, data = _TYPE_INT_DEC, value
        else:
            raw = intern(str(value))
            value_type, data = _TYPE_STRING, raw
        return (
            struct.pack("<iii", namespace, name_index, raw)
            + struct.pack("<HBBI", 8, 0, value_type, data)
        )

    def encode(element: Elem) -> bytes:
        name_index = intern(element.name)
        attributes = b"".join(attribute(key, value) for key, value in element.attrs.items())
        size = 16 + 20 + len(attributes)
        chunk = (
            struct.pack("<HHI", 0x0102, 16, size)
            + struct.pack("<II", 1, 0xFFFFFFFF)
            + struct.pack("<ii", -1, name_index)
            + struct.pack("<HHHHHH", 20, 20, len(element.attrs), 0, 0, 0)
            + attributes
        )
        if element.text:
            text_index = intern(element.text)
            chunk += (
                struct.pack("<HHI", 0x0104, 16, 28)
                + struct.pack("<II", 1, 0xFFFFFFFF)
                + struct.pack("<i", text_index)
                + struct.pack("<HBBI", 8, 0, _TYPE_STRING, text_index)
            )
        for child in element.children:
            chunk += encode(child)
        chunk += (
            struct.pack("<HHI", 0x0103, 16, 24)
            + struct.pack("<II", 1, 0xFFFFFFFF)
            + struct.pack("<ii", -1, name_index)
        )
        return chunk

    body = (
        struct.pack("<HHI", 0x0100, 16, 24)
        + struct.pack("<II", 1, 0xFFFFFFFF)
        + struct.pack("<ii", prefix, uri)
        + encode(root)
        + struct.pack("<HHI", 0x0101, 16, 24)
        + struct.pack("<II", 1, 0xFFFFFFFF)
        + struct.pack("<ii", prefix, uri)
    )
    resource_map = b""
    if names_via_resource_map:
        ids = [ATTR_IDS.get(name, 0) for name in slots]
        resource_map = struct.pack("<HHI", 0x0180, 8, 8 + 4 * len(ids)) + struct.pack(
            f"<{len(ids)}I", *ids
        )
    pool_chunk = encode_string_pool(pool, utf8=utf8)
    total = 8 + len(pool_chunk) + len(resource_map) + len(body)
    return struct.pack("<HHI", 0x0003, 8, total) + pool_chunk + resource_map + body


# --------------------------------------------------------------------------
# resources.arsc encoding
# --------------------------------------------------------------------------


def encode_arsc(values: list[str], *, package_name: str = "com.acme.app") -> bytes:
    """One package, one type, one string entry per value (id 0x7f01000N)."""

    global_pool = encode_string_pool(values)
    type_pool = encode_string_pool(["string"])
    key_pool = encode_string_pool([f"key{position}" for position in range(len(values))])

    config_size = 56
    config = struct.pack("<I", config_size) + b"\x00" * (config_size - 4)
    header_size = 20 + config_size
    entries = b""
    offsets: list[int] = []
    for position in range(len(values)):
        offsets.append(len(entries))
        entries += struct.pack("<HHI", 8, 0, position) + struct.pack(
            "<HBBI", 8, 0, _TYPE_STRING, position
        )
    entries_start = header_size + 4 * len(values)
    type_chunk = (
        struct.pack("<HHI", 0x0201, header_size, entries_start + len(entries))
        + bytes([1, 0])
        + struct.pack("<H", 0)
        + struct.pack("<II", len(values), entries_start)
        + config
        + struct.pack(f"<{len(values)}I", *offsets)
        + entries
    )
    name_bytes = package_name.encode("utf-16-le")[:254].ljust(256, b"\x00")
    package_body = (
        struct.pack("<I", 0x7F)
        + name_bytes
        + struct.pack("<IIIII", 288, 0, 288 + len(type_pool), 0, 0)
    )
    package_chunk = (
        struct.pack(
            "<HHI",
            0x0200,
            288,
            288 + len(type_pool) + len(key_pool) + len(type_chunk),
        )
        + package_body
        + type_pool
        + key_pool
        + type_chunk
    )
    table_size = 12 + len(global_pool) + len(package_chunk)
    return struct.pack("<HHII", 0x0002, 12, table_size, 1) + global_pool + package_chunk


def resource_id(position: int) -> Ref:
    return Ref(0x7F010000 + position)


# --------------------------------------------------------------------------
# DEX, certificates, signing block
# --------------------------------------------------------------------------


def fake_dex(*, methods: int = 1200, classes: int = 90, version: bytes = b"035") -> bytes:
    header = bytearray(112)
    header[0:8] = b"dex\n" + version + b"\x00"
    struct.pack_into(
        "<12I", header, 56, 4000, 0, 800, 0, 600, 0, 900, 0, methods, 0, classes, 0
    )
    return bytes(header) + b"\x00" * 128


def _der(tag: int, payload: bytes) -> bytes:
    if len(payload) < 0x80:
        return bytes([tag, len(payload)]) + payload
    encoded = len(payload).to_bytes((len(payload).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(encoded)]) + encoded + payload


def _der_int(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return _der(0x02, raw)


def _der_oid(dotted: str) -> bytes:
    parts = [int(piece) for piece in dotted.split(".")]
    payload = bytes([parts[0] * 40 + parts[1]])
    for part in parts[2:]:
        chunk = bytes([part & 0x7F])
        part >>= 7
        while part:
            chunk = bytes([(part & 0x7F) | 0x80]) + chunk
            part >>= 7
        payload += chunk
    return _der(0x06, payload)


def _der_name(common_name: str) -> bytes:
    attribute = _der(0x30, _der_oid("2.5.4.3") + _der(0x13, common_name.encode("ascii")))
    return _der(0x30, _der(0x31, attribute))


def build_certificate(
    *,
    common_name: str = "CN Test",
    not_before: str = "200101000000Z",
    not_after: str = "300101000000Z",
    key_bits: int = 2048,
    algorithm: str = "1.2.840.113549.1.1.11",
) -> bytes:
    algorithm_id = _der(0x30, _der_oid(algorithm) + _der(0x05, b""))
    validity = _der(
        0x30, _der(0x17, not_before.encode("ascii")) + _der(0x17, not_after.encode("ascii"))
    )
    modulus = (1 << (key_bits - 1)) | 1
    public_key = _der(0x30, _der_int(modulus) + _der_int(65537))
    spki = _der(
        0x30,
        _der(0x30, _der_oid("1.2.840.113549.1.1.1") + _der(0x05, b""))
        + _der(0x03, b"\x00" + public_key),
    )
    tbs = _der(
        0x30,
        _der(0xA0, _der_int(2))
        + _der_int(0x1234)
        + algorithm_id
        + _der_name(common_name)
        + validity
        + _der_name(common_name)
        + spki,
    )
    return _der(0x30, tbs + algorithm_id + _der(0x03, b"\x00" + b"\xAA" * 32))


def _prefixed(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


def v2_signer_block(certificates: list[bytes]) -> bytes:
    certificate_sequence = b"".join(_prefixed(der) for der in certificates)
    signed_data = (
        _prefixed(b"digests") + _prefixed(certificate_sequence) + _prefixed(b"attributes")
    )
    signer = _prefixed(signed_data) + _prefixed(b"signatures") + _prefixed(b"publickey")
    # The block value is a length-prefixed sequence of length-prefixed signers.
    return _prefixed(_prefixed(signer))


def inject_signing_block(path: Path, pairs: dict[int, bytes]) -> None:
    data = bytearray(path.read_bytes())
    eocd = data.rfind(b"PK\x05\x06")
    cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    payload = b"".join(
        struct.pack("<Q", len(value) + 4) + struct.pack("<I", block_id) + value
        for block_id, value in pairs.items()
    )
    block_size = len(payload) + 24
    block = (
        struct.pack("<Q", block_size)
        + payload
        + struct.pack("<Q", block_size)
        + b"APK Sig Block 42"
    )
    patched = bytearray(data[:cd_offset] + block + data[cd_offset:])
    struct.pack_into("<I", patched, eocd + len(block) + 16, cd_offset + len(block))
    path.write_bytes(bytes(patched))


# --------------------------------------------------------------------------
# APK fixtures
# --------------------------------------------------------------------------


def component(
    kind: str,
    name: str,
    *,
    exported: bool | None = None,
    permission: str | None = None,
    launcher: bool = False,
    filters: bool = False,
    authorities: str | None = None,
    grant_uri: bool | None = None,
) -> Elem:
    attrs: dict[str, object] = {"name": name}
    if exported is not None:
        attrs["exported"] = exported
    if permission is not None:
        attrs["permission"] = permission
    if authorities is not None:
        attrs["authorities"] = authorities
    if grant_uri is not None:
        attrs["grantUriPermissions"] = grant_uri
    children: list[Elem] = []
    if filters or launcher:
        intent = Elem("intent-filter", {}, [Elem("action", {"name": "android.intent.action.MAIN"})])
        if launcher:
            intent.children.append(
                Elem("category", {"name": "android.intent.category.LAUNCHER"})
            )
        children.append(intent)
    return Elem(kind, attrs, children)


def manifest_element(
    *,
    package: str = "com.acme.app",
    version_code: int = 42,
    version_name: str = "1.2.3",
    min_sdk: int = 24,
    target_sdk: int = 34,
    application_attrs: dict[str, object] | None = None,
    permissions: tuple[str, ...] = ("android.permission.INTERNET",),
    defined_permissions: tuple[tuple[str, str], ...] = (),
    components: tuple[Elem, ...] = (),
) -> Elem:
    application = Elem(
        "application",
        {"label": "Acme", "name": ".App", **(application_attrs or {})},
        list(components),
    )
    children = [Elem("uses-sdk", {"minSdkVersion": min_sdk, "targetSdkVersion": target_sdk})]
    children += [Elem("uses-permission", {"name": name}) for name in permissions]
    children += [
        Elem("permission", {"name": name, "protectionLevel": level})
        for name, level in defined_permissions
    ]
    children.append(application)
    return Elem(
        "manifest",
        {"!package": package, "versionCode": version_code, "versionName": version_name},
        children,
    )


def write_apk(
    directory: Path,
    *,
    manifest: Elem | None = None,
    manifest_bytes: bytes | None = None,
    extra: dict[str, bytes] | None = None,
    name: str = "app.apk",
    stored: frozenset[str] = frozenset(),
) -> Path:
    path = directory / name
    payload = manifest_bytes
    if payload is None:
        payload = encode_axml(manifest if manifest is not None else manifest_element())
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", payload)
        for entry_name, blob in (extra or {}).items():
            method = zipfile.ZIP_STORED if entry_name in stored else zipfile.ZIP_DEFLATED
            archive.writestr(zipfile.ZipInfo(entry_name), blob, compress_type=method)
    return path


def make_context(tmp_path: Path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


async def analyze(tmp_path: Path, path: Path, **arguments: object) -> dict:
    tool = AnalyzeApkTool()
    return await tool.execute(
        {"path": path.name, **arguments}, make_context(tmp_path)
    )


def finding_ids(result: dict) -> set[str]:
    return {finding["id"] for finding in result["findings"]}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_tool_declares_read_capability_and_strict_schema() -> None:
    tool = AnalyzeApkTool()
    assert tool.capabilities == frozenset({ToolCapability.LOCAL_READ})
    assert tool.input_schema["required"] == list(tool.input_schema["properties"].keys())
    assert tool.input_schema["additionalProperties"] is False


async def test_reports_manifest_facts(tmp_path) -> None:
    manifest = manifest_element(
        components=(
            component("activity", ".MainActivity", exported=True, launcher=True),
            component("service", ".SyncService", exported=False),
        ),
        permissions=("android.permission.INTERNET", "android.permission.CAMERA"),
    )
    path = write_apk(tmp_path, manifest=manifest)

    result = await analyze(tmp_path, path)

    app = result["app"]
    assert app["package"] == "com.acme.app"
    assert app["version_code"] == 42
    assert app["version_name"] == "1.2.3"
    assert app["min_sdk"] == 24
    assert app["target_sdk"] == 34
    assert app["application_class"] == "com.acme.app.App"
    assert result["components"]["counts"] == {"activity": 1, "service": 1}
    assert result["components"]["launcher_activities"] == ["com.acme.app.MainActivity"]
    assert result["permissions"]["used_count"] == 2
    assert "com.acme.app" in result["summary"]
    assert len(result["sha256"]) == 64


async def test_resolves_label_through_resources_arsc(tmp_path) -> None:
    manifest = manifest_element(application_attrs={"label": resource_id(0)})
    path = write_apk(
        tmp_path, manifest=manifest, extra={"resources.arsc": encode_arsc(["Acme App"])}
    )

    result = await analyze(tmp_path, path)

    assert result["app"]["label"] == "Acme App"


async def test_unresolved_reference_stays_readable(tmp_path) -> None:
    manifest = manifest_element(application_attrs={"label": resource_id(0)})
    path = write_apk(tmp_path, manifest=manifest)

    result = await analyze(tmp_path, path)

    assert result["app"]["label"] == "@0x7f010000"


async def test_flags_debuggable_and_unprotected_exported_components(tmp_path) -> None:
    manifest = manifest_element(
        application_attrs={"debuggable": True, "allowBackup": True},
        components=(
            component("receiver", ".BootReceiver", exported=True),
            component("provider", ".Files", exported=True, authorities="com.acme.files"),
        ),
    )
    path = write_apk(tmp_path, manifest=manifest)

    result = await analyze(tmp_path, path)
    ids = finding_ids(result)

    assert "debuggable" in ids
    assert "backup_allowed" in ids
    assert "exported_receiver_unprotected" in ids
    assert "exported_provider_unprotected" in ids
    debuggable = next(item for item in result["findings"] if item["id"] == "debuggable")
    assert debuggable["severity"] == "high"
    assert result["findings"][0]["severity"] in {"critical", "high"}


async def test_permission_protected_component_is_not_flagged(tmp_path) -> None:
    manifest = manifest_element(
        components=(
            component(
                "service",
                ".Api",
                exported=True,
                permission="com.acme.permission.CALL",
            ),
        ),
        defined_permissions=(("com.acme.permission.CALL", "signature"),),
    )
    path = write_apk(tmp_path, manifest=manifest)

    result = await analyze(tmp_path, path)

    assert "exported_service_unprotected" not in finding_ids(result)
    assert "weak_custom_permission" not in finding_ids(result)


async def test_normal_level_custom_permission_is_flagged(tmp_path) -> None:
    manifest = manifest_element(
        components=(
            component("service", ".Api", exported=True, permission="com.acme.permission.CALL"),
        ),
        defined_permissions=(("com.acme.permission.CALL", "normal"),),
    )
    path = write_apk(tmp_path, manifest=manifest)

    result = await analyze(tmp_path, path)

    assert "weak_custom_permission" in finding_ids(result)


async def test_missing_exported_declaration_on_android_12(tmp_path) -> None:
    manifest = manifest_element(
        target_sdk=33,
        components=(component("activity", ".MainActivity", launcher=True),),
    )
    path = write_apk(tmp_path, manifest=manifest)

    result = await analyze(tmp_path, path)
    finding = next(
        item for item in result["findings"] if item["id"] == "missing_exported_declaration"
    )

    assert finding["severity"] == "high"
    assert finding["evidence"] == ["com.acme.app.MainActivity"]
    # It is still exported: a filtered activity defaults to exported.
    assert result["components"]["exported_count"] == 1


async def test_old_target_sdk_and_cleartext_defaults(tmp_path) -> None:
    manifest = manifest_element(min_sdk=19, target_sdk=26)
    path = write_apk(tmp_path, manifest=manifest)

    ids = finding_ids(await analyze(tmp_path, path))

    assert "target_sdk_outdated" in ids
    assert "cleartext_traffic_default" in ids
    assert "min_sdk_legacy" in ids


async def test_network_security_config_findings(tmp_path) -> None:
    config = Elem(
        "network-security-config",
        {},
        [
            Elem(
                "domain-config",
                {"!cleartextTrafficPermitted": True},
                [
                    Elem("domain", {"!includeSubdomains": True}, text="api.acme.test"),
                    Elem(
                        "trust-anchors",
                        {},
                        [Elem("certificates", {"!src": "user"})],
                    ),
                ],
            )
        ],
    )
    manifest = manifest_element(
        application_attrs={"networkSecurityConfig": resource_id(0)},
    )
    path = write_apk(
        tmp_path,
        manifest=manifest,
        extra={
            "resources.arsc": encode_arsc(["res/xml/network_security_config.xml"]),
            "res/xml/network_security_config.xml": encode_axml(config),
        },
    )

    result = await analyze(tmp_path, path)
    ids = finding_ids(result)

    assert "cleartext_traffic_config" in ids
    assert "user_ca_trusted" in ids
    detail = result["app"]["network_security_config_detail"]
    assert detail["cleartext_permitted"] == ["api.acme.test"]


async def test_contents_dex_and_framework_summary(tmp_path) -> None:
    path = write_apk(
        tmp_path,
        extra={
            "classes.dex": fake_dex(methods=1200, classes=90),
            "classes2.dex": fake_dex(methods=63000, classes=400),
            "lib/armeabi-v7a/libflutter.so": b"\x7fELF" + b"\x00" * 512,
            "assets/flutter_assets/app.bin": b"\x00" * 128,
        },
    )

    result = await analyze(tmp_path, path)

    assert result["dex"]["count"] == 2
    assert result["dex"]["total_methods"] == 64200
    assert result["dex"]["total_classes"] == 490
    assert result["contents"]["abis"] == ["armeabi-v7a"]
    assert "Flutter" in result["contents"]["frameworks"]
    assert result["contents"]["categories"]["dex"]["count"] == 2
    ids = finding_ids(result)
    assert "no_64bit_abi" in ids
    assert "dex_method_pressure" in ids


async def test_uncompressed_native_libs_are_not_flagged(tmp_path) -> None:
    path = write_apk(
        tmp_path,
        extra={"lib/arm64-v8a/libnative.so": b"\x7fELF" + b"\x00" * 512},
        stored=frozenset({"lib/arm64-v8a/libnative.so"}),
    )

    ids = finding_ids(await analyze(tmp_path, path))

    assert "compressed_native_libs" not in ids
    assert "no_64bit_abi" not in ids


async def test_unsigned_apk_is_critical(tmp_path) -> None:
    path = write_apk(tmp_path)

    result = await analyze(tmp_path, path)

    assert result["signing"]["signed"] is False
    assert "unsigned" in finding_ids(result)
    assert result["findings"][0]["severity"] == "critical"


async def test_v1_only_signature(tmp_path) -> None:
    path = write_apk(
        tmp_path,
        extra={
            "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n",
            "META-INF/CERT.SF": b"Signature-Version: 1.0\n",
            "META-INF/CERT.RSA": b"not-a-real-pkcs7",
        },
    )

    result = await analyze(tmp_path, path)

    assert result["signing"]["schemes"] == ["v1"]
    assert result["signing"]["verified"] is False
    assert "v1_only_signature" in finding_ids(result)


async def test_v2_signature_certificate_is_read(tmp_path) -> None:
    path = write_apk(tmp_path)
    certificate = build_certificate(
        common_name="Android Debug",
        not_before="180101000000Z",
        not_after="190101000000Z",
        key_bits=1024,
        algorithm="1.2.840.113549.1.1.5",
    )
    inject_signing_block(path, {0x7109871A: v2_signer_block([certificate])})

    result = await analyze(tmp_path, path)
    signing = result["signing"]
    ids = finding_ids(result)

    assert signing["schemes"] == ["v2"]
    assert signing["signer_count"] == 1
    entry = signing["certificates"][0]
    assert entry["subject"] == "CN=Android Debug"
    assert entry["signature_algorithm"] == "SHA1withRSA"
    assert entry["key_algorithm"] == "RSA"
    assert entry["key_bits"] == 1024
    assert entry["valid_until"] == "2019-01-01T00:00:00Z"
    assert entry["self_signed"] is True
    assert len(entry["sha256"]) == 64
    assert {"debug_certificate", "weak_signature_algorithm", "small_signing_key"} <= ids
    assert "certificate_expired" in ids
    assert "v1_only_signature" not in ids


async def test_utf8_string_pool_is_supported(tmp_path) -> None:
    manifest = manifest_element(package="com.acme.utf8")
    path = write_apk(tmp_path, manifest_bytes=encode_axml(manifest, utf8=True))

    result = await analyze(tmp_path, path)

    assert result["app"]["package"] == "com.acme.utf8"


async def test_attribute_names_recovered_from_resource_map(tmp_path) -> None:
    manifest = manifest_element(
        application_attrs={"debuggable": True},
        components=(component("activity", ".MainActivity", exported=True),),
    )
    path = write_apk(
        tmp_path, manifest_bytes=encode_axml(manifest, names_via_resource_map=True)
    )

    result = await analyze(tmp_path, path)

    assert result["app"]["min_sdk"] == 24
    assert result["app"]["debuggable"] is True
    assert result["components"]["exported_count"] == 1


async def test_sections_can_be_narrowed(tmp_path) -> None:
    path = write_apk(tmp_path)

    result = await analyze(tmp_path, path, sections=["manifest"])

    assert "app" in result
    assert "components" not in result
    assert "signing" not in result
    assert "findings" in result


async def test_findings_only_report(tmp_path) -> None:
    path = write_apk(tmp_path)

    result = await analyze(tmp_path, path, sections=[])

    assert set(result) >= {"findings", "summary", "sha256"}
    assert "app" not in result


async def test_min_severity_filters_findings(tmp_path) -> None:
    manifest = manifest_element(
        permissions=("android.permission.CAMERA",),
        application_attrs={"debuggable": True},
    )
    path = write_apk(tmp_path, manifest=manifest)

    everything = await analyze(tmp_path, path)
    filtered = await analyze(tmp_path, path, min_severity="high")

    assert "sensitive_permissions" in finding_ids(everything)
    assert all(item["severity"] in {"critical", "high"} for item in filtered["findings"])
    assert "debuggable" in finding_ids(filtered)


async def test_rejects_unknown_section(tmp_path) -> None:
    path = write_apk(tmp_path)

    with pytest.raises(ToolArgumentError):
        await analyze(tmp_path, path, sections=["manifest", "nope"])


async def test_missing_path_argument(tmp_path) -> None:
    tool = AnalyzeApkTool()

    with pytest.raises(ToolArgumentError):
        await tool.execute({"path": "   "}, make_context(tmp_path))


async def test_zip_without_manifest_is_rejected(tmp_path) -> None:
    path = tmp_path / "plain.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "hello")

    with pytest.raises(ToolExecutionError, match="not an APK"):
        await analyze(tmp_path, path)


async def test_app_bundle_is_reported_as_such(tmp_path) -> None:
    path = tmp_path / "app.aab"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BundleConfig.pb", b"\x00")
        archive.writestr("base/manifest/AndroidManifest.xml", b"\x00")

    with pytest.raises(ToolExecutionError, match="bundletool"):
        await analyze(tmp_path, path)


async def test_non_zip_file_is_rejected(tmp_path) -> None:
    path = tmp_path / "app.apk"
    path.write_bytes(b"definitely not a zip")

    with pytest.raises(ToolExecutionError, match="not a readable ZIP"):
        await analyze(tmp_path, path)


async def test_plain_text_manifest_is_reported(tmp_path) -> None:
    path = write_apk(tmp_path, manifest_bytes=b"<manifest package='com.acme.app'/>")

    with pytest.raises(ToolExecutionError, match="plain text"):
        await analyze(tmp_path, path)
