"""
价格监控报警系统 - 主启动脚本

功能：
- 实时监控多个代币价格
- 异常波动时发出声音报警
- Rich终端表格显示实时数据
"""

import asyncio
import sys
import signal
import yaml
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.text import Text

from core.adapters.exchanges.factory import get_exchange_factory
from core.adapters.exchanges.interface import ExchangeConfig, ExchangeType
from core.adapters.exchanges.adapters.binance import BinanceAdapter
from core.services.price_alert.implementations.price_alert_service_impl import PriceAlertServiceImpl
from core.services.price_alert.models.alert_config import PriceAlertSystemConfig


class PriceAlertApp:
    """价格监控报警应用"""

    def __init__(self, config_file: str):
        self.config_file = config_file
        self.config: PriceAlertSystemConfig = None
        self.service: PriceAlertServiceImpl = None
        self.exchange_adapter = None
        self.console = Console()
        self._should_stop = False

    def load_config(self) -> bool:
        """加载配置"""
        try:
            config_path = Path(self.config_file)
            if not config_path.exists():
                print(f"❌ 配置文件不存在: {config_path}")
                return False

            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)

            self.config = PriceAlertSystemConfig.from_dict(config_data)
            print(f"✅ 配置文件加载成功: {config_path}")
            return True

        except Exception as e:
            print(f"❌ 加载配置文件失败: {e}")
            return False

    async def initialize(self) -> bool:
        """初始化"""
        try:
            # 加载配置
            if not self.load_config():
                return False

            # 创建交易所适配器
            exchange_name = self.config.exchange.lower()
            print(f"🔧 创建{exchange_name.upper()}适配器...")
            
            if exchange_name == "binance":
                # 直接实例化Binance适配器（用于公开数据访问，不需要真实API密钥）
                config = ExchangeConfig(
                    exchange_id="binance",
                    name="Binance",
                    exchange_type=ExchangeType.SPOT,
                    api_key="public_data_only",  # 占位符，公开数据不需要验证
                    api_secret="public_data_only",
                    testnet=False
                )
                self.exchange_adapter = BinanceAdapter(config=config)
            else:
                # 其他交易所使用工厂创建
                factory = get_exchange_factory()
                self.exchange_adapter = factory.create_adapter(
                    exchange_id=exchange_name,
                    api_key="public_data_only",
                    api_secret="public_data_only"
                )

            # 创建服务
            print("🔧 创建价格监控服务...")
            self.service = PriceAlertServiceImpl(self.exchange_adapter)

            # 初始化服务
            if not await self.service.initialize(self.config):
                print("❌ 服务初始化失败")
                return False

            return True

        except Exception as e:
            print(f"❌ 初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def run(self):
        """运行"""
        service_task = None
        ui_task = None
        
        try:
            # 启动服务
            service_task = asyncio.create_task(self.service.start())
            
            # 启动UI显示
            ui_task = asyncio.create_task(self._run_ui())
            
            # 等待任意任务完成或中断
            done, pending = await asyncio.wait(
                [service_task, ui_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 取消未完成的任务
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except KeyboardInterrupt:
            self.console.print("\n⚠️  收到中断信号 (Ctrl+C)")
        except Exception as e:
            self.console.print(f"\n❌ 运行错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 取消所有任务
            if service_task and not service_task.done():
                service_task.cancel()
                try:
                    await service_task
                except asyncio.CancelledError:
                    pass
            
            if ui_task and not ui_task.done():
                ui_task.cancel()
                try:
                    await ui_task
                except asyncio.CancelledError:
                    pass
            
            await self.shutdown()

    async def _run_ui(self):
        """运行终端UI"""
        refresh_interval = self.config.display.refresh_interval
        import os

        # 等待数据加载
        await asyncio.sleep(2)
        
        try:
            # 🔥 不使用 Live，改用手动刷新避免重叠
            while not self._should_stop:
                try:
                    # 每次更新前完全清屏
                    os.system('clear' if os.name == 'posix' else 'cls')
                    
                    # 直接打印表格
                    self.console.print(self._generate_table())
                    
                    # 等待刷新间隔
                    await asyncio.sleep(refresh_interval)
                except asyncio.CancelledError:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            # 退出时保留最后的表格
            self.console.print(self._generate_table())

    def _generate_table(self) -> Table:
        """生成显示表格"""
        table = Table(
            title="🚨 价格监控报警系统", 
            show_header=True, 
            header_style="bold magenta",
            border_style="bright_blue",
            title_style="bold cyan"
        )

        # 添加列 - 优化宽度和对齐
        table.add_column("代币", style="cyan bold", no_wrap=True, width=10)
        table.add_column("当前价格", justify="right", width=11)
        table.add_column("24h变化", justify="right", width=9)
        table.add_column("窗口变化\n(阈值)", justify="center", width=11)
        table.add_column("上限", justify="right", width=11, style="green")
        table.add_column("下限", justify="right", width=11, style="red")
        table.add_column("报警次数", justify="center", width=10)
        table.add_column("更新", justify="center", style="dim", width=8)

        # 添加行数据
        statistics = self.service.get_statistics()

        for symbol, stats in statistics.items():
            row_data = []

            # 获取代币配置
            symbol_config = None
            for s in self.config.symbols:
                if s.symbol == symbol:
                    symbol_config = s
                    break

            # 代币符号
            row_data.append(symbol)

            # 当前价格 - 格式化显示
            price_str = f"{float(stats.current_price):,.2f}"
            price_text = Text(price_str)
            if stats.price_24h_ago and stats.current_price > stats.price_24h_ago:
                price_text.stylize("green")
            elif stats.price_24h_ago and stats.current_price < stats.price_24h_ago:
                price_text.stylize("red")
            row_data.append(price_text)

            # 24小时变化
            change_24h = stats.get_24h_change_percent()
            if change_24h is not None:
                change_text = Text(f"{change_24h:+.2f}%")
                if change_24h > 0:
                    change_text.stylize("green")
                elif change_24h < 0:
                    change_text.stylize("red")
                row_data.append(change_text)
            else:
                row_data.append("N/A")

            # 时间窗口变化（紧凑格式）
            if symbol_config and symbol_config.volatility_alert.enabled:
                time_window = symbol_config.volatility_alert.time_window
                threshold = symbol_config.volatility_alert.threshold_percent
                change_window = stats.get_price_change_percent(time_window)
                
                if change_window is not None:
                    # 紧凑格式：+0.5% (±1%)
                    change_str = f"{change_window:+.1f}%\n(±{threshold}%)"
                    change_text = Text(change_str)
                    if abs(change_window) >= threshold:
                        change_text.stylize("yellow bold")
                    elif change_window > 0:
                        change_text.stylize("green")
                    elif change_window < 0:
                        change_text.stylize("red")
                    row_data.append(change_text)
                else:
                    row_data.append(f"等待\n(±{threshold}%)")
            else:
                row_data.append("-")

            # 价格上限
            if symbol_config and symbol_config.price_alert.enabled and symbol_config.price_alert.upper_limit > 0:
                upper_limit = symbol_config.price_alert.upper_limit
                upper_text = Text(f"{upper_limit:,.0f}")
                if stats.current_price >= upper_limit:
                    upper_text.stylize("bold reverse green")
                row_data.append(upper_text)
            else:
                row_data.append("-")
            
            # 价格下限
            if symbol_config and symbol_config.price_alert.enabled and symbol_config.price_alert.lower_limit > 0:
                lower_limit = symbol_config.price_alert.lower_limit
                lower_text = Text(f"{lower_limit:,.0f}")
                if stats.current_price <= lower_limit:
                    lower_text.stylize("bold reverse red")
                row_data.append(lower_text)
            else:
                row_data.append("-")

            # 报警次数（简洁格式）
            if stats.total_alerts > 0:
                alert_text = Text(f"{stats.total_alerts}", style="yellow bold")
            else:
                alert_text = "0"
            row_data.append(alert_text)

            # 最后更新时间
            if stats.last_update_time:
                update_text = stats.last_update_time.strftime("%H:%M:%S")
                row_data.append(update_text)
            else:
                row_data.append("-")

            table.add_row(*row_data)

        # 添加底部统计 - 简洁格式
        total_alerts = sum(s.total_alerts for s in statistics.values())
        table.caption = f"[bold cyan]报警: {total_alerts}次[/] | [dim]刷新: {self.config.display.refresh_interval}秒 | Ctrl+C 退出[/]"

        return table

    async def shutdown(self):
        """关闭"""
        print("\n⏸️  正在关闭...")
        self._should_stop = True

        if self.service:
            await self.service.stop()

        print("✅ 已关闭")


async def main():
    """主函数"""
    # 默认配置文件
    config_file = "config/price_alert/binance_alert.yaml"

    # 从命令行参数获取配置文件
    if len(sys.argv) > 1:
        config_file = sys.argv[1]

    # 静默启动，不显示启动信息（避免干扰UI）
    import os
    os.system('clear' if os.name == 'posix' else 'cls')  # 清屏
    
    # 创建应用
    app = PriceAlertApp(config_file)
    
    # 简化启动消息（避免干扰UI）
    print("🚀 价格监控系统启动中...")
    print("⏳ 连接交易所...\n")

    # 设置信号处理（用于优雅关闭）
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        """信号处理器 - 设置停止标志"""
        print("\n⚠️  收到退出信号...")
        app._should_stop = True
    
    # 注册信号处理器（asyncio方式）
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        # 初始化
        if not await app.initialize():
            print("❌ 初始化失败，退出程序")
            return

        # 运行
        await app.run()
    finally:
        # 移除信号处理器
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.remove_signal_handler(sig)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("\n✅ 程序正常退出")
    except KeyboardInterrupt:
        print("\n✅ 程序已退出 (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ 程序异常退出: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

