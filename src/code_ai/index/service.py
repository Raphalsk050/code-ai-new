"""The code index: what gets indexed, when, and how a query is answered.

The service owns one :class:`IndexStore` per workspace and exposes three
operations. ``refresh`` walks the tree (or a subtree) and brings the index in
line with the files on disk, incrementally: an unchanged file costs one
``stat``. ``touch`` does the same for a single path and is what keeps the
index following the agent - every file a tool reads, writes or edits is
re-indexed in the background. ``search`` ranks chunks lexically (BM25) and,
when an embedding model is configured, semantically, and fuses the two.

Indexing runs off the event loop and under one lock, so a refresh triggered
from the UI and a touch triggered by a tool never interleave on the store.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hashlib
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from code_ai.config.models import IndexConfig
from code_ai.core.errors import ProviderError
from code_ai.events.models import EventEnvelope
from code_ai.index.chunking import Chunk, chunk_file
from code_ai.index.embeddings import EmbeddingClient
from code_ai.index.store import ChunkRow, IndexStore
from code_ai.tools.filesystem.list_files import DEFAULT_EXCLUDES

logger = logging.getLogger(__name__)

# Files a tool touches through these names are (re)indexed in the background.
TOUCHING_TOOLS = frozenset({"read_file", "write_file", "edit_code"})

_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".svgz",
        ".psd",
        ".mp3",
        ".wav",
        ".ogg",
        ".flac",
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        ".zip",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".tar",
        ".jar",
        ".whl",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".a",
        ".lib",
        ".o",
        ".obj",
        ".pdb",
        ".pyc",
        ".pyo",
        ".class",
        ".wasm",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".lock",
        ".min.js",
        ".min.css",
        ".map",
    }
)
# Directory names that are build metadata rather than source, on top of the
# shared DEFAULT_EXCLUDES (which list_files and search_code also honour).
_EXCLUDED_DIR_SUFFIXES = (".egg-info", ".dist-info")
_RRF_K = 60.0


@dataclass(slots=True)
class RefreshReport:
    full: bool
    scanned: int = 0
    indexed: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0
    chunks: int = 0
    embedded: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        parts = [
            f"{self.indexed} file(s) indexed",
            f"{self.unchanged} unchanged",
            f"{self.removed} removed",
        ]
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.embedded:
            parts.append(f"{self.embedded} chunk(s) embedded")
        text = ", ".join(parts) + f" in {self.duration_s:.1f}s"
        if self.errors:
            text += f"; {len(self.errors)} error(s): {self.errors[0]}"
        return text


@dataclass(slots=True)
class IndexStatus:
    enabled: bool
    index_path: str
    files: int = 0
    chunks: int = 0
    embedding_model: str = ""
    embedded_chunks: int = 0
    lexical_engine: str = "fts5"
    last_refresh: str | None = None
    last_refresh_full: bool | None = None
    last_error: str | None = None
    pending_touches: int = 0
    refreshing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def empty(self) -> bool:
        return self.chunks == 0

    @property
    def semantic_ready(self) -> bool:
        return bool(self.embedding_model) and self.embedded_chunks > 0


@dataclass(slots=True)
class SearchHit:
    path: str
    start_line: int
    end_line: int
    symbol: str
    kind: str
    score: float
    snippet: str
    sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodeIndexService:
    def __init__(
        self,
        *,
        workspace: Path,
        config: IndexConfig,
        store: IndexStore,
        embedder: EmbeddingClient | None = None,
        event_bus: Any = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.config = config
        self.store = store
        self.embedder = embedder
        self.event_bus = event_bus
        self._subscription: Any = None
        self._lock = asyncio.Lock()
        self._pending: set[str] = set()
        self._drain_task: asyncio.Task[None] | None = None
        self._refreshing = False
        self._last_error: str | None = None
        self._closed = False
        self._adopt_embedder(embedder)

    def _adopt_embedder(self, embedder: EmbeddingClient | None) -> None:
        # Vectors are only comparable within one model, so a model that differs
        # from the one the store was built with invalidates every stored vector.
        self.embedder = embedder
        if embedder is None:
            return
        previous = self.store.get_meta("embedding_model")
        if previous and previous != embedder.model:
            self.store.drop_embeddings()
        self.store.set_meta("embedding_model", embedder.model)

    async def set_embedder(self, embedder: EmbeddingClient | None) -> int:
        """Swap the embedding client live; returns how many chunks now need one.

        This is what makes a model chosen in the setup dialog take effect
        without a restart. The previous client is closed, vectors computed with
        a different model are dropped, and the caller decides whether to embed
        the outstanding chunks now (``embed_pending``) or leave it to the next
        refresh.
        """

        previous = self.embedder
        async with self._lock:
            self._adopt_embedder(embedder)
            self._last_error = None
        if previous is not None and previous is not embedder:
            with contextlib.suppress(Exception):
                await previous.close()
        if embedder is None:
            return 0
        _, chunks = self.store.counts()
        return chunks - self.store.embedded_count(embedder.model)

    async def embed_pending(self, *, cancel_event: asyncio.Event | None = None) -> RefreshReport:
        """Embed every indexed chunk that has no vector for the current model."""

        report = RefreshReport(full=False)
        if self.embedder is None:
            return report
        started = time.monotonic()
        async with self._lock:
            report.embedded = await self._embed_missing(report, cancel_event)
        report.duration_s = time.monotonic() - started
        await self._emit("index.embedded", report.to_dict())
        return report

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def status(self) -> IndexStatus:
        files, chunks = self.store.counts()
        model = self.embedder.model if self.embedder else ""
        last = self.store.get_meta("last_refresh")
        last_full = self.store.get_meta("last_refresh_full")
        return IndexStatus(
            enabled=self.config.enabled,
            index_path=str(self.store.path),
            files=files,
            chunks=chunks,
            embedding_model=model,
            embedded_chunks=self.store.embedded_count(model) if model else 0,
            lexical_engine="fts5" if self.store.fts_available else "scan",
            last_refresh=last,
            last_refresh_full=None if last_full is None else last_full == "1",
            last_error=self._last_error,
            pending_touches=len(self._pending),
            refreshing=self._refreshing,
        )

    # ------------------------------------------------------------------ #
    # Indexing
    # ------------------------------------------------------------------ #
    async def refresh(
        self,
        *,
        full: bool = False,
        subtree: str = "",
        cancel_event: asyncio.Event | None = None,
    ) -> RefreshReport:
        """Bring the index in line with the workspace (or ``subtree``).

        ``full`` drops everything first and re-reads every file; otherwise a
        file whose size and mtime match the stored record is trusted
        unchanged, and files that vanished from disk are dropped.
        """

        async with self._lock:
            self._refreshing = True
            try:
                report = await asyncio.to_thread(self._refresh_sync, full, subtree, cancel_event)
                if self.embedder is not None and not (cancel_event and cancel_event.is_set()):
                    report.embedded = await self._embed_missing(report, cancel_event)
                self.store.set_meta("last_refresh", datetime.now(UTC).isoformat(timespec="seconds"))
                self.store.set_meta("last_refresh_full", "1" if full else "0")
            finally:
                self._refreshing = False
        await self._emit(
            "index.refreshed",
            {"full": full, "subtree": subtree, **report.to_dict()},
        )
        return report

    def _refresh_sync(
        self, full: bool, subtree: str, cancel_event: asyncio.Event | None
    ) -> RefreshReport:
        started = time.monotonic()
        report = RefreshReport(full=full)
        root = self.workspace / subtree if subtree else self.workspace
        if full and not subtree:
            self.store.clear()
        known = self.store.files()
        seen: set[str] = set()
        for path in self._walk(root):
            if cancel_event is not None and cancel_event.is_set():
                report.errors.append("cancelled")
                break
            relative = self._relative(path)
            if relative is None:
                continue
            seen.add(relative)
            report.scanned += 1
            try:
                outcome = self._index_path(path, relative, known.get(relative), force=full)
            except OSError as exc:
                report.errors.append(f"{relative}: {exc.strerror or exc}")
                continue
            if outcome is None:
                report.skipped += 1
            elif outcome == 0:
                report.unchanged += 1
            else:
                report.indexed += 1
                report.chunks += outcome
        prefix = f"{subtree.strip('/')}/" if subtree else ""
        for relative in known:
            if relative in seen:
                continue
            if prefix and not relative.startswith(prefix):
                continue
            if self.store.remove_file(relative):
                report.removed += 1
        report.duration_s = time.monotonic() - started
        return report

    def _index_path(self, path: Path, relative: str, record: Any, *, force: bool) -> int | None:
        """Index one file. ``None`` = skipped, 0 = unchanged, n = chunks written."""

        stat = path.stat()
        if stat.st_size > self.config.max_file_bytes:
            self.store.remove_file(relative)
            return None
        if (
            not force
            and record is not None
            and record.mtime_ns == stat.st_mtime_ns
            and record.size == stat.st_size
        ):
            return 0
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            self.store.remove_file(relative)
            return None
        digest = hashlib.sha256(data).hexdigest()
        if not force and record is not None and record.sha256 == digest:
            self.store.touch_file(relative, mtime_ns=stat.st_mtime_ns, size=stat.st_size)
            return 0
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        chunks = chunk_file(
            relative,
            text,
            window_lines=self.config.chunk_lines,
            overlap_lines=self.config.chunk_overlap_lines,
        )
        return self.store.replace_file(
            relative,
            sha256=digest,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            chunks=chunks,
        )

    async def _embed_missing(
        self, report: RefreshReport, cancel_event: asyncio.Event | None
    ) -> int:
        assert self.embedder is not None
        model = self.embedder.model
        batch_size = max(1, self.config.embedding_batch_size)
        embedded = 0
        while True:
            if cancel_event is not None and cancel_event.is_set():
                break
            batch = self.store.chunks_without_embedding(model, limit=batch_size)
            if not batch:
                break
            try:
                vectors = await self.embedder.embed([text for _, text in batch])
            except ProviderError as exc:
                self._last_error = str(exc)
                report.errors.append(f"embeddings: {exc}")
                logger.warning("code index: embeddings unavailable: %s", exc)
                break
            self.store.store_embeddings(
                model,
                [(chunk_id, vector) for (chunk_id, _), vector in zip(batch, vectors, strict=True)],
            )
            embedded += len(batch)
            self._last_error = None
        return embedded

    async def touch(self, relative_path: str) -> None:
        """Queue one workspace file for (re)indexing in the background."""

        if self._closed:
            return
        normalized = relative_path.replace("\\", "/").strip("/")
        if not normalized:
            return
        self._pending.add(normalized)
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain())

    async def touch_now(self, relative_path: str) -> int | None:
        """Index one file right away; returns the chunk count (``None`` skipped)."""

        normalized = relative_path.replace("\\", "/").strip("/")
        async with self._lock:
            return await asyncio.to_thread(self._touch_sync, normalized)

    def _touch_sync(self, relative: str) -> int | None:
        path = self.workspace / relative
        if not path.is_file() or not self._allowed(path, relative):
            self.store.remove_file(relative)
            return None
        try:
            outcome = self._index_path(
                path, relative, self.store.file_record(relative), force=False
            )
        except OSError as exc:
            self._last_error = f"{relative}: {exc.strerror or exc}"
            return None
        return outcome

    async def _drain(self) -> None:
        try:
            while self._pending and not self._closed:
                async with self._lock:
                    batch = sorted(self._pending)
                    self._pending.clear()
                    changed = 0
                    for relative in batch:
                        outcome = await asyncio.to_thread(self._touch_sync, relative)
                        if outcome:
                            changed += outcome
                    if changed and self.embedder is not None:
                        await self._embed_missing(RefreshReport(full=False), None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background indexing must not crash
            self._last_error = str(exc)
            logger.warning("code index: background indexing failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        path_prefix: str = "",
        semantic: bool = True,
        snippet_chars: int = 1200,
    ) -> list[SearchHit]:
        limit = max(1, limit)
        prefix = path_prefix.replace("\\", "/").strip("/").rstrip(".")
        candidates = max(limit * 3, 20)
        lexical = await asyncio.to_thread(
            self.store.lexical_search, query, limit=candidates, path_prefix=prefix
        )
        vector: list[tuple[int, float]] = []
        if (
            semantic
            and self.embedder is not None
            and self.store.embedded_count(self.embedder.model)
        ):
            try:
                [query_vector] = await self.embedder.embed([query])
                vector = await asyncio.to_thread(
                    self.store.vector_search,
                    self.embedder.model,
                    query_vector,
                    limit=candidates,
                    path_prefix=prefix,
                )
            except ProviderError as exc:
                self._last_error = str(exc)
                logger.warning("code index: semantic search unavailable: %s", exc)
        fused = _fuse(lexical, vector)
        paths = await asyncio.to_thread(self.store.chunk_paths, list(fused))
        weight = _path_weights(query, paths)
        ordered = sorted(
            fused.items(),
            key=lambda item: (-item[1][0] * weight(item[0]), item[0]),
        )[:limit]
        rows = self.store.chunk_rows([chunk_id for chunk_id, _ in ordered])
        hits: list[SearchHit] = []
        for chunk_id, (score, sources) in ordered:
            row = rows.get(chunk_id)
            if row is None:
                continue
            hits.append(_hit(row, score, sources, snippet_chars))
        return hits

    # ------------------------------------------------------------------ #
    # Event wiring
    # ------------------------------------------------------------------ #
    def attach(self, event_bus: Any) -> None:
        """Follow the agent: index whatever its file tools touch."""

        self.event_bus = event_bus
        if self.config.auto_index_touched_files:
            # The bus unsubscribes by identity, and ``self._on_event`` builds a
            # fresh bound method on every attribute access - so detaching later
            # only works if the object that was subscribed is the one handed
            # back, rather than an equal one made on the spot.
            self._subscription = event_bus.subscribe(self._on_event)

    async def _on_event(self, event: EventEnvelope) -> None:
        if event.event_type != "tool.call.completed":
            return
        payload = event.payload or {}
        if str(payload.get("name") or "") not in TOUCHING_TOOLS:
            return
        result = payload.get("result")
        if not isinstance(result, dict):
            return
        location = result.get("location")
        if location not in (None, "workspace"):
            return
        path = str(result.get("path") or "")
        if path:
            await self.touch(path)

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_bus is None:
            return
        with contextlib.suppress(Exception):
            await self.event_bus.emit(event_type, payload, source="index")

    async def close(self) -> None:
        self._closed = True
        # Stop following the agent first. The bus outlives the index when the
        # session is retargeted at another project, and a closed index left
        # subscribed would keep writing the new project's files into the old
        # project's database.
        if self.event_bus is not None and self._subscription is not None:
            with contextlib.suppress(Exception):
                self.event_bus.unsubscribe(self._subscription)
            self._subscription = None
        self.event_bus = None
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
            with contextlib.suppress(BaseException):
                await self._drain_task
        if self.embedder is not None:
            with contextlib.suppress(Exception):
                await self.embedder.close()
        self.store.close()

    # ------------------------------------------------------------------ #
    # Walking
    # ------------------------------------------------------------------ #
    def _relative(self, path: Path) -> str | None:
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return None

    def _allowed(self, path: Path, relative: str) -> bool:
        parts = relative.split("/")
        if any(part.startswith(".") for part in parts):
            return False
        if any(part in DEFAULT_EXCLUDES for part in parts[:-1]):
            return False
        if any(part.endswith(_EXCLUDED_DIR_SUFFIXES) for part in parts[:-1]):
            return False
        name = parts[-1].lower()
        if any(name.endswith(suffix) for suffix in _BINARY_SUFFIXES):
            return False
        if self.config.exclude_globs and any(
            fnmatch.fnmatch(relative, pattern) for pattern in self.config.exclude_globs
        ):
            return False
        if self.config.include_globs and not any(
            fnmatch.fnmatch(relative, pattern) for pattern in self.config.include_globs
        ):
            return False
        return not path.is_symlink()

    def _walk(self, root: Path) -> Iterable[Path]:
        if root.is_file():
            relative = self._relative(root)
            if relative is not None and self._allowed(root, relative):
                yield root
            return
        if not root.is_dir():
            return
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and d not in DEFAULT_EXCLUDES
                and not d.endswith(_EXCLUDED_DIR_SUFFIXES)
                and not (current_path / d).is_symlink()
            )
            for filename in sorted(files):
                path = current_path / filename
                relative = self._relative(path)
                if relative is not None and self._allowed(path, relative):
                    yield path


def _fuse(
    lexical: list[tuple[int, float]], vector: list[tuple[int, float]]
) -> dict[int, tuple[float, list[str]]]:
    """Reciprocal rank fusion: rank position matters, raw score scales do not."""

    fused: dict[int, tuple[float, list[str]]] = {}
    for source, ranked in (("lexical", lexical), ("semantic", vector)):
        for position, (chunk_id, _) in enumerate(ranked):
            contribution = 1.0 / (_RRF_K + position + 1)
            score, sources = fused.get(chunk_id, (0.0, []))
            fused[chunk_id] = (score + contribution, [*sources, source])
    return fused


# Paths whose chunks answer "how is this tested" but rarely "how does this
# work". A test asserting a behaviour repeats its vocabulary more densely than
# the implementation does, so on a plain BM25 ranking the test file wins and the
# code the user asked about is pushed off the page.
_SECONDARY_MARKERS = ("test", "spec", "fixture", "mock", "example", "sample", "benchmark")
_SECONDARY_PENALTY = 0.55


def _path_weights(query: str, paths: dict[int, str]):
    """A per-chunk rank multiplier that keeps tests from burying the source.

    Skipped entirely when the query itself is about tests: someone asking
    "where is the retry policy tested" wants exactly the files this demotes.
    """

    lowered = query.lower()
    if any(marker in lowered for marker in _SECONDARY_MARKERS):
        return lambda chunk_id: 1.0
    penalised = {
        chunk_id
        for chunk_id, path in paths.items()
        if any(marker in path.lower() for marker in _SECONDARY_MARKERS)
    }
    return lambda chunk_id: _SECONDARY_PENALTY if chunk_id in penalised else 1.0


def _hit(row: ChunkRow, score: float, sources: list[str], snippet_chars: int) -> SearchHit:
    text = row.text
    if len(text) > snippet_chars:
        text = text[:snippet_chars].rstrip() + "\n...[truncated]"
    return SearchHit(
        path=row.path,
        start_line=row.start_line,
        end_line=row.end_line,
        symbol=row.symbol,
        kind=row.kind,
        score=round(score, 4),
        snippet=text,
        sources=sources,
    )


__all__ = [
    "Chunk",
    "CodeIndexService",
    "IndexStatus",
    "RefreshReport",
    "SearchHit",
    "TOUCHING_TOOLS",
]
