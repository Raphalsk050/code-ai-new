from __future__ import annotations

from dataclasses import dataclass, field

from code_ai.tools.apk.arsc import ResourceTable
from code_ai.tools.apk.axml import AxmlElement

COMPONENT_TAGS: dict[str, str] = {
    "activity": "activity",
    "activity-alias": "activity-alias",
    "service": "service",
    "receiver": "receiver",
    "provider": "provider",
}

_MAX_METADATA = 60
_MAX_INTENT_FILTERS = 8


@dataclass(slots=True)
class Component:
    kind: str
    name: str
    declared_exported: bool | None = None
    exported: bool = False
    permission: str | None = None
    read_permission: str | None = None
    write_permission: str | None = None
    authorities: str | None = None
    grant_uri_permissions: bool | None = None
    enabled: bool | None = None
    process: str | None = None
    intent_filters: list[str] = field(default_factory=list)
    launcher: bool = False
    exported_by_default: bool = False

    @property
    def protected(self) -> bool:
        """Whether some permission gates this component at all."""

        return bool(self.permission or self.read_permission or self.write_permission)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "name": self.name,
            "exported": self.exported,
            "exported_declared": self.declared_exported,
            "permission": self.permission,
            "intent_filters": list(self.intent_filters),
        }
        if self.kind == "provider":
            payload["authorities"] = self.authorities
            payload["read_permission"] = self.read_permission
            payload["write_permission"] = self.write_permission
            payload["grant_uri_permissions"] = self.grant_uri_permissions
        if self.launcher:
            payload["launcher"] = True
        if self.enabled is False:
            payload["enabled"] = False
        if self.process:
            payload["process"] = self.process
        return payload


@dataclass(slots=True)
class ManifestInfo:
    package: str | None = None
    version_code: int | None = None
    version_name: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    max_sdk: int | None = None
    compile_sdk: int | None = None
    install_location: str | None = None
    shared_user_id: str | None = None
    application_label: str | None = None
    application_class: str | None = None
    debuggable: bool | None = None
    test_only: bool | None = None
    allow_backup: bool | None = None
    backup_agent: str | None = None
    uses_cleartext_traffic: bool | None = None
    network_security_config: str | None = None
    extract_native_libs: bool | None = None
    request_legacy_external_storage: bool | None = None
    permissions_used: list[dict[str, object]] = field(default_factory=list)
    permissions_defined: list[dict[str, object]] = field(default_factory=list)
    features: list[dict[str, object]] = field(default_factory=list)
    libraries: list[dict[str, object]] = field(default_factory=list)
    metadata: list[dict[str, object]] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)

    def components_of(self, kind: str) -> list[Component]:
        return [component for component in self.components if component.kind == kind]

    def to_dict(self) -> dict[str, object]:
        return {
            "package": self.package,
            "version_code": self.version_code,
            "version_name": self.version_name,
            "min_sdk": self.min_sdk,
            "target_sdk": self.target_sdk,
            "max_sdk": self.max_sdk,
            "compile_sdk": self.compile_sdk,
            "install_location": self.install_location,
            "shared_user_id": self.shared_user_id,
            "label": self.application_label,
            "application_class": self.application_class,
            "debuggable": self.debuggable,
            "test_only": self.test_only,
            "allow_backup": self.allow_backup,
            "backup_agent": self.backup_agent,
            "uses_cleartext_traffic": self.uses_cleartext_traffic,
            "network_security_config": self.network_security_config,
            "extract_native_libs": self.extract_native_libs,
            "request_legacy_external_storage": self.request_legacy_external_storage,
        }


def parse_manifest(root: AxmlElement, table: ResourceTable | None = None) -> ManifestInfo:
    """Turn a decoded AndroidManifest.xml into the facts the analysis needs."""

    info = ManifestInfo()
    info.package = _text(root, "package", table)
    info.version_code = _int(root, "versionCode", table)
    info.version_name = _text(root, "versionName", table)
    info.install_location = _text(root, "installLocation", table)
    info.shared_user_id = _text(root, "sharedUserId", table)
    info.compile_sdk = _int(root, "compileSdkVersion", table)

    sdk = root.child("uses-sdk")
    if sdk is not None:
        info.min_sdk = _int(sdk, "minSdkVersion", table)
        info.target_sdk = _int(sdk, "targetSdkVersion", table)
        info.max_sdk = _int(sdk, "maxSdkVersion", table)
    if info.target_sdk is None:
        # An absent targetSdkVersion means "same as minSdkVersion", which is the
        # rule the platform itself applies when deciding default behaviours.
        info.target_sdk = info.min_sdk

    for element in root.iter_children("uses-permission"):
        info.permissions_used.append(_permission_use(element, table))
    for element in root.iter_children("uses-permission-sdk-23"):
        entry = _permission_use(element, table)
        entry["since_sdk"] = 23
        info.permissions_used.append(entry)
    for element in root.iter_children("permission"):
        info.permissions_defined.append(
            {
                "name": _text(element, "name", table),
                "protection_level": _protection_level(element),
            }
        )
    for element in root.iter_children("uses-feature"):
        feature: dict[str, object] = {
            "name": _text(element, "name", table),
            "required": _bool(element, "required"),
        }
        gles = element.attr("glEsVersion")
        if gles is not None:
            feature["gles_version"] = f"0x{gles.data:08x}"
        info.features.append(feature)

    application = root.child("application")
    if application is not None:
        _read_application(application, info, table)
    return info


def _read_application(
    application: AxmlElement, info: ManifestInfo, table: ResourceTable | None
) -> None:
    info.application_label = _text(application, "label", table)
    info.application_class = _qualify(_text(application, "name", table), info.package)
    info.debuggable = _bool(application, "debuggable")
    info.test_only = _bool(application, "testOnly")
    info.allow_backup = _bool(application, "allowBackup")
    info.backup_agent = _qualify(_text(application, "backupAgent", table), info.package)
    info.uses_cleartext_traffic = _bool(application, "usesCleartextTraffic")
    info.network_security_config = _text(application, "networkSecurityConfig", table)
    info.extract_native_libs = _bool(application, "extractNativeLibs")
    info.request_legacy_external_storage = _bool(application, "requestLegacyExternalStorage")

    for child in application.children:
        if child.name == "meta-data" and len(info.metadata) < _MAX_METADATA:
            info.metadata.append(
                {
                    "name": _text(child, "name", table),
                    "value": _text(child, "value", table)
                    or _text(child, "resource", table),
                }
            )
        elif child.name in {"uses-library", "uses-native-library"}:
            info.libraries.append(
                {
                    "name": _text(child, "name", table),
                    "required": _bool(child, "required"),
                    "native": child.name == "uses-native-library",
                }
            )
        elif child.name in COMPONENT_TAGS:
            info.components.append(_read_component(child, info, table))


def _read_component(
    element: AxmlElement, info: ManifestInfo, table: ResourceTable | None
) -> Component:
    kind = COMPONENT_TAGS[element.name]
    component = Component(
        kind=kind,
        name=_qualify(_text(element, "name", table), info.package) or "",
        declared_exported=_bool(element, "exported"),
        permission=_text(element, "permission", table),
        read_permission=_text(element, "readPermission", table),
        write_permission=_text(element, "writePermission", table),
        authorities=_text(element, "authorities", table),
        grant_uri_permissions=_bool(element, "grantUriPermissions"),
        enabled=_bool(element, "enabled"),
        process=_text(element, "process", table),
    )
    filters = list(element.iter_children("intent-filter"))
    for intent_filter in filters[:_MAX_INTENT_FILTERS]:
        summary, launcher = _describe_intent_filter(intent_filter, table)
        component.intent_filters.append(summary)
        component.launcher = component.launcher or launcher
    if len(filters) > _MAX_INTENT_FILTERS:
        component.intent_filters.append(f"... {len(filters) - _MAX_INTENT_FILTERS} more")

    has_filter = bool(filters)
    if component.declared_exported is not None:
        component.exported = component.declared_exported
    else:
        component.exported_by_default = True
        if kind == "provider":
            # Providers were exported by default until API 17.
            component.exported = info.target_sdk is None or info.target_sdk < 17
        else:
            component.exported = has_filter
    return component


def _describe_intent_filter(
    element: AxmlElement, table: ResourceTable | None
) -> tuple[str, bool]:
    actions = [_text(child, "name", table) or "" for child in element.iter_children("action")]
    categories = [
        _text(child, "name", table) or "" for child in element.iter_children("category")
    ]
    data: list[str] = []
    for child in element.iter_children("data"):
        scheme = _text(child, "scheme", table)
        host = _text(child, "host", table)
        path = (
            _text(child, "path", table)
            or _text(child, "pathPrefix", table)
            or _text(child, "pathPattern", table)
        )
        mime = _text(child, "mimeType", table)
        pieces = "".join(
            [
                f"{scheme}://" if scheme else "",
                host or "",
                path or "",
            ]
        )
        if pieces:
            data.append(pieces)
        elif mime:
            data.append(mime)

    fragments = [f"action={_short_names(actions)}"] if actions else []
    if categories:
        fragments.append(f"category={_short_names(categories)}")
    if data:
        fragments.append(f"data={','.join(data[:4])}")
    launcher = "android.intent.category.LAUNCHER" in categories
    return " ".join(fragments) or "(empty)", launcher


def _short_names(values: list[str]) -> str:
    """Shorten framework constants; a custom action keeps its full name."""

    shortened = [
        value.rsplit(".", 1)[-1] if value.startswith("android.intent.") else value
        for value in values
        if value
    ]
    return ",".join(shortened[:4]) + ("..." if len(shortened) > 4 else "")


def _permission_use(element: AxmlElement, table: ResourceTable | None) -> dict[str, object]:
    entry: dict[str, object] = {"name": _text(element, "name", table)}
    max_sdk = _int(element, "maxSdkVersion", table)
    if max_sdk is not None:
        entry["max_sdk"] = max_sdk
    return entry


_PROTECTION_LEVELS = {0: "normal", 1: "dangerous", 2: "signature", 3: "signatureOrSystem"}
_PROTECTION_FLAGS = {
    0x10: "privileged",
    0x20: "development",
    0x40: "appop",
    0x80: "pre23",
    0x100: "installer",
    0x200: "verifier",
    0x400: "preinstalled",
    0x800: "setup",
    0x1000: "instant",
    0x2000: "runtime",
}


def _protection_level(element: AxmlElement) -> str | None:
    attribute = element.attr("protectionLevel")
    if attribute is None:
        # An omitted protectionLevel is "normal": any app may hold it.
        return "normal"
    value = attribute.value
    if isinstance(value, str) and not value.startswith(("@", "?")):
        return value
    if not isinstance(attribute.data, int):
        return None
    base = _PROTECTION_LEVELS.get(attribute.data & 0x0F)
    flags = [name for bit, name in _PROTECTION_FLAGS.items() if attribute.data & bit]
    return "|".join(filter(None, [base, *flags])) or None


def _qualify(name: str | None, package: str | None) -> str | None:
    """Expand the ``.Relative`` and ``Bare`` class names a manifest may use."""

    if not name:
        return name
    if name.startswith("."):
        return f"{package}{name}" if package else name
    if "." not in name and package:
        return f"{package}.{name}"
    return name


def _text(element: AxmlElement, name: str, table: ResourceTable | None) -> str | None:
    # The android namespace first, then any namespace: a handful of manifest
    # attributes (``package``, tooling ones) carry no namespace at all.
    attribute = element.attr(name) or element.attr(name, namespace=None)
    if attribute is None:
        return None
    if attribute.is_reference() and table is not None:
        resolved = table.resolve(attribute.data)
        if resolved is not None:
            return resolved
    return attribute.as_text() or None


def _bool(element: AxmlElement, name: str) -> bool | None:
    attribute = element.attr(name)
    if attribute is None:
        return None
    value = attribute.value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1"}:
            return True
        if lowered in {"false", "0"}:
            return False
    return None


def _int(element: AxmlElement, name: str, table: ResourceTable | None) -> int | None:
    attribute = element.attr(name) or element.attr(name, namespace=None)
    if attribute is None:
        return None
    value = attribute.value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if attribute.is_reference() and table is not None:
        resolved = table.resolve(attribute.data)
        if resolved is not None and resolved.lstrip("-").isdigit():
            return int(resolved)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None
