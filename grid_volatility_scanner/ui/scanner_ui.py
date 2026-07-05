"""
网格波动率扫描器UI - Scanner UI

基于Rich库的稳定终端UI显示系统
遵循终端UI稳定显示设计指南
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any, List, Deque
from logging.handlers import RotatingFileHandler
from decimal import Decimal

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

from ..models.simulation_result import SimulationResult


class UILogHandler(logging.Handler):
    """
    UI日志处理器 - 将日志捕获到队列中供UI显示

    关键特性：
    - 线程安全（使用deque）
    - 固定大小队列（自动淘汰旧日志）
    - 简化格式（移除冗余信息）
    """

    def __init__(self, log_queue: Deque, max_size: int = 20):
        super().__init__()
        self.log_queue = log_queue
        self.max_size = max_size

    def emit(self, record: logging.LogRecord):
        """捕获日志记录"""
        try:
            # 格式化日志消息（简化格式）
            msg = self.format(record)

            # 添加到队列（保持最新N条）
            self.log_queue.append({
                'time': datetime.fromtimestamp(record.created).strftime('%H:%M:%S'),
                'level': record.levelname,
                'module': record.name.split('.')[-1] if '.' in record.name else record.name,
                'message': msg,
            })

            # 保持队列大小
            while len(self.log_queue) > self.max_size:
                self.log_queue.popleft()
        except Exception:
            # 忽略处理日志时的错误，避免死循环
            pass


class ScannerUI:
    """
    网格波动率扫描器终端UI

    功能：
    - 实时显示扫描结果排行榜
    - 显示详细的统计数据
    - 捕获并显示日志
    - 稳定无抖动的布局
    """

    def __init__(self):
        """初始化UI"""
        self.console = Console()
        self.log_queue: Deque = deque(maxlen=20)
        self.ui_log_handler: Optional[UILogHandler] = None
        self._running = False

        # 🔥 修复 P0-3：用于从 scanner_config 读取 top_n_results 等参数
        self.scanner_config: Dict[str, Any] = {}

        # 当前扫描数据
        self.scan_results: List[SimulationResult] = []
        self.scan_start_time: Optional[datetime] = None
        self.total_markets: int = 0
        self.active_markets: int = 0

        # 设置日志捕获
        self._setup_log_capture()

        self.logger = logging.getLogger(__name__)
        self.logger.info("扫描器UI初始化完成")

    def _setup_log_capture(self):
        """设置日志捕获并禁用控制台输出"""
        try:
            # 创建UI日志处理器
            self.ui_log_handler = UILogHandler(self.log_queue, max_size=20)
            self.ui_log_handler.setLevel(logging.INFO)

            # 简化日志格式（UI表格会显示时间、级别、模块）
            formatter = logging.Formatter('%(message)s')
            self.ui_log_handler.setFormatter(formatter)

            # 关键模块列表（需要捕获日志的模块）
            key_modules = [
                'grid_volatility_scanner.scanner',
                'grid_volatility_scanner.core.price_monitor',
                'grid_volatility_scanner.models.virtual_grid',
            ]

            # 禁用root logger的控制台输出
            root_logger = logging.getLogger()
            for handler in root_logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler) and \
                   not isinstance(handler, RotatingFileHandler):
                    root_logger.removeHandler(handler)

            # 为每个关键模块配置日志
            for module_name in key_modules:
                module_logger = logging.getLogger(module_name)

                # 移除控制台输出handler（保留文件输出）
                for handler in module_logger.handlers[:]:
                    if isinstance(handler, logging.StreamHandler) and \
                       not isinstance(handler, RotatingFileHandler):
                        module_logger.removeHandler(handler)

                # 🔥 确保日志级别足够低，能捕获INFO级别的日志
                module_logger.setLevel(logging.DEBUG)

                # 添加UI日志处理器
                if self.ui_log_handler not in module_logger.handlers:
                    module_logger.addHandler(self.ui_log_handler)

                # 🔥 保持传播到root logger，以便写入日志文件
                module_logger.propagate = True

        except Exception as e:
            print(f"⚠️ 设置日志捕获失败: {e}")

    def _ensure_console_logging_disabled(self):
        """确保控制台日志输出已禁用"""
        key_modules = [
            'grid_volatility_scanner',
        ]

        # 移除控制台handler（保留文件handler）
        for module_name in key_modules:
            module_logger = logging.getLogger(module_name)
            for handler in module_logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler) and \
                   not isinstance(handler, RotatingFileHandler):
                    module_logger.removeHandler(handler)
            # 🔥 保持传播，以便日志写入文件
            module_logger.propagate = True

        # 禁用root logger的控制台输出
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            if isinstance(handler, logging.StreamHandler) and \
               not isinstance(handler, RotatingFileHandler):
                root_logger.removeHandler(handler)

    def create_header(self) -> Panel:
        """创建标题栏"""
        header_text = Text()
        header_text.append("🎯 网格波动率扫描器 ", style="bold white")
        header_text.append("Grid Volatility Scanner v1.0", style="cyan")

        return Panel(
            header_text,
            border_style="white",
            height=3
        )

    def create_summary_panel(self) -> Panel:
        """创建摘要面板"""
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("指标", style="cyan", width=20, no_wrap=True)
        table.add_column("数值", style="white", width=30, no_wrap=True)

        # 运行时长
        if self.scan_start_time:
            running_seconds = int(
                (datetime.now() - self.scan_start_time).total_seconds())
            hours = running_seconds // 3600
            minutes = (running_seconds % 3600) // 60
            seconds = running_seconds % 60
            running_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            running_time = "00:00:00"

        table.add_row("📊 运行时长", running_time)
        table.add_row("🪙 监控市场数", f"{self.active_markets}/{self.total_markets}")
        table.add_row("📈 有效结果数", f"{len(self.scan_results)}")

        # 最佳APR
        if self.scan_results:
            best_apr = max(self.scan_results, key=lambda x: x.estimated_apr)
            table.add_row(
                "🔥 最佳APR",
                f"{best_apr.symbol}: {best_apr.estimated_apr:.2f}% ({best_apr.rating})"
            )
        else:
            table.add_row("🔥 最佳APR", "[dim]等待数据...[/dim]")

        return Panel(
            table,
            title="📋 扫描摘要",
            border_style="green",
            height=8
        )

    def create_rankings_table(self) -> Panel:
        """创建排行榜表格"""
        table = Table(show_header=True, box=None, padding=(0, 1))

        # 定义列
        table.add_column("排名", style="bold yellow", width=6,
                         no_wrap=True, justify="center")
        table.add_column("代币", style="bold cyan", width=10, no_wrap=True)
        table.add_column("当前价", style="white", width=12,
                         no_wrap=True, justify="right")
        table.add_column("循环", style="green", width=12,
                         no_wrap=True, justify="right")
        table.add_column("最近5分", style="green", width=10,
                         no_wrap=True, justify="right")
        table.add_column("预估APR", style="bold magenta",
                         width=12, no_wrap=True, justify="right")
        table.add_column("24h量", style="cyan", width=10,
                         no_wrap=True, justify="right")
        table.add_column("评级", style="bold", width=8,
                         no_wrap=True, justify="center")
        table.add_column("S持续", style="bold red", width=10,
                         no_wrap=True, justify="center")

        # 如果没有数据，显示提示
        if not self.scan_results:
            table.add_row(
                "[dim]--[/dim]",
                "[dim]等待数据[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]",
                "[dim]--[/dim]"  # S持续时间列
            )
        else:
            # 🔥 自定义排序：BTC永远第一，其他按APR排序
            def sort_key(result):
                # 检查是否为BTC（匹配 BTC, BTC-USD, BTCUSDT 等）
                symbol_upper = result.symbol.upper()
                is_btc = 'BTC' in symbol_upper and not any(
                    x in symbol_upper for x in ['WBTC', 'TBTC', 'RBTC'])

                if is_btc:
                    # BTC返回极高值，确保排第一
                    return (float('inf'), float(result.estimated_apr))
                else:
                    # 其他代币按APR排序
                    return (0, float(result.estimated_apr))

            sorted_results = sorted(
                self.scan_results,
                key=sort_key,
                reverse=True  # 从高到低
            )  # 🔥 修复 P0-3：从配置读取 top_n_results（默认 50）
            top_n = self.scanner_config.get('top_n_results', 50)
            sorted_results = sorted_results[:top_n]

            for rank, result in enumerate(sorted_results, 1):
                # 排名样式
                if rank == 1:
                    rank_str = "🥇"
                elif rank == 2:
                    rank_str = "🥈"
                elif rank == 3:
                    rank_str = "🥉"
                else:
                    rank_str = f"{rank}"

                # APR颜色
                apr = float(result.estimated_apr)
                if apr >= 500:
                    apr_style = "[bold red]"
                elif apr >= 300:
                    apr_style = "[bold magenta]"
                elif apr >= 150:
                    apr_style = "[bold yellow]"
                elif apr >= 50:
                    apr_style = "[green]"
                else:
                    apr_style = "[dim]"

                # 🔥 完整价格显示（不硬编码2位小数）
                price = float(result.current_price)
                if price >= 1000:
                    price_str = f"${price:,.2f}"  # 大价格：2位小数
                elif price >= 1:
                    price_str = f"${price:,.4f}"  # 中价格：4位小数
                elif price >= 0.01:
                    price_str = f"${price:.6f}"   # 小价格：6位小数
                else:
                    price_str = f"${price:.8f}"   # 极小价格：8位小数

                # 🔥 循环列：总循环 / 平均5分钟循环
                cycles_str = f"{result.complete_cycles}/{result.avg_cycles_per_5min:.1f}"

                # 🔥 最近5分钟循环次数
                recent_5min_str = f"{result.recent_5min_cycles}"
                
                # 🔥 S级持续时间（只有S级才显示，其他显示"--"）
                s_duration_str = result.s_rating_duration_str
                if s_duration_str != "--":
                    # S级且有持续时间 → 红色高亮
                    s_duration_display = f"[bold red]{s_duration_str}[/bold red]"
                else:
                    # 非S级 → 灰色显示
                    s_duration_display = "[dim]--[/dim]"

                table.add_row(
                    rank_str,
                    result.symbol,
                    price_str,
                    cycles_str,
                    recent_5min_str,
                    f"{apr_style}{result.estimated_apr:.2f}%[/]",
                    result.get_volume_str(),
                    result.rating,
                    s_duration_display  # S级持续时间
                )

        return Panel(
            table,
            title=f"🏆 代币波动率排行榜 (Top {self.scanner_config.get('top_n_results', 50)}) - 按APR从高到低排序",
            border_style="yellow"
        )

    def create_logs_table(self) -> Panel:
        """创建日志显示表格"""
        table = Table(show_header=True, box=None, padding=(0, 1))

        # 定义列（不限制消息长度，完整显示）
        table.add_column("时间", style="dim", width=8, no_wrap=True)
        table.add_column("级别", style="bold", width=6, no_wrap=True)
        table.add_column("模块", style="cyan", width=15, no_wrap=True)
        table.add_column("消息", style="white")  # 无长度限制

        # 如果没有日志，显示提示
        if not self.log_queue:
            table.add_row("--:--:--", "--", "等待日志", "[dim]暂无日志[/dim]")
        else:
            # 显示最新N条日志
            for log_entry in list(self.log_queue):
                # 根据日志级别设置颜色
                level = log_entry['level']
                if level == 'ERROR':
                    level_style = "[bold red]ERROR[/bold red]"
                elif level == 'WARNING':
                    level_style = "[bold yellow]WARN[/bold yellow]"
                elif level == 'INFO':
                    level_style = "[bold green]INFO[/bold green]"
                elif level == 'DEBUG':
                    level_style = "[dim]DEBUG[/dim]"
                else:
                    level_style = level

                # 简化消息格式
                message = self._format_log_message(log_entry['message'])

                table.add_row(
                    log_entry['time'],
                    level_style,
                    log_entry['module'][:15],  # 限制模块名长度
                    message
                )

        # 返回Panel（固定高度）
        return Panel(
            table,
            title="📋 最新日志 (最新20条)",
            border_style="blue",
            height=23  # 1标题+1表头+20数据+1边框
        )

    def _format_log_message(self, message: str) -> str:
        """格式化日志消息"""
        # 移除常见的前缀emoji
        emoji_map = {
            '✅ ': '', '❌ ': '', '⚠️ ': '', '📝 ': '',
            '📨 ': '', '🔄 ': '', '🔗 ': '', '💓 ': '',
            '📦 ': '', '📊 ': '', '🔍 ': '', '🚀 ': '',
            '🎯 ': '', '🪙 ': '', '🔥 ': '', '⭐ ': '',
        }
        for emoji, replacement in emoji_map.items():
            message = message.replace(emoji, replacement)

        return message

    def create_controls_panel(self) -> Panel:
        """创建控制命令面板"""
        controls_text = Text()
        controls_text.append("📌 控制命令: ", style="bold white")
        controls_text.append("Ctrl+C ", style="bold red")
        controls_text.append("停止扫描  ", style="white")
        controls_text.append("| ", style="dim")
        controls_text.append("数据每 ", style="white")
        controls_text.append("0.5秒 ", style="bold yellow")
        controls_text.append("刷新一次", style="white")

        return Panel(
            controls_text,
            border_style="white",
            height=3
        )

    def create_layout(self) -> Layout:
        """创建完整布局"""
        layout = Layout()

        # 垂直分割：header + summary + rankings + logs + controls
        layout.split_column(
            Layout(self.create_header(), size=3),          # 标题栏
            Layout(self.create_summary_panel(), size=8),   # 摘要面板
            Layout(self.create_rankings_table()),          # 排行榜（自适应高度）
            Layout(self.create_logs_table(), size=23),     # 日志表格（固定高度）
            Layout(self.create_controls_panel(), size=3)   # 控制命令
        )

        return layout

    async def run(self, scan_duration: Optional[int] = None):
        """
        运行终端界面（持续监控模式）

        Args:
            scan_duration: 扫描时长（秒），None表示持续运行直到用户中断
        """
        self._running = True
        self.scan_start_time = datetime.now()

        # 确保控制台日志已禁用
        self._ensure_console_logging_disabled()

        if scan_duration is None:
            self.logger.info("🎯 启动扫描器UI（持续监控模式）")
        else:
            self.logger.info(f"启动扫描器UI，预计运行 {scan_duration} 秒")

        # 创建Live显示
        with Live(
            self.create_layout(),
            refresh_per_second=2,  # 刷新频率：2次/秒
            console=self.console,
            screen=True,  # 全屏模式
            transient=False
        ) as live:
            try:
                while self._running:
                    # 更新界面（固定表格，实时数据更新）
                    live.update(self.create_layout())

                    # 检查是否超时（仅定时模式）
                    if scan_duration is not None and self.scan_start_time:
                        elapsed = (datetime.now() -
                                   self.scan_start_time).total_seconds()
                        if elapsed >= scan_duration:
                            self.logger.info(f"扫描完成，运行时长 {int(elapsed)} 秒")
                            break

                    # 控制刷新频率
                    await asyncio.sleep(0.5)

            except KeyboardInterrupt:
                self.logger.info("用户中断扫描")
                self._running = False

    def stop(self):
        """停止UI"""
        self._running = False
        self.logger.info("UI已停止")

    def update_results(self, results: List[SimulationResult]):
        """
        更新扫描结果

        Args:
            results: 模拟结果列表
        """
        self.scan_results = results

    def update_stats(self, total_markets: int, active_markets: int):
        """
        更新统计数据

        Args:
            total_markets: 总市场数
            active_markets: 活跃市场数
        """
        self.total_markets = total_markets
        self.active_markets = active_markets
