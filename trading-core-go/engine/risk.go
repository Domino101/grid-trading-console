package engine

import (
	"fmt"
	"math"
	"sync"
	"time"

	pb "trading-core-go/pb"
)

// RiskRuleConfig 風控配置參數
type RiskRuleConfig struct {
	MaxOrderQuantity     float64 // 單筆訂單最大數量 (例如 50 BTC)
	MaxPositionQuantity  float64 // 單機器人最大持倉數量 (例如 200 BTC)
	MaxPriceDeviationPct float64 // 胖手指偏離保護 (預設 0.05 即 5%)
	MaxDrawdownPct       float64 // 機器人最大允許回撤比例 (預設 0.15 即 15%)
}

// DefaultRiskRuleConfig 預設風控參數
func DefaultRiskRuleConfig() RiskRuleConfig {
	return RiskRuleConfig{
		MaxOrderQuantity:     100.0,
		MaxPositionQuantity:  500.0,
		MaxPriceDeviationPct: 0.05,
		MaxDrawdownPct:       0.15,
	}
}

// BotRiskProfile 機器人風控當前狀態
type BotRiskProfile struct {
	BotID              string
	CurrentPositionQty float64
	CurrentDrawdownPct float64
	CircuitBreaker     bool
	CircuitReason      string
	LastUpdated        time.Time
}

// RiskGuard 毫秒級極速風控衛哨
type RiskGuard struct {
	mu     sync.RWMutex
	config RiskRuleConfig
	bots   map[string]*BotRiskProfile
}

// NewRiskGuard 建立風控實例
func NewRiskGuard(cfg RiskRuleConfig) *RiskGuard {
	return &RiskGuard{
		config: cfg,
		bots:   make(map[string]*BotRiskProfile, 64),
	}
}

// GetOrCreateProfile 取得或初始化機器人風控狀態
func (rg *RiskGuard) getOrCreateProfile(botID string) *BotRiskProfile {
	profile, exists := rg.bots[botID]
	if !exists {
		profile = &BotRiskProfile{
			BotID:       botID,
			LastUpdated: time.Now(),
		}
		rg.bots[botID] = profile
	}
	return profile
}

// EvaluateOrder 評估訂單風控 (耗時 < 1μs)
func (rg *RiskGuard) EvaluateOrder(req *pb.OrderRequest, bestBid, bestAsk float64) (approved bool, reason string, circuitTriggered bool) {
	rg.mu.Lock()
	defer rg.mu.Unlock()

	profile := rg.getOrCreateProfile(req.BotId)

	// 1. 檢查是否已觸發全域或單機熔斷
	if profile.CircuitBreaker {
		if !req.ReduceOnly {
			return false, fmt.Sprintf("CIRCUIT_BREAKER_ACTIVE: %s (only reduce_only allowed)", profile.CircuitReason), true
		}
	}

	// 2. 檢查單筆訂單數量上限與數值有效性 (DEF-09)
	if req.Quantity <= 0 || math.IsNaN(req.Quantity) || math.IsInf(req.Quantity, 0) {
		return false, "INVALID_QUANTITY: quantity must be positive and finite", false
	}
	if req.Quantity > rg.config.MaxOrderQuantity {
		return false, fmt.Sprintf("ORDER_QTY_LIMIT_EXCEEDED: qty=%.4f > max=%.4f", req.Quantity, rg.config.MaxOrderQuantity), false
	}

	// 2.1 檢查限價單價格有效性 (DEF-09)
	if req.OrderType == pb.OrderType_LIMIT {
		if req.Price <= 0 || math.IsNaN(req.Price) || math.IsInf(req.Price, 0) {
			return false, "INVALID_PRICE: limit order price must be positive and finite", false
		}
	}

	// 3. 檢查預期持倉上限 (非 reduce_only 情況下)
	if !req.ReduceOnly {
		delta := req.Quantity
		if req.Side == pb.OrderSide_SELL {
			delta = -delta
		}
		projectedPos := math.Abs(profile.CurrentPositionQty + delta)
		if projectedPos > rg.config.MaxPositionQuantity {
			return false, fmt.Sprintf("POSITION_LIMIT_EXCEEDED: projected=%.4f > max=%.4f", projectedPos, rg.config.MaxPositionQuantity), false
		}
	}

	// 4. 胖手指 (Fat-Finger) 價格異常偏離保護
	if req.OrderType == pb.OrderType_LIMIT && req.Price > 0 {
		if req.Side == pb.OrderSide_BUY && bestAsk > 0 {
			deviation := (req.Price - bestAsk) / bestAsk
			if deviation > rg.config.MaxPriceDeviationPct {
				return false, fmt.Sprintf("FAT_FINGER_BUY_PRICE_TOO_HIGH: price=%.4f best_ask=%.4f dev=%.2f%% > max=%.2f%%",
					req.Price, bestAsk, deviation*100, rg.config.MaxPriceDeviationPct*100), false
			}
		} else if req.Side == pb.OrderSide_SELL && bestBid > 0 {
			deviation := (bestBid - req.Price) / bestBid
			if deviation > rg.config.MaxPriceDeviationPct {
				return false, fmt.Sprintf("FAT_FINGER_SELL_PRICE_TOO_LOW: price=%.4f best_bid=%.4f dev=%.2f%% > max=%.2f%%",
					req.Price, bestBid, deviation*100, rg.config.MaxPriceDeviationPct*100), false
			}
		}
	}

	return true, "APPROVED", false
}

// UpdateBotMetrics 更新機器人外部運行狀態 (如當前持倉與回撤比例)
func (rg *RiskGuard) UpdateBotMetrics(botID string, posQty, drawdownPct float64) (circuitTripped bool, reason string) {
	rg.mu.Lock()
	defer rg.mu.Unlock()

	profile := rg.getOrCreateProfile(botID)
	profile.CurrentPositionQty = posQty
	profile.CurrentDrawdownPct = drawdownPct
	profile.LastUpdated = time.Now()

	// 檢查回撤是否突破熔斷閾值
	if drawdownPct >= rg.config.MaxDrawdownPct {
		profile.CircuitBreaker = true
		profile.CircuitReason = fmt.Sprintf("MAX_DRAWDOWN_BREACHED: current=%.2f%% >= max=%.2f%%",
			drawdownPct*100, rg.config.MaxDrawdownPct*100)
		return true, profile.CircuitReason
	}

	return false, ""
}

// TripCircuitBreaker 手動或緊急觸發熔斷切斷
func (rg *RiskGuard) TripCircuitBreaker(botID, reason string) {
	rg.mu.Lock()
	defer rg.mu.Unlock()
	profile := rg.getOrCreateProfile(botID)
	profile.CircuitBreaker = true
	profile.CircuitReason = reason
}

// ResetCircuitBreaker 解除熔斷狀態
func (rg *RiskGuard) ResetCircuitBreaker(botID string) {
	rg.mu.Lock()
	defer rg.mu.Unlock()
	profile := rg.getOrCreateProfile(botID)
	profile.CircuitBreaker = false
	profile.CircuitReason = ""
}
