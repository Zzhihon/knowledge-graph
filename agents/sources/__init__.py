"""Multi-source ingestion adapters.

Unified interface for pulling knowledge from external sources:
- RSS feeds (blogs, podcasts)
- GitHub PR comments (future)
- Slack discussions (future)
"""

from agents.sources.base import BaseAdapter, SourceDocument

__all__ = ["BaseAdapter", "SourceDocument"]
