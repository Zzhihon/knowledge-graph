"""Mask what looks like a secret before local text (log tails, command lines) is shown.

Built on the same detector the eval miner uses to drop questions (`kg.eval.retrieval._SECRETISH`),
so "looks like a key" means one thing across the repo. That detector only says *where* a secret
starts (`token=`, `https://`); here the value after a keyword, a bearer token and a whole link
are masked too. Loopback links stay readable without their query string, since a log line like
"listening on http://127.0.0.1:8765" is the most useful line there is.

Masking errs on the side of hiding: a long hex id or a git sha is masked as well.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from kg.eval.retrieval import _SECRETISH

MASK = "‹已遮›"
LINK_MASK = "‹链接›"

_KEYWORDS = (r"api[_ -]?key|access[_ -]?key|secret|token|password|passwd|authorization|cookie|"
             r"密钥|密码|口令")
# keyword (possibly inside a longer name: GITHUB_TOKEN, client_secret), separator, value.
_KEY_VALUE = re.compile(
    rf"(?i)((?:[\w-]*(?:{_KEYWORDS})[\w-]*)[\"']?\s*(?:[:：=]|是|为)\s*)"
    r"(?:(bearer|basic|token)\s+)?(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_URL = re.compile(r"https?://[^\s\"'<>`]+")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _link(match: re.Match[str]) -> str:
    url = match.group(0)
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return LINK_MASK
    if host not in _LOOPBACK_HOSTS or "@" in parts.netloc:
        return LINK_MASK
    tail = "?" + MASK if parts.query else ""
    return f"{parts.scheme}://{parts.netloc}{parts.path}{tail}"


def _looks_like_path(text: str, start: int, token: str) -> bool:
    """`\\b[A-Za-z0-9+/_-]{40,}\\b` also catches file paths; keep those that are plainly paths."""
    if start > 0 and text[start - 1] in "/~.":
        return True
    segments = token.split("/")
    return len(segments) >= 4 and max(len(s) for s in segments) < 24


def _token(text: str) -> str:
    out, last = [], 0
    for m in _SECRETISH.finditer(text):
        token = m.group(0)
        # Links and "keyword:" were handled above; what is left of them here is already masked.
        if token.startswith(("http://", "https://")) \
                or token.rstrip().endswith((":", "：", "=", "是", "为")):
            continue
        if "/" in token and _looks_like_path(text, m.start(), token):
            continue
        out.append(text[last:m.start()])
        out.append(MASK)
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def redact(text: str) -> str:
    """Return `text` with keys, tokens, passwords and non-local links masked."""
    text = _URL.sub(_link, text)
    text = _KEY_VALUE.sub(
        lambda m: f"{m.group(1)}{m.group(2) + ' ' if m.group(2) else ''}{MASK}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    return _token(text)
