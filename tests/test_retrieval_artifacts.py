"""Artifact pages in the local index (#6 → #7): incremental sync, kinds, masking, fallbacks.

A synthetic vault (three entries) and a synthetic state store (artifact resources pointing at
local HTML pages in tmp_path); the character-hash embedder from the index tests, no model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from kg.plane import store
from kg.plane.store import Resource
from kg.retrieval.errors import IndexMissingError, RetrievalError
from kg.retrieval.index import Index
from kg.retrieval.search import Searcher
from tests.test_retrieval_index import FakeEmbedder, _entry

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000
FAKE_KEY = "sk-" + "Qw7" * 8  # matches the credential pattern; not a real key
MIGRATION = """<html><head><title>迁移方案页</title><style>.a{}</style></head><body>
<h1>对象存储迁移</h1><p>先双写两周，再切读流量，最后下线旧桶。</p>
<p>回滚：切回旧桶的读路径，双写不停。</p></body></html>"""
LEAKY = f"""<html><head><title>部署记录页</title></head><body>
<p>数据库在 10.20.30.40，备机 db01.intra.test，密钥 {FAKE_KEY}。</p>
<p>灰度发布按机房分批推进。</p></body></html>"""


def _page(tmp: Path, name: str, html: str) -> Path:
    path = tmp / "pages" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _artifact(rid: str, file: Path, title: str, **attrs) -> Resource:
    return Resource(rid, "artifact", title, "artifacts",
                    {"title": title, "adapter": rid.split(":")[1], "local_file": str(file),
                     **attrs})


def _store(vault: Path, resources: list[Resource], gone: list[str] = ()) -> None:
    conn = store.connect(store.db_path(vault))
    with store.transaction(conn):
        store.upsert_resources(conn, resources, now=NOW)
        for rid in gone:
            conn.execute("UPDATE resource SET gone_at = ? WHERE id = ?", (NOW, rid))
    conn.close()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("KG_INDEX_DIR", raising=False)
    root = tmp_path / "vault"
    _entry(root, "01-Principles/cache.md", "ke-cache", "缓存击穿与互斥重建",
           "热点键过期的瞬间，大量请求同时打到数据库。用互斥锁只让一个请求重建缓存。")
    _entry(root, "03-Debug/oom.md", "ke-oom", "网关内存溢出排查",
           "请求体全部读进内存没有上限，大文件上传时内存翻倍。")
    _entry(root, "04-Architecture/raft.md", "ke-raft", "Raft 选主过程",
           "跟随者超时后成为候选人，发起投票，拿到多数票成为领导者。")
    return root


@pytest.fixture
def with_artifacts(vault: Path, tmp_path: Path) -> dict[str, Path]:
    pages = {"mig": _page(tmp_path, "mig.html", MIGRATION),
             "leak": _page(tmp_path, "leak.html", LEAKY)}
    (vault / ".kg" / "plane").mkdir(parents=True, exist_ok=True)
    (vault / ".kg" / "plane" / "artifacts.yaml").write_text(
        "local: []\nsensitive:\n  internal_domains: [intra.test]\n", encoding="utf-8")
    _store(vault, [
        _artifact("artifact:local:mig", pages["mig"], "迁移方案页", project="demo",
                  url="https://share.example.test/a/mig"),
        _artifact("artifact:orca:leak", pages["leak"], "部署记录页",
                  url="https://share.example.test/a/leak"),
        Resource("artifact:claude:remote", "artifact", "没有本地文件", "artifacts",
                 {"title": "没有本地文件"}),
    ])
    return pages


def _search(vault: Path, query: str, *, mode: str = "bm25", kind: str | None = None,
            kinds: tuple[str, ...] | None = None, top_k: int = 5):
    searcher = Searcher(vault, mode=mode, embedder=FakeEmbedder(), kinds=kinds)
    return searcher.search(query, top_k=top_k, filters={"kind": kind} if kind else None).hits


def _art_ids(index: Index) -> list[str]:
    table = index.artifact_table()
    return sorted(set(table.to_arrow().column("entry_id").to_pylist())) if table else []


def test_no_store_or_no_artifact_resources_changes_nothing(vault: Path) -> None:
    index = Index(vault)
    report = index.rebuild(FakeEmbedder())
    assert index.artifact_table() is None and report.artifacts == 0 and not report.errors
    assert index.manifest()["artifacts"] == {}

    _store(vault, [Resource("service:launchd:demo", "service", "demo", "services", {})])
    report = index.update(FakeEmbedder())
    assert index.artifact_table() is None and not report.errors
    with pytest.raises(IndexMissingError):
        _search(vault, "迁移", kind="artifact")
    assert [h.kind for h in _search(vault, "网关内存")] == ["entry"]


def test_artifacts_are_indexed_incrementally_by_id_and_hash(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    index = Index(vault)
    report = index.rebuild(FakeEmbedder())
    assert (report.artifacts_added, report.artifacts) == (2, 2)  # the remote one has no text
    assert _art_ids(index) == ["artifact:local:mig", "artifact:orca:leak"]
    hashes = dict(index.manifest()["artifacts"])

    emb = FakeEmbedder()
    report = index.update(emb)  # nothing changed: nothing embedded
    assert (report.artifacts_added, report.artifacts_modified, report.artifacts_deleted,
            emb.calls) == (0, 0, 0, 0)

    with_artifacts["mig"].write_text(MIGRATION.replace("双写两周", "双写三周"), encoding="utf-8")
    report = index.update(emb)
    assert (report.artifacts_added, report.artifacts_modified, report.artifacts_deleted) == (
        0, 1, 0)
    assert emb.calls == 1  # one short page → one piece re-embedded, the other left alone
    new = index.manifest()["artifacts"]
    assert new["artifact:orca:leak"] == hashes["artifact:orca:leak"]
    assert new["artifact:local:mig"]["sha"] != hashes["artifact:local:mig"]["sha"]
    rows = index.artifact_table().to_arrow().column("entry_id").to_pylist()
    assert rows.count("artifact:local:mig") == 1  # replaced, not duplicated
    assert "三周" in _search(vault, "双写", kind="artifact")[0].snippet


def test_artifacts_gone_from_the_export_are_deleted(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    index = Index(vault)
    index.rebuild(FakeEmbedder())
    _store(vault, [], gone=["artifact:orca:leak"])
    report = index.update(FakeEmbedder())
    assert (report.artifacts_deleted, report.artifacts) == (1, 1)
    assert _art_ids(index) == ["artifact:local:mig"]
    assert "artifact:orca:leak" not in index.manifest()["artifacts"]
    assert not _search(vault, "灰度发布", kind="artifact")

    with_artifacts["mig"].unlink()  # the local file went away: its document goes too
    index.update(FakeEmbedder())
    assert _art_ids(index) == []
    with pytest.raises(IndexMissingError):  # an empty table counts as "no artifacts"
        _search(vault, "迁移", kind="artifact")


def test_hits_say_their_kind_and_carry_link_and_file(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    Index(vault).rebuild(FakeEmbedder())
    for mode in ("bm25", "hybrid"):
        hits = _search(vault, "对象存储迁移 回滚", mode=mode, kinds=("entry", "artifact"))
        top = hits[0]
        assert (top.entry_id, top.kind) == ("artifact:local:mig", "artifact")
        assert top.url == "https://share.example.test/a/mig"
        assert top.path == str(with_artifacts["mig"]) and top.project == "demo"
        data = top.to_dict()
        assert data["kind"] == "artifact" and data["local_file"] == str(with_artifacts["mig"])
        assert data["url"] == top.url
    entry = _search(vault, "网关内存溢出", kinds=("entry", "artifact"))[0]
    assert entry.kind == "entry" and entry.to_dict()["kind"] == "entry"
    assert "url" not in entry.to_dict()


def test_body_text_is_searchable_not_only_the_title(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    Index(vault).rebuild(FakeEmbedder())
    hits = _search(vault, "下线旧桶", kind="artifact")  # only in a paragraph, not in the title
    assert [h.entry_id for h in hits][:1] == ["artifact:local:mig"]
    assert "下线旧桶" in hits[0].snippet


def test_default_kinds_keep_artifacts_out_of_entry_search(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    Index(vault).rebuild(FakeEmbedder())
    plain = _search(vault, "迁移 双写 回滚 数据库", mode="hybrid", kinds=("entry",), top_k=10)
    assert {h.kind for h in plain} == {"entry"}
    assert _search(vault, "迁移 双写", kind="all")[0].kind == "artifact"
    with pytest.raises(RetrievalError):
        _search(vault, "迁移", kind="nonsense")


def test_sensitive_page_is_masked_in_the_index_and_flagged(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    index = Index(vault)
    index.rebuild(FakeEmbedder())
    table = index.artifact_table().to_arrow()
    blob = " ".join(table.column("text").to_pylist() + table.column("body").to_pylist()
                    + table.column("tokens").to_pylist())
    assert "灰度发布" in blob
    for secret in ("10.20.30.40", "db01.intra.test", FAKE_KEY):
        assert secret not in blob and secret.lower() not in blob
    hit = _search(vault, "灰度发布 机房", kind="artifact")[0]
    assert hit.entry_id == "artifact:orca:leak" and hit.sensitive is True
    assert "10.20.30.40" not in hit.snippet
    searcher = Searcher(vault, mode="bm25", embedder=FakeEmbedder())
    assert FAKE_KEY not in searcher.artifact_text("artifact:orca:leak")
    assert "灰度发布" in searcher.artifact_text("artifact:orca:leak")


def test_long_page_is_cut_into_pieces_and_collapses_to_one_hit(
        vault: Path, tmp_path: Path) -> None:
    filler = "".join(f"<p>第{i}段：常规的流水记录，没有特别的内容。</p>" for i in range(400))
    page = _page(tmp_path, "long.html", f"<title>长页</title><body>{filler}"
                 "<p>末尾写着冷备切换演练的结论。</p></body>")
    _store(vault, [_artifact("artifact:local:long", page, "长页")])
    report = Index(vault).rebuild(FakeEmbedder())
    assert report.artifacts == 1 and report.chunks_written > 4
    hits = _search(vault, "冷备切换演练", kind="artifact")
    assert [h.entry_id for h in hits] == ["artifact:local:long"]


def test_broken_store_is_reported_and_entries_still_update(vault: Path) -> None:
    path = store.db_path(vault)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a sqlite file at all" * 10)
    report = Index(vault).rebuild(FakeEmbedder())
    assert report.entries == 3 and any(e.startswith("工件") for e in report.errors)


def test_service_search_gives_ask_the_masked_text(
        vault: Path, with_artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from kg.retrieval import service

    Index(vault).rebuild(FakeEmbedder())
    searcher = Searcher(vault, mode="bm25", embedder=FakeEmbedder())
    monkeypatch.setattr(service, "get_searcher", lambda *a, **k: searcher)
    results = service.search("灰度发布 机房", top_k=3, filters={"kind": "all"})
    top = results[0]
    assert top["kind"] == "artifact" and top["file_path"] == str(with_artifacts["leak"])
    assert "灰度发布" in top["content"] and FAKE_KEY not in top["content"]


# ── callers: MCP, kg ask, web search ──


def _plane(vault: Path, searcher: Searcher | None = None):
    from kg.mcp import tools

    def run(query, top_k, filters, rerank):
        if searcher is None:
            raise IndexMissingError("检索索引还没建")
        return searcher.search(query, top_k=top_k, filters=filters)

    return tools.Plane(vault=vault, db=store.db_path(vault), search_fn=run, clock=lambda: NOW)


def test_find_artifact_uses_the_index_and_falls_back_to_words(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    from kg.mcp import tools

    Index(vault).rebuild(FakeEmbedder())
    searcher = Searcher(vault, mode="hybrid", embedder=FakeEmbedder())
    data = tools.find_artifact(_plane(vault, searcher), "下线旧桶")
    assert data["via"] == "index"
    assert [r["id"] for r in data["results"]] == ["artifact:local:mig"]
    assert data["results"][0]["url"] == "https://share.example.test/a/mig"
    assert data["results"][0]["local_file"] == str(with_artifacts["mig"])

    data = tools.find_artifact(_plane(vault, None), "下线旧桶")  # index unavailable
    assert data["via"] == "words" and data["results"][0]["id"] == "artifact:local:mig"

    leak = tools.find_artifact(_plane(vault, searcher), "灰度发布")["results"][0]
    assert leak["id"] == "artifact:orca:leak" and "10.20.30.40" not in leak["snippet"]


def test_find_artifact_ignores_hits_that_are_only_the_nearest_vector(
        vault: Path, with_artifacts: dict[str, Path]) -> None:
    from kg.mcp import tools

    Index(vault).rebuild(FakeEmbedder())
    searcher = Searcher(vault, mode="hybrid", embedder=FakeEmbedder())
    data = tools.find_artifact(_plane(vault, searcher), "量子纠缠")
    assert data["results"] == [] and data["via"] == "words"


def test_search_knowledge_artifact_board(vault: Path, with_artifacts: dict[str, Path]) -> None:
    from kg.mcp import tools

    Index(vault).rebuild(FakeEmbedder())
    plane = _plane(vault, Searcher(vault, mode="hybrid", embedder=FakeEmbedder()))
    data = tools.search_knowledge(plane, "对象存储迁移", board="工件")
    top = data["results"][0]
    assert (top["id"], top["kind"], top["board"]) == ("artifact:local:mig", "artifact", "artifact")
    assert top["url"] and top["local_file"] == str(with_artifacts["mig"])
    plain = tools.search_knowledge(plane, "对象存储迁移")
    assert all(not r["id"].startswith("artifact:") for r in plain["results"])


def test_ask_adds_matching_artifacts_after_the_entries(
        vault: Path, with_artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from agents import ask
    from kg.retrieval import search, service

    Index(vault).rebuild(FakeEmbedder())
    searcher = Searcher(vault, mode="hybrid", embedder=FakeEmbedder())
    monkeypatch.setattr(service, "get_searcher", lambda *a, **k: searcher)
    # The bigram embedder's cosines run lower than bge-m3's; the gate itself is tested below.
    monkeypatch.setattr(search, "ARTIFACT_WORD_SIM", 0.2)
    entries = [{"id": "ke-oom", "title": "网关内存溢出排查", "kind": "entry"}]
    got = ask._with_artifacts("灰度发布按机房推进", entries)
    assert got[0] is entries[0] and got[1]["id"] == "artifact:orca:leak"
    context = ask._build_context(got[1:], config=None, use_graph=False)
    assert "工件: artifact:orca:leak" in context and "灰度发布" in context
    assert FAKE_KEY not in context and "10.20.30.40" not in context
    assert ask._link("https://u:pw@share.example.test/a/x?token=abc#f") == (
        "https://share.example.test/a/x")

    monkeypatch.setenv("KG_ASK_ARTIFACTS", "0")
    assert ask._with_artifacts("灰度发布按机房推进", entries) == entries


def test_ask_without_artifact_index_keeps_entry_hits(
        vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agents import ask
    from kg.retrieval import service

    Index(vault).rebuild(FakeEmbedder())
    searcher = Searcher(vault, mode="hybrid", embedder=FakeEmbedder())
    monkeypatch.setattr(service, "get_searcher", lambda *a, **k: searcher)
    entries = [{"id": "ke-oom"}]
    assert ask._with_artifacts("迁移", entries) == entries


def test_typeahead_returns_artifacts_only_when_asked(
        vault: Path, with_artifacts: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agents.api_routes import search as route
    from kg.retrieval import service

    Index(vault).rebuild(FakeEmbedder())
    searcher = Searcher(vault, mode="bm25", embedder=FakeEmbedder())
    monkeypatch.setattr(service, "get_searcher", lambda *a, **k: searcher)
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    client = TestClient(app)
    plain = client.get("/api/typeahead", params={"q": "灰度发布 机房"}).json()
    assert all(r["kind"] == "entry" for r in plain["results"])
    arts = client.get("/api/typeahead", params={"q": "灰度发布 机房", "kind": "artifact"}).json()
    top = arts["results"][0]
    assert (top["id"], top["kind"], top["sensitive"]) == ("artifact:orca:leak", "artifact", True)
    assert top["url"] == "https://share.example.test/a/leak"
    assert "10.20.30.40" not in top["snippet"] and FAKE_KEY not in str(arts)
    bad = client.get("/api/typeahead", params={"q": "迁移", "kind": "nonsense"}).json()
    assert bad["results"] == [] and bad["error"]["code"] == "retrieval_failed"


def test_relevance_gate_needs_a_word_and_closeness_or_a_close_vector() -> None:
    from kg.retrieval.search import ARTIFACT_MIN_SIM, ARTIFACT_WORD_SIM, Hit, relevant

    def hit(ranks, sim):
        return Hit("artifact:local:x", "t", "/p", "", "", "artifact", [], 0.1, ranks,
                   kind="artifact", similarity=sim)

    assert relevant(hit({"bm25": 3, "vector": 9}, ARTIFACT_WORD_SIM + 0.01))
    assert not relevant(hit({"bm25": 1}, ARTIFACT_WORD_SIM - 0.05))  # only a common word
    assert not relevant(hit({"bm25": 1}, None))
    assert relevant(hit({"vector": 1}, ARTIFACT_MIN_SIM + 0.01))
    assert not relevant(hit({"vector": 1}, ARTIFACT_MIN_SIM - 0.05))  # only the nearest one
