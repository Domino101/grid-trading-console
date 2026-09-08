package main

import (
	"fmt"
	"math/rand"
	"testing"
	"time"

	"trading-core-go/engine"
	pb "trading-core-go/pb"
)

func BenchmarkRiskGuardEvaluation(b *testing.B) {
	rg := engine.NewRiskGuard(engine.DefaultRiskRuleConfig())
	req := &pb.OrderRequest{
		BotId:     "bot-test-1",
		Symbol:    "BTC/USDT",
		Side:      pb.OrderSide_BUY,
		OrderType: pb.OrderType_LIMIT,
		Price:     65000.0,
		Quantity:  1.0,
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		approved, _, _ := rg.EvaluateOrder(req, 64990.0, 65010.0)
		if !approved {
			b.Fatal("unexpected rejection")
		}
	}
}

func BenchmarkOrderBookMatching(b *testing.B) {
	ob := engine.NewOrderBook("BTC/USDT")
	// 放流動性
	for i := 1; i <= 1000; i++ {
		ob.ProcessOrder(&engine.Order{
			ID:           fmt.Sprintf("ask-%d", i),
			BotID:        "maker",
			Symbol:       "BTC/USDT",
			Side:         pb.OrderSide_SELL,
			Type:         pb.OrderType_LIMIT,
			Price:        65000.0 + float64(i)*0.5,
			Quantity:     10.0,
			RemainingQty: 10.0,
			Status:       pb.OrderStatus_OPEN,
			TimestampNs:  time.Now().UnixNano(),
		})
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		taker := &engine.Order{
			ID:           fmt.Sprintf("taker-%d", i),
			BotID:        "taker",
			Symbol:       "BTC/USDT",
			Side:         pb.OrderSide_BUY,
			Type:         pb.OrderType_LIMIT,
			Price:        65100.0,
			Quantity:     0.01,
			RemainingQty: 0.01,
			Status:       pb.OrderStatus_OPEN,
			TimestampNs:  time.Now().UnixNano(),
		}
		res := ob.ProcessOrder(taker)
		if res.ExecutedQty == 0 {
			// 如果吃完流動性就跳過
		}
	}
}

func BenchmarkParallelMatching(b *testing.B) {
	ob := engine.NewOrderBook("BTC/USDT")
	basePrice := 65000.0

	// 預先鋪單
	for i := 1; i <= 100; i++ {
		ob.ProcessOrder(&engine.Order{
			ID:           fmt.Sprintf("seed-ask-%d", i),
			BotID:        "maker",
			Symbol:       "BTC/USDT",
			Side:         pb.OrderSide_SELL,
			Type:         pb.OrderType_LIMIT,
			Price:        basePrice + float64(i)*2.0,
			Quantity:     100.0,
			RemainingQty: 100.0,
			Status:       pb.OrderStatus_OPEN,
			TimestampNs:  time.Now().UnixNano(),
		})
	}

	b.ResetTimer()
	b.RunParallel(func(pbWorker *testing.PB) {
		rng := rand.New(rand.NewSource(time.Now().UnixNano()))
		idx := 0
		for pbWorker.Next() {
			idx++
			isBuy := rng.Intn(2) == 0
			side := pb.OrderSide_BUY
			if !isBuy {
				side = pb.OrderSide_SELL
			}
			order := &engine.Order{
				ID:           fmt.Sprintf("p-%d", idx),
				BotID:        "bot-p",
				Symbol:       "BTC/USDT",
				Side:         side,
				Type:         pb.OrderType_LIMIT,
				Price:        basePrice + (rng.Float64()*100.0 - 50.0),
				Quantity:     0.05,
				RemainingQty: 0.05,
				Status:       pb.OrderStatus_OPEN,
				TimestampNs:  time.Now().UnixNano(),
			}
			ob.ProcessOrder(order)
		}
	})
}

func TestRiskGuardValidation(t *testing.T) {
	rg := engine.NewRiskGuard(engine.DefaultRiskRuleConfig())

	// 1. 負數限價單價格 (DEF-09)
	reqNeg := &pb.OrderRequest{
		BotId:     "bot-t1",
		OrderType: pb.OrderType_LIMIT,
		Side:      pb.OrderSide_BUY,
		Price:     -10.0,
		Quantity:  1.0,
	}
	app, _, _ := rg.EvaluateOrder(reqNeg, 100, 101)
	if app {
		t.Fatal("Negative price must be rejected")
	}

	// 2. 零價格限價單 (DEF-09)
	reqZero := &pb.OrderRequest{
		BotId:     "bot-t1",
		OrderType: pb.OrderType_LIMIT,
		Side:      pb.OrderSide_BUY,
		Price:     0.0,
		Quantity:  1.0,
	}
	app, _, _ = rg.EvaluateOrder(reqZero, 100, 101)
	if app {
		t.Fatal("Zero price must be rejected")
	}

	// 3. 負數量 (DEF-09)
	reqNegQty := &pb.OrderRequest{
		BotId:     "bot-t1",
		OrderType: pb.OrderType_LIMIT,
		Side:      pb.OrderSide_BUY,
		Price:     100.0,
		Quantity:  -1.0,
	}
	app, _, _ = rg.EvaluateOrder(reqNegQty, 100, 101)
	if app {
		t.Fatal("Negative quantity must be rejected")
	}
}

func TestOrderBookCancelByClientID(t *testing.T) {
	ob := engine.NewOrderBook("BTC/USDT")
	order := &engine.Order{
		ID:            "order-12345",
		ClientOrderID: "client-custom-999",
		BotID:         "bot-1",
		Symbol:        "BTC/USDT",
		Side:          pb.OrderSide_BUY,
		Type:          pb.OrderType_LIMIT,
		Price:         50000.0,
		Quantity:      1.0,
		RemainingQty:  1.0,
		Status:        pb.OrderStatus_OPEN,
		TimestampNs:   time.Now().UnixNano(),
	}
	ob.ProcessOrder(order)

	// DEF-10: 透過 ClientOrderID 撤單
	cancelled, err := ob.CancelOrder("client-custom-999")
	if err != nil {
		t.Fatalf("Failed to cancel by ClientOrderID: %v", err)
	}
	if cancelled.ID != "order-12345" {
		t.Fatalf("Cancelled order mismatch: got %s, want order-12345", cancelled.ID)
	}
}

