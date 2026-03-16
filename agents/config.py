"""Configuration loader for the knowledge graph system.

Reads config.yaml from the project root and provides typed access
to domains, entry types, agent settings, and review parameters.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _find_project_root() -> Path:
    """Locate the project root by searching upward for config.yaml.

    Starts from this file's directory and walks up until config.yaml
    is found or the filesystem root is reached.

    Returns:
        The directory containing config.yaml.

    Raises:
        FileNotFoundError: If config.yaml cannot be located.
    """
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "config.yaml").is_file():
            return current
        current = current.parent
    raise FileNotFoundError(
        "找不到 config.yaml. 请确认项目根目录结构正确。"
    )


@dataclass(frozen=True)
class DomainConfig:
    """A single knowledge domain definition."""

    key: str
    label: str
    icon: str
    sub_domains: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EntryTypeConfig:
    """A single entry type definition."""

    key: str
    label: str
    description: str
    template: str


@dataclass(frozen=True)
class APIKeyConfig:
    """Single API key configuration."""

    key: str
    model: str
    weight: float = 1.0
    description: str = ""


@dataclass(frozen=True)
class AgentConfig:
    """Agent runtime settings."""

    model: str
    embedding_model: str
    embedding_dim: int
    vector_db: str
    vector_db_path: str
    graph_db: str
    graph_db_path: str
    confidence_threshold: float
    search_alpha: float  # 向量权重 (0.0=纯BM25, 1.0=纯向量)
    api_keys: list[APIKeyConfig] = field(default_factory=list)
    base_url: str = ""


@dataclass(frozen=True)
class ReviewConfig:
    """Review cycle settings."""

    cycle_days: int
    auto_flag_outdated_days: int
    domains_priority: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectConfig:
    """Top-level project configuration."""

    name: str
    version: str
    author: str
    vault_type: str
    root_path: Path
    domains: dict[str, DomainConfig]
    entry_types: dict[str, EntryTypeConfig]
    depth_levels: dict[str, str]
    scopes: dict[str, str]
    agent: AgentConfig
    review: ReviewConfig

    def get_domain(self, key: str) -> DomainConfig | None:
        """Look up a domain by its key."""
        return self.domains.get(key)

    def get_entry_type(self, key: str) -> EntryTypeConfig | None:
        """Look up an entry type by its key."""
        return self.entry_types.get(key)

    @property
    def domain_keys(self) -> list[str]:
        """Return all domain keys sorted alphabetically."""
        return sorted(self.domains.keys())

    @property
    def all_sub_domains(self) -> dict[str, list[str]]:
        """Return a mapping of domain key to sub-domain list."""
        return {k: v.sub_domains for k, v in self.domains.items()}

    @property
    def vault_path(self) -> Path:
        """The root path to the knowledge vault."""
        return self.root_path


def _parse_domains(raw: dict[str, Any]) -> dict[str, DomainConfig]:
    """Parse the domains section of config.yaml."""
    domains: dict[str, DomainConfig] = {}
    for key, spec in raw.items():
        domains[key] = DomainConfig(
            key=key,
            label=spec.get("label", key),
            icon=spec.get("icon", ""),
            sub_domains=spec.get("sub_domains", []),
        )
    return domains


def _parse_entry_types(raw: dict[str, Any]) -> dict[str, EntryTypeConfig]:
    """Parse the entry_types section of config.yaml."""
    types: dict[str, EntryTypeConfig] = {}
    for key, spec in raw.items():
        types[key] = EntryTypeConfig(
            key=key,
            label=spec.get("label", key),
            description=spec.get("description", ""),
            template=spec.get("template", "knowledge-entry"),
        )
    return types


def _parse_agent(raw: dict[str, Any]) -> AgentConfig:
    """Parse the agent section of config.yaml."""
    # Parse API keys if present
    api_keys = []
    if "api_keys" in raw:
        for key_config in raw["api_keys"]:
            api_keys.append(APIKeyConfig(
                key=key_config.get("key", ""),
                model=key_config.get("model", "claude-sonnet-4-20250514"),
                weight=float(key_config.get("weight", 1.0)),
                description=key_config.get("description", ""),
            ))

    return AgentConfig(
        model=raw.get("model", "claude-sonnet-4-20250514"),
        embedding_model=raw.get("embedding_model", "all-MiniLM-L6-v2"),
        embedding_dim=int(raw.get("embedding_dim", 384)),
        vector_db=raw.get("vector_db", "qdrant"),
        vector_db_path=raw.get("vector_db_path", "./indexes/qdrant"),
        graph_db=raw.get("graph_db", "surrealdb"),
        graph_db_path=raw.get("graph_db_path", "./indexes/surrealdb"),
        confidence_threshold=float(raw.get("confidence_threshold", 0.7)),
        search_alpha=float(raw.get("search_alpha", 0.7)),
        api_keys=api_keys,
        base_url=raw.get("base_url", ""),
    )


def _parse_review(raw: dict[str, Any]) -> ReviewConfig:
    """Parse the review section of config.yaml."""
    return ReviewConfig(
        cycle_days=int(raw.get("cycle_days", 30)),
        auto_flag_outdated_days=int(raw.get("auto_flag_outdated_days", 180)),
        domains_priority=raw.get("domains_priority", []),
    )


def load_config(config_path: Path | None = None) -> ProjectConfig:
    """Load and parse the project configuration.

    Args:
        config_path: Explicit path to config.yaml. If None, the
                     project root is auto-detected.

    Returns:
        A fully parsed ProjectConfig instance.

    Raises:
        FileNotFoundError: If config.yaml does not exist.
        yaml.YAMLError: If the YAML is malformed.
    """
    if config_path is None:
        root = _find_project_root()
        config_path = root / "config.yaml"
    else:
        root = config_path.parent

    if not config_path.is_file():
        print(f"[错误] 配置文件不存在: {config_path}", file=sys.stderr)
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    project_raw = raw.get("project", {})

    return ProjectConfig(
        name=project_raw.get("name", "Knowledge Graph"),
        version=project_raw.get("version", "0.1.0"),
        author=project_raw.get("author", ""),
        vault_type=project_raw.get("vault_type", "obsidian"),
        root_path=root,
        domains=_parse_domains(raw.get("domains", {})),
        entry_types=_parse_entry_types(raw.get("entry_types", {})),
        depth_levels=raw.get("depth_levels", {}),
        scopes=raw.get("scopes", {}),
        agent=_parse_agent(raw.get("agent", {})),
        review=_parse_review(raw.get("review", {})),
    )
