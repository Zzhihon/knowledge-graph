"""The few things self-healing asks of the agent-autoloop hub, behind one narrow interface.

`HubActions` is all the heal code sees. `HttpHubActions` calls the hub's owner API:

    POST /v1/requires/{cap}/pause   {"reason", "until"}   pause every rule that requires `cap`
    POST /v1/requires/{cap}/resume  {}
    POST /v1/rules/{name}/run       {"reason"}            one run now, through the usual queue
    GET  /v1/state                                        lines[].requires / skippedAt, skipped[]

The owner key is read from the hub's config.json on every call, goes only into the
Authorization header of a request to the hub, and is scrubbed from every message that leaves
this module. A hub older than these endpoints answers 404 "没有这个地址"; that becomes
`HubUnsupportedError`, and the caller falls back to only suggesting the action.

`FakeHubActions` is the in-memory stand-in for tests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class HubActionError(Exception):
    """The hub said no or could not be reached. The message is safe to show and to log."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class HubUnsupportedError(HubActionError):
    """The hub has no such endpoint (an older version): only suggest."""


class NothingRequiresError(HubActionError):
    """No rule declares this capability, so there is nothing to pause."""


@dataclass(frozen=True)
class RuleRef:
    name: str
    scheduled: bool  # cron rule (True) or event rule
    paused: bool
    # Last time the hub skipped it because of a pause (a cron tick, or an event it did not take).
    skipped_at: int | None = None


class HubActions(Protocol):
    def pause_capability(self, cap: str, reason: str, until: int | None = None
                         ) -> dict[str, Any]: ...
    def resume_capability(self, cap: str) -> dict[str, Any]: ...
    def run_rule(self, name: str, reason: str = "") -> dict[str, Any]: ...
    def list_rules_requiring(self, cap: str) -> list[RuleRef]: ...


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _ts(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


# The hub is on this machine: never send its traffic, or the owner key, through a proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class HttpHubActions:
    config_file: Path
    timeout: float = 15.0

    def _config(self) -> tuple[str, str]:
        try:
            config = json.loads(self.config_file.expanduser().read_text())
        except FileNotFoundError as exc:
            raise HubActionError(503, "找不到中台的 config.json") from exc
        except (OSError, ValueError) as exc:
            raise HubActionError(503, f"读不了中台的 config.json：{type(exc).__name__}") from exc
        url, owner = config.get("url"), config.get("owner")
        if not url or not owner:
            raise HubActionError(503, "中台的 config.json 里没有地址或本人的密钥")
        return str(url).rstrip("/"), str(owner)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url, owner = self._config()
        request = urllib.request.Request(
            url + path, method=method,
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode(),
            headers={"authorization": f"Bearer {owner}", "content-type": "application/json"},
        )
        try:
            with _opener.open(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = str(json.loads(raw).get("error") or raw)
            except (ValueError, AttributeError):
                message = raw or f"中台返回 {exc.code}"
            message = _scrub(message[:500], owner)
            if exc.code == 404 and "requires" in message:
                raise NothingRequiresError(404, message) from None
            if exc.code == 404 and "规则" not in message:
                raise HubUnsupportedError(404, f"中台没有 {path.split('/')[2]} 接口（版本旧）："
                                          f"{message}") from None
            raise HubActionError(exc.code, message) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HubActionError(502, _scrub(f"中台没响应：{reason}", owner)) from None
        text = _scrub(text, owner)
        try:
            return json.loads(text) if text else None
        except ValueError:
            raise HubActionError(502, f"中台返回的不是 JSON：{text[:200]}") from None

    def pause_capability(self, cap: str, reason: str, until: int | None = None
                         ) -> dict[str, Any]:
        body: dict[str, Any] = {"reason": reason[:200]}
        if until is not None:
            body["until"] = _iso(until)
        return self._call("POST", f"/v1/requires/{urllib.parse.quote(cap, safe='')}/pause", body)

    def resume_capability(self, cap: str) -> dict[str, Any]:
        return self._call("POST", f"/v1/requires/{urllib.parse.quote(cap, safe='')}/resume", {})

    def run_rule(self, name: str, reason: str = "") -> dict[str, Any]:
        return self._call("POST", f"/v1/rules/{urllib.parse.quote(name, safe='')}/run",
                          {"reason": reason[:200]})

    def list_rules_requiring(self, cap: str) -> list[RuleRef]:
        state = self._call("GET", "/v1/state") or {}
        lines = state.get("lines") or []
        if lines and not any("requires" in line for line in lines):
            raise HubUnsupportedError(404, "中台的 /v1/state 没有 requires（版本旧）")
        skipped: dict[str, int] = {}
        for event in state.get("skipped") or []:
            at = _ts(event.get("receivedAt"))
            for name in event.get("rules") or []:
                if at is not None:
                    skipped[name] = max(skipped.get(name, 0), at)
        out = []
        for line in lines:
            if cap not in (line.get("requires") or []):
                continue
            name = str(line.get("name") or line.get("id"))
            cron_at = _ts(line.get("skippedAt"))
            event_at = skipped.get(name)
            last = max((t for t in (cron_at, event_at) if t is not None), default=None)
            out.append(RuleRef(name, bool(line.get("every")), bool(line.get("paused")), last))
        return out


@dataclass
class FakeHubActions:
    """In-memory hub for tests: records every call; `unsupported=True` acts like an old hub."""

    rules: dict[str, list[str]] = field(default_factory=dict)  # rule name -> requires
    scheduled: set[str] = field(default_factory=set)
    unsupported: bool = False
    calls: list[tuple[str, ...]] = field(default_factory=list)
    paused: set[str] = field(default_factory=set)  # capabilities paused
    skipped_at: dict[str, int] = field(default_factory=dict)

    def _check(self) -> None:
        if self.unsupported:
            raise HubUnsupportedError(404, "中台没有这个接口（版本旧）")

    def _requiring(self, cap: str) -> list[str]:
        return sorted(n for n, req in self.rules.items() if cap in req)

    def pause_capability(self, cap: str, reason: str, until: int | None = None
                         ) -> dict[str, Any]:
        self.calls.append(("pause", cap))
        self._check()
        names = self._requiring(cap)
        if not names:
            raise NothingRequiresError(404, f"没有规则写了 requires「{cap}」")
        changed = cap not in self.paused
        self.paused.add(cap)
        return {"changed": changed, "requires": cap, "rules": names}

    def resume_capability(self, cap: str) -> dict[str, Any]:
        self.calls.append(("resume", cap))
        self._check()
        changed = cap in self.paused
        self.paused.discard(cap)
        return {"changed": changed, "requires": cap, "rules": self._requiring(cap),
                "stillPaused": []}

    def run_rule(self, name: str, reason: str = "") -> dict[str, Any]:
        self.calls.append(("run", name))
        self._check()
        if name not in self.rules:
            raise HubActionError(404, f"没有这条规则：{name}")
        if any(cap in self.paused for cap in self.rules[name]):
            raise HubActionError(409, f"规则「{name}」暂停中")
        return {"event": len(self.calls), "run": len(self.calls)}

    def list_rules_requiring(self, cap: str) -> list[RuleRef]:
        self.calls.append(("list", cap))
        self._check()
        return [RuleRef(n, n in self.scheduled, any(c in self.paused for c in self.rules[n]),
                        self.skipped_at.get(n)) for n in self._requiring(cap)]
