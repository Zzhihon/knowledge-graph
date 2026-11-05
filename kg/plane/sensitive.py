"""Find what should not be in a shared page: credentials, IP addresses, internal host names.

Used by the artifact collector (a public link whose text hits here opens a critical incident)
and by the artifact export for search (hits are masked before the text is indexed).

- Credentials reuse `_SECRETISH` from `kg.eval.retrieval`, the filter that keeps secrets out of
  the eval set, minus two of its alternatives that are fine in a report: a bare `http(s)://`
  (every page has links) and a run of hex digits (commit and file hashes). Its catch-all for
  any 40+ character run only counts when the run looks random (upper, lower and digits mixed,
  few separators, high entropy); otherwise long paths, test names and migration file names in
  a report would all count. A `password:` style label only counts when a token-looking value
  follows it.
- IPv4 addresses, except loopback, `0.0.0.0` and the documentation ranges (RFC 5737). Private
  ranges (RFC 1918, CGNAT, link-local) are `private_ip`, the rest `public_ip` (an exit IP in a
  write-up says where you come from). Well-known public resolvers (8.8.8.8, 1.1.1.1, ...) are
  left out: naming them says nothing about this machine.
- Host names under internal suffixes: a few generic ones (`.internal`, `.corp`, `.lan`, ...)
  plus whatever `<vault>/.kg/plane/artifacts.yaml` lists under `sensitive.internal_domains`
  (the real company domains stay in that local file, never in the repo).

A hit keeps a masked sample (enough to find it in the page, not enough to use it).
"""

from __future__ import annotations

import ipaddress
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kg.eval.retrieval import _SECRETISH

if TYPE_CHECKING:
    from collections.abc import Iterable

GENERIC_INTERNAL_SUFFIXES = (
    "internal", "intranet", "corp", "lan", "localdomain", "home.arpa", "cluster.local", "svc",
)

KINDS = {
    "credential": "凭据",
    "private_ip": "私网 IP",
    "public_ip": "公网 IP",
    "internal_host": "内网主机",
}

_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.]*\d)")
_HEX = re.compile(r"[A-Fa-f0-9]+")
_KNOWN_PREFIX = re.compile(r"sk-|ark-|AKIA|gh[pousr]_|xox[abp]-|eyJ")
_LABEL = re.compile(r"(?i:api[_ -]?key|secret|token|password|passwd|密钥|密码|口令)")
_VALUE = re.compile(r"\s*[`'\"]?([^\s`'\"<>，。,;；]{8,})")
_DOC_NETS = tuple(ipaddress.ip_network(n) for n in
                  ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))
_RESOLVERS = frozenset({"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "149.112.112.112",
                        "208.67.222.222", "114.114.114.114", "223.5.5.5", "223.6.6.6",
                        "119.29.29.29", "180.76.76.76"})
_PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in
                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
                       "169.254.0.0/16"))


@dataclass(frozen=True)
class Hit:
    kind: str  # one of KINDS
    sample: str  # masked
    offset: int
    end: int  # what `redact` covers: the secret itself (for `password: x`, only the value)


def _mask(text: str, keep: int = 4) -> str:
    if len(text) <= keep:
        return "*" * len(text)
    return text[:keep] + "…" + ("*" * min(4, len(text) - keep))


def _mask_ip(ip: str) -> str:
    a, b, *_ = ip.split(".")
    return f"{a}.{b}.*.*"


def _looks_like_value(raw: str) -> bool:
    """A value after `password:` that could be a secret: has a digit or symbol, not a word."""
    return bool(re.search(r"\d", raw) and re.search(r"[A-Za-z]", raw)) or bool(
        re.search(r"[^\w一-鿿]", raw) and len(raw) >= 12)


def _random_looking(token: str) -> bool:
    """A long run that reads like a key, not like a path or an identifier."""
    if sum(token.count(c) for c in "/_-") > 3:
        return False
    if not (sum(c.isupper() for c in token) >= 2 and sum(c.islower() for c in token) >= 2
            and sum(c.isdigit() for c in token) >= 2):
        return False
    counts = Counter(token)
    entropy = -sum(n / len(token) * math.log2(n / len(token)) for n in counts.values())
    return entropy >= 4.2


def _credentials(text: str) -> Iterable[Hit]:
    for m in _SECRETISH.finditer(text):
        found = m.group(0)
        if found.lower() in ("http://", "https://") or _HEX.fullmatch(found):
            continue
        if (not _KNOWN_PREFIX.match(found) and not _LABEL.match(found)
                and not _random_looking(found)):
            continue
        if _LABEL.match(found):
            value = _VALUE.match(text, m.end())
            if not value or not _looks_like_value(value.group(1)):
                continue
            yield Hit("credential", f"{found.strip()} " + _mask(value.group(1), 2),
                      value.start(1), value.end(1))
            continue
        yield Hit("credential", _mask(found), m.start(), m.end())


def _ips(text: str) -> Iterable[Hit]:
    for m in _IPV4.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group(0))
        except ValueError:
            continue
        if (ip.is_loopback or ip.is_unspecified or str(ip) in _RESOLVERS
                or any(ip in n for n in _DOC_NETS)):
            continue
        kind = "private_ip" if any(ip in n for n in _PRIVATE_NETS) else "public_ip"
        yield Hit(kind, _mask_ip(m.group(0)), m.start(), m.end())


def _host_pattern(suffixes: Iterable[str]) -> re.Pattern[str] | None:
    clean = sorted({s.strip().strip(".").lower() for s in suffixes if s and s.strip(". ")},
                   key=len, reverse=True)
    if not clean:
        return None
    alts = "|".join(re.escape(s) for s in clean)
    return re.compile(rf"(?<![\w.-])((?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+(?:{alts}))"
                      rf"(?![\w-])", re.IGNORECASE)


def _hosts(text: str, pattern: re.Pattern[str] | None) -> Iterable[Hit]:
    if pattern is None:
        return
    for m in pattern.finditer(text):
        host = m.group(1)
        if "example" in host.lower().split("."):
            continue  # RFC 2606 style placeholders in docs
        first, _, rest = host.partition(".")
        yield Hit("internal_host", f"{_mask(first, 2)}.{rest}", m.start(1), m.end(1))


class Scanner:
    """Compiled once per run; `internal_domains` are extra host suffixes from the local config."""

    def __init__(self, internal_domains: Iterable[str] = ()) -> None:
        self.hosts = _host_pattern((*GENERIC_INTERNAL_SUFFIXES, *internal_domains))

    def scan(self, text: str) -> list[Hit]:
        hits = [*_credentials(text), *_ips(text), *_hosts(text, self.hosts)]
        return sorted(hits, key=lambda h: h.offset)

    def redact(self, text: str) -> str:
        """The text with every hit replaced by `[已遮盖]` (for the search index)."""
        out, last = [], 0
        for hit in self.scan(text):
            if hit.offset < last:
                continue
            out += [text[last:hit.offset], "[已遮盖]"]
            last = hit.end
        out.append(text[last:])
        return "".join(out)


def summarize(hits: Iterable[Hit], samples: int = 3) -> list[dict[str, object]]:
    """[{kind, label, count, samples}] per kind, most frequent first, for signal detail."""
    by_kind: dict[str, list[str]] = {}
    for h in hits:
        by_kind.setdefault(h.kind, []).append(h.sample)
    counts = Counter({k: len(v) for k, v in by_kind.items()})
    return [{"kind": k, "label": KINDS[k], "count": n,
             "samples": list(dict.fromkeys(by_kind[k]))[:samples]}
            for k, n in counts.most_common()]
