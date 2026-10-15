from __future__ import annotations

import errno
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from code_ai.cli.main import build_parser
from code_ai.config import loader
from code_ai.config.defaults import PLACEHOLDER_API_KEY
from code_ai.config.loader import (
    config_init,
    default_config_path,
    load_config,
    persist_config_updates,
    redacted_config_json,
)
from code_ai.config.models import AppConfig, SamplingConfig
from code_ai.core.errors import ConfigurationError
from code_ai.util import fileio
from code_ai.util.fileio import FileOperationError


def test_default_config_path_is_under_home() -> None:
    path = default_config_path()
    assert path.name == "config.json"
    assert path.parent.name == ".code-ai"


def test_file_environment_and_cli_precedence(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "base_url": "http://file.example/v1",
                "model": "file-model",
                "workspace": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BASE_URL", "http://env.example/v1")
    config = load_config(
        explicit_path=config_path,
        cli_overrides={"model": "cli-model"},
    )
    assert config.base_url == "http://env.example/v1"
    assert config.model == "cli-model"


def test_vision_model_loads_from_config_file_and_defaults_empty(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "vision_model": "qwen2.5-vl:7b",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.vision_model == "qwen2.5-vl:7b"

    config_path.write_text(
        json.dumps({"api_mode": "ollama", "workspace": str(tmp_path)}),
        encoding="utf-8",
    )
    assert load_config(explicit_path=config_path).vision_model == ""


def test_terminal_theme_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "terminal_theme": "tokyo-night",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.terminal_theme == "tokyo-night"


def test_terminal_banner_font_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "terminal_banner_font": "future_1",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.terminal_banner_font == "future_1"


def test_terminal_spinner_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "terminal_spinner": "braille-full",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.terminal_spinner == "braille-full"


def test_memory_config_loads_from_config_file_with_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "memory": {"reflection_enabled": False, "consolidation_min_new": 5},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.memory.reflection_enabled is False
    assert config.memory.consolidation_min_new == 5
    # Unspecified knobs keep their defaults.
    assert config.memory.reflection_min_tool_calls == 3
    assert config.memory.render_limit_per_kind == 25
    assert config.memory.lesson_pin_count == 5


def test_invalid_memory_config_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "memory": {"render_limit_per_kind": 0},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(explicit_path=config_path)


def test_a_config_with_retired_planner_keys_still_loads(tmp_path) -> None:
    # Settings of the removed completion gate may linger in a user's config;
    # they are ignored, never an error.
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "planner": {
                    "mode": "act",
                    "require_verification_for_changes": True,
                    "double_check_completion": False,
                    "require_review_for_risky_changes": True,
                    "max_no_progress_rounds": 50,
                    "max_completion_rejections": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.planner.mode == "act"
    assert not hasattr(config.planner, "double_check_completion")


def test_invalid_budget_is_rejected(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "budgets": {"max_context_tokens": 1},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(explicit_path=config_path)


def test_zero_lifts_a_cap_only_where_a_cap_can_be_lifted() -> None:
    from code_ai.config.models import BudgetConfig

    uncapped = BudgetConfig.from_mapping({"max_model_steps": 0, "max_tool_calls": 0})
    uncapped.validate()
    assert uncapped.turn_timeout() is None
    assert BudgetConfig.from_mapping({"max_turn_seconds": 600}).turn_timeout() == 600
    with pytest.raises(ConfigurationError):
        BudgetConfig.from_mapping({"default_tool_timeout_s": 0}).validate()


def test_a_config_naming_retired_budgets_still_loads(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "budgets": {"max_orchestration_rounds": 60, "max_orchestration_seconds": 60},
            }
        ),
        encoding="utf-8",
    )
    load_config(explicit_path=config_path)


def test_redacted_config_output_hides_api_key(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "responses",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-real-secret",
                "workspace": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    rendered = redacted_config_json(config)
    assert "sk-real-secret" not in rendered
    assert "<redacted>" in rendered
    assert '"max_context_tokens": 256000' in rendered


def test_sampling_loads_from_config_file(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "base_url": "http://localhost:11434/v1",
                "workspace": str(tmp_path),
                "sampling": {
                    "temperature": 0.6,
                    "top_p": 0.95,
                    "presence_penalty": 0.0,
                    "top_k": 20,
                    "min_p": 0,
                    "extra_body": {"repetition_penalty": 1.05},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    sampling = config.sampling
    assert sampling.temperature == 0.6
    assert sampling.top_k == 20

    chat = sampling.chat_completion_kwargs()
    assert chat["temperature"] == 0.6
    assert chat["top_p"] == 0.95
    assert chat["presence_penalty"] == 0.0
    # top_k/min_p are not OpenAI fields -> forwarded via extra_body alongside passthrough.
    # The default effort is none, which also tells the chat template not to think.
    assert chat["extra_body"] == {
        "repetition_penalty": 1.05,
        "top_k": 20,
        "min_p": 0.0,
        "repeat_penalty": 1.05,
        "chat_template_kwargs": {"enable_thinking": False},
    }

    responses = sampling.responses_kwargs()
    assert "presence_penalty" not in responses
    assert responses["extra_body"]["top_k"] == 20


def test_sampling_omits_unset_fields(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "workspace": str(tmp_path),
                "sampling": {
                    "temperature": None,
                    "top_p": None,
                    "presence_penalty": None,
                    "frequency_penalty": None,
                    "top_k": None,
                    "min_p": None,
                    "repeat_penalty": None,
                    "reasoning_effort": None,
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.sampling.chat_completion_kwargs() == {}


def test_sampling_reasoning_controls_for_responses(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "responses",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "workspace": str(tmp_path),
                "sampling": {"reasoning_effort": "high", "reasoning_summary": "auto"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(explicit_path=config_path)
    assert config.sampling.responses_kwargs()["reasoning"] == {
        "effort": "high",
        "summary": "auto",
    }


def test_sampling_rejects_invalid_reasoning_effort(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "workspace": str(tmp_path),
                "sampling": {"reasoning_effort": "turbo"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(explicit_path=config_path)


def test_config_init_writes_placeholder_api_key_treated_as_unset(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    # The file never carries a blank api_key — just a generic placeholder...
    assert saved["api_key"] == PLACEHOLDER_API_KEY
    # ...which is treated as "unset" once loaded, so it never reaches a provider.
    config = load_config(explicit_path=config_path)
    assert config.api_key == ""


def test_persisting_change_keeps_placeholder_when_key_unset(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)

    config = load_config(explicit_path=config_path)
    persist_config_updates(config, {"model": "other-model"}, explicit_path=config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["model"] == "other-model"
    assert saved["api_key"] == PLACEHOLDER_API_KEY


def test_a_blocked_config_write_does_not_destroy_the_config(tmp_path, monkeypatch) -> None:
    """The write used to truncate first, so a host that blocked it left nothing.

    ``Path.write_text`` opens the file for truncation before it writes a byte.
    On a machine where an encryption or DLP agent can refuse the write halfway,
    that turned "could not save your change" into "you no longer have a
    configuration". Going through the atomic write means the file the user had
    survives a write that cannot happen.
    """

    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)
    config = load_config(explicit_path=config_path)
    before = config_path.read_text(encoding="utf-8")

    def held(*args, **kwargs):
        exc = OSError(errno.EACCES, "The process cannot access the file")
        exc.winerror = 32
        return (_ for _ in ()).throw(exc)

    monkeypatch.setattr(fileio.tempfile, "mkstemp", held)
    monkeypatch.setattr(fileio, "_rewrite_in_place", held)

    with pytest.raises(FileOperationError):
        persist_config_updates(config, {"model": "other-model"}, explicit_path=config_path)

    assert config_path.read_text(encoding="utf-8") == before
    assert json.loads(before)["model"] == config.model


def test_a_config_read_waits_out_whatever_is_holding_the_file(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)
    real = Path.read_bytes
    calls = {"n": 0}

    def guarded(self):
        calls["n"] += 1
        if calls["n"] < 3:
            exc = OSError(errno.EACCES, "held")
            exc.winerror = 32
            raise exc
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", guarded)

    assert load_config(explicit_path=config_path).workspace == tmp_path
    assert calls["n"] == 3


def test_config_init_accepts_overrides_after_subcommand(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "--config",
            str(tmp_path / "config.json"),
            "config",
            "init",
            "--workspace",
            str(tmp_path),
            "--api-mode",
            "ollama",
            "--model",
            "local-model",
        ]
    )
    assert args.init_workspace == tmp_path
    assert args.init_api_mode == "ollama"
    assert args.init_model == "local-model"


def test_repeat_penalty_is_lifted_out_of_extra_body(tmp_path) -> None:
    # It used to be reachable only through the passthrough; a file from then
    # must keep sending it, and the Model tab's knob must show it.
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "api_mode": "completions",
                "workspace": str(tmp_path),
                "sampling": {"extra_body": {"repeat_penalty": 1.3, "other": 1}},
            }
        ),
        encoding="utf-8",
    )
    sampling = load_config(explicit_path=config_path).sampling

    assert sampling.repeat_penalty == 1.3
    assert sampling.extra_body == {"other": 1}
    assert sampling.chat_completion_kwargs()["extra_body"]["repeat_penalty"] == 1.3
    assert sampling.responses_kwargs()["extra_body"]["repeat_penalty"] == 1.3
    assert sampling.ollama_options()["repeat_penalty"] == 1.3


def test_the_defaults_do_not_think_and_keep_a_cool_sampler() -> None:
    from code_ai.config.models import SamplingConfig

    sampling = SamplingConfig.from_mapping({})

    assert sampling.reasoning_effort == "none"
    assert sampling.temperature == 0.6
    assert sampling.frequency_penalty == 0.0
    assert sampling.repeat_penalty == 1.05
    assert sampling.ollama_options()["repeat_penalty"] == 1.05


@pytest.mark.parametrize(
    ("configured", "light"),
    [
        (None, None),
        ("xhigh", "low"),
        ("high", "low"),
        ("medium", "low"),
        ("low", "low"),
        ("minimal", "minimal"),
        ("none", "none"),
    ],
)
def test_light_reasoning_effort_only_ever_lowers(configured, light) -> None:
    assert SamplingConfig(reasoning_effort=configured).light_reasoning_effort() == light


@pytest.mark.parametrize(
    ("main", "followup", "expected"),
    [
        ("xhigh", "low", "low"),
        ("low", "high", "low"),
        ("xhigh", None, None),
        (None, "low", None),
    ],
)
def test_followup_effort_never_exceeds_the_main_one(main, followup, expected) -> None:
    sampling = SamplingConfig(reasoning_effort=main, followup_reasoning_effort=followup)
    assert sampling.followup_effort() == expected


def test_effort_none_switches_thinking_off_on_the_wire() -> None:
    kwargs = SamplingConfig(reasoning_effort="none").chat_completion_kwargs()

    assert kwargs["reasoning_effort"] == "none"
    # Qwen-style templates think unless told not to, whatever the effort says.
    assert kwargs["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    high = SamplingConfig(reasoning_effort="high").chat_completion_kwargs()
    assert "chat_template_kwargs" not in high.get("extra_body", {})


def test_the_user_s_own_template_kwargs_win() -> None:
    mine = {"enable_thinking": True, "custom": 1}
    sampling = SamplingConfig(reasoning_effort="none", extra_body={"chat_template_kwargs": mine})

    sent = sampling.chat_completion_kwargs()["extra_body"]["chat_template_kwargs"]

    assert sent == {"enable_thinking": True, "custom": 1}
    assert mine == {"enable_thinking": True, "custom": 1}


def test_a_step_at_effort_none_does_not_think() -> None:
    sampling = SamplingConfig(reasoning_effort="high", followup_reasoning_effort="none")

    kwargs = sampling.chat_completion_kwargs(sampling.followup_effort())

    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # A session that sends no effort is left alone by a request's own.
    assert SamplingConfig().chat_completion_kwargs("none") == {}
    assert not SamplingConfig().thinking_off("none")


def test_followup_reasoning_effort_is_validated(tmp_path) -> None:
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping(
            {"workspace": str(tmp_path), "sampling": {"followup_reasoning_effort": "turbo"}}
        )


@pytest.mark.parametrize(
    ("base_url", "model", "local"),
    [
        ("http://localhost:11434/v1", "qwen3:32b", True),
        ("http://127.0.0.1:1234/v1", r"C:\models\qwen3.gguf", True),
        ("http://localhost:11434/v1", "gemma4:cloud", False),
        ("http://localhost:11434/v1", "gemma4:31b-cloud", False),
        ("https://api.openai.com/v1", "gpt-5", False),
    ],
)
def test_serves_model_locally_excludes_ollama_cloud_models(base_url, model, local) -> None:
    assert AppConfig(base_url=base_url, model=model).serves_model_locally() is local


def test_completions_is_the_default_api_mode(tmp_path) -> None:
    assert AppConfig.from_mapping({}).api_mode == "completions"
    assert load_config(explicit_path=tmp_path / "missing.json").api_mode == "completions"


def _version_1_file(path: Path) -> None:
    # The shape every save wrote before version 2: the defaults of the day,
    # frozen, around the few things the user actually set.
    path.write_text(
        json.dumps(
            {
                "api_key": PLACEHOLDER_API_KEY,
                "api_mode": "responses",
                "base_url": "http://gpu.local:8000/v1",
                "model": "qwen3",
                "permission_mode": "ask",
                "workspace": str(path.parent),
                "budgets": {
                    "max_context_tokens": 128000,
                    "max_model_call_s": 180,
                    "max_model_steps": 80,
                    "max_stall_rounds": 7,
                    "max_turn_seconds": 900,
                    "max_tool_wall_time_s": 900,
                    "subagent_worker_timeout_s": 300,
                },
            }
        ),
        encoding="utf-8",
    )


def test_a_config_from_before_version_2_drops_what_it_froze(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _version_1_file(config_path)

    config = load_config(explicit_path=config_path)

    assert config.api_mode == "completions"
    assert config.budgets.model_timeout() is None
    assert config.budgets.max_model_steps == 0
    assert config.budgets.turn_timeout() is None
    assert config.budgets.max_tool_wall_time_s == 3600
    assert config.budgets.subagent_worker_timeout_s == 1800
    # What the user chose stays.
    assert config.budgets.max_context_tokens == 128000
    assert config.budgets.max_stall_rounds == 7
    assert config.base_url == "http://gpu.local:8000/v1"
    assert config.model == "qwen3"

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["config_version"] == 2
    assert saved["budgets"] == {"max_context_tokens": 128000, "max_stall_rounds": 7}
    assert "api_mode" not in saved
    assert "permission_mode" not in saved
    assert saved["model"] == "qwen3"
    assert saved["api_key"] == PLACEHOLDER_API_KEY


def test_responses_chosen_after_the_migration_is_kept(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _version_1_file(config_path)
    config = load_config(explicit_path=config_path)

    persist_config_updates(config, {"api_mode": "responses"}, explicit_path=config_path)

    assert load_config(explicit_path=config_path).api_mode == "responses"
    assert load_config(explicit_path=config_path).api_mode == "responses"


def test_a_file_that_cannot_be_rewritten_is_still_migrated(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    _version_1_file(config_path)
    before = config_path.read_text(encoding="utf-8")

    def held(*args, **kwargs):
        raise PermissionError(errno.EACCES, "held by another process")

    monkeypatch.setattr(loader, "atomic_write_text", held)

    assert load_config(explicit_path=config_path).api_mode == "completions"
    assert config_path.read_text(encoding="utf-8") == before


def test_a_save_writes_only_what_differs_from_the_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_init(config_path, workspace=tmp_path)
    config = load_config(explicit_path=config_path)
    budgets = asdict(config.budgets)
    budgets["max_context_tokens"] = 64000

    # The panel saves a section whole, changed or not.
    persist_config_updates(
        config, {"budgets": budgets, "permission_mode": "ask"}, explicit_path=config_path
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["budgets"] == {"max_context_tokens": 64000}
    assert "permission_mode" not in saved
    assert saved["config_version"] == 2
    assert {"api_key", "base_url", "model", "workspace"} <= set(saved)


def test_a_first_save_does_not_write_a_key_from_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    config_path = tmp_path / "config.json"
    config = load_config(explicit_path=config_path)

    persist_config_updates(config, {"model": "other-model"}, explicit_path=config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["api_key"] == PLACEHOLDER_API_KEY
    assert saved["model"] == "other-model"
