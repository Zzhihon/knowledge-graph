"""The control plane's state store: one SQLite file at `<vault>/.kg/plane/state.db` (gitignored).

Three tables answer "what is there" and "how has it been":

- `resource`  one row per thing (service, egress line, artifact, ...), keyed by a stable id
              (see `kg.plane.ids`). A resource a collector stops seeing gets `gone_at`; it is
              never deleted, because incidents and entries keep pointing at it.
- `signal`    its status over time. Raw rows have `samples = 0`; after RAW_DAYS they are folded
              into one row per resource and hour (`samples` = how many raw rows it stands for),
              and after KEEP_DAYS they are dropped.
- `incident`  what the anomaly rules opened and closed. At most one open incident per
              (resource, rule), enforced by a partial unique index, so re-running a rule is safe.

Times are integer Unix seconds. JSON columns are stored as text and returned parsed.
The connection is in autocommit mode; wrap several writes in `transaction()` to make them atomic.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kg.plane.ids import parse_id

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

STATUSES = ("up", "down", "degraded", "unknown", "asleep")
SEVERITIES = ("info", "warning", "critical")
RAW_DAYS = 30
KEEP_DAYS = 365
HOUR = 3600
DAY = 86400

# Worst first: breaks ties when an hour's rollup has as many of one status as another.
_WORST_FIRST = ("down", "degraded", "unknown", "asleep", "up")

_MIGRATIONS: tuple[str, ...] = (
    f"""
    CREATE TABLE resource(
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      name TEXT NOT NULL DEFAULT '',
      source TEXT NOT NULL,
      attrs TEXT NOT NULL DEFAULT '{{}}',
      first_seen INTEGER NOT NULL,
      last_seen INTEGER NOT NULL,
      gone_at INTEGER
    );
    CREATE INDEX resource_kind ON resource(kind);
    CREATE INDEX resource_source ON resource(source);

    CREATE TABLE signal(
      resource_id TEXT NOT NULL,
      ts INTEGER NOT NULL,
      status TEXT NOT NULL CHECK (status IN ({", ".join(f"'{s}'" for s in STATUSES)})),
      value REAL,
      unit TEXT,
      detail TEXT,
      samples INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX signal_resource_ts ON signal(resource_id, ts);
    CREATE INDEX signal_ts ON signal(ts);

    CREATE TABLE incident(
      id INTEGER PRIMARY KEY,
      resource_id TEXT NOT NULL,
      rule TEXT NOT NULL,
      opened_at INTEGER NOT NULL,
      closed_at INTEGER,
      severity TEXT NOT NULL,
      summary TEXT NOT NULL DEFAULT '',
      evidence TEXT,
      entry_id TEXT
    );
    CREATE UNIQUE INDEX incident_open_once ON incident(resource_id, rule) WHERE closed_at IS NULL;
    CREATE INDEX incident_resource ON incident(resource_id, opened_at);
    """,
)
SCHEMA_VERSION = len(_MIGRATIONS)


@dataclass(frozen=True)
class Resource:
    id: str
    kind: str
    name: str
    source: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Signal:
    resource_id: str
    ts: int
    status: str
    value: float | None = None
    unit: str | None = None
    detail: dict[str, Any] | None = None
    samples: int = 0  # 0 = a raw reading; N = an hourly rollup of N readings

    def to_dict(self) -> dict[str, Any]:
        return {"resource_id": self.resource_id, "ts": self.ts, "status": self.status,
                "value": self.value, "unit": self.unit, "detail": self.detail,
                "samples": self.samples}


@dataclass
class UpsertResult:
    new: list[str] = field(default_factory=list)  # first time seen
    returned: list[str] = field(default_factory=list)  # had gone_at, seen again


@dataclass
class CompactResult:
    folded: int = 0  # rows replaced by hourly rollups
    buckets: int = 0  # rollup rows written
    dropped: int = 0  # rows older than the keep period


def db_path(vault: Path) -> Path:
    return vault / ".kg" / "plane" / "state.db"


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(raw: str | None) -> Any:
    return None if raw is None else json.loads(raw)


def connect(path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) and migrate the store. `":memory:"` gives a throwaway one."""
    memory = str(path) == ":memory:"
    if not memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread off: FastAPI may open a request's connection in one threadpool thread and
    # use or close it in another. Each connection still serves one request at a time.
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    if not memory:
        # WAL: the API can read while a collector writes.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; the applied count is kept in PRAGMA user_version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"状态库版本 {version} 比代码认识的 {SCHEMA_VERSION} 新，先升级 kg")
    for number in range(version + 1, SCHEMA_VERSION + 1):
        # executescript commits first; the version bump is inside the same transaction.
        conn.executescript(
            f"BEGIN IMMEDIATE;\n{_MIGRATIONS[number - 1]}\nPRAGMA user_version = {number};\nCOMMIT;"
        )
    return SCHEMA_VERSION


@contextmanager
def transaction(conn: sqlite3.Connection, *,
                rollback: bool = False) -> Iterator[sqlite3.Connection]:
    """All writes inside commit together; `rollback=True` throws them away (dry runs)."""
    if conn.in_transaction:  # already inside one: let the outer block decide
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("ROLLBACK" if rollback else "COMMIT")


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else int(now)


# ── resources ──


def upsert_resources(conn: sqlite3.Connection, resources: Iterable[Resource], *,
                     now: int | None = None) -> UpsertResult:
    """Insert or refresh resources; seeing one again clears its gone_at."""
    now = _now(now)
    items = list({r.id: r for r in resources}.values())
    for r in items:
        kind, _ = parse_id(r.id)
        if kind != r.kind:
            raise ValueError(f"资源 {r.id} 的 kind 是 {r.kind}，和 id 前缀不一致")
    known = {
        row["id"]: row["gone_at"]
        for row in conn.execute(
            "SELECT id, gone_at FROM resource WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps([r.id for r in items]),),
        )
    }
    result = UpsertResult(
        new=[r.id for r in items if r.id not in known],
        returned=[r.id for r in items if r.id in known and known[r.id] is not None],
    )
    conn.executemany(
        """INSERT INTO resource(id, kind, name, source, attrs, first_seen, last_seen, gone_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
           ON CONFLICT(id) DO UPDATE SET name = excluded.name, source = excluded.source,
             attrs = excluded.attrs, last_seen = excluded.last_seen, gone_at = NULL""",
        [(r.id, r.kind, r.name, r.source, _dumps(r.attrs or {}), now, now) for r in items],
    )
    return result


def mark_gone(conn: sqlite3.Connection, source: str, seen: Iterable[str], *,
              now: int | None = None, prefixes: Iterable[str] | None = None) -> list[str]:
    """Set gone_at on this source's live resources that were not seen this run.

    `prefixes` limits it to ids the run fully covered (e.g. only `service:launchd:` when the
    port listing failed), so a partial run does not mark everything else gone.
    """
    now = _now(now)
    seen_ids = set(seen)
    scopes = tuple(prefixes) if prefixes is not None else None
    gone = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM resource WHERE source = ? AND gone_at IS NULL ORDER BY id", (source,)
        )
        if row["id"] not in seen_ids and (scopes is None or row["id"].startswith(scopes))
    ]
    conn.executemany("UPDATE resource SET gone_at = ? WHERE id = ?", [(now, rid) for rid in gone])
    return gone


def _resource_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["attrs"] = _loads(item["attrs"]) or {}
    return item


def get_resource(conn: sqlite3.Connection, resource_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM resource WHERE id = ?", (resource_id,)).fetchone()
    return _resource_dict(row) if row else None


def list_resources(conn: sqlite3.Connection, *, kind: str | None = None, source: str | None = None,
                   include_gone: bool = False) -> list[dict[str, Any]]:
    """Resources with their latest signal (`status`, `status_ts`, `value`, `unit`; None if none)."""
    where, args = [], []
    if kind:
        where.append("r.kind = ?")
        args.append(kind)
    if source:
        where.append("r.source = ?")
        args.append(source)
    if not include_gone:
        where.append("r.gone_at IS NULL")
    sql = """SELECT r.*, s.status AS status, s.ts AS status_ts, s.value AS value, s.unit AS unit
             FROM resource r
             LEFT JOIN signal s ON s.rowid = (
               SELECT rowid FROM signal WHERE resource_id = r.id
               ORDER BY ts DESC, rowid DESC LIMIT 1)"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [_resource_dict(row) for row in conn.execute(sql + " ORDER BY r.kind, r.id", args)]


# ── signals ──


def append_signals(conn: sqlite3.Connection, signals: Iterable[Signal]) -> int:
    rows = []
    for s in signals:
        if s.status not in STATUSES:
            raise ValueError(f"信号状态 {s.status!r} 不在 {STATUSES} 里")
        rows.append((s.resource_id, int(s.ts), s.status, s.value, s.unit, _dumps(s.detail),
                     s.samples))
    conn.executemany(
        "INSERT INTO signal(resource_id, ts, status, value, unit, detail, samples)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def _signal(row: sqlite3.Row) -> Signal:
    return Signal(row["resource_id"], row["ts"], row["status"], row["value"], row["unit"],
                  _loads(row["detail"]), row["samples"])


def list_signals(conn: sqlite3.Connection, resource_id: str, *, since: int | None = None,
                 until: int | None = None, limit: int | None = None) -> list[Signal]:
    """Signals of one resource, oldest first; with `limit`, the latest `limit` of them."""
    where, args = ["resource_id = ?"], [resource_id]
    if since is not None:
        where.append("ts >= ?")
        args.append(since)
    if until is not None:
        where.append("ts <= ?")
        args.append(until)
    sql = f"SELECT * FROM signal WHERE {' AND '.join(where)} ORDER BY ts DESC, rowid DESC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [_signal(row) for row in reversed(conn.execute(sql, args).fetchall())]


def _rollup(resource_id: str, bucket: int, rows: list[Signal]) -> Signal:
    statuses: Counter[str] = Counter()
    weighted, weight = 0.0, 0
    lows, highs = [], []
    unit = None
    for s in rows:
        n = s.samples or 1
        if s.samples and s.detail and "statuses" in s.detail:
            statuses.update(s.detail["statuses"])
        else:
            statuses[s.status] += n
        if s.value is not None:
            weighted += s.value * n
            weight += n
            lows.append((s.detail or {}).get("min", s.value) if s.samples else s.value)
            highs.append((s.detail or {}).get("max", s.value) if s.samples else s.value)
        unit = unit or s.unit
    top = max(statuses.values())
    status = next(st for st in _WORST_FIRST if statuses.get(st) == top)
    detail: dict[str, Any] = {"statuses": dict(sorted(statuses.items()))}
    if weight:
        detail.update(min=min(lows), max=max(highs))
    return Signal(resource_id, bucket, status, weighted / weight if weight else None, unit, detail,
                  sum(s.samples or 1 for s in rows))


def compact(conn: sqlite3.Connection, *, now: int | None = None, raw_days: int = RAW_DAYS,
            keep_days: int = KEEP_DAYS) -> CompactResult:
    """Fold raw signals older than `raw_days` into hourly rollups; drop rows older than `keep_days`.

    Only whole hours that ended before the cutoff are folded, so running it again (or every
    collect) changes nothing until another hour crosses the line. A rollup's status is the
    hour's most frequent status (ties go to the worse one); `detail.statuses` keeps the counts
    and `value` is the mean, with `detail.min` / `detail.max`.
    """
    now = _now(now)
    cutoff = now - raw_days * DAY
    cutoff -= cutoff % HOUR
    result = CompactResult()
    with transaction(conn):
        # Drop first, so rows past the keep period are not folded into a rollup that survives.
        result.dropped = conn.execute(
            "DELETE FROM signal WHERE ts < ?", (now - keep_days * DAY,)).rowcount
        buckets = conn.execute(
            "SELECT DISTINCT resource_id, ts - ts % 3600 AS bucket FROM signal"
            " WHERE samples = 0 AND ts < ? ORDER BY resource_id, bucket",
            (cutoff,),
        ).fetchall()
        for row in buckets:
            rid, bucket = row["resource_id"], row["bucket"]
            rows = [_signal(r) for r in conn.execute(
                "SELECT * FROM signal WHERE resource_id = ? AND ts >= ? AND ts < ? ORDER BY ts",
                (rid, bucket, bucket + HOUR))]
            conn.execute("DELETE FROM signal WHERE resource_id = ? AND ts >= ? AND ts < ?",
                         (rid, bucket, bucket + HOUR))
            append_signals(conn, [_rollup(rid, bucket, rows)])
            result.folded += len(rows)
            result.buckets += 1
    return result


# ── incidents ──


def _incident_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["evidence"] = _loads(item["evidence"])
    return item


def get_open_incident(conn: sqlite3.Connection, resource_id: str,
                      rule: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM incident WHERE resource_id = ? AND rule = ? AND closed_at IS NULL",
        (resource_id, rule),
    ).fetchone()
    return _incident_dict(row) if row else None


def open_incident(conn: sqlite3.Connection, *, resource_id: str, rule: str, opened_at: int,
                  severity: str = "warning", summary: str = "",
                  evidence: dict[str, Any] | None = None) -> int | None:
    """Open an incident; returns its id, or None when one is already open for (resource, rule)."""
    if severity not in SEVERITIES:
        raise ValueError(f"严重程度 {severity!r} 不在 {SEVERITIES} 里")
    if get_open_incident(conn, resource_id, rule):
        return None
    try:
        cur = conn.execute(
            "INSERT INTO incident(resource_id, rule, opened_at, severity, summary, evidence)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (resource_id, rule, int(opened_at), severity, summary, _dumps(evidence)),
        )
    except sqlite3.IntegrityError:  # another writer opened it in between
        return None
    return cur.lastrowid


def close_incident(conn: sqlite3.Connection, *, resource_id: str, rule: str, closed_at: int,
                   evidence: dict[str, Any] | None = None) -> int | None:
    """Close the open incident for (resource, rule); returns its id, or None if none was open.

    Closing evidence is added under `evidence["closed"]`, next to what opened it.
    """
    current = get_open_incident(conn, resource_id, rule)
    if current is None:
        return None
    merged = dict(current["evidence"] or {})
    if evidence is not None:
        merged["closed"] = evidence
    conn.execute("UPDATE incident SET closed_at = ?, evidence = ? WHERE id = ?",
                 (int(closed_at), _dumps(merged), current["id"]))
    return int(current["id"])


def list_incidents(conn: sqlite3.Connection, *, open_only: bool = False,
                   resource_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Incidents, newest first."""
    where, args = [], []
    if open_only:
        where.append("closed_at IS NULL")
    if resource_id:
        where.append("resource_id = ?")
        args.append(resource_id)
    sql = "SELECT * FROM incident"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY opened_at DESC, id DESC LIMIT {int(limit)}"
    return [_incident_dict(row) for row in conn.execute(sql, args)]
