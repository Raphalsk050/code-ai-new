"""The board on disk: one JSON file per workspace, beside its saved conversations."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from code_ai.core.board.models import Board
from code_ai.util.fileio import atomic_write_bytes

logger = logging.getLogger(__name__)


def load_board(path: Path | None) -> Board:
    """The saved board, or a fresh one with the three default lanes."""

    if path is None or not path.is_file():
        return Board.empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A board that cannot be read is set aside rather than overwritten.
        logger.warning("The board at %s could not be read (%s); starting a new one.", path, exc)
        _set_aside(path)
        return Board.empty()
    if not isinstance(raw, dict):
        _set_aside(path)
        return Board.empty()
    return Board.from_dict(raw)


def save_board(path: Path | None, board: Board) -> None:
    if path is None:
        return
    text = json.dumps(board.to_dict(), ensure_ascii=False, indent=1)
    atomic_write_bytes(path, text.encode("utf-8"), create_parents=True)


def _set_aside(path: Path) -> None:
    try:
        path.replace(path.with_name(path.name + ".unreadable"))
    except OSError:
        pass
