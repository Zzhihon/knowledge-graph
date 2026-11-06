"""MCP server (#10): every tool on a synthetic vault + state store, the secret filter, and the
stdio protocol end to end (`kg mcp serve` as a child process).

Secrets are planted in every place a tool reads from (entry bodies and front matter, session
messages, project conclusions, artifact titles / links / local files, incident summaries and
evidence, search snippets); no tool may return any of them.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from kg.mcp import tools
from kg.mcp.scrub import Scrubber
from kg.mcp.server import build_server
from kg.plane.store import (
    Resource,
    Signal,
    append_signals,
    close_incident,
    connect,
    open_incident,
    upsert_resources,
)
from kg.retrieval.errors import IndexStaleError
from kg.retrieval.search import Hit, Result

ROOT = Path(__file__).resolve().parents[1]
NOW = int(time.time())  # get_projects judges "waiting" against the real clock

# Built from pieces so no scanner mistakes this file for a leak.
SECRETS = {
    "openai": "sk-" + "proj" + "Q7w8E9r0T1y2U3i4O5p6",
    "github": "gh" + "p_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6",
    "aws": "AKIA" + "Q3EXAMPLE7KEY9ABCD",
    "jwt": "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0",
    "password": "Hunter2" + "pass99",
    "bearer": "Kq8" + "Zr2Lm5Np7Xw3Yt6",
    "random": "Zx9Qw8Er7Ty6" + "Ui5Op4As3Df2Gh1Jk0LmNbV",
    "hex": "9f86d081884c7d659a2feaa0c55ad015" + "a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "ip": "10.23." + "45.67",
    "home": "/Users/" + "someone",
    "internal": "build01.corp",
    "query": "sig=" + "Qm9vb3Bz12345678",
}


def leak_text() -> str:
    s = SECRETS
    return (f"key {s['openai']} and {s['github']}, aws {s['aws']}, jwt {s['jwt']}, "
            f"password: {s['password']}, Authorization: Bearer {s['bearer']}, id {s['random']}, "
            f"sha {s['hex']}, host {s['ip']} / {s['internal']}, dir {s['home']}/repo, "
            f"see https://example.com/doc?{s['query']}")


def assert_clean(value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False)
    for name, secret in SECRETS.items():
        assert secret not in text, f"{name} leaked: {secret}"


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────


def write_entry(vault: Path, rel: str, meta: dict[str, Any], body: str) -> None:
    import yaml

    file = vault / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n\n{body}\n",
                    encoding="utf-8")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    write_entry(root, "03-Debug/ke-20261101-egress-tls-outage.md",
                {"id": "ke-20261101-egress-tls-outage", "title": "出口 TLS 握手失败复盘",
                 "type": "debug", "status": "draft", "updated": "2026-11-01",
                 "note": f"token: {SECRETS['github']}"},
                "## 现象\n\n出口连续 TLS 握手失败。\n\n" + leak_text())
    write_entry(root, "10-Experience/xp-20261102-named-differently.md",
                {"id": "xp-renamed", "title": "文件名和 id 不一样", "type": "experience"},
                "正文")
    write_entry(root, "07-Projects/proj-demo.md",
                {"id": "proj-demo", "title": "Demo 项目", "status": "active",
                 "aliases": ["demo"]},
                "## Current State\n\n> [!info] 当前状态\n> **阶段**：联调。下一步接入 MCP。\n")
    write_entry(root, "07-Projects/proj-paper-only.md",
                {"id": "proj-paper-only", "title": "只有项目页", "status": "active"},
                "## Current State\n\n> [!info] 当前状态\n> **阶段**：调研。\n")
    return root


def _seed(db: Path, vault: Path) -> None:
    art_file = vault.parent / "pages" / "plan.html"
    art_file.parent.mkdir(parents=True)
    art_file.write_text("<html><head><title>MCP 接入方案</title></head><body><h1>MCP 接入方案"
                        f"</h1><p>统一入口的设计。{leak_text()}</p></body></html>",
                        encoding="utf-8")
    session = {
        "source": "claude", "session_id": "0b5f3e2a-1111-4222-8333-944455556666",
        "project": "demo", "cwd": f"{SECRETS['home']}/demo", "branch": "main",
        "started": NOW - 7200, "last_active": NOW - 600, "turns": 12,
        "last_user": f"把 {SECRETS['openai']} 换掉", "last_agent": "已改完，等你确认",
        "last_agent_tail": f"已改完。password: {SECRETS['password']}", "state": "waiting",
        "waiting_reason": "最后一步在问你",
        "resume": f"cd {SECRETS['home']}/demo && claude --resume 0b5f3e2a",
    }
    old = {**session, "session_id": "0b5f3e2a-old", "last_active": NOW - 5 * 86400,
           "state": "done", "last_user": "旧会话", "last_agent_tail": "旧结论"}
    project = {
        "name": "demo", "stage": "waiting", "stage_label": "等你", "reason": "1 个会话在等你",
        "last_action": NOW - 600,
        "sessions": {"total": 2, "recent": 1, "working": 0, "waiting": 1, "done": 1,
                     "interrupted": 0},
        "last_conclusion": {"text": f"接好了 {SECRETS['aws']}", "ts": NOW - 600,
                            "session": session["session_id"], "source": "claude"},
        "repos": [{"path": f"{SECRETS['home']}/demo", "name": "demo", "branch": "main",
                   "upstream": "origin/main", "ahead": 1, "behind": 0, "dirty": 3,
                   "changed": 2, "untracked": 1, "conflicts": 0, "last_commit": NOW - 3600,
                   "github": "owner/demo"}],
        "dirty": 3, "open_prs": 1,
        "prs": [{"number": 7, "title": "接 MCP", "url": "https://github.com/owner/demo/pull/7"}],
        "orca": None,
        "kb": {"path": "07-Projects/proj-demo.md", "title": "Demo 项目", "status": "active",
               "state": "联调。下一步接入 MCP。", "as_of": "", "updated": ""},
        "collected_at": NOW - 60,
    }
    conn = connect(db)
    with conn:
        upsert_resources(conn, [
            Resource("project:demo", "project", "demo", "sessions", project),
            Resource("project:other", "project", "other", "sessions",
                     {**project, "name": "other", "stage": "idle", "stage_label": "歇着",
                      "last_action": NOW - 86400 * 2}),
            Resource(f"session:claude:{session['session_id']}", "session", "demo · 换钥匙",
                     "sessions", session),
            Resource("session:claude:0b5f3e2a-old", "session", "demo · 旧", "sessions", old),
            Resource("artifact:local:plan", "artifact", "MCP 接入方案", "artifacts",
                     {"title": "MCP 接入方案", "origin": "local", "adapter": "local",
                      "visibility": "public",
                      "url": f"https://share.example.com/a/plan?{SECRETS['query']}",
                      "local_file": str(art_file), "updated_at": NOW - 3600,
                      "expires_at": NOW + 2 * 86400, "project": "demo"}),
            Resource("artifact:orca:leaky", "artifact", "带钥匙的链接", "artifacts",
                     {"title": "带钥匙的链接 MCP", "visibility": "public",
                      "url": f"https://user:{SECRETS['password']}@share.example.com/x"}),
            Resource("egress:main", "egress", "main", "egress", {}),
            Resource("service:launchd:com.example.web", "service", "com.example.web",
                     "services", {}),
        ], now=NOW - 60)
        append_signals(conn, [
            Signal("project:demo", NOW - 60, "degraded", 1, "sessions", {"stage": "waiting"}),
            Signal("egress:main", NOW - 60, "down", 900, "ms", {"exit": 35}),
            Signal("service:launchd:com.example.web", NOW - 60, "up"),
        ])
        open_incident(conn, resource_id="egress:main", rule="egress.outage",
                      opened_at=NOW - 1800, severity="warning",
                      summary=f"出口断线：TLS 握手失败 {SECRETS['ip']}",
                      evidence={"exit": 35, "note": leak_text()})
        open_incident(conn, resource_id="service:launchd:com.example.web", rule="service.down",
                      opened_at=NOW - 86400 * 3, severity="critical", summary="服务没在运行")
        close_incident(conn, resource_id="service:launchd:com.example.web", rule="service.down",
                       closed_at=NOW - 86400 * 3 + 1200)
        conn.execute("UPDATE incident SET entry_id = ? WHERE rule = ?",
                     ("ke-20261101-egress-tls-outage", "service.down"))
    conn.close()


@pytest.fixture
def plane(vault: Path, tmp_path: Path) -> tools.Plane:
    db = tmp_path / "state.db"
    _seed(db, vault)
    return tools.Plane(vault=vault, db=db, search_fn=stub_search, clock=lambda: NOW)


def stub_search(query: str, top_k: int, filters: dict[str, str] | None,
                rerank: bool) -> Result:
    hits = [
        Hit("ke-20261101-egress-tls-outage", "出口 TLS 握手失败复盘",
            "03-Debug/ke-20261101-egress-tls-outage.md", "现象", leak_text(), "debug",
            ["network"], 0.9),
        Hit("xp-renamed", "文件名和 id 不一样", "10-Experience/xp-20261102-named-differently.md",
            "", "正文", "experience", [], 0.5),
    ]
    if filters and filters.get("type"):
        hits = [h for h in hits if h.type == filters["type"]]
    return Result(hits[:top_k], [], {"total_ms": 1.0, "rerank": float(rerank)})


def call(plane: tools.Plane, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a tool through the MCP server, as an agent would; return the structured result."""
    server = build_server(plane)
    result = asyncio.run(server.call_tool(name, args))
    assert not result.is_error, result
    text = result.content[0].text
    data = json.loads(text)
    assert_clean(text)
    if result.structured_content is not None:
        assert_clean(result.structured_content)
    return data


# ── the filter ────────────────────────────────────────────────────────────────────────────────


def test_scrubber_masks_every_planted_secret_and_keeps_ids_readable() -> None:
    s = Scrubber(("corp",))
    slug = "ke-20260228-containerd-cri-plugin-mirrors-config-path-conflict"
    out = s.scrub({"text": leak_text(), f"key {SECRETS['openai']}": [leak_text()],
                   "id": slug, "path": f"03-Debug/{slug}.md",
                   "local": "listening on http://127.0.0.1:8765/api"})
    assert_clean(out)
    assert out["id"] == "ke-20260228-containerd-cri-plugin-mirrors-config-path-conflict"
    assert out["path"].endswith("path-conflict.md")
    assert "~/repo" in out["text"]
    assert "http://127.0.0.1:8765/api" in out["local"]


def test_scrubber_keeps_share_links_without_query_or_userinfo() -> None:
    s = Scrubber()
    assert s.url("https://share.example.com/a/plan") == "https://share.example.com/a/plan"
    kept = s.url(f"https://share.example.com/a/plan?{SECRETS['query']}")
    assert kept.startswith("https://share.example.com/a/plan?") and SECRETS["query"] not in kept
    assert s.url(f"https://u:{SECRETS['password']}@h.example.com/") == "‹链接›"
    assert s.url(f"http://{SECRETS['ip']}/x") == "‹链接›"
    assert s.url(f"https://h.example.com/{SECRETS['openai']}") == "‹链接›"
    assert s.url("https://build01.corp/x") == "‹链接›"


# ── tools ─────────────────────────────────────────────────────────────────────────────────────


def test_search_knowledge_returns_hits_and_filters_by_board(plane: tools.Plane) -> None:
    data = call(plane, "search_knowledge", {"query": "出口断线"})
    assert [r["id"] for r in data["results"]] == ["ke-20261101-egress-tls-outage", "xp-renamed"]
    assert data["results"][0]["board"] == "排障" and data["mode"] == "hybrid"
    data = call(plane, "search_knowledge", {"query": "x", "board": "经验", "limit": 3})
    assert [r["id"] for r in data["results"]] == ["xp-renamed"]
    data = call(plane, "search_knowledge", {"query": "x", "board": "debug", "rerank": True})
    assert [r["board"] for r in data["results"]] == ["排障"]
    assert data["mode"] == "hybrid+rerank"
    data = call(plane, "search_knowledge", {"query": "x", "board": "不存在"})
    assert data["error"]["code"] == "unknown_board" and data["results"] == []


def test_search_knowledge_passes_the_retrieval_error_through(plane: tools.Plane) -> None:
    def broken(*_: Any) -> Result:
        raise IndexStaleError("索引落后：新增 3 个文件还没进索引", hint="运行 kg index update")

    plane.search_fn = broken
    data = call(plane, "search_knowledge", {"query": "x"})
    assert data["results"] == []
    assert data["error"] == {"code": "index_stale",
                             "message": "索引落后：新增 3 个文件还没进索引",
                             "hint": "运行 kg index update"}


def test_get_entry_by_id_path_and_frontmatter_id(plane: tools.Plane) -> None:
    data = call(plane, "get_entry", {"id": "ke-20261101-egress-tls-outage"})
    assert data["title"] == "出口 TLS 握手失败复盘" and "TLS 握手失败" in data["body"]
    assert data["path"] == "03-Debug/ke-20261101-egress-tls-outage.md"
    assert call(plane, "get_entry", {"id": "xp-renamed"})["path"].endswith(
        "named-differently.md")
    assert call(plane, "get_entry", {"id": "07-Projects/proj-demo.md"})["id"] == "proj-demo"
    assert call(plane, "get_entry", {"id": "../../etc/passwd"})["error"]["code"] == \
        "entry_not_found"
    assert call(plane, "get_entry", {"id": "nope"})["error"]["code"] == "entry_not_found"


def test_project_status_matches_the_board(plane: tools.Plane) -> None:
    from agents.api_routes.sessions import get_projects

    data = call(plane, "project_status", {"project": "Demo"})
    board = next(p for p in get_projects(plane.db)["items"] if p["name"] == "demo")
    assert data["project"] == "demo"
    for key in ("stage", "stage_label", "reason", "dirty", "open_prs", "sessions"):
        assert data[key] == board[key], key
    assert data["kb"]["state"] == "联调。下一步接入 MCP。"
    assert data["prs"][0]["title"] == "接 MCP"
    assert data["prs"][0]["url"] == "https://github.com/owner/demo/pull/7"
    assert [s["state"] for s in data["active_sessions"]] == ["waiting"]
    assert data["active_sessions"][0]["resume"].startswith("cd ~/demo")
    assert data["repos"][0]["dirty"] == 3
    # Also by the project page id.
    assert call(plane, "project_status", {"project": "proj-demo"})["project"] == "demo"


def test_project_status_falls_back_to_the_project_page_or_says_what_exists(
        plane: tools.Plane) -> None:
    data = call(plane, "project_status", {"project": "paper-only"})
    assert data["kb"]["state"] == "调研。" and "warning" in data
    data = call(plane, "project_status", {"project": "nothing-like-this"})
    assert data["error"]["code"] == "project_not_found"
    assert sorted(data["known_projects"]) == ["demo", "other"]


def test_project_status_without_a_store(vault: Path, tmp_path: Path) -> None:
    plane = tools.Plane(vault=vault, db=tmp_path / "missing.db", search_fn=stub_search)
    data = call(plane, "project_status", {"project": "demo"})
    assert data["kb"]["title"] == "Demo 项目"
    assert "状态库还没建" in data["warning"]
    assert not (tmp_path / "missing.db").exists()


def test_recent_sessions_window_and_project(plane: tools.Plane) -> None:
    data = call(plane, "recent_sessions", {})
    assert data["count"] == 1 and data["sessions"][0]["state"] == "waiting"
    assert data["sessions"][0]["conclusion"].startswith("已改完")
    data = call(plane, "recent_sessions", {"project": "demo", "hours": 168})
    assert data["count"] == 2
    data = call(plane, "recent_sessions", {"project": "zzz"})
    assert data["error"]["code"] == "project_not_found"


def test_plane_health_reports_groups_incidents_and_index(plane: tools.Plane) -> None:
    data = call(plane, "plane_health", {})
    groups = {g["id"]: g for g in data["groups"]}
    assert groups["egress"]["status"] == "down" and groups["services"]["status"] == "up"
    assert [i["rule"] for i in data["open_incidents"]] == ["egress.outage"]
    assert data["open_incidents"][0]["minutes_open"] == 30
    assert data["egress"][0]["status"] == "down"
    assert data["waiting_sessions"] == 1
    assert data["index"]["built"] is False and data["index"]["error"]["code"] == "index_missing"


def test_find_artifact_by_title_and_body(plane: tools.Plane) -> None:
    data = call(plane, "find_artifact", {"query": "MCP 接入方案"})
    top = data["results"][0]
    assert top["id"] == "artifact:local:plan"
    assert top["url"].startswith("https://share.example.com/a/plan?")
    assert top["days_left"] == 2.0 and top["expired"] is False
    assert "统一入口" in call(plane, "find_artifact", {"query": "统一入口"})["results"][0][
        "snippet"]
    leaky = next(r for r in data["results"] if r["id"] == "artifact:orca:leaky")
    assert leaky["url"] == "‹链接›"
    assert call(plane, "find_artifact", {"query": "毫不相干"})["results"] == []


def test_similar_incidents_ranks_by_overlap_and_attaches_retro(plane: tools.Plane) -> None:
    data = call(plane, "similar_incidents", {"text": "出口 TLS 握手失败"})
    assert data["incidents"][0]["rule"] == "egress.outage"
    assert data["incidents"][0]["open"] is True
    data = call(plane, "similar_incidents", {"text": "com.example.web 服务没在运行"})
    first = data["incidents"][0]
    assert first["rule"] == "service.down" and first["minutes"] == 20
    assert first["retro"] == {"id": "ke-20261101-egress-tls-outage",
                              "path": "03-Debug/ke-20261101-egress-tls-outage.md"}
    assert [e["id"] for e in data["retro_entries"]] == ["ke-20261101-egress-tls-outage"]


def test_every_tool_is_read_only_and_listed(plane: tools.Plane) -> None:
    from kg.interfaces.cli.commands.mcp import TOOLS

    listed = asyncio.run(build_server(plane).list_tools())
    assert sorted(t.name for t in listed) == sorted(TOOLS)
    assert all(t.annotations and t.annotations.read_only_hint for t in listed)


def test_no_tool_returns_a_planted_secret(plane: tools.Plane) -> None:
    calls = {
        "search_knowledge": {"query": "出口"},
        "get_entry": {"id": "ke-20261101-egress-tls-outage"},
        "project_status": {"project": "demo"},
        "recent_sessions": {"hours": 168},
        "plane_health": {},
        "find_artifact": {"query": "MCP"},
        "similar_incidents": {"text": "出口 TLS"},
    }
    for name, args in calls.items():
        data = call(plane, name, args)  # call() asserts the raw text is clean
        assert "error" not in data, (name, data.get("error"))


# ── protocol, end to end ──────────────────────────────────────────────────────────────────────


def _rpc(proc: subprocess.Popen[str], msg: dict[str, Any]) -> dict[str, Any] | None:
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if "id" not in msg:
        return None
    deadline = time.time() + 60
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        reply = json.loads(line)
        if reply.get("id") == msg["id"]:
            return reply
    raise AssertionError(f"no reply to {msg['method']}")


def test_kg_mcp_serve_over_stdio(plane: tools.Plane) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "kg.interfaces.cli.commands.mcp", "serve",
         "--vault", str(plane.vault), "--db", str(plane.db)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        cwd=ROOT, env=env)
    try:
        init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}}})
        assert init and init["result"]["serverInfo"]["name"] == "kg"
        assert "project_status" in init["result"]["instructions"]
        _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert listed
        names = {t["name"] for t in listed["result"]["tools"]}
        assert names == {"search_knowledge", "get_entry", "project_status", "recent_sessions",
                         "plane_health", "find_artifact", "similar_incidents"}
        reply = _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "project_status",
                                       "arguments": {"project": "demo"}}})
        assert reply and not reply["result"].get("isError")
        text = reply["result"]["content"][0]["text"]
        assert_clean(text)
        assert json.loads(text)["stage"] == "waiting"
        bad = _rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "drop_tables", "arguments": {}}})
        assert bad and ("error" in bad or bad["result"].get("isError"))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ── session-start brief ───────────────────────────────────────────────────────────────────────


def test_brief_for_a_known_folder_is_short_clean_and_matches_project_status(
        plane: tools.Plane, tmp_path: Path) -> None:
    from kg.mcp.brief import MAX_LINES, run

    work = tmp_path / "work"
    (work / "demo" / "src").mkdir(parents=True)
    (plane.vault / ".kg" / "capture").mkdir(parents=True)
    (plane.vault / ".kg" / "capture" / "config.yaml").write_text(
        f"allow:\n  - path: {work}\n    container: true\n", encoding="utf-8")
    text = run(plane.vault, plane.db, work / "demo" / "src")
    lines = text.splitlines()
    assert lines[0].startswith("## kg：demo 的现状")
    assert any(line.startswith("- 阶段：等你，1 个会话在等你") for line in lines)
    assert any("有会话在等你回答" in line for line in lines)
    assert any("3 处未提交" in line for line in lines)
    assert any("未合并 PR 1 个：#7 接 MCP" in line for line in lines)
    assert any("未关闭事故" in line and "egress" not in line for line in lines)
    assert len(lines) <= MAX_LINES
    assert_clean(text)
    # Not a known project, or no store: nothing at all, so the hook adds no noise.
    assert run(plane.vault, plane.db, tmp_path) == ""
    assert run(plane.vault, tmp_path / "missing.db", work / "demo") == ""


def test_brief_command_never_fails_the_hook(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from kg.interfaces.cli.commands.mcp import mcp_group

    result = CliRunner().invoke(mcp_group, ["brief", "--cwd", str(tmp_path), "--vault",
                                            str(tmp_path / "nope")])
    assert result.exit_code == 0 and result.output == ""
