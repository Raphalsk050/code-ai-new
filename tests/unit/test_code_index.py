from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_ai.config.models import AppConfig, IndexConfig
from code_ai.core.errors import (
    ConfigurationError,
    ProviderError,
    ToolExecutionError,
    WorkspaceBoundaryError,
)
from code_ai.events.bus import AsyncEventBus
from code_ai.index import build_code_index
from code_ai.index.chunking import chunk_file
from code_ai.index.service import CodeIndexService
from code_ai.index.store import IndexStore, _fts_query, query_terms, split_identifiers
from code_ai.tools.base import ToolContext
from code_ai.tools.search import IndexWorkspaceTool, SearchIndexTool
from code_ai.util.paths import WorkspacePolicy

PY_SOURCE = '''"""Module docstring."""

import os


def parse_tool_call(payload):
    """Turn a raw payload into a ToolCall."""
    return payload


@decorator
class RetryPolicy:
    def backoff(self, attempt):
        return attempt * 2

    def reset(self):
        pass
'''

TS_SOURCE = """import { x } from "y";

export function approveRequest(id: string) {
  return id;
}

const handleDenial = async (reason) => {
  console.log(reason);
};

class Widget {
  render() {}
}
"""


def _write(workspace: Path, relative: str, text: str) -> Path:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_service(
    workspace: Path, *, embedder=None, config: IndexConfig | None = None
) -> CodeIndexService:
    store = IndexStore(workspace.parent / f"{workspace.name}-index" / "code.sqlite")
    return CodeIndexService(
        workspace=workspace,
        config=config or IndexConfig(),
        store=store,
        embedder=embedder,
    )


def make_context(workspace: Path, index: CodeIndexService | None) -> ToolContext:
    config = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(workspace)})
    return ToolContext(
        config=config,
        workspace=WorkspacePolicy.from_path(workspace),
        event_bus=AsyncEventBus(session_id="session"),
        cancel_event=asyncio.Event(),
        code_index=index,
    )


class FakeEmbedder:
    """Deterministic embeddings: a bag of a few keywords, so cosine is testable."""

    vocabulary = ("retry", "backoff", "approve", "denial", "widget", "parse")

    def __init__(self, *, fail: bool = False) -> None:
        self.model = "fake-embed"
        self.calls: list[list[str]] = []
        self.fail = fail

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise ProviderError("embedding server down")
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            lowered = text.lower()
            vector = [float(lowered.count(word)) for word in self.vocabulary]
            vectors.append(vector if any(vector) else [0.01] * len(self.vocabulary))
        return vectors

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------- chunking


def test_python_chunks_follow_symbols_and_keep_decorators() -> None:
    chunks = chunk_file("pkg/mod.py", PY_SOURCE)
    by_symbol = {chunk.symbol: chunk for chunk in chunks if chunk.symbol}
    assert "parse_tool_call" in by_symbol
    assert by_symbol["parse_tool_call"].kind == "function"
    assert by_symbol["RetryPolicy"].text.startswith("@decorator")
    assert by_symbol["RetryPolicy"].kind == "class"
    # The import block between symbols is kept as a plain block.
    assert any(chunk.symbol == "" and "import os" in chunk.text for chunk in chunks)
    for chunk in chunks:
        assert chunk.start_line <= chunk.end_line
        assert chunk.text == "\n".join(
            PY_SOURCE.splitlines()[chunk.start_line - 1 : chunk.end_line]
        )


def test_non_python_chunks_use_declaration_regex() -> None:
    chunks = chunk_file("web/app.ts", TS_SOURCE)
    symbols = {chunk.symbol for chunk in chunks}
    assert {"approveRequest", "handleDenial", "Widget"} <= symbols


def test_unstructured_text_is_windowed_with_overlap() -> None:
    text = "\n".join(f"line {i}" for i in range(1, 151))
    chunks = chunk_file("notes.txt", text, window_lines=60, overlap_lines=10)
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 60), (51, 110), (101, 150)]
    assert chunk_file("empty.txt", "\n\n  \n") == []


def test_identifier_splitting_and_query_terms() -> None:
    assert split_identifiers("ToolCall parse_tool_call") == ["tool", "call", "parse"]
    # "where" and "is" describe the question, not the code: kept, they match
    # most of the tree and BM25 ranks by them instead of by "toolcall".
    assert query_terms("where is ToolCall approved?") == [
        "toolcall",
        "tool",
        "call",
        "approved",
    ]


def test_a_query_made_only_of_stopwords_still_searches_for_something() -> None:
    # Dropping every term would turn a bad query into an empty result set, which
    # reads as "no such code" rather than as "ask me a better question".
    assert query_terms("how does it") == ["how", "does", "it"]
    assert query_terms("how does it work") == ["work"]


def test_an_inflected_query_word_reaches_the_form_the_code_uses() -> None:
    # A prefix match only grows to the right, so "approved" alone never reaches
    # "approval"; the stem is asked for alongside it.
    assert '"approv"*' in _fts_query(query_terms("approved"))
    assert '"compress"*' in _fts_query(query_terms("compressed"))
    # Short words keep their shape rather than being cut to noise.
    assert _fts_query(["ids"]) == '"ids"'


# ---------------------------------------------------------------- service


async def test_the_source_outranks_the_test_that_exercises_it(tmp_path) -> None:
    """A test repeats a behaviour's vocabulary more densely than the code does.

    Left alone, BM25 hands the top slots to the test file and the model reads
    assertions instead of the implementation it asked for.
    """

    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    _write(
        workspace,
        "tests/test_retry.py",
        "def test_retry_backoff_retries_with_backoff():\n"
        "    # retry backoff retry backoff retry backoff\n"
        "    assert RetryPolicy().backoff(1) == 2\n",
    )
    service = make_service(workspace)
    try:
        await service.refresh()
        hits = await service.search("retry backoff")
        assert hits[0].path == "pkg/retry.py"

        # ...unless the question is about the tests, which is the one case where
        # the demoted files are the answer.
        hits = await service.search("test retry backoff")
        assert hits[0].path == "tests/test_retry.py"
    finally:
        await service.close()


async def test_one_search_cannot_spend_the_whole_output_budget(tmp_path) -> None:
    """Twenty-five hits at full snippet length would be three times the budget."""

    workspace = tmp_path / "ws"
    body = "\n".join(
        f"    # retry backoff attempt {line} of the provider call" for line in range(40)
    )
    for number in range(30):
        _write(workspace, f"pkg/mod{number}.py", f"def retry_backoff_{number}():\n{body}\n")
    service = make_service(workspace)
    try:
        await service.refresh()
        context = make_context(workspace, service)
        budget = context.config.budgets.max_tool_output_chars
        result = await SearchIndexTool().execute(
            {"query": "retry backoff", "max_results": 25}, context
        )
        assert len(json.dumps(result)) <= budget
        assert 0 < len(result["hits"]) < 25
        assert "truncated" in result

        # A modest request is answered in full, with nothing held back.
        small = await SearchIndexTool().execute(
            {"query": "retry backoff", "max_results": 3}, context
        )
        assert len(small["hits"]) == 3 and "truncated" not in small
    finally:
        await service.close()


async def test_refresh_indexes_and_search_ranks_by_symbol(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    _write(workspace, "web/app.ts", TS_SOURCE)
    _write(workspace, "node_modules/dep/index.js", "function approveRequest() {}")
    _write(workspace, ".git/config", "approveRequest")
    (workspace / "blob.bin").write_bytes(b"\x00\x01approveRequest")
    service = make_service(workspace)
    try:
        report = await service.refresh()
        assert report.indexed == 2 and report.removed == 0 and not report.errors
        status = service.status()
        assert status.files == 2 and status.chunks > 0 and not status.empty
        assert status.last_refresh is not None and status.last_refresh_full is False

        hits = await service.search("retry backoff")
        assert hits and hits[0].path == "pkg/retry.py"
        assert hits[0].symbol.startswith("RetryPolicy")
        assert hits[0].sources == ["lexical"]

        # camelCase identifiers match their split words, and prefixes match too.
        hits = await service.search("approve request")
        assert hits[0].path == "web/app.ts" and hits[0].symbol == "approveRequest"
        assert all(hit.path.startswith(("pkg/", "web/")) for hit in hits)

        # A path prefix narrows the results.
        hits = await service.search("approve", path_prefix="pkg")
        assert all(hit.path.startswith("pkg/") for hit in hits)
    finally:
        await service.close()


async def test_refresh_is_incremental_and_drops_deleted_files(tmp_path) -> None:
    workspace = tmp_path / "ws"
    retry = _write(workspace, "pkg/retry.py", PY_SOURCE)
    _write(workspace, "web/app.ts", TS_SOURCE)
    service = make_service(workspace)
    try:
        await service.refresh()
        second = await service.refresh()
        assert second.indexed == 0 and second.unchanged == 2

        retry.write_text(PY_SOURCE.replace("backoff", "cooldown"), encoding="utf-8")
        (workspace / "web/app.ts").unlink()
        third = await service.refresh()
        assert third.indexed == 1 and third.removed == 1
        assert service.status().files == 1
        assert not await service.search("approveRequest")
        assert (await service.search("cooldown"))[0].path == "pkg/retry.py"

        full = await service.refresh(full=True)
        assert full.full and full.indexed == 1
        assert service.status().last_refresh_full is True
    finally:
        await service.close()


async def test_subtree_refresh_only_touches_that_subtree(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    _write(workspace, "web/app.ts", TS_SOURCE)
    service = make_service(workspace)
    try:
        await service.refresh()
        (workspace / "pkg/retry.py").unlink()
        report = await service.refresh(subtree="web")
        # pkg/ was not walked, so its stale record survives until a full pass.
        assert report.scanned == 1 and report.removed == 0
        assert service.status().files == 2
        report = await service.refresh(subtree="pkg")
        assert report.removed == 1 and service.status().files == 1
    finally:
        await service.close()


async def test_touch_follows_tool_events(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    service = make_service(workspace)
    bus = AsyncEventBus(session_id="s")
    service.attach(bus)
    try:
        assert service.status().empty
        await bus.emit(
            "tool.call.completed",
            {"name": "read_file", "result": {"path": "pkg/retry.py", "location": "workspace"}},
        )
        # Sandbox writes are not part of the workspace and never indexed.
        await bus.emit(
            "tool.call.completed",
            {"name": "write_file", "result": {"path": "scratch.py", "location": "sandbox"}},
        )
        await bus.emit(
            "tool.call.completed",
            {"name": "execute_command", "result": {"path": "pkg/retry.py"}},
        )
        for _ in range(50):
            if service._drain_task is not None and service._drain_task.done():
                break
            await asyncio.sleep(0.02)
        assert service.status().files == 1
        assert (await service.search("RetryPolicy backoff"))[0].path == "pkg/retry.py"

        # Editing the file re-indexes it; deleting it drops it.
        (workspace / "pkg/retry.py").write_text("def brand_new():\n    pass\n", encoding="utf-8")
        assert await service.touch_now("pkg/retry.py") == 1
        assert (await service.search("brand new"))[0].symbol == "brand_new"
        (workspace / "pkg/retry.py").unlink()
        assert await service.touch_now("pkg/retry.py") is None
        assert service.status().empty
    finally:
        await service.close()


async def test_semantic_search_fuses_with_lexical_and_survives_outage(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    _write(workspace, "web/app.ts", TS_SOURCE)
    embedder = FakeEmbedder()
    service = make_service(workspace, embedder=embedder)
    try:
        report = await service.refresh()
        status = service.status()
        assert report.embedded == status.chunks > 0
        assert status.embedded_chunks == status.chunks and status.semantic_ready
        # Chunks are embedded in batches, never one request per chunk.
        assert len(embedder.calls) <= 2

        hits = await service.search("retry backoff")
        assert hits[0].path == "pkg/retry.py"
        assert set(hits[0].sources) == {"lexical", "semantic"}

        # Semantic-only hit: the query word is absent from the text but the
        # embedding space knows it, so the chunk still surfaces.
        embedder.fail = True
        hits = await service.search("widget")
        assert hits and hits[0].sources == ["lexical"]
        assert service.status().last_error == "embedding server down"
    finally:
        await service.close()


async def test_embedding_failure_keeps_lexical_index_usable(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    service = make_service(workspace, embedder=FakeEmbedder(fail=True))
    try:
        report = await service.refresh()
        assert report.indexed == 1 and report.embedded == 0
        assert report.errors and "embedding server down" in report.errors[0]
        assert (await service.search("backoff"))[0].sources == ["lexical"]
    finally:
        await service.close()


async def test_changing_embedding_model_drops_old_vectors(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    first = make_service(workspace, embedder=FakeEmbedder())
    await first.refresh()
    assert first.status().embedded_chunks > 0
    await first.close()

    other = FakeEmbedder()
    other.model = "other-model"
    second = make_service(workspace, embedder=other)
    try:
        assert second.status().embedded_chunks == 0
        await second.refresh()
        assert second.status().embedded_chunks == second.status().chunks
    finally:
        await second.close()


def test_lexical_scan_fallback_matches_fts(tmp_path) -> None:
    store = IndexStore(tmp_path / "idx" / "code.sqlite")
    try:
        chunks = chunk_file("pkg/retry.py", PY_SOURCE)
        store.replace_file("pkg/retry.py", sha256="x", mtime_ns=1, size=1, chunks=chunks)
        fts = store.lexical_search("retry backoff", limit=5)
        store.fts_available = False
        scan = store.lexical_search("retry backoff", limit=5)
        assert fts and scan and fts[0][0] == scan[0][0]
    finally:
        store.close()


async def test_set_embedder_applies_live_and_backfills(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    service = make_service(workspace)
    try:
        # Lexical only to begin with: refreshing embeds nothing.
        await service.refresh()
        assert service.status().embedding_model == ""
        chunks = service.status().chunks

        embedder = FakeEmbedder()
        pending = await service.set_embedder(embedder)
        assert pending == chunks
        assert service.status().embedding_model == "fake-embed"
        # Swapping the client alone embeds nothing; the backfill does.
        assert service.status().embedded_chunks == 0

        report = await service.embed_pending()
        assert report.embedded == chunks
        assert service.status().semantic_ready
        hits = await service.search("retry backoff")
        assert set(hits[0].sources) == {"lexical", "semantic"}

        # A different model invalidates the vectors, which are recomputed.
        other = FakeEmbedder()
        other.model = "other-embed"
        assert await service.set_embedder(other) == chunks
        assert service.status().embedded_chunks == 0
        assert (await service.embed_pending()).embedded == chunks

        # Clearing it turns semantic retrieval off and leaves lexical answering.
        assert await service.set_embedder(None) == 0
        assert service.status().embedding_model == ""
        assert not service.status().semantic_ready
        assert (await service.search("retry backoff"))[0].sources == ["lexical"]
        assert (await service.embed_pending()).embedded == 0
    finally:
        await service.close()


async def test_application_switches_embedding_model_without_restart(tmp_path, monkeypatch) -> None:
    from code_ai.app import service as app_service

    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    index = make_service(workspace)
    await index.refresh()
    chunks = index.status().chunks

    built: list[str] = []

    def fake_build(config):
        model = config.index.embedding_model
        built.append(model)
        if not model:
            return None
        embedder = FakeEmbedder()
        embedder.model = model
        return embedder

    monkeypatch.setattr(app_service, "build_embedding_client", fake_build)
    application = _application_with_index(tmp_path, index)
    try:
        pending = await application.set_embedding_model("nomic-embed-text")
        assert pending == chunks
        assert built == ["nomic-embed-text"]
        # The live config is updated too, so a later /index uses the same model.
        assert application.session.config.index.embedding_model == "nomic-embed-text"
        assert index.embedder is not None and index.embedder.model == "nomic-embed-text"

        assert application.start_embedding_backfill() is True
        # A second request while the first is running is a no-op, not a double pass.
        assert application.start_embedding_backfill() is False
        await application._embedding_task
        assert index.status().embedded_chunks == chunks

        assert await application.set_embedding_model("") == 0
        assert index.embedder is None
        assert application.start_embedding_backfill() is False
    finally:
        await application.close()


def _application_with_index(tmp_path, index):
    """A minimal application facade wired to a ready-made index service."""

    from code_ai.app.service import CodeAIApplication
    from code_ai.app.session import ApplicationSession
    from code_ai.events.bus import AsyncEventBus

    async def _drain_learning() -> None:
        return None

    config = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(index.workspace), "model": "fake-model"}
    )

    class _NullProvider:
        async def close(self) -> None:
            return None

    return CodeAIApplication(
        session=ApplicationSession(session_id="s", config=config),
        event_bus=AsyncEventBus(session_id="s"),
        orchestrator=SimpleNamespace(drain_learning=_drain_learning),
        provider=_NullProvider(),
        compressor=None,
        code_index=index,
    )


# ---------------------------------------------------------------- tools


async def test_search_index_tool_reports_empty_index_then_hits(tmp_path) -> None:
    workspace = tmp_path / "ws"
    _write(workspace, "pkg/retry.py", PY_SOURCE)
    service = make_service(workspace)
    context = make_context(workspace, service)
    try:
        result = await SearchIndexTool().execute({"query": "backoff"}, context)
        assert result["hits"] == [] and "index_workspace" in result["hint"]

        built = await IndexWorkspaceTool().execute({}, context)
        assert built["indexed"] == 1 and built["index"]["files"] == 1
        assert "1 file(s) indexed" in built["summary"]

        result = await SearchIndexTool().execute(
            {"query": "backoff", "path": "pkg", "max_results": 3}, context
        )
        assert "hint" not in result
        assert result["hits"][0]["path"] == "pkg/retry.py"
        assert result["hits"][0]["start_line"] >= 1
        assert "backoff" in result["hits"][0]["snippet"]
        assert result["index"]["semantic"] is False
    finally:
        await service.close()


async def test_index_tools_refuse_paths_outside_workspace_and_when_disabled(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = make_service(workspace)
    try:
        context = make_context(workspace, service)
        with pytest.raises(WorkspaceBoundaryError):
            await SearchIndexTool().execute({"query": "x", "path": "../"}, context)
        disabled = make_context(workspace, None)
        with pytest.raises(ToolExecutionError):
            await SearchIndexTool().execute({"query": "x"}, disabled)
        with pytest.raises(ToolExecutionError):
            await IndexWorkspaceTool().execute({}, disabled)
    finally:
        await service.close()


# ---------------------------------------------------------------- config


def test_index_config_loads_and_validates(tmp_path) -> None:
    config = AppConfig.from_mapping(
        {
            "api_mode": "ollama",
            "workspace": str(tmp_path),
            "index": {"embedding_model": "nomic-embed-text", "exclude_globs": "docs/*"},
        }
    )
    assert config.index.semantic_enabled
    assert config.index.exclude_globs == ["docs/*"]
    # Default location is patched to a temp dir by conftest; an explicit
    # index_dir is honoured verbatim.
    explicit = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(tmp_path), "index": {"index_dir": "~/x/idx"}}
    )
    assert explicit.index.resolved_index_dir(tmp_path) == Path("~/x/idx").expanduser()
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping(
            {"api_mode": "ollama", "workspace": str(tmp_path), "index": {"chunk_lines": 2}}
        )
    with pytest.raises(ConfigurationError):
        AppConfig.from_mapping(
            {
                "api_mode": "ollama",
                "workspace": str(tmp_path),
                "index": {"embedding_api_mode": "cohere"},
            }
        )


def test_build_code_index_honours_enabled_switch(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    disabled = AppConfig.from_mapping(
        {"api_mode": "ollama", "workspace": str(workspace), "index": {"enabled": False}}
    )
    assert build_code_index(disabled, index_dir=tmp_path / "idx") is None
    enabled = AppConfig.from_mapping({"api_mode": "ollama", "workspace": str(workspace)})
    service = build_code_index(enabled, index_dir=tmp_path / "idx")
    assert service is not None and service.embedder is None
    assert (tmp_path / "idx" / "code.sqlite").exists()
    service.store.close()


# ---------------------------------------------------------------- progress


def test_the_progress_bar_shows_the_share_done() -> None:
    from code_ai.ui.terminal.view_models import render_index_progress

    assert "0/8 files" in render_index_progress("indexing", 0, 8)
    half = render_index_progress("indexing", 4, 8)
    assert "50%" in half and "4/8 files" in half
    assert half.count("█") == half.count("░")
    assert "100%" in render_index_progress("indexing", 8, 8)
    # The embedding pass counts chunks, not files.
    assert "chunks" in render_index_progress("embedding", 1, 2)


def test_a_total_that_is_not_known_yet_does_not_draw_a_full_bar() -> None:
    """A bar with no total would read as finished the moment it appeared."""

    from code_ai.ui.terminal.view_models import render_index_progress

    line = render_index_progress("indexing", 12, 0)
    assert "12 done" in line and "█" not in line and "%" not in line


async def test_the_progress_bar_updates_while_the_refresh_runs(tmp_path) -> None:
    """It has to move as the walk moves, not appear finished at the end.

    It used to be a conversation line, mutated in place as progress arrived.
    The transcript is append-only - a line is mounted once and never re-drawn -
    so every update after the first was invisible and the bar sat at 0% for the
    whole refresh. It lives in its own widget for that reason.
    """

    from code_ai.ui.terminal.view_models import TerminalViewModel

    workspace = tmp_path / "ws"
    for number in range(60):
        _write(workspace, f"pkg/mod{number}.py", PY_SOURCE)
    bus = AsyncEventBus(session_id="session")
    view_model = TerminalViewModel()
    seen: list[str] = []

    async def subscriber(event) -> None:
        view_model.apply(event)
        if event.event_type == "index.progress":
            seen.append(view_model.index_progress)

    bus.subscribe(subscriber)
    service = make_service(workspace)
    service.attach(bus)
    try:
        await service.refresh(full=True)
        # More than one distinct reading, and not all of them zero: that is the
        # difference between a bar that moves and the bug this pins down.
        assert len(set(seen)) > 1, f"the bar never changed: {set(seen)}"
        assert seen[0] != seen[-1]
        assert "0/60" in seen[0] and "60/60" in seen[-1]
        # Never in the transcript, which cannot redraw it.
        assert view_model.conversation == []
        # The refresh is over, so the live bar goes away and the summary that
        # whoever asked for it appends is what remains.
        assert view_model.index_progress == ""
        assert view_model.index_progress_visible is False
    finally:
        await service.close()
