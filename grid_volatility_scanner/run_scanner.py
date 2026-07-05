"""
网格波动率扫描器 - 启动脚本

使用方法：
    python grid_volatility_scanner/run_scanner.py --duration 3600
    python grid_volatility_scanner/run_scanner.py --exchange lighter --duration 1800
"""

import asyncio
import argparse
import logging
import sys
from pathlib import Path
from datetime import datetime

# 🔥 先添加项目根目录到路径，再导入模块
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入扫描器（必须在添加 sys.path 之后）
from grid_volatility_scanner.scanner import GridVolatilityScanner


class TradeOnlyFilter(logging.Filter):
    """
    只允许成交相关的日志通过的过滤器
    用于BTC日志文件，只记录买入成交、卖出成交和循环完成，过滤掉普通的价格更新
    """

    def filter(self, record):
        msg = record.getMessage()
        # 允许通过的日志：成交、循环完成、启动标记
        return any(keyword in msg for keyword in ['成交', '完成', '启动', '====='])


class ImmediateFileHandler(logging.FileHandler):
    """
    立即写入的文件Handler
    每次emit后立即flush，确保日志实时写入文件
    """

    def emit(self, record):
        super().emit(record)
        self.flush()  # 每次写入后立即flush到磁盘


def setup_logging(log_level: str = "INFO"):
    """
    设置日志系统 - 记录扫描器日志和BTC的详细日志

    Args:
        log_level: 日志级别
    """
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 配置日志格式
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    date_format = '%H:%M:%S'

    # 🔥 1. 扫描器总日志（记录订阅统计、系统信息等）
    scanner_log_file = log_dir / f"grid_scanner_main_{timestamp}.log"
    scanner_file_handler = ImmediateFileHandler(
        scanner_log_file, encoding='utf-8', mode='a')
    scanner_file_handler.setLevel(logging.DEBUG)
    scanner_file_handler.setFormatter(logging.Formatter(log_format, date_format))
    
    # 为scanner模块添加文件handler
    scanner_logger = logging.getLogger('grid_volatility_scanner.scanner')
    scanner_logger.setLevel(logging.DEBUG)
    scanner_logger.addHandler(scanner_file_handler)
    scanner_logger.propagate = True  # 允许传播到UI handler
    
    # 写入启动标记
    scanner_logger.info("=" * 80)
    scanner_logger.info("🎯 网格波动率扫描器日志系统启动")
    scanner_logger.info("=" * 80)

    # 🔥 2. BTC专用日志文件（只记录成交）
    btc_log_file = log_dir / f"grid_scanner_BTC_{timestamp}.log"
    btc_file_handler = ImmediateFileHandler(
        btc_log_file, encoding='utf-8', mode='a')
    btc_file_handler.setLevel(logging.DEBUG)
    btc_file_handler.setFormatter(logging.Formatter(log_format, date_format))
    # 🔥 添加过滤器：只记录成交日志，过滤掉普通价格更新
    btc_file_handler.addFilter(TradeOnlyFilter())

    # 只为virtual_grid模块添加BTC日志handler
    vgrid_logger = logging.getLogger(
        'grid_volatility_scanner.models.virtual_grid')
    vgrid_logger.setLevel(logging.DEBUG)
    vgrid_logger.addHandler(btc_file_handler)
    vgrid_logger.propagate = True  # 允许传播到UI handler

    # 写入启动标记到BTC日志
    vgrid_logger.info("=" * 80)
    vgrid_logger.info("🎯 BTC专用日志系统启动")
    vgrid_logger.info("=" * 80)

    print(f"✅ 扫描器主日志: {scanner_log_file}")
    print(f"   📝 记录订阅统计、系统信息、错误详情")
    print(f"✅ BTC专用日志: {btc_log_file}")
    print(f"   📝 只记录BTC的买入成交、卖出成交事件")

    return scanner_log_file, scanner_file_handler, btc_log_file, btc_file_handler


async def create_exchange_adapter(exchange_name: str):
    """
    创建交易所适配器（仅用于公开市场数据，无需API密钥）

    Args:
        exchange_name: 交易所名称（lighter/hyperliquid/backpack等）

    Returns:
        交易所适配器实例
    """
    from core.adapters.exchanges import get_exchange_factory, ExchangeConfig, ExchangeType

    print(f"🔌 正在连接交易所: {exchange_name}")

    try:
        # 创建最小配置对象（仅用于公开市场数据）
        # 由于只是订阅WebSocket的公开数据流，不需要真实的API密钥
        exchange_config = ExchangeConfig(
            exchange_id=exchange_name,
            name=exchange_name.capitalize(),
            exchange_type=ExchangeType.FUTURES,  # 默认使用FUTURES类型
            api_key="",  # 空密钥（仅获取公开数据）
            api_secret="",  # 空密钥（仅获取公开数据）
            testnet=False
        )

        print(f"   📡 仅订阅公开市场数据，无需API密钥")

        # 获取交易所工厂
        factory = get_exchange_factory()

        # 创建适配器（传入配置）
        adapter = factory.create_adapter(exchange_name, config=exchange_config)

        # 连接交易所（异步方法）
        print(f"   连接中...")
        await adapter.connect()

        print(f"✅ 交易所连接成功: {exchange_name}")
        return adapter

    except Exception as e:
        print(f"❌ 连接交易所失败: {e}")
        import traceback
        traceback.print_exc()
        raise


async def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='网格波动率扫描器 - 分析市场波动性并推荐网格交易标的',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 扫描1小时
  python grid_volatility_scanner/run_scanner.py --duration 3600
  
  # 扫描30分钟，Hyperliquid交易所
  python grid_volatility_scanner/run_scanner.py --exchange hyperliquid --duration 1800
  
  # 使用自定义配置文件
  python grid_volatility_scanner/run_scanner.py --config custom_config.yaml
        """
    )

    parser.add_argument(
        '--exchange',
        type=str,
        default='lighter',
        choices=['lighter', 'hyperliquid', 'backpack', 'binance'],
        help='交易所名称（默认: lighter）'
    )

    parser.add_argument(
        '--duration',
        type=int,
        default=None,
        help='扫描时长（秒）（可选，默认持续运行直到Ctrl+C）'
    )

    parser.add_argument(
        '--config',
        type=str,
        default=None,
        help='配置文件路径（默认: config/market_config.yaml）'
    )

    parser.add_argument(
        '--log-level',
        type=str,
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='日志级别（默认: INFO）'
    )

    parser.add_argument(
        '--no-ui',
        action='store_true',
        help='禁用终端 UI(背景模式,結果定時輸出到 stdout,適合 PM2/systemd)'
    )

    # 2026-06-27 新增:手動覆蓋交易所費率(例如 VIP 變動、促銷活動時)
    parser.add_argument(
        '--fee-override',
        type=float,
        default=None,
        help='手動覆蓋雙邊手續費率(單位 %% 數值,如 0.003 表示 0.003%%)'
    )

    args = parser.parse_args()

    # 打印启动信息
    print("=" * 80)
    print("🎯 网格波动率扫描器 - Grid Volatility Scanner v1.0")
    print("=" * 80)
    print(f"📊 交易所: {args.exchange}")

    if args.duration:
        print(f"⏱️  运行模式: 定时扫描 ({args.duration} 秒 = {args.duration//60} 分钟)")
    else:
        print(f"⏱️  运行模式: 持续监控 (实时计算过去5分钟APR)")

    print(f"📝 日志级别: {args.log_level}")
    if args.config:
        print(f"⚙️  配置文件: {args.config}")
    print("=" * 80)
    print()

    # 设置日志
    scanner_log_file, scanner_file_handler, btc_log_file, btc_file_handler = setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    try:
        # 1. 创建交易所适配器
        adapter = await create_exchange_adapter(args.exchange)

        # 2. 创建扫描器
        print("🔧 正在初始化扫描器...")
        scanner = GridVolatilityScanner(
            exchange_adapter=adapter,
            config_path=args.config
        )

        # 2.5 提前注入 CLI 覆蓋項(2026-06-27 新增)
        #   必須在 initialize() 之前,否則 _resolve_exchange_id 找不到 _active_exchange_id
        scanner.scanner_config['_active_exchange_id'] = args.exchange
        if args.fee_override is not None:
            # CLI 覆蓋優先於 yaml 的 exchanges.<id>.fee_rate_percent
            scanner.scanner_config['fee_rate_percent'] = args.fee_override
            print(f"⚙️  手動覆蓋手續費率: {args.fee_override}%")

        # 3. 初始化扫描器
        await scanner.initialize()

        # 🔥 重新确保scanner和BTC文件handler以及UI日志handler正确配置
        
        # 1. Scanner主日志handler
        scanner_logger = logging.getLogger('grid_volatility_scanner.scanner')
        has_scanner_handler = any(
            isinstance(h, ImmediateFileHandler) and
            hasattr(h, 'baseFilename') and
            'main' in str(h.baseFilename)
            for h in scanner_logger.handlers
        )
        if not has_scanner_handler:
            scanner_logger.addHandler(scanner_file_handler)
            scanner_logger.setLevel(logging.DEBUG)
            scanner_logger.propagate = True
            scanner_logger.info("✅ Scanner主日志handler已重新配置")
            print("✅ 已重新配置Scanner主日志handler")
        
        # 2. BTC专用日志handler
        vgrid_logger = logging.getLogger(
            'grid_volatility_scanner.models.virtual_grid')
        has_btc_handler = any(
            isinstance(h, ImmediateFileHandler) and
            hasattr(h, 'baseFilename') and
            'BTC' in str(h.baseFilename)
            for h in vgrid_logger.handlers
        )
        if not has_btc_handler:
            # 重新添加BTC文件handler
            vgrid_logger.addHandler(btc_file_handler)
            vgrid_logger.setLevel(logging.DEBUG)
            vgrid_logger.propagate = True
            # 写入测试日志验证
            vgrid_logger.info("✅ BTC文件日志handler已重新配置")
            print("✅ 已重新配置BTC文件日志handler")

        # 🔥 确保UI日志handler存在（用于终端显示）
        # UI handler应该已经在ScannerUI初始化时添加，但这里再次确认
        if scanner.ui and scanner.ui.ui_log_handler:
            ui_handler = scanner.ui.ui_log_handler
            if ui_handler not in vgrid_logger.handlers:
                vgrid_logger.addHandler(ui_handler)
                vgrid_logger.setLevel(logging.DEBUG)
                vgrid_logger.propagate = True
                vgrid_logger.info("✅ BTC UI日志handler已配置")
                print("✅ 已配置BTC UI日志handler（终端显示）")

        # 4. 开始扫描
        if args.duration:
            print(f"\n🚀 开始扫描（{args.duration}秒）...")
            print("📌 提示: 按 Ctrl+C 可随时停止扫描\n")
            await scanner.scan(duration_seconds=args.duration, no_ui=args.no_ui)
        else:
            print("\n🚀 开始持续监控...")
            print("📌 提示: 按 Ctrl+C 停止监控\n")
            print("📊 实时APR计算: 基于过去5分钟的循环数据\n")
            await scanner.scan(no_ui=args.no_ui)  # 持续运行,不传递时长

        # 5. 打印摘要
        scanner.print_summary()

        print("\n✅ 扫描完成！")
        print(f"📁 扫描器主日志: {scanner_log_file}")
        print(f"📁 BTC成交日志: {btc_log_file}")

    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断扫描")
        logger.info("用户中断扫描")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        logger.error(f"扫描失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 再见！")
