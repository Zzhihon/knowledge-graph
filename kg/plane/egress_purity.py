"""Purity checks of an egress line: is the exit the kind of exit we want to be seen with?

Run hourly and whenever the exit IP changes (the collector decides when). Each check returns
`{"status", "checked_at", "summary", "detail"}` where status is one of

    ok       nothing to worry about
    warn     got worse / leaking: the `egress.purity` rule opens a reminder
    hint     worth knowing, never alerts (time zone / language mismatch)
    unknown  could not tell this time (service down, not enough data)
    skipped  turned off, or cannot be done on this machine (the summary says why)

Checks
- `ip_profile`     ASN, data-center / proxy flags and location of the exit IP, from one public
                   lookup (ip-api.com by default) requested through the proxy, cached per IP.
                   Warns when the profile got worse than the first one seen for this line:
                   proxy or hosting flag turned on, or the ASN changed.
- `dns_leak`       bash.ws DNS leak test with every lookup made through the proxy: the
                   resolvers that answered for the proxied names must be in the exit's country.
                   Lookups from the local resolver are deliberately not made: that would be a
                   request that does not go through the proxy.
- `direct_leak`    connection table sample (`lsof`): established TCP connections of the listed
                   processes to public addresses other than through the proxy. With a TUN-mode
                   client every connection looks direct, so this only means something when the
                   processes are configured to use the proxy explicitly. Other processes'
                   environment variables are not read: they hold credentials.
- `locale`         local time zone and language region against the exit's (hint only).
- `ip_stability`   computed by the page from the probe signals, not here.

Everything that talks to the network does it through `kg.plane.egress_probe.curl_argv`, i.e.
through the proxy and without credentials.
"""

from __future__ import annotations

import ipaddress
import json
import os
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kg.plane.egress_probe import curl_argv

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kg.plane.egress_probe import LineConfig, PurityConfig, Transport

CHECKS = ("ip_profile", "dns_leak", "direct_leak", "locale")


def result(status: str, summary: str, now: int, **detail: Any) -> dict[str, Any]:
    return {"status": status, "checked_at": now, "summary": summary, "detail": detail}


def overall(checks: Mapping[str, Mapping[str, Any]]) -> str:
    """Signal status of one purity run: degraded if anything warns, unknown if nothing told."""
    statuses = [c.get("status") for c in checks.values()]
    if "warn" in statuses:
        return "degraded"
    if any(s in ("ok", "hint") for s in statuses):
        return "up"
    return "unknown"


# ── ip profile ────────────────────────────────────────────────────────────


def parse_profile(body: str) -> dict[str, Any] | None:
    """ip-api.com JSON → the fields we keep; None when the lookup failed."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("status") not in (None, "success"):
        return None
    asn = str(data.get("as") or "").split(" ")[0] or None
    return {
        "asn": asn,
        "org": data.get("org") or data.get("isp"),
        "country": data.get("countryCode"),
        "region": data.get("regionName"),
        "city": data.get("city"),
        "timezone": data.get("timezone"),
        "proxy": bool(data.get("proxy")) if "proxy" in data else None,
        "hosting": bool(data.get("hosting")) if "hosting" in data else None,
    }


def compare_profile(profile: Mapping[str, Any], baseline: Mapping[str, Any] | None
                    ) -> tuple[str, str]:
    flags = [k for k in ("proxy", "hosting") if profile.get(k)]
    label = f"{profile.get('asn') or '?'} {profile.get('org') or ''}".strip()
    names = {"proxy": "代理", "hosting": "机房"}
    tags = f"（标记：{'、'.join(names[f] for f in flags)}）" if flags else "（无代理/机房标记）"
    if baseline is None:
        return "ok", f"{label}，{profile.get('country') or '?'}{tags}；记为基线"
    worse = [k for k in ("proxy", "hosting") if profile.get(k) and not baseline.get(k)]
    if profile.get("asn") and baseline.get("asn") and profile["asn"] != baseline["asn"]:
        worse.append("asn")
    if worse:
        what = {"proxy": "新增代理标记", "hosting": "新增机房标记", "asn": "ASN 变了"}
        return "warn", f"IP 画像变差：{'、'.join(what[w] for w in worse)}；{label}{tags}"
    return "ok", f"{label}，{profile.get('country') or '?'}{tags}"


def check_ip_profile(line: LineConfig, cfg: PurityConfig, transport: Transport, *, exit_ip: str,
                     now: int, cache: dict[str, Any], baseline: Mapping[str, Any] | None
                     ) -> dict[str, Any]:
    profiles = cache.setdefault("ip_profiles", {})
    cached = profiles.get(exit_ip)
    if cached and now - int(cached.get("fetched_at", 0)) < cfg.profile_ttl_s:
        profile = cached["profile"]
        source = "cache"
    else:
        r = transport.curl(curl_argv(line.proxy, cfg.profile_url.format(ip=exit_ip)))
        profile = parse_profile(r.body) if r.rc == 0 and r.http_code == 200 else None
        if profile is None:
            return result("unknown", f"IP 画像查询失败（curl {r.rc}，HTTP {r.http_code}）", now)
        profiles[exit_ip] = {"fetched_at": now, "profile": profile}
        source = "lookup"
    status, summary = compare_profile(profile, baseline)
    return result(status, summary, now, profile=profile, source=source)


# ── dns leak ──────────────────────────────────────────────────────────────


def parse_dns_leak(body: str) -> list[dict[str, Any]]:
    """bash.ws `dnsleak/test/<id>?json` → the DNS servers it saw."""
    try:
        data = json.loads(body)
    except ValueError:
        return []
    return [
        {"ip": d.get("ip"), "country": (d.get("country") or "").upper() or None,
         "asn": d.get("asn")}
        for d in data if isinstance(d, dict) and d.get("type") == "dns"
    ] if isinstance(data, list) else []


def check_dns_leak(line: LineConfig, cfg: PurityConfig, transport: Transport, *, now: int,
                   exit_country: str | None) -> dict[str, Any]:
    base = cfg.dns_leak_base
    r = transport.curl(curl_argv(line.proxy, f"https://{base}/id", timeout_s=8))
    test_id = r.body.strip()
    if r.rc != 0 or not test_id.isalnum():
        return result("unknown", f"DNS 泄漏测试取不到编号（curl {r.rc}）", now)
    # Make the proxy resolve a few unique names; the connection itself may fail, that is fine.
    for i in range(1, 4):
        transport.curl(curl_argv(line.proxy, f"https://{i}.{test_id}.{base}/", head=True,
                                 timeout_s=4))
    r = transport.curl(curl_argv(line.proxy, f"https://{base}/dnsleak/test/{test_id}?json",
                                 timeout_s=8))
    servers = parse_dns_leak(r.body) if r.rc == 0 else []
    if not servers:
        return result("unknown", "DNS 泄漏测试没有看到解析服务器", now)
    countries = sorted({s["country"] for s in servers if s["country"]})
    if exit_country and any(c != exit_country.upper() for c in countries):
        return result("warn", f"经代理的解析服务器在 {'、'.join(countries)}，出口在 {exit_country}",
                      now, servers=servers)
    return result("ok", f"{len(servers)} 个解析服务器，都在 {'、'.join(countries) or '?'}", now,
                  servers=servers)


# ── direct leak ───────────────────────────────────────────────────────────


def _public(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


def parse_lsof_established(text: str) -> list[tuple[int, str, str]]:
    """`lsof -nP -iTCP -sTCP:ESTABLISHED -F pcn` → (pid, command, remote host)."""
    out = []
    pid, command = 0, ""
    for raw in text.splitlines():
        tag, value = raw[:1], raw[1:]
        if tag == "p":
            pid, command = int(value) if value.isdigit() else 0, ""
        elif tag == "c":
            command = value
        elif tag == "n" and "->" in value:
            remote = value.split("->", 1)[1]
            host = remote.rpartition(":")[0]
            out.append((pid, command, host))
    return out


def check_direct_leak(cfg: PurityConfig, run: Callable[..., str], *, now: int,
                      proxy_host: str) -> dict[str, Any]:
    from kg.plane.collectors import CommandError

    names = cfg.direct_leak_processes
    if not names:
        return result("skipped", "没配置要抽查的进程名（purity.direct_leak.processes）", now)
    try:
        text = run(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED", "-F", "pcn", "+c", "0"],
                   timeout=20)
    except CommandError as exc:
        return result("unknown", f"读不到连接表：{exc}", now)
    watched = [(p, c, h) for p, c, h in parse_lsof_established(text)
               if any(c.startswith(n) for n in names)]
    direct = [(p, c, h) for p, c, h in watched if _public(h) and h != proxy_host]
    if direct:
        sample = sorted({f"{c}→{h}" for _, c, h in direct})[:5]
        return result("warn", f"{len(direct)} 条连接没经过代理：{'，'.join(sample)}", now,
                      connections=len(watched), direct=len(direct))
    return result("ok", f"抽查 {len(watched)} 条连接，都经过本地代理", now,
                  connections=len(watched), direct=0)


# ── locale ────────────────────────────────────────────────────────────────


def _offset_h(tz: str, at: int) -> float | None:
    try:
        return datetime.fromtimestamp(at, ZoneInfo(tz)).utcoffset().total_seconds() / 3600
    except (ZoneInfoNotFoundError, ValueError, AttributeError):
        return None


def check_locale(profile: Mapping[str, Any] | None, *, now: int, local_offset_h: float,
                 lang: str | None) -> dict[str, Any]:
    if not profile or not profile.get("timezone"):
        return result("unknown", "没有出口时区（IP 画像未取到）", now)
    exit_off = _offset_h(str(profile["timezone"]), now)
    if exit_off is None:
        return result("unknown", f"认不出时区 {profile['timezone']}", now)
    notes = []
    diff = abs(exit_off - local_offset_h)
    if diff >= 3:
        notes.append(f"本机时区 UTC{local_offset_h:+g}，出口 {profile['timezone']}"
                     f"（UTC{exit_off:+g}），差 {diff:g} 小时")
    region = (lang or "").split(".")[0].split("_")[-1].upper() if lang and "_" in lang else None
    if region and profile.get("country") and region != profile["country"]:
        notes.append(f"系统语言地区 {region}，出口在 {profile['country']}")
    if notes:
        return result("hint", "；".join(notes) + "（只提示，不改）", now)
    return result("ok", "本机时区、语言与出口一致", now)


def local_locale() -> tuple[float, str | None]:
    offset = -time.timezone if not time.localtime().tm_isdst else -time.altzone
    return offset / 3600, os.environ.get("LC_ALL") or os.environ.get("LANG")


# ── one run ───────────────────────────────────────────────────────────────


def run_purity(line: LineConfig, cfg: PurityConfig, transport: Transport, run: Callable[..., str],
               *, now: int, exit_ip: str | None, exit_country: str | None,
               cache: dict[str, Any], baseline: Mapping[str, Any] | None,
               locale: tuple[float, str | None] | None = None) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    profile = None
    if exit_ip:
        checks["ip_profile"] = check_ip_profile(line, cfg, transport, exit_ip=exit_ip, now=now,
                                                cache=cache, baseline=baseline)
        profile = checks["ip_profile"]["detail"].get("profile")
    else:
        checks["ip_profile"] = result("unknown", "这次没拿到出口 IP", now)
    if cfg.dns_leak:
        checks["dns_leak"] = check_dns_leak(line, cfg, transport, now=now,
                                            exit_country=exit_country)
    else:
        checks["dns_leak"] = result("skipped", "配置里关掉了（purity.dns_leak: false）", now)
    checks["direct_leak"] = check_direct_leak(cfg, run, now=now,
                                              proxy_host=line.proxy_host_port[0])
    offset, lang = locale or local_locale()
    checks["locale"] = check_locale(profile, now=now, local_offset_h=offset, lang=lang)
    return checks

