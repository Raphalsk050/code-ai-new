from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from code_ai.config.defaults import (
    DEFAULT_BUDGETS,
    DEFAULT_PLANNER,
    DEFAULT_SAMPLING,
    PLACEHOLDER_API_KEY,
)
from code_ai.core.errors import ConfigurationError
from code_ai.util.redaction import redact_mapping

SUPPORTED_API_MODES = {"responses", "completions", "ollama"}
SUPPORTED_PERMISSION_MODES = {"ask", "auto", "bypass"}
SUPPORTED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
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


@dataclass(slots=True)
class BudgetConfig:
    build_tool_timeout_s: int = DEFAULT_BUDGETS["build_tool_timeout_s"]
    default_tool_timeout_s: int = DEFAULT_BUDGETS["default_tool_timeout_s"]
    max_context_tokens: int = DEFAULT_BUDGETS["max_context_tokens"]
    max_model_call_s: int = DEFAULT_BUDGETS["max_model_call_s"]
    max_model_step_seconds: int = DEFAULT_BUDGETS["max_model_step_seconds"]
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
    subagent_circuit_failure_threshold: int = DEFAULT_BUDGETS[
        "subagent_circuit_failure_threshold"
    ]
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
            require_verification_for_changes=bool(
                values["require_verification_for_changes"]
            ),
            double_check_completion=bool(values["double_check_completion"]),
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
class SamplingConfig:
    """Model sampling and reasoning controls shared by every provider.

    Standard fields map directly onto the OpenAI request body. ``top_k`` and
    ``min_p`` are not part of the OpenAI schema, so they are forwarded through
    ``extra_body`` for OpenAI-compatible servers (vLLM, SGLang, ...).
    ``reasoning_effort``/``reasoning_summary`` only apply to the Responses API.
    Any value left as ``None`` is omitted so the endpoint default applies.
    """

    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    reasoning_effort: str | None = None
    reasoning_summary: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> SamplingConfig:
        values = dict(DEFAULT_SAMPLING)
        if data:
            values.update(data)
        extra = values.get("extra_body") or {}
        if not isinstance(extra, dict):
            raise ConfigurationError("sampling.extra_body must be a JSON object.")
        return cls(
            temperature=_opt_float(values.get("temperature")),
            top_p=_opt_float(values.get("top_p")),
            presence_penalty=_opt_float(values.get("presence_penalty")),
            frequency_penalty=_opt_float(values.get("frequency_penalty")),
            top_k=_opt_int(values.get("top_k")),
            min_p=_opt_float(values.get("min_p")),
            reasoning_effort=_opt_str(values.get("reasoning_effort")),
            reasoning_summary=_opt_str(values.get("reasoning_summary")),
            extra_body=dict(extra),
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
        for name, value in (
            ("presence_penalty", self.presence_penalty),
            ("frequency_penalty", self.frequency_penalty),
        ):
            if value is not None and not -2.0 <= value <= 2.0:
                raise ConfigurationError(f"sampling.{name} must be between -2.0 and 2.0.")
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in SUPPORTED_REASONING_EFFORTS
        ):
            raise ConfigurationError(
                "sampling.reasoning_effort must be one of "
                f"{sorted(SUPPORTED_REASONING_EFFORTS)}."
            )
        if (
            self.reasoning_summary is not None
            and self.reasoning_summary not in SUPPORTED_REASONING_SUMMARIES
        ):
            raise ConfigurationError(
                "sampling.reasoning_summary must be one of "
                f"{sorted(SUPPORTED_REASONING_SUMMARIES)}."
            )

    def _extra_body_with_passthrough(self) -> dict[str, Any]:
        extra_body = dict(self.extra_body)
        if self.top_k is not None:
            extra_body.setdefault("top_k", self.top_k)
        if self.min_p is not None:
            extra_body.setdefault("min_p", self.min_p)
        return extra_body

    def chat_completion_kwargs(self) -> dict[str, Any]:
        """Sampling kwargs for the OpenAI Chat Completions endpoint."""
        kwargs: dict[str, Any] = {}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.top_p is not None:
            kwargs["top_p"] = self.top_p
        if self.presence_penalty is not None:
            kwargs["presence_penalty"] = self.presence_penalty
        if self.frequency_penalty is not None:
            kwargs["frequency_penalty"] = self.frequency_penalty
        extra_body = self._extra_body_with_passthrough()
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
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
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
    terminal_theme: str = "textual-dark"
    terminal_banner_font: str = "tarty2"
    terminal_spinner: str = "ascii"
    terminal_session_collapsed: bool = False
    learn: bool = True
    # Directory for persistent cross-session failure memories. ``None`` resolves
    # to ``<config dir>/memories`` at startup; tests point it at a temp dir.
    memories_dir: str | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AppConfig:
        budgets = BudgetConfig.from_mapping(
            data.get("budgets") if isinstance(data.get("budgets"), dict) else None
        )
        planner = PlannerConfig.from_mapping(
            data.get("planner") if isinstance(data.get("planner"), dict) else None
        )
        sampling = SamplingConfig.from_mapping(
            data.get("sampling") if isinstance(data.get("sampling"), dict) else None
        )
        workspace = Path(str(data.get("workspace", Path.cwd()))).expanduser()
        config = cls(
            api_key=normalize_api_key(str(data.get("api_key", ""))),
            api_mode=normalize_api_mode(str(data.get("api_mode", "responses"))),
            base_url=str(data.get("base_url", "http://localhost:11434/v1")),
            permission_mode=str(data.get("permission_mode", "ask")).strip().lower(),
            budgets=budgets,
            planner=planner,
            sampling=sampling,
            language=str(data.get("language", "en")),
            model=str(data.get("model", "gemma4:31b-cloud")),
            inline_hints_enabled=bool(data.get("inline_hints_enabled", False)),
            inline_model=str(data.get("inline_model", "")),
            vision_model=str(data.get("vision_model", "")),
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
            terminal_theme=str(data.get("terminal_theme", "textual-dark")),
            terminal_banner_font=str(data.get("terminal_banner_font", "tarty2")),
            terminal_spinner=str(data.get("terminal_spinner", "ascii")),
            terminal_session_collapsed=bool(data.get("terminal_session_collapsed", False)),
            learn=bool(data.get("learn", True)),
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
        self.planner.validate()
        self.sampling.validate()
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
