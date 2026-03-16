"""API client manager with load balancing support.

Manages multiple API keys with different models and provides
round-robin or weighted load balancing.
"""

from __future__ import annotations

import random
from typing import Any

import anthropic
import httpx

from agents.config import AgentConfig, APIKeyConfig


class APIClientManager:
    """Manages multiple API clients with load balancing."""

    def __init__(self, config: AgentConfig):
        """Initialize the API client manager.

        Args:
            config: Agent configuration with API keys.
        """
        self.config = config
        self.clients: list[tuple[anthropic.Anthropic, APIKeyConfig]] = []

        # Create clients for each API key
        if config.api_keys:
            for key_config in config.api_keys:
                # 创建客户端时，需要清除环境变量以避免冲突
                import os
                env_backup = {}
                for env_key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]:
                    if env_key in os.environ:
                        env_backup[env_key] = os.environ.pop(env_key)

                try:
                    client = anthropic.Anthropic(
                        api_key=key_config.key,
                        base_url=config.base_url if config.base_url else None,
                        http_client=httpx.Client(
                            timeout=httpx.Timeout(180.0, connect=30.0),
                        ),
                    )
                    self.clients.append((client, key_config))
                finally:
                    # 恢复环境变量
                    for env_key, env_value in env_backup.items():
                        os.environ[env_key] = env_value
        else:
            # Fallback to environment variable
            client = anthropic.Anthropic(
                base_url=config.base_url if config.base_url else None,
                http_client=httpx.Client(
                    timeout=httpx.Timeout(180.0, connect=30.0),
                ),
            )
            # Create a default key config
            default_key_config = APIKeyConfig(
                key="",  # Will use env var
                model=config.model,
                weight=1.0,
                description="Default (from env)",
            )
            self.clients.append((client, default_key_config))

        self._current_index = 0

    def get_client(self, prefer_model: str | None = None) -> tuple[anthropic.Anthropic, str]:
        """Get an API client using load balancing.

        Args:
            prefer_model: Preferred model name. If specified, tries to find
                         a client configured for that model.

        Returns:
            Tuple of (client, model_name).
        """
        if not self.clients:
            raise RuntimeError("No API clients configured")

        # If prefer_model is specified, try to find a matching client
        if prefer_model:
            for client, key_config in self.clients:
                if key_config.model == prefer_model:
                    return client, key_config.model

        # Weighted random selection
        total_weight = sum(kc.weight for _, kc in self.clients)
        if total_weight == 0:
            # All weights are 0, use round-robin
            client, key_config = self.clients[self._current_index]
            self._current_index = (self._current_index + 1) % len(self.clients)
            return client, key_config.model

        # Weighted random
        rand = random.uniform(0, total_weight)
        cumulative = 0.0
        for client, key_config in self.clients:
            cumulative += key_config.weight
            if rand <= cumulative:
                return client, key_config.model

        # Fallback to first client
        return self.clients[0][0], self.clients[0][1].model

    def get_all_models(self) -> list[str]:
        """Get all configured model names.

        Returns:
            List of model names.
        """
        return [key_config.model for _, key_config in self.clients]

    def get_client_info(self) -> list[dict[str, Any]]:
        """Get information about all configured clients.

        Returns:
            List of client info dicts.
        """
        return [
            {
                "model": key_config.model,
                "weight": key_config.weight,
                "description": key_config.description,
            }
            for _, key_config in self.clients
        ]
