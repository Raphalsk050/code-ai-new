from __future__ import annotations

import asyncio
import io
import tarfile

import pytest

from code_ai.config.models import AppConfig
from code_ai.core.errors import ToolArgumentError, ToolExecutionError, WorkspaceBoundaryError
from code_ai.events.bus import AsyncEventBus
from code_ai.tools.base import TOOL_IMAGES_KEY, ToolContext
from code_ai.tools.office import converters
from code_ai.tools.office.common import (
    attach_images,
    display_path,
    parse_page_spec,
    resolve_input,
    resolve_output,
)
from code_ai.tools.office.libreoffice_install import _unpack_deb
from code_ai.util.paths import WorkspacePolicy


def make_context(tmp_path) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(tmp_path)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(tmp_path),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
    )


def test_page_spec_keeps_order_and_counts_from_the_end() -> None:
    assert parse_page_spec(None, 3) == [0, 1, 2]
    assert parse_page_spec("all", 2) == [0, 1]
    assert parse_page_spec("1-3,last,-2", 10) == [0, 1, 2, 9, 8]
    assert parse_page_spec("3-1", 5) == [2, 1, 0]
    assert parse_page_spec([2, "4"], 4) == [1, 3]
    assert parse_page_spec(1, 4) == [0]


def test_page_spec_rejects_out_of_range_and_garbage() -> None:
    with pytest.raises(ToolArgumentError):
        parse_page_spec("5", 4)
    with pytest.raises(ToolArgumentError):
        parse_page_spec("abc", 4)
    with pytest.raises(ToolArgumentError):
        parse_page_spec({"page": 1}, 4)


def test_paths_stay_inside_the_workspace(tmp_path) -> None:
    context = make_context(tmp_path)
    (tmp_path / "in.pdf").write_bytes(b"%PDF")
    assert resolve_input(context, "in.pdf", suffixes=(".pdf",)).name == "in.pdf"
    with pytest.raises(ToolArgumentError):
        resolve_input(context, "in.pdf", suffixes=(".docx",))
    out = resolve_output(context, "deep/dir/out.pdf")
    assert out.parent.is_dir()
    assert display_path(context, out) == "deep/dir/out.pdf"
    with pytest.raises(ToolExecutionError):
        resolve_output(context, "in.pdf", overwrite=False)
    with pytest.raises(WorkspaceBoundaryError):
        resolve_output(context, "../escape.pdf")


def test_images_travel_under_the_reserved_key() -> None:
    payload = attach_images({"path": "a.pdf"}, [b"png"])
    assert payload[TOOL_IMAGES_KEY][0]["media_type"] == "image/png"
    assert TOOL_IMAGES_KEY not in attach_images({}, [])


def _deb(files: dict[str, bytes]) -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:xz") as archive:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    members = [
        (b"debian-binary", b"2.0\n"),
        (b"control.tar.xz", b""),
        (b"data.tar.xz", data.getvalue()),
    ]
    out = io.BytesIO()
    out.write(b"!<arch>\n")
    for name, body in members:
        header = name.ljust(16) + b"0".ljust(12) + b"0".ljust(6) * 2 + b"100644".ljust(8)
        header += str(len(body)).encode().ljust(10) + b"`\n"
        out.write(header + body + (b"\n" if len(body) % 2 else b""))
    return out.getvalue()


def test_a_deb_unpacks_without_dpkg(tmp_path) -> None:
    soffice = b"#!/bin/sh\n"
    _unpack_deb(_deb({"./opt/libreoffice9.9/program/soffice": soffice}), tmp_path)
    assert (tmp_path / "opt/libreoffice9.9/program/soffice").read_bytes() == soffice


def test_a_non_deb_is_refused(tmp_path) -> None:
    with pytest.raises(ToolExecutionError):
        _unpack_deb(b"not an archive", tmp_path)


async def test_without_any_office_the_error_says_how_to_install(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(converters, "find_libreoffice", lambda: None)
    monkeypatch.setattr(converters, "ms_office_available", lambda _: False)
    source = tmp_path / "a.docx"
    source.write_bytes(b"x")
    with pytest.raises(ToolExecutionError, match="install_converter"):
        await converters.convert(source, "pdf", tmp_path / "a.pdf")
