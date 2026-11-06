"""Search over the local index: jieba BM25 and vector lists, fused by RRF, optionally reranked.

Chunks are ranked per list, each list is collapsed to entries (an entry ranks where its best
chunk ranks), the entry lists are fused with reciprocal rank fusion, and the top of the fused
list can be re-scored by a local cross-encoder on (query, best chunk).

Two kinds of document are searched: vault entries (table `chunks`) and artifact pages (table
`artifacts`, #6). `kinds` picks which; a filter `{"kind": "artifact"}` overrides it for one call.
Each list queries the chosen tables and merges them by score before collapsing, so a kind that is
not asked for never takes a place in the pool.

Failures raise `RetrievalError` subclasses; nothing here returns an empty list to hide one.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kg.retrieval.errors import (
    EmbeddingError,
    IndexMissingError,
    IndexStaleError,
    RetrievalError,
)
from kg.retrieval.index import Index
from kg.retrieval.models import Embedder, Reranker
from kg.retrieval.text import add_terms, tokenize

if TYPE_CHECKING:
    from pathlib import Path

MODES = ("bm25", "vector", "hybrid")
RRF_K = 60
POOL = 50  # chunks taken from each list before collapsing to entries
RERANK_POOL = 30  # fused entries re-scored by the cross-encoder
STALE_AFTER = 36 * 3600  # an unindexed change older than this means the nightly update is broken
KINDS = ("entry", "artifact")
# A vector list always returns its nearest neighbours, and BM25 matches common words like 做法,
# so neither alone says "this artifact is about the question". Measured on the real index
# (19 pages, bge-m3): pages found by a distinctive title word had cosine 0.47–0.61; unrelated
# questions (cooking, poetry, running) topped out at 0.45 with or without a word match.
ARTIFACT_WORD_SIM = float(os.environ.get("KG_ARTIFACT_WORD_SIM", "0.42"))  # with a BM25 match
ARTIFACT_MIN_SIM = float(os.environ.get("KG_ARTIFACT_MIN_SIM", "0.55"))  # vector alone
ARTIFACT_VECTOR_POOL = 400  # artifact pieces are few: score them all, so each hit has a cosine
# What a search returns when the caller does not say (KG_SEARCH_KINDS=entry,artifact to change).
DEFAULT_KINDS = tuple(k for k in os.environ.get("KG_SEARCH_KINDS", "entry").split(",")
                      if k in KINDS) or ("entry",)


@dataclass
class Hit:
    entry_id: str
    title: str
    path: str
    heading: str
    snippet: str
    type: str
    domain: list[str]
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # list name → 1-based rank
    kind: str = "entry"  # entry | artifact
    url: str | None = None  # artifact: its share link
    project: str | None = None
    sensitive: bool = False  # artifact: the page had hits; snippet and text are masked
    similarity: float | None = None  # cosine of the best chunk, when the vector list had it

    def to_dict(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if self.kind != "entry":
            extra = {"url": self.url, "local_file": self.path, "project": self.project,
                     "sensitive": self.sensitive}
        return {
            "id": self.entry_id,
            "title": self.title,
            "file_path": self.path,
            "heading": self.heading,
            "snippet": self.snippet,
            "type": self.type,
            "domain": ", ".join(self.domain),
            "score": round(self.score, 4),
            "ranks": self.ranks,
            "kind": self.kind,
            **extra,
        }


def relevant(hit: Hit) -> bool:
    """A hit that is about the query, not just the nearest one (needs the vector list)."""
    sim = hit.similarity
    if sim is None:
        return False
    return sim >= (ARTIFACT_WORD_SIM if "bm25" in hit.ranks else ARTIFACT_MIN_SIM)


@dataclass
class Result:
    hits: list[Hit]
    warnings: list[str]
    timings: dict[str, float]


def _rrf(lists: dict[str, list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in lists.values():
        for i, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + i + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


class Searcher:
    """Opens the index once; call `search()` many times. Thread-safe for reads."""

    def __init__(
        self,
        vault: Path,
        *,
        mode: str = "hybrid",
        rerank: bool = False,
        bm25: str = "lance",
        root: Path | None = None,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        strict_fresh: bool = True,
        kinds: tuple[str, ...] | None = None,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.vault = vault
        self.mode = mode
        self.rerank = rerank
        self.bm25_backend = bm25
        self.index = Index(vault, root)
        self.strict_fresh = strict_fresh
        self.kinds = tuple(kinds or DEFAULT_KINDS)
        if not set(self.kinds) <= set(KINDS):
            raise ValueError(f"kinds must be among {KINDS}")
        manifest = self.index.manifest()
        if manifest is None:
            raise IndexMissingError("检索索引还没建", hint="运行 kg index rebuild")
        self.manifest = manifest
        self.embedder = embedder or Embedder(manifest["model"])
        if self.embedder.name != manifest["model"]:
            raise EmbeddingError(
                f"索引用 {manifest['model']} 建的，查询用的是 {self.embedder.name}",
                hint="换回同一个模型，或 kg index rebuild",
            )
        self.reranker = reranker or (Reranker() if rerank else None)
        self.table = self.index.table()
        cols = ["chunk_id", "entry_id", "path", "title", "heading", "type", "domain", "body",
                "tokens"]
        data = self.table.to_arrow().select(cols).to_pylist()
        self.rows = {r["chunk_id"]: r | {"kind": "entry"} for r in data}
        self.art_table = self.index.artifact_table()
        if self.art_table is not None and not self.art_table.count_rows():
            self.art_table = None
        if self.art_table is not None:
            acols = ["chunk_id", "entry_id", "path", "title", "heading", "type", "body", "url",
                     "project", "sensitive"]
            for r in self.art_table.to_arrow().select(acols).to_pylist():
                self.rows[r["chunk_id"]] = r | {"kind": "artifact", "domain": []}
        add_terms(manifest.get("terms", []))
        self._bm25: Any = None
        if bm25 == "bm25s":
            import bm25s

            self._bm25_ids = [r["chunk_id"] for r in data]
            self._bm25 = bm25s.BM25()
            self._bm25.index([r["tokens"].split() for r in data], show_progress=False)

    # ── freshness ──────────────────────────────────────────────────────

    def freshness(self) -> list[str]:
        """Warnings for changes not yet indexed; raise if they have waited too long."""
        pend = self.index.pending()
        if not pend.count:
            return []
        now = time.time()
        oldest = min(
            ((self.vault / p).stat().st_mtime for p in pend.added + pend.modified),
            default=now,
        )
        msg = (
            f"索引落后：新增 {len(pend.added)}、修改 {len(pend.modified)}、"
            f"删除 {len(pend.deleted)} 个文件还没进索引"
        )
        if self.strict_fresh and now - oldest > STALE_AFTER:
            since = datetime.fromtimestamp(oldest).astimezone().isoformat(timespec="minutes")
            raise IndexStaleError(f"{msg}，最早一个改于 {since}", hint="运行 kg index update")
        return [msg + "（运行 kg index update）"]

    # ── lists ──────────────────────────────────────────────────────────

    def _tables(self, kinds: tuple[str, ...]) -> list[Any]:
        tables = [self.table] if "entry" in kinds else []
        if "artifact" in kinds and self.art_table is not None:
            tables.append(self.art_table)
        return tables

    def _bm25_list(self, query: str, n: int,
                   kinds: tuple[str, ...] = ("entry",)) -> list[tuple[str, float]]:
        """(chunk id, BM25 score), best first, over the tables of `kinds`."""
        toks = tokenize(query)
        if not toks:
            return []
        if self._bm25 is not None and kinds == ("entry",):
            import numpy as np

            q = [t for t in toks if t in self._bm25.vocab_dict] if hasattr(
                self._bm25, "vocab_dict") else toks
            if not q:
                return []
            docs, scores = self._bm25.retrieve([q], k=min(n, len(self._bm25_ids)),
                                               show_progress=False)
            return [(self._bm25_ids[int(d)], float(np.asarray(s)))
                    for d, s in zip(docs[0], scores[0], strict=True) if float(np.asarray(s)) > 0]
        from lancedb.query import MatchQuery

        found: list[tuple[str, float]] = []
        for table in self._tables(kinds):
            res = (
                table.search(MatchQuery(" ".join(toks), "tokens"), query_type="fts")
                .select(["chunk_id", "_score"]).limit(n).to_list()
            )
            found += [(r["chunk_id"], float(r["_score"])) for r in res]
        return sorted(found, key=lambda x: -x[1])[:n] if len(kinds) > 1 else found

    def _vector_list(self, query: str, n: int,
                     kinds: tuple[str, ...] = ("entry",)) -> list[tuple[str, float]]:
        """(chunk id, cosine similarity), best first, over the tables of `kinds`."""
        tables = self._tables(kinds)
        if not tables:
            return []
        vec = self.embedder.query(query)
        found: list[tuple[str, float]] = []
        for table in tables:
            limit = n if table is self.table else max(n, ARTIFACT_VECTOR_POOL)
            res = (
                table.search(vec, vector_column_name="vector").metric("cosine")
                .select(["chunk_id", "_distance"]).limit(limit).to_list()
            )
            found += [(r["chunk_id"], 1.0 - float(r["_distance"])) for r in res]
        return sorted(found, key=lambda x: -x[1])[:n] if len(tables) > 1 else found

    def _collapse(self, chunk_ids: list[str]) -> tuple[list[str], dict[str, str]]:
        """Entry ranking from a chunk ranking, plus each entry's best chunk."""
        order: list[str] = []
        best: dict[str, str] = {}
        for cid in chunk_ids:
            row = self.rows.get(cid)
            if row is None:
                continue
            eid = row["entry_id"]
            if eid not in best:
                best[eid] = cid
                order.append(eid)
        return order, best

    # ── public ─────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 10,
               filters: dict[str, str] | None = None) -> Result:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        warnings = self.freshness()
        timings["fresh_ms"] = (time.perf_counter() - t0) * 1000

        filters = dict(filters or {})
        kinds = self._kinds(filters.pop("kind", None))
        if kinds == ("artifact",) and self.art_table is None:
            raise IndexMissingError("索引里还没有工件",
                                    hint="先运行 kg plane collect artifacts，再 kg index update")
        lists: dict[str, list[str]] = {}
        best: dict[str, str] = {}
        sims: dict[str, float] = {}
        for name in (["bm25", "vector"] if self.mode == "hybrid" else [self.mode]):
            t = time.perf_counter()
            scored = self._bm25_list(query, POOL, kinds) if name == "bm25" else self._vector_list(
                query, POOL, kinds)
            timings[f"{name}_ms"] = (time.perf_counter() - t) * 1000
            if name == "vector":
                for cid, sim in scored:
                    sims.setdefault(cid, sim)
            order, top = self._collapse([cid for cid, _ in scored])
            lists[name] = order
            for eid, cid in top.items():
                best.setdefault(eid, cid)

        fused = _rrf(lists) if len(lists) > 1 else [
            (eid, 1.0 / (RRF_K + i + 1)) for i, eid in enumerate(next(iter(lists.values())))
        ]
        if filters:
            fused = [(e, s) for e, s in fused if self._match(self.rows[best[e]], filters)]
        deleted = set(self.index.pending().deleted) if warnings else set()
        fused = [(e, s) for e, s in fused if self.rows[best[e]]["path"] not in deleted]

        if self.reranker is not None and fused:
            t = time.perf_counter()
            pool = fused[:RERANK_POOL]
            passages = [self._passage(best[e]) for e, _ in pool]
            scores = self.reranker.scores(query, passages)
            fused = sorted(zip([e for e, _ in pool], scores, strict=True), key=lambda x: -x[1])
            timings["rerank_ms"] = (time.perf_counter() - t) * 1000

        hits = []
        for eid, s in fused[:top_k]:
            row = self.rows[best[eid]]
            hits.append(Hit(
                entry_id=eid, title=row["title"], path=row["path"], heading=row["heading"],
                snippet=row["body"][:240], type=row["type"], domain=list(row["domain"] or []),
                score=float(s),
                ranks={n: lst.index(eid) + 1 for n, lst in lists.items() if eid in lst},
                kind=row["kind"], url=row.get("url") or None, project=row.get("project") or None,
                sensitive=bool(row.get("sensitive")),
                similarity=round(sims[best[eid]], 4) if best[eid] in sims else None,
            ))
        timings["total_ms"] = (time.perf_counter() - t0) * 1000
        return Result(hits, warnings, {k: round(v, 1) for k, v in timings.items()})

    def _kinds(self, want: str | None) -> tuple[str, ...]:
        if not want:
            return self.kinds
        if want in ("all", "*"):
            return KINDS
        picked = tuple(k for k in KINDS if k in want.split(","))
        if not picked:
            raise RetrievalError(f"未知的文档种类：{want}", hint="可选 entry、artifact 或 all")
        return picked

    def artifact_text(self, artifact_id: str, limit: int = 6000) -> str:
        """The indexed (masked) text of one artifact, its pieces in order."""
        parts = sorted(
            (r for r in self.rows.values() if r["kind"] == "artifact" and
             r["entry_id"] == artifact_id),
            key=lambda r: int(r["chunk_id"].rsplit("#", 1)[1]))
        return "\n\n".join(r["body"] for r in parts)[:limit]

    def _passage(self, chunk_id: str) -> str:
        row = self.rows[chunk_id]
        head = row["title"] + (f"\n{row['heading']}" if row["heading"] else "")
        return f"{head}\n{row['body']}"[:1500]

    @staticmethod
    def _match(row: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, want in filters.items():
            if not want:
                continue
            if key == "domain":
                if want.lower() not in [str(d).lower() for d in row["domain"] or []]:
                    return False
            elif key == "type" and str(row["type"]).lower() != want.lower():
                return False
        return True

