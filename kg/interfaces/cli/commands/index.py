"""`kg index` handlers: build and incrementally update the local retrieval index.

  kg index update    only new / modified / deleted entry files (mtime + size, then sha256),
                     then artifact pages from the state store whose content hash changed
  kg index rebuild   drop and re-index the whole vault
  kg index status    last success, last error, files waiting to be indexed

Exit codes: 0 ok, 2 index missing (run rebuild), 3 stale or behind (status only), 1 other failure.
Every run writes `.kg/index/status.json`, also on failure, and one signal on `index:kg:main` in
the state store, where rule `index.failed` opens an incident (`kg.plane.collectors.index_build`).
`--autoloop` prints the agent-autoloop receipt instead (see `run_index_command`).

The group is a plain Click group so the main CLI can `cli.add_command(index_group)`; until then
it also runs as `python -m kg.interfaces.cli.commands.index <command>`.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

EXIT_FAILED, EXIT_MISSING, EXIT_BEHIND = 1, 2, 3


def _exit_code(exc: Exception) -> int:
    from kg.retrieval.errors import IndexMissingError

    return EXIT_MISSING if isinstance(exc, IndexMissingError) else EXIT_FAILED


def _outcome(action: str, report: Any, exc: Exception | None, *, model: str, root: str):  # type: ignore[no-untyped-def]
    from kg.plane.collectors.index_build import BuildOutcome
    from kg.retrieval.errors import RetrievalError

    if exc is not None:
        error = (exc.to_dict() if isinstance(exc, RetrievalError)
                 else {"code": "index_failed", "message": f"{type(exc).__name__}: {exc}"})
        return BuildOutcome(action, False, error["code"], str(error.get("message") or ""),
                            model=model, root=root)
    counts = {k: v for k, v in asdict(report).items() if isinstance(v, int | float)}
    return BuildOutcome(action, True, seconds=report.seconds, model=model, root=root,
                        counts=counts, errors=list(report.errors))


def _record(vault: Path, outcome: Any) -> tuple[Any, str]:
    """Write the run into the state store; never lets a store problem hide the index result."""
    import time

    from kg.plane.collectors.index_build import record_build

    try:
        return record_build(vault, outcome, now=int(time.time())), ""
    except Exception as exc:  # locked or broken store: say so, the index result still counts
        return None, f"写状态库失败：{type(exc).__name__}: {exc}"


def _autoloop_line(action: str, outcome: Any, report: Any, result: Any, store_error: str) -> str:
    verb = "重建" if action == "rebuild" else "更新"
    if outcome.ok:
        line = (f"索引{verb}完成：新增 {report.added}、修改 {report.modified}、"
                f"删除 {report.deleted}，现有 {report.entries} 个条目，用时 {report.seconds}s")
        if report.errors:
            line += f"；{len(report.errors)} 个文件或工件读不了"
    else:
        line = f"索引{verb}失败（{outcome.code}）：{outcome.message[:200]}"
    if result is not None:
        opened = sum(1 for c in result.changes if c.action == "open")
        closed = sum(1 for c in result.changes if c.action == "close")
        line += f"；事故 新开 {opened}、关闭 {closed}"
    if store_error:
        line += f"；{store_error}"
    return line


def run_index_command(
    *, action: str, granularity: str | None, model: str | None, download: bool,
    as_json: bool, console: Console, autoloop: bool = False,
) -> None:
    """Build or update the index, then record the run in the state store (`index:kg:main`).

    With `autoloop` the output is what an agent-autoloop `command` agent prints (see
    `kg plane collect --autoloop`): one line, then a receipt block when `index.failed` opened
    or closed. A failed build then exits 0, because the incident carries the failure and a
    non-zero exit would make agent-autoloop throw the receipt away; it exits 1 only when the
    state store could not be written either, so a failure is never silent.
    """
    from kg.infrastructure.config.loader import load_config
    from kg.retrieval.index import Index
    from kg.retrieval.models import DEFAULT_EMBED_MODEL, Embedder

    vault = load_config().vault_path
    index = Index(vault)
    say = (lambda _m: None) if autoloop else (lambda m: console.print(f"[dim]{m}[/]"))
    report = failure = None
    name = model or DEFAULT_EMBED_MODEL
    try:
        if action == "rebuild":
            embedder = Embedder(name, download=download)
            report = index.rebuild(embedder, granularity=granularity or "entry", progress=say)
        else:
            manifest = index.manifest()
            name = model or (manifest or {}).get("model") or DEFAULT_EMBED_MODEL
            embedder = Embedder(name, download=download)
            report = index.update(embedder, granularity=granularity, progress=say)
    except Exception as exc:
        failure = exc
    outcome = _outcome(action, report, failure, model=name, root=str(index.root))
    result, store_error = _record(vault, outcome)

    if autoloop:
        from kg.interfaces.cli.commands.plane import _events, _receipt

        line = _autoloop_line(action, outcome, report, result, store_error)
        events = _events(result) if result is not None else []
        if not events:
            # Same contract as `kg plane collect`: nothing opened or closed, no notification.
            print(line if line.startswith("没事可做") else f"没事可做：{line}")
        else:
            print(line + "\n")
            print(_receipt(events))
        if store_error and failure is not None:
            sys.exit(_exit_code(failure))
        return

    if failure is not None:
        if as_json:
            from kg.retrieval.errors import RetrievalError

            error = (failure.to_dict() if isinstance(failure, RetrievalError)
                     else {"code": outcome.code, "message": outcome.message})
            print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        else:
            verb = "重建" if action == "rebuild" else "更新"
            console.print(f"[red]索引{verb}失败：{failure}[/]")
            console.print(f"[dim]状态已写入 {index.status_path}[/]")
            if store_error:
                console.print(f"[yellow]{store_error}[/]")
        sys.exit(_exit_code(failure))
    assert report is not None
    if as_json:
        print(json.dumps({"ok": True, **asdict(report)}, ensure_ascii=False))
        return
    console.print(
        f"[green]索引{('重建' if action == 'rebuild' else '更新')}完成[/]：新增 {report.added}、"
        f"修改 {report.modified}、删除 {report.deleted}、仅时间变化 {report.touched}、"
        f"无 id 跳过 {report.skipped}；写入 {report.chunks_written} 块，"
        f"现有 {report.entries} 个条目 / {report.chunks} 块，"
        f"用时 {report.seconds}s（向量 {report.embed_seconds}s）"
    )
    console.print(
        f"工件：现有 {report.artifacts} 个；新增 {report.artifacts_added}、"
        f"修改 {report.artifacts_modified}、删除 {report.artifacts_deleted}"
    )
    for err in report.errors:
        console.print(f"[yellow]读不了：{err}[/]")
    if store_error:
        console.print(f"[yellow]{store_error}[/]")


def run_index_status_command(*, as_json: bool, console: Console) -> None:
    from kg.infrastructure.config.loader import load_config
    from kg.retrieval.errors import RetrievalError
    from kg.retrieval.index import Index

    index = Index(load_config().vault_path)
    status = index.status()
    try:
        pend = index.pending()
        status["pending"] = {"added": len(pend.added), "modified": len(pend.modified),
                             "deleted": len(pend.deleted)}
        code = EXIT_BEHIND if pend.count or status.get("ok") is False else 0
    except RetrievalError as exc:
        status["error"] = exc.to_dict()
        code = EXIT_MISSING
    if as_json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        for key in ("ok", "last_success", "last_attempt", "model", "granularity", "entries",
                    "files", "pending", "error"):
            if key in status:
                console.print(f"{key:13s} {status[key]}")
    sys.exit(code)


@click.group(name="index")
def index_group() -> None:
    """本地检索索引（LanceDB）：增量更新、重建、查看状态。"""


_model_opt = click.option("--model", default=None,
                          help="本地向量模型（默认 BAAI/bge-m3，或 KG_EMBED_MODEL）")
_json_opt = click.option("--json", "as_json", is_flag=True, help="输出 JSON，给脚本和流水线用")
_download_opt = click.option("--download", is_flag=True,
                             help="模型不在本机缓存时允许下载（只下载模型，不上传任何内容）")


_autoloop_opt = click.option(
    "--autoloop", is_flag=True,
    help="按 agent-autoloop 回执格式输出，index.failed 事故开关时发出 plane.incident.* 事件")


@index_group.command(name="update")
@_model_opt
@_download_opt
@_json_opt
@_autoloop_opt
def index_update(model: str | None, download: bool, as_json: bool, autoloop: bool) -> None:
    """只处理新建、修改、删除的条目文件。"""
    from rich.console import Console

    run_index_command(action="update", granularity=None, model=model, download=download,
                      as_json=as_json, console=Console(stderr=as_json or autoloop),
                      autoloop=autoloop)


@index_group.command(name="rebuild")
@click.option("--granularity", type=click.Choice(["entry", "section"]), default="entry",
              show_default=True,
              help="整篇一块（评测集 v1 上最好），或按标题切小节")
@_model_opt
@_download_opt
@_json_opt
@_autoloop_opt
def index_rebuild(granularity: str, model: str | None, download: bool, as_json: bool,
                  autoloop: bool) -> None:
    """清空并重建整个索引。"""
    from rich.console import Console

    run_index_command(action="rebuild", granularity=granularity, model=model,
                      download=download, as_json=as_json,
                      console=Console(stderr=as_json or autoloop), autoloop=autoloop)


@index_group.command(name="status")
@_json_opt
def index_status(as_json: bool) -> None:
    """最近一次成功、最近一次错误、还没进索引的文件数。"""
    from rich.console import Console

    run_index_status_command(as_json=as_json, console=Console())


if __name__ == "__main__":
    index_group()
