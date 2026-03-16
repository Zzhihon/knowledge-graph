"""Cross-domain knowledge discovery engine.

Finds semantically similar entries across different knowledge domains,
revealing shared patterns and transferable insights (e.g., "Go scheduler
work-stealing ↔ K8s scheduler bin-packing").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from agents.config import ProjectConfig, load_config
from agents.embeddings import embed_single
from agents.json_utils import parse_json_robust, strip_code_fence
from agents.utils import load_entries
from agents.vector_store import get_vector_store

logger = logging.getLogger(__name__)


@dataclass
class CrossDomainInsight:
    """A discovered cross-domain connection between two entries."""

    domain_a: str
    domain_b: str
    entry_a_id: str
    entry_a_title: str
    entry_b_id: str
    entry_b_title: str
    similarity: float
    description: str = ""


def discover_cross_domain(
    min_similarity: float = 0.6,
    max_insights: int = 20,
    describe: bool = True,
    config: ProjectConfig | None = None,
) -> list[CrossDomainInsight]:
    """Discover cross-domain knowledge connections.

    Algorithm:
      1. Load all entries, group by domain
      2. For each entry, search Qdrant excluding its own domain(s)
      3. Deduplicate bidirectional pairs (A↔B == B↔A)
      4. Sort by similarity, take top N
      5. Optionally generate Claude descriptions for connections

    Args:
        min_similarity: Minimum cosine similarity to consider.
        max_insights: Maximum number of insights to return.
        describe: Whether to generate Claude descriptions.
        config: Project configuration.

    Returns:
        List of CrossDomainInsight sorted by similarity descending.
    """
    if config is None:
        config = load_config()

    pairs = _find_cross_domain_pairs(config, min_similarity)

    if not pairs:
        return []

    # Sort by similarity descending, take top N
    pairs.sort(key=lambda p: p[2], reverse=True)
    pairs = pairs[:max_insights]

    # Build insight objects
    insights: list[CrossDomainInsight] = []
    for entry_a, entry_b, sim in pairs:
        meta_a = entry_a.get("metadata", {})
        meta_b = entry_b.get("metadata", {})
        domain_a = meta_a.get("domain", "unknown")
        domain_b = meta_b.get("domain", "unknown")
        if isinstance(domain_a, list):
            domain_a = domain_a[0] if domain_a else "unknown"
        if isinstance(domain_b, list):
            domain_b = domain_b[0] if domain_b else "unknown"

        insights.append(CrossDomainInsight(
            domain_a=domain_a,
            domain_b=domain_b,
            entry_a_id=meta_a.get("id", ""),
            entry_a_title=meta_a.get("title", ""),
            entry_b_id=meta_b.get("id", ""),
            entry_b_title=meta_b.get("title", ""),
            similarity=sim,
        ))

    # Generate descriptions
    if describe and insights:
        descriptions = _generate_insight_descriptions(insights, config)
        for insight, desc in zip(insights, descriptions):
            insight.description = desc

    return insights


def _find_cross_domain_pairs(
    config: ProjectConfig,
    min_similarity: float,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Find cross-domain entry pairs above similarity threshold.

    Returns list of (entry_a, entry_b, similarity) tuples,
    deduplicated so (A,B) and (B,A) only appear once.
    """
    entries = load_entries(config.vault_path)
    if not entries:
        return []

    # Group entries by domain
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        meta = entry.get("metadata", {})
        domains = meta.get("domain", [])
        if isinstance(domains, str):
            domains = [domains]
        for d in domains:
            if d:
                by_domain.setdefault(d, []).append(entry)

    if len(by_domain) < 2:
        return []

    seen_pairs: set[tuple[str, str]] = set()
    results: list[tuple[dict[str, Any], dict[str, Any], float]] = []

    with get_vector_store(config) as store:
        for entry in entries:
            meta = entry.get("metadata", {})
            entry_id = meta.get("id", "")
            if not entry_id:
                continue

            domains = meta.get("domain", [])
            if isinstance(domains, str):
                domains = [domains]
            if not domains:
                continue

            # Build embeddable text
            title = meta.get("title", "")
            tags = meta.get("tags", [])
            tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
            query_text = f"{title}\n{tags_str}\n\n{entry.get('content', '')}"

            embedding = embed_single(query_text)

            # Search excluding own domains
            hits = store.search_cross_domain(
                query_embedding=embedding,
                exclude_domains=domains,
                top_k=3,
            )

            for hit in hits:
                hit_id = hit.get("entry_id", "")
                score = hit.get("score", 0.0)

                if score < min_similarity:
                    continue

                # Deduplicate
                pair_key = tuple(sorted([entry_id, hit_id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # Find the matching entry object
                hit_entry = None
                for e in entries:
                    if e.get("metadata", {}).get("id") == hit_id:
                        hit_entry = e
                        break

                if hit_entry:
                    results.append((entry, hit_entry, score))

    return results


def _generate_insight_descriptions(
    insights: list[CrossDomainInsight],
    config: ProjectConfig,
) -> list[str]:
    """Generate Claude descriptions for cross-domain connections.

    Batches all insights into a single API call for efficiency.
    """
    pairs_text = "\n".join(
        f"{i+1}. [{insight.domain_a}] {insight.entry_a_title} "
        f"↔ [{insight.domain_b}] {insight.entry_b_title} "
        f"(相似度: {insight.similarity:.2f})"
        for i, insight in enumerate(insights)
    )

    prompt = f"""\
以下是跨域知识条目对，它们在语义空间中具有高相似度。
请为每对简要描述它们的共通模式或可迁移的思想（每条 1-2 句话，中文）。

{pairs_text}

请以 JSON 数组返回，每个元素是一个描述字符串，按顺序对应上面的编号。
只返回 JSON 数组，不要有其他文字。"""

    try:
        from agents.api_client import get_anthropic_client
        client, model = get_anthropic_client()
        message = client.messages.create(
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        response_text = strip_code_fence(response_text)
        descriptions = parse_json_robust(response_text)
        if isinstance(descriptions, list):
            while len(descriptions) < len(insights):
                descriptions.append("")
            return descriptions[:len(insights)]
    except (anthropic.APITimeoutError, httpx.ConnectTimeout):
        logger.warning("跨域描述生成超时，跳过描述生成")
    except Exception:
        logger.warning("生成跨域描述失败", exc_info=True)

    return [""] * len(insights)
