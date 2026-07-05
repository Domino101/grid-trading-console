"""
模拟结果模型 - Simulation Result Model

存储和管理网格模拟的最终结果
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class SimulationResult:
    """
    网格模拟结果

    用于存储和展示每个交易对的模拟结果
    """

    # 基础信息
    symbol: str                              # 交易对符号
    current_price: Decimal                   # 当前价格

    # 网格配置
    grid_width_percent: Decimal              # 网格宽度（%）
    grid_interval_percent: Decimal           # 格子间距（%）
    grid_count: int                          # 网格数量
    price_range: str                         # 价格区间（格式化字符串）

    # 统计数据
    running_seconds: int                     # 运行时长（秒）
    total_crosses: int                       # 总穿越次数
    buy_crosses: int                         # 买入穿越
    sell_crosses: int                        # 卖出穿越
    complete_cycles: int                     # 完整循环次数
    cycles_per_hour: Decimal                 # 每小时循环次数
    avg_cycles_per_5min: Decimal             # 平均5分钟循环次数（基于总运行时间）
    recent_5min_cycles: int                  # 最近5分钟的循环次数

    # APR预估
    estimated_apr: Decimal                   # 预估年化APR（%）

    # 市场数据
    volume_24h_usdc: Decimal                 # 24小时成交量（USDC）
    price_change_24h_percent: Decimal        # 24小时价格变化（%）

    # 评级
    rating: str = ""                         # 评级（S/A/B/C/D）
    score: float = 0.0                       # 综合评分（0-100）
    s_rating_duration_str: str = "--"        # S级持续时间（格式: D/H/M）
    order_value_usdc: Decimal = Decimal('10')  # 每格订单价值 (2026-06-27 新增,供资金占用评分用;放最末以免破壞 dataclass 欄位順序)

    def calculate_rating(self) -> str:
        """
        根据APR计算评级

        评级标准 (2026-06-27 调整,与新 APR 公式对齐):
        - S级: APR >= 1000%(极度推荐,只出现在高频机会)
        - A级: APR >= 300% (强烈推荐,对齐 apr_alert_threshold)
        - B级: APR >= 100% (推荐,主流币常态)
        - C级: APR >= 30%  (可考虑,低频策略)
        - D级: APR < 30%   (不推荐)

        旧阈值 (500/300/150/50) 在新公式下过于宽松:
        - 旧公式 BTC(0.03%/20%)@50 次/时 ≈ 11% APR(被严重低估)
        - 新公式同条件 ≈ 11,388% APR(数学正确)
        因此旧 S 级 (500%) 在新公式下等于「不限制」,失去筛选意义。

        Returns:
            评级字符串（带emoji）
        """
        apr = float(self.estimated_apr)

        if apr >= 1000:
            self.rating = "🔥 S"
            self.score = 95.0
        elif apr >= 300:
            self.rating = "⭐ A"
            self.score = 85.0
        elif apr >= 100:
            self.rating = "✅ B"
            self.score = 75.0
        elif apr >= 30:
            self.rating = "🟡 C"
            self.score = 60.0
        else:
            self.rating = "❌ D"
            self.score = 40.0

        # === 修正后的微调规则 (2026-06-27) ===
        # 旧规则问题:cycles_per_hour >50 加 5 分 / <5 扣 10 分(不对称 + 惩罚低频)
        # 新规则:删除循环频率的奖惩(频率已通过 APR 体现),
        #         成交量与资金占用采用对称的小幅微调。

        # 流动性微调(对称, 2026-06-27)
        if self.volume_24h_usdc > 0:
            volume_millions = float(self.volume_24h_usdc) / 1_000_000
            if volume_millions >= 10:
                self.score += 5.0   # 高流动性加分
            elif volume_millions < 1:
                self.score -= 5.0   # 低流动性温和扣分(原 -10 过重)

        # 资金占用可行性微调(2026-06-27 新增)
        # 总本金 = order_value_usdc × grid_count
        # 零售资金大多在 $5k 以下,$10k 以上的网格开起来太重,扣分避免推荐
        order_value = float(self.order_value_usdc) if hasattr(self, 'order_value_usdc') else 10.0
        total_capital = order_value * self.grid_count
        if total_capital > 50000:
            self.score -= 20.0
        elif total_capital > 10000:
            self.score -= 10.0
        elif total_capital > 5000:
            self.score -= 5.0
        # < $5k 不扣分(适合零售)

        # 限制评分范围
        self.score = max(0.0, min(100.0, self.score))

        return self.rating

    def get_running_time_str(self) -> str:
        """获取运行时长（格式化字符串）"""
        hours = self.running_seconds // 3600
        minutes = (self.running_seconds % 3600) // 60
        seconds = self.running_seconds % 60

        if hours > 0:
            return f"{hours}时{minutes}分"
        elif minutes > 0:
            return f"{minutes}分{seconds}秒"
        else:
            return f"{seconds}秒"

    def get_volume_str(self) -> str:
        """获取成交量（格式化字符串）"""
        volume = float(self.volume_24h_usdc)

        if volume >= 1_000_000:
            return f"${volume/1_000_000:.2f}M"
        elif volume >= 1_000:
            return f"${volume/1_000:.2f}K"
        else:
            return f"${volume:.0f}"

    @classmethod
    def from_virtual_grid(
        cls,
        grid: 'VirtualGrid',
        order_value_usdc: Decimal = Decimal('10')
    ) -> 'SimulationResult':
        """
        从虚拟网格创建模拟结果

        Args:
            grid: 虚拟网格对象
            order_value_usdc: 每格订单价值（USDC），2026-06-27 新增参数

        Returns:
            SimulationResult对象
        """
        result = cls(
            symbol=grid.symbol,
            current_price=grid.current_price,
            grid_width_percent=grid.grid_width_percent,
            grid_interval_percent=grid.grid_interval_percent,
            grid_count=grid.grid_count,
            price_range=f'${grid.lower_price:,.2f} - ${grid.upper_price:,.2f}',
            running_seconds=grid.get_running_time_seconds(),
            total_crosses=grid.total_crosses,
            buy_crosses=grid.buy_crosses,
            sell_crosses=grid.sell_crosses,
            complete_cycles=grid.complete_cycles,
            cycles_per_hour=grid.cycles_per_hour,
            avg_cycles_per_5min=grid.get_avg_cycles_per_5min(),
            recent_5min_cycles=grid.get_recent_5min_cycles(),
            estimated_apr=grid.estimated_apr,
            order_value_usdc=order_value_usdc,
            volume_24h_usdc=grid.volume_24h_usdc,
            price_change_24h_percent=grid.price_change_24h_percent,
        )

        # 计算评级
        result.calculate_rating()

        # 🔥 更新VirtualGrid的评级并获取S级持续时间
        # 🔥 修复 P2-12：直接调用实时计算方法，而非读缓存字段
        grid.update_rating(result.rating)
        result.s_rating_duration_str = grid.get_s_rating_duration_str()

        return result

    def __str__(self) -> str:
        """字符串表示"""
        return (
            f"{self.symbol}: "
            f"APR={self.estimated_apr:.2f}%, "
            f"循环={self.complete_cycles}次, "
            f"评级={self.rating}"
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'symbol': self.symbol,
            'current_price': float(self.current_price),
            'grid_width': f'{self.grid_width_percent}%',
            'grid_interval': f'{self.grid_interval_percent}%',
            'grid_count': self.grid_count,
            'price_range': self.price_range,
            'running_time': self.get_running_time_str(),
            'total_crosses': self.total_crosses,
            'buy_crosses': self.buy_crosses,
            'sell_crosses': self.sell_crosses,
            'complete_cycles': self.complete_cycles,
            'cycles_per_hour': float(self.cycles_per_hour),
            'estimated_apr': float(self.estimated_apr),
            'volume_24h': self.get_volume_str(),
            'price_change_24h': f'{self.price_change_24h_percent:+.2f}%',
            'rating': self.rating,
            'score': self.score,
        }
