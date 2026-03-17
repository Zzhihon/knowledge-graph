"""Ingestion agent for extracting structured knowledge entries.

Reads markdown files (conversation exports or raw notes), sends them
to Claude for structured extraction, and writes entries with proper
YAML frontmatter to the appropriate vault directories.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.json_utils import parse_json_robust, strip_code_fence
from agents.utils import generate_id, get_entry_dir
from agents.api_client import APIClientManager

console = Console()

def _get_api_manager(config: ProjectConfig) -> APIClientManager:
    """Get the global API client manager from api_client module."""
    from agents.api_client import _get_manager
    return _get_manager()

# Extraction prompt template for Claude
_EXTRACTION_PROMPT = """\
你是一个知识提取助手。请从以下内容中提取结构化的知识条目。

对于每个独立的知识点，提取以下信息：
1. title: 简明的中文标题
2. question: 这个知识点回答了什么问题？
3. domain: 知识域 (从以下选择: {domain_keys})
4. sub_domain: 子域 (对应所选 domain 的 sub_domains)
5. entry_type: 条目类型 (principle | pattern | debug | architecture | research | problem)
6. depth: 深度层级 (surface | intermediate | deep)
7. tags: 相关标签列表
8. analysis: 核心分析内容 (markdown 格式，包含代码示例)
9. key_insights: 关键洞察列表 (3-5 条)
10. related_topics: 相关主题列表

可用的知识域及其子域:
{domain_definitions}

请以 JSON 数组格式返回，每个元素是一个知识条目对象。
只返回 JSON，不要有其他文字。

---
内容:
{content}
"""


def _build_domain_definitions(config: ProjectConfig) -> str:
    """Build a human-readable domain definition string for the prompt."""
    lines: list[str] = []
    for key, domain in config.domains.items():
        subs = ", ".join(domain.sub_domains)
        lines.append(f"- {key} ({domain.label} {domain.icon}): [{subs}]")
    return "\n".join(lines)


def _call_claude_extract(
    content: str,
    config: ProjectConfig,
    max_retries: int = 3,
) -> list[dict[str, Any]]:
    """Call the Claude API to extract structured knowledge entries.

    Args:
        content: Raw markdown content to analyze.
        config: Project configuration for domain definitions.
        max_retries: Maximum number of retry attempts.

    Returns:
        List of extracted entry dicts.

    Raises:
        RuntimeError: If the API call fails or returns unparseable output.
    """
    import time

    # Get API client manager
    api_manager = _get_api_manager(config)

    prompt = _EXTRACTION_PROMPT.format(
        domain_keys=", ".join(config.domain_keys),
        domain_definitions=_build_domain_definitions(config),
        content=content,
    )

    last_error = None
    response_text = ""
    stop_reason = "end_turn"

    for attempt in range(max_retries):
        try:
            # Get client with load balancing (UnifiedClient supports both Anthropic and OpenAI)
            client, model = api_manager.get_client()

            key_hint = client.key_config.key[:8] if hasattr(client, 'key_config') else "?"
            console.print(f"[dim]使用模型: {model} (key: {key_hint}...)[/]")

            # 使用统一 streaming 接口，自动适配 Anthropic / OpenAI
            response_text, stop_reason = client.stream_extract(prompt, max_tokens=16384)
            break  # 成功则跳出重试循环

        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 指数退避: 1s, 2s, 4s
                console.print(f"[yellow]API 调用失败，{wait_time}秒后重试... (尝试 {attempt + 1}/{max_retries})[/]")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"API 调用失败（已重试 {max_retries} 次）: {exc}") from exc
    else:
        raise RuntimeError(f"API 调用失败（已重试 {max_retries} 次）: {last_error}") from last_error

    # Check if response was truncated
    console.print(f"[dim]响应长度: {len(response_text)} 字符, stop_reason: {stop_reason}[/]")
    if stop_reason == "max_tokens":
        console.print("[yellow]警告: 响应被截断（达到 max_tokens 限制）[/]")

    response_text = strip_code_fence(response_text)

    try:
        entries = parse_json_robust(response_text)
    except RuntimeError as exc:
        # 如果 JSON 解析失败且是因为截断，记录警告但继续
        if stop_reason == "max_tokens":
            console.print(f"[red]错误: 响应被截断，无法解析 JSON[/]")
            console.print(f"[yellow]建议: 文章可能太长，考虑分段处理或增加 max_tokens[/]")
            return []
        # 其他错误继续抛出
        raise

    if not isinstance(entries, list):
        entries = [entries]

    return entries


def _build_entry_markdown(entry: dict[str, Any], entry_id: str) -> str:
    """Build a complete markdown file with YAML frontmatter for an entry.

    Args:
        entry: Extracted entry dict from Claude.
        entry_id: Generated knowledge entry ID.

    Returns:
        Full markdown string ready to write to disk.
    """
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    metadata: dict[str, Any] = {
        "id": entry_id,
        "title": entry.get("title", "Untitled"),
        "domain": entry.get("domain", ""),
        "sub_domain": entry.get("sub_domain", ""),
        "type": entry.get("entry_type", "research"),
        "depth": entry.get("depth", "intermediate"),
        "confidence": 0.7,
        "status": "draft",
        "tags": entry.get("tags", []),
        "created": now,
        "updated": now,
        "related_topics": entry.get("related_topics", []),
    }

    # Build the body content
    body_parts: list[str] = []

    question = entry.get("question", "")
    if question:
        body_parts.append(f"## 问题\n\n{question}\n")

    analysis = entry.get("analysis", "")
    if analysis:
        body_parts.append(f"## 分析\n\n{analysis}\n")

    insights = entry.get("key_insights", [])
    if insights:
        items = "\n".join(f"- {insight}" for insight in insights)
        body_parts.append(f"## 关键洞察\n\n{items}\n")

    related = entry.get("related_topics", [])
    if related:
        links = "\n".join(f"- [[{topic}]]" for topic in related)
        body_parts.append(f"## 相关主题\n\n{links}\n")

    body = "\n".join(body_parts)

    post = frontmatter.Post(body, **metadata)
    return frontmatter.dumps(post)


def ingest_file_with_quality(
    file_path: Path,
    config: ProjectConfig | None = None,
    dry_run: bool = False,
    novelty_threshold: float = 0.3,
    quality_threshold: float = 0.4,
    extra_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Ingest a file with quality assessment and deduplication.

    Enhanced pipeline: extract → assess quality → CREATE/MERGE/SKIP.

    Args:
        file_path: Path to the source file.
        config: Project configuration. Auto-loaded if None.
        dry_run: If True, preview without writing.
        novelty_threshold: Below this novelty score, entries are merged.
        quality_threshold: Below this quality score, entries are skipped.

    Returns:
        List of result dicts with quality assessment info.
    """
    from agents.quality import QualityAssessment, assess_entries, merge_into_existing

    if config is None:
        config = load_config()

    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    content = file_path.read_text(encoding="utf-8")
    if not content.strip():
        return []

    console.print("[bold blue]正在调用 Claude 提取知识条目...[/]")
    extracted = _call_claude_extract(content, config)
    console.print(f"[green]提取到 {len(extracted)} 个知识条目[/]")

    console.print("[bold blue]正在进行质量评估与去重检测...[/]")
    assessments = assess_entries(
        extracted, config,
        novelty_threshold=novelty_threshold,
        quality_threshold=quality_threshold,
    )

    results: list[dict[str, Any]] = []
    for assessment in assessments:
        entry = assessment.entry
        title = entry.get("title", "untitled")
        entry_id = generate_id(title)

        result: dict[str, Any] = {
            "id": entry_id,
            "title": title,
            "type": entry.get("entry_type", "research"),
            "domain": entry.get("domain", ""),
            "action": assessment.action,
            "novelty_score": assessment.novelty_score,
            "quality_score": assessment.quality_score,
            "reason": assessment.reason,
        }

        if assessment.action == "create":
            # Inject extra_tags (e.g. RSS source tags) into the entry
            if extra_tags:
                existing = entry.get("tags", [])
                entry["tags"] = list(dict.fromkeys(existing + extra_tags))

            entry_type = entry.get("entry_type", "research")
            try:
                target_dir_name = get_entry_dir(entry_type)
            except ValueError:
                target_dir_name = "05-Research"
            target_dir = config.vault_path / target_dir_name
            target_file = target_dir / f"{entry_id}.md"
            result["target"] = str(target_file)

            if dry_run:
                result["status"] = "dry-run"
                _preview_entry(entry, entry_id, target_file)
            else:
                markdown_content = _build_entry_markdown(entry, entry_id)
                target_dir.mkdir(parents=True, exist_ok=True)
                target_file.write_text(markdown_content, encoding="utf-8")
                result["status"] = "created"
                console.print(f"  [green]已创建:[/] {target_file}")

        elif assessment.action == "merge":
            result["merge_target_id"] = assessment.merge_target_id
            if dry_run:
                result["status"] = "dry-run"
                console.print(
                    f"  [yellow]将合并:[/] {title} → {assessment.merge_target_id}"
                )
            elif assessment.merge_target_path:
                merge_result = merge_into_existing(entry, assessment.merge_target_path, config)
                result["status"] = "merged"
                result["merge_info"] = merge_result
                console.print(
                    f"  [yellow]已合并:[/] {title} → {assessment.merge_target_id}"
                )
            else:
                result["status"] = "skipped"
                result["reason"] = "合并目标文件路径不可用"

        else:  # skip
            result["status"] = "skipped"
            console.print(f"  [dim]已跳过:[/] {title} ({assessment.reason})")

        results.append(result)

    # Summary
    created = sum(1 for r in results if r.get("action") == "create")
    merged = sum(1 for r in results if r.get("action") == "merge")
    skipped = sum(1 for r in results if r.get("action") == "skip")
    console.print(
        f"\n[bold]质量评估结果:[/] "
        f"[green]创建 {created}[/] | [yellow]合并 {merged}[/] | [dim]跳过 {skipped}[/]"
    )

    return results


def ingest_file(
    file_path: Path,
    config: ProjectConfig | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Ingest a markdown file and extract structured knowledge entries.

    Args:
        file_path: Path to the source markdown file.
        config: Project configuration. Auto-loaded if None.
        dry_run: If True, preview entries without writing to disk.

    Returns:
        List of created entry metadata dicts.

    Raises:
        FileNotFoundError: If the source file does not exist.
        RuntimeError: If extraction or writing fails.
    """
    if config is None:
        config = load_config()

    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    console.print(f"[bold blue]正在读取文件:[/] {file_path}")
    content = file_path.read_text(encoding="utf-8")

    if not content.strip():
        console.print("[yellow]文件内容为空，跳过处理。[/]")
        return []

    console.print("[bold blue]正在调用 Claude 提取知识条目...[/]")
    extracted = _call_claude_extract(content, config)
    console.print(f"[green]提取到 {len(extracted)} 个知识条目[/]")

    results: list[dict[str, Any]] = []

    for entry in extracted:
        title = entry.get("title", "untitled")
        entry_id = generate_id(title)
        entry_type = entry.get("entry_type", "research")

        try:
            target_dir_name = get_entry_dir(entry_type)
        except ValueError:
            console.print(
                f"[yellow]未知类型 '{entry_type}'，归入 05-Research[/]"
            )
            target_dir_name = "05-Research"

        target_dir = config.vault_path / target_dir_name
        target_file = target_dir / f"{entry_id}.md"

        markdown_content = _build_entry_markdown(entry, entry_id)

        result: dict[str, Any] = {
            "id": entry_id,
            "title": title,
            "type": entry_type,
            "domain": entry.get("domain", ""),
            "target": str(target_file),
        }

        if dry_run:
            result["status"] = "dry-run"
            _preview_entry(entry, entry_id, target_file)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file.write_text(markdown_content, encoding="utf-8")
            result["status"] = "created"
            console.print(f"  [green]已创建:[/] {target_file}")

        results.append(result)

    return results


def _preview_entry(
    entry: dict[str, Any],
    entry_id: str,
    target_path: Path,
) -> None:
    """Display a rich preview of an entry that would be created."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")

    table.add_row("ID", entry_id)
    table.add_row("标题", entry.get("title", ""))
    table.add_row("类型", entry.get("entry_type", ""))
    table.add_row("域", entry.get("domain", ""))
    table.add_row("子域", entry.get("sub_domain", ""))
    table.add_row("深度", entry.get("depth", ""))
    table.add_row("目标路径", str(target_path))

    insights = entry.get("key_insights", [])
    if insights:
        table.add_row("洞察", "\n".join(f"  - {i}" for i in insights))

    tags = entry.get("tags", [])
    if tags:
        table.add_row("标签", ", ".join(tags))

    console.print(Panel(table, title="[bold]预览 (dry-run)[/]", border_style="yellow"))
