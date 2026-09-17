"""
CoinFull Prime 一籃子持倉平衡器 (Position Diffing & Order Execution)
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set
from decimal import Decimal

from .models import UniverseRankResult, RebalanceOrder, RebalanceReport, RankedItem
from core.adapters.exchanges.interface import ExchangeInterface
from core.adapters.exchanges.models import OrderSide, OrderType
from core.services.dca.ladder import quantize_quantity, quantize_price

logger = logging.getLogger("CoinFullPrime.Rebalancer")


class PortfolioRebalancer:
    """CoinFull Prime 組合換倉引擎 (Diffing 狀態機)"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        portfolio_cfg = self.config.get("portfolio", {})
        self.total_capital = float(portfolio_cfg.get("total_capital_usdt", 1000.0))
        self.leverage = float(portfolio_cfg.get("leverage", 1.0))
        self.dry_run = bool(portfolio_cfg.get("dry_run", True))
        self.max_slippage_pct = float(portfolio_cfg.get("max_slippage_pct", 0.005))
        self.min_order_notional = 5.0  # 幣安/各大交易所最小下單金額門檻 (5 USDT)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """統一將交易所合約符號標準化為純字串格式 (例: 'ORCA/USDT:USDT' -> 'ORCAUSDT')"""
        if not symbol:
            return ""
        clean = str(symbol).strip()
        # 若含有 CCXT 合約結算幣種尾碼 (如 :USDT)，先去除冒號結算幣種，避免出現 USDTUSDT
        if ":" in clean:
            clean = clean.split(":")[0]
        return clean.upper().replace("/", "").replace("-", "").replace("_", "").strip()

    def calculate_diff(
        self,
        current_positions: Dict[str, float],
        prices: Dict[str, float],
        rank_result: UniverseRankResult,
        managed_symbols: Optional[Set[str]] = None,
        protected_symbols: Optional[Set[str]] = None,
        gross_scale: float = 1.0,
        allow_new_entries: bool = True,
        force_flatten: bool = False
    ) -> Tuple[List[RebalanceOrder], List[str], List[str]]:
        """
        比對現有庫存與目標清單，產出最少交易次數的差異訂單清單（含雙軌防干擾與防碰撞機制）。
        
        防護機制：
        1. 外部機器人保護 (protected_symbols)：
           若標的屬於活躍中的網格/DCA機器人（或同一基礎幣種），嚴格豁免，絕不平倉、不反向操作。
        2. 專屬持倉籃子隔離 (managed_symbols)：
           若當前持倉並非由 CoinFull Prime 所建立與管理（例如手動合約或外部部位），
           CoinFull Prime 視為外部獨立資產，跳過平倉。只有登記在 managed_symbols 中的淘汰幣才允許平倉。

        風險閘門參數（docs/CoinFull_Prime_03_NoTrade_Trigger_Spec.md）：
        * force_flatten (T1)：
           平掉「所有」受管且未受保護的部位（reduce-only），不產生任何開倉/加倉單。
           平倉單永遠不受閘門阻擋 —— 本參數只是「額外平倉」，不會阻止既有平倉邏輯。
        * allow_new_entries = False：
           不建立任何新倉；已持有標的僅允許「減倉」（目標名義不得高於現有名義）。
        * gross_scale：
           目標名義金額的總倍數（0 ~ 2）。1.15 = 加碼、0.5 = 減半、
           0.35 = 風險關閉積極版的部分倉位。
        """
        orders: List[RebalanceOrder] = []
        closed_symbols: List[str] = []
        opened_symbols: List[str] = []
        scale = min(max(float(gross_scale), 0.0), 2.0)
        orders: List[RebalanceOrder] = []
        closed_symbols: List[str] = []
        opened_symbols: List[str] = []

        # 庫存符號正規化 (防禦不同交易所回傳格式差異)
        norm_positions: Dict[str, float] = {}
        for s, qty in (current_positions or {}).items():
            norm_positions[self._normalize_symbol(s)] = float(qty)

        # 價格字典符號正規化
        norm_prices: Dict[str, float] = {}
        for s, px in (prices or {}).items():
            norm_prices[self._normalize_symbol(s)] = float(px)

        target_map: Dict[str, RankedItem] = {}
        for item in rank_result.long_list:
            target_map[self._normalize_symbol(item.symbol)] = item
        for item in rank_result.short_list:
            target_map[self._normalize_symbol(item.symbol)] = item

        # 解析外部機器人保護清單（包含全名與基礎幣種，例如 SOLUSDC -> 排除 SOLUSDC 與 SOL）
        clean_protected: Set[str] = set()
        clean_protected_base: Set[str] = set()
        if protected_symbols:
            for s in protected_symbols:
                ns = self._normalize_symbol(s)
                clean_protected.add(ns)
                for quote in ["USDT", "USDC", "BUSD", "USD"]:
                    if ns.endswith(quote) and len(ns) > len(quote):
                        clean_protected_base.add(ns[:-len(quote)])

        def _is_protected(sym: str) -> bool:
            if not sym:
                return False
            ns = self._normalize_symbol(sym)
            if ns in clean_protected:
                return True
            for quote in ["USDT", "USDC", "BUSD", "USD"]:
                if ns.endswith(quote) and len(ns) > len(quote):
                    base = ns[:-len(quote)]
                    if base in clean_protected_base or base in clean_protected:
                        return True
            return False

        # 專屬持倉籃子過濾
        clean_managed: Optional[Set[str]] = None
        if managed_symbols is not None:
            clean_managed = {self._normalize_symbol(s) for s in managed_symbols}

        # 1. 識別需要平倉的標的（舊持倉有，但不在今日 Top8 / Bottom8 名單內）
        for sym, curr_qty in norm_positions.items():
            price = norm_prices.get(sym, 0.0)
            if price <= 0:
                continue
            curr_notional = abs(curr_qty * price)
            if curr_notional < self.min_order_notional:
                continue

            # 護欄 1：外部活躍機器人標的/幣種絕對保護
            if _is_protected(sym):
                logger.info(f"🛡️ [外部機器人保護] 標的 {sym} 屬於網格/DCA等運行中機器人，CoinFull Prime 嚴格跳過，絕不執行平倉！")
                continue

            # 護欄 2：專屬籃子記帳隔離（若非本策略管理之標的，視為外部資產，跳過平倉）
            if clean_managed is not None and sym not in clean_managed:
                logger.info(f"🛡️ [未納管部位隔離] 標的 {sym} 非 CoinFull Prime 專屬籃子標的，CoinFull Prime 視為獨立外部部位，跳過平倉。")
                continue

            if force_flatten or sym not in target_map:
                # 退出名單 -> 全部平倉 (釋放保證金)
                # T1 風險關閉 (force_flatten) -> 受管部位「全部」平倉，不問是否仍在名單內
                side = "sell" if curr_qty > 0 else "buy"
                orders.append(
                    RebalanceOrder(
                        symbol=sym,
                        side=side,
                        order_type="market",
                        amount=abs(curr_qty),
                        notional_usdt=round(curr_notional, 2),
                        reduce_only=True,
                        reason="regime_flatten" if force_flatten else "exit_old",
                        status="pending"
                    )
                )
                closed_symbols.append(sym)

        if force_flatten:
            # T1 風險關閉：平倉後不產生任何開倉/調倉單（allow_new_entries 已為 False 的語意）
            return orders, closed_symbols, opened_symbols

        # 2. 識別需要開倉或調倉的標的
        for sym, target_item in target_map.items():
            # 護欄：若入榜標的與外部機器人重疊，觸發避碰保護
            if _is_protected(sym):
                logger.warning(f"🛡️ [避碰保護攔截] 標的 {sym} 與外部機器人衝突，跳過開倉與調倉！")
                continue

            price = norm_prices.get(sym, 0.0)
            if price <= 0:
                logger.warning(f"缺少 {sym} 最新市價，跳過換倉計算")
                continue

            curr_qty = norm_positions.get(sym, 0.0)
            is_held = abs(curr_qty * price) >= self.min_order_notional

            # 風險閘門：禁止開新倉時，尚未持有的標的一律不進場
            if not allow_new_entries and not is_held:
                continue

            target_notional = target_item.target_notional * scale
            target_qty = (target_notional / price) if target_item.side == "long" else -(target_notional / price)

            diff_qty = target_qty - curr_qty

            # 風險閘門：禁止開新倉時，已持有標的僅允許減倉（目標量不得高於現存量）
            if not allow_new_entries:
                if (curr_qty > 0 and target_qty > curr_qty) or (curr_qty < 0 and target_qty < curr_qty):
                    diff_qty = 0.0

            diff_notional = diff_qty * price

            # 若差異未達到最小交易門檻（如 5 USDT），則不調整以節省手續費與摩擦
            if abs(diff_notional) < self.min_order_notional:
                continue

            side = "buy" if diff_qty > 0 else "sell"
            reason = "enter_new" if not is_held else "adjust_weight"
            # 減倉單標記 reduce-only（實盤執行時帶 reduceOnly 參數、於先平倉階段送出）
            reducing = (side == "sell" and curr_qty > 0) or (side == "buy" and curr_qty < 0)

            orders.append(
                RebalanceOrder(
                    symbol=sym,
                    side=side,
                    order_type="market",
                    amount=abs(diff_qty),
                    notional_usdt=round(abs(diff_notional), 2),
                    reduce_only=reducing,
                    reason=reason,
                    status="pending"
                )
            )
            if not is_held:
                opened_symbols.append(sym)

        return orders, closed_symbols, opened_symbols

    @staticmethod
    def _is_paper_adapter(exchange_adapter: Optional[ExchangeInterface]) -> bool:
        """判定適配器是否為模擬帳戶 (Paper)"""
        if not exchange_adapter:
            return False
        cfg = getattr(exchange_adapter, "config", None)
        ex_id = getattr(cfg, "exchange_id", "") if cfg else ""
        cfg_name = getattr(cfg, "name", "") if cfg else ""
        adapter_name = getattr(exchange_adapter, "name", "") or getattr(exchange_adapter, "exchange_name", "") or cfg_name
        cls_name = type(exchange_adapter).__name__
        return (
            "paper" in str(ex_id).lower()
            or "paper" in str(adapter_name).lower()
            or cls_name.lower().startswith("paper")
        )

    @classmethod
    def _find_exchange_market(cls, exchange_adapter: Optional[ExchangeInterface], symbol: str) -> Optional[dict]:
        """從交易所適配器查找標的之合約市場資訊"""
        if not exchange_adapter:
            return None
        if cls._is_paper_adapter(exchange_adapter):
            return {"symbol": symbol, "active": True, "linear": True}

        markets = getattr(exchange_adapter, "_market_info", {})
        if not markets and hasattr(exchange_adapter, "_rest"):
            markets = getattr(exchange_adapter._rest, "_market_info", {})

        if not markets:
            return None

        clean_sym = symbol.upper().replace("/", "").replace(":", "")
        for m in markets.values():
            if not isinstance(m, dict):
                continue
            m_id = (m.get("id") or "").upper().replace("/", "").replace(":", "")
            m_sym = (m.get("symbol") or "").upper().replace("/", "").replace(":", "")
            if clean_sym in (m_id, m_sym):
                if m.get("linear") or m.get("contract") or m.get("swap"):
                    return m
        return None

    @staticmethod
    def _quantize_order_amount(amount: float, market: Optional[dict]) -> Tuple[float, Optional[str]]:
        """
        依交易所合約之精度（precision.amount / stepSize）與最小下單量動態裁切。
        """
        if not market:
            precision_dec = 6 if amount < 1.0 else 4
            clean_amt = float(quantize_quantity(Decimal(str(amount)), precision_dec))
            return (clean_amt, None if clean_amt > 0 else "數量經精度裁切後為 0")

        step = market.get("precision", {}).get("amount")
        min_amt = market.get("limits", {}).get("amount", {}).get("min") or 0.0

        if step is not None and float(step) > 0:
            step_val = float(step)
            step_dec = Decimal(str(step_val))
            amt_dec = Decimal(str(amount))
            clean_dec = (amt_dec // step_dec) * step_dec
            clean_amt = float(clean_dec)
        else:
            precision_dec = 6 if amount < 1.0 else 4
            clean_amt = float(quantize_quantity(Decimal(str(amount)), precision_dec))

        if clean_amt <= 0:
            if min_amt and amount < float(min_amt):
                return 0.0, f"數量 ({amount}) 低於交易所最小合約下單量 ({min_amt})"
            return 0.0, f"數量 ({amount}) 經合約步長 ({step}) 裁切後為 0"

        if min_amt and clean_amt < float(min_amt):
            return 0.0, f"數量 ({clean_amt}) 低於交易所最小合約下單量 ({min_amt})"

        return clean_amt, None

    async def execute_rebalance(
        self,
        rank_result: UniverseRankResult,
        exchange_adapter: Optional[ExchangeInterface] = None,
        dry_run: Optional[bool] = None,
        prices_override: Optional[Dict[str, float]] = None,
        capital_override: Optional[float] = None,
        leverage_override: Optional[float] = None,
        managed_symbols: Optional[Set[str]] = None,
        protected_symbols: Optional[Set[str]] = None,
        account_id: Optional[str] = None,
        gross_scale: float = 1.0,
        allow_new_entries: bool = True,
        force_flatten: bool = False,
        gate_pts: Optional[int] = None,
        batch_id: Optional[str] = None
    ) -> RebalanceReport:
        """
        執行一籃子換倉調度（支援動態本金/槓桿覆蓋與專屬持倉籃子/外部機器人隔離保護）

        風險閘門參數由 service 層依 RegimeDecision 傳入：
        gross_scale / allow_new_entries / force_flatten（見 calculate_diff 說明）。

        記帳（P4-1 統一記帳層）：
        * 一律經 ``StrategyLedger``（→ LedgerRepository → create_ledger_store），
          因此 PostgreSQL 真相源模式下不會產生幽靈 SQLite 資料源。
        * ``batch_id`` 為本批換倉的穩定識別碼，作為 ``bot_events`` 的 dedupe_key，
          使同批重跑/補跑為冪等 no-op。
        * 實盤成交以均價法套用到 ``bot_baskets`` 並回寫 realized_pnl；
          Dry-Run 僅以唯讀方式估算損益，**絕不改變帳本狀態**。
        """
        is_dry_run = self.dry_run if dry_run is None else dry_run
        effective_capital = float(capital_override) if capital_override is not None and float(capital_override) > 0 else self.total_capital
        effective_leverage = float(leverage_override) if leverage_override is not None and float(leverage_override) > 0 else self.leverage

        target_account = (
            account_id
            or getattr(exchange_adapter, "name", None)
            or getattr(exchange_adapter, "exchange_name", None)
            or self.config.get("portfolio", {}).get("exchange", "paper")
        ).lower().strip()
        bot_id = f"coinfull_prime:{target_account}"
        batch = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        report = RebalanceReport(
            dry_run=is_dry_run,
            exchange=target_account,
            total_capital_usdt=effective_capital,
            leverage=effective_leverage,
            managed_symbols=sorted(list(managed_symbols or set())),
            protected_symbols=sorted(list(protected_symbols or set())),
            gate_pts=gate_pts,
            gate_scale=round(min(max(float(gross_scale), 0.0), 2.0), 4),
            gate_allow_new_entries=bool(allow_new_entries),
            gate_force_flatten=bool(force_flatten),
            batch_id=batch
        )

        # 0. 向 Canonical Ledger 登記/更新機器人身分（遵循 P1-6 帳本完整性規範）
        #    P4-1：唯一路徑為 StrategyLedger（自動路由 SQLite / PostgreSQL 真相源），
        #    不再直接 new SqliteLedgerStore()（幽靈資料源 + 繞過 EnvironmentGuard）。
        from core.infrastructure.database.strategy_ledger import StrategyLedger

        ledger = StrategyLedger.instance()
        mode = "live" if "live" in target_account else "paper"
        ledger.ensure_bot(
            bot_id=bot_id,
            strategy_type="coinfull_prime",
            symbol="BASKET_16",
            exchange=target_account,
            account_mode=mode,
            status="running",
        )

        # 1. 取得當前現有持倉與行情價格
        current_positions: Dict[str, float] = {}
        prices: Dict[str, float] = prices_override or dict(getattr(rank_result, "prices", {}))

        if exchange_adapter:
            try:
                positions = await exchange_adapter.get_positions()
                for p in positions:
                    raw_size = getattr(p, "size", None) or getattr(p, "amount", Decimal("0"))
                    if p.symbol and raw_size != Decimal("0"):
                        size = float(raw_size)
                        side_str = str(getattr(p.side, "value", p.side) if hasattr(p, "side") else "long").lower()
                        signed_size = size if ("long" in side_str or "buy" in side_str) else -size
                        clean_sym = self._normalize_symbol(p.symbol)
                        current_positions[clean_sym] = signed_size
            except Exception as e:
                logger.warning(f"獲取現有持倉失敗，假設目前零庫存: {e}")

            # 針對今日所有標的補全價格
            target_symbols = [x.symbol for x in rank_result.long_list + rank_result.short_list]
            all_needed_symbols = set(target_symbols) | set(current_positions.keys())
            for s in all_needed_symbols:
                if s not in prices:
                    try:
                        ticker = await exchange_adapter.get_ticker(s)
                        if ticker and ticker.last_price:
                            prices[s] = float(ticker.last_price)
                    except Exception:
                        pass

        # 2. 計算差異訂單 (傳入外部機器人保護名單、專屬籃子名單與風險閘門參數)
        orders, closed_syms, opened_syms = self.calculate_diff(
            current_positions=current_positions,
            prices=prices,
            rank_result=rank_result,
            managed_symbols=managed_symbols,
            protected_symbols=protected_symbols,
            gross_scale=gross_scale,
            allow_new_entries=allow_new_entries,
            force_flatten=force_flatten
        )
        report.orders = orders
        report.closed_symbols = closed_syms
        report.opened_symbols = opened_syms

        if force_flatten:
            logger.info(
                f"⛔ [風險閘門 T1] pts={gate_pts} 風險關閉 → 強制平倉所有受管部位，停止新倉 "
                f"(scale={report.gate_scale}, allow_new_entries={allow_new_entries})"
            )
        elif not allow_new_entries:
            logger.info(
                f"️ [風險閘門] pts={gate_pts} 停止開新倉 (scale={report.gate_scale}，僅允許減倉)"
            )

        logger.info(
            f"換倉計算完成: 共生成 {len(orders)} 筆訂單 (平倉退出: {len(closed_syms)}, 新進開倉: {len(opened_syms)}) "
            f"[閘門: pts={gate_pts}, scale={report.gate_scale}, allow_new={allow_new_entries}, flatten={force_flatten}]"
        )

        # 3. 若為 Dry-Run 模擬模式，標記為 simulated、寫入模擬流水帳本並返回
        if is_dry_run:
            logger.info("⚡ [DRY-RUN 模式] 僅模擬換倉，不發送真實訂單至交易所")
            # Dry-Run 記帳：寫入模擬成交流水（可回放、可稽核），但 **不改變籃子狀態**；
            # 損益以唯讀均價法估算，讓紙上驗證的 PnL 與實盤同一套公式。
            basket_view = self._basket_view(ledger, bot_id, target_account)
            for o in report.orders:
                o.status = "simulated"
                price = prices.get(o.symbol, 0.0)
                try:
                    from core.infrastructure.database.models import TradeFill

                    sim_role = "simulated_exit" if o.reduce_only else "simulated_entry"
                    estimated_pnl = self._estimate_realized_pnl(
                        basket_view.get(o.symbol), o.side, price, o.amount, o.reduce_only
                    )
                    fill = TradeFill(
                        # 穩定 fill_id（含 batch）→ 同批重跑為冪等 no-op，不再產生重複流水
                        fill_id=f"sim_{target_account}_{batch}_{o.symbol}_{o.side}",
                        bot_id=bot_id,
                        exchange_order_id=None,
                        client_order_id=f"sim_{batch}_{o.symbol}_{o.side}",
                        side=o.side,
                        role=sim_role,
                        price=price,
                        quantity=o.amount,
                        fee=0.0,
                        fee_currency="USDT",
                        realized_pnl=round(estimated_pnl, 8),
                        executed_at=time.time(),
                    )
                    ledger.record_fill(fill)
                except Exception as fe:
                    logger.warning(f"記錄模擬成交流水異常（不影響換倉）: {fe}")

            report.status = "completed_dry_run"

            # 寫入 bot_events 稽核紀錄（含完整訂單明細；dedupe_key 保證同批冪等）
            try:
                orders_payload = self._orders_payload(report.orders, prices)
                ledger.append_event(
                    bot_id=bot_id,
                    event_type="daily_rebalance",
                    payload={
                        "account_id": target_account,
                        "batch_id": batch,
                        "dry_run": True,
                        "capital": effective_capital,
                        "leverage": effective_leverage,
                        "orders_count": len(report.orders),
                        "closed_symbols": closed_syms,
                        "opened_symbols": opened_syms,
                        "status": report.status,
                        "orders": orders_payload,
                        "estimated_realized_pnl": round(
                            sum(
                                self._estimate_realized_pnl(
                                    basket_view.get(o.symbol), o.side, prices.get(o.symbol, 0.0), o.amount, o.reduce_only
                                )
                                for o in report.orders
                                if o.reduce_only
                            ),
                            8,
                        ),
                        "gate": {
                            "pts": gate_pts,
                            "scale": report.gate_scale,
                            "allow_new_entries": report.gate_allow_new_entries,
                            "force_flatten": report.gate_force_flatten
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    },
                    dedupe_key=f"rebalance:{target_account}:{batch}:dry_run",
                )
            except Exception as ee:
                logger.warning(f"寫入 Dry-Run 換倉事件失敗: {ee}")

            return report

        # 4. 實盤執行階段 (先平後開，分批限速 Semaphore=3)
        if not exchange_adapter:
            report.status = "failed"
            report.error = "未提供有效之交易所適配器實例"
            return report

        sem = asyncio.Semaphore(3)
        # 本批換倉的已實現損益與手續費累積（由籃子均價法結算，寫入事件供稽核重算）
        realized_by_symbol: Dict[str, float] = {}
        fee_by_symbol: Dict[str, float] = {}

        async def send_order(ord_spec: RebalanceOrder):
            async with sem:
                try:
                    price = prices.get(ord_spec.symbol, 0.0)
                    if price <= 0:
                        ord_spec.status = "failed"
                        ord_spec.error = "無法取得即時市價"
                        return

                    # 0. 交易所合約市場支援度預檢
                    is_paper = self._is_paper_adapter(exchange_adapter)
                    market = self._find_exchange_market(exchange_adapter, ord_spec.symbol)
                    if not is_paper and not market:
                        ord_spec.status = "skipped"
                        ord_spec.error = f"標的 {ord_spec.symbol} 未在目標交易所合約市場上架或不支援交易"
                        logger.warning(f"⏩ [預檢攔截] 跳過未支援標的: {ord_spec.symbol} ({ord_spec.error})")
                        return

                    # 1. 精度與步長動態對齊裁切
                    clean_amount, skip_reason = self._quantize_order_amount(ord_spec.amount, market)
                    if skip_reason or clean_amount <= 0:
                        ord_spec.status = "skipped"
                        ord_spec.error = skip_reason or "數量經精度裁切後為 0"
                        logger.warning(f"⏩ [精度跳過] 標的 {ord_spec.symbol}: {ord_spec.error}")
                        return

                    # 2. 最小名義價值預檢 (5 USDT)
                    if clean_amount * price < self.min_order_notional:
                        ord_spec.status = "skipped"
                        ord_spec.error = f"下單名義價值 ({round(clean_amount * price, 2)} USDT) 低於交易所門檻 ({self.min_order_notional} USDT)"
                        logger.warning(f"⏩ [金額跳過] 標的 {ord_spec.symbol}: {ord_spec.error}")
                        return

                    order_side = OrderSide.BUY if ord_spec.side == "buy" else OrderSide.SELL
                    params = {"reduceOnly": True} if ord_spec.reduce_only else {}

                    res = await exchange_adapter.create_order(
                        symbol=ord_spec.symbol,
                        side=order_side,
                        order_type=OrderType.MARKET,
                        amount=Decimal(str(clean_amount)),
                        params=params
                    )
                    ord_spec.status = "filled"
                    logger.info(f"✅ 換倉下單成功: {ord_spec.symbol} {ord_spec.side} {clean_amount} ({ord_spec.reason})")

                    # 寫入 Canonical Ledger：籃子部位（均價法結算 realized_pnl）+ 逐筆成交流水
                    try:
                        from core.infrastructure.database.models import TradeFill

                        exec_price = float(getattr(res, "average", None) or getattr(res, "price", None) or price)
                        exec_amount = float(getattr(res, "filled", None) or clean_amount)
                        exec_order_id = str(getattr(res, "id", None) or getattr(res, "order_id", None) or "")
                        exec_client_id = str(
                            getattr(res, "client_id", None) or f"cfp_{batch}_{ord_spec.symbol}_{ord_spec.side}"
                        )
                        fee_val = 0.0
                        fee_curr = "USDT"
                        if hasattr(res, "fee") and isinstance(res.fee, dict):
                            fee_val = float(res.fee.get("cost", 0.0) or 0.0)
                            fee_curr = str(res.fee.get("currency", "USDT"))
                        now_ts = time.time()
                        live_role = "rebalance_exit" if ord_spec.reduce_only else "rebalance_entry"
                        # 籃子方向語意：開倉單的 side 即部位方向；平倉單以既有成分方向結算
                        if ord_spec.reduce_only:
                            basket_side = "short" if ord_spec.side == "buy" else "long"
                        else:
                            basket_side = "short" if ord_spec.side == "sell" else "long"
                        # 先把成交套用到籃子（bot_baskets），取回本次已實現損益後再寫流水，
                        # 使 trade_fills.realized_pnl 與籃子均價一致、可由 DB 獨立重算。
                        realized = ledger.apply_basket_fill(
                            bot_id=bot_id,
                            account_id=target_account,
                            symbol=ord_spec.symbol,
                            side=basket_side,
                            price=exec_price,
                            quantity=exec_amount,
                            reduce_only=bool(ord_spec.reduce_only),
                        )
                        realized_by_symbol[ord_spec.symbol] = (
                            realized_by_symbol.get(ord_spec.symbol, 0.0) + float(realized or 0.0)
                        )
                        fee_by_symbol[ord_spec.symbol] = fee_by_symbol.get(ord_spec.symbol, 0.0) + fee_val
                        fill = TradeFill(
                            # 穩定 fill_id：優先用交易所 order id；否則用 batch+symbol+side，
                            # 避免毫秒時間戳造成的重複入帳（稽核 L13）
                            fill_id=f"cfp_{target_account}_{exec_order_id or batch}_{ord_spec.symbol}_{ord_spec.side}",
                            bot_id=bot_id,
                            exchange_order_id=exec_order_id or None,
                            client_order_id=exec_client_id,
                            side=ord_spec.side,
                            role=live_role,
                            price=exec_price,
                            quantity=exec_amount,
                            fee=fee_val,
                            fee_currency=fee_curr,
                            realized_pnl=round(float(realized or 0.0), 8),
                            executed_at=now_ts
                        )
                        ledger.record_fill(fill)
                    except Exception as fe:
                        logger.warning(f"寫入成交紀錄至 trade_fills 失敗（記帳降級，已計入健康度）: {fe}")

                except Exception as ex:
                    ord_spec.status = "failed"
                    ord_spec.error = str(ex)
                    logger.error(f"❌ 換倉下單失敗: {ord_spec.symbol} {ord_spec.side}: {ex}")

        # 第一階段：先執行退出平倉單（釋放保證金）
        exit_orders = [o for o in report.orders if o.reduce_only]
        if exit_orders:
            logger.info(f"執行第 1 階段平倉單 ({len(exit_orders)} 筆)...")
            await asyncio.gather(*[send_order(o) for o in exit_orders])

        # 第二階段：再執行開倉與補差額單
        entry_orders = [o for o in report.orders if not o.reduce_only]
        if entry_orders:
            logger.info(f"執行第 2 階段開倉單 ({len(entry_orders)} 筆)...")
            await asyncio.gather(*[send_order(o) for o in entry_orders])

        # 寫入 Canonical Ledger 稽核記帳（完整 orders 清單 + 損益/手續費彙總 + 冪等 dedupe_key）
        try:
            orders_payload = self._orders_payload(report.orders, prices)
            realized_total = round(sum(realized_by_symbol.values()), 8)
            fee_sum = round(sum(fee_by_symbol.values()), 8)
            report.realized_pnl_usdt = realized_total
            report.fees_usdt = fee_sum
            ledger.append_event(
                bot_id=bot_id,
                event_type="daily_rebalance",
                payload={
                    "account_id": target_account,
                    "batch_id": batch,
                    "dry_run": False,
                    "capital": effective_capital,
                    "leverage": effective_leverage,
                    "orders_count": len(report.orders),
                    "closed_symbols": closed_syms,
                    "opened_symbols": opened_syms,
                    "status": report.status,
                    "orders": orders_payload,
                    "realized_pnl_usdt": realized_total,
                    "realized_by_symbol": {k: round(v, 8) for k, v in realized_by_symbol.items()},
                    "fees_usdt": fee_sum,
                    "gate": {
                        "pts": gate_pts,
                        "scale": report.gate_scale,
                        "allow_new_entries": report.gate_allow_new_entries,
                        "force_flatten": report.gate_force_flatten
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat()
                },
                dedupe_key=f"rebalance:{target_account}:{batch}:live",
            )
            logger.info(
                f"已將實盤換倉事件寫入 Canonical Ledger (bot_id={bot_id}, batch={batch}, "
                f"orders={len(report.orders)}, realized={realized_total} USDT, fees={fee_sum} USDT)"
            )
        except Exception as ee:
            logger.warning(f"寫入換倉事件至 bot_events 失敗: {ee}")

        # 籃子成分同步（DB 為真相源；service 層另寫檔案鏡像）
        try:
            entries = []
            from core.infrastructure.database.models import BasketEntry

            target_side_map: Dict[str, str] = {}
            for item in list(rank_result.long_list or []) + list(rank_result.short_list or []):
                target_side_map[self._normalize_symbol(item.symbol)] = "long" if item.side != "short" else "short"
            for o in report.orders:
                if o.status not in ("filled", "simulated"):
                    continue
                # 只同步「開倉/調倉」成分；平倉成分的 status/quantity 由 apply_basket_fill
                # 結算擁有（否則已平倉的成分會被名單同步重新標記為 active → 幽靈持倉）
                if o.reduce_only:
                    continue
                entries.append(
                    BasketEntry(
                        bot_id=bot_id,
                        account_id=target_account,
                        symbol=o.symbol,
                        side=target_side_map.get(o.symbol, "short" if o.side == "sell" and not o.reduce_only else "long"),
                        quantity=0.0,           # 保留 apply_basket_fill 已結算的數量
                        notional_usdt=float(o.notional_usdt or 0.0),
                        status="active",
                    )
                )
            if entries:
                ledger.save_basket(bot_id, target_account, entries, close_missing=False)
        except Exception as be:
            logger.warning(f"同步籃子成分至 canonical ledger 失敗: {be}")

        return report

    # ------------------------------------------------------------------
    # P4-1 統一記帳輔助
    # ------------------------------------------------------------------
    @staticmethod
    def _orders_payload(orders: List[RebalanceOrder], prices: Dict[str, float]) -> List[dict]:
        """把訂單清單轉為可稽核的 JSON 負載（事件與 API 共用同一格式）。"""
        return [
            {
                "symbol": o.symbol,
                "side": o.side,
                "amount": o.amount,
                "price": prices.get(o.symbol, 0.0),
                "notional_usdt": o.notional_usdt or round(prices.get(o.symbol, 0.0) * o.amount, 2),
                "reduce_only": o.reduce_only,
                "reason": o.reason,
                "status": o.status,
                "error": o.error,
            }
            for o in orders
        ]

    @staticmethod
    def _basket_view(ledger, bot_id: str, account_id: str) -> Dict[str, Any]:
        """讀取籃子 currently-active 成分（Dry-Run 損益估算用，唯讀不改帳）。"""
        try:
            return {e.symbol: e for e in ledger.load_basket(bot_id, account_id=account_id, active_only=True)}
        except Exception as ex:
            logger.debug(f"讀取籃子成分失敗（估算損益退回 0）: {ex}")
            return {}

    @staticmethod
    def _estimate_realized_pnl(entry, side: str, price: float, quantity: float, reduce_only: bool) -> float:
        """以均價法「唯讀」估算一筆成交的已實現損益（與 apply_basket_fill 同一公式）。"""
        if entry is None or not reduce_only:
            return 0.0
        entry_price = getattr(entry, "entry_price", None)
        held_qty = float(getattr(entry, "quantity", 0.0) or 0.0)
        if not entry_price or held_qty <= 0.0 or price <= 0.0:
            return 0.0
        closed_qty = min(float(quantity or 0.0), held_qty)
        direction_sign = -1.0 if (getattr(entry, "side", "long") == "short") else 1.0
        return round((float(price) - float(entry_price)) * closed_qty * direction_sign, 8)
