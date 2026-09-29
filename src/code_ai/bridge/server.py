from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Coroutine
from typing import Any, TextIO

from code_ai import updates
from code_ai.app.commands import SessionCommands
from code_ai.app.service import CodeAIApplication
from code_ai.app.slash_catalog import catalog_payload
from code_ai.bridge.approval import WireApprovalGateway
from code_ai.events.models import EventEnvelope
from code_ai.providers.models import ImageContent
from code_ai.util.unicode import scrub_json

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 error codes we use.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32000


class BridgeServer:
    """Drives a :class:`CodeAIApplication` over a line-delimited JSON-RPC stream.

    Events emitted on the bus are forwarded to the client as ``event``
    notifications; the client drives the agent by sending JSON-RPC requests that
    map onto the application facade. One JSON object per line, both directions.
    """

    def __init__(self, app: CodeAIApplication, *, stdin: TextIO, stdout: TextIO) -> None:
        self._app = app
        self._stdin = stdin
        self._stdout = stdout
        self._gateway = WireApprovalGateway()
        self._commands = SessionCommands(app)
        self._stop = asyncio.Event()
        self._turn_tasks: set[asyncio.Task[Any]] = set()

    async def run(self) -> int:
        # Gated tool calls block on the wire gateway instead of a terminal modal.
        self._app.orchestrator.approval_gateway = self._gateway
        self._app.subscribe(self._forward_event)
        await self._app.start()
        try:
            await self._read_loop()
        finally:
            self._gateway.close()
            for task in list(self._turn_tasks):
                task.cancel()
            await self._app.close()
        return 0

    # -- event egress ------------------------------------------------------

    async def _forward_event(self, event: EventEnvelope) -> None:
        self._send({"jsonrpc": "2.0", "method": "event", "params": event.to_dict()})

    def _send(self, message: dict[str, Any]) -> None:
        # asyncio is single-threaded and every payload is written as one full
        # line, so events and responses never interleave mid-line.
        self._stdout.write(json.dumps(message, default=str) + "\n")
        self._stdout.flush()

    # -- command ingress ---------------------------------------------------

    async def _read_loop(self) -> None:
        # One bad line must never end the session: the extension does not get a
        # second chance to send anything once this loop is gone.
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            try:
                line = await loop.run_in_executor(None, self._stdin.readline)
            except (UnicodeDecodeError, ValueError):
                logger.exception("Bridge could not read a line")
                self._send_error(None, _PARSE_ERROR, "The message was not valid text.")
                continue
            if line == "":  # EOF: the client closed the pipe.
                break
            line = line.strip()
            if not line:
                continue
            try:
                await self._handle_line(line)
            except Exception:  # noqa: BLE001 - reported, and the loop goes on
                logger.exception("Bridge failed on a message")
                self._send_error(None, _PARSE_ERROR, "The bridge could not handle a message.")

    async def _handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self._send_error(None, _PARSE_ERROR, "Invalid JSON.")
            return
        if not isinstance(message, dict) or "method" not in message:
            self._send_error(message.get("id") if isinstance(message, dict) else None,
                             _INVALID_REQUEST, "Not a JSON-RPC request.")
            return

        request_id = message.get("id")
        method = str(message["method"])
        params = message.get("params") or {}
        if not isinstance(params, dict):
            self._send_error(request_id, _INVALID_REQUEST, "params must be an object.")
            return
        # A lone surrogate (JSON may escape one) would break every later request.
        params = scrub_json(params)

        handler = self._HANDLERS.get(method)
        if handler is None:
            self._send_error(request_id, _METHOD_NOT_FOUND, f"Unknown method: {method}")
            return
        # AI analyses (explain/refactor) drive a model call that can take up to
        # the model timeout (180s). Run them off the read loop so the bridge
        # stays responsive — otherwise a slow analysis would stall every other
        # request (a second selection, a cancel) behind it.
        if method in self._CONCURRENT_METHODS:
            task = asyncio.create_task(self._run_and_reply(handler, params, request_id, method))
            self._turn_tasks.add(task)
            task.add_done_callback(self._turn_tasks.discard)
            return
        try:
            result = await handler(self, params)
        except Exception as exc:  # surface handler failures as JSON-RPC errors
            logger.exception("Bridge handler %s failed", method)
            self._send_error(request_id, _INTERNAL_ERROR, str(exc) or type(exc).__name__)
            return
        if request_id is not None:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _run_and_reply(
        self, handler: Any, params: dict[str, Any], request_id: Any, method: str
    ) -> None:
        try:
            result = await handler(self, params)
        except Exception as exc:
            logger.exception("Bridge handler %s failed", method)
            self._send_error(request_id, _INTERNAL_ERROR, str(exc) or type(exc).__name__)
            return
        if request_id is not None:
            self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_error(self, request_id: Any, code: int, message: str) -> None:
        # Notifications (no id) get no error response per JSON-RPC, but a parse
        # error has no id at all, so we still surface it with a null id.
        if request_id is None and code != _PARSE_ERROR:
            return
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        )

    # -- handlers ----------------------------------------------------------

    def _start_background_turn(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a turn-producing coroutine off the read loop.

        Turns stream their progress as events; acknowledging the RPC right away
        keeps the read loop responsive (e.g. to cancel/approve) while the turn
        runs. A turn awaited inline could even deadlock: an approval prompt
        inside it blocks on the very RPC the frozen loop would never read.
        """
        task = asyncio.create_task(coro)
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)
        # Heartbeat: a reasoning model can go silent for tens of seconds with no
        # events, leaving the UI unable to tell "still working" from "died". A
        # ticking heartbeat from this process is the liveness proof. It is tied to
        # the turn's lifetime (cancelled the moment the turn ends) and kept out of
        # _turn_tasks so it never blocks shutdown or the turn's own completion.
        hb = asyncio.create_task(self._heartbeat(task))
        task.add_done_callback(lambda _: hb.cancel())

    async def _h_submit(self, params: dict[str, Any]) -> dict[str, Any]:
        text = str(params.get("text") or "")
        context = str(params.get("context") or "")
        images = _parse_images(params.get("images"))
        # Clients that say nothing keep the old behaviour: a message sent while
        # the agent works steers the running turn. The VSCode composer sends
        # false on plain Enter and true on Alt+Enter.
        steer = params.get("steer")
        self._start_background_turn(
            self._app.submit_user_message(
                text,
                context=context,
                images=images,
                steer=True if steer is None else bool(steer),
            )
        )
        return {"status": "accepted"}

    async def _heartbeat(self, turn_task: asyncio.Task[Any]) -> None:
        start = time.monotonic()
        bus = getattr(self._app, "event_bus", None)
        if bus is None:
            return
        try:
            while not turn_task.done() and not self._stop.is_set():
                await asyncio.sleep(2.0)
                if turn_task.done() or self._stop.is_set():
                    break
                await bus.emit(
                    "turn.heartbeat",
                    {"elapsed_s": round(time.monotonic() - start, 1)},
                    source="bridge",
                )
        except asyncio.CancelledError:
            pass

    async def _h_new_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = params.get("conversation_id") or params.get("id")
        await self._app.reset_conversation(
            conversation_id=str(conversation_id) if conversation_id else None
        )
        return {"status": "ok", "conversation_id": self._app.conversation_id}

    async def _h_list_conversations(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"conversations": await self._app.list_conversations()}

    async def _h_load_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(params.get("conversation_id") or params.get("id") or "")
        if params.get("adopt"):
            return await self._app.load_conversation(conversation_id, adopt=True)
        return await self._app.load_conversation(conversation_id)

    async def _h_delete_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        deleted = await self._app.delete_conversation(
            str(params.get("conversation_id") or params.get("id") or "")
        )
        return {"deleted": deleted}

    async def _h_explain_code(self, params: dict[str, Any]) -> dict[str, Any]:
        markdown = await self._app.explain_code(
            code=str(params.get("code") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
        )
        return {"markdown": markdown}

    async def _h_inline_complete(self, params: dict[str, Any]) -> dict[str, Any]:
        completion = await self._app.inline_complete(
            prefix=str(params.get("prefix") or ""),
            suffix=str(params.get("suffix") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
        )
        return {"completion": completion}

    async def _h_analyze_refactor(self, params: dict[str, Any]) -> dict[str, Any]:
        improvements = await self._app.analyze_refactor(
            code=str(params.get("code") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
        )
        return {"improvements": improvements}

    async def _h_plan_refactor(self, params: dict[str, Any]) -> dict[str, Any]:
        improvements = params.get("improvements")
        markdown = await self._app.plan_refactor(
            code=str(params.get("code") or ""),
            path=str(params.get("path") or ""),
            language=str(params.get("language") or ""),
            improvements=improvements if isinstance(improvements, list) else [],
        )
        return {"markdown": markdown}

    async def _h_get_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._app.get_settings()

    async def _h_list_models(self, params: dict[str, Any]) -> dict[str, Any]:
        models = await self._app.list_models()
        return {"models": models}

    async def _h_update_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        updates = params.get("updates")
        if not isinstance(updates, dict):
            updates = {k: v for k, v in params.items() if k != "updates"}
        return await self._app.update_settings(updates)

    async def _h_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.cancel_current_turn()
        return {"status": "ok"}

    async def _h_compact(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._app.request_context_compression()
        return {
            "compressed": result.compressed,
            "previous_tokens": result.previous_tokens,
            "active_tokens": result.active_tokens,
        }

    async def _h_set_planner_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.set_planner_mode(str(params.get("mode") or ""))
        return {"status": "ok"}

    async def _h_set_permission_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._app.set_permission_mode(str(params.get("mode") or ""))
        return {"status": "ok"}

    async def _h_resolve_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        call_id = str(params.get("call_id") or "")
        scope = str(params.get("scope") or "")
        reason = str(params.get("reason") or "")
        resolved = self._gateway.resolve_scope(call_id, scope, reason)
        return {"resolved": resolved}

    async def _h_answer_question(self, params: dict[str, Any]) -> dict[str, Any]:
        # The answer resumes the paused turn, so it runs in the background
        # exactly like a submitted message.
        self._start_background_turn(
            self._app.submit_question_answer(str(params.get("answer") or ""))
        )
        return {"status": "accepted"}

    # -- planner, goal, terminal and index --------------------------------
    #
    # Thin wrappers over SessionCommands: the behaviour is shared with the
    # terminal UI, and only the transport lives here.

    async def _h_plan_snapshot(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._commands.plan_snapshot().to_payload()

    async def _h_replan(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._commands.replan(str(params.get("reason") or ""))
        return result.to_payload()

    async def _h_start_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self._commands.start_plan()).to_payload()

    async def _h_goal_define(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._commands.goal_define(str(params.get("objective") or ""))
        return result.to_payload()

    async def _h_goal_start(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self._commands.goal_start()).to_payload()

    async def _h_goal_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self._commands.goal_stop()).to_payload()

    async def _h_goal_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._commands.goal_status().to_payload()

    async def _h_terminal_start(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._commands.terminal_start(str(params.get("command") or ""))
        return result.to_payload()

    async def _h_terminal_write(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._commands.terminal_write(str(params.get("text") or ""))
        return result.to_payload()

    async def _h_terminal_control(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._commands.terminal_control(str(params.get("key") or "c"))
        return result.to_payload()

    async def _h_terminal_kill(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self._commands.terminal_kill()).to_payload()

    async def _h_terminal_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self._commands.terminal_status()).to_payload()

    async def _h_index_refresh(self, params: dict[str, Any]) -> dict[str, Any]:
        result = await self._commands.index_refresh(
            full=bool(params.get("full")), subtree=str(params.get("subtree") or "")
        )
        return result.to_payload()

    async def _h_index_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._commands.index_status().to_payload()

    async def _h_list_slash_commands(self, params: dict[str, Any]) -> dict[str, Any]:
        assets = self._commands.list_assets().data
        return {
            "commands": catalog_payload(
                workflows=assets.get("workflows", []),
                skills=assets.get("skills", []),
                mcp_prompts=self._commands.mcp_prompts(),
            )
        }

    async def _h_list_assets(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._commands.list_assets().to_payload()

    async def _h_list_rules(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._commands.list_rules().to_payload()

    async def _h_list_agents(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._commands.list_agents().to_payload()

    async def _h_run_asset(self, params: dict[str, Any]) -> dict[str, Any]:
        token = str(params.get("token") or "")
        argument = str(params.get("argument") or "")
        if token.lstrip("/").startswith("mcp:"):
            # Asks the server for the prompt's text, so it is a request of its own.
            result = await self._commands.resolve_mcp_prompt(token, argument)
        else:
            result = self._commands.resolve_asset(token, argument)
        if not result.ok:
            return result.to_payload()
        prompt = str(result.data.get("prompt") or "")
        # A workflow or skill is a turn like any other, so it runs off the read
        # loop: awaiting it here would hold the pipe its own approvals need.
        self._start_background_turn(self._app.submit_user_message(prompt))
        return {"ok": True, "message": result.message, "kind": result.data.get("kind"),
                "name": result.data.get("name")}

    async def _h_summarize_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = params.get("conversation_id") or params.get("id")
        return await self._app.summarize_conversation(
            str(conversation_id) if conversation_id else None
        )

    # -- orchestrator graph ------------------------------------------------

    async def _h_graph_catalog(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._app.graph_catalog()

    async def _h_graph_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        graph = params.get("graph")
        if not isinstance(graph, dict):
            return {"ok": False, "issues": [{"message": "No graph was sent.", "severity": "error"}]}
        return self._app.validate_graph(graph)

    async def _h_graph_run(self, params: dict[str, Any]) -> dict[str, Any]:
        graph = params.get("graph")
        if not isinstance(graph, dict):
            return {"ok": False, "message": "No graph was sent."}
        variables = params.get("variables")
        secrets = params.get("secrets")
        return await self._app.start_graph_run(
            graph,
            variables=variables if isinstance(variables, dict) else None,
            secrets=secrets if isinstance(secrets, dict) else None,
        )

    async def _h_graph_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._app.stop_graph_run(str(params.get("run_id") or ""))

    async def _h_graph_answer(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._app.answer_graph_question(
            str(params.get("run_id") or ""),
            str(params.get("call_id") or ""),
            str(params.get("text") or ""),
        )

    async def _h_graph_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._app.graph_snapshot()

    async def _h_graph_list_models(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            models = await self._app.list_models_for_endpoint(
                base_url=str(params.get("base_url") or ""),
                api_mode=str(params.get("api_mode") or ""),
                api_key_ref=str(params.get("api_key_ref") or "session"),
            )
        except Exception as exc:  # the editor shows the reason next to the field
            return {"ok": False, "message": str(exc) or type(exc).__name__, "models": []}
        return {"ok": True, "models": models}

    # -- MCP servers -------------------------------------------------------

    def _mcp(self) -> Any:
        if self._app.mcp is None:
            raise RuntimeError("MCP is not available in this session.")
        return self._app.mcp

    async def _h_mcp_list(self, params: dict[str, Any]) -> dict[str, Any]:
        mcp = self._app.mcp
        return {
            "enabled": bool(self._app.session.config.mcp_enabled),
            # With env and header values: this is what the editor edits.
            "servers": mcp.snapshot(secrets=True) if mcp is not None else [],
        }

    async def _h_mcp_reconnect(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._mcp().reconnect(str(params.get("name") or ""))

    async def _h_mcp_approve(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._mcp().approve(str(params.get("name") or ""))

    async def _h_mcp_set_enabled(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._mcp().set_enabled(
            str(params.get("name") or ""), bool(params.get("enabled"))
        )

    async def _h_mcp_save(self, params: dict[str, Any]) -> dict[str, Any]:
        server = params.get("server")
        if not isinstance(server, dict):
            raise ValueError("No server was sent.")
        return await self._mcp().upsert(server, previous=str(params.get("previous") or ""))

    async def _h_mcp_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._mcp().remove(str(params.get("name") or ""))
        return {"ok": True}

    async def _h_mcp_sign_in(self, params: dict[str, Any]) -> dict[str, Any]:
        mcp = self._mcp()
        name = str(params.get("name") or "")
        if name not in mcp.servers:
            return {"ok": False, "message": f"There is no MCP server named {name!r}."}

        async def sign_in() -> None:
            # The page goes out as mcp.auth.url, which the extension opens; how
            # it ends travels as the server's status.
            with contextlib.suppress(Exception):
                await mcp.sign_in(name, open_browser=None)

        task = asyncio.create_task(sign_in())
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)
        return {"ok": True, "message": f"Opening the sign-in page for {name}."}

    async def _h_mcp_sign_out(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._mcp().sign_out(str(params.get("name") or ""))

    async def _h_mcp_elicitation_answer(self, params: dict[str, Any]) -> dict[str, Any]:
        broker = getattr(self._mcp(), "elicitation", None)
        if broker is None:
            return {"ok": False, "message": "Nothing in this session can take the answer."}
        problem = broker.answer(
            str(params.get("id") or ""), str(params.get("action") or ""), params.get("content")
        )
        return {"ok": not problem, "message": problem}

    async def _h_check_for_update(self, params: dict[str, Any]) -> dict[str, Any]:
        """The newest release when it is newer than this build; {} otherwise."""
        verify = self._app.session.config.ssl_verification
        release = await updates.check(verify_ssl=verify)
        return release.describe(updates.current_version()) if release else {}

    async def _h_download_update(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch the newest release's VS Code package for ``target``, e.g. win32-x64."""
        target = str(params.get("target") or "")
        verify = self._app.session.config.ssl_verification
        release = await updates.check(verify_ssl=verify)
        if release is None:
            raise ValueError("Code-AI is already up to date.")
        asset = updates.asset_for(release, "vsix", target)
        if asset is None:
            raise ValueError(f"Release v{release.version} has no VS Code package for {target}.")
        path = await updates.download(asset, verify_ssl=verify)
        return {"path": str(path), "version": release.version}

    async def _h_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        self._stop.set()
        return {"status": "closing"}

    _HANDLERS: dict[str, Any] = {
        "submitUserMessage": _h_submit,
        "newConversation": _h_new_conversation,
        "listConversations": _h_list_conversations,
        "loadConversation": _h_load_conversation,
        "deleteConversation": _h_delete_conversation,
        "getSettings": _h_get_settings,
        "listModels": _h_list_models,
        "updateSettings": _h_update_settings,
        "explainCode": _h_explain_code,
        "inlineComplete": _h_inline_complete,
        "analyzeRefactor": _h_analyze_refactor,
        "planRefactor": _h_plan_refactor,
        "cancel": _h_cancel,
        "compact": _h_compact,
        "setPlannerMode": _h_set_planner_mode,
        "setPermissionMode": _h_set_permission_mode,
        "resolveApproval": _h_resolve_approval,
        "answerQuestion": _h_answer_question,
        "planSnapshot": _h_plan_snapshot,
        "replan": _h_replan,
        "startPlan": _h_start_plan,
        "goalDefine": _h_goal_define,
        "goalStart": _h_goal_start,
        "goalStop": _h_goal_stop,
        "goalStatus": _h_goal_status,
        "terminalStart": _h_terminal_start,
        "terminalWrite": _h_terminal_write,
        "terminalControl": _h_terminal_control,
        "terminalKill": _h_terminal_kill,
        "terminalStatus": _h_terminal_status,
        "indexRefresh": _h_index_refresh,
        "indexStatus": _h_index_status,
        "listSlashCommands": _h_list_slash_commands,
        "listAssets": _h_list_assets,
        "listRules": _h_list_rules,
        "listAgents": _h_list_agents,
        "runAsset": _h_run_asset,
        "summarizeConversation": _h_summarize_conversation,
        "graphCatalog": _h_graph_catalog,
        "graphValidate": _h_graph_validate,
        "graphRun": _h_graph_run,
        "graphStop": _h_graph_stop,
        "graphAnswer": _h_graph_answer,
        "graphStatus": _h_graph_status,
        "graphListModels": _h_graph_list_models,
        "mcpList": _h_mcp_list,
        "mcpReconnect": _h_mcp_reconnect,
        "mcpApprove": _h_mcp_approve,
        "mcpSetEnabled": _h_mcp_set_enabled,
        "mcpSave": _h_mcp_save,
        "mcpRemove": _h_mcp_remove,
        "mcpElicitationAnswer": _h_mcp_elicitation_answer,
        "mcpSignIn": _h_mcp_sign_in,
        "mcpSignOut": _h_mcp_sign_out,
        "checkForUpdate": _h_check_for_update,
        "downloadUpdate": _h_download_update,
        "shutdown": _h_shutdown,
    }

    # Methods whose model call may run long; dispatched off the read loop.
    _CONCURRENT_METHODS = frozenset(
        {
            "explainCode",
            "inlineComplete",
            "analyzeRefactor",
            "planRefactor",
            "listModels",
            # A goal derives its criteria with a model call, an index refresh
            # walks the workspace, and a stop cancels the running turn: none of
            # them may hold the read loop, which is what carries approvals.
            "goalDefine",
            "goalStart",
            "goalStop",
            "replan",
            "indexRefresh",
            # Writing the history card's summary is a model call of its own.
            "summarizeConversation",
            # A graph run spawns sub-agents and a stop has to reach them;
            # neither may hold the read loop, which carries the approvals
            # those sub-agents wait on. Listing a node's models is network.
            "graphRun",
            "graphStop",
            "graphListModels",
            # An MCP prompt is fetched from its server before the turn starts.
            "runAsset",
            # Stopping a server can wait on its process for a few seconds.
            "mcpReconnect",
            "mcpApprove",
            "mcpSetEnabled",
            "mcpSave",
            "mcpRemove",
            "mcpSignOut",
            # Both talk to GitHub; a download can take minutes.
            "checkForUpdate",
            "downloadUpdate",
        }
    )


def _parse_images(raw: Any) -> list[ImageContent]:
    """Decode pasted images from the wire.

    The client sends what a clipboard gives it - base64 plus a media type - and
    anything malformed is dropped rather than failing the turn: a screenshot
    that did not survive the trip is not a reason to refuse the question it
    came with.
    """
    if not isinstance(raw, list):
        return []
    images: list[ImageContent] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = str(item.get("data") or "").strip()
        if not data:
            continue
        images.append(
            ImageContent(data=data, media_type=str(item.get("media_type") or "image/png"))
        )
    return images


async def run_bridge(app: CodeAIApplication, *, stdin: TextIO, stdout: TextIO) -> int:
    """Serve a single application over stdio JSON-RPC until the client disconnects."""
    return await BridgeServer(app, stdin=stdin, stdout=stdout).run()
