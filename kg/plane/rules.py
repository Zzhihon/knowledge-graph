"""Anomaly rules: pure functions from a resource's recent signals to "should an incident be open".

A rule is registered per resource kind with `@rule(kind, name, window=N)`. It gets the latest
N signals (oldest first) and returns a `Verdict`: `firing=True` means an incident should be
open, `firing=False` means it should be closed, and `None` means "can't tell, keep what is".
The rule never touches the store; `evaluate` (live) and `replay` (tests, backtests on history)
step through signals one at a time and turn verdicts into open / close, so both give the same
times. Opening when one is already open, or closing when none is, does nothing.

Rules are registered when their module is imported. Put a kind's rules in its collector's
module under `kg/plane/collectors/` (or a module one of them imports): `load_all()` imports
every module there, so the rules are known even when that collector is not the one running.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kg.plane.store import Signal, close_incident, get_open_incident, list_signals, open_incident

if TYPE_CHECKING:
    import sqlite3


@dataclass(frozen=True)
class Verdict:
    firing: bool
    severity: str = "warning"
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


RuleFn = Callable[[Sequence[Signal]], Verdict | None]


@dataclass(frozen=True)
class Rule:
    kind: str
    name: str  # stored in incident.rule; like resource ids, never rename one that has fired
    fn: RuleFn
    window: int


@dataclass(frozen=True)
class Change:
    action: str  # "open" | "close"
    incident_id: int
    resource_id: str
    rule: str
    ts: int
    severity: str
    summary: str


_RULES: dict[str, Rule] = {}


def _origin(fn: RuleFn) -> tuple[str, str]:
    return fn.__module__, fn.__qualname__


def rule(kind: str, name: str, *, window: int) -> Callable[[RuleFn], RuleFn]:
    """Register `fn` as rule `name` for resources of `kind`; it sees the last `window` signals."""
    if window < 1:
        raise ValueError("window 至少是 1")

    def wrap(fn: RuleFn) -> RuleFn:
        existing = _RULES.get(name)
        # Re-importing the same module is fine; two different rules with one name is not.
        if existing and _origin(existing.fn) != _origin(fn):
            raise ValueError(f"规则名 {name} 已被 {existing.fn.__module__} 占用")
        _RULES[name] = Rule(kind, name, fn, window)
        return fn

    return wrap


def _ensure_loaded() -> None:
    from kg.plane.collectors import load_all

    load_all()


def rules_for(kind: str) -> list[Rule]:
    _ensure_loaded()
    return [r for r in _RULES.values() if r.kind == kind]


def all_rules() -> list[Rule]:
    _ensure_loaded()
    return list(_RULES.values())


def _step(r: Rule, history: Sequence[Signal], is_open: bool) -> tuple[str | None, Verdict | None]:
    verdict = r.fn(history[-r.window:])
    if verdict is None:
        return None, None
    if verdict.firing and not is_open:
        return "open", verdict
    if not verdict.firing and is_open:
        return "close", verdict
    return None, verdict


def replay(r: Rule, signals: Sequence[Signal]) -> list[tuple[str, int]]:
    """Run a rule over a whole history; returns [("open" | "close", ts), ...] in order."""
    ordered = sorted(signals, key=lambda s: s.ts)
    is_open = False
    events: list[tuple[str, int]] = []
    for i in range(len(ordered)):
        action, _ = _step(r, ordered[: i + 1], is_open)
        if action:
            is_open = action == "open"
            events.append((action, ordered[i].ts))
    return events


def evaluate(conn: sqlite3.Connection, new_signals: Mapping[str, int],
             kinds: Mapping[str, str]) -> list[Change]:
    """Apply every rule of each resource's kind to the signals just appended.

    `new_signals` is {resource id: how many signals this run appended}; `kinds` is
    {resource id: kind}. Each new signal is judged in turn, as `replay` would.
    """
    changes: list[Change] = []
    for rid, count in new_signals.items():
        rules = rules_for(kinds[rid]) if count else []
        if not rules:
            continue
        width = max(r.window for r in rules)
        history = list_signals(conn, rid, limit=width + count - 1)
        start = len(history) - count
        for r in rules:
            for i in range(start, len(history)):
                current = get_open_incident(conn, rid, r.name)
                action, verdict = _step(r, history[: i + 1], current is not None)
                if action is None or verdict is None:
                    continue
                ts = history[i].ts
                if action == "open":
                    iid = open_incident(conn, resource_id=rid, rule=r.name, opened_at=ts,
                                        severity=verdict.severity, summary=verdict.summary,
                                        evidence=verdict.evidence)
                    if iid is not None:
                        changes.append(Change("open", iid, rid, r.name, ts, verdict.severity,
                                              verdict.summary))
                else:
                    iid = close_incident(conn, resource_id=rid, rule=r.name, closed_at=ts,
                                         evidence=verdict.evidence)
                    if iid is not None and current is not None:
                        changes.append(Change("close", iid, rid, r.name, ts, current["severity"],
                                              verdict.summary or current["summary"]))
    return changes
