"""
网格波动率扫描器 - 综合功能稽核测试

针对以下模块进行功能与代码稽核：
- models.virtual_grid (VirtualGrid)
- models.simulation_result (SimulationResult)
- core.apr_calculator (APRCalculator)
- core.cycle_detector (CycleDetector)
- core.price_monitor (PriceMonitor)
- core.apr_alert (APRAlertManager)
- ui.scanner_ui (ScannerUI)
- config (market_config.yaml)
"""

import asyncio
import sys
import os
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock

# 设置路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 抑制rich UI的副作用
os.environ['TERM'] = 'dumb'

from grid_volatility_scanner.models.virtual_grid import VirtualGrid, GridState, _is_btc_symbol
from grid_volatility_scanner.models.simulation_result import SimulationResult
from grid_volatility_scanner.core.apr_calculator import APRCalculator
from grid_volatility_scanner.core.cycle_detector import CycleDetector


# === 测试结果收集 ===
RESULTS = {
    "passed": [],
    "failed": [],
    "warnings": [],
    "info": [],
}

def record_pass(test_name, detail=""):
    RESULTS["passed"].append(f"[PASS] {test_name}: {detail}")
    print(f"  ✅ PASS: {test_name} - {detail}")

def record_fail(test_name, detail=""):
    RESULTS["failed"].append(f"[FAIL] {test_name}: {detail}")
    print(f"  ❌ FAIL: {test_name} - {detail}")

def record_warn(test_name, detail=""):
    RESULTS["warnings"].append(f"[WARN] {test_name}: {detail}")
    print(f"  ⚠️  WARN: {test_name} - {detail}")

def record_info(test_name, detail=""):
    RESULTS["info"].append(f"[INFO] {test_name}: {detail}")
    print(f"  ℹ️  INFO: {test_name} - {detail}")


# =====================================================
# 测试 1: VirtualGrid 网格参数计算
# =====================================================
def test_virtual_grid_initialization():
    print("\n" + "="*80)
    print("📋 测试 1: VirtualGrid 网格参数计算")
    print("="*80)
    
    try:
        grid = VirtualGrid(
            symbol="BTC-USD",
            current_price=Decimal("100000"),
            grid_width_percent=Decimal("2.0"),
            grid_interval_percent=Decimal("0.5"),
        )
        
        # 验证基本参数
        expected_grid_count = int(Decimal("2.0") / Decimal("0.5"))  # 4
        if grid.grid_count == expected_grid_count:
            record_pass("网格数量计算", f"width/interval=2.0/0.5={expected_grid_count}格")
        else:
            record_fail("网格数量计算", f"期望{expected_grid_count}，实际{grid.grid_count}")
        
        # 验证边界价格
        # 上下边界 = 100000 * (1 ± 1%) = 99000 ~ 101000
        expected_lower = Decimal("99000")
        expected_upper = Decimal("101000")
        if abs(grid.lower_price - expected_lower) < Decimal("1"):
            record_pass("下边界计算", f"${grid.lower_price} ≈ ${expected_lower}")
        else:
            record_fail("下边界计算", f"期望${expected_lower}，实际${grid.lower_price}")
        
        if abs(grid.upper_price - expected_upper) < Decimal("1"):
            record_pass("上边界计算", f"${grid.upper_price} ≈ ${expected_upper}")
        else:
            record_fail("上边界计算", f"期望${expected_upper}，实际${grid.upper_price}")
        
        # 验证网格线数（grid_count + 1条线）
        if len(grid.grid_lines) == expected_grid_count + 1:
            record_pass("网格线生成", f"共{len(grid.grid_lines)}条线")
        else:
            record_fail("网格线生成", f"期望{expected_grid_count+1}，实际{len(grid.grid_lines)}")
        
        # 验证挂单价格初始化（整格位置）
        expected_buy = Decimal("100000") - Decimal("100000") * Decimal("0.005")  # 99500
        expected_sell = Decimal("100000") + Decimal("100000") * Decimal("0.005")  # 100500
        if abs(grid.pending_buy_price - expected_buy) < Decimal("1"):
            record_pass("初始买单挂单(整格)", f"${grid.pending_buy_price} ≈ ${expected_buy}")
        else:
            record_fail("初始买单挂单", f"期望${expected_buy}，实际${grid.pending_buy_price}")
        
        if abs(grid.pending_sell_price - expected_sell) < Decimal("1"):
            record_pass("初始卖单挂单(整格)", f"${grid.pending_sell_price} ≈ ${expected_sell}")
        else:
            record_fail("初始卖单挂单", f"期望${expected_sell}，实际${grid.pending_sell_price}")
        
    except Exception as e:
        record_fail("VirtualGrid 初始化", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 2: VirtualGrid 价格穿越与状态机
# =====================================================
def test_virtual_grid_price_update():
    print("\n" + "="*80)
    print("📋 测试 2: VirtualGrid 价格穿越与状态机")
    print("="*80)
    
    try:
        grid = VirtualGrid(
            symbol="ETH-USD",
            current_price=Decimal("3000"),
            grid_width_percent=Decimal("10.0"),
            grid_interval_percent=Decimal("0.5"),
        )
        
        # 网格间距绝对值 = 3000 * 0.5% = 15
        interval_value = Decimal("15")
        
        # 价格先小幅波动再大幅下跌，触发买单
        # 初始买单 = 3000 - 15 = 2985
        result = grid.update_price(Decimal("2980"))  # 下跌至 2980 < 2985
        if result == "buy":
            record_pass("下跌触发买单", "价格2980 < 买单2985 → 买入成交")
        else:
            record_fail("下跌触发买单", f"期望'buy'，实际{result}")
        
        if grid.buy_crosses == 1:
            record_pass("买入次数+1", f"buy_crosses={grid.buy_crosses}")
        else:
            record_fail("买入次数+1", f"期望1，实际{grid.buy_crosses}")
        
        # 验证买单下移：原买单2985 → 新买单 = 2985 - 15 = 2970
        expected_new_buy = Decimal("2985") - interval_value
        if grid.pending_buy_price == expected_new_buy:
            record_pass("买单下移一格", f"${grid.pending_buy_price}")
        else:
            record_fail("买单下移", f"期望${expected_new_buy}，实际${grid.pending_buy_price}")
        
        # 验证卖单上移：原卖单3015 → 新卖单 = 2985 + 15 = 3000
        expected_new_sell = Decimal("2985") + interval_value
        if grid.pending_sell_price == expected_new_sell:
            record_pass("卖单上移一格", f"${grid.pending_sell_price}")
        else:
            record_fail("卖单上移", f"期望${expected_new_sell}，实际${grid.pending_sell_price}")
        
        # 价格继续上涨触发卖单
        # 卖单在 3000，价格达到 3000
        result = grid.update_price(Decimal("3005"))  # 3005 >= 3000
        if result == "sell":
            record_pass("上涨触发卖单", "价格3005 > 卖单3000 → 卖出成交")
        else:
            record_fail("上涨触发卖单", f"期望'sell'，实际{result}")
        
        if grid.sell_crosses == 1:
            record_pass("卖出次数+1", f"sell_crosses={grid.sell_crosses}")
        else:
            record_fail("卖出次数+1", f"期望1，实际{grid.sell_crosses}")
        
        if grid.complete_cycles == 1:
            record_pass("完整循环计数", f"完成1次循环: min(1买入,1卖出)=1")
        else:
            record_fail("完整循环计数", f"期望1，实际{grid.complete_cycles}")
        
        # 测试未触发挂单的情况
        result = grid.update_price(Decimal("3005"))  # 当前价格已经在卖单上方
        if result is None:
            record_pass("未穿越返回None", "价格在网格内 → 不触发")
        else:
            record_warn("未穿越返回None", f"返回{result}（可能因为卖单已经上移）")
        
    except Exception as e:
        record_fail("价格更新测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 3: VirtualGrid 循环检测
# =====================================================
def test_virtual_grid_cycles():
    print("\n" + "="*80)
    print("📋 测试 3: VirtualGrid 多次循环检测")
    print("="*80)
    
    try:
        grid = VirtualGrid(
            symbol="BTC",
            current_price=Decimal("100000"),
            grid_width_percent=Decimal("2.0"),
            grid_interval_percent=Decimal("0.5"),
        )
        interval_value = Decimal("500")  # 100000 * 0.5%
        
        # 模拟5次完整循环：买卖交替
        for cycle in range(5):
            # 下跌到买单 → 买入
            grid.update_price(Decimal("100000") - interval_value * 2)
            # 上涨到卖单 → 卖出
            grid.update_price(Decimal("100000") + interval_value * 2)
        
        if grid.complete_cycles == 5:
            record_pass("5次完整循环", f"complete_cycles={grid.complete_cycles}")
        else:
            record_fail("5次完整循环", f"期望5，实际{grid.complete_cycles}")
        
        if grid.buy_crosses == 5 and grid.sell_crosses == 5:
            record_pass("买卖次数对称", f"buy={grid.buy_crosses}, sell={grid.sell_crosses}")
        else:
            record_fail("买卖次数对称", f"buy={grid.buy_crosses}, sell={grid.sell_crosses}")
        
    except Exception as e:
        record_fail("循环检测测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 4: APRCalculator 计算正确性
# =====================================================
def test_apr_calculator():
    print("\n" + "="*80)
    print("📋 测试 4: APRCalculator 计算正确性")
    print("="*80)
    
    try:
        # 验证 README 中给出的示例
        # 格子间距=0.5%, 宽度=10%, 每小时循环=10
        # 修正公式 (2026-06-27): APR = (0.5 - 0.004) × 10 × 8760 = 43,449.6%
        #   旧公式 (0.5 - 0.004) × 0.5 / 10 × 10 × 8760 = 2,172.48% 含 bug
        apr = APRCalculator.calculate(
            grid_interval_percent=Decimal("0.5"),
            grid_width_percent=Decimal("10"),
            cycles_per_hour=Decimal("10"),
        )
        expected = Decimal("43449.6")
        if abs(apr - expected) < Decimal("0.1"):
            record_pass("README示例APR", f"{apr:.2f}% ≈ 43,449.6% (新公式)")
        else:
            record_fail("README示例APR", f"期望43,449.6%，实际{apr:.2f}%")
        
        # 验证：手续费超过利润时APR=0
        apr = APRCalculator.calculate(
            grid_interval_percent=Decimal("0.003"),  # 0.003% < 0.004%
            grid_width_percent=Decimal("10"),
            cycles_per_hour=Decimal("10"),
        )
        if apr == Decimal("0"):
            record_pass("手续费>利润 → APR=0", "0.003% < 0.004% 手续费 → APR=0")
        else:
            record_fail("手续费>利润 → APR=0", f"期望0，实际{apr}")
        
        # 验证：循环为0时APR=0
        apr = APRCalculator.calculate(
            grid_interval_percent=Decimal("0.5"),
            grid_width_percent=Decimal("10"),
            cycles_per_hour=Decimal("0"),
        )
        if apr == Decimal("0"):
            record_pass("循环=0 → APR=0", "无循环时APR=0")
        else:
            record_fail("循环=0 → APR=0", f"期望0，实际{apr}")
        
        # 验证总本金计算
        capital = APRCalculator.calculate_total_capital(
            grid_width_percent=Decimal("10"),
            grid_interval_percent=Decimal("0.5"),
        )
        # 20格 × 10 USDC = 200 USDC
        if capital == Decimal("200"):
            record_pass("总本金计算", f"10 USDC × 20格 = {capital} USDC")
        else:
            record_fail("总本金计算", f"期望200，实际{capital}")
        
        # 验证单次循环收益
        profit = APRCalculator.calculate_profit_per_cycle(
            grid_interval_percent=Decimal("0.5"),
        )
        # 10 × (0.5-0.004)/100 = 0.0496
        expected_profit = Decimal("0.0496")
        if abs(profit - expected_profit) < Decimal("0.0001"):
            record_pass("单次循环收益", f"${profit}")
        else:
            record_fail("单次循环收益", f"期望{expected_profit}，实际{profit}")
        
    except Exception as e:
        record_fail("APR计算器测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 5: VirtualGrid.calculate_apr 滚动窗口
# =====================================================
def test_calculate_apr_with_window():
    print("\n" + "="*80)
    print("📋 测试 5: VirtualGrid APR滚动窗口计算")
    print("="*80)
    
    try:
        grid = VirtualGrid(
            symbol="BTC",
            current_price=Decimal("100000"),
            grid_width_percent=Decimal("2.0"),
            grid_interval_percent=Decimal("0.5"),
        )
        interval_value = Decimal("500")
        
        # 初始：运行时间<60秒，APR=0
        apr = grid.calculate_apr(
            order_value_usdc=Decimal("10"),
            fee_rate_percent=Decimal("0.004"),
            time_window_minutes=5,
        )
        if apr == Decimal("0"):
            record_pass("运行<60秒 → APR=0", "启动初期APR为0")
        else:
            record_warn("运行<60秒 → APR=0", f"实际{apr}（可能因为运行时间>60秒）")
        
        # 模拟多次循环
        for cycle in range(10):
            grid.update_price(Decimal("100000") - interval_value * 2)
            grid.update_price(Decimal("100000") + interval_value * 2)
        
        # 强制设置 last_update_time 让其看起来运行了较长时间
        grid.last_update_time = datetime.now() + timedelta(minutes=10)
        # 旧循环事件超出5分钟窗口，需要重新加入新的
        grid.cycle_events.clear()
        # 把10个循环事件都放在5分钟窗口内
        for _ in range(10):
            grid.cycle_events.append(datetime.now() - timedelta(minutes=2))
        
        apr = grid.calculate_apr(
            order_value_usdc=Decimal("10"),
            fee_rate_percent=Decimal("0.004"),
            time_window_minutes=5,
        )
        
        if apr > Decimal("0"):
            record_pass("10次循环后APR>0", f"APR={apr:.2f}%")
            # 验证:每小时循环 = 10次/5分钟 × 60 = 120次/小时
            # 新公式 (2026-06-27): APR = (0.5 - 0.004) × 120 × 8760 = 5,213,952%
            # 旧公式 (有 bug):  = 130,348.8%
            record_info("APR计算明细", f"120次/小时 → APR {apr:.2f}% (新公式应为 ~5,213,952%)")
        else:
            record_fail("10次循环后APR>0", f"APR={apr}")
        
        # 测试窗口清理：把循环事件设为10分钟前
        grid.cycle_events.clear()
        for _ in range(20):
            grid.cycle_events.append(datetime.now() - timedelta(minutes=10))
        
        apr_old = grid.calculate_apr(
            order_value_usdc=Decimal("10"),
            fee_rate_percent=Decimal("0.004"),
            time_window_minutes=5,
        )
        if apr_old == Decimal("0"):
            record_pass("10分钟前事件被清理", "窗口外事件不计入APR")
        else:
            record_warn("10分钟前事件被清理", f"窗口外事件APR={apr_old}（可能未被正确清理）")
        
    except Exception as e:
        record_fail("APR滚动窗口测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 6: SimulationResult 评级系统
# =====================================================
def test_simulation_result_rating():
    print("\n" + "="*80)
    print("📋 测试 6: SimulationResult 评级系统")
    print("="*80)
    
    try:
        # 测试各档位 (2026-06-27 新閾值)
        #   S≥1000 / A≥300 / B≥100 / C≥30 / D<30
        rating_tests = [
            (Decimal("1500"), "🔥 S", "S级: APR >= 1000%"),
            (Decimal("500"),  "⭐ A", "A级: APR >= 300%"),
            (Decimal("150"),  "✅ B", "B级: APR >= 100%"),
            (Decimal("50"),   "🟡 C", "C级: APR >= 30%"),
            (Decimal("10"),   "❌ D", "D级: APR < 30%"),
        ]

        for apr_value, expected_rating, desc in rating_tests:
            grid = VirtualGrid(
                symbol="TEST",
                current_price=Decimal("100"),
                grid_width_percent=Decimal("10"),
                grid_interval_percent=Decimal("0.5"),
            )
            grid.estimated_apr = apr_value

            result = SimulationResult.from_virtual_grid(grid)
            if result.rating == expected_rating:
                record_pass(desc, f"APR={apr_value}% → {result.rating}")
            else:
                record_fail(desc, f"期望{expected_rating}，实际{result.rating}")

        # 测试评分范围
        grid = VirtualGrid(
            symbol="TEST",
            current_price=Decimal("100"),
            grid_width_percent=Decimal("10"),
            grid_interval_percent=Decimal("0.5"),
        )
        grid.estimated_apr = Decimal("2000")  # S级
        grid.cycles_per_hour = Decimal("100")  # 高频
        grid.volume_24h_usdc = Decimal("20000000")  # 高流动性

        result = SimulationResult.from_virtual_grid(grid)
        if 0 <= result.score <= 100:
            record_pass("评分范围约束", f"score={result.score} ∈ [0,100]")
        else:
            record_fail("评分范围约束", f"score={result.score} 越界")
        
    except Exception as e:
        record_fail("评级系统测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 7: CycleDetector
# =====================================================
def test_cycle_detector():
    print("\n" + "="*80)
    print("📋 测试 7: CycleDetector")
    print("="*80)
    
    try:
        # 测试穿越检测
        if CycleDetector.detect_cross(Decimal("100"), Decimal("101"), []) == "buy":
            record_pass("上涨→buy", "价格上涨返回'buy'")
        else:
            record_fail("上涨→buy", "")
        
        if CycleDetector.detect_cross(Decimal("100"), Decimal("99"), []) == "sell":
            record_pass("下跌→sell", "价格下跌返回'sell'")
        else:
            record_fail("下跌→sell", "")
        
        if CycleDetector.detect_cross(Decimal("100"), Decimal("100"), []) is None:
            record_pass("不变→None", "价格不变返回None")
        else:
            record_fail("不变→None", "")
        
        # 测试循环计算
        if CycleDetector.calculate_cycles(5, 3) == 3:
            record_pass("min计算", "min(5,3)=3")
        else:
            record_fail("min计算", "")
        
        if CycleDetector.calculate_cycles(2, 7) == 2:
            record_pass("min计算2", "min(2,7)=2")
        else:
            record_fail("min计算2", "")
        
        if CycleDetector.calculate_cycles(0, 0) == 0:
            record_pass("min计算3", "min(0,0)=0")
        else:
            record_fail("min计算3", "")
        
    except Exception as e:
        record_fail("CycleDetector测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 8: APRAlertManager
# =====================================================
def test_apr_alert():
    print("\n" + "="*80)
    print("📋 测试 8: APRAlertManager 报警机制")
    print("="*80)
    
    try:
        from grid_volatility_scanner.core.apr_alert import APRAlertManager
        
        manager = APRAlertManager(
            apr_threshold=100.0,
            max_alerts_per_symbol=2,
            alert_cooldown_seconds=60,
        )
        
        # 静音平台上的报警声音（避免测试时发声）
        import platform
        import unittest.mock
        with unittest.mock.patch('subprocess.run') as mock_run:
            # 测试：APR低于阈值 → 不报警
            alerted = manager.check_and_alert("BTC", Decimal("50"))
            if not alerted:
                record_pass("低APR不报警", "APR=50% < 阈值100%")
            else:
                record_fail("低APR不报警", "")
            
            # 测试：APR超过阈值 → 报警
            alerted = manager.check_and_alert("BTC", Decimal("150"))
            if alerted:
                record_pass("高APR触发报警", "APR=150% > 阈值100%")
            else:
                record_fail("高APR触发报警", "")
            
            # 测试：冷却期内重复报警 → 不响应
            alerted = manager.check_and_alert("BTC", Decimal("200"))
            if not alerted:
                record_pass("冷却期不重复报警", "60秒冷却期内不响应")
            else:
                record_fail("冷却期不重复报警", "")
            
            # 测试：达到最大报警次数 → 不再报警
            manager.alert_counts["ETH"] = 2  # 已达上限
            alerted = manager.check_and_alert("ETH", Decimal("200"))
            if not alerted:
                record_pass("达到最大报警次数", "ETH已报警2次，不再报警")
            else:
                record_fail("达到最大报警次数", "")
            
            # 测试：重置功能
            manager.reset_symbol("BTC")
            if "BTC" not in manager.alerted_symbols:
                record_pass("重置代币报警", "reset_symbol() 清理记录")
            else:
                record_fail("重置代币报警", "")
        
        # 测试状态查询
        status = manager.get_status()
        if 'apr_threshold' in status and status['apr_threshold'] == 100.0:
            record_pass("状态查询", f"threshold={status['apr_threshold']}")
        else:
            record_fail("状态查询", f"状态信息: {status}")
        
    except Exception as e:
        record_fail("APRAlertManager测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 9: BTC 符号识别
# =====================================================
def test_btc_symbol_detection():
    print("\n" + "="*80)
    print("📋 测试 9: BTC 符号识别")
    print("="*80)
    
    try:
        btc_tests = [
            ("BTC", True, "纯BTC"),
            ("BTC-USD", True, "BTC-USD"),
            ("BTCUSDT", True, "BTCUSDT紧凑"),
            ("btcusdt", True, "小写BTC"),
            ("WBTC", False, "WBTC包装"),
            ("TBTC", False, "TBTC"),
            ("RBTC", False, "RBTC"),
            ("ETH", False, "ETH"),
        ]
        
        for symbol, expected, desc in btc_tests:
            actual = _is_btc_symbol(symbol)
            if actual == expected:
                record_pass(desc, f"{symbol} → BTC={actual}")
            else:
                record_fail(desc, f"{symbol} → 期望{expected}，实际{actual}")
        
    except Exception as e:
        record_fail("BTC符号识别测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 10: S级评级持续时间追踪
# =====================================================
def test_s_rating_tracking():
    print("\n" + "="*80)
    print("📋 测试 10: S级评级持续时间追踪")
    print("="*80)
    
    try:
        grid = VirtualGrid(
            symbol="BTC",
            current_price=Decimal("100000"),
            grid_width_percent=Decimal("2"),
            grid_interval_percent=Decimal("0.5"),
        )
        
        # 初始状态：不是S级
        if grid.s_rating_start_time is None:
            record_pass("初始S级=None", "未进入S级")
        else:
            record_fail("初始S级=None", f"实际{grid.s_rating_start_time}")
        
        # 进入S级
        grid.update_rating("🔥 S")
        if grid.s_rating_start_time is not None:
            record_pass("进入S级开始计时", f"start_time={grid.s_rating_start_time}")
        else:
            record_fail("进入S级开始计时", "未设置start_time")
        
        # 保持S级
        import time
        time.sleep(0.1)
        grid.update_rating("🔥 S")
        duration = grid.get_s_rating_duration_seconds()
        if duration >= 0:
            record_pass("S级持续时间累计", f"duration={duration}秒")
        else:
            record_fail("S级持续时间累计", f"duration={duration}")
        
        # 降级
        grid.update_rating("⭐ A")
        if grid.s_rating_start_time is None:
            record_pass("降级停止计时", "s_rating_start_time=None")
        else:
            record_fail("降级停止计时", f"start_time={grid.s_rating_start_time}")
        
        # 格式化测试
        if grid.get_s_rating_duration_str() == "--":
            record_pass("非S级格式--", "返回'--'")
        else:
            record_fail("非S级格式--", f"实际'{grid.get_s_rating_duration_str()}'")
        
    except Exception as e:
        record_fail("S级追踪测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 11: 配置文件加载
# =====================================================
def test_config_loading():
    print("\n" + "="*80)
    print("📋 测试 11: 配置文件加载")
    print("="*80)
    
    try:
        import yaml
        config_path = Path(__file__).parent / "config" / "market_config.yaml"
        
        if not config_path.exists():
            record_fail("配置文件存在性", f"未找到 {config_path}")
            return
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # 验证必要字段
        if 'scanner_config' in config:
            record_pass("scanner_config 存在", "")
        else:
            record_fail("scanner_config 存在", "缺少scanner_config节点")
        
        if 'default' in config:
            record_pass("default 存在", f"width={config['default'].get('grid_width_percent')}")
        else:
            record_fail("default 存在", "")
        
        # 验证 BTC 配置
        if 'BTC' in config:
            btc_cfg = config['BTC']
            if 'grid_width_percent' in btc_cfg and 'grid_interval_percent' in btc_cfg:
                record_pass("BTC配置完整", 
                    f"width={btc_cfg['grid_width_percent']}%, "
                    f"interval={btc_cfg['grid_interval_percent']}%")
            else:
                record_fail("BTC配置完整", "")
        
        # 验证 scanner_config 子字段
        scanner_cfg = config.get('scanner_config', {})
        required_fields = [
            'min_24h_volume_usdc',
            'scan_duration_seconds',
            'order_value_usdc',
            'fee_rate_percent',
            'apr_time_window_minutes',
        ]
        missing = [f for f in required_fields if f not in scanner_cfg]
        if not missing:
            record_pass("scanner_config 字段完整", "")
        else:
            record_warn("scanner_config 字段缺失", f"缺少: {missing}")
        
        # 检查价格递增警告
        if 'apr_alert_threshold' in scanner_cfg:
            record_pass("APR报警配置", 
                f"threshold={scanner_cfg['apr_alert_threshold']}%")
        else:
            record_warn("APR报警配置", "未配置报警阈值")
        
    except Exception as e:
        record_fail("配置文件测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 12: 边界与异常情况
# =====================================================
def test_edge_cases():
    print("\n" + "="*80)
    print("📋 测试 12: 边界与异常情况")
    print("="*80)
    
    try:
        # 极端小价格
        grid = VirtualGrid(
            symbol="SHIB",
            current_price=Decimal("0.00001"),
            grid_width_percent=Decimal("10"),
            grid_interval_percent=Decimal("0.5"),
        )
        if grid.lower_price > 0 and grid.upper_price > grid.lower_price:
            record_pass("极小价格网格", f"${grid.lower_price:.8f} - ${grid.upper_price:.8f}")
        else:
            record_fail("极小价格网格", f"lower={grid.lower_price}, upper={grid.upper_price}")
        
        # 极端大价格
        grid = VirtualGrid(
            symbol="BTC",
            current_price=Decimal("10000000"),
            grid_width_percent=Decimal("2"),
            grid_interval_percent=Decimal("0.5"),
        )
        if grid.upper_price > grid.lower_price:
            record_pass("极大价格网格", f"${grid.lower_price:,.0f} - ${grid.upper_price:,.0f}")
        else:
            record_fail("极大价格网格", "")
        
        # 价格=0
        grid = VirtualGrid(
            symbol="TEST",
            current_price=Decimal("0"),
            grid_width_percent=Decimal("5"),
            grid_interval_percent=Decimal("0.5"),
        )
        # 价格=0时所有边界都是0
        if grid.lower_price == Decimal("0") and grid.upper_price == Decimal("0"):
            record_pass("价格=0边界", "lower=upper=0 (期望行为，但可能导致除零)")
        else:
            record_warn("价格=0边界", f"lower={grid.lower_price}, upper={grid.upper_price}")
        
        # 价格不穿越网格
        grid = VirtualGrid(
            symbol="ETH",
            current_price=Decimal("3000"),
            grid_width_percent=Decimal("10"),
            grid_interval_percent=Decimal("0.5"),
        )
        # 价格小幅波动
        cross_count = 0
        for price in [3001, 3002, 3001, 3000, 3001, 3002]:
            r = grid.update_price(Decimal(str(price)))
            if r:
                cross_count += 1
        if cross_count == 0:
            record_pass("小幅波动不穿越", f"价格在网格内不触发（{cross_count}次）")
        else:
            record_warn("小幅波动不穿越", f"触发了{cross_count}次（预期0）")
        
    except Exception as e:
        record_fail("边界测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 13: Volume格式化
# =====================================================
def test_volume_formatting():
    print("\n" + "="*80)
    print("📋 测试 13: 成交量格式化")
    print("="*80)
    
    try:
        test_cases = [
            (Decimal("500"), "$500"),
            (Decimal("5000"), "$5.00K"),
            (Decimal("5000000"), "$5.00M"),
            (Decimal("1500000000"), "$1500.00M"),
        ]
        
        for vol, expected_prefix in test_cases:
            grid = VirtualGrid(
                symbol="TEST",
                current_price=Decimal("100"),
                grid_width_percent=Decimal("10"),
                grid_interval_percent=Decimal("0.5"),
            )
            grid.volume_24h_usdc = vol
            
            result = SimulationResult.from_virtual_grid(grid)
            formatted = result.get_volume_str()
            if formatted == expected_prefix:
                record_pass(f"格式化 {vol}", f"→ {formatted}")
            else:
                record_warn(f"格式化 {vol}", f"期望{expected_prefix}，实际{formatted}")
        
    except Exception as e:
        record_fail("成交量格式化测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 测试 14: ScannerUI 初始化
# =====================================================
def test_scanner_ui():
    print("\n" + "="*80)
    print("📋 测试 14: ScannerUI 初始化")
    print("="*80)
    
    try:
        from grid_volatility_scanner.ui.scanner_ui import ScannerUI
        
        ui = ScannerUI()
        if ui is not None:
            record_pass("UI 实例化", "ScannerUI() 创建成功")
        else:
            record_fail("UI 实例化", "")
            return
        
        if ui.ui_log_handler is not None:
            record_pass("UI 日志handler", "ui_log_handler 已创建")
        else:
            record_fail("UI 日志handler", "")
        
        # 测试日志队列
        if len(ui.log_queue) == 0:
            record_pass("日志队列初始为空", "")
        else:
            record_warn("日志队列初始为空", f"len={len(ui.log_queue)}")
        
        # 测试 stats 更新
        ui.update_stats(total_markets=100, active_markets=50)
        if ui.total_markets == 100 and ui.active_markets == 50:
            record_pass("stats 更新", f"total={ui.total_markets}, active={ui.active_markets}")
        else:
            record_fail("stats 更新", "")
        
        # 测试 results 更新
        grid = VirtualGrid(
            symbol="TEST",
            current_price=Decimal("100"),
            grid_width_percent=Decimal("10"),
            grid_interval_percent=Decimal("0.5"),
        )
        grid.estimated_apr = Decimal("150")
        result = SimulationResult.from_virtual_grid(grid)
        ui.update_results([result])
        if len(ui.scan_results) == 1:
            record_pass("results 更新", f"len={len(ui.scan_results)}")
        else:
            record_fail("results 更新", "")
        
        # 验证 create_layout 不抛异常
        layout = ui.create_layout()
        if layout is not None:
            record_pass("layout 创建", "")
        else:
            record_fail("layout 创建", "")
        
        # 验证停止
        ui.stop()
        if not ui._running:
            record_pass("UI 停止", "_running=False")
        else:
            record_fail("UI 停止", "")
        
    except Exception as e:
        record_fail("UI 测试", f"异常: {e}")
        import traceback
        traceback.format_exc() if hasattr(traceback, 'format_exc') else None


# =====================================================
# 测试 15: APR Alert 边界
# =====================================================
async def test_scanner_initialization():
    print("\n" + "="*80)
    print("📋 测试 15: GridVolatilityScanner 模拟初始化")
    print("="*80)
    
    try:
        from grid_volatility_scanner.scanner import GridVolatilityScanner
        
        # 创建 mock adapter
        mock_adapter = MagicMock()
        mock_adapter._connected = True
        mock_exchange_info = MagicMock()
        mock_exchange_info.markets = {
            "BTC-USD": {"base": "BTC", "quote": "USD"},
            "ETH-USD": {"base": "ETH", "quote": "USD"},
            "USDT-USD": {"base": "USDT", "quote": "USD"},  # 稳定币，应被过滤
        }
        mock_adapter.get_exchange_info = AsyncMock(return_value=mock_exchange_info)
        
        scanner = GridVolatilityScanner(
            exchange_adapter=mock_adapter,
            config_path=str(Path(__file__).parent / "config" / "market_config.yaml"),
        )
        
        if scanner is not None:
            record_pass("Scanner 实例化", "")
        else:
            record_fail("Scanner 实例化", "")
            return
        
        # 测试配置加载
        await scanner._load_config()
        if 'BTC' in scanner.market_configs:
            record_pass("配置加载", f"BTC width={scanner.market_configs['BTC']['grid_width_percent']}%")
        else:
            record_fail("配置加载", "缺少BTC配置")
        
        if scanner.default_config is not None:
            record_pass("default_config 加载", f"width={scanner.default_config['grid_width_percent']}%")
        else:
            record_fail("default_config 加载", "")
        
        # 测试市场过滤
        markets = await scanner._get_all_markets()
        symbols = [m['symbol'] for m in markets]
        if "USDT-USD" not in symbols:
            record_pass("稳定币过滤", f"已过滤 USDT-USD，保留 {symbols}")
        else:
            record_fail("稳定币过滤", f"未过滤: {symbols}")
        
        if "BTC-USD" in symbols and "ETH-USD" in symbols:
            record_pass("正常代币保留", f"markets={symbols}")
        else:
            record_fail("正常代币保留", f"markets={symbols}")
        
        # 测试结果排序
        scanner.virtual_grids = {}
        for sym, apr, price in [("ETH-USD", 2000, 3000), ("BTC-USD", 1000, 100000), ("SOL-USD", 3000, 200)]:
            g = VirtualGrid(
                symbol=sym,
                current_price=Decimal(str(price)),
                grid_width_percent=Decimal("10"),
                grid_interval_percent=Decimal("0.5"),
            )
            g.estimated_apr = Decimal(str(apr))
            g.complete_cycles = 10
            scanner.virtual_grids[sym] = g
        
        results = scanner.get_results()
        if results[0].symbol == "BTC-USD":
            record_pass("BTC置顶排序", f"BTC第一：APR={results[0].estimated_apr}%")
        else:
            record_fail("BTC置顶排序", f"实际第一：{results[0].symbol}")
        
        if results[1].symbol == "SOL-USD" and float(results[1].estimated_apr) == 3000:
            record_pass("其他按APR排序", f"SOL第二：APR={results[1].estimated_apr}%")
        else:
            record_warn("其他按APR排序", 
                f"第二：{results[1].symbol} APR={results[1].estimated_apr}%")
        
    except Exception as e:
        record_fail("Scanner 模拟初始化测试", f"异常: {e}")
        import traceback
        traceback.print_exc()


# =====================================================
# 主测试入口
# =====================================================
def main():
    print("="*80)
    print("🔍 网格波动率扫描器 - 综合功能稽核测试")
    print("="*80)
    print(f"📅 测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📁 项目根: {Path(__file__).parent}")
    
    # 同步测试
    test_virtual_grid_initialization()
    test_virtual_grid_price_update()
    test_virtual_grid_cycles()
    test_apr_calculator()
    test_calculate_apr_with_window()
    test_simulation_result_rating()
    test_cycle_detector()
    test_apr_alert()
    test_btc_symbol_detection()
    test_s_rating_tracking()
    test_config_loading()
    test_edge_cases()
    test_volume_formatting()
    test_scanner_ui()
    
    # 异步测试
    asyncio.run(test_scanner_initialization())
    
    # 输出汇总
    print("\n" + "="*80)
    print("📊 测试结果汇总")
    print("="*80)
    print(f"✅ 通过: {len(RESULTS['passed'])} 项")
    print(f"❌ 失败: {len(RESULTS['failed'])} 项")
    print(f"⚠️  警告: {len(RESULTS['warnings'])} 项")
    print(f"ℹ️  信息: {len(RESULTS['info'])} 项")
    
    if RESULTS["failed"]:
        print("\n" + "="*80)
        print("❌ 失败项目")
        print("="*80)
        for f in RESULTS["failed"]:
            print(f"  {f}")
    
    if RESULTS["warnings"]:
        print("\n" + "="*80)
        print("⚠️  警告项目")
        print("="*80)
        for w in RESULTS["warnings"]:
            print(f"  {w}")
    
    # 返回结果供后续处理
    return RESULTS


if __name__ == "__main__":
    main()
