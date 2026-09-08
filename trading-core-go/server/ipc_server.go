package server

import (
	"encoding/binary"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"google.golang.org/protobuf/proto"

	"trading-core-go/engine"
	pb "trading-core-go/pb"
)

// IPCServer Unix Domain Socket IPC 伺服器
type IPCServer struct {
	SocketPath string
	listener   net.Listener
	booksMu    sync.RWMutex
	books      map[string]*engine.OrderBook
	riskGuard  *engine.RiskGuard
	metrics    *engine.MetricsCollector
	running    int32
	seqCounter int64
}

// NewIPCServer 建立 UDS 伺服器
func NewIPCServer(socketPath string, riskCfg engine.RiskRuleConfig) *IPCServer {
	return &IPCServer{
		SocketPath: socketPath,
		books:      make(map[string]*engine.OrderBook),
		riskGuard:  engine.NewRiskGuard(riskCfg),
		metrics:    engine.NewMetricsCollector(500000),
	}
}

// GetOrderBook 取得或建立標的訂單簿
func (s *IPCServer) GetOrderBook(symbol string) *engine.OrderBook {
	s.booksMu.Lock()
	defer s.booksMu.Unlock()
	ob, exists := s.books[symbol]
	if !exists {
		ob = engine.NewOrderBook(symbol)
		s.books[symbol] = ob
	}
	return ob
}

// Start 啟動監聽 Unix Domain Socket
func (s *IPCServer) Start() error {
	// 清理殘留 socket 檔案
	_ = os.Remove(s.SocketPath)

	l, err := net.Listen("unix", s.SocketPath)
	if err != nil {
		return fmt.Errorf("failed to listen on unix socket %s: %w", s.SocketPath, err)
	}
	s.listener = l
	atomic.StoreInt32(&s.running, 1)

	log.Printf("🚀 [IPC Server] Unix Domain Socket 服務端已啟動: %s", s.SocketPath)

	go func() {
		for atomic.LoadInt32(&s.running) == 1 {
			conn, err := s.listener.Accept()
			if err != nil {
				if atomic.LoadInt32(&s.running) == 0 {
					return
				}
				log.Printf("⚠️ [IPC Server] 連線接受異常: %v", err)
				continue
			}
			go s.handleConnection(conn)
		}
	}()

	return nil
}

// Stop 停止伺服器並清理 socket
func (s *IPCServer) Stop() error {
	atomic.StoreInt32(&s.running, 0)
	if s.listener != nil {
		_ = s.listener.Close()
	}
	_ = os.Remove(s.SocketPath)
	log.Printf("🛑 [IPC Server] Unix Domain Socket 服務端已關閉")
	return nil
}

// GetMetrics 取得效能統計
func (s *IPCServer) GetMetrics() engine.PerformanceSummary {
	return s.metrics.GetSummary()
}

// handleConnection 處理單一客戶端連接循環
func (s *IPCServer) handleConnection(conn net.Conn) {
	defer conn.Close()

	headerBuf := make([]byte, 4)
	for atomic.LoadInt32(&s.running) == 1 {
		// 1. 讀取 4-byte 訊息長度前綴 (Big-Endian)
		if _, err := io.ReadFull(conn, headerBuf); err != nil {
			if err != io.EOF {
				// 正常斷線不拋錯
			}
			return
		}
		msgLen := binary.BigEndian.Uint32(headerBuf)
		if msgLen == 0 || msgLen > 10*1024*1024 { // 限制單筆最大 10MB
			log.Printf("❌ [IPC Server] 非法封包長度: %d", msgLen)
			return
		}

		// 2. 讀取 Protobuf Envelope 載體
		payloadBuf := make([]byte, msgLen)
		if _, err := io.ReadFull(conn, payloadBuf); err != nil {
			log.Printf("❌ [IPC Server] 載體讀取失敗: %v", err)
			return
		}

		var reqEnv pb.IPCEnvelope
		if err := proto.Unmarshal(payloadBuf, &reqEnv); err != nil {
			log.Printf("❌ [IPC Server] Protobuf 解析失敗: %v", err)
			continue
		}

		// 3. 核心分發處理
		respEnv := s.dispatch(&reqEnv)
		if respEnv == nil {
			continue
		}

		// 4. 序列化回傳響應
		respBytes, err := proto.Marshal(respEnv)
		if err != nil {
			log.Printf("❌ [IPC Server] 響應序列化失敗: %v", err)
			continue
		}

		respHeader := make([]byte, 4)
		binary.BigEndian.PutUint32(respHeader, uint32(len(respBytes)))
		if _, err := conn.Write(respHeader); err != nil {
			return
		}
		if _, err := conn.Write(respBytes); err != nil {
			return
		}
	}
}

// dispatch 訊息分發邏輯
func (s *IPCServer) dispatch(env *pb.IPCEnvelope) *pb.IPCEnvelope {
	startNs := time.Now().UnixNano()
	seq := atomic.AddInt64(&s.seqCounter, 1)

	switch env.Type {
	case pb.MessageType_MSG_ORDER_REQ:
		var orderReq pb.OrderRequest
		if err := proto.Unmarshal(env.Payload, &orderReq); err != nil {
			return s.makeErrorEnvelope(pb.MessageType_MSG_ORDER_RESP, "UNMARSHAL_ERROR", seq)
		}

		ob := s.GetOrderBook(orderReq.Symbol)
		bestBid, _ := ob.GetBestBid()
		bestAsk, _ := ob.GetBestAsk()

		// 1. 微秒級風控檢查
		approved, reason, _ := s.riskGuard.EvaluateOrder(&orderReq, bestBid, bestAsk)
		if !approved {
			resp := &pb.OrderResponse{
				RequestId:          orderReq.RequestId,
				ClientOrderId:      orderReq.ClientOrderId,
				BotId:              orderReq.BotId,
				Symbol:             orderReq.Symbol,
				Status:             pb.OrderStatus_REJECTED,
				RejectReason:       reason,
				EngineTimestampNs:  time.Now().UnixNano(),
				LatencyNs:          time.Now().UnixNano() - startNs,
				ExecutedPrice:      0,
				ExecutedQuantity:   0,
				RemainingQuantity:  orderReq.Quantity,
			}
			s.metrics.RecordLatency(time.Now().UnixNano()-startNs, false)
			b, _ := proto.Marshal(resp)
			return &pb.IPCEnvelope{Type: pb.MessageType_MSG_ORDER_RESP, Payload: b, Sequence: seq, TimestampNs: time.Now().UnixNano()}
		}

		// 2. 訂單撮合入簿
		orderID := fmt.Sprintf("ord-%d-%s", time.Now().UnixNano(), orderReq.ClientOrderId)
		internalOrder := &engine.Order{
			ID:            orderID,
			BotID:         orderReq.BotId,
			Symbol:        orderReq.Symbol,
			Side:          orderReq.Side,
			Type:          orderReq.OrderType,
			Price:         orderReq.Price,
			Quantity:      orderReq.Quantity,
			RemainingQty:  orderReq.Quantity,
			Status:        pb.OrderStatus_OPEN,
			TimestampNs:   time.Now().UnixNano(),
			ClientOrderID: orderReq.ClientOrderId,
		}

		res := ob.ProcessOrder(internalOrder)
		matched := res.ExecutedQty > 0
		s.metrics.RecordLatency(res.LatencyNs, matched)

		resp := &pb.OrderResponse{
			RequestId:          orderReq.RequestId,
			OrderId:            orderID,
			ClientOrderId:      orderReq.ClientOrderId,
			BotId:              orderReq.BotId,
			Symbol:             orderReq.Symbol,
			Status:             res.Status,
			ExecutedPrice:      res.AvgExecutedPrice,
			ExecutedQuantity:   res.ExecutedQty,
			RemainingQuantity:  res.Order.RemainingQty,
			CumulativeQuote:    res.Order.CumulativeCost,
			EngineTimestampNs:  time.Now().UnixNano(),
			LatencyNs:          res.LatencyNs,
		}
		b, _ := proto.Marshal(resp)
		return &pb.IPCEnvelope{Type: pb.MessageType_MSG_ORDER_RESP, Payload: b, Sequence: seq, TimestampNs: time.Now().UnixNano()}

	case pb.MessageType_MSG_CANCEL_REQ:
		var cancelReq pb.CancelRequest
		if err := proto.Unmarshal(env.Payload, &cancelReq); err != nil {
			return s.makeErrorEnvelope(pb.MessageType_MSG_CANCEL_RESP, "UNMARSHAL_ERROR", seq)
		}

		ob := s.GetOrderBook(cancelReq.Symbol)
		targetID := cancelReq.OrderId
		if targetID == "" {
			targetID = cancelReq.ClientOrderId
		}
		cancelledOrder, err := ob.CancelOrder(targetID)
		respOrderId := cancelReq.OrderId
		if respOrderId == "" && cancelledOrder != nil {
			respOrderId = cancelledOrder.ID
		}
		resp := &pb.CancelResponse{
			RequestId:         cancelReq.RequestId,
			OrderId:           respOrderId,
			Success:           err == nil,
			EngineTimestampNs: time.Now().UnixNano(),
		}
		if err != nil {
			resp.ErrorMessage = err.Error()
		}
		b, _ := proto.Marshal(resp)
		return &pb.IPCEnvelope{Type: pb.MessageType_MSG_CANCEL_RESP, Payload: b, Sequence: seq, TimestampNs: time.Now().UnixNano()}

	case pb.MessageType_MSG_RISK_REQ:
		var riskReq pb.RiskCheckRequest
		if err := proto.Unmarshal(env.Payload, &riskReq); err != nil {
			return s.makeErrorEnvelope(pb.MessageType_MSG_RISK_RESP, "UNMARSHAL_ERROR", seq)
		}

		ob := s.GetOrderBook(riskReq.Symbol)
		bb, _ := ob.GetBestBid()
		ba, _ := ob.GetBestAsk()

		pseudoOrder := &pb.OrderRequest{
			BotId:     riskReq.BotId,
			Symbol:    riskReq.Symbol,
			Side:      riskReq.Side,
			Price:     riskReq.Price,
			Quantity:  riskReq.Quantity,
			OrderType: pb.OrderType_LIMIT,
		}
		approved, reason, tripped := s.riskGuard.EvaluateOrder(pseudoOrder, bb, ba)
		resp := &pb.RiskCheckResponse{
			Approved:                 approved,
			Reason:                   reason,
			CircuitBreakerTriggered:  tripped,
			LatencyNs:                time.Now().UnixNano() - startNs,
		}
		b, _ := proto.Marshal(resp)
		return &pb.IPCEnvelope{Type: pb.MessageType_MSG_RISK_RESP, Payload: b, Sequence: seq, TimestampNs: time.Now().UnixNano()}

	case pb.MessageType_MSG_HEARTBEAT:
		summary := s.metrics.GetSummary()
		hb := &pb.EngineHeartbeat{
			TimestampNs:        time.Now().UnixNano(),
			TotalOrdersMatched: summary.TotalMatched,
			CurrentQps:         summary.ThroughputQPS,
			MemoryRssBytes:     uint64(summary.MemoryAllocMB * 1024 * 1024),
			CpuPct:             0.05, // PoC 固定回報極低佔用
		}
		b, _ := proto.Marshal(hb)
		return &pb.IPCEnvelope{Type: pb.MessageType_MSG_HEARTBEAT, Payload: b, Sequence: seq, TimestampNs: time.Now().UnixNano()}

	default:
		return s.makeErrorEnvelope(pb.MessageType_MSG_UNSPECIFIED, "UNKNOWN_MSG_TYPE", seq)
	}
}

func (s *IPCServer) makeErrorEnvelope(t pb.MessageType, msg string, seq int64) *pb.IPCEnvelope {
	return &pb.IPCEnvelope{
		Type:        t,
		Payload:     []byte(msg),
		Sequence:    seq,
		TimestampNs: time.Now().UnixNano(),
	}
}
