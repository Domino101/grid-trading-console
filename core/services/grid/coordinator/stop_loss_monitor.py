"""
止损保护监控模块

功能：监控价格向不利方向脱离网格，触发止损保护机制

止损逻辑：
1. 做多网格：价格跌破下边界 + 持续N秒 → 触发
2. 做空网格：价格涨破上边界 + 持续N秒 → 触发
3. 判断实时APR：
   - APR ≥ 阈值 → 市价平仓 → 取消订单 → 重置网格
   - APR < 阈值 → 市价平仓 → 取消订单 → 停止程序
4. 优先级：最高（覆盖所有其他模式）
"""

import asyncio
import time
from typing import Optional
from decimal import Decimal
from datetime import datetime

from ....logging import get_logger
from ..models import GridType


class StopLossMonitor:
    """
    止损保护监控器

    职责：
    1. 持续监控价格位置（是否脱离不利方向）
    2. 记录脱离开始时间
    3. 判断是否触发止损条件
    4. 执行止损操作（市价平仓 → 取消订单 → 重置/停止）
    """

    def __init__(self, engine, config, coordinator):
        """
        初始化止损监控器

        Args:
            engine: 执行引擎
            config: 网格配置
            coordinator: 协调器引用
        """
        self.logger = get_logger(__name__)
        self.engine = engine
        self.config = config
        self.coordinator = coordinator

        # 止损配置
        self._enabled = config.stop_loss_protection_enabled
        self._trigger_percent = config.stop_loss_trigger_percent
        self._escape_timeout = config.stop_loss_escape_timeout
        self._apr_threshold = config.stop_loss_apr_threshold

        # 状态跟踪
        self._adverse_escape_start_time: Optional[float] = None  # 不利方向脱离开始时间
        self._is_adverse_escaped = False  # 是否处于不利方向脱离状态
        self._stop_loss_triggered = False  # 是否已触发止损（防止重复触发）
        
        # 🔥 UI显示需要的实时数据
        self._current_price: Optional[Decimal] = None  # 当前价格
        self._trigger_price: Optional[Decimal] = None  # 触发价格
        
        # 监控任务
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        
        self.logger.info(
            f"🛑 止损保护监控器初始化: "
            f"启用={self._enabled}, "
            f"触发百分比={self._trigger_percent}%, "
            f"脱离超时={self._escape_timeout}秒, "
            f"APR阈值={self._apr_threshold}%"
        )

    async def start_monitoring(self):
        """启动止损监控"""
        if not self._enabled:
            self.logger.info("⏸️  止损保护未启用，跳过监控")
            return
        
        if self._running:
            self.logger.warning("⚠️ 止损监控已经在运行")
            return
        
        self._running = True
        self._stop_loss_triggered = False
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info("🛑 止损保护监控已启动")

    async def stop_monitoring(self):
        """停止止损监控"""
        if not self._running:
            return
        
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("🛑 止损保护监控已停止")

    async def _monitor_loop(self):
        """止损监控主循环"""
        try:
            while self._running:
                try:
                    await self._check_stop_loss_condition()
                    await asyncio.sleep(1)  # 每秒检查一次
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"❌ 止损监控异常: {e}")
                    import traceback
                    self.logger.error(traceback.format_exc())
                    await asyncio.sleep(5)  # 异常后等待5秒再继续
        except asyncio.CancelledError:
            self.logger.info("🛑 止损监控循环已取消")
        finally:
            self.logger.info("🛑 止损监控循环已退出")

    async def _check_stop_loss_condition(self):
        """检查止损触发条件"""
        # 如果已触发止损，不再重复检查
        if self._stop_loss_triggered:
            return
        
        # 获取当前价格
        current_price = self._get_current_price()
        if current_price is None:
            return
        
        # 🔥 更新当前价格（用于UI显示）
        self._current_price = current_price
        
        # 🔥 计算并更新触发价格（用于UI显示）
        self._calculate_trigger_price()
        
        # 检查是否向不利方向脱离网格
        is_adverse_escaped = self._is_price_adverse_escaped(current_price)
        
        # 状态变化：从正常 → 脱离
        if is_adverse_escaped and not self._is_adverse_escaped:
            self._is_adverse_escaped = True
            self._adverse_escape_start_time = time.time()
            self.logger.warning(
                f"⚠️ 价格向不利方向脱离网格: "
                f"当前价格=${current_price}, "
                f"开始计时..."
            )
        
        # 状态变化：从脱离 → 正常（价格回归）
        elif not is_adverse_escaped and self._is_adverse_escaped:
            elapsed = time.time() - self._adverse_escape_start_time if self._adverse_escape_start_time else 0
            self._is_adverse_escaped = False
            self._adverse_escape_start_time = None
            self.logger.info(
                f"✅ 价格已回归网格范围: "
                f"当前价格=${current_price}, "
                f"脱离持续时间={elapsed:.0f}秒 (未触发止损)"
            )
        
        # 持续脱离状态：检查是否超时
        elif is_adverse_escaped and self._is_adverse_escaped:
            if self._adverse_escape_start_time:
                elapsed = time.time() - self._adverse_escape_start_time
                remaining = self._escape_timeout - elapsed
                
                # 每30秒打印一次状态
                if int(elapsed) % 30 == 0:
                    self.logger.warning(
                        f"⏱️  价格持续脱离: "
                        f"当前价格=${current_price}, "
                        f"已持续={elapsed:.0f}秒, "
                        f"剩余{remaining:.0f}秒触发止损"
                    )
                
                # 超时：触发止损
                if elapsed >= self._escape_timeout:
                    await self._trigger_stop_loss(current_price)

    def _calculate_trigger_price(self):
        """计算并更新触发价格（用于UI显示）"""
        if self.config.lower_price is None or self.config.upper_price is None:
            self._trigger_price = None
            return
        
        # 计算网格总高度
        grid_range = self.config.upper_price - self.config.lower_price
        
        # 计算百分比对应的价格距离
        trigger_distance = grid_range * (self._trigger_percent / Decimal('100'))
        
        # 做多网格：从upper_price往下计算触发价格
        if self.config.grid_type in [GridType.LONG, GridType.FOLLOW_LONG, GridType.MARTINGALE_LONG]:
            self._trigger_price = self.config.upper_price - trigger_distance
        
        # 做空网格：从lower_price往上计算触发价格
        elif self.config.grid_type in [GridType.SHORT, GridType.FOLLOW_SHORT, GridType.MARTINGALE_SHORT]:
            self._trigger_price = self.config.lower_price + trigger_distance
        
        else:
            self._trigger_price = None

    def _is_price_adverse_escaped(self, current_price: Decimal) -> bool:
        """
        判断价格是否到达止损触发位置（基于百分比）

        Args:
            current_price: 当前价格

        Returns:
            True: 到达或超过触发位置, False: 未到达

        计算逻辑：
        - 做多网格：从upper_price往下，跌到网格总高度的X%位置时触发
          触发价格 = upper - (upper-lower) × (trigger_percent/100)
          当 current_price <= trigger_price 时触发
        
        - 做空网格：从lower_price往上，涨到网格总高度的X%位置时触发
          触发价格 = lower + (upper-lower) × (trigger_percent/100)
          当 current_price >= trigger_price 时触发
        
        示例：
        - 做多网格：lower=3000, upper=4000, trigger_percent=10%
          trigger_price = 4000 - 1000 × 0.1 = 3900
          价格跌到 <= 3900 时触发
        
        - trigger_percent=100% 时，等同于完全脱离网格范围（原逻辑）
        """
        if self.config.lower_price is None or self.config.upper_price is None:
            return False
        
        # 计算网格总高度
        grid_range = self.config.upper_price - self.config.lower_price
        
        # 计算百分比对应的价格距离
        trigger_distance = grid_range * (self._trigger_percent / Decimal('100'))
        
        # 做多网格：从upper_price往下计算触发价格
        if self.config.grid_type in [GridType.LONG, GridType.FOLLOW_LONG, GridType.MARTINGALE_LONG]:
            trigger_price = self.config.upper_price - trigger_distance
            is_triggered = current_price <= trigger_price
            
            # 首次触发时记录日志
            if is_triggered and not self._is_adverse_escaped:
                self.logger.warning(
                    f"🎯 止损触发位置: ${trigger_price:.2f} "
                    f"(从上边界${self.config.upper_price}往下{self._trigger_percent}%)"
                )
            
            return is_triggered
        
        # 做空网格：从lower_price往上计算触发价格
        elif self.config.grid_type in [GridType.SHORT, GridType.FOLLOW_SHORT, GridType.MARTINGALE_SHORT]:
            trigger_price = self.config.lower_price + trigger_distance
            is_triggered = current_price >= trigger_price
            
            # 首次触发时记录日志
            if is_triggered and not self._is_adverse_escaped:
                self.logger.warning(
                    f"🎯 止损触发位置: ${trigger_price:.2f} "
                    f"(从下边界${self.config.lower_price}往上{self._trigger_percent}%)"
                )
            
            return is_triggered
        
        return False

    def _get_current_price(self) -> Optional[Decimal]:
        """获取当前价格"""
        try:
            # 从协调器的统计数据中获取当前价格
            if hasattr(self.coordinator, 'state') and self.coordinator.state:
                return self.coordinator.state.current_price
            return None
        except Exception as e:
            self.logger.error(f"❌ 获取当前价格失败: {e}")
            return None

    async def _trigger_stop_loss(self, current_price: Decimal):
        """
        触发止损保护

        流程：
        1. 获取实时APR
        2. 判断APR阈值
        3. 市价平仓
        4. 取消所有订单
        5. 检查持仓和订单
        6. 根据APR执行重置或停止

        Args:
            current_price: 当前价格
        """
        self._stop_loss_triggered = True  # 标记已触发，防止重复
        
        try:
            self.logger.warning("=" * 80)
            self.logger.warning("🛑 止损保护触发！")
            self.logger.warning("=" * 80)
            self.logger.warning(
                f"触发原因: 价格向不利方向脱离网格且持续{self._escape_timeout}秒"
            )
            self.logger.warning(f"当前价格: ${current_price}")
            self.logger.warning(f"网格范围: [${self.config.lower_price}, ${self.config.upper_price}]")
            
            # 1. 获取实时APR（过去10分钟）
            realtime_apr = await self._get_realtime_apr()
            self.logger.warning(f"实时循环APR (过去10分钟): {realtime_apr:.2f}%")
            self.logger.warning(f"APR阈值: {self._apr_threshold}%")
            
            # 🔥 检查运行时间，提供详细说明
            if hasattr(self.coordinator, '_cycle_start_time') and self.coordinator._cycle_start_time:
                from datetime import datetime
                running_time = datetime.now() - self.coordinator._cycle_start_time
                running_minutes = running_time.total_seconds() / 60
                
                if running_minutes < 10:
                    self.logger.warning(
                        f"⚠️  注意: 程序运行时间不足10分钟 (当前 {running_minutes:.1f} 分钟)"
                    )
                    self.logger.warning(
                        f"   由于数据不足，实时APR = 0%"
                    )
                    self.logger.warning(
                        f"   建议: 如果不希望过早停止，请调高 stop_loss_escape_timeout 参数"
                    )
            
            # 2. 判断执行动作
            should_reset = realtime_apr >= self._apr_threshold
            
            # 🔥 详细的决策说明
            self.logger.warning("📊 决策判断:")
            self.logger.warning(f"   实时APR ({realtime_apr:.2f}%) {'≥' if should_reset else '<'} 阈值 ({self._apr_threshold}%)")
            
            if should_reset:
                self.logger.warning(f"   ✅ APR达标 → 执行动作: 🔄 重置网格")
                self.logger.warning(f"   说明: 虽然触发止损，但APR表现良好，重置网格继续运行")
            else:
                if realtime_apr == 0:
                    self.logger.warning(f"   ⚠️  APR为0 → 执行动作: ⛔ 停止程序")
                    self.logger.warning(f"   原因: 运行时间不足或无循环数据，无法评估策略有效性")
                else:
                    self.logger.warning(f"   ⚠️  APR不达标 → 执行动作: ⛔ 停止程序")
                    self.logger.warning(f"   原因: 实时APR低于阈值，策略表现不佳，停止以避免继续亏损")
            
            self.logger.warning("=" * 80)
            
            # 3. 市价平仓
            self.logger.warning("📊 步骤1: 市价平仓...")
            await self._close_all_positions()
            
            # 4. 取消所有订单
            self.logger.warning("📊 步骤2: 取消所有订单...")
            await self._cancel_all_orders()
            
            # 🔥 等待3秒，确保交易所完成处理（防止延迟导致误判）
            self.logger.info("⏱️  等待3秒，确保交易所响应...")
            await asyncio.sleep(3)
            
            # 5. 检查持仓和订单，清理残留（最多重试3次）
            self.logger.warning("📊 步骤3: 检查并清理残留...")
            await self._verify_and_cleanup_residual(max_retries=3)
            
            # 6. 执行重置或停止
            if should_reset:
                self.logger.warning("=" * 80)
                self.logger.warning("🔄 执行网格重置...")
                self.logger.warning(f"   原因: 实时APR ({realtime_apr:.2f}%) ≥ 阈值 ({self._apr_threshold}%)")
                self.logger.warning(f"   结果: 持仓和订单已清理完毕，将重新初始化网格")
                self.logger.warning("=" * 80)
                await self._reset_grid(current_price)
            else:
                self.logger.warning("=" * 80)
                self.logger.warning("⛔ 执行程序停止...")
                if realtime_apr == 0:
                    self.logger.warning(f"   原因: 实时APR为0（运行时间不足或无循环数据）")
                else:
                    self.logger.warning(f"   原因: 实时APR ({realtime_apr:.2f}%) < 阈值 ({self._apr_threshold}%)")
                self.logger.warning(f"   结果: 持仓和订单已清理完毕，程序即将退出")
                self.logger.warning("=" * 80)
                await self._stop_program()
            
        except Exception as e:
            self.logger.error(f"❌ 止损保护执行失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # 失败也要停止程序，避免继续亏损
            await self._stop_program()

    async def _get_realtime_apr(self) -> Decimal:
        """获取实时循环APR（过去10分钟）"""
        try:
            # 🔥 修复：从GridStatistics对象获取APR，而不是GridState
            # GridState是网格状态，GridStatistics是统计数据
            if hasattr(self.coordinator, 'get_statistics'):
                stats = await self.coordinator.get_statistics()
                apr = stats.realtime_cycle_apr_estimate or Decimal('0')
                
                # 🔥 记录详细的APR数据（用于调试）
                if stats.realtime_apr_formula_data:
                    formula_data = stats.realtime_apr_formula_data
                    recent_cycles = formula_data.get('recent_cycles', 0)
                    time_window = formula_data.get('time_window', 10)
                    self.logger.info(
                        f"📈 实时APR详情: {apr:.2f}%, "
                        f"近{time_window}分钟完成{recent_cycles}次循环"
                    )
                
                return apr
            
            self.logger.warning("⚠️ 无法获取coordinator统计数据，实时APR默认为0")
            return Decimal('0')
        except Exception as e:
            self.logger.error(f"❌ 获取实时APR失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return Decimal('0')

    async def _close_all_positions(self):
        """市价平仓所有持仓"""
        try:
            # 查询当前持仓（注意：get_positions 需要传入列表）
            positions = await self.engine.exchange.get_positions([self.config.symbol])
            
            if not positions or len(positions) == 0:
                self.logger.info("✅ 无持仓，跳过平仓")
                return
            
            for position in positions:
                if position.size == 0:
                    continue
                
                self.logger.warning(
                    f"🔻 平仓: {position.symbol}, "
                    f"数量={position.size}, "
                    f"方向={'多' if position.size > 0 else '空'}"
                )
                
                # 市价平仓
                from core.adapters.exchanges.models import OrderSide
                side = OrderSide.SELL if position.size > 0 else OrderSide.BUY
                quantity = abs(position.size)
                
                await self.engine.exchange.place_market_order(
                    symbol=position.symbol,
                    side=side,
                    quantity=quantity,
                    reduce_only=True  # 🔥 只减仓，不开新仓
                )
            
            self.logger.info("✅ 所有持仓已平仓（等待交易所确认...）")
            
        except Exception as e:
            self.logger.error(f"❌ 平仓失败: {e}")
            raise

    async def _cancel_all_orders(self):
        """取消所有挂单"""
        try:
            # 🛑 熔斷守衛：BAN 期間略過 get_open_orders 查詢（仍嘗試 cancel_all_orders 以保護倉位）
            if hasattr(self.engine, '_is_circuit_open') and self.engine._is_circuit_open():
                self.logger.warning(
                    f"⛔ 取消挂单：API 熔斷中 "
                    f"(code={getattr(self.engine, '_last_api_error_code', None)})，"
                    f"略過 get_open_orders 直接調用 cancel_all_orders"
                )
                await self.engine.exchange.cancel_all_orders(self.config.symbol)
                self.logger.info("✅ 已嘗試 cancel_all_orders（BAN 中可能失敗）")
                return

            orders = await self.engine.exchange.get_open_orders(self.config.symbol)

            if not orders or len(orders) == 0:
                self.logger.info("✅ 无挂单，跳过取消")
                return

            self.logger.warning(f"🚫 取消{len(orders)}个挂单...")
            await self.engine.exchange.cancel_all_orders(self.config.symbol)
            
            self.logger.info("✅ 所有订单已取消（等待交易所确认...）")
            
        except Exception as e:
            self.logger.error(f"❌ 取消订单失败: {e}")
            raise

    async def _verify_and_cleanup_residual(self, max_retries: int = 3):
        """
        验证持仓和订单已清理，如有残留则继续清理
        
        Args:
            max_retries: 最大重试次数（默认3次）
        """
        for retry in range(max_retries):
            try:
                # 🛑 熔斷守衛：BAN 期間無法查詢，跳過驗證並提前中止重試循環
                if hasattr(self.engine, '_is_circuit_open') and self.engine._is_circuit_open():
                    self.logger.error(
                        f"⛔ 殘留驗證：API 熔斷中 "
                        f"(code={getattr(self.engine, '_last_api_error_code', None)})，"
                        f"無法執行驗證查詢，提前中止重試"
                    )
                    return
                # 重新查询当前持仓和订单（注意：get_positions 需要传入列表）
                positions = await self.engine.exchange.get_positions([self.config.symbol])
                orders = await self.engine.exchange.get_open_orders(self.config.symbol)
                
                # 统计残留数量
                residual_positions = [p for p in positions if p.size != 0] if positions else []
                residual_orders = orders if orders else []
                
                position_count = len(residual_positions)
                order_count = len(residual_orders)
                
                # 如果都清理干净，验证通过
                if position_count == 0 and order_count == 0:
                    if retry > 0:
                        self.logger.info(f"✅ 验证通过: 持仓和订单已清空（第{retry + 1}次检查）")
                    else:
                        self.logger.info("✅ 验证通过: 持仓和订单已清空")
                    return
                
                # 发现残留，记录详情
                self.logger.warning(
                    f"⚠️ 发现残留（第{retry + 1}次检查）: "
                    f"持仓={position_count}个, 订单={order_count}个"
                )
                
                # 如果是最后一次重试，抛出异常
                if retry >= max_retries - 1:
                    self.logger.error(
                        f"❌ 清理失败: 重试{max_retries}次后仍有残留 "
                        f"(持仓={position_count}个, 订单={order_count}个)"
                    )
                    raise Exception(f"持仓或订单未完全清理（重试{max_retries}次失败）")
                
                # 继续清理残留的持仓
                if position_count > 0:
                    self.logger.warning(f"🔻 清理残留持仓: {position_count}个")
                    for position in residual_positions:
                        self.logger.warning(
                            f"   - 平仓: {position.symbol}, "
                            f"数量={position.size}, "
                            f"方向={'多' if position.size > 0 else '空'}"
                        )
                        
                        # 针对残留持仓下市价平仓单
                        from core.adapters.exchanges.models import OrderSide
                        side = OrderSide.SELL if position.size > 0 else OrderSide.BUY
                        quantity = abs(position.size)
                        
                        try:
                            await self.engine.exchange.place_market_order(
                                symbol=position.symbol,
                                side=side,
                                quantity=quantity,
                                reduce_only=True  # 🔥 只减仓，不开新仓
                            )
                        except Exception as e:
                            self.logger.error(f"   ❌ 平仓失败: {e}")
                
                # 继续清理残留的订单
                if order_count > 0:
                    self.logger.warning(f"🚫 清理残留订单: {order_count}个")
                    for order in residual_orders:
                        self.logger.warning(
                            f"   - 取消订单: {order.id[:10]}..., "
                            f"{order.side.value} {order.amount}@{order.price}"
                        )
                    
                    try:
                        # 批量取消所有残留订单
                        await self.engine.exchange.cancel_all_orders(self.config.symbol)
                    except Exception as e:
                        self.logger.error(f"   ❌ 取消订单失败: {e}")
                
                # 🔥 等待3秒，确保交易所完成处理（防止延迟导致误判）
                if position_count > 0 or order_count > 0:
                    self.logger.info("⏱️  等待3秒，确保交易所响应...")
                    await asyncio.sleep(3)
                
                # 继续下一次检查
                self.logger.info(f"⏭️  继续第{retry + 2}次检查...")
                
            except Exception as e:
                self.logger.error(f"❌ 验证和清理失败: {e}")
                raise

    async def _reset_grid(self, current_price: Decimal):
        """
        重置网格

        Args:
            current_price: 当前价格（用于重新计算价格区间）
        """
        try:
            self.logger.warning("🔄 开始重置网格...")
            
            # 调用协调器的网格重置方法
            if hasattr(self.coordinator, 'reset_grid_manager'):
                # 重置网格（会重新计算价格区间、清空统计等）
                await self.coordinator.reset_grid_manager.reset_grid(
                    reason="止损保护触发-重置",
                    current_price=current_price
                )
                self.logger.warning("✅ 网格重置完成")
            else:
                self.logger.error("❌ 协调器无重置管理器，无法重置网格")
                raise Exception("无法重置网格")
            
            # 重置止损状态，允许下次触发
            self._stop_loss_triggered = False
            self._is_adverse_escaped = False
            self._adverse_escape_start_time = None
            
        except Exception as e:
            self.logger.error(f"❌ 网格重置失败: {e}")
            # 重置失败，停止程序
            await self._stop_program()

    async def _stop_program(self):
        """停止程序"""
        try:
            self.logger.warning("⛔ 止损保护: 停止网格程序")
            
            # 停止所有监控任务
            if hasattr(self.coordinator, 'stop'):
                await self.coordinator.stop()
            
            # 设置全局停止标志
            import sys
            sys.exit(0)
            
        except Exception as e:
            self.logger.error(f"❌ 停止程序失败: {e}")
            import sys
            sys.exit(1)

    def get_status(self) -> dict:
        """获取止损监控状态（用于UI显示）"""
        if not self._enabled:
            return {
                "enabled": False,
                "is_escaped": False,
                "elapsed_seconds": 0,
                "remaining_seconds": 0,
                "timeout": self._escape_timeout,
                "trigger_percent": float(self._trigger_percent),
                "apr_threshold": float(self._apr_threshold),
                "current_price": None,
                "trigger_price": None,
                "triggered": False
            }
        
        elapsed = 0
        if self._is_adverse_escaped and self._adverse_escape_start_time:
            elapsed = int(time.time() - self._adverse_escape_start_time)
        
        remaining = max(0, self._escape_timeout - elapsed)
        
        return {
            "enabled": True,
            "is_escaped": self._is_adverse_escaped,
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining,
            "timeout": self._escape_timeout,
            "trigger_percent": float(self._trigger_percent),
            "apr_threshold": float(self._apr_threshold),
            "current_price": float(self._current_price) if self._current_price else None,
            "trigger_price": float(self._trigger_price) if self._trigger_price else None,
            "triggered": self._stop_loss_triggered
        }

