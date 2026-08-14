"""
刷量交易服务实现

核心功能：
1. 监控订单簿价格稳定
2. 同时挂买1和卖1订单
3. 其中一个成交后立即取消另一个
4. 市价平仓持仓
5. 循环执行
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ....adapters.exchanges.interface import ExchangeInterface
from ....adapters.exchanges.models import OrderSide, OrderType, OrderData, OrderBookData, PositionSide, OrderStatus
from ....logging import get_logger

from ..interfaces.volume_maker_service import IVolumeMakerService
from ..models.volume_maker_config import VolumeMakerConfig
from ..models.volume_maker_statistics import (
    VolumeMakerStatistics,
    CycleResult,
    CycleStatus
)
from ..hourly_statistics import HourlyStatisticsTracker


class VolumeMakerServiceImpl(IVolumeMakerService):
    """刷量交易服务实现"""

    def __init__(self, exchange_adapter: ExchangeInterface):
        """
        初始化刷量服务

        Args:
            exchange_adapter: 交易所适配器
        """
        self.adapter = exchange_adapter
        self.config: Optional[VolumeMakerConfig] = None
        self.statistics = VolumeMakerStatistics()

        # 运行状态
        self._running = False
        self._paused = False
        self._should_stop = False

        # 当前轮次状态
        self._current_buy_order: Optional[OrderData] = None
        self._current_sell_order: Optional[OrderData] = None
        self._current_position = Decimal("0")

        # 日志
        self.logger: Optional[logging.Logger] = None

        # 任务
        self._main_task: Optional[asyncio.Task] = None

        # 🔥 WebSocket 订单追踪（用于获取成交价格）
        # order_id -> {price, amount, timestamp, side}
        self._order_fills: Dict[str, Dict[str, Any]] = {}
        # order_id -> Event（等待成交通知）
        self._order_fill_events: Dict[str, asyncio.Event] = {}
        self._ws_order_subscribed = False  # WebSocket订单是否已订阅
        self._ws_order_healthy = False  # WebSocket订单连接是否健康
        # 最后收到消息的时间
        self._ws_order_last_message_time: Optional[datetime] = None

        # 🔥 WebSocket 订单簿缓存（用于价格稳定检测）
        self._latest_orderbook: Optional['OrderBookData'] = None  # 最新订单簿数据
        self._ws_orderbook_subscribed = False  # WebSocket订单簿是否已订阅
        self._ws_orderbook_healthy = False  # WebSocket订单簿连接是否健康
        # 最后收到消息的时间
        self._ws_orderbook_last_message_time: Optional[datetime] = None

        # 🔥 WebSocket 重连任务
        self._ws_reconnect_task: Optional[asyncio.Task] = None
        self._ws_reconnect_interval = 10.0  # 重连间隔（秒）

        # 📊 小时级统计跟踪器
        self._hourly_tracker: Optional[HourlyStatisticsTracker] = None

    async def initialize(self, config: VolumeMakerConfig) -> bool:
        """初始化刷量服务"""
        try:
            self.config = config

            # 初始化日志
            self._setup_logging()

            # 初始化小时级统计跟踪器
            self._hourly_tracker = HourlyStatisticsTracker()

            # 连接交易所
            if not self.adapter.is_connected():
                await self.adapter.connect()

            # 认证
            if not self.adapter.is_authenticated():
                await self.adapter.authenticate()

            self.logger.info(
                f"✅ 刷量服务初始化成功 - {config.exchange}/{config.symbol}")
            return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"❌ 初始化失败: {e}")
            return False

    def _setup_logging(self) -> None:
        """设置日志"""
        if not self.config or not self.config.logging.enabled:
            return

        # 创建日志目录
        log_dir = Path(self.config.logging.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # 创建logger
        logger_name = f"volume_maker.{self.config.exchange}.{self.config.symbol}"
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(getattr(logging, self.config.logging.log_level))

        # 清除现有handlers
        self.logger.handlers.clear()

        # 文件handler（带轮转）
        if self.config.logging.log_to_file:
            log_file = log_dir / self.config.logging.log_file
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=self.config.logging.max_bytes,
                backupCount=self.config.logging.backup_count,
                encoding=self.config.logging.encoding
            )
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

        # 控制台handler（如果启用）
        if self.config.logging.log_to_console:
            console_handler = logging.StreamHandler()
            console_formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            )
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

        # 🔥 为 WebSocket 创建独立的日志配置
        # 创建 WebSocket 专用 logger
        ws_logger = logging.getLogger(
            f'websocket.{self.config.exchange}.{self.config.symbol}')
        ws_logger.setLevel(getattr(logging, self.config.logging.log_level))
        ws_logger.handlers.clear()

        # WebSocket 专用文件handler
        if self.config.logging.log_to_file:
            ws_log_file = log_dir / f"websocket_{self.config.exchange}.log"
            ws_file_handler = RotatingFileHandler(
                ws_log_file,
                maxBytes=self.config.logging.max_bytes,
                backupCount=self.config.logging.backup_count,
                encoding=self.config.logging.encoding
            )
            ws_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            ws_file_handler.setFormatter(ws_formatter)
            ws_logger.addHandler(ws_file_handler)

            # 如果配置了控制台输出，也为 WebSocket logger 添加
            if self.config.logging.log_to_console:
                ws_console_handler = logging.StreamHandler()
                ws_console_handler.setFormatter(ws_formatter)
                ws_logger.addHandler(ws_console_handler)

            # 🔥 将 WebSocket logger 赋值给适配器及其所有子模块
            # 1. 适配器本身
            if hasattr(self.adapter, 'logger'):
                self.adapter.logger = ws_logger

            # 2. WebSocket 子模块（包含所有 WebSocket 连接、订阅、订单成交等）
            if hasattr(self.adapter, '_websocket') and hasattr(self.adapter._websocket, 'logger'):
                self.adapter._websocket.logger = ws_logger

            # 3. REST 子模块（也可能有 WebSocket 相关的操作，如订单成交查询）
            if hasattr(self.adapter, '_rest') and hasattr(self.adapter._rest, 'logger'):
                self.adapter._rest.logger = ws_logger

            self.logger.info(f"✅ WebSocket 日志已配置到独立文件: {ws_log_file}")

    async def start(self) -> None:
        """启动刷量交易"""
        if self._running:
            self.logger.warning("⚠️ 服务已在运行中")
            return

        self._running = True
        self._should_stop = False
        self.statistics.is_running = True
        self.statistics.start_time = datetime.now()

        self.logger.info("🚀 刷量服务启动")

        # 🔥 如果配置为WebSocket模式，订阅订单更新
        if self.config.fill_price_method == "websocket":
            await self._subscribe_order_updates()

        # 🔥 如果配置为WebSocket模式，订阅订单簿
        if self.config.orderbook_method == "websocket":
            await self._subscribe_orderbook()

        # 🔥 启动WebSocket重连任务（始终启动，内部会根据配置判断）
        self.logger.info("🔄 正在启动 WebSocket 重连监控任务...")
        self._ws_reconnect_task = asyncio.create_task(
            self._ws_reconnect_loop())

        # 📊 启动小时级统计跟踪器
        if self._hourly_tracker:
            self._hourly_tracker.start()

        # 启动主循环
        self._main_task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        """停止刷量交易"""
        self.logger.info("🛑 停止刷量服务...")

        self._should_stop = True
        self._running = False
        self.statistics.is_running = False

        # 取消主任务
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        # 🔥 取消重连任务
        if self._ws_reconnect_task and not self._ws_reconnect_task.done():
            self._ws_reconnect_task.cancel()
            try:
                await self._ws_reconnect_task
            except asyncio.CancelledError:
                pass

        # 📊 停止小时级统计跟踪器
        if self._hourly_tracker:
            await self._hourly_tracker.stop()

        # 清理当前订单和持仓
        await self._cleanup_current_cycle()

        self.logger.info("✅ 刷量服务已停止")

    async def pause(self) -> None:
        """暂停刷量交易"""
        if not self._running:
            return

        self._paused = True
        self.statistics.is_paused = True
        self.logger.info("⏸️ 刷量服务已暂停")

    async def resume(self) -> None:
        """恢复刷量交易"""
        if not self._paused:
            return

        self._paused = False
        self.statistics.is_paused = False
        self.logger.info("▶️ 刷量服务已恢复")

    def get_statistics(self) -> VolumeMakerStatistics:
        """获取统计数据"""
        # 更新运行时间
        if self._running:
            self.statistics.running_time = datetime.now() - self.statistics.start_time
        return self.statistics

    def is_running(self) -> bool:
        """检查是否正在运行"""
        return self._running

    def is_paused(self) -> bool:
        """检查是否已暂停"""
        return self._paused

    async def emergency_stop(self) -> None:
        """紧急停止"""
        self.logger.warning("🚨 执行紧急停止！")

        # 取消所有订单
        try:
            await self.adapter.cancel_all_orders(self.config.symbol)
        except Exception as e:
            self.logger.error(f"取消订单失败: {e}")

        # 平仓所有持仓
        try:
            positions = await self.adapter.get_positions([self.config.symbol])
            for pos in positions:
                if abs(pos.size) > 0:
                    # 市价平仓
                    side = OrderSide.SELL if pos.size > 0 else OrderSide.BUY
                    await self.adapter.create_order(
                        symbol=self.config.symbol,
                        side=side,
                        order_type=OrderType.MARKET,
                        amount=abs(pos.size)
                    )
        except Exception as e:
            self.logger.error(f"平仓失败: {e}")

        # 停止服务
        await self.stop()

    def get_status_text(self) -> str:
        """获取状态文本"""
        if not self._running:
            return "已停止"
        elif self._paused:
            return "已暂停"
        else:
            return "运行中"

    # ========== 核心交易逻辑 ==========

    async def _main_loop(self) -> None:
        """主循环"""
        try:
            while self._running and not self._should_stop:
                # 检查是否暂停
                if self._paused:
                    await asyncio.sleep(1)
                    continue

                # 检查是否达到最大轮次
                if self.statistics.total_cycles >= self.config.max_cycles:
                    self.logger.info(f"✅ 达到最大轮次 {self.config.max_cycles}，停止交易")
                    break

                # 检查连续失败次数
                if self.statistics.consecutive_fails >= self.config.max_consecutive_fails:
                    # 如果配置了等待时间，则等待后继续；否则停止
                    if self.config.consecutive_fail_wait_minutes > 0:
                        wait_seconds = self.config.consecutive_fail_wait_minutes * 60
                        self.logger.warning(
                            f"⚠️ 连续失败 {self.config.max_consecutive_fails} 次，"
                            f"等待 {self.config.consecutive_fail_wait_minutes} 分钟后继续..."
                        )

                        # 分段等待，以便能够响应停止信号
                        wait_start = datetime.now()
                        while (datetime.now() - wait_start).total_seconds() < wait_seconds:
                            if self._should_stop:
                                self.logger.info("⚠️ 等待期间收到停止信号")
                                break
                            # 每5秒检查一次停止信号
                            await asyncio.sleep(5.0)

                        # 如果没有收到停止信号，重置失败计数并继续
                        if not self._should_stop:
                            self.logger.info(
                                f"✅ 等待完成，重置连续失败计数器，继续交易"
                            )
                            self.statistics.consecutive_fails = 0
                    else:
                        # 配置为0，直接停止（保持原有行为）
                        self.logger.error(
                            f"❌ 连续失败 {self.config.max_consecutive_fails} 次，停止交易"
                        )
                        break

                # 执行一轮交易
                try:
                    await self._execute_cycle()
                except Exception as e:
                    self.logger.error(f"❌ 执行轮次出错: {e}", exc_info=True)
                    # 清理
                    await self._cleanup_current_cycle()
                    # 等待一段时间再继续
                    await asyncio.sleep(5)

                # 轮次间隔
                if self.config.cycle_interval > 0:
                    await asyncio.sleep(self.config.cycle_interval)

        except asyncio.CancelledError:
            self.logger.info("主循环被取消")
        except Exception as e:
            self.logger.error(f"主循环异常: {e}", exc_info=True)
        finally:
            self._running = False
            self.statistics.is_running = False

    async def _execute_cycle(self) -> None:
        """执行一轮交易（根据配置选择限价或市价模式）"""
        # 🔥 余额检查（风控）
        if self.config.min_balance is not None:
            if not await self._check_balance():
                self.logger.error("❌ 余额不足，停止交易")
                self._running = False
                return

        # 🔥 根据配置选择执行模式
        if self.config.order_mode == 'market':
            await self._execute_market_cycle()
        else:
            await self._execute_limit_cycle()

    async def _execute_limit_cycle(self) -> None:
        """执行一轮交易（限价开仓模式）"""
        cycle_id = self.statistics.total_cycles + 1
        start_time = datetime.now()

        self.logger.info(f"━━━━━━ 开始第 {cycle_id} 轮（限价模式）━━━━━━")

        result = CycleResult(
            cycle_id=cycle_id,
            status=CycleStatus.FAILED,
            start_time=start_time,
            end_time=start_time,
            duration=timedelta(seconds=0),
            bid_price=Decimal("0"),
            ask_price=Decimal("0"),
            spread=Decimal("0")
        )

        try:
            # 步骤1: 等待价格稳定
            self.logger.info("📊 等待价格稳定...")
            stable_prices = await self._wait_for_stable_price()
            if not stable_prices:
                result.status = CycleStatus.TIMEOUT
                result.error_message = "价格稳定检测超时"
                return

            bid_price, ask_price, quantity_ratio = stable_prices
            result.bid_price = bid_price
            result.ask_price = ask_price
            result.spread = ask_price - bid_price
            result.quantity_ratio = quantity_ratio  # 保存比例值

            self.logger.info(
                f"✅ 价格稳定 - 买1: {bid_price}, 卖1: {ask_price}, 价差: {result.spread}")

            # 步骤2: 同时挂买单和卖单
            self.logger.info("📝 挂买单和卖单...")
            success = await self._place_both_orders(bid_price, ask_price)
            if not success:
                result.status = CycleStatus.FAILED
                result.error_message = "下单失败"
                return

            # 步骤3: 等待其中一个成交
            self.logger.info("⏳ 等待订单成交...")
            filled_order = await self._wait_for_fill()
            if not filled_order:
                result.status = CycleStatus.TIMEOUT
                result.error_message = "订单超时未成交"
                # 取消两个订单
                await self._cancel_both_orders()
                return

            result.filled_side = 'buy' if filled_order.side == OrderSide.BUY else 'sell'
            result.filled_price = filled_order.average or filled_order.price
            result.filled_amount = filled_order.filled

            # 🔥 如果成交数量为0或None（WebSocket没有数据），使用配置的订单数量
            if not result.filled_amount or result.filled_amount == Decimal("0"):
                result.filled_amount = self.config.order_size
                self.logger.warning(
                    f"⚠️ 未获取到成交数量，使用配置值: {result.filled_amount}")

            # 如果成交价格为None，使用订单价格
            if not result.filled_price:
                result.filled_price = bid_price if filled_order.side == OrderSide.BUY else ask_price
                self.logger.warning(
                    f"⚠️ 未获取到成交价格，使用订单价: {result.filled_price}")

            if result.filled_side == 'buy':
                result.buy_order_id = filled_order.id
            else:
                result.sell_order_id = filled_order.id

            self.logger.info(
                f"✅ {result.filled_side.upper()}单成交 - 价格: {result.filled_price}, 数量: {result.filled_amount}")

            # 步骤4: 取消未成交的订单
            self.logger.info("❌ 取消未成交订单...")
            await self._cancel_unfilled_order(filled_order)

            # 步骤5: 市价平仓
            self.logger.info("💰 市价平仓...")
            close_result = await self._close_position()
            if close_result:
                result.close_price, result.close_amount = close_result

                # 计算盈亏
                if result.filled_side == 'buy':
                    result.pnl = (result.close_price -
                                  result.filled_price) * result.filled_amount
                else:
                    result.pnl = (result.filled_price -
                                  result.close_price) * result.filled_amount

                self.logger.info(
                    f"✅ 平仓完成 - 价格: {result.close_price}, 盈亏: {result.pnl}")

            result.status = CycleStatus.SUCCESS

        except Exception as e:
            result.status = CycleStatus.FAILED
            result.error_message = str(e)
            self.logger.error(f"❌ 轮次执行失败: {e}", exc_info=True)

        finally:
            # 清理
            await self._cleanup_current_cycle()

            # 更新结果
            result.end_time = datetime.now()
            result.duration = result.end_time - result.start_time

            # 更新统计
            self.statistics.update_from_cycle(result)

            # 📊 更新小时级统计
            if self._hourly_tracker:
                self._hourly_tracker.add_cycle(result)

            self.logger.info(
                f"━━━━━━ 第 {cycle_id} 轮结束 - {result.status.value} ━━━━━━\n")

    async def _execute_market_cycle(self) -> None:
        """执行一轮交易（市价开仓模式）"""
        cycle_id = self.statistics.total_cycles + 1
        start_time = datetime.now()

        self.logger.info(f"━━━━━━ 开始第 {cycle_id} 轮（市价模式）━━━━━━")

        result = CycleResult(
            cycle_id=cycle_id,
            status=CycleStatus.FAILED,
            start_time=start_time,
            end_time=start_time,
            duration=timedelta(seconds=0),
            bid_price=Decimal("0"),
            ask_price=Decimal("0"),
            spread=Decimal("0")
        )

        try:
            # 步骤1: 等待价格稳定
            self.logger.info("📊 等待价格稳定...")
            stable_prices = await self._wait_for_stable_price()
            if not stable_prices:
                result.status = CycleStatus.TIMEOUT
                result.error_message = "价格稳定检测超时"
                return

            bid_price, ask_price, quantity_ratio = stable_prices
            result.bid_price = bid_price
            result.ask_price = ask_price
            result.spread = ask_price - bid_price
            result.quantity_ratio = quantity_ratio  # 保存比例值

            self.logger.info(
                f"✅ 价格稳定 - 买1: {bid_price}, 卖1: {ask_price}, 价差: {result.spread}")

            # 步骤2: 获取订单簿，分析买卖量
            self.logger.info(
                f"📊 分析订单簿买卖量({self.config.orderbook_method.upper()})...")

            # 🔥 根据配置选择获取方式
            if self.config.orderbook_method == "websocket" and self._latest_orderbook is not None:
                # WebSocket 模式：使用缓存的订单簿
                orderbook = self._latest_orderbook
            else:
                # REST API 模式 或 WebSocket 未就绪：实时获取订单簿
                orderbook = await self.adapter.get_orderbook(self.config.symbol)

            if not orderbook.bids or not orderbook.asks:
                result.status = CycleStatus.FAILED
                result.error_message = "订单簿为空"
                return

            # 获取买1和卖1的数量
            # 🔥 OrderBookLevel的属性是size，不是amount
            bid_amount = orderbook.bids[0].size
            ask_amount = orderbook.asks[0].size

            # 🔥 关键逻辑：选择数量少的一方进行开仓
            # 如果卖1数量少，就开多（吃卖1）
            # 如果买1数量少，就开空（吃买1）
            if ask_amount < bid_amount:
                open_side = OrderSide.BUY  # 开多
                self.logger.info(
                    f"✅ 卖1数量({ask_amount})少于买1数量({bid_amount})，选择开多（吃卖1）")
            else:
                open_side = OrderSide.SELL  # 开空
                self.logger.info(
                    f"✅ 买1数量({bid_amount})少于等于卖1数量({ask_amount})，选择开空（吃买1）")

            # 步骤3: 快速开平仓 - 连续提交两个反向订单
            # 🚀 新逻辑：不等待开仓确认，直接提交反向订单
            self.logger.info(
                f"🚀 快速开平仓 - 开仓方向: {'买入' if open_side == OrderSide.BUY else '卖出'}")

            # 🔥 提交开仓订单（不等待）
            self.logger.info(f"📝 提交开仓订单...")
            open_order = await self.adapter.create_order(
                symbol=self.config.symbol,
                side=open_side,
                order_type=OrderType.MARKET,
                amount=self.config.order_size,
                params=None
            )
            self.logger.info(f"✅ 开仓订单已提交: {open_order.id}")

            # 🔧 决定等待策略（三选一）
            if self.config.market_wait_price_change:
                # 策略1: 等待价格变化（优先级最高）
                self.logger.info("⏳ 启用价格变化触发模式，等待订单簿价格变化...")
                # 🔥 传入买卖单数量，用于反转检测
                _, wait_time, close_reason = await self._wait_for_price_change(
                    bid_price, ask_price,
                    initial_bid_amount=bid_amount,
                    initial_ask_amount=ask_amount,
                    timeout=self.config.market_wait_timeout)
                result.wait_time = wait_time
                result.close_reason = close_reason  # 保存平仓原因
            elif self.config.market_order_interval_ms > 0:
                # 策略2: 固定时间间隔
                interval_seconds = self.config.market_order_interval_ms / 1000.0
                self.logger.info(
                    f"⏱️  等待 {self.config.market_order_interval_ms}ms 后提交平仓订单...")
                await asyncio.sleep(interval_seconds)
                result.close_reason = "interval"  # 固定间隔
            else:
                # 策略3: 立即提交（无等待）
                result.close_reason = "immediate"  # 立即平仓

            # 🔥 提交反向订单（平仓）
            close_side = OrderSide.SELL if open_side == OrderSide.BUY else OrderSide.BUY
            if self.config.market_wait_price_change:
                self.logger.info(f"📝 价格已变化，提交平仓订单...")
            elif self.config.market_order_interval_ms == 0:
                self.logger.info(f"📝 立即提交平仓订单...")
            else:
                self.logger.info(f"📝 提交平仓订单...")
            close_order = await self.adapter.create_order(
                symbol=self.config.symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                amount=self.config.order_size,
                params=None
            )
            self.logger.info(f"✅ 平仓订单已提交: {close_order.id}")

            # 步骤4: 检查持仓状态（两次提交完成后）
            self.logger.info("🔍 检查持仓状态...")
            await asyncio.sleep(0.5)  # 短暂等待让订单执行

            try:
                positions = await self.adapter.get_positions([self.config.symbol])
                final_position_size = Decimal("0")

                if positions and abs(positions[0].size) >= Decimal("0.00001"):
                    final_position_size = positions[0].size
                    self.logger.warning(
                        f"⚠️ 发现剩余持仓: {final_position_size}，某次订单可能失败")

                    # 步骤5: 纠正 - 如果还有持仓，再次平仓
                    self.logger.info("🔧 纠正持仓 - 再次平仓...")
                    correction_result = await self._close_position()

                    if correction_result:
                        self.logger.info("✅ 持仓纠正成功")
                    else:
                        self.logger.error("❌ 持仓纠正失败")
                        result.status = CycleStatus.FAILED
                        result.error_message = "持仓纠正失败"
                        return
                else:
                    self.logger.info("✅ 持仓已清零，开平仓成功")

            except Exception as e:
                self.logger.error(f"❌ 检查持仓失败: {e}")

            # 步骤6: 获取成交信息（用于统计）
            open_price, open_amount = await self._get_fill_info(open_order.id, open_side)

            # 如果没有查到成交记录，使用估算值
            if open_price is None:
                orderbook = await self.adapter.get_orderbook(self.config.symbol)
                if open_side == OrderSide.BUY and orderbook.asks:
                    open_price = orderbook.asks[0].price
                elif open_side == OrderSide.SELL and orderbook.bids:
                    open_price = orderbook.bids[0].price
                else:
                    open_price = bid_price if open_side == OrderSide.BUY else ask_price
                self.logger.warning(
                    f"⚠️ 未找到开仓成交记录，使用市场价估算: {open_price}")

            result.filled_side = 'buy' if open_side == OrderSide.BUY else 'sell'
            result.filled_price = open_price
            result.filled_amount = self.config.order_size  # 使用订单数量

            if result.filled_side == 'buy':
                result.buy_order_id = open_order.id
            else:
                result.sell_order_id = open_order.id

            self.logger.info(
                f"✅ 开仓完成 - {result.filled_side.upper()} {result.filled_amount}@{open_price}")

            # 获取平仓价格
            close_price, close_amount = await self._get_fill_info(close_order.id, close_side)

            if close_price is None:
                orderbook = await self.adapter.get_orderbook(self.config.symbol)
                if close_side == OrderSide.SELL and orderbook.bids:
                    close_price = orderbook.bids[0].price
                elif close_side == OrderSide.BUY and orderbook.asks:
                    close_price = orderbook.asks[0].price
                else:
                    close_price = ask_price if close_side == OrderSide.BUY else bid_price

            result.close_price = close_price
            result.close_amount = result.filled_amount

            # 计算盈亏
            if result.filled_side == 'buy':
                result.pnl = (result.close_price -
                              result.filled_price) * result.filled_amount
            else:
                result.pnl = (result.filled_price -
                              result.close_price) * result.filled_amount

            self.logger.info(
                f"✅ 平仓完成 - 价格: {result.close_price}, 盈亏: {result.pnl}")

            result.status = CycleStatus.SUCCESS

        except Exception as e:
            result.status = CycleStatus.FAILED
            result.error_message = str(e)
            self.logger.error(f"❌ 轮次执行失败: {e}", exc_info=True)

        finally:
            # 清理
            await self._cleanup_current_cycle()

            # 更新结果
            result.end_time = datetime.now()
            result.duration = result.end_time - result.start_time

            # 更新统计
            self.statistics.update_from_cycle(result)

            # 📊 更新小时级统计
            if self._hourly_tracker:
                self._hourly_tracker.add_cycle(result)

            self.logger.info(
                f"━━━━━━ 第 {cycle_id} 轮结束 - {result.status.value} ━━━━━━\n")

    async def _subscribe_order_updates(self) -> None:
        """
        订阅 WebSocket 订单更新（用于获取成交价格）
        """
        try:
            if self._ws_order_subscribed:
                return

            self.logger.info("📡 订阅 WebSocket 订单更新...")

            # 订阅用户数据流（包含订单更新）
            await self.adapter.subscribe_user_data(self._on_order_update)

            self._ws_order_subscribed = True
            self._ws_order_healthy = True
            self._ws_order_last_message_time = datetime.now()
            self.logger.info("✅ WebSocket 订单更新订阅成功")

        except Exception as e:
            self.logger.error(f"❌ WebSocket 订单更新订阅失败: {e}", exc_info=True)
            self._ws_order_subscribed = False
            self._ws_order_healthy = False

    async def _subscribe_orderbook(self) -> None:
        """
        订阅 WebSocket 订单簿更新（用于价格稳定检测）
        """
        try:
            if self._ws_orderbook_subscribed:
                return

            self.logger.info("📡 订阅 WebSocket 订单簿...")

            # 订阅订单簿数据流
            await self.adapter.subscribe_orderbook(self.config.symbol, self._on_orderbook_update)

            self._ws_orderbook_subscribed = True
            self._ws_orderbook_healthy = True
            self._ws_orderbook_last_message_time = datetime.now()
            self.logger.info("✅ WebSocket 订单簿订阅成功")

            # 🔥 立即获取一次订单簿快照，避免等待首次推送
            # 这样终端UI可以立即显示数据
            try:
                self.logger.info("📊 获取初始订单簿快照...")
                initial_orderbook = await self.adapter.get_orderbook(self.config.symbol)
                if initial_orderbook and initial_orderbook.bids and initial_orderbook.asks:
                    self._latest_orderbook = initial_orderbook
                    self.logger.info(
                        f"✅ 初始订单簿加载成功 - "
                        f"买1: ${initial_orderbook.bids[0].price}, "
                        f"卖1: ${initial_orderbook.asks[0].price}")
                else:
                    self.logger.warning("⚠️ 初始订单簿为空")
            except Exception as snapshot_error:
                self.logger.warning(f"⚠️ 获取初始订单簿快照失败: {snapshot_error}")

        except Exception as e:
            self.logger.error(f"❌ WebSocket 订单簿订阅失败: {e}", exc_info=True)
            self._ws_orderbook_subscribed = False
            self._ws_orderbook_healthy = False

    async def _on_orderbook_update(self, orderbook: 'OrderBookData') -> None:
        """
        WebSocket 订单簿更新回调

        Args:
            orderbook: 订单簿数据
        """
        try:
            # 🔥 更新心跳时间
            self._ws_orderbook_last_message_time = datetime.now()
            if not self._ws_orderbook_healthy:
                self._ws_orderbook_healthy = True
                self.logger.info("✅ WebSocket 订单簿连接已恢复健康")

            # 🔥 关键修复：只有当订单簿数据完整时才更新缓存
            # 这样可以避免不完整的数据覆盖掉有效数据
            if not orderbook or not orderbook.bids or not orderbook.asks:
                # 数据不完整，记录警告但保留旧数据
                if not hasattr(self, '_incomplete_orderbook_count'):
                    self._incomplete_orderbook_count = 0
                self._incomplete_orderbook_count += 1

                # 每10次不完整数据记录一次警告
                if self._incomplete_orderbook_count % 10 == 1:
                    self.logger.warning(
                        f"⚠️ 收到不完整的订单簿数据 (第{self._incomplete_orderbook_count}次) - "
                        f"买单: {len(orderbook.bids) if orderbook and orderbook.bids else 0}, "
                        f"卖单: {len(orderbook.asks) if orderbook and orderbook.asks else 0}")
                return

            # 数据完整，更新缓存
            self._latest_orderbook = orderbook

            # 🔍 调试日志（仅首次和每10秒记录一次，避免刷屏）
            if not hasattr(self, '_last_orderbook_log_time'):
                self._last_orderbook_log_time = datetime.now()
                self.logger.info(
                    f"📖 收到首次完整订单簿推送 - "
                    f"买1: ${orderbook.bids[0].price} × {orderbook.bids[0].size}, "
                    f"卖1: ${orderbook.asks[0].price} × {orderbook.asks[0].size}")
            elif (datetime.now() - self._last_orderbook_log_time).total_seconds() >= 10:
                self._last_orderbook_log_time = datetime.now()
                self.logger.debug(
                    f"📖 订单簿更新 - "
                    f"买1: ${orderbook.bids[0].price} × {orderbook.bids[0].size}, "
                    f"卖1: ${orderbook.asks[0].price} × {orderbook.asks[0].size}")
        except Exception as e:
            self.logger.error(f"❌ 处理WebSocket订单簿更新失败: {e}", exc_info=True)

    async def _on_order_update(self, update_data: Dict[str, Any]) -> None:
        """
        WebSocket 订单更新回调

        Backpack 订单更新格式:
        {
            "stream": "account.orderUpdate",
            "data": {
                "e": "orderFilled",         # 事件类型
                "i": "15115730686",          # 订单ID
                "X": "Filled",               # 订单状态
                "p": "110520.9",             # 成交价格 ⭐
                "z": "0.01",                 # 成交数量 ⭐
                "s": "BTC_USDC_PERP",        # 交易对
                "S": "Bid",                  # 方向
                "T": 1729768293700           # 时间戳
            }
        }
        """
        try:
            # 🔥 更新心跳时间
            self._ws_order_last_message_time = datetime.now()
            if not self._ws_order_healthy:
                self._ws_order_healthy = True
                self.logger.info("✅ WebSocket 订单连接已恢复健康")

            data = update_data.get('data', {})
            event_type = data.get('e', '')
            order_id = data.get('i', '')
            status = data.get('X', '')

            # 只处理已成交的订单
            if status == 'Filled' and order_id:
                # 🔥 Backpack WebSocket 字段映射：
                # - 'L': 最后成交价格（Last Price）⭐
                # - 'Z': 成交总金额（cumulative quote quantity）
                # - 'z': 成交数量（cumulative filled quantity）
                price_str = data.get('L') or data.get(
                    'p')  # 优先使用 L（Last Price）
                amount_str = data.get('z')
                side_str = data.get('S', '')

                if price_str and amount_str:
                    # 保存成交信息
                    self._order_fills[order_id] = {
                        'price': Decimal(str(price_str)),
                        'amount': Decimal(str(amount_str)),
                        'timestamp': datetime.now(),
                        'side': side_str,
                        'raw_data': data
                    }

                    self.logger.info(
                        f"📨 WebSocket收到订单成交 - ID: {order_id}, "
                        f"价格: {price_str}, 数量: {amount_str}, 方向: {side_str}")

                    # 触发等待事件
                    if order_id in self._order_fill_events:
                        self._order_fill_events[order_id].set()

        except Exception as e:
            self.logger.error(f"❌ 处理WebSocket订单更新失败: {e}", exc_info=True)

    async def _get_fill_info_from_ws(
        self,
        order_id: str,
        side: OrderSide,
        timeout: float = 5.0
    ) -> Tuple[Optional[Decimal], Decimal]:
        """
        从 WebSocket 获取订单成交信息（实时，延迟<100ms）

        Args:
            order_id: 订单ID
            side: 订单方向
            timeout: 等待超时时间（秒）

        Returns:
            (成交价格, 成交数量)
        """
        try:
            # 检查是否已经收到成交信息
            if order_id in self._order_fills:
                fill_info = self._order_fills[order_id]
                self.logger.info(
                    f"✅ 从WebSocket缓存获取成交信息 - 订单: {order_id}, "
                    f"价格: {fill_info['price']}, 数量: {fill_info['amount']}")
                return (fill_info['price'], fill_info['amount'])

            # 创建等待事件
            event = asyncio.Event()
            self._order_fill_events[order_id] = event

            self.logger.info(
                f"⏳ 等待WebSocket推送订单成交 - 订单: {order_id}, 超时: {timeout}秒")

            try:
                # 等待 WebSocket 推送成交信息
                await asyncio.wait_for(event.wait(), timeout=timeout)

                # 获取成交信息
                if order_id in self._order_fills:
                    fill_info = self._order_fills[order_id]
                    self.logger.info(
                        f"✅ 从WebSocket获取成交信息 - 订单: {order_id}, "
                        f"价格: {fill_info['price']}, 数量: {fill_info['amount']}")
                    return (fill_info['price'], fill_info['amount'])
                else:
                    self.logger.warning(
                        f"⚠️ WebSocket事件触发但未找到成交信息: {order_id}")
                    return (None, self.config.order_size)

            except asyncio.TimeoutError:
                self.logger.warning(
                    f"⚠️ WebSocket等待超时({timeout}秒) - 订单: {order_id}")
                return (None, self.config.order_size)

        except Exception as e:
            self.logger.error(f"❌ 从WebSocket获取成交信息失败: {e}", exc_info=True)
            return (None, self.config.order_size)

        finally:
            # 清理
            self._order_fill_events.pop(order_id, None)
            # 保留成交信息一段时间（用于统计），后续可以定期清理

    async def _get_fill_info(self, order_id: str, side: OrderSide) -> Tuple[Optional[Decimal], Decimal]:
        """
        获取订单成交信息（支持REST和WebSocket两种方式）

        方式选择（通过配置 fill_price_method）：
        - "websocket": 实时WebSocket推送（延迟<100ms，100%准确，推荐）
        - "rest": REST API查询历史订单（延迟10-30秒，准确率~95%）

        Args:
            order_id: 订单ID
            side: 订单方向

        Returns:
            (成交价格, 成交数量)
        """
        # 🔥 根据配置选择获取方式
        if self.config.fill_price_method == "websocket":
            # 🔥 检查WebSocket健康状态
            if not self._ws_order_healthy:
                self.logger.warning(
                    f"⚠️ WebSocket 订单连接不健康，直接使用 REST API - 订单: {order_id}")
                return await self._get_fill_info_from_rest(order_id, side)

            # WebSocket 方式（推荐）
            fill_price, fill_amount = await self._get_fill_info_from_ws(order_id, side, timeout=5.0)

            # 如果WebSocket方式失败，降级到REST方式
            if fill_price is None:
                self.logger.warning(
                    f"⚠️ WebSocket方式失败，降级到REST API查询 - 订单: {order_id}")
                # 标记为不健康，触发重连
                self._ws_order_healthy = False
                return await self._get_fill_info_from_rest(order_id, side)

            return (fill_price, fill_amount)
        else:
            # REST API 方式
            return await self._get_fill_info_from_rest(order_id, side)

    async def _get_fill_info_from_rest(self, order_id: str, side: OrderSide) -> Tuple[Optional[Decimal], Decimal]:
        """
        从 REST API 查询订单成交信息（延迟较高但稳定）

        策略：
        1. 查询历史订单（get_order_history）- 包含已完全成交的订单
        2. 降级到查询活跃订单（get_order）- 如果订单还在活跃列表
        3. 最后降级到市场价估算

        Args:
            order_id: 订单ID
            side: 订单方向

        Returns:
            (成交价格, 成交数量)
        """
        try:
            # 🔥 策略1: 查询历史订单（推荐！适合已完全成交的订单）
            max_retries = 10  # 10次重试
            retry_delay = 3.0  # 3秒延迟（总计最多等待30秒）

            for attempt in range(max_retries):
                # 短暂等待让订单完全成交并进入历史记录
                if attempt > 0:
                    await asyncio.sleep(retry_delay)

                # 🔥 查询历史订单（limit=20 最近20条，增加查找范围）
                history_orders = await self.adapter.get_order_history(
                    symbol=self.config.symbol,
                    limit=20
                )

                self.logger.info(
                    f"🔍 查询历史订单 - 目标: {order_id}, 获取到: {len(history_orders)} 条, "
                    f"尝试 {attempt + 1}/{max_retries}")

                # 查找对应的订单
                for order in history_orders:
                    if order.id == order_id:
                        # 找到了！检查成交状态
                        if order.status == OrderStatus.FILLED and order.average is not None:
                            self.logger.info(
                                f"✅ 从历史订单获取成交信息 - 订单: {order_id}, "
                                f"平均价格: {order.average}, 成交数量: {order.filled}")
                            return (order.average, order.filled)

                        # 部分成交或其他状态
                        if order.filled > 0 and order.average is not None:
                            self.logger.warning(
                                f"⚠️ 订单部分成交 - 订单: {order_id}, "
                                f"平均价格: {order.average}, 成交: {order.filled}/{order.amount}")
                            return (order.average, order.filled)

                # 没找到，继续重试
                if attempt < max_retries - 1:
                    self.logger.info(
                        f"🔍 历史订单中未找到订单 {order_id}，重试 {attempt + 1}/{max_retries} (等待 {int(retry_delay*1000)}ms)")

            # 🔥 策略2: 降级到查询活跃订单（可能订单还在活跃列表）
            self.logger.debug(f"🔍 尝试查询活跃订单: {order_id}")
            try:
                order = await self.adapter.get_order(order_id, self.config.symbol)
                if order.status == OrderStatus.FILLED and order.average is not None:
                    self.logger.info(
                        f"✅ 从活跃订单获取成交信息 - 订单: {order_id}, "
                        f"平均价格: {order.average}, 成交数量: {order.filled}")
                    return (order.average, order.filled)
            except Exception as e:
                self.logger.debug(f"查询活跃订单失败: {e}")

            # 🔥 策略3: 降级到市场价估算
            self.logger.warning(
                f"⚠️ 无法获取订单成交信息 - 订单: {order_id}, 使用市场价估算")

            orderbook = await self.adapter.get_orderbook(self.config.symbol)
            fill_price = None
            if side == OrderSide.SELL and orderbook.bids:
                fill_price = orderbook.bids[0].price
            elif side == OrderSide.BUY and orderbook.asks:
                fill_price = orderbook.asks[0].price
            fill_amount = self.config.order_size

            self.logger.warning(
                f"⚠️ 使用市场价估算 - 价格: {fill_price}, 数量: {fill_amount}")

            return (fill_price, fill_amount)

        except Exception as e:
            self.logger.error(f"❌ 获取订单信息失败: {e}", exc_info=True)
            # 最后的降级方案
            try:
                orderbook = await self.adapter.get_orderbook(self.config.symbol)
                fill_price = None
                if side == OrderSide.SELL and orderbook.bids:
                    fill_price = orderbook.bids[0].price
                elif side == OrderSide.BUY and orderbook.asks:
                    fill_price = orderbook.asks[0].price
                return (fill_price, self.config.order_size)
            except Exception:
                return (None, self.config.order_size)

    async def _wait_for_stable_price(self) -> Optional[Tuple[Decimal, Decimal, Optional[float]]]:
        """
        等待价格稳定（支持 WebSocket 和 REST API 两种方式）

        根据配置 orderbook_method 选择：
        - "websocket": 使用 WebSocket 实时订单簿（延迟低，推荐）
        - "rest": 使用 REST API 轮询订单簿（延迟高，兼容模式）

        Returns:
            (bid_price, ask_price, quantity_ratio) 或 None
            quantity_ratio: 买卖单数量比例（百分比），如果未启用则为 None
        """
        # 根据配置选择获取方式
        if self.config.orderbook_method == "websocket":
            return await self._wait_for_stable_price_ws()
        else:
            return await self._wait_for_stable_price_rest()

    async def _wait_for_stable_price_ws(self) -> Optional[Tuple[Decimal, Decimal, Optional[float]]]:
        """
        使用 WebSocket 订单簿等待价格稳定（推荐方式）

        Returns:
            (bid_price, ask_price, quantity_ratio) 或 None
            quantity_ratio: WebSocket模式下暂未实现，返回 None
        """
        duration = self.config.stability_check_duration
        tolerance = self.config.price_tolerance
        interval = 0.01  # WebSocket 模式下检查间隔更短（10ms）

        last_bid: Optional[Decimal] = None
        last_ask: Optional[Decimal] = None
        stable_start: Optional[datetime] = None

        timeout = 300  # 最多等待5分钟
        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < timeout:
            try:
                # 🔥 从 WebSocket 缓存中获取最新订单簿
                if self._latest_orderbook is None:
                    # 如果还没收到订单簿推送，等待一下
                    await asyncio.sleep(interval)
                    continue

                orderbook = self._latest_orderbook

                if not orderbook.bids or not orderbook.asks:
                    await asyncio.sleep(interval)
                    continue

                current_bid = orderbook.bids[0].price
                current_ask = orderbook.asks[0].price

                # 检查价格是否稳定
                if last_bid is not None and last_ask is not None:
                    bid_changed = abs(current_bid - last_bid) > tolerance
                    ask_changed = abs(current_ask - last_ask) > tolerance

                    if bid_changed or ask_changed:
                        # 价格变化，重置
                        stable_start = None
                    elif stable_start is None:
                        # 开始稳定计时
                        stable_start = datetime.now()
                    else:
                        # 检查稳定时长
                        stable_duration = (
                            datetime.now() - stable_start).total_seconds()
                        if stable_duration >= duration:
                            # 价格稳定达到要求
                            # WebSocket模式下，计算当前比例用于记录
                            bid_amount = orderbook.bids[0].size
                            ask_amount = orderbook.asks[0].size
                            max_amount = max(bid_amount, ask_amount)
                            min_amount = min(bid_amount, ask_amount)
                            final_ratio = None
                            if min_amount > 0:
                                final_ratio = float(
                                    max_amount / min_amount) * 100
                            return (current_bid, current_ask, final_ratio)

                last_bid = current_bid
                last_ask = current_ask

                await asyncio.sleep(interval)

            except Exception as e:
                self.logger.error(f"检查价格稳定失败(WebSocket): {e}")
                await asyncio.sleep(interval)

        self.logger.warning("⚠️ 等待价格稳定超时(WebSocket)")
        return None

    async def _wait_for_stable_price_rest(self) -> Optional[Tuple[Decimal, Decimal, Optional[float]]]:
        """
        使用 REST API 轮询订单簿等待价格稳定（兼容方式）

        支持可选的买卖单数量对比反转检测

        Returns:
            (bid_price, ask_price, quantity_ratio) 或 None
            quantity_ratio: 买卖单数量比例（百分比），如果未启用则为 None
        """
        duration = self.config.stability_check_duration
        tolerance = self.config.price_tolerance
        interval = self.config.check_interval
        check_reversal = self.config.check_orderbook_reversal

        last_bid: Optional[Decimal] = None
        last_ask: Optional[Decimal] = None
        stable_start: Optional[datetime] = None

        # 🔥 买卖单数量对比反转检测（新增）
        initial_orderbook_side: Optional[str] = None  # "ask_more" 或 "bid_more"
        reversal_count = 0  # 记录反转次数
        final_ratio: Optional[float] = None  # 最终的买卖单数量比例

        timeout = 300  # 最多等待5分钟
        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < timeout:
            try:
                # 🔥 通过 REST API 获取订单簿
                orderbook = await self.adapter.get_orderbook(self.config.symbol)

                if not orderbook.bids or not orderbook.asks:
                    await asyncio.sleep(interval)
                    continue

                current_bid = orderbook.bids[0].price
                current_ask = orderbook.asks[0].price

                # 🔥 获取买1和卖1的数量（用于反转检测）
                bid_amount = orderbook.bids[0].size
                ask_amount = orderbook.asks[0].size

                # 检查价格是否稳定
                if last_bid is not None and last_ask is not None:
                    bid_changed = abs(current_bid - last_bid) > tolerance
                    ask_changed = abs(current_ask - last_ask) > tolerance

                    # 🔥 检查买卖单数量对比是否发生反转（如果启用）
                    orderbook_reversed = False
                    if check_reversal:
                        # 确定当前的买卖单数量对比关系
                        current_side = "ask_more" if ask_amount > bid_amount else "bid_more"

                        # 如果是第一次记录，保存初始状态
                        if initial_orderbook_side is None:
                            initial_orderbook_side = current_side
                        # 否则检查是否发生反转
                        elif current_side != initial_orderbook_side:
                            orderbook_reversed = True
                            reversal_count += 1

                            # 记录反转信息
                            self.logger.info(
                                f"📊 买卖单数量对比发生反转 (第{reversal_count}次) - "
                                f"初始: {initial_orderbook_side}, 当前: {current_side}, "
                                f"买1数量: {bid_amount}, 卖1数量: {ask_amount}")

                    # 判断是否需要重置稳定计时
                    if bid_changed or ask_changed or orderbook_reversed:
                        # 价格变化 或 数量对比反转，重置
                        if orderbook_reversed:
                            # 反转时，重置初始状态为当前状态
                            current_side = "ask_more" if ask_amount > bid_amount else "bid_more"
                            initial_orderbook_side = current_side
                            self.logger.info(
                                f"   → 重新计时，新的初始状态: {current_side}")

                        stable_start = None
                    elif stable_start is None:
                        # 开始稳定计时
                        stable_start = datetime.now()

                        # 记录稳定开始时的状态
                        if check_reversal and initial_orderbook_side:
                            self.logger.debug(
                                f"✅ 开始稳定计时 - 买卖单状态: {initial_orderbook_side}, "
                                f"买1: {bid_amount}, 卖1: {ask_amount}")
                    else:
                        # 检查稳定时长
                        stable_duration = (
                            datetime.now() - stable_start).total_seconds()
                        if stable_duration >= duration:
                            # 🔥 最后一道检查：买卖单数量比例（如果启用）
                            if self.config.orderbook_quantity_ratio > 0:
                                # 计算比例：数量多的 / 数量少的
                                max_amount = max(bid_amount, ask_amount)
                                min_amount = min(bid_amount, ask_amount)

                                if min_amount > 0:
                                    ratio = float(
                                        max_amount / min_amount) * 100  # 转换为百分比
                                    final_ratio = ratio  # 保存比例值

                                    if ratio < self.config.orderbook_quantity_ratio:
                                        # 比例不满足要求，重新计时
                                        self.logger.info(
                                            f"⚠️ 买卖单数量比例不满足要求，重新计时 - "
                                            f"当前比例: {ratio:.1f}%, "
                                            f"要求: {self.config.orderbook_quantity_ratio:.1f}%, "
                                            f"买1: {bid_amount}, 卖1: {ask_amount}")
                                        stable_start = None
                                        continue
                                    else:
                                        self.logger.info(
                                            f"✅ 买卖单数量比例符合要求 - "
                                            f"比例: {ratio:.1f}% >= {self.config.orderbook_quantity_ratio:.1f}%, "
                                            f"买1: {bid_amount}, 卖1: {ask_amount}")
                            else:
                                # 未启用数量比例检查，计算当前比例用于记录
                                max_amount = max(bid_amount, ask_amount)
                                min_amount = min(bid_amount, ask_amount)
                                if min_amount > 0:
                                    final_ratio = float(
                                        max_amount / min_amount) * 100

                            # 🔥 检查最小数量（仅市价模式，不重置倒计时）
                            if (self.config.order_mode == "market" and
                                    self.config.orderbook_min_quantity > 0):
                                # 找出数量多的那一方
                                larger_amount = max(bid_amount, ask_amount)
                                side_name = "买1" if bid_amount > ask_amount else "卖1"

                                if larger_amount < Decimal(str(self.config.orderbook_min_quantity)):
                                    # 数量不足，不重置倒计时，继续等待
                                    self.logger.info(
                                        f"⏳ 订单簿数量不足，继续等待 - "
                                        f"{side_name}数量: {larger_amount}, "
                                        f"要求: {self.config.orderbook_min_quantity}, "
                                        f"买1: {bid_amount}, 卖1: {ask_amount} "
                                        f"(倒计时不重置，当前已稳定: {stable_duration:.1f}秒)")
                                    # 不重置 stable_start，继续循环
                                    await asyncio.sleep(interval)
                                    continue
                                else:
                                    self.logger.info(
                                        f"✅ 订单簿数量符合要求 - "
                                        f"{side_name}数量: {larger_amount} >= {self.config.orderbook_min_quantity}, "
                                        f"买1: {bid_amount}, 卖1: {ask_amount}")

                            # 价格稳定达到要求
                            if check_reversal and reversal_count > 0:
                                self.logger.info(
                                    f"✅ 价格和买卖单数量对比稳定 - "
                                    f"历史反转次数: {reversal_count}, "
                                    f"最终状态: {initial_orderbook_side}")
                            return (current_bid, current_ask, final_ratio)

                last_bid = current_bid
                last_ask = current_ask

                await asyncio.sleep(interval)

            except Exception as e:
                self.logger.error(f"获取订单簿失败(REST API): {e}")
                await asyncio.sleep(interval)

        self.logger.warning("⚠️ 等待价格稳定超时(REST API)")
        return None

    async def _place_both_orders(self, bid_price: Decimal, ask_price: Decimal) -> bool:
        """
        同时挂买单和卖单

        Args:
            bid_price: 买1价格
            ask_price: 卖1价格

        Returns:
            是否成功
        """
        try:
            # 🔥 重要：不传递任何额外参数，避免签名验证失败
            # Backpack API 对额外参数非常敏感，任何不支持的参数都会导致 "Invalid signature" 错误
            # 参考网格交易的成功经验：params=None

            # 挂买单（以买1价格）
            buy_order = await self.adapter.create_order(
                symbol=self.config.symbol,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                amount=self.config.order_size,
                price=bid_price,
                params=None  # 不传递任何额外参数，避免签名问题
            )
            self._current_buy_order = buy_order
            self.logger.info(f"✅ 买单已挂: {buy_order.id} @ {bid_price}")

            # 挂卖单（以卖1价格）
            sell_order = await self.adapter.create_order(
                symbol=self.config.symbol,
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                amount=self.config.order_size,
                price=ask_price,
                params=None  # 不传递任何额外参数，避免签名问题
            )
            self._current_sell_order = sell_order
            self.logger.info(f"✅ 卖单已挂: {sell_order.id} @ {ask_price}")

            return True

        except Exception as e:
            self.logger.error(f"❌ 下单失败: {e}")
            # 清理已挂订单
            await self._cancel_both_orders()
            return False

    async def _wait_for_fill(self) -> Optional[OrderData]:
        """
        等待其中一个订单成交

        参考网格交易的方法：
        使用 get_open_orders() 查询所有订单，避免 Backpack API 不支持单独查询订单的问题

        Returns:
            成交的订单或None
        """
        timeout = self.config.order_timeout
        start_time = datetime.now()

        while (datetime.now() - start_time).total_seconds() < timeout:
            try:
                # 🔥 参考网格交易：使用 get_open_orders() 而不是 get_order()
                # Backpack API 不支持单独查询订单（返回 404）
                open_orders = await self.adapter.get_open_orders(self.config.symbol)

                # 创建订单ID集合
                open_order_ids = {
                    order.id for order in open_orders if order.id}

                # 检查买单是否已成交（不在挂单列表中）
                if self._current_buy_order and self._current_buy_order.id not in open_order_ids:
                    self.logger.info(
                        f"买单已成交（不在挂单列表）: {self._current_buy_order.id}")
                    # 🔥 从WebSocket缓存中更新成交信息
                    if self._current_buy_order.id in self._order_fills:
                        fill_info = self._order_fills[self._current_buy_order.id]
                        self._current_buy_order.average = Decimal(
                            str(fill_info['price']))
                        self._current_buy_order.filled = Decimal(
                            str(fill_info['amount']))
                        self.logger.info(
                            f"✅ 从WebSocket获取成交信息 - 价格: {self._current_buy_order.average}, 数量: {self._current_buy_order.filled}")
                    return self._current_buy_order

                # 检查卖单是否已成交（不在挂单列表中）
                if self._current_sell_order and self._current_sell_order.id not in open_order_ids:
                    self.logger.info(
                        f"卖单已成交（不在挂单列表）: {self._current_sell_order.id}")
                    # 🔥 从WebSocket缓存中更新成交信息
                    if self._current_sell_order.id in self._order_fills:
                        fill_info = self._order_fills[self._current_sell_order.id]
                        self._current_sell_order.average = Decimal(
                            str(fill_info['price']))
                        self._current_sell_order.filled = Decimal(
                            str(fill_info['amount']))
                        self.logger.info(
                            f"✅ 从WebSocket获取成交信息 - 价格: {self._current_sell_order.average}, 数量: {self._current_sell_order.filled}")
                    return self._current_sell_order

                await asyncio.sleep(0.5)

            except Exception as e:
                self.logger.error(f"检查订单状态失败: {e}")
                await asyncio.sleep(0.5)

        return None

    async def _cancel_unfilled_order(self, filled_order: OrderData) -> None:
        """取消未成交的订单"""
        try:
            # 确定要取消的订单
            if filled_order.side == OrderSide.BUY and self._current_sell_order:
                await self.adapter.cancel_order(
                    self._current_sell_order.id,
                    self.config.symbol
                )
                self.logger.info(
                    f"✅ 已取消卖单: {self._current_sell_order.id}")
                self._current_sell_order = None
            elif filled_order.side == OrderSide.SELL and self._current_buy_order:
                await self.adapter.cancel_order(
                    self._current_buy_order.id,
                    self.config.symbol
                )
                self.logger.info(
                    f"✅ 已取消买单: {self._current_buy_order.id}")
                self._current_buy_order = None
        except Exception as e:
            self.logger.error(f"取消订单失败: {e}")

    async def _cancel_both_orders(self) -> None:
        """取消两个订单"""
        try:
            if self._current_buy_order:
                await self.adapter.cancel_order(
                    self._current_buy_order.id,
                    self.config.symbol
                )
                self._current_buy_order = None
        except Exception as e:
            self.logger.error(f"取消买单失败: {e}")

        try:
            if self._current_sell_order:
                await self.adapter.cancel_order(
                    self._current_sell_order.id,
                    self.config.symbol
                )
                self._current_sell_order = None
        except Exception as e:
            self.logger.error(f"取消卖单失败: {e}")

    async def _close_position(self) -> Optional[Tuple[Decimal, Decimal]]:
        """
        市价平仓（带重试和严格确认）

        Returns:
            (close_price, close_amount) 或 None（平仓失败）
        """
        max_retries = 3  # 最多重试3次
        confirm_timeout = 1.0  # 每次确认超时1秒
        check_interval = 0.1  # 每100ms检查一次

        for attempt in range(1, max_retries + 1):
            try:
                # 获取当前持仓
                positions = await self.adapter.get_positions([self.config.symbol])
                if not positions or abs(positions[0].size) < Decimal("0.00001"):
                    if attempt == 1:
                        self.logger.info("✅ 无持仓，无需平仓")
                        return None
                    else:
                        # 重试后发现持仓已清零
                        self.logger.info(f"✅ 持仓已清零（第{attempt}次尝试）")
                        break

                position = positions[0]
                close_amount_target = abs(position.size)
                # 🔥 重要：根据持仓方向（side字段）判断平仓方向，而不是size
                # size在某些交易所中始终是正数（绝对值）
                side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY

                # 市价平仓
                if attempt == 1:
                    self.logger.info(
                        f"📝 下平仓单 - {side.value} {close_amount_target}")
                else:
                    self.logger.warning(
                        f"⚠️ 第{attempt}次尝试平仓 - 持仓仍有 {position.size}")

                close_order = await self.adapter.create_order(
                    symbol=self.config.symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    amount=close_amount_target
                )

                # 🔥 立即轮询确认持仓是否清零
                self.logger.info(f"⏳ 确认平仓（轮询 {confirm_timeout}秒）...")
                position_closed = False
                start_time = datetime.now()

                while (datetime.now() - start_time).total_seconds() < confirm_timeout:
                    try:
                        positions = await self.adapter.get_positions([self.config.symbol])
                        # 持仓消失或变为0，说明平仓成功
                        if not positions or abs(positions[0].size) < Decimal("0.00001"):
                            position_closed = True
                            elapsed = (datetime.now() -
                                       start_time).total_seconds()
                            self.logger.info(f"✅ 持仓已清零 (耗时: {elapsed:.2f}秒)")
                            break
                    except Exception as e:
                        self.logger.warning(f"查询持仓失败: {e}")

                    await asyncio.sleep(check_interval)

                # 如果确认平仓成功，跳出重试循环
                if position_closed:
                    break
                else:
                    # {confirm_timeout}秒后持仓还在
                    if attempt < max_retries:
                        self.logger.warning(
                            f"⚠️ {confirm_timeout}秒内持仓未清零，准备第{attempt + 1}次平仓")
                    else:
                        # 最后一次尝试失败
                        self.logger.error(
                            f"❌ 平仓失败！{max_retries}次尝试后持仓仍未清零")
                        return None

            except Exception as e:
                self.logger.error(f"平仓过程出错（第{attempt}次）: {e}")
                if attempt >= max_retries:
                    return None
                await asyncio.sleep(0.5)  # 出错后等待0.5秒再重试

        # 🔥 平仓成功后，获取平仓成交信息（用于统计）
        close_price = None
        close_amount = Decimal("0")

        try:
            trades = await self.adapter.get_trades(
                symbol=self.config.symbol,
                limit=20  # 增加到20条，因为可能有多次平仓
            )

            # 查找所有平仓成交记录
            for trade in trades:
                if hasattr(trade, 'order_id'):
                    # 匹配任何一次平仓尝试的订单ID
                    close_price = trade.price
                    close_amount += trade.amount
                    self.logger.info(
                        f"✅ 找到平仓成交 - 价格: {trade.price}, 数量: {trade.amount}")
                    break  # 找到一条就够了

        except Exception as e:
            self.logger.warning(f"查询成交记录失败: {e}")

        # 如果没找到成交记录，使用当前市场价估算
        if close_price is None:
            try:
                orderbook = await self.adapter.get_orderbook(self.config.symbol)
                if side == OrderSide.SELL and orderbook.bids:
                    close_price = orderbook.bids[0].price
                elif side == OrderSide.BUY and orderbook.asks:
                    close_price = orderbook.asks[0].price
                close_amount = close_amount_target
                self.logger.warning(
                    f"⚠️ 未找到平仓成交记录，使用市场价估算 - 价格: {close_price}, 数量: {close_amount}")
            except Exception as e:
                self.logger.error(f"获取市场价失败: {e}")
                # 即使获取市场价失败，也返回成功（因为持仓已确认清零）
                # 使用一个估算值
                close_price = Decimal("0")
                close_amount = close_amount_target

        self.logger.info(
            f"✅ 平仓完成 - 价格: {close_price}, 数量: {close_amount}")

        return (close_price, close_amount)

    async def _check_balance(self) -> bool:
        """
        检查账户余额是否满足最小要求

        Returns:
            True: 余额充足，可以继续交易
            False: 余额不足，应该停止交易
        """
        try:
            # 🔥 参考网格脚本的方法：使用 get_balances() 获取所有余额
            balances = await self.adapter.get_balances()

            # 查找USDC余额
            usdc_balance = None
            for balance in balances:
                if balance.currency.upper() == 'USDC':
                    usdc_balance = balance
                    break

            if not usdc_balance:
                self.logger.error("❌ 未找到USDC余额")
                return False

            # 🔥 从 raw_data 中提取余额信息
            raw_data = usdc_balance.raw_data

            net_equity = Decimal(str(raw_data.get('_account_netEquity', '0')))
            available_qty = Decimal(
                str(raw_data.get('availableQuantity', '0')))
            net_equity_available = Decimal(
                str(raw_data.get('_account_netEquityAvailable', '0')))
            lend_quantity = Decimal(str(raw_data.get('lendQuantity', '0')))

            # ✅ Backpack 统一账户模型（支持组合保证金）
            # - 组合保证金账户：现货可用为0是正常的，资金在权益账户
            # - 只要账户权益（netEquity）足够，就可以开仓
            available = net_equity

            self.logger.info(
                f"💰 账户余额检查 - 账户权益: {net_equity} USDC, "
                f"阈值: {self.config.min_balance} USDC "
                f"(可用净资产: {net_equity_available}, 现货可用: {available_qty})")

            # 判断账户权益是否低于阈值
            if available < self.config.min_balance:
                self.logger.error(
                    f"❌ 余额不足！账户权益: {available} USDC < 阈值: {self.config.min_balance} USDC")
                return False

            self.logger.info(f"✅ 余额充足，继续交易")
            return True

        except Exception as e:
            self.logger.error(f"❌ 查询余额失败: {e}", exc_info=True)
            # 查询失败时，为安全起见，返回 False 停止交易
            return False

    async def _wait_for_price_change(self, initial_bid: Decimal, initial_ask: Decimal,
                                     initial_bid_amount: Optional[Decimal] = None,
                                     initial_ask_amount: Optional[Decimal] = None,
                                     timeout: float = 30.0) -> tuple[bool, float, str]:
        """
        等待订单簿价格变化（支持 WebSocket 和 REST API 两种方式）

        根据配置 orderbook_method 自动选择获取方式
        可选支持订单簿数量反转检测（仅市价模式）

        Args:
            initial_bid: 初始买1价格
            initial_ask: 初始卖1价格
            initial_bid_amount: 初始买1数量（用于反转检测）
            initial_ask_amount: 初始卖1数量（用于反转检测）
            timeout: 超时时间（秒）

        Returns:
            tuple: (是否成功, 等待时间秒数, 平仓原因)
            平仓原因: "price_change"(价格变化), "quantity_reversal"(数量反转), "timeout"(超时)
        """
        start_time = datetime.now()
        # WebSocket 模式下检查间隔更短（10ms vs 100ms）
        check_interval = 0.01 if self.config.orderbook_method == "websocket" else 0.1

        # 🔥 是否启用数量反转检测（仅市价模式且提供了初始数量）
        check_reversal = (self.config.order_mode == "market" and
                          self.config.market_close_on_quantity_reversal and
                          initial_bid_amount is not None and
                          initial_ask_amount is not None)

        # 记录初始数量关系
        initial_side = None
        if check_reversal:
            initial_side = "bid_more" if initial_bid_amount > initial_ask_amount else "ask_more"
            self.logger.info(
                f"📊 等待平仓触发({self.config.orderbook_method.upper()}) - "
                f"初始买1: {initial_bid}, 初始卖1: {initial_ask}, "
                f"买1数量: {initial_bid_amount}, 卖1数量: {initial_ask_amount}, "
                f"初始状态: {'买单多' if initial_side == 'bid_more' else '卖单多'}, "
                f"超时: {timeout}秒, 价格变化次数要求: {self.config.market_price_change_count}")
        else:
            self.logger.info(
                f"📊 等待价格变化({self.config.orderbook_method.upper()}) - "
                f"初始买1: {initial_bid}, 初始卖1: {initial_ask}, "
                f"超时: {timeout}秒, 价格变化次数要求: {self.config.market_price_change_count}")

        # 🔥 价格变化次数统计
        price_change_count = 0
        last_bid = initial_bid
        last_ask = initial_ask
        required_count = self.config.market_price_change_count

        try:
            while (datetime.now() - start_time).total_seconds() < timeout:
                # 🔥 根据配置选择获取方式
                if self.config.orderbook_method == "websocket":
                    # WebSocket 模式：使用缓存的订单簿
                    if self._latest_orderbook is None:
                        await asyncio.sleep(check_interval)
                        continue
                    orderbook = self._latest_orderbook
                else:
                    # REST API 模式：实时获取订单簿
                    orderbook = await self.adapter.get_orderbook(self.config.symbol)

                if not orderbook.bids or not orderbook.asks:
                    await asyncio.sleep(check_interval)
                    continue

                current_bid = orderbook.bids[0].price
                current_ask = orderbook.asks[0].price

                # 🔥 检查订单簿数量反转（如果启用）
                # 数量反转立即触发，不受价格变化次数限制
                if check_reversal:
                    current_bid_amount = orderbook.bids[0].size
                    current_ask_amount = orderbook.asks[0].size
                    current_side = "bid_more" if current_bid_amount > current_ask_amount else "ask_more"

                    if current_side != initial_side:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        self.logger.info(
                            f"✅ 订单簿数量发生反转({self.config.orderbook_method.upper()}) - "
                            f"初始: {'买单多' if initial_side == 'bid_more' else '卖单多'}, "
                            f"当前: {'买单多' if current_side == 'bid_more' else '卖单多'}, "
                            f"买1数量: {current_bid_amount}, 卖1数量: {current_ask_amount} "
                            f"(耗时: {elapsed:.2f}秒) [平仓原因: 数量反转]")
                        return (True, elapsed, "quantity_reversal")

                # 🔥 检查价格是否相对于上一次变化
                if current_bid != last_bid or current_ask != last_ask:
                    price_change_count += 1
                    self.logger.info(
                        f"📈 价格变化 #{price_change_count}/{required_count} - "
                        f"买1: {last_bid} → {current_bid}, "
                        f"卖1: {last_ask} → {current_ask}")

                    # 更新上一次的价格
                    last_bid = current_bid
                    last_ask = current_ask

                    # 🔥 达到要求的变化次数，触发平仓
                    if price_change_count >= required_count:
                        elapsed = (datetime.now() - start_time).total_seconds()
                        self.logger.info(
                            f"✅ 价格变化达到要求次数({self.config.orderbook_method.upper()}) - "
                            f"变化{price_change_count}次 >= 要求{required_count}次 "
                            f"(耗时: {elapsed:.2f}秒) [平仓原因: 价格变化]")
                        return (True, elapsed, "price_change")

                await asyncio.sleep(check_interval)

            # 超时
            elapsed = (datetime.now() - start_time).total_seconds()
            self.logger.warning(
                f"⚠️ 等待价格变化超时（{timeout}秒，{self.config.orderbook_method.upper()}模式），继续平仓 [平仓原因: 超时]")
            return (True, elapsed, "timeout")  # 即使超时也返回True继续平仓

        except Exception as e:
            elapsed = (datetime.now() - start_time).total_seconds()
            self.logger.error(
                f"❌ 监控价格变化失败({self.config.orderbook_method.upper()}): {e} [平仓原因: 异常]")
            return (True, elapsed, "error")  # 出错也继续平仓

    async def _ws_reconnect_loop(self) -> None:
        """
        WebSocket 重连循环任务

        持续检查WebSocket健康状态，并在需要时重连

        参考网格脚本的实现：
        1. 优先检查连接状态（_ws_connected）
        2. 其次检查心跳超时（_last_heartbeat，120秒）
        3. 最后检查消息时间（仅提示，300秒）
        """
        self.logger.info("🔄 WebSocket 重连任务已启动")
        check_count = 0
        import time

        while not self._should_stop:
            try:
                await asyncio.sleep(self._ws_reconnect_interval)
                check_count += 1
                current_time = time.time()

                # 每30秒（第3次检查）输出一次状态信息
                if check_count % 3 == 0:
                    # 根据配置显示正确的状态
                    if self.config.fill_price_method == "websocket":
                        order_status = "健康" if self._ws_order_healthy else "不健康"
                    else:
                        order_status = "REST模式"

                    if self.config.orderbook_method == "websocket":
                        orderbook_status = "健康" if self._ws_orderbook_healthy else "不健康"
                    else:
                        orderbook_status = "REST模式"

                    self.logger.info(
                        f"💓 WebSocket 健康检查 (#{check_count}) - "
                        f"订单: {order_status}, 订单簿: {orderbook_status}")

                # 🔥 检查订单WebSocket健康状态（参考网格脚本）
                if self.config.fill_price_method == "websocket":
                    if not self._ws_order_healthy or not self._ws_order_subscribed:
                        self.logger.warning("⚠️ 检测到 WebSocket 订单连接异常，尝试重连...")
                        try:
                            # 重置状态
                            self._ws_order_subscribed = False
                            self._ws_order_healthy = False

                            # 重新订阅
                            await self._subscribe_order_updates()
                            if self._ws_order_subscribed and self._ws_order_healthy:
                                self.logger.info("✅ WebSocket 订单重连成功")
                        except Exception as e:
                            self.logger.error(
                                f"❌ WebSocket 订单重连失败: {e}", exc_info=True)
                    else:
                        # 🔥 三层检查机制（参考网格脚本）

                        # 步骤1: 检查连接状态（优先级最高）
                        ws_connected = True
                        if hasattr(self.adapter, '_ws_connected'):
                            ws_connected = self.adapter._ws_connected

                        if not ws_connected:
                            self.logger.error(
                                f"❌ WebSocket订单连接断开，标记为不健康")
                            self._ws_order_healthy = False
                            continue

                        # 步骤2: 检查心跳超时（120秒）
                        heartbeat_age = 0
                        if hasattr(self.adapter, '_last_heartbeat'):
                            last_heartbeat = self.adapter._last_heartbeat
                            # 处理可能的datetime对象
                            if isinstance(last_heartbeat, datetime):
                                last_heartbeat = last_heartbeat.timestamp()
                            heartbeat_age = current_time - last_heartbeat

                            if heartbeat_age > 120:  # 2分钟心跳超时
                                self.logger.error(
                                    f"❌ WebSocket订单心跳超时（{heartbeat_age:.0f}秒未更新），"
                                    f"标记为不健康")
                                self._ws_order_healthy = False
                                continue

                        # 步骤3: 消息时间仅提示（不标记为不健康）
                        if self._ws_order_last_message_time:
                            message_age = (
                                datetime.now() - self._ws_order_last_message_time).total_seconds()
                            # 💡 只在极长时间（如5分钟）无消息时提示，但不标记为不健康
                            if message_age > 300 and check_count % 30 == 0:
                                self.logger.info(
                                    f"💡 提示: {message_age:.0f}秒未收到订单更新 "
                                    f"(无订单成交时的正常现象，连接仍然健康)")

                        # 连接和心跳都正常
                        if check_count % 3 == 0 and heartbeat_age > 0:
                            message_age = (datetime.now() - self._ws_order_last_message_time).total_seconds(
                            ) if self._ws_order_last_message_time else 0
                            self.logger.debug(
                                f"💓 WebSocket订单健康: 连接正常, 心跳 {heartbeat_age:.0f}秒前, "
                                f"消息 {message_age:.0f}秒前")

                # 🔥 检查订单簿WebSocket健康状态（参考网格脚本）
                if self.config.orderbook_method == "websocket":
                    if not self._ws_orderbook_healthy or not self._ws_orderbook_subscribed:
                        self.logger.warning("⚠️ 检测到 WebSocket 订单簿连接异常，尝试重连...")
                        try:
                            # 重置状态
                            self._ws_orderbook_subscribed = False
                            self._ws_orderbook_healthy = False

                            # 重新订阅
                            await self._subscribe_orderbook()
                            if self._ws_orderbook_subscribed and self._ws_orderbook_healthy:
                                self.logger.info("✅ WebSocket 订单簿重连成功")
                        except Exception as e:
                            self.logger.error(
                                f"❌ WebSocket 订单簿重连失败: {e}", exc_info=True)
                    else:
                        # 🔥 三层检查机制（参考网格脚本）

                        # 步骤1: 检查连接状态（优先级最高）
                        ws_connected = True
                        if hasattr(self.adapter, '_ws_connected'):
                            ws_connected = self.adapter._ws_connected

                        if not ws_connected:
                            self.logger.error(
                                f"❌ WebSocket订单簿连接断开，标记为不健康")
                            self._ws_orderbook_healthy = False
                            continue

                        # 步骤2: 检查心跳超时（120秒）
                        heartbeat_age = 0
                        if hasattr(self.adapter, '_last_heartbeat'):
                            last_heartbeat = self.adapter._last_heartbeat
                            # 处理可能的datetime对象
                            if isinstance(last_heartbeat, datetime):
                                last_heartbeat = last_heartbeat.timestamp()
                            heartbeat_age = current_time - last_heartbeat

                            if heartbeat_age > 120:  # 2分钟心跳超时
                                self.logger.error(
                                    f"❌ WebSocket订单簿心跳超时（{heartbeat_age:.0f}秒未更新），"
                                    f"标记为不健康")
                                self._ws_orderbook_healthy = False
                                continue

                        # 步骤3: 消息时间仅提示（不标记为不健康）
                        if self._ws_orderbook_last_message_time:
                            message_age = (
                                datetime.now() - self._ws_orderbook_last_message_time).total_seconds()
                            # 订单簿推送频率较高，300秒提示即可
                            if message_age > 300 and check_count % 30 == 0:
                                self.logger.info(
                                    f"💡 提示: {message_age:.0f}秒未收到订单簿更新 "
                                    f"(市场冷清时的正常现象，连接仍然健康)")

                        # 连接和心跳都正常
                        if check_count % 3 == 0 and heartbeat_age > 0:
                            message_age = (datetime.now() - self._ws_orderbook_last_message_time).total_seconds(
                            ) if self._ws_orderbook_last_message_time else 0
                            self.logger.debug(
                                f"💓 WebSocket订单簿健康: 连接正常, 心跳 {heartbeat_age:.0f}秒前, "
                                f"消息 {message_age:.0f}秒前")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ WebSocket 重连任务出错: {e}", exc_info=True)

        self.logger.info("🔄 WebSocket 重连任务已停止")

    async def _cleanup_current_cycle(self) -> None:
        """清理当前轮次"""
        # 取消所有订单
        await self._cancel_both_orders()

        # 重置状态
        self._current_buy_order = None
        self._current_sell_order = None
        self._current_position = Decimal("0")
