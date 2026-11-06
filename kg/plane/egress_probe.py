"""Live probe of one egress line, segment by segment, and its local config.

    local_port   TCP connect to the local proxy                   (no network beyond loopback)
    proxy_api    GET <controller>/proxies/<group>                 (loopback only; may carry the
                                                                   controller's own secret)
    identity     curl -x <proxy> <trace url>                      exit IP / location / colo
    target       curl -x <proxy> -I https://<target host>/        any answer below 500 = reachable

Every request that leaves the machine goes through the configured local proxy with `curl -q`
(no ~/.curlrc), a clean environment (no *_proxy variables, no netrc) and no header options
beyond a fixed User-Agent: no Authorization, no Cookie, no API key, ever. `curl_argv()` is the
only place that builds those commands, and the tests check what it sends.

The controller secret is only ever sent to a loopback controller URL; anything else is refused.
Config lives in `<vault>/.kg/plane/egress.yaml` (or the file named by `KG_EGRESS_CONFIG`); see
`egress.yaml.example`. Expected exit IPs stay in that local file.
"""

from __future__ import annotations

import errno
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import yaml

from kg.plane.egress import (
    Observation,
    SegmentResult,
    classify_curl_rc,
    classify_proxy_api,
    classify_target,
    classify_tcp,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

CONFIG_ENV = "KG_EGRESS_CONFIG"
DEFAULT_TRACE = "https://www.cloudflare.com/cdn-cgi/trace"
USER_AGENT = "kg-plane-egress/1"
_MARK = "\n__KG_PROBE__"
_WRITE_OUT = _MARK + "%{exitcode}\t%{http_code}\t%{time_connect}\t%{time_appconnect}\t%{time_total}"
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
# Environment curl runs with: nothing that could add a proxy, a header or credentials.
_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TMPDIR")


@dataclass(frozen=True)
class Controller:
    url: str  # http://127.0.0.1:<port>
    group: str = "GLOBAL"
    secret_env: str | None = None
    secret_file: str | None = None

    def secret(self) -> str | None:
        if self.secret_env and os.environ.get(self.secret_env):
            return os.environ[self.secret_env]
        if self.secret_file:
            path = Path(self.secret_file).expanduser()
            if path.exists():
                return path.read_text(encoding="utf-8").strip() or None
        return None


@dataclass(frozen=True)
class LineConfig:
    name: str
    proxy: str  # http://127.0.0.1:<port> — every outside request goes through it
    trace_url: str = DEFAULT_TRACE
    expect_ips: tuple[str, ...] = ()
    expect_country: str | None = None
    target_hosts: tuple[str, ...] = ()
    controller: Controller | None = None

    @property
    def proxy_host_port(self) -> tuple[str, int]:
        u = urllib.parse.urlsplit(self.proxy if "://" in self.proxy else f"http://{self.proxy}")
        return (u.hostname or "127.0.0.1", u.port or (443 if u.scheme == "https" else 80))


@dataclass(frozen=True)
class PurityConfig:
    enabled: bool = True
    every_s: int = 3600
    profile_url: str = (
        "http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,regionName,"
        "city,timezone,isp,org,as,proxy,hosting,query"
    )
    profile_ttl_s: int = 86400
    dns_leak: bool = True
    dns_leak_base: str = "bash.ws"
    direct_leak_processes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EgressConfig:
    lines: tuple[LineConfig, ...]
    interval_s: int = 60
    availability_target: float = 0.995
    recheck_delay_s: int = 75
    wake_grace_s: int = 120
    pmset: bool = False
    purity: PurityConfig = field(default_factory=PurityConfig)
    path: Path | None = None  # where it was read from; the cache sits next to it

    @property
    def cache_path(self) -> Path | None:
        return self.path.parent / "egress-cache.json" if self.path else None


def config_path(vault: Path | None) -> Path | None:
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env).expanduser()
    return vault / ".kg" / "plane" / "egress.yaml" if vault else None


def _tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    return tuple(str(x) for x in raw)


def parse_config(raw: Mapping[str, Any], path: Path | None = None) -> EgressConfig:
    lines = []
    for item in raw.get("lines") or []:
        ctl = item.get("controller")
        controller = None
        if ctl and ctl.get("url"):
            controller = Controller(str(ctl["url"]).rstrip("/"), str(ctl.get("group") or "GLOBAL"),
                                    ctl.get("secret_env"), ctl.get("secret_file"))
        expect = _tuple(item.get("expect_ips"))
        if item.get("expect_ips_env"):
            expect += _tuple(os.environ.get(str(item["expect_ips_env"]), ""))
        lines.append(LineConfig(
            name=str(item["name"]),
            proxy=str(item["proxy"]),
            trace_url=str(item.get("trace_url") or DEFAULT_TRACE),
            expect_ips=expect,
            expect_country=item.get("expect_country"),
            target_hosts=_tuple(item.get("target_hosts")),
            controller=controller,
        ))
    p = raw.get("purity") or {}
    default = PurityConfig()
    purity = PurityConfig(
        enabled=bool(p.get("enabled", True)),
        every_s=int(p.get("every_s", default.every_s)),
        profile_url=str(p.get("profile_url") or default.profile_url),
        profile_ttl_s=int(p.get("profile_ttl_s", default.profile_ttl_s)),
        dns_leak=bool(p.get("dns_leak", True)),
        dns_leak_base=str(p.get("dns_leak_base") or default.dns_leak_base),
        direct_leak_processes=_tuple((p.get("direct_leak") or {}).get("processes")),
    )
    return EgressConfig(
        lines=tuple(lines),
        interval_s=int(raw.get("interval_s", 60)),
        availability_target=float(raw.get("availability_target", 0.995)),
        recheck_delay_s=int(raw.get("recheck_delay_s", 75)),
        wake_grace_s=int(raw.get("wake_grace_s", 120)),
        pmset=bool(raw.get("pmset", False)),
        purity=purity,
        path=path,
    )


def load_config(vault: Path | None) -> EgressConfig | None:
    path = config_path(vault)
    if path is None or not path.exists():
        return None
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path)


# ── transports (injectable) ───────────────────────────────────────────────


def curl_argv(proxy: str, url: str, *, head: bool = False, timeout_s: int = 10) -> list[str]:
    """The only command line used for requests that leave the machine. No credentials."""
    argv = ["curl", "-q", "-sS", "--proxy", proxy, "--connect-timeout", str(min(5, timeout_s)),
            "--max-time", str(timeout_s), "--proto", "=http,https", "-A", USER_AGENT]
    if head:
        argv += ["-I", "-o", "/dev/null"]
    return [*argv, "-w", _WRITE_OUT, url]


def clean_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env["HOME"] = os.environ.get("TMPDIR", "/tmp")  # no ~/.netrc, no ~/.curlrc
    return env


@dataclass(frozen=True)
class CurlResult:
    rc: int
    http_code: int | None
    body: str
    connect_ms: int | None = None
    tls_ms: int | None = None
    total_ms: int | None = None


def _ms(raw: str) -> int | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(round(value * 1000)) if value > 0 else None


def parse_curl_output(stdout: str, returncode: int) -> CurlResult:
    body, _, tail = stdout.rpartition(_MARK)
    if not _:
        return CurlResult(returncode or -1, None, stdout)
    cols = tail.strip().split("\t")
    cols += [""] * (5 - len(cols))
    try:
        rc = int(cols[0])
    except ValueError:
        rc = returncode
    code = int(cols[1]) if cols[1].isdigit() and int(cols[1]) > 0 else None
    return CurlResult(rc, code, body, _ms(cols[2]), _ms(cols[3]), _ms(cols[4]))


def run_curl(argv: Sequence[str], timeout: float = 20) -> CurlResult:
    try:
        done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout,
                              check=False, env=clean_env())
    except subprocess.TimeoutExpired:
        return CurlResult(28, None, "")
    except OSError:
        return CurlResult(-1, None, "")
    return parse_curl_output(done.stdout, done.returncode)


def tcp_connect(host: str, port: int, timeout: float = 3) -> tuple[bool, str | None, int | None]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None, int((time.monotonic() - start) * 1000)
    except TimeoutError:
        return False, "ETIMEDOUT", None
    except OSError as exc:
        name = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
        return False, name, None


def is_loopback_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def local_get(url: str, *, secret: str | None = None, timeout: float = 3
              ) -> tuple[int | None, Any, int | None]:
    """GET a loopback control API without any proxy. Returns (http code, parsed JSON, ms)."""
    if not is_loopback_url(url):
        raise ValueError("代理内核控制接口只能是本机回环地址")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if secret:
        req.add_header("Authorization", f"Bearer {secret}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    start = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, None, int((time.monotonic() - start) * 1000)
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, None, None
    ms = int((time.monotonic() - start) * 1000)
    try:
        return code, json.loads(body or b"null"), ms
    except ValueError:
        return code, None, ms


class Transport(Protocol):
    def tcp(self, host: str, port: int) -> tuple[bool, str | None, int | None]: ...
    def curl(self, argv: Sequence[str]) -> CurlResult: ...
    def local_get(self, url: str, secret: str | None) -> tuple[int | None, Any, int | None]: ...


@dataclass
class RealTransport:
    tcp_timeout: float = 3

    def tcp(self, host: str, port: int) -> tuple[bool, str | None, int | None]:
        return tcp_connect(host, port, self.tcp_timeout)

    def curl(self, argv: Sequence[str]) -> CurlResult:
        return run_curl(argv)

    def local_get(self, url: str, secret: str | None) -> tuple[int | None, Any, int | None]:
        return local_get(url, secret=secret)


# ── segments ──────────────────────────────────────────────────────────────


def parse_trace(body: str) -> dict[str, str]:
    """`key=value` lines of a Cloudflare trace."""
    out = {}
    for raw in body.splitlines():
        key, sep, value = raw.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def probe_controller(ctl: Controller, transport: Transport) -> tuple[SegmentResult, dict[str, Any]]:
    """Current node of the configured group (following nested groups) and its last delay."""
    secret = ctl.secret()
    name = ctl.group
    code, data, ms = transport.local_get(f"{ctl.url}/proxies/{urllib.parse.quote(name)}", secret)
    node, delay = None, None
    if code is not None and 200 <= code < 300 and isinstance(data, dict):
        chain = [name]
        for _ in range(4):  # Selector → URLTest → node
            nxt = data.get("now")
            if not nxt or nxt in chain:
                break
            chain.append(nxt)
            c2, d2, _ms2 = transport.local_get(
                f"{ctl.url}/proxies/{urllib.parse.quote(nxt)}", secret)
            if c2 is None or not isinstance(d2, dict):
                break
            data = d2
        node = chain[-1] if len(chain) > 1 else None
        history = data.get("history") or []
        if history and isinstance(history[-1], dict):
            delay = history[-1].get("delay") or None
    seg = classify_proxy_api(code, node=node, latency_ms=ms)
    return seg, {"node": node, "node_delay_ms": delay}


def probe_targets(line: LineConfig, transport: Transport, extra: dict[str, Any]
                  ) -> SegmentResult:
    """HEAD each target API host through the proxy; the first one that fails decides."""
    codes: dict[str, int | None] = {}
    slowest: int | None = None
    for target in line.target_hosts:
        url = target if "://" in target else f"https://{target}/"
        t = transport.curl(curl_argv(line.proxy, url, head=True))
        seg = classify_target(t.rc, t.http_code, t.total_ms)
        codes[target] = t.http_code
        extra["target_codes"] = codes
        if seg.status != "up":
            return SegmentResult("target", seg.status, f"{target}：{seg.reason}", seg.latency_ms,
                                 {**seg.detail, "host": target})
        slowest = max(slowest or 0, t.total_ms or 0) or slowest
    return SegmentResult("target", "up", latency_ms=slowest, detail={"codes": codes})


@dataclass(frozen=True)
class ProbeResult:
    obs: Observation
    extra: dict[str, Any]  # loc, colo, node, node_delay_ms, target codes


def probe_line(line: LineConfig, transport: Transport, *, now: int) -> ProbeResult:
    segments: list[SegmentResult] = []
    extra: dict[str, Any] = {}
    host, port = line.proxy_host_port
    ok, err, ms = transport.tcp(host, port)
    segments.append(classify_tcp(ok, err, ms))
    if line.controller:
        seg, info = probe_controller(line.controller, transport)
        segments.append(seg)
        extra.update(info)
    exit_ip = None
    ip_ok = None
    rc = tls = total = None
    if ok:
        r = transport.curl(curl_argv(line.proxy, line.trace_url))
        rc, tls, total = r.rc, r.tls_ms, r.total_ms
        if r.rc != 0:
            seg = classify_curl_rc(r.rc)
        elif r.http_code != 200:
            seg = SegmentResult("identity", "down", f"trace 返回 {r.http_code}",
                                detail={"code": r.http_code})
        else:
            trace = parse_trace(r.body)
            exit_ip = trace.get("ip")
            extra.update(loc=trace.get("loc"), colo=trace.get("colo"))
            seg = SegmentResult("identity", "up", latency_ms=total, detail={"tls_ms": tls})
            if line.expect_ips and exit_ip:
                ip_ok = exit_ip in line.expect_ips
            elif line.expect_country and trace.get("loc"):
                ip_ok = trace.get("loc") == line.expect_country
        segments.append(seg)
        if seg.status == "up" and line.target_hosts:
            segments.append(probe_targets(line, transport, extra))
    obs = Observation(ts=now, segments=tuple(segments), line=line.name, exit_ip=exit_ip,
                      ip_ok=ip_ok, curl_rc=rc, tls_ms=tls, total_ms=total)
    return ProbeResult(obs, extra)


# ── wake time ─────────────────────────────────────────────────────────────


def parse_waketime(text: str) -> int | None:
    """`sysctl -n kern.waketime` → `{ sec = 1793889907, usec = 249088 } Thu Nov ...`."""
    import re

    m = re.search(r"sec\s*=\s*(\d+)", text)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def last_wake(run: Callable[..., str]) -> int | None:
    from kg.plane.collectors import CommandError

    try:
        return parse_waketime(run(["sysctl", "-n", "kern.waketime"], timeout=5))
    except CommandError:
        return None
