"""Behavior of session capture: what gets read, what is skipped, and resuming."""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from kg.capture.scan import (
    TZ,
    AllowRoot,
    Budget,
    CaptureConfig,
    pending_batches,
    project_for,
    scan,
)
from kg.capture.sessions import AUTOLOOP_PROMPT_PREFIX


def _at(minute: int) -> datetime:
    """Distinct scan times (batch ids are per second), all after the files were written."""
    return datetime.now(TZ) + timedelta(minutes=minute)


def _claude_line(kind: str, content: object, cwd: str, **extra: object) -> str:
    record = {"type": kind, "cwd": cwd, "sessionId": "s-" + Path(cwd).name, "gitBranch": "main",
              "timestamp": "2026-11-04T02:00:00Z", "entrypoint": "cli",
              "message": {"role": kind, "content": content}, **extra}
    return json.dumps(record, ensure_ascii=False) + "\n"


def _session(folder: Path, name: str, lines: list[str], *, age: float = 3600) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{name}.jsonl"
    file.write_text("".join(lines), encoding="utf-8")
    stamp = time.time() - age
    os.utime(file, (stamp, stamp))
    return file


def _config(tmp_path: Path, **budget: int) -> CaptureConfig:
    return CaptureConfig(
        root=tmp_path / "vault" / ".kg" / "capture",
        sources={"claude": tmp_path / "claude", "codex": tmp_path / "codex"},
        allow=(AllowRoot(tmp_path / "work", container=True, exact=False),),
        deny=(tmp_path / "work" / "company",),
        start_date=date(2026, 1, 1),
        budget=Budget(**{"min_user_chars": 5, **budget}),
    )


def _talk(cwd: Path, question: str = "为什么 goroutine 会泄漏？") -> list[str]:
    return [
        _claude_line("user", question, str(cwd)),
        _claude_line("assistant", [
            {"type": "text", "text": "因为 channel 没有接收方，发送方会一直阻塞。"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "go test ./..."}},
        ], str(cwd)),
        _claude_line("user", [{"type": "tool_result", "is_error": True,
                               "content": "FAIL leak_test.go:12"}], str(cwd)),
        _claude_line("user", "<system-reminder>ignore me</system-reminder>好的修一下", str(cwd)),
    ]


def test_project_names_follow_allow_and_deny(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert project_for(str(tmp_path / "work" / "app" / "sub"), config) == "app"
    assert project_for(str(tmp_path / "work" / "company" / "x"), config) is None
    assert project_for(str(tmp_path / "elsewhere"), config) is None
    exact = CaptureConfig(**{**vars(config), "allow": (AllowRoot(tmp_path, False, True),)})
    assert project_for(str(tmp_path), exact) == tmp_path.name
    assert project_for(str(tmp_path / "child"), exact) is None


def test_scan_writes_readable_digest_and_skips_what_it_must(tmp_path: Path) -> None:
    config = _config(tmp_path)
    claude = tmp_path / "claude"
    _session(claude / "p1", "good", _talk(tmp_path / "work" / "app"))
    _session(claude / "p2", "company", _talk(tmp_path / "work" / "company"))
    _session(claude / "p3", "ours", [_claude_line(
        "user", AUTOLOOP_PROMPT_PREFIX + "「整理」把这件活派给你", str(tmp_path / "work" / "kg"))])
    _session(claude / "p4", "fresh", _talk(tmp_path / "work" / "busy"), age=60)
    _session(claude / "p1" / "subagents", "helper", _talk(tmp_path / "work" / "app"))

    result = scan(config)

    assert [p.project for p in result.parts] == ["app"]
    assert result.skipped == {"autoloop": 1, "not-allowed": 1}
    batch = config.inbox / str(result.batch_id)
    manifest = json.loads((batch / "manifest.json").read_text())
    digest = (batch / manifest["parts"][0]["digest"]).read_text()
    assert "[用户 11-04 10:00] 为什么 goroutine 会泄漏？" in digest
    assert "[操作] 跑了 1 条命令（如 go test ./...）" in digest
    assert "[报错] FAIL leak_test.go:12" in digest
    assert "ignore me" not in digest and "好的修一下" in digest
    assert pending_batches(config) == [result.batch_id]


def test_scan_resumes_where_it_stopped_and_reads_only_new_lines(tmp_path: Path) -> None:
    config = _config(tmp_path, max_pending_batches=5)
    cwd = tmp_path / "work" / "app"
    file = _session(tmp_path / "claude" / "p1", "s", _talk(cwd))
    first = scan(config, now=_at(0))
    assert len(first.parts) == 1

    again = scan(config, now=_at(1))
    assert again.parts == []

    with file.open("a", encoding="utf-8") as handle:
        handle.write(_claude_line("user", "再补一个问题：context 取消怎么传？", str(cwd)))
        handle.write('{"type": "user", "partial')  # still being written: must wait
    stamp = time.time() - 3600
    os.utime(file, (stamp, stamp))
    later = scan(config, now=_at(2))
    assert len(later.parts) == 1
    text = later.parts[0].text
    assert "context 取消怎么传" in text and "goroutine" not in text
    assert later.parts[0].end == file.stat().st_size - len('{"type": "user", "partial')


def test_budget_splits_a_long_session_across_batches(tmp_path: Path) -> None:
    config = _config(tmp_path, max_chars_per_session=200, max_pending_batches=10)
    cwd = str(tmp_path / "work" / "app")
    lines = [_claude_line("user", f"第 {i} 个问题：" + "细节" * 40, cwd) for i in range(6)]
    file = _session(tmp_path / "claude" / "p1", "long", lines)
    seen: list[str] = []
    for minute in range(10):
        result = scan(config, now=_at(minute))
        if not result.parts:
            break
        assert len(result.parts[0].text) < 400
        seen.append(result.parts[0].text)
    assert len(seen) >= 3
    assert all(f"第 {i} 个问题" in "".join(seen) for i in range(6))
    state = json.loads((config.root / "state.json").read_text())["files"][str(file)]
    assert state["offset"] == file.stat().st_size


def test_pending_batches_stop_new_reads(tmp_path: Path) -> None:
    config = _config(tmp_path, max_pending_batches=1)
    _session(tmp_path / "claude" / "p1", "a", _talk(tmp_path / "work" / "app"))
    first = scan(config, now=_at(0))
    _session(tmp_path / "claude" / "p2", "b", _talk(tmp_path / "work" / "other"))
    blocked = scan(config, now=_at(1))
    assert blocked.parts == [] and blocked.pending == [first.batch_id]

    (config.inbox / str(first.batch_id) / "result.md").write_text("done")
    resumed = scan(config, now=_at(2))
    assert [p.project for p in resumed.parts] == ["other"]


def test_codex_sessions_skip_preambles_and_subagents(tmp_path: Path) -> None:
    config = _config(tmp_path)
    cwd = str(tmp_path / "work" / "app")

    def line(kind: str, payload: dict[str, object]) -> str:
        return json.dumps({"type": kind, "timestamp": "2026-11-04T02:00:00Z", "payload": payload},
                          ensure_ascii=False) + "\n"

    def message(role: str, text: str) -> str:
        part = "input_text" if role == "user" else "output_text"
        return line("response_item", {"type": "message", "role": role,
                                      "content": [{"type": part, "text": text}]})

    meta = {"id": "c1", "cwd": cwd, "thread_source": "user"}
    _session(tmp_path / "codex", "main", [
        line("session_meta", meta),
        message("user", "# AGENTS.md instructions\n<INSTRUCTIONS>…"),
        message("user", "<pasted_content id=\"a\">" + "mixed-port: 7890\n" * 50
                + "</pasted_content>\n帮我看看 Tailscale 为什么慢"),
        line("response_item", {"type": "custom_tool_call", "name": "apply_patch",
                               "input": "*** Begin Patch\n*** Update File: net.go\n"}),
        message("assistant", "Clash TUN 让流量绕路了，关掉后 RTT 从 80ms 降到 12ms。"),
    ])
    _session(tmp_path / "codex", "sub", [
        line("session_meta", {**meta, "id": "c2", "thread_source": "subagent"}),
        message("user", "子任务"),
    ])
    result = scan(config)
    assert len(result.parts) == 1
    text = result.parts[0].text
    assert "AGENTS.md" not in text
    assert "〔粘贴 849 字：mixed-port: 7890" in text and text.count("mixed-port") < 15
    assert "帮我看看 Tailscale 为什么慢" in text
    assert "[操作] 改了 net.go" in text and "RTT 从 80ms" in text
    assert result.skipped == {"subagent": 1}


def test_brief_collects_finished_batches_and_inlines_extra_files(tmp_path: Path) -> None:
    from kg.capture.brief import collect_brief

    vault = tmp_path / "vault"
    extra = tmp_path / "digest-{date}.md"
    config = CaptureConfig(**{**vars(_config(tmp_path)),
                              "brief": {"aihot": False, "extra": [str(extra)]}})
    _session(tmp_path / "claude" / "p1", "a", _talk(tmp_path / "work" / "app"))
    done = scan(config)
    (config.inbox / str(done.batch_id) / "result.md").write_text("# 整理结果")
    scan(config, now=_at(1))  # nothing new; no unfinished batch should be listed either
    today = datetime.now(TZ).date().isoformat()
    Path(str(extra).replace("{date}", today)).write_text("GitHub 汇总正文")
    (vault / "11-Experiments").mkdir(parents=True)
    (vault / "11-Experiments" / "lab-x.md").write_text("---\nid: lab-x\nstatus: trying\n---\n")

    brief_id, file = collect_brief(vault, config, fetch=False)
    data = json.loads(file.read_text())

    assert data["id"] == brief_id and brief_id.startswith(today)
    assert data["batches"] == [f".kg/capture/inbox/{done.batch_id}/result.md"]
    assert data["extra"][0]["content"] == "GitHub 汇总正文"
    assert data["experiments"][0]["status"] == "trying"
    assert "aihot" not in data
    again_id, _ = collect_brief(vault, config, fetch=False)
    assert json.loads(file.read_text())["batches"] == []  # the window moved on
    assert again_id != brief_id
