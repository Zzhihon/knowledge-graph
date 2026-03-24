// Go benchmark — equivalent to bench_python.py for fair comparison.
// Uses bytedance/sonic for high-performance JSON parsing.
//
// Benchmarks:
//  1. Concurrency overhead (goroutines + errgroup)
//  2. SSE stream parsing (sonic)
//  3. JSON parsing — encoding/json vs sonic comparison
//  4. Concurrent HTTP+SSE requests (sonic)
//  5. Memory per worker
//  6. End-to-end pipeline simulation (sonic)
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/bytedance/sonic"
)

var mockServer = "http://127.0.0.1:9876"

func init() {
	if s := os.Getenv("MOCK_SERVER"); s != "" {
		mockServer = s
	}
}

// ── Types ───────────────────────────────────────────────────────────

type KnowledgeEntry struct {
	Title         string   `json:"title"`
	Question      string   `json:"question"`
	Domain        string   `json:"domain"`
	SubDomain     string   `json:"sub_domain"`
	EntryType     string   `json:"entry_type"`
	Depth         string   `json:"depth"`
	Tags          []string `json:"tags"`
	Analysis      string   `json:"analysis"`
	KeyInsights   []string `json:"key_insights"`
	RelatedTopics []string `json:"related_topics"`
}

// ── Benchmark 1: Concurrency Overhead ───────────────────────────────

func benchConcurrencyOverhead() map[int]map[string]interface{} {
	results := make(map[int]map[string]interface{})

	for _, n := range []int{10, 50, 100, 500, 1000, 10000, 100000} {
		var memBefore runtime.MemStats
		runtime.GC()
		runtime.ReadMemStats(&memBefore)

		start := time.Now()
		var wg sync.WaitGroup
		wg.Add(n)
		for i := 0; i < n; i++ {
			go func() {
				defer wg.Done()
				time.Sleep(1 * time.Millisecond)
			}()
		}
		wg.Wait()
		elapsed := time.Since(start)

		var memAfter runtime.MemStats
		runtime.ReadMemStats(&memAfter)

		memDelta := int64(memAfter.TotalAlloc) - int64(memBefore.TotalAlloc)
		if memDelta < 0 {
			memDelta = 0
		}

		results[n] = map[string]interface{}{
			"wall_time_ms":    round2(float64(elapsed.Microseconds()) / 1000.0),
			"memory_delta_kb": round2(float64(memDelta) / 1024.0),
			"per_worker_us":   round2(float64(elapsed.Microseconds()) / float64(n)),
		}
	}
	return results
}

// ── Benchmark 2: SSE Stream Parsing (sonic) ─────────────────────────

func generateSSELines() []string {
	base := []string{
		`data: {"type":"response.output_text.delta","delta":"Go 运行时采用"}`,
		`data: {"type":"response.output_text.delta","delta":" GMP 模型"}`,
		`data: {"type":"response.output_text.delta","delta":"：G(Goroutine)"}`,
		`data: {"type":"response.output_text.delta","delta":" 是用户态协程"}`,
		`data: {"type":"response.output_text.delta","delta":"，M(Machine)"}`,
		`data: {"type":"response.output_text.delta","delta":" 是操作系统线程"}`,
		`data: {"type":"response.output_text.delta","delta":"，P(Processor)"}`,
		`data: {"type":"response.output_text.delta","delta":" 是逻辑处理器。"}`,
	}
	lines := make([]string, 0, len(base)*200)
	for i := 0; i < 200; i++ {
		lines = append(lines, base...)
	}
	return lines
}

type SSEEvent struct {
	Type  string `json:"type"`
	Delta string `json:"delta"`
}

func benchSSEParsing() map[string]interface{} {
	lines := generateSSELines()
	iterations := 100

	// Benchmark with sonic
	sonicTimes := make([]float64, 0, iterations)
	for i := 0; i < iterations; i++ {
		start := time.Now()
		var sb strings.Builder
		for _, line := range lines {
			if line == "" || line[0] == ':' {
				continue
			}
			if strings.HasPrefix(line, "data: ") {
				dataStr := line[6:]
				if dataStr == "[DONE]" {
					break
				}
				var evt SSEEvent
				if err := sonic.UnmarshalString(dataStr, &evt); err != nil {
					continue
				}
				if evt.Type == "response.output_text.delta" {
					sb.WriteString(evt.Delta)
				}
			}
		}
		elapsed := time.Since(start)
		sonicTimes = append(sonicTimes, float64(elapsed.Microseconds())/1000.0)
	}

	// Benchmark with encoding/json for comparison
	stdTimes := make([]float64, 0, iterations)
	for i := 0; i < iterations; i++ {
		start := time.Now()
		var sb strings.Builder
		for _, line := range lines {
			if line == "" || line[0] == ':' {
				continue
			}
			if strings.HasPrefix(line, "data: ") {
				dataStr := line[6:]
				if dataStr == "[DONE]" {
					break
				}
				var evt SSEEvent
				if err := json.Unmarshal([]byte(dataStr), &evt); err != nil {
					continue
				}
				if evt.Type == "response.output_text.delta" {
					sb.WriteString(evt.Delta)
				}
			}
		}
		elapsed := time.Since(start)
		stdTimes = append(stdTimes, float64(elapsed.Microseconds())/1000.0)
	}

	sort.Float64s(sonicTimes)
	sort.Float64s(stdTimes)
	return map[string]interface{}{
		"iterations":     iterations,
		"lines_per_iter": len(lines),
		"sonic": map[string]interface{}{
			"mean_ms":                  round3(mean(sonicTimes)),
			"p50_ms":                   round3(sonicTimes[len(sonicTimes)/2]),
			"p99_ms":                   round3(sonicTimes[int(float64(len(sonicTimes))*0.99)]),
			"throughput_lines_per_sec": int(float64(len(lines)) / (mean(sonicTimes) / 1000.0)),
		},
		"encoding_json": map[string]interface{}{
			"mean_ms":                  round3(mean(stdTimes)),
			"p50_ms":                   round3(stdTimes[len(stdTimes)/2]),
			"p99_ms":                   round3(stdTimes[int(float64(len(stdTimes))*0.99)]),
			"throughput_lines_per_sec": int(float64(len(lines)) / (mean(stdTimes) / 1000.0)),
		},
	}
}

// ── Benchmark 3: JSON Parsing — encoding/json vs sonic ──────────────

func generateSampleJSON() []byte {
	entries := make([]KnowledgeEntry, 0, 3)
	for i := 0; i < 3; i++ {
		entries = append(entries, KnowledgeEntry{
			Title:         "Goroutine 调度器 GMP 模型",
			Question:      "Go 运行时的 GMP 调度模型如何工作？",
			Domain:        "golang",
			SubDomain:     "runtime",
			EntryType:     "principle",
			Depth:         "deep",
			Tags:          []string{"goroutine", "scheduler", "GMP"},
			Analysis:      strings.Repeat("Go 运行时采用 GMP 模型。", 100),
			KeyInsights:   []string{"P 的数量默认等于 CPU 核数", "P 的数量默认等于 CPU 核数", "P 的数量默认等于 CPU 核数", "P 的数量默认等于 CPU 核数", "P 的数量默认等于 CPU 核数"},
			RelatedTopics: []string{"channel", "sync.Pool"},
		})
	}
	data, _ := sonic.Marshal(entries)
	return data
}

func benchJSONParsing() map[string]interface{} {
	jsonBytes := generateSampleJSON()
	jsonStr := string(jsonBytes)
	iterations := 1000

	// Sonic Unmarshal (typed binding)
	sonicTimes := make([]float64, 0, iterations)
	for i := 0; i < iterations; i++ {
		start := time.Now()
		var entries []KnowledgeEntry
		_ = sonic.Unmarshal(jsonBytes, &entries)
		for _, e := range entries {
			_ = e.Title
			_ = e.Domain
			_ = e.Tags
			_ = e.Analysis
		}
		elapsed := time.Since(start)
		sonicTimes = append(sonicTimes, float64(elapsed.Nanoseconds())/1000.0)
	}

	// Sonic UnmarshalString (zero-copy from string)
	sonicStrTimes := make([]float64, 0, iterations)
	for i := 0; i < iterations; i++ {
		start := time.Now()
		var entries []KnowledgeEntry
		_ = sonic.UnmarshalString(jsonStr, &entries)
		for _, e := range entries {
			_ = e.Title
			_ = e.Domain
			_ = e.Tags
			_ = e.Analysis
		}
		elapsed := time.Since(start)
		sonicStrTimes = append(sonicStrTimes, float64(elapsed.Nanoseconds())/1000.0)
	}

	// Sonic Get (lazy parse, no full deserialization)
	sonicGetTimes := make([]float64, 0, iterations)
	for i := 0; i < iterations; i++ {
		start := time.Now()
		// Parse only specific fields without full unmarshal
		node, err := sonic.Get(jsonBytes)
		if err == nil {
			arr, _ := node.ArrayUseNode()
			for _, item := range arr {
				_, _ = item.Get("title").String()
				_, _ = item.Get("domain").String()
				_, _ = item.Get("analysis").String()
			}
		}
		elapsed := time.Since(start)
		sonicGetTimes = append(sonicGetTimes, float64(elapsed.Nanoseconds())/1000.0)
	}

	// encoding/json (baseline)
	stdTimes := make([]float64, 0, iterations)
	for i := 0; i < iterations; i++ {
		start := time.Now()
		var entries []KnowledgeEntry
		_ = json.Unmarshal(jsonBytes, &entries)
		for _, e := range entries {
			_ = e.Title
			_ = e.Domain
			_ = e.Tags
			_ = e.Analysis
		}
		elapsed := time.Since(start)
		stdTimes = append(stdTimes, float64(elapsed.Nanoseconds())/1000.0)
	}

	sort.Float64s(sonicTimes)
	sort.Float64s(sonicStrTimes)
	sort.Float64s(sonicGetTimes)
	sort.Float64s(stdTimes)

	return map[string]interface{}{
		"iterations":      iterations,
		"json_size_bytes": len(jsonBytes),
		"sonic_unmarshal": map[string]interface{}{
			"mean_us":                  round2(mean(sonicTimes)),
			"p50_us":                   round2(sonicTimes[len(sonicTimes)/2]),
			"p99_us":                   round2(sonicTimes[int(float64(len(sonicTimes))*0.99)]),
			"throughput_parses_per_sec": int(1_000_000.0 / mean(sonicTimes)),
		},
		"sonic_unmarshal_string": map[string]interface{}{
			"mean_us":                  round2(mean(sonicStrTimes)),
			"p50_us":                   round2(sonicStrTimes[len(sonicStrTimes)/2]),
			"p99_us":                   round2(sonicStrTimes[int(float64(len(sonicStrTimes))*0.99)]),
			"throughput_parses_per_sec": int(1_000_000.0 / mean(sonicStrTimes)),
		},
		"sonic_get_lazy": map[string]interface{}{
			"mean_us":                  round2(mean(sonicGetTimes)),
			"p50_us":                   round2(sonicGetTimes[len(sonicGetTimes)/2]),
			"p99_us":                   round2(sonicGetTimes[int(float64(len(sonicGetTimes))*0.99)]),
			"throughput_parses_per_sec": int(1_000_000.0 / mean(sonicGetTimes)),
		},
		"encoding_json": map[string]interface{}{
			"mean_us":                  round2(mean(stdTimes)),
			"p50_us":                   round2(stdTimes[len(stdTimes)/2]),
			"p99_us":                   round2(stdTimes[int(float64(len(stdTimes))*0.99)]),
			"throughput_parses_per_sec": int(1_000_000.0 / mean(stdTimes)),
		},
	}
}

// ── Benchmark 4: Concurrent HTTP+SSE Requests (sonic) ───────────────

func doSSERequest(client *http.Client, url string) (float64, int, int, error) {
	start := time.Now()

	payload := `{"model":"mock","input":"test","max_output_tokens":4096,"stream":true}`
	req, err := http.NewRequest("POST", url+"/v1/responses", strings.NewReader(payload))
	if err != nil {
		return 0, 0, 0, err
	}
	req.Header.Set("Authorization", "Bearer mock-key")
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return 0, 0, 0, err
	}
	defer resp.Body.Close()

	var sb strings.Builder
	tokenCount := 0
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 64*1024), 64*1024)

	for scanner.Scan() {
		line := scanner.Text()
		if line == "" || line[0] == ':' {
			continue
		}
		if strings.HasPrefix(line, "data: ") {
			dataStr := line[6:]
			if dataStr == "[DONE]" {
				break
			}
			var evt SSEEvent
			if err := sonic.UnmarshalString(dataStr, &evt); err != nil {
				continue
			}
			if evt.Type == "response.output_text.delta" {
				sb.WriteString(evt.Delta)
				tokenCount++
			}
		}
	}

	elapsed := time.Since(start)
	return float64(elapsed.Microseconds()) / 1000.0, tokenCount, sb.Len(), nil
}

func benchConcurrentHTTP() map[int]map[string]interface{} {
	results := make(map[int]map[string]interface{})
	totalRequests := 32

	for _, n := range []int{1, 4, 8, 16, 32} {
		client := &http.Client{
			Transport: &http.Transport{
				MaxIdleConnsPerHost: n * 2,
				MaxConnsPerHost:     n * 2,
			},
			Timeout: 30 * time.Second,
		}

		latencies := make([]float64, 0, totalRequests)
		var mu sync.Mutex
		failures := 0

		start := time.Now()
		sem := make(chan struct{}, n)
		var wg sync.WaitGroup

		for i := 0; i < totalRequests; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()

				elapsed, _, _, err := doSSERequest(client, mockServer)
				mu.Lock()
				if err != nil {
					failures++
				} else {
					latencies = append(latencies, elapsed)
				}
				mu.Unlock()
			}()
		}
		wg.Wait()
		totalElapsed := time.Since(start)

		sort.Float64s(latencies)
		meanLat := float64(-1)
		p99Lat := float64(-1)
		if len(latencies) > 0 {
			meanLat = mean(latencies)
			p99Lat = latencies[int(float64(len(latencies))*0.99)]
		}

		results[n] = map[string]interface{}{
			"total_requests":   totalRequests,
			"concurrency":     n,
			"total_time_ms":   round2(float64(totalElapsed.Microseconds()) / 1000.0),
			"throughput_rps":   round2(float64(totalRequests) / totalElapsed.Seconds()),
			"mean_latency_ms": round2(meanLat),
			"p99_latency_ms":  round2(p99Lat),
			"failures":        failures,
		}
	}
	return results
}

// ── Benchmark 5: Memory Per Goroutine ───────────────────────────────

func benchMemoryPerWorker() map[int]map[string]interface{} {
	results := make(map[int]map[string]interface{})

	for _, n := range []int{10, 50, 100, 500, 1000, 10000, 100000} {
		runtime.GC()
		var memBefore runtime.MemStats
		runtime.ReadMemStats(&memBefore)

		ch := make(chan struct{})
		var wg sync.WaitGroup

		for i := 0; i < n; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				buf := make([]byte, 8192)
				_ = buf
				data := map[string]interface{}{
					"entries": []interface{}{},
					"config":  map[string]string{"model": "test"},
				}
				_ = data
				<-ch
			}()
		}

		runtime.Gosched()
		time.Sleep(50 * time.Millisecond)

		var memAfter runtime.MemStats
		runtime.ReadMemStats(&memAfter)

		close(ch)
		wg.Wait()

		memDelta := int64(memAfter.TotalAlloc) - int64(memBefore.TotalAlloc)
		if memDelta < 0 {
			memDelta = 0
		}

		results[n] = map[string]interface{}{
			"workers":         n,
			"total_memory_kb": round2(float64(memDelta) / 1024.0),
			"per_worker_kb":   round2(float64(memDelta) / 1024.0 / float64(n)),
		}
	}
	return results
}

// ── Benchmark 6: End-to-End Pipeline Simulation (sonic) ─────────────

func benchPipelineE2E() map[int]map[string]interface{} {
	results := make(map[int]map[string]interface{})
	nArticles := 16

	for _, nWorkers := range []int{1, 4, 8, 16} {
		client := &http.Client{
			Transport: &http.Transport{
				MaxIdleConnsPerHost: nWorkers * 2,
				MaxConnsPerHost:     nWorkers * 2,
			},
			Timeout: 30 * time.Second,
		}

		latencies := make([]float64, 0, nArticles)
		var mu sync.Mutex

		start := time.Now()
		sem := make(chan struct{}, nWorkers)
		var wg sync.WaitGroup

		for i := 0; i < nArticles; i++ {
			wg.Add(1)
			go func(articleID int) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()

				t0 := time.Now()
				_ = strings.Repeat(fmt.Sprintf("Article %d content ", articleID), 200)
				elapsed, _, _, _ := doSSERequest(client, mockServer)
				_ = elapsed
				time.Sleep(1 * time.Millisecond)

				totalMs := float64(time.Since(t0).Microseconds()) / 1000.0
				mu.Lock()
				latencies = append(latencies, totalMs)
				mu.Unlock()
			}(i)
		}
		wg.Wait()
		totalElapsed := time.Since(start)

		sort.Float64s(latencies)
		results[nWorkers] = map[string]interface{}{
			"articles":                   nArticles,
			"workers":                    nWorkers,
			"total_time_ms":              round2(float64(totalElapsed.Microseconds()) / 1000.0),
			"throughput_articles_per_sec": round2(float64(nArticles) / totalElapsed.Seconds()),
			"mean_latency_ms":            round2(mean(latencies)),
			"p99_latency_ms":             round2(latencies[int(float64(len(latencies))*0.99)]),
		}
	}
	return results
}

// ── Helpers ─────────────────────────────────────────────────────────

func mean(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	sum := 0.0
	for _, v := range vals {
		sum += v
	}
	return sum / float64(len(vals))
}

func round2(v float64) float64 { return math.Round(v*100) / 100 }
func round3(v float64) float64 { return math.Round(v*1000) / 1000 }

func checkServer() bool {
	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get(mockServer + "/health")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	io.ReadAll(resp.Body)
	return resp.StatusCode == 200
}

// ── Main ────────────────────────────────────────────────────────────

func main() {
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("Go Benchmark Suite (goroutines + net/http + sonic)")
	fmt.Printf("Go %s | GOMAXPROCS=%d | PID %d\n", runtime.Version(), runtime.GOMAXPROCS(0), os.Getpid())
	fmt.Println(strings.Repeat("=", 60))

	allResults := make(map[string]interface{})

	// Bench 1
	fmt.Println("\n[1/6] Concurrency Overhead...")
	r1 := benchConcurrencyOverhead()
	allResults["concurrency_overhead"] = r1
	for _, n := range []int{10, 50, 100, 500, 1000, 10000, 100000} {
		if v, ok := r1[n]; ok {
			fmt.Printf("  %6d goroutines: %8.2fms total, %8.2fμs/goroutine, mem +%.0fKB\n",
				n, v["wall_time_ms"], v["per_worker_us"], v["memory_delta_kb"])
		}
	}

	// Bench 2
	fmt.Println("\n[2/6] SSE Stream Parsing (sonic vs encoding/json)...")
	r2 := benchSSEParsing()
	allResults["sse_parsing"] = r2
	sonicSSE := r2["sonic"].(map[string]interface{})
	stdSSE := r2["encoding_json"].(map[string]interface{})
	fmt.Printf("  sonic:         mean=%.3fms, p99=%.3fms, throughput=%d lines/s\n",
		sonicSSE["mean_ms"], sonicSSE["p99_ms"], sonicSSE["throughput_lines_per_sec"])
	fmt.Printf("  encoding/json: mean=%.3fms, p99=%.3fms, throughput=%d lines/s\n",
		stdSSE["mean_ms"], stdSSE["p99_ms"], stdSSE["throughput_lines_per_sec"])

	// Bench 3
	fmt.Println("\n[3/6] JSON Parsing (4-way comparison)...")
	r3 := benchJSONParsing()
	allResults["json_parsing"] = r3
	sonicU := r3["sonic_unmarshal"].(map[string]interface{})
	sonicUS := r3["sonic_unmarshal_string"].(map[string]interface{})
	sonicG := r3["sonic_get_lazy"].(map[string]interface{})
	stdJ := r3["encoding_json"].(map[string]interface{})
	fmt.Printf("  %dB × %d iters:\n", r3["json_size_bytes"], r3["iterations"])
	fmt.Printf("    sonic.Unmarshal:       mean=%6.2fμs  p99=%6.2fμs  %6d parses/s\n",
		sonicU["mean_us"], sonicU["p99_us"], sonicU["throughput_parses_per_sec"])
	fmt.Printf("    sonic.UnmarshalString: mean=%6.2fμs  p99=%6.2fμs  %6d parses/s\n",
		sonicUS["mean_us"], sonicUS["p99_us"], sonicUS["throughput_parses_per_sec"])
	fmt.Printf("    sonic.Get (lazy):      mean=%6.2fμs  p99=%6.2fμs  %6d parses/s\n",
		sonicG["mean_us"], sonicG["p99_us"], sonicG["throughput_parses_per_sec"])
	fmt.Printf("    encoding/json:         mean=%6.2fμs  p99=%6.2fμs  %6d parses/s\n",
		stdJ["mean_us"], stdJ["p99_us"], stdJ["throughput_parses_per_sec"])

	// Network benchmarks
	serverOK := checkServer()
	if serverOK {
		fmt.Println("\n[4/6] Concurrent HTTP+SSE Requests (sonic)...")
		r4 := benchConcurrentHTTP()
		allResults["concurrent_http"] = r4
		for _, n := range []int{1, 4, 8, 16, 32} {
			if v, ok := r4[n]; ok {
				fmt.Printf("  concurrency=%2d: %6.2f rps, mean=%8.2fms, total=%8.2fms\n",
					n, v["throughput_rps"], v["mean_latency_ms"], v["total_time_ms"])
			}
		}

		fmt.Println("\n[5/6] Memory Per Goroutine...")
		r5 := benchMemoryPerWorker()
		allResults["memory_per_worker"] = r5
		for _, n := range []int{10, 50, 100, 500, 1000, 10000, 100000} {
			if v, ok := r5[n]; ok {
				fmt.Printf("  %6d goroutines: total=%8.2fKB, per_goroutine=%6.2fKB\n",
					n, v["total_memory_kb"], v["per_worker_kb"])
			}
		}

		fmt.Println("\n[6/6] End-to-End Pipeline Simulation (sonic)...")
		r6 := benchPipelineE2E()
		allResults["pipeline_e2e"] = r6
		for _, n := range []int{1, 4, 8, 16} {
			if v, ok := r6[n]; ok {
				fmt.Printf("  %2d workers: %6.2f articles/s, mean=%8.2fms, total=%8.2fms\n",
					n, v["throughput_articles_per_sec"], v["mean_latency_ms"], v["total_time_ms"])
			}
		}
	} else {
		fmt.Println("\n[4-6] SKIPPED — Mock SSE server not running")
		fmt.Printf("  Start with: go run benchmarks/mock_sse_server.go\n")
	}

	// Write results (use sonic for output too)
	data, _ := sonic.MarshalIndent(allResults, "", "  ")
	outputPath := "benchmarks/results_go_sonic.json"
	os.WriteFile(outputPath, data, 0644)
	fmt.Printf("\nResults saved to %s\n", outputPath)
}
