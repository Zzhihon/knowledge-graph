"""API client manager with load balancing support.

Manages multiple API keys with different models and provides
weighted load balancing. Supports both Anthropic and OpenAI models
through a unified interface.
"""

from __future__ import annotations

import os
import random
from typing import Any, Generator

import anthropic
import httpx

from agents.config import AgentConfig, APIKeyConfig


def _is_openai_model(model: str) -> bool:
    """Check if a model name requires the OpenAI SDK."""
    return model.startswith(("gpt-", "o1", "o3", "o4"))


class UnifiedClient:
    """Wraps either Anthropic or OpenAI client behind a common interface."""

    def __init__(self, key_config: APIKeyConfig, base_url: str):
        self.key_config = key_config
        self.model = key_config.model
        self.is_openai = _is_openai_model(key_config.model)

        if self.is_openai:
            # 使用 httpx 直接调用 /v1/responses (OpenAI Responses API)
            self._api_key = key_config.key
            self._base_url = base_url.rstrip("/") if base_url else "https://api.openai.com"
            self._http = httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0))
            self._openai = None
            self._anthropic = None
        else:
            # 清除环境变量以避免冲突
            env_backup = {}
            for env_key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]:
                if env_key in os.environ:
                    env_backup[env_key] = os.environ.pop(env_key)
            try:
                self._anthropic = anthropic.Anthropic(
                    api_key=key_config.key,
                    base_url=base_url if base_url else None,
                    http_client=httpx.Client(
                        timeout=httpx.Timeout(180.0, connect=30.0),
                    ),
                )
            finally:
                for env_key, env_value in env_backup.items():
                    os.environ[env_key] = env_value
            self._openai = None

    def stream_extract(
        self,
        prompt: str,
        max_tokens: int = 16384,
    ) -> tuple[str, str]:
        """Send extraction prompt and return (response_text, stop_reason).

        Uses streaming for both Anthropic and OpenAI to avoid proxy timeouts.
        """
        if self.is_openai:
            return self._stream_openai(prompt, max_tokens)
        else:
            return self._stream_anthropic(prompt, max_tokens)

    def _stream_anthropic(self, prompt: str, max_tokens: int) -> tuple[str, str]:
        """Stream via Anthropic SDK."""
        response_text = ""
        with self._anthropic.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                response_text += text
            message = stream.get_final_message()

        return response_text, message.stop_reason or "end_turn"

    def _stream_openai(self, prompt: str, max_tokens: int) -> tuple[str, str]:
        """Stream via OpenAI Responses API (/v1/responses)."""
        import json

        response_text = ""
        stop_reason = "end_turn"

        with self._http.stream(
            "POST",
            f"{self._base_url}/v1/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": prompt,
                "max_output_tokens": max_tokens,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    # Extract text delta from response.output_text.delta events
                    event_type = data.get("type", "")
                    if event_type == "response.output_text.delta":
                        response_text += data.get("delta", "")
                    elif event_type == "response.completed":
                        usage = data.get("response", {}).get("usage", {})
                        if usage.get("output_tokens") and usage["output_tokens"] >= max_tokens:
                            stop_reason = "max_tokens"

        return response_text, stop_reason


class APIClientManager:
    """Manages multiple API clients with load balancing."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.clients: list[tuple[UnifiedClient, APIKeyConfig]] = []

        if config.api_keys:
            for key_config in config.api_keys:
                # 跳过 embedding 专用 key (weight=0)
                if key_config.weight <= 0:
                    continue
                client = UnifiedClient(key_config, config.base_url)
                self.clients.append((client, key_config))
        else:
            # Fallback: 用环境变量创建默认 Anthropic 客户端
            default_kc = APIKeyConfig(
                key="", model=config.model, weight=1.0, description="Default (env)",
            )
            client = UnifiedClient(default_kc, config.base_url)
            self.clients.append((client, default_kc))

        self._current_index = 0

    def get_client(self, prefer_model: str | None = None) -> tuple[UnifiedClient, str]:
        """Get a client using weighted random load balancing."""
        if not self.clients:
            raise RuntimeError("No API clients configured")

        # 如果指定了 prefer_model，优先匹配
        if prefer_model:
            for client, kc in self.clients:
                if kc.model == prefer_model:
                    return client, kc.model

        # 加权随机
        total_weight = sum(kc.weight for _, kc in self.clients)
        rand = random.uniform(0, total_weight)
        cumulative = 0.0
        for client, kc in self.clients:
            cumulative += kc.weight
            if rand <= cumulative:
                return client, kc.model

        return self.clients[0][0], self.clients[0][1].model

    def get_all_models(self) -> list[str]:
        return [kc.model for _, kc in self.clients]

    def get_client_info(self) -> list[dict[str, Any]]:
        return [
            {
                "model": kc.model,
                "weight": kc.weight,
                "description": kc.description,
                "type": "openai" if _is_openai_model(kc.model) else "anthropic",
            }
            for _, kc in self.clients
        ]
