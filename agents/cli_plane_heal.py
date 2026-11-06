"""Click commands for #9, added to the `kg plane` group by `register(plane)` in agents/cli.py.

    kg plane notify --autoloop [--context FILE]
    kg plane report --week | --days N
    kg plane heal list [--incident N] [--json]
    kg plane heal undo <审计 id>
    kg plane incident link <事故 id> <条目 id> [--force]
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

console = Console()


def register(plane: click.Group) -> None:
    @plane.command(name="notify")
    @click.option("--autoloop", is_flag=True, help="按 agent-autoloop 回执格式输出卡片和事件")
    @click.option("--context", "context", type=click.Path(dir_okay=False),
                  help="这一轮的资料文件，默认读 AGENT_AUTOLOOP_CONTEXT")
    def plane_notify(autoloop: bool, context: str | None) -> None:
        """把 plane.incident.* 事件变成卡片、通知和自愈动作。"""
        from kg.interfaces.cli.commands.plane_heal import run_plane_notify_command

        run_plane_notify_command(autoloop=autoloop, context=context, console=console)

    @plane.command(name="report")
    @click.option("--week", is_flag=True, help="最近 7 天（默认）")
    @click.option("--days", type=int, default=None, help="最近 N 天")
    def plane_report(week: bool, days: int | None) -> None:
        """每个资源的可用性、事故次数和时长、错误预算剩余（Markdown）。"""
        from kg.interfaces.cli.commands.plane_heal import run_plane_report_command

        run_plane_report_command(days=days or 7, console=console)

    @plane.group(name="heal")
    def plane_heal() -> None:
        """自愈动作的审计记录和撤销。"""

    @plane_heal.command(name="list")
    @click.option("--incident", type=int, default=None)
    @click.option("--json", "as_json", is_flag=True)
    def plane_heal_list(incident: int | None, as_json: bool) -> None:
        """列出自愈审计记录。"""
        from kg.interfaces.cli.commands.plane_heal import run_heal_list_command

        run_heal_list_command(incident=incident, as_json=as_json, console=console)

    @plane_heal.command(name="undo")
    @click.argument("audit_id")
    def plane_heal_undo(audit_id: str) -> None:
        """撤销一条自愈动作（恢复暂停的规则、取消静默、索引标回正常）。"""
        from kg.interfaces.cli.commands.plane_heal import run_heal_undo_command

        if not run_heal_undo_command(audit_id=audit_id, console=console):
            sys.exit(1)

    @plane.group(name="incident")
    def plane_incident() -> None:
        """事故和知识条目的关联。"""

    @plane_incident.command(name="link")
    @click.argument("incident_id", type=int)
    @click.argument("entry_id")
    @click.option("--force", is_flag=True, help="条目不存在或没通过格式校验也写")
    def plane_incident_link(incident_id: int, entry_id: str, force: bool) -> None:
        """把复盘条目 id 写回事故（incident.entry_id）。"""
        from kg.interfaces.cli.commands.plane_heal import run_incident_link_command

        if not run_incident_link_command(incident_id=incident_id, entry_id=entry_id,
                                         force=force, console=console):
            sys.exit(1)
