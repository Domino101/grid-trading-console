"""
持仓跟踪器实现

跟踪网格系统的持仓、盈亏、交易历史等
"""

import time
from typing import Dict, List, Deque, Optional
from decimal import Decimal
from datetime import datetime, timedelta
from collections import deque

from ....logging import get_logger
from ..interfaces.position_tracker import IPositionTracker
from ..models import (
    GridOrder, GridStatistics, GridMetrics,
    GridConfig, GridState
)


class PositionTrackerImpl(IPositionTracker):
    """
    持仓跟踪器实现

    功能：
    1. 跟踪当前持仓和成本
    2. 计算已实现和未实现盈亏
    3. 记录交易历史
    4. 生成统计数据
    """

    def __init__(self, config: GridConfig, grid_state: GridState, bot_id: Optional[str] = None):
        """
        初始化持仓跟踪器

        Args:
            config: 网格配置
            grid_state: 网格状态
            bot_id: 穩定機器人身份（P1-3）；提供時逐筆寫入不可變成交流水 trade_fills
        """
        self.logger = get_logger(__name__)
        self.config = config
        self.state = grid_state
        self.bot_id = bot_id

        # 網格自身虛擬持倉（僅受網格自身掛單成交影響，隔離手動干預）
        self.virtual_position = Decimal('0')      # 網格自身持倉數量
        self.virtual_average_cost = Decimal('0')  # 網格自身加權成本
        self.current_position = Decimal('0')      # 兼容舊屬性（指向虛擬持倉）
        self.position_cost = Decimal('0')         # 持仓总成本
        self.average_cost = Decimal('0')          # 平均成本

        # 盈亏统计
        self.grid_profit = Decimal('0')           # 網格套利毛利
        self.realized_pnl = Decimal('0')          # 已实现盈亏（兼容舊屬性）
        self.total_fees = Decimal('0')            # 总手续费

        # 交易所全帳戶資訊（可選）
        self.account_position = None              # 交易所全帳戶持倉
        self.account_unrealized_pnl = None        # 交易所全帳戶未實現盈虧

        # 交易历史（最近1000条）
        self.trade_history: Deque[Dict] = deque(maxlen=1000)

        # 🔥 FIFO 未撮合單隊列（用於真實網格套利配對與真實利潤計算，取代 min(buy, sell) 估算）
        self._unmatched_buys: Deque[Dict] = deque()
        self._unmatched_sells: Deque[Dict] = deque()

        # 统计信息
        self.buy_count = 0
        self.sell_count = 0
        self.completed_cycles = 0
        self.loss_cycles = 0

        # 资金信息（需要从交易所获取）
        self.available_balance = Decimal('0')
        self.frozen_balance = Decimal('0')

        # 时间信息
        self.start_time = datetime.now()
        self.last_trade_time = datetime.now()

        self.logger.info("持仓跟踪器初始化完成 (支援虛擬持倉與手動干預隔離，啟用 FIFO 真實套利撮合)")

    def record_filled_order(self, order: GridOrder):
        """
        記錄網格訂單成交，並以 FIFO 真實撮合維護獨立虛擬持倉與套利毛利。
        """
        if not order.is_filled():
            self.logger.warning(f"订单{order.order_id}未成交，跳过记录")
            return

        filled_price = order.filled_price or order.price
        filled_amount = order.filled_amount or order.amount

        # 1. 維護網格策略自身的獨立虛擬持倉 (Virtual Position)
        if order.is_buy_order():
            self.buy_count += 1
            old_pos = self.virtual_position
            self.virtual_position += filled_amount
            if self.virtual_position > 0:
                if old_pos > 0 and self.virtual_average_cost > 0:
                    self.virtual_average_cost = (old_pos * self.virtual_average_cost + filled_amount * filled_price) / self.virtual_position
                else:
                    self.virtual_average_cost = filled_price
            self.logger.debug(f"買入記錄: {filled_amount}@{filled_price}, 虛擬持倉: {self.virtual_position}")
        else:
            self.sell_count += 1
            old_pos = self.virtual_position
            self.virtual_position -= filled_amount
            if self.virtual_position < 0:
                if old_pos < 0 and self.virtual_average_cost > 0:
                    self.virtual_average_cost = (abs(old_pos) * self.virtual_average_cost + filled_amount * filled_price) / abs(self.virtual_position)
                else:
                    self.virtual_average_cost = filled_price
            self.logger.debug(f"賣出記錄: {filled_amount}@{filled_price}, 虛擬持倉: {self.virtual_position}")

        if self.virtual_position == 0:
            self.virtual_average_cost = Decimal('0')

        self.current_position = self.virtual_position
        self.average_cost = self.virtual_average_cost

        # 2. 計算手續費（優先使用訂單實際手續費，若無則依交易對與配置解析）
        order_fee = getattr(order, 'fee', None)
        if order_fee is not None:
            fee = Decimal(str(order_fee))
        else:
            fee_rate = self.resolve_fee_rate()
            fee = filled_price * filled_amount * fee_rate
        self.total_fees += fee

        # 3. 🔥 真實 FIFO 訂單配對撮合（計算真實套利輪迴數與真實價差毛利）
        matched_profit = Decimal('0')
        matched_any = False
        rem_amount = filled_amount

        if order.is_buy_order():
            # 買單成交：若有未撮合的賣單（空頭網格或中性網格先賣後買），以 FIFO 撮合
            while rem_amount > Decimal('1e-8') and self._unmatched_sells:
                sell_lot = self._unmatched_sells[0]
                match_qty = min(rem_amount, sell_lot['amount'])
                lot_profit = (sell_lot['price'] - filled_price) * match_qty
                matched_profit += lot_profit
                self.grid_profit += lot_profit
                self.realized_pnl = self.grid_profit
                if lot_profit > Decimal('0'):
                    self.completed_cycles += 1
                else:
                    self.loss_cycles += 1
                matched_any = True

                sell_lot['amount'] -= match_qty
                rem_amount -= match_qty
                if sell_lot['amount'] <= Decimal('1e-8'):
                    self._unmatched_sells.popleft()

            if rem_amount > Decimal('1e-8'):
                self._unmatched_buys.append({
                    'order_id': order.order_id,
                    'price': filled_price,
                    'amount': rem_amount,
                    'time': order.filled_at or datetime.now()
                })
        else:
            # 賣單成交：若有未撮合的買單（多頭網格或中性網格先買後賣），以 FIFO 撮合
            while rem_amount > Decimal('1e-8') and self._unmatched_buys:
                buy_lot = self._unmatched_buys[0]
                match_qty = min(rem_amount, buy_lot['amount'])
                lot_profit = (filled_price - buy_lot['price']) * match_qty
                matched_profit += lot_profit
                self.grid_profit += lot_profit
                self.realized_pnl = self.grid_profit
                if lot_profit > Decimal('0'):
                    self.completed_cycles += 1
                else:
                    self.loss_cycles += 1
                matched_any = True

                buy_lot['amount'] -= match_qty
                rem_amount -= match_qty
                if buy_lot['amount'] <= Decimal('1e-8'):
                    self._unmatched_buys.popleft()

            if rem_amount > Decimal('1e-8'):
                self._unmatched_sells.append({
                    'order_id': order.order_id,
                    'price': filled_price,
                    'amount': rem_amount,
                    'time': order.filled_at or datetime.now()
                })

        # 4. 記錄交易歷史
        profit_for_record = matched_profit if matched_any else None
        self._record_trade(order, filled_price, filled_amount, fee, profit_for_record)

        # 5. P1-3: 逐筆寫入不可變成交流水（append-only，冪等；失敗不影響交易）
        self._persist_fill_to_ledger(
            order, filled_price, filled_amount, fee,
            matched_profit if matched_any else Decimal('0'),
        )

        # 更新最后交易时间
        self.last_trade_time = datetime.now()

        self.logger.info(
            f"記錄成交: {order.side.value} {filled_amount}@{filled_price}, "
            f"套利配對: {matched_any} (本次獲利={matched_profit}), "
            f"累計套利={self.completed_cycles}格, 套利毛利={self.grid_profit}, 手續費={self.total_fees}"
        )

    def _persist_fill_to_ledger(
        self,
        order: GridOrder,
        price: Decimal,
        amount: Decimal,
        fee: Decimal,
        realized_pnl: Decimal,
    ) -> None:
        """P1-3: 將單筆 grid 成交寫入不可變成交流水 trade_fills（冪等 append-only）。"""
        if not self.bot_id:
            return
        try:
            from core.infrastructure.database.ledger_repository import LedgerRepository
            from core.infrastructure.database.models import TradeFill

            executed_at = (
                order.filled_at.timestamp()
                if order.filled_at is not None
                else time.time()
            )
            client_order_id = order.client_id or order.order_id
            fill = TradeFill(
                fill_id=order.client_id or order.order_id,
                bot_id=self.bot_id,
                exchange_order_id=order.order_id,
                client_order_id=client_order_id,
                side="buy" if order.is_buy_order() else "sell",
                role="grid",
                price=float(price),
                quantity=float(amount),
                fee=float(fee) if fee is not None else 0.0,
                fee_currency=None,
                realized_pnl=float(realized_pnl) if realized_pnl is not None else 0.0,
                executed_at=executed_at,
            )
            LedgerRepository.instance().record_fill(fill)
        except Exception as exc:  # 帳本失敗絕不阻斷交易
            self.logger.warning(f"寫入不可變成交流水失敗 (略過，不影響交易): {exc}")

    def resolve_fee_rate(self) -> Decimal:
        """
        解析交易對手續費率（優先依據用戶配置，若無或為None則依交易所與交易對規則自動判斷）
        """
        cfg_fee = getattr(self.config, 'fee_rate', None)
        if cfg_fee is not None:
            return Decimal(str(cfg_fee))

        exchange = str(getattr(self.config, 'exchange', '')).lower()
        symbol = str(getattr(self.config, 'symbol', '')).upper()

        if exchange == 'binance':
            # 幣安合約 USDC 保證金交易對享有 0 掛單手續費 (Zero Maker Fee Promotion)
            if 'USDC' in symbol:
                return Decimal('0')
            # 幣安 USDT 合約標準 Maker 費率 0.02%
            return Decimal('0.0002')
        elif exchange == 'lighter':
            return Decimal('0.00002')
        elif exchange == 'hyperliquid':
            return Decimal('0.0001')
        elif exchange == 'okx':
            return Decimal('0.0002')
        elif exchange == 'paper':
            return Decimal('0')

        return Decimal('0')

    def _record_trade(self, order: GridOrder, price: Decimal, amount: Decimal, fee: Decimal = Decimal('0'), profit: Decimal = None):
        """
        记录交易到历史

        Args:
            order: 订单
            price: 成交价格
            amount: 成交数量
            fee: 交易手续费
            profit: 利润（套利配对成功才有）
        """
        trade_record = {
            'time': order.filled_at or datetime.now(),
            'order_id': order.order_id,
            'grid_id': order.grid_id,
            'side': order.side.value if hasattr(order.side, 'value') else str(order.side),
            'price': float(price),
            'amount': float(amount),
            'value': float(price * amount),
            'fee': float(fee) if fee is not None else 0.0,
            'profit': float(profit) if profit is not None else None,
            'position_after': float(self.current_position),
            'realized_pnl': float(self.realized_pnl)
        }

        self.trade_history.append(trade_record)

    def get_current_position(self) -> Decimal:
        """
        获取当前持仓

        Returns:
            持仓数量（正数=多头，负数=空头）
        """
        return self.current_position

    def get_average_cost(self) -> Decimal:
        """
        获取平均持仓成本

        Returns:
            平均成本
        """
        return self.average_cost

    def calculate_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """
        計算網格自身虛擬持倉的未實現盈虧（完全隔離手動干預）

        Args:
            current_price: 当前价格

        Returns:
            未实现盈亏
        """
        pos = self.virtual_position if self.virtual_position != 0 else self.current_position
        cost = self.virtual_average_cost if self.virtual_average_cost != 0 else self.average_cost
        if pos == 0 or cost == 0:
            return Decimal('0')

        if pos > 0:
            return (current_price - cost) * pos
        else:
            return (cost - current_price) * abs(pos)

    def get_realized_pnl(self) -> Decimal:
        """
        获取网格套利毛利（已实现盈亏）

        Returns:
            已实现盈亏
        """
        return self.grid_profit or self.realized_pnl

    def get_total_pnl(self, current_price: Decimal) -> Decimal:
        """
        获取总盈亏（网格毛利 + 虚拟未实现盈亏）

        Args:
            current_price: 当前价格

        Returns:
            总盈亏
        """
        unrealized = self.calculate_unrealized_pnl(current_price)
        return (self.grid_profit or self.realized_pnl) + unrealized

    def get_statistics(self) -> GridStatistics:
        """
        获取统计数据（三柱收益体系）

        Returns:
            网格统计数据
        """
        # 获取当前价格
        current_price = self.state.current_price or self.config.get_first_order_price()

        # 若當前交易對為 0 手續費率（如幣安 USDC 合約），確保手續費為 0
        if self.resolve_fee_rate() == Decimal('0'):
            self.total_fees = Decimal('0')

        # 计算未实现盈亏与净收益
        unrealized_pnl = self.calculate_unrealized_pnl(current_price)
        total_pnl = self.grid_profit + unrealized_pnl
        net_profit = total_pnl - self.total_fees

        # 计算收益率
        initial_capital = self.config.order_amount * \
            self.config.grid_count * current_price
        profit_rate = (net_profit / initial_capital *
                       100) if initial_capital > 0 else Decimal('0')

        # 计算资金利用率
        total_balance = self.available_balance + self.frozen_balance
        capital_utilization = (
            self.frozen_balance / total_balance * 100) if total_balance > 0 else 0.0

        # 运行时长
        running_time = datetime.now() - self.start_time

        statistics = GridStatistics(
            grid_count=self.config.grid_count,
            grid_interval=self.config.grid_interval,
            price_range=(self.config.lower_price, self.config.upper_price),
            current_price=current_price,
            current_grid_id=self.state.current_grid_id or 1,
            current_position=self.virtual_position,
            average_cost=self.virtual_average_cost,
            pending_buy_orders=self.state.pending_buy_orders,
            pending_sell_orders=self.state.pending_sell_orders,
            total_pending_orders=self.state.pending_buy_orders + self.state.pending_sell_orders,
            filled_buy_count=self.buy_count,
            filled_sell_count=self.sell_count,
            completed_cycles=self.completed_cycles,
            realized_profit=self.grid_profit,
            unrealized_profit=unrealized_pnl,
            total_profit=total_pnl,
            total_fees=self.total_fees,
            net_profit=net_profit,
            profit_rate=profit_rate,
            grid_utilization=self.state.get_grid_utilization(),
            spot_balance=self.available_balance,  # 本地追踪器计算的余额映射为现货余额
            collateral_balance=Decimal('0'),  # 本地追踪器不计算抵押品
            order_locked_balance=self.frozen_balance,  # 订单冻结资金
            total_balance=total_balance,
            capital_utilization=capital_utilization,
            running_time=running_time,
            last_trade_time=self.last_trade_time,
            grid_profit=self.grid_profit,
            grid_position=self.virtual_position,
            grid_average_cost=self.virtual_average_cost,
            account_position=self.account_position,
            account_unrealized_pnl=self.account_unrealized_pnl
        )

        return statistics

    def get_metrics(self) -> GridMetrics:
        """
        获取性能指标

        Returns:
            网格性能指标
        """
        metrics = GridMetrics()

        # 获取当前价格
        current_price = self.state.current_price or self.config.get_first_order_price()

        # 计算总利润
        metrics.total_profit = self.get_total_pnl(current_price)

        # 计算收益率
        initial_capital = self.config.order_amount * \
            self.config.grid_count * current_price
        if initial_capital > 0:
            metrics.profit_rate = (
                metrics.total_profit / initial_capital * 100)

        # 交易统计
        metrics.total_trades = self.buy_count + self.sell_count
        metrics.win_trades = self.completed_cycles
        metrics.loss_trades = self.loss_cycles

        total_matched = self.completed_cycles + self.loss_cycles
        if total_matched > 0:
            metrics.win_rate = (Decimal(str(metrics.win_trades)) / Decimal(str(total_matched))) * Decimal('100')
        elif metrics.total_trades > 0:
            metrics.win_rate = Decimal('0')

        # 手续费
        metrics.total_fees = self.total_fees

        # 运行天数
        running_time = datetime.now() - self.start_time
        metrics.running_days = max(1, running_time.days)

        # 日均收益
        if metrics.running_days > 0:
            metrics.daily_profit = metrics.total_profit / \
                Decimal(str(metrics.running_days))

        # 计算平均每笔收益
        if total_matched > 0:
            metrics.avg_profit_per_trade = self.realized_pnl / \
                Decimal(str(total_matched))
        elif self.completed_cycles > 0:
            metrics.avg_profit_per_trade = self.realized_pnl / \
                Decimal(str(self.completed_cycles))

        # 持仓统计
        metrics.max_position = abs(self.virtual_position)  # 简化处理
        metrics.avg_position = abs(self.virtual_position)

        return metrics

    def get_trade_history(self, limit: int = 10) -> List[Dict]:
        """
        获取交易历史

        Args:
            limit: 返回记录数

        Returns:
            交易记录列表
        """
        # 返回最新的N条记录
        history_list = list(self.trade_history)
        return history_list[-limit:] if len(history_list) > limit else history_list

    def update_balance(self, available: Decimal, frozen: Decimal):
        """
        更新资金信息

        Args:
            available: 可用资金
            frozen: 冻结资金
        """
        self.available_balance = available
        self.frozen_balance = frozen

    def reset(self):
        """重置跟踪器"""
        self.virtual_position = Decimal('0')
        self.virtual_average_cost = Decimal('0')
        self.current_position = Decimal('0')
        self.position_cost = Decimal('0')
        self.average_cost = Decimal('0')
        self.grid_profit = Decimal('0')
        self.realized_pnl = Decimal('0')
        self.total_fees = Decimal('0')
        self.trade_history.clear()
        self._unmatched_buys.clear()
        self._unmatched_sells.clear()
        self.buy_count = 0
        self.sell_count = 0
        self.completed_cycles = 0
        self.loss_cycles = 0
        self.start_time = datetime.now()
        self.last_trade_time = datetime.now()

        self.logger.info("持仓跟踪器已重置")

    def sync_recovered_position(self, virtual_position: Decimal, virtual_cost: Decimal):
        """
        從歷史快照恢復虛擬持倉時，同步重建未撮合隊列，確保重啟後的第一筆反向單能正確撮合。
        """
        self.virtual_position = virtual_position
        self.current_position = virtual_position
        self.virtual_average_cost = virtual_cost
        self.average_cost = virtual_cost
        self._unmatched_buys.clear()
        self._unmatched_sells.clear()
        if virtual_position > 0:
            self._unmatched_buys.append({
                'order_id': 'recovered_position',
                'price': virtual_cost if virtual_cost > 0 else Decimal('0'),
                'amount': virtual_position,
                'time': datetime.now()
            })
        elif virtual_position < 0:
            self._unmatched_sells.append({
                'order_id': 'recovered_position',
                'price': virtual_cost if virtual_cost > 0 else Decimal('0'),
                'amount': abs(virtual_position),
                'time': datetime.now()
            })

    def sync_initial_position(self, position: Decimal, entry_price: Decimal):
        """
        記錄交易所實際全帳戶持倉（供全帳戶參考，不污染網格自身虛擬持倉）

        Args:
            position: 交易所持仓数量（正数=多仓，负数=空仓）
            entry_price: 平均入场价格
        """
        old_position = self.account_position
        self.account_position = position
        self.account_average_cost = entry_price

        # 只在首次同步或持仓变化时输出info
        if old_position != position:
            self.logger.info(
                f"🔄 交易所全帳戶持倉參考: {old_position} → {position}, "
                f"成本=${entry_price} (網格自身虛擬持倉: {self.virtual_position})"
            )
        else:
            self.logger.debug(
                f"🔄 交易所全帳戶持倉: 數量={position}, 成本=${entry_price}"
            )

    def __repr__(self) -> str:
        return (
            f"PositionTracker(grid_pos={self.virtual_position}, "
            f"grid_profit={self.grid_profit}, "
            f"account_pos={self.account_position})"
        )
