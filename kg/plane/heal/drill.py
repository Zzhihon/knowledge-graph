"""A simulated egress outage, end to end, with nobody touching anything.

    python -m kg.plane.heal.drill            # prints each step; exit code 1 if a check fails

Nothing real is touched: a temporary vault and state store, probe observations made up minute
by minute (3 up, 14 down, 3 up) fed through the real `egress.outage` rule via `run_collector`,
and a stub agent-autoloop hub on a free 127.0.0.1 port that records every owner request
(pause / resume / run / state) and checks the owner key. The receipts the notifier prints are
routed back the way the hub would: cards become open cards, answers and events come back in the
next round's context. The real proxy and the real hub are never contacted.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from kg.interfaces.cli.commands.plane import _events
from kg.plane.collectors import CollectContext, Collection, run_collector
from kg.plane.egress import Observation, SegmentResult, classify_curl_rc
from kg.plane.egress_signals import observation_to_signal
from kg.plane.heal.config import load_config
from kg.plane.heal.hub_actions import HttpHubActions
from kg.plane.heal.notify import Notifier
from kg.plane.store import Resource, connect, db_path

T0 = 1_800_000_000
OWNER_KEY = "drill-owner-key-not-real"
LINE = "egress:drill"
HEAL_YAML = "actions:\n  egress_pause: auto\n  service_restart: suggest\n  index_stale: suggest\n"


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


@dataclass
class StubHub:
    """Just enough of the hub's owner API, on 127.0.0.1, recording what it was asked."""

    clock: list[int]  # [now]; the drill moves it
    rules: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "夜间沉淀": {"requires": ["egress"], "every": "每 5 分钟"},
        "日报": {"requires": ["egress"], "every": None},
        "采集出口": {"requires": [], "every": "每分钟"},
    })
    requests: list[dict[str, Any]] = field(default_factory=list)
    paused_at: int | None = None
    skipped: dict[str, int] = field(default_factory=dict)

    def start(self) -> str:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self, status: int, body: Any) -> None:
                data = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                ok = self.headers.get("authorization") == f"Bearer {OWNER_KEY}"
                path = unquote(self.path)
                hub.requests.append({"method": method, "path": path, "body": body,
                                     "auth_ok": ok, "at": hub.clock[0]})
                if not ok:
                    return self._reply(401, {"error": "钥匙不对"})
                status, answer = hub.route(method, path, body)
                self._reply(status, answer)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _requiring(self, cap: str) -> list[str]:
        return [n for n, r in self.rules.items() if cap in r["requires"]]

    def route(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        now = self.clock[0]
        if self.paused_at is not None:  # every cron rule that needs egress misses its ticks
            for name in self._requiring("egress"):
                if self.rules[name]["every"]:
                    self.skipped[name] = now
        if method == "GET" and path == "/v1/state":
            lines = [{"name": n, "requires": r["requires"], "every": r["every"],
                      "paused": [{"kind": "requires", "name": "egress"}]
                      if self.paused_at is not None and "egress" in r["requires"] else [],
                      "skippedAt": _iso(self.skipped[n]) if n in self.skipped else None}
                     for n, r in self.rules.items()]
            return 200, {"lines": lines, "skipped": []}
        parts = path.strip("/").split("/")
        if method == "POST" and parts[:2] == ["v1", "requires"] and len(parts) == 4:
            cap, action = parts[2], parts[3]
            names = self._requiring(cap)
            if not names:
                return 404, {"error": f"没有规则写了 requires「{cap}」"}
            if action == "pause":
                changed = self.paused_at is None
                self.paused_at = self.paused_at or now
                return 200, {"changed": changed, "requires": cap, "rules": names}
            if action == "resume":
                changed = self.paused_at is not None
                self.paused_at = None
                return 200, {"changed": changed, "requires": cap, "rules": names,
                             "stillPaused": []}
        if method == "POST" and parts[:2] == ["v1", "rules"] and len(parts) == 4 \
                and parts[3] == "run":
            name = parts[2]
            if name not in self.rules:
                return 404, {"error": f"没有这条规则：{name}"}
            if self.paused_at is not None and "egress" in self.rules[name]["requires"]:
                return 409, {"error": f"规则「{name}」暂停中"}
            return 200, {"event": len(self.requests), "run": len(self.requests)}
        return 404, {"error": "没有这个地址"}


class _Probe:
    """A collector that reports one made-up probe of the drill line per call."""

    name = "egress-drill"

    def __init__(self) -> None:
        self.next: Observation | None = None

    def collect(self, ctx: CollectContext) -> Collection:
        assert self.next is not None
        return Collection(resources=[Resource(LINE, "egress", "drill", self.name)],
                          signals=[observation_to_signal(self.next, resource_id=LINE,
                                                         interval_s=60)])


def _up(ts: int) -> Observation:
    return Observation(ts, (SegmentResult("local_port", "up"), SegmentResult("identity", "up")),
                       exit_ip="192.0.2.7", ip_ok=True)


def _down(ts: int) -> Observation:
    return Observation(ts, (SegmentResult("local_port", "up"), classify_curl_rc(35)), curl_rc=35)


@dataclass
class DrillResult:
    log: list[str]
    receipts: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    incidents: list[dict[str, Any]]
    retro_events: list[dict[str, Any]]
    vault: Path
    files: dict[str, str]
    problems: list[str]


def run_drill(root: Path, *, pattern: str = "uuu" + "d" * 14 + "uuu",
              echo: bool = False) -> DrillResult:
    """Run the drill in `root` (an empty temporary directory)."""
    vault = root / "vault"
    (vault / ".kg" / "plane").mkdir(parents=True)
    (vault / ".kg" / "plane" / "heal.yaml").write_text(HEAL_YAML, encoding="utf-8")
    clock = [T0]
    hub = StubHub(clock)
    url = hub.start()
    hub_config = root / "hub-config.json"
    hub_config.write_text(json.dumps({"url": url, "owner": OWNER_KEY}), encoding="utf-8")
    config = load_config(vault)
    actions_api = HttpHubActions(hub_config, timeout=5)
    log: list[str] = []
    receipts: list[dict[str, Any]] = []
    open_cards: list[dict[str, Any]] = []
    retro: list[dict[str, Any]] = []
    rounds = [0]

    def say(text: str) -> None:
        log.append(text)
        if echo:
            print(text)

    def round_(context: dict[str, Any]) -> dict[str, Any]:
        rounds[0] += 1
        conn = connect(db_path(vault))
        try:
            n = Notifier(conn, vault, config, clock[0], run=str(rounds[0]), hub=actions_api)
            receipt = n.handle({**context, "open": list(open_cards)})
        finally:
            conn.close()
        body = receipt.body()
        receipts.append(body)
        for card in body.get("ask", []):
            open_cards.append({"card": 100 + len(receipts), "title": card["title"]})
        resolved = {r["card"] for r in body.get("resolve", [])}
        open_cards[:] = [c for c in open_cards if c["card"] not in resolved]
        retro.extend(e for e in body.get("events", []) if e["type"] == "incident.retro.ready")
        say(f"  中台第 {rounds[0]} 轮 → {receipt.render().splitlines()[0]}")
        return body

    probe = _Probe()
    try:
        say(f"假中台 {url.split('//')[1].split(':')[0]}，假出口线路 {LINE}，"
            "heal.yaml: egress_pause=auto")
        for i, ch in enumerate(pattern):
            clock[0] = T0 + i * 60
            probe.next = (_up if ch == "u" else _down)(clock[0])
            conn = connect(db_path(vault))
            try:
                result = run_collector(conn, probe, ctx=CollectContext(now=clock[0], vault=vault))
            finally:
                conn.close()
            events = _events(result)
            if not events:
                continue
            say(f"第 {i} 分钟 探测{'成功' if ch == 'u' else '失败'}："
                + "，".join(f"{e['type']} {e['id']}" for e in events))
            round_({"events": events})
            if events[0]["type"] == "plane.incident.opened":
                say("  （同一事件再投递一次，验证不重复通知）")
                again = round_({"events": events})
                if again.get("ask") or again.get("notice"):
                    say("  !! 重复通知了")
        conn = connect(db_path(vault))
        try:
            incidents = [dict(r) for r in conn.execute("SELECT * FROM incident ORDER BY id")]
        finally:
            conn.close()
        for e in retro:
            say(f"复盘事件 {e['id']} → {e['detail']}")
        files = {}
        for name in ("heal-audit.jsonl", "notified.json"):
            p = vault / ".kg" / "plane" / name
            files[name] = p.read_text(encoding="utf-8") if p.exists() else ""
        for e in retro:
            files[e["detail"]] = (vault / e["detail"]).read_text(encoding="utf-8")
    finally:
        hub.stop()
    problems = check(receipts, hub.requests, incidents, retro, files)
    return DrillResult(log, receipts, hub.requests, incidents, retro, vault, files, problems)


def check(receipts: list[dict[str, Any]], requests: list[dict[str, Any]],
          incidents: list[dict[str, Any]], retro: list[dict[str, Any]],
          files: dict[str, str]) -> list[str]:
    """What the drill must show; each failure is one line."""
    problems = []
    asks = [a for r in receipts for a in r.get("ask", [])]
    notices = [n for r in receipts for n in r.get("notice", [])]
    owner_calls = [(q["method"], q["path"]) for q in requests]
    if len(incidents) != 1 or incidents[0]["closed_at"] is None:
        problems.append(f"应该开关一次事故，实际 {len(incidents)} 次")
    if len(asks) != 1:
        problems.append(f"卡片应该只发一次，实际 {len(asks)} 次")
    if not any("已恢复，持续" in n["title"] for n in notices):
        problems.append("没有带持续时间的恢复通知")
    for want in (("POST", "/v1/requires/egress/pause"), ("POST", "/v1/requires/egress/resume"),
                 ("POST", "/v1/rules/夜间沉淀/run")):
        if want not in owner_calls:
            problems.append(f"中台没收到 {want[0]} {want[1]}")
    if ("POST", "/v1/rules/日报/run") in owner_calls:
        problems.append("没错过时段的规则也被补跑了")
    if owner_calls.count(("POST", "/v1/requires/egress/pause")) != 1:
        problems.append("暂停请求不止一次")
    if not all(q["auth_ok"] for q in requests):
        problems.append("有请求没带对主人密钥")
    if len(retro) != 1:
        problems.append(f"应该有一条复盘事件，实际 {len(retro)} 条")
    everything = json.dumps(receipts, ensure_ascii=False) + "".join(files.values())
    if OWNER_KEY in everything:
        problems.append("主人密钥出现在回执或本地文件里")
    if "192.0.2.7" in everything:
        problems.append("出口 IP 没脱敏")
    return problems


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="kg-heal-drill-") as tmp:
        result = run_drill(Path(tmp), echo=True)
        print("\n中台收到的本人请求：")
        for q in result.requests:
            body = json.dumps(q["body"], ensure_ascii=False) if q["body"] else ""
            print(f"  t+{(q['at'] - T0) // 60:>2} 分  {q['method']} {q['path']} {body}")
        print("\n回执（卡片 / 通知 / 结案 / 事件）：")
        for r in result.receipts:
            if r:
                print("  " + json.dumps(r, ensure_ascii=False)[:600])
        print("\n审计记录：")
        for line in result.files.get("heal-audit.jsonl", "").splitlines():
            e = json.loads(line)
            print(f"  {e['id']} #{e['incident']} {e['action']} {e['target']} "
                  f"{e['mode']}/{e['result']} {e['undo']}")
        print("\n检查：" + ("全部通过" if not result.problems else "；".join(result.problems)))
        return 1 if result.problems else 0


if __name__ == "__main__":
    sys.exit(main())
