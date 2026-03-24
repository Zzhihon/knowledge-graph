#!/usr/bin/env python3
"""Python benchmark — measures current architecture's performance characteristics.

Benchmarks:
  1. Concurrency overhead (ThreadPoolExecutor)
  2. SSE stream parsing
  3. JSON parsing (LLM response format)
  4. Concurrent HTTP+SSE requests
  5. Memory per worker
  6. End-to-end pipeline simulation
"""

import json
import os
import resource
import statistics
import sys
import tempfile
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

MOCK_SERVER = os.environ.get("MOCK_SERVER", "http://127.0.0.1:9876")

# ── Realistic test data ��─────────────────────────────────────────────

SAMPLE_SSE_LINES = [
    'data: {"type":"response.output_text.delta","delta":"Go 运行时采用"}',
    'data: {"type":"response.output_text.delta","delta":" GMP 模型"}',
    'data: {"type":"response.output_text.delta","delta":"：G(Goroutine)"}',
    'data: {"type":"response.output_text.delta","delta":" 是用户态协程"}',
    'data: {"type":"response.output_text.delta","delta":"，M(Machine)"}',
    'data: {"type":"response.output_text.delta","delta":" 是操作系统线程"}',
    'data: {"type":"response.output_text.delta","delta":"，P(Processor)"}',
    'data: {"type":"response.output_text.delta","delta":" 是逻辑处理器。"}',
] * 200  # ~1600 lines, realistic SSE stream size

SAMPLE_JSON = json.dumps([
    {
        "title": "Goroutine 调度器 GMP 模型",
        "question": "Go 运行时的 GMP 调度模型如何工作？",
        "domain": "golang",
        "sub_domain": "runtime",
        "entry_type": "principle",
        "depth": "deep",
        "tags": ["goroutine", "scheduler", "GMP"],
        "analysis": "Go 运行时采用 GMP 模型。" * 100,
        "key_insights": ["P 的数量默认等于 CPU 核数"] * 5,
        "related_topics": ["channel", "sync.Pool"],
    }
] * 3)  # ~3 entries, typical extraction result

# ── Benchmark 1: Concurrency Overhead ────────────────────────────────

def bench_concurrency_overhead():
    """Measure ThreadPoolExecutor worker creation and scheduling overhead."""
    results = {}
    for n_workers in [10, 50, 100, 500, 1000]:
        tracemalloc.start()
        mem_before = tracemalloc.get_traced_memory()[0]

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(lambda: time.sleep(0.001)) for _ in range(n_workers)]
            for f in as_completed(futures):
                f.result()
        elapsed = time.perf_counter() - start

        mem_after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()

        results[n_workers] = {
            "wall_time_ms": round(elapsed * 1000, 2),
            "memory_delta_kb": round((mem_after - mem_before) / 1024, 2),
            "per_worker_us": round(elapsed * 1_000_000 / n_workers, 2),
        }
    return results


# ── Benchmark 2: SSE Stream Parsing ──────────────────────────────────

def bench_sse_parsing():
    """Parse SSE lines (simulating streaming LLM response)."""
    iterations = 100
    times = []

    for _ in range(iterations):
        start = time.perf_counter()
        response_text = ""
        for line in SAMPLE_SSE_LINES:
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                data = json.loads(data_str)
                if data.get("type") == "response.output_text.delta":
                    response_text += data.get("delta", "")
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return {
        "iterations": iterations,
        "lines_per_iter": len(SAMPLE_SSE_LINES),
        "mean_ms": round(statistics.mean(times) * 1000, 3),
        "p50_ms": round(sorted(times)[len(times) // 2] * 1000, 3),
        "p99_ms": round(sorted(times)[int(len(times) * 0.99)] * 1000, 3),
        "throughput_lines_per_sec": round(len(SAMPLE_SSE_LINES) / statistics.mean(times)),
    }


# ── Benchmark 3: JSON Parsing ────────────────────────────────────────

def bench_json_parsing():
    """Parse LLM-style JSON responses (knowledge entry arrays)."""
    iterations = 1000
    times = []

    for _ in range(iterations):
        start = time.perf_counter()
        entries = json.loads(SAMPLE_JSON)
        # Simulate field access (what ingest.py does)
        for entry in entries:
            _ = entry.get("title", "")
            _ = entry.get("domain", "")
            _ = entry.get("tags", [])
            _ = entry.get("analysis", "")
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return {
        "iterations": iterations,
        "json_size_bytes": len(SAMPLE_JSON.encode()),
        "mean_us": round(statistics.mean(times) * 1_000_000, 2),
        "p50_us": round(sorted(times)[len(times) // 2] * 1_000_000, 2),
        "p99_us": round(sorted(times)[int(len(times) * 0.99)] * 1_000_000, 2),
        "throughput_parses_per_sec": round(1.0 / statistics.mean(times)),
    }


# ── Benchmark 4: Concurrent HTTP+SSE Requests ───────────────────────

def _do_sse_request(client: httpx.Client, url: str) -> dict:
    """Single SSE request + parse (mirrors api_client.py pattern)."""
    start = time.perf_counter()
    response_text = ""
    token_count = 0

    with client.stream(
        "POST", f"{url}/v1/responses",
        json={"model": "mock", "input": "test", "max_output_tokens": 4096, "stream": True},
        headers={"Authorization": "Bearer mock-key", "Content-Type": "application/json"},
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or line.startswith(":"):
                continue
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "response.output_text.delta":
                    response_text += data.get("delta", "")
                    token_count += 1

    elapsed = time.perf_counter() - start
    return {"elapsed_ms": round(elapsed * 1000, 2), "tokens": token_count, "bytes": len(response_text)}


def bench_concurrent_http(concurrency_levels=None):
    """Concurrent HTTP+SSE requests at various concurrency levels."""
    if concurrency_levels is None:
        concurrency_levels = [1, 4, 8, 16, 32]

    results = {}
    total_requests = 32  # Fixed total work

    for n in concurrency_levels:
        client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        request_times = []

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [
                pool.submit(_do_sse_request, client, MOCK_SERVER)
                for _ in range(total_requests)
            ]
            for f in as_completed(futures):
                try:
                    result = f.result()
                    request_times.append(result["elapsed_ms"])
                except Exception as e:
                    request_times.append(-1)
        total_elapsed = time.perf_counter() - start
        client.close()

        successful = [t for t in request_times if t > 0]
        results[n] = {
            "total_requests": total_requests,
            "concurrency": n,
            "total_time_ms": round(total_elapsed * 1000, 2),
            "throughput_rps": round(total_requests / total_elapsed, 2),
            "mean_latency_ms": round(statistics.mean(successful), 2) if successful else -1,
            "p99_latency_ms": round(sorted(successful)[int(len(successful) * 0.99)], 2) if successful else -1,
            "failures": total_requests - len(successful),
        }

    return results


# ── Benchmark 5: Memory Per Worker ───────────────────────────────────

def bench_memory_per_worker():
    """Measure memory overhead per ThreadPoolExecutor worker."""
    results = {}
    for n in [10, 50, 100, 500]:
        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()
        mem_before = tracemalloc.get_traced_memory()[0]

        # Create workers that hold state (simulating real worker context)
        workers_alive = []
        barrier = __import__("threading").Barrier(n + 1)

        def worker():
            # Simulate worker state: HTTP client buffer + JSON parsing context
            local_buf = bytearray(8192)  # 8KB buffer per worker
            local_data = {"entries": [], "config": {"model": "test"}}
            barrier.wait()  # Hold all workers alive
            return local_buf, local_data

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(worker) for _ in range(n)]
            barrier.wait()  # Release all workers

            mem_after = tracemalloc.get_traced_memory()[0]

            for f in futures:
                f.result()

        tracemalloc.stop()

        results[n] = {
            "workers": n,
            "total_memory_kb": round((mem_after - mem_before) / 1024, 2),
            "per_worker_kb": round((mem_after - mem_before) / 1024 / n, 2),
        }

    return results


# ── Benchmark 6: End-to-End Pipeline Simulation ─────────────────────

def bench_pipeline_e2e():
    """Simulate full RSS pipeline: fetch → parse → LLM extract → write."""
    results = {}
    n_articles = 16  # Simulate 16 RSS articles

    for n_workers in [1, 4, 8, 16]:
        client = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
        article_times = []

        start = time.perf_counter()

        def process_article(article_id):
            t0 = time.perf_counter()

            # Phase 1: Simulate RSS content extraction (local, no LLM)
            content = f"Article {article_id} content " * 200  # ~4KB

            # Phase 2: LLM extraction call (HTTP+SSE)
            result = _do_sse_request(client, MOCK_SERVER)

            # Phase 3: JSON parsing
            # (response is already parsed in _do_sse_request, simulate post-processing)
            time.sleep(0.001)  # Simulate file write

            elapsed = time.perf_counter() - t0
            return {"article_id": article_id, "elapsed_ms": round(elapsed * 1000, 2)}

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = [pool.submit(process_article, i) for i in range(n_articles)]
            for f in as_completed(futures):
                article_times.append(f.result())

        total_elapsed = time.perf_counter() - start
        client.close()

        latencies = [a["elapsed_ms"] for a in article_times]
        results[n_workers] = {
            "articles": n_articles,
            "workers": n_workers,
            "total_time_ms": round(total_elapsed * 1000, 2),
            "throughput_articles_per_sec": round(n_articles / total_elapsed, 2),
            "mean_latency_ms": round(statistics.mean(latencies), 2),
            "p99_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.99)], 2),
        }

    return results


# ── Runner ───────────────────────────────────────────────────────────

def check_server():
    """Check if mock SSE server is running."""
    try:
        resp = httpx.get(f"{MOCK_SERVER}/health", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def main():
    print("=" * 60)
    print("Python Benchmark Suite (ThreadPoolExecutor + httpx)")
    print(f"Python {sys.version.split()[0]} | PID {os.getpid()}")
    print("=" * 60)

    all_results = {}

    # Bench 1: Concurrency overhead
    print("\n[1/6] Concurrency Overhead...")
    all_results["concurrency_overhead"] = bench_concurrency_overhead()
    for k, v in all_results["concurrency_overhead"].items():
        print(f"  {k:>5} workers: {v['wall_time_ms']:>8.2f}ms total, {v['per_worker_us']:>8.2f}μs/worker, mem +{v['memory_delta_kb']:.0f}KB")

    # Bench 2: SSE parsing
    print("\n[2/6] SSE Stream Parsing...")
    all_results["sse_parsing"] = bench_sse_parsing()
    r = all_results["sse_parsing"]
    print(f"  {r['lines_per_iter']} lines × {r['iterations']} iters: mean={r['mean_ms']:.3f}ms, p99={r['p99_ms']:.3f}ms, throughput={r['throughput_lines_per_sec']} lines/s")

    # Bench 3: JSON parsing
    print("\n[3/6] JSON Parsing...")
    all_results["json_parsing"] = bench_json_parsing()
    r = all_results["json_parsing"]
    print(f"  {r['json_size_bytes']}B × {r['iterations']} iters: mean={r['mean_us']:.2f}μs, p99={r['p99_us']:.2f}μs, throughput={r['throughput_parses_per_sec']} parses/s")

    # Network benchmarks (require mock server)
    server_ok = check_server()
    if server_ok:
        # Bench 4: Concurrent HTTP
        print("\n[4/6] Concurrent HTTP+SSE Requests...")
        all_results["concurrent_http"] = bench_concurrent_http()
        for k, v in all_results["concurrent_http"].items():
            print(f"  concurrency={k:>2}: {v['throughput_rps']:>6.2f} rps, mean={v['mean_latency_ms']:>8.2f}ms, total={v['total_time_ms']:>8.2f}ms")

        # Bench 5: Memory per worker
        print("\n[5/6] Memory Per Worker...")
        all_results["memory_per_worker"] = bench_memory_per_worker()
        for k, v in all_results["memory_per_worker"].items():
            print(f"  {k:>3} workers: total={v['total_memory_kb']:>8.2f}KB, per_worker={v['per_worker_kb']:>6.2f}KB")

        # Bench 6: E2E pipeline
        print("\n[6/6] End-to-End Pipeline Simulation...")
        all_results["pipeline_e2e"] = bench_pipeline_e2e()
        for k, v in all_results["pipeline_e2e"].items():
            print(f"  {k:>2} workers: {v['throughput_articles_per_sec']:>6.2f} articles/s, mean={v['mean_latency_ms']:>8.2f}ms, total={v['total_time_ms']:>8.2f}ms")
    else:
        print("\n[4-6] SKIPPED — Mock SSE server not running")
        print(f"  Start with: go run benchmarks/mock_sse_server.go")

    # Write results
    output_path = Path(__file__).parent / "results_python.json"
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
