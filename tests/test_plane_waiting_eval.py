"""The behaviour check of the "waiting for you" judgement: answer key, replay, scores.

Every session here is synthetic; real session content never goes into tests.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from kg.capture.scan import load_capture_config
from kg.plane import waiting_eval
from kg.plane.progress import DAY, HOUR
from kg.plane.waiting_eval import (
    Moment,
    label_ends,
    last_points,
    options,
    replay_all,
    replay_file,
    reply_label,
    report,
    review_points,
    scores,
    wilson,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def claude(kind: str, content: Any, ts: int, *, sid: str = "s-1", cwd: str = "/w/app"
           ) -> dict[str, Any]:
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    return {"type": kind, "sessionId": sid, "cwd": cwd, "gitBranch": "main", "timestamp": iso(ts),
            "message": {"role": kind, "content": content}}


def tool(name: str, ts: int, **kw: Any) -> dict[str, Any]:
    return claude("assistant", [{"type": "tool_use", "name": name, "input": {}}], ts, **kw)


def write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")
    last = datetime.fromisoformat(records[-1]["timestamp"].replace("Z", "+00:00")).timestamp()
    os.utime(path, (last, last))
    return path


def project_of(cwd: str) -> str | None:
    return cwd.rsplit("/", 1)[-1] if cwd.startswith("/w/") else None


# ── the answer key ──

ASKS = "两种做法：\n\n1. 先灰度一台机器\n2. 直接全量发布\n\n选哪个？"


@pytest.mark.parametrize(("agent", "reply", "label", "rule"), [
    (ASKS, "1", True, "选了编号"),
    (ASKS, "选 2 吧", True, "选了编号"),
    (ASKS, "A和C", True, "选了编号"),
    (ASKS, "2，另外把日志也带上", True, "选了编号"),
    (ASKS, "灰度一台机器就行", True, "引用了选项"),
    ("要不要我现在提交？", "好的", True, "确认或拒绝开头"),
    ("要不要我现在提交？", "不用，先别动", True, "确认或拒绝开头"),
    ("要不要我现在提交？", "OK", True, "确认或拒绝开头"),
    ("第一阶段做完了，下一步是迁移。", "继续", True, "确认或拒绝开头"),
    ("第一阶段做完了。", "继续做迁移", True, "继续"),
    ("请你先在浏览器里登录，做完告诉我。", "登好了", True, "回报做完了"),
    ("要不要把缓存目录也清理掉？", "缓存目录清理掉", True, "回应了问句"),
    ("改完了，测试全过。",
     "对了，帮我看一下另一个仓库为什么构建一直失败，日志在昨天晚上的那次运行里，顺便看看缓存",
     False, "长发言，新任务"),
    ("改完了，测试全过。", "好像还有一个页面没改", None, "短发言，看不出"),
    ("改完了，测试全过。", "可以帮我顺手把文档也更新一下吗", None, "短发言，看不出"),
    ("改完了。", "/clear", False, "斜杠命令"),
    ("改完了。", "/compact", None, "维护命令"),
    ("把报错贴给我。", "〔粘贴 900 字：Traceback…〕", None, "粘贴"),
    ("改完了。", "  ", None, "空发言"),
])
def test_reply_label(agent: str, reply: str, label: bool | None, rule: str) -> None:
    assert reply_label(agent, reply) == (label, rule)


def test_options_reads_numbered_and_lettered_items() -> None:
    text = "说明\n\n- **A. 留在本机**：最省事\n- B）放到云上\n3. 都不做\n正文里的 1.5 倍不算"
    assert [label for label, _ in options(text)] == ["a", "b", "3"]
    assert options(text)[0][1].startswith("留在本机")


# ── replaying one session ──


def test_replay_judges_each_turn_end_on_what_was_there(tmp_path: Path) -> None:
    file = write(tmp_path / "s-1.jsonl", [
        claude("user", "看看这个 PR 能不能合", NOW),
        claude("assistant", "测试都过了。要不要我现在合并？", NOW + 10),
        claude("user", "好的", NOW + 20),
        tool("Bash", NOW + 30),
        claude("assistant", "合并完了，分支也删了。", NOW + 40),
        claude("user", "换个事：把首页的加载速度查一下，首屏超过三秒，先找出最慢的几个请求，"
                       "再看看能不能并行加载", NOW + 50),
        claude("assistant", "查到三个慢请求，已经写在报告里。", NOW + 60),
    ])
    rep = replay_file("claude", file, project_of=project_of)
    assert (rep.project, rep.session_id, rep.first_user) == ("app", "s-1", NOW)
    got = [(m.kind, m.predicted, m.label, m.ts) for m in rep.moments]
    assert got == [("turn", True, True, NOW + 10), ("turn", False, False, NOW + 40),
                   ("end", False, None, NOW + 60)]
    assert rep.moments[0].reason


def test_breaking_in_and_ask_tools_are_counted_apart(tmp_path: Path) -> None:
    file = write(tmp_path / "s-1.jsonl", [
        claude("user", "帮我选个数据库并建好表", NOW),
        claude("assistant", "我先列一下选项。", NOW + 5),
        tool("AskUserQuestion", NOW + 6),
        claude("assistant", "好，用第一个。开始建表。", NOW + 30),
        tool("Bash", NOW + 31),
        claude("user", "等一下，表名要带前缀", NOW + 40),
        claude("assistant", "已加前缀。", NOW + 50),
    ])
    kinds = [(m.kind, m.predicted, m.label) for m in
             replay_file("claude", file, project_of=project_of).moments]
    assert kinds == [("ask_tool", True, True), ("interrupted", False, None),
                     ("end", False, None)]


def test_skipped_and_trivial_sessions_have_no_moments(tmp_path: Path) -> None:
    other = write(tmp_path / "a.jsonl", [claude("user", "公司的事", NOW, cwd="/elsewhere/x"),
                                         claude("assistant", "好", NOW + 1, cwd="/elsewhere/x")])
    hi = write(tmp_path / "b.jsonl", [claude("user", "hi", NOW), claude("assistant", "Hi!", NOW)])
    for file in (other, hi):
        rep = replay_file("claude", file, project_of=project_of)
        assert rep.skip and not rep.moments


def test_texts_only_on_request(tmp_path: Path) -> None:
    file = write(tmp_path / "s-1.jsonl", [claude("user", "改一下标题颜色", NOW),
                                          claude("assistant", "改好了，要提交吗？", NOW + 1),
                                          claude("user", "提交", NOW + 2)])
    texts: dict[str, tuple[str, str]] = {}
    rep = replay_file("claude", file, project_of=project_of, keep_text=texts)
    assert list(texts.values()) == [("改好了，要提交吗？", "提交")]
    assert "改好了" not in json.dumps(rep.to_json(), ensure_ascii=False)


# ── sessions that just stopped, copies, scores ──


def M(sid: str, ts: int, kind: str = "end", predicted: bool = True,  # noqa: N802 - a table row
      label: bool | None = None, key: str = "", project: str = "app") -> Moment:
    return Moment("claude", sid, project, ts, kind, predicted, "", label, "", key or f"{sid}{ts}")


def test_label_ends_by_what_came_next_in_the_project() -> None:
    reps = [
        waiting_eval.Replay("claude", "a", 0, 0, "a", "app", first_user=NOW - 30 * DAY,
                            moments=[M("a", NOW - 20 * DAY)]),
        waiting_eval.Replay("claude", "b", 0, 0, "b", "app", first_user=NOW - 20 * DAY + HOUR,
                            moments=[M("b", NOW - 15 * DAY)]),
        waiting_eval.Replay("claude", "c", 0, 0, "c", "lib", first_user=NOW - 2 * DAY,
                            moments=[M("c", NOW - DAY, project="lib")]),
    ]
    got = {m.session_id: (m.label, m.rule) for m in label_ends(reps, now=NOW)}
    assert got == {"a": (False, "同一项目另开了会话"), "b": (None, "之后什么都没发生"),
                   "c": (None, "还在观察期")}


def test_copied_sessions_keep_the_turn_you_replied_to() -> None:
    reps = [
        waiting_eval.Replay("claude", "old", 0, 0, "old", "app", first_user=NOW - 9 * DAY,
                            moments=[M("old", NOW - 8 * DAY, key="same")]),
        waiting_eval.Replay("claude", "new", 0, 0, "new", "app", first_user=NOW - 7 * DAY,
                            moments=[M("new", NOW - 8 * DAY, kind="turn", label=True,
                                       key="same")]),
    ]
    [only] = label_ends(reps, now=NOW)
    assert (only.session_id, only.kind, only.label) == ("new", "turn", True)


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    lo, hi = wilson(18, 20)
    assert lo == pytest.approx(0.699, abs=0.002) and hi == pytest.approx(0.972, abs=0.002)


def test_scores_count_both_kinds_of_mistake() -> None:
    ms = [M("a", 1, "turn", True, True), M("a", 2, "turn", True, False),
          M("b", 3, "turn", False, True), M("c", 4, "turn", False, False),
          M("c", 5, "turn", False, False), M("d", 6, "end", True, None)]
    s = scores(ms, boot=200)
    assert (s["tp"], s["fp"], s["fn"], s["tn"], s["labelled"], s["unknown"]) == (1, 1, 1, 2, 5, 1)
    assert (s["precision"], s["recall"], s["accuracy"]) == (0.5, 0.5, 0.6)
    assert len(s["accuracy_ci_by_session"]) == 2


def test_last_points_and_review_points() -> None:
    ms = [M("a", NOW, "turn", label=True), M("a", NOW + 2 * HOUR, "end"),
          M("a", NOW + 3 * HOUR, "ask_tool")]
    assert [m.ts for m in last_points(ms)] == [NOW + 2 * HOUR]
    seen = datetime.fromtimestamp(NOW + 60).strftime("%m-%d %H:%M")
    review = [{"key": "claude:a", "last_active": seen, "model": True},
              {"key": "claude:gone", "last_active": seen}]
    picked, extra = review_points(ms, review, year=datetime.fromtimestamp(NOW).year)
    assert [m.ts for m in picked] == [NOW]
    assert extra["not_found"] == 1
    assert extra["model_review_vs_behaviour"] == {"judged": 1, "agree": 1}


def test_report_splits_by_time_and_holds_no_text() -> None:
    ms = [M("a", NOW - 30 * DAY, "turn", True, True), M("b", NOW - 5 * DAY, "turn", False, False),
          M("b", NOW - 4 * DAY, "turn", True, False)]
    out = report(ms, split_at=NOW - 20 * DAY, since=NOW - 60 * DAY, review=[], now=NOW)
    assert (out["train"]["labelled"], out["test"]["labelled"]) == (1, 2)
    assert out["test_mistakes"] == {"false_waiting": ["claude:b"], "missed_waiting": []}
    json.dumps(out)  # numbers and ids only


def test_replay_all_reads_a_file_again_only_when_it_changed(tmp_path: Path, monkeypatch) -> None:
    vault, sessions = tmp_path / "vault", tmp_path / "claude"
    (vault / ".kg" / "capture").mkdir(parents=True)
    (vault / ".kg" / "capture" / "config.yaml").write_text(
        f"sources:\n  claude: {sessions}\n  codex: {tmp_path / 'codex'}\n"
        "allow:\n  - /w/app\n", encoding="utf-8")
    config = load_capture_config(vault)
    file = write(sessions / "p" / "s-1.jsonl", [claude("user", "改一下标题颜色", NOW),
                                                claude("assistant", "改好了，要提交吗？", NOW + 1)])
    calls: list[str] = []
    real = waiting_eval.replay_file

    def counting(source: str, f: Path, **kw: Any):
        calls.append(f.name)
        return real(source, f, **kw)

    monkeypatch.setattr(waiting_eval, "replay_file", counting)
    cache = tmp_path / "cache.json"
    first = replay_all(config, now=NOW + 10, days=60, cache_file=cache)
    again = replay_all(config, now=NOW + 10, days=60, cache_file=cache)
    assert calls == ["s-1.jsonl"] and first[0].moments == again[0].moments
    assert "改好了" not in cache.read_text(encoding="utf-8")
    with file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(claude("user", "提交", NOW + 2), ensure_ascii=False) + "\n")
    os.utime(file, (NOW + 2, NOW + 2))
    third = replay_all(config, now=NOW + 10, days=60, cache_file=cache)
    assert calls == ["s-1.jsonl", "s-1.jsonl"]
    assert [m.kind for m in third[0].moments] == ["turn"]
