"""`kg eval` handlers: mine candidate questions from sessions, score retrievers on the labels."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from kg.capture.scan import TZ, load_capture_config
from kg.eval.retrieval import (
    RETRIEVERS,
    bm25_retriever,
    current_retriever,
    eval_dir,
    load_cases,
    mine,
    score,
)
from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_eval_mine_command(*, days: int, limit: int, console: Console) -> None:
    vault = load_config().vault_path
    config = load_capture_config(vault)
    since = datetime.now(TZ) - timedelta(days=days)
    found = mine(config, since=since, limit=limit)
    out = eval_dir(vault) / "candidates.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for c in found:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    by_project: dict[str, int] = {}
    for c in found:
        by_project[c.project] = by_project.get(c.project, 0) + 1
    console.print(f"挖到 {len(found)} 个候选问题（最近 {days} 天），写到 {out}")
    console.print(
        "按项目："
        + "，".join(f"{p} {n}" for p, n in sorted(by_project.items(), key=lambda x: -x[1]))
    )


def run_eval_run_command(*, retriever: str, console: Console) -> None:
    vault = load_config().vault_path
    path = eval_dir(vault) / "retrieval.jsonl"
    if not path.exists():
        console.print(f"[red]还没有评测集：{path}[/]（先 kg eval mine，再标注应命中的条目）")
        sys.exit(1)
    cases = load_cases(path)
    names = RETRIEVERS if retriever == "all" else (retriever,)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    for name in names:
        closer = None
        if name == "bm25":
            run, closer = bm25_retriever()
        else:
            run = current_retriever()
        try:
            result = score(name, run, cases)
        finally:
            if closer:
                closer()
        report = eval_dir(vault) / f"report-{stamp}-{name}.json"
        report.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        console.print(
            f"{name:8s} 题数 {result.cases}  hit@1 {result.hit1:.2f}  "
            f"recall@5 {result.recall5:.2f}  "
            f"recall@10 {result.recall10:.2f}  MRR@10 {result.mrr10:.2f}  出错 {result.errors}  "
            f"{result.seconds}s  → {report.name}"
        )
