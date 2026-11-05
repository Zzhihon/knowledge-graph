"""Retrieval failures that callers must see, instead of an empty result list.

Every error carries a stable `code` so the CLI can pick an exit code, the API can return it to
the page, and the evaluation can count it as an error rather than a miss.
"""

from __future__ import annotations

from typing import Any


class RetrievalError(RuntimeError):
    code = "retrieval_failed"

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint}

    def __str__(self) -> str:
        return f"{self.message}（{self.hint}）" if self.hint else self.message


class IndexMissingError(RetrievalError):
    code = "index_missing"


class IndexStaleError(RetrievalError):
    code = "index_stale"


class EmbeddingError(RetrievalError):
    code = "embedding_failed"


class RerankError(RetrievalError):
    code = "rerank_failed"
