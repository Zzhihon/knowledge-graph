"""Regression tests for the Weaviate-backed vector store."""

from __future__ import annotations

import socket
from contextlib import closing
from typing import TYPE_CHECKING

import pytest

from agents.config import AgentConfig
from agents.vector_store import VectorStore, _entry_id_to_uuid

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def weaviate_store(tmp_path: Path) -> Iterator[VectorStore]:
    """Create an isolated embedded Weaviate store for integration coverage."""
    config = AgentConfig(
        model="test-model",
        embedding_model="test-embedding",
        embedding_dim=3,
        vector_db="weaviate",
        vector_db_path=str(tmp_path / "weaviate"),
        graph_db="surrealdb",
        graph_db_path=str(tmp_path / "surrealdb"),
        confidence_threshold=0.7,
        search_alpha=0.5,
        weaviate_port=_free_port(),
        weaviate_grpc_port=_free_port(),
    )

    store = VectorStore(str(tmp_path / "weaviate"), config=config)
    try:
        yield store
    finally:
        store.close()


def _entries() -> list[dict[str, object]]:
    return [
        {
            "metadata": {
                "id": "ke-go-scheduler",
                "title": "Go GMP scheduler",
                "domain": ["golang"],
                "tags": ["go", "scheduler"],
                "type": "principle",
                "depth": "intermediate",
                "status": "evergreen",
                "confidence": 0.9,
                "file_path": "go.md",
                "content_hash": "hash-go",
            },
            "content": "goroutine scheduler gmp work stealing",
        },
        {
            "metadata": {
                "id": "ke-redis-persistence",
                "title": "Redis persistence",
                "domain": ["databases"],
                "tags": ["redis", "aof"],
                "type": "principle",
                "depth": "intermediate",
                "status": "evergreen",
                "confidence": 0.8,
                "file_path": "redis.md",
                "content_hash": "hash-redis",
            },
            "content": "redis aof rdb persistence",
        },
    ]


def test_weaviate_vector_store_indexes_searches_filters_and_deletes(
    weaviate_store: VectorStore,
) -> None:
    entries = _entries()
    embeddings = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]

    weaviate_store.init_collection()

    assert weaviate_store.upsert_entries(entries, embeddings) == 2
    assert weaviate_store.get_stats()["points_count"] == 2
    assert set(weaviate_store.get_all_payloads()) == {
        "ke-go-scheduler",
        "ke-redis-persistence",
    }

    vector_results = weaviate_store.search([1.0, 0.0, 0.0], top_k=1)
    assert [result["entry_id"] for result in vector_results] == ["ke-go-scheduler"]

    hybrid_results = weaviate_store.hybrid_search(
        query="goroutine scheduler",
        query_embedding=[1.0, 0.0, 0.0],
        alpha=0.5,
        top_k=2,
    )
    assert hybrid_results[0]["entry_id"] == "ke-go-scheduler"
    assert "explain_score" in hybrid_results[0]

    filtered_results = weaviate_store.hybrid_search(
        query="persistence",
        query_embedding=[0.0, 1.0, 0.0],
        alpha=0.5,
        top_k=2,
        filters={"domain": "databases"},
    )
    assert [result["entry_id"] for result in filtered_results] == [
        "ke-redis-persistence"
    ]

    assert weaviate_store.delete_points(["ke-go-scheduler"]) == 1
    assert "ke-go-scheduler" not in weaviate_store.get_all_payloads()


def test_weaviate_vector_store_validates_embedding_count() -> None:
    store = object.__new__(VectorStore)

    with pytest.raises(ValueError, match="条目数量"):
        store.upsert_entries(_entries(), [[1.0, 0.0, 0.0]])


def test_entry_id_to_uuid_is_stable() -> None:
    entry_id = "ke-go-scheduler"

    assert _entry_id_to_uuid(entry_id) == _entry_id_to_uuid(entry_id)
    assert _entry_id_to_uuid(entry_id) != _entry_id_to_uuid("ke-redis-persistence")
