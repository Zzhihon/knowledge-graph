"""The local retrieval index: one LanceDB table of chunks (text, jieba tokens, vector).

Layout under `<vault>/.kg/index/` (not in git):
  lancedb/        LanceDB database, table `chunks`
  manifest.json   model, granularity, and per-file mtime / size / sha256 of what is indexed
  status.json     last attempt, last success, last error, counts (read by health checks)

`update()` only touches files whose mtime or size changed and whose content hash really
differs; `rebuild()` drops everything and indexes the whole vault. Both write status.json on
success and on failure, and failures raise, so a nightly run cannot fail silently.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kg.retrieval import corpus
from kg.retrieval.errors import IndexMissingError, RetrievalError
from kg.retrieval.text import add_terms, tokenize

if TYPE_CHECKING:
    from collections.abc import Callable

    from kg.retrieval.models import Embedder

TABLE = "chunks"
SCHEMA_VERSION = 1


def index_root(vault: Path) -> Path:
    override = os.environ.get("KG_INDEX_DIR")
    return Path(override) if override else vault / ".kg" / "index"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


@dataclass
class UpdateReport:
    mode: str
    added: int = 0
    modified: int = 0
    deleted: int = 0
    touched: int = 0  # mtime changed, content did not
    skipped: int = 0  # files without an entry id
    chunks_written: int = 0
    entries: int = 0
    chunks: int = 0
    seconds: float = 0.0
    embed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


@dataclass
class Pending:
    added: list[str]
    modified: list[str]
    deleted: list[str]

    @property
    def count(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)


class Index:
    def __init__(self, vault: Path, root: Path | None = None) -> None:
        self.vault = vault
        self.root = root or index_root(vault)
        self.manifest_path = self.root / "manifest.json"
        self.status_path = self.root / "status.json"

    # ── state files ────────────────────────────────────────────────────

    def manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.exists():
            return None
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def status(self) -> dict[str, Any]:
        if not self.status_path.exists():
            return {}
        return json.loads(self.status_path.read_text(encoding="utf-8"))

    def _record(self, report: UpdateReport | None, error: RetrievalError | Exception | None,
                manifest: dict[str, Any] | None) -> None:
        status = self.status()
        status["last_attempt"] = _now()
        if error is None and report is not None:
            status.update(ok=True, last_success=status["last_attempt"], error=None,
                          last_update=asdict(report))
        else:
            status["ok"] = False
            status["error"] = (
                error.to_dict() if isinstance(error, RetrievalError)
                else {"code": "index_failed", "message": f"{type(error).__name__}: {error}"}
            )
        if manifest:
            status.update(model=manifest["model"], granularity=manifest["granularity"],
                          entries=len({f["entry_id"] for f in manifest["files"].values()
                                       if f.get("entry_id")}),
                          files=len(manifest["files"]))
        _write_json(self.status_path, status)

    # ── change detection ───────────────────────────────────────────────

    def pending(self, stamps: dict[str, corpus.FileStamp] | None = None) -> Pending:
        """Files that differ from the manifest by mtime or size (no hashing, cheap)."""
        manifest = self.manifest()
        if manifest is None:
            raise IndexMissingError("检索索引还没建", hint="运行 kg index rebuild")
        stamps = corpus.scan(self.vault) if stamps is None else stamps
        files = manifest["files"]
        added = [p for p in stamps if p not in files]
        modified = [
            p for p, s in stamps.items()
            if p in files and (files[p]["mtime_ns"], files[p]["size"]) != (s.mtime_ns, s.size)
        ]
        deleted = [p for p in files if p not in stamps]
        return Pending(added, modified, deleted)

    # ── writing ────────────────────────────────────────────────────────

    def _db(self) -> Any:
        import lancedb

        return lancedb.connect(str(self.root / "lancedb"))

    def table(self) -> Any:
        db = self._db()
        if TABLE not in db.list_tables().tables:
            raise IndexMissingError("检索索引表不存在", hint="运行 kg index rebuild")
        return db.open_table(TABLE)

    @staticmethod
    def _rows(chunks: list[corpus.Chunk], vectors: Any) -> list[dict[str, Any]]:
        return [
            {
                "chunk_id": c.chunk_id,
                "entry_id": c.entry_id,
                "path": c.path,
                "title": c.title,
                "heading": c.heading,
                "type": c.meta.get("type", ""),
                "domain": list(c.meta.get("domain", [])),
                "body": c.body,
                "text": c.text,
                "tokens": " ".join(tokenize(c.text)),
                "vector": vectors[i].astype("float32"),
            }
            for i, c in enumerate(chunks)
        ]

    def rebuild(self, embedder: Embedder, *, granularity: str = "section",
                progress: Callable[[str], None] | None = None) -> UpdateReport:
        return self._run(embedder, granularity=granularity, full=True, progress=progress)

    def update(self, embedder: Embedder, *, granularity: str | None = None,
               progress: Callable[[str], None] | None = None) -> UpdateReport:
        manifest = self.manifest()
        if manifest is None:
            raise IndexMissingError("检索索引还没建，不能增量更新", hint="运行 kg index rebuild")
        granularity = granularity or manifest["granularity"]
        if manifest["model"] != embedder.name or manifest["granularity"] != granularity:
            raise RetrievalError(
                f"索引用的是 {manifest['model']} / {manifest['granularity']}，"
                f"这次是 {embedder.name} / {granularity}，不能混着更新",
                hint="运行 kg index rebuild",
            )
        return self._run(embedder, granularity=granularity, full=False, progress=progress)

    def _run(self, embedder: Embedder, *, granularity: str, full: bool,
             progress: Callable[[str], None] | None) -> UpdateReport:
        say = progress or (lambda _msg: None)
        started = time.monotonic()
        report = UpdateReport("rebuild" if full else "update")
        manifest: dict[str, Any] | None = None
        try:
            if granularity not in corpus.GRANULARITIES:
                raise RetrievalError(f"未知的切块方式：{granularity}")
            stamps = corpus.scan(self.vault)
            if not stamps:
                raise RetrievalError(f"知识库里没有条目文件：{self.vault}")
            old = None if full else self.manifest()
            files: dict[str, Any] = dict(old["files"]) if old else {}

            if full:
                todo = sorted(stamps)
                report.added = len(todo)
                gone: list[str] = []
            else:
                pend = self.pending(stamps)
                gone = list(pend.deleted)
                todo = list(pend.added)
                for rel in pend.modified:
                    entry_sha = corpus.load(self.vault, rel)
                    sha = entry_sha.sha if entry_sha else None
                    if sha is not None and sha == files[rel].get("sha"):
                        s = stamps[rel]
                        files[rel].update(mtime_ns=s.mtime_ns, size=s.size)
                        report.touched += 1
                    else:
                        todo.append(rel)
                        gone.append(rel)
                        report.modified += 1
                report.added = len(pend.added)
                report.deleted = len(pend.deleted)

            entries: list[corpus.Entry] = []
            for rel in todo:
                try:
                    entry = corpus.load(self.vault, rel)
                except OSError as exc:
                    report.errors.append(f"{rel}: {exc}")
                    continue
                if entry is None:
                    report.skipped += 1
                    s = stamps[rel]
                    files[rel] = {"mtime_ns": s.mtime_ns, "size": s.size, "sha": None,
                                  "entry_id": None}
                    continue
                entries.append(entry)

            terms = sorted(set((old or {}).get("terms", [])) | {t for e in entries for t in e.tags})
            add_terms(terms)
            chunks = [c for e in entries for c in corpus.chunks(e, granularity)]
            say(f"{len(entries)} 个条目 → {len(chunks)} 块，计算向量……")
            t0 = time.monotonic()
            vectors = embedder.documents([c.text for c in chunks]) if chunks else None
            report.embed_seconds = round(time.monotonic() - t0, 1)

            db = self._db()
            if full:
                if not chunks:
                    raise RetrievalError("没有可索引的条目")
                table = db.create_table(TABLE, data=self._rows(chunks, vectors), mode="overwrite")
            else:
                table = self.table()
                for i in range(0, len(gone), 200):
                    batch = ", ".join("'" + p.replace("'", "''") + "'" for p in gone[i:i + 200])
                    table.delete(f"path IN ({batch})")
                if chunks:
                    table.add(self._rows(chunks, vectors))
            for p in gone:
                files.pop(p, None)
            for e in entries:
                s = stamps[e.path]
                files[e.path] = {"mtime_ns": s.mtime_ns, "size": s.size, "sha": e.sha,
                                 "entry_id": e.entry_id}
            if full or todo or gone:
                from lancedb.index import FTS

                # Tokens are already jieba-cut and lower-cased: split on spaces, nothing else.
                table.create_index(
                    "tokens",
                    replace=True,
                    config=FTS(base_tokenizer="whitespace", lower_case=False, stem=False,
                               remove_stop_words=False, ascii_folding=False,
                               max_token_length=None),
                )
                if not full:
                    table.optimize()
            report.chunks_written = len(chunks)
            report.chunks = table.count_rows()
            manifest = {
                "schema": SCHEMA_VERSION,
                "model": embedder.name,
                "dim": int(vectors.shape[1]) if vectors is not None else (old or {}).get("dim"),
                "granularity": granularity,
                "updated": _now(),
                "terms": terms,
                "files": files,
            }
            report.entries = len({f["entry_id"] for f in files.values() if f.get("entry_id")})
            report.seconds = round(time.monotonic() - started, 1)
            _write_json(self.manifest_path, manifest)
        except Exception as exc:
            self._record(None, exc, self.manifest())
            raise
        self._record(report, None, manifest)
        return report
