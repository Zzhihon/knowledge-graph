"""Compatibility wrapper for project configuration loading."""

from __future__ import annotations

from agents.config import (
    AgentConfig,
    APIKeyConfig,
    DomainConfig,
    EntryTypeConfig,
    FallbackConfig,
    ProjectConfig,
    ReviewConfig,
    load_config,
)

__all__ = [
    "AgentConfig",
    "APIKeyConfig",
    "DomainConfig",
    "EntryTypeConfig",
    "FallbackConfig",
    "ProjectConfig",
    "ReviewConfig",
    "load_config",
]
