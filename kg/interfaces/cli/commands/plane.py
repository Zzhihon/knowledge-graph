"""`kg plane` handlers: run a collector into the state store, show what the store holds.

With `autoloop=True` the output is what an agent-autoloop `command` agent prints: a one-line
conclusion, then (only when an incident opened or closed) a receipt block with one event each.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from typing import TYPE_CHECKING

from rich.markup import escape

from kg.infrastructure.config.loader import load_config
from kg.plane import collectors
from kg.plane.collectors import CollectContext, collector_names, get_collector, run_collector
from kg.plane.store import connect, db_path, list_incidents, list_resources

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from kg.plane.collectors import RunResult


def _receipt(events: list[dict[str, str]]) -> str:
    return f"```agent-autoloop\n{json.dumps({'events': events}, ensure_ascii=False)}\n```"


def _vault(console: Console) -> Path:
    try:
        return load_config().vault_path
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)


def _summary(result: RunResult) -> str:
    statuses = "，".join(f"{k} {v}" for k, v in sorted(result.statuses.items())) or "无"
    line = f"{result.collector}：{result.resources} 个资源（{statuses}）"
    moved = [f"新增 {len(result.new)}" if result.new else "",
             f"消失 {len(result.gone)}" if result.gone else "",
             f"重新出现 {len(result.returned)}" if result.returned else ""]
    if any(moved):
        line += "；" + "，".join(m for m in moved if m)
    opened = [c for c in result.changes if c.action == "open"]
    closed = [c for c in result.changes if c.action == "close"]
    line += f"；事故 新开 {len(opened)}、关闭 {len(closed)}"
    if result.notes:
        line += "；注意：" + "；".join(result.notes)
    return line


def _events(result: RunResult) -> list[dict[str, str]]:
    events = []
    for c in result.changes:
        verb = "opened" if c.action == "open" else "closed"
        events.append({"id": f"plane-incident-{c.incident_id}-{verb}",
                       "type": f"plane.incident.{verb}", "subject": c.resource_id,
                       "summary": f"{'出事' if c.action == 'open' else '恢复'}：{c.resource_id} "
                                  f"{c.summary}".strip()})
    return events


def run_plane_collect_command(*, name: str, dry_run: bool, autoloop: bool, console: Console,
                              vault: Path | None = None) -> RunResult:
    vault = vault or _vault(console)
    try:
        collector = get_collector(name)
    except KeyError as exc:
        console.print(f"[red]{exc.args[0]}[/]")
        sys.exit(2)
    path = db_path(vault)
    # A dry run on a machine without a store yet must not create one.
    conn = connect(path if path.exists() or not dry_run else ":memory:")
    try:
        ctx = CollectContext(now=int(time.time()), vault=vault, run=collectors.run_command)
        result = run_collector(conn, collector, ctx=ctx, dry_run=dry_run)
    finally:
        conn.close()
    line = _summary(result)
    if dry_run:
        console.print(f"[yellow]预览，不写入：[/]{line}")
        for c in result.changes:
            verb = "开" if c.action == "open" else "关"
            console.print(f"  会{verb}事故 {c.resource_id} {c.summary}")
        for rid in result.gone:
            console.print(f"  会记消失 {rid}")
        return result
    if not autoloop:
        console.print(line)
        return result
    events = _events(result)
    if not events:
        print(f"没事可做：{line}")
        return result
    print(line + "\n")
    print(_receipt(events))
    return result


def _ago(ts: int | None, now: int) -> str:
    if ts is None:
        return "从未"
    minutes = (now - ts) // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    return f"{minutes // 60} 小时前" if minutes < 48 * 60 else f"{minutes // 1440} 天前"


def run_plane_status_command(*, console: Console, vault: Path | None = None) -> None:
    vault = vault or _vault(console)
    path = db_path(vault)
    if not path.exists():
        console.print(f"状态库还没建：{path}（先跑 kg plane collect <采集器>）")
        console.print(f"采集器：{', '.join(collector_names())}")
        return
    conn = connect(path)
    try:
        live = list_resources(conn)
        gone = [r for r in list_resources(conn, include_gone=True) if r["gone_at"] is not None]
        incidents = list_incidents(conn, open_only=True)
    finally:
        conn.close()
    now = int(time.time())
    by_source: dict[str, list[dict]] = {}
    for r in live:
        by_source.setdefault(r["source"], []).append(r)
    console.print(f"状态库 {path}：{len(live)} 个资源在线，{len(gone)} 个已消失")
    for source, items in sorted(by_source.items()):
        statuses = Counter(r["status"] or "无信号" for r in items)
        last = max(r["last_seen"] for r in items)
        console.print(f"  {source}：{len(items)} 个，"
                      f"{'，'.join(f'{k} {v}' for k, v in sorted(statuses.items()))}；"
                      f"上次采集 {_ago(last, now)}")
    console.print(f"未关闭的事故：{len(incidents) or '无'}")
    for i in incidents:
        console.print(escape(f"  [{i['severity']}] {i['resource_id']} {i['summary']}"
                             f"（{_ago(i['opened_at'], now)}开）"))
    console.print(f"采集器：{', '.join(collector_names())}")
