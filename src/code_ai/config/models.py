from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from code_ai.config.defaults import (
    DEFAULT_BROWSER,
    DEFAULT_BUDGETS,
    DEFAULT_DECISIONS,
    DEFAULT_FILE_IO,
    DEFAULT_GOAL,
    DEFAULT_GRAPH,
    DEFAULT_INDEX,
    DEFAULT_MEMORY,
    DEFAULT_PLANNER,
    DEFAULT_SAMPLING,
    DEFAULT_SANDBOX,
    PLACEHOLDER_API_KEY,
    default_sandbox_base_dir,
    project_browser_dir,
    project_index_dir,
)
from code_ai.core.errors import ConfigurationError
from code_ai.util.redaction import redact_mapping

SUPPORTED_API_MODES = {"responses", "completions", "ollama"}
SUPPORTED_PERMISSION_MODES = {"ask", "auto", "bypass"}
# "auto" probes native function calling and remembers a refusal; "native"
# insists on it; "text" always uses the prompt protocol instead.
SUPPORTED_TOOL_CALLING_MODES = {"auto", "native", "text"}


def _unique_names(raw: Any) -> list[str]:
    """Clean a user-entered name list, keeping their order and dropping repeats."""

    seen: list[str] = []
    for item in raw or ():
        name = str(item).strip()
        if name and name not in seen:
            seen.append(name)
    return seen
# Weakest to strongest.
_REASONING_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh")
SUPPORTED_REASONING_EFFORTS = set(_REASONING_EFFORT_ORDER)
# What bookkeeping calls (summaries, lessons, reflection) think at.
_LIGHT_REASONING_EFFORT = "low"
SUPPORTED_REASONING_SUMMARIES = {"auto", "concise", "detailed"}


def normalize_api_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode == "chat_completions":
        return "completions"
    return mode


def normalize_api_key(value: str) -> str:
    """Treat the saved placeholder as "unset" so runtime logic ignores it."""
    cleaned = value.strip()
    return "" if cleaned == PLACEHOLDER_API_KEY else cleaned


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")


def _is_cloud_model(model: str) -> bool:
    # Ollama forwards "name:cloud" and "name:tag-cloud" to ollama.com.
    tag = model.strip().lower().rpartition(":")[2]
    return tag == "cloud" or tag.endswith("-cloud")


@dataclass(slots=True)
class BudgetConfig:
    build_tool_timeout_s: int = DEFAULT_BUDGETS["build_tool_timeout_s"]
    default_tool_timeout_s: int = DEFAULT_BUDGETS["default_tool_timeout_s"]
    max_context_tokens: int = DEFAULT_BUDGETS["max_context_tokens"]
    max_model_call_s: int = DEFAULT_BUDGETS["max_model_call_s"]
    max_model_step_seconds: int = DEFAULT_BUDGETS["max_model_step_seconds"]
    model_queue_notice_s: int = DEFAULT_BUDGETS["model_queue_notice_s"]
    max_model_steps: int = DEFAULT_BUDGETS["max_model_steps"]
    max_orchestration_rounds: int = DEFAULT_BUDGETS["max_orchestration_rounds"]
    max_orchestration_seconds: int = DEFAULT_BUDGETS["max_orchestration_seconds"]
    max_stall_rounds: int = DEFAULT_BUDGETS["max_stall_rounds"]
    max_tool_call_seconds: int = DEFAULT_BUDGETS["max_tool_call_seconds"]
    max_tool_calls: int = DEFAULT_BUDGETS["max_tool_calls"]
    max_tool_output_chars: int = DEFAULT_BUDGETS["max_tool_output_chars"]
    max_tool_wall_time_s: int = DEFAULT_BUDGETS["max_tool_wall_time_s"]
    max_turn_seconds: int = DEFAULT_BUDGETS["max_turn_seconds"]
    max_turn_wall_time_s: int = DEFAULT_BUDGETS["max_turn_wall_time_s"]
    subagent_explorer_timeout_s: int = DEFAULT_BUDGETS["subagent_explorer_timeout_s"]
    subagent_worker_timeout_s: int = DEFAULT_BUDGETS["subagent_worker_timeout_s"]
    max_subagent_depth: int = DEFAULT_BUDGETS["max_subagent_depth"]
    max_concurrent_subagents: int = DEFAULT_BUDGETS["max_concurrent_subagents"]
    max_subagents_per_turn: int = DEFAULT_BUDGETS["max_subagents_per_turn"]
    subagent_retry_max_attempts: int = DEFAULT_BUDGETS["subagent_retry_max_attempts"]
    subagent_circuit_failure_threshold: int = DEFAULT_BUDGETS["subagent_circuit_failure_threshold"]
    subagent_circuit_reset_s: int = DEFAULT_BUDGETS["subagent_circuit_reset_s"]

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> BudgetConfig:
        values = dict(DEFAULT_BUDGETS)
        if data:
            values.update(data)
        return cls(
            **{key: int(value) for key, value in values.items() if key in cls.__dataclass_fields__}
        )

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if value <= 0:
                raise ConfigurationError(f"Budget value {key} must be positive.")
        if self.max_context_tokens < 4096:
            raise ConfigurationError("max_context_tokens must be at least 4096.")

    def model_timeout(self) -> float:
        return float(min(self.max_model_call_s, self.max_model_step_seconds))

    def turn_timeout(self) -> float:
        return float(min(self.max_turn_seconds, self.max_turn_wall_time_s))


@dataclass(slots=True)
class PlannerConfig:
    enabled: bool = bool(DEFAULT_PLANNER["enabled"])
    mode: str = str(DEFAULT_PLANNER["mode"])
    tool_policy: str = str(DEFAULT_PLANNER["tool_policy"])
    local_first: bool = bool(DEFAULT_PLANNER["local_first"])
    require_plan_for_mutations: bool = bool(DEFAULT_PLANNER["require_plan_for_mutations"])
    require_verification_for_changes: bool = bool(
        DEFAULT_PLANNER["require_verification_for_changes"]
    )
    double_check_completion: bool = bool(DEFAULT_PLANNER["double_check_completion"])
    require_review_for_risky_changes: bool = bool(
        DEFAULT_PLANNER["require_review_for_risky_changes"]
    )
    max_plan_steps: int = int(DEFAULT_PLANNER["max_plan_steps"])
    max_discovery_rounds: int = int(DEFAULT_PLANNER["max_discovery_rounds"])
    max_replans: int = int(DEFAULT_PLANNER["max_replans"])
    max_step_attempts: int = int(DEFAULT_PLANNER["max_step_attempts"])
    max_no_progress_rounds: int = int(DEFAULT_PLANNER["max_no_progress_rounds"])
    max_completion_rejections: int = int(DEFAULT_PLANNER["max_completion_rejections"])
    persist_plan: bool = bool(DEFAULT_PLANNER["persist_plan"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> PlannerConfig:
        values = dict(DEFAULT_PLANNER)
        if data:
            values.update(data)
        return cls(
            enabled=bool(values["enabled"]),
            mode=str(values["mode"]),
            tool_policy=_resolve_tool_policy(data),
            local_first=bool(values["local_first"]),
            require_plan_for_mutations=bool(values["require_plan_for_mutations"]),
            require_verification_for_changes=bool(values["require_verification_for_changes"]),
            double_check_completion=bool(values["double_check_completion"]),
            require_review_for_risky_changes=bool(values["require_review_for_risky_changes"]),
            max_plan_steps=int(values["max_plan_steps"]),
            max_discovery_rounds=int(values["max_discovery_rounds"]),
            max_replans=int(values["max_replans"]),
            max_step_attempts=int(values["max_step_attempts"]),
            max_no_progress_rounds=int(values["max_no_progress_rounds"]),
            max_completion_rejections=int(values["max_completion_rejections"]),
            persist_plan=bool(values["persist_plan"]),
        )

    @property
    def strict_tool_policy(self) -> bool:
        return self.tool_policy == "strict"

    @property
    def advisory_tool_policy(self) -> bool:
        return self.tool_policy == "advisory"

    def validate(self) -> None:
        if self.mode not in {"auto", "plan", "act"}:
            raise ConfigurationError(f"Unsupported planner mode: {self.mode}.")
        if self.tool_policy not in {"advisory", "strict"}:
            raise ConfigurationError(f"Unsupported tool_policy: {self.tool_policy}.")
        limits = {
            "max_plan_steps": self.max_plan_steps,
            "max_discovery_rounds": self.max_discovery_rounds,
            "max_replans": self.max_replans,
            "max_step_attempts": self.max_step_attempts,
            "max_no_progress_rounds": self.max_no_progress_rounds,
        }
        for key, value in limits.items():
            if value <= 0:
                raise ConfigurationError(f"Planner value {key} must be positive.")
        if self.max_plan_steps > 100:
            raise ConfigurationError("max_plan_steps must be 100 or lower.")
        if self.max_no_progress_rounds > 20:
            raise ConfigurationError("max_no_progress_rounds must be 20 or lower.")


@dataclass(slots=True)
class MemoryConfig:
    """Learning-loop knobs: post-turn reflection, consolidation, rendering.

    All passes are best-effort and bounded — disabling any of them degrades to
    the previous behavior (model-initiated ``remember`` calls plus failure
    lessons) without breaking a session.
    """

    reflection_enabled: bool = bool(DEFAULT_MEMORY["reflection_enabled"])
    reflection_min_tool_calls: int = int(DEFAULT_MEMORY["reflection_min_tool_calls"])
    reflection_max_output_tokens: int = int(DEFAULT_MEMORY["reflection_max_output_tokens"])
    consolidation_enabled: bool = bool(DEFAULT_MEMORY["consolidation_enabled"])
    consolidation_min_new: int = int(DEFAULT_MEMORY["consolidation_min_new"])
    render_limit_per_kind: int = int(DEFAULT_MEMORY["render_limit_per_kind"])
    lessons_render_limit: int = int(DEFAULT_MEMORY["lessons_render_limit"])
    lesson_pin_count: int = int(DEFAULT_MEMORY["lesson_pin_count"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> MemoryConfig:
        values = dict(DEFAULT_MEMORY)
        if data:
            values.update(data)
        return cls(
            reflection_enabled=bool(values["reflection_enabled"]),
            reflection_min_tool_calls=int(values["reflection_min_tool_calls"]),
            reflection_max_output_tokens=int(values["reflection_max_output_tokens"]),
            consolidation_enabled=bool(values["consolidation_enabled"]),
            consolidation_min_new=int(values["consolidation_min_new"]),
            render_limit_per_kind=int(values["render_limit_per_kind"]),
            lessons_render_limit=int(values["lessons_render_limit"]),
            lesson_pin_count=int(values["lesson_pin_count"]),
        )

    def validate(self) -> None:
        positives = {
            "reflection_max_output_tokens": self.reflection_max_output_tokens,
            "consolidation_min_new": self.consolidation_min_new,
            "render_limit_per_kind": self.render_limit_per_kind,
            "lessons_render_limit": self.lessons_render_limit,
            "lesson_pin_count": self.lesson_pin_count,
        }
        for key, value in positives.items():
            if value <= 0:
                raise ConfigurationError(f"Memory value {key} must be positive.")
        if self.reflection_min_tool_calls < 0:
            raise ConfigurationError("reflection_min_tool_calls must be zero or positive.")


@dataclass(slots=True)
class FileIOConfig:
    """How hard to try when the filesystem says "not right now".

    Written for Windows, where a file operation can fail because some other
    process happens to have the file open - an antivirus scanning it, a search
    indexer reading it, a sync client uploading it, an encryption agent
    rewriting it. None of those are real errors; they are timing. Raise
    ``retry_attempts`` on a host where the interference is heavy.
    """

    retry_attempts: int = int(DEFAULT_FILE_IO["retry_attempts"])
    retry_initial_delay_ms: int = int(DEFAULT_FILE_IO["retry_initial_delay_ms"])
    retry_max_delay_ms: int = int(DEFAULT_FILE_IO["retry_max_delay_ms"])
    allow_non_atomic_fallback: bool = bool(DEFAULT_FILE_IO["allow_non_atomic_fallback"])
    windows_in_place_first: bool = bool(DEFAULT_FILE_IO["windows_in_place_first"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> FileIOConfig:
        values = dict(DEFAULT_FILE_IO)
        if data:
            values.update(data)
        return cls(
            retry_attempts=int(values["retry_attempts"]),
            retry_initial_delay_ms=int(values["retry_initial_delay_ms"]),
            retry_max_delay_ms=int(values["retry_max_delay_ms"]),
            allow_non_atomic_fallback=bool(values["allow_non_atomic_fallback"]),
            windows_in_place_first=bool(values["windows_in_place_first"]),
        )

    def validate(self) -> None:
        if self.retry_attempts < 1:
            raise ConfigurationError("file_io retry_attempts must be at least 1.")
        if self.retry_initial_delay_ms < 0:
            raise ConfigurationError("file_io retry_initial_delay_ms must be zero or positive.")
        if self.retry_max_delay_ms < self.retry_initial_delay_ms:
            raise ConfigurationError(
                "file_io retry_max_delay_ms must be at least retry_initial_delay_ms."
            )


@dataclass(slots=True)
class SandboxConfig:
    """Where the agent is allowed to build, run and scribble.

    The sandbox is an isolated directory per session that absorbs every
    byproduct of working on a project - build outputs, generated scripts,
    temporary files, captured test logs - so the user's tree only ever changes
    through a deliberate source edit. Disabling it degrades to the previous
    behaviour rather than blocking: tools keep working against the workspace.
    """

    enabled: bool = bool(DEFAULT_SANDBOX["enabled"])
    base_dir: str = str(DEFAULT_SANDBOX["base_dir"])
    ttl_hours: int = int(DEFAULT_SANDBOX["ttl_hours"])
    cleanup_on_exit: bool = bool(DEFAULT_SANDBOX["cleanup_on_exit"])
    max_artifact_bytes: int = int(DEFAULT_SANDBOX["max_artifact_bytes"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> SandboxConfig:
        values = dict(DEFAULT_SANDBOX)
        if data:
            values.update(data)
        return cls(
            enabled=bool(values["enabled"]),
            base_dir=str(values["base_dir"] or ""),
            ttl_hours=int(values["ttl_hours"]),
            cleanup_on_exit=bool(values["cleanup_on_exit"]),
            max_artifact_bytes=int(values["max_artifact_bytes"]),
        )

    def resolved_base_dir(self) -> Path:
        """Absolute base directory holding one sandbox per session."""

        configured = self.base_dir.strip()
        if configured:
            return Path(configured).expanduser()
        return default_sandbox_base_dir()

    def validate(self) -> None:
        if self.ttl_hours <= 0:
            raise ConfigurationError("sandbox ttl_hours must be positive.")
        if self.max_artifact_bytes <= 0:
            raise ConfigurationError("sandbox max_artifact_bytes must be positive.")


SUPPORTED_EMBEDDING_API_MODES = {"", "ollama", "openai"}


# The installed browsers Playwright can drive in place of its own Chromium.
BROWSER_CHANNELS = frozenset(
    {
        "chrome",
        "chrome-beta",
        "chrome-dev",
        "chrome-canary",
        "msedge",
        "msedge-beta",
        "msedge-dev",
        "msedge-canary",
    }
)


@dataclass(slots=True)
class BrowserConfig:
    """The browser the agent drives, and the profile it keeps its logins in."""

    enabled: bool = bool(DEFAULT_BROWSER["enabled"])
    profile_dir: str = str(DEFAULT_BROWSER["profile_dir"])
    headless: bool = bool(DEFAULT_BROWSER["headless"])
    timeout_ms: int = int(DEFAULT_BROWSER["timeout_ms"])
    channel: str = str(DEFAULT_BROWSER["channel"])
    auto_install: bool = bool(DEFAULT_BROWSER["auto_install"])
    dialog_policy: str = str(DEFAULT_BROWSER["dialog_policy"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> BrowserConfig:
        values = {**DEFAULT_BROWSER, **(data or {})}
        return cls(
            enabled=bool(values["enabled"]),
            profile_dir=str(values["profile_dir"] or ""),
            headless=bool(values["headless"]),
            timeout_ms=int(values["timeout_ms"]),
            channel=str(values["channel"] or "").strip().lower(),
            auto_install=bool(values["auto_install"]),
            dialog_policy=str(values["dialog_policy"] or "").strip().lower(),
        )

    def resolved_profile_dir(self, workspace: Path | str) -> Path:
        configured = self.profile_dir.strip()
        if configured:
            return Path(configured).expanduser()
        return project_browser_dir(workspace)

    def validate(self) -> None:
        if self.timeout_ms < 1000:
            raise ConfigurationError("browser timeout_ms must be at least 1000.")
        # Refused here rather than at the first browser call, where a typo
        # would read as "that browser is not installed".
        if self.dialog_policy not in {"accept", "dismiss"}:
            raise ConfigurationError('browser dialog_policy must be "accept" or "dismiss".')
        if self.channel and self.channel not in BROWSER_CHANNELS:
            raise ConfigurationError(
                "browser channel must be empty (Playwright's own Chromium) or one of: "
                + ", ".join(sorted(BROWSER_CHANNELS))
                + "."
            )


@dataclass(slots=True)
class IndexConfig:
    """The code index: chunked, searchable copy of the workspace's source.

    Retrieval over the index answers "where is X handled" in milliseconds from
    a local SQLite file, where a fresh grep walks the whole tree every time.
    Lexical search (FTS5) always works; set ``embedding_model`` to add semantic
    retrieval through the provider's embeddings endpoint, fused with the
    lexical ranking so identifier matches and meaning matches both surface.
    """

    enabled: bool = bool(DEFAULT_INDEX["enabled"])
    index_dir: str = str(DEFAULT_INDEX["index_dir"])
    embedding_model: str = str(DEFAULT_INDEX["embedding_model"])
    embedding_api_mode: str = str(DEFAULT_INDEX["embedding_api_mode"])
    embedding_base_url: str = str(DEFAULT_INDEX["embedding_base_url"])
    embedding_batch_size: int = int(DEFAULT_INDEX["embedding_batch_size"])
    embedding_max_chars: int = int(DEFAULT_INDEX["embedding_max_chars"])
    embedding_parallel: int = int(DEFAULT_INDEX["embedding_parallel"])
    auto_index_touched_files: bool = bool(DEFAULT_INDEX["auto_index_touched_files"])
    max_file_bytes: int = int(DEFAULT_INDEX["max_file_bytes"])
    chunk_lines: int = int(DEFAULT_INDEX["chunk_lines"])
    chunk_overlap_lines: int = int(DEFAULT_INDEX["chunk_overlap_lines"])
    include_globs: list[str] = field(default_factory=list)
    exclude_globs: list[str] = field(default_factory=list)
    respect_gitignore: bool = bool(DEFAULT_INDEX["respect_gitignore"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> IndexConfig:
        values = dict(DEFAULT_INDEX)
        if data:
            values.update(data)
        return cls(
            enabled=bool(values["enabled"]),
            index_dir=str(values["index_dir"] or ""),
            embedding_model=str(values["embedding_model"] or "").strip(),
            embedding_api_mode=str(values["embedding_api_mode"] or "").strip().lower(),
            embedding_base_url=str(values["embedding_base_url"] or "").strip(),
            embedding_batch_size=int(values["embedding_batch_size"]),
            embedding_max_chars=int(values["embedding_max_chars"]),
            embedding_parallel=int(values["embedding_parallel"]),
            auto_index_touched_files=bool(values["auto_index_touched_files"]),
            max_file_bytes=int(values["max_file_bytes"]),
            chunk_lines=int(values["chunk_lines"]),
            chunk_overlap_lines=int(values["chunk_overlap_lines"]),
            include_globs=_string_list(values.get("include_globs")),
            exclude_globs=_string_list(values.get("exclude_globs")),
            respect_gitignore=bool(values["respect_gitignore"]),
        )

    def resolved_index_dir(self, workspace: Path | str) -> Path:
        configured = self.index_dir.strip()
        if configured:
            return Path(configured).expanduser()
        return project_index_dir(workspace)

    @property
    def semantic_enabled(self) -> bool:
        return bool(self.embedding_model)

    def validate(self) -> None:
        if self.embedding_api_mode not in SUPPORTED_EMBEDDING_API_MODES:
            raise ConfigurationError(
                "index embedding_api_mode must be empty, 'ollama' or 'openai'."
            )
        if self.embedding_batch_size < 1:
            raise ConfigurationError("index embedding_batch_size must be at least 1.")
        if self.embedding_max_chars < 200:
            raise ConfigurationError("index embedding_max_chars must be at least 200.")
        if self.embedding_parallel < 1:
            raise ConfigurationError("index embedding_parallel must be at least 1.")
        if self.max_file_bytes <= 0:
            raise ConfigurationError("index max_file_bytes must be positive.")
        if self.chunk_lines < 5:
            raise ConfigurationError("index chunk_lines must be at least 5.")
        if not 0 <= self.chunk_overlap_lines < self.chunk_lines:
            raise ConfigurationError(
                "index chunk_overlap_lines must be zero or positive and below chunk_lines."
            )
        if self.embedding_base_url:
            parsed = urlparse(self.embedding_base_url)
            if parsed.scheme not in {"http", "https"}:
                raise ConfigurationError("index embedding_base_url must be an http or https URL.")


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        raise ConfigurationError("index glob lists must be lists of strings.")
    return [str(item) for item in value if str(item).strip()]


@dataclass(slots=True)
class GoalConfig:
    """Limits and behavior of the /goal persistent-objective loop."""

    max_iterations: int = int(DEFAULT_GOAL["max_iterations"])
    max_goal_minutes: int = int(DEFAULT_GOAL["max_goal_minutes"])
    max_no_progress_iterations: int = int(DEFAULT_GOAL["max_no_progress_iterations"])
    judge_enabled: bool = bool(DEFAULT_GOAL["judge_enabled"])
    confirm_criteria: bool = bool(DEFAULT_GOAL["confirm_criteria"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> GoalConfig:
        values = dict(DEFAULT_GOAL)
        if data:
            values.update(data)
        return cls(
            max_iterations=int(values["max_iterations"]),
            max_goal_minutes=int(values["max_goal_minutes"]),
            max_no_progress_iterations=int(values["max_no_progress_iterations"]),
            judge_enabled=bool(values["judge_enabled"]),
            confirm_criteria=bool(values["confirm_criteria"]),
        )

    def validate(self) -> None:
        limits = {
            "max_iterations": self.max_iterations,
            "max_goal_minutes": self.max_goal_minutes,
            "max_no_progress_iterations": self.max_no_progress_iterations,
        }
        for key, value in limits.items():
            if value <= 0:
                raise ConfigurationError(f"Goal value {key} must be positive.")
        if self.max_iterations > 500:
            raise ConfigurationError("goal.max_iterations must be 500 or lower.")


SUPPORTED_DECISION_ENGINES = {"", "openjev"}
SUPPORTED_DECISION_APIS = {"auto", "systemone", "llamacpp"}


@dataclass(slots=True)
class DecisionConfig:
    """The local engine that answers the runtime's questions about a turn."""

    engine: str = str(DEFAULT_DECISIONS["engine"])
    base_url: str = str(DEFAULT_DECISIONS["base_url"])
    api: str = str(DEFAULT_DECISIONS["api"])
    api_key: str = str(DEFAULT_DECISIONS["api_key"])
    timeout_s: float = float(DEFAULT_DECISIONS["timeout_s"])
    retry_after_s: float = float(DEFAULT_DECISIONS["retry_after_s"])
    confidence: float = float(DEFAULT_DECISIONS["confidence"])
    nudge_confidence: float = float(DEFAULT_DECISIONS["nudge_confidence"])
    max_final_answer_nudges: int = int(DEFAULT_DECISIONS["max_final_answer_nudges"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> DecisionConfig:
        values = dict(DEFAULT_DECISIONS)
        if data:
            values.update(data)
        return cls(
            engine=str(values["engine"] or "").strip().lower(),
            base_url=str(values["base_url"] or "").strip(),
            api=str(values["api"] or "auto").strip().lower(),
            api_key=str(values["api_key"] or "").strip(),
            timeout_s=float(values["timeout_s"]),
            retry_after_s=float(values["retry_after_s"]),
            confidence=float(values["confidence"]),
            nudge_confidence=float(values["nudge_confidence"]),
            max_final_answer_nudges=int(values["max_final_answer_nudges"]),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.engine)

    def validate(self) -> None:
        if self.engine not in SUPPORTED_DECISION_ENGINES:
            raise ConfigurationError(
                f"Unsupported decisions.engine: {self.engine}. Choose 'openjev' or leave it empty."
            )
        if self.engine and urlparse(self.base_url).scheme not in {"http", "https"}:
            raise ConfigurationError("decisions.base_url must be an http or https URL.")
        if self.api not in SUPPORTED_DECISION_APIS:
            raise ConfigurationError(
                f"Unsupported decisions.api: {self.api}. "
                f"Choose one of {sorted(SUPPORTED_DECISION_APIS)}."
            )
        if self.timeout_s <= 0:
            raise ConfigurationError("decisions.timeout_s must be positive.")
        if self.retry_after_s < 0:
            raise ConfigurationError("decisions.retry_after_s must be zero or positive.")
        # Below one half a "yes" would also be a "no".
        for key in ("confidence", "nudge_confidence"):
            if not 0.5 <= getattr(self, key) < 1.0:
                raise ConfigurationError(f"decisions.{key} must be at least 0.5 and below 1.")
        if self.max_final_answer_nudges < 0:
            raise ConfigurationError("decisions.max_final_answer_nudges must be zero or positive.")


@dataclass(slots=True)
class GraphConfig:
    """Limits for one orchestrator-graph run (the Blueprint-style editor)."""

    max_nodes_executed: int = int(DEFAULT_GRAPH["max_nodes_executed"])
    max_seconds: int = int(DEFAULT_GRAPH["max_seconds"])
    max_loop_iterations: int = int(DEFAULT_GRAPH["max_loop_iterations"])
    max_concurrent_nodes: int = int(DEFAULT_GRAPH["max_concurrent_nodes"])

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> GraphConfig:
        values = dict(DEFAULT_GRAPH)
        if data:
            values.update(data)
        return cls(
            max_nodes_executed=int(values["max_nodes_executed"]),
            max_seconds=int(values["max_seconds"]),
            max_loop_iterations=int(values["max_loop_iterations"]),
            max_concurrent_nodes=int(values["max_concurrent_nodes"]),
        )

    def validate(self) -> None:
        for key, value in asdict(self).items():
            if value <= 0:
                raise ConfigurationError(f"Graph value {key} must be positive.")
        if self.max_nodes_executed > 5000:
            raise ConfigurationError("graph.max_nodes_executed must be 5000 or lower.")
        if self.max_loop_iterations > 500:
            raise ConfigurationError("graph.max_loop_iterations must be 500 or lower.")


def _resolve_tool_policy(data: dict[str, Any] | None) -> str:
    """Resolve the tool policy, honoring the legacy ``strict_tool_policy`` flag."""
    if data:
        raw = data.get("tool_policy")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().lower()
        legacy = data.get("strict_tool_policy")
        if isinstance(legacy, bool):
            return "strict" if legacy else "advisory"
    return str(DEFAULT_PLANNER["tool_policy"])


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


@dataclass(slots=True)
class ExperimentalConfig:
    """Features still being measured, each off by default (Doctor > Experimental)."""

    # Every tool outside the planner's control tools is left out of the request
    # until the model loads it with load_tool. See tools/on_demand.py.
    on_demand_tools: bool = False

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> ExperimentalConfig:
        data = data or {}
        return cls(on_demand_tools=bool(data.get("on_demand_tools", False)))


@dataclass(slots=True)
class SamplingConfig:
    """Model sampling and reasoning controls shared by every provider.

    Standard fields map directly onto the OpenAI request body. ``top_k``,
    ``min_p`` and ``repeat_penalty`` are not part of the OpenAI schema, so they
    are forwarded through ``extra_body`` for OpenAI-compatible servers (vLLM,
    SGLang, llama.cpp, ...) and as ``options`` in native Ollama mode.
    ``reasoning_effort`` is sent on both Chat Completions and Responses (it is
    what enables thinking on most OpenAI-compatible local servers, not just a
    dial for how much); ``reasoning_summary`` only applies to the Responses API.
    Any value left as ``None`` is omitted so the endpoint default applies.
    """

    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    followup_reasoning_effort: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> SamplingConfig:
        values = dict(DEFAULT_SAMPLING)
        if data:
            values.update(data)
        extra = values.get("extra_body") or {}
        if not isinstance(extra, dict):
            raise ConfigurationError("sampling.extra_body must be a JSON object.")
        extra = dict(extra)
        if "repeat_penalty" in extra and "repeat_penalty" not in (data or {}):
            # It used to live only in the passthrough. A file from then keeps
            # working, and the value shows up on the Model tab's own knob
            # instead of being buried in the JSON field.
            values["repeat_penalty"] = extra.pop("repeat_penalty")
        return cls(
            temperature=_opt_float(values.get("temperature")),
            top_p=_opt_float(values.get("top_p")),
            presence_penalty=_opt_float(values.get("presence_penalty")),
            frequency_penalty=_opt_float(values.get("frequency_penalty")),
            top_k=_opt_int(values.get("top_k")),
            min_p=_opt_float(values.get("min_p")),
            repeat_penalty=_opt_float(values.get("repeat_penalty")),
            reasoning_effort=_opt_str(values.get("reasoning_effort")),
            reasoning_summary=_opt_str(values.get("reasoning_summary")),
            followup_reasoning_effort=_opt_str(values.get("followup_reasoning_effort")),
            extra_body=extra,
        )

    def validate(self) -> None:
        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ConfigurationError("sampling.temperature must be between 0.0 and 2.0.")
        if self.top_p is not None and not 0.0 < self.top_p <= 1.0:
            raise ConfigurationError("sampling.top_p must be within (0.0, 1.0].")
        if self.min_p is not None and not 0.0 <= self.min_p <= 1.0:
            raise ConfigurationError("sampling.min_p must be between 0.0 and 1.0.")
        if self.top_k is not None and self.top_k < 0:
            raise ConfigurationError("sampling.top_k must be zero or positive.")
        if self.repeat_penalty is not None and not 0.0 <= self.repeat_penalty <= 2.0:
            raise ConfigurationError("sampling.repeat_penalty must be between 0.0 and 2.0.")
        for name, value in (
            ("presence_penalty", self.presence_penalty),
            ("frequency_penalty", self.frequency_penalty),
        ):
            if value is not None and not -2.0 <= value <= 2.0:
                raise ConfigurationError(f"sampling.{name} must be between -2.0 and 2.0.")
        for name, effort in (
            ("reasoning_effort", self.reasoning_effort),
            ("followup_reasoning_effort", self.followup_reasoning_effort),
        ):
            if effort is not None and effort not in SUPPORTED_REASONING_EFFORTS:
                raise ConfigurationError(
                    f"sampling.{name} must be one of {sorted(SUPPORTED_REASONING_EFFORTS)}."
                )
        if (
            self.reasoning_summary is not None
            and self.reasoning_summary not in SUPPORTED_REASONING_SUMMARIES
        ):
            raise ConfigurationError(
                "sampling.reasoning_summary must be one of "
                f"{sorted(SUPPORTED_REASONING_SUMMARIES)}."
            )

    def light_reasoning_effort(self) -> str | None:
        """Effort for bookkeeping calls: capped at "low", never switched on.

        An unset effort stays unset - an endpoint that never saw the field may
        refuse it, and that refusal drops every sampling knob for the session.
        """
        effort = self.reasoning_effort
        if effort is None or effort not in _REASONING_EFFORT_ORDER:
            return effort
        light = _REASONING_EFFORT_ORDER.index(_LIGHT_REASONING_EFFORT)
        return _REASONING_EFFORT_ORDER[min(_REASONING_EFFORT_ORDER.index(effort), light)]

    def followup_effort(self) -> str | None:
        """Effort for a routine tool-loop step: the follow-up knob, never above
        reasoning_effort, and nothing when either is unset."""
        followup, main = self.followup_reasoning_effort, self.reasoning_effort
        order = _REASONING_EFFORT_ORDER
        if followup not in order or main not in order:
            return None
        return order[min(order.index(followup), order.index(main))]

    def effort_for(self, requested: str | None) -> str | None:
        """The effort one request runs at: its own replaces the configured one,
        but never switches reasoning on when the session sends none."""
        if requested and self.reasoning_effort is not None:
            return requested
        return self.reasoning_effort

    def thinking_off(self, requested: str | None = None) -> bool:
        return self.effort_for(requested) == "none"

    def _extra_body_with_passthrough(self) -> dict[str, Any]:
        extra_body = dict(self.extra_body)
        if self.top_k is not None:
            extra_body.setdefault("top_k", self.top_k)
        if self.min_p is not None:
            extra_body.setdefault("min_p", self.min_p)
        if self.repeat_penalty is not None:
            extra_body.setdefault("repeat_penalty", self.repeat_penalty)
        return extra_body

    def chat_completion_kwargs(self, effort: str | None = None) -> dict[str, Any]:
        """Sampling kwargs for the OpenAI Chat Completions endpoint; ``effort``
        is the request's own, see ``effort_for``."""
        effort = self.effort_for(effort)
        kwargs: dict[str, Any] = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.presence_penalty is not None:
            kwargs["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty is not None:
            kwargs["frequency_penalty"] = self.frequency_penalty
        if effort is not None:
            # Chat Completions carries reasoning_effort as a plain field, and for
            # several local servers it is the *only* switch that turns thinking
            # on: Ollama's OpenAI-compatible endpoint ignores its own `think`
            # flag here and enables reasoning solely on this parameter, then
            # streams the result as `delta.reasoning`. Omitting it left every
            # such model apparently thinking-free. A server that rejects the
            # field trips the sampling-retry path and the request goes again
            # without any sampling kwargs.
            kwargs["reasoning_effort"] = effort
        extra_body = self._extra_body_with_passthrough()
        template = extra_body.get("chat_template_kwargs")
        if effort == "none" and (template is None or isinstance(template, dict)):
            # Qwen-style templates on vLLM, SGLang and llama.cpp think unless
            # told not to, whatever the effort says. The user's own keys win.
            extra_body["chat_template_kwargs"] = {"enable_thinking": False, **(template or {})}
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def responses_kwargs(self) -> dict[str, Any]:
        """Sampling kwargs for the OpenAI Responses endpoint."""
        kwargs: dict[str, Any] = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        # presence/frequency penalties are not part of the Responses schema.
        reasoning: dict[str, Any] = {}
        if self.reasoning_effort is not None:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_summary is not None:
            reasoning["summary"] = self.reasoning_summary
        if reasoning:
            kwargs["reasoning"] = reasoning
        extra_body = self._extra_body_with_passthrough()
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def ollama_options(self) -> dict[str, Any]:
        """Sampling options for the native Ollama ``/api/chat`` endpoint."""
        options: dict[str, Any] = {}
        if self.temperature is not None:
            options["temperature"] = self.temperature
        if self.top_p is not None:
            options["top_p"] = self.top_p
        if self.top_k is not None:
            options["top_k"] = self.top_k
        if self.min_p is not None:
            options["min_p"] = self.min_p
        if self.repeat_penalty is not None:
            options["repeat_penalty"] = self.repeat_penalty
        if self.presence_penalty is not None:
            options["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty is not None:
            options["frequency_penalty"] = self.frequency_penalty
        return options


@dataclass(slots=True)
class AppConfig:
    api_key: str = ""
    api_mode: str = "responses"
    base_url: str = "http://localhost:11434/v1"
    permission_mode: str = "ask"
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    decisions: DecisionConfig = field(default_factory=DecisionConfig)
    experimental: ExperimentalConfig = field(default_factory=ExperimentalConfig)
    goal: GoalConfig = field(default_factory=GoalConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    file_io: FileIOConfig = field(default_factory=FileIOConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    language: str = "en"
    model: str = "gemma4:31b-cloud"
    # Inline code hints (editor ghost text) in the VSCode extension. Off by
    # default; ``inline_model`` overrides the model used for these completions
    # (empty falls back to ``model``) so a small/fast model can drive hints while
    # the agent uses a stronger one.
    inline_hints_enabled: bool = False
    inline_model: str = ""
    # Vision sidekick for non-multimodal main models. When set, images pasted
    # into a prompt are described by this model in a one-off call and the
    # description is injected into the conversation instead of the raw pixels,
    # so the main model never receives image payloads it cannot read. Empty
    # sends images straight to the main model (multimodal setups).
    vision_model: str = ""
    # Hard cap on images carried by a single model request. 0 leaves it to the
    # endpoint, which is the common case: a server that only takes one image per
    # prompt says so when it refuses one, and the limit is learned from there.
    # Set it when the endpoint refuses without naming a number.
    max_images_per_request: int = 0
    # How many of the most recent image-bearing messages still carry their
    # pixels in a request; older ones travel as a one-line note. Every
    # screenshot kept in the history is re-uploaded on every step and, on the
    # engines that cache the prompt prefix, the cache stops at the first
    # image - so an hour-long desktop session pays for all of them each step.
    # 0 keeps every image.
    image_history_limit: int = 2
    debug: bool = False
    show_ui: bool = True
    ssl_verification: bool = False
    use_remote_conversation_state: bool = True
    strict_tools: bool = False
    workspace: Path = field(default_factory=Path.cwd)
    context_compression_threshold: float = 0.82
    context_compression_target: float = 0.55
    output_token_reserve: int = 32768
    headless_event_format: str = "text"
    terminal_theme: str = "monokai"
    terminal_banner_font: str = "tarty2"
    terminal_spinner: str = "ascii"
    terminal_session_collapsed: bool = False
    # Show the file being written as the model streams it. Off leaves only the
    # one-line progress trace, for terminals where repainting a code window on
    # every fragment is too expensive (a slow SSH link, a heavy multiplexer).
    terminal_live_code: bool = True
    learn: bool = True
    # Tools switched off (Doctor > Tools). The agent is built as if they did not
    # exist: no schema, no prompt catalog entry, and a call is an unknown tool.
    disabled_tools: list[str] = field(default_factory=list)
    # Extra models sub-agents may run on (Doctor > Sub-agents). They are served
    # by the same endpoint as the main model - only the model name changes - so
    # a machine hosting several models can spend a cheap one on exploration and
    # keep the expensive one for the conversation. Empty means every sub-agent
    # inherits the session model.
    subagent_models: list[str] = field(default_factory=list)
    # MCP servers, in the shape Claude Code and Cursor use: name -> {command,
    # args, env} or {type: "http", url, headers}. The workspace can declare more
    # (.code-ai/mcp.json, .mcp.json, ...); those start once their fingerprint
    # is in mcp_approved.
    mcp_enabled: bool = True
    # Commands get the user's environment minus what looks like a credential
    # (OPENAI_API_KEY, GITHUB_TOKEN); on, those reach them too.
    commands_see_secrets: bool = False
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    mcp_approved: list[str] = field(default_factory=list)
    # How tool calls reach the model. "auto" asks for native function calling
    # and falls back to the text protocol the first time an endpoint refuses
    # it; "native" insists; "text" never asks. The fallback is what makes a
    # vLLM served without a tool parser usable at all.
    tool_calling: str = "auto"
    # Directory for persistent cross-session failure memories. ``None`` resolves
    # to ``<config dir>/memories`` at startup; tests point it at a temp dir.
    memories_dir: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AppConfig:
        budgets = BudgetConfig.from_mapping(
            data.get("budgets") if isinstance(data.get("budgets"), dict) else None
        )
        experimental = ExperimentalConfig.from_mapping(
            data.get("experimental") if isinstance(data.get("experimental"), dict) else None
        )
        decisions = DecisionConfig.from_mapping(
            data.get("decisions") if isinstance(data.get("decisions"), dict) else None
        )
        goal = GoalConfig.from_mapping(
            data.get("goal") if isinstance(data.get("goal"), dict) else None
        )
        graph = GraphConfig.from_mapping(
            data.get("graph") if isinstance(data.get("graph"), dict) else None
        )
        memory_config = MemoryConfig.from_mapping(
            data.get("memory") if isinstance(data.get("memory"), dict) else None
        )
        planner = PlannerConfig.from_mapping(
            data.get("planner") if isinstance(data.get("planner"), dict) else None
        )
        sampling = SamplingConfig.from_mapping(
            data.get("sampling") if isinstance(data.get("sampling"), dict) else None
        )
        sandbox = SandboxConfig.from_mapping(
            data.get("sandbox") if isinstance(data.get("sandbox"), dict) else None
        )
        file_io = FileIOConfig.from_mapping(
            data.get("file_io") if isinstance(data.get("file_io"), dict) else None
        )
        browser = BrowserConfig.from_mapping(
            data.get("browser") if isinstance(data.get("browser"), dict) else None
        )
        index = IndexConfig.from_mapping(
            data.get("index") if isinstance(data.get("index"), dict) else None
        )
        workspace = Path(str(data.get("workspace", Path.cwd()))).expanduser()
        config = cls(
            api_key=normalize_api_key(str(data.get("api_key", ""))),
            api_mode=normalize_api_mode(str(data.get("api_mode", "responses"))),
            base_url=str(data.get("base_url", "http://localhost:11434/v1")),
            permission_mode=str(data.get("permission_mode", "ask")).strip().lower(),
            budgets=budgets,
            decisions=decisions,
            experimental=experimental,
            goal=goal,
            graph=graph,
            memory=memory_config,
            planner=planner,
            sampling=sampling,
            sandbox=sandbox,
            file_io=file_io,
            browser=browser,
            index=index,
            language=str(data.get("language", "en")),
            model=str(data.get("model", "gemma4:31b-cloud")),
            inline_hints_enabled=bool(data.get("inline_hints_enabled", False)),
            inline_model=str(data.get("inline_model", "")),
            vision_model=str(data.get("vision_model", "")),
            max_images_per_request=int(data.get("max_images_per_request", 0) or 0),
            image_history_limit=int(data.get("image_history_limit", 2) or 0),
            debug=bool(data.get("debug", False)),
            show_ui=bool(data.get("show_ui", True)),
            ssl_verification=bool(data.get("ssl_verification", False)),
            use_remote_conversation_state=bool(data.get("use_remote_conversation_state", True)),
            strict_tools=bool(data.get("strict_tools", False)),
            workspace=workspace,
            context_compression_threshold=float(data.get("context_compression_threshold", 0.82)),
            context_compression_target=float(data.get("context_compression_target", 0.55)),
            output_token_reserve=int(data.get("output_token_reserve", 32768)),
            headless_event_format=str(data.get("headless_event_format", "text")),
            terminal_theme=str(data.get("terminal_theme", "monokai")),
            terminal_banner_font=str(data.get("terminal_banner_font", "tarty2")),
            terminal_spinner=str(data.get("terminal_spinner", "ascii")),
            terminal_session_collapsed=bool(data.get("terminal_session_collapsed", False)),
            terminal_live_code=bool(data.get("terminal_live_code", True)),
            learn=bool(data.get("learn", True)),
            disabled_tools=sorted(
                {str(name).strip() for name in data.get("disabled_tools") or ()} - {""}
            ),
            # Order is the user's, not sorted: the list is a preference ranking
            # they build by hand in the Doctor, and the first entry is the one a
            # sub-agent gets by default.
            subagent_models=_unique_names(data.get("subagent_models")),
            mcp_enabled=bool(data.get("mcp_enabled", True)),
            commands_see_secrets=bool(data.get("commands_see_secrets", False)),
            mcp_servers=(
                dict(data["mcp_servers"]) if isinstance(data.get("mcp_servers"), dict) else {}
            ),
            mcp_approved=_unique_names(data.get("mcp_approved")),
            tool_calling=str(data.get("tool_calling", "auto")),
            memories_dir=(
                str(data["memories_dir"]) if data.get("memories_dir") is not None else None
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        self.api_mode = normalize_api_mode(self.api_mode)
        if self.api_mode not in SUPPORTED_API_MODES:
            raise ConfigurationError(f"Unsupported api_mode: {self.api_mode}.")
        self.permission_mode = self.permission_mode.strip().lower()
        if self.permission_mode not in SUPPORTED_PERMISSION_MODES:
            raise ConfigurationError(
                f"Unsupported permission_mode: {self.permission_mode}. "
                f"Choose one of {sorted(SUPPORTED_PERMISSION_MODES)}."
            )
        if not self.model.strip():
            raise ConfigurationError("model must be non-empty.")
        if not self.workspace.exists() or not self.workspace.is_dir():
            raise ConfigurationError(f"workspace must exist and be a directory: {self.workspace}")
        self.workspace = self.workspace.resolve()
        self.budgets.validate()
        self.decisions.validate()
        self.goal.validate()
        self.graph.validate()
        self.memory.validate()
        self.planner.validate()
        self.sampling.validate()
        self.sandbox.validate()
        self.file_io.validate()
        self.index.validate()
        self.browser.validate()
        parsed = urlparse(self.base_url)
        if self.api_mode in {"responses", "completions", "ollama"} and parsed.scheme not in {
            "http",
            "https",
        }:
            raise ConfigurationError("base_url must be an http or https URL.")
        if not (0.1 <= self.context_compression_target < self.context_compression_threshold < 1.0):
            raise ConfigurationError(
                "context_compression_target must be below threshold and both values must be "
                "between 0.1 and 1.0."
            )
        if self.output_token_reserve <= 0:
            raise ConfigurationError("output_token_reserve must be positive.")
        if self.max_images_per_request < 0:
            raise ConfigurationError("max_images_per_request must be zero or positive.")
        if self.image_history_limit < 0:
            raise ConfigurationError("image_history_limit must be zero or positive.")
        self.tool_calling = self.tool_calling.strip().lower()
        if self.tool_calling not in SUPPORTED_TOOL_CALLING_MODES:
            raise ConfigurationError(
                f"Unsupported tool_calling: {self.tool_calling}. "
                f"Choose one of {sorted(SUPPORTED_TOOL_CALLING_MODES)}."
            )
        self.subagent_models = _unique_names(self.subagent_models)
        if not self.terminal_theme.strip():
            raise ConfigurationError("terminal_theme must be non-empty.")
        if not self.terminal_banner_font.strip():
            raise ConfigurationError("terminal_banner_font must be non-empty.")
        if not self.terminal_spinner.strip():
            raise ConfigurationError("terminal_spinner must be non-empty.")
        if (
            self.api_mode in {"responses", "completions"}
            and not self.api_key
            and not _is_local_base_url(self.base_url)
        ):
            raise ConfigurationError(
                "api_key is required for non-local OpenAI-compatible endpoints."
            )

    def serves_model_locally(self) -> bool:
        """A loopback endpoint running the model itself - not an Ollama cloud
        model, which the local daemon only forwards."""
        return _is_local_base_url(self.base_url) and not _is_cloud_model(self.model)

    def endpoint_requires_placeholder_key(self) -> bool:
        return (
            self.api_mode in {"responses", "completions"}
            and not self.api_key
            and _is_local_base_url(self.base_url)
        )

    def provider_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.endpoint_requires_placeholder_key():
            return "code-ai-local-placeholder"
        return ""

    def to_dict(self, *, redacted: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["workspace"] = str(self.workspace)
        if redacted:
            return redact_mapping(data)
        return data
