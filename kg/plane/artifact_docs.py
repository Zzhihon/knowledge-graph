"""Artifact text for the search index: one document per artifact whose body can be read locally.

The artifact collector (`kg plane collect artifacts`) records each artifact in the state store
with `attrs.local_file` when a local copy exists. `export_docs` reads those files, strips the
HTML down to its text and masks credentials, IPs and internal hosts (`kg.plane.sensitive`), so
the index never holds them. Nothing is fetched from the network: an Orca link without a local
source file, or a Claude Artifact without a local file, gets no document.

The index itself (#7) is rebuilt elsewhere; it only needs `export_docs` (or `doc_from_file`)
and the `ArtifactDoc` fields. `ArtifactDoc.id` is the artifact's resource id, stable across
runs, so the index can upsert by it and drop ids that are no longer exported.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

    from kg.plane.sensitive import Scanner

MAX_BYTES = 2_000_000  # larger files are cut here before stripping
TEXT_SUFFIXES = (".html", ".htm", ".md", ".markdown", ".txt")

_SKIP = {"script", "style", "noscript", "template", "iframe", "object", "canvas"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "li",
          "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
          "figure", "figcaption", "br", "hr", "dt", "dd", "details", "summary", "text"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.h1: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_h1 = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP:
            self._skip += 1
        if tag == "h1":
            self._in_h1 = True
        if tag in _BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP and self._skip:
            self._skip -= 1
        if tag == "h1":
            self._in_h1 = False
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title.append(data)
            return
        if self._skip:
            return
        if self._in_h1:
            self.h1.append(data)
        self.parts.append(data)


def _squeeze(text: str) -> str:
    lines = (re.sub(r"[ \t\r\f\v ]+", " ", line).strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text) of an HTML page: `<title>` (else the first `<h1>`) and the visible text."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    title = _squeeze("".join(parser.title)) or _squeeze("".join(parser.h1))
    return title.replace("\n", " "), _squeeze("".join(parser.parts))


def read_text(path: Path, max_bytes: int = MAX_BYTES) -> tuple[str, str]:
    """(title, text) of a local artifact file; Markdown's title is its first `# ` heading."""
    with path.open("rb") as fh:
        raw = fh.read(max_bytes)
    body = raw.decode("utf-8", errors="replace")
    if path.suffix.lower() in (".html", ".htm"):
        return html_to_text(body)
    title = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), "")
    return title, body.strip()


@dataclass(frozen=True)
class ArtifactDoc:
    id: str  # the artifact's resource id, e.g. artifact:orca:<slug>
    title: str
    text: str  # visible text, credentials / IPs / internal hosts masked
    source: str  # orca | local | claude (the adapter), see attrs.origin for the app
    url: str | None  # the share link, when there is one
    file: str  # the local file the text came from
    updated_at: int | None
    project: str | None
    sensitive: bool  # the unmasked text had hits; the index may want to hide these

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def doc_from_file(rid: str, path: Path, *, attrs: dict[str, Any], scanner: Scanner,
                  max_bytes: int = MAX_BYTES) -> ArtifactDoc | None:
    """One document for an artifact's local file, or None when it can't be read as text."""
    if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
        return None
    try:
        title, text = read_text(path, max_bytes)
    except OSError:
        return None
    if not text:
        return None
    return ArtifactDoc(
        id=rid,
        title=str(attrs.get("title") or title or path.stem),
        text=scanner.redact(text),
        source=str(attrs.get("adapter") or rid.split(":")[1]),
        url=attrs.get("url"),
        file=str(path),
        updated_at=attrs.get("updated_at"),
        project=attrs.get("project"),
        sensitive=bool(scanner.scan(text)),
    )


def export_docs(conn: sqlite3.Connection, scanner: Scanner, *,
                max_bytes: int = MAX_BYTES) -> list[ArtifactDoc]:
    """A document for every live artifact in the state store that has a readable local file.

    An artifact seen in two places (an Orca link and the local page it was shared from) gives
    two documents with the same `file`; the index can collapse them by `file` if it wants one.
    """
    from kg.plane.store import list_resources

    docs: list[ArtifactDoc] = []
    for item in list_resources(conn, kind="artifact"):
        attrs = item["attrs"] or {}
        file = attrs.get("local_file")
        if not file:
            continue
        doc = doc_from_file(item["id"], Path(file), attrs={"title": item["name"], **attrs},
                            scanner=scanner, max_bytes=max_bytes)
        if doc:
            docs.append(doc)
    return docs
