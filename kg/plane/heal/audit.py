"""`.kg/plane/heal-audit.jsonl`: one line per action taken or suggested, and how to undo it.

An entry is never rewritten. Undoing appends a new entry with `undo_of`; whether an action is
still in effect is worked out by reading the file, so a crash between two writes cannot leave
the log saying something that did not happen.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from kg.plane.heal.config import plane_dir

if TYPE_CHECKING:
    from pathlib import Path

# Results.
DONE = "done"  # the action ran
SUGGESTED = "suggested"  # mode was suggest: written on the card, not run
SUGGEST_ONLY = "suggest-only"  # mode was auto but the hub is too old: fell back to suggesting
SKIPPED = "skipped"  # not applicable (rate limit, nothing to pause, ...)
FAILED = "failed"


def audit_path(vault: Path) -> Path:
    return plane_dir(vault) / "heal-audit.jsonl"


@dataclass
class AuditEntry:
    id: str
    at: int
    incident: int | None
    action: str  # pause_capability, resume_capability, run_rule, kickstart, index_stale, silence...
    mode: str  # auto | suggest | owner (from a card answer or the command line)
    result: str
    target: str = ""  # capability, rule, launchd label, ...
    detail: dict[str, Any] = field(default_factory=dict)
    undo: str = ""  # how to undo it, in words; empty when it cannot be undone
    undo_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def new_id(now: int) -> str:
    return f"h{now}-{secrets.token_hex(2)}"


def append(vault: Path, entry: AuditEntry) -> AuditEntry:
    file = audit_path(vault)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def record(vault: Path, *, now: int, incident: int | None, action: str, mode: str, result: str,
           target: str = "", detail: dict[str, Any] | None = None, undo: str = "",
           undo_of: str | None = None) -> AuditEntry:
    entry_id = new_id(now)
    # `undo` may say "kg plane heal undo <id>" before the id exists.
    return append(vault, AuditEntry(entry_id, now, incident, action, mode, result, target,
                                    detail or {}, undo.replace("<id>", entry_id), undo_of))


def read(vault: Path) -> list[AuditEntry]:
    file = audit_path(vault)
    if not file.is_file():
        return []
    out = []
    for line in file.read_text(encoding="utf-8").splitlines():
        try:
            raw = json.loads(line)
            out.append(AuditEntry(**raw))
        except (ValueError, TypeError):
            continue
    return out


def undone_ids(entries: list[AuditEntry]) -> set[str]:
    return {e.undo_of for e in entries if e.undo_of and e.result == DONE}


def find(vault: Path, audit_id: str) -> AuditEntry | None:
    return next((e for e in read(vault) if e.id == audit_id), None)


def for_incident(vault: Path, incident: int) -> list[AuditEntry]:
    return [e for e in read(vault) if e.incident == incident]


def active(vault: Path, incident: int, action: str) -> list[AuditEntry]:
    """This incident's `action` entries that ran and were not undone yet."""
    entries = read(vault)
    undone = undone_ids(entries)
    return [e for e in entries if e.incident == incident and e.action == action
            and e.result == DONE and e.id not in undone and not e.undo_of]
