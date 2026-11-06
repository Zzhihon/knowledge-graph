"""Compatibility surface for search behavior during the staged migration.

`search` is the local LanceDB retrieval (`kg.retrieval`); it raises `RetrievalError` instead of
returning an empty list when the index or the embedding model is unusable. `build_index` and
`print_results` still come from the legacy Weaviate module.
"""

from __future__ import annotations

from agents.query import build_index, print_results
from kg.retrieval.service import search

__all__ = ["build_index", "print_results", "search"]
