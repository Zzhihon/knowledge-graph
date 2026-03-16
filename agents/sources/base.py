"""Base classes for source adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SourceDocument:
    """Unified intermediate format for all source adapters.

    All adapters output this format, which then flows into
    the existing ingest pipeline.
    """

    source_type: str          # "rss_article" | "github_pr" | "slack_thread"
    source_id: str            # Unique identifier (URL, PR#, thread_ts)
    title: str
    content: str              # Markdown-formatted content
    timestamp: datetime
    author: str | list[str]
    url: str                  # Canonical URL for reference

    # Domain hints for ingestion
    domain: str | None = None
    tags: list[str] = field(default_factory=list)

    # Quality signals (pre-computed by adapter)
    quality_signals: dict[str, Any] = field(default_factory=dict)

    # Source-specific metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Convert to markdown format consumable by ingest pipeline.

        Returns:
            Markdown string with metadata header and content body.
        """
        lines = [
            f"# {self.title}",
            "",
            f"**来源**: {self.source_type}",
            f"**作者**: {self._format_author()}",
            f"**时间**: {self.timestamp.strftime('%Y-%m-%d')}",
            f"**链接**: {self.url}",
            "",
        ]

        if self.domain:
            lines.append(f"**领域**: {self.domain}")

        if self.tags:
            lines.append(f"**标签**: {', '.join(self.tags)}")

        lines.extend(["", "---", "", self.content])

        return "\n".join(lines)

    def _format_author(self) -> str:
        if isinstance(self.author, list):
            return ", ".join(self.author)
        return self.author


class BaseAdapter(ABC):
    """Base class for all source adapters."""

    @abstractmethod
    def fetch(self, since: datetime | None = None) -> list[SourceDocument]:
        """Fetch documents from the source.

        Args:
            since: Only fetch documents newer than this timestamp.
                   If None, fetch all available documents.

        Returns:
            List of SourceDocument objects.
        """
        pass

    @abstractmethod
    def get_watermark(self) -> datetime | str | None:
        """Get the current watermark (last fetch timestamp/cursor).

        Returns:
            Watermark value, or None if never fetched.
        """
        pass

    @abstractmethod
    def set_watermark(self, watermark: datetime | str) -> None:
        """Update the watermark after successful fetch.

        Args:
            watermark: New watermark value to persist.
        """
        pass
