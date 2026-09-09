"""SQLite-backed store for the code index.

One file per workspace holds the chunk table, an FTS5 full-text index over it
for lexical (BM25) retrieval, and an optional embedding per chunk for semantic
retrieval. Everything is local: opening the store needs no server, and a
search is a single indexed query rather than a walk of the tree.

The connection is shared across threads (the service runs indexing off the
event loop) and every public method takes the store lock, so callers never
need to reason about SQLite's threading rules.
"""

from __future__ import annotations

import array
import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from code_ai.index.chunking import Chunk

SCHEMA_VERSION = 1

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(slots=True, frozen=True)
class FileRecord:
    path: str
    sha256: str
    mtime_ns: int
    size: int


@dataclass(slots=True, frozen=True)
class ChunkRow:
    id: int
    path: str
    start_line: int
    end_line: int
    symbol: str
    kind: str
    text: str


def split_identifiers(text: str) -> list[str]:
    """Words hidden inside identifiers: ``parseToolCall`` -> parse, tool, call.

    FTS5's default tokenizer already splits on ``_`` and punctuation, so
    snake_case needs no help; camelCase does. Emitted once per identifier so
    the derived column stays small.
    """

    seen: set[str] = set()
    out: list[str] = []
    for identifier in _IDENTIFIER.findall(text):
        for part in _CAMEL_BOUNDARY.split(identifier):
            for word in part.split("_"):
                lowered = word.lower()
                if len(lowered) >= 2 and lowered != identifier.lower() and lowered not in seen:
                    seen.add(lowered)
                    out.append(lowered)
    return out


# Words a natural-language question is made of rather than words the code
# contains. Left in, they dominate the query: "where does the planner go quiet"
# scores every chunk holding "where" or "does", which is most of the tree, and
# BM25 then ranks by the noise instead of by "planner". They are dropped only
# when something is left to search for, so a query that is nothing but glue
# still returns its best effort rather than nothing.
_STOPWORDS = frozenset(
    """
    a an and any are as at be been before but by can could do does doing done
    for from get gets getting had has have how if in into is it its me my no
    not of on or our out over set should so some than that the their then there
    these they this those to under until up use used uses using want was we
    what when where which while who why will with would you your
    """.split()
)


def query_terms(query: str) -> list[str]:
    """Normalise a free-text query into the terms the lexical index understands."""

    seen: set[str] = set()
    terms: list[str] = []
    for token in _TOKEN.findall(query):
        candidates = [token]
        candidates.extend(_CAMEL_BOUNDARY.split(token))
        for candidate in candidates:
            lowered = candidate.lower()
            if len(lowered) >= 2 and lowered not in seen:
                seen.add(lowered)
                terms.append(lowered)
    content = [term for term in terms if term not in _STOPWORDS]
    return content or terms


# Inflections worth cutting before a prefix match. A prefix only grows to the
# right, so "approved" never reaches "approval" and "compressed" never reaches
# "compression" - which is exactly the shape of the mismatch between how a
# question is phrased and how the code is named. Longest suffix first, so
# "compressions" loses "ions" rather than "s".
_SUFFIXES = ("ations", "ation", "ings", "ing", "ions", "ion", "ers", "er", "ed", "es", "s")
_MIN_STEM = 4


def _stem(term: str) -> str:
    """The term with one trailing inflection removed, or the term unchanged."""

    for suffix in _SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= _MIN_STEM:
            return term[: -len(suffix)]
    return term


def _fts_query(terms: list[str]) -> str:
    # Every term is quoted (so nothing is read as FTS syntax) and OR-ed:
    # BM25 already rewards chunks matching more of the terms, and an AND would
    # return nothing for a query that happens to include one word the code
    # never uses. Longer terms also match as prefixes, so "orchestr" finds
    # "orchestrator" and "orchestration" alike, and an inflected word is asked
    # for again as its stem so it reaches the other forms the code may use.
    parts: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for candidate in (term, _stem(term)):
            if candidate in seen:
                continue
            seen.add(candidate)
            escaped = candidate.replace('"', '""')
            parts.append(f'"{escaped}"*' if len(candidate) >= 4 else f'"{escaped}"')
    return " OR ".join(parts)


def _pack(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _unpack(blob: bytes) -> array.array:
    values = array.array("f")
    values.frombytes(blob)
    return values


class IndexStore:
    """Chunks, their lexical index and their embeddings, in one SQLite file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.fts_available = self._probe_fts()
        self._ensure_schema()

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _probe_fts(self) -> bool:
        try:
            self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
            self._conn.execute("DROP TABLE IF EXISTS _fts_probe")
            return True
        except sqlite3.OperationalError:
            return False

    def _ensure_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    symbol TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'block',
                    text TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
                CREATE TABLE IF NOT EXISTS embeddings (
                    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS embeddings_model ON embeddings(model);
                """
            )
            if self.fts_available:
                self._conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                        path, symbol, body,
                        content='chunks', content_rowid='id',
                        tokenize='unicode61'
                    );
                    CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                        INSERT INTO chunks_fts(rowid, path, symbol, body)
                        VALUES (new.id, new.path, new.symbol, new.body);
                    END;
                    CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                        INSERT INTO chunks_fts(chunks_fts, rowid, path, symbol, body)
                        VALUES ('delete', old.id, old.path, old.symbol, old.body);
                    END;
                    """
                )
            stored = self.get_meta("schema_version")
            if stored is None:
                self.set_meta("schema_version", str(SCHEMA_VERSION))

    # ------------------------------------------------------------------ #
    # Metadata
    # ------------------------------------------------------------------ #
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ #
    # Files and chunks
    # ------------------------------------------------------------------ #
    def files(self) -> dict[str, FileRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT path, sha256, mtime_ns, size FROM files").fetchall()
        return {
            str(row["path"]): FileRecord(
                path=str(row["path"]),
                sha256=str(row["sha256"]),
                mtime_ns=int(row["mtime_ns"]),
                size=int(row["size"]),
            )
            for row in rows
        }

    def file_record(self, path: str) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT path, sha256, mtime_ns, size FROM files WHERE path = ?", (path,)
            ).fetchone()
        if row is None:
            return None
        return FileRecord(
            path=str(row["path"]),
            sha256=str(row["sha256"]),
            mtime_ns=int(row["mtime_ns"]),
            size=int(row["size"]),
        )

    def touch_file(self, path: str, *, mtime_ns: int, size: int) -> None:
        """Record a new stat for a file whose content hash did not change."""

        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE files SET mtime_ns = ?, size = ? WHERE path = ?", (mtime_ns, size, path)
            )

    def replace_file(
        self,
        path: str,
        *,
        sha256: str,
        mtime_ns: int,
        size: int,
        chunks: list[Chunk],
    ) -> int:
        """Store ``chunks`` as the whole content of ``path``; returns the chunk count."""

        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
            self._conn.execute(
                "INSERT INTO files(path, sha256, mtime_ns, size) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, "
                "mtime_ns = excluded.mtime_ns, size = excluded.size",
                (path, sha256, mtime_ns, size),
            )
            self._conn.executemany(
                "INSERT INTO chunks(path, start_line, end_line, symbol, kind, text, body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk.path,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.symbol,
                        chunk.kind,
                        chunk.text,
                        _searchable_body(chunk),
                    )
                    for chunk in chunks
                ],
            )
        return len(chunks)

    def remove_file(self, path: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM files WHERE path = ?", (path,))
        return cursor.rowcount > 0

    def clear(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM files")
            self._conn.execute("DELETE FROM chunks")
            self._conn.execute("DELETE FROM embeddings")
            if self.fts_available:
                self._conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")

    def counts(self) -> tuple[int, int]:
        with self._lock:
            files = self._conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
            chunks = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        return int(files), int(chunks)

    def chunk_rows(self, ids: list[int]) -> dict[int, ChunkRow]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path, start_line, end_line, symbol, kind, text FROM chunks "
                f"WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {
            int(row["id"]): ChunkRow(
                id=int(row["id"]),
                path=str(row["path"]),
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                symbol=str(row["symbol"]),
                kind=str(row["kind"]),
                text=str(row["text"]),
            )
            for row in rows
        }

    def chunk_paths(self, ids: list[int]) -> dict[int, str]:
        """Just the paths of these chunks, for reranking before the text is read.

        Reranking needs to see every candidate, but the candidate list is wider
        than what is returned; pulling each chunk's ``text`` only to throw most
        of it away is the expensive part, so the rerank runs on paths alone.
        """

        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, path FROM chunks WHERE id IN ({placeholders})", ids
            ).fetchall()
        return {int(row["id"]): str(row["path"]) for row in rows}

    # ------------------------------------------------------------------ #
    # Lexical search
    # ------------------------------------------------------------------ #
    def lexical_search(
        self, query: str, *, limit: int, path_prefix: str = ""
    ) -> list[tuple[int, float]]:
        """Chunk ids ranked by BM25 relevance (best first) with their scores."""

        terms = query_terms(query)
        if not terms:
            return []
        if self.fts_available:
            return self._fts_search(terms, limit=limit, path_prefix=path_prefix)
        return self._scan_search(terms, limit=limit, path_prefix=path_prefix)

    def _fts_search(
        self, terms: list[str], *, limit: int, path_prefix: str
    ) -> list[tuple[int, float]]:
        sql = (
            "SELECT c.id AS id, bm25(chunks_fts, 1.0, 6.0, 1.0) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ?"
        )
        params: list[object] = [_fts_query(terms)]
        if path_prefix:
            sql += " AND c.path LIKE ? ESCAPE '\\'"
            params.append(_like_prefix(path_prefix))
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        # bm25() is "lower is better" and negative; flip so callers see a
        # positive relevance where bigger means better.
        return [(int(row["id"]), -float(row["rank"])) for row in rows]

    def _scan_search(
        self, terms: list[str], *, limit: int, path_prefix: str
    ) -> list[tuple[int, float]]:
        # No FTS5 in this SQLite build: score in Python. Slower, but the index
        # still answers, and it is still far cheaper than reading the tree.
        sql = "SELECT id, symbol, body FROM chunks"
        params: list[object] = []
        if path_prefix:
            sql += " WHERE path LIKE ? ESCAPE '\\'"
            params.append(_like_prefix(path_prefix))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        total = max(1, len(rows))
        document_frequency = {term: 0 for term in terms}
        prepared: list[tuple[int, str, str]] = []
        for row in rows:
            body = str(row["body"]).lower()
            symbol = str(row["symbol"]).lower()
            prepared.append((int(row["id"]), symbol, body))
            for term in terms:
                if term in body:
                    document_frequency[term] += 1
        scored: list[tuple[int, float]] = []
        for chunk_id, symbol, body in prepared:
            score = 0.0
            for term in terms:
                occurrences = body.count(term)
                if not occurrences:
                    continue
                idf = math.log(1 + total / (1 + document_frequency[term]))
                score += idf * (1 + math.log(occurrences))
                if term in symbol:
                    score += idf * 3
            if score > 0:
                scored.append((chunk_id, score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #
    def chunks_without_embedding(self, model: str, *, limit: int) -> list[tuple[int, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.id AS id, c.path AS path, c.symbol AS symbol, c.text AS text "
                "FROM chunks c LEFT JOIN embeddings e "
                "ON e.chunk_id = c.id AND e.model = ? WHERE e.chunk_id IS NULL "
                "ORDER BY c.id LIMIT ?",
                (model, limit),
            ).fetchall()
        return [
            (int(row["id"]), _embedding_text(row["path"], row["symbol"], row["text"]))
            for row in rows
        ]

    def store_embeddings(self, model: str, vectors: list[tuple[int, list[float]]]) -> None:
        if not vectors:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO embeddings(chunk_id, model, dim, vector) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chunk_id) DO UPDATE SET model = excluded.model, "
                "dim = excluded.dim, vector = excluded.vector",
                [(chunk_id, model, len(vector), _pack(vector)) for chunk_id, vector in vectors],
            )

    def drop_embeddings(self, *, except_model: str | None = None) -> None:
        with self._lock, self._conn:
            if except_model is None:
                self._conn.execute("DELETE FROM embeddings")
            else:
                self._conn.execute("DELETE FROM embeddings WHERE model <> ?", (except_model,))

    def embedded_count(self, model: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM embeddings WHERE model = ?", (model,)
            ).fetchone()
        return int(row["n"])

    def vector_search(
        self,
        model: str,
        query_vector: list[float],
        *,
        limit: int,
        path_prefix: str = "",
    ) -> list[tuple[int, float]]:
        """Chunk ids ranked by cosine similarity to ``query_vector``."""

        sql = "SELECT e.chunk_id AS id, e.vector AS vector FROM embeddings e"
        params: list[object] = []
        if path_prefix:
            sql += " JOIN chunks c ON c.id = e.chunk_id"
        sql += " WHERE e.model = ?"
        params.append(model)
        if path_prefix:
            sql += " AND c.path LIKE ? ESCAPE '\\'"
            params.append(_like_prefix(path_prefix))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        query = array.array("f", query_vector)
        query_norm = math.sqrt(sum(value * value for value in query))
        if query_norm == 0:
            return []
        scored: list[tuple[int, float]] = []
        for row in rows:
            vector = _unpack(row["vector"])
            if len(vector) != len(query):
                continue
            score = _cosine(query, vector, query_norm)
            if score > 0:
                scored.append((int(row["id"]), score))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def dump_meta(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM meta").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}


def _cosine(query: array.array, vector: array.array, query_norm: float) -> float:
    try:
        import numpy  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - numpy is optional
        dot = 0.0
        norm = 0.0
        for a, b in zip(query, vector, strict=True):
            dot += a * b
            norm += b * b
        return dot / (query_norm * math.sqrt(norm)) if norm else 0.0
    a = numpy.frombuffer(query.tobytes(), dtype=numpy.float32)
    b = numpy.frombuffer(vector.tobytes(), dtype=numpy.float32)
    norm = float(numpy.linalg.norm(b))
    return float(a.dot(b)) / (query_norm * norm) if norm else 0.0


def _like_prefix(prefix: str) -> str:
    normalized = prefix.strip("/")
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}/%" if normalized else "%"


def _searchable_body(chunk: Chunk) -> str:
    # The chunk text plus the words hidden in its camelCase identifiers, so a
    # query for "tool call" finds ``ToolCall`` as well as ``tool_call``. The
    # path's own words are appended too: file names are strong signals.
    extras = split_identifiers(chunk.text)
    path_words = " ".join(_TOKEN.findall(chunk.path.replace("/", " ")))
    return f"{chunk.text}\n{' '.join(extras)}\n{path_words}"


def _embedding_text(path: str, symbol: str, text: str) -> str:
    header = f"# {path}" + (f" :: {symbol}" if symbol else "")
    return f"{header}\n{text}"
