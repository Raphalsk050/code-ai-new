"""Files attached to a board's tasks: kept beside the board, swept when let go, handed to agents."""

from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

from code_ai.app import board as board_module
from code_ai.app.board import BoardService
from code_ai.app.board_runner import _attached_images
from code_ai.bridge.server import BridgeServer
from code_ai.core.board import prompts
from code_ai.events.bus import AsyncEventBus

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def service(tmp_path) -> BoardService:
    return BoardService(
        path=tmp_path / "project" / "board.json",
        event_bus=AsyncEventBus(),
        runtime=lambda: None,
        max_parallel=lambda: 2,
    )


async def test_files_are_kept_with_the_task_and_swept_when_let_go(tmp_path) -> None:
    board = service(tmp_path)
    await board.apply([{"op": "add_task", "title": "Login", "subtasks": ["Form"]}])
    spec = tmp_path / "spec.md"
    spec.write_text("# Login\nEmail and password.", encoding="utf-8")

    from_disk = await board.attach("T1", source=str(spec))
    assert from_disk["ok"], from_disk
    pasted = await board.attach("T1", name="mockup.png", data=PNG)
    assert pasted["ok"], pasted

    task = board.board.task("T1")
    assert [item.name for item in task.attachments] == ["spec.md", "mockup.png"]
    assert task.attachments[1].mime == "image/png" and task.attachments[1].size == len(PNG)
    kept = [
        tmp_path / "project" / "board-files" / "T1" / f"{item.id}-{item.name}"
        for item in task.attachments
    ]
    assert [path.read_bytes() for path in kept] == [spec.read_bytes(), PNG]
    # The user's file stays where it was: the board keeps a copy.
    assert spec.is_file()

    # The prompt says where they are; the picture also goes as an image.
    prompt = prompts.worker_prompt(task, task.subtasks[0])
    assert str(kept[0]) in prompt and "mockup.png (image/png" in prompt
    images = _attached_images(task)
    assert len(images) == 1 and images[0].media_type == "image/png"
    assert base64.b64decode(images[0].data) == PNG

    removed = await board.apply(
        [{"op": "remove_attachment", "task": "T1", "attachment": "spec.md"}]
    )
    assert removed["ok"]
    assert not kept[0].exists() and kept[1].exists()
    await board.apply([{"op": "remove_task", "task": "T1"}])
    assert not kept[1].exists()
    assert not (tmp_path / "project" / "board-files" / "T1").exists()


async def test_what_cannot_be_attached_says_why(tmp_path, monkeypatch) -> None:
    board = service(tmp_path)
    await board.apply([{"op": "add_task", "title": "Login"}])
    assert "No task" in (await board.attach("T9", data=b"x"))["message"]
    assert (
        "no file at" in (await board.attach("T1", source=str(tmp_path / "missing.txt")))["message"]
    )
    monkeypatch.setattr(board_module, "MAX_ATTACHMENT_BYTES", 4)
    assert (
        "MB an attachment may be"
        in (await board.attach("T1", name="big.bin", data=b"12345"))["message"]
    )
    board.board.task("T1").status = "running"
    assert "being worked on" in (await board.attach("T1", name="a.txt", data=b"x"))["message"]
    # A name cannot take the file out of the board's folder.
    board.board.task("T1").status = "draft"
    sneaky = await board.attach("T1", name="../../evil.txt", data=b"x")
    assert sneaky["ok"]
    path = board.board.task("T1").attachments[0].path
    assert "board-files" in path and path.endswith("evil.txt") and ".." not in path


async def test_the_bridge_takes_a_dropped_file_and_a_path(tmp_path) -> None:
    board = service(tmp_path)
    await board.apply([{"op": "add_task", "title": "Login"}])
    out = io.StringIO()
    app = SimpleNamespace(
        board=board,
        desktop_avatar=SimpleNamespace(attach=lambda app: None),
        orchestrator=SimpleNamespace(approval_gateway=None),
    )
    server = BridgeServer(app, stdin=io.StringIO(), stdout=out)

    async def call(params):
        await server._handle_line(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "boardAttach", "params": params})
        )
        return json.loads(out.getvalue().splitlines()[-1])["result"]

    dropped = await call({"task": "T1", "name": "shot.png", "data": base64.b64encode(PNG).decode()})
    assert dropped["ok"] and dropped["attachment"]["name"] == "shot.png"
    notes = tmp_path / "notes.txt"
    notes.write_text("hi", encoding="utf-8")
    picked = await call({"task": "T1", "path": str(notes)})
    assert picked["ok"] and picked["board"]["tasks"][0]["attachments"][1]["name"] == "notes.txt"
    broken = await call({"task": "T1", "name": "x.png", "data": "not base64!"})
    assert broken["ok"] is False and "whole" in broken["message"]
