"""BM25 keyword retriever with CJK bigram tokenization.

Provides a zero-dependency BM25 implementation for hybrid search.
Chinese text is tokenized using character bigrams (same approach as
Elasticsearch CJKBigramFilter); English text uses whitespace splitting.
"""

from __future__ import annotations

import math
import re
from typing import Any

# Regex: fenced code blocks (``` ... ```)
_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")

# Regex: CJK Unified Ideographs range
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")

# Regex: ASCII word characters
_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokenize mixed CJK/English text.

    - Strips fenced code blocks before tokenizing.
    - ASCII segments: lowercased whole words.
    - CJK segments: character bigrams (single chars kept as unigrams).

    Args:
        text: Input text to tokenize.

    Returns:
        List of tokens.
    """
    # Remove fenced code blocks
    text = _CODE_BLOCK_RE.sub(" ", text)

    tokens: list[str] = []

    for ascii_match in _ASCII_WORD_RE.finditer(text):
        tokens.append(ascii_match.group().lower())

    for cjk_match in _CJK_RE.finditer(text):
        chars = cjk_match.group()
        if len(chars) == 1:
            tokens.append(chars)
        else:
            for i in range(len(chars) - 1):
                tokens.append(chars[i : i + 2])

    return tokens


class BM25Retriever:
    """In-memory BM25 (Okapi) retriever.

    Standard BM25 scoring: IDF(q) * tf*(k1+1) / (tf + k1*(1-b+b*dl/avgdl))
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._doc_ids: list[str] = []
        self._doc_lens: list[int] = []
        self._avgdl: float = 0.0
        self._doc_freqs: dict[str, int] = {}  # term -> num docs containing term
        self._tf: list[dict[str, int]] = []  # per-doc term frequencies
        self._n: int = 0

    def build(self, entries: list[dict[str, Any]]) -> None:
        """Build the BM25 index from vault entries.

        Uses the same text construction as ``build_index()`` in query.py:
        ``f"{title}\\n{tags}\\n\\n{content}"``.

        Args:
            entries: List of entry dicts with 'metadata' and 'content' keys.
        """
        self._doc_ids = []
        self._doc_lens = []
        self._doc_freqs = {}
        self._tf = []

        for entry in entries:
            meta = entry.get("metadata", {})
            entry_id = meta.get("id", "")
            if not entry_id:
                continue

            title = meta.get("title", "")
            tags = meta.get("tags", [])
            tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
            content = entry.get("content", "")
            doc_text = f"{title}\n{tags_str}\n\n{content}"

            tokens = tokenize(doc_text)
            self._doc_ids.append(entry_id)
            self._doc_lens.append(len(tokens))

            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            self._tf.append(tf)

            for term in tf:
                self._doc_freqs[term] = self._doc_freqs.get(term, 0) + 1

        self._n = len(self._doc_ids)
        self._avgdl = (
            sum(self._doc_lens) / self._n if self._n > 0 else 0.0
        )

    def query(self, query_text: str, top_k: int = 10) -> list[dict[str, float]]:
        """Score documents against a query using BM25.

        Args:
            query_text: Raw query string.
            top_k: Maximum number of results.

        Returns:
            List of ``{"entry_id": str, "score": float}`` sorted by
            descending score. Scores are raw BM25 values (not normalized).
        """
        if self._n == 0:
            return []

        q_tokens = tokenize(query_text)
        if not q_tokens:
            return []

        scores: list[float] = [0.0] * self._n
        k1 = self._k1
        b = self._b

        for term in q_tokens:
            df = self._doc_freqs.get(term, 0)
            if df == 0:
                continue
            idf = math.log((self._n - df + 0.5) / (df + 0.5) + 1.0)

            for i in range(self._n):
                tf = self._tf[i].get(term, 0)
                if tf == 0:
                    continue
                dl = self._doc_lens[i]
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * dl / self._avgdl)
                scores[i] += idf * numerator / denominator

        # Collect non-zero scores, sort descending, take top_k
        results: list[dict[str, float]] = []
        for i, score in enumerate(scores):
            if score > 0:
                results.append({"entry_id": self._doc_ids[i], "score": score})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
