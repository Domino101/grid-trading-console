package engine

import (
	"runtime"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// MetricsCollector 延遲與吞吐量統計收集器
type MetricsCollector struct {
	mu           sync.Mutex
	latenciesNs  []int64
	sampleLimit  int
	totalOrders  uint64
	totalMatched uint64
	startTime    time.Time
}

// NewMetricsCollector 建立度量收集器
func NewMetricsCollector(sampleLimit int) *MetricsCollector {
	if sampleLimit <= 0 {
		sampleLimit = 500000
	}
	return &MetricsCollector{
		latenciesNs: make([]int64, 0, 100000),
		sampleLimit: sampleLimit,
		startTime:   time.Now(),
	}
}

// RecordLatency 紀錄單筆訂單處理耗時 (納秒)
func (mc *MetricsCollector) RecordLatency(latencyNs int64, matched bool) {
	atomic.AddUint64(&mc.totalOrders, 1)
	if matched {
		atomic.AddUint64(&mc.totalMatched, 1)
	}

	mc.mu.Lock()
	if len(mc.latenciesNs) < mc.sampleLimit {
		mc.latenciesNs = append(mc.latenciesNs, latencyNs)
	}
	mc.mu.Unlock()
}

// PerformanceSummary 效能摘要報告
type PerformanceSummary struct {
	TotalOrders      uint64  `json:"total_orders"`
	TotalMatched     uint64  `json:"total_matched"`
	ElapsedSec       float64 `json:"elapsed_sec"`
	ThroughputQPS    float64 `json:"throughput_qps"`
	P50LatencyMicros float64 `json:"p50_latency_us"`
	P90LatencyMicros float64 `json:"p90_latency_us"`
	P99LatencyMicros float64 `json:"p99_latency_us"`
	MaxLatencyMicros float64 `json:"max_latency_us"`
	MemoryAllocMB    float64 `json:"memory_alloc_mb"`
	MemorySysMB      float64 `json:"memory_sys_mb"`
	NumGoroutine     int     `json:"num_goroutine"`
}

// GetSummary 計算並取得當前效能統計 (百分位數分析)
func (mc *MetricsCollector) GetSummary() PerformanceSummary {
	mc.mu.Lock()
	copied := make([]int64, len(mc.latenciesNs))
	copy(copied, mc.latenciesNs)
	mc.mu.Unlock()

	var p50, p90, p99, maxUs float64
	if len(copied) > 0 {
		sort.Slice(copied, func(i, j int) bool { return copied[i] < copied[j] })
		n := len(copied)
		p50 = float64(copied[n*50/100]) / 1000.0
		p90 = float64(copied[n*90/100]) / 1000.0
		p99 = float64(copied[n*99/100]) / 1000.0
		maxUs = float64(copied[n-1]) / 1000.0
	}

	elapsed := time.Since(mc.startTime).Seconds()
	tot := atomic.LoadUint64(&mc.totalOrders)
	var qps float64
	if elapsed > 0 {
		qps = float64(tot) / elapsed
	}

	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	return PerformanceSummary{
		TotalOrders:      tot,
		TotalMatched:     atomic.LoadUint64(&mc.totalMatched),
		ElapsedSec:       elapsed,
		ThroughputQPS:    qps,
		P50LatencyMicros: p50,
		P90LatencyMicros: p90,
		P99LatencyMicros: p99,
		MaxLatencyMicros: maxUs,
		MemoryAllocMB:    float64(m.Alloc) / (1024 * 1024),
		MemorySysMB:      float64(m.Sys) / (1024 * 1024),
		NumGoroutine:     runtime.NumGoroutine(),
	}
}
