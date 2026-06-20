from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from code_ai.config.defaults import DEFAULT_BUDGETS
from code_ai.core.errors import ConfigurationError
from code_ai.util.redaction import redact_mapping

SUPPORTED_API_MODES = {"responses", "completions", "ollama"}


def normalize_api_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode == "chat_completions":
        return "completions"
    return mode


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
    max_tool_call_seconds: int = DEFAULT_BUDGETS["max_tool_call_seconds"]
    max_tool_calls: int = DEFAULT_BUDGETS["max_tool_calls"]
    max_tool_output_chars: int = DEFAULT_BUDGETS["max_tool_output_chars"]
    max_tool_wall_time_s: int = DEFAULT_BUDGETS["max_tool_wall_time_s"]
    max_turn_seconds: int = DEFAULT_BUDGETS["max_turn_seconds"]
    max_turn_wall_time_s: int = DEFAULT_BUDGETS["max_turn_wall_time_s"]
    subagent_explorer_timeout_s: int = DEFAULT_BUDGETS["subagent_explorer_timeout_s"]
    subagent_worker_timeout_s: int = DEFAULT_BUDGETS["subagent_worker_timeout_s"]

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
class AppConfig:
    api_key: str = ""
    api_mode: str = "responses"
    base_url: str = "http://localhost:11434/v1"
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    language: str = "en"
    model: str = "gemma4:31b-cloud"
    show_ui: bool = True
    ssl_verification: bool = False
    use_remote_conversation_state: bool = True
    workspace: Path = field(default_factory=Path.cwd)
    context_compression_threshold: float = 0.82
    context_compression_target: float = 0.55
    output_token_reserve: int = 4096
    headless_event_format: str = "text"
    terminal_theme: str = "textual-dark"
    terminal_banner_font: str = "tarty2"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AppConfig:
        budgets = BudgetConfig.from_mapping(
            data.get("budgets") if isinstance(data.get("budgets"), dict) else None
        )
        workspace = Path(str(data.get("workspace", Path.cwd()))).expanduser()
        config = cls(
            api_key=str(data.get("api_key", "")),
            api_mode=normalize_api_mode(str(data.get("api_mode", "responses"))),
            base_url=str(data.get("base_url", "http://localhost:11434/v1")),
            budgets=budgets,
            language=str(data.get("language", "en")),
            model=str(data.get("model", "gemma4:31b-cloud")),
            show_ui=bool(data.get("show_ui", True)),
            ssl_verification=bool(data.get("ssl_verification", False)),
            use_remote_conversation_state=bool(data.get("use_remote_conversation_state", True)),
            workspace=workspace,
            context_compression_threshold=float(data.get("context_compression_threshold", 0.82)),
            context_compression_target=float(data.get("context_compression_target", 0.55)),
            output_token_reserve=int(data.get("output_token_reserve", 4096)),
            headless_event_format=str(data.get("headless_event_format", "text")),
            terminal_theme=str(data.get("terminal_theme", "textual-dark")),
            terminal_banner_font=str(data.get("terminal_banner_font", "tarty2")),
        )
        config.validate()
        return config

    def validate(self) -> None:
        self.api_mode = normalize_api_mode(self.api_mode)
        if self.api_mode not in SUPPORTED_API_MODES:
            raise ConfigurationError(f"Unsupported api_mode: {self.api_mode}.")
        if not self.model.strip():
            raise ConfigurationError("model must be non-empty.")
        if not self.workspace.exists() or not self.workspace.is_dir():
            raise ConfigurationError(f"workspace must exist and be a directory: {self.workspace}")
        self.workspace = self.workspace.resolve()
        self.budgets.validate()
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
