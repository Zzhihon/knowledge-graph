"""`<vault>/.kg/plane/heal.yaml`: which actions run by themselves, and the limits around them.

Every action is `off`, `suggest` (the default: the card says what would help) or `auto`.
Without the file everything is `suggest`, so nothing on this machine changes on its own.
The template is `heal.yaml.example` at the repository root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODES = ("off", "suggest", "auto")
ACTIONS = ("egress_pause", "service_restart", "index_stale")
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def plane_dir(vault: Path) -> Path:
    return vault / ".kg" / "plane"


def config_path(vault: Path) -> Path:
    return plane_dir(vault) / "heal.yaml"


@dataclass(frozen=True)
class HealConfig:
    actions: dict[str, str] = field(
        default_factory=lambda: dict.fromkeys(ACTIONS, "suggest"))
    # Hub: the owner key is read from this file at call time and never stored elsewhere.
    hub_config: Path = Path("~/.agent-autoloop/config.json")
    hub_timeout: float = 15.0
    # Notifications.
    min_severity: str = "warning"  # info incidents only show up in the weekly report
    mute_rules: tuple[str, ...] = ()  # rules that never get a card
    silence_days: int = 7  # what "忽略一周" means
    desktop: bool = False  # also pop a macOS notification (the hub's desktop channel does already)
    # egress.outage
    egress_rules: tuple[str, ...] = ("egress.outage",)
    egress_capability: str = "egress"
    max_pause_hours: float = 6.0  # a pause the hub lifts by itself if nobody resumes it
    # service.down
    restart_rules: tuple[str, ...] = ("service.down", "service.health")
    restart_groups: tuple[str, ...] = ("mine",)
    restart_modes: tuple[str, ...] = ("keepalive",)
    restart_per_hour: int = 2
    recheck_minutes: int = 10
    # index
    index_rule_prefix: str = "index."
    # Retro: only incidents that lasted this long, or the second of their kind.
    retro_min_minutes: int = 10
    retro_repeat_days: int = 90
    # Error budget (weekly report): availability targets by resource kind.
    slo: dict[str, float] = field(default_factory=lambda: {"default": 0.99, "egress": 0.995})

    def mode(self, action: str) -> str:
        return self.actions.get(action, "suggest")

    def slo_for(self, kind: str) -> float:
        return float(self.slo.get(kind, self.slo.get("default", 0.99)))

    def notifies(self, severity: str, rule: str) -> bool:
        if rule in self.mute_rules:
            return False
        return SEVERITY_ORDER.get(severity, 1) >= SEVERITY_ORDER.get(self.min_severity, 1)


def _tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def parse_config(raw: dict[str, Any]) -> HealConfig:
    base = HealConfig()
    actions = dict(base.actions)
    for name, mode in (raw.get("actions") or {}).items():
        if name not in ACTIONS:
            raise ValueError(f"heal.yaml：不认识的动作 {name}，可选 {', '.join(ACTIONS)}")
        mode = "off" if mode is False else str(mode)
        if mode not in MODES:
            raise ValueError(f"heal.yaml：{name} 只能是 {' / '.join(MODES)}，不是 {mode}")
        actions[name] = mode
    hub = raw.get("hub") or {}
    notify = raw.get("notify") or {}
    egress = raw.get("egress") or {}
    restart = raw.get("service_restart") or {}
    retro = raw.get("retro") or {}
    slo = dict(base.slo)
    slo.update({str(k): float(v) for k, v in (raw.get("slo") or {}).items()})
    min_severity = str(notify.get("min_severity", base.min_severity))
    if min_severity not in SEVERITY_ORDER:
        raise ValueError(f"heal.yaml：notify.min_severity 只能是 {', '.join(SEVERITY_ORDER)}")
    return HealConfig(
        actions=actions,
        hub_config=Path(str(hub.get("config_file", base.hub_config))),
        hub_timeout=float(hub.get("timeout", base.hub_timeout)),
        min_severity=min_severity,
        mute_rules=_tuple(notify.get("mute_rules"), base.mute_rules),
        silence_days=int(notify.get("silence_days", base.silence_days)),
        desktop=bool(notify.get("desktop", base.desktop)),
        egress_rules=_tuple(egress.get("rules"), base.egress_rules),
        egress_capability=str(egress.get("capability", base.egress_capability)),
        max_pause_hours=float(egress.get("max_pause_hours", base.max_pause_hours)),
        restart_rules=_tuple(restart.get("rules"), base.restart_rules),
        restart_groups=_tuple(restart.get("groups"), base.restart_groups),
        restart_modes=_tuple(restart.get("modes"), base.restart_modes),
        restart_per_hour=int(restart.get("per_hour", base.restart_per_hour)),
        recheck_minutes=int(restart.get("recheck_minutes", base.recheck_minutes)),
        index_rule_prefix=str((raw.get("index") or {}).get("rule_prefix", base.index_rule_prefix)),
        retro_min_minutes=int(retro.get("min_minutes", base.retro_min_minutes)),
        retro_repeat_days=int(retro.get("repeat_days", base.retro_repeat_days)),
        slo=slo,
    )


def load_config(vault: Path) -> HealConfig:
    """heal.yaml under the vault (or KG_HEAL_CONFIG); defaults when there is none.

    KG_HUB_CONFIG, which kg-web's hub page also honours, overrides where the hub config is.
    """
    file = Path(os.environ["KG_HEAL_CONFIG"]) if os.environ.get("KG_HEAL_CONFIG") \
        else config_path(vault)
    raw: dict[str, Any] = {}
    if file.is_file():
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    config = parse_config(raw)
    if os.environ.get("KG_HUB_CONFIG"):
        from dataclasses import replace

        config = replace(config, hub_config=Path(os.environ["KG_HUB_CONFIG"]))
    return config
