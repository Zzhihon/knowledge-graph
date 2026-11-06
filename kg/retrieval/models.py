"""Local embedding and reranking models. Nothing here sends text off the machine.

Models are read from the local Hugging Face cache only (`local_files_only`); downloading is a
separate, explicit step (`kg index rebuild --download`), so a query never waits on the network.
Loaded models are kept per process, so the API server pays the load time once.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from kg.retrieval.errors import EmbeddingError, RerankError

if TYPE_CHECKING:
    import numpy as np

DEFAULT_EMBED_MODEL = os.environ.get("KG_EMBED_MODEL", "BAAI/bge-m3")
DEFAULT_RERANK_MODEL = os.environ.get("KG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

# Query-side instructions for models trained with one; bge-m3 uses none.
_QUERY_PROMPTS = {
    "Qwen/Qwen3-Embedding-0.6B": "Instruct: 根据问题检索能回答它的个人笔记段落\nQuery: ",
}

_lock = threading.Lock()
_cache: dict[tuple[str, str], Any] = {}


def _device() -> str:
    forced = os.environ.get("KG_DEVICE")
    if forced:
        return forced
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _load(kind: str, name: str, download: bool) -> Any:
    key = (kind, name)
    with _lock:
        if key in _cache:
            return _cache[key]
        if not download:
            # local_files_only alone still lets the hub client look up model metadata online.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        if kind == "embed":
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(name, device=_device(), local_files_only=not download)
        else:
            from sentence_transformers import CrossEncoder

            model = CrossEncoder(name, device=_device(), local_files_only=not download)
        _cache[key] = model
        return model


class Embedder:
    def __init__(
        self, name: str = DEFAULT_EMBED_MODEL, *, max_tokens: int = 1024, download: bool = False
    ) -> None:
        self.name = name
        self.max_tokens = max_tokens
        self.download = download

    def _model(self) -> Any:
        try:
            model = _load("embed", self.name, self.download)
        except Exception as exc:
            raise EmbeddingError(
                f"本地向量模型 {self.name} 加载失败：{exc}",
                hint="模型不在本机缓存时先运行 kg index rebuild --download",
            ) from exc
        model.max_seq_length = self.max_tokens
        return model

    @property
    def dim(self) -> int:
        return int(self._model().get_sentence_embedding_dimension())

    def documents(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        model = self._model()
        # Sorting by length keeps padding (and memory) down on long-tailed batches.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        try:
            vecs = model.encode(
                [texts[i] for i in order],
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingError(f"条目向量计算失败：{exc}") from exc
        out = vecs.copy()
        out[order] = vecs
        return out

    def query(self, text: str) -> np.ndarray:
        model = self._model()
        try:
            vec = model.encode(
                [_QUERY_PROMPTS.get(self.name, "") + text],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0]
        except Exception as exc:
            raise EmbeddingError(f"查询向量计算失败：{exc}") from exc
        return vec


class Reranker:
    def __init__(
        self, name: str = DEFAULT_RERANK_MODEL, *, max_tokens: int = 512, download: bool = False
    ) -> None:
        self.name = name
        self.max_tokens = max_tokens
        self.download = download

    def scores(self, query: str, passages: list[str]) -> list[float]:
        try:
            model = _load("rerank", self.name, self.download)
            model.max_length = self.max_tokens
            raw = model.predict([(query, p) for p in passages], batch_size=16,
                                show_progress_bar=False)
        except Exception as exc:
            raise RerankError(f"本地重排模型 {self.name} 失败：{exc}") from exc
        return [float(s) for s in raw]
