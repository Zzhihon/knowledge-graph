#!/usr/bin/env bash
# run_benchmarks.sh — Runs Python vs Go benchmarks with mock SSE server
set -euo pipefail

cd "$(dirname "$0")/.."
BENCH_DIR="benchmarks"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║       Python vs Go LLM Pipeline Benchmark Suite         ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# 1. Build Go benchmark binary
echo "→ Building Go benchmark..."
(cd "$BENCH_DIR/go_bench" && go build -o ../go_bench_bin .)
echo "  ✓ Go binary built"

# 2. Start mock SSE server
echo "→ Starting mock SSE server (latency=500ms)..."
go run "$BENCH_DIR/mock_sse_server.go" -latency 500 -port 9876 &
SERVER_PID=$!
sleep 1

# Verify server is up
if curl -sf http://127.0.0.1:9876/health > /dev/null 2>&1; then
    echo "  ✓ Mock SSE server running (PID=$SERVER_PID)"
else
    echo "  ✗ Failed to start mock server"
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi

cleanup() {
    echo ""
    echo "→ Cleaning up..."
    kill $SERVER_PID 2>/dev/null || true
    rm -f "$BENCH_DIR/go_bench_bin"
    echo "  ✓ Done"
}
trap cleanup EXIT

# 3. Run Python benchmark
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PYTHON BENCHMARK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
MOCK_SERVER="http://127.0.0.1:9876" python3 "$BENCH_DIR/bench_python.py"

# 4. Run Go benchmark
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  GO BENCHMARK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
MOCK_SERVER="http://127.0.0.1:9876" "$BENCH_DIR/go_bench_bin"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Results: $BENCH_DIR/results_python.json"
echo "           $BENCH_DIR/results_go.json"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
