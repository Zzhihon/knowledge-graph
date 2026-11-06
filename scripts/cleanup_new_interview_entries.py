#!/usr/bin/env python3
"""Safely clean recently generated interview entries.

This script only applies low-risk normalization by default:
- Strict entry subset selection via interview type + ID prefix
- Date normalization to UTC ISO format
- domain/tags array normalization
- focus_topic cleanup (trim/separator normalization/common typo fixes)
- body section header normalization

Riskier changes (title/category suggestions) are report-only unless --apply-risky
is explicitly enabled.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHECKPOINT_FILE = ROOT / ".interview_cleanup_checkpoint.json"
REPORT_FILE = ROOT / ".interview_cleanup_report.json"
INTERVIEW_DIR = ROOT / "09-Interview"
DEFAULT_PREFIX = "iq-20260326-"
DATE_FIELDS = ("created", "updated", "review_date", "last_reviewed")
HEADER_MAP = {
    "## Question": "## 问题",
    "## Answer": "## 参考答案",
    "## Key Points": "## 答案要点",
}
FOCUS_TOPIC_TYPO_MAP = {
    "chanel": "channel",
}


@dataclass
class CleanupResult:
    entry_id: str
    path: str
    changed: bool
    applied_changes: list[str]
    suggested_title: str | None
    suggested_category: str | None
    errors: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely clean newly generated interview entries.",
    )
    parser.add_argument(
        "--entry-id-prefix",
        default=DEFAULT_PREFIX,
        help=(
            "Only process interview entries whose id starts with this prefix "
            f"(default: {DEFAULT_PREFIX})."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of matched entries to process.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files or checkpoint.",
    )
    parser.add_argument(
        "--apply-risky",
        action="store_true",
        help="Apply suggested title/category changes in addition to safe cleanup.",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Reset checkpoint before processing.",
    )
    return parser.parse_args()


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_checkpoint(path: Path) -> None:
    if path.exists():
        path.unlink()


def iso_utc_now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None

    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt


def normalize_datetime_value(value: Any) -> str | None:
    dt = parse_datetime_value(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_list_field(value: Any) -> list[Any]:
    if value is None:
        return []
    candidates = value if isinstance(value, list) else [value]

    normalized: list[Any] = []
    seen: set[str] = set()
    for item in candidates:
        if item is None:
            continue
        if isinstance(item, str):
            cleaned = item.strip()
            if not cleaned:
                continue
            dedupe_key = cleaned.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(cleaned)
        else:
            dedupe_key = repr(item)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(item)
    return normalized


def normalize_focus_topic(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        raw = ", ".join(str(v) for v in value if str(v).strip())
    else:
        raw = str(value)
    raw = raw.strip()
    if not raw:
        return ""

    normalized = re.sub(r"\s*([，、,/])\s*", ", ", raw)
    parts = [part.strip() for part in normalized.split(",")]

    cleaned_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        fixed = part
        for typo, replacement in FOCUS_TOPIC_TYPO_MAP.items():
            fixed = re.sub(typo, replacement, fixed, flags=re.IGNORECASE)
        dedupe_key = fixed.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned_parts.append(fixed)

    return ", ".join(cleaned_parts)


def normalize_body_headers(content: str) -> tuple[str, bool]:
    changed = False
    new_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        replacement = HEADER_MAP.get(stripped)
        if replacement is not None and stripped != replacement:
            indent = line[: len(line) - len(line.lstrip())]
            new_lines.append(f"{indent}{replacement}")
            changed = True
        else:
            new_lines.append(line)
    normalized = "\n".join(new_lines)
    if content.endswith("\n"):
        normalized += "\n"
    return normalized, changed


def suggest_title(title: Any) -> str | None:
    if not isinstance(title, str):
        return None
    cleaned = " ".join(title.split())
    if len(cleaned) <= 60:
        return None
    for sep in ("？", "。", "：", ":", "，", ","):
        idx = cleaned.find(sep)
        if 16 <= idx <= 60:
            candidate = cleaned[: idx + 1].strip()
            if candidate and candidate != cleaned:
                return candidate
    candidate = cleaned[:57].rstrip(" ，,：:") + "..."
    return candidate if candidate != cleaned else None


def suggest_category(title: Any, content: str, current: Any) -> str | None:
    if not isinstance(title, str):
        return None
    text = f"{title}\n{content}"
    suggested = None
    if re.search(r"请结合.*项目|你在项目中|你做过的项目|一次具体经历|项目经历", text):
        suggested = "project-deep-dive"
    elif re.search(r"技术选型|为什么选择|如何选择|trade-?off|选型", text, flags=re.IGNORECASE):
        suggested = "tech-choices"
    elif re.search(r"如果|线上|生产|故障|场景|你会如何|延迟升高|问题同时涉及", text):
        suggested = "real-scenarios"
    if suggested and suggested != current:
        return suggested
    return None


def collect_target_entries(entry_id_prefix: str, limit: int | None) -> list[dict[str, Any]]:
    from agents.config import load_config
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path, filters={"type": "interview"})
    matched = []
    for entry in entries:
        meta = entry["metadata"]
        entry_id = str(meta.get("id", ""))
        path = Path(entry["path"])
        if not str(path).startswith(str(INTERVIEW_DIR)):
            continue
        if not entry_id.startswith(entry_id_prefix):
            continue
        matched.append(entry)
    matched.sort(key=lambda item: str(item["path"]))
    if limit is not None:
        matched = matched[:limit]
    return matched


def process_entry(entry: dict[str, Any], apply_risky: bool, dry_run: bool) -> CleanupResult:
    path = Path(entry["path"])
    post = frontmatter.load(path)
    metadata = post.metadata
    content = post.content
    applied_changes: list[str] = []
    errors: list[str] = []
    changed = False

    for field in DATE_FIELDS:
        if field not in metadata:
            continue
        original = metadata.get(field)
        normalized = normalize_datetime_value(original)
        if normalized is None:
            errors.append(f"unparseable_{field}: {original}")
            continue
        if original != normalized:
            metadata[field] = normalized
            applied_changes.append(f"normalized_{field}")
            changed = True

    for field in ("domain", "tags"):
        if field not in metadata:
            continue
        original = metadata.get(field)
        normalized = normalize_list_field(original)
        if original != normalized:
            metadata[field] = normalized
            applied_changes.append(f"normalized_{field}")
            changed = True

    if "focus_topic" in metadata:
        original = metadata.get("focus_topic")
        normalized = normalize_focus_topic(original)
        if normalized is not None and original != normalized:
            metadata["focus_topic"] = normalized
            applied_changes.append("cleaned_focus_topic")
            changed = True

    normalized_content, header_changed = normalize_body_headers(content)
    if header_changed:
        post.content = normalized_content
        applied_changes.append("normalized_body_headers")
        changed = True

    suggested_title = suggest_title(metadata.get("title"))
    suggested_category = suggest_category(
        metadata.get("title"),
        post.content,
        metadata.get("category"),
    )

    if apply_risky:
        if suggested_title and metadata.get("title") != suggested_title:
            metadata["title"] = suggested_title
            applied_changes.append("applied_suggested_title")
            changed = True
        if suggested_category and metadata.get("category") != suggested_category:
            metadata["category"] = suggested_category
            applied_changes.append("applied_suggested_category")
            changed = True

    if changed:
        metadata["updated"] = iso_utc_now()
        if "normalized_updated" not in applied_changes:
            applied_changes.append("updated_timestamp")

    if changed and not dry_run:
        path.write_text(frontmatter.dumps(post), encoding="utf-8")

    return CleanupResult(
        entry_id=str(metadata.get("id", "")),
        path=str(path),
        changed=changed,
        applied_changes=applied_changes,
        suggested_title=suggested_title,
        suggested_category=suggested_category,
        errors=errors,
    )


def main() -> int:
    args = parse_args()

    if args.reset_checkpoint and not args.dry_run:
        reset_checkpoint(CHECKPOINT_FILE)

    checkpoint = load_checkpoint(CHECKPOINT_FILE) if not args.dry_run else {}
    checkpoint_done = set(checkpoint.get("done", []))
    failed_from_checkpoint = checkpoint.get("failed", [])
    checkpoint_failed = (
        list(failed_from_checkpoint) if isinstance(failed_from_checkpoint, list) else []
    )

    matched_entries = collect_target_entries(args.entry_id_prefix, args.limit)
    pending_entries = [
        entry for entry in matched_entries
        if str(entry["metadata"].get("id", "")) not in checkpoint_done
    ]

    report_entries: list[dict[str, Any]] = []
    safe_changes = 0
    risky_suggestions = 0
    parse_failures = 0
    changed_files = 0
    failed_runs: list[dict[str, str]] = []

    run_started_at = checkpoint.get("run_started_at") if checkpoint else None
    if not run_started_at:
        run_started_at = iso_utc_now()

    active_checkpoint = {
        "selector": {"entry_id_prefix": args.entry_id_prefix},
        "done": sorted(checkpoint_done),
        "failed": checkpoint_failed,
        "run_started_at": run_started_at,
    }

    for entry in pending_entries:
        entry_id = str(entry["metadata"].get("id", ""))
        try:
            result = process_entry(entry, apply_risky=args.apply_risky, dry_run=args.dry_run)
        except Exception as exc:
            failed_runs.append({"id": entry_id, "error": str(exc)})
            parse_failures += 1
            report_entries.append({
                "id": entry_id,
                "path": str(entry["path"]),
                "applied_changes": [],
                "suggested_title": None,
                "suggested_category": None,
                "errors": [str(exc)],
            })
            if not args.dry_run:
                active_checkpoint.setdefault("failed", []).append(
                    {"id": entry_id, "error": str(exc)}
                )
                save_checkpoint(CHECKPOINT_FILE, active_checkpoint)
            continue

        if result.changed:
            changed_files += 1
            safe_changes += len([
                change for change in result.applied_changes
                if not change.startswith("applied_suggested_")
            ])
        if result.errors:
            parse_failures += len(result.errors)
        if result.suggested_title or result.suggested_category:
            risky_suggestions += 1

        report_entries.append({
            "id": result.entry_id,
            "path": result.path,
            "applied_changes": result.applied_changes,
            "suggested_title": result.suggested_title,
            "suggested_category": result.suggested_category,
            "errors": result.errors,
        })

        if not args.dry_run:
            checkpoint_done.add(result.entry_id)
            active_checkpoint["done"] = sorted(checkpoint_done)
            save_checkpoint(CHECKPOINT_FILE, active_checkpoint)

    report_payload = {
        "selector": {"entry_id_prefix": args.entry_id_prefix},
        "dry_run": args.dry_run,
        "apply_risky": args.apply_risky,
        "run_started_at": run_started_at,
        "summary": {
            "matched": len(matched_entries),
            "pending": len(pending_entries),
            "safe_changes": safe_changes,
            "risky_suggestions": risky_suggestions,
            "parse_failures": parse_failures,
            "changed_files": changed_files,
            "failed_entries": len(failed_runs),
        },
        "entries": report_entries,
    }
    REPORT_FILE.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"matched={len(matched_entries)}")
    print(f"pending={len(pending_entries)}")
    print(f"safe_changes={safe_changes}")
    print(f"risky_suggestions={risky_suggestions}")
    print(f"parse_failures={parse_failures}")
    print(f"changed_files={changed_files}")
    print(f"report={REPORT_FILE}")
    if args.dry_run:
        print("checkpoint=skipped (dry-run)")
    else:
        print(f"checkpoint={CHECKPOINT_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
