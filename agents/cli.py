"""Main CLI entry point for the Knowledge Graph tool.

Provides commands: ingest, query, ask, review, stats, init, link, quiz,
radar, export, history, sync, graph, diff, backlinks.
Uses Click for argument parsing and Rich for formatted output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="kg-cli")
def cli() -> None:
    """Knowledge Graph CLI - 知识图谱管理工具

    管理和查询你的个人知识图谱。支持知识提取、语义检索、
    定期审查等功能。
    """


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="预览模式，不实际写入文件")
@click.option(
    "--quality-check/--no-quality-check",
    default=True,
    help="启用质量评估与去重 (默认启用)",
)
@click.option("-r", "--recursive", is_flag=True, help="递归扫描子目录")
@click.option("--workers", type=int, default=3, help="并行线程数 (默认: 3)")
def ingest(
    path: Path,
    dry_run: bool,
    quality_check: bool,
    recursive: bool,
    workers: int,
) -> None:
    """从对话/文档中提取知识条目。

    支持单文件 (.md/.txt/.pdf) 或目录批量导入。
    使用 Claude 提取结构化知识条目，并写入知识库对应目录。

    PATH: 待处理的文件或目录路径
    """
    if path.is_dir():
        from agents.batch_ingest import ingest_directory

        try:
            batch_result = ingest_directory(
                dir_path=path,
                dry_run=dry_run,
                quality_check=quality_check,
                max_workers=workers,
                recursive=recursive,
            )
            console.print(
                f"\n[bold green]批量导入完成:[/] "
                f"处理 {batch_result.processed}/{batch_result.total_files} 个文件 | "
                f"[green]创建 {batch_result.entries_created}[/] | "
                f"[yellow]合并 {batch_result.entries_merged}[/] | "
                f"[dim]跳过 {batch_result.entries_skipped}[/]"
            )
            if batch_result.errors:
                console.print(f"[red]错误: {len(batch_result.errors)} 个文件处理失败[/]")
                for err in batch_result.errors:
                    console.print(f"  [red]{err['file']}: {err['error']}[/]")
        except RuntimeError as exc:
            console.print(f"[red]批量处理错误: {exc}[/]")
            sys.exit(1)
        return

    # Single file
    if quality_check:
        from agents.ingest import ingest_file_with_quality

        try:
            results = ingest_file_with_quality(file_path=path, dry_run=dry_run)
            if results:
                created = sum(1 for r in results if r.get("action") == "create")
                merged = sum(1 for r in results if r.get("action") == "merge")
                skipped = sum(1 for r in results if r.get("action") == "skip")
                action = "预览" if dry_run else "处理"
                console.print(
                    f"\n[bold green]完成: {action}了 {len(results)} 个知识条目[/] "
                    f"([green]创建 {created}[/] | [yellow]合并 {merged}[/] | "
                    f"[dim]跳过 {skipped}[/])"
                )
            else:
                console.print("[yellow]未提取到任何知识条目。[/]")
        except FileNotFoundError as exc:
            console.print(f"[red]文件错误: {exc}[/]")
            sys.exit(1)
        except RuntimeError as exc:
            console.print(f"[red]处理错误: {exc}[/]")
            sys.exit(1)
    else:
        from agents.ingest import ingest_file

        try:
            results = ingest_file(file_path=path, dry_run=dry_run)
            if results:
                action = "预览" if dry_run else "创建"
                console.print(
                    f"\n[bold green]完成: {action}了 {len(results)} 个知识条目[/]"
                )
            else:
                console.print("[yellow]未提取到任何知识条目。[/]")
        except FileNotFoundError as exc:
            console.print(f"[red]文件错误: {exc}[/]")
            sys.exit(1)
        except RuntimeError as exc:
            console.print(f"[red]处理错误: {exc}[/]")
            sys.exit(1)


@cli.command()
@click.argument("question", required=False)
@click.option("--build", is_flag=True, help="重建向量索引")
@click.option("--domain", type=str, default=None, help="按知识域筛选")
@click.option("--type", "entry_type", type=str, default=None, help="按条目类型筛选")
@click.option("--depth", type=str, default=None, help="按深度层级筛选")
@click.option("--status", type=str, default=None, help="按状态筛选")
@click.option("--top-k", type=int, default=5, help="返回结果数量 (默认: 5)")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["rich", "json"]),
    default="rich",
    help="输出格式: rich(终端表格) 或 json(机器可读)",
)
def query(
    question: str | None,
    build: bool,
    domain: str | None,
    entry_type: str | None,
    depth: str | None,
    status: str | None,
    top_k: int,
    output_format: str,
) -> None:
    """语义检索知识库。

    输入自然语言问题，在知识库中进行语义搜索，返回最相关的条目。

    QUESTION: 查询问题（与 --build 互斥）
    """
    if build:
        from agents.query import build_index

        count = build_index()
        console.print(f"[green]索引构建完成，共 {count} 个条目。[/]")
        console.print("[dim]提示: 推荐使用 `kg sync` 同时构建向量索引和图数据库。[/]")
        return

    from agents.query import print_results, search

    # In JSON mode, redirect query module's Rich console to stderr
    # so diagnostic messages don't contaminate the JSON output on stdout.
    if output_format == "json":
        import agents.query as _qmod

        _qmod.console = Console(stderr=True)

    if not question:
        console.print("[red]请提供查询问题，或使用 --build 构建索引。[/]")
        console.print("用法: kg query '你的问题' [选项]")
        console.print("[dim]提示: 首次使用前请先运行 `kg sync` 构建索引。[/]")
        sys.exit(1)

    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain
    if entry_type:
        filters["type"] = entry_type
    if depth:
        filters["depth"] = depth
    if status:
        filters["status"] = status

    results = search(
        query=question,
        filters=filters if filters else None,
        top_k=top_k,
    )

    if output_format == "json":
        import json

        print(json.dumps(results, ensure_ascii=False))
    else:
        print_results(results, question)


@cli.command()
@click.argument("question")
@click.option("--top-k", type=int, default=5, help="检索条目数量 (默认: 5)")
@click.option("--domain", type=str, default=None, help="限定知识域")
@click.option("--no-graph", is_flag=True, help="禁用图上下文增强")
def ask(question: str, top_k: int, domain: str | None, no_graph: bool) -> None:
    """RAG 知识问答 — 检索相关条目并生成综合回答。

    输入自然语言问题，检索知识库中最相关的条目，结合图关系上下文，
    通过 Claude 生成综合性回答并标注来源。

    QUESTION: 你的问题
    """
    from agents.ask import ask as do_ask

    try:
        do_ask(
            question=question,
            top_k=top_k,
            domain=domain,
            use_graph=not no_graph,
        )
    except RuntimeError as exc:
        console.print(f"[red]问答错误: {exc}[/]")
        sys.exit(1)


@cli.command()
@click.option("--domain", type=str, default=None, help="审查特定知识域")
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="将报告写入文件",
)
@click.option("--report", is_flag=True, help="生成完整 markdown 报告")
def review(domain: str | None, output: Path | None, report: bool) -> None:
    """审查知识库健康状态。

    扫描所有条目，检查过期、低置信度、草稿状态等问题，
    并进行域覆盖分析。
    """
    from agents.review import generate_review_report, print_review_summary

    if report or output:
        report_text = generate_review_report(
            domain_filter=domain,
            output_path=output,
        )
        if not output:
            # Print report to stdout when no output file specified
            console.print(report_text)
    else:
        print_review_summary(domain_filter=domain)


@cli.command()
def stats() -> None:
    """显示知识库统计信息。

    展示各知识域、条目类型、深度层级的分布统计。
    """
    from agents.config import load_config
    from agents.utils import load_entries

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    entries = load_entries(config.vault_path)

    if not entries:
        console.print(Panel(
            "[yellow]知识库为空。[/]\n"
            "使用 `kg ingest <file>` 开始添加知识条目。",
            title="知识库统计",
            border_style="yellow",
        ))
        return

    # Aggregate statistics
    domain_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    depth_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}

    for entry in entries:
        meta = entry["metadata"]
        d = meta.get("domain", "unknown")
        t = meta.get("type", "unknown")
        dp = meta.get("depth", "unknown")
        s = meta.get("status", "unknown")

        domains = d if isinstance(d, list) else [d]
        for dk in domains:
            domain_counts[dk] = domain_counts.get(dk, 0) + 1
        type_counts[t] = type_counts.get(t, 0) + 1
        depth_counts[dp] = depth_counts.get(dp, 0) + 1
        status_counts[s] = status_counts.get(s, 0) + 1

    # Summary panel
    console.print(Panel(
        f"[bold]总条目数: {len(entries)}[/]\n"
        f"知识域数: {len(domain_counts)}  "
        f"类型数: {len(type_counts)}  "
        f"深度层级: {len(depth_counts)}",
        title="知识库统计",
        border_style="blue",
    ))

    # Domain distribution table
    domain_table = Table(title="知识域分布", show_lines=True)
    domain_table.add_column("域", style="bold cyan")
    domain_table.add_column("条目数", justify="right", style="green")
    domain_table.add_column("占比", justify="right")

    for domain_key, count in sorted(
        domain_counts.items(), key=lambda x: x[1], reverse=True
    ):
        domain_cfg = config.get_domain(domain_key)
        label = f"{domain_cfg.icon} {domain_cfg.label}" if domain_cfg else domain_key
        pct = count / len(entries) * 100
        domain_table.add_row(label, str(count), f"{pct:.1f}%")
    console.print(domain_table)

    # Type distribution table
    type_table = Table(title="条目类型分布", show_lines=True)
    type_table.add_column("类型", style="bold magenta")
    type_table.add_column("条目数", justify="right", style="green")

    for type_key, count in sorted(
        type_counts.items(), key=lambda x: x[1], reverse=True
    ):
        type_cfg = config.get_entry_type(type_key)
        label = type_cfg.label if type_cfg else type_key
        type_table.add_row(label, str(count))
    console.print(type_table)

    # Depth distribution table
    depth_table = Table(title="深度层级分布", show_lines=True)
    depth_table.add_column("层级", style="bold yellow")
    depth_table.add_column("条目数", justify="right", style="green")

    for depth_key, count in sorted(
        depth_counts.items(), key=lambda x: x[1], reverse=True
    ):
        label = config.depth_levels.get(depth_key, depth_key)
        depth_table.add_row(label, str(count))
    console.print(depth_table)

    # Status distribution
    status_table = Table(title="状态分布", show_lines=True)
    status_table.add_column("状态", style="bold")
    status_table.add_column("条目数", justify="right", style="green")

    for status_key, count in sorted(
        status_counts.items(), key=lambda x: x[1], reverse=True
    ):
        style = "green" if status_key == "validated" else (
            "yellow" if status_key == "draft" else "white"
        )
        status_table.add_row(f"[{style}]{status_key}[/]", str(count))
    console.print(status_table)


@cli.command()
def init() -> None:
    """初始化/验证知识库目录结构。

    检查并创建必要的目录和索引文件。
    """
    from agents.config import load_config
    from agents.utils import get_entry_dir

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    vault = config.vault_path
    console.print(f"[bold blue]知识库根目录:[/] {vault}\n")

    # Required directories
    required_dirs = [
        "00-Index",
        "01-Principles",
        "02-Patterns",
        "03-Debug",
        "04-Architecture",
        "05-Research",
        "06-Team",
        "07-Projects",
        "08-Problems",
        "agents",
        "Assets",
        "Templates",
    ]

    # Optional directories created by the system
    system_dirs = [
        "indexes",
        "indexes/qdrant",
        "indexes/surrealdb",
    ]

    all_dirs = required_dirs + system_dirs
    created = 0
    verified = 0

    table = Table(show_header=True, show_lines=False)
    table.add_column("目录", style="cyan")
    table.add_column("状态", width=12)

    for dir_name in all_dirs:
        dir_path = vault / dir_name
        if dir_path.is_dir():
            table.add_row(dir_name, "[green]已存在[/]")
            verified += 1
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
            table.add_row(dir_name, "[yellow]已创建[/]")
            created += 1

    console.print(table)

    # Verify config.yaml exists
    config_file = vault / "config.yaml"
    if config_file.is_file():
        console.print(f"\n[green]config.yaml 已验证[/]")
    else:
        console.print(f"\n[red]config.yaml 缺失![/]")

    # Summary
    console.print(Panel(
        f"已验证: {verified} 个目录\n"
        f"已创建: {created} 个目录\n"
        f"知识库名称: {config.name}\n"
        f"作者: {config.author}",
        title="初始化完成",
        border_style="green",
    ))


@cli.command()
@click.option("--top-n", type=int, default=10, help="返回建议链接数量 (默认: 10)")
@click.option(
    "--threshold", type=float, default=0.75, help="最低相似度阈值 (默认: 0.75)"
)
def link(top_n: int, threshold: float) -> None:
    """发现知识条目之间的潜在链接。

    基于内容语义相似度，在条目间查找尚未建立的关联，
    并以表格形式展示建议的链接。
    """
    from agents.link import find_links, print_suggestions

    suggestions = find_links(top_n=top_n, threshold=threshold)
    print_suggestions(suggestions)

    if suggestions:
        console.print(
            f"\n[dim]共发现 {len(suggestions)} 条潜在链接。"
            f"请在 Obsidian 中手动建立关联。[/]"
        )


@cli.command()
@click.option("--domain", type=str, default=None, help="按知识域筛选复习条目")
@click.option("--count", type=int, default=3, help="待复习条目数量 (默认: 3)")
@click.option(
    "--interactive", "-i", is_flag=True,
    help="交互模式：显示答案 + 自评 + 写回 frontmatter",
)
def quiz(domain: str | None, count: int, interactive: bool) -> None:
    """间隔重复复习引擎。

    根据复习日期、创建时间和置信度评分，选择最需要复习的条目，
    并展示问题内容供回顾。使用 -i 进入交互模式完成复习闭环。
    """
    from agents.quiz import print_quiz_question, select_review_entries

    entries = select_review_entries(domain=domain, count=count)
    if not entries:
        console.print(Panel(
            "[green]暂无需要复习的条目。[/]\n"
            "所有知识条目均已按计划复习。",
            title="间隔重复",
            border_style="green",
        ))
        return

    console.print(f"\n[bold blue]待复习条目: {len(entries)} 条[/]\n")

    if not interactive:
        for idx, entry in enumerate(entries, 1):
            console.print(f"[bold]--- 第 {idx}/{len(entries)} 题 ---[/]\n")
            print_quiz_question(entry)
            console.print()
        return

    # Interactive mode: show answer → self-assess → update frontmatter
    from agents.quiz import print_quiz_answer, update_review_schedule

    for idx, entry in enumerate(entries, 1):
        console.print(f"[bold]--- 第 {idx}/{len(entries)} 题 ---[/]\n")
        print_quiz_question(entry)

        click.pause("按任意键查看答案...")
        print_quiz_answer(entry)

        response = click.prompt(
            "\n自评",
            type=click.Choice(["confident", "partial", "forgot"], case_sensitive=False),
        )

        entry_path = Path(entry["metadata"].get("file_path", entry.get("path", "")))
        update_review_schedule(entry_path, response.lower())
        console.print(f"[green]已更新: {response}[/]\n")


@cli.command()
@click.option("--domain", type=str, default=None, help="仅显示指定知识域的强度")
def radar(domain: str | None) -> None:
    """展示知识域强度雷达图。

    计算各知识域的覆盖率、深度、新鲜度和置信度指标，
    以表格形式展示综合评分。
    """
    from agents.radar import (
        compute_all_strengths,
        compute_domain_strength,
        print_radar,
    )

    if domain:
        from agents.config import load_config
        from agents.utils import load_entries

        config = load_config()
        entries = load_entries(config.vault_path)
        strength = compute_domain_strength(domain, entries, config)
        strengths = {domain: strength}
        print_radar(strengths, config=config)
    else:
        strengths = compute_all_strengths()
        print_radar(strengths)


@cli.command(name="export")
@click.option(
    "--format",
    "format_name",
    type=click.Choice(["blog", "guide", "onboarding"]),
    required=True,
    help="导出格式: blog(博客), guide(学习指南), onboarding(入职文档)",
)
@click.option("--domain", type=str, default=None, help="按知识域筛选")
@click.option("--output", type=click.Path(path_type=Path), default=None, help="输出文件路径")
def export_cmd(format_name: str, domain: str | None, output: Path | None) -> None:
    """导出知识条目为格式化文档。

    支持导出为技术博客、学习指南或团队入职文档。
    """
    from agents.config import load_config
    from agents.export_entries import (
        export_blog,
        export_onboarding,
        export_study_guide,
        write_export,
    )
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    if not entries:
        console.print("[yellow]知识库为空，无法导出。[/]")
        sys.exit(1)

    if format_name == "blog":
        content = export_blog(entries, domain=domain)
    elif format_name == "guide":
        content = export_study_guide(entries, domain=domain)
    elif format_name == "onboarding":
        content = export_onboarding(entries, team=domain)
    else:
        console.print(f"[red]未知导出格式: {format_name}[/]")
        sys.exit(1)

    result_path = write_export(content, output_path=output, format_name=format_name)
    console.print(f"[bold green]导出完成:[/] {result_path}")


@cli.command()
@click.argument("entry_id")
def history(entry_id: str) -> None:
    """展示知识条目的演进历史。

    追踪 supersedes 链，显示从最早版本到最新版本的完整演进时间线。

    ENTRY_ID: 知识条目 ID (如 ke-20250226-xxx)
    """
    from agents.config import load_config
    from agents.history import build_supersedes_chain, find_related_evolution, print_history
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    if not entries:
        console.print("[yellow]知识库为空。[/]")
        sys.exit(1)

    chain = build_supersedes_chain(entry_id, entries)

    if not chain:
        # Try to find the entry and show related evolution instead
        target = None
        for e in entries:
            if str(e["metadata"].get("id", "")).lower() == entry_id.lower():
                target = e
                break

        if target is None:
            console.print(f"[red]未找到条目: {entry_id}[/]")
            sys.exit(1)

        related = find_related_evolution(target, entries)
        if related:
            console.print(
                "[yellow]未发现 supersedes 链，显示相关演进条目:[/]\n"
            )
            print_history([target] + related, highlight_id=entry_id)
        else:
            console.print(f"[yellow]条目 {entry_id} 无演进历史记录。[/]")
        return

    print_history(chain, highlight_id=entry_id)


@cli.command()
@click.option("--full", is_flag=True, help="强制全量重建（跳过增量检测）")
def sync(full: bool) -> None:
    """同步知识库到 Qdrant 向量索引 + SurrealDB 图数据库。

    默认增量同步：只处理新增、修改、删除的条目。
    使用 --full 强制全量重建。
    """
    from agents.config import load_config
    from agents.utils import load_entries

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    entries = load_entries(config.vault_path)
    if not entries:
        console.print("[yellow]知识库为空，无需同步。[/]")
        return

    valid_entries = _prepare_entries(entries)
    if not valid_entries:
        console.print("[yellow]未找到有效的知识条目。[/]")
        return

    if full:
        _full_sync(config, valid_entries)
    else:
        _incremental_sync(config, valid_entries)


def _prepare_entries(entries: list) -> list:
    """过滤并准备有效条目，注入 content_hash 和 file_path."""
    from agents.utils import compute_content_hash

    valid: list = []
    for entry in entries:
        meta = entry["metadata"]
        entry_id = meta.get("id", "")
        if not entry_id:
            continue
        entry_copy = dict(entry)
        entry_copy["metadata"] = dict(meta)
        entry_copy["metadata"]["file_path"] = str(entry["path"])
        entry_copy["metadata"]["content_hash"] = compute_content_hash(entry["path"])
        valid.append(entry_copy)
    return valid


def _entry_to_text(entry: dict) -> str:
    """将条目转换为用于 embedding 的文本."""
    meta = entry["metadata"]
    title = meta.get("title", "")
    tags = meta.get("tags", [])
    if isinstance(tags, list):
        tags_str = ", ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)
    return f"{title}\n{tags_str}\n\n{entry['content']}"


def _full_sync(config, valid_entries: list) -> None:
    """全量重建：drop + recreate 所有索引."""
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    console.print(f"[bold blue]全量同步 {len(valid_entries)} 个条目...[/]\n")

    texts = [_entry_to_text(e) for e in valid_entries]

    # Step 1: Qdrant
    console.print("[bold]1/2 Qdrant 向量索引[/]")
    console.print("[dim]  正在生成嵌入向量...[/]")
    embeddings = embed_texts(texts)

    with get_vector_store(config) as store:
        store.init_collection()
        vec_count = store.upsert_entries(valid_entries, embeddings)
    console.print(f"[green]  {vec_count} 个条目已写入 Qdrant[/]\n")

    # Step 2: SurrealDB
    console.print("[bold]2/2 SurrealDB 图数据库[/]")
    with get_graph_store(config) as gs:
        gs.init_schema()
        result = gs.sync_entries_and_relations(valid_entries)
    console.print(
        f"[green]  {result['entries_synced']} 个节点, "
        f"{result['edges_created']} 条边已创建, "
        f"{result['edges_removed']} 条过期边已移除[/]\n"
    )

    # Summary
    summary_table = Table(title="全量同步摘要", show_lines=True)
    summary_table.add_column("引擎", style="bold cyan")
    summary_table.add_column("操作", style="green")
    summary_table.add_column("数量", justify="right")
    summary_table.add_row("Qdrant", "向量入库", str(vec_count))
    summary_table.add_row("SurrealDB", "节点同步", str(result["entries_synced"]))
    summary_table.add_row("SurrealDB", "边创建", str(result["edges_created"]))
    summary_table.add_row("SurrealDB", "过期边移除", str(result["edges_removed"]))
    console.print(summary_table)


def _incremental_sync(config, valid_entries: list) -> None:
    """增量同步：只处理新增、修改、删除的条目."""
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    # Build disk state: {entry_id: entry}
    disk_map: dict[str, dict] = {}
    for entry in valid_entries:
        eid = entry["metadata"]["id"]
        disk_map[eid] = entry

    with get_vector_store(config) as store:
        # ensure_collection returns True if collection already existed
        existed = store.ensure_collection()

        if not existed:
            # 新 collection → 首次全量
            console.print("[yellow]首次同步，执行全量构建...[/]\n")
            store.close()
            _full_sync(config, valid_entries)
            return

        # Scroll existing payloads
        stored_payloads = store.get_all_payloads()

        # 检测旧格式（无 content_hash）
        if stored_payloads:
            sample = next(iter(stored_payloads.values()))
            if not sample.get("content_hash"):
                console.print(
                    "[yellow]检测到旧格式索引（缺少 content_hash），"
                    "自动执行全量重建...[/]\n"
                )
                store.close()
                _full_sync(config, valid_entries)
                return

        # Diff: new / changed / deleted / unchanged
        stored_ids = set(stored_payloads.keys())
        disk_ids = set(disk_map.keys())

        new_ids = disk_ids - stored_ids
        deleted_ids = stored_ids - disk_ids
        common_ids = disk_ids & stored_ids

        changed_ids: set[str] = set()
        unchanged_ids: set[str] = set()
        for eid in common_ids:
            disk_hash = disk_map[eid]["metadata"]["content_hash"]
            stored_hash = stored_payloads[eid].get("content_hash", "")
            if disk_hash != stored_hash:
                changed_ids.add(eid)
            else:
                unchanged_ids.add(eid)

        # Check if nothing changed
        if not new_ids and not changed_ids and not deleted_ids:
            console.print(
                Panel(
                    f"[green]所有 {len(unchanged_ids)} 个条目均为最新，无需操作。[/]",
                    title="增量同步",
                    border_style="green",
                )
            )
            return

        # Report diff
        console.print(f"[bold blue]增量同步检测结果:[/]")
        console.print(f"  新增: [green]{len(new_ids)}[/]")
        console.print(f"  修改: [yellow]{len(changed_ids)}[/]")
        console.print(f"  删除: [red]{len(deleted_ids)}[/]")
        console.print(f"  未变: [dim]{len(unchanged_ids)}[/]\n")

        # Step 1: Qdrant — embed + upsert new/changed, delete removed
        to_process_ids = new_ids | changed_ids
        to_process = [disk_map[eid] for eid in to_process_ids]
        vec_upserted = 0
        vec_deleted = 0

        if to_process:
            console.print("[bold]1/2 Qdrant 向量索引[/]")
            texts = [_entry_to_text(e) for e in to_process]
            console.print(f"[dim]  正在生成 {len(texts)} 条嵌入向量...[/]")
            embeddings = embed_texts(texts)
            vec_upserted = store.upsert_entries(to_process, embeddings)
            console.print(f"[green]  {vec_upserted} 个条目已更新[/]")

        if deleted_ids:
            vec_deleted = store.delete_points(list(deleted_ids))
            console.print(f"[green]  {vec_deleted} 个条目已删除[/]")

        if not to_process and not deleted_ids:
            console.print("[bold]1/2 Qdrant 向量索引[/]")
            console.print("[dim]  无需更新[/]")

        console.print()

    # Step 2: SurrealDB — incremental graph sync + diff recording
    console.print("[bold]2/2 SurrealDB 图数据库[/]")
    changed_entries = [disk_map[eid] for eid in (new_ids | changed_ids)]

    diff_count = 0
    with get_graph_store(config) as gs:
        gs.init_schema()
        graph_result = gs.sync_partial(
            changed_entries=changed_entries,
            deleted_ids=list(deleted_ids),
            all_known_ids=disk_ids,
        )

        # Record diffs for evolution tracking
        from agents.diff_store import DiffStore

        ds = DiffStore(gs)
        ds.init_schema()

        for eid in new_ids:
            entry = disk_map[eid]
            ds.record_change(
                eid, "created", "", entry["content"],
                "", entry["metadata"]["content_hash"],
            )
            diff_count += 1

        for eid in changed_ids:
            entry = disk_map[eid]
            old_content = ds.get_latest_content(eid) or ""
            ds.record_change(
                eid, "modified", old_content, entry["content"],
                stored_payloads[eid].get("content_hash", ""),
                entry["metadata"]["content_hash"],
            )
            diff_count += 1

        for eid in deleted_ids:
            old_content = ds.get_latest_content(eid) or ""
            ds.record_change(
                eid, "deleted", old_content, "",
                stored_payloads[eid].get("content_hash", ""),
                "",
            )
            diff_count += 1

    console.print(
        f"[green]  {graph_result['entries_upserted']} 个节点更新, "
        f"{graph_result['entries_deleted']} 个节点删除, "
        f"{graph_result['edges_created']} 条边创建[/]"
    )
    if diff_count:
        console.print(f"[green]  {diff_count} 条变更记录已保存[/]")
    console.print()

    # Summary
    summary_table = Table(title="增量同步摘要", show_lines=True)
    summary_table.add_column("类别", style="bold cyan")
    summary_table.add_column("操作", style="green")
    summary_table.add_column("数量", justify="right")
    summary_table.add_row("变更检测", "新增", str(len(new_ids)))
    summary_table.add_row("变更检测", "修改", str(len(changed_ids)))
    summary_table.add_row("变更检测", "删除", str(len(deleted_ids)))
    summary_table.add_row("变更检测", "未变", str(len(unchanged_ids)))
    summary_table.add_row("Qdrant", "向量更新", str(vec_upserted))
    summary_table.add_row("Qdrant", "向量删除", str(vec_deleted))
    summary_table.add_row("SurrealDB", "节点更新", str(graph_result["entries_upserted"]))
    summary_table.add_row("SurrealDB", "节点删除", str(graph_result["entries_deleted"]))
    summary_table.add_row("SurrealDB", "边创建", str(graph_result["edges_created"]))
    summary_table.add_row("演化追踪", "变更记录", str(diff_count))
    console.print(summary_table)


@cli.command(name="diff")
@click.argument("entry_id")
@click.option("--limit", type=int, default=10, help="显示最近 N 条变更 (默认: 10)")
@click.option("--stats", "stats_only", is_flag=True, help="仅显示统计信息")
def diff_cmd(entry_id: str, limit: int, stats_only: bool) -> None:
    """查看知识条目的内容演化历史。

    显示条目随时间的内容变更记录，包括 unified diff 和统计信息。

    ENTRY_ID: 知识条目 ID (如 ke-20260226-xxx)
    """
    from rich.syntax import Syntax

    from agents.config import load_config
    from agents.diff_store import DiffStore
    from agents.graph_store import get_graph_store

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    try:
        gs = get_graph_store(config)
        gs.connect()
    except Exception as exc:
        console.print(f"[red]无法连接图数据库: {exc}[/]")
        console.print("[dim]提示: 请先运行 `kg sync` 构建图数据库。[/]")
        sys.exit(1)

    try:
        ds = DiffStore(gs)

        if stats_only:
            s = ds.get_stats(entry_id)
            if s["total_changes"] == 0:
                console.print(f"[yellow]条目 {entry_id} 无变更记录。[/]")
                return

            console.print(Panel(
                f"[bold]总变更次数:[/] {s['total_changes']}\n"
                f"[bold]最近修改:[/] {s['last_modified']}\n"
                f"[bold]总新增行:[/] [green]+{s['total_additions']}[/]\n"
                f"[bold]总删除行:[/] [red]-{s['total_deletions']}[/]",
                title=f"条目演化统计: {entry_id}",
                border_style="blue",
            ))
            return

        history = ds.get_history(entry_id, limit=limit)

        if not history:
            console.print(f"[yellow]条目 {entry_id} 无变更记录。[/]")
            console.print("[dim]提示: 变更记录在 `kg sync` 时自动生成。[/]")
            return

        console.print(Panel(
            f"[bold]共 {len(history)} 条变更记录[/]",
            title=f"条目演化历史: {entry_id}",
            border_style="blue",
        ))

        type_style = {"created": "green", "modified": "yellow", "deleted": "red"}

        for record in history:
            change_type = record.get("change_type", "unknown")
            timestamp = record.get("timestamp", "?")
            stats = record.get("stats", {})
            if not isinstance(stats, dict):
                stats = {}
            additions = stats.get("additions", 0)
            deletions = stats.get("deletions", 0)
            style = type_style.get(change_type, "white")

            header = (
                f"[{style}]{change_type}[/{style}]  "
                f"{timestamp}  "
                f"[green]+{additions}[/] [red]-{deletions}[/]"
            )
            console.print(f"\n{header}")

            diff_text = record.get("diff_text", "")
            if diff_text:
                console.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=False))
            elif change_type == "created":
                console.print("[dim]  (首次创建，无差异对比)[/]")

    finally:
        gs.close()


@cli.command()
@click.argument("entry_id")
@click.option("--depth", type=int, default=2, help="遍历深度 (默认: 2)")
@click.option(
    "--format", "fmt",
    type=click.Choice(["text", "mermaid", "canvas"]),
    default="text",
    help="输出格式: text(默认) / mermaid / canvas",
)
@click.option("--output", "-o", type=click.Path(), default=None, help="输出文件路径")
def graph(entry_id: str, depth: int, fmt: str, output: str | None) -> None:
    """探索知识条目的图关系网络。

    显示指定条目的出入边关系和 N 跳邻居节点。
    支持 text / mermaid / canvas 三种输出格式。

    ENTRY_ID: 知识条目 ID (如 ke-20260226-xxx)
    """
    import json

    from rich.tree import Tree

    from agents.config import load_config
    from agents.graph_store import get_graph_store

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    try:
        gs = get_graph_store(config)
        gs.connect()
    except Exception as exc:
        console.print(f"[red]无法连接图数据库: {exc}[/]")
        console.print("[dim]提示: 请先运行 `kg sync` 构建图数据库。[/]")
        sys.exit(1)

    try:
        entry = gs.get_entry(entry_id)
        if entry is None:
            console.print(f"[red]未找到条目: {entry_id}[/]")
            console.print("[dim]提示: 请先运行 `kg sync` 同步图数据库。[/]")
            gs.close()
            sys.exit(1)

        # ── Mermaid / Canvas formats ──
        if fmt in ("mermaid", "canvas"):
            from agents.graph_viz import build_graph_data, to_canvas, to_mermaid

            graph_data = build_graph_data(gs, entry_id, depth)

            if fmt == "mermaid":
                mermaid_str = to_mermaid(graph_data)
                if output:
                    Path(output).parent.mkdir(parents=True, exist_ok=True)
                    Path(output).write_text(mermaid_str)
                    console.print(f"[green]Mermaid 已写入: {output}[/]")
                else:
                    console.print(mermaid_str)
            else:  # canvas
                canvas_data = to_canvas(graph_data, config.vault_path)
                out_path = output or str(
                    config.vault_path / "graphs" / f"graph-{entry_id}.canvas"
                )
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_text(
                    json.dumps(canvas_data, ensure_ascii=False, indent=2)
                )
                console.print(f"[green]Canvas 已写入: {out_path}[/]")
            return

        # ── Text format (original Rich tree) ──
        title = entry.get("title", entry_id)
        console.print(Panel(
            f"[bold]{title}[/]\n"
            f"类型: {entry.get('entry_type', '?')}  "
            f"深度: {entry.get('depth', '?')}  "
            f"置信度: {entry.get('confidence', 0):.0%}",
            title=f"条目: {entry_id}",
            border_style="blue",
        ))

        # Relations
        relations = gs.get_relations(entry_id, direction="both")

        if relations:
            # Group by type and direction
            tree = Tree(f"[bold cyan]{entry_id}[/] 关系网络")

            outbound = [r for r in relations if r["direction"] == "out"]
            inbound = [r for r in relations if r["direction"] == "in"]

            if outbound:
                out_branch = tree.add("[bold green]出边 (→)[/]")
                for rel in outbound:
                    out_branch.add(
                        f"[green]--{rel['rel_type']}-->[/] "
                        f"[cyan]{rel['target_id']}[/]"
                    )

            if inbound:
                in_branch = tree.add("[bold yellow]入边 (←)[/]")
                for rel in inbound:
                    in_branch.add(
                        f"[yellow]<--{rel['rel_type']}--[/] "
                        f"[cyan]{rel['target_id']}[/]"
                    )

            console.print(tree)
        else:
            console.print("[dim]无关系边。[/]")

        # N-hop neighborhood
        if depth >= 2:
            neighborhood = gs.neighborhood(entry_id, depth=depth)
            nodes = neighborhood.get("nodes", [])
            if nodes:
                console.print(f"\n[bold]{depth}-跳邻居 ({len(nodes)} 个节点):[/]")
                nb_table = Table(show_lines=False)
                nb_table.add_column("ID", style="cyan", max_width=45)
                nb_table.add_column("标题", style="white", max_width=35)
                nb_table.add_column("类型", style="magenta", width=12)

                for node in nodes[:20]:
                    nid = node.get("id", "")
                    if isinstance(nid, dict):
                        nid = str(nid)
                    if str(nid).startswith("entry:"):
                        nid = str(nid)[6:].strip("`").strip("⟨").strip("⟩")
                    nb_table.add_row(
                        str(nid),
                        node.get("title", ""),
                        node.get("entry_type", ""),
                    )

                console.print(nb_table)
                if len(nodes) > 20:
                    console.print(f"[dim]  ... 还有 {len(nodes) - 20} 个节点[/]")

    finally:
        gs.close()


@cli.command()
@click.argument("entry_id")
def backlinks(entry_id: str) -> None:
    """查看引用了指定条目的所有条目（反向链接）。

    综合图数据库入边和 [[wiki link]] 扫描，展示哪些条目引用了目标条目。

    ENTRY_ID: 知识条目 ID (如 ke-20260315-xxx)
    """
    from agents.backlinks import find_backlinks, print_backlinks

    results = find_backlinks(entry_id)
    print_backlinks(entry_id, results)

    if results:
        console.print(
            f"\n[dim]共 {len(results)} 个条目引用了 {entry_id}。[/]"
        )


@cli.command()
@click.option("--port", type=int, default=8765, help="API 服务器端口 (默认: 8765)")
@click.option("--dev", is_flag=True, help="同时启动 Vite 开发服务器")
def serve(port: int, dev: bool) -> None:
    """启动 Knowledge Graph Web UI。

    生产模式: uvicorn 直接服务 FastAPI + 静态前端。
    开发模式 (--dev): 后台启动 Vite dev server + uvicorn API。
    """
    import subprocess

    import uvicorn

    if dev:
        web_dir = Path(__file__).parent.parent / "web"
        if not (web_dir / "package.json").exists():
            console.print(f"[red]前端项目未找到: {web_dir}[/]")
            console.print("[dim]请先在 web/ 目录下运行 npm install[/]")
            sys.exit(1)

        console.print("[bold blue]开发模式: 启动 Vite + FastAPI[/]")
        console.print(f"  Vite dev server: http://localhost:5173")
        console.print(f"  FastAPI backend: http://localhost:{port}")
        console.print()

        # Start Vite dev server in background
        vite_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(web_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            uvicorn.run("agents.api:app", host="0.0.0.0", port=port, reload=True)
        finally:
            vite_proc.terminate()
            vite_proc.wait()
    else:
        static_dir = Path(__file__).parent / "static"
        if not static_dir.is_dir():
            console.print("[yellow]提示: 未找到前端构建产物。[/]")
            console.print("[dim]请先运行 cd web && npm run build[/]")
            console.print(f"[dim]或使用 --dev 进入开发模式。[/]\n")

        console.print(f"[bold green]Knowledge Graph Web UI[/]")
        console.print(f"  访问: http://localhost:{port}")
        console.print()
        uvicorn.run("agents.api:app", host="0.0.0.0", port=port)


@cli.command(name="cross-domain")
@click.option(
    "--min-sim", type=float, default=0.6, help="最低相似度阈值 (默认: 0.6)"
)
@click.option(
    "--max", "max_insights", type=int, default=20, help="最大洞察数量 (默认: 20)"
)
@click.option(
    "--no-describe", is_flag=True, help="跳过 Claude 描述生成，仅输出相似度"
)
def cross_domain(min_sim: float, max_insights: int, no_describe: bool) -> None:
    """发现跨域知识连接。

    在不同知识域之间搜索语义相似的条目，发现跨领域的共通模式
    （如 "Go 调度器 ↔ K8s 调度器" 的负载均衡思想）。
    """
    from agents.cross_domain import discover_cross_domain

    try:
        insights = discover_cross_domain(
            min_similarity=min_sim,
            max_insights=max_insights,
            describe=not no_describe,
        )
    except RuntimeError as exc:
        console.print(f"[red]跨域发现错误: {exc}[/]")
        sys.exit(1)

    if not insights:
        console.print("[yellow]未发现跨域知识连接。[/]")
        console.print("[dim]提示: 可尝试降低 --min-sim 阈值或增加知识条目数量。[/]")
        return

    table = Table(title=f"跨域知识发现 ({len(insights)} 条)", show_lines=True)
    table.add_column("域 A", style="cyan", width=12)
    table.add_column("条目 A", style="white", max_width=25)
    table.add_column("↔", justify="center", width=8)
    table.add_column("域 B", style="magenta", width=12)
    table.add_column("条目 B", style="white", max_width=25)
    table.add_column("相似度", justify="right", style="green", width=8)

    for insight in insights:
        table.add_row(
            insight.domain_a,
            insight.entry_a_title,
            f"[bold]{insight.similarity:.2f}[/]",
            insight.domain_b,
            insight.entry_b_title,
            f"{insight.similarity:.2f}",
        )

    console.print(table)

    # Print descriptions if available
    described = [i for i in insights if i.description]
    if described:
        console.print(f"\n[bold]跨域洞察描述:[/]\n")
        for i, insight in enumerate(described, 1):
            console.print(
                f"  [bold]{i}.[/] [cyan]{insight.entry_a_title}[/] "
                f"↔ [magenta]{insight.entry_b_title}[/]"
            )
            console.print(f"     {insight.description}\n")


@cli.group()
def pull() -> None:
    """从外部源拉取知识（RSS、GitHub、Slack）。

    支持多种外部知识源的自动摄取。
    """


@pull.command(name="rss")
@click.option(
    "--feeds",
    type=click.Path(exists=True, path_type=Path),
    help="feeds.yaml 配置文件路径（默认: vault 根目录）",
)
@click.option("--since", type=int, help="只拉取最近 N 天的文章")
@click.option("--dry-run", is_flag=True, help="预览模式，不实际写入")
@click.option("--workers", type=int, default=5, help="并行拉取数 (默认: 5)")
@click.option(
    "--quality-check/--no-quality-check",
    default=True,
    help="启用质量评估与去重 (默认启用)",
)
@click.option(
    "--skip-threshold",
    type=float,
    default=0.85,
    help="Embedding 预过滤阈值 (0 禁用, 默认: 0.85)",
)
def pull_rss(
    feeds: Path | None,
    since: int | None,
    dry_run: bool,
    workers: int,
    quality_check: bool,
    skip_threshold: float,
) -> None:
    """从 RSS feeds 拉取技术博客文章。

    读取 feeds.yaml 配置，拉取订阅的技术博客，
    提取知识条目并写入 vault。

    示例:

    \b
    # 拉取所有 feeds（增量，基于 watermark）
    kg pull rss

    \b
    # 只拉取最近 7 天的文章
    kg pull rss --since 7

    \b
    # 预览模式
    kg pull rss --dry-run
    """
    from datetime import datetime, timedelta, timezone
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import yaml
    from agents.config import load_config
    from agents.sources.rss import RSSAdapter
    from agents.sources.state import SourceStateManager
    from agents.ingest import ingest_file_with_quality, ingest_file
    import tempfile

    config = load_config()

    # Load feeds.yaml
    if feeds is None:
        feeds = config.vault_path / "feeds.yaml"

    if not feeds.exists():
        console.print(f"[red]错误: feeds.yaml 不存在: {feeds}[/]")
        console.print("[dim]请创建 feeds.yaml 配置文件。参考 feeds.yaml.example[/]")
        sys.exit(1)

    try:
        with open(feeds, encoding="utf-8") as f:
            feeds_config = yaml.safe_load(f)
    except Exception as exc:
        console.print(f"[red]解析 feeds.yaml 失败: {exc}[/]")
        sys.exit(1)

    feed_list = feeds_config.get("feeds", [])
    global_config = feeds_config.get("config", {})

    if not feed_list:
        console.print("[yellow]feeds.yaml 中没有配置任何 feed。[/]")
        return

    console.print(f"[bold blue]准备拉取 {len(feed_list)} 个 RSS feeds...[/]")

    # Determine cutoff time
    cutoff = None
    if since:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=since)
        console.print(f"[dim]只拉取 {since} 天内的文章（{cutoff.strftime('%Y-%m-%d')} 之后）[/]")

    state_manager = SourceStateManager()

    # Fetch all feeds in parallel
    all_documents = []

    def fetch_feed(feed_cfg):
        adapter = RSSAdapter(
            feed_url=feed_cfg["url"],
            feed_name=feed_cfg["name"],
            domain=feed_cfg.get("domain"),
            tags=feed_cfg.get("tags", []),
            quality_weight=feed_cfg.get("quality_weight", 1.0),
            state_manager=state_manager,
        )

        # Use watermark if no --since specified
        fetch_since = cutoff
        if fetch_since is None:
            fetch_since = adapter.get_watermark()

        docs = adapter.fetch(since=fetch_since)

        # Update watermark
        if docs:
            latest = max(doc.timestamp for doc in docs)
            adapter.set_watermark(latest)

        return feed_cfg["name"], docs

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch_feed, feed): feed for feed in feed_list}

        for future in as_completed(futures):
            feed_cfg = futures[future]
            try:
                feed_name, docs = future.result()
                all_documents.extend(docs)
                if docs:
                    console.print(f"  [green]✓[/] {feed_name}: {len(docs)} 篇文章")
                else:
                    console.print(f"  [dim]○[/] {feed_name}: 无新文章")
            except Exception as exc:
                console.print(f"  [red]✗[/] {feed_cfg['name']}: {exc}")

    if not all_documents:
        console.print("\n[yellow]未拉取到任何新文章。[/]")
        return

    console.print(f"\n[bold green]共拉取 {len(all_documents)} 篇文章[/]")

    # ── Embedding pre-filter ────────────────────────────────────────
    if skip_threshold > 0 and all_documents:
        from agents.prefilter import prefilter_documents

        console.print(
            f"[dim]正在进行 embedding 预过滤（阈值: {skip_threshold:.2f}）...[/]"
        )
        passed_docs, skipped_results = prefilter_documents(
            all_documents, config, skip_threshold
        )

        if skipped_results:
            console.print(
                f"[yellow]预过滤跳过 {len(skipped_results)} 篇重复文章:[/]"
            )
            for result in skipped_results:
                console.print(
                    f"  [dim]✗[/] {result.document.title[:60]}"
                    f" [dim](相似度: {result.similarity:.2f},"
                    f" 匹配: {result.matched_title[:40]})[/]"
                )

        all_documents = passed_docs
        console.print(
            f"[bold green]预过滤后剩余 {len(all_documents)} 篇文章[/]"
        )

        if not all_documents:
            console.print("[yellow]所有文章均为重复内容，无需处理。[/]")
            return

    # ── Checkpoint: load previous progress ──────────────────────────
    checkpoint_path = config.vault_path / ".kg" / "rss_checkpoint.yaml"
    checkpoint: dict = {}
    processed_ids: set[str] = set()

    if checkpoint_path.exists():
        with open(checkpoint_path, encoding="utf-8") as f:
            checkpoint = yaml.safe_load(f) or {}
        processed_ids = set(checkpoint.get("processed", {}).keys())
        if processed_ids:
            before = len(all_documents)
            all_documents = [d for d in all_documents if d.source_id not in processed_ids]
            resumed = before - len(all_documents)
            if resumed:
                console.print(
                    f"[cyan]从断点恢复: 跳过已处理的 {resumed} 篇文章，"
                    f"剩余 {len(all_documents)} 篇[/]"
                )
                # 累计之前的计数
                for v in checkpoint.get("processed", {}).values():
                    if isinstance(v, dict):
                        pass  # 计数在最终汇总时从 checkpoint 读取

    if not all_documents:
        console.print("[yellow]所有文章均已处理，无需继续。[/]")
        # 清理 checkpoint
        checkpoint_path.unlink(missing_ok=True)
        return

    console.print(f"[bold blue]开始提取知识条目（{workers} 并发）...[/]")

    # ── Checkpoint helper ─────────────────────────────────────────
    from threading import Lock
    _ckpt_lock = Lock()

    def _save_checkpoint(source_id: str, result: dict) -> None:
        """Save single article result to checkpoint file."""
        with _ckpt_lock:
            if not checkpoint.get("processed"):
                checkpoint["processed"] = {}
            checkpoint["processed"][source_id] = result
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            with open(checkpoint_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(checkpoint, f, allow_unicode=True, sort_keys=False)

    # ── Process documents ─────────────────────────────────────────
    total_created = 0
    total_merged = 0
    total_skipped = 0
    total_failed = 0

    def process_doc(args):
        i, doc = args
        console.print(f"[dim]处理 {i}/{len(all_documents)}: {doc.title}[/]")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(doc.to_markdown())
            tmp_path = Path(tmp.name)

        try:
            # Build source tags from RSS metadata
            source_tags = ["source:rss"]
            if doc.metadata.get("feed_name"):
                feed_slug = (
                    doc.metadata["feed_name"]
                    .lower().replace(" ", "-").replace(".", "")[:30]
                )
                source_tags.append(f"feed:{feed_slug}")
            if doc.domain:
                source_tags.append(f"domain:{doc.domain}")

            if quality_check:
                results = ingest_file_with_quality(
                    file_path=tmp_path,
                    config=config,
                    dry_run=dry_run,
                    novelty_threshold=global_config.get("novelty_threshold", 0.3),
                    quality_threshold=global_config.get("quality_threshold", 0.4),
                    extra_tags=source_tags,
                )
            else:
                raw_results = ingest_file(
                    file_path=tmp_path,
                    config=config,
                    dry_run=dry_run,
                )
                results = [
                    {**r, "action": "create", "novelty_score": None, "quality_score": None}
                    for r in raw_results
                ]

            created = sum(1 for r in results if r.get("action") == "create")
            merged = sum(1 for r in results if r.get("action") == "merge")
            skipped = sum(1 for r in results if r.get("action") == "skip")

            # 保存到 checkpoint
            _save_checkpoint(doc.source_id, {
                "title": doc.title,
                "created": created, "merged": merged, "skipped": skipped,
            })

            return created, merged, skipped, None

        except Exception as exc:
            console.print(f"  [red]✗[/] 处理失败: {doc.title[:60]}")
            console.print(f"  [dim]  原因: {exc}[/]")

            # 失败也记录 checkpoint（标记为 failed），下次不重试
            # 如需重试失败的，删除 checkpoint 文件即可
            _save_checkpoint(doc.source_id, {
                "title": doc.title, "failed": True, "error": str(exc)[:200],
            })

            return 0, 0, 0, str(exc)

        finally:
            tmp_path.unlink(missing_ok=True)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = list(executor.map(process_doc, enumerate(all_documents, 1)))

    for created, merged, skipped, err in futures:
        if err is None:
            total_created += created
            total_merged += merged
            total_skipped += skipped
        else:
            total_failed += 1

    # 加上之前 checkpoint 中已处理的计数
    for v in checkpoint.get("processed", {}).values():
        if isinstance(v, dict) and not v.get("failed"):
            if v.get("created") and v.get("title") not in {d.title for d in all_documents}:
                total_created += v.get("created", 0)
                total_merged += v.get("merged", 0)
                total_skipped += v.get("skipped", 0)

    # Summary
    summary = (
        f"\n[bold]RSS 拉取完成:[/] "
        f"[green]创建 {total_created}[/] | "
        f"[yellow]合并 {total_merged}[/] | "
        f"[dim]跳过 {total_skipped}[/]"
    )
    if total_failed:
        summary += f" | [red]失败 {total_failed}[/]"
    console.print(summary)

    # 全部完成后清理 checkpoint
    checkpoint_path.unlink(missing_ok=True)
    console.print("[dim]checkpoint 已清理[/]")

    if not dry_run:
        console.print(
            f"\n[dim]提示: 运行 `kg sync` 更新向量索引和图关系。[/]"
        )


@cli.command()
@click.option("--discover", is_flag=True, help="发现可合并的候选组")
@click.option("--threshold", type=float, default=0.80, help="相似度阈值 (默认: 0.80)")
@click.option("--dry-run", is_flag=True, help="预览模式，不实际执行")
@click.argument("entry_ids", nargs=-1)
def distill(
    discover: bool,
    threshold: float,
    dry_run: bool,
    entry_ids: tuple[str, ...],
) -> None:
    """知识蒸馏 — 合并相似条目为规范条目。

    两种使用模式:

    \b
    # 发现候选组
    kg distill --discover [--threshold 0.80]

    \b
    # 执行合并（可加 --dry-run 预览）
    kg distill ke-xxx ke-yyy [--dry-run]
    """
    from agents.distill import discover_candidates, execute_distill, print_candidates

    if discover:
        console.print(f"[bold blue]正在发现相似度 >= {threshold} 的候选组...[/]")
        try:
            groups = discover_candidates(threshold=threshold)
        except Exception as exc:
            console.print(f"[red]发现失败: {exc}[/]")
            sys.exit(1)
        print_candidates(groups)
        if groups:
            console.print(
                f"\n[dim]使用 `kg distill <id1> <id2> ...` 执行蒸馏，"
                f"或加 --dry-run 预览。[/]"
            )
        return

    if not entry_ids:
        console.print("[red]错误: 请提供 --discover 或至少两个条目 ID。[/]")
        console.print("[dim]示例: kg distill ke-xxx ke-yyy[/]")
        sys.exit(1)

    if len(entry_ids) < 2:
        console.print("[red]错误: 至少需要两个条目 ID 才能执行蒸馏。[/]")
        sys.exit(1)

    mode = "[yellow]预览模式[/]" if dry_run else "[red]执行模式[/]"
    console.print(f"[bold]知识蒸馏 {mode}[/]")
    console.print(f"  待合并条目: {', '.join(entry_ids)}")

    if not dry_run:
        confirm = click.confirm(f"确认合并 {len(entry_ids)} 个条目并删除原条目?", default=False)
        if not confirm:
            console.print("[dim]已取消。[/]")
            return

    try:
        result = execute_distill(list(entry_ids), dry_run=dry_run)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]蒸馏失败: {exc}[/]")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]新条目 ID:[/] {result.new_entry_id}\n"
            f"[bold]标题:[/] {result.new_entry_title}\n"
            f"[bold]路径:[/] {result.new_entry_path}\n"
            f"[bold]已删除原条目:[/] {result.deleted_count} 个",
            title="[green]蒸馏完成[/]" if not dry_run else "[yellow]预览结果[/]",
            border_style="green" if not dry_run else "yellow",
        )
    )


if __name__ == "__main__":
    cli()
