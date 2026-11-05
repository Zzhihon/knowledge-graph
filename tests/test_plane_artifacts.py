"""Artifact center (#6): adapters, sensitivity, expiry rules, search export, API.

Every page, link and id here is synthetic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kg.plane import store
from kg.plane.artifact_docs import export_docs, html_to_text
from kg.plane.collectors import CollectContext, CommandError, run_collector
from kg.plane.collectors.artifacts import (
    ArtifactsCollector,
    OriginGuesser,
    load_orca_shares,
    parse_claude_import,
    parse_orca_list,
    parse_time,
)
from kg.plane.rules import replay, rules_for
from kg.plane.sensitive import Scanner
from kg.plane.store import DAY, HOUR, Signal

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000
FAKE_KEY = "sk-" + "Zq8" * 8  # matches the credential pattern; not a real key
SENSITIVE_PAGE = f"""<!doctype html><html><head><title>合成测试页</title>
<style>.x {{ color: red }}</style><script>var hidden = "10.9.9.9";</script></head>
<body><h1>部署记录</h1><p>数据库在 10.20.30.40，备机 db01.intra.test。</p>
<p>密钥 {FAKE_KEY}</p></body></html>"""
CLEAN_PAGE = """<html><head><title>干净的页</title></head><body>
<p>提交 0123456789abcdef0123456789abcdef01234567，见 https://example.com/docs。</p>
<p>本机服务 127.0.0.1:8000，文档示例 192.0.2.10，</p>
<p>路径 operator/internal/controllers/reconcile_loop_handler_v2.go。</p>
</body></html>"""


# ── sensitivity ──


def test_scanner_finds_credentials_ips_and_internal_hosts() -> None:
    scanner = Scanner(["intra.test"])
    kinds = {h.kind for h in scanner.scan(html_to_text(SENSITIVE_PAGE)[1])}
    assert kinds == {"private_ip", "internal_host", "credential"}


def test_scanner_ignores_hashes_links_loopback_docs_ranges_and_paths() -> None:
    assert Scanner().scan(html_to_text(CLEAN_PAGE)[1]) == []


def test_scanner_password_label_needs_a_token_like_value() -> None:
    scanner = Scanner()
    assert scanner.scan("token: 用量统计按天汇总") == []
    assert [h.kind for h in scanner.scan("password: hunter2Xy9!")] == ["credential"]


def test_public_resolvers_are_not_sensitive_but_other_public_ips_are() -> None:
    scanner = Scanner()
    assert scanner.scan("DNS 用 8.8.8.8 和 1.1.1.1") == []
    assert [h.kind for h in scanner.scan("出口是 45.77.1.2")] == ["public_ip"]


def test_redact_masks_every_hit_and_keeps_the_rest() -> None:
    scanner = Scanner(["intra.test"])
    out = scanner.redact(f"在 10.20.30.40 上，db01.intra.test，{FAKE_KEY}，其余照旧")
    assert "10.20" not in out and "intra.test" not in out and FAKE_KEY not in out
    assert out.count("[已遮盖]") == 3 and out.endswith("其余照旧")


def test_samples_are_masked() -> None:
    hits = Scanner().scan(f"key {FAKE_KEY} at 10.20.30.40")
    assert all(FAKE_KEY not in h.sample and "30.40" not in h.sample for h in hits)


# ── text ──


def test_html_to_text_drops_script_and_style_and_takes_the_title() -> None:
    title, text = html_to_text(SENSITIVE_PAGE)
    assert title == "合成测试页"
    assert "部署记录" in text and "hidden" not in text and "color" not in text


def test_html_to_text_falls_back_to_h1() -> None:
    assert html_to_text("<body><h1>只有标题</h1><p>正文</p></body>")[0] == "只有标题"


# ── adapters ──


def _orca_json(items: list[dict], cursor: str | None = None) -> str:
    result: dict = {"artifacts": items}
    if cursor:
        result["nextCursor"] = cursor
    return json.dumps({"id": "x", "ok": True, "result": result})


def _orca_item(slug: str, *, expires: int, title: str | None = None) -> dict:
    def iso(ts: int) -> str:
        return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")

    return {
        "artifact": {
            "version": 1,
            "slug": slug,
            "title": title,
            "originalFileName": f"{slug}.html",
            "sourceContentType": "text/html",
            "createdAt": iso(NOW - DAY),
            "updatedAt": iso(NOW - HOUR),
            "expiresAt": iso(expires),
            "byteSize": 10,
            "deletedAt": None,
        },
        "shareUrl": f"https://share.example.test/a/{slug}",
    }


def test_parse_orca_list_and_time() -> None:
    items, cursor = parse_orca_list(_orca_json([_orca_item("abc", expires=NOW)], "next1"))
    assert cursor == "next1" and items[0]["artifact"]["slug"] == "abc"
    assert parse_time("2026-11-05T17:20:30.914Z") == 1793899230
    assert parse_time(1_800_000_000_000) == 1_800_000_000
    with pytest.raises(ValueError):
        parse_orca_list(json.dumps({"ok": False, "error": "not signed in"}))


def test_orca_share_map_reads_paths_and_slugs_only(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<p>x</p>", encoding="utf-8")
    shares = tmp_path / "profiles" / "p1" / "artifact-shares.json"
    shares.parent.mkdir(parents=True)
    shares.write_text(
        json.dumps(
            {
                "version": 1,
                "shares": {
                    str(page): {
                        "slug": "abc",
                        "editToken": "do-not-keep",
                        "shareUrl": "https://x.test/a",
                    }
                },
            }
        )
    )
    found = load_orca_shares([str(tmp_path / "profiles" / "*" / "artifact-shares.json")])
    assert found == {"abc": page}


def test_parse_claude_import() -> None:
    raw = {
        "version": 1,
        "synced_at": "2026-11-06T09:00:00+08:00",
        "items": [
            {"url": "https://claude.ai/artifact/abc-123", "title": "方案", "updated_at": NOW},
            {"url": "https://claude.ai/code/artifact/00000000-0000-4000-8000-000000000000"},
            {"url": "https://elsewhere.test/artifact/x"},
        ],
    }
    entries, synced, problems = parse_claude_import(raw)
    assert [e.id for e in entries] == [
        "artifact:claude:abc-123",
        "artifact:claude:00000000-0000-4000-8000-000000000000",
    ]
    assert all(e.visibility == "private" for e in entries)
    assert synced == parse_time("2026-11-06T09:00:00+08:00") and len(problems) == 1
    with pytest.raises(ValueError):
        parse_claude_import({"items": []})


def test_origin_from_git_root_and_claude_scratchpad(tmp_path: Path) -> None:
    repo = tmp_path / "demo-repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "docs").mkdir()
    guesser = OriginGuesser(None)
    assert guesser.guess(repo / "docs" / "plan.html") == ("demo-repo", None)
    uuid = "12345678-1234-4234-8234-123456789abc"
    pad = tmp_path / "tmp" / "-Users-someone-demo" / uuid / "scratchpad" / "p.html"
    assert guesser.guess(pad) == (None, f"session:claude:{uuid}")


# ── the collector, end to end ──


def _vault(tmp_path: Path, *, claude: dict | None = None) -> tuple[Path, Path, Path]:
    vault = tmp_path / "vault"
    pages = tmp_path / "pages"
    shared = tmp_path / "shared"
    for d in (vault / ".kg" / "plane", pages, shared):
        d.mkdir(parents=True)
    shares = tmp_path / "orca-shares.json"
    (vault / ".kg" / "plane" / "artifacts.yaml").write_text(
        f"local:\n  - path: {json.dumps(str(pages))}\n    origin: test-pages\n"
        f"orca:\n  shares: [{json.dumps(str(shares))}]\n"
        f"sensitive:\n  internal_domains: [intra.test]\n",
        encoding="utf-8",
    )
    (shared / "leak.html").write_text(SENSITIVE_PAGE, encoding="utf-8")
    (shared / "clean.html").write_text(CLEAN_PAGE, encoding="utf-8")
    shares.write_text(
        json.dumps(
            {
                "shares": {
                    str(shared / "leak.html"): {"slug": "leak1", "editToken": "t"},
                    str(shared / "clean.html"): {"slug": "clean1", "editToken": "t"},
                }
            }
        )
    )
    (pages / "local-leak.html").write_text(SENSITIVE_PAGE, encoding="utf-8")
    if claude is not None:
        (vault / ".kg" / "plane" / "claude-artifacts.json").write_text(json.dumps(claude))
    return vault, pages, shared


def _orca_run(items: list[dict]):
    def run(argv, timeout=0):
        assert argv[:3] == ["orca", "artifacts", "list"]
        return _orca_json(items)

    return run


def test_public_link_with_sensitive_page_opens_critical(tmp_path: Path) -> None:
    vault, _, _ = _vault(tmp_path)
    items = [
        _orca_item("leak1", expires=NOW + 20 * DAY),
        _orca_item("clean1", expires=NOW + 20 * DAY),
    ]
    conn = store.connect(tmp_path / "state.db")
    ctx = CollectContext(now=NOW, vault=vault, run=_orca_run(items))
    result = run_collector(conn, ArtifactsCollector(), ctx=ctx)
    opened = {(c.resource_id, c.rule, c.severity) for c in result.changes if c.action == "open"}
    # Only the public one: the same text in a local-only page is not an incident.
    assert opened == {("artifact:orca:leak1", "artifact.public_sensitive", "critical")}
    inc = store.list_incidents(conn, open_only=True)[0]
    assert FAKE_KEY not in json.dumps(inc, ensure_ascii=False)
    assert "10.20.30.40" not in json.dumps(inc, ensure_ascii=False)
    res = {r["id"]: r for r in store.list_resources(conn, kind="artifact")}
    assert len(res) == 3
    leak = res["artifact:orca:leak1"]["attrs"]
    assert leak["visibility"] == "public" and leak["local_file"].endswith("leak.html")
    assert "editToken" not in json.dumps(res) and '"t"' not in json.dumps(res)
    assert any(
        r["attrs"]["origin"] == "test-pages" and r["attrs"]["visibility"] == "local"
        for r in res.values()
    )


def test_sensitive_incident_closes_when_the_page_is_cleaned(tmp_path: Path) -> None:
    vault, _, shared = _vault(tmp_path)
    items = [_orca_item("leak1", expires=NOW + 20 * DAY)]
    conn = store.connect(tmp_path / "state.db")
    run_collector(conn, ArtifactsCollector(), ctx=CollectContext(NOW, vault, _orca_run(items)))
    (shared / "leak.html").write_text(CLEAN_PAGE, encoding="utf-8")
    result = run_collector(
        conn, ArtifactsCollector(), ctx=CollectContext(NOW + 1800, vault, _orca_run(items))
    )
    assert [(c.action, c.rule) for c in result.changes] == [("close", "artifact.public_sensitive")]


def test_expiry_warning_three_days_before(tmp_path: Path) -> None:
    vault, _, _ = _vault(tmp_path)
    conn = store.connect(tmp_path / "state.db")
    items = [_orca_item("clean1", expires=NOW + 2 * DAY + 5 * HOUR)]
    result = run_collector(
        conn, ArtifactsCollector(), ctx=CollectContext(NOW, vault, _orca_run(items))
    )
    assert [(c.action, c.rule, c.severity, c.summary) for c in result.changes] == [
        ("open", "artifact.expiring", "warning", "3 天内过期")
    ]


def _sig(ts: int, expires: int | None, **detail) -> Signal:
    return Signal("artifact:orca:x", ts, "up", detail={"expires_at": expires, **detail})


def test_expiring_rule_replay_open_renew_and_expire() -> None:
    (r,) = [x for x in rules_for("artifact") if x.name == "artifact.expiring"]
    t = NOW + 30 * DAY
    history = [
        _sig(t - 5 * DAY, t),
        _sig(t - 3 * DAY - HOUR, t),
        _sig(t - 3 * DAY + HOUR, t),
        _sig(t - DAY, t),
        _sig(t + HOUR, t),
    ]
    assert replay(r, history) == [("open", t - 3 * DAY + HOUR), ("close", t + HOUR)]
    renewed = [_sig(t - 2 * DAY, t), _sig(t - DAY, t + 30 * DAY)]
    assert replay(r, renewed) == [("open", t - 2 * DAY), ("close", t - DAY)]


def test_public_sensitive_rule_skips_when_there_is_no_local_text() -> None:
    (r,) = [x for x in rules_for("artifact") if x.name == "artifact.public_sensitive"]
    hits = [{"kind": "private_ip", "label": "私网 IP", "count": 1}]
    history = [
        _sig(NOW, None, visibility="public", sensitive=None),
        _sig(NOW + 1, None, visibility="public", sensitive=True, hits=hits),
        _sig(NOW + 2, None, visibility="public", sensitive=None),
        _sig(NOW + 3, None, visibility="private", sensitive=True, hits=hits),
    ]
    assert replay(r, history) == [("open", NOW + 1), ("close", NOW + 3)]


def test_orca_failure_keeps_orca_resources(tmp_path: Path) -> None:
    vault, _, _ = _vault(tmp_path)
    conn = store.connect(tmp_path / "state.db")
    items = [_orca_item("clean1", expires=NOW + 20 * DAY)]
    run_collector(conn, ArtifactsCollector(), ctx=CollectContext(NOW, vault, _orca_run(items)))

    def broken(argv, timeout=0):
        raise CommandError("orca: not running")

    result = run_collector(
        conn, ArtifactsCollector(), ctx=CollectContext(NOW + 1800, vault, broken)
    )
    assert result.gone == [] and any("Orca" in n for n in result.notes)


def test_claude_import_feeds_resources_and_warns_when_stale(tmp_path: Path) -> None:
    claude = {
        "version": 1,
        "synced_at": NOW - 3 * DAY,
        "items": [{"url": "https://claude.ai/artifact/abc-123", "title": "合成方案"}],
    }
    vault, _, _ = _vault(tmp_path, claude=claude)
    conn = store.connect(tmp_path / "state.db")
    result = run_collector(
        conn, ArtifactsCollector(), ctx=CollectContext(NOW, vault, _orca_run([]))
    )
    assert store.get_resource(conn, "artifact:claude:abc-123")["attrs"]["visibility"] == "private"
    assert any("3 天没同步" in n for n in result.notes)


# ── export for search ──


def test_export_docs_masks_and_carries_link(tmp_path: Path) -> None:
    vault, _, _ = _vault(tmp_path)
    conn = store.connect(tmp_path / "state.db")
    items = [_orca_item("leak1", expires=NOW + 20 * DAY, title="合成公开页")]
    run_collector(conn, ArtifactsCollector(), ctx=CollectContext(NOW, vault, _orca_run(items)))
    docs = {d.id: d for d in export_docs(conn, Scanner(["intra.test"]))}
    doc = docs["artifact:orca:leak1"]
    assert doc.title == "合成公开页" and doc.url == "https://share.example.test/a/leak1"
    assert "部署记录" in doc.text and "10.20.30.40" not in doc.text and FAKE_KEY not in doc.text
    assert doc.sensitive and doc.source == "orca"
    assert set(doc.to_dict()) >= {"id", "title", "text", "source", "url", "file"}


# ── API ──


@pytest.fixture
def api(tmp_path: Path):
    from agents.api_routes import artifacts as route
    from agents.api_routes import plane

    vault, pages, shared = _vault(tmp_path)
    path = tmp_path / "state.db"
    conn = store.connect(path)
    items = [_orca_item("leak1", expires=NOW + 2 * DAY)]
    run_collector(conn, ArtifactsCollector(), ctx=CollectContext(NOW, vault, _orca_run(items)))
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    app.dependency_overrides[plane.plane_db_path] = lambda: path
    from kg.plane.collectors.artifacts import load_artifacts_config

    app.dependency_overrides[route.artifacts_config] = lambda: load_artifacts_config(vault)
    return TestClient(app), conn, pages, shared, tmp_path


def _local_id(client: TestClient) -> str:
    items = client.get("/api/artifacts").json()["items"]
    return next(i["id"] for i in items if i["attrs"]["adapter"] == "local")


def test_api_lists_artifacts_with_incidents(api) -> None:
    client, *_ = api
    body = client.get("/api/artifacts").json()
    assert body["count"] == 2
    leak = next(i for i in body["items"] if i["id"] == "artifact:orca:leak1")
    assert {i["rule"] for i in leak["incidents"]} == {
        "artifact.public_sensitive",
        "artifact.expiring",
    }
    assert leak["sensitive"] is True and leak["previewable"] is False  # outside the roots
    assert next(i for i in body["items"] if i["attrs"]["adapter"] == "local")["previewable"]


def test_api_preview_serves_registered_file_sandboxed(api) -> None:
    client, *_ = api
    res = client.get("/api/artifacts/preview", params={"id": _local_id(client)})
    assert res.status_code == 200 and "部署记录" in res.text
    assert res.headers["content-security-policy"].startswith("sandbox")
    assert res.headers["content-type"].startswith("text/html")


def test_api_preview_refuses_files_outside_registered_folders(api) -> None:
    client, conn, pages, shared, tmp_path = api
    assert (
        client.get("/api/artifacts/preview", params={"id": "artifact:orca:leak1"}).status_code
        == 403
    )
    secret = tmp_path / "secret.html"
    secret.write_text("<p>outside</p>", encoding="utf-8")
    (pages / "link.html").symlink_to(secret)
    cases = {
        "artifact:local:dotdot": str(pages / ".." / "secret.html"),
        "artifact:local:symlink": str(pages / "link.html"),
        "artifact:local:absolute": "/etc/hosts",
    }
    with store.transaction(conn):
        store.upsert_resources(
            conn,
            [
                store.Resource(rid, "artifact", rid, "artifacts", {"local_file": file})
                for rid, file in cases.items()
            ],
            now=NOW,
        )
    for rid in cases:
        res = client.get("/api/artifacts/preview", params={"id": rid})
        assert res.status_code == 403, rid
        assert "outside" not in res.text


def test_api_preview_unknown_and_non_artifact(api) -> None:
    client, *_ = api
    assert (
        client.get("/api/artifacts/preview", params={"id": "artifact:local:nope"}).status_code
        == 404
    )
    assert client.get("/api/artifacts/preview", params={"id": "service:port:1"}).status_code == 404
