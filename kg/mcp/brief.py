"""Session-start brief: "where did this project stop, what is broken, what bit us before".

    kg mcp brief [--cwd DIR]

Meant for a Claude Code `SessionStart` hook (example in
`integrations/mcp/session-start-hook.json.example`): its stdout becomes context for the new
session, so the owner does not have to explain the background again. It uses the same functions
as the MCP tools `project_status`, `plane_health` and `similar_incidents`, without the search
index (no model load, well under a second), and goes through the same secret filter.

It prints nothing and exits 0 when the folder is not a known project or the store is missing:
a hook must never block a session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kg.mcp import tools

if TYPE_CHECKING:
    from pathlib import Path

MAX_LINES = 20


def _line(text: Any, limit: int) -> str:
    return " ".join(tools.cut(text, limit).split())


def project_for_cwd(vault: Path, cwd: Path) -> str | None:
    """The board's project name for a folder, by the same allow list the collector uses."""
    from kg.capture.scan import load_capture_config, project_for

    try:
        config = load_capture_config(vault)
    except (FileNotFoundError, ValueError):
        return None
    return project_for(str(cwd), config)


def brief(plane: tools.Plane, project: str) -> list[str]:
    status = tools.project_status(plane, project)
    if "error" in status:
        return []
    lines = [f"## kg：{status['project']} 的现状（{status.get('collected_at') or '未采集'}）"]
    head = f"- 阶段：{status.get('stage_label') or '?'}，{status.get('reason') or ''}"
    lines.append(head.rstrip("，"))
    kb = status.get("kb") or {}
    if kb.get("state"):
        lines.append(f"- 项目页：{kb['state']}（{kb.get('path')}）")
    last = status.get("last_conclusion") or {}
    if last.get("text"):
        lines.append(f"- 上次结论（{last.get('ts')}）：{_line(last['text'], 240)}")
    for s in status.get("active_sessions") or []:
        if s.get("state") == "waiting":
            lines.append(f"- 有会话在等你回答：{_line(s.get('last_user'), 80)}"
                         f"（{s.get('resume') or s.get('id')}）")
    repos = [r for r in status.get("repos") or [] if r.get("dirty") or r.get("ahead")]
    for r in repos[:3]:
        lines.append(f"- 仓库 {r['name']}（{r.get('branch')}）：{r.get('dirty') or 0} 处未提交，"
                     f"领先 {r.get('ahead') or 0}、落后 {r.get('behind') or 0}")
    if status.get("open_prs"):
        titles = "；".join(f"#{p.get('number')} {p.get('title')}" for p in status["prs"][:3])
        lines.append(f"- 未合并 PR {status['open_prs']} 个：{titles}")
    health = tools.plane_health(plane)
    for inc in health["open_incidents"]:
        if inc["severity"] in ("critical", "warning"):
            lines.append(f"- 未关闭事故 #{inc['id']}（{inc['severity']}）：{inc['summary']}，"
                         f"已 {inc['minutes_open']} 分钟")
    if last.get("text"):
        past = tools.similar_incidents(plane, last["text"], 2, with_entries=False)
        for inc in past["incidents"]:
            if not inc["open"] and inc["score"] >= 3:
                retro = (inc.get("retro") or {}).get("path")
                lines.append(f"- 以前遇到过：#{inc['id']} {inc['summary']}"
                             + (f"，复盘 {retro}" if retro else ""))
    if status.get("warning"):
        lines.append(f"- 注意：{status['warning']}")
    lines.append("细节用 kg MCP 工具 project_status / similar_incidents 查。")
    return lines[:MAX_LINES]


def run(vault: Path, db: Path, cwd: Path) -> str:
    from kg.mcp.scrub import scrubber_for

    if not db.exists():
        return ""
    project = project_for_cwd(vault, cwd)
    if not project:
        return ""
    plane = tools.Plane(vault=vault, db=db)
    lines: list[Any] = brief(plane, project)
    return scrubber_for(vault).text("\n".join(lines)) if lines else ""
