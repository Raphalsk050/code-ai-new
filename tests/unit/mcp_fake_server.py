"""A small MCP server over stdio, for the client and manager tests.

Run as ``python mcp_fake_server.py [flags]``. Every request is served on its own
thread, so a slow tool call can be cancelled and a tool can ask the client
something (roots/list) while the reader keeps reading.

Flags: ``--banner`` prints a non-JSON line first, ``--exit-after-init`` exits
once initialized, ``--bare`` offers tools only, ``--many N`` adds N filler tools,
``--instructions TEXT`` sets the server's instructions.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

ARGS = sys.argv[1:]
LOCK = threading.Lock()
PENDING: dict[str, dict] = {}
PENDING_EVENTS: dict[str, threading.Event] = {}
CANCELLED: set = set()
EXTRA_TOOLS: list[dict] = []


def flag(name: str) -> bool:
    return name in ARGS


def option(name: str, default: str = "") -> str:
    if name in ARGS:
        index = ARGS.index(name)
        if index + 1 < len(ARGS):
            return ARGS[index + 1]
    return default


def send(message: dict) -> None:
    with LOCK:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def schema(**properties: str) -> dict:
    return {
        "type": "object",
        "properties": {name: {"type": kind} for name, kind in properties.items()},
    }


def tools() -> list[dict]:
    listed = [
        {
            "name": "echo",
            "description": "Echo the text back.",
            "inputSchema": {**schema(text="string"), "required": ["text"]},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "add",
            "description": "Add two numbers.",
            "inputSchema": schema(a="number", b="number"),
        },
        {
            "name": "slow",
            "description": "Take a while, reporting progress.",
            "inputSchema": schema(steps="integer"),
        },
        {"name": "fail", "description": "Report a failure.", "inputSchema": schema()},
        {"name": "crash", "description": "Exit the process.", "inputSchema": schema()},
        {
            "name": "ask_roots",
            "description": "Ask the client for its roots.",
            "inputSchema": schema(),
        },
        {"name": "grow", "description": "Add a tool and announce it.", "inputSchema": schema()},
        {"name": "picture", "description": "Return an image.", "inputSchema": schema()},
        {"name": "shout", "description": "Log a message.", "inputSchema": schema(text="string")},
        {
            "name": "ask_model",
            "description": "Ask the client's model.",
            "inputSchema": schema(prompt="string"),
        },
        {"name": "ask_user", "description": "Ask the user who they are.", "inputSchema": schema()},
    ]
    for index in range(int(option("--many", "0"))):
        listed.append(
            {"name": f"filler_{index}", "description": "Filler.", "inputSchema": schema()}
        )
    return listed + EXTRA_TOOLS


def ask_client(method: str, params: dict | None = None, timeout: float = 5.0) -> dict:
    request_id = f"srv-{time.monotonic_ns()}"
    event = threading.Event()
    PENDING_EVENTS[request_id] = event
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    send(message)
    event.wait(timeout)
    return PENDING.pop(request_id, {})


def call_tool(request_id, name: str, arguments: dict) -> dict:
    if name == "echo":
        return {"content": [{"type": "text", "text": str(arguments.get("text", ""))}]}
    if name == "add":
        total = float(arguments.get("a", 0)) + float(arguments.get("b", 0))
        return {
            "content": [{"type": "text", "text": f"{total:g}"}],
            "structuredContent": {"sum": total},
        }
    if name == "slow":
        steps = int(arguments.get("steps", 50))
        token = arguments.get("__token")
        for step in range(steps):
            if request_id in CANCELLED:
                return {"content": [{"type": "text", "text": "stopped"}]}
            if token is not None:
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": step + 1, "total": steps},
                    }
                )
            time.sleep(0.05)
        return {"content": [{"type": "text", "text": "done"}]}
    if name == "fail":
        return {"content": [{"type": "text", "text": "it broke"}], "isError": True}
    if name == "crash":
        sys.stdout.flush()
        os._exit(3)
    if name == "ask_roots":
        answer = ask_client("roots/list")
        roots = answer.get("result", {}).get("roots", [])
        return {"content": [{"type": "text", "text": json.dumps(roots)}]}
    if name == "grow":
        if not any(tool["name"] == "extra" for tool in EXTRA_TOOLS):
            EXTRA_TOOLS.append(
                {"name": "extra", "description": "Added later.", "inputSchema": schema()}
            )
        send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        return {"content": [{"type": "text", "text": "grown"}]}
    if name == "picture":
        return {"content": [{"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"}]}
    if name == "shout":
        send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "warning", "data": str(arguments.get("text", ""))},
            }
        )
        return {"content": [{"type": "text", "text": "shouted"}]}
    if name == "extra":
        return {"content": [{"type": "text", "text": "extra ran"}]}
    if name == "ask_model":
        answer = ask_client(
            "sampling/createMessage",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": arguments.get("prompt", "hi")},
                    }
                ],
                "systemPrompt": "Answer in one word.",
                "maxTokens": 50,
            },
            timeout=20,
        )
        if "error" in answer:
            return {
                "content": [{"type": "text", "text": answer["error"]["message"]}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": answer["result"]["content"]["text"]}]}
    if name == "ask_user":
        answer = ask_client(
            "elicitation/create",
            {
                "message": "Who are you?",
                "requestedSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "title": "Name"},
                        "age": {"type": "integer", "minimum": 0},
                        "plan": {"type": "string", "enum": ["free", "pro"]},
                    },
                    "required": ["name"],
                },
            },
            timeout=20,
        )
        return {"content": [{"type": "text", "text": json.dumps(answer.get("result") or answer)}]}
    raise LookupError(name)


def handle(message: dict) -> None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    try:
        if method == "initialize":
            capabilities: dict = {"tools": {"listChanged": True}, "logging": {}}
            if not flag("--bare"):
                capabilities["resources"] = {}
                capabilities["prompts"] = {}
            result: dict = {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": capabilities,
                "serverInfo": {"name": "fake", "title": "Fake Server", "version": "1.0"},
            }
            instructions = option("--instructions")
            if instructions:
                result["instructions"] = instructions
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            listed = tools()
            if params.get("cursor") == "page2":
                result = {"tools": listed[3:]}
            else:
                result = {"tools": listed[:3], "nextCursor": "page2"}
        elif method == "tools/call":
            arguments = dict(params.get("arguments") or {})
            token = (params.get("_meta") or {}).get("progressToken")
            if token is not None:
                arguments["__token"] = token
            result = call_tool(request_id, params.get("name"), arguments)
        elif method == "resources/list":
            result = {
                "resources": [
                    {"uri": "file:///notes.txt", "name": "notes", "mimeType": "text/plain"}
                ]
            }
        elif method == "resources/templates/list":
            result = {"resourceTemplates": [{"uriTemplate": "file:///{path}", "name": "files"}]}
        elif method == "resources/read":
            result = {
                "contents": [
                    {
                        "uri": params.get("uri"),
                        "mimeType": "text/plain",
                        "text": "remember the milk",
                    }
                ]
            }
        elif method == "prompts/list":
            result = {
                "prompts": [
                    {
                        "name": "review",
                        "description": "Review a file.",
                        "arguments": [{"name": "path", "required": True}],
                    }
                ]
            }
        elif method == "prompts/get":
            path = (params.get("arguments") or {}).get("path", "?")
            result = {
                "description": "Review",
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": f"Review {path} closely."}}
                ],
            }
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"{method} not found"},
                }
            )
            return
    except LookupError as exc:
        send(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"no {exc}"}}
        )
        return
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def main() -> None:
    if flag("--banner"):
        send_line = "fake server starting up"
        with LOCK:
            sys.stdout.write(send_line + "\n")
            sys.stdout.flush()
    sys.stderr.write("fake server ready\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if "method" not in message:
            key = str(message.get("id"))
            PENDING[key] = message
            event = PENDING_EVENTS.pop(key, None)
            if event is not None:
                event.set()
            continue
        if message["method"] == "notifications/cancelled":
            CANCELLED.add((message.get("params") or {}).get("requestId"))
            continue
        if message["method"] == "notifications/initialized":
            if flag("--exit-after-init"):
                sys.stderr.write("giving up on purpose\n")
                sys.stderr.flush()
                os._exit(2)
            continue
        if "id" not in message:
            continue
        threading.Thread(target=handle, args=(message,), daemon=True).start()


if __name__ == "__main__":
    main()
