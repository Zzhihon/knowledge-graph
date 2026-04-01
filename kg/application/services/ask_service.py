"""Compatibility surface for ask/RAG behavior during the staged migration."""

from __future__ import annotations

from agents.ask import ask, ask_stream

__all__ = ["ask", "ask_stream"]
