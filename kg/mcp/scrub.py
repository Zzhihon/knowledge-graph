"""The last filter before anything leaves the MCP server: every string in every tool result.

Tools return plain JSON-like values; `Scrubber.scrub` walks them and masks, in every string
(dict keys included):

- the value after a key-like word (`token=`, `password:`, `密钥是`), bearer values and every
  non-loopback link (`kg.plane.redact`, built on `kg.eval.retrieval._SECRETISH`);
- credentials, non-loopback IPv4 addresses and host names under internal suffixes, as the
  artifact scanner finds them (`kg.plane.sensitive`, plus `sensitive.internal_domains` from the
  local `.kg/plane/artifacts.yaml`); its "random-looking" test keeps entry ids and paths such
  as `03-Debug/ke-20260228-containerd-mirrors-config-path-conflict.md` readable, which the
  blunter `kg.plane.redact` token rule would mask;
- runs of 32+ hex digits (the scanner lets those through as commit hashes), and random-looking
  runs from 24 characters on (the scanner starts at 40; many keys are 32);
- the home directory: `/Users/<name>` and `/home/<name>` become `~`.

One exception: a string under a key named `url` that is a plain http(s) link keeps its scheme,
host and path, so `find_artifact` can still hand over the share link it was asked for. Its query
string and fragment are masked, and the whole link is masked when it carries a user name or
password, points at an IP or an internal host, or has a known key prefix in its path.

The filter errs on the side of hiding: a long random-looking id in an entry is masked too.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from kg.plane.redact import _BEARER, _KEY_VALUE, _URL, LINK_MASK, MASK, _link
from kg.plane.sensitive import Scanner, _random_looking

_KNOWN_KEY = re.compile(r"sk-[A-Za-z0-9_-]{12,}|ark-[0-9a-f-]{16,}|AKIA[0-9A-Z]{12,}|"
                        r"gh[pousr]_[A-Za-z0-9]{20,}|xox[abp]-|eyJ[A-Za-z0-9_-]{16,}")
_IP_HOST = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$|:")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_HEX_RUN = re.compile(r"\b[A-Fa-f0-9]{32,}\b")
_RANDOM_RUN = re.compile(r"\b[A-Za-z0-9+/_-]{24,}\b")  # `_SECRETISH` only looks at 40+
_HOME = re.compile(r"/(?:Users|home)/[^/\s\"'`]+")


class Scrubber:
    def __init__(self, internal_domains: tuple[str, ...] = ()) -> None:
        self.scanner = Scanner(internal_domains)

    def text(self, text: str) -> str:
        text = _URL.sub(_link, text)
        text = _KEY_VALUE.sub(
            lambda m: f"{m.group(1)}{m.group(2) + ' ' if m.group(2) else ''}{MASK}", text)
        text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
        out, last = [], 0
        for hit in self.scanner.scan(text):
            if hit.offset < last:
                continue
            out += [text[last:hit.offset], MASK]
            last = hit.end
        out.append(text[last:])
        text = _HEX_RUN.sub(MASK, "".join(out))
        text = _RANDOM_RUN.sub(lambda m: MASK if _random_looking(m.group(0)) else m.group(0), text)
        return _HOME.sub("~", text)

    def url(self, url: str) -> str:
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname or ""
        except ValueError:
            return LINK_MASK
        if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc \
                or " " in url.strip():
            return self.text(url)
        if (_IP_HOST.search(host) and host not in _LOOPBACK) or _KNOWN_KEY.search(url) \
                or self.scanner.scan(f" {host} "):
            return LINK_MASK
        tail = ("?" + MASK if parts.query else "") + ("#" + MASK if parts.fragment else "")
        return f"{parts.scheme}://{parts.netloc}{parts.path}{tail}"

    def scrub(self, value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            return self.url(value) if key == "url" else self.text(value)
        if isinstance(value, dict):
            return {self.text(str(k)): self.scrub(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(v, key) for v in value]
        return value


def scrubber_for(vault: Any) -> Scrubber:
    """A scrubber that also knows the internal domains listed in the local artifacts config."""
    from kg.plane.collectors.artifacts import load_artifacts_config

    try:
        domains = load_artifacts_config(vault).internal_domains
    except Exception:  # noqa: BLE001 - a broken local config must not open the filter
        domains = ()
    return Scrubber(tuple(domains))
