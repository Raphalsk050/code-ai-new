from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from code_ai.bootstrap import build_application
from code_ai.cli.headless import run_headless
from code_ai.config.loader import config_init, load_config, redacted_config_json
from code_ai.core.errors import CodeAIError, ConfigurationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="code-ai")
    parser.add_argument("--config", type=Path, help="Configuration file path.")
    parser.add_argument("--workspace", type=Path, help="Workspace override.")
    parser.add_argument("--model", help="Model override.")
    parser.add_argument(
        "--api-mode", choices=["responses", "completions", "chat_completions", "ollama"]
    )
    parser.add_argument("--base-url", help="Provider base URL override.")
    parser.add_argument("--headless", action="store_true", help="Run without the Textual UI.")
    parser.add_argument(
        "--events-jsonl", action="store_true", help="Write JSON Lines events in headless mode."
    )

    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run one headless task.")
    run_parser.add_argument("task", nargs="*", help="Task text. Reads stdin when omitted.")

    subparsers.add_parser(
        "bridge",
        help="Serve the agent over stdio JSON-RPC for an embedding client (e.g. the VSCode ext).",
    )

    config_parser = subparsers.add_parser("config", help="Manage configuration.")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    init_parser = config_sub.add_parser("init", help="Create a safe example configuration.")
    init_parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing config file."
    )
    init_parser.add_argument(
        "--workspace",
        type=Path,
        dest="init_workspace",
        help="Workspace for the new config.",
    )
    init_parser.add_argument(
        "--api-mode",
        choices=["responses", "completions", "chat_completions", "ollama"],
        dest="init_api_mode",
        help="API mode for the new config.",
    )
    init_parser.add_argument(
        "--base-url", dest="init_base_url", help="Provider base URL for the new config."
    )
    init_parser.add_argument("--model", dest="init_model", help="Model for the new config.")
    show_parser = config_sub.add_parser("show", help="Show redacted effective configuration.")
    show_parser.add_argument(
        "--raw", action="store_true", help="Reserved for future machine formats."
    )
    return parser


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "workspace": str(args.workspace.resolve()) if args.workspace else None,
        "model": args.model,
        "api_mode": args.api_mode,
        "base_url": args.base_url,
        "show_ui": False if args.headless else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "config":
            if args.config_command == "init":
                path = config_init(
                    args.config,
                    force=args.force,
                    workspace=args.init_workspace or args.workspace,
                    overrides={
                        "api_mode": args.init_api_mode or args.api_mode,
                        "base_url": args.init_base_url or args.base_url,
                        "model": args.init_model or args.model,
                    },
                )
                print(f"Created configuration: {path}")
                return 0
            if args.config_command == "show":
                config = load_config(explicit_path=args.config, cli_overrides=_overrides(args))
                print(redacted_config_json(config))
                return 0

        if args.command == "run":
            task = " ".join(args.task).strip() if args.task else sys.stdin.read().strip()
            if not task:
                raise ConfigurationError("A task is required for headless run.")
            app = build_application(
                config_path=args.config, cli_overrides=_overrides(args) | {"show_ui": False}
            )
            return asyncio.run(run_headless(app, task, events_jsonl=args.events_jsonl))

        if args.command == "bridge":
            from code_ai.bridge import run_bridge

            app = build_application(
                config_path=args.config, cli_overrides=_overrides(args) | {"show_ui": False}
            )
            return asyncio.run(run_bridge(app, stdin=sys.stdin, stdout=sys.stdout))

        config = load_config(explicit_path=args.config, cli_overrides=_overrides(args))
        if args.headless or not config.show_ui:
            task = sys.stdin.read().strip()
            if not task:
                raise ConfigurationError("No task provided on stdin for headless mode.")
            app = build_application(config=config)
            return asyncio.run(run_headless(app, task, events_jsonl=args.events_jsonl))

        from code_ai.ui.terminal.app import run_terminal_ui

        return run_terminal_ui(config_path=args.config, cli_overrides=_overrides(args))
    except KeyboardInterrupt:
        return 130
    except CodeAIError as exc:
        print(f"code-ai: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
