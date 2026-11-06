"""Score `search_knowledge` through the real MCP protocol on the retrieval evaluation set.

    python -m kg.mcp.evaluate [--rerank] [--out FILE]

Starts `kg mcp serve` as a child process, speaks JSON-RPC over its stdin / stdout as an agent
would (initialize → tools/call search_knowledge for every labelled question), and scores the
returned ids with `kg.eval.retrieval.score`, the same scorer as `kg eval run`. The numbers should
match the `hybrid` (or `hybrid-rerank`) report of #7: anything lower means the MCP layer (the
secret filter, the result shape) loses hits. A tool error counts as an error, not as a miss.

The report goes to `<vault>/.kg/eval/report-<time>-mcp-hybrid[-rerank].json` unless `--out`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class StdioClient:
    """The smallest MCP client: one server process, requests answered in order."""

    def __init__(self, argv: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
                 ) -> None:
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, cwd=cwd, env=env or {**os.environ, "PYTHONPATH": str(ROOT)})
        self._id = 0
        self.info = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "kg-mcp-evaluate", "version": "0"}})
        self.notify("notifications/initialized")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method,
                    **({"params": params} if params is not None else {})})
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            reply = json.loads(line)
            if reply.get("id") == self._id:
                if "error" in reply:
                    raise RuntimeError(reply["error"].get("message", "MCP error"))
                return reply["result"]
        raise RuntimeError("MCP 服务提前退出了")

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": tool, "arguments": arguments})
        text = result["content"][0]["text"]
        if result.get("isError"):
            raise RuntimeError(text)
        return json.loads(text)

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)


class ToolError(RuntimeError):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(f"{error.get('message')}（{error.get('hint')}）")
        self.code = error.get("code", "tool_error")


def main(argv: list[str] | None = None) -> int:
    from kg.eval.retrieval import eval_dir, load_cases, score
    from kg.infrastructure.config.loader import load_config

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rerank", action="store_true", help="search_knowledge(rerank=true)")
    ap.add_argument("--out", type=Path, help="报告文件，默认写到 <vault>/.kg/eval/")
    args = ap.parse_args(argv)

    vault = load_config().vault_path
    cases = load_cases(eval_dir(vault) / "retrieval.jsonl")
    client = StdioClient([sys.executable, "-m", "kg.interfaces.cli.commands.mcp", "serve"])

    def run(q: str, k: int) -> list[str]:
        data = client.call("search_knowledge", {"query": q, "limit": k, "rerank": args.rerank})
        if "error" in data:
            raise ToolError(data["error"])
        return [r["id"] for r in data["results"]]

    name = "mcp-hybrid" + ("-rerank" if args.rerank else "")
    try:
        result = score(name, run, cases)
    finally:
        client.close()
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out = args.out or eval_dir(vault) / f"report-{stamp}-{name}.json"
    out.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}  题数 {result.cases}  hit@1 {result.hit1:.3f}  recall@5 {result.recall5:.3f}  "
          f"recall@10 {result.recall10:.3f}  MRR@10 {result.mrr10:.3f}  出错 {result.errors}  "
          f"{result.seconds}s  → {out}")
    return 0 if not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
