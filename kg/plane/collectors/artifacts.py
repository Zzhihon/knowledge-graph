"""Artifacts: pages agents made, wherever they ended up. `kg plane collect artifacts`.

One adapter per place, each turned into `resource(kind=artifact)`:

- `artifact:orca:<slug>`     Orca shares (`orca artifacts list --json`). Public links that
                             expire (30 days). The local file a share came from is looked up in
                             Orca's own share map (`artifact-shares.json`; only its paths and
                             slugs are read, never the edit tokens in it).
- `artifact:local:<hash>`    HTML (or Markdown) pages in the folders listed in
                             `<vault>/.kg/plane/artifacts.yaml` (`local:`), by default the
                             answer-me-with-html pages folder. Only on this machine. `<hash>` is
                             the first 16 hex digits of sha1(resolved path), so a page keeps its
                             id as long as it stays where it is.
- `artifact:claude:<id>`     Claude Artifacts. There is no local API, so a daily read-only
                             Claude task writes `<vault>/.kg/plane/claude-artifacts.json` (format
                             in `claude-artifacts.json.example`) and this adapter reads it.

Each artifact's attrs: `title`, `origin` (the app), `adapter`, `visibility` (public / private /
local), `url`, `local_file`, `created_at`, `updated_at`, `expires_at`, `project`, `session`,
`content_hash`, `same_file` (other artifact ids made from the same local file).

The text is only read from local files; nothing is fetched from a share link to scan it. Each
signal carries what the rules judge: `visibility`, `sensitive` (True / False, None when there
was no local text), `hits` (masked, see `kg.plane.sensitive`) and `expires_at`.

Rules (both window=1, judged on every run):
- `artifact.public_sensitive`  critical while a public link's text has sensitive hits.
- `artifact.expiring`          warning from 3 days before `expires_at`; closes when the link is
                               renewed or has expired.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from kg.plane.artifact_docs import MAX_BYTES, html_to_text, read_text
from kg.plane.collectors import CollectContext, Collection, CommandError, register
from kg.plane.ids import check_part, make_id
from kg.plane.rules import Verdict, rule
from kg.plane.sensitive import Scanner, summarize
from kg.plane.store import DAY, Resource, Signal

if TYPE_CHECKING:
    from collections.abc import Sequence

NAME = "artifacts"
EXPIRY_WARN = 3 * DAY
CLAUDE_STALE = 2 * DAY  # the import is daily; older than this, say so in the notes
ORCA_PAGES = 20
VISIBILITIES = ("public", "private", "local")

DEFAULT_LOCAL = ({"path": "~/.answer-me-with-html/pages", "origin": "answer-me-with-html",
                  "glob": ["*.html"]},)
DEFAULT_ORCA_SHARES = ("~/Library/Application Support/orca/profiles/*/artifact-shares.json",)
_CLAUDE_URL = re.compile(r"^https://claude\.ai/(?:code/)?artifacts?/([A-Za-z0-9_-]+)/?$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ── config ──


@dataclass(frozen=True)
class LocalDir:
    path: Path
    origin: str
    patterns: tuple[str, ...] = ("*.html",)


@dataclass(frozen=True)
class ArtifactsConfig:
    local: tuple[LocalDir, ...]
    orca: bool = True
    orca_shares: tuple[str, ...] = DEFAULT_ORCA_SHARES
    claude_import: Path | None = None
    internal_domains: tuple[str, ...] = ()
    preview_roots: tuple[Path, ...] = ()
    max_bytes: int = MAX_BYTES

    @property
    def roots(self) -> tuple[Path, ...]:
        """Folders whose files the preview endpoint may serve (resolved)."""
        return tuple(_resolve(p) for p in (*(d.path for d in self.local), *self.preview_roots))


def _expand(raw: str) -> Path:
    return Path(os.path.expanduser(str(raw)))


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def config_path(vault: Path) -> Path:
    return vault / ".kg" / "plane" / "artifacts.yaml"


def load_artifacts_config(vault: Path | None) -> ArtifactsConfig:
    """`<vault>/.kg/plane/artifacts.yaml`, or the defaults when it is missing."""
    raw: dict[str, Any] = {}
    if vault is not None and config_path(vault).exists():
        raw = yaml.safe_load(config_path(vault).read_text(encoding="utf-8")) or {}
    local = []
    for item in raw.get("local", DEFAULT_LOCAL) or []:
        if isinstance(item, str):
            item = {"path": item}
        patterns = item.get("glob") or ["*.html"]
        local.append(LocalDir(_expand(item["path"]), str(item.get("origin") or "local"),
                              tuple([patterns] if isinstance(patterns, str) else patterns)))
    orca = raw.get("orca") or {}
    claude = raw.get("claude") or {}
    sensitive = raw.get("sensitive") or {}
    imported = claude.get("import", "claude-artifacts.json")
    claude_import = None
    if imported and vault is not None:
        path = _expand(imported)
        claude_import = path if path.is_absolute() else vault / ".kg" / "plane" / path
    return ArtifactsConfig(
        local=tuple(local),
        orca=bool(orca.get("enabled", True)),
        orca_shares=tuple(orca.get("shares") or DEFAULT_ORCA_SHARES),
        claude_import=claude_import,
        internal_domains=tuple(str(d) for d in sensitive.get("internal_domains") or ()),
        preview_roots=tuple(_expand(p) for p in raw.get("preview_roots") or ()),
        max_bytes=int(raw.get("max_bytes") or MAX_BYTES),
    )


# ── the common shape ──


@dataclass
class Entry:
    adapter: str  # orca | local | claude: the second part of the id
    local_id: str
    title: str
    origin: str  # the app it came from
    visibility: str
    url: str | None = None
    local_file: Path | None = None
    created_at: int | None = None
    updated_at: int | None = None
    expires_at: int | None = None
    project: str | None = None
    session: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return make_id("artifact", self.adapter, self.local_id)


def parse_time(raw: Any) -> int | None:
    """ISO 8601 (with `Z`) or Unix seconds / milliseconds to Unix seconds."""
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return int(raw / 1000) if raw > 10**11 else int(raw)
    try:
        return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _stable(part: str) -> str | None:
    try:
        return check_part(part)
    except ValueError:
        return None


# ── where an artifact came from ──


class OriginGuesser:
    """Project (and Claude session) of a local file, from where it sits.

    1. The capture config's allowed roots (`kg.capture.scan.project_for`), when there is one.
    2. The nearest folder with `.git` (a repo or worktree), by its name.
    3. A Claude scratchpad (`.../<project slug>/<session uuid>/scratchpad/...`): the session id,
       and the project when the slug matches a project folder under a capture root.
    """

    def __init__(self, vault: Path | None) -> None:
        self.capture = None
        if vault is not None:
            try:
                from kg.capture.scan import load_capture_config

                self.capture = load_capture_config(vault)
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                self.capture = None
        self._children: dict[Path, list[str]] = {}

    def _slug_project(self, slug: str) -> str | None:
        if self.capture is None:
            return None
        for root in self.capture.allow:
            encoded = str(root.path).replace("/", "-").replace(".", "-")
            if root.container and slug.startswith(encoded + "-"):
                rest = slug[len(encoded) + 1:]
                if root.path not in self._children:
                    try:
                        self._children[root.path] = sorted(
                            (p.name for p in root.path.iterdir() if p.is_dir()), key=len,
                            reverse=True)
                    except OSError:
                        self._children[root.path] = []
                for name in self._children[root.path]:
                    flat = name.replace(".", "-")
                    if rest == flat or rest.startswith(flat + "-"):
                        return name
        return None

    def guess(self, path: Path) -> tuple[str | None, str | None]:
        parts = path.parts
        session = project = None
        for i, part in enumerate(parts[1:], start=1):
            if _UUID.match(part) and parts[i - 1].startswith("-"):
                session = make_id("session", "claude", part)
                project = self._slug_project(parts[i - 1])
                break
        if project is None and self.capture is not None:
            from kg.capture.scan import project_for

            project = project_for(str(path.parent), self.capture)
        if project is None:
            home = Path.home()
            for folder in path.parents:
                if folder in (home, folder.parent):
                    break
                if (folder / ".git").exists():
                    project = folder.name
                    break
        return project, session


# ── adapters ──


def parse_orca_list(text: str) -> tuple[list[dict[str, Any]], str | None]:
    """Items and the next-page cursor of `orca artifacts list --json`."""
    data = json.loads(text)
    if isinstance(data, dict) and data.get("ok") is False:
        raise ValueError(str(data.get("error") or "orca 返回 ok=false"))
    result = data.get("result", data) if isinstance(data, dict) else {"artifacts": data}
    items = result.get("artifacts") or []
    cursor = result.get("nextCursor") or result.get("cursor") or None
    return items, (str(cursor) if cursor else None)


def load_orca_shares(patterns: Sequence[str]) -> dict[str, Path]:
    """{slug: local file it was shared from}, from Orca's share maps. Edit tokens are skipped."""
    found: dict[str, Path] = {}
    for pattern in patterns:
        for file in sorted(glob.glob(os.path.expanduser(pattern))):
            try:
                shares = json.loads(Path(file).read_text(encoding="utf-8")).get("shares") or {}
            except (OSError, ValueError, AttributeError):
                continue
            for path, share in shares.items():
                slug = share.get("slug") if isinstance(share, dict) else None
                if slug and isinstance(path, str):
                    found[str(slug)] = Path(path)
    return found


def orca_entries(items: list[dict[str, Any]], shares: dict[str, Path]) -> tuple[list[Entry], int]:
    entries, skipped = [], 0
    for item in items:
        art = item.get("artifact") or item
        slug = _stable(str(art.get("slug") or ""))
        if not slug or art.get("deletedAt"):
            skipped += bool(not slug)
            continue
        source = shares.get(slug)
        title = art.get("title") or art.get("originalFileName") or slug
        entries.append(Entry(
            "orca", slug, str(title), "orca", "public",
            url=item.get("shareUrl"),
            local_file=source if source and source.is_file() else None,
            created_at=parse_time(art.get("createdAt")),
            updated_at=parse_time(art.get("updatedAt")),
            expires_at=parse_time(art.get("expiresAt")),
            extra={"file_name": art.get("originalFileName"), "bytes": art.get("byteSize"),
                   "version": art.get("version"), "content_type": art.get("sourceContentType")},
        ))
    return entries, skipped


def local_entries(folder: LocalDir, max_bytes: int) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[Path] = set()
    for pattern in folder.patterns:
        for path in sorted(folder.path.glob(pattern)):
            real = _resolve(path)
            if real in seen or not real.is_file():
                continue
            seen.add(real)
            st = real.stat()
            try:
                title, _ = read_text(real, min(max_bytes, 200_000))
            except OSError:
                title = ""
            digest = hashlib.sha1(str(real).encode("utf-8")).hexdigest()[:16]
            entries.append(Entry(
                "local", digest, title or real.stem, folder.origin, "local",
                local_file=real,
                created_at=int(getattr(st, "st_birthtime", st.st_mtime)),
                updated_at=int(st.st_mtime),
                extra={"file_name": real.name, "bytes": st.st_size},
            ))
    return entries


def parse_claude_import(raw: Any) -> tuple[list[Entry], int | None, list[str]]:
    """Entries, `synced_at` and problems of a `claude-artifacts.json` (version 1)."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("claude-artifacts.json 不是 version 1 的格式")
    entries, problems = [], []
    for n, item in enumerate(raw.get("items") or []):
        url = str((item or {}).get("url") or "")
        m = _CLAUDE_URL.match(url)
        if not m:
            problems.append(f"第 {n + 1} 项的 url 不是 claude.ai 工件链接")
            continue
        visibility = str(item.get("visibility") or "private")
        file = item.get("file")
        path = _expand(file) if file else None
        entries.append(Entry(
            "claude", m.group(1), str(item.get("title") or m.group(1)), "claude",
            visibility if visibility in VISIBILITIES else "private",
            url=url,
            local_file=path if path and path.is_file() else None,
            created_at=parse_time(item.get("created_at")),
            updated_at=parse_time(item.get("updated_at")),
            project=item.get("project") or None,
            session=item.get("session") or None,
            extra={"scope": item.get("scope") or "mine"},
        ))
    return entries, parse_time(raw.get("synced_at")), problems


# ── scanning and building ──


def scan_file(path: Path, scanner: Scanner,
              max_bytes: int) -> tuple[list[dict[str, Any]], str, str]:
    """(masked hits, sha256 of the bytes read, page title) of a local file."""
    with path.open("rb") as fh:
        data = fh.read(max_bytes)
    text, title = data.decode("utf-8", errors="replace"), ""
    if path.suffix.lower() in (".html", ".htm"):
        title, body = html_to_text(text)
        text = f"{title}\n{body}"
    return summarize(scanner.scan(text)), hashlib.sha256(data).hexdigest(), title


def build(entries: list[Entry], now: int, scanner: Scanner, guesser: OriginGuesser,
          max_bytes: int) -> tuple[list[Resource], list[Signal], list[str]]:
    by_file: dict[Path, list[str]] = {}
    for e in entries:
        if e.local_file:
            by_file.setdefault(_resolve(e.local_file), []).append(e.id)
    resources, signals, notes = [], [], []
    for e in entries:
        hits: list[dict[str, Any]] | None = None
        digest = None
        if e.local_file:
            if e.project is None or e.session is None:
                project, session = guesser.guess(_resolve(e.local_file))
                e.project, e.session = e.project or project, e.session or session
            try:
                hits, digest, title = scan_file(e.local_file, scanner, max_bytes)
            except OSError as exc:
                notes.append(f"{e.id} 的本地文件读不到：{exc.strerror}")
            else:
                # An Orca share without a title is named after its file; the page says better.
                if title and e.title in (e.local_id, e.extra.get("file_name")):
                    e.title = title
        same = [i for i in by_file.get(_resolve(e.local_file), []) if i != e.id] \
            if e.local_file else []
        attrs = {
            "title": e.title, "origin": e.origin, "adapter": e.adapter,
            "visibility": e.visibility, "url": e.url,
            "local_file": str(e.local_file) if e.local_file else None,
            "created_at": e.created_at, "updated_at": e.updated_at, "expires_at": e.expires_at,
            "project": e.project, "session": e.session, "content_hash": digest,
            "same_file": same, **{k: v for k, v in e.extra.items() if v is not None},
        }
        resources.append(Resource(e.id, "artifact", e.title, NAME, attrs))
        expired = e.expires_at is not None and e.expires_at <= now
        left = None if e.expires_at is None else round((e.expires_at - now) / DAY, 2)
        signals.append(Signal(e.id, now, "down" if expired else "up", left,
                              "d" if left is not None else None,
                              {"visibility": e.visibility,
                               "sensitive": None if hits is None else bool(hits),
                               "hits": hits or [], "expires_at": e.expires_at,
                               "url": e.url}))
    return resources, signals, notes


@register
class ArtifactsCollector:
    name = NAME

    def collect(self, ctx: CollectContext) -> Collection:
        cfg = load_artifacts_config(ctx.vault)
        out = Collection()
        scopes: list[str] = []
        entries: list[Entry] = []

        if cfg.orca:
            try:
                items, cursor, pages = [], None, 0
                while True:
                    argv = ["orca", "artifacts", "list", "--json"]
                    text = ctx.run([*argv, "--cursor", cursor] if cursor else argv, timeout=30)
                    page, cursor = parse_orca_list(text)
                    items += page
                    pages += 1
                    if not cursor or pages >= ORCA_PAGES:
                        break
            except (CommandError, ValueError) as exc:
                out.notes.append(f"Orca 工件列不出来，这轮不判消失：{exc}")
            else:
                found, skipped = orca_entries(items, load_orca_shares(cfg.orca_shares))
                entries += found
                if cursor:
                    out.notes.append(f"Orca 工件超过 {ORCA_PAGES} 页，后面的没列，这轮不判消失")
                else:
                    scopes.append("artifact:orca:")
                if skipped:
                    out.notes.append(f"{skipped} 个 Orca 工件的 slug 不能做资源 id，跳过")

        missing = [str(d.path) for d in cfg.local if not d.path.is_dir()]
        for folder in cfg.local:
            if folder.path.is_dir():
                entries += local_entries(folder, cfg.max_bytes)
        if missing:
            out.notes.append(f"{len(missing)} 个登记目录不存在，本地页这轮不判消失")
        else:
            scopes.append("artifact:local:")

        if cfg.claude_import and cfg.claude_import.exists():
            try:
                raw = json.loads(cfg.claude_import.read_text(encoding="utf-8"))
                found, synced, problems = parse_claude_import(raw)
            except (OSError, ValueError) as exc:
                out.notes.append(f"Claude 工件清单读不了，这轮不判消失：{exc}")
            else:
                entries += found
                scopes.append("artifact:claude:")
                out.notes += problems
                if synced is None or ctx.now - synced > CLAUDE_STALE:
                    days = "?" if synced is None else math.floor((ctx.now - synced) / DAY)
                    out.notes.append(f"Claude 工件清单 {days} 天没同步了")
        else:
            out.notes.append("还没有 Claude 工件清单（claude-artifacts.json），跳过")

        resources, signals, notes = build(entries, ctx.now, Scanner(cfg.internal_domains),
                                          OriginGuesser(ctx.vault), cfg.max_bytes)
        out.resources, out.signals = resources, signals
        out.notes += notes
        out.scopes = tuple(scopes)
        return out


# ── rules ──


def _kinds(hits: list[dict[str, Any]]) -> str:
    return "、".join(f"{h.get('label', h.get('kind'))} ×{h.get('count', 1)}" for h in hits)


@rule("artifact", "artifact.public_sensitive", window=1)
def public_sensitive(history: Sequence[Signal]) -> Verdict | None:
    """Critical while a public link's local text has credentials, IPs or internal hosts."""
    last = history[-1]
    d = last.detail or {}
    if d.get("visibility") != "public":
        return Verdict(False, summary="已不是公开链接", evidence={"ts": last.ts})
    if d.get("sensitive") is None:
        return None  # no local text to judge by
    if d["sensitive"]:
        hits = d.get("hits") or []
        return Verdict(True, "critical", f"公开链接里有敏感内容：{_kinds(hits)}",
                       {"ts": last.ts, "hits": hits, "url": d.get("url")})
    return Verdict(False, summary="公开链接里已没有敏感内容", evidence={"ts": last.ts})


@rule("artifact", "artifact.expiring", window=1)
def expiring(history: Sequence[Signal]) -> Verdict | None:
    """Warning from EXPIRY_WARN before expires_at; closes once renewed or expired."""
    last = history[-1]
    expires = (last.detail or {}).get("expires_at")
    if not expires:
        return Verdict(False, summary="不再有过期时间", evidence={"ts": last.ts})
    left = expires - last.ts
    if left <= 0:
        return Verdict(False, summary="已过期", evidence={"ts": last.ts, "expires_at": expires})
    if left <= EXPIRY_WARN:
        days = max(1, math.ceil(left / DAY))
        return Verdict(True, "warning", f"{days} 天内过期",
                       {"ts": last.ts, "expires_at": expires,
                        "url": (last.detail or {}).get("url")})
    return Verdict(False, summary="已续期", evidence={"ts": last.ts, "expires_at": expires})
