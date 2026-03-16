"""Embedding-based pre-filter for RSS documents.

Checks incoming articles against existing knowledge entries in Qdrant
using embedding similarity. Articles above the skip threshold are
considered duplicates and skipped, saving Claude API calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.config import ProjectConfig
from agents.sources.base import SourceDocument

logger = logging.getLogger(__name__)


@dataclass
class PrefilterResult:
    """Result of similarity check for a single document."""

    document: SourceDocument
    similarity: float  # highest cosine similarity to existing entries
    matched_title: str  # title of the closest existing entry
    matched_entry_id: str  # entry_id of the closest existing entry


def _build_query_text(doc: SourceDocument) -> str:
    """Build query text from a document for embedding.

    Uses title + first 500 chars of content to balance topic signal
    and content detail without degrading embedding quality.
    """
    content_preview = doc.content[:500] if doc.content else ""
    return f"{doc.title}\n{content_preview}"


def prefilter_documents(
    documents: list[SourceDocument],
    config: ProjectConfig,
    skip_threshold: float = 0.85,
) -> tuple[list[SourceDocument], list[PrefilterResult]]:
    """Filter documents by embedding similarity to existing entries.

    Args:
        documents: Source documents fetched from RSS.
        config: Project configuration (for vector store path).
        skip_threshold: Cosine similarity above which to skip.
            Set to 0 to disable prefiltering.

    Returns:
        Tuple of (passed documents, skipped results with similarity info).
    """
    if not documents:
        return [], []

    # Threshold 0 = disabled
    if skip_threshold <= 0:
        return documents, []

    # Late imports to avoid circular deps and heavy init on CLI load
    from agents.vector_store import get_vector_store
    from agents.embeddings import embed_texts

    # Check if vector store has any entries
    try:
        store = get_vector_store(config)
    except Exception as exc:
        logger.warning("Failed to open vector store, skipping prefilter: %s", exc)
        return documents, []

    try:
        stats = store.get_stats()
        if not stats.get("exists") or stats.get("points_count", 0) == 0:
            logger.info("Vector store empty, skipping prefilter")
            store.close()
            return documents, []
    except Exception as exc:
        logger.warning("Failed to get vector store stats: %s", exc)
        store.close()
        return documents, []

    # Batch embed all documents
    query_texts = [_build_query_text(doc) for doc in documents]
    try:
        embeddings = embed_texts(query_texts)
    except Exception as exc:
        logger.warning("Embedding failed, skipping prefilter: %s", exc)
        store.close()
        return documents, []

    # Search for top-1 similar entry per document
    passed: list[SourceDocument] = []
    skipped: list[PrefilterResult] = []

    try:
        for doc, embedding in zip(documents, embeddings):
            results = store.search(query_embedding=embedding, top_k=1)

            if not results:
                # No matches at all — new content
                passed.append(doc)
                continue

            top = results[0]
            similarity = top.get("score", 0.0)

            if similarity > skip_threshold:
                skipped.append(
                    PrefilterResult(
                        document=doc,
                        similarity=similarity,
                        matched_title=top.get("title", ""),
                        matched_entry_id=top.get("entry_id", ""),
                    )
                )
            else:
                passed.append(doc)
    finally:
        store.close()

    logger.info(
        "Prefilter: %d passed, %d skipped (threshold=%.2f)",
        len(passed),
        len(skipped),
        skip_threshold,
    )
    return passed, skipped
