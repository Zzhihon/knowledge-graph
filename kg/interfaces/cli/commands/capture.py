"""`kg capture` and `kg brief` handlers: read sessions into batches, gather the brief input.

With `autoloop=True` the output is what an agent-autoloop `command` agent prints:
a one-line conclusion, then a receipt block emitting the event for the next rule.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from kg.capture.brief import collect_brief
from kg.capture.scan import load_capture_config, pending_batches, scan
from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def _receipt(event: dict[str, str]) -> str:
    return f"```agent-autoloop\n{json.dumps({'events': [event]}, ensure_ascii=False)}\n```"


def _capture_config(console: Console):  # type: ignore[no-untyped-def]
    try:
        vault = load_config().vault_path
        return vault, load_capture_config(vault)
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)


def run_capture_scan_command(*, dry_run: bool, autoloop: bool, console: Console) -> None:
    _, config = _capture_config(console)
    result = scan(config, dry_run=dry_run)
    skipped = "，".join(f"{k} {v}" for k, v in sorted(result.skipped.items())) or "无"
    if len(result.pending) >= config.budget.max_pending_batches:
        waiting = ", ".join(result.pending)
        print(f"没事可做：还有 {len(result.pending)} 批没整理完（{waiting}），先不读新的")
        return
    if not result.parts:
        print(f"没事可做：没有新的会话内容（跳过：{skipped}）")
        return
    chars = sum(len(p.text) for p in result.parts)
    projects = sorted({p.project for p in result.parts})
    line = (f"读了 {len(result.parts)} 段会话，{chars} 字，项目：{'、'.join(projects)}；"
            f"跳过：{skipped}；还有 {result.remaining} 个会话留到下次")
    if dry_run:
        console.print(f"[yellow]预览，不写入：[/]{line}")
        for part in result.parts:
            console.print(f"  {part.source} {part.project} {part.info.session_id[:8]} "
                          f"{len(part.text)} 字 {part.file.name}")
        return
    if not autoloop:
        console.print(f"批次 {result.batch_id}：{line}")
        return
    print(line + "\n")
    print(_receipt({"id": f"capture-{result.batch_id}", "type": "capture.ready",
                    "subject": f"capture:{result.batch_id}",
                    "summary": f"{len(result.parts)} 段会话待整理"}))


def run_capture_status_command(*, console: Console) -> None:
    _, config = _capture_config(console)
    state = config.root / "state.json"
    files = json.loads(state.read_text())["files"] if state.exists() else {}
    skipped: dict[str, int] = {}
    for value in files.values():
        if value.get("skip"):
            skipped[value["skip"]] = skipped.get(value["skip"], 0) + 1
    console.print(f"记录了 {len(files)} 个会话文件的进度；跳过：{skipped or '无'}")
    pending = pending_batches(config)
    console.print(f"待整理批次：{', '.join(pending) if pending else '无'}")


def run_brief_collect_command(*, autoloop: bool, no_fetch: bool, console: Console) -> None:
    vault, config = _capture_config(console)
    brief_id, file = collect_brief(vault, config, fetch=not no_fetch)
    data = json.loads(file.read_text(encoding="utf-8"))
    aihot = data.get("aihot", {})
    errors = [k for k in ("daily_error", "items_error") if k in aihot]
    line = (f"日报资料齐了：{len(data['batches'])} 批整理结果、"
            f"{len(data['projects'])} 个项目页更新、AIHOT {len(aihot.get('items', []))} 条")
    if errors:
        line += f"（AIHOT 出错：{errors}）"
    if not autoloop:
        console.print(f"{line}\n{file}")
        return
    print(line + "\n")
    print(_receipt({"id": f"brief-{brief_id}", "type": "brief.ready",
                    "subject": f"brief:{brief_id}", "summary": "今日日报资料已备好"}))
