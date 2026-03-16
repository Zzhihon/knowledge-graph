"""RSS feed adapter for knowledge ingestion.

Fetches articles from RSS/Atom feeds, extracts clean content,
and converts to SourceDocument format.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import feedparser
import html2text
from trafilatura import extract

from agents.sources.base import BaseAdapter, SourceDocument
from agents.sources.state import SourceStateManager

logger = logging.getLogger(__name__)


class RSSAdapter(BaseAdapter):
    """Adapter for RSS/Atom feeds."""

    def __init__(
        self,
        feed_url: str,
        feed_name: str,
        domain: str | None = None,
        tags: list[str] | None = None,
        quality_weight: float = 1.0,
        state_manager: SourceStateManager | None = None,
    ):
        """Initialize RSS adapter.

        Args:
            feed_url: RSS/Atom feed URL.
            feed_name: Human-readable feed name.
            domain: Knowledge domain hint.
            tags: Default tags for all articles from this feed.
            quality_weight: Quality multiplier (0.0-1.0).
            state_manager: State manager for watermark persistence.
        """
        self.feed_url = feed_url
        self.feed_name = feed_name
        self.domain = domain
        self.tags = tags or []
        self.quality_weight = quality_weight
        self.state_manager = state_manager or SourceStateManager()

        # html2text converter
        self.h2t = html2text.HTML2Text()
        self.h2t.ignore_links = False
        self.h2t.ignore_images = False
        self.h2t.body_width = 0  # No wrapping

    def fetch(self, since: datetime | None = None) -> list[SourceDocument]:
        """Fetch articles from the RSS feed.

        Args:
            since: Only fetch articles published after this time.

        Returns:
            List of SourceDocument objects.
        """
        logger.info(f"Fetching RSS feed: {self.feed_name} ({self.feed_url})")

        try:
            feed = feedparser.parse(self.feed_url)
        except Exception as exc:
            logger.error(f"Failed to parse feed {self.feed_url}: {exc}")
            return []

        if feed.bozo:
            logger.warning(f"Feed parsing warning for {self.feed_url}: {feed.bozo_exception}")

        documents: list[SourceDocument] = []

        for entry in feed.entries:
            try:
                doc = self._process_entry(entry, since)
                if doc:
                    documents.append(doc)
            except Exception as exc:
                logger.warning(f"Failed to process entry {entry.get('link', 'unknown')}: {exc}")
                continue

        logger.info(f"Fetched {len(documents)} articles from {self.feed_name}")
        return documents

    def _process_entry(
        self,
        entry: Any,
        since: datetime | None,
    ) -> SourceDocument | None:
        """Process a single feed entry.

        Args:
            entry: feedparser entry object.
            since: Cutoff timestamp.

        Returns:
            SourceDocument or None if filtered out.
        """
        # Extract basic fields
        title = entry.get("title", "Untitled")
        link = entry.get("link", "")
        author = entry.get("author", self.feed_name)

        # Parse published date
        published_tuple = entry.get("published_parsed") or entry.get("updated_parsed")
        if published_tuple:
            published = datetime(*published_tuple[:6], tzinfo=timezone.utc)
        else:
            published = datetime.now(tz=timezone.utc)

        # Filter by date
        if since and published <= since:
            return None

        # Extract content
        content_html = self._get_content_html(entry)
        if not content_html:
            logger.debug(f"Skipping entry with no content: {link}")
            return None

        # Extract clean text using trafilatura
        clean_text = extract(content_html, include_comments=False, include_tables=True)
        if not clean_text:
            # Fallback to html2text
            clean_text = self.h2t.handle(content_html)

        if not clean_text or len(clean_text.strip()) < 100:
            logger.debug(f"Skipping short/empty content: {link}")
            return None

        # Build SourceDocument
        return SourceDocument(
            source_type="rss_article",
            source_id=link,
            title=title,
            content=clean_text.strip(),
            timestamp=published,
            author=author,
            url=link,
            domain=self.domain,
            tags=self.tags.copy(),
            quality_signals={
                "feed_quality_weight": self.quality_weight,
                "content_length": len(clean_text),
            },
            metadata={
                "feed_name": self.feed_name,
                "feed_url": self.feed_url,
            },
        )

    def _get_content_html(self, entry: Any) -> str:
        """Extract HTML content from entry.

        Tries multiple fields in order of preference.
        """
        # Try content field (Atom)
        if "content" in entry and entry.content:
            return entry.content[0].get("value", "")

        # Try summary/description (RSS)
        if "summary" in entry:
            return entry.summary

        if "description" in entry:
            return entry.description

        return ""

    def get_watermark(self) -> datetime | None:
        """Get last fetch timestamp for this feed."""
        state = self.state_manager.get_state("rss", self.feed_url)
        if state and "last_published" in state:
            return datetime.fromisoformat(state["last_published"])
        return None

    def set_watermark(self, watermark: datetime) -> None:
        """Update last fetch timestamp."""
        self.state_manager.set_state(
            "rss",
            self.feed_url,
            {
                "last_published": watermark.isoformat(),
                "last_checked": datetime.now(tz=timezone.utc).isoformat(),
            },
        )
