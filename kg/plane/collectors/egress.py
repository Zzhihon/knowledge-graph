"""Egress lines: segment-by-segment probe every minute, purity checks hourly.

Config: `<vault>/.kg/plane/egress.yaml` (template `egress.yaml.example`; `KG_EGRESS_CONFIG`
points at another file). Without it the collector reports nothing and touches nothing.

Per line and run:

1. Probe local port, proxy controller, trace, target hosts (`kg.plane.egress_probe`).
2. A failure right after a wake (machine woke within `wake_grace_s`, from `kern.waketime`, or
   the gap since the last probe is beyond 2.5 intervals) is probed once more after
   `recheck_delay_s` (60–90 s) in the same run. Both probes become signals; the rules decide
   from the pair whether it was a wake-up blip or the start of an outage.
3. Purity checks run when `purity.every_s` has passed or the exit IP changed.

Resources: `egress:<line>` (probe signals, rules `egress.outage` / `egress.identity`) and
`egress:<line>:purity` (purity signals, rule `egress.purity`). The rules live in
`kg.plane.egress_signals`, imported here so `load_all()` registers them.

A small cache next to the config (`egress-cache.json`) keeps the last probe time and exit IP per
line, the purity results with their check times, and the IP profile lookups. It is local state,
never committed; the collector does not read the state store.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kg.plane.collectors import CollectContext, Collection, CommandError, register
from kg.plane.egress import IDENTITY_MISMATCH, assess
from kg.plane.egress_probe import (
    RealTransport,
    Transport,
    last_wake,
    load_config,
    probe_line,
)
from kg.plane.egress_purity import CHECKS, overall, run_purity
from kg.plane.egress_signals import (
    GAP_FACTOR,
    PURITY_KIND,
    line_id,
    observation_to_signal,
    purity_id,
)
from kg.plane.egress_sleep import parse_pmset_log
from kg.plane.store import Resource, Signal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from kg.plane.egress_probe import EgressConfig, LineConfig, ProbeResult


def read_cache(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def post_wake(ts: int, *, last_probe: int | None, woke_at: int | None, cfg: EgressConfig) -> bool:
    if last_probe is not None and ts - last_probe > GAP_FACTOR * cfg.interval_s:
        return True
    return woke_at is not None and 0 <= ts - woke_at <= cfg.wake_grace_s


def _failed(result: ProbeResult) -> bool:
    verdict = assess(result.obs)
    return verdict.status == "down" and verdict.fault != IDENTITY_MISMATCH


def _pmset_wake(run: Callable[..., str]) -> int | None:
    try:
        events = parse_pmset_log(run(["pmset", "-g", "log"], timeout=30))
    except CommandError:
        return None
    wakes = [e.ts for e in events if e.kind in ("wake", "darkwake")]
    return wakes[-1] if wakes else None


@dataclass
class EgressCollector:
    name = "egress"
    transport: Transport = field(default_factory=RealTransport)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time

    def collect(self, ctx: CollectContext) -> Collection:
        out = Collection()
        cfg = load_config(ctx.vault)
        if cfg is None or not cfg.lines:
            out.scopes = ()  # nothing looked at: mark nothing gone
            out.notes.append("没有 .kg/plane/egress.yaml（模板 egress.yaml.example），跳过")
            return out
        cache = read_cache(cfg.cache_path)
        lines_cache: dict[str, dict[str, Any]] = cache.setdefault("lines", {})
        woke_at: int | None = None
        woke_read = False

        def wake() -> int | None:
            nonlocal woke_at, woke_read
            if not woke_read:
                woke_read = True
                woke_at = last_wake(ctx.run)
                if cfg.pmset:
                    pm = _pmset_wake(ctx.run)
                    woke_at = max(filter(None, (woke_at, pm)), default=None)
            return woke_at

        first: dict[str, ProbeResult] = {}
        for line in cfg.lines:
            first[line.name] = probe_line(line, self.transport, now=ctx.now)
        recheck = [
            line for line in cfg.lines
            if _failed(first[line.name])
            and post_wake(ctx.now, last_probe=lines_cache.get(line.name, {}).get("last_probe"),
                          woke_at=wake(), cfg=cfg)
        ]
        second: dict[str, ProbeResult] = {}
        if recheck:
            self.sleep(cfg.recheck_delay_s)
            at = max(int(self.clock()), ctx.now + 1)
            for line in recheck:
                second[line.name] = probe_line(line, self.transport, now=at)

        for line in cfg.lines:
            self._line(out, ctx, cfg, line, first[line.name], second.get(line.name),
                       lines_cache.setdefault(line.name, {}), cache, wake)
        write_cache(cfg.cache_path, cache)
        return out

    def _line(self, out: Collection, ctx: CollectContext, cfg: EgressConfig, line: LineConfig,
              first: ProbeResult, second: ProbeResult | None, state: dict[str, Any],
              cache: dict[str, Any], wake: Callable[[], int | None]) -> None:
        rid = line_id(line.name)
        out.resources.append(Resource(rid, "egress", line.name, self.name, {
            "proxy": line.proxy, "trace_url": line.trace_url,
            "targets": list(line.target_hosts), "controller": bool(line.controller),
            "expect": "ips" if line.expect_ips else "country" if line.expect_country else None,
            "interval_s": cfg.interval_s, "availability_target": cfg.availability_target,
        }))
        woke = wake() if _failed(first) else None
        probes = [(first, second is not None), (second, False)] if second else [(first, False)]
        last_exit = state.get("last_exit_ip")
        exit_ip, exit_country = None, None
        for result, rechecking in probes:
            if result is None:
                continue
            # The collector's best guess, written as status; the rules re-classify anyway.
            status = "asleep" if rechecking else None
            extra = dict(result.extra)
            if rechecking:
                extra["recheck_after_s"] = cfg.recheck_delay_s
            if result is second:
                extra["recheck_of"] = first.obs.ts
            out.signals.append(observation_to_signal(
                result.obs, resource_id=rid, interval_s=cfg.interval_s, status=status,
                woke_at=woke, extra=extra))
            if result.obs.exit_ip:
                exit_ip, exit_country = result.obs.exit_ip, result.extra.get("loc")
        state["last_probe"] = (second or first).obs.ts
        if exit_ip:
            state["last_exit_ip"] = exit_ip

        if not cfg.purity.enabled:
            return
        due = ctx.now - int(state.get("purity_at") or 0) >= cfg.purity.every_s
        changed = bool(exit_ip and last_exit and exit_ip != last_exit)
        prid = purity_id(line.name)
        out.resources.append(Resource(prid, "egress", f"{line.name} 纯净度", self.name,
                                      {"line": line.name, "every_s": cfg.purity.every_s}))
        if not (due or changed) or not exit_ip:
            return  # keep the resource alive without a new reading
        checks = run_purity(line, cfg.purity, self.transport, ctx.run, now=ctx.now,
                            exit_ip=exit_ip, exit_country=exit_country, cache=cache,
                            baseline=state.get("baseline_profile"))
        profile = checks.get("ip_profile", {}).get("detail", {}).get("profile")
        if profile and not state.get("baseline_profile"):
            state["baseline_profile"] = profile
        state["purity_at"] = ctx.now
        state["purity"] = {**(state.get("purity") or {}), **checks}
        out.signals.append(Signal(prid, ctx.now, overall(checks), None, None, {
            "kind": PURITY_KIND, "reason": "exit_changed" if changed else "hourly",
            "checks": {k: checks[k] for k in CHECKS if k in checks},
        }))


register(EgressCollector)
