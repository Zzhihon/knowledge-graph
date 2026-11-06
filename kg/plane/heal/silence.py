"""`.kg/plane/silence.json`: (rule, resource) pairs whose incidents get no card until a time.

"忽略一周" on a card writes one here. A silenced incident is still opened, closed, counted in
the weekly report and healed as configured; only the card and the recovery notice are skipped.
Expired entries are ignored and dropped on the next write.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from kg.plane.heal.config import plane_dir

if TYPE_CHECKING:
    from pathlib import Path


def silence_path(vault: Path) -> Path:
    return plane_dir(vault) / "silence.json"


def key(rule: str, resource_id: str) -> str:
    return f"{rule}|{resource_id}"


def _read(vault: Path) -> dict[str, dict[str, Any]]:
    file = silence_path(vault)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(vault: Path, data: dict[str, dict[str, Any]]) -> None:
    file = silence_path(vault)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, file)


def active(vault: Path, *, now: int) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in _read(vault).items() if int(v.get("until", 0)) > now}


def silenced(vault: Path, rule: str, resource_id: str, *, now: int) -> dict[str, Any] | None:
    """The silence covering this (rule, resource) right now, or None."""
    return active(vault, now=now).get(key(rule, resource_id))


def add(vault: Path, rule: str, resource_id: str, *, now: int, days: int, reason: str = "",
        incident: int | None = None) -> dict[str, Any]:
    data = active(vault, now=now)
    item = {"rule": rule, "resource": resource_id, "since": now, "until": now + days * 86400,
            "reason": reason, "incident": incident}
    data[key(rule, resource_id)] = item
    _write(vault, data)
    return item


def remove(vault: Path, rule: str, resource_id: str, *, now: int) -> bool:
    data = active(vault, now=now)
    found = data.pop(key(rule, resource_id), None) is not None
    _write(vault, data)
    return found
