"""Talking to the local agent-autoloop hub on the owner's behalf, and managing its rules file.

The owner key is read from the hub's config.json on every call and only ever goes into the
Authorization header of a request to the hub. Nothing here returns it, and every text that leaves
this module is scrubbed of it in case the hub ever echoes it back.

Rules are edited as a file: each write is checked with `agent-autoloop check` first, backed up to
`rules.json.bak-<time>`, then swapped in atomically; the hub picks the new file up by itself.
A CLI whose help lists `check [文件]` checks the candidate file directly and touches no hub. An
older one can only check its own RULES_FILE and, by importing the hub's runtime, records the rules
it reads into that home's log; it runs with a throwaway AGENT_AUTOLOOP_HOME, or a check of a
candidate would apply it to the running hub.

Pausing, resuming and running a rule now go through the hub's owner API (HubControl): they are
facts in the hub's log, and rules.json is not touched. A hub too old to have that API answers 404
"没有这个地址", which becomes ControlUnsupportedError instead of a server error.
"""

from __future__ import annotations

import difflib
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

HOME = Path("~/.agent-autoloop")
BACKUP_PREFIX = ".bak-"
_STAMP = re.compile(r"\d{8}-\d{6}(-\d+)?")
_LOCAL_SETTINGS = Path(".kg") / "hub.json"
_ENV = {
    "config_file": "KG_HUB_CONFIG",
    "rules_file": "KG_HUB_RULES",
    "cli": "KG_HUB_CLI",
    "audit_file": "KG_HUB_AUDIT",
}


@dataclass(frozen=True)
class HubSettings:
    """Where the hub's files are: standard defaults, overridden by `.kg/hub.json` or env."""

    config_file: Path = HOME / "config.json"
    rules_file: Path = HOME / "rules.json"
    cli: tuple[str, ...] = ("agent-autoloop",)
    audit_file: Path = Path(".kg") / "plane" / "hub-audit.jsonl"
    timeout: float = 15.0

    def resolved(self, root: Path) -> HubSettings:
        def place(p: Path) -> Path:
            p = p.expanduser()
            return p if p.is_absolute() else root / p

        return HubSettings(
            config_file=place(self.config_file),
            rules_file=place(self.rules_file),
            cli=self.cli,
            audit_file=place(self.audit_file),
            timeout=self.timeout,
        )


def load_settings(root: Path) -> HubSettings:
    """Defaults, then `<root>/.kg/hub.json`, then KG_HUB_* environment variables."""
    values: dict[str, Any] = {}
    local = root / _LOCAL_SETTINGS
    if local.is_file():
        values.update(
            {k: v for k, v in json.loads(local.read_text()).items() if not k.startswith("_")}
        )
    for key, env in _ENV.items():
        if os.environ.get(env):
            values[key] = os.environ[env]
    kwargs: dict[str, Any] = {}
    for key in ("config_file", "rules_file", "audit_file"):
        if key in values:
            kwargs[key] = Path(values[key])
    if "cli" in values:
        cli = values["cli"]
        kwargs["cli"] = tuple(cli.split() if isinstance(cli, str) else cli)
    if "timeout" in values:
        kwargs["timeout"] = float(values["timeout"])
    return HubSettings(**kwargs).resolved(root)


# ── The hub's HTTP API ──────────────────────────────────────────────────────────────────────────


class HubError(Exception):
    """The hub said no, or could not be reached (status 502). The message is safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


# The hub is on this machine: never send its traffic, or the owner key, through a system proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class HubClient:
    def __init__(self, settings: HubSettings):
        self.settings = settings

    def _config(self) -> tuple[str, str]:
        try:
            config = json.loads(self.settings.config_file.read_text())
        except FileNotFoundError as exc:
            raise HubError(503, "找不到中台的 config.json：agent-autoloop 装好了吗？") from exc
        except (OSError, ValueError) as exc:
            raise HubError(503, f"读不了中台的 config.json：{type(exc).__name__}") from exc
        url, owner = config.get("url"), config.get("owner")
        if not url or not owner:
            raise HubError(503, "中台的 config.json 里没有地址或本人的密钥（只接活的电脑看不了）")
        return str(url).rstrip("/"), str(owner)

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url, owner = self._config()
        request = urllib.request.Request(
            url + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"authorization": f"Bearer {owner}", "content-type": "application/json"},
        )
        try:
            with _opener.open(request, timeout=self.settings.timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = str(json.loads(raw).get("error") or raw)
            except (ValueError, AttributeError):
                message = raw or f"中台返回 {exc.code}"
            raise HubError(exc.code, _scrub(message[:2000], owner)) from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HubError(502, _scrub(f"中台没响应：{reason}", owner)) from None
        text = _scrub(text, owner)
        try:
            return json.loads(text) if text else None
        except ValueError:
            raise HubError(502, f"中台返回的不是 JSON：{text[:300]}") from None


# ── The rules file ──────────────────────────────────────────────────────────────────────────────


class RulesInvalidError(Exception):
    """`agent-autoloop check` rejected the candidate; issues are its lines, one problem each."""

    def __init__(self, issues: list[str]):
        super().__init__("\n".join(issues))
        self.issues = issues


class CheckUnavailableError(Exception):
    """The checker could not run at all, so nothing may be written."""


class StaleVersionError(Exception):
    """The file changed after the page read it."""


class Checker(Protocol):
    def __call__(self, text: str) -> list[str]: ...


# What the help of a CLI that can check any file says; older ones print `check   检查 rules.json`.
_CHECKS_A_FILE = re.compile(r"agent-autoloop check \[")


@dataclass
class CliChecker:
    """`agent-autoloop check` on a candidate file (see module docstring for the two ways)."""

    cli: tuple[str, ...]
    timeout: float = 30.0
    _direct: bool | None = field(default=None, repr=False)

    def _env(self) -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if not k.startswith("AGENT_AUTOLOOP_")}

    def checks_a_file(self) -> bool:
        """Whether this CLI takes `check <file>`: asked once, from its help, which is harmless."""
        if self._direct is None:
            try:
                done = subprocess.run(
                    [*self.cli, "help"],
                    env=self._env(),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
                self._direct = bool(_CHECKS_A_FILE.search(done.stdout or ""))
            except (OSError, subprocess.TimeoutExpired):
                # Not found or stuck: the check below says so in its own words.
                return False
        return self._direct

    def __call__(self, text: str) -> list[str]:
        direct = self.checks_a_file()
        with tempfile.TemporaryDirectory(prefix="kg-rules-check-") as tmp:
            candidate = Path(tmp) / "rules.json"
            candidate.write_text(text, encoding="utf-8")
            env = self._env()
            argv = [*self.cli, "check", str(candidate)]
            if not direct:
                env |= {
                    "AGENT_AUTOLOOP_HOME": str(Path(tmp) / "home"),
                    "AGENT_AUTOLOOP_RULES": str(candidate),
                }
                argv = [*self.cli, "check"]
            try:
                done = subprocess.run(
                    argv,
                    env=env,
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise CheckUnavailableError(
                    f"找不到 {self.cli[0]} 命令，没法校验，所以没写入"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise CheckUnavailableError(
                    f"校验超过 {self.timeout:.0f} 秒没结束，没写入"
                ) from exc
        if done.returncode == 0:
            return []
        out = re.sub(r"\x1b\[[0-9;]*m", "", (done.stderr or "") + (done.stdout or ""))
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        # A stack trace means the checker itself broke, not that the rules are wrong.
        if re.search(r"^\s+at .+:\d+:\d+\)?$", out, re.M):
            raise CheckUnavailableError("校验程序出错了：" + (lines[0] if lines else "")[:300])
        lines = [ln for ln in lines if not ln.startswith("rules.json 有问题")]
        lines = [ln.replace(str(candidate), "候选文件") for ln in lines]
        return lines or [f"校验没通过（退出码 {done.returncode}）"]


def version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def dump_rules(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _changes(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    """Rules added, removed or changed (with their fields), and other top-level keys changed."""

    def by_name(d: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        rules = (d or {}).get("rules") or []
        return {r.get("name", ""): r for r in rules if isinstance(r, dict)}

    old, new = by_name(before), by_name(after)
    changed = {}
    for name in old.keys() & new.keys():
        fields = sorted(
            k for k in old[name].keys() | new[name].keys() if old[name].get(k) != new[name].get(k)
        )
        if fields:
            changed[name] = fields
    tops = sorted(
        k
        for k in ((before or {}).keys() | after.keys()) - {"rules"}
        if (before or {}).get(k) != after.get(k)
    )
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": changed,
        "sections": tops,
    }


def _parse(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class RulesStore:
    """Read, check, back up, write and roll back the hub's rules file, with an audit trail."""

    rules_file: Path
    audit_file: Path
    check: Checker
    who: str = field(default_factory=lambda: f"{getpass.getuser()}@kg-web")
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Reading.
    def read(self) -> dict[str, Any]:
        raw = self.rules_file.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        data = _parse(text)
        return {
            "file": str(self.rules_file),
            "version": version_of(raw),
            "text": text,
            "data": data,
            "parseError": None if data is not None else "rules.json 现在不是合法的 JSON 对象",
            "backups": self.backups(),
        }

    def backups(self, limit: int = 30) -> list[dict[str, Any]]:
        """Backups this page made (newest first), then any other `rules.json.bak-*` by mtime."""
        folder, base = self.rules_file.parent, self.rules_file.name + BACKUP_PREFIX
        found = [p for p in folder.glob(base + "*") if p.is_file()]
        ours = sorted((p for p in found if _STAMP.fullmatch(p.name[len(base) :])), reverse=True)
        others = sorted(
            (p for p in found if p not in ours), key=lambda p: p.stat().st_mtime, reverse=True
        )
        return [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
                "ours": p in ours,
            }
            for p in [*ours, *others][:limit]
        ]

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_file.is_file():
            return []
        lines = self.audit_file.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # Checking.
    def validate(self, text: str) -> list[str]:
        return self.check(text)

    def rule_candidate(
        self, original: str | None, rule: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """The whole file with one rule created (original None) or replaced in place."""
        data = _parse(self.rules_file.read_text(encoding="utf-8"))
        if data is None:
            raise RulesInvalidError(["rules.json 现在不是合法的 JSON 对象，先修好文件或回滚"])
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("name"), str)
            or not rule["name"].strip()
        ):
            raise RulesInvalidError(["规则要有名字"])
        rules = list(data.get("rules") or [])
        names = [r.get("name") for r in rules if isinstance(r, dict)]
        if original is None:
            if rule["name"] in names:
                raise RulesInvalidError([f"已经有一条叫「{rule['name']}」的规则"])
            rules.append(rule)
        else:
            if original not in names:
                raise RulesInvalidError([f"没有叫「{original}」的规则，可能刚被别处改掉了"])
            if rule["name"] != original and rule["name"] in names:
                raise RulesInvalidError([f"已经有一条叫「{rule['name']}」的规则"])
            rules[names.index(original)] = rule
        data = {**data, "rules": rules}
        return dump_rules(data), data

    # Writing.
    def write(self, text: str, version: str, action: str, note: str = "") -> dict[str, Any]:
        """Check, back up, swap in.

        Refusals raise StaleVersionError, RulesInvalidError or CheckUnavailableError.
        """
        with self._lock:
            current = self.rules_file.read_bytes()
            if version != version_of(current):
                raise StaleVersionError("rules.json 在你打开之后被改过了，刷新后再改")
            issues = self.check(text)
            if issues:
                raise RulesInvalidError(issues)
            backup = self._backup(current)
            self._swap(text)
            before = _parse(current.decode("utf-8", errors="replace"))
            after = _parse(text) or {}
            diff = "".join(
                difflib.unified_diff(
                    current.decode("utf-8", errors="replace").splitlines(keepends=True),
                    text.splitlines(keepends=True),
                    "rules.json（之前）",
                    "rules.json（之后）",
                    n=1,
                )
            )
            entry = {
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "by": self.who,
                "action": action,
                "note": note,
                "backup": backup.name,
                "before": version_of(current),
                "after": version_of(text.encode()),
                "changes": _changes(before, after),
                "diff": diff[:20000],
            }
            self._log(entry)
            return {"version": entry["after"], "backup": backup.name, "audit": entry}

    def rollback(self, version: str, backup: str | None = None) -> dict[str, Any]:
        known = self.backups(limit=10_000)
        names = [b["name"] for b in known]
        ours = [b["name"] for b in known if b["ours"]]
        if not backup and not ours:
            raise RulesInvalidError(
                ["还没有网页保存时留下的备份，没法自动回滚；可以在列表里选一个备份"]
            )
        name = backup or ours[0]
        if name not in names:
            raise RulesInvalidError([f"没有这个备份：{name}"])
        text = (self.rules_file.parent / name).read_text(encoding="utf-8")
        return self.write(text, version, "rollback", note=f"回滚到 {name}")

    def _backup(self, current: bytes) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.rules_file.with_name(f"{self.rules_file.name}{BACKUP_PREFIX}{stamp}")
        n = 2
        while target.exists():
            target = self.rules_file.with_name(f"{self.rules_file.name}{BACKUP_PREFIX}{stamp}-{n}")
            n += 1
        target.write_bytes(current)
        shutil.copymode(self.rules_file, target)
        return target

    def _swap(self, text: str) -> None:
        tmp = self.rules_file.with_name(f".{self.rules_file.name}.kg-tmp")
        tmp.write_text(text, encoding="utf-8")
        shutil.copymode(self.rules_file, tmp)
        os.replace(tmp, self.rules_file)

    def _log(self, entry: dict[str, Any]) -> None:
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Pause / resume / run now ────────────────────────────────────────────────────────────────────


class ControlUnsupportedError(Exception):
    """The hub has no rule-control API: it is older than agent-autoloop's pause / resume / run."""


UPGRADE = (
    "中台版本不支持暂停 / 恢复 / 立即运行，需要升级 agent-autoloop"
    "（agent-autoloop update 后重启中台）"
)


class RuleControl(Protocol):
    """Pause, resume and run now, for one rule or for every rule that requires a capability."""

    def pause(self, rule: str, reason: str = "", until: str | None = None) -> dict[str, Any]: ...
    def resume(self, rule: str) -> dict[str, Any]: ...
    def run_now(self, rule: str, reason: str = "", subject: str = "") -> dict[str, Any]: ...
    def pause_requires(
        self, capability: str, reason: str = "", until: str | None = None
    ) -> dict[str, Any]: ...
    def resume_requires(self, capability: str) -> dict[str, Any]: ...


def control_summary(state: dict[str, Any]) -> dict[str, Any]:
    """What the page needs to know from /v1/state: whether control works, and rule → its pauses."""
    if "pauses" not in state:
        return {"available": False, "reason": UPGRADE, "paused": {}}
    paused = {
        line["name"]: line["paused"]
        for line in state.get("lines") or []
        if isinstance(line, dict) and line.get("paused")
    }
    return {"available": True, "reason": None, "paused": paused}


@dataclass
class HubControl:
    """Rule control through the hub's owner API, with the owner key kept in HubClient."""

    client: HubClient

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.client.call("POST", path, body)
        except HubError as exc:
            # The hub's own 404 for an address it lacks; an unknown rule is a 404 in other words.
            if exc.status == 404 and exc.message == "没有这个地址":
                raise ControlUnsupportedError(UPGRADE) from None
            raise

    @staticmethod
    def _seg(name: str) -> str:
        return urllib.parse.quote(name, safe="")

    @staticmethod
    def _pause_body(reason: str, until: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"reason": reason} if reason else {}
        if until:
            body["until"] = until
        return body

    def pause(self, rule: str, reason: str = "", until: str | None = None) -> dict[str, Any]:
        return self._post(f"/v1/rules/{self._seg(rule)}/pause", self._pause_body(reason, until))

    def resume(self, rule: str) -> dict[str, Any]:
        return self._post(f"/v1/rules/{self._seg(rule)}/resume", {})

    def run_now(self, rule: str, reason: str = "", subject: str = "") -> dict[str, Any]:
        body = {k: v for k, v in (("reason", reason), ("subject", subject)) if v}
        return self._post(f"/v1/rules/{self._seg(rule)}/run", body)

    def pause_requires(
        self, capability: str, reason: str = "", until: str | None = None
    ) -> dict[str, Any]:
        path = f"/v1/requires/{self._seg(capability)}/pause"
        return self._post(path, self._pause_body(reason, until))

    def resume_requires(self, capability: str) -> dict[str, Any]:
        return self._post(f"/v1/requires/{self._seg(capability)}/resume", {})
