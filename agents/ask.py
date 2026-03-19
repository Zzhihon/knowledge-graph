"""RAG question-answering over the knowledge base.

Retrieves top-k entries via hybrid search, augments with 1-hop graph
context, and streams a synthesized answer from Claude with source
citations.

Features:
- Automatic continuation when response hits max_tokens
- Fallback to direct LLM answering when knowledge base has no results
- Auto-ingestion of fallback answers into the knowledge base
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.query import search

console = Console()

_SYSTEM_PROMPT = """\
你是知识库问答助手。基于以下检索到的知识条目回答问题。
引用来源时使用 [条目ID] 格式。如果知识库中没有相关信息，明确说明。
回答应准确、简洁，并整合多个条目的信息给出综合性回答。"""

_FALLBACK_SYSTEM_PROMPT = """\
你是知识库问答助手。知识库中暂无相关条目，请基于你的知识给出准确、详细的回答。
回答时注意结构清晰，分段阐述，并在适当位置给出示例。"""

_MAX_TOKENS = 8192
_MAX_CONTINUATIONS = 5
_CONTINUATION_MSG = "请继续。"


def _load_entry_content(file_path: str) -> str:
    """Read full markdown content from a knowledge entry file."""
    p = Path(file_path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _get_graph_neighbors(entry_id: str, config: ProjectConfig) -> list[str]:
    """Get 1-hop neighbor titles from the graph store."""
    try:
        from agents.graph_store import get_graph_store

        gs = get_graph_store(config)
        gs.connect()
        try:
            relations = gs.get_relations(entry_id, direction="both")
            titles: list[str] = []
            for rel in relations:
                target_id = rel["target_id"]
                entry = gs.get_entry(target_id)
                if entry:
                    titles.append(entry.get("title", target_id))
                else:
                    titles.append(target_id)
            return titles
        finally:
            gs.close()
    except Exception:
        return []


def _build_context(
    results: list[dict[str, Any]],
    config: ProjectConfig,
    use_graph: bool = True,
) -> str:
    """Assemble context from top-k search results + graph neighbors."""
    blocks: list[str] = []

    for result in results:
        entry_id = result.get("id", "")
        title = result.get("title", "")
        domain = result.get("domain", "")
        file_path = result.get("file_path", "")

        content = _load_entry_content(file_path) if file_path else ""

        neighbors_str = ""
        if use_graph and entry_id:
            neighbors = _get_graph_neighbors(entry_id, config)
            if neighbors:
                neighbors_str = ", ".join(neighbors)

        block_parts = [
            f"--- 条目: {entry_id} ---",
            f"标题: {title}",
            f"域: {domain}",
        ]
        if neighbors_str:
            block_parts.append(f"相关条目: {neighbors_str}")
        block_parts.append("")
        if content:
            block_parts.append(content)
        else:
            block_parts.append("(内容不可用)")

        blocks.append("\n".join(block_parts))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Streaming with automatic continuation
# ---------------------------------------------------------------------------

def _stream_answer(
    question: str,
    context: str | None,
    model: str,
    client: anthropic.Anthropic | None = None,
    system_prompt: str | None = None,
) -> str:
    """Stream a Claude answer to the console with automatic continuation.

    Returns the full accumulated text (used by auto-ingest).
    """
    if client is None:
        from agents.api_client import get_anthropic_client
        client, model = get_anthropic_client()

    sys_prompt = system_prompt or _SYSTEM_PROMPT

    if context:
        user_content = f"知识库上下文:\n\n{context}\n\n---\n问题: {question}"
    else:
        user_content = question

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_content},
    ]

    full_text = ""
    continuations = 0

    console.print()
    while True:
        with client.messages.stream(
            model=model,
            max_tokens=_MAX_TOKENS,
            temperature=0,
            system=sys_prompt,
            messages=messages,
        ) as stream:
            chunk_text = ""
            for text in stream.text_stream:
                console.print(text, end="", highlight=False)
                chunk_text += text
            msg = stream.get_final_message()

        full_text += chunk_text

        if msg.stop_reason != "max_tokens" or continuations >= _MAX_CONTINUATIONS:
            break

        # Continue: append assistant response + user continuation message
        continuations += 1
        console.print("\n[dim](...续传中)[/]", highlight=False)
        messages.append({"role": "assistant", "content": full_text})
        messages.append({"role": "user", "content": _CONTINUATION_MSG})

    console.print("\n")
    return full_text


def _stream_answer_sse(
    question: str,
    context: str | None,
    model: str,
    client: anthropic.Anthropic | None = None,
    system_prompt: str | None = None,
) -> Generator[dict[str, Any], None, str]:
    """SSE streaming generator with automatic continuation.

    Yields:
        {"type": "token", "data": {"text": "..."}}

    Returns (via StopIteration.value):
        The full accumulated text.
    """
    if client is None:
        from agents.api_client import get_anthropic_client
        client, model = get_anthropic_client()

    sys_prompt = system_prompt or _SYSTEM_PROMPT

    if context:
        user_content = f"知识库上下文:\n\n{context}\n\n---\n问题: {question}"
    else:
        user_content = question

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_content},
    ]

    full_text = ""
    continuations = 0

    while True:
        with client.messages.stream(
            model=model,
            max_tokens=_MAX_TOKENS,
            temperature=0,
            system=sys_prompt,
            messages=messages,
        ) as stream:
            chunk_text = ""
            for text in stream.text_stream:
                yield {"type": "token", "data": {"text": text}}
                chunk_text += text
            msg = stream.get_final_message()

        full_text += chunk_text

        if msg.stop_reason != "max_tokens" or continuations >= _MAX_CONTINUATIONS:
            break

        continuations += 1
        messages.append({"role": "assistant", "content": full_text})
        messages.append({"role": "user", "content": _CONTINUATION_MSG})

    return full_text


# ---------------------------------------------------------------------------
# Auto-ingest Q&A into knowledge base (no LLM call)
# ---------------------------------------------------------------------------

def _auto_ingest_qa(
    question: str,
    answer: str,
    config: ProjectConfig,
    entry_type: str = "research",
    depth: str = "intermediate",
    do_sync: bool = True,
) -> str | None:
    """Write a Q&A pair directly as a knowledge entry. No LLM extraction.

    Returns entry_id on success, None on failure.
    """
    try:
        import frontmatter as fm

        from agents.query import _detect_domains
        from agents.utils import generate_id, get_entry_dir

        # Detect domains from question
        domains = _detect_domains(question)
        domain = domains[0] if domains else "general"

        # Generate ID and target path
        # Use first ~60 chars of question as title
        title = question[:60].rstrip("？?。. ")
        entry_id = generate_id(title)
        target_dir = config.vault_path / get_entry_dir(entry_type)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / f"{entry_id}.md"

        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        metadata: dict[str, Any] = {
            "id": entry_id,
            "title": title,
            "domain": domain,
            "sub_domain": "",
            "type": entry_type,
            "depth": depth,
            "confidence": 0.6,
            "status": "draft",
            "tags": ["source:fallback", f"domain:{domain}"],
            "created": now,
            "updated": now,
            "related_topics": [],
        }

        body_parts = [
            f"## 问题\n\n{question}\n",
            f"## 回答\n\n{answer}\n",
        ]
        body = "\n".join(body_parts)

        post = fm.Post(body, **metadata)
        target_file.write_text(fm.dumps(post), encoding="utf-8")

        if do_sync:
            try:
                from agents.sync_engine import incremental_sync
                incremental_sync(config)
            except Exception:
                pass  # sync failure is non-fatal

        return entry_id
    except Exception as exc:
        console.print(f"[yellow]自动写入失败: {exc}[/]")
        return None


def _print_sources(results: list[dict[str, Any]]) -> None:
    """Print a source reference table after the answer."""
    table = Table(title="来源条目", show_lines=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("条目ID", style="cyan", max_width=45)
    table.add_column("标题", style="bold", max_width=40)
    table.add_column("域", style="green", width=16)
    table.add_column("分数", style="yellow", width=8, justify="right")

    for idx, result in enumerate(results, 1):
        table.add_row(
            str(idx),
            result.get("id", ""),
            result.get("title", ""),
            result.get("domain", ""),
            str(result.get("score", "")),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask(
    question: str,
    top_k: int = 5,
    domain: str | None = None,
    use_graph: bool = True,
    enable_fallback: bool = True,
    config: ProjectConfig | None = None,
) -> None:
    """RAG question-answering: retrieve + graph context + Claude streaming.

    Args:
        question: The user's natural language question.
        top_k: Number of entries to retrieve.
        domain: Optional domain filter for retrieval.
        use_graph: Whether to include 1-hop graph neighbor context.
        enable_fallback: Whether to use fallback when no results found.
        config: Project configuration. Auto-loaded if None.
    """
    if config is None:
        config = load_config()

    # Step 1: Retrieve top-k entries
    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain

    console.print(f"[dim]正在检索相关条目 (top-{top_k})...[/]")
    results = search(
        query=question,
        filters=filters if filters else None,
        top_k=top_k,
        config=config,
    )

    if not results:
        fb = config.fallback
        if enable_fallback and fb.enabled:
            # Fallback: direct LLM answer + auto-ingest
            console.print(Panel(
                "[yellow]知识库中未找到相关条目，正在使用 Fallback 直接回答...[/]",
                title=f"问题: {question}",
                border_style="yellow",
            ))

            from agents.api_client import get_fallback_client
            client, model = get_fallback_client(fb.key_index)

            console.print(Panel(f"[bold]{question}[/]", title="问题 (Fallback)", border_style="magenta"))
            full_text = _stream_answer(
                question, None, model,
                client=client,
                system_prompt=_FALLBACK_SYSTEM_PROMPT,
            )

            if fb.auto_ingest and full_text.strip():
                entry_id = _auto_ingest_qa(
                    question, full_text, config,
                    entry_type=fb.entry_type,
                    depth=fb.depth,
                    do_sync=fb.auto_sync,
                )
                if entry_id:
                    console.print(Panel(
                        f"[green]已自动创建知识条目: {entry_id}[/]",
                        border_style="green",
                    ))
            return

        # No fallback — original behavior
        console.print(Panel(
            "[yellow]未找到相关知识条目。[/]\n"
            "建议:\n"
            "  - 尝试不同的关键词\n"
            "  - 检查索引是否已构建 (kg sync)\n"
            "  - 放宽筛选条件",
            title=f"问题: {question}",
            border_style="yellow",
        ))
        return

    console.print(f"[dim]已检索到 {len(results)} 个相关条目，正在构建上下文...[/]")

    # Step 2: Build context with graph augmentation
    context = _build_context(results, config, use_graph=use_graph)

    # Step 3: Stream answer from Claude (with continuation)
    console.print(Panel(f"[bold]{question}[/]", title="问题", border_style="blue"))
    _stream_answer(question, context, config.agent.model)

    # Step 4: Print source references
    _print_sources(results)


def ask_stream(
    question: str,
    top_k: int = 5,
    domain: str | None = None,
    use_graph: bool = True,
    enable_fallback: bool = True,
    config: ProjectConfig | None = None,
) -> Any:
    """Streaming RAG generator: yields dicts for SSE consumption.

    Yields:
        {"type": "status", "data": {"message": "..."}}
        {"type": "token", "data": {"text": "..."}}
        {"type": "sources", "data": {"sources": [...]}}
        {"type": "ingested", "data": {"entry_id": "..."}}
        {"type": "done", "data": {}}
    """
    if config is None:
        config = load_config()

    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain

    results = search(
        query=question,
        filters=filters if filters else None,
        top_k=top_k,
        config=config,
    )

    if not results:
        fb = config.fallback
        if enable_fallback and fb.enabled:
            yield {"type": "status", "data": {"message": "知识库无结果，Fallback 直接回答..."}}

            from agents.api_client import get_fallback_client
            client, model = get_fallback_client(fb.key_index)

            gen = _stream_answer_sse(
                question, None, model,
                client=client,
                system_prompt=_FALLBACK_SYSTEM_PROMPT,
            )

            # Consume the generator, collecting full_text from StopIteration
            full_text = ""
            try:
                while True:
                    event = next(gen)
                    yield event
                    if event["type"] == "token":
                        full_text += event["data"]["text"]
            except StopIteration as stop:
                full_text = stop.value or full_text

            if fb.auto_ingest and full_text.strip():
                entry_id = _auto_ingest_qa(
                    question, full_text, config,
                    entry_type=fb.entry_type,
                    depth=fb.depth,
                    do_sync=fb.auto_sync,
                )
                if entry_id:
                    yield {"type": "ingested", "data": {"entry_id": entry_id}}

            yield {"type": "sources", "data": {"sources": []}}
            yield {"type": "done", "data": {}}
            return

        # No fallback
        yield {"type": "token", "data": {"text": "未找到相关知识条目，请尝试不同的关键词。"}}
        yield {"type": "sources", "data": {"sources": []}}
        yield {"type": "done", "data": {}}
        return

    context = _build_context(results, config, use_graph=use_graph)

    from agents.api_client import get_anthropic_client
    client, model = get_anthropic_client()

    gen = _stream_answer_sse(question, context, model, client=client)
    try:
        while True:
            yield next(gen)
    except StopIteration:
        pass

    sources = [
        {
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "domain": r.get("domain", ""),
            "score": r.get("score", 0),
        }
        for r in results
    ]
    yield {"type": "sources", "data": {"sources": sources}}
    yield {"type": "done", "data": {}}
