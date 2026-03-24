"""Web page adapter for knowledge ingestion.

Fetches a single URL, extracts clean text content,
and converts to SourceDocument format.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html import unescape

import html2text
import httpx
from trafilatura import bare_extraction, extract

from agents.sources.base import SourceDocument

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _extract_title_from_html(html: str) -> str | None:
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:title["\']',
        r'<title[^>]*>(.*?)</title>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = unescape(match.group(1)).strip()
            if title:
                return re.sub(r'\s+', ' ', title)
    return None


class WebAdapter:
    """Adapter for fetching and extracting content from a single web page."""

    def __init__(self) -> None:
        self.h2t = html2text.HTML2Text()
        self.h2t.ignore_links = False
        self.h2t.ignore_images = False
        self.h2t.body_width = 0

    def fetch_url(
        self,
        url: str,
        timeout: int = 30,
        extra_tags: list[str] | None = None,
    ) -> SourceDocument:
        """Fetch a URL and extract content into a SourceDocument.

        Args:
            url: The web page URL to fetch.
            timeout: HTTP request timeout in seconds.
            extra_tags: Additional tags to attach to the document.

        Returns:
            SourceDocument with extracted content.

        Raises:
            ValueError: If the URL cannot be fetched or has no extractable content.
        """
        logger.info(f"Fetching URL: {url}")

        # Fetch HTML
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"HTTP {exc.response.status_code} fetching {url}") from exc
        except httpx.RequestError as exc:
            raise ValueError(f"Failed to fetch URL: {exc}") from exc

        html = resp.text
        if not html or len(html.strip()) < 100:
            raise ValueError("No extractable content found at URL")

        # Extract metadata via bare_extraction
        meta = bare_extraction(html, url=url, include_comments=False)
        title = (meta.title if meta else None) or _extract_title_from_html(html) or url
        author = (meta.author if meta else None) or ""
        date_str = meta.date if meta else None

        if date_str:
            try:
                published = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                published = datetime.now(tz=timezone.utc)
        else:
            published = datetime.now(tz=timezone.utc)

        # Extract clean text — trafilatura first, html2text fallback
        clean_text = extract(html, include_comments=False, include_tables=True)
        if not clean_text:
            clean_text = self.h2t.handle(html)

        if not clean_text or len(clean_text.strip()) < 200:
            raise ValueError("No extractable content found at URL")

        # Build tags
        tags = ["source:web"]
        if extra_tags:
            tags.extend(extra_tags)

        return SourceDocument(
            source_type="web_article",
            source_id=url,
            title=title,
            content=clean_text.strip(),
            timestamp=published,
            author=author,
            url=url,
            tags=tags,
            quality_signals={"content_length": len(clean_text)},
            metadata={"fetch_url": url},
        )
