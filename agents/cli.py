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

from agents.cli_plane_heal import register as register_plane_heal
from kg.eval.retrieval import RETRIEVERS
from kg.interfaces.cli.commands.index import index_group

console = Console()


@click.group()
@click.version_option(version="0.1.0", prog_name="kg-cli")
def cli() -> None:
    """Knowledge Graph CLI - 知识图谱管理工具

    管理和查询你的个人知识图谱。支持知识提取、语义检索、
    定期审查等功能。
    """


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--url", type=str, help="从网页链接导入内容")
@click.option("--dry-run", is_flag=True, help="预览模式，不实际写入文件")
@click.option(
    "--quality-check/--no-quality-check",
    default=True,
    help="启用质量评估与去重 (默认启用)",
)
@click.option("--timeout", type=int, default=30, help="HTTP 超时秒数 (默认: 30，仅 --url 使用)")
@click.option("-r", "--recursive", is_flag=True, help="递归扫描子目录")
@click.option("--workers", type=int, default=3, help="并行线程数 (默认: 3)")
def ingest(
    path: Path | None,
    url: str | None,
    dry_run: bool,
    quality_check: bool,
    timeout: int,
    recursive: bool,
    workers: int,
) -> None:
    """从对话/文档中提取知识条目。

    支持单文件 (.md/.txt/.pdf) 或目录批量导入。
    使用 Claude 提取结构化知识条目，并写入知识库对应目录。

    PATH: 待处理的文件或目录路径
    """
    if bool(path) == bool(url):
        console.print("[red]请提供 PATH 或 --url，且只能二选一。[/]")
        sys.exit(1)

    if url:
        import tempfile

        from agents.sources.web import WebAdapter

        adapter = WebAdapter()
        try:
            console.print(f"[dim]正在抓取: {url}[/]")
            doc = adapter.fetch_url(url=url, timeout=timeout, extra_tags=[])
        except ValueError as exc:
            console.print(f"[red]抓取失败: {exc}[/]")
            sys.exit(1)

        console.print(f"[dim]标题: {doc.title}[/]")
        console.print(f"[dim]内容: {len(doc.content):,} 字符[/]")

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8",
            ) as tmp:
                tmp.write(doc.to_markdown())
                tmp_path = Path(tmp.name)

            if quality_check:
                from agents.ingest import ingest_file_with_quality

                results = ingest_file_with_quality(file_path=tmp_path, dry_run=dry_run)
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
            else:
                from agents.ingest import ingest_file

                results = ingest_file(file_path=tmp_path, dry_run=dry_run)
                if results:
                    action = "预览" if dry_run else "创建"
                    console.print(
                        f"\n[bold green]完成: {action}了 {len(results)} 个知识条目[/]"
                    )
                else:
                    console.print("[yellow]未提取到任何知识条目。[/]")
        except RuntimeError as exc:
            console.print(f"[red]处理错误: {exc}[/]")
            sys.exit(1)
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        return

    if path is None:
        console.print("[red]缺少 PATH 参数。[/]")
        sys.exit(1)

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
    from kg.interfaces.cli.commands.query import run_query_command

    run_query_command(
        question=question,
        build=build,
        domain=domain,
        entry_type=entry_type,
        depth=depth,
        status=status,
        top_k=top_k,
        output_format=output_format,
        console=console,
    )


@cli.command()
@click.argument("question")
@click.option("--top-k", type=int, default=5, help="检索条目数量 (默认: 5)")
@click.option("--domain", type=str, default=None, help="限定知识域")
@click.option("--no-graph", is_flag=True, help="禁用图上下文增强")
@click.option("--no-fallback", is_flag=True, help="禁用知识库 miss 时的 Fallback 直接回答")
def ask(question: str, top_k: int, domain: str | None, no_graph: bool, no_fallback: bool) -> None:
    """RAG 知识问答 — 检索相关条目并生成综合回答。

    输入自然语言问题，检索知识库中最相关的条目，结合图关系上下文，
    通过 Claude 生成综合性回答并标注来源。

    当知识库无结果时，自动 Fallback 到直接 LLM 回答并写入知识库。
    使用 --no-fallback 禁用此行为。

    QUESTION: 你的问题
    """
    from kg.interfaces.cli.commands.ask import run_ask_command

    run_ask_command(
        question=question,
        top_k=top_k,
        domain=domain,
        no_graph=no_graph,
        no_fallback=no_fallback,
        console=console,
    )


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
    from kg.interfaces.cli.commands.review import run_review_command

    run_review_command(
        domain=domain,
        output=output,
        report=report,
        console=console,
    )


@cli.command()
def stats() -> None:
    """显示知识库统计信息。

    展示各知识域、条目类型、深度层级的分布统计。
    """
    from kg.interfaces.cli.commands.stats import run_stats_command

    run_stats_command(console=console)


@cli.command()
def init() -> None:
    """初始化/验证知识库目录结构。

    检查并创建必要的目录和索引文件。
    """
    from kg.interfaces.cli.commands.init import run_init_command

    run_init_command(console=console)


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
    """同步知识库到 Weaviate 向量索引 + SurrealDB 图数据库。

    默认增量同步：只处理新增、修改、删除的条目。
    使用 --full 强制全量重建。
    """
    from kg.interfaces.cli.commands.sync import run_sync_command

    run_sync_command(full=full, console=console)


@cli.command(name="diff")
@click.argument("entry_id")
@click.option("--limit", type=int, default=10, help="显示最近 N 条变更 (默认: 10)")
@click.option("--stats", "stats_only", is_flag=True, help="仅显示统计信息")
def diff_cmd(entry_id: str, limit: int, stats_only: bool) -> None:
    """查看知识条目的内容演化历史。

    显示条目随时间的内容变更记录，包括 unified diff 和统计信息。

    ENTRY_ID: 知识条目 ID (如 ke-20260226-xxx)
    """
    from kg.interfaces.cli.commands.diff import run_diff_command

    run_diff_command(
        entry_id=entry_id,
        limit=limit,
        stats_only=stats_only,
        console=console,
    )


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
    from kg.interfaces.cli.commands.graph import run_graph_command

    run_graph_command(
        entry_id=entry_id,
        depth=depth,
        fmt=fmt,
        output=output,
        console=console,
    )


@cli.command()
@click.argument("entry_id")
def backlinks(entry_id: str) -> None:
    """查看引用了指定条目的所有条目（反向链接）。

    综合图数据库入边和 [[wiki link]] 扫描，展示哪些条目引用了目标条目。

    ENTRY_ID: 知识条目 ID (如 ke-20260315-xxx)
    """
    from kg.interfaces.cli.commands.graph import run_backlinks_command

    run_backlinks_command(entry_id=entry_id, console=console)


@cli.group()
def capture() -> None:
    """自动沉淀：增量读取 Claude Code / Codex 会话，按预算切成待整理批次。"""


@capture.command(name="scan")
@click.option("--dry-run", is_flag=True, help="只预览这一批会读哪些会话，不写入、不移动进度")
@click.option("--autoloop", is_flag=True, help="按 agent-autoloop 回执格式输出，发出 capture.ready 事件")
def capture_scan(dry_run: bool, autoloop: bool) -> None:
    """读下一批会话（白名单目录、额度上限、断点续读见 .kg/capture/config.yaml）。"""
    from kg.interfaces.cli.commands.capture import run_capture_scan_command

    run_capture_scan_command(dry_run=dry_run, autoloop=autoloop, console=console)


@capture.command(name="status")
def capture_status() -> None:
    """查看已记录的会话进度和待整理的批次。"""
    from kg.interfaces.cli.commands.capture import run_capture_status_command

    run_capture_status_command(console=console)


@cli.group()
def brief() -> None:
    """每日日报。"""


@brief.command(name="collect")
@click.option("--autoloop", is_flag=True, help="按 agent-autoloop 回执格式输出，发出 brief.ready 事件")
@click.option("--no-fetch", is_flag=True, help="不请求 AIHOT")
def brief_collect(autoloop: bool, no_fetch: bool) -> None:
    """收集写日报要用的资料（不调用模型）。"""
    from kg.interfaces.cli.commands.capture import run_brief_collect_command

    run_brief_collect_command(autoloop=autoloop, no_fetch=no_fetch, console=console)


@cli.group(name="eval")
def eval_group() -> None:
    """检索评测：从会话挖候选问题，按标注的评测集给检索打分。"""


@eval_group.command(name="mine")
@click.option("--days", type=int, default=60, show_default=True, help="只看最近 N 天的会话")
@click.option("--limit", type=int, default=400, show_default=True, help="最多挖多少个候选问题")
def eval_mine(days: int, limit: int) -> None:
    """从白名单里的会话挖出像问题的用户发言，写到 .kg/eval/candidates.jsonl。"""
    from kg.interfaces.cli.commands.eval import run_eval_mine_command

    run_eval_mine_command(days=days, limit=limit, console=console)


@eval_group.command(name="run")
@click.option("--retriever", type=click.Choice([*RETRIEVERS, "all"]), default="all", show_default=True)
@click.option("--tag", default="", help="报告文件名后缀")
def eval_run(retriever: str, tag: str) -> None:
    """按 .kg/eval/retrieval.jsonl 给检索打分：hit@1、recall@5/10、MRR@10。"""
    from kg.interfaces.cli.commands.eval import run_eval_run_command

    run_eval_run_command(retriever=retriever, console=console, tag=tag)


@cli.group()
def plane() -> None:
    """本地管控面：采集器写状态库，异常规则开关事故（不调用模型）。"""


@plane.command(name="collect")
@click.argument("name")
@click.option("--dry-run", is_flag=True, help="完整跑一遍再回滚：看会开关哪些事故，不写入")
@click.option("--autoloop", is_flag=True, help="按 agent-autoloop 回执格式输出，事故开关时发出 plane.incident.* 事件")
def plane_collect(name: str, dry_run: bool, autoloop: bool) -> None:
    """运行一个采集器（如 services），写进 .kg/plane/state.db。"""
    from kg.interfaces.cli.commands.plane import run_plane_collect_command

    run_plane_collect_command(name=name, dry_run=dry_run, autoloop=autoloop, console=console)


@plane.command(name="status")
def plane_status() -> None:
    """查看状态库：各采集器的资源和状态、未关闭的事故。"""
    from kg.interfaces.cli.commands.plane import run_plane_status_command

    run_plane_status_command(console=console)


@plane.group(name="egress")
def plane_egress() -> None:
    """代理出口探活。"""


@plane_egress.command(name="replay")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--expect", multiple=True, help="期望出口 IP（只放本地），可重复")
@click.option("--pmset-log", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--interval", type=int, help="探测周期（秒），默认按记录推断")
def plane_egress_replay(path: Path, expect: tuple[str, ...], pmset_log: Path | None, interval: int | None) -> None:
    """回放探测记录，打印事故和可用性。"""
    from kg.interfaces.cli.commands.egress import run_egress_replay_command

    run_egress_replay_command(path=path, expect=tuple(expect), pmset_log=pmset_log,
                              interval=interval, console=console)


@plane_egress.command(name="import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--db", type=click.Path(dir_okay=False, path_type=Path), help="状态库文件，默认 <vault>/.kg/plane/state.db")
@click.option("--line", default="default", show_default=True, help="线路名，资源 id 为 egress:<线路名>")
@click.option("--expect", multiple=True, help="期望出口 IP（只放本地），可重复")
@click.option("--pmset-log", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--interval", type=int, help="探测周期（秒），默认按记录推断")
@click.option("--dry-run", is_flag=True)
def plane_egress_import(path: Path, db: Path | None, line: str, expect: tuple[str, ...], pmset_log: Path | None,
                        interval: int | None, dry_run: bool) -> None:
    """把旧探测记录导入状态库并逐条回放规则；和整段回放不一致时退出码 1。"""
    from kg.interfaces.cli.commands.egress import run_egress_import_command

    sys.exit(run_egress_import_command(path=path, db=db, line=line, expect=tuple(expect), pmset_log=pmset_log,
                                       interval=interval, dry_run=dry_run, console=console))


register_plane_heal(plane)
cli.add_command(index_group)


@cli.command()
@click.option("--port", type=int, default=8765, help="API 服务器端口 (默认: 8765)")
@click.option("--dev", is_flag=True, help="同时启动 Vite 开发服务器")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="监听地址；要让局域网访问再写 0.0.0.0（知识库内容会对局域网可读）")
def serve(port: int, dev: bool, host: str) -> None:
    """启动 Knowledge Graph Web UI。

    生产模式: uvicorn 直接服务 FastAPI + 静态前端。
    开发模式 (--dev): 后台启动 Vite dev server + uvicorn API。
    """
    from kg.interfaces.cli.commands.serve import run_serve_command

    run_serve_command(port=port, dev=dev, console=console, host=host)


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
