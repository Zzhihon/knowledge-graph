"""Quality assessment gate for knowledge ingestion.

Evaluates each extracted entry before creation, deciding whether to
CREATE (new, high quality), MERGE (duplicate but valuable), or SKIP
(low quality).  Uses vector similarity for novelty detection and
heuristic scoring for quality.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import frontmatter

from agents.config import ProjectConfig, load_config
from agents.embeddings import embed_single
from agents.vector_store import VectorStore, get_vector_store

logger = logging.getLogger(__name__)

# Module-level lock — Qdrant local mode only supports one client at a time.
# This serializes all novelty checks across threads.
_qdrant_lock = threading.Lock()


@dataclass
class QualityAssessment:
    """Result of quality evaluation for a single extracted entry."""

    entry: dict[str, Any]
    action: Literal["create", "merge", "skip"]
    novelty_score: float          # 0 = exact duplicate, 1 = completely new
    quality_score: float          # 0 = low quality, 1 = high quality
    reason: str
    merge_target_id: str | None = None
    merge_target_path: Path | None = None


def assess_entries(
    extracted: list[dict[str, Any]],
    config: ProjectConfig | None = None,
    novelty_threshold: float = 0.3,
    quality_threshold: float = 0.4,
) -> list[QualityAssessment]:
    """Assess a batch of extracted entries for quality and novelty.

    Decision matrix:
      novelty >= threshold AND quality >= threshold → CREATE
      novelty < threshold  AND quality >= threshold → MERGE
      quality < threshold                           → SKIP

    Args:
        extracted: List of entry dicts from Claude extraction.
        config: Project configuration (auto-loaded if None).
        novelty_threshold: Below this, entry is considered a duplicate.
        quality_threshold: Below this, entry is skipped.

    Returns:
        List of QualityAssessment, one per input entry.
    """
    if config is None:
        config = load_config()

    results: list[QualityAssessment] = []

    # Open VectorStore once for all novelty checks in this batch.
    # Uses the module-level lock to prevent Qdrant file contention
    # when multiple threads call assess_entries concurrently.
    with _qdrant_lock:
        try:
            store = get_vector_store(config)
        except Exception:
            logger.warning("无法打开向量数据库，跳过新颖度检测", exc_info=True)
            store = None

        try:
            for entry in extracted:
                quality = _compute_quality(entry)
                novelty, target_id, target_path = _compute_novelty(entry, config, store)

                if quality < quality_threshold:
                    action: Literal["create", "merge", "skip"] = "skip"
                    reason = f"质量分 {quality:.2f} 低于阈值 {quality_threshold}"
                elif novelty < novelty_threshold:
                    action = "merge"
                    reason = (
                        f"新颖度 {novelty:.2f} 低于阈值 {novelty_threshold}，与已有条目相似"
                    )
                else:
                    action = "create"
                    reason = f"质量 {quality:.2f}, 新颖度 {novelty:.2f} — 满足创建条件"

                results.append(QualityAssessment(
                    entry=entry,
                    action=action,
                    novelty_score=novelty,
                    quality_score=quality,
                    reason=reason,
                    merge_target_id=target_id if action == "merge" else None,
                    merge_target_path=target_path if action == "merge" else None,
                ))
        finally:
            if store is not None:
                store.close()

    return results


def _compute_novelty(
    entry: dict[str, Any],
    config: ProjectConfig,
    store: VectorStore | None = None,
) -> tuple[float, str | None, Path | None]:
    """Measure how novel an entry is relative to existing knowledge.

    Embeds title + key insights, searches Qdrant for the most similar
    existing entry, and returns ``1 - best_similarity``.

    Args:
        entry: Extracted entry dict.
        config: Project configuration.
        store: Pre-opened VectorStore (avoids repeated open/close).

    Returns:
        (novelty_score, best_match_entry_id, best_match_file_path)
    """
    if store is None:
        return 1.0, None, None

    title = entry.get("title", "")
    insights = entry.get("key_insights", [])
    query_text = title + " " + " ".join(insights)

    if not query_text.strip():
        return 1.0, None, None

    try:
        embedding = embed_single(query_text)
        hits = store.search(embedding, top_k=1)

        if not hits:
            return 1.0, None, None

        best = hits[0]
        similarity = best.get("score", 0.0)
        entry_id = best.get("entry_id", "")
        file_path_str = best.get("metadata", {}).get("file_path", "")
        file_path = Path(file_path_str) if file_path_str else None

        novelty = max(0.0, 1.0 - similarity)
        return novelty, entry_id, file_path
    except Exception:
        logger.warning("向量搜索失败，假定为全新条目", exc_info=True)
        return 1.0, None, None


def _compute_quality(entry: dict[str, Any]) -> float:
    """Heuristic quality scoring for an extracted entry.

    Weighted components:
      - analysis_depth (0.4): length > 200 chars, contains code
      - evidence (0.3): >= 3 key insights, technical terms
      - specificity (0.3): has question, tags, sub_domain
    """
    # Analysis depth (weight 0.4)
    analysis = entry.get("analysis", "")
    depth_score = 0.0
    if len(analysis) > 200:
        depth_score += 0.5
    if len(analysis) > 500:
        depth_score += 0.2
    if "```" in analysis:  # contains code block
        depth_score += 0.3
    depth_score = min(depth_score, 1.0)

    # Evidence (weight 0.3)
    insights = entry.get("key_insights", [])
    evidence_score = 0.0
    if len(insights) >= 3:
        evidence_score += 0.5
    if len(insights) >= 5:
        evidence_score += 0.2
    # Check for technical specificity in insights
    technical_markers = ["API", "O(", "http", "func", "class", "import", "协议", "算法"]
    insight_text = " ".join(str(i) for i in insights)
    if any(m in insight_text for m in technical_markers):
        evidence_score += 0.3
    evidence_score = min(evidence_score, 1.0)

    # Specificity (weight 0.3)
    specificity_score = 0.0
    if entry.get("question"):
        specificity_score += 0.4
    if entry.get("tags") and len(entry["tags"]) >= 2:
        specificity_score += 0.3
    if entry.get("sub_domain"):
        specificity_score += 0.3
    specificity_score = min(specificity_score, 1.0)

    return depth_score * 0.4 + evidence_score * 0.3 + specificity_score * 0.3


def merge_into_existing(
    new_entry: dict[str, Any],
    target_path: Path,
    config: ProjectConfig | None = None,
) -> dict[str, Any]:
    """Append new insights from an entry into an existing vault file.

    Reads the target markdown, appends new key_insights and analysis
    sections, and writes back.

    Args:
        new_entry: The extracted entry to merge from.
        target_path: Path to the existing entry markdown file.
        config: Project configuration (unused, reserved for future).

    Returns:
        Dict with merge status information.
    """
    if not target_path.is_file():
        return {"status": "error", "reason": f"目标文件不存在: {target_path}"}

    post = frontmatter.load(str(target_path))

    # Append new insights
    new_insights = new_entry.get("key_insights", [])
    if new_insights:
        existing_insights_text = post.content
        additions: list[str] = []
        for insight in new_insights:
            if str(insight) not in existing_insights_text:
                additions.append(f"- {insight}")
        if additions:
            post.content += f"\n\n## 补充洞察\n\n" + "\n".join(additions) + "\n"

    # Append additional analysis
    new_analysis = new_entry.get("analysis", "")
    if new_analysis and new_analysis not in post.content:
        post.content += f"\n\n## 补充分析\n\n{new_analysis}\n"

    # Merge tags
    existing_tags = list(post.metadata.get("tags", []))
    new_tags = new_entry.get("tags", [])
    for tag in new_tags:
        if tag not in existing_tags:
            existing_tags.append(tag)
    post.metadata["tags"] = existing_tags

    # Update timestamp
    from datetime import datetime, timezone

    post.metadata["updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    target_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    return {
        "status": "merged",
        "target_path": str(target_path),
        "target_id": post.metadata.get("id", ""),
        "insights_added": len(new_insights),
    }
