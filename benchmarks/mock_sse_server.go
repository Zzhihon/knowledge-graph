// mock_sse_server.go — Simulates LLM streaming API responses (Anthropic SSE format)
// Both Python and Go benchmarks hit this server for fair comparison.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"strings"
	"sync/atomic"
	"time"
)

var (
	requestCount atomic.Int64
	port         int
	latencyMs    int
	chunkSize    int
	responseLen  int
)

// Simulated knowledge entry JSON (realistic size matching actual LLM output)
func generateResponseJSON() string {
	entries := []map[string]interface{}{
		{
			"title":          "Goroutine 调度器 GMP 模型深度解析",
			"question":       "Go 运行时的 GMP 调度模型如何工作？",
			"domain":         "golang",
			"sub_domain":     "runtime",
			"entry_type":     "principle",
			"depth":          "deep",
			"tags":           []string{"goroutine", "scheduler", "GMP", "runtime"},
			"analysis":       strings.Repeat("Go 运行时采用 GMP 模型：G(Goroutine) 是用户态协程，M(Machine) 是操作系统线程，P(Processor) 是逻辑处理器。调度器通过 work-stealing 算法在 P 之间均衡负载。", 5),
			"key_insights":   []string{"P 的数量默认等于 CPU 核数", "work-stealing 确保负载均衡", "阻塞系统调用会触发 M-P 解绑"},
			"related_topics": []string{"channel", "sync.Pool", "GOMAXPROCS"},
		},
		{
			"title":          "Kubernetes Pod 调度策略与亲和性",
			"question":       "如何通过亲和性规则控制 Pod 调度？",
			"domain":         "cloud-native",
			"sub_domain":     "kubernetes",
			"entry_type":     "pattern",
			"depth":          "intermediate",
			"tags":           []string{"kubernetes", "scheduling", "affinity", "node-selector"},
			"analysis":       strings.Repeat("Kubernetes 提供多层次调度控制：nodeSelector 是最简单的节点选择，nodeAffinity 提供更灵活的表达式，podAffinity/podAntiAffinity 控制 Pod 间关系。", 5),
			"key_insights":   []string{"preferredDuringScheduling 是软约束", "requiredDuringScheduling 是硬约束", "拓扑域控制跨 AZ 分布"},
			"related_topics": []string{"taints-tolerations", "topology-spread", "scheduler-plugins"},
		},
	}
	data, _ := json.Marshal(entries)
	return string(data)
}

func sseHandler(w http.ResponseWriter, r *http.Request) {
	requestCount.Add(1)

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	response := generateResponseJSON()

	// Simulate streaming: send chunks with inter-token delay
	tokenDelay := time.Duration(latencyMs) * time.Millisecond / time.Duration(len(response)/chunkSize+1)

	for i := 0; i < len(response); i += chunkSize {
		end := i + chunkSize
		if end > len(response) {
			end = len(response)
		}
		chunk := response[i:end]

		event := map[string]interface{}{
			"type":  "response.output_text.delta",
			"delta": chunk,
		}
		eventJSON, _ := json.Marshal(event)
		fmt.Fprintf(w, "data: %s\n\n", eventJSON)
		flusher.Flush()

		if tokenDelay > 0 {
			time.Sleep(tokenDelay)
		}
	}

	// Send completion event
	usage := map[string]interface{}{
		"type": "response.completed",
		"response": map[string]interface{}{
			"usage": map[string]int{
				"input_tokens":  512,
				"output_tokens": len(response) / 4,
			},
		},
	}
	usageJSON, _ := json.Marshal(usage)
	fmt.Fprintf(w, "data: %s\n\n", usageJSON)
	fmt.Fprintf(w, "data: [DONE]\n\n")
	flusher.Flush()
}

func statsHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"total_requests": requestCount.Load(),
	})
}

// /health for readiness check
func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("ok"))
}

func main() {
	flag.IntVar(&port, "port", 9876, "Server port")
	flag.IntVar(&latencyMs, "latency", 500, "Total simulated response latency in ms")
	flag.IntVar(&chunkSize, "chunk", 50, "Characters per SSE chunk")
	flag.IntVar(&responseLen, "response-len", 0, "unused, auto from template")
	flag.Parse()

	// Add jitter to latency
	rand.New(rand.NewSource(time.Now().UnixNano()))

	mux := http.NewServeMux()
	mux.HandleFunc("/v1/responses", sseHandler)
	mux.HandleFunc("/v1/messages", sseHandler) // Anthropic-style endpoint
	mux.HandleFunc("/stats", statsHandler)
	mux.HandleFunc("/health", healthHandler)

	addr := fmt.Sprintf(":%d", port)
	log.Printf("Mock SSE server starting on %s (latency=%dms, chunk=%d)", addr, latencyMs, chunkSize)
	log.Fatal(http.ListenAndServe(addr, mux))
}
