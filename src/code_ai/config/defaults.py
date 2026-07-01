from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_CONFIG_DIRNAME = ".code-ai"
DEFAULT_CONFIG_FILENAME = "config.json"

# Rules - mandatory instructions always injected into the system prompt. Global
# rules live install-wide; project rules live inside the workspace so they can be
# committed and shared with a team. The global dir is overridable via an env var
# so tests never touch the real home directory (mirrors CODE_AI_SKILLS_DIR).
RULES_DIRNAME = "rules"
RULES_DIR_ENV = "CODE_AI_RULES_DIR"

# Written into a saved config.json when no real api_key is set, so the field is
# never left blank. Treated as "unset" at runtime (see config.models), so it
# never reaches a provider and never satisfies the non-local key requirement.
PLACEHOLDER_API_KEY = "REPLACE-WITH-YOUR-API-KEY"


def default_config_path() -> Path:
    return Path.home() / DEFAULT_CONFIG_DIRNAME / DEFAULT_CONFIG_FILENAME


def default_memories_dir() -> Path:
    """Directory holding the agent's persistent failure memories.

    Lives beside ``config.json`` in the config dir so lessons learned survive
    across sessions and travel with the install, not the workspace.
    """

    return Path.home() / DEFAULT_CONFIG_DIRNAME / "memories"


def global_knowledge_dir() -> Path:
    """Directory holding durable, cross-project memories about the user.

    Lives under the config dir so ``user``/``feedback`` facts the agent learns
    apply in every workspace, the same way :func:`default_memories_dir` keeps
    failure lessons install-wide.
    """

    return Path.home() / DEFAULT_CONFIG_DIRNAME / "memories" / "knowledge"


def global_rules_dir() -> Path:
    """Directory holding install-wide mandatory rules.

    Lives under the config dir so personal rules apply in every workspace, the
    same way :func:`global_knowledge_dir` keeps cross-project facts. Overridable
    via ``CODE_AI_RULES_DIR`` for tests and alternate setups.
    """

    override = os.environ.get(RULES_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_DIRNAME / RULES_DIRNAME


def project_rules_dir(workspace: Path | str) -> Path:
    """Directory holding mandatory rules scoped to a single workspace.

    Lives inside the workspace (``<workspace>/.code-ai/rules``), unlike memories,
    so project rules can be committed to the repository and shared with the team,
    the way Cline's ``.clinerules`` travel with the project.
    """

    resolved = Path(workspace).expanduser().resolve()
    return resolved / DEFAULT_CONFIG_DIRNAME / RULES_DIRNAME


def project_conversations_dir(workspace: Path | str) -> Path:
    """Directory holding saved conversations scoped to a single workspace.

    Keyed by a hash of the absolute workspace path and kept under the config dir
    (mirrors :func:`project_memories_dir`) so saved chats persist across sessions
    and bridge restarts, let the user resume where they left off, and never leak
    across unrelated projects.
    """

    resolved = Path(workspace).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    slug = f"{resolved.name or 'root'}-{digest}"
    return Path.home() / DEFAULT_CONFIG_DIRNAME / "projects" / slug / "conversations"


def project_memories_dir(workspace: Path | str) -> Path:
    """Directory holding memories scoped to a single workspace.

    Keyed by a hash of the absolute workspace path and kept under the config dir
    (not inside the repo) so project facts never pollute the user's tree and
    never leak across unrelated projects.
    """

    resolved = Path(workspace).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    slug = f"{resolved.name or 'root'}-{digest}"
    return Path.home() / DEFAULT_CONFIG_DIRNAME / "projects" / slug / "memories"


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


DEFAULT_SAMPLING: dict[str, object] = {
    # Standard OpenAI sampling controls. ``None`` means "omit and let the
    # endpoint use its own default".
    "temperature": 0.6,
    "top_p": 0.95,
    "presence_penalty": 0.0,
    "frequency_penalty": None,
    # Not part of the OpenAI schema; forwarded via ``extra_body`` for
    # OpenAI-compatible servers (vLLM, SGLang, Ollama's OpenAI shim, ...).
    "top_k": 20,
    "min_p": 0.0,
    # Responses-API reasoning controls (official OpenAI reasoning models).
    "reasoning_effort": None,
    "reasoning_summary": None,
    # Free-form passthrough merged into the request ``extra_body``.
    "extra_body": {},
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
    "api_key": PLACEHOLDER_API_KEY,
    "api_mode": "responses",
    "base_url": "http://localhost:11434/v1",
    "permission_mode": "ask",
    "budgets": DEFAULT_BUDGETS,
    "planner": DEFAULT_PLANNER,
    "sampling": DEFAULT_SAMPLING,
    "language": "en",
    "model": "gemma4:31b-cloud",
    "show_ui": True,
    "ssl_verification": False,
    "use_remote_conversation_state": True,
    "workspace": str(Path.cwd()),
    "context_compression_threshold": 0.82,
    "context_compression_target": 0.55,
    "output_token_reserve": 32768,
    "headless_event_format": "text",
    "terminal_theme": "textual-dark",
    "terminal_banner_font": "tarty2",
    "terminal_spinner": "ascii",
    "terminal_session_collapsed": False,
}
