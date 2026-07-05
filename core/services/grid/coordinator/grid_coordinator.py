"""
网格交易系统协调器

核心协调逻辑：
1. 初始化网格系统
2. 处理订单成交事件
3. 自动挂反向订单
4. 异常处理和暂停恢复

🔥 重要优化说明（2025-11-02）：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Lighter 订单ID统一方案
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【新方案】
下单时在 lighter_rest.py 中立即查询 order_index，
让 OrderData.id 和 OrderData.client_id 都使用 order_index（统一标识）。

【优势】
1. 消除双键映射问题（不再需要 client_order_id ⇄ order_index 的映射）
2. 简化代码逻辑（删除了 ~200 行同步代码）
3. 降低 bug 风险（无内存泄漏、无匹配失败）
4. 架构更清晰（订单ID在下单时就确定，不需要事后同步）

【影响范围】
仅 Lighter 交易所内部实现，不影响其他交易所。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
重置期间立即成交订单的延迟处理机制（2025-11-02）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【问题】
网格重置时（止盈/本金保护/价格脱离），批量下单中的立即成交订单被 `_resetting` 锁拦截，
导致反向订单未挂出，网格不完整。

【根本原因】
- 立即成交检测时间：批量下单完成 + 3秒（REST API同步延迟）
- 重置锁释放时间：整个重置流程完成（批量下单 + 本金初始化 + 清理，约16秒）
- **锁释放总是晚于立即成交检测** → 反向订单被跳过

【解决方案】
1. 重置期间缓存立即成交订单到 `_pending_immediate_fills`
2. 重置完成、锁释放后，调用 `process_pending_immediate_fills()` 处理缓存订单
3. 此时本金已重新初始化，可以安全挂反向订单

【修改文件】
- grid_coordinator.py: 添加缓存列表 and 处理方法
- scalping_operations.py: 在 finally 块中调用处理
- grid_reset_manager.py: 在所有重置方法的 finally 块中调用处理

【影响范围】
所有交易所（Backpack、Hyperliquid、Lighter）均受益.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional
from decimal import Decimal
from datetime import datetime, timedelta

from ....logging import get_logger
from ..interfaces import IGridStrategy, IGridEngine, IPositionTracker
from ..models import (
    GridConfig, GridState, GridOrder, GridOrderSide,
    GridOrderStatus, GridStatus, GridStatistics, GridType
)
from .grid_reset_manager import GridResetManager
from .position_monitor import PositionMonitor
from .balance_monitor import BalanceMonitor
from .scalping_operations import ScalpingOperations
from .stop_loss_monitor import StopLossMonitor


class GridCoordinator:
    """
    网格协调器
    负责组织和调度网格引擎、策略、状态与风控监控
    """
    def __init__(
        self,
        config: GridConfig,
        strategy: IGridStrategy,
        engine: IGridEngine,
        tracker: IPositionTracker,
        grid_state: GridState
    ):
        self.config = config
        self.strategy = strategy
        self.engine = engine
        self.tracker = tracker
        self.state = grid_state
        
        self.logger = get_logger(f"GridCoordinator.{self.config.symbol}")

        # 核心控制状态
        self._paused = False
        self._paused_reason = ""
        self._last_error: Optional[Exception] = None
        self._resetting = False  # 标志当前是否正在执行重置流程（止盈/保護重置/手動重置）

        # 延迟处理队列：重置期间产生的立即成交订单
        self._pending_immediate_fills: List[GridOrder] = []  # 🔥 重置期间缓存的立即成交订单
        self._order_fill_lock = asyncio.Lock()  # 🔥 并发下反手单锁

        # 子模块实例化
        self.reset_manager = GridResetManager(self)
        self.position_monitor = PositionMonitor(self)
        self.balance_monitor = BalanceMonitor(self)
        self.scalping_ops = ScalpingOperations(self)
        self.stop_loss_monitor = StopLossMonitor(self)

    async def start(self):
        """启动网格系统"""
        try:
            self.logger.info("正在启动网格协调器...")
            
            # 1. 检查账户余额与仓位
            await self.balance_monitor.check_initial_balance()
            
            # 2. 检查未完结的旧网格订单
            await self.recover_old_orders()
            
            # 3. 初始化首批网格订单
            if not self.state.active_orders:
                await self.reset_manager.initialize_grid_orders()

            # 4. 订阅订单更新
            self.engine.subscribe_order_updates(self._on_order_filled)
            self.logger.info("订单更新订阅完成")

            # 5. 启动后台监控任务
            asyncio.create_task(self._run_monitoring_loop())
            self.logger.info("后台监控任务启动完成")

            self.state.status = GridStatus.RUNNING
            self.logger.info("🎉 网格协调器启动成功！")

        except Exception as e:
            self.logger.error(f"启动网格协调器失败: {str(e)}")
            self.state.set_error()
            raise

    async def _on_order_filled(self, filled_order: GridOrder):
        """
        订单成交回调 - 核心逻辑
        """
        if self._paused:
            self.logger.info(f"網格處於暫停狀態，忽略成交事件：{filled_order.order_id}")
            return

        # 0. 價格鎖定判斷（極速行情防護）
        if self.config.price_lock_enabled:
            # 若觸發價格鎖定，則暫停並攔截
            pass

        # 1. 重置期间的立即成交延迟处理
        if self._resetting:
            self.logger.warning(
                f"⚠️ 網格正在執行重置/止盈/保護中，成交訂單 {filled_order.order_id} 暫不處理掛反手，"
                f"寫入延遲處理緩存 _pending_immediate_fills。"
            )
            self._pending_immediate_fills.append(filled_order)
            return

        self.logger.info(f"🔔 收到訂單成交通知: {filled_order.order_id} | Grid: {filled_order.grid_id} | Price: {filled_order.price}")

        # 2. 更新本地状态中的订单信息
        self.state.mark_order_filled(
            order_id=filled_order.order_id,
            price=filled_order.filled_price,
            amount=filled_order.filled_amount
        )

        # 3. 统计更新
        self.state.statistics.update_on_fill(filled_order)

        # 4. 计算反向订单参数
        # 🔥 剥头皮模式下可能不挂反向订单
        if self.config.is_scalping_enabled():
            # 走剥头皮專屬邏輯，可能直接獲利了結或取消其他掛單
            await self.scalping_ops.handle_scalping_fill(filled_order)
            return

        reverse_params = self.strategy.calculate_reverse_order(
            filled_order=filled_order,
            interval=self.config.grid_interval,
            distance=self.config.reverse_order_grid_distance
        )
        if not reverse_params:
            self.logger.info(f"根據策略，訂單 {filled_order.order_id} 成交後無需掛出反向訂單")
            return

        new_side, new_price, new_grid_id = reverse_params

        reverse_order = GridOrder(
            order_id="",
            grid_id=new_grid_id,
            side=new_side,
            price=new_price,
            amount=filled_order.amount,
            status=GridOrderStatus.PENDING,
            created_at=datetime.now(),
            parent_order_id=filled_order.order_id
        )

        # 🔥 [并发安全格位去重与下单] 使用 asyncio Lock 保证只有一个成交协程在做判定 and 下单
        async with self._order_fill_lock:
            # [格位去重] 反手单掛出前，先检查本地状态中是否已有同 grid_id 的 PENDING 订单
            # 场景：横盘时价格反复穿越同一格，每次成交都会挂反手单，导致同格累积多张
            existing_at_grid = [
                o for o in self.state.active_orders.values()
                if o.grid_id == new_grid_id and o.status == GridOrderStatus.PENDING
            ]
            if existing_at_grid:
                self.logger.warning(
                    f"⚠️ 反手单去重：Grid {new_grid_id} @${new_price} 已有 {len(existing_at_grid)} 张 PENDING 掛单，"
                    f"跳过重复挂单（已有 OrderID: {existing_at_grid[0].order_id[:12]}...）"
                )
                return

            # 6. 下反向订单
            # 🔥 Lighter交易所：grid_engine_impl.py 的 place_order 方法已自动使用全局锁
            # 确保所有下单操作（反手单、止盈订单、健康检查补单等）都串行执行
            placed_order = await self.engine.place_order(reverse_order, source="反手单")
            self.state.add_order(placed_order)

    async def _on_batch_orders_filled(self, filled_orders: List[GridOrder]):
        """
        批量订单成交回调 (如交易所支持批量推送或網格補掛)
        """
        if self._paused or not filled_orders:
            return

        self.logger.info(f"🔔 收到 {len(filled_orders)} 個訂單批量成交通知")
        
        # 1. 批量更新本地狀態
        for o in filled_orders:
            self.state.mark_order_filled(o.order_id, o.filled_price, o.filled_amount)
            self.state.statistics.update_on_fill(o)

        # 2. 計算批量反向訂單
        reverse_params = self.strategy.calculate_batch_reverse_orders(
            filled_orders=filled_orders,
            interval=self.config.grid_interval,
            distance=self.config.reverse_order_grid_distance
        )

        # 3. 创建反向订单列表
        reverse_orders = []
        for side, price, grid_id, amount in reverse_params:
            # 批量下反手单去重
            existing_at_grid = [
                o for o in self.state.active_orders.values()
                if o.grid_id == grid_id and o.status == GridOrderStatus.PENDING
            ]
            if existing_at_grid:
                self.logger.warning(
                    f"⚠️ 批量去重：Grid {grid_id} @${price} 已有 PENDING 掛单，跳过该反手单"
                )
                continue
            order = GridOrder(
                order_id="",
                grid_id=grid_id,
                side=side,
                price=price,
                amount=amount,
                status=GridOrderStatus.PENDING,
                created_at=datetime.now()
            )
            reverse_orders.append(order)

        # 4. 批量下单（统一幣安對稱與上限控制）
        placed_orders = await self.engine.place_batch_orders(reverse_orders)
        
        # 5. 寫入本地狀態
        for po in placed_orders:
            self.state.add_order(po)

    async def process_pending_immediate_fills(self):
        """
        🔥 處理重置期間緩存的立即成交訂單
        在 `grid_reset_manager` 或重置流程釋放 `_resetting` 鎖後，於 `finally` 區塊調用。
        此時本金已重新初始化完畢，可以安全且正確地掛出反向單，不再受 `_resetting` 的干擾。
        """
        if not self._pending_immediate_fills:
            return

        cached_count = len(self._pending_immediate_fills)
        self.logger.info(f"🚀 開始補掛重置期間緩存的 {cached_count} 個立即成交訂單的反向單...")
        
        # 1. 拷貝並清空緩存，防止重入或並發干擾
        orders_to_process = list(self._pending_immediate_fills)
        self._pending_immediate_fills.clear()

        # 2. 為這些成交訂單，更新統計與計算反向訂單
        for o in orders_to_process:
            self.state.mark_order_filled(o.order_id, o.filled_price, o.filled_amount)
            self.state.statistics.update_on_fill(o)

        reverse_params = self.strategy.calculate_batch_reverse_orders(
            filled_orders=orders_to_process,
            interval=self.config.grid_interval,
            distance=self.config.reverse_order_grid_distance
        )

        # 2. 创建反向订单列表
        reverse_orders = []
        for side, price, grid_id, amount in reverse_params:
            # 缓存订单补挂反手单去重
            existing_at_grid = [
                o for o in self.state.active_orders.values()
                if o.grid_id == grid_id and o.status == GridOrderStatus.PENDING
            ]
            if existing_at_grid:
                self.logger.warning(
                    f"⚠️ 缓存补挂去重：Grid {grid_id} @${price} 已有 PENDING 掛单，跳过该反手单"
                )
                continue
            order = GridOrder(
                order_id="",
                grid_id=grid_id,
                side=side,
                price=price,
                amount=amount,
                status=GridOrderStatus.PENDING,
                created_at=datetime.now()
            )
            reverse_orders.append(order)

        # 3. 批量下单（统一幣安對稱與上限控制）
        placed_orders = await self.engine.place_batch_orders(reverse_orders)
        
        # 4. 寫入本地狀態
        for po in placed_orders:
            self.state.add_order(po)

        self.logger.info(f"✨ 成功補掛了 {len(placed_orders)} 個反向訂單，重置期緩存立即成交訂單處理完畢！")

    async def _run_monitoring_loop(self):
        """後台監跨循環"""
        while not self._paused:
            try:
                # 1. 更新並監控倉位
                await self.position_monitor.check_positions()
                
                # 2. 定時檢查餘額與風控
                await self.balance_monitor.check_balance_risk()

                # 3. 止損監控
                await self.stop_loss_monitor.check_stop_loss()

                # 4. 定時健康自檢 (補單/對齊/防漏掛防漏成交)
                await self.health_self_check()

                await asyncio.sleep(self.config.monitoring_interval)
                
            except Exception as e:
                self.logger.error(f"後台監控循環異常: {str(e)}")
                # 風控策略：如果是嚴重API異常，觸發暫停
                await asyncio.sleep(5)

    async def health_self_check(self):
        """
        網格健康自檢
        1. 比對交易所 PENDING 訂單 與 本地狀態
        2. 防漏掛、補單（健康檢查補單）
        """
        if self._paused or self._resetting:
            return

        # 具體實現比對與補掛邏輯
        pass

    async def recover_old_orders(self):
        """恢復歷史網格狀態（重啟時調用）"""
        pass

    def pause(self, reason: str = ""):
        """暫停網格交易"""
        self._paused = True
        self._paused_reason = reason
        self.state.status = GridStatus.PAUSED
        self.logger.warning(f"⏸ 網格已暫停! 原因: {reason}")

    def resume(self):
        """恢復網格交易"""
        self._paused = False
        self._paused_reason = ""
        self.state.status = GridStatus.RUNNING
        self.logger.info("▶️ 網格交易已恢復運行")
