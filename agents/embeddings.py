"""Embedding provider for the knowledge graph.

Supports two backends:
  1. Gemini API (default) — gemini-embedding-2-preview, 3072/768 dims
  2. Local sentence-transformers (fallback) — all-MiniLM-L6-v2, 384 dims

Backend selection is controlled by config.yaml agent.embedding_model.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Gemini embedding config ──────────────────────────────────────────────────
GEMINI_EMBEDDING_MODEL = "gemini-embedding-2-preview"
GEMINI_EMBEDDING_DIM = 3072  # 代理不支持 output_dimensionality 降维，使用全量维度
GEMINI_TASK_TYPE = "SEMANTIC_SIMILARITY"

# ── Local model config ───────────────────────────────────────────────────────
LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
LOCAL_EMBEDDING_DIM = 384

# Model is already cached locally after first download.
# Skip network checks to avoid HuggingFace timeout in China mainland.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

_local_model = None
_use_gemini: bool | None = None
EMBEDDING_DIM: int = GEMINI_EMBEDDING_DIM  # updated at init


def _init_backend() -> None:
    """Initialise embedding backend based on config."""
    global _use_gemini, EMBEDDING_DIM

    if _use_gemini is not None:
        return  # already initialised

    try:
        from agents.config import load_config
        config = load_config()
        model_name = config.agent.embedding_model
        api_keys = config.agent.api_keys
        base_url = config.agent.base_url
    except Exception:
        model_name = LOCAL_EMBEDDING_MODEL
        api_keys = []
        base_url = ""

    # Use Gemini if:
    # 1. embedding_model is set to gemini-embedding-* in config
    # 2. OR api_keys are configured (implies Gemini API is available)
    if "gemini" in model_name.lower() or api_keys:
        # Find the embedding-dedicated key (weight=0, gemini model)
        api_key = None
        if api_keys:
            # 优先找 embedding 专用 key (gemini model)
            for kc in api_keys:
                if "gemini" in kc.model.lower() or "embedding" in kc.description.lower():
                    api_key = kc.key
                    break
            # 没有专用 key 就用第一个
            if not api_key:
                api_key = api_keys[0].key

        if api_key and base_url:
            _use_gemini = True
            EMBEDDING_DIM = GEMINI_EMBEDDING_DIM
            logger.info(f"Embedding backend: Gemini ({GEMINI_EMBEDDING_MODEL}, {EMBEDDING_DIM}d)")
            return

    # Fallback to local model
    _use_gemini = False
    EMBEDDING_DIM = LOCAL_EMBEDDING_DIM
    logger.info(f"Embedding backend: Local ({LOCAL_EMBEDDING_MODEL}, {EMBEDDING_DIM}d)")


def _get_local_model():
    """Lazily initialise the sentence-transformers model (heavy import)."""
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer
        _local_model = SentenceTransformer(LOCAL_EMBEDDING_MODEL)
    return _local_model


def _embed_gemini(texts: list[str]) -> list[list[float]]:
    """Generate embeddings using Gemini API."""
    import httpx
    from agents.config import load_config

    config = load_config()
    base_url = config.agent.base_url

    # 专门找 embedding key（description 含 embedding 或 model 含 gemini-embedding）
    api_key = None
    for key_config in config.agent.api_keys:
        if "embedding" in key_config.model.lower() or "embedding" in key_config.description.lower():
            api_key = key_config.key
            break
    if api_key is None:
        api_key = config.agent.api_keys[0].key  # fallback to first key

    client = httpx.Client(
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30.0,
    )

    results = []
    for text in texts:
        resp = client.post(
            f"{base_url}/v1beta/models/{GEMINI_EMBEDDING_MODEL}:embedContent",
            json={
                "content": {"parts": [{"text": text}]},
                "taskType": GEMINI_TASK_TYPE,
            },
        )
        resp.raise_for_status()
        results.append(resp.json()["embedding"]["values"])
    return results


def _embed_local(texts: list[str]) -> list[list[float]]:
    """Generate embeddings using local sentence-transformers model."""
    model = _get_local_model()
    results = model.encode(texts, show_progress_bar=False)
    return [v.tolist() for v in results]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a batch of texts.

    Uses Gemini API if configured, otherwise falls back to local model.

    Args:
        texts: List of strings to embed.

    Returns:
        List of embedding vectors.
    """
    _init_backend()

    if _use_gemini:
        try:
            return _embed_gemini(texts)
        except Exception as exc:
            logger.warning(f"Gemini embedding failed, falling back to local: {exc}")
            return _embed_local(texts)
    else:
        return _embed_local(texts)


def embed_single(text: str) -> list[float]:
    """Embed a single text string.

    Convenience wrapper around :func:`embed_texts`.
    """
    return embed_texts([text])[0]
