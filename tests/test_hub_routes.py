"""/api/hub/* and /api/eval/*: the owner key stays on the server, writes only from this page,
rules are checked before they are written, backed up, rolled back and audited.

A stub hub runs on an ephemeral loopback port; the real hub and rules file are never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents.api_routes import eval_label, hub
from agents.autoloop import CliChecker, HubSettings, RulesStore, load_settings

OWNER = "owner-key-Zk3xQ9-must-never-leave-the-server"
ORIGIN = "http://127.0.0.1:8765"
SAME = {"origin": ORIGIN, "content-type": "application/json"}


class StubHub:
    """Answers like agent-autoloop for the few paths the page uses, and remembers what it got."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        self.replies: dict[tuple[str, str], tuple[int, Any]] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(length) if length else b""
                stub.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "auth": self.headers.get("authorization"),
                        "body": json.loads(body) if body else None,
                    }
                )
                if self.headers.get("authorization") != f"Bearer {OWNER}":
                    code, data = 401, {"error": "要本人的密钥"}
                else:
                    code, data = stub.replies.get(
                        (self.command, self.path), (404, {"error": "没有这个地址"})
                    )
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _reply  # noqa: N815

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


RULES = {
    "sources": {"local": {"name": "本机"}},
    "agents": {"claude": {"name": "Claude Code", "worker": "mac", "use": "claude"}},
    "rules": [
        {
            "name": "晨报",
            "when": {"cron": "0 9 * * *"},
            "to": "claude",
            "cwd": "~/x",
            "prompt": "写",
        },
        {
            "name": "处理",
            "when": {"source": "local", "types": ["a.*"]},
            "to": "claude",
            "cwd": "~/x",
            "skill": "s",
        },
    ],
}


def fake_check(text: str) -> list[str]:
    """Stands in for `agent-autoloop check`: rejects non-JSON and rules sent to unknown agents."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [f"不是 JSON：{exc}"]
    return [
        f"规则「{r.get('name')}」的 to 不是已登记的 Agent：{r.get('to')}"
        for r in data.get("rules", [])
        if r.get("to") not in data.get("agents", {})
    ]


@pytest.fixture
def env(tmp_path: Path):
    stub = StubHub()
    home = tmp_path / "autoloop"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"url": stub.url, "owner": OWNER}))
    rules_file = home / "rules.json"
    rules_file.write_text(json.dumps(RULES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    settings = HubSettings(
        config_file=home / "config.json",
        rules_file=rules_file,
        cli=("false",),
        audit_file=tmp_path / ".kg" / "plane" / "hub-audit.jsonl",
        timeout=5,
    )
    store = RulesStore(rules_file, settings.audit_file, fake_check, who="tester@kg-web")
    app = FastAPI()
    app.include_router(hub.router, prefix="/api")
    app.include_router(eval_label.router, prefix="/api")
    app.dependency_overrides[hub.get_settings] = lambda: settings
    app.dependency_overrides[hub.get_store] = lambda: store
    app.dependency_overrides[eval_label.get_vault] = lambda: tmp_path
    client = TestClient(app, base_url=ORIGIN)
    yield {
        "client": client,
        "stub": stub,
        "rules": rules_file,
        "store": store,
        "tmp": tmp_path,
        "settings": settings,
        "home": home,
    }
    stub.close()


def _no_key(response) -> None:
    assert OWNER not in response.text
    assert OWNER not in json.dumps(dict(response.headers))


# ── Proxy and the owner key ─────────────────────────────────────────────────────────────────────


def test_state_is_forwarded_with_the_key_and_the_key_never_comes_back(env) -> None:
    stub = env["stub"]
    stub.replies[("GET", "/v1/state")] = (200, {"cards": [], "runs": [], "lines": []})
    r = env["client"].get("/api/hub/state")
    assert r.status_code == 200
    assert r.json()["schedules"] == {"晨报": "0 9 * * *"}
    control = r.json()["control"]
    assert control["available"] is False, "an old hub has no pauses"
    assert "需要升级" in control["reason"]
    assert stub.seen[-1]["auth"] == f"Bearer {OWNER}"
    _no_key(r)


def test_key_is_scrubbed_even_if_the_hub_echoes_it(env) -> None:
    stub = env["stub"]
    stub.replies[("GET", "/v1/state")] = (200, {"note": f"token {OWNER}", "runs": []})
    stub.replies[("GET", "/v1/runs/7/why")] = (500, {"error": f"出错了 Bearer {OWNER}"})
    ok, bad = env["client"].get("/api/hub/state"), env["client"].get("/api/hub/runs/7/why")
    assert bad.status_code == 500 and "出错了" in bad.json()["error"]
    for r in (ok, bad):
        _no_key(r)


def test_hub_errors_reach_the_page_as_the_hub_said_them(env) -> None:
    env["stub"].replies[("POST", "/v1/cards/5/answer")] = (409, {"error": "这张卡片已经回答过了"})
    r = env["client"].post("/api/hub/cards/5/answer", json={"choice": 0}, headers=SAME)
    assert r.status_code == 409
    assert r.json() == {"error": "这张卡片已经回答过了", "source": "hub"}
    _no_key(r)


def test_unreachable_hub_is_502_without_the_key(env, tmp_path: Path) -> None:
    env["stub"].close()
    r = env["client"].get("/api/hub/state")
    assert r.status_code == 502 and "中台没响应" in r.json()["error"]
    _no_key(r)


def test_missing_config_is_503_and_names_no_secret(env) -> None:
    env["settings"].config_file.unlink()
    r = env["client"].get("/api/hub/state")
    assert r.status_code == 503
    _no_key(r)


def test_answer_forwards_only_choice_or_text(env) -> None:
    stub = env["stub"]
    stub.replies[("POST", "/v1/cards/3/answer")] = (200, {"changed": True, "state": "closed"})
    r = env["client"].post("/api/hub/cards/3/answer", json={"choice": 1, "by": "x"}, headers=SAME)
    assert r.status_code == 200 and stub.seen[-1]["body"] == {"choice": 1}
    r = env["client"].post("/api/hub/cards/3/answer", json={"text": " 先放着 "}, headers=SAME)
    assert r.status_code == 200 and stub.seen[-1]["body"] == {"text": "先放着"}
    r = env["client"].post(
        "/api/hub/cards/3/answer", json={"choice": 1, "text": "两个"}, headers=SAME
    )
    assert r.status_code == 400


# ── Same-origin writes ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example", "content-type": "application/json"},
        {"origin": "http://127.0.0.1:9999", "content-type": "application/json"},
        {"origin": "null", "content-type": "application/json"},
        {"content-type": "application/json"},
        {"referer": "https://evil.example/page", "content-type": "application/json"},
        {**SAME, "sec-fetch-site": "cross-site"},
    ],
)
def test_cross_site_answer_is_refused_and_never_reaches_the_hub(env, headers) -> None:
    r = env["client"].post("/api/hub/cards/3/answer", json={"choice": 0}, headers=headers)
    assert r.status_code == 403
    assert env["stub"].seen == []


@pytest.mark.parametrize(
    "kind", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_non_json_answer_is_refused(env, kind) -> None:
    r = env["client"].post(
        "/api/hub/cards/3/answer",
        content=b'{"choice":0}',
        headers={"origin": ORIGIN, "content-type": kind},
    )
    assert r.status_code == 415
    assert env["stub"].seen == []


def test_referer_counts_when_the_browser_sends_no_origin(env) -> None:
    env["stub"].replies[("POST", "/v1/cards/3/answer")] = (200, {"changed": True})
    headers = {"referer": f"{ORIGIN}/#hub", "content-type": "application/json"}
    assert (
        env["client"]
        .post("/api/hub/cards/3/answer", json={"choice": 0}, headers=headers)
        .status_code
        == 200
    )


def test_foreign_host_header_is_refused_even_for_reads(env) -> None:
    r = env["client"].get("/api/hub/state", headers={"host": "attacker.example:8765"})
    assert r.status_code == 403
    assert env["stub"].seen == []


def test_cross_site_rule_writes_are_refused(env) -> None:
    before = env["rules"].read_bytes()
    version = env["client"].get("/api/hub/rules").json()["version"]
    evil = {"origin": "https://evil.example", "content-type": "application/json"}
    for method, path, body in [
        (
            "POST",
            "/api/hub/rules/save",
            {"original": "晨报", "rule": RULES["rules"][0], "version": version},
        ),
        ("PUT", "/api/hub/rules/text", {"text": "{}", "version": version}),
        ("POST", "/api/hub/rules/rollback", {"version": version}),
        ("POST", "/api/hub/rules/control", {"name": "晨报", "action": "pause"}),
        ("POST", "/api/eval/draft", {"items": []}),
    ]:
        assert env["client"].request(method, path, json=body, headers=evil).status_code == 403
    assert env["rules"].read_bytes() == before


# ── Rules ───────────────────────────────────────────────────────────────────────────────────────


def _version(env) -> str:
    return env["client"].get("/api/hub/rules").json()["version"]


def test_invalid_rule_is_refused_with_the_checkers_words_and_the_file_is_unchanged(env) -> None:
    before = env["rules"].read_bytes()
    rule = {**RULES["rules"][0], "to": "nobody"}
    r = env["client"].post(
        "/api/hub/rules/save",
        json={"original": "晨报", "rule": rule, "version": _version(env)},
        headers=SAME,
    )
    assert r.status_code == 422
    assert r.json()["issues"] == ["规则「晨报」的 to 不是已登记的 Agent：nobody"]
    assert env["rules"].read_bytes() == before
    assert env["store"].backups() == []
    assert not env["settings"].audit_file.exists()


def test_check_is_a_dry_run(env) -> None:
    before = env["rules"].read_bytes()
    r = env["client"].post("/api/hub/rules/check", json={"text": "{nope"}, headers=SAME)
    assert r.status_code == 200 and r.json()["ok"] is False and "不是 JSON" in r.json()["issues"][0]
    rule = {**RULES["rules"][0], "prompt": "改一下"}
    r = env["client"].post(
        "/api/hub/rules/check", json={"original": "晨报", "rule": rule}, headers=SAME
    )
    assert r.json() == {"ok": True, "issues": []}
    assert env["rules"].read_bytes() == before


def test_valid_edit_backs_up_writes_audits_and_rolls_back(env) -> None:
    client, rules = env["client"], env["rules"]
    original = rules.read_bytes()
    rule = {**RULES["rules"][0], "when": {"cron": "30 8 * * *"}}
    r = client.post(
        "/api/hub/rules/save",
        json={"original": "晨报", "rule": rule, "version": _version(env)},
        headers=SAME,
    )
    assert r.status_code == 200, r.text
    backup = r.json()["backup"]
    assert backup.startswith("rules.json.bak-")
    assert (rules.parent / backup).read_bytes() == original
    assert json.loads(rules.read_text())["rules"][0]["when"] == {"cron": "30 8 * * *"}
    [entry] = client.get("/api/hub/rules/audit").json()["entries"]
    assert entry["by"] == "tester@kg-web" and entry["action"] == "edit"
    assert entry["changes"] == {
        "added": [],
        "removed": [],
        "changed": {"晨报": ["when"]},
        "sections": [],
    }
    assert entry["backup"] == backup and "30 8" in entry["diff"]

    r = client.post("/api/hub/rules/rollback", json={"version": _version(env)}, headers=SAME)
    assert r.status_code == 200, r.text
    assert rules.read_bytes() == original
    entries = client.get("/api/hub/rules/audit").json()["entries"]
    assert [e["action"] for e in entries] == ["rollback", "edit"]
    assert len(env["store"].backups()) == 2


def test_new_rule_and_duplicate_name(env) -> None:
    client = env["client"]
    rule = {
        "name": "新的",
        "when": {"cron": "0 7 * * *"},
        "to": "claude",
        "cwd": "~/y",
        "prompt": "p",
    }
    r = client.post(
        "/api/hub/rules/save", json={"rule": rule, "version": _version(env)}, headers=SAME
    )
    assert r.status_code == 200
    assert [x["name"] for x in json.loads(env["rules"].read_text())["rules"]] == [
        "晨报",
        "处理",
        "新的",
    ]
    r = client.post(
        "/api/hub/rules/save", json={"rule": rule, "version": _version(env)}, headers=SAME
    )
    assert r.status_code == 422 and "已经有" in r.json()["issues"][0]


def test_stale_version_is_refused(env) -> None:
    version = _version(env)
    env["rules"].write_text(
        env["rules"].read_text() + " ", encoding="utf-8"
    )  # someone edits by hand
    before = env["rules"].read_bytes()
    r = env["client"].put(
        "/api/hub/rules/text", json={"text": json.dumps(RULES), "version": version}, headers=SAME
    )
    assert r.status_code == 409
    assert env["rules"].read_bytes() == before


def test_whole_file_edit_runs_through_the_check(env) -> None:
    bad = {**RULES, "rules": [{**RULES["rules"][0], "to": "ghost"}]}
    before = env["rules"].read_bytes()
    r = env["client"].put(
        "/api/hub/rules/text",
        json={"text": json.dumps(bad), "version": _version(env)},
        headers=SAME,
    )
    assert r.status_code == 422 and env["rules"].read_bytes() == before


def test_rollback_without_backups_is_refused(env) -> None:
    r = env["client"].post("/api/hub/rules/rollback", json={"version": _version(env)}, headers=SAME)
    assert r.status_code == 422 and "还没有网页保存时留下的备份" in r.json()["issues"][0]


def test_rollback_to_an_invalid_backup_is_refused(env) -> None:
    (env["rules"].parent / "rules.json.bak-20260101-000000").write_text('{"rules": [{"to": "x"}]}')
    before = env["rules"].read_bytes()
    r = env["client"].post("/api/hub/rules/rollback", json={"version": _version(env)}, headers=SAME)
    assert r.status_code == 422 and env["rules"].read_bytes() == before


def _control(env, **body: Any):
    return env["client"].post("/api/hub/rules/control", json=body, headers=SAME)


def test_rule_control_goes_to_the_hubs_owner_api(env) -> None:
    stub = env["stub"]
    paused = {"changed": True, "rule": "晨报", "paused": [{"kind": "rule", "reason": "放假"}]}
    stub.replies[("POST", "/v1/rules/%E6%99%A8%E6%8A%A5/pause")] = (200, paused)
    stub.replies[("POST", "/v1/rules/%E6%99%A8%E6%8A%A5/resume")] = (200, {"changed": True})
    stub.replies[("POST", "/v1/rules/%E6%99%A8%E6%8A%A5/run")] = (200, {"event": 3, "run": 9})
    until = "2026-11-07T09:00:00+08:00"
    r = _control(env, name="晨报", action="pause", reason=" 放假 ", until=until)
    assert r.status_code == 200 and r.json() == paused
    assert stub.seen[-1]["body"] == {"reason": "放假", "until": until}
    assert stub.seen[-1]["auth"] == f"Bearer {OWNER}"
    assert _control(env, name="晨报", action="resume").json() == {"changed": True}
    assert stub.seen[-1]["body"] == {}
    r = _control(env, name="晨报", action="run", reason="看一眼")
    assert r.json() == {"event": 3, "run": 9}
    assert stub.seen[-1]["body"] == {"reason": "看一眼"}
    _no_key(r)


def test_capability_control_and_its_limits(env) -> None:
    stub = env["stub"]
    paused = {"changed": True, "rules": ["晨报"]}
    stub.replies[("POST", "/v1/requires/egress/pause")] = (200, paused)
    stub.replies[("POST", "/v1/requires/egress/resume")] = (
        200,
        {"changed": True, "rules": ["晨报"], "stillPaused": []},
    )
    r = _control(env, name="egress", action="pause", scope="requires", reason="出口断了")
    assert r.json()["rules"] == ["晨报"] and stub.seen[-1]["body"] == {"reason": "出口断了"}
    assert _control(env, name="egress", action="resume", scope="requires").status_code == 200
    n = len(stub.seen)
    assert _control(env, name="egress", action="run", scope="requires").status_code == 400
    assert len(stub.seen) == n, "refused here, never sent"


def test_hub_refusals_keep_their_words(env) -> None:
    stub = env["stub"]
    stub.replies[("POST", "/v1/rules/x/pause")] = (404, {"error": "没有这条规则：x"})
    refused = {"error": "规则「x」暂停中（这条规则），先恢复再手动运行"}
    stub.replies[("POST", "/v1/rules/x/run")] = (409, refused)
    r = _control(env, name="x", action="pause")
    assert r.status_code == 404 and r.json() == {"error": "没有这条规则：x", "source": "hub"}
    r = _control(env, name="x", action="run")
    assert r.status_code == 409 and "先恢复" in r.json()["error"]


def test_an_old_hub_says_it_needs_an_upgrade_not_a_server_error(env) -> None:
    # The stub, like a hub without rule control, answers 404 没有这个地址 for paths it doesn't know.
    for body in (
        {"name": "晨报", "action": "pause"},
        {"name": "晨报", "action": "resume"},
        {"name": "晨报", "action": "run"},
        {"name": "egress", "action": "pause", "scope": "requires"},
    ):
        r = _control(env, **body)
        assert r.status_code == 501, body
        assert "中台版本不支持暂停" in r.json()["error"] and r.json()["upgrade"] is True


def test_state_says_what_is_paused_on_a_new_hub(env) -> None:
    stub = env["stub"]
    pause = {"kind": "requires", "name": "egress", "by": "owner", "reason": "出口断了"}
    stub.replies[("GET", "/v1/state")] = (
        200,
        {
            "runs": [],
            "pauses": [{**pause, "rules": ["晨报"]}],
            "lines": [
                {"name": "晨报", "paused": [pause], "requires": ["egress"]},
                {"name": "处理", "paused": [], "requires": []},
            ],
        },
    )
    control = env["client"].get("/api/hub/state").json()["control"]
    assert control == {"available": True, "reason": None, "paused": {"晨报": [pause]}}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "晨报", "action": "stop"},
        {"name": "", "action": "pause"},
        {"name": "晨报", "action": "pause", "scope": "all"},
        {"name": "晨报", "action": "pause", "reason": "长" * 501},
    ],
)
def test_bad_control_bodies_never_reach_the_hub(env, body) -> None:
    assert _control(env, **body).status_code == 422
    assert env["stub"].seen == []


# ── The real checker, in a throwaway home ───────────────────────────────────────────────────────


def _fake_cli(tmp_path: Path, real_home: Path) -> Path:
    """A stand-in `agent-autoloop` that fails loudly if pointed at the real home."""
    script = tmp_path / "fake-autoloop"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "home = os.environ.get('AGENT_AUTOLOOP_HOME', '')\n"
        "rules = os.environ.get('AGENT_AUTOLOOP_RULES')\n"
        "if sys.argv[1:] == ['help']:\n"
        "    print('  agent-autoloop check                   检查 rules.json')\n"
        "    sys.exit(0)\n"
        f"assert not home.startswith({str(real_home)!r}), 'checked in the real home'\n"
        "assert sys.argv[1:] == ['check']\n"
        "data = json.load(open(rules))\n"
        "bad = [r['name'] for r in data['rules'] if r['to'] not in data['agents']]\n"
        "if bad:\n"
        "    print('rules.json 有问题：', file=sys.stderr)\n"
        "    [print(f'规则「{n}」的 to 不是已登记的 Agent', file=sys.stderr) for n in bad]\n"
        "    sys.exit(1)\n"
        "print('规则没问题。')\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _new_cli(tmp_path: Path, real_home: Path, log: Path) -> Path:
    """A stand-in for a CLI with `check [文件]`: checks the file it is given, never a home."""
    script = tmp_path / "new-autoloop"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps([sys.argv[1:], "
        "{k: v for k, v in os.environ.items() if k.startswith('AGENT_AUTOLOOP_')}]) + '\\n')\n"
        "if sys.argv[1:] == ['help']:\n"
        "    print('  agent-autoloop check [文件]            检查规则文件，不碰中台')\n"
        "    sys.exit(0)\n"
        "assert sys.argv[1] == 'check' and len(sys.argv) == 3, sys.argv\n"
        "data = json.load(open(sys.argv[2]))\n"
        "bad = [r['name'] for r in data['rules'] if r['to'] not in data['agents']]\n"
        "if bad:\n"
        "    print('rules.json 有问题：', file=sys.stderr)\n"
        "    [print(f'规则「{n}」的 to 不是已登记的 Agent', file=sys.stderr) for n in bad]\n"
        "    sys.exit(1)\n"
        "print('规则没问题：' + sys.argv[2])\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_a_cli_that_checks_a_file_gets_the_file_and_no_home(tmp_path: Path, monkeypatch) -> None:
    real_home = tmp_path / "real-home"
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(real_home))
    checker = CliChecker((str(_new_cli(tmp_path, real_home, log)),))
    assert checker(json.dumps(RULES)) == []
    bad = {**RULES, "rules": [{**RULES["rules"][0], "to": "ghost"}]}
    assert checker(json.dumps(bad)) == ["规则「晨报」的 to 不是已登记的 Agent"]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [argv[0] for argv, _ in calls] == ["help", "check", "check"], "help is asked once"
    assert all(len(argv) == 2 for argv, _ in calls[1:]), "the candidate file is named"
    assert all(env == {} for _, env in calls), "no throwaway home, and not the real one either"
    assert not real_home.exists()


def test_cli_checker_uses_a_throwaway_home(tmp_path: Path, monkeypatch) -> None:
    # An older CLI, which checks only its own RULES_FILE: its help doesn't offer a file to check.
    real_home = tmp_path / "real-home"
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(real_home))
    checker = CliChecker((str(_fake_cli(tmp_path, real_home)),))
    assert checker(json.dumps(RULES)) == []
    assert checker.checks_a_file() is False
    bad = {**RULES, "rules": [{**RULES["rules"][0], "to": "ghost"}]}
    assert checker(json.dumps(bad)) == ["规则「晨报」的 to 不是已登记的 Agent"]
    assert not real_home.exists()


def test_missing_checker_writes_nothing(env) -> None:
    store = RulesStore(
        env["rules"], env["settings"].audit_file, CliChecker(("no-such-autoloop-cli",))
    )
    before = env["rules"].read_bytes()
    env["client"].app.dependency_overrides[hub.get_store] = lambda: store
    r = env["client"].put(
        "/api/hub/rules/text",
        json={"text": json.dumps(RULES), "version": _version(env)},
        headers=SAME,
    )
    assert r.status_code == 503 and env["rules"].read_bytes() == before


@pytest.mark.skipif(shutil.which("agent-autoloop") is None, reason="agent-autoloop not installed")
def test_real_agent_autoloop_check_rejects_and_accepts(tmp_path: Path) -> None:
    checker = CliChecker(("agent-autoloop",))
    assert checker(json.dumps(RULES)) == []
    issues = checker(json.dumps({**RULES, "rules": [{**RULES["rules"][0], "to": "ghost", "x": 1}]}))
    assert any("不是已登记的 Agent" in i for i in issues) and any(
        "不认识的字段 x" in i for i in issues
    )
    assert "不是 JSON" in checker("{")[0]
    assert not any(
        k.startswith("AGENT_AUTOLOOP_") and "kg-rules-check" in v for k, v in os.environ.items()
    )


# ── Eval review page ────────────────────────────────────────────────────────────────────────────


def test_eval_draft_save_and_publish(env) -> None:
    vault: Path = env["tmp"]
    (vault / "07-Projects").mkdir()
    (vault / "07-Projects" / "p1.md").write_text(
        "---\nid: p1\ntitle: 项目一\n---\n正文\n", encoding="utf-8"
    )
    folder = vault / ".kg" / "eval"
    folder.mkdir(parents=True)
    items = [
        {
            "n": 0,
            "q": "问一",
            "source": "s",
            "project": "x",
            "expected": ["p1"],
            "candidates": [],
            "state": "ok",
        },
        {
            "n": 1,
            "q": "问二",
            "source": "s",
            "project": "x",
            "expected": [],
            "candidates": ["p1"],
            "state": "skip",
        },
    ]
    (folder / "draft.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    client = env["client"]
    d = client.get("/api/eval/draft").json()
    assert d["entries"]["p1"] == {
        "title": "项目一",
        "board": "07-Projects",
        "path": "07-Projects/p1.md",
    }
    items[0]["state"] = "bogus"
    assert client.post("/api/eval/draft", json={"items": items}, headers=SAME).json() == {
        "saved": 2
    }
    assert json.loads((folder / "draft.json").read_text())[0]["state"] == "draft"
    r = client.post("/api/eval/publish", json={"items": items}, headers=SAME).json()
    assert r == {"saved": 2, "published": 1, "labelled": 1}
    assert [json.loads(x)["q"] for x in (folder / "retrieval.jsonl").read_text().splitlines()] == [
        "问一"
    ]


# ── Settings ────────────────────────────────────────────────────────────────────────────────────


def test_settings_defaults_local_file_and_env(tmp_path: Path, monkeypatch) -> None:
    for env in ("KG_HUB_CONFIG", "KG_HUB_RULES", "KG_HUB_CLI", "KG_HUB_AUDIT"):
        monkeypatch.delenv(env, raising=False)
    s = load_settings(tmp_path)
    assert s.rules_file == Path("~/.agent-autoloop/rules.json").expanduser()
    assert s.audit_file == tmp_path / ".kg" / "plane" / "hub-audit.jsonl"
    (tmp_path / ".kg").mkdir()
    (tmp_path / ".kg" / "hub.json").write_text(
        json.dumps({"_说明": "x", "rules_file": "/x/rules.json", "cli": "node /x/cli.ts"})
    )
    s = load_settings(tmp_path)
    assert s.rules_file == Path("/x/rules.json") and s.cli == ("node", "/x/cli.ts")
    monkeypatch.setenv("KG_HUB_RULES", str(tmp_path / "r.json"))
    assert load_settings(tmp_path).rules_file == tmp_path / "r.json"


def test_example_settings_file_loads(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[1] / "hub.json.example"
    (tmp_path / ".kg").mkdir()
    shutil.copy(example, tmp_path / ".kg" / "hub.json")
    assert load_settings(tmp_path).cli == ("agent-autoloop",)


def test_previous_version_ignores_hand_made_backups(env) -> None:
    client, rules = env["client"], env["rules"]
    original = rules.read_bytes()
    rule = {**RULES["rules"][0], "prompt": "改过"}
    body = {"original": "晨报", "rule": rule, "version": _version(env)}
    assert client.post("/api/hub/rules/save", json=body, headers=SAME).status_code == 200
    # Someone keeps a copy by hand afterwards; its name sorts after the timestamps.
    (rules.parent / "rules.json.bak-test").write_text('{"rules": []}')
    names = [b["name"] for b in env["store"].backups()]
    assert names[-1] == "rules.json.bak-test"
    r = client.post("/api/hub/rules/rollback", json={"version": _version(env)}, headers=SAME)
    assert r.status_code == 200 and rules.read_bytes() == original
