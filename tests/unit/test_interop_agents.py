"""Reading what Claude Code, Codex, OpenCode and the shared conventions leave on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_ai.core.rules import RulesService
from code_ai.core.workflows import WorkflowService, render_workflow_invocation
from code_ai.interop import (
    external_rule_sources,
    mcp_declarations,
    paths,
    skill_sources,
    subagent_profiles,
    workflow_sources,
)
from code_ai.mcp.config import load_servers
from code_ai.tools.base import ToolCapability
from code_ai.tools.skills.common import (
    SKILLS_DIR_ENV,
    discover_skills_from,
    load_skill_from,
    render_skill_invocation,
    render_skills_catalog,
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    target = tmp_path / "home"
    target.mkdir()
    monkeypatch.setenv(paths.HOME_ENV, str(target))
    monkeypatch.setenv(SKILLS_DIR_ENV, str(tmp_path / "own-skills"))
    monkeypatch.setenv("CODE_AI_WORKFLOWS_DIR", str(tmp_path / "own-workflows"))
    return target


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rules(tmp_path: Path, workspace: Path) -> RulesService:
    return RulesService(
        global_dir=tmp_path / "missing-global",
        project_dir=tmp_path / "missing-project",
        extra_sources=external_rule_sources(workspace),
    )


# --------------------------------------------------------------------------- #
# Instruction files
# --------------------------------------------------------------------------- #


def test_agents_md_is_read_from_the_repository_root_down(tmp_path, home, repo) -> None:
    workspace = repo / "packages" / "web"
    _write(repo / "AGENTS.md", "Root rule.")
    _write(workspace / "AGENTS.md", "Package rule.")

    records = _rules(tmp_path, workspace).load()

    assert [(record.name, record.body, record.origin) for record in records] == [
        ("AGENTS.md", "Root rule.", "agents"),
        ("packages/web/AGENTS.md", "Package rule.", "agents"),
    ]
    assert all(record.authoritative for record in records)


def test_nothing_above_the_repository_is_read(tmp_path, home, repo) -> None:
    _write(repo.parent / "AGENTS.md", "Not this project's.")
    _write(repo / "AGENTS.md", "This project's.")

    assert [record.body for record in _rules(tmp_path, repo).load()] == ["This project's."]


def test_the_override_replaces_agents_md_in_its_directory(tmp_path, home, repo) -> None:
    _write(repo / "AGENTS.md", "Normal.")
    _write(repo / "AGENTS.override.md", "Temporary.")

    assert [record.body for record in _rules(tmp_path, repo).load()] == ["Temporary."]


def test_codex_fallback_names_count_where_there_is_no_agents_md(tmp_path, home, repo) -> None:
    _write(home / ".codex" / "config.toml", 'project_doc_fallback_filenames = ["TEAM.md"]\n')
    _write(repo / "TEAM.md", "Team guide.")

    assert [record.body for record in _rules(tmp_path, repo).load()] == ["Team guide."]


def test_claude_files_come_before_agents_md_and_the_local_one_last(
    tmp_path, home, repo
) -> None:
    _write(repo / "AGENTS.md", "Shared.")
    _write(repo / "CLAUDE.md", "Claude.")
    _write(repo / "CLAUDE.local.md", "Mine.")
    _write(repo / ".claude" / "CLAUDE.md", "Nested.")

    records = _rules(tmp_path, repo).load()

    assert [record.body for record in records] == ["Claude.", "Nested.", "Mine.", "Shared."]
    assert records[1].name == ".claude/CLAUDE.md"


def test_an_imported_agents_md_is_inlined_once(tmp_path, home, repo) -> None:
    _write(repo / "AGENTS.md", "Use tabs.")
    _write(repo / "CLAUDE.md", "@AGENTS.md\n\nClaude only: be brief.")

    records = _rules(tmp_path, repo).load()

    assert len(records) == 1
    assert records[0].body == "Use tabs.\n\nClaude only: be brief."


def test_imports_inside_code_and_mentions_are_left_alone(tmp_path, home, repo) -> None:
    _write(repo / "docs" / "git.md", "Rebase, never merge.")
    _write(
        repo / "CLAUDE.md",
        "See @docs/git.md for git.\nKeep `@docs/git.md` literal.\nAsk @someone.\n"
        "```\n@docs/git.md\n```",
    )

    body = _rules(tmp_path, repo).load()[0].body

    assert body.startswith("See @docs/git.md for git.\nRebase, never merge.\n")
    assert "Keep `@docs/git.md` literal." in body
    assert "Ask @someone." in body
    assert "```\n@docs/git.md\n```" in body


def test_a_repository_cannot_import_files_from_outside_it(tmp_path, home, repo) -> None:
    secret = _write(tmp_path / "secret.txt", "TOKEN=abc")
    _write(repo / "CLAUDE.md", f"@{secret}\n@../secret.txt\nRules.")

    body = _rules(tmp_path, repo).load()[0].body

    assert "TOKEN" not in body


def test_the_users_own_files_may_import_from_anywhere(tmp_path, home, repo) -> None:
    shared = _write(tmp_path / "elsewhere" / "style.md", "House style.")
    _write(home / ".claude" / "CLAUDE.md", f"@{shared}")

    records = _rules(tmp_path, repo).load()

    assert [(record.name, record.scope, record.body) for record in records] == [
        ("~/.claude/CLAUDE.md", "global", "House style.")
    ]


def test_html_comments_are_dropped_from_claude_files(tmp_path, home, repo) -> None:
    _write(repo / "CLAUDE.md", "<!-- maintainers only -->\nKeep this.\n```\n<!-- code -->\n```")

    body = _rules(tmp_path, repo).load()[0].body

    assert "maintainers" not in body
    assert "<!-- code -->" in body


def test_global_instruction_files_of_every_agent(tmp_path, home, repo) -> None:
    _write(home / ".claude" / "CLAUDE.md", "Claude global.")
    _write(home / ".codex" / "AGENTS.md", "Codex global.")
    _write(home / ".config" / "opencode" / "AGENTS.md", "OpenCode global.")

    records = _rules(tmp_path, repo).load()

    assert [(record.body, record.scope, record.origin) for record in records] == [
        ("Claude global.", "global", "claude-code"),
        ("Codex global.", "global", "codex"),
        ("OpenCode global.", "global", "opencode"),
    ]


def test_the_same_text_in_two_files_is_read_once(tmp_path, home, repo) -> None:
    _write(repo / "AGENTS.md", "Same words.\n")
    _write(repo / "CLAUDE.md", "Same   words.")

    assert len(_rules(tmp_path, repo).load()) == 1


def test_opencode_instructions_field_is_honoured(tmp_path, home, repo) -> None:
    _write(repo / "docs" / "guide.md", "Guide.")
    _write(repo / "CONTRIBUTING.md", "Contributing.")
    _write(
        repo / "opencode.jsonc",
        '{\n  // comments are allowed\n  "instructions": ["CONTRIBUTING.md", "docs/*.md",'
        ' "https://example.com/x.md", "../outside.md"],\n}',
    )
    _write(repo.parent / "outside.md", "Outside.")

    bodies = [record.body for record in _rules(tmp_path, repo).load()]

    assert bodies == ["Contributing.", "Guide."]


def test_claude_rules_directory_with_path_scoped_rules(tmp_path, home, repo) -> None:
    _write(
        repo / ".claude" / "rules" / "api" / "validation.md",
        '---\npaths:\n  - "src/api/**/*.ts"\n---\nValidate every input.',
    )
    _write(home / ".claude" / "rules" / "prefs.md", "Be terse.")

    service = _rules(tmp_path, repo)
    records = service.load()
    rendered = service.render_for_prompt()

    assert [(record.body, record.scope, record.authoritative) for record in records] == [
        ("Be terse.", "global", False),
        ("Validate every input.", "project", False),
    ]
    assert records[1].paths == ("src/api/**/*.ts",)
    assert "Applies only to files matching `src/api/**/*.ts`" in rendered


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("relative", "origin"),
    [
        (".claude/skills", "claude-code"),
        (".opencode/skills", "opencode"),
        (".opencode/skill", "opencode"),
        (".agents/skills", "agents"),
    ],
)
def test_project_skill_directories(tmp_path, home, repo, relative, origin) -> None:
    _write(
        repo / relative / "triage" / "SKILL.md",
        "---\nname: triage\ndescription: Triage a bug.\n---\nAsk for repro steps.",
    )

    record = load_skill_from("triage", sources=skill_sources(repo))

    assert (record.name, record.origin) == ("triage", origin)


@pytest.mark.parametrize(
    ("relative", "origin"),
    [
        (".claude/skills", "claude-code"),
        (".codex/skills", "codex"),
        (".agents/skills", "agents"),
        (".config/opencode/skills", "opencode"),
    ],
)
def test_personal_skill_directories(tmp_path, home, repo, relative, origin) -> None:
    _write(
        home / relative / "pdf" / "SKILL.md",
        "---\nname: pdf\ndescription: Read PDFs.\n---\nUse pypdf.",
    )

    assert [(record.name, record.origin) for record in discover_skills_from(
        skill_sources(repo)
    )] == [("pdf", origin)]


def test_claude_skill_arguments_and_directory(tmp_path, home, repo) -> None:
    skill = _write(
        repo / ".claude" / "skills" / "migrate" / "SKILL.md",
        "---\nname: migrate\ndescription: >-\n  Migrate a component\n  between languages.\n"
        "arguments: [component, from, to]\n---\n"
        "Migrate $component from $from to $to ($0). Run ${CLAUDE_SKILL_DIR}/check.sh",
    )

    record = load_skill_from("migrate", sources=skill_sources(repo))
    message = render_skill_invocation(record, 'Button "React JS" Vue')

    assert record.description == "Migrate a component between languages."
    assert "Migrate Button from React JS to Vue (Button)." in message
    assert f"{skill.parent}/check.sh" in message
    assert "Task:" not in message


def test_invocation_flags_decide_who_may_run_a_skill(tmp_path, home, repo) -> None:
    _write(
        repo / ".claude" / "skills" / "deploy" / "SKILL.md",
        "---\nname: deploy\ndescription: Ship it.\ndisable-model-invocation: true\n---\nShip.",
    )
    _write(
        repo / ".claude" / "skills" / "legacy" / "SKILL.md",
        "---\nname: legacy\ndescription: Old system.\nuser-invocable: false\n---\nNotes.",
    )

    sources = skill_sources(repo)
    catalog = render_skills_catalog(sources)
    records = {record.name: record for record in discover_skills_from(sources)}

    assert "deploy" not in catalog
    assert "legacy" in catalog
    assert records["deploy"].user_invocable
    assert not records["legacy"].user_invocable


# --------------------------------------------------------------------------- #
# Commands and prompts as workflows
# --------------------------------------------------------------------------- #


def test_claude_commands_are_namespaced_and_take_arguments(tmp_path, home, repo) -> None:
    _write(
        repo / ".claude" / "commands" / "frontend" / "component.md",
        "---\ndescription: Make a component\nargument-hint: [name]\n---\n"
        "Create the $0 component. Costs $5.",
    )

    service = WorkflowService(sources=workflow_sources(repo))
    record = service.find("/component")

    assert record is not None
    assert record.name == "frontend:component"
    assert service.find("/frontend:component") == record
    message = render_workflow_invocation(record, "Header")
    assert "Create the Header component. Costs $5." in message
    assert "Additional input" not in message


def test_codex_prompts_run_as_prompts_name(tmp_path, home, repo) -> None:
    _write(
        home / ".codex" / "prompts" / "draftpr.md",
        "---\ndescription: Draft a PR\n---\nDraft a PR for $1 touching $FILE. $$HOME stays.",
    )

    service = WorkflowService(sources=workflow_sources(repo))
    record = service.find("/prompts:draftpr")

    assert record is not None and record.origin == "codex"
    message = render_workflow_invocation(record, "feature-x FILE=app.py")
    assert "Draft a PR for feature-x touching app.py. $HOME stays." in message


def test_opencode_commands_from_files_and_config(tmp_path, home, repo) -> None:
    _write(repo / ".opencode" / "commands" / "test.md", "Run tests for $ARGUMENTS.\n!`git status`")
    _write(
        repo / "opencode.json",
        json.dumps({"command": {"review": {"template": "Review $1.", "description": "Review"}}}),
    )

    service = WorkflowService(sources=workflow_sources(repo))
    test_record, review = service.find("test"), service.find("review")

    assert test_record is not None and review is not None
    assert (review.description, review.origin) == ("Review", "opencode")
    message = render_workflow_invocation(test_record, "the parser")
    assert "Run tests for the parser." in message
    assert "run it first and use what it prints" in message
    assert "Review main.py." in render_workflow_invocation(review, "main.py")


def test_a_project_command_wins_over_a_global_one_across_agents(tmp_path, home, repo) -> None:
    _write(home / ".claude" / "commands" / "deploy.md", "Global.")
    _write(repo / ".opencode" / "commands" / "deploy.md", "Project.")

    record = WorkflowService(sources=workflow_sources(repo)).find("deploy")

    assert record is not None and record.body == "Project."


# --------------------------------------------------------------------------- #
# Sub-agents
# --------------------------------------------------------------------------- #


def test_claude_agents_become_profiles_with_mapped_tools(tmp_path, home, repo) -> None:
    _write(
        repo / ".claude" / "agents" / "review" / "reviewer.md",
        "---\nname: code-reviewer\ndescription: Reviews code.\ntools: Read, Grep, Bash(git *)\n"
        "maxTurns: 12\n---\nYou review code.",
    )
    _write(home / ".claude" / "agents" / "notes.md", "No frontmatter: documentation.")

    [profile] = subagent_profiles(repo)

    assert profile.name == "code-reviewer"
    assert profile.origin == "claude-code"
    assert profile.allowed_capabilities == {ToolCapability.LOCAL_READ, ToolCapability.PROCESS}
    assert profile.max_model_steps == 12
    assert profile.role_prompt == "You review code."


def test_an_agent_without_a_tools_list_gets_everything_a_sub_agent_may_hold(
    tmp_path, home, repo
) -> None:
    _write(
        home / ".claude" / "agents" / "helper.md",
        "---\nname: helper\ndescription: Helps.\ndisallowedTools: Write, Edit\n---\nHelp.",
    )

    [profile] = subagent_profiles(repo)

    assert ToolCapability.LOCAL_WRITE not in profile.allowed_capabilities
    assert {ToolCapability.PROCESS, ToolCapability.WEB, ToolCapability.MCP} <= (
        profile.allowed_capabilities
    )


def test_opencode_agents_honour_mode_and_permissions(tmp_path, home, repo) -> None:
    _write(
        repo / ".opencode" / "agents" / "docs.md",
        "---\ndescription: Writes docs.\nmode: subagent\ntools:\n  bash: false\n"
        "permission:\n  webfetch: deny\n---\nWrite docs.",
    )
    _write(
        repo / ".opencode" / "agents" / "build.md",
        "---\ndescription: Main.\nmode: primary\n---\nx",
    )
    _write(
        home / ".config" / "opencode" / "opencode.json",
        json.dumps({"agent": {"planner": {"description": "Plans.", "prompt": "Plan it.",
                                          "permission": {"edit": "deny", "bash": "deny"}}}}),
    )

    profiles = {profile.name: profile for profile in subagent_profiles(repo)}

    assert set(profiles) == {"docs", "planner"}
    assert profiles["docs"].allowed_capabilities == {
        ToolCapability.LOCAL_READ, ToolCapability.LOCAL_WRITE, ToolCapability.MCP,
    }
    assert profiles["planner"].read_only
    assert profiles["planner"].role_prompt == "Plan it."


def test_a_built_in_name_is_never_taken_over(tmp_path, home, repo) -> None:
    _write(
        repo / ".claude" / "agents" / "explorer.md",
        "---\nname: explorer\ndescription: Mine.\n---\nMine.",
    )

    assert subagent_profiles(repo, reserved=["explorer"]) == []


# --------------------------------------------------------------------------- #
# MCP servers
# --------------------------------------------------------------------------- #


def test_claude_user_and_project_servers(tmp_path, home, repo) -> None:
    _write(
        home / ".claude.json",
        json.dumps(
            {
                "mcpServers": {"github": {"type": "http", "url": "https://api.example.com/mcp"}},
                "projects": {
                    repo.resolve().as_posix(): {
                        "mcpServers": {"db": {"command": "npx", "args": ["-y", "db-mcp"]}},
                        "disabledMcpServers": ["github"],
                    }
                },
            }
        ),
    )

    servers = {server.name: server for server in load_servers({}, repo)}

    assert servers["db"].command == "npx" and not servers["db"].user_level
    assert servers["github"].user_level and not servers["github"].enabled
    assert servers["github"].source == "claude-code"
    assert all(server.from_workspace for server in servers.values())


def test_codex_servers_from_toml(tmp_path, home, repo) -> None:
    _write(
        home / ".codex" / "config.toml",
        '[mcp_servers.docs]\ncommand = "npx"\nargs = ["-y", "docs-mcp"]\n'
        'startup_timeout_sec = 20\n\n[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n'
        'bearer_token_env_var = "LINEAR_TOKEN"\nenabled = false\n',
    )

    servers = {server.name: server for server in load_servers({}, repo)}

    assert servers["docs"].args == ["-y", "docs-mcp"]
    assert servers["docs"].startup_timeout_s == 20
    assert servers["linear"].transport == "http"
    assert servers["linear"].headers == {"Authorization": "Bearer ${LINEAR_TOKEN}"}
    assert not servers["linear"].enabled


def test_opencode_servers_translate_their_shape(tmp_path, home, repo) -> None:
    _write(
        repo / "opencode.jsonc",
        '{"mcp": {"local": {"type": "local", "command": ["bun", "x", "srv"],'
        ' "environment": {"KEY": "{env:MY_KEY}"}},'
        ' "remote": {"type": "remote", "url": "https://x.dev/mcp", "enabled": false},}}',
    )

    servers = {server.name: server for server in load_servers({}, repo)}

    assert (servers["local"].command, servers["local"].args) == ("bun", ["x", "srv"])
    assert servers["local"].env == {"KEY": "${MY_KEY}"}
    assert not servers["local"].user_level
    assert servers["remote"].transport == "http" and not servers["remote"].enabled


def test_cline_extension_settings_are_read(tmp_path, home, repo) -> None:
    settings = (
        paths.app_data() / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
        / "settings" / "cline_mcp_settings.json"
    )
    _write(
        settings,
        json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["fs"], "disabled": True}}}),
    )

    [declaration] = mcp_declarations(repo)

    assert (declaration.name, declaration.origin, declaration.user_level) == ("fs", "cline", True)


def test_the_users_config_wins_and_user_level_approval_spans_workspaces(
    tmp_path, home, repo
) -> None:
    _write(home / ".claude.json", json.dumps({"mcpServers": {"x": {"command": "theirs"}}}))

    [mine] = load_servers({"x": {"command": "mine"}}, repo)
    [theirs] = load_servers({}, repo)

    assert mine.command == "mine" and not mine.from_workspace
    assert theirs.fingerprint(repo) == theirs.fingerprint(tmp_path / "another")


def test_a_broken_foreign_file_is_skipped(tmp_path, home, repo) -> None:
    _write(home / ".claude.json", "{not json")
    _write(home / ".codex" / "config.toml", "[[[")

    assert load_servers({}, repo) == []


# --------------------------------------------------------------------------- #
# A whole session
# --------------------------------------------------------------------------- #


class _SilentProvider:
    @property
    def capabilities(self):
        from code_ai.providers.models import ProviderCapabilities

        return ProviderCapabilities(streaming=True, tool_calling=True)

    async def stream(self, request):
        from code_ai.providers.models import ModelResponse, ProviderEvent

        yield ProviderEvent(kind="completed", response=ModelResponse(text=""))

    async def complete(self, request):
        from code_ai.providers.models import ModelResponse

        return ModelResponse(text="")

    async def close(self) -> None:
        return None


def test_a_session_starts_with_everything_another_agent_left(tmp_path, home, repo) -> None:
    from code_ai.bootstrap import build_application
    from code_ai.config.models import AppConfig

    _write(repo / "CLAUDE.md", "@AGENTS.md\nAlways answer in pt-BR.")
    _write(repo / "AGENTS.md", "Run pytest -q before finishing.")
    _write(home / ".codex" / "prompts" / "release.md", "Cut release $1.")
    _write(
        repo / ".claude" / "agents" / "reviewer.md",
        "---\nname: code-reviewer\ndescription: Reviews diffs.\ntools: Read, Grep\n---\nReview.",
    )
    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(repo), "model": "fake"}
    )

    app = build_application(config=config, provider=_SilentProvider())

    prompt = app.orchestrator.conversation.messages[0].content
    assert "# Project instructions (HIGHEST PRIORITY)" in prompt
    assert "Run pytest -q before finishing.\nAlways answer in pt-BR." in prompt
    assert "## CLAUDE.md (project, claude-code)" in prompt
    assert app.workflows.find("/prompts:release") is not None
    assert app.subagent_profiles.get("code-reviewer") is not None
    dispatch = app.orchestrator.tool_registry.get("dispatch_agent")
    assert "code-reviewer: Reviews diffs." in dispatch.description


def test_a_platform_without_a_machine_location_never_means_the_current_directory(
    monkeypatch, tmp_path
) -> None:
    from code_ai.interop import codex

    monkeypatch.delenv(paths.HOME_ENV)
    monkeypatch.setattr(paths.sys, "platform", "win32")

    assert paths.machine_file("", "/etc/x", "/etc/x") is None
    assert all(str(source.root) != "." for source in codex.skill_sources(tmp_path))
