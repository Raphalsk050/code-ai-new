from __future__ import annotations

from pathlib import Path

from code_ai.core.rules import RuleSource
from code_ai.core.workflows import KIND_OPENCODE_JSON, WorkflowSource
from code_ai.interop import paths
from code_ai.interop.mcp import McpDeclaration, opencode_server
from code_ai.interop.subagents import KIND_OPENCODE_JSON as AGENTS_JSON
from code_ai.interop.subagents import AgentSource
from code_ai.tools.skills.common import SkillSource
from code_ai.util import jsonc
from code_ai.util.arguments import OPENCODE

# sst's OpenCode. Global files live in ~/.config/opencode on every platform:
#
#   AGENTS.md                               global instructions
#   opencode.json(c)                        "instructions", "mcp", "command", "agent"
#   {commands,agents,skills}/               singular names are still read too
#   <dir>/opencode.json(c), <dir>/.opencode/  the same, per project
#
# OPENCODE_CONFIG names one more config file and OPENCODE_CONFIG_DIR one more
# directory laid out like .opencode. Remote "instructions" URLs are not fetched.

ORIGIN = "opencode"
CONFIG_ENV = "OPENCODE_CONFIG"
CONFIG_DIR_ENV = "OPENCODE_CONFIG_DIR"

DIRNAME = ".opencode"
_CONFIG_NAMES = ("opencode.json", "opencode.jsonc")


def global_dirs() -> list[Path]:
    dirs = [paths.config_home() / "opencode"]
    extra = None if paths.redirected() else paths.env_path(CONFIG_DIR_ENV)
    if extra is not None:
        dirs.append(extra)
    return dirs


def _project_dirs(workspace: Path | str) -> list[Path]:
    """``.opencode`` directories, nearest first."""

    return [
        directory / DIRNAME for directory in reversed(paths.project_directories(workspace))
    ]


def global_config_files() -> list[Path]:
    files = [directory / name for directory in global_dirs()
             for name in (*_CONFIG_NAMES, "config.json")]
    custom = None if paths.redirected() else paths.env_path(CONFIG_ENV)
    if custom is not None:
        files.append(custom)
    return [path for path in files if path.is_file()]


def project_config_files(workspace: Path | str) -> list[Path]:
    """Nearest first; in each directory the root file before the one in .opencode."""

    files: list[Path] = []
    for directory in reversed(paths.project_directories(workspace)):
        files += [directory / name for name in _CONFIG_NAMES]
        files += [directory / DIRNAME / name for name in _CONFIG_NAMES]
    return [path for path in files if path.is_file()]


def global_instruction_sources() -> list[RuleSource]:
    sources = [
        RuleSource(
            path=directory / "AGENTS.md",
            scope="global",
            origin=ORIGIN,
            authoritative=True,
            display_dir=paths.display_dir(directory),
        )
        for directory in global_dirs()
    ]
    for config in global_config_files():
        sources += _configured_instructions(config, scope="global", root=None)
    return sources


def project_instruction_sources(workspace: Path | str) -> list[RuleSource]:
    """What the project's ``instructions`` field lists, after its AGENTS.md files."""

    root = paths.project_directories(workspace)[0]
    sources: list[RuleSource] = []
    for config in reversed(project_config_files(workspace)):
        sources += _configured_instructions(config, scope="project", root=root)
    return sources


def _configured_instructions(config: Path, *, scope: str, root: Path | None) -> list[RuleSource]:
    entries = jsonc.load_file(config).get("instructions")
    if not isinstance(entries, list):
        return []
    base = config.parent.parent if config.parent.name == DIRNAME else config.parent
    sources: list[RuleSource] = []
    for entry in entries:
        pattern = str(entry or "").strip()
        if not pattern or "://" in pattern:
            continue
        expanded = Path(pattern).expanduser()
        matches = [expanded] if expanded.is_absolute() else _glob(base, pattern)
        for match in matches:
            resolved = match.resolve()
            if root is not None and not resolved.is_relative_to(root):
                continue
            sources.append(
                RuleSource(
                    path=resolved,
                    scope=scope,
                    origin=ORIGIN,
                    authoritative=True,
                    display_dir=paths.display_dir(resolved.parent, root),
                )
            )
    return sources


def _glob(base: Path, pattern: str) -> list[Path]:
    if not any(char in pattern for char in "*?["):
        return [base / pattern]
    try:
        return sorted(path for path in base.glob(pattern) if path.is_file())
    except (OSError, ValueError):
        return []


def rule_sources(workspace: Path | str) -> list[RuleSource]:
    return []


def skill_sources(workspace: Path | str) -> list[SkillSource]:
    project = [
        SkillSource(root=directory / name, origin=ORIGIN)
        for directory in _project_dirs(workspace)
        for name in ("skills", "skill")
    ]
    personal = [
        SkillSource(root=directory / name, origin=ORIGIN)
        for directory in global_dirs()
        for name in ("skills", "skill")
    ]
    return [*project, *personal]


def workflow_sources(workspace: Path | str) -> list[WorkflowSource]:
    sources = [
        WorkflowSource(root=directory / name, scope="project", origin=ORIGIN, dialect=OPENCODE)
        for directory in _project_dirs(workspace)
        for name in ("commands", "command")
    ]
    sources += [
        WorkflowSource(root=config, scope="project", origin=ORIGIN, dialect=OPENCODE,
                       kind=KIND_OPENCODE_JSON)
        for config in project_config_files(workspace)
    ]
    sources += [
        WorkflowSource(root=directory / name, scope="global", origin=ORIGIN, dialect=OPENCODE)
        for directory in global_dirs()
        for name in ("commands", "command")
    ]
    sources += [
        WorkflowSource(root=config, scope="global", origin=ORIGIN, dialect=OPENCODE,
                       kind=KIND_OPENCODE_JSON)
        for config in global_config_files()
    ]
    return sources


def agent_sources(workspace: Path | str) -> list[AgentSource]:
    def folders(directories: list[Path]) -> list[AgentSource]:
        return [
            AgentSource(root=directory / name, origin=ORIGIN, dialect=OPENCODE)
            for directory in directories
            for name in ("agents", "agent")
        ]

    def configs(files: list[Path]) -> list[AgentSource]:
        return [
            AgentSource(root=path, origin=ORIGIN, dialect=OPENCODE, kind=AGENTS_JSON)
            for path in files
        ]

    return [
        *folders(_project_dirs(workspace)),
        *configs(project_config_files(workspace)),
        *folders(global_dirs()),
        *configs(global_config_files()),
    ]


def mcp_declarations(workspace: Path | str) -> list[McpDeclaration]:
    found: list[McpDeclaration] = []
    files = [(path, False) for path in project_config_files(workspace)]
    files += [(path, True) for path in global_config_files()]
    for path, user_level in files:
        servers = jsonc.load_file(path).get("mcp")
        if not isinstance(servers, dict):
            continue
        for name, raw in servers.items():
            if isinstance(raw, dict):
                found.append(
                    McpDeclaration(
                        str(name), opencode_server(raw, path.parent), ORIGIN, path, user_level
                    )
                )
    return found
