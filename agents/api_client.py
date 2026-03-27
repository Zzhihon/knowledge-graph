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
            # Auto-detect input format: "list" or "string", None = not yet probed
            self._openai_input_fmt: str | None = None
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

    _CONT_MSG = "请继续输出，从上次中断处严格续写，不要重复已输出的内容。"

    def stream_extract(
        self,
        prompt: str,
        max_tokens: int = 16384,
        max_continuations: int = 0,
        max_retries: int = 3,
    ) -> tuple[str, str]:
        """Send extraction prompt and return (response_text, stop_reason).

        Uses streaming to avoid proxy timeouts. When *max_continuations* > 0,
        automatically continues on ``max_tokens`` hit or mid-stream connection
        drop (if partial text was already received).
        """
        if self.is_openai:
            return self._stream_openai(prompt, max_tokens, max_continuations, max_retries)
        else:
            return self._stream_anthropic(prompt, max_tokens, max_continuations, max_retries)

    # -- Anthropic ---------------------------------------------------------

    def _stream_anthropic(
        self, prompt: str, max_tokens: int, max_continuations: int = 0, max_retries: int = 3,
    ) -> tuple[str, str]:
        """Stream via Anthropic SDK with optional auto-continuation and zero-byte retry."""
        import time
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        full_text = ""
        continuations = 0

        while True:
            chunk = ""
            stop_reason = "end_turn"
            need_continue = False
            last_exc: Exception | None = None

            for attempt in range(max_retries):
                chunk = ""
                try:
                    with self._anthropic.messages.stream(
                        model=self.model,
                        max_tokens=max_tokens,
                        messages=messages,
                    ) as stream:
                        for text in stream.text_stream:
                            chunk += text
                        msg = stream.get_final_message()
                        stop_reason = msg.stop_reason or "end_turn"
                        need_continue = stop_reason == "max_tokens"
                    last_exc = None
                    break  # success
                except Exception as exc:
                    last_exc = exc
                    if chunk:
                        # Got partial data — don't retry, go to continuation logic
                        need_continue = continuations < max_continuations
                        break
                    # Zero-byte drop — wait and retry
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)  # 1s, 2s, 4s
                    continue

            if last_exc and not chunk:
                raise last_exc

            full_text += chunk

            if not need_continue or continuations >= max_continuations:
                return full_text, stop_reason

            continuations += 1
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": full_text},
                {"role": "user", "content": self._CONT_MSG},
            ]

    # -- OpenAI Responses API ----------------------------------------------

    def _build_openai_input(self, prompt: str, fmt: str) -> str | list:
        """Build input payload in the requested format."""
        if fmt == "list":
            return [{"role": "user", "content": prompt}]
        return prompt  # "string"

    def _do_openai_stream(
        self, input_data: str | list, max_tokens: int,
    ) -> tuple[str, str]:
        """Execute a single OpenAI Responses API streaming call.

        Returns (chunk_text, stop_reason).
        Raises httpx.HTTPStatusError on 4xx/5xx.
        """
        import json as _json

        chunk = ""
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
                "input": input_data,
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
                        data = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        continue
                    event_type = data.get("type", "")
                    if event_type == "response.output_text.delta":
                        chunk += data.get("delta", "")
                    elif event_type == "response.completed":
                        usage = data.get("response", {}).get("usage", {})
                        if (
                            usage.get("output_tokens")
                            and usage["output_tokens"] >= max_tokens
                        ):
                            stop_reason = "max_tokens"

        return chunk, stop_reason

    def _probe_openai_format(self, prompt: str, max_tokens: int) -> tuple[str, str, str]:
        """Try both input formats, cache the one that works.

        Returns (chunk_text, stop_reason, winning_format).
        """
        import httpx as _httpx

        # Order: try list first (more common with proxies), then string
        formats = ["list", "string"]
        if self._openai_input_fmt == "string":
            formats = ["string", "list"]

        last_exc: Exception | None = None
        for fmt in formats:
            input_data = self._build_openai_input(prompt, fmt)
            try:
                chunk, stop_reason = self._do_openai_stream(input_data, max_tokens)
                self._openai_input_fmt = fmt  # cache for future calls
                return chunk, stop_reason, fmt
            except _httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    last_exc = exc
                    continue  # try the other format
                raise  # non-format errors propagate immediately
            except Exception:
                raise

        raise last_exc  # type: ignore[misc]

    def _stream_openai(
        self, prompt: str, max_tokens: int, max_continuations: int = 0, max_retries: int = 3,
    ) -> tuple[str, str]:
        """Stream via OpenAI Responses API with optional auto-continuation and zero-byte retry."""
        import time
        import httpx as _httpx

        full_text = ""
        continuations = 0
        # Resolved format after first successful call
        resolved_fmt: str | None = self._openai_input_fmt

        while True:
            chunk = ""
            stop_reason = "end_turn"
            need_continue = False
            last_exc: Exception | None = None

            for attempt in range(max_retries):
                chunk = ""
                try:
                    if resolved_fmt is None:
                        # First call: probe both formats
                        chunk, stop_reason, resolved_fmt = self._probe_openai_format(prompt, max_tokens)
                    else:
                        # Known format — build input directly
                        if continuations == 0:
                            input_data = self._build_openai_input(prompt, resolved_fmt)
                        else:
                            # Continuations always use list format
                            input_data = [
                                {"role": "user", "content": prompt},
                                {"role": "assistant", "content": full_text},
                                {"role": "user", "content": self._CONT_MSG},
                            ]
                        chunk, stop_reason = self._do_openai_stream(input_data, max_tokens)

                    need_continue = stop_reason == "max_tokens"
                    last_exc = None
                    break  # success
                except _httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 400 and resolved_fmt is not None:
                        # Cached format stopped working — reset and re-probe
                        resolved_fmt = None
                        self._openai_input_fmt = None
                        continue
                    last_exc = exc
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                    continue
                except Exception as exc:
                    last_exc = exc
                    if chunk:
                        need_continue = continuations < max_continuations
                        break
                    if attempt < max_retries - 1:
                        time.sleep(2 ** attempt)
                    continue

            if last_exc and not chunk:
                raise last_exc

            full_text += chunk

            if not need_continue or continuations >= max_continuations:
                return full_text, stop_reason

            continuations += 1


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


# ── 全局单例 ─────────────────────────────────────────────────────────
# 所有模块应通过此处获取 LLM 客户端，而非直接 anthropic.Anthropic()

_manager: APIClientManager | None = None


def _get_manager() -> APIClientManager:
    """Lazy init global APIClientManager singleton."""
    global _manager
    if _manager is None:
        from agents.config import load_config
        _manager = APIClientManager(load_config().agent)
    return _manager


def get_fallback_client(key_index: int = 0) -> tuple[anthropic.Anthropic, str]:
    """获取指定索引的 Anthropic 客户端，绕过 weight 过滤。

    用于 fallback 场景（知识库 miss 时直接调用 Key1）。

    Args:
        key_index: api_keys 列表中的索引，默认 0 (Key1)。

    Returns:
        (client, model_name)
    """
    from agents.config import load_config

    config = load_config().agent
    if not config.api_keys or key_index >= len(config.api_keys):
        raise RuntimeError(f"api_keys[{key_index}] not configured")

    key_config = config.api_keys[key_index]
    uc = UnifiedClient(key_config, config.base_url)
    if uc._anthropic is None:
        raise RuntimeError(f"api_keys[{key_index}] is not an Anthropic client")
    return uc._anthropic, key_config.model


def get_anthropic_client() -> tuple[anthropic.Anthropic, str]:
    """获取受权重控制的 Anthropic 客户端。

    替代直接调用 anthropic.Anthropic()，确保走 config 中的
    负载均衡逻辑，不受环境变量影响。

    Returns:
        (client, model_name) — client 是原生 anthropic.Anthropic 实例
    """
    mgr = _get_manager()
    # 只从 Anthropic 类型的客户端中选
    anthropic_clients = [
        (c, kc) for c, kc in mgr.clients if not _is_openai_model(kc.model)
    ]
    if not anthropic_clients:
        # 没有配置 Anthropic key，fallback 到任意可用的
        uc, model = mgr.get_client()
        if uc._anthropic is None:
            raise RuntimeError("No Anthropic client available")
        return uc._anthropic, model

    # 加权随机选一个 Anthropic 客户端
    total = sum(kc.weight for _, kc in anthropic_clients)
    rand = random.uniform(0, total)
    cumulative = 0.0
    for uc, kc in anthropic_clients:
        cumulative += kc.weight
        if rand <= cumulative:
            return uc._anthropic, kc.model

    uc, kc = anthropic_clients[0]
    return uc._anthropic, kc.model
