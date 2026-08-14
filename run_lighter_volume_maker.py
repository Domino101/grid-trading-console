#!/usr/bin/env python3
"""
Lighter市价刷量交易主程序（支持Backpack/Hyperliquid信号源）

架构：
- 信号源：Backpack（REST API）或 Hyperliquid（WebSocket）
- 执行端：Lighter（执行市价交易）
- 模式：仅市价模式

特性：
- 支持多信号源选择（Backpack/Hyperliquid）
- Hyperliquid使用WebSocket实现低延迟
- 完全独立的脚本，不影响原Backpack刷量脚本
- 复用原脚本的所有判断逻辑
- 简单高效的市价交易
"""

from core.services.volume_maker.terminal_ui import VolumeMakerTerminalUI
from core.services.volume_maker.models.volume_maker_config import VolumeMakerConfig
from core.services.volume_maker.implementations.lighter_market_volume_maker_service import LighterMarketVolumeMakerService
from core.adapters.exchanges.interface import ExchangeConfig, ExchangeType
from core.adapters.exchanges.factory import get_exchange_factory
import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional
import yaml

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))


class LighterVolumeMakerApp:
    """Lighter刷量交易应用"""

    def __init__(self, config_file: str):
        """
        初始化应用

        Args:
            config_file: 配置文件路径
        """
        self.config_file = config_file
        self.config: Optional[VolumeMakerConfig] = None
        self.service: Optional[LighterMarketVolumeMakerService] = None
        self.ui: Optional[VolumeMakerTerminalUI] = None

        self.signal_adapter = None  # Backpack适配器
        self.execution_adapter = None  # Lighter适配器

        self._stop_requested = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _setup_signal_handlers(self):
        """设置信号处理器"""
        import sys
        import os

        self._stop_count = 0  # 记录Ctrl+C次数

        def signal_handler():
            """信号处理器"""
            self._stop_count += 1

            if self._stop_count == 1:
                # 第一次Ctrl+C：优雅退出
                print("\n")
                print("=" * 70)
                print("🛑 收到停止信号 - 正在优雅退出...")
                print("=" * 70)
                print("   提示：再按一次 Ctrl+C 可立即强制退出")
                print("=" * 70)
                self._stop_requested = True

                # 🔥 调用服务的 stop() 方法来优雅退出
                # stop() 方法会输出完整的统计信息和清理日志
                if self.service:
                    # 先设置停止标志（让主循环可以快速响应）
                    self.service._should_stop = True

                    # 然后异步调用 stop() 方法（输出统计、清理资源）
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self.service.stop(),
                            self._loop
                        )

                if self.ui:
                    try:
                        self.ui.stop()
                    except Exception:
                        pass

            elif self._stop_count == 2:
                # 第二次Ctrl+C：立即强制退出
                print("\n")
                print("=" * 70)
                print("⚡ 收到第二次停止信号 - 立即强制退出")
                print("=" * 70)
                os._exit(0)

        try:
            if self._loop and hasattr(self._loop, 'add_signal_handler'):
                for sig in (signal.SIGINT, signal.SIGTERM):
                    self._loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    async def _safe_stop(self):
        """安全停止服务"""
        try:
            print("⏸️  正在停止服务...")
            if self.service:
                await self.service.stop()
            print("✅ 服务已停止")
        except Exception as e:
            print(f"⚠️  停止服务时出错: {e}")

    def load_config(self) -> bool:
        """加载配置"""
        try:
            config_path = Path(self.config_file)
            if not config_path.exists():
                print(f"❌ 配置文件不存在: {config_path}")
                return False

            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)

            self.config = VolumeMakerConfig.from_dict(config_data)

            # 强制设置为市价模式
            self.config.order_mode = 'market'

            print(f"✅ 配置文件加载成功: {config_path}")
            return True

        except Exception as e:
            print(f"❌ 加载配置文件失败: {e}")
            return False

    def load_exchange_config(self, exchange_name: str) -> Optional[ExchangeConfig]:
        """
        加载交易所配置

        Args:
            exchange_name: 交易所名称（backpack、hyperliquid 或 lighter）
        """
        try:
            exchange_config_file = Path(
                f"config/exchanges/{exchange_name}_config.yaml")

            if not exchange_config_file.exists():
                print(f"❌ 交易所配置文件不存在: {exchange_config_file}")
                return None

            with open(exchange_config_file, 'r', encoding='utf-8') as f:
                exchange_data = yaml.safe_load(f)

            # 根据交易所类型解析配置
            if exchange_name == "backpack":
                # Backpack配置
                exchange_conf = exchange_data.get('backpack', {})
                auth_conf = exchange_conf.get('authentication', {})
                api_conf = exchange_conf.get('api', {})

                api_key = auth_conf.get('api_key', '')
                api_secret = auth_conf.get('private_key', '')

                config = ExchangeConfig(
                    exchange_id="backpack",
                    name=exchange_conf.get('name', 'Backpack'),
                    exchange_type=ExchangeType(
                        exchange_conf.get('type', 'spot')),
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=exchange_conf.get('testnet', False),
                    base_url=api_conf.get('base_url'),
                    ws_url=api_conf.get('ws_url'),
                    default_leverage=exchange_conf.get('default_leverage', 1),
                    default_margin_mode=exchange_conf.get(
                        'default_margin_mode', 'cross')
                )

            elif exchange_name == "hyperliquid":
                # Hyperliquid配置
                exchange_conf = exchange_data.get('hyperliquid', {})
                auth_conf = exchange_conf.get('authentication', {})
                api_conf = exchange_conf.get('api', {})

                api_key = auth_conf.get('api_key', '')
                api_secret = auth_conf.get('private_key', '')

                config = ExchangeConfig(
                    exchange_id="hyperliquid",
                    name=exchange_conf.get('name', 'Hyperliquid'),
                    exchange_type=ExchangeType(
                        exchange_conf.get('type', 'perpetual')),
                    api_key=api_key,
                    api_secret=api_secret,
                    testnet=exchange_conf.get('testnet', False),
                    base_url=api_conf.get('base_url'),
                    ws_url=api_conf.get('ws_url'),
                    default_leverage=exchange_conf.get('default_leverage', 1),
                    default_margin_mode=exchange_conf.get(
                        'default_margin_mode', 'cross')
                )

            elif exchange_name == "lighter":
                # Lighter配置
                api_config = exchange_data.get('api_config', {})
                auth_config = api_config.get('auth', {})

                # Lighter使用字典配置
                config = {
                    "testnet": api_config.get('testnet', False),
                    "api_key_private_key": auth_config.get('api_key_private_key', ''),
                    "account_index": auth_config.get('account_index', 0),
                    "api_key_index": auth_config.get('api_key_index', 0),
                    "base_url": api_config.get('base_url', ''),
                }

            else:
                print(f"❌ 不支持的交易所: {exchange_name}")
                return None

            # 显示配置加载状态
            if isinstance(config, ExchangeConfig):
                if config.api_key and config.api_secret:
                    masked_key = f"{config.api_key[:8]}...{config.api_key[-4:]}" if len(
                        config.api_key) > 12 else "***"
                    print(f"✅ {exchange_name.upper()}配置加载成功")
                    print(f"   API Key: {masked_key}")
            else:
                if config.get('api_key_private_key'):
                    masked_key = f"{config['api_key_private_key'][:10]}..."
                    print(f"✅ {exchange_name.upper()}配置加载成功")
                    print(f"   API Key: {masked_key}")

            return config

        except Exception as e:
            print(f"❌ 加载{exchange_name}配置失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    async def initialize(self) -> bool:
        """初始化"""
        try:
            # 加载配置
            if not self.load_config():
                return False

            # 🔥 根据配置选择信号源交易所
            signal_exchange = self.config.signal_exchange.lower()
            print(f"🔧 信号源交易所: {signal_exchange.upper()}")

            # 🔥 加载信号源配置
            print(f"🔧 加载{signal_exchange.upper()}配置（信号源）...")
            signal_config = self.load_exchange_config(signal_exchange)
            if not signal_config:
                return False

            # 🔥 加载Lighter配置（执行端）
            print(f"🔧 加载Lighter配置（执行端）...")
            lighter_config = self.load_exchange_config("lighter")
            if not lighter_config:
                return False

            # 🔥 创建信号源适配器
            print(f"🔧 创建{signal_exchange.upper()}适配器（信号源）...")
            factory = get_exchange_factory()

            if signal_exchange == "hyperliquid":
                # 🔥 Hyperliquid使用完整适配器（包含REST+WebSocket）
                from core.adapters.exchanges.adapters.hyperliquid import HyperliquidAdapter
                self.signal_adapter = HyperliquidAdapter(config=signal_config)
                # 🔥 手动调用 connect() 确保连接建立
                print(f"🔧 Hyperliquid适配器已创建，开始连接...")
                if not await self.signal_adapter.connect():
                    print(f"❌ Hyperliquid连接失败")
                    return False
                print(f"✅ Hyperliquid适配器已连接（支持REST+WebSocket）")
            else:
                # 🔥 Backpack使用REST适配器（传统方式）
                self.signal_adapter = factory.create_adapter(
                    exchange_id=signal_exchange,
                    config=signal_config
                )

            # 🔥 创建Lighter适配器（执行端）- 直接使用LighterRest
            print(f"🔧 创建Lighter适配器（执行端）...")
            from core.adapters.exchanges.adapters.lighter_rest import LighterRest
            
            # 🔥 将滑点配置从volume_maker_config传递到lighter_config
            lighter_config['slippage'] = str(self.config.slippage)
            
            self.execution_adapter = LighterRest(config=lighter_config)
            await self.execution_adapter.initialize()

            # 🔥 创建Lighter刷量服务（双适配器）
            print("🔧 创建Lighter刷量服务...")
            self.service = LighterMarketVolumeMakerService(
                signal_adapter=self.signal_adapter,
                execution_adapter=self.execution_adapter
            )

            # 初始化服务
            print("🔧 初始化Lighter刷量服务...")
            if not await self.service.initialize(self.config):
                return False

            # 创建终端UI
            if self.config.ui.enabled:
                print("🔧 创建终端UI...")
                self.ui = VolumeMakerTerminalUI(self.service)

            print("✅ 初始化完成")
            return True

        except Exception as e:
            print(f"❌ 初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def run(self) -> None:
        """运行应用"""
        try:
            # 获取当前事件循环并设置信号处理器
            self._loop = asyncio.get_running_loop()
            self._setup_signal_handlers()

            # 启动服务
            print("🚀 启动Lighter刷量服务...")
            await self.service.start()

            # 如果启用UI，运行UI
            if self.ui:
                print("🎨 启动终端UI...")
                ui_task = asyncio.create_task(self.ui.run())

                # 等待UI完成或停止请求
                while not self._stop_requested and self.service.is_running():
                    await asyncio.sleep(0.5)
                    if ui_task.done():
                        break

                # 如果是停止请求，取消UI任务
                if not ui_task.done():
                    ui_task.cancel()
                    try:
                        await ui_task
                    except asyncio.CancelledError:
                        pass
            else:
                # 否则等待服务完成或停止请求
                while not self._stop_requested and self.service.is_running():
                    await asyncio.sleep(0.5)

        except KeyboardInterrupt:
            print("\n\n🛑 检测到 Ctrl+C，正在停止...")
        except Exception as e:
            print(f"❌ 运行出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        """清理资源"""
        try:
            print("\n🧹 清理资源...")

            # 停止UI
            if self.ui:
                try:
                    self.ui.stop()
                    print("  ✅ UI已停止")
                except Exception as e:
                    print(f"  ⚠️  停止UI失败: {e}")

            # 停止服务
            if self.service and self.service.is_running():
                try:
                    print("  ⏸️  正在停止Lighter刷量服务...")
                    await asyncio.wait_for(self.service.stop(), timeout=10.0)
                    print("  ✅ Lighter刷量服务已停止")
                except asyncio.TimeoutError:
                    print("  ⚠️  停止服务超时（10秒）")
                except Exception as e:
                    print(f"  ⚠️  停止服务失败: {e}")

            # 断开信号源连接
            if self.signal_adapter and hasattr(self.signal_adapter, 'is_connected'):
                try:
                    signal_exchange = self.config.signal_exchange.upper() if self.config else "信号源"
                    if self.signal_adapter.is_connected():
                        print(f"  ⏸️  正在断开{signal_exchange}连接...")
                        await asyncio.wait_for(self.signal_adapter.disconnect(), timeout=5.0)
                        print(f"  ✅ {signal_exchange}连接已断开")
                except Exception as e:
                    print(f"  ⚠️  断开信号源连接失败: {e}")

            # 断开Lighter连接
            if self.execution_adapter and hasattr(self.execution_adapter, 'is_connected'):
                try:
                    if self.execution_adapter.is_connected():
                        print("  ⏸️  正在断开Lighter连接...")
                        await asyncio.wait_for(self.execution_adapter.disconnect(), timeout=5.0)
                        print("  ✅ Lighter连接已断开")
                except Exception as e:
                    print(f"  ⚠️  断开Lighter连接失败: {e}")

            print("\n✅ 清理完成\n")

        except Exception as e:
            print(f"\n⚠️  清理过程出错: {e}\n")


async def main():
    """主函数"""
    # 默认配置文件
    config_file = "config/volume_maker/lighter_volume_maker.yaml"

    # 从命令行参数获取配置文件
    if len(sys.argv) > 1:
        config_file = sys.argv[1]

    print("=" * 70)
    print("🎯 Lighter市价刷量系统（多信号源支持）v2.0")
    print("=" * 70)
    print(f"配置文件: {config_file}")
    print()

    # 创建应用
    app = LighterVolumeMakerApp(config_file)

    # 初始化
    if not await app.initialize():
        print("❌ 初始化失败，退出程序")
        return

    # 运行
    try:
        await app.run()
    finally:
        # 清理资源
        await app.cleanup()

    print()
    print("=" * 70)
    print("✅ Lighter刷量系统已安全退出")
    print("=" * 70)
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # 这个不应该被触发，因为信号处理器会处理Ctrl+C
        print("\n⚠️  程序被键盘中断")
    except Exception as e:
        print(f"\n❌ 程序异常退出: {e}")
        import traceback
        traceback.print_exc()
