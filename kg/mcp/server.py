"""`kg mcp serve`: the control plane as an MCP server over stdio, for any local agent.

Seven read-only tools (see `kg.mcp.tools` for where each answer comes from). Nothing here
writes: card answers, heal actions and collection stay out of MCP. Every result goes through
`kg.mcp.scrub` on its way out, so a key that slipped into an entry, a session or an artifact
does not reach the agent's transcript.

stdio only: the server is a child process of the agent and opens no port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

if TYPE_CHECKING:
    from pathlib import Path

    from kg.mcp.tools import Plane

NAME = "kg"

INSTRUCTIONS = """本机知识库和管控面（只读）。
- 用户问某个项目做到哪了、上次停在哪、有没有未提交改动或未合并 PR：调 project_status。
- 问最近做了什么、哪些会话在等回答：recent_sessions。
- 问之前怎么做 / 怎么修过某件事、要背景知识：search_knowledge，需要全文再 get_entry。
- 问代理出口、本地服务通不通、有没有事故：plane_health。
- 找某份方案页 / 报告 / 分享链接：find_artifact。
- 遇到报错或故障，先看以前有没有类似的：similar_incidents。
返回里带 error 时，把 error.message 和 hint 原样告诉用户，不要当成"没有结果"。
返回里带 warning 时，回答里要提到数据可能过时。"""


BOARD_HELP = "板块：项目 / 通识 / 经验 / 实验 / 排障，或目录名（03-Debug）、条目类型（debug）"


def build_server(plane: Plane) -> Any:
    from mcp.server.mcpserver import MCPServer
    from mcp_types import ToolAnnotations

    from kg.mcp import tools
    from kg.mcp.scrub import scrubber_for

    scrubber = scrubber_for(plane.vault)
    server = MCPServer(NAME, instructions=INSTRUCTIONS, version="0.1.0")
    ro = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                         open_world_hint=False)

    def out(value: dict[str, Any]) -> dict[str, Any]:
        return scrubber.scrub(value)

    @server.tool(annotations=ro)
    def search_knowledge(
        query: Annotated[str, Field(description="要找的内容，中英文都行")],
        board: Annotated[str | None, Field(description=BOARD_HELP)] = None,
        limit: Annotated[int, Field(ge=1, le=20, description="返回几条")] = 5,
        rerank: Annotated[bool, Field(
            description="本地重排：更准但慢（十几秒），默认只做 BM25 + 向量融合")] = False,
    ) -> dict[str, Any]:
        """检索知识库条目（BM25 + 向量融合），返回标题、摘要、路径和条目 id。"""
        return out(tools.search_knowledge(plane, query, board, limit, rerank))

    @server.tool(annotations=ro)
    def get_entry(
        id: Annotated[str, Field(description="条目 id（ke-…、proj-…）或库内相对路径")],  # noqa: A002
    ) -> dict[str, Any]:
        """取一条知识的全文和元数据。"""
        return out(tools.get_entry(plane, id))

    @server.tool(annotations=ro)
    def project_status(
        project: Annotated[str, Field(description="项目名（看板上的名字、仓库名或项目页 id）")],
    ) -> dict[str, Any]:
        """项目当前状态，和"进行中"看板同一份数据：阶段和原因、知识库项目页摘要、最后结论、
        活跃会话、未提交改动、未合并 PR。"""
        return out(tools.project_status(plane, project))

    @server.tool(annotations=ro)
    def recent_sessions(
        project: Annotated[str | None, Field(description="只看这个项目；不填看全部")] = None,
        hours: Annotated[float, Field(gt=0, le=168, description="最近多少小时")] = 24,
    ) -> dict[str, Any]:
        """最近的 Claude Code / Codex 会话，每个会话的状态、最后一句话和最后的结论。"""
        return out(tools.recent_sessions(plane, project, hours))

    @server.tool(annotations=ro)
    def plane_health() -> dict[str, Any]:
        """本地服务、代理出口线路的当前状态，未关闭的事故，检索索引是否落后。"""
        return out(tools.plane_health(plane))

    @server.tool(annotations=ro)
    def find_artifact(
        query: Annotated[str, Field(description="标题或正文里的词")],
    ) -> dict[str, Any]:
        """按标题 / 正文找 Agent 产出的工件（Orca 分享、本地页面等）。

        返回链接、本地文件和过期时间。"""
        return out(tools.find_artifact(plane, query))

    @server.tool(annotations=ro)
    def similar_incidents(
        text: Annotated[str, Field(description="现象、报错或事故描述")],
    ) -> dict[str, Any]:
        """找相似的历史事故（规则、资源、时长、自愈动作、复盘条目）和知识库里的排障条目。"""
        return out(tools.similar_incidents(plane, text))

    return server


def serve(vault: Path, db: Path) -> None:
    from kg.mcp.tools import Plane

    build_server(Plane(vault=vault, db=db)).run("stdio")
