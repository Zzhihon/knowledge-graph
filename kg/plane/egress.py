"""Egress probe observations: parse probe records and locate which segment broke.

A probe of one egress line walks four segments, in order:

- `local_port`  TCP connect to the local proxy port      (down: the proxy process is gone)
- `proxy_api`   the proxy core's own control API          (down: core unhealthy, traffic may flow)
- `identity`    a trace request through the proxy         (down: upstream broken;
                                                           wrong exit: identity mismatch)
- `target`      credential-free HEAD/GET to the target API (down: target unreachable)

`assess()` turns the segment results into one verdict: a signal status
(up | down | degraded | unknown; `asleep` is added later by `kg.plane.egress_sleep`) and the
fault, i.e. where the line broke. Everything here is pure: no clock, no network, no database.

The legacy probe (one trace request every 5 minutes, one ndjson line per run) only exercised
the `identity` segment; `parse_probe_record()` maps its rows onto the same model, inferring
`local_port` from curl exit codes that mean "could not reach the proxy at all".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping
    from pathlib import Path

SEGMENTS = ("local_port", "proxy_api", "identity", "target")
STATUSES = ("up", "down", "degraded", "unknown", "asleep")

# Faults, i.e. "where it broke". Each maps to the segment that failed first.
PROXY_PROCESS = "proxy_process"
PROXY_API = "proxy_api"
UPSTREAM = "upstream"
IDENTITY_MISMATCH = "identity_mismatch"
TARGET = "target"

FAULT_LABELS = {
    PROXY_PROCESS: "本地代理进程不在，端口连不上",
    PROXY_API: "代理内核控制接口异常，流量可能仍通",
    UPSTREAM: "上游不通，代理在、出去的那段断了",
    IDENTITY_MISMATCH: "出口身份与期望不符",
    TARGET: "目标服务不可达",
}

# curl exit codes as seen when every request goes through `-x <local proxy>`.
# segment "local_port" means curl never got a connection to the proxy itself.
_CURL_RC: dict[int, tuple[str, str]] = {
    5: ("local_port", "代理地址解析失败（配置错误）"),
    7: ("local_port", "连不上本地代理端口"),
    6: ("identity", "代理侧解析目标域名失败"),
    28: ("identity", "超时"),
    35: ("identity", "经代理的 TLS 握手失败"),
    51: ("identity", "证书与域名不符（可能被劫持）"),
    52: ("identity", "代理返回空响应"),
    55: ("identity", "发送失败"),
    56: ("identity", "接收失败（代理拒绝 CONNECT 或上游断开）"),
    60: ("identity", "证书不受信任（可能被劫持）"),
    97: ("identity", "代理握手失败"),
}

# errno names from a raw TCP connect to the local proxy port.
_TCP_ERRORS = {
    "ECONNREFUSED": "端口没有进程监听",
    "ETIMEDOUT": "连接超时（进程挂起或被拦截）",
    "EHOSTUNREACH": "本机回环不可达",
    "ECONNRESET": "连接被重置",
}


@dataclass(frozen=True)
class SegmentResult:
    segment: str  # one of SEGMENTS
    status: str  # up | down | degraded | unknown
    reason: str = ""
    latency_ms: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    """One probe run of one egress line. `ts` is Unix seconds."""

    ts: int
    segments: tuple[SegmentResult, ...] = ()
    line: str = "default"
    exit_ip: str | None = None  # kept in memory and the local state store only
    ip_ok: bool | None = None  # the probe's own comparison, used when no expectation is given
    curl_rc: int | None = None
    tls_ms: int | None = None
    total_ms: int | None = None

    def segment(self, name: str) -> SegmentResult | None:
        for s in self.segments:
            if s.segment == name:
                return s
        return None


@dataclass(frozen=True)
class Verdict:
    status: str  # up | down | degraded | unknown
    fault: str | None = None
    reason: str = ""


# ── per-segment classifiers ───────────────────────────────────────────────


def classify_curl_rc(rc: int, *, segment: str = "identity") -> SegmentResult:
    """A curl exit code (request made through the local proxy) → the segment that failed."""
    if rc == 0:
        return SegmentResult(segment, "up")
    failed, reason = _CURL_RC.get(rc, (segment, f"curl 退出码 {rc}"))
    # A request to the target that cannot reach the proxy is still a local_port failure;
    # any other failure belongs to the segment that was being probed.
    if failed != "local_port":
        failed = segment
    return SegmentResult(failed, "down", reason, detail={"curl_rc": rc})


def classify_tcp(
    ok: bool, error: str | None = None, latency_ms: int | None = None
) -> SegmentResult:
    """A TCP connect to the local proxy port."""
    if ok:
        return SegmentResult("local_port", "up", latency_ms=latency_ms)
    name = (error or "").upper()
    reason = _TCP_ERRORS.get(name, f"连接失败 {error}" if error else "连接失败")
    return SegmentResult("local_port", "down", reason, detail={"error": name or None})


def classify_proxy_api(
    http_code: int | None, *, node: str | None = None, latency_ms: int | None = None
) -> SegmentResult:
    """The proxy core's control API. A failure here is degraded, not down: traffic may flow."""
    detail = {"http_code": http_code, "node": node}
    if http_code is None:
        return SegmentResult("proxy_api", "degraded", "控制接口无响应", latency_ms, detail)
    if 200 <= http_code < 300:
        return SegmentResult("proxy_api", "up", latency_ms=latency_ms, detail=detail)
    return SegmentResult("proxy_api", "degraded", f"控制接口返回 {http_code}", latency_ms, detail)


def classify_target(
    rc: int, http_code: int | None = None, latency_ms: int | None = None
) -> SegmentResult:
    """A credential-free request to the target API; any answer (401/404 too) means reachable."""
    if rc != 0:
        return classify_curl_rc(rc, segment="target")
    if http_code is None or http_code >= 500:
        return SegmentResult(
            "target", "down", f"目标返回 {http_code}", latency_ms, {"http_code": http_code}
        )
    return SegmentResult("target", "up", latency_ms=latency_ms, detail={"http_code": http_code})


# ── verdict ───────────────────────────────────────────────────────────────


def identity_ok(obs: Observation, expected_ips: Iterable[str] | None = None) -> bool | None:
    """Does the exit match? Expected values come from local config; fall back to the probe's own."""
    expected = set(expected_ips or ())
    if expected and obs.exit_ip is not None:
        return obs.exit_ip in expected
    return obs.ip_ok


def assess(obs: Observation, expected_ips: Iterable[str] | None = None) -> Verdict:
    """Where did this probe break? First failing segment wins, in probe order."""
    port = obs.segment("local_port")
    api = obs.segment("proxy_api")
    ident = obs.segment("identity")
    target = obs.segment("target")
    if port and port.status == "down":
        return Verdict("down", PROXY_PROCESS, port.reason)
    if ident and ident.status == "down":
        return Verdict("down", UPSTREAM, ident.reason)
    if ident and ident.status == "up" and identity_ok(obs, expected_ips) is False:
        return Verdict("down", IDENTITY_MISMATCH, FAULT_LABELS[IDENTITY_MISMATCH])
    if target and target.status == "down":
        return Verdict("down", TARGET, target.reason)
    if not any(s and s.status == "up" for s in (ident, target)):
        return Verdict("unknown", None, "没有数据面探测结果")
    if api and api.status != "up":
        return Verdict("degraded", PROXY_API, api.reason)
    return Verdict("up")


# ── legacy ndjson rows ────────────────────────────────────────────────────


def parse_ts(value: Any) -> int:
    """`2026-11-05T12:59:46+0800`, ISO 8601 with colon, or Unix seconds → Unix seconds."""
    if isinstance(value, int | float):
        return int(value)
    text = str(value)
    try:
        return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp())
    except ValueError:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            raise ValueError(f"timestamp without a timezone: {text}") from None
        return int(moment.timestamp())


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_probe_record(rec: Mapping[str, Any], *, line: str = "default") -> Observation:
    """One row of the legacy probe log → an Observation.

    Rows: `up` (trace answered 200: exit_ip, ip_ok, tls_ms, total_ms), `down` (curl failed:
    curl_rc), `bad` (trace answered something other than 200: code).
    """
    ts = parse_ts(rec["ts"])
    status = rec.get("status")
    rc = _int(rec.get("curl_rc"))
    tls_ms, total_ms = _int(rec.get("tls_ms")), _int(rec.get("total_ms"))
    if status == "up":
        seg = SegmentResult(
            "identity",
            "up",
            latency_ms=total_ms,
            detail={"tls_ms": tls_ms, "code": rec.get("code")},
        )
    elif status == "down":
        seg = classify_curl_rc(rc if rc is not None else -1)
    elif status == "bad":
        seg = SegmentResult(
            "identity", "down", f"trace 返回 {rec.get('code')}", detail={"code": rec.get("code")}
        )
    else:
        seg = SegmentResult("identity", "unknown", f"未知状态 {status}")
    ip_ok = rec.get("ip_ok")
    return Observation(
        ts=ts,
        segments=(seg,),
        line=line,
        exit_ip=rec.get("exit_ip"),
        ip_ok=ip_ok if isinstance(ip_ok, bool) else None,
        curl_rc=rc,
        tls_ms=tls_ms,
        total_ms=total_ms,
    )


def parse_probe_line(text: str, *, line: str = "default") -> Observation | None:
    """One ndjson line → an Observation; blank or unreadable lines → None."""
    text = text.strip()
    if not text:
        return None
    try:
        rec = json.loads(text)
        return parse_probe_record(rec, line=line)
    except (ValueError, KeyError, TypeError):
        return None


def parse_probe_lines(lines: Iterable[str], *, line: str = "default") -> list[Observation]:
    """ndjson lines → observations sorted by time (unreadable lines are dropped)."""
    found = [o for o in (parse_probe_line(t, line=line) for t in lines) if o is not None]
    return sorted(found, key=lambda o: o.ts)


def iter_probe_file(path: Path, *, line: str = "default") -> Iterator[Observation]:
    with path.open(encoding="utf-8") as f:
        yield from parse_probe_lines(f, line=line)
