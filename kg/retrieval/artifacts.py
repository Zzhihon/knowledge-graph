"""Artifact pages as a second kind of index document (#6), next to vault entries.

The artifact collector records each artifact in the state store; `kg.plane.artifact_docs`
turns the ones with a readable local file into `ArtifactDoc`s (HTML stripped, credentials, IPs
and internal hosts masked). They go into their own LanceDB table `artifacts`, so the entry
table, its schema and its full-text index stay exactly as #7 built them.

Incremental: the manifest keeps one content hash per `ArtifactDoc.id`; only new or changed
documents are embedded, and ids no longer exported are deleted. When the state store does not
exist or holds no artifact resources yet, nothing is touched.

An artifact can be a long page, so its text is cut into pieces of about `PIECE_CHARS`; each
piece starts with the title, like an entry section, and search collapses them back to one hit.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from kg.retrieval.corpus import Chunk, _split_long

if TYPE_CHECKING:
    from pathlib import Path

    from kg.plane.artifact_docs import ArtifactDoc

TABLE = "artifacts"
KIND = "artifact"
PIECE_CHARS = 1500
MAX_PIECES = 40  # 60k chars of a page; the rest of a very long page is not indexed


def load_docs(vault: Path) -> list[ArtifactDoc] | None:
    """Documents for every live artifact with local text; None when there are no artifacts yet."""
    from kg.plane.artifact_docs import export_docs
    from kg.plane.collectors.artifacts import load_artifacts_config
    from kg.plane.sensitive import Scanner
    from kg.plane.store import connect, db_path, list_resources

    path = db_path(vault)
    if not path.exists():
        return None
    conn = connect(path)
    try:
        if not list_resources(conn, kind=KIND):
            return None
        return export_docs(conn, Scanner(load_artifacts_config(vault).internal_domains))
    finally:
        conn.close()


def doc_hash(doc: ArtifactDoc) -> str:
    """What decides a re-embed: the indexed text and the fields shown in a hit."""
    keep = {k: getattr(doc, k) for k in ("title", "text", "source", "url", "file", "project",
                                         "sensitive")}
    raw = json.dumps(keep, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def pieces(text: str) -> list[str]:
    out: list[str] = []
    for part in _split_long(text):
        while len(part) > PIECE_CHARS * 2:
            out.append(part[:PIECE_CHARS])
            part = part[PIECE_CHARS:]
        out.append(part)
    return [p for p in out if p.strip()][:MAX_PIECES] or [""]


def chunks(doc: ArtifactDoc) -> list[Chunk]:
    head = doc.title + (f"\n项目：{doc.project}" if doc.project else "")
    meta = {"type": KIND, "domain": [], "url": doc.url or "", "source": doc.source,
            "project": doc.project or "", "sensitive": doc.sensitive}
    return [
        Chunk(chunk_id=f"{doc.id}#{i}", entry_id=doc.id, path=doc.file, title=doc.title,
              heading="", body=body, text=f"{head}\n\n{body}".strip(), meta=meta)
        for i, body in enumerate(pieces(doc.text))
    ]


def rows(chunk_list: list[Chunk], vectors: Any) -> list[dict[str, Any]]:
    from kg.retrieval.text import tokenize

    return [
        {
            "chunk_id": c.chunk_id,
            "entry_id": c.entry_id,
            "path": c.path,
            "title": c.title,
            "heading": c.heading,
            "type": KIND,
            "body": c.body,
            "text": c.text,
            "tokens": " ".join(tokenize(c.text)),
            "url": c.meta["url"],
            "source": c.meta["source"],
            "project": c.meta["project"],
            "sensitive": bool(c.meta["sensitive"]),
            "vector": vectors[i].astype("float32"),
        }
        for i, c in enumerate(chunk_list)
    ]
