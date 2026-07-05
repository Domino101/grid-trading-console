"""
APR计算器 - APR Calculator

计算预估年化APR
"""

from decimal import Decimal


class APRCalculator:
    """
    APR计算器

    公式 (2026-06-27 修正):
        APR(%) = (grid_interval_percent − fee_rate_percent) × cycles_per_hour × 8760

    说明:
        - grid_interval_percent 与 fee_rate_percent 均为「百分比数值」
          (例如 0.5 代表 0.5%,而非 0.005)
        - cycles_per_hour 为滚动窗口推算的每小时循环次数
        - 8760 = 24 × 365(每年小时数)
        - 结果单位为 % (年化)

    推导:
        单次完整循环净利 = 仓位价值 × (interval% − fee%) / 100
        以「仓位价值」为基数,单次 ROI = interval% − fee%
        年化 = 单次 ROI × 年循环次数 = 单次 ROI × cycles_per_hour × 8760

    ⚠️ 旧公式错误:
        旧版乘以 (interval × interval / width),把「仓位占总资金比例」误当收益率
        ⇒ BTC 主流币被低估 ~670 倍,导致评分永远落不到 S 级
    """

    # 固定参数
    ORDER_VALUE_USDC = Decimal('10')         # 每格订单价值(默认值,运行时可被 scanner_config 覆盖)
    FEE_RATE_PERCENT = Decimal('0.004')      # 默认双边手续费率(%),0.02% × 2
    HOURS_PER_YEAR = Decimal('8760')         # 一年小时数

    @staticmethod
    def calculate(
        grid_interval_percent: Decimal,
        grid_width_percent: Decimal,
        cycles_per_hour: Decimal,
        fee_rate_percent: Decimal = FEE_RATE_PERCENT
    ) -> Decimal:
        """
        计算预估APR (修正版)

        Args:
            grid_interval_percent: 格子间距百分比(数值,如 0.5 代表 0.5%)
            grid_width_percent: 网格总宽度百分比(保留参数,新公式不再使用,但维持向后兼容)
            cycles_per_hour: 每小时循环次数
            fee_rate_percent: 双边手续费率(%),默认 0.004

        Returns:
            年化APR(%)

        公式:
            APR(%) = (interval% − fee%) × cycles_per_hour × 8760

        示例:
            interval = 0.5%
            fee = 0.004%
            cycles = 10/小时
            ⇒ APR = (0.5 − 0.004) × 10 × 8760 = 43,449.6%
        """
        # 计算净收益率(单次循环,单位 %)
        net_profit_rate = grid_interval_percent - fee_rate_percent

        if net_profit_rate <= 0:
            return Decimal('0')  # 手续费超过利润，无法盈利

        # 修正后的年化公式(去除 interval × interval / width 冗余因子)
        annual_apr = net_profit_rate * cycles_per_hour * APRCalculator.HOURS_PER_YEAR

        # 防御上限,避免 UI 显示溢出
        if annual_apr > Decimal('999999'):
            annual_apr = Decimal('999999')

        return annual_apr
    
    @staticmethod
    def calculate_total_capital(
        grid_width_percent: Decimal,
        grid_interval_percent: Decimal
    ) -> Decimal:
        """
        计算总投入本金
        
        Args:
            grid_width_percent: 网格总宽度百分比
            grid_interval_percent: 格子间距百分比
            
        Returns:
            总投入本金（USDC）
            
        公式：
            网格数量 = grid_width_percent / grid_interval_percent
            总本金 = ORDER_VALUE_USDC × 网格数量
        """
        grid_count = grid_width_percent / grid_interval_percent
        total_capital = APRCalculator.ORDER_VALUE_USDC * grid_count
        return total_capital
    
    @staticmethod
    def calculate_profit_per_cycle(
        grid_interval_percent: Decimal
    ) -> Decimal:
        """
        计算单次循环收益
        
        Args:
            grid_interval_percent: 格子间距百分比
            
        Returns:
            单次循环净收益（USDC）
            
        公式：
            净收益 = ORDER_VALUE_USDC × (grid_interval_percent - FEE_RATE_PERCENT) / 100
        """
        net_rate = (grid_interval_percent - APRCalculator.FEE_RATE_PERCENT) / Decimal('100')
        profit = APRCalculator.ORDER_VALUE_USDC * net_rate
        return profit

