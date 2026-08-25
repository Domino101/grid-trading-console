"""
网格策略实现

实现网格策略的核心逻辑：
- 做多网格（Long Grid）
- 做空网格（Short Grid）
- 统一的反向挂单机制
"""

from typing import List, Tuple, Optional
from decimal import Decimal
from datetime import datetime

from ....logging import get_logger
from ..interfaces.grid_strategy import IGridStrategy
from ..models import (
    GridConfig, GridOrder, GridOrderSide, GridOrderStatus,
    GridType
)


class GridStrategyImpl(IGridStrategy):
    """
    网格策略实现

    核心原则：
    1. 做多和做空网格本质相同，只是初始化方向不同
    2. 任何订单成交后立即挂反向订单
    3. 买单成交 → 向上移动一格挂卖单
    4. 卖单成交 → 向下移动一格挂买单
    """

    def __init__(self):
        self.logger = get_logger(__name__)
        self.config: GridConfig = None
        self.grid_prices: List[Decimal] = []

    def initialize(self, config: GridConfig) -> List[GridOrder]:
        """
        初始化网格 - 一次性生成所有网格订单

        做多网格：为每个网格价格挂买单（200个买单）
        做空网格：为每个网格价格挂卖单（200个卖单）

        Args:
            config: 网格配置

        Returns:
            所有网格的初始订单列表（200个订单）
        """
        self.config = config
        self.grid_prices = self._calculate_grid_prices()

        # 🔥 价格移动网格：价格区间在运行时动态设置
        if config.is_follow_mode():
            self.logger.info(
                f"初始化{config.grid_type.value}网格: "
                f"区间[动态跟随], "
                f"间隔{config.grid_interval}, {config.grid_count}个网格"
            )
        else:
            self.logger.info(
                f"初始化{config.grid_type.value}网格: "
                f"区间[{config.lower_price}, {config.upper_price}], "
                f"间隔{config.grid_interval}, {config.grid_count}个网格"
            )

        # 为所有网格创建初始订单
        all_orders = self._create_all_initial_orders()

        self.logger.info(f"生成{len(all_orders)}个初始订单，准备批量挂单")

        return all_orders

    def _calculate_grid_prices(self) -> List[Decimal]:
        """
        计算所有网格价格

        Returns:
            价格列表（按网格ID排序）
        """
        prices = []
        for grid_id in range(1, self.config.grid_count + 1):
            price = self.config.get_grid_price(grid_id)
            prices.append(price)

        return prices

    def _create_all_initial_orders(self) -> List[GridOrder]:
        """
        创建所有网格的初始订单

        做多网格：为每个网格价格创建买单
        做空网格：为每个网格价格创建卖单

        Returns:
            所有网格的初始订单列表
        """
        all_orders = []

        if self.config.grid_type in [GridType.LONG, GridType.MARTINGALE_LONG, GridType.FOLLOW_LONG]:
            # 做多网格：为每个网格挂买单（包括普通、马丁、价格移动）
            for grid_id in range(1, self.config.grid_count + 1):
                price = self.config.get_grid_price(grid_id)
                # 🔥 使用格式化后的金额（符合交易所精度）
                amount = self.config.get_formatted_grid_order_amount(grid_id)

                order = GridOrder(
                    order_id="",  # 等待执行引擎填充
                    grid_id=grid_id,
                    side=GridOrderSide.BUY,
                    price=price,
                    amount=amount,  # 格式化后的金额
                    status=GridOrderStatus.PENDING,
                    created_at=datetime.now()
                )
                all_orders.append(order)

            self.logger.info(
                f"做多网格：生成{len(all_orders)}个买单，"
                f"价格范围 ${all_orders[0].price:,.2f} - ${all_orders[-1].price:,.2f}"
            )

        elif self.config.grid_type == GridType.NEUTRAL:
            # 中性网格：初始挂单逻辑
            # 1. 获以当前价格为中心
            # 2. 上方挂卖单，下方挂买单
            
            # 由于 initialize 方法没有当前价格信息，我们需要在 coordinator 中处理，
            # 或者在这里生成所有可能的订单，但标记为 PENDING，由 coordinator 决定挂哪些。
            # 但根据用户需求 "中性网格预设是没有订单的"，我们这里返回空列表。
            # 等待后续价格移动或用户手动触发。
            
            # 修正：虽然初始化时不挂单，但我们需要计算好网格价格，以便后续使用。
            # _calculate_grid_prices 已经计算了所有价格。
            
            self.logger.info("中性网格：初始不生成订单（等待价格触发或手动下单）")
            # 返回空列表，确保启动时不挂单
            pass

        else:  # SHORT, MARTINGALE_SHORT, FOLLOW_SHORT
            # 做空网格：为每个网格挂卖单（包括普通、马丁、价格移动）
            for grid_id in range(1, self.config.grid_count + 1):
                price = self.config.get_grid_price(grid_id)
                # 🔥 使用格式化后的金额（符合交易所精度）
                amount = self.config.get_formatted_grid_order_amount(grid_id)

                order = GridOrder(
                    order_id="",  # 等待执行引擎填充
                    grid_id=grid_id,
                    side=GridOrderSide.SELL,
                    price=price,
                    amount=amount,  # 格式化后的金额
                    status=GridOrderStatus.PENDING,
                    created_at=datetime.now()
                )
                all_orders.append(order)

            self.logger.info(
                f"做空网格：生成{len(all_orders)}个卖单，"
                f"价格范围 ${all_orders[0].price:,.2f} - ${all_orders[-1].price:,.2f}"
            )

        return all_orders

    def calculate_reverse_order(
        self,
        filled_order: GridOrder,
        grid_interval: Optional[Decimal] = None,
        distance: int = 1,
        interval: Optional[Decimal] = None,
        **kwargs
    ) -> Tuple[GridOrderSide, Decimal, int]:
        """
        计算反向订单参数

        核心逻辑：
        - 买单成交 → 向上移动N格挂卖单
        - 卖单成交 → 向下移动N格挂买单

        Args:
            filled_order: 已成交订单
            grid_interval: 网格间隔（兼容 interval 参数）
            distance: 反手挂单的格子距离（默认1格）
            interval: 兼容別名

        Returns:
            (订单方向, 价格, 网格ID)
        """
        eff_interval = grid_interval if grid_interval is not None else (
            interval if interval is not None else (
                self.config.grid_interval if self.config else Decimal('0')
            )
        )

        if filled_order.is_buy_order():
            # 买单成交 → 挂卖单
            new_side = GridOrderSide.SELL
            # 🔥 关键修复：使用【下单价格】而非【成交价格】计算反手价格
            # 这样可以保证网格间距的一致性，避免市价成交导致间距错乱
            new_price = filled_order.price + (eff_interval * distance)
            if self.config:
                new_grid_id = self.config.get_grid_index_by_price(new_price)
            else:
                new_grid_id = filled_order.grid_id + distance

            self.logger.debug(
                f"买单成交 (下单价@{filled_order.price}, 成交价@{filled_order.filled_price}), "
                f"挂卖单@{new_price} (Grid {new_grid_id}, 向上移动{distance}格)"
            )
        else:
            # 卖单成交 → 挂买单
            new_side = GridOrderSide.BUY
            # 🔥 关键修复：使用【下单价格】而非【成交价格】计算反手价格
            new_price = filled_order.price - (eff_interval * distance)
            if self.config:
                new_grid_id = self.config.get_grid_index_by_price(new_price)
            else:
                new_grid_id = filled_order.grid_id - distance

            self.logger.debug(
                f"卖单成交 (下单价@{filled_order.price}, 成交价@{filled_order.filled_price}), "
                f"挂买单@{new_price} (Grid {new_grid_id}, 向下移动{distance}格)"
            )

        return (new_side, new_price, new_grid_id)

    def calculate_batch_reverse_orders(
        self,
        filled_orders: List[GridOrder],
        grid_interval: Optional[Decimal] = None,
        distance: int = 1,
        interval: Optional[Decimal] = None,
        **kwargs
    ) -> List[Tuple[GridOrderSide, Decimal, int, Decimal]]:
        """
        批量计算反向订单参数

        用于处理多个订单同时成交的情况

        Args:
            filled_orders: 已成交订单列表
            grid_interval: 网格间隔
            distance: 反手挂单的格子距离（默认1格）
            interval: 兼容別名

        Returns:
            [(订单方向, 价格, 网格ID, 数量), ...]
        """
        eff_interval = grid_interval if grid_interval is not None else (
            interval if interval is not None else (
                self.config.grid_interval if self.config else Decimal('0')
            )
        )
        reverse_orders = []

        for order in filled_orders:
            side, price, grid_id = self.calculate_reverse_order(
                order, eff_interval, distance)
            # 数量与成交订单保持一致
            amount = order.filled_amount or order.amount
            reverse_orders.append((side, price, grid_id, amount))

        self.logger.info(
            f"批量成交: {len(filled_orders)}个订单, "
            f"准备挂{len(reverse_orders)}个反向订单（距离{distance}格）"
        )

        return reverse_orders

    def get_grid_prices(self) -> List[Decimal]:
        """获取所有网格价格"""
        return self.grid_prices.copy()

    def validate_price_range(self, current_price: Decimal) -> bool:
        """
        验证当前价格是否在网格区间内

        Args:
            current_price: 当前价格

        Returns:
            是否在区间内
        """
        in_range = self.config.is_price_in_range(current_price)

        if not in_range:
            self.logger.warning(
                f"价格{current_price}超出网格区间"
                f"[{self.config.lower_price}, {self.config.upper_price}]"
            )

        return in_range

    def get_grid_id_by_price(self, price: Decimal) -> int:
        """
        根据价格获取网格ID

        Args:
            price: 价格

        Returns:
            网格ID
        """
        return self.config.get_grid_index_by_price(price)

    def __repr__(self) -> str:
        if self.config:
            return (
                f"GridStrategy({self.config.grid_type.value}, "
                f"{self.config.grid_count} grids)"
            )
        return "GridStrategy(not initialized)"
