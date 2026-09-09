"""Build, refresh, inspect or query the code index from the command line.

``code-ai index`` is the trigger outside a session: run it once after cloning
a project (or from a git hook) and every later session starts with a warm
index. Needs no chat provider; only the optional embedding endpoint is
contacted, and only when ``index.embedding_model`` is set.
"""

from __future__ import annotations

from argparse import Namespace

from code_ai.config.models import AppConfig
from code_ai.index import build_code_index


async def run_index_command(config: AppConfig, args: Namespace) -> int:
    service = build_code_index(config)
    if service is None:
        print("code-ai: the code index is disabled (config: index.enabled).")
        return 2
    try:
        if getattr(args, "index_status", False) or getattr(args, "status", False):
            _print_status(service)
            return 0
        query = str(getattr(args, "index_search", "") or "").strip()
        if query:
            hits = await service.search(query, limit=10, snippet_chars=400)
            if not hits:
                print(
                    "no hits"
                    if not service.status().empty
                    else "index is empty; run `code-ai index`"
                )
                return 1
            for hit in hits:
                symbol = f" {hit.symbol}" if hit.symbol else ""
                sources = ", ".join(hit.sources)
                print(f"{hit.path}:{hit.start_line}-{hit.end_line}{symbol}  [{sources}]")
                first = hit.snippet.strip().splitlines()[:3]
                for line in first:
                    print(f"    {line}")
            return 0
        subtree = str(getattr(args, "index_path", "") or "").strip().replace("\\", "/").strip("/")
        full = bool(getattr(args, "full", False))
        report = await service.refresh(full=full, subtree=subtree)
        print(("Rebuilt" if full else "Refreshed") + f" the code index: {report.summary()}")
        _print_status(service)
        return 1 if report.errors else 0
    finally:
        await service.close()


def _print_status(service) -> None:
    status = service.status()
    semantic = (
        f"{status.embedding_model} ({status.embedded_chunks}/{status.chunks} embedded)"
        if status.embedding_model
        else "off"
    )
    print(f"index: {status.index_path}")
    print(f"files: {status.files}  chunks: {status.chunks}  lexical: {status.lexical_engine}")
    print(f"semantic: {semantic}")
    print(f"last refresh: {status.last_refresh or 'never'}")
    if status.last_error:
        print(f"last error: {status.last_error}")
