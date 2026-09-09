"""Code index: chunked, searchable copy of the workspace for fast retrieval."""

from __future__ import annotations

from pathlib import Path

from code_ai.config.models import AppConfig
from code_ai.index.chunking import Chunk, chunk_file
from code_ai.index.embeddings import EmbeddingClient, HttpEmbeddingClient, build_embedding_client
from code_ai.index.service import CodeIndexService, IndexStatus, RefreshReport, SearchHit
from code_ai.index.store import IndexStore


def build_code_index(
    config: AppConfig,
    *,
    index_dir: Path | None = None,
    embedder: EmbeddingClient | None = None,
) -> CodeIndexService | None:
    """The workspace's index service, or ``None`` when the index is disabled.

    Best effort like the sandbox: a config dir that cannot be written leaves
    the agent without an index rather than without a session.
    """

    if not config.index.enabled:
        return None
    directory = index_dir or config.index.resolved_index_dir(config.workspace)
    try:
        store = IndexStore(directory / "code.sqlite")
    except OSError:
        return None
    return CodeIndexService(
        workspace=config.workspace,
        config=config.index,
        store=store,
        embedder=embedder if embedder is not None else build_embedding_client(config),
    )


__all__ = [
    "Chunk",
    "CodeIndexService",
    "EmbeddingClient",
    "HttpEmbeddingClient",
    "IndexStatus",
    "IndexStore",
    "RefreshReport",
    "SearchHit",
    "build_code_index",
    "build_embedding_client",
    "chunk_file",
]
