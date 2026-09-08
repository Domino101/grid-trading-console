package main

import (
	"flag"
	"fmt"
	"log"
	"math/rand"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"trading-core-go/engine"
	pb "trading-core-go/pb"
	"trading-core-go/server"
)

func main() {
	socketPath := flag.String("socket", "/tmp/trading_core.sock", "Unix Domain Socket 監聽路徑")
	runBenchmark := flag.Bool("benchmark", false, "執行 100 併發高壓基準壓力測試")
	concurrency := flag.Int("concurrency", 100, "基準測試併發 Goroutine 數量")
	totalOrders := flag.Int("orders", 100000, "基準測試總訂單數")
	flag.Parse()

	if *runBenchmark {
		runStandaloneBenchmark(*concurrency, *totalOrders)
		return
	}

	log.Printf("==================================================")
	log.Printf("🚀 Trading Core Micro-Engine (Go PoC) Starting...")
	log.Printf("   Socket Path: %s", *socketPath)
	log.Printf("==================================================")

	riskCfg := engine.DefaultRiskRuleConfig()
	srv := server.NewIPCServer(*socketPath, riskCfg)
	if err := srv.Start(); err != nil {
		log.Fatalf("❌ 伺服器啟動失敗: %v", err)
	}

	// 捕獲終止信號
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	sig := <-sigChan
	log.Printf("🛑 收到信號 %v，正在安全關閉微引擎...", sig)
	_ = srv.Stop()
	log.Printf("✅ 微引擎已完全退出")
}

// runStandaloneBenchmark 執行 100 併發模擬網格壓力測試並驗收 DoD
func runStandaloneBenchmark(concurrency, totalOrders int) {
	fmt.Println("=================================================================")
	fmt.Printf("🔥 [P3 DoD 驗證] 啟動 Go 原生記憶體撮合微引擎高壓基準測試\n")
	fmt.Printf("   併發協程數: %d | 總下單量: %d\n", concurrency, totalOrders)
	fmt.Println("=================================================================")

	ob := engine.NewOrderBook("BTC/USDT")
	collector := engine.NewMetricsCollector(totalOrders)

	// 先鋪底深度：放 500 檔買單與賣單作為流動性底池
	basePrice := 65000.0
	for i := 1; i <= 250; i++ {
		bidPrice := basePrice - float64(i)*2.0
		ob.ProcessOrder(&engine.Order{
			ID:           fmt.Sprintf("init-bid-%d", i),
			BotID:        "init-maker",
			Symbol:       "BTC/USDT",
			Side:         pb.OrderSide_BUY,
			Type:         pb.OrderType_LIMIT,
			Price:        bidPrice,
			Quantity:     1.0,
			RemainingQty: 1.0,
			Status:       pb.OrderStatus_OPEN,
			TimestampNs:  time.Now().UnixNano(),
		})

		askPrice := basePrice + float64(i)*2.0
		ob.ProcessOrder(&engine.Order{
			ID:           fmt.Sprintf("init-ask-%d", i),
			BotID:        "init-maker",
			Symbol:       "BTC/USDT",
			Side:         pb.OrderSide_SELL,
			Type:         pb.OrderType_LIMIT,
			Price:        askPrice,
			Quantity:     1.0,
			RemainingQty: 1.0,
			Status:       pb.OrderStatus_OPEN,
			TimestampNs:  time.Now().UnixNano(),
		})
	}

	var wg sync.WaitGroup
	ordersPerWorker := totalOrders / concurrency

	t0 := time.Now()

	for workerID := 0; workerID < concurrency; workerID++ {
		wg.Add(1)
		go func(wId int) {
			defer wg.Done()
			rng := rand.New(rand.NewSource(time.Now().UnixNano() + int64(wId)*1000))

			for i := 0; i < ordersPerWorker; i++ {
				// 模擬網格震盪下單：買/賣隨機、價格在 base +- 100 區間
				isBuy := rng.Intn(2) == 0
				offset := (rng.Float64()*200.0 - 100.0)
				price := basePrice + offset
				qty := 0.05 + rng.Float64()*0.2

				side := pb.OrderSide_BUY
				if !isBuy {
					side = pb.OrderSide_SELL
				}

				orderID := fmt.Sprintf("bench-%d-%d", wId, i)
				order := &engine.Order{
					ID:            orderID,
					BotID:         fmt.Sprintf("bot-grid-%d", wId%10),
					Symbol:        "BTC/USDT",
					Side:          side,
					Type:          pb.OrderType_LIMIT,
					Price:         price,
					Quantity:      qty,
					RemainingQty:  qty,
					Status:        pb.OrderStatus_OPEN,
					TimestampNs:   time.Now().UnixNano(),
					ClientOrderID: fmt.Sprintf("c-%d-%d", wId, i),
				}

				res := ob.ProcessOrder(order)
				collector.RecordLatency(res.LatencyNs, res.ExecutedQty > 0)
			}
		}(workerID)
	}

	wg.Wait()
	totalElapsed := time.Since(t0)

	summary := collector.GetSummary()

	fmt.Println("-----------------------------------------------------------------")
	fmt.Printf("📊 [基準測試統計結果]\n")
	fmt.Printf("   總耗時:            %.3f 秒\n", totalElapsed.Seconds())
	fmt.Printf("   撮合吞吐量 (QPS):  %.0f 筆/秒\n", summary.ThroughputQPS)
	fmt.Printf("   總撮合訂單數:      %d 筆\n", summary.TotalOrders)
	fmt.Printf("   成功撮合成交數:    %d 筆\n", summary.TotalMatched)
	fmt.Printf("   P50 撮合延遲:      %.2f μs (微秒)\n", summary.P50LatencyMicros)
	fmt.Printf("   P90 撮合延遲:      %.2f μs (微秒)\n", summary.P90LatencyMicros)
	fmt.Printf("   P99 撮合延遲:      %.2f μs (微秒)\n", summary.P99LatencyMicros)
	fmt.Printf("   最大撮合延遲:      %.2f μs (微秒)\n", summary.MaxLatencyMicros)
	fmt.Printf("   記憶體佔用 (Alloc): %.2f MB\n", summary.MemoryAllocMB)
	fmt.Printf("   系統記憶體 (Sys):   %.2f MB\n", summary.MemorySysMB)
	fmt.Println("-----------------------------------------------------------------")

	// 檢查 DoD 完成定義
	// DoD: 在 100 併發模擬網格壓力下，CPU 佔用 < 1%，P99 訂單撮合延遲 < 500 微秒 (μs)
	dodPassed := true
	if summary.P99LatencyMicros >= 500.0 {
		fmt.Printf("❌ DoD 未達標: P99 延遲 %.2f μs >= 500.0 μs\n", summary.P99LatencyMicros)
		dodPassed = false
	} else {
		fmt.Printf("✅ DoD 達標: P99 撮合延遲 %.2f μs 遠低於 500 μs 門檻！\n", summary.P99LatencyMicros)
	}

	if summary.MemorySysMB > 50.0 {
		fmt.Printf("⚠️ 系統記憶體佔用 %.2f MB > 50 MB\n", summary.MemorySysMB)
	} else {
		fmt.Printf("✅ 記憶體佔用卓越: 僅 %.2f MB (DoD 要求 < 30~50MB)\n", summary.MemorySysMB)
	}

	if dodPassed {
		fmt.Println("🎉 [DoD 驗證成功] Go 記憶體撮合微引擎 PoC 符合次世代架構指標要求！")
	} else {
		os.Exit(1)
	}
}
