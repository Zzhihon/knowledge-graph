"""The search index as a state-store resource: every `kg index update` / `rebuild` is a signal.

Resource `index:kg:main` (the vault's LanceDB index, `kg.retrieval`). The index commands call
`record_build` after each run, success or failure, so a failed nightly update opens an incident
like any other collector would, and the next good run closes it:

  status  up        the run finished with no errors
          degraded  it finished, but some files or artifacts could not be read (`errors`)
          down      it failed; `detail.code` is the retrieval error code (`index_missing`,
                    `embedding_failed`, ...) or `index_failed` for anything else

Rule `index.failed` (warning): open on the first `down`, close on the next run that finishes.
The heal action for `index.*` incidents (`kg.plane.heal.actions`) marks the index stale while
it is open, so searches say "结果可能不全".

There is nothing on the machine to poll here, so the collector is not registered for
`kg plane collect`: it only reports the build it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kg.plane.collectors import CollectContext, Collection, RunResult, run_collector
from kg.plane.ids import make_id
from kg.plane.rules import Verdict, rule
from kg.plane.store import Resource, Signal, connect, db_path

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

RESOURCE_ID = make_id("index", "kg", "main")
SOURCE = "index"
_ACTIONS = {"update": "更新", "rebuild": "重建"}


@dataclass(frozen=True)
class BuildOutcome:
    """What one `kg index update` / `rebuild` did; built by the CLI from the report or error."""

    action: str  # "update" | "rebuild"
    ok: bool
    code: str = ""  # error code when not ok
    message: str = ""
    seconds: float | None = None
    model: str = ""
    root: str = ""  # index directory, shown in the incident evidence
    counts: dict[str, Any] = field(default_factory=dict)  # UpdateReport numbers when ok
    errors: list[str] = field(default_factory=list)  # files / artifacts that could not be read


def build_signal(outcome: BuildOutcome, now: int) -> Signal:
    if not outcome.ok:
        status = "down"
    elif outcome.errors:
        status = "degraded"
    else:
        status = "up"
    detail: dict[str, Any] = {"action": outcome.action, "model": outcome.model}
    if outcome.ok:
        detail.update(outcome.counts)
        if outcome.errors:
            detail["errors"] = outcome.errors[:5]
            detail["error_count"] = len(outcome.errors)
    else:
        detail.update(code=outcome.code or "index_failed", message=outcome.message[:500])
    return Signal(RESOURCE_ID, now, status, outcome.seconds, "s", detail)


class IndexBuildCollector:
    name = SOURCE

    def __init__(self, outcome: BuildOutcome) -> None:
        self.outcome = outcome

    def collect(self, ctx: CollectContext) -> Collection:
        attrs = {"model": self.outcome.model, "root": self.outcome.root}
        return Collection(
            resources=[Resource(RESOURCE_ID, "index", "检索索引", SOURCE, attrs)],
            signals=[build_signal(self.outcome, ctx.now)],
        )


def record_build(vault: Path, outcome: BuildOutcome, *, now: int) -> RunResult:
    """Write one build into `<vault>/.kg/plane/state.db` and run `index.failed` on it."""
    conn = connect(db_path(vault))
    try:
        return run_collector(conn, IndexBuildCollector(outcome),
                             ctx=CollectContext(now=now, vault=vault))
    finally:
        conn.close()


@rule("index", "index.failed", window=1)
def index_failed(history: Sequence[Signal]) -> Verdict | None:
    last = history[-1]
    detail = last.detail or {}
    action = _ACTIONS.get(str(detail.get("action")), "更新")
    if last.status == "down":
        code = detail.get("code") or "index_failed"
        return Verdict(True, "warning", f"检索索引{action}失败（{code}）",
                       {"ts": last.ts, "code": code,
                        "message": detail.get("message", "")})
    if last.status in ("up", "degraded"):
        return Verdict(False, summary=f"检索索引{action}成功",
                       evidence={"ts": last.ts, "status": last.status})
    return None
