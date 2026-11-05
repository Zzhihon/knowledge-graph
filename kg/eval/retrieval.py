"""Mine candidate questions from sessions, and score retrievers against a labelled set.

The labelled set lives in `<vault>/.kg/eval/retrieval.jsonl` (personal, not in git), one case
per line: {"q": "...", "expected": ["ke-...", "xp-..."], "source": "claude:7f3a9c1e 2026-10-24"}
`expected` lists the entry ids that answer the question; an empty list marks a question the
knowledge base should not answer (it is skipped by recall but kept for later abstention checks).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kg.capture.scan import TZ, CaptureConfig, project_for
from kg.capture.sessions import SessionInfo, iter_blocks

if TYPE_CHECKING:
    from pathlib import Path

Retriever = Callable[[str, int], list[str]]

# A user turn worth asking the knowledge base: a real question, not an instruction or a reply.
_QUESTION = re.compile(
    r"[？?]|怎么|为什么|为何|如何|是什么|什么是|区别|原理|能不能|有没有|是否|哪些|哪个|多少"
)
# Anything that looks like a credential or a raw link is never kept as a question.
_SECRETISH = re.compile(
    r"sk-[A-Za-z0-9_-]{12,}|ark-[0-9a-f-]{16,}|AKIA[0-9A-Z]{12,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[abp]-|eyJ[A-Za-z0-9_-]{16,}|\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/_-]{40,}\b|https?://|"
    r"(?i:api[_ -]?key|secret|token|password|密钥)\s*[:：=]"
)
_NOT_QUESTION = re.compile(
    r"^(继续|好的|可以|执行|开始|嗯|ok|yes|no|对|是的|不用|行|帮我|请帮|你帮|给我|把|改|修|跑|运行|提交|commit|/)",
    re.IGNORECASE,
)


@dataclass
class Candidate:
    q: str
    source: str
    project: str


@dataclass
class Case:
    q: str
    expected: list[str]
    source: str = ""
    note: str = ""


@dataclass
class Score:
    retriever: str
    cases: int
    hit1: float
    recall5: float
    recall10: float
    mrr10: float
    errors: int
    seconds: float
    misses: list[dict[str, Any]] = field(default_factory=list)


def eval_dir(vault: Path) -> Path:
    path = vault / ".kg" / "eval"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── mining ──────────────────────────────────────────────────────────────


def _questions(text: str) -> Iterator[str]:
    """Question-like sentences of one user turn, pasted material removed."""
    text = re.sub(r"〔粘贴[^〕]*〕", " ", text)
    for part in re.split(r"(?<=[？?。！!])\s*|\n+", text):
        part = part.strip(" \t-*>#")
        if _SECRETISH.search(part) or part.startswith(("[", "{")):
            continue
        if 6 <= len(part) <= 160 and _QUESTION.search(part) and not _NOT_QUESTION.match(part):
            yield part


def mine(
    config: CaptureConfig, *, since: datetime | None = None, limit: int = 400
) -> list[Candidate]:
    """Question-like user turns from allowed sessions, newest first, near-duplicates dropped."""
    files: list[tuple[float, str, Path]] = []
    floor = since.timestamp() if since else 0.0
    for source, folder in config.sources.items():
        if not folder.is_dir():
            continue
        for file in folder.rglob("*.jsonl"):
            if "subagents" in file.parts:
                continue
            mtime = file.stat().st_mtime
            if mtime >= floor:
                files.append((mtime, source, file))
    files.sort(reverse=True)

    seen: set[str] = set()
    found: list[Candidate] = []
    for mtime, source, file in files:
        info = SessionInfo(source)
        data = file.read_bytes()
        day = datetime.fromtimestamp(mtime, TZ).date().isoformat()
        for block in iter_blocks(source, data, 0, info):
            if info.skip:
                break
            if block.role != "user":
                continue
            project = project_for(info.cwd, config) if info.cwd else None
            if not project:
                continue
            for q in _questions(block.text):
                key = re.sub(r"\W+", "", q.lower())[:40]
                if key in seen:
                    continue
                seen.add(key)
                found.append(Candidate(q, f"{source}:{info.session_id[:8]} {day}", project))
                if len(found) >= limit:
                    return found
    return found


# ── scoring ─────────────────────────────────────────────────────────────


def load_cases(path: Path) -> list[Case]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            cases.append(
                Case(
                    raw["q"],
                    list(raw.get("expected", [])),
                    raw.get("source", ""),
                    raw.get("note", ""),
                )
            )
    return cases


def score(name: str, retriever: Retriever, cases: list[Case], k: int = 10) -> Score:
    """hit@1, recall@5, recall@10 and MRR@10 over cases that have expected entries."""
    labelled = [c for c in cases if c.expected]
    hit1 = r5 = r10 = mrr = 0.0
    errors = 0
    misses: list[dict[str, Any]] = []
    started = time.monotonic()
    for case in labelled:
        try:
            got = retriever(case.q, k)
        except Exception as exc:  # a broken retriever scores zero, it does not stop the run
            errors += 1
            got = []
            misses.append({"q": case.q, "error": str(exc)[:200]})
        want = set(case.expected)
        ranks = [i for i, eid in enumerate(got[:k]) if eid in want]
        hit1 += 1.0 if ranks and ranks[0] == 0 else 0.0
        r5 += len({got[i] for i in ranks if i < 5}) / len(want)
        r10 += len({got[i] for i in ranks}) / len(want)
        mrr += 1.0 / (ranks[0] + 1) if ranks else 0.0
        if not ranks and not (misses and misses[-1].get("q") == case.q):
            misses.append({"q": case.q, "expected": case.expected, "got": got[:5]})
    n = max(len(labelled), 1)
    return Score(
        name,
        len(labelled),
        hit1 / n,
        r5 / n,
        r10 / n,
        mrr / n,
        errors,
        round(time.monotonic() - started, 1),
        misses,
    )


# ── retrievers ──────────────────────────────────────────────────────────


def current_retriever() -> Retriever:
    """The live `kg ask` path: domain detection, query expansion, Weaviate hybrid search."""
    from agents.query import search

    return lambda q, k: [r["id"] for r in search(q, top_k=k)]


def bm25_retriever() -> tuple[Retriever, Callable[[], None]]:
    """Weaviate's BM25 alone: no embedding call, so it works when the embedding service does not."""
    from agents.config import load_config
    from agents.vector_store import get_vector_store

    store = get_vector_store(load_config())
    collection = store._get_collection()

    def run(q: str, k: int) -> list[str]:
        return [
            str(o.properties.get("entry_id", ""))
            for o in collection.query.bm25(query=q, limit=k).objects
        ]

    return run, store.close


RETRIEVERS = ("current", "bm25")
