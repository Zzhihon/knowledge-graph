"""The in-progress board: session digests, waiting / stage judgements, the collector and API.

Every session here is synthetic; real session content never goes into tests.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kg.plane import progress, session_reader, store
from kg.plane.collectors import CollectContext, CommandError, run_collector
from kg.plane.collectors.sessions import SessionsCollector, project_stalled
from kg.plane.progress import (
    DAY,
    HOUR,
    current_state,
    excerpt,
    is_question,
    judge_project,
    parse_git_status,
    parse_orca_projects,
    parse_orca_ps,
    redact,
    session_state,
)
from kg.plane.rules import Rule, replay
from kg.plane.session_reader import Digest, read_session
from kg.plane.store import Signal

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000
FAKE_KEY = "sk-" + "a1B2c3D4e5F6g7H8i9J0"  # shaped like a key, not one
FAKE_GH = "ghp_" + "Z" * 30


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def claude(kind: str, content: Any, ts: int, *, sid: str = "s-1", cwd: str = "/w/app",
           **extra: Any) -> dict[str, Any]:
    return {"type": kind, "sessionId": sid, "cwd": cwd, "gitBranch": "main", "timestamp": iso(ts),
            "message": {"role": kind, "content": content}, **extra}


def say(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def write(path: Path, records: list[dict[str, Any]], *, mode: str = "w") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def codex_meta(sid: str, cwd: str, ts: int, **payload: Any) -> dict[str, Any]:
    return {"type": "session_meta", "timestamp": iso(ts),
            "payload": {"id": sid, "cwd": cwd, "originator": "codex-tui", "source": "cli",
                        **payload}}


def codex_msg(role: str, text: str, ts: int) -> dict[str, Any]:
    kind = "input_text" if role == "user" else "output_text"
    return {"type": "response_item", "timestamp": iso(ts),
            "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}}


# ── redaction ──


def test_redact_removes_links_keys_assigned_secrets_and_ips() -> None:
    text = (f"看 https://example.com/a?b=1 ，key {FAKE_KEY}，{FAKE_GH}，password: hunter2x，"
            "出口 203.0.113.7 正常，版本 1.2.3 不动")
    out = redact(text)
    for leaked in ("example.com", FAKE_KEY, FAKE_GH, "hunter2x", "203.0.113.7"):
        assert leaked not in out
    assert "〔链接〕" in out and "〔IP〕" in out and "1.2.3" in out


def test_excerpt_head_and_tail() -> None:
    text = "第一行\n\n\n第二行   有空格\n" + "x" * 50
    assert excerpt(text, 100) == "第一行\n第二行 有空格\n" + "x" * 50
    assert excerpt("abcdef", 3) == "abc…"
    assert excerpt("abcdef", 3, tail=True) == "…def"


# ── waiting ──


@pytest.mark.parametrize("text", [
    "改完了，测试都过了。要不要我顺手把文档也更新？",
    "两个方案都写在上面了。\n\n你想先做哪个？",
    "## 需要你决定\n1. 合并 #12\n2. 选方案 A 还是 B",
    "已经起草好了。\n**需要你来做的：**\n- 试听一下旁白",
    "重启好之后发我一句，我再接着查。",
    "Done with the refactor. Which option do you prefer — A or B?",
    "报告在上面。\n选哪个告诉我，我就开始写。",
    "结论如上。还在等你确认企业微信那 11G 要不要删。",
    "Hi! What are you working on today?\n\nSome tools did not connect; most tasks work anyway.",
    "这一步会改签名，建议你自己确认后再做。\n\n" + "说明。" * 200,
    # A decision put to you as a heading plus a yes / no, with no "选哪个" wording. The
    # question is what blocks the work; a whitelist of decision verbs cannot be required here.
    "## 需要你处理\n- 审阅并合并 #127 和 #1544\n"
    "- 仍然需要你决定：CPU 上限要不要调高？",
    # Go-ahead questions use all sorts of verbs. A whitelist of decision verbs dropped these
    # three real ones from the blind review (the agent stopped until the answer came).
    "改动已经整理好。要我现在改吗？",
    "要我先把哪块做起来？可选：① 刷新条目；② 起草 yaml；③ 整理配置步骤。",
    "P0 和 P1 里，ops 侧的两项我可以直接写 PR。你想先做哪些？",
])
def test_is_question_true(text: str) -> None:
    assert is_question(text)


@pytest.mark.parametrize("text", [
    "全部改完，测试 42 个都过了，已提交。",
    "为什么会这样？原因是缓存没失效。修好了，已经验证。\n\n下面是改动清单：\n- a.py\n- b.py",
    "做完了。需要的话可以再帮你写个脚本，要做跟我说。",
    "If you need any of them, tell me; most tasks work without them.",
    "```bash\nread -p 'continue?'\n```\n脚本已经放好了。",
    "",
    "这一步能不能省？" + "我查了，可以省，已经删掉，测试也补上了。" * 30,
    "如果想自己跑，可以手动执行 make test。全部改好了。",
])
def test_is_question_false(text: str) -> None:
    assert not is_question(text)


def test_session_state() -> None:
    def state(event: str, question: bool, ago: int) -> str:
        return session_state(last_event=event, last_question=question, mtime=NOW - ago, now=NOW)

    assert state("assistant", True, HOUR) == "waiting"
    assert state("ask", False, 0) == "waiting"
    assert state("tool", False, 60) == "working"
    assert state("assistant", False, HOUR) == "done"
    assert state("tool", False, HOUR) == "interrupted"


# ── project stage ──


def S(state: str, ago: int) -> dict[str, Any]:  # noqa: N802 - reads like a table row
    return {"state": state, "last_active": NOW - ago}


def R(dirty: int, ago: int | None) -> dict[str, Any]:  # noqa: N802
    return {"dirty": dirty, "last_commit": NOW - ago if ago is not None else None}


@pytest.mark.parametrize(("sessions", "repos", "prs", "stage"), [
    ([S("waiting", 2 * HOUR), S("working", 60)], [], None, "waiting"),
    ([S("working", 60)], [R(3, 5 * DAY)], None, "active"),
    ([S("done", 30 * 60)], [R(0, 9 * DAY)], 0, "active"),
    ([S("done", 4 * DAY)], [R(2, 5 * DAY)], None, "stalled"),
    ([], [R(0, 10 * DAY)], 2, "stalled"),
    ([S("done", 5 * HOUR)], [R(0, DAY)], 0, "done"),
    ([S("done", 5 * DAY)], [R(0, 5 * DAY)], None, "done"),
    ([S("done", 5 * HOUR)], [R(4, DAY)], None, "idle"),
    # A question left unanswered for more than 3 days no longer counts as waiting.
    ([S("waiting", 4 * DAY)], [R(1, 4 * DAY)], None, "stalled"),
])
def test_judge_project(sessions: list, repos: list, prs: int | None, stage: str) -> None:
    assert judge_project(now=NOW, sessions=sessions, repos=repos, open_prs=prs).stage == stage


def test_judge_project_counts_terminal_output_as_activity() -> None:
    got = judge_project(now=NOW, sessions=[], repos=[R(2, 20 * DAY)], open_prs=None,
                        other_activity=NOW - 600)
    assert got.stage == "active" and got.last_action == NOW - 600


def test_project_stalled_rule_opens_and_closes() -> None:
    def sig(ts: int, stage: str) -> Signal:
        return Signal("project:app", ts, "degraded", detail={"stage": stage, "reason": "r"})

    history = [sig(NOW, "idle"), sig(NOW + 300, "stalled"), sig(NOW + 600, "stalled"),
               sig(NOW + 900, "active")]
    assert replay(Rule("project", "project.stalled", project_stalled, 1), history) == [
        ("open", NOW + 300), ("close", NOW + 900)]


# ── parsers ──


def test_parse_git_status() -> None:
    text = ("# branch.oid abc\n# branch.head feat/x\n# branch.upstream origin/feat/x\n"
            "# branch.ab +2 -5\n1 .M N... 100644 100644 100644 a b src/a.py\n"
            "2 R. N... 100644 100644 100644 a b R100 new.py\told.py\nu UU N... x\n? notes.txt\n")
    s = parse_git_status(text)
    assert (s.branch, s.upstream, s.ahead, s.behind) == ("feat/x", "origin/feat/x", 2, 5)
    assert (s.changed, s.untracked, s.conflicts, s.dirty) == (2, 1, 1, 4)
    detached = parse_git_status("# branch.head (detached)\n")
    assert detached.branch == "" and detached.ahead is None


def test_github_repo_from_remote() -> None:
    assert progress.github_repo("git@github.com:Owner/Repo.git") == "owner/repo"
    assert progress.github_repo("https://github.com/owner/repo") == "owner/repo"
    assert progress.github_repo("git@example.org:team/repo.git") == ""


ORCA_PS = {"ok": True, "result": {"worktrees": [
    {"path": "/w/app", "repo": "app", "repoId": "r1", "branch": "refs/heads/main",
     "status": "working", "liveTerminalCount": 2, "unread": True,
     "lastOutputAt": NOW * 1000, "lastActivityAt": NOW * 1000,
     "preview": "terminal text " + FAKE_KEY,
     "agents": [{"agentType": "claude", "state": "working", "prompt": "secret prompt"}]},
    {"path": "/w/old", "repo": "old", "branch": "refs/heads/x", "isArchived": True},
]}}
ORCA_PROJECTS = {"ok": True, "result": {"projects": [
    {"providerIdentity": {"provider": "github", "owner": "Me", "repo": "App"},
     "sourceRepoIds": ["r1"]},
    {"gitRemoteIdentity": {"canonicalKey": "example.org/team/x"}, "sourceRepoIds": ["r2"]},
]}}


def test_parse_orca_ps_keeps_counts_and_drops_terminal_text() -> None:
    [w] = parse_orca_ps(ORCA_PS)
    assert (w.path, w.branch, w.live, w.unread, w.status) == ("/w/app", "main", 2, True, "working")
    assert w.last_output == NOW and w.agents == [{"type": "claude", "state": "working"}]
    assert FAKE_KEY not in json.dumps(vars(w)) and "secret prompt" not in json.dumps(vars(w))
    assert parse_orca_projects(ORCA_PROJECTS) == {"r1": "me/app"}


PAGE = """---
id: proj-app
title: App
status: active
repo: /w/app
aliases: [the-app]
updated: "2026-11-01"
---
# App

## Current State

> [!info] 当前状态（截至 11-01 会话）
> **阶段**：灰度发布中。第二步等审批。
> **进度**：看 https://example.com/x

## 下一步
"""


def test_current_state_takes_the_stage_sentence() -> None:
    assert current_state(PAGE) == ("灰度发布中。", "当前状态（截至 11-01 会话）")
    plain = "## Current State\n\n第一句话说明现状。第二句。\n\n## Other\n"
    assert current_state(plain) == ("第一句话说明现状。", "")
    assert current_state("# no state\n") == ("", "")


# ── incremental reading ──


def test_read_session_reads_only_appended_lines(tmp_path: Path, monkeypatch) -> None:
    file = tmp_path / "s-1.jsonl"
    write(file, [claude("user", "把登录页改成新设计，顺便看看为什么慢", NOW - 600),
                 claude("assistant", say("改好了，慢是因为图片没压缩。"), NOW - 590)])
    starts: list[int] = []
    real = session_reader.iter_blocks

    def spy(source, data, start, info):
        starts.append(start)
        return real(source, data, start, info)

    monkeypatch.setattr(session_reader, "iter_blocks", spy)
    first = read_session("claude", file, None)
    assert (first.turns, first.last_event, first.offset) == (1, "assistant", file.stat().st_size)
    assert read_session("claude", file, first) is first and starts == [0]  # unchanged: not opened
    size = file.stat().st_size
    write(file, [claude("user", "再把按钮颜色调深一点", NOW - 300)], mode="a")
    with file.open("a") as f:
        f.write('{"type": "assistant", "half a line')  # still being written
    second = read_session("claude", file, Digest.from_json(first.to_json()))
    assert starts[-1] == size  # resumed where the last read stopped
    assert (second.turns, second.last_event) == (2, "user")
    assert second.last_user == "再把按钮颜色调深一点"
    assert second.offset < file.stat().st_size  # the partial line is left for next time
    assert second.started == NOW - 600 and second.last_ts == NOW - 300


def test_read_session_in_small_chunks_matches_one_read(tmp_path: Path) -> None:
    file = tmp_path / "s-1.jsonl"
    records = []
    for i in range(30):
        records += [claude("user", f"第 {i} 个问题，帮我看看这里", NOW + i * 10),
                    claude("assistant", say(f"第 {i} 个回答" + "。" * 200), NOW + i * 10 + 5)]
    write(file, records)
    whole = read_session("claude", file, None)
    chunked = read_session("claude", file, None, chunk=97)
    assert whole.to_json() == chunked.to_json() and whole.turns == 30


def test_long_reply_is_judged_by_its_end(tmp_path: Path) -> None:
    file = tmp_path / "s-1.jsonl"
    long = "结论：" + "细节。" * 2000 + "\n\n要不要我现在就合并？"
    write(file, [claude("user", "看看这个 PR 能不能合", NOW), claude("assistant", say(long), NOW)])
    d = read_session("claude", file, None)
    assert d.last_question and d.last_agent.startswith("结论：")
    assert d.last_agent_tail.endswith("要不要我现在就合并？") and len(d.last_agent) <= 601


def test_pending_ask_tool_means_waiting(tmp_path: Path) -> None:
    file = tmp_path / "s-1.jsonl"
    write(file, [claude("user", "帮我选个数据库", NOW),
                 claude("assistant", say("我先列一下选项。"), NOW),
                 claude("assistant", [{"type": "tool_use", "name": "AskUserQuestion",
                                       "input": {"questions": []}}], NOW)])
    d = read_session("claude", file, None)
    assert (d.last_event, d.last_tool) == ("ask", "AskUserQuestion")
    assert session_state(last_event=d.last_event, last_question=d.last_question, mtime=0,
                         now=NOW) == "waiting"


def test_secrets_never_reach_the_digest(tmp_path: Path) -> None:
    file = tmp_path / "s-1.jsonl"
    write(file, [claude("user", f"token: {FAKE_GH} 帮我配一下 https://internal.example/x", NOW),
                 claude("assistant", say(f"已写入，key 是 {FAKE_KEY}，主机 198.51.100.4"), NOW)])
    raw = json.dumps(read_session("claude", file, None).to_json(), ensure_ascii=False)
    for leaked in (FAKE_GH, FAKE_KEY, "internal.example", "198.51.100.4"):
        assert leaked not in raw


@pytest.mark.parametrize(("source", "records", "skip"), [
    ("claude", [claude("user", "跑一下夜间任务", NOW, entrypoint="sdk-cli")], "headless"),
    ("claude", [claude("user", "Agent 自动化中台按规则 x 派来的活", NOW)], "autoloop"),
    ("codex", [codex_meta("c-1", "/w/app", NOW, thread_source="subagent")], "subagent"),
    ("codex", [codex_meta("c-1", "/w/app", NOW, originator="codex_exec", source="exec"),
               codex_msg("user", "批量改名", NOW)], "headless"),
    ("codex", [codex_meta("c-1", "/w/app", NOW),
               codex_msg("user", "看图\n\n[external unsupported block: image]", NOW)], "imported"),
    ("claude", [claude("user", "这是公司仓库", NOW, cwd="/elsewhere/x")], "not-allowed"),
])
def test_skip_rules(tmp_path: Path, source: str, records: list, skip: str) -> None:
    file = tmp_path / "x.jsonl"
    write(file, records)
    d = read_session(source, file, None, allowed=lambda cwd: cwd.startswith("/w/"))
    assert d.skip == skip and d.offset == file.stat().st_size
    assert read_session(source, file, d) is d


# ── the collector ──


def _vault(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    work = tmp_path / "work"
    claude_dir, codex_dir = tmp_path / "claude", tmp_path / "codex"
    (vault / ".kg" / "capture").mkdir(parents=True)
    (vault / ".kg" / "capture" / "config.yaml").write_text(
        f"sources:\n  claude: {claude_dir}\n  codex: {codex_dir}\n"
        f"allow:\n  - {{ path: {work}, container: true }}\n", encoding="utf-8")
    (vault / ".kg" / "plane").mkdir(parents=True)
    (vault / ".kg" / "plane" / "config.yaml").write_text(
        f"sessions:\n  github_snapshot: {tmp_path / 'gh.json'}\n", encoding="utf-8")
    (tmp_path / "gh.json").write_text(json.dumps({
        "me": "me", "prsRepo": "Me/App", "readAt": "2026-11-06T01:00:00Z",
        "prs": [{"number": 7, "title": "feat", "author": "me", "mine": True},
                {"number": 8, "title": "other", "author": "you", "mine": False}]}),
        encoding="utf-8")
    pages = vault / "07-Projects"
    pages.mkdir()
    (pages / "proj-app.md").write_text(PAGE.replace("/w/app", str(work / "app")), encoding="utf-8")
    for name in ("app", "lib"):
        (work / name).mkdir(parents=True)
    return vault, work, claude_dir


def _fake_run(work: Path, *, orca: bool = True):
    def run(argv: list[str], timeout: float = 20) -> str:
        if argv[0] == "orca":
            if not orca:
                raise CommandError("orca: not found")
            if argv[1:3] == ["worktree", "ps"]:
                ps = json.loads(json.dumps(ORCA_PS))
                ps["result"]["worktrees"][0]["path"] = str(work / "app")
                ps["result"]["worktrees"].append(
                    {"path": "/elsewhere/company", "repo": "company", "branch": "refs/heads/m",
                     "liveTerminalCount": 3})
                return json.dumps(ps)
            return json.dumps(ORCA_PROJECTS)
        assert argv[0] == "git"
        path, cmd = argv[2], argv[3]
        if cmd == "rev-parse":
            return path + "\n"
        if cmd == "status":
            dirty = "? a.txt\n? b.txt\n" if path.endswith("lib") else ""
            return f"# branch.head main\n# branch.upstream origin/main\n# branch.ab +1 -0\n{dirty}"
        if cmd == "log":
            return f"{NOW - 10 * DAY}\n"
        if cmd == "config":
            if path.endswith("app"):
                return "git@github.com:Me/App.git\n"
            raise CommandError("git 退出码 1")
        raise AssertionError(argv)
    return run


def _sessions(tmp_path: Path, work: Path, claude_dir: Path) -> None:
    app, lib = str(work / "app"), str(work / "lib")
    write(claude_dir / "p-app" / "s-wait.jsonl", [
        claude("user", "灰度第二步怎么做比较稳", NOW - 2 * HOUR, sid="s-wait", cwd=app),
        claude("assistant", say(f"两种做法，A 更稳。用 {FAKE_KEY} 调接口。\n\n选 A 还是 B？"),
               NOW - 2 * HOUR + 30, sid="s-wait", cwd=app)])
    write(claude_dir / "p-app" / "s-hi.jsonl", [
        claude("user", "hi", NOW - HOUR, sid="s-hi", cwd=app),
        claude("assistant", say("Hi! What would you like to work on?"), NOW - HOUR, sid="s-hi",
               cwd=app)])
    write(claude_dir / "p-app" / "s-wait" / "subagents" / "agent-1.jsonl", [
        claude("user", "子任务：读代码", NOW - HOUR, sid="s-wait", cwd=app)])
    write(claude_dir / "p-lib" / "s-lib.jsonl", [
        claude("user", "把解析器重构一下，保证接口不变", NOW - 5 * DAY, sid="s-lib", cwd=lib),
        claude("assistant", say("重构完了，测试全过。"), NOW - 5 * DAY + 60, sid="s-lib",
               cwd=lib)])
    write(claude_dir / "p-x" / "s-x.jsonl", [
        claude("user", "公司的事", NOW - HOUR, sid="s-x", cwd="/elsewhere/company")])
    for f in claude_dir.rglob("*.jsonl"):  # file times follow the last record
        last = json.loads(f.read_text().splitlines()[-1])["timestamp"]
        ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        os.utime(f, (ts, ts))


def _collect(tmp_path: Path, *, orca: bool = True):
    vault, work, claude_dir = _vault(tmp_path)
    _sessions(tmp_path, work, claude_dir)
    ctx = CollectContext(now=NOW, vault=vault, run=_fake_run(work, orca=orca))
    return vault, work, SessionsCollector().collect(ctx)


def test_collector_builds_projects_and_sessions(tmp_path: Path) -> None:
    vault, work, out = _collect(tmp_path)
    ids = sorted(r.id for r in out.resources)
    assert ids == ["project:app", "project:lib", "session:claude:s-lib", "session:claude:s-wait"]
    by_id = {r.id: r.attrs for r in out.resources}
    wait = by_id["session:claude:s-wait"]
    assert wait["state"] == "waiting" and wait["turns"] == 1
    assert wait["resume"] == f"cd {work / 'app'} && claude --resume s-wait"
    assert wait["orca_worktree"] == str(work / "app")
    app = by_id["project:app"]
    assert app["stage"] == "waiting" and app["open_prs"] == 1 and app["prs"][0]["number"] == 7
    assert app["orca"]["live"] == 2 and app["orca"]["unread"] == 1
    assert app["kb"]["path"] == "07-Projects/proj-app.md" and app["kb"]["state"] == "灰度发布中。"
    assert app["repos"][0]["github"] == "me/app" and app["repos"][0]["ahead"] == 1
    lib = by_id["project:lib"]
    assert lib["stage"] == "stalled" and lib["dirty"] == 2 and lib["open_prs"] is None
    assert lib["kb"] is None and lib["orca"] == {"live": 0, "unread": 0, "worktrees": []}
    text = json.dumps([r.attrs for r in out.resources], ensure_ascii=False)
    assert FAKE_KEY not in text and "company" not in text  # off the allow list: not shown
    assert any("没聊起来" in n for n in out.notes) and any("不在白名单" in n for n in out.notes)
    assert (vault / ".kg" / "plane" / "sessions-cursor.json").exists()


def test_collector_without_orca_records_unknown(tmp_path: Path) -> None:
    _, _, out = _collect(tmp_path, orca=False)
    projects = [r.attrs for r in out.resources if r.kind == "project"]
    assert [p["name"] for p in projects] == ["app", "lib"]
    assert all(p["orca"] is None for p in projects)
    assert any("Orca 读不到" in n for n in out.notes)


def test_collector_without_capture_config_marks_nothing_gone(tmp_path: Path) -> None:
    out = SessionsCollector().collect(CollectContext(now=NOW, vault=tmp_path,
                                                     run=_fake_run(tmp_path)))
    assert out.resources == [] and out.scopes == () and out.notes


def test_run_collector_opens_stalled_incident_and_api_serves_board(tmp_path: Path,
                                                                  monkeypatch) -> None:
    from agents.api_routes import plane, sessions

    vault, work, claude_dir = _vault(tmp_path)
    _sessions(tmp_path, work, claude_dir)
    path = tmp_path / "state.db"
    conn = store.connect(path)
    ctx = CollectContext(now=NOW, vault=vault, run=_fake_run(work))
    result = run_collector(conn, SessionsCollector(), ctx=ctx)
    assert [(c.action, c.resource_id) for c in result.changes] == [("open", "project:lib")]

    app = FastAPI()
    app.include_router(plane.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.dependency_overrides[plane.plane_db_path] = lambda: path
    client = TestClient(app)
    monkeypatch.setattr(sessions.time, "time", lambda: NOW + 60)
    body = client.get("/api/plane/projects").json()
    listed = client.get("/api/plane/sessions", params={"project": "lib"}).json()
    assert [p["name"] for p in body["items"]] == ["app", "lib"]  # waiting first
    assert [s["session_id"] for s in body["attention"]["waiting"]] == ["s-wait"]
    assert [p["name"] for p in body["attention"]["stalled"]] == ["lib"]
    assert body["stale"] is False and body["collected_at"] == NOW
    assert [s["session_id"] for s in listed["items"]] == ["s-lib"]


def test_api_without_store_is_empty(tmp_path: Path) -> None:
    from agents.api_routes import plane, sessions

    path = tmp_path / "none.db"
    app = FastAPI()
    app.include_router(sessions.router, prefix="/api")
    app.dependency_overrides[plane.plane_db_path] = lambda: path
    body = TestClient(app).get("/api/plane/projects").json()
    assert body["items"] == [] and body["stale"] is True and not path.exists()


def test_cursor_round_trip_and_version(tmp_path: Path) -> None:
    file = tmp_path / "s-1.jsonl"
    write(file, [claude("user", "改一下", NOW),
                 claude("assistant", say("改好了。可以合并吗？"), NOW)])
    cursor = tmp_path / "cursor.json"
    digest = read_session("claude", file, None)
    session_reader.save_cursor(cursor, {str(file): digest})
    loaded = session_reader.load_cursor(cursor)
    assert loaded[str(file)].to_json() == digest.to_json()
    assert loaded[str(file)].last_reason == "结尾是问句"
    raw = json.loads(cursor.read_text())
    raw["version"] = 0
    cursor.write_text(json.dumps(raw))
    assert session_reader.load_cursor(cursor) == {}  # an older cache is rebuilt
    cursor.write_text("{not json")
    assert session_reader.load_cursor(cursor) == {}


def test_waiting_review_sample_is_balanced_and_score_counts_mistakes() -> None:
    from kg.plane import waiting_review

    sessions = [{"state": "waiting" if i < 4 else "done", "last_active": i, "source": "claude",
                 "session_id": f"s{i}", "project": "p", "last_event": "assistant",
                 "last_tool": "", "turns": 1, "last_user": "u", "last_agent": "a",
                 "last_agent_tail": "a", "resume": "", "waiting_reason": ""} for i in range(30)]
    picked = waiting_review.sample(sessions, 10, seed=1)
    assert len(picked) == 10 and sum(s["state"] == "waiting" for s in picked) == 4
    lines = [waiting_review.review_line(s) for s in picked]
    assert all(x["human"] is None for x in lines)
    lines[0]["human"] = lines[0]["predicted"]
    lines[1]["human"] = not lines[1]["predicted"]
    got = waiting_review.score(lines, "human")
    assert (got["judged"], got["agree"], got["accuracy"]) == (2, 1, 0.5)
    assert got["disagreements"] == [lines[1]["key"]]
