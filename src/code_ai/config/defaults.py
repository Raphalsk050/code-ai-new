from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

DEFAULT_CONFIG_DIRNAME = ".code-ai"
DEFAULT_CONFIG_FILENAME = "config.json"

# Rules - mandatory instructions always injected into the system prompt. Global
# rules live install-wide; project rules live inside the workspace so they can be
# committed and shared with a team. The global dir is overridable via an env var
# so tests never touch the real home directory (mirrors CODE_AI_SKILLS_DIR).
RULES_DIRNAME = "rules"
RULES_DIR_ENV = "CODE_AI_RULES_DIR"

# The project's own instruction file: one markdown file at the workspace root
# saying how this codebase wants to be worked on. It sits at the root rather than
# inside ``.code-ai/`` so everyone on the team can see it, and it is read as the
# most authoritative rule there is - the point of the file is to be able to
# override the agent's built-in guidance without editing the agent.
#
# Three files, in ascending precedence: an install-wide one for how *you* work,
# the committed project file for how the *team* works, and a local file for
# personal overrides in this one checkout (keep it out of version control).
INSTRUCTIONS_FILENAME = "CODEAI.md"
INSTRUCTIONS_LOCAL_FILENAME = "CODEAI.local.md"

# Workflows - named procedures invoked on demand (unlike rules, which are always
# injected). Same two scopes and the same override story as rules: global ones
# live install-wide, project ones live in the workspace so they can be committed.
WORKFLOWS_DIRNAME = "workflows"
WORKFLOWS_DIR_ENV = "CODE_AI_WORKFLOWS_DIR"

# Sandbox - the isolated scratch root where builds, generated code, temporary
# files and captured test output live, so none of them ever land in the user's
# project tree. One directory per session under a shared base; the base is
# overridable via an env var for tests and for machines whose temp dir is small.
SANDBOX_DIRNAME = "python_agent_sandbox"
SANDBOX_DIR_ENV = "CODE_AI_SANDBOX_DIR"

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


def global_instructions_file() -> Path:
    """Install-wide ``CODEAI.md``: how you want the agent to work everywhere.

    Follows ``CODE_AI_RULES_DIR`` when set so a test or an alternate setup that
    relocates rules relocates this file with them, and never reads the real home
    directory by accident.
    """

    override = os.environ.get(RULES_DIR_ENV)
    root = Path(override).expanduser() if override else Path.home() / DEFAULT_CONFIG_DIRNAME
    return root / INSTRUCTIONS_FILENAME


def project_instructions_files(workspace: Path | str) -> list[Path]:
    """Workspace ``CODEAI.md`` files, least authoritative first.

    The committed file states how the project wants to be worked on; the
    ``.local`` one is for a single checkout and should stay out of version
    control. Returned in precedence order so a later file's wording wins where
    the two disagree.
    """

    resolved = Path(workspace).expanduser().resolve()
    return [
        resolved / INSTRUCTIONS_FILENAME,
        resolved / INSTRUCTIONS_LOCAL_FILENAME,
    ]


def global_workflows_dir() -> Path:
    """Directory holding install-wide workflows.

    Sits beside the global rules so personal procedures are available in every
    workspace. Overridable via ``CODE_AI_WORKFLOWS_DIR``.
    """

    override = os.environ.get(WORKFLOWS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_CONFIG_DIRNAME / WORKFLOWS_DIRNAME


def project_workflows_dir(workspace: Path | str) -> Path:
    """Directory holding workflows scoped to a single workspace.

    Lives inside the workspace (``<workspace>/.code-ai/workflows``), like project
    rules, so a team's procedures are committed with the repository.
    """

    resolved = Path(workspace).expanduser().resolve()
    return resolved / DEFAULT_CONFIG_DIRNAME / WORKFLOWS_DIRNAME


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


def default_sandbox_base_dir() -> Path:
    """Base directory holding one isolated sandbox per session.

    Lives in the system temp dir rather than under the config dir or the
    workspace: sandboxes are disposable by design, and the OS already reclaims
    that space if a process dies before its own cleanup runs. ``CODE_AI_SANDBOX_DIR``
    relocates it for tests and for hosts whose temp dir is too small to build in.
    """

    override = os.environ.get(SANDBOX_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(tempfile.gettempdir()) / SANDBOX_DIRNAME


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
    # Sub-agent orchestration limits. Depth 1 means the main agent may delegate
    # but a sub-agent may not delegate further (no unbounded recursion).
    "max_subagent_depth": 1,
    "max_concurrent_subagents": 4,
    "max_subagents_per_turn": 12,
    # Resilience: retry attempts per sub-agent (total tries, including the first)
    # and the circuit breaker thresholds (consecutive failures / cooldown).
    "subagent_retry_max_attempts": 2,
    "subagent_circuit_failure_threshold": 3,
    "subagent_circuit_reset_s": 30,
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
    # High-risk changes (many files, a failed verification this turn, complex
    # tasks) must be backed by an independent review (code_review or a reviewer
    # sub-agent) run after the last change, when review tools are available.
    "require_review_for_risky_changes": True,
    "max_plan_steps": 20,
    "max_discovery_rounds": 8,
    "max_replans": 3,
    "max_step_attempts": 3,
    "max_no_progress_rounds": 3,
    # Consecutive complete_task rejections tolerated while the progress
    # fingerprint stays frozen before the gate fails open and accepts with the
    # unresolved requirements surfaced as limitations.
    "max_completion_rejections": 2,
    "persist_plan": True,
}


DEFAULT_MEMORY: dict[str, object] = {
    # Post-turn reflection: one bounded meta-call after a substantive turn that
    # distills durable memories (user corrections, project facts, references)
    # without the model having to remember to call the ``remember`` tool.
    "reflection_enabled": True,
    # A turn is worth reflecting on once it executed at least this many tool
    # calls; 0 reflects on every completed turn.
    "reflection_min_tool_calls": 3,
    # Output cap for the reflection meta-call. Generous because reasoning
    # models spend output budget on hidden thinking before the JSON answer.
    "reflection_max_output_tokens": 4096,
    # Automatic consolidation: merge near-duplicate memories and retire
    # contradicted facts once enough new entries accumulated since the last
    # pass, so the store stays small and coherent without manual curation.
    "consolidation_enabled": True,
    "consolidation_min_new": 20,
    # Prompt rendering bounds. Identity ("user" kind) is always rendered in
    # full; other kinds are capped per kind so an ever-growing store cannot
    # silently bloat every request.
    "render_limit_per_kind": 25,
    "lessons_render_limit": 8,
    # A failure lesson reinforced this many times is pinned: it stays in the
    # prompt even when newer one-off lessons would otherwise crowd it out.
    "lesson_pin_count": 5,
}


DEFAULT_GOAL: dict[str, object] = {
    # Hard ceilings for the /goal loop: the runner never spins past these, so a
    # goal that cannot converge ends in EXHAUSTED with an honest report instead
    # of consuming the session forever.
    "max_iterations": 25,
    "max_goal_minutes": 60,
    # Consecutive iterations failing the *same* criteria with no workspace
    # progress before the goal blocks and escalates to the user.
    "max_no_progress_iterations": 3,
    # Whether JUDGE criteria are evaluated by a one-off model call. Disabled,
    # they pass with an explicit caveat (deterministic criteria still gate).
    "judge_enabled": True,
    # Whether /goal waits for the user to confirm the derived acceptance
    # criteria (/goal start) before the loop begins.
    "confirm_criteria": True,
}


DEFAULT_SANDBOX: dict[str, object] = {
    # Master switch. Disabled, tools fall back to the previous behaviour (every
    # write and every command lands in the workspace) instead of failing.
    "enabled": True,
    # Empty resolves to :func:`default_sandbox_base_dir` at startup.
    "base_dir": "",
    # How long a sandbox left behind by a crashed session survives before the
    # next startup reaps it.
    "ttl_hours": 24,
    # Whether this session's own sandbox is removed when the app closes. Turn it
    # off to inspect what a build produced after the fact.
    "cleanup_on_exit": True,
    # Per-stream ceiling for a captured run log, so one runaway build cannot
    # fill the disk with a single artifact.
    "max_artifact_bytes": 2_000_000,
}


DEFAULT_CONFIG: dict[str, object] = {
    "api_key": PLACEHOLDER_API_KEY,
    "api_mode": "responses",
    "base_url": "http://localhost:11434/v1",
    "permission_mode": "ask",
    "budgets": DEFAULT_BUDGETS,
    "goal": DEFAULT_GOAL,
    "memory": DEFAULT_MEMORY,
    "planner": DEFAULT_PLANNER,
    "sandbox": DEFAULT_SANDBOX,
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
    "terminal_live_code": True,
}
