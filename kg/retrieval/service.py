"""Search entry point for `kg ask`, `kg query` and the web API.

Keeps one `Searcher` per (mode, rerank) per process and reopens it when the index manifest
changes (after `kg index update` in another process), so the API server loads models once.
Errors are raised as `RetrievalError`; callers turn them into exit codes or API error bodies.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from kg.retrieval.search import Result, Searcher

if TYPE_CHECKING:
    from pathlib import Path

# Best on evaluation set v1 (35 labelled questions): entry-level BM25 + vector, RRF, then the
# local cross-encoder. Override with KG_SEARCH_MODE=bm25|vector|hybrid, KG_SEARCH_RERANK=0|1.
DEFAULT_MODE = os.environ.get("KG_SEARCH_MODE", "hybrid")
DEFAULT_RERANK = os.environ.get("KG_SEARCH_RERANK", "1") == "1"

_lock = threading.Lock()
_searchers: dict[tuple[str, str, bool], tuple[int, Searcher]] = {}


def _vault() -> Path:
    from kg.infrastructure.config.loader import load_config

    return load_config().vault_path


def get_searcher(mode: str | None = None, rerank: bool | None = None,
                 vault: Path | None = None) -> Searcher:
    from kg.retrieval.index import Index

    vault = vault or _vault()
    mode = mode or DEFAULT_MODE
    rerank = DEFAULT_RERANK if rerank is None else rerank
    manifest = Index(vault).manifest_path
    stamp = manifest.stat().st_mtime_ns if manifest.exists() else 0
    key = (str(vault), mode, rerank)
    with _lock:
        cached = _searchers.get(key)
        if cached and cached[0] == stamp:
            return cached[1]
        searcher = Searcher(vault, mode=mode, rerank=rerank)  # raises IndexMissingError
        _searchers[key] = (stamp, searcher)
        return searcher


def search_full(query: str, *, top_k: int = 10, filters: dict[str, str] | None = None,
                mode: str | None = None, rerank: bool | None = None) -> Result:
    return get_searcher(mode, rerank).search(query, top_k=top_k, filters=filters)


def search(query: str, filters: dict[str, str] | None = None, top_k: int = 5,
           config: Any = None, **kw: Any) -> list[dict[str, Any]]:
    """Drop-in for the old `agents.query.search`: same result dicts, but failures raise."""
    searcher = get_searcher(kw.get("mode"), kw.get("rerank"))
    result = searcher.search(query, top_k=top_k, filters=filters)
    out = []
    for h in result.hits:
        path = str(searcher.vault / h.path)  # absolute: `kg ask` reads the file for context
        out.append(h.to_dict() | {"file_path": path, "metadata": {"file_path": path}})
    return out
