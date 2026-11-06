"""Behavior of the local retrieval index: Chinese BM25, incremental update, explicit failures.

Uses a tiny synthetic vault and a deterministic character-hash embedder, so no model is loaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import TYPE_CHECKING

import numpy as np
import pytest

from kg.retrieval import corpus
from kg.retrieval.errors import EmbeddingError, IndexMissingError, IndexStaleError
from kg.retrieval.index import Index
from kg.retrieval.search import Searcher, _rrf
from kg.retrieval.text import tokenize

if TYPE_CHECKING:
    from pathlib import Path


class FakeEmbedder:
    """Bag of character bigrams hashed into 64 dims: similar text → similar vector."""

    name = "fake-embedder"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(64, dtype="float32")
        for a, b in zip(text, text[1:], strict=False):
            v[int(hashlib.md5((a + b).encode()).hexdigest(), 16) % 64] += 1
        return v / (np.linalg.norm(v) or 1)

    def documents(self, texts: list[str]) -> np.ndarray:
        self.calls += len(texts)
        return np.stack([self._vec(t) for t in texts])

    def query(self, text: str) -> np.ndarray:
        if self.fail:
            raise EmbeddingError("查询向量计算失败：模型没加载")
        return self._vec(text)


def _entry(vault: Path, rel: str, entry_id: str, title: str, body: str, tags=()) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tag_list = ", ".join(tags)
    path.write_text(
        f"---\nid: {entry_id}\ntitle: {title}\ntype: principle\ndomain: [demo]\n"
        f"tags: [{tag_list}]\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("KG_INDEX_DIR", raising=False)
    _entry(tmp_path, "01-Principles/cache.md", "ke-cache", "缓存击穿与互斥重建",
           "# 缓存击穿\n\n热点键过期的瞬间，大量请求同时打到数据库。\n\n"
           "## 解决\n\n用互斥锁只让一个请求重建缓存，其余请求等待或返回旧值。" * 3,
           tags=["redis", "缓存击穿"])
    _entry(tmp_path, "03-Debug/oom.md", "ke-oom", "网关内存溢出排查",
           "# 现象\n\n网关进程被系统杀掉，容器重启。\n\n## 根因\n\n请求体全部读进内存没有上限，"
           "大文件上传时内存翻倍。\n\n## 修复\n\n给请求体加预算，超出直接拒绝。", tags=["oom"])
    _entry(tmp_path, "04-Architecture/raft.md", "ke-raft", "Raft 选主过程",
           "# 选主\n\n跟随者超时后成为候选人，发起投票，拿到多数票成为领导者。", tags=["raft"])
    (tmp_path / "02-Patterns").mkdir()
    (tmp_path / "02-Patterns/no-id.md").write_text("# 没有 frontmatter\n", encoding="utf-8")
    return tmp_path


def _searcher(vault: Path, mode: str = "hybrid", **kw) -> Searcher:
    return Searcher(vault, mode=mode, embedder=kw.pop("embedder", FakeEmbedder()), **kw)


def test_tokenize_splits_chinese_and_drops_fillers() -> None:
    toks = tokenize("为什么网关内存溢出了？")
    assert "网关" in toks and "内存" in toks
    assert "为什么" not in toks and "？" not in toks


def test_sections_respect_fences_and_carry_context(vault: Path) -> None:
    entry = corpus.Entry("x", "a.md", "标题", "principle", [], ["t1"],
                         "# 一\n\n" + "正文" * 150 + "\n\n```\n# 不是标题\n```\n\n## 二\n\n"
                         + "内容" * 150, "sha")
    parts = corpus.chunks(entry)
    assert [p.heading for p in parts] == ["一", "一 / 二"]
    assert "# 不是标题" in parts[0].body
    assert parts[1].text.startswith("标题\n标签：t1\n小节：一 / 二")


def test_bm25_finds_chinese_entry_without_vectors(vault: Path) -> None:
    Index(vault).rebuild(FakeEmbedder())
    hits = _searcher(vault, "bm25").search("网关为什么内存溢出", top_k=3).hits
    assert hits[0].entry_id == "ke-oom"
    assert hits[0].ranks == {"bm25": 1}


def test_hybrid_returns_entries_once_with_both_ranks(vault: Path) -> None:
    Index(vault).rebuild(FakeEmbedder())
    hits = _searcher(vault).search("缓存击穿怎么解决", top_k=5).hits
    ids = [h.entry_id for h in hits]
    assert ids[0] == "ke-cache" and len(ids) == len(set(ids))
    assert set(hits[0].ranks) == {"bm25", "vector"}


def test_rrf_prefers_items_ranked_well_in_both_lists() -> None:
    fused = _rrf({"a": ["x", "y", "z"], "b": ["y", "x", "w"]})
    assert [k for k, _ in fused][:2] in (["x", "y"], ["y", "x"])
    assert fused[-1][0] in ("z", "w")


def test_update_touches_only_changed_files(vault: Path) -> None:
    index = Index(vault)
    index.rebuild(FakeEmbedder())
    emb = FakeEmbedder()
    report = index.update(emb)
    assert (report.added, report.modified, report.deleted, emb.calls) == (0, 0, 0, 0)

    _entry(vault, "03-Debug/oom.md", "ke-oom", "网关内存溢出排查", "改过的正文：限流和背压。")
    (vault / "04-Architecture/raft.md").unlink()
    _entry(vault, "10-Experience/new.md", "xp-new", "新的经验", "夜间整理后新写的条目，讲背压。")
    cache = vault / "01-Principles/cache.md"
    os.utime(cache, (time.time() + 5, time.time() + 5))  # mtime only, same bytes

    report = index.update(emb)
    assert (report.added, report.modified, report.deleted, report.touched) == (1, 1, 1, 1)
    assert index.pending().count == 0
    ids = [h.entry_id for h in _searcher(vault, "bm25").search("背压", top_k=5).hits]
    assert set(ids) == {"ke-oom", "xp-new"}
    rows = index.table().to_arrow().column("entry_id").to_pylist()
    assert "ke-raft" not in rows
    status = json.loads(index.status_path.read_text(encoding="utf-8"))
    assert status["ok"] is True and status["last_update"]["modified"] == 1


def test_missing_index_raises_instead_of_empty(vault: Path) -> None:
    with pytest.raises(IndexMissingError) as err:
        _searcher(vault)
    assert err.value.to_dict()["code"] == "index_missing"
    with pytest.raises(IndexMissingError):
        Index(vault).update(FakeEmbedder())


def test_query_embedding_failure_raises(vault: Path) -> None:
    Index(vault).rebuild(FakeEmbedder())
    searcher = _searcher(vault, embedder=FakeEmbedder(fail=True))
    with pytest.raises(EmbeddingError):
        searcher.search("缓存")


def test_old_unindexed_change_is_stale_recent_one_is_a_warning(vault: Path) -> None:
    Index(vault).rebuild(FakeEmbedder())
    new = _entry(vault, "10-Experience/late.md", "xp-late", "迟到的条目", "还没进索引。")
    result = _searcher(vault, "bm25").search("条目")
    assert result.warnings and "新增 1" in result.warnings[0]

    old = time.time() - 3 * 86400
    os.utime(new, (old, old))
    with pytest.raises(IndexStaleError):
        _searcher(vault, "bm25").search("条目")
    assert _searcher(vault, "bm25", strict_fresh=False).search("条目").warnings


def test_failed_build_records_status_and_raises(vault: Path) -> None:
    index = Index(vault)

    class Broken(FakeEmbedder):
        def documents(self, texts: list[str]) -> np.ndarray:
            raise EmbeddingError("条目向量计算失败：显存不足")

    with pytest.raises(EmbeddingError):
        index.rebuild(Broken())
    status = index.status()
    assert status["ok"] is False and status["error"]["code"] == "embedding_failed"
    assert "last_success" not in status


def test_model_mismatch_is_explicit(vault: Path) -> None:
    Index(vault).rebuild(FakeEmbedder())

    class Other(FakeEmbedder):
        name = "other-model"

    with pytest.raises(EmbeddingError):
        Searcher(vault, embedder=Other())
