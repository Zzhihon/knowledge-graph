"""Shared JSON parsing utilities for robust LLM output handling.

Extracted from ingest.py to be reused by problem_generator and other
modules that parse Claude API responses.
"""

from __future__ import annotations

import json
from typing import Any


def parse_json_robust(text: str) -> Any:
    """Parse JSON with fallback repair for common LLM output issues.

    Handles:
      1. Control characters inside string values (strict=False)
      2. Unescaped double quotes inside JSON string values
      3. Truncated output (find last complete object, close array)
    """
    # Attempt 1: standard parse
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        pass

    # Attempt 2: fix unescaped double quotes inside string values.
    # Claude sometimes writes  是"逻辑单线程"  with real " instead of \"
    repaired = fix_unescaped_quotes(text)
    try:
        return json.loads(repaired, strict=False)
    except json.JSONDecodeError:
        pass

    # Attempt 3: truncation repair — find the last complete JSON object
    last_brace = repaired.rfind("}")
    if last_brace > 0:
        candidate = repaired[: last_brace + 1].rstrip()

        # Remove trailing comma if present
        if candidate.endswith(","):
            candidate = candidate[:-1]

        # Ensure array is properly closed
        if not candidate.rstrip().endswith("]"):
            # Find the last complete object
            if candidate.count("{") > candidate.count("}"):
                # Incomplete object, remove it
                last_complete = candidate.rfind("},")
                if last_complete > 0:
                    candidate = candidate[: last_complete + 1]
            candidate += "\n]"

        try:
            return json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            pass

    raise RuntimeError(
        f"无法解析 Claude 返回的 JSON（所有修复策略均失败）\n"
        f"响应长度: {len(text)} 字符\n"
        f"响应内容（前 1000 字符）: {text[:1000]}\n"
        f"响应内容（后 500 字符）: ...{text[-500:]}"
    )


def fix_unescaped_quotes(text: str) -> str:
    """Escape double quotes that appear inside JSON string values.

    Walks the text character-by-character, tracking whether we are
    inside a JSON string.  When we encounter a ``"`` that is clearly
    mid-value (next char is not a JSON structural char), we escape it.
    """
    result: list[str] = []
    in_string = False
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == "\\" and in_string:
            # Escaped character — pass through both chars
            result.append(ch)
            if i + 1 < n:
                i += 1
                result.append(text[i])
            i += 1
            continue

        if ch == '"':
            if not in_string:
                # Opening a string
                in_string = True
                result.append(ch)
            else:
                # Could be the real closing quote, or an unescaped interior quote.
                # Heuristic: peek at the next non-whitespace character.
                # If it's a JSON structural char ( : , } ] ) then this is
                # the real closing quote.  Otherwise it's interior — escape it.
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] in ":,}]\n":
                    # Real closing quote
                    in_string = False
                    result.append(ch)
                elif j >= n:
                    # End of text — must be the closing quote
                    in_string = False
                    result.append(ch)
                else:
                    # Interior quote — escape it
                    result.append('\\"')
        else:
            result.append(ch)

        i += 1

    return "".join(result)


def strip_code_fence(text: str) -> str:
    """Remove outer markdown code fence from Claude API response text.

    Only strips the first/last fence lines, preserving any inner code
    blocks that may appear inside JSON string values like "analysis".
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text
