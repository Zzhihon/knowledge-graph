"""Request checks for endpoints that act with the owner's authority on this machine.

- Every request must name a loopback host, which stops DNS rebinding from reading local data.
- A write must come from a page served by this same origin (Origin, or Referer when the browser
  sends no Origin, equal to the Host) and carry a JSON body. Another site can still make the
  browser send a request, but it cannot forge those headers, and a cross-site JSON request needs a
  CORS preflight that this server does not grant to foreign origins.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import HTTPException, Request

LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _hostname(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("["):
        return netloc.split("]")[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


def require_local(request: Request) -> None:
    host = request.headers.get("host", "")
    if _hostname(host) not in LOOPBACK:
        raise HTTPException(status_code=403, detail="只接受本机访问")


def require_same_origin_json(request: Request) -> None:
    require_local(request)
    host = request.headers.get("host", "").strip().lower()
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if not origin or origin == "null" or urlsplit(origin).netloc.lower() != host:
        raise HTTPException(status_code=403, detail="只接受本页面发起的请求")
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(status_code=403, detail="只接受本页面发起的请求")
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind != "application/json":
        raise HTTPException(
            status_code=415, detail="只接受 JSON 请求（Content-Type: application/json）"
        )
