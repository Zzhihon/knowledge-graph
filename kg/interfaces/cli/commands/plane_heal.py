"""`kg plane notify / report / heal / incident` handlers (#9).

`notify --autoloop` is what the agent-autoloop `command` agent for incident events runs: it reads
the round's context from AGENT_AUTOLOOP_CONTEXT (or `--context`) and prints a one-line conclusion
plus the receipt block (cards, notices, resolves, timers, retro events).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from kg.plane.heal import actions, audit
from kg.plane.heal.config import load_config as load_heal_config
from kg.plane.heal.hub_actions import HttpHubActions
from kg.plane.heal.notify import Notifier, read_context
from kg.plane.heal.report import to_markdown, weekly_report
from kg.plane.heal.retro import find_entry, get_incident, link_entry, validate_retro_entry
from kg.plane.store import connect, db_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from rich.console import Console

    from kg.plane.heal.hub_actions import HubActions


def _vault(console: Console) -> Path:
    from kg.infrastructure.config.loader import load_config

    try:
        return load_config().vault_path
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)


def _hub(vault: Path) -> HubActions:
    config = load_heal_config(vault)
    return HttpHubActions(config.hub_config, config.hub_timeout)


def run_plane_notify_command(*, autoloop: bool, console: Console, context: str | None = None,
                             vault: Path | None = None, hub: HubActions | None = None,
                             runner: Callable[..., str] | None = None,
                             now: int | None = None) -> str:
    vault = vault or _vault(console)
    path = context or os.environ.get("AGENT_AUTOLOOP_CONTEXT")
    data = read_context(path)
    config = load_heal_config(vault)
    store = db_path(vault)
    if not store.exists():
        out = "没事可做：状态库还没建"
        print(out) if autoloop else console.print(out)
        return out
    conn = connect(store)
    try:
        notifier = Notifier(conn, vault, config, int(time.time()) if now is None else now,
                            run=os.environ.get("AGENT_AUTOLOOP_RUN", ""),
                            hub=hub if hub is not None else _hub(vault), runner=runner)
        receipt = notifier.handle(data)
    finally:
        conn.close()
    out = receipt.render()
    if autoloop:
        print(out)
    else:
        console.print(escape(out))
    return out


def run_plane_report_command(*, days: int, console: Console, vault: Path | None = None,
                             now: int | None = None) -> str:
    vault = vault or _vault(console)
    store = db_path(vault)
    if not store.exists():
        console.print(f"状态库还没建：{store}")
        return ""
    conn = connect(store)
    try:
        now = int(time.time()) if now is None else now
        text = to_markdown(weekly_report(conn, load_heal_config(vault), now=now, days=days),
                           now=now, days=days)
    finally:
        conn.close()
    print(text)
    return text


def _ctx(vault: Path, hub: HubActions | None) -> actions.HealContext:
    return actions.HealContext(vault, load_heal_config(vault), int(time.time()),
                               hub if hub is not None else _hub(vault))


def run_heal_undo_command(*, audit_id: str, console: Console, vault: Path | None = None,
                          hub: HubActions | None = None) -> bool:
    vault = vault or _vault(console)
    try:
        outcome = actions.undo(_ctx(vault, hub), audit_id)
    except ValueError as exc:
        console.print(f"[red]{escape(str(exc))}[/]")
        return False
    style = "green" if outcome.result == audit.DONE else "red"
    console.print(f"[{style}]{escape(outcome.text)}[/]（审计 {outcome.audit_id or '—'}）")
    return outcome.result == audit.DONE


def entry_view(e: audit.AuditEntry, undone: set[str]) -> dict[str, Any]:
    item = e.to_dict()
    item["undone"] = e.id in undone
    item["undoable"] = (e.result == audit.DONE and not e.undo_of and e.id not in undone
                        and e.action in ("pause_capability", "index_stale", "silence"))
    return item


def run_heal_list_command(*, console: Console, incident: int | None = None, as_json: bool = False,
                          vault: Path | None = None, limit: int = 30) -> list[dict[str, Any]]:
    vault = vault or _vault(console)
    entries = audit.read(vault)
    undone = audit.undone_ids(entries)
    picked = [e for e in entries if incident is None or e.incident == incident][-limit:]
    items = [entry_view(e, undone) for e in picked]
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=1))
        return items
    if not items:
        console.print("还没有自愈记录")
    for i in items:
        flag = "（已撤销）" if i["undone"] else ("（可撤销）" if i["undoable"] else "")
        console.print(escape(f"{i['id']}  #{i['incident']}  {i['action']} {i['target']}  "
                             f"{i['mode']}/{i['result']}{flag}"))
    return items


def run_incident_link_command(*, incident_id: int, entry_id: str, force: bool, console: Console,
                              vault: Path | None = None) -> bool:
    vault = vault or _vault(console)
    entry = find_entry(vault, entry_id)
    if entry is None and not force:
        console.print(f"[red]知识库里找不到条目 {escape(entry_id)}（先写条目，或加 --force）[/]")
        return False
    if entry is not None:
        problems = validate_retro_entry(entry.read_text(encoding="utf-8"))
        if problems and not force:
            console.print("[red]条目没通过格式校验：[/]" + escape("；".join(problems)))
            return False
    conn = connect(db_path(vault))
    try:
        if get_incident(conn, incident_id) is None:
            console.print(f"[red]没有事故 #{incident_id}[/]")
            return False
        try:
            link_entry(conn, incident_id, entry_id)
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/]")
            return False
    finally:
        conn.close()
    console.print(f"事故 #{incident_id} → {escape(entry_id)}")
    return True
