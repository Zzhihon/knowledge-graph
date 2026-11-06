#!/usr/bin/env python3
"""Turn a raw egress probe log into a fixture that is safe for a public repository.

Keeps only ts, status, curl_rc, tls_ms, total_ms and the ip_ok boolean; drops the exit IP,
location, data-center code and anything else that could identify the line.

    python scripts/egress_fixture.py <raw.ndjson> tests/fixtures/egress_probe_history.ndjson
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KEEP = ("ts", "status", "curl_rc", "tls_ms", "total_ms")


def scrub(rec: dict) -> dict:
    out = {k: rec[k] for k in KEEP if k in rec}
    if isinstance(rec.get("ip_ok"), bool):
        out["ip_ok"] = rec["ip_ok"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="raw probe ndjson (read only)")
    parser.add_argument("output", type=Path, help="fixture to write")
    args = parser.parse_args(argv)
    rows = []
    for text in args.source.read_text(encoding="utf-8").splitlines():
        if text.strip():
            rows.append(scrub(json.loads(text)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"{len(rows)} rows → {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
