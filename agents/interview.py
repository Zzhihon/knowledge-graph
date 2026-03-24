"""Interview question bank: resume-based question generation and management.

Generates structured interview questions from resume content using LLM,
writes them as vault markdown entries with STAR/PREP answer frameworks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import frontmatter
import yaml

from agents.config import ProjectConfig, load_config
from agents.json_utils import parse_json_robust, strip_code_fence
from agents.utils import slugify, load_entries


def _get_interview_config(config: ProjectConfig) -> dict[str, Any]:
    """Load interview section from config.yaml."""
    config_path = config.root_path / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw.get("interview", {})


def load_resume(config: ProjectConfig) -> str:
    """Read resume content from the configured PDF path.

    Falls back to plain text reading if PDF extraction is not available.
    """
    iv_config = _get_interview_config(config)
    resume_path = Path(iv_config.get("resume_path", ""))

    if not resume_path.exists():
        raise FileNotFoundError(f"简历文件不存在: {resume_path}")

    suffix = resume_path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pymupdf
            doc = pymupdf.open(str(resume_path))
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text.strip()
        except ImportError:
            # Fallback: try pdfplumber
            try:
                import pdfplumber
                with pdfplumber.open(resume_path) as pdf:
                    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                return text.strip()
            except ImportError:
                raise RuntimeError(
                    "需要安装 pymupdf 或 pdfplumber 来解析 PDF 简历: "
                    "pip install pymupdf"
                )
    else:
        return resume_path.read_text(encoding="utf-8").strip()


def get_interview_stats(config: ProjectConfig | None = None) -> dict[str, Any]:
    """Return aggregate statistics about the interview question bank."""
    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path, filters={"type": "interview"})
    iv_config = _get_interview_config(config)
    categories = iv_config.get("categories", {})

    total = len(entries)
    category_dist: dict[str, int] = {}
    project_dist: dict[str, int] = {}
    difficulty_dist: dict[str, int] = {}
    tag_dist: dict[str, int] = {}
    needs_review = 0
    confidence_sum = 0.0
    confidence_count = 0
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    for entry in entries:
        meta = entry["metadata"]

        cat = str(meta.get("category", "unknown"))
        category_dist[cat] = category_dist.get(cat, 0) + 1

        proj = meta.get("project")
        if proj:
            project_dist[str(proj)] = project_dist.get(str(proj), 0) + 1

        diff = str(meta.get("difficulty", "medium")).lower()
        difficulty_dist[diff] = difficulty_dist.get(diff, 0) + 1

        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        for tag in tags:
            tag_dist[str(tag)] = tag_dist.get(str(tag), 0) + 1

        review_date = str(meta.get("review_date", ""))
        if review_date and review_date <= today:
            needs_review += 1

        conf = meta.get("confidence")
        if conf is not None:
            confidence_sum += float(conf)
            confidence_count += 1

    return {
        "total_questions": total,
        "category_distribution": category_dist,
        "project_distribution": project_dist,
        "difficulty_distribution": difficulty_dist,
        "tag_distribution": tag_dist,
        "needs_review": needs_review,
        "avg_confidence": round(confidence_sum / confidence_count, 2) if confidence_count else None,
        "categories": {
            k: {"label": v.get("label", k), "description": v.get("description", "")}
            for k, v in categories.items()
        },
    }


def get_interview_categories(config: ProjectConfig | None = None) -> list[dict[str, Any]]:
    """List all interview categories with question counts."""
    if config is None:
        config = load_config()

    iv_config = _get_interview_config(config)
    categories = iv_config.get("categories", {})
    entries = load_entries(config.vault_path, filters={"type": "interview"})

    cat_counts: dict[str, int] = {}
    for entry in entries:
        cat = str(entry["metadata"].get("category", ""))
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    result = []
    for key, info in categories.items():
        result.append({
            "key": key,
            "label": info.get("label", key),
            "description": info.get("description", ""),
            "question_count": cat_counts.get(key, 0),
        })
    return result


def _build_prompt(
    category: str,
    project: str | None,
    skill_domain: str | None,
    focus_topic: str | None,
    resume_text: str,
    existing_titles: list[str],
    count: int,
    difficulty_distribution: dict[str, int],
    iv_config: dict[str, Any],
    domain_definitions: str,
) -> str:
    """Construct the LLM prompt for interview question generation."""
    categories = iv_config.get("categories", {})
    cat_info = categories.get(category, {})
    cat_label = cat_info.get("label", category)
    cat_desc = cat_info.get("description", "")

    # Build focus context
    focus_context = ""
    if project:
        projects = iv_config.get("projects", [])
        proj_info = next((p for p in projects if p.get("slug") == project), None)
        proj_name = proj_info["name"] if proj_info else project
        focus_context += f"\n重点聚焦项目：「{proj_name}」，所有题目必须围绕该项目展开。\n"
    if skill_domain:
        focus_context += (
            f"\n重点聚焦技能域：「{skill_domain}」。"
            f"所有题目必须围绕该技能域展开，深入考察底层原理、实战经验和边界场景。"
            f"可以结合简历中涉及该领域的项目经历来出题，但核心考点是该技能域本身。\n"
        )
    if focus_topic:
        focus_context += (
            f"\n重点聚焦关键词/主题：「{focus_topic}」。"
            f"这是比项目或技能域更细粒度的追问方向，优先围绕这个主题出题。"
            f"如果同时提供了项目或技能域，请在对应上下文中围绕该主题深挖，"
            f"但所有题目仍必须严格基于候选人简历中的真实经历、项目和技术栈，"
            f"不要生成与候选人背景无关的泛化题目。\n"
        )

    existing_str = "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "（暂无）"

    # Category-specific angle
    if category == "project-deep-dive":
        angle = (
            "像真实面试一样，从架构选型、技术难点、故障处理、性能优化等角度深挖。"
            "追问要逐层递进，从基础确认 → 实现细节 → 边界/故障场景。"
        )
        framework = "STAR"
    elif category == "fundamentals":
        angle = (
            "考察候选人对底层原理的理解深度。"
            "例如：GMP 模型、channel 底层实现、K8s 调度原理、数据库索引原理等。"
            "追问要从概念 → 实现机制 → 源码/数据结构层面。"
        )
        framework = "PREP"
    elif category == "tech-choices":
        angle = (
            "考察候选人的技术决策推理能力。"
            "例如：为什么选 RabbitMQ 而不是 Kafka？为什么用 Cilium 而不是 Calico？"
            "追问要从场景分析 → 方案对比 → 权衡取舍。"
        )
        framework = "PREP"
    else:  # real-scenarios
        angle = (
            "考察候选人解决实际工程问题的能力。"
            "例如：线上内存泄漏如何排查？服务雪崩如何降级？"
            "追问要从问题发现 → 定位方法 → 解决方案 → 预防措施。"
        )
        framework = "STAR"

    distribution_parts = []
    for difficulty in ("easy", "medium", "hard"):
        qty = difficulty_distribution.get(difficulty, 0)
        if qty > 0:
            label = {"easy": "简单", "medium": "中等", "hard": "困难"}[difficulty]
            distribution_parts.append(f"{label} {qty} 道")
    difficulty_distribution_text = "、".join(distribution_parts)

    return f"""\
你是一位资深技术面试官，正在针对候选人进行「{cat_label}」类别的面试。
{cat_desc}
{angle}
{focus_context}
候选人简历内容：
---
{resume_text}
---

可用的技术域参考（用于标注 domain 字段）：
{domain_definitions}

要求：
1. 生成 {count} 道面试题，问题必须基于简历中的真实经历和技术栈
2. 面试官口语化风格，像真实面试中的提问
3. 每道题包含 2-3 个递进追问
4. 答案使用 {framework} 框架组织
5. 难度分布：{difficulty_distribution_text}
6. 为每道题标注相关技术域（从上方可用技术域中选择）

已有题目（避免重复）：
{existing_str}

请以 JSON 数组返回，每个元素格式：
{{
  "title": "面试题标题（简短概括）",
  "question": "面试官提出的问题（口语化）",
  "difficulty": "easy|medium|hard",
  "domain": ["相关技术域"],
  "tags": ["标签1", "标签2"],
  "key_points": ["要点1", "要点2", "要点3"],
  "answer": "使用 {framework} 框架的结构化回答（markdown 格式）",
  "follow_ups": [
    {{"question": "追问1", "answer": "参考回答1"}},
    {{"question": "追问2", "answer": "参考回答2"}}
  ]
}}

只返回 JSON 数组，不要有其他文字。"""


def _calculate_difficulty_distribution(count: int) -> dict[str, int]:
    """Return a difficulty split that sums exactly to count."""
    if count <= 1:
        return {"easy": 1, "medium": 0, "hard": 0}
    if count == 2:
        return {"easy": 1, "medium": 1, "hard": 0}
    return {"easy": 1, "medium": count - 2, "hard": 1}


def _write_interview_entry(
    question_data: dict[str, Any],
    category: str,
    project: str | None,
    focus_topic: str | None,
    framework: str,
    config: ProjectConfig,
) -> str:
    """Write a single interview question as a vault markdown file.

    Returns the file path of the created entry.
    """
    title = question_data["title"]
    date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    slug = slugify(title, max_length=50)
    entry_id = f"iq-{date_str}-{slug}"
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Build frontmatter
    meta: dict[str, Any] = {
        "id": entry_id,
        "title": title,
        "type": "interview",
        "category": category,
        "project": project,
        "focus_topic": focus_topic,
        "difficulty": question_data.get("difficulty", "medium"),
        "answer_framework": framework,
        "domain": question_data.get("domain", []),
        "tags": question_data.get("tags", []),
        "confidence": None,
        "status": "active",
        "created": today,
        "updated": today,
        "review_date": today,
    }

    # Build markdown body
    question_text = question_data.get("question", "")
    key_points = question_data.get("key_points", [])
    answer_text = question_data.get("answer", "")
    follow_ups = question_data.get("follow_ups", [])

    body_parts = [
        f"# {title}\n",
        "## Question",
        f"> [!question] 面试题\n> {question_text}\n",
        "## Key Points",
    ]
    for pt in key_points:
        body_parts.append(f"- {pt}")

    body_parts.append(f"\n## Answer\n**框架**: {framework}\n\n{answer_text}\n")

    if follow_ups:
        body_parts.append("## Follow-ups")
        for i, fu in enumerate(follow_ups, 1):
            body_parts.append(f"### 追问 {i}: {fu.get('question', '')}")
            body_parts.append(f"**参考回答**: {fu.get('answer', '')}\n")

    body = "\n".join(body_parts)

    # Write file
    post = frontmatter.Post(body, **meta)
    out_dir = config.vault_path / "09-Interview"
    out_dir.mkdir(exist_ok=True)
    file_path = out_dir / f"{entry_id}.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))

    return str(file_path)


def generate_interview_questions(
    category: str | None,
    project: str | None,
    count: int,
    skill_domain: str | None = None,
    focus_topic: str | None = None,
    config: ProjectConfig | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Generate interview questions via LLM, yielding progress events.

    Yields dicts with event types: start, question_done, error, complete.
    """
    if config is None:
        config = load_config()

    iv_config = _get_interview_config(config)
    categories = iv_config.get("categories", {})

    # Build domain definitions string from config for prompt context
    domain_lines = []
    for dk, dv in config.domains.items():
        subs = ", ".join(dv.sub_domains) if dv.sub_domains else ""
        domain_lines.append(f"- {dk} ({dv.label}): {subs}")
    domain_definitions = "\n".join(domain_lines)

    normalized_focus_topic = focus_topic.strip() if focus_topic else None
    if normalized_focus_topic:
        normalized_focus_topic = normalized_focus_topic[:120]

    # Determine which categories to generate for
    if category:
        cats_to_generate = [category]
    else:
        cats_to_generate = list(categories.keys())

    # Load resume
    try:
        resume_text = load_resume(config)
    except Exception as exc:
        yield {"event": "error", "message": f"无法加载简历: {exc}"}
        return

    # Get existing titles to avoid duplicates
    existing_entries = load_entries(config.vault_path, filters={"type": "interview"})
    existing_titles = [
        str(e["metadata"].get("title", "")) for e in existing_entries
    ]

    # Get LLM client
    from agents.api_client import _get_manager
    manager = _get_manager()
    client, model = manager.get_client()

    total_expected = count * len(cats_to_generate)
    yield {"event": "start", "total_expected": total_expected, "model": model}

    total_created = 0
    total_failed = 0
    global_index = 0

    for cat in cats_to_generate:
        # Determine answer framework by category
        framework = "STAR" if cat in ("project-deep-dive", "real-scenarios") else "PREP"
        difficulty_distribution = _calculate_difficulty_distribution(count)

        prompt = _build_prompt(
            category=cat,
            project=project,
            skill_domain=skill_domain,
            focus_topic=normalized_focus_topic,
            resume_text=resume_text,
            existing_titles=existing_titles,
            count=count,
            difficulty_distribution=difficulty_distribution,
            iv_config=iv_config,
            domain_definitions=domain_definitions,
        )

        try:
            response_text, stop_reason = client.stream_extract(prompt, max_tokens=16384)
            cleaned = strip_code_fence(response_text)
            questions = parse_json_robust(cleaned)

            if not isinstance(questions, list):
                questions = [questions]

            for q in questions:
                global_index += 1
                try:
                    file_path = _write_interview_entry(
                        question_data=q,
                        category=cat,
                        project=project,
                        focus_topic=normalized_focus_topic,
                        framework=framework,
                        config=config,
                    )
                    existing_titles.append(q.get("title", ""))
                    total_created += 1
                    yield {
                        "event": "question_done",
                        "index": global_index,
                        "total": total_expected,
                        "title": q.get("title", ""),
                        "category": cat,
                        "difficulty": q.get("difficulty", "medium"),
                        "file_path": file_path,
                    }
                except Exception as exc:
                    total_failed += 1
                    yield {
                        "event": "error",
                        "message": f"写入失败 [{q.get('title', '?')}]: {exc}",
                    }

        except Exception as exc:
            total_failed += count
            yield {
                "event": "error",
                "message": f"LLM 生成失败 [category={cat}]: {exc}",
            }

    yield {
        "event": "complete",
        "total_created": total_created,
        "total_failed": total_failed,
    }
