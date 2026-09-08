package engine

import (
	"errors"
	"sort"
	"sync"
	"time"

	pb "trading-core-go/pb"
)

var (
	ErrOrderNotFound   = errors.New("order not found")
	ErrInvalidQuantity = errors.New("order quantity must be positive")
	ErrInvalidPrice    = errors.New("order price must be positive for limit orders")
)

// Order 內部訂單結構
type Order struct {
	ID             string
	BotID          string
	Symbol         string
	Side           pb.OrderSide
	Type           pb.OrderType
	Price          float64
	Quantity       float64
	RemainingQty   float64
	ExecutedQty    float64
	CumulativeCost float64
	Status         pb.OrderStatus
	TimestampNs    int64
	ClientOrderID  string
}

// Trade 成交紀錄
type Trade struct {
	MakerOrderID string
	TakerOrderID string
	Price        float64
	Quantity     float64
	TimestampNs  int64
}

// MatchResult 撮合結果
type MatchResult struct {
	Order            *Order
	Trades           []*Trade
	ExecutedQty      float64
	AvgExecutedPrice float64
	Fee              float64
	Status           pb.OrderStatus
	LatencyNs        int64
}

// PriceLevel 單一價位深度
type PriceLevel struct {
	Price    float64
	TotalQty float64
	Orders   []*Order
}

// OrderBook 記憶體高速訂單簿
type OrderBook struct {
	Symbol     string
	mu         sync.RWMutex
	bids       []*PriceLevel // 買單：價格由高至低 (降序)
	asks       []*PriceLevel // 賣單：價格由低至高 (升序)
	orderIndex map[string]*Order
}

// NewOrderBook 建立指定標的之訂單簿
func NewOrderBook(symbol string) *OrderBook {
	return &OrderBook{
		Symbol:     symbol,
		bids:       make([]*PriceLevel, 0, 64),
		asks:       make([]*PriceLevel, 0, 64),
		orderIndex: make(map[string]*Order, 1024),
	}
}

// GetBestBid 取得當前最高買價
func (ob *OrderBook) GetBestBid() (float64, bool) {
	ob.mu.RLock()
	defer ob.mu.RUnlock()
	if len(ob.bids) == 0 {
		return 0, false
	}
	return ob.bids[0].Price, true
}

// GetBestAsk 取得當前最低賣價
func (ob *OrderBook) GetBestAsk() (float64, bool) {
	ob.mu.RLock()
	defer ob.mu.RUnlock()
	if len(ob.asks) == 0 {
		return 0, false
	}
	return ob.asks[0].Price, true
}

// GetDepth 取得指定深度價位切片 (Thread-safe)
func (ob *OrderBook) GetDepth(limit int) ([]*pb.PriceLevel, []*pb.PriceLevel) {
	ob.mu.RLock()
	defer ob.mu.RUnlock()

	bidsCount := len(ob.bids)
	if limit > 0 && bidsCount > limit {
		bidsCount = limit
	}
	bids := make([]*pb.PriceLevel, bidsCount)
	for i := 0; i < bidsCount; i++ {
		bids[i] = &pb.PriceLevel{Price: ob.bids[i].Price, Quantity: ob.bids[i].TotalQty}
	}

	asksCount := len(ob.asks)
	if limit > 0 && asksCount > limit {
		asksCount = limit
	}
	asks := make([]*pb.PriceLevel, asksCount)
	for i := 0; i < asksCount; i++ {
		asks[i] = &pb.PriceLevel{Price: ob.asks[i].Price, Quantity: ob.asks[i].TotalQty}
	}

	return bids, asks
}

// CancelOrder 撤回在簿訂單 (O(1) 索引查詢 + 價位隊列移除)
func (ob *OrderBook) CancelOrder(orderID string) (*Order, error) {
	ob.mu.Lock()
	defer ob.mu.Unlock()

	order, exists := ob.orderIndex[orderID]
	if !exists {
		return nil, ErrOrderNotFound
	}

	if order.Side == pb.OrderSide_BUY {
		ob.removeOrderFromLevels(&ob.bids, order)
	} else {
		ob.removeOrderFromLevels(&ob.asks, order)
	}

	delete(ob.orderIndex, order.ID)
	if order.ClientOrderID != "" {
		delete(ob.orderIndex, order.ClientOrderID)
	}
	order.Status = pb.OrderStatus_CANCELLED
	return order, nil
}

// removeOrderFromLevels 從價位深度中剔除指定訂單
func (ob *OrderBook) removeOrderFromLevels(levels *[]*PriceLevel, order *Order) {
	slice := *levels
	for i, lvl := range slice {
		if lvl.Price == order.Price {
			newOrders := make([]*Order, 0, len(lvl.Orders))
			for _, o := range lvl.Orders {
				if o.ID != order.ID {
					newOrders = append(newOrders, o)
				}
			}
			lvl.Orders = newOrders
			lvl.TotalQty -= order.RemainingQty
			if lvl.TotalQty < 1e-9 || len(lvl.Orders) == 0 {
				*levels = append(slice[:i], slice[i+1:]...)
			}
			return
		}
	}
}

// insertLevel 依排序插入新訂單至深度簿
func (ob *OrderBook) insertOrder(order *Order) {
	if order.Side == pb.OrderSide_BUY {
		// 買單：依價格降序 (高價優先)
		idx := sort.Search(len(ob.bids), func(i int) bool {
			return ob.bids[i].Price <= order.Price
		})
		if idx < len(ob.bids) && ob.bids[idx].Price == order.Price {
			ob.bids[idx].Orders = append(ob.bids[idx].Orders, order)
			ob.bids[idx].TotalQty += order.RemainingQty
		} else {
			lvl := &PriceLevel{
				Price:    order.Price,
				TotalQty: order.RemainingQty,
				Orders:   []*Order{order},
			}
			ob.bids = append(ob.bids, nil)
			copy(ob.bids[idx+1:], ob.bids[idx:])
			ob.bids[idx] = lvl
		}
	} else {
		// 賣單：依價格升序 (低價優先)
		idx := sort.Search(len(ob.asks), func(i int) bool {
			return ob.asks[i].Price >= order.Price
		})
		if idx < len(ob.asks) && ob.asks[idx].Price == order.Price {
			ob.asks[idx].Orders = append(ob.asks[idx].Orders, order)
			ob.asks[idx].TotalQty += order.RemainingQty
		} else {
			lvl := &PriceLevel{
				Price:    order.Price,
				TotalQty: order.RemainingQty,
				Orders:   []*Order{order},
			}
			ob.asks = append(ob.asks, nil)
			copy(ob.asks[idx+1:], ob.asks[idx:])
			ob.asks[idx] = lvl
		}
	}
	ob.orderIndex[order.ID] = order
	if order.ClientOrderID != "" {
		ob.orderIndex[order.ClientOrderID] = order
	}
}

// ProcessOrder 處理下單與高速記憶體撮合
func (ob *OrderBook) ProcessOrder(order *Order) *MatchResult {
	ob.mu.Lock()
	defer ob.mu.Unlock()
	start := time.Now().UnixNano()

	result := &MatchResult{
		Order:       order,
		Trades:      make([]*Trade, 0, 4),
		Status:      pb.OrderStatus_OPEN,
		ExecutedQty: 0,
	}

	if order.Side == pb.OrderSide_BUY {
		ob.matchBuy(order, result)
	} else {
		ob.matchSell(order, result)
	}

	// 若仍有未成交量且非市價單與 IOC/FOK，掛入訂單簿
	if order.RemainingQty > 1e-9 && order.Type == pb.OrderType_LIMIT {
		if order.ExecutedQty > 0 {
			order.Status = pb.OrderStatus_PARTIALLY_FILLED
		} else {
			order.Status = pb.OrderStatus_OPEN
		}
		ob.insertOrder(order)
	} else if order.RemainingQty <= 1e-9 {
		order.Status = pb.OrderStatus_FILLED
	} else if order.Type == pb.OrderType_MARKET {
		// 市價單剩餘量自動撤銷
		if order.ExecutedQty > 0 {
			order.Status = pb.OrderStatus_PARTIALLY_FILLED
		} else {
			order.Status = pb.OrderStatus_CANCELLED
		}
	}

	result.Status = order.Status
	result.ExecutedQty = order.ExecutedQty
	if order.ExecutedQty > 0 {
		result.AvgExecutedPrice = order.CumulativeCost / order.ExecutedQty
	}
	result.LatencyNs = time.Now().UnixNano() - start
	return result
}

// matchBuy 撮合買單 (吃對手賣單 asks)
func (ob *OrderBook) matchBuy(taker *Order, res *MatchResult) {
	now := time.Now().UnixNano()
	i := 0
	for i < len(ob.asks) && taker.RemainingQty > 1e-9 {
		lvl := ob.asks[i]
		if taker.Type == pb.OrderType_LIMIT && lvl.Price > taker.Price {
			break // 賣價已高於買方可接受上限
		}

		j := 0
		for j < len(lvl.Orders) && taker.RemainingQty > 1e-9 {
			maker := lvl.Orders[j]
			matchQty := maker.RemainingQty
			if taker.RemainingQty < matchQty {
				matchQty = taker.RemainingQty
			}

			// 成交結算
			tradeCost := matchQty * lvl.Price
			maker.RemainingQty -= matchQty
			maker.ExecutedQty += matchQty
			maker.CumulativeCost += tradeCost
			lvl.TotalQty -= matchQty

			taker.RemainingQty -= matchQty
			taker.ExecutedQty += matchQty
			taker.CumulativeCost += tradeCost

			trade := &Trade{
				MakerOrderID: maker.ID,
				TakerOrderID: taker.ID,
				Price:        lvl.Price,
				Quantity:     matchQty,
				TimestampNs:  now,
			}
			res.Trades = append(res.Trades, trade)

			if maker.RemainingQty <= 1e-9 {
				maker.Status = pb.OrderStatus_FILLED
				delete(ob.orderIndex, maker.ID)
				if maker.ClientOrderID != "" {
					delete(ob.orderIndex, maker.ClientOrderID)
				}
				j++
			} else {
				maker.Status = pb.OrderStatus_PARTIALLY_FILLED
				break
			}
		}

		lvl.Orders = lvl.Orders[j:]
		if len(lvl.Orders) == 0 {
			i++
		} else {
			break
		}
	}

	if i > 0 {
		ob.asks = ob.asks[i:]
	}
}

// matchSell 撮合賣單 (吃對手買單 bids)
func (ob *OrderBook) matchSell(taker *Order, res *MatchResult) {
	now := time.Now().UnixNano()
	i := 0
	for i < len(ob.bids) && taker.RemainingQty > 1e-9 {
		lvl := ob.bids[i]
		if taker.Type == pb.OrderType_LIMIT && lvl.Price < taker.Price {
			break // 買價已低於賣方可接受下限
		}

		j := 0
		for j < len(lvl.Orders) && taker.RemainingQty > 1e-9 {
			maker := lvl.Orders[j]
			matchQty := maker.RemainingQty
			if taker.RemainingQty < matchQty {
				matchQty = taker.RemainingQty
			}

			// 成交結算
			tradeCost := matchQty * lvl.Price
			maker.RemainingQty -= matchQty
			maker.ExecutedQty += matchQty
			maker.CumulativeCost += tradeCost
			lvl.TotalQty -= matchQty

			taker.RemainingQty -= matchQty
			taker.ExecutedQty += matchQty
			taker.CumulativeCost += tradeCost

			trade := &Trade{
				MakerOrderID: maker.ID,
				TakerOrderID: taker.ID,
				Price:        lvl.Price,
				Quantity:     matchQty,
				TimestampNs:  now,
			}
			res.Trades = append(res.Trades, trade)

			if maker.RemainingQty <= 1e-9 {
				maker.Status = pb.OrderStatus_FILLED
				delete(ob.orderIndex, maker.ID)
				if maker.ClientOrderID != "" {
					delete(ob.orderIndex, maker.ClientOrderID)
				}
				j++
			} else {
				maker.Status = pb.OrderStatus_PARTIALLY_FILLED
				break
			}
		}

		lvl.Orders = lvl.Orders[j:]
		if len(lvl.Orders) == 0 {
			i++
		} else {
			break
		}
	}

	if i > 0 {
		ob.bids = ob.bids[i:]
	}
}
