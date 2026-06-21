from __future__ import annotations

from pathlib import Path

DEFAULT_CONFIG_DIRNAME = ".code-ai"
DEFAULT_CONFIG_FILENAME = "config.json"


def default_config_path() -> Path:
    return Path.home() / DEFAULT_CONFIG_DIRNAME / DEFAULT_CONFIG_FILENAME


DEFAULT_BUDGETS: dict[str, int] = {
    "build_tool_timeout_s": 300,
    "default_tool_timeout_s": 60,
    "max_context_tokens": 256000,
    "max_model_call_s": 180,
    "max_model_step_seconds": 180,
    "max_model_steps": 80,
    "max_orchestration_rounds": 60,
    "max_stall_rounds": 4,
    "max_orchestration_seconds": 60,
    "max_tool_call_seconds": 900,
    "max_tool_calls": 250,
    "max_tool_output_chars": 12000,
    "max_tool_wall_time_s": 900,
    "max_turn_seconds": 900,
    "max_turn_wall_time_s": 900,
    "subagent_explorer_timeout_s": 120,
    "subagent_worker_timeout_s": 300,
}


DEFAULT_PLANNER: dict[str, object] = {
    "enabled": True,
    "mode": "auto",
    "tool_policy": "advisory",
    "local_first": True,
    "require_plan_for_mutations": True,
    "require_verification_for_changes": True,
    "double_check_completion": True,
    "max_plan_steps": 20,
    "max_discovery_rounds": 8,
    "max_replans": 3,
    "max_step_attempts": 3,
    "max_no_progress_rounds": 3,
    "persist_plan": True,
}


DEFAULT_CONFIG: dict[str, object] = {
    "api_key": "",
    "api_mode": "responses",
    "base_url": "http://localhost:11434/v1",
    "budgets": DEFAULT_BUDGETS,
    "planner": DEFAULT_PLANNER,
    "language": "en",
    "model": "gemma4:31b-cloud",
    "show_ui": True,
    "ssl_verification": False,
    "use_remote_conversation_state": True,
    "workspace": str(Path.cwd()),
    "context_compression_threshold": 0.82,
    "context_compression_target": 0.55,
    "output_token_reserve": 4096,
    "headless_event_format": "text",
    "terminal_theme": "textual-dark",
    "terminal_banner_font": "tarty2",
}
