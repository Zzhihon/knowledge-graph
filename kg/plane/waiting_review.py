"""Check the "waiting for you" judgement against people, on real sessions kept on this machine.

    python -m kg.plane.waiting_review export [--size 20] [--seed 5] [--days N]
                                             [--exclude <earlier.jsonl>]
    python -m kg.plane.waiting_review score [--field human|model]

`export` takes the sessions the board would show right now (same reading and filters as
`kg plane collect sessions`), draws a sample with as many judged "waiting" as "not waiting"
(so both kinds of mistake show up), and writes `<vault>/.kg/plane/waiting-review.jsonl`
(gitignored), one session per line: the judgement, why, and the redacted excerpts it was made
from, plus the command to open the session. A reviewer fills `human` with true / false on each
line (`note` is free text). `score` reports agreement of `human` (or `model`) with `predicted`.

What "waiting" means: the agent's last move asks you to answer, choose, confirm, approve or do
something, and the work will not go on until you do. An offer made after the work is finished
("要的话我可以再…，跟我说") is not waiting; a direct question at the very end is.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

FILE = "waiting-review.jsonl"


def review_path(vault: Path) -> Path:
    return vault / ".kg" / "plane" / FILE


def sample(sessions: list[dict[str, Any]], size: int, seed: int) -> list[dict[str, Any]]:
    """Up to `size` sessions, half judged waiting and half not when there are enough of each."""
    rng = random.Random(seed)
    waiting = [s for s in sessions if s["state"] == "waiting"]
    other = [s for s in sessions if s["state"] != "waiting"]
    rng.shuffle(waiting)
    rng.shuffle(other)
    take = min(len(waiting), size // 2)
    picked = waiting[:take] + other[:size - take]
    if len(picked) < size:
        picked += waiting[take:take + size - len(picked)]
    return sorted(picked, key=lambda s: -s["last_active"])


def review_line(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": f"{s['source']}:{s['session_id']}", "project": s["project"],
        "predicted": s["state"] == "waiting", "state": s["state"],
        "reason": s.get("waiting_reason", ""), "last_event": s["last_event"],
        "last_tool": s["last_tool"], "turns": s["turns"],
        "last_active": time.strftime("%m-%d %H:%M", time.localtime(s["last_active"])),
        "last_user": s["last_user"], "last_agent_head": s["last_agent"],
        "last_agent_tail": s["last_agent_tail"], "resume": s["resume"],
        "human": None, "model": None, "note": "",
    }


def score(lines: list[dict[str, Any]], field: str) -> dict[str, Any]:
    judged = [x for x in lines if isinstance(x.get(field), bool)]
    agree = [x for x in judged if x[field] == x["predicted"]]
    confusion = {
        "both_waiting": sum(1 for x in judged if x["predicted"] and x[field]),
        "both_not": sum(1 for x in judged if not x["predicted"] and not x[field]),
        "false_waiting": sum(1 for x in judged if x["predicted"] and not x[field]),
        "missed_waiting": sum(1 for x in judged if not x["predicted"] and x[field]),
    }
    return {"field": field, "judged": len(judged), "of": len(lines), "agree": len(agree),
            "accuracy": round(len(agree) / len(judged), 3) if judged else None,
            **confusion,
            "disagreements": [x["key"] for x in judged if x[field] != x["predicted"]]}


def _vault() -> Path:
    from kg.infrastructure.config.loader import load_config

    return load_config().vault_path


def export(vault: Path, size: int, seed: int, exclude: set[str] | None = None,
           days: int | None = None) -> Path:
    from kg.capture.scan import load_capture_config
    from kg.plane.collectors.sessions import (
        board_sessions,
        cursor_path,
        load_settings,
        read_sessions,
    )
    from kg.plane.session_reader import load_cursor, save_cursor

    config = load_capture_config(vault)
    settings = load_settings(vault)
    if days:
        settings.days = days  # a wider pool for a held-out round; the board keeps its window
    now = int(time.time())
    cursor = load_cursor(cursor_path(vault))
    digests = read_sessions(config, cursor, now=now, days=settings.days, notes=[])
    save_cursor(cursor_path(vault), cursor)
    sessions, _ = board_sessions(config, digests, now=now, settings=settings)
    # A held-out round: sessions already reviewed (and maybe tuned on) are left out.
    sessions = [s for s in sessions if f"{s['source']}:{s['session_id']}" not in (exclude or set())]
    path = review_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    picked = sample(sessions, size, seed)
    path.write_text("".join(json.dumps(review_line(s), ensure_ascii=False) + "\n"
                            for s in picked), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kg.plane.waiting_review")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="抽样导出待人工核对的会话")
    ex.add_argument("--size", type=int, default=20)
    ex.add_argument("--seed", type=int, default=5)
    ex.add_argument("--days", type=int, default=None, help="抽样范围（天），默认跟看板一样")
    ex.add_argument("--exclude", action="append", default=[],
                    help="之前一轮的审核文件；里面的会话不再抽")
    sc = sub.add_parser("score", help="统计人工（或模型）判断和程序判断的一致率")
    sc.add_argument("--field", choices=["human", "model"], default="human")
    args = parser.parse_args(argv)
    vault = _vault()
    if args.cmd == "export":
        done = set()
        for earlier in args.exclude:
            with open(earlier, encoding="utf-8") as f:
                done |= {json.loads(line)["key"] for line in f if line.strip()}
        path = export(vault, args.size, args.seed, done, args.days)
        print(f"写好了 {path}：逐行把 human 改成 true（在等你）或 false（不在等你），"
              "再运行 python -m kg.plane.waiting_review score")
        return 0
    path = review_path(vault)
    if not path.exists():
        print(f"还没有 {path}，先运行 export", file=sys.stderr)
        return 1
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    print(json.dumps(score(lines, args.field), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
