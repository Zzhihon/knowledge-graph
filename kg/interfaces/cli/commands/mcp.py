"""`kg mcp` handlers: the MCP server for local agents (#10).

  kg mcp serve [--vault DIR] [--db FILE]   stdio MCP server, read-only tools (kg.mcp.server)
  kg mcp tools                             list the tool names, one per line

The group is a plain Click group so the main CLI can `cli.add_command(mcp_group)`; until then
it also runs as `python -m kg.interfaces.cli.commands.mcp <command>`.
"""

from __future__ import annotations

from pathlib import Path

import click

TOOLS = ("search_knowledge", "get_entry", "project_status", "recent_sessions", "plane_health",
         "find_artifact", "similar_incidents")


def _vault(vault: Path | None) -> Path:
    if vault is not None:
        return vault
    from kg.infrastructure.config.loader import load_config

    return load_config().vault_path


@click.group(name="mcp")
def mcp_group() -> None:
    """MCP 服务：让 Claude Code / Codex 查项目状态、知识、工件和健康（只读）。"""


@mcp_group.command(name="serve")
@click.option("--vault", type=click.Path(file_okay=False, path_type=Path),
              help="知识库根目录，默认读配置")
@click.option("--db", type=click.Path(dir_okay=False, path_type=Path),
              help="状态库文件，默认 <vault>/.kg/plane/state.db")
def mcp_serve(vault: Path | None, db: Path | None) -> None:
    """走 stdio 提供 MCP 服务，由 Agent 当子进程启动；不开端口。"""
    from kg.mcp.server import serve
    from kg.plane.store import db_path

    root = _vault(vault)
    serve(root, db or db_path(root))


@mcp_group.command(name="brief")
@click.option("--cwd", type=click.Path(file_okay=False, path_type=Path), default=".",
              help="会话所在目录，默认当前目录")
@click.option("--vault", type=click.Path(file_okay=False, path_type=Path),
              help="知识库根目录，默认读配置")
@click.option("--db", type=click.Path(dir_okay=False, path_type=Path),
              help="状态库文件，默认 <vault>/.kg/plane/state.db")
def mcp_brief(cwd: Path, vault: Path | None, db: Path | None) -> None:
    """会话开场简报：所属项目上次做到哪、未关闭的事故、踩过的坑（SessionStart hook 用）。"""
    from kg.mcp.brief import run
    from kg.plane.store import db_path

    try:
        root = _vault(vault)
        text = run(root, db or db_path(root), cwd.resolve())
    except Exception as exc:  # noqa: BLE001 - a hook must never block the session
        click.echo(f"kg mcp brief 失败：{exc}", err=True)
        return
    if text:
        click.echo(text)


@mcp_group.command(name="tools")
def mcp_tools() -> None:
    """列出工具名（写 --allowedTools 用）。"""
    for name in TOOLS:
        click.echo(name)


if __name__ == "__main__":
    mcp_group()
