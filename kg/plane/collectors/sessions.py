"""Projects in progress: recent Claude / Codex sessions, Orca worktrees, git state, open PRs.

`kg plane collect sessions`, every 5 minutes. Reads only the machine:

1. Sessions changed in the last 7 days, through `kg.plane.session_reader` (incremental: a cache
   of per-file digests and byte offsets in `<vault>/.kg/plane/sessions-cursor.json`). Which
   folders may be read comes from the capture config (`.kg/capture/config.yaml`): the same
   allow / deny lists and the same skip rules as the nightly capture.
2. Orca: `orca worktree ps --json` (live terminals, unread, branch) and `orca project list
   --json` (which GitHub repo a worktree belongs to). If Orca is missing or fails, every
   project records `orca: null` (unknown) and the run goes on.
3. git, per repo the project's sessions and worktrees live in: branch, uncommitted files,
   ahead / behind, last commit time.
4. Open PRs from the GitHub digest's latest snapshot (`sessions.github_snapshot`); GitHub
   itself is never called. Repos the snapshot does not cover get `open_prs: null`.
5. The knowledge base: `07-Projects/proj-*.md` whose repo, aliases or id match the project,
   with the first sentence of its Current State.

Resources: `session:<claude|codex>:<session id>` and `project:<name>`, where the name is what
`kg capture` calls the project (the allow-list folder, or its child for container folders).
The project's stage (`kg.plane.progress.judge_project`) is its signal; rule `project.stalled`
opens an info incident while a project stays stalled.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import frontmatter
import yaml

from kg.plane.collectors import CollectContext, Collection, CommandError, register
from kg.plane.ids import is_valid_id
from kg.plane.progress import (
    STAGE_LABELS,
    STAGE_STATUS,
    WRITING_WINDOW,
    GitStatus,
    current_state,
    github_repo,
    judge_project,
    parse_git_status,
    parse_orca_projects,
    parse_orca_ps,
    session_state,
)
from kg.plane.rules import Verdict, rule
from kg.plane.session_reader import Digest, load_cursor, read_session, save_cursor
from kg.plane.store import DAY, Resource, Signal

if TYPE_CHECKING:
    from collections.abc import Iterable

    from kg.capture.scan import CaptureConfig

DEFAULT_DAYS = 7
CURSOR_KEEP_DAYS = 30  # digests of files idle this long are dropped from the cache
MAX_REPOS = 6  # per project
# A short session whose last message is one of these never got going (launch checks).
_OPENER = re.compile(r"(?i)^(?:hi|hello|hey|你好|您好|在吗|在不在|test|测试|ping|exit|quit|"
                     r"/?clear|/?login)[\s!！。.?？~～]*$")
DEFAULT_GITHUB_SNAPSHOT = "~/.agent-autoloop/work/github-digest/input.json"
DEFAULT_RESUME = {"claude": "claude --resume {id}", "codex": "codex resume {id}"}
SESSION_STATUS = {"working": "up", "waiting": "degraded", "done": "asleep",
                  "interrupted": "unknown"}


@dataclass
class Settings:
    days: int = DEFAULT_DAYS
    github_snapshot: str | None = DEFAULT_GITHUB_SNAPSHOT
    resume: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_RESUME))
    orca: bool = True


def load_settings(vault: Path) -> Settings:
    file = vault / ".kg" / "plane" / "config.yaml"
    raw: dict[str, Any] = {}
    if file.exists():
        raw = (yaml.safe_load(file.read_text(encoding="utf-8")) or {}).get("sessions") or {}
    settings = Settings()
    if "days" in raw:
        settings.days = int(raw["days"])
    if "github_snapshot" in raw:
        settings.github_snapshot = raw["github_snapshot"] or None
    settings.resume.update({k: str(v) for k, v in (raw.get("resume") or {}).items()})
    if "orca" in raw:
        settings.orca = bool(raw["orca"])
    return settings


def cursor_path(vault: Path) -> Path:
    return vault / ".kg" / "plane" / "sessions-cursor.json"


# ── sessions ──


def _session_files(config: CaptureConfig, since: float) -> Iterable[tuple[str, Path, float]]:
    for source, folder in config.sources.items():
        if not folder.is_dir():
            continue
        for file in folder.rglob("*.jsonl"):
            if source == "claude" and "subagents" in file.parts:
                continue
            try:
                mtime = file.stat().st_mtime
            except OSError:
                continue
            if mtime >= since:
                yield source, file, mtime


def read_sessions(config: CaptureConfig, cursor: dict[str, Digest], *, now: int, days: int,
                  notes: list[str]) -> list[Digest]:
    """Digests of the sessions changed in the last `days`; updates `cursor` in place."""
    from kg.capture.scan import project_for

    def allowed(cwd: str) -> bool:
        return project_for(cwd, config) is not None

    seen: list[Digest] = []
    failed = 0
    for source, file, _ in _session_files(config, now - days * DAY):
        key = str(file)
        previous = cursor.get(key)
        if previous and previous.skip == "not-allowed" and allowed(previous.cwd):
            previous = None  # the allow list grew since
        try:
            digest = read_session(source, file, previous, allowed=allowed)
        except OSError:
            failed += 1
            continue
        cursor[key] = digest
        seen.append(digest)
    keep = now - CURSOR_KEEP_DAYS * DAY
    for key in [k for k, d in cursor.items() if d.mtime < keep]:
        del cursor[key]
    if failed:
        notes.append(f"{failed} 个会话文件读不了")
    return seen


def is_trivial(digest: Digest) -> bool:
    """A session that never got going: no agent reply, or a few turns ending on an opener
    ("hi", "login") that the agent answered with a greeting."""
    if not digest.last_agent:
        return True
    return digest.turns <= 3 and bool(_OPENER.match(digest.last_user.strip()))


def resume_command(digest: Digest, templates: dict[str, str]) -> str:
    template = templates.get(digest.source, "")
    if not template:
        return ""
    command = template.format(id=digest.session_id)
    return f"cd {shlex.quote(digest.cwd)} && {command}" if digest.cwd else command


def waiting_reason(digest: Digest) -> str:
    if digest.last_event == "ask":
        return f"Agent 用提问工具等你选（{digest.last_tool}）"
    return digest.last_reason or "Agent 最后一条是提问"


def session_view(digest: Digest, project: str, now: int, templates: dict[str, str]
                 ) -> dict[str, Any]:
    state = session_state(last_event=digest.last_event, last_question=digest.last_question,
                          mtime=digest.mtime, now=now)
    last_active = int(max(digest.last_ts or 0, digest.last_agent_ts or 0) or digest.mtime)
    return {
        "source": digest.source, "session_id": digest.session_id, "project": project,
        "cwd": digest.cwd, "branch": digest.branch, "started": digest.started,
        "last_active": last_active, "turns": digest.turns, "scheduled": digest.scheduled,
        "last_user": digest.last_user, "last_user_ts": digest.last_user_ts,
        "last_agent": digest.last_agent, "last_agent_tail": digest.last_agent_tail,
        "last_agent_ts": digest.last_agent_ts, "last_event": digest.last_event,
        "last_tool": digest.last_tool, "writing": now - digest.mtime <= WRITING_WINDOW,
        "state": state, "waiting": state == "waiting",
        "waiting_reason": waiting_reason(digest) if state == "waiting" else "",
        "resume": resume_command(digest, templates),
    }


def board_sessions(config: CaptureConfig, digests: Iterable[Digest], *, now: int,
                   settings: Settings) -> tuple[list[dict[str, Any]], int]:
    """The sessions the board shows, and how many were left out as never having got going.

    Shown: allowed, not skipped, and either being written right now or with something said in
    the last `days` (a file can be touched by a resume without anyone saying anything), with
    an agent reply and more than a one-word opener.
    """
    from kg.capture.scan import project_for

    shown: list[dict[str, Any]] = []
    silent = 0
    for d in digests:
        project = project_for(d.cwd, config) if not d.skip else None
        if not project:
            continue
        view = session_view(d, project, now, settings.resume)
        if view["writing"]:
            shown.append(view)
        elif now - view["last_active"] > settings.days * DAY:
            continue
        elif is_trivial(d):
            silent += 1
        else:
            shown.append(view)
    return shown, silent


# ── git ──


@dataclass
class Repo:
    path: str
    status: GitStatus
    last_commit: int | None
    github: str

    def view(self) -> dict[str, Any]:
        s = self.status
        return {"path": self.path, "name": Path(self.path).name, "branch": s.branch,
                "upstream": s.upstream, "ahead": s.ahead, "behind": s.behind, "dirty": s.dirty,
                "changed": s.changed, "untracked": s.untracked, "conflicts": s.conflicts,
                "last_commit": self.last_commit, "github": self.github}


class Git:
    """git through ctx.run, with toplevel lookups cached for the run."""

    def __init__(self, ctx: CollectContext) -> None:
        self.ctx = ctx
        self._top: dict[str, str | None] = {}
        self._repo: dict[str, Repo | None] = {}
        self.failures = 0

    def toplevel(self, path: str) -> str | None:
        if path not in self._top:
            top = None
            if path and os.path.isdir(path):
                try:
                    top = self.ctx.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                                       timeout=10).strip() or None
                except CommandError:
                    top = None
            self._top[path] = top
        return self._top[path]

    def repo(self, top: str, github_hint: str = "") -> Repo | None:
        if top in self._repo:
            return self._repo[top]
        run = self.ctx.run
        try:
            status = parse_git_status(run(["git", "-C", top, "status", "--porcelain=v2",
                                           "--branch"], timeout=20))
        except CommandError:
            self.failures += 1
            self._repo[top] = None
            return None
        try:
            last = int(run(["git", "-C", top, "log", "-1", "--format=%ct"], timeout=10).strip())
        except (CommandError, ValueError):
            last = None
        try:
            remote = run(["git", "-C", top, "config", "--get", "remote.origin.url"], timeout=5)
        except CommandError:
            remote = ""
        repo = Repo(top, status, last, github_repo(remote) or github_hint)
        self._repo[top] = repo
        return repo


# ── orca, GitHub snapshot, knowledge base ──


def read_orca(ctx: CollectContext, notes: list[str]) -> tuple[list[Any], dict[str, str]] | None:
    try:
        worktrees = parse_orca_ps(json.loads(ctx.run(["orca", "worktree", "ps", "--json"],
                                                     timeout=20)))
    except (CommandError, ValueError, AttributeError) as exc:
        notes.append(f"Orca 读不到，终端数记为未知：{str(exc)[:120]}")
        return None
    try:
        repos = parse_orca_projects(json.loads(ctx.run(["orca", "project", "list", "--json"],
                                                       timeout=20)))
    except (CommandError, ValueError, AttributeError):
        repos = {}
    return worktrees, repos


@dataclass
class PullRequests:
    repo: str  # owner/name the snapshot covers
    read_at: str
    mine: list[dict[str, Any]]


def read_github_snapshot(path: str | None, notes: list[str]) -> PullRequests | None:
    """Open PRs from the GitHub digest's snapshot; never calls GitHub."""
    if not path:
        return None
    file = Path(os.path.expanduser(path))
    if not file.exists():
        return None
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notes.append("GitHub 汇总快照读不了，未合并 PR 记为未知")
        return None
    me = str(raw.get("me") or "")
    mine = [p for p in raw.get("prs") or []
            if isinstance(p, dict) and (p.get("mine") or (me and p.get("author") == me))]
    return PullRequests(str(raw.get("prsRepo") or "").lower(), str(raw.get("readAt") or ""),
                        [{"number": p.get("number"), "title": p.get("title", ""),
                          "draft": bool(p.get("isDraft")), "updated": p.get("updatedAt", "")}
                         for p in mine])


@dataclass
class ProjectPage:
    path: str  # relative to the vault
    id: str
    title: str
    status: str
    updated: str
    state: str
    as_of: str
    keys: set[str]


def read_project_pages(vault: Path) -> list[ProjectPage]:
    folder = vault / "07-Projects"
    pages: list[ProjectPage] = []
    if not folder.is_dir():
        return pages
    for file in sorted(folder.glob("proj-*.md")):
        try:
            post = frontmatter.load(str(file))
        except Exception:  # noqa: BLE001 - one broken page must not stop the board
            continue
        meta = post.metadata
        page_id = str(meta.get("id") or file.stem)
        keys = {page_id.lower(), page_id.lower().removeprefix("proj-")}
        if meta.get("repo"):
            keys.add(Path(str(meta["repo"])).name.lower())
        aliases = meta.get("aliases") or []
        keys.update(str(a).lower() for a in (aliases if isinstance(aliases, list) else [aliases]))
        state, as_of = current_state(post.content)
        pages.append(ProjectPage(str(file.relative_to(vault)), page_id, str(meta.get("title", "")),
                                 str(meta.get("status", "")), str(meta.get("updated", "")),
                                 state, as_of, keys))
    return pages


def match_page(name: str, pages: list[ProjectPage]) -> ProjectPage | None:
    hits = [p for p in pages if name.lower() in p.keys]
    if not hits:
        return None
    return max(hits, key=lambda p: (p.status == "active", p.updated))


# ── the collector ──


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


@register
class SessionsCollector:
    name = "sessions"

    def collect(self, ctx: CollectContext) -> Collection:
        from kg.capture.scan import load_capture_config, project_for

        out = Collection()
        if ctx.vault is None:
            out.scopes = ()
            out.notes.append("没有知识库路径，跳过")
            return out
        try:
            config = load_capture_config(ctx.vault)
        except (FileNotFoundError, ValueError) as exc:
            out.scopes = ()  # nothing was looked at: mark nothing gone
            out.notes.append(f"读不到会话白名单：{exc}")
            return out
        settings = load_settings(ctx.vault)
        now = ctx.now
        cursor_file = cursor_path(ctx.vault)
        cursor = load_cursor(cursor_file)
        digests = read_sessions(config, cursor, now=now, days=settings.days, notes=out.notes)
        save_cursor(cursor_file, cursor)

        sessions, silent = board_sessions(config, digests, now=now, settings=settings)

        orca = read_orca(ctx, out.notes) if settings.orca else None
        worktrees, orca_repos = orca if orca else ([], {})
        by_project_wt: dict[str, list[Any]] = {}
        hidden_wt = 0
        for w in worktrees:
            project = project_for(w.path, config)
            if not project:
                hidden_wt += 1
                continue
            recent = (w.last_activity or 0) >= now - settings.days * DAY
            if w.live or recent:
                by_project_wt.setdefault(project, []).append(w)
        for s in sessions:
            homes = [w for w in by_project_wt.get(s["project"], []) if _inside(s["cwd"], w.path)]
            s["orca_worktree"] = max(homes, key=lambda w: len(w.path)).path if homes else None

        prs = read_github_snapshot(settings.github_snapshot, out.notes)
        pages = read_project_pages(ctx.vault)
        git = Git(ctx)
        names = sorted({s["project"] for s in sessions} | set(by_project_wt))
        # Several local clones can share one GitHub repo; its PRs go to the project named
        # after it when there is one, so they are not counted twice.
        pr_owner = prs.repo.rpartition("/")[2] if prs else ""
        pr_owner = pr_owner if pr_owner in {n.lower() for n in names} else ""
        for name in names:
            rid = f"project:{name}"
            if not is_valid_id(rid):
                out.notes.append(f"项目名 {name!r} 不能做资源 id，跳过")
                continue
            mine = sorted((s for s in sessions if s["project"] == name),
                          key=lambda s: -s["last_active"])
            wts = by_project_wt.get(name, [])
            owns_prs = prs if not pr_owner or name.lower() == pr_owner else None
            attrs = self._project(name, mine, wts, orca is not None, orca_repos, owns_prs, pages,
                                  git, now)
            out.resources.append(Resource(rid, "project", name, self.name, attrs))
            out.signals.append(Signal(rid, now, STAGE_STATUS[attrs["stage"]],
                                      float(attrs["sessions"]["recent"]), "sessions",
                                      {"stage": attrs["stage"], "reason": attrs["reason"],
                                       "dirty": attrs["dirty"], "open_prs": attrs["open_prs"]}))
        for s in sessions:
            rid = f"session:{s['source']}:{s['session_id']}"
            if not is_valid_id(rid):
                continue
            label = s["last_user"].splitlines()[0][:40] if s["last_user"] else s["session_id"][:8]
            out.resources.append(Resource(rid, "session", f"{s['project']} · {label}", self.name,
                                          s))
            out.signals.append(Signal(rid, now, SESSION_STATUS[s["state"]], float(s["turns"]),
                                      "turns", {"state": s["state"]}))
        if git.failures:
            out.notes.append(f"{git.failures} 个仓库 git status 失败")
        if silent:
            out.notes.append(f"{silent} 个会话没聊起来（没有回复或只打了招呼），不展示")
        if hidden_wt:
            out.notes.append(f"{hidden_wt} 个 Orca 工作区不在白名单，不展示")
        return out

    @staticmethod
    def _project(name: str, sessions: list[dict[str, Any]], worktrees: list[Any], orca_known: bool,
                 orca_repos: dict[str, str], prs: PullRequests | None, pages: list[ProjectPage],
                 git: Git, now: int) -> dict[str, Any]:
        # Repos: where the sessions ran and the worktrees live, most recent first.
        places: list[tuple[int, str, str]] = [(s["last_active"], s["cwd"], "") for s in sessions]
        places += [(w.last_output or w.last_activity or 0, w.path, orca_repos.get(w.repo_id, ""))
                   for w in worktrees]
        repos: list[Repo] = []
        for _, path, hint in sorted(places, key=lambda p: -p[0]):
            top = git.toplevel(path)
            if not top or any(r.path == top for r in repos):
                continue
            repo = git.repo(top, hint)
            if repo:
                repos.append(repo)
            if len(repos) >= MAX_REPOS:
                break
        repo_views = [r.view() for r in repos]
        open_prs: int | None = None
        pr_items: list[dict[str, Any]] = []
        if prs and any(r.github == prs.repo for r in repos):
            pr_items = prs.mine
            open_prs = len(pr_items)
        counts = Counter(s["state"] for s in sessions)
        terminal_output = max((w.last_output or 0 for w in worktrees), default=0) or None
        stage = judge_project(now=now, sessions=sessions, repos=repo_views, open_prs=open_prs,
                              other_activity=terminal_output)
        conclusion = next((s for s in sessions if s["last_agent"]), None)
        page = match_page(name, pages)
        orca: dict[str, Any] | None = None
        if orca_known:
            orca = {
                "live": sum(w.live for w in worktrees),
                "unread": sum(1 for w in worktrees if w.unread),
                "worktrees": [{"path": w.path, "name": Path(w.path).name, "branch": w.branch,
                               "live": w.live, "unread": w.unread, "status": w.status,
                               "last_output": w.last_output, "agents": w.agents}
                              for w in sorted(worktrees, key=lambda w: -w.live)],
            }
        return {
            "name": name, "stage": stage.stage, "stage_label": STAGE_LABELS[stage.stage],
            "reason": stage.reason, "last_action": stage.last_action,
            "sessions": {"total": len(sessions),
                         "recent": sum(1 for s in sessions if now - s["last_active"] <= DAY),
                         **{k: counts.get(k, 0) for k in SESSION_STATUS}},
            "last_conclusion": {"text": conclusion["last_agent"],
                                "ts": conclusion["last_agent_ts"] or conclusion["last_active"],
                                "session": conclusion["session_id"],
                                "source": conclusion["source"]} if conclusion else None,
            "repos": repo_views, "dirty": sum(r["dirty"] for r in repo_views),
            "open_prs": open_prs, "prs": pr_items,
            "prs_read_at": prs.read_at if prs and open_prs is not None else None,
            "orca": orca,
            "kb": {"path": page.path, "title": page.title, "status": page.status,
                   "state": page.state, "as_of": page.as_of, "updated": page.updated}
            if page else None,
            "collected_at": now,
        }


@rule("project", "project.stalled", window=1)
def project_stalled(history: list[Signal]) -> Verdict | None:
    """Open while a project is stalled (3 days still, work left open); close once it is not."""
    last = history[-1]
    detail = last.detail or {}
    if "stage" not in detail:
        return None
    if detail["stage"] == "stalled":
        return Verdict(True, "info", f"项目停滞：{detail.get('reason', '')}",
                       {"ts": last.ts, "dirty": detail.get("dirty"),
                        "open_prs": detail.get("open_prs")})
    return Verdict(False, summary=f"不再停滞（{STAGE_LABELS.get(detail['stage'], '')}）",
                   evidence={"ts": last.ts})
