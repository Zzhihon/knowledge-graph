"""Collector registry and the one place that writes a collection into the state store.

A collector is a class with a `name` and a `collect(ctx) -> Collection` method, registered
with `@register`. It only reads the machine (through `ctx.run` for commands, so tests can feed
it sample output) and returns what it saw; it never opens the store. `run_collector` then, in
one transaction: upserts the resources, marks this collector's unseen resources gone, appends
the signals, runs the anomaly rules on them, and folds old signals into hourly rollups.

Every module in this package is imported by `load_all()`, so a new collector only needs a
new file here; nothing else has to list it.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol, TypeVar

from kg.plane.store import (
    CompactResult,
    Resource,
    Signal,
    append_signals,
    compact,
    mark_gone,
    transaction,
    upsert_resources,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Sequence
    from pathlib import Path


class CommandError(RuntimeError):
    """A command a collector needs could not run (missing, timed out, failed)."""


def run_command(argv: Sequence[str], timeout: float = 20) -> str:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CommandError(f"{argv[0]}: {exc}") from exc
    if done.returncode != 0 and not done.stdout:
        raise CommandError(f"{argv[0]} 退出码 {done.returncode}: {done.stderr.strip()[:200]}")
    return done.stdout


@dataclass(frozen=True)
class CollectContext:
    now: int
    vault: Path | None = None
    run: Callable[..., str] = run_command  # run(argv, timeout=...) -> stdout


@dataclass
class Collection:
    resources: list[Resource] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    # Id prefixes this run fully covered; resources outside them are not marked gone.
    # None means the run covered everything this collector owns.
    scopes: tuple[str, ...] | None = None
    notes: list[str] = field(default_factory=list)  # what went wrong but did not stop the run


class Collector(Protocol):
    name: str

    def collect(self, ctx: CollectContext) -> Collection: ...


C = TypeVar("C", bound=type)
_REGISTRY: dict[str, type] = {}
_loaded = False


def register(cls: C) -> C:
    name = getattr(cls, "name", "")
    if not name:
        raise ValueError(f"{cls.__name__} 没有 name")
    _REGISTRY[name] = cls
    return cls


def load_all() -> None:
    """Import every collector module in this package (which also registers their rules)."""
    global _loaded
    if _loaded:
        return
    for module in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{module.name}")
    _loaded = True


def collector_names() -> list[str]:
    load_all()
    return sorted(_REGISTRY)


def get_collector(name: str) -> Collector:
    load_all()
    if name not in _REGISTRY:
        raise KeyError(f"没有叫 {name} 的采集器，可选：{', '.join(sorted(_REGISTRY)) or '无'}")
    return _REGISTRY[name]()


@dataclass
class RunResult:
    collector: str
    now: int
    dry_run: bool
    resources: int = 0
    signals: int = 0
    statuses: dict[str, int] = field(default_factory=dict)  # latest status per resource, counted
    new: list[str] = field(default_factory=list)
    returned: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    changes: list = field(default_factory=list)  # list[kg.plane.rules.Change]
    compacted: CompactResult | None = None
    notes: list[str] = field(default_factory=list)


def _close_gone(conn: sqlite3.Connection, gone: Sequence[str], now: int) -> list:
    """A resource that disappeared gets no more signals, so no rule could ever close its open
    incidents (an exposed dev server that was stopped, say). Close them here, saying why."""
    from kg.plane.rules import Change
    from kg.plane.store import close_incident, list_incidents

    changes = []
    for rid in gone:
        for incident in list_incidents(conn, open_only=True, resource_id=rid):
            iid = close_incident(conn, resource_id=rid, rule=incident["rule"], closed_at=now,
                                 evidence={"ts": now, "reason": "gone"})
            if iid is not None:
                changes.append(Change("close", iid, rid, incident["rule"], now,
                                      incident["severity"], "资源已消失"))
    return changes


def run_collector(conn: sqlite3.Connection, collector: Collector, *,
                  ctx: CollectContext | None = None, dry_run: bool = False,
                  compact_after: bool = True) -> RunResult:
    """Collect once and write it all in one transaction; `dry_run` rolls it back at the end."""
    from kg.plane.rules import evaluate

    ctx = ctx or CollectContext(now=int(time.time()))
    collection = collector.collect(ctx)
    # The framework owns `source`: gone_at is decided per collector.
    resources = [replace(r, source=collector.name) for r in collection.resources]
    kinds = {r.id: r.kind for r in resources}
    stray = sorted({s.resource_id for s in collection.signals} - kinds.keys())
    if stray:
        raise ValueError(f"{collector.name} 给没上报的资源写了信号：{', '.join(stray[:5])}")
    latest: dict[str, Signal] = {}
    for s in sorted(collection.signals, key=lambda s: s.ts):
        latest[s.resource_id] = s
    result = RunResult(collector.name, ctx.now, dry_run, resources=len(resources),
                       signals=len(collection.signals),
                       statuses=dict(Counter(s.status for s in latest.values())),
                       notes=list(collection.notes))
    with transaction(conn, rollback=dry_run):
        upserted = upsert_resources(conn, resources, now=ctx.now)
        result.new, result.returned = upserted.new, upserted.returned
        result.gone = mark_gone(conn, collector.name, kinds, now=ctx.now,
                                prefixes=collection.scopes)
        append_signals(conn, sorted(collection.signals, key=lambda s: s.ts))
        result.changes = evaluate(conn, Counter(s.resource_id for s in collection.signals), kinds)
        result.changes += _close_gone(conn, result.gone, ctx.now)
        if compact_after:
            result.compacted = compact(conn, now=ctx.now)
    return result
