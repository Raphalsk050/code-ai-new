from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from code_ai.core.errors import ToolExecutionError
from code_ai.tools.apk.arsc import ResourceTable, parse_resource_table
from code_ai.tools.apk.axml import AxmlElement, parse_axml
from code_ai.tools.apk.dex import DEX_METHOD_LIMIT, DexInfo, parse_dex_header
from code_ai.tools.apk.manifest import Component, ManifestInfo, parse_manifest
from code_ai.tools.apk.models import ApkReport, Finding, Severity
from code_ai.tools.apk.signing import SigningInfo, analyze_signing

SECTIONS: tuple[str, ...] = (
    "manifest",
    "permissions",
    "components",
    "signing",
    "contents",
    "dex",
)

# Google Play's rolling requirement for new apps and updates. Below this an
# upload is refused outright, which is a build problem rather than a taste one.
PLAY_TARGET_SDK_FLOOR = 34
# Below this the app runs on releases that predate scoped storage and the
# modern TLS defaults.
LEGACY_MIN_SDK = 21

_MANIFEST_NAME = "AndroidManifest.xml"
_MAX_MANIFEST_BYTES = 32 * 1024 * 1024
_MAX_ARSC_BYTES = 96 * 1024 * 1024
_MAX_NSC_BYTES = 4 * 1024 * 1024
_MAX_FINDINGS = 40
_HASH_CHUNK = 1024 * 1024

_ABI_64_BIT = frozenset({"arm64-v8a", "x86_64", "mips64", "riscv64"})

# Permissions worth naming back to the reader: either they gate personal data
# or Play asks for a separate declaration before it will accept the upload.
_SENSITIVE_PERMISSIONS: dict[str, str] = {
    "android.permission.READ_SMS": "reads SMS",
    "android.permission.RECEIVE_SMS": "receives SMS",
    "android.permission.SEND_SMS": "sends SMS",
    "android.permission.READ_CONTACTS": "reads contacts",
    "android.permission.READ_CALL_LOG": "reads the call log",
    "android.permission.PROCESS_OUTGOING_CALLS": "intercepts outgoing calls",
    "android.permission.ACCESS_FINE_LOCATION": "precise location",
    "android.permission.ACCESS_BACKGROUND_LOCATION": "background location",
    "android.permission.RECORD_AUDIO": "microphone",
    "android.permission.CAMERA": "camera",
    "android.permission.READ_PHONE_STATE": "phone identity/state",
    "android.permission.QUERY_ALL_PACKAGES": "lists every installed app (Play declaration)",
    "android.permission.REQUEST_INSTALL_PACKAGES": "installs other APKs (Play declaration)",
    "android.permission.MANAGE_EXTERNAL_STORAGE": "full storage access (Play declaration)",
    "android.permission.SYSTEM_ALERT_WINDOW": "draws over other apps",
    "android.permission.WRITE_SETTINGS": "changes system settings",
    "android.permission.SCHEDULE_EXACT_ALARM": "exact alarms (Play declaration)",
    "android.permission.BIND_ACCESSIBILITY_SERVICE": "accessibility service",
}

_FRAMEWORK_MARKERS: tuple[tuple[str, str], ...] = (
    ("libflutter.so", "Flutter"),
    ("flutter_assets/", "Flutter"),
    ("libreactnativejni.so", "React Native"),
    ("libreactnative.so", "React Native"),
    ("index.android.bundle", "React Native"),
    ("libhermes.so", "Hermes (React Native)"),
    ("libunity.so", "Unity"),
    ("libil2cpp.so", "Unity (IL2CPP)"),
    ("libgodot", "Godot"),
    ("libmonodroid.so", "Xamarin / .NET"),
    ("libmonosgen", "Xamarin / .NET"),
    ("assets/www/cordova.js", "Cordova / Ionic"),
    ("libcocos2d", "Cocos2d-x"),
    ("kotlin/kotlin.kotlin_builtins", "Kotlin"),
    ("androidx.compose.ui_ui.version", "Jetpack Compose"),
)


@dataclass(slots=True)
class AnalysisOptions:
    sections: frozenset[str] = frozenset(SECTIONS)
    max_components: int = 25
    max_files: int = 15
    max_permissions: int = 80
    min_severity: Severity | None = None


@dataclass(slots=True)
class _Contents:
    file_count: int = 0
    compressed_bytes: int = 0
    uncompressed_bytes: int = 0
    categories: dict[str, dict[str, int]] = field(default_factory=dict)
    abis: list[str] = field(default_factory=list)
    stored_native_libs: int = 0
    largest: list[dict[str, object]] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    dex_names: list[str] = field(default_factory=list)


class ApkAnalyzer:
    """Reads a built APK and reports what shipped inside it.

    The analysis is static and offline: the archive is parsed in place, nothing
    is executed, extracted, or installed, and no signature is verified
    cryptographically. What it produces is the set of facts a reviewer would
    otherwise collect with aapt, apksigner and unzip, plus the findings those
    facts imply.
    """

    def analyze(self, path: Path, *, display_path: str, options: AnalysisOptions) -> ApkReport:
        notes: list[str] = []
        size = path.stat().st_size
        digest = _sha256_file(path)

        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise ToolExecutionError(
                f"{display_path} is not a readable ZIP archive, so it is not an APK: {exc}"
            ) from exc

        with archive:
            names = set(archive.namelist())
            _reject_non_apk(display_path, names)
            manifest_root = self._read_manifest(archive, display_path)
            table = self._read_resource_table(archive, notes)
            manifest = parse_manifest(manifest_root, table)
            contents = self._read_contents(archive, options)
            dex_files = self._read_dex(archive, contents.dex_names)
            signing = analyze_signing(path, archive)
            network_config = self._read_network_security_config(archive, manifest, table, notes)

        findings = _build_findings(manifest, signing, contents, dex_files, network_config)
        if options.min_severity is not None:
            floor = options.min_severity.rank
            findings = [finding for finding in findings if finding.severity.rank >= floor]
        findings.sort(key=lambda finding: (-finding.severity.rank, finding.id))
        if len(findings) > _MAX_FINDINGS:
            notes.append(f"{len(findings) - _MAX_FINDINGS} lower-severity findings were dropped.")
            findings = findings[:_MAX_FINDINGS]

        sections = self._build_sections(
            manifest, signing, contents, dex_files, network_config, options
        )
        return ApkReport(
            path=display_path,
            size_bytes=size,
            sha256=digest,
            sections=sections,
            findings=findings,
            summary=_summarize(manifest, signing, size, findings),
            notes=notes,
        )

    def _read_manifest(self, archive: zipfile.ZipFile, display_path: str) -> AxmlElement:
        try:
            entry = archive.getinfo(_MANIFEST_NAME)
        except KeyError as exc:
            raise ToolExecutionError(
                f"{display_path} has no AndroidManifest.xml at its root, so it is not an APK."
            ) from exc
        if entry.file_size > _MAX_MANIFEST_BYTES:
            raise ToolExecutionError("AndroidManifest.xml is implausibly large; refusing to read.")
        return parse_axml(archive.read(_MANIFEST_NAME))

    def _read_resource_table(
        self, archive: zipfile.ZipFile, notes: list[str]
    ) -> ResourceTable | None:
        try:
            entry = archive.getinfo("resources.arsc")
        except KeyError:
            return None
        if entry.file_size > _MAX_ARSC_BYTES:
            notes.append("resources.arsc was too large to parse; @references stay unresolved.")
            return None
        try:
            return parse_resource_table(archive.read("resources.arsc"))
        except (OSError, ValueError, zipfile.BadZipFile):
            notes.append("resources.arsc could not be parsed; @references stay unresolved.")
            return None

    def _read_contents(self, archive: zipfile.ZipFile, options: AnalysisOptions) -> _Contents:
        contents = _Contents()
        abis: set[str] = set()
        frameworks: set[str] = set()
        entries: list[tuple[int, str, int, bool]] = []

        for entry in archive.infolist():
            if entry.is_dir():
                continue
            contents.file_count += 1
            contents.compressed_bytes += entry.compress_size
            contents.uncompressed_bytes += entry.file_size
            name = entry.filename
            lowered = name.lower()
            category = _categorize(lowered)
            bucket = contents.categories.setdefault(category, {"count": 0, "bytes": 0})
            bucket["count"] += 1
            bucket["bytes"] += entry.file_size
            entries.append((entry.file_size, name, entry.compress_size, entry.compress_type == 0))

            if category == "dex":
                contents.dex_names.append(name)
            if lowered.startswith("lib/"):
                parts = name.split("/")
                if len(parts) > 2:
                    abis.add(parts[1])
                if entry.compress_type == zipfile.ZIP_STORED:
                    contents.stored_native_libs += 1
            for marker, framework in _FRAMEWORK_MARKERS:
                if marker in lowered:
                    frameworks.add(framework)

        contents.abis = sorted(abis)
        contents.frameworks = sorted(frameworks)
        contents.dex_names.sort()
        entries.sort(key=lambda item: item[0], reverse=True)
        contents.largest = [
            {
                "name": name,
                "size_bytes": size,
                "compressed_bytes": compressed,
                "stored": stored,
            }
            for size, name, compressed, stored in entries[: options.max_files]
        ]
        return contents

    def _read_dex(self, archive: zipfile.ZipFile, names: list[str]) -> list[DexInfo]:
        parsed: list[DexInfo] = []
        for name in names:
            try:
                entry = archive.getinfo(name)
                with archive.open(name) as handle:
                    # Only the fixed-size header is needed, so a 20 MB dex is
                    # never inflated in full.
                    header = handle.read(112)
            except (KeyError, OSError, zipfile.BadZipFile):
                continue
            info = parse_dex_header(header, name=name, size=entry.file_size)
            if info is not None:
                parsed.append(info)
        return parsed

    def _read_network_security_config(
        self,
        archive: zipfile.ZipFile,
        manifest: ManifestInfo,
        table: ResourceTable | None,
        notes: list[str],
    ) -> dict[str, object] | None:
        reference = manifest.network_security_config
        if not reference:
            return None
        if reference.startswith("@"):
            notes.append(
                "android:networkSecurityConfig points at a resource that could not be resolved."
            )
            return None
        names = set(archive.namelist())
        candidates = [reference, reference.lstrip("/")]
        entry_name = next((name for name in candidates if name in names), None)
        if entry_name is None:
            return None
        try:
            entry = archive.getinfo(entry_name)
            if entry.file_size > _MAX_NSC_BYTES:
                return None
            root = parse_axml(archive.read(entry_name))
        except (KeyError, OSError, ToolExecutionError, zipfile.BadZipFile):
            notes.append(f"{entry_name} could not be decoded.")
            return None
        summary = _summarize_network_config(root)
        summary["file"] = entry_name
        return summary

    def _build_sections(
        self,
        manifest: ManifestInfo,
        signing: SigningInfo,
        contents: _Contents,
        dex_files: list[DexInfo],
        network_config: dict[str, object] | None,
        options: AnalysisOptions,
    ) -> dict[str, object]:
        sections: dict[str, object] = {}
        if "manifest" in options.sections:
            app = manifest.to_dict()
            if network_config is not None:
                app["network_security_config_detail"] = network_config
            if manifest.metadata:
                app["meta_data"] = manifest.metadata
            if manifest.libraries:
                app["libraries"] = manifest.libraries
            if manifest.features:
                app["features"] = manifest.features
            sections["app"] = app
        if "permissions" in options.sections:
            used = manifest.permissions_used
            sections["permissions"] = {
                "used_count": len(used),
                "used": used[: options.max_permissions],
                "used_truncated": len(used) > options.max_permissions,
                "defined": manifest.permissions_defined[: options.max_permissions],
            }
        if "components" in options.sections:
            sections["components"] = _components_section(manifest, options.max_components)
        if "signing" in options.sections:
            sections["signing"] = signing.to_dict()
        if "contents" in options.sections:
            sections["contents"] = {
                "file_count": contents.file_count,
                "uncompressed_bytes": contents.uncompressed_bytes,
                "compressed_bytes": contents.compressed_bytes,
                "categories": contents.categories,
                "abis": contents.abis,
                "frameworks": contents.frameworks,
                "largest_entries": contents.largest,
            }
        if "dex" in options.sections:
            sections["dex"] = {
                "count": len(dex_files),
                "total_classes": sum(info.class_defs for info in dex_files),
                "total_methods": sum(info.method_ids for info in dex_files),
                "files": [info.to_dict() for info in dex_files],
            }
        return sections


def _components_section(manifest: ManifestInfo, limit: int) -> dict[str, object]:
    counts: dict[str, int] = {}
    exported: list[Component] = []
    for component in manifest.components:
        counts[component.kind] = counts.get(component.kind, 0) + 1
        if component.exported:
            exported.append(component)
    launchers = [component.name for component in manifest.components if component.launcher]
    return {
        "counts": counts,
        "total": len(manifest.components),
        "launcher_activities": launchers,
        "exported_count": len(exported),
        "exported": [component.to_dict() for component in exported[:limit]],
        "exported_truncated": len(exported) > limit,
    }


def _build_findings(
    manifest: ManifestInfo,
    signing: SigningInfo,
    contents: _Contents,
    dex_files: list[DexInfo],
    network_config: dict[str, object] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    findings += _manifest_findings(manifest)
    findings += _network_findings(manifest, network_config)
    findings += _component_findings(manifest)
    findings += _signing_findings(signing)
    findings += _packaging_findings(manifest, contents, dex_files)
    return findings


def _manifest_findings(manifest: ManifestInfo) -> list[Finding]:
    findings: list[Finding] = []
    if manifest.debuggable:
        findings.append(
            Finding(
                id="debuggable",
                severity=Severity.HIGH,
                title="Ships with android:debuggable=true",
                detail=(
                    "Anyone can attach a debugger to the app on a stock device and read or "
                    "change its memory and data. This is a debug build."
                ),
                recommendation=(
                    "Remove android:debuggable from the manifest and rebuild in release."
                ),
            )
        )
    if manifest.test_only:
        findings.append(
            Finding(
                id="test_only",
                severity=Severity.MEDIUM,
                title="Marked android:testOnly=true",
                detail=(
                    "The package installs only with 'adb install -t' and Play will reject it. "
                    "Android Studio's Run button produces this."
                ),
                recommendation=(
                    "Build with Gradle (assembleRelease/bundleRelease) for anything shipped."
                ),
            )
        )
    if manifest.allow_backup is not False:
        explicit = manifest.allow_backup is True
        findings.append(
            Finding(
                id="backup_allowed",
                severity=Severity.MEDIUM if explicit else Severity.LOW,
                title="App data can be backed up",
                detail=(
                    "android:allowBackup is "
                    + ("true" if explicit else "unset, which defaults to true")
                    + ", so app data can be pulled off a device with adb backup and restored "
                    "onto another one."
                ),
                recommendation=(
                    "Set android:allowBackup=\"false\", or restrict what is backed up with "
                    "android:dataExtractionRules."
                ),
            )
        )
    if manifest.shared_user_id:
        findings.append(
            Finding(
                id="shared_user_id",
                severity=Severity.LOW,
                title="Uses the deprecated android:sharedUserId",
                detail=(
                    f"Shares a UID as {manifest.shared_user_id!r}. The attribute is deprecated "
                    "and cannot be removed later without breaking upgrades."
                ),
            )
        )
    if manifest.request_legacy_external_storage:
        findings.append(
            Finding(
                id="legacy_external_storage",
                severity=Severity.LOW,
                title="Opts out of scoped storage",
                detail=(
                    "android:requestLegacyExternalStorage=true is ignored from API 30 onwards, "
                    "so the app breaks on modern devices if it depends on it."
                ),
            )
        )
    if manifest.target_sdk is not None and manifest.target_sdk < PLAY_TARGET_SDK_FLOOR:
        findings.append(
            Finding(
                id="target_sdk_outdated",
                severity=Severity.MEDIUM,
                title=f"targetSdkVersion {manifest.target_sdk} is below Play's floor",
                detail=(
                    f"Google Play refuses new uploads below API {PLAY_TARGET_SDK_FLOOR}, and an "
                    "old target opts the app out of the platform's newer privacy defaults."
                ),
                recommendation=(
                    "Raise targetSdkVersion and retest the behaviour changes it enables."
                ),
            )
        )
    if manifest.min_sdk is not None and manifest.min_sdk < LEGACY_MIN_SDK:
        findings.append(
            Finding(
                id="min_sdk_legacy",
                severity=Severity.LOW,
                title=f"minSdkVersion {manifest.min_sdk} covers pre-Lollipop devices",
                detail=(
                    "Those releases have no scoped storage, no modern TLS defaults, and no "
                    "security updates."
                ),
            )
        )
    return findings


def _network_findings(
    manifest: ManifestInfo, network_config: dict[str, object] | None
) -> list[Finding]:
    findings: list[Finding] = []
    config = network_config or {}
    cleartext_domains = list(config.get("cleartext_permitted", []))
    user_ca_scopes = list(config.get("user_ca_trusted", []))

    if manifest.uses_cleartext_traffic:
        findings.append(
            Finding(
                id="cleartext_traffic",
                severity=Severity.MEDIUM,
                title="Cleartext HTTP is enabled app-wide",
                detail=(
                    "android:usesCleartextTraffic=true lets every request fall back to plain "
                    "HTTP, which anyone on the same network can read or rewrite."
                ),
                recommendation=(
                    "Drop the attribute and allow-list any HTTP host in a network security "
                    "config."
                ),
            )
        )
    elif (
        manifest.uses_cleartext_traffic is None
        and not manifest.network_security_config
        and manifest.target_sdk is not None
        and manifest.target_sdk < 28
    ):
        findings.append(
            Finding(
                id="cleartext_traffic_default",
                severity=Severity.MEDIUM,
                title="Cleartext HTTP is permitted by default",
                detail=(
                    f"targetSdk {manifest.target_sdk} predates API 28, where cleartext became "
                    "opt-in, and no network security config narrows it."
                ),
                recommendation="Raise targetSdkVersion, or ship a network security config.",
            )
        )
    if cleartext_domains:
        findings.append(
            Finding(
                id="cleartext_traffic_config",
                severity=Severity.MEDIUM,
                title="Network security config permits cleartext",
                detail="The shipped network security config allows plain HTTP for these scopes.",
                evidence=cleartext_domains[:10],
            )
        )
    if user_ca_scopes:
        findings.append(
            Finding(
                id="user_ca_trusted",
                severity=Severity.MEDIUM,
                title="Trusts user-installed certificate authorities",
                detail=(
                    "Outside debug-overrides, trusting the user CA store means anyone who can "
                    "install a certificate on the device can read the app's TLS traffic."
                ),
                evidence=user_ca_scopes[:10],
            )
        )
    return findings


def _component_findings(manifest: ManifestInfo) -> list[Finding]:
    findings: list[Finding] = []
    weak_permissions = {
        str(entry.get("name")): str(entry.get("protection_level") or "normal")
        for entry in manifest.permissions_defined
    }

    undeclared = [
        component.name
        for component in manifest.components
        if component.declared_exported is None and component.intent_filters
    ]
    if undeclared and manifest.target_sdk is not None and manifest.target_sdk >= 31:
        findings.append(
            Finding(
                id="missing_exported_declaration",
                severity=Severity.HIGH,
                title="Components with intent filters do not declare android:exported",
                detail=(
                    "From Android 12 (API 31) the installer rejects a package whose filtered "
                    "components leave android:exported unset."
                ),
                evidence=undeclared[:10],
                recommendation=(
                    "Declare android:exported explicitly on every component with an "
                    "intent-filter."
                ),
            )
        )

    by_kind: dict[str, list[Component]] = {}
    for component in manifest.components:
        if component.exported and not component.protected and component.enabled is not False:
            by_kind.setdefault(component.kind, []).append(component)
    for kind, components in sorted(by_kind.items()):
        severity = Severity.HIGH if kind == "provider" else Severity.MEDIUM
        evidence = [
            component.name + (" (exported by default)" if component.exported_by_default else "")
            for component in components[:10]
        ]
        findings.append(
            Finding(
                id=f"exported_{kind.replace('-', '_')}_unprotected",
                severity=severity,
                title=f"{len(components)} exported {kind}(s) with no permission",
                detail=(
                    f"Any app on the device can reach {'these' if len(components) > 1 else 'this'} "
                    f"{kind}(s) directly. Confirm each one is meant to be part of the app's "
                    "public surface."
                ),
                evidence=evidence,
                recommendation=(
                    'Set android:exported="false" where it is not needed, or guard it with a '
                    "signature-level permission."
                ),
            )
        )

    weakly_guarded = [
        f"{component.name} (permission {component.permission}: "
        f"{weak_permissions.get(component.permission or '', 'normal')})"
        for component in manifest.components
        if component.exported
        and component.permission
        and component.permission in weak_permissions
        and "signature" not in weak_permissions.get(component.permission, "normal")
    ]
    if weakly_guarded:
        findings.append(
            Finding(
                id="weak_custom_permission",
                severity=Severity.MEDIUM,
                title="Exported components are guarded by a non-signature permission",
                detail=(
                    "A custom permission at normal or dangerous level is granted to any app "
                    "that asks for it, so it does not restrict who can call the component."
                ),
                evidence=weakly_guarded[:10],
                recommendation='Declare the permission with android:protectionLevel="signature".',
            )
        )

    grant_uri = [
        component.name
        for component in manifest.components
        if component.kind == "provider" and component.exported and component.grant_uri_permissions
    ]
    if grant_uri:
        findings.append(
            Finding(
                id="provider_grants_uri_permissions",
                severity=Severity.HIGH,
                title="Exported provider grants URI permissions",
                detail=(
                    "android:grantUriPermissions=true on an exported provider lets a caller be "
                    "handed access to any URI it serves, including ones it should not reach."
                ),
                evidence=grant_uri[:10],
            )
        )

    sensitive = [
        f"{name} - {_SENSITIVE_PERMISSIONS[name]}"
        for name in (str(entry.get("name")) for entry in manifest.permissions_used)
        if name in _SENSITIVE_PERMISSIONS
    ]
    if sensitive:
        findings.append(
            Finding(
                id="sensitive_permissions",
                severity=Severity.INFO,
                title=f"Requests {len(sensitive)} sensitive permission(s)",
                detail=(
                    "Listed so the request can be matched against what the app actually does; "
                    "the ones marked 'Play declaration' need a form filled in before upload."
                ),
                evidence=sensitive[:15],
            )
        )
    return findings


def _signing_findings(signing: SigningInfo) -> list[Finding]:
    findings: list[Finding] = []
    if not signing.signed:
        findings.append(
            Finding(
                id="unsigned",
                severity=Severity.CRITICAL,
                title="No signature found",
                detail="Without a v1, v2 or v3 signature Android refuses to install the package.",
                recommendation="Sign the APK with apksigner before distributing it.",
            )
        )
        return findings

    modern = {"v2", "v3", "v3.1"} & set(signing.schemes)
    if not modern:
        findings.append(
            Finding(
                id="v1_only_signature",
                severity=Severity.MEDIUM,
                title="Signed with the v1 JAR scheme only",
                detail=(
                    "v1 covers file contents but not the archive layout, which is what the "
                    "Janus class of attacks abused, and it installs more slowly."
                ),
                recommendation="Re-sign with apksigner so v2 and v3 blocks are added.",
            )
        )

    now = datetime.now(UTC)
    for certificate in signing.certificates:
        label = certificate.subject or certificate.sha256[:16]
        if certificate.debug_certificate:
            findings.append(
                Finding(
                    id="debug_certificate",
                    severity=Severity.HIGH,
                    title="Signed with the Android debug certificate",
                    detail=(
                        "The debug keystore is shared by every SDK install, so this signature "
                        "proves nothing and Play will not accept it."
                    ),
                    evidence=[label],
                )
            )
        if certificate.weak_algorithm:
            findings.append(
                Finding(
                    id="weak_signature_algorithm",
                    severity=Severity.MEDIUM,
                    title=f"Certificate uses {certificate.signature_algorithm}",
                    detail="SHA-1 and MD5 signatures are no longer considered collision-resistant.",
                    evidence=[label],
                )
            )
        if certificate.key_algorithm == "RSA" and (certificate.key_bits or 0) < 2048:
            findings.append(
                Finding(
                    id="small_signing_key",
                    severity=Severity.MEDIUM,
                    title=f"RSA signing key is only {certificate.key_bits} bits",
                    detail="Play requires at least 2048-bit RSA for upload keys.",
                    evidence=[label],
                )
            )
        expiry = _parse_timestamp(certificate.not_after)
        if expiry is not None and expiry < now:
            findings.append(
                Finding(
                    id="certificate_expired",
                    severity=Severity.MEDIUM,
                    title="Signing certificate has expired",
                    detail=(
                        f"Valid until {certificate.not_after}. Installed apps keep working, but "
                        "Play will not accept an upload signed with it."
                    ),
                    evidence=[label],
                )
            )
    return findings


def _packaging_findings(
    manifest: ManifestInfo, contents: _Contents, dex_files: list[DexInfo]
) -> list[Finding]:
    findings: list[Finding] = []
    if contents.abis and not (set(contents.abis) & _ABI_64_BIT):
        findings.append(
            Finding(
                id="no_64bit_abi",
                severity=Severity.MEDIUM,
                title="Ships 32-bit native libraries only",
                detail=(
                    "Play has required a 64-bit variant of every native library since 2019, and "
                    "64-bit-only devices cannot run this build."
                ),
                evidence=contents.abis,
                recommendation="Add arm64-v8a (and x86_64 for emulators) to the ABI split.",
            )
        )
    compressed_libs = contents.abis and contents.stored_native_libs == 0
    if compressed_libs and manifest.extract_native_libs is not False:
        findings.append(
            Finding(
                id="compressed_native_libs",
                severity=Severity.LOW,
                title="Native libraries are compressed in the APK",
                detail=(
                    "Compressed .so files are copied out at install time, roughly doubling the "
                    "space they take on the device."
                ),
                recommendation=(
                    'Set android:extractNativeLibs="false" (or useLegacyPackaging=false) so the '
                    "libraries are mapped straight out of the APK."
                ),
            )
        )
    crowded = [
        f"{info.name}: {info.method_ids} methods"
        for info in dex_files
        if info.method_ids > DEX_METHOD_LIMIT * 0.9
    ]
    if crowded:
        findings.append(
            Finding(
                id="dex_method_pressure",
                severity=Severity.LOW,
                title="A dex file is close to the 64K method limit",
                detail=(
                    f"The per-dex ceiling is {DEX_METHOD_LIMIT} method ids; crossing it fails "
                    "the build unless multidex absorbs it."
                ),
                evidence=crowded,
            )
        )
    return findings


def _summarize_network_config(root: AxmlElement) -> dict[str, object]:
    """Collect the two things a network security config can quietly loosen."""

    cleartext: list[str] = []
    user_ca: list[str] = []

    def visit(element: AxmlElement, scope: str, in_debug: bool) -> None:
        local_scope = scope
        if element.name == "domain-config":
            domains = [child.text.strip() for child in element.iter_children("domain")]
            local_scope = ",".join(domain for domain in domains if domain) or "domain-config"
        elif element.name == "base-config":
            local_scope = "base-config"
        debug = in_debug or element.name == "debug-overrides"

        permitted = element.attr("cleartextTrafficPermitted", namespace=None)
        if permitted is not None and permitted.as_text().lower() == "true" and not debug:
            cleartext.append(local_scope)
        if element.name == "certificates" and not debug:
            source = element.attr("src", namespace=None)
            if source is not None and source.as_text().strip().lower() == "user":
                user_ca.append(local_scope)
        for child in element.children:
            visit(child, local_scope, debug)

    visit(root, "base-config", False)
    return {
        "cleartext_permitted": sorted(set(cleartext)),
        "user_ca_trusted": sorted(set(user_ca)),
    }


def _categorize(lowered_name: str) -> str:
    if lowered_name.endswith(".dex"):
        return "dex"
    if lowered_name.startswith("lib/"):
        return "native_libs"
    if lowered_name == "resources.arsc":
        return "resource_table"
    if lowered_name.startswith("res/"):
        return "resources"
    if lowered_name.startswith("assets/"):
        return "assets"
    if lowered_name.startswith("meta-inf/"):
        return "meta_inf"
    if lowered_name.startswith("kotlin/"):
        return "kotlin"
    return "other"


def _reject_non_apk(display_path: str, names: set[str]) -> None:
    if _MANIFEST_NAME in names:
        return
    if "base/manifest/AndroidManifest.xml" in names or "BundleConfig.pb" in names:
        raise ToolExecutionError(
            f"{display_path} is an Android App Bundle (.aab), not an APK. Its manifest is "
            "protobuf, not binary XML. Convert it first: "
            "'bundletool build-apks --bundle=... --mode=universal'."
        )
    if any(name.endswith(".apk") for name in names):
        raise ToolExecutionError(
            f"{display_path} is an APK set (.apks/.xapk) that contains other APKs. "
            "Unzip it and analyse base.apk."
        )
    raise ToolExecutionError(f"{display_path} has no AndroidManifest.xml, so it is not an APK.")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _summarize(
    manifest: ManifestInfo, signing: SigningInfo, size: int, findings: list[Finding]
) -> str:
    version = manifest.version_name or "?"
    code = manifest.version_code if manifest.version_code is not None else "?"
    schemes = "+".join(signing.schemes) if signing.schemes else "unsigned"
    serious = sum(
        1 for finding in findings if finding.severity in {Severity.CRITICAL, Severity.HIGH}
    )
    return (
        f"{manifest.package or 'unknown package'} {version} (versionCode {code}), "
        f"minSdk {manifest.min_sdk or '?'} / targetSdk {manifest.target_sdk or '?'}, "
        f"{_human_size(size)}, signed {schemes}, "
        f"{len(findings)} finding(s), {serious} high or critical."
    )


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"
