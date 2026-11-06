#!/usr/bin/env python3
"""Shared read-only helper for project-level OMX knowledge-graph skills."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kg.application.services.ask_service import ask  # noqa: E402
from kg.application.services.search_service import search  # noqa: E402


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    return parts[2].lstrip()


def _read_excerpt(file_path: str, max_chars: int = 260) -> str:
    path = Path(file_path)
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = _strip_frontmatter(content)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    excerpt = " ".join(lines[:6])
    return excerpt[:max_chars]


def _build_filters(domain: str | None, entry_type: str | None = None) -> dict[str, str] | None:
    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain
    if entry_type:
        filters["type"] = entry_type
    return filters or None


def _suggest_domain(results: list[dict[str, Any]], explicit_domain: str | None) -> str:
    if explicit_domain:
        return explicit_domain
    for item in results:
        domain = str(item.get("domain", "")).strip()
        if domain:
            return domain
    return "general"


def run_load(topic: str, top_k: int, domain: str | None) -> int:
    results = search(
        query=topic,
        filters=_build_filters(domain),
        top_k=top_k,
    )
    print(f"# KG Context: {topic}\n")

    if not results:
        print("- 未找到相关知识条目。")
        return 0

    for idx, item in enumerate(results, 1):
        excerpt = _read_excerpt(item.get("file_path", ""))
        meta = (
            f"`{item.get('id', '')}` | "
            f"{item.get('type', '')} | "
            f"{item.get('domain', '')} | "
            f"score={item.get('score', '')}"
        )
        print(
            f"{idx}. **{item.get('title', '')}** "
            f"({meta})"
        )
        if excerpt:
            print(f"   - {excerpt}")
        file_path = item.get("file_path", "")
        if file_path:
            print(f"   - path: `{file_path}`")
        print()
    return 0


def run_review(topic: str, top_k: int, domain: str | None) -> int:
    results = search(
        query=topic,
        filters=_build_filters(domain),
        top_k=top_k,
    )
    print(f"# KG Review: {topic}\n")

    if not results:
        print("- 未找到相关原则/模式/排障记录。")
        return 0

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        grouped.setdefault(item.get("type", "unknown"), []).append(item)

    order = ["principle", "pattern", "debug", "architecture", "research", "unknown"]
    for group in order:
        items = grouped.get(group)
        if not items:
            continue
        print(f"## {group}")
        for item in items:
            excerpt = _read_excerpt(item.get("file_path", ""))
            meta = f"`{item.get('id', '')}` | {item.get('domain', '')}"
            print(f"- **{item.get('title', '')}** ({meta})")
            if excerpt:
                print(f"  - {excerpt}")
        print()
    return 0


def run_ask(question: str, top_k: int, domain: str | None) -> int:
    ask(
        question=question,
        top_k=top_k,
        domain=domain,
        use_graph=True,
        enable_fallback=False,
    )
    return 0


def run_capture(
    subject: str,
    notes: str | None,
    top_k: int,
    domain: str | None,
    entry_type: str,
    depth: str,
) -> int:
    query_text = f"{subject}\n{notes or ''}".strip()
    results = search(
        query=query_text,
        filters=_build_filters(domain),
        top_k=top_k,
    )
    suggested_domain = _suggest_domain(results, domain)

    print(f"# KG Capture Proposal: {subject}\n")
    print("- mode: proposal-only")
    print(f"- suggested_domain: {suggested_domain}")
    print(f"- suggested_entry_type: {entry_type}")
    print(f"- suggested_depth: {depth}")
    print()

    if results:
        print("## Related Entries")
        for item in results:
            print(
                f"- `{item.get('id', '')}` — {item.get('title', '')} "
                f"({item.get('type', '')} | {item.get('domain', '')})"
            )
        print()

    print("## Draft")
    print("### Topic")
    print(subject)
    print()
    print("### Notes")
    print(notes.strip() if notes else "- （补充你的结论/证据/场景）")
    print()
    print("### Why it matters")
    print("- 这条结论解决了什么问题？")
    print("- 未来在什么场景下值得复用？")
    print()
    print("### Capture Decision")
    print("- 如果需要正式入库，再显式执行 ingest / integrate 流程。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only OMX skill adapter for the knowledge graph",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    load_parser = subparsers.add_parser("load", help="Load compact knowledge context for a topic")
    load_parser.add_argument("topic")
    load_parser.add_argument("--top-k", type=int, default=5)
    load_parser.add_argument("--domain", type=str, default=None)

    review_parser = subparsers.add_parser(
        "review",
        help="Review relevant principles/patterns/debug records",
    )
    review_parser.add_argument("topic")
    review_parser.add_argument("--top-k", type=int, default=8)
    review_parser.add_argument("--domain", type=str, default=None)

    ask_parser = subparsers.add_parser("ask", help="Ask the local knowledge graph directly")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--top-k", type=int, default=5)
    ask_parser.add_argument("--domain", type=str, default=None)

    capture_parser = subparsers.add_parser(
        "capture",
        help="Generate a proposal-only knowledge capture draft",
    )
    capture_parser.add_argument("subject")
    capture_parser.add_argument("--notes", type=str, default=None)
    capture_parser.add_argument("--top-k", type=int, default=5)
    capture_parser.add_argument("--domain", type=str, default=None)
    capture_parser.add_argument("--entry-type", type=str, default="research")
    capture_parser.add_argument("--depth", type=str, default="intermediate")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "load":
        return run_load(topic=args.topic, top_k=args.top_k, domain=args.domain)
    if args.command == "review":
        return run_review(topic=args.topic, top_k=args.top_k, domain=args.domain)
    if args.command == "ask":
        return run_ask(question=args.question, top_k=args.top_k, domain=args.domain)
    if args.command == "capture":
        return run_capture(
            subject=args.subject,
            notes=args.notes,
            top_k=args.top_k,
            domain=args.domain,
            entry_type=args.entry_type,
            depth=args.depth,
        )

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
