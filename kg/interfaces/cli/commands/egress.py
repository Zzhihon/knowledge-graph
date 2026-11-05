"""`kg plane egress replay`: run the egress rules over a probe ndjson and print what they decide.

Also runnable without registration: `python -m kg.interfaces.cli.commands.egress <file.ndjson>`.
Expected exit IPs come from `--expect` or the `KG_EGRESS_EXPECT` environment variable (comma
separated); without either, the probe's own `ip_ok` field is trusted.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from kg.plane.egress import iter_probe_file
from kg.plane.egress_rules import replay
from kg.plane.egress_sleep import SleepRules, parse_pmset_log

if TYPE_CHECKING:
    from rich.console import Console

TZ = ZoneInfo("Asia/Shanghai")


def _t(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M:%S") if ts else "未关闭"


def run_egress_replay_command(
    *,
    path: Path,
    expect: tuple[str, ...] = (),
    pmset_log: Path | None = None,
    interval: int | None = None,
    console: Console,
) -> None:
    if not expect:
        env = os.environ.get("KG_EGRESS_EXPECT", "")
        expect = tuple(x.strip() for x in env.split(",") if x.strip())
    events = parse_pmset_log(pmset_log.read_text(encoding="utf-8")) if pmset_log else []
    result = replay(
        list(iter_probe_file(path)),
        expected_ips=expect or None,
        power_events=events,
        sleep_rules=SleepRules(expected_interval_s=interval),
    )
    rows = result.classified
    if not rows:
        console.print(f"[red]没有读到探测记录：{path}[/]")
        return
    console.print(
        f"{len(rows)} 次探测  {_t(rows[0].obs.ts)} → {_t(rows[-1].obs.ts)}  "
        f"周期 {result.interval_s}s  期望出口 {'参数给定' if expect else '用记录里的 ip_ok'}"
        f"  休眠记录 {len(events)} 条"
    )
    console.print("\n[bold]唤醒后的探测[/]")
    for c in rows:
        if c.post_wake:
            gap = f"{c.gap_s / 60:.1f} 分钟" if c.gap_s is not None else "-"
            verdict = "断线起点（下一次也失败）" if c.promoted else c.status
            rc = c.obs.curl_rc if c.obs.curl_rc is not None else "-"
            console.print(f"  {_t(c.obs.ts)}  空档 {gap:>10}  rc={rc}  → {verdict}")
    console.print(f"\n[bold]事故 {len(result.incidents)} 个[/]")
    for inc in result.incidents:
        console.print(
            f"  \\[{inc.severity}] {inc.rule}  开始 {_t(inc.started_at)}  "
            f"开事故 {_t(inc.opened_at)}  关闭 {_t(inc.closed_at)}\n    {inc.summary}"
        )
    a = result.availability
    ratio = f"{a.ratio * 100:.2f}%" if a.ratio is not None else "-"
    left = a.budget_left_ratio
    console.print(
        f"\n[bold]可用性[/] {ratio}（目标 {a.target * 100:.1f}%，最近 30 天，排除休眠）  "
        f"正常 {a.good_s // 60} 分钟  断线 {a.bad_s // 60} 分钟  不计 {a.excluded_s // 60} 分钟  "
        f"错误预算 {a.budget_s / 60:.1f} 分钟，剩 "
        + (f"{left * 100:.0f}%" if left is not None else "-")
    )


def main(argv: list[str] | None = None) -> int:
    from rich.console import Console

    parser = argparse.ArgumentParser(description="回放出口探测记录，打印事故和可用性")
    parser.add_argument("path", type=Path)
    parser.add_argument("--expect", action="append", default=[], help="期望出口 IP，可重复")
    parser.add_argument("--pmset-log", type=Path, help="`pmset -g log` 的输出文件")
    parser.add_argument("--interval", type=int, help="探测周期（秒），默认按记录推断")
    args = parser.parse_args(argv)
    run_egress_replay_command(
        path=args.path,
        expect=tuple(args.expect),
        pmset_log=args.pmset_log,
        interval=args.interval,
        console=Console(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
