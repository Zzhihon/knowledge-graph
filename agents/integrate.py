"""Smart Integration Gateway: semantic routing, interview linking, MOC update, relation completion.

After new entries are ingested, this module runs as a background process to:
1. Validate/correct domain and sub_domain via LLM
2. Link to existing interview entries + optionally generate new ones
3. Update or create domain-level MOC (Map of Content) entries
4. Complete `related` fields via semantic similarity search
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import frontmatter
from rich.console import Console

from agents.config import ProjectConfig, load_config

console = Console()

_CHECKPOINT_FILE = Path(__file__).resolve().parent.parent / ".integrate_checkpoint.json"


def _load_checkpoint() -> set[str]:
    """Load set of already-integrated entry IDs from checkpoint file."""
    try:
        if _CHECKPOINT_FILE.exists():
            data = json.loads(_CHECKPOINT_FILE.read_text())
            return set(data.get("done", []))
    except Exception:
        pass
    return set()


def _save_checkpoint(done: set[str]) -> None:
    """Persist completed entry IDs to checkpoint file."""
    try:
        _CHECKPOINT_FILE.write_text(json.dumps({"done": sorted(done)}, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Phase 1: Semantic routing — validate/correct domain + sub_domain
# ---------------------------------------------------------------------------

_ROUTE_PROMPT = """\
你是知识分类专家。请根据以下知识条目内容，判断最合适的 domain 和 sub_domain。

可用的 domain 及其 sub_domains:
{domain_definitions}

条目标题: {title}
条目内容摘要:
{snippet}

请返回 JSON 格式:
{{"domain": "最合适的domain", "sub_domain": "最合适的sub_domain", "secondary_domains": ["可选的次要domain"], "confidence": 0.0-1.0}}

只返回 JSON，不要有其他文字。"""


def _semantic_route(
    entry_path: Path,
    config: ProjectConfig,
) -> dict[str, Any]:
    """Validate and correct domain/sub_domain for a single entry."""
    from agents.api_client import _get_manager
    from agents.json_utils import parse_json_robust, strip_code_fence

    post = frontmatter.load(str(entry_path))
    meta = dict(post.metadata)
    title = meta.get("title", "")
    content = post.content[:2000]  # first 2000 chars as snippet

    # Build domain definitions
    lines = []
    for key, domain in config.domains.items():
        subs = ", ".join(domain.sub_domains)
        lines.append(f"- {key}: [{subs}]")
    domain_defs = "\n".join(lines)

    prompt = _ROUTE_PROMPT.format(
        domain_definitions=domain_defs,
        title=title,
        snippet=content,
    )

    mgr = _get_manager()
    client, model = mgr.get_client()
    text, _ = client.stream_extract(prompt, max_tokens=512)
    text = strip_code_fence(text)
    result = parse_json_robust(text)
    if isinstance(result, list):
        result = result[0]

    return {
        "original_domain": meta.get("domain", ""),
        "original_sub_domain": meta.get("sub_domain", ""),
        "domain": result.get("domain", meta.get("domain", "")),
        "sub_domain": result.get("sub_domain", meta.get("sub_domain", "")),
        "secondary_domains": result.get("secondary_domains", []),
        "confidence": result.get("confidence", 0.5),
        "changed": result.get("domain") != meta.get("domain"),
    }


def _apply_route(entry_path: Path, route: dict[str, Any]) -> None:
    """Write the corrected domain/sub_domain back to the entry."""
    post = frontmatter.load(str(entry_path))
    post.metadata["domain"] = route["domain"]
    post.metadata["sub_domain"] = route["sub_domain"]
    post.metadata["updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry_path.write_text(frontmatter.dumps(post), encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 2: Interview linking + generation
# ---------------------------------------------------------------------------

_INTERVIEW_JUDGE_PROMPT = """\
你是面试题专家。判断以下知识条目是否有面试价值，如果有，应该归入哪个面试类别。

面试类别:
- fundamentals: 底层原理深度理解
- tech-choices: 技术选型决策推理
- real-scenarios: 实际工程问题解决（大厂实战）
- project-deep-dive: 项目细节深挖
- industry-practice: 行业最佳实践

条目标题: {title}
条目域: {domain}
条目摘要:
{snippet}

请返回 JSON:
{{"has_interview_value": true/false, "categories": ["适合的category"], "suggested_questions": ["建议的面试问题(1-3个)"], "reason": "简要理由"}}

只返回 JSON。"""


def _assess_interview_value(
    entry_path: Path,
    config: ProjectConfig,
) -> dict[str, Any]:
    """Judge if an entry has interview value and which categories."""
    from agents.api_client import _get_manager
    from agents.json_utils import parse_json_robust, strip_code_fence

    post = frontmatter.load(str(entry_path))
    meta = dict(post.metadata)

    prompt = _INTERVIEW_JUDGE_PROMPT.format(
        title=meta.get("title", ""),
        domain=meta.get("domain", ""),
        snippet=post.content[:1500],
    )

    mgr = _get_manager()
    client, model = mgr.get_client()
    text, _ = client.stream_extract(prompt, max_tokens=1024)
    text = strip_code_fence(text)
    result = parse_json_robust(text)
    if isinstance(result, list):
        result = result[0]

    return {
        "has_value": result.get("has_interview_value", False),
        "categories": result.get("categories", []),
        "suggested_questions": result.get("suggested_questions", []),
        "reason": result.get("reason", ""),
    }


def _find_related_interviews(
    entry_path: Path,
    config: ProjectConfig,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Find existing interview entries related to this entry via semantic search."""
    from agents.query import search

    post = frontmatter.load(str(entry_path))
    title = post.metadata.get("title", "")

    results = search(
        query=title,
        filters={"type": "interview"},
        top_k=top_k,
        config=config,
    )
    return [
        {"id": r.get("id", ""), "title": r.get("title", ""), "score": r.get("score", 0)}
        for r in results
        if r.get("score", 0) > 0.3
    ]


def _generate_interview_entry(
    question: str,
    source_entry_path: Path,
    category: str,
    config: ProjectConfig,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Generate a single interview entry from a suggested question."""
    from agents.api_client import _get_manager
    from agents.json_utils import parse_json_robust, strip_code_fence
    from agents.utils import slugify

    post = frontmatter.load(str(source_entry_path))
    source_meta = dict(post.metadata)

    prompt = f"""\
基于以下知识条目内容，为面试问题生成完整的答案框架。

知识条目: {source_meta.get('title', '')}
内容:
{post.content[:3000]}

面试问题: {question}
类别: {category}

请返回 JSON:
{{"answer": "详细的参考答案(markdown)", "difficulty": "easy/medium/hard", "tags": ["相关标签"], "key_points": ["答案要点"]}}

只返回 JSON。"""

    mgr = _get_manager()
    client, model = mgr.get_client()
    text, _ = client.stream_extract(prompt, max_tokens=4096)
    text = strip_code_fence(text)
    result = parse_json_robust(text)
    if isinstance(result, list):
        result = result[0]

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry_id = f"iq-{datetime.now(tz=timezone.utc).strftime('%Y%m%d')}-{slugify(question[:40])}"

    domain = source_meta.get("domain", "")
    if isinstance(domain, str):
        domain = [domain]

    metadata = {
        "id": entry_id,
        "title": question,
        "type": "interview",
        "category": category,
        "project": None,
        "focus_topic": source_meta.get("sub_domain", ""),
        "difficulty": result.get("difficulty", "medium"),
        "answer_framework": "STAR" if category in ("project-deep-dive", "real-scenarios") else "PREP",
        "domain": domain,
        "sub_domain": source_meta.get("sub_domain", ""),
        "tags": result.get("tags", []) + [f"source:{source_meta.get('id', '')}"],
        "confidence": None,
        "status": "active",
        "created": now,
        "updated": now,
        "review_date": now,
        "related": [source_meta.get("id", "")],
    }

    answer = result.get("answer", "")
    key_points = result.get("key_points", [])
    body_parts = [f"## 问题\n\n{question}\n"]
    if answer:
        body_parts.append(f"## 参考答案\n\n{answer}\n")
    if key_points:
        items = "\n".join(f"- {p}" for p in key_points)
        body_parts.append(f"## 答案要点\n\n{items}\n")

    body = "\n".join(body_parts)

    if dry_run:
        return {"id": entry_id, "title": question, "category": category, "status": "dry-run"}

    target_dir = config.vault_path / "09-Interview"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{entry_id}.md"

    fm_post = frontmatter.Post(body, **metadata)
    target_file.write_text(frontmatter.dumps(fm_post), encoding="utf-8")

    return {"id": entry_id, "title": question, "category": category, "file_path": str(target_file)}


# ---------------------------------------------------------------------------
# Phase 3: MOC (Map of Content) update
# ---------------------------------------------------------------------------

def _update_moc(
    entry_path: Path,
    domain: str,
    config: ProjectConfig,
) -> dict[str, Any]:
    """Update or create the domain-level MOC to include this entry."""
    moc_dir = config.vault_path / "00-Index"
    moc_file = moc_dir / f"{domain}.md"

    post_meta = frontmatter.load(str(entry_path))
    entry_id = post_meta.metadata.get("id", "")
    entry_title = post_meta.metadata.get("title", "")

    link_line = f"- [[{entry_id}|{entry_title}]]"
    created_moc = False

    if not moc_file.exists():
        # Create a new MOC
        moc_dir.mkdir(parents=True, exist_ok=True)
        domain_cfg = config.domains.get(domain)
        icon = domain_cfg.icon if domain_cfg else ""
        label = domain_cfg.label if domain_cfg else domain

        moc_content = frontmatter.Post(
            f"## 条目列表\n\n{link_line}\n",
            id=f"moc-{domain}",
            title=f"{label} 知识地图",
            type="moc",
            domain=domain,
            icon=icon,
        )
        moc_file.write_text(frontmatter.dumps(moc_content), encoding="utf-8")
        created_moc = True
    else:
        existing = moc_file.read_text(encoding="utf-8")
        # Check if already linked
        if f"[[{entry_id}" in existing:
            return {"domain": domain, "action": "already_linked", "moc_path": str(moc_file)}

        # Append to the entry list section
        if "## 条目列表" in existing:
            existing = existing.replace("## 条目列表\n", f"## 条目列表\n{link_line}\n", 1)
        else:
            existing += f"\n## 条目列表\n\n{link_line}\n"
        moc_file.write_text(existing, encoding="utf-8")

    return {
        "domain": domain,
        "action": "created_moc" if created_moc else "updated",
        "moc_path": str(moc_file),
    }


# ---------------------------------------------------------------------------
# Phase 4: Related entries completion
# ---------------------------------------------------------------------------

def _complete_relations(
    entry_path: Path,
    config: ProjectConfig,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Find semantically similar entries and add to `related` field."""
    from agents.query import search

    post = frontmatter.load(str(entry_path))
    meta = dict(post.metadata)
    entry_id = meta.get("id", "")
    title = meta.get("title", "")
    existing_related = meta.get("related_topics", [])

    results = search(query=title, top_k=top_k, config=config)
    new_relations = []

    for r in results:
        rid = r.get("id", "")
        if rid == entry_id or rid in existing_related:
            continue
        if r.get("score", 0) < 0.3:
            continue
        new_relations.append({
            "id": rid,
            "title": r.get("title", ""),
            "score": r.get("score", 0),
        })

    if new_relations:
        new_ids = [nr["id"] for nr in new_relations[:5]]
        post.metadata["related_topics"] = list(dict.fromkeys(existing_related + new_ids))
        post.metadata["updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    return new_relations[:5]


# ---------------------------------------------------------------------------
# Public API: integration generator (yields events for SSE)
# ---------------------------------------------------------------------------

def integrate_entries(
    entry_ids: list[str],
    dry_run: bool = False,
    skip_interview: bool = False,
    config: ProjectConfig | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Run the full integration pipeline on a list of entry IDs.

    Yields event dicts for SSE consumption:
      {"event": "start", "total": N}
      {"event": "phase", "entry_id": ..., "phase": "route|interview|moc|relations", "message": ...}
      {"event": "route_done", "entry_id": ..., ...}
      {"event": "interview_linked", "entry_id": ..., ...}
      {"event": "interview_generated", "entry_id": ..., ...}
      {"event": "moc_updated", "entry_id": ..., ...}
      {"event": "relations_done", "entry_id": ..., ...}
      {"event": "entry_done", "entry_id": ..., "index": ..., "total": ...}
      {"event": "complete", ...}
    """
    if config is None:
        config = load_config()

    # Load checkpoint — skip already-done entries for resumability
    done_ids = _load_checkpoint()

    # Resolve entry IDs to file paths
    from agents.utils import load_entries
    all_entries = load_entries(config.vault_path)
    id_to_path: dict[str, Path] = {}
    for e in all_entries:
        eid = e["metadata"].get("id", "")
        if eid in entry_ids:
            id_to_path[eid] = e["path"]

    resolved_ids = [eid for eid in entry_ids if eid in id_to_path]
    # Filter out already-done entries
    pending_ids = [eid for eid in resolved_ids if eid not in done_ids]
    total = len(resolved_ids)
    skipped = len(resolved_ids) - len(pending_ids)

    yield {"event": "start", "total": total, "entry_ids": resolved_ids,
           "pending": len(pending_ids), "skipped_checkpoint": skipped}

    total_interview_linked = 0
    total_interview_generated = 0
    total_moc_updated = 0
    total_relations_added = 0

    for idx, entry_id in enumerate(pending_ids, start=skipped + 1):
        entry_path = id_to_path[entry_id]

        # --- Phase 1: Semantic Route ---
        yield {"event": "phase", "entry_id": entry_id, "phase": "route",
               "message": f"语义路由 ({idx}/{total}): {entry_id}"}
        try:
            route = _semantic_route(entry_path, config)
            if route["changed"] and not dry_run:
                _apply_route(entry_path, route)
            yield {"event": "route_done", "entry_id": entry_id, **route}
            domain = route["domain"]
        except Exception as exc:
            yield {"event": "route_done", "entry_id": entry_id, "error": str(exc), "changed": False}
            post = frontmatter.load(str(entry_path))
            domain = post.metadata.get("domain", "")

        # --- Phase 2: Interview linking ---
        if not skip_interview:
            yield {"event": "phase", "entry_id": entry_id, "phase": "interview",
                   "message": f"面试关联 ({idx}/{total}): {entry_id}"}
            try:
                assessment = _assess_interview_value(entry_path, config)
                if assessment["has_value"]:
                    # 2a: Link existing
                    linked = _find_related_interviews(entry_path, config)
                    if linked:
                        total_interview_linked += len(linked)
                        yield {"event": "interview_linked", "entry_id": entry_id,
                               "linked": linked}

                    # 2b: Generate new questions
                    for q in assessment.get("suggested_questions", [])[:2]:
                        cat = assessment["categories"][0] if assessment["categories"] else "real-scenarios"
                        gen_result = _generate_interview_entry(q, entry_path, cat, config, dry_run=dry_run)
                        if gen_result:
                            total_interview_generated += 1
                            yield {"event": "interview_generated", "entry_id": entry_id, **gen_result}
                else:
                    yield {"event": "interview_skipped", "entry_id": entry_id,
                           "reason": assessment.get("reason", "无面试价值")}
            except Exception as exc:
                yield {"event": "interview_skipped", "entry_id": entry_id, "error": str(exc)}

        # --- Phase 3: MOC update ---
        yield {"event": "phase", "entry_id": entry_id, "phase": "moc",
               "message": f"更新主题总览 ({idx}/{total}): {entry_id}"}
        try:
            if isinstance(domain, list):
                domain = domain[0] if domain else ""
            if domain:
                moc_result = _update_moc(entry_path, domain, config)
                if moc_result["action"] != "already_linked":
                    total_moc_updated += 1
                yield {"event": "moc_updated", "entry_id": entry_id, **moc_result}
        except Exception as exc:
            yield {"event": "moc_updated", "entry_id": entry_id, "error": str(exc)}

        # --- Phase 4: Related entries ---
        yield {"event": "phase", "entry_id": entry_id, "phase": "relations",
               "message": f"关系补全 ({idx}/{total}): {entry_id}"}
        try:
            relations = _complete_relations(entry_path, config)
            total_relations_added += len(relations)
            yield {"event": "relations_done", "entry_id": entry_id, "relations": relations}
        except Exception as exc:
            yield {"event": "relations_done", "entry_id": entry_id, "error": str(exc)}

        yield {"event": "entry_done", "entry_id": entry_id, "index": idx, "total": total}
        done_ids.add(entry_id)
        if not dry_run:
            _save_checkpoint(done_ids)

    # Sync index
    try:
        from agents.sync_engine import incremental_sync
        yield {"event": "phase", "entry_id": "", "phase": "sync", "message": "同步索引..."}
        incremental_sync(config)
    except Exception:
        pass

    yield {
        "event": "complete",
        "total_entries": total,
        "interview_linked": total_interview_linked,
        "interview_generated": total_interview_generated,
        "moc_updated": total_moc_updated,
        "relations_added": total_relations_added,
    }
