#!/usr/bin/env python3
"""
网格交易系统启动脚本

独立启动网格交易系统

⚠️ 【重要】Lighter 交易所用户必读：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
由于 Lighter SDK (v0.1.4) 的底层 C 库存在 Bug，脚本无法自动设置逐仓（isolated）模式。

🔧 解决方案：
1. 登录 Lighter 交易所网站 (https://app.lighter.xyz)
2. 手动为每个交易对设置保证金模式和杠杆倍数
3. 设置完成后，运行本脚本即可正常交易

📝 建议配置：
   - 主流币（BTC/ETH）：Cross 或 Isolated，10-20倍杠杆
   - 小市值币（MEGA/VIRTUAL）：Cross 或 Isolated，3-5倍杠杆
   - 做空操作：建议至少 3 倍杠杆

💡 提示：
   - 配置文件中的 margin_mode 和 leverage 仅作为参考，不会自动设置
   - 网页端设置一次后，脚本会使用该设置，无需每次重复设置
   - 详细说明见：docs/fixes/lighter_margin_mode_sdk_bug.md
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from core.adapters.exchanges.utils import setup_optimized_logging
from core.adapters.exchanges.models import ExchangeType
from core.adapters.exchanges import ExchangeFactory, ExchangeConfig
from core.services.grid.terminal_ui import GridTerminalUI
from core.services.grid.coordinator import GridCoordinator
from core.services.grid.implementations import (
    GridStrategyImpl,
    GridEngineImpl,
    PositionTrackerImpl
)
from core.services.grid.models import GridConfig, GridType, GridState
from core.services.grid.reserve import (
    SpotReserveManager,
    ReserveMonitor,
    check_spot_reserve_on_startup
)
from core.logging import get_system_logger
import sys
import asyncio
import yaml
from pathlib import Path
from decimal import Decimal
import argparse
import logging
import os
import hashlib

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 🔥 配置优化的日志系统（简洁清晰，不丢失信息）
setup_optimized_logging(use_colored=True)

def acquire_lock(config_path: str) -> str:
    """
    获取进程锁，防止同一配置文件被重复运行。如果发现旧进程，则自动将其结束。
    """
    import time
    import signal
    # 基于配置文件路径生成唯一的锁文件名称
    abs_path = os.path.abspath(config_path)
    config_hash = hashlib.md5(abs_path.encode('utf-8')).hexdigest()
    config_name = Path(config_path).stem
    lock_file = f"/tmp/grid_{config_name}_{config_hash[:8]}.pid"

    if os.path.exists(lock_file):
        with open(lock_file, 'r') as f:
            old_pid = f.read().strip()
        if old_pid.isdigit():
            old_pid_int = int(old_pid)
            # 检查该PID是否仍在运行
            try:
                os.kill(old_pid_int, 0)
                # 如果没有抛出异常，说明进程存在
                print(f"⚠️ 发现旧进程 (PID: {old_pid}) 正在运行配置文件 {config_path}")
                print(f"🔄 正在尝试自动结束旧进程...")
                
                try:
                    # 发送 SIGTERM 信号要求优雅退出
                    os.kill(old_pid_int, signal.SIGTERM)
                    
                    # 等待最多 5 秒
                    for _ in range(50):
                        try:
                            os.kill(old_pid_int, 0)
                            time.sleep(0.1)
                        except OSError:
                            # 进程已退出
                            break
                    else:
                        # 如果 5 秒后进程仍然存在，强制结束
                        print(f"⏳ 旧进程未响应，正在强制结束 (SIGKILL)...")
                        try:
                            os.kill(old_pid_int, signal.SIGKILL)
                            time.sleep(0.5)
                        except OSError:
                            pass
                except PermissionError:
                    print(f"❌ 启动失败：没有权限结束旧进程 (PID: {old_pid})，请使用 root 权限或手动结束！")
                    sys.exit(1)
                except OSError as e:
                    print(f"⚠️ 结束旧进程时发生异常: {e}")
                    
                print(f"✅ 旧进程已成功结束")
            except OSError:
                # 进程不存在，说明是残留的锁文件
                pass

    # 写入当前PID
    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))
    
    return lock_file


# 导入交易所适配器


async def load_config(config_path: str) -> dict:
    """
    加载配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        print(f"❌ 加载配置文件失败: {e}")
        raise


def load_grid_defaults() -> dict:
    """
    載入全局網格默認配置

    優先級：代碼硬編碼默認值 ← defaults.yaml ← 個別幣對 YAML

    Returns:
        defaults.yaml 中 grid_defaults 區塊的字典，若文件不存在則返回空字典
    """
    defaults_path = Path("config/grid/defaults.yaml")
    if defaults_path.exists():
        try:
            with open(defaults_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            defaults = data.get('grid_defaults', {})
            if defaults:
                print(f"   ✓ 已載入全局默認配置: {defaults_path} ({len(defaults)} 個參數)")
            return defaults
        except Exception as e:
            print(f"   ⚠️ 載入全局默認配置失敗: {e}，使用代碼級默認值")
            return {}
    return {}


def create_grid_config(config_data: dict) -> GridConfig:
    """
    创建网格配置对象

    🔥 重構（2026-05）：參數分層加載
    優先級：代碼默認值 ← defaults.yaml ← 個別幣對 YAML

    Args:
        config_data: 配置数据

    Returns:
        网格配置对象
    """
    # 載入全局默認值
    defaults = load_grid_defaults()
    grid_config = config_data['grid_system']
    grid_type = GridType(grid_config['grid_type'])

    # 🔥 合併工具函數：個別 YAML → defaults.yaml → 代碼默認值
    def get(key, code_fallback=None):
        """依優先級查找參數值"""
        if key in grid_config:
            return grid_config[key]
        if key in defaults:
            return defaults[key]
        return code_fallback

    # 基础参数
    params = {
        'exchange': grid_config['exchange'],
        'symbol': grid_config['symbol'],
        'grid_type': grid_type,
        'order_amount': Decimal(str(grid_config['order_amount'])),
        'max_position': Decimal(str(grid_config.get('max_position'))) if grid_config.get('max_position') else None,
        'enable_notifications': get('enable_notifications', False),
        'order_health_check_enabled': get('order_health_check_enabled', True),
        'order_health_check_interval': get('order_health_check_interval', 600),
        'rest_position_query_interval': get('rest_position_query_interval', 1),
        'fee_rate': Decimal(str(get('fee_rate', '0.0001'))),
    }

    # 🔥 精度參數（可選：省略時由 auto_fill_precision() 自動從交易所獲取）
    qty_prec = get('quantity_precision')
    price_dec = get('price_decimals')
    if qty_prec is not None:
        params['quantity_precision'] = int(qty_prec)
    if price_dec is not None:
        params['price_decimals'] = int(price_dec)

    # 🔥 網格密度參數：grid_count 與 grid_interval（二選一）
    if 'grid_count' in grid_config:
        params['_grid_count_input'] = int(grid_config['grid_count'])
    if 'grid_interval' in grid_config:
        params['grid_interval'] = Decimal(str(grid_config['grid_interval']))

    # 🔥 价格移动网格：使用 follow_grid_count
    if grid_type in [GridType.FOLLOW_LONG, GridType.FOLLOW_SHORT]:
        params['follow_grid_count'] = grid_config['follow_grid_count']
        params['follow_timeout'] = grid_config.get('follow_timeout', 300)
        params['follow_distance'] = grid_config.get('follow_distance', 1)
        params['price_offset_grids'] = grid_config.get(
            'price_offset_grids', 0)  # 🆕 价格偏移网格数
        # lower_price 和 upper_price 保持默认值 None
    else:
        # 普通网格和马丁网格：从 price_range 读取
        params['lower_price'] = Decimal(
            str(grid_config['price_range']['lower_price']))
        params['upper_price'] = Decimal(
            str(grid_config['price_range']['upper_price']))

    # 🔥 马丁网格：添加 martingale_increment
    if 'martingale_increment' in grid_config:
        params['martingale_increment'] = Decimal(
            str(grid_config['martingale_increment']))

    # 🔥 剥头皮模式：添加剥头皮参数
    if 'scalping_enabled' in grid_config:
        params['scalping_enabled'] = grid_config['scalping_enabled']
    if 'scalping_trigger_percent' in grid_config:
        params['scalping_trigger_percent'] = grid_config['scalping_trigger_percent']
    if 'scalping_take_profit_grids' in grid_config:
        params['scalping_take_profit_grids'] = grid_config['scalping_take_profit_grids']

    # 🧠 智能剥头皮模式：添加智能剥头皮参数
    if 'smart_scalping_enabled' in grid_config:
        params['smart_scalping_enabled'] = grid_config['smart_scalping_enabled']
    if 'allowed_deep_drops' in grid_config:
        params['allowed_deep_drops'] = grid_config['allowed_deep_drops']
    if 'min_drop_threshold_percent' in grid_config:
        params['min_drop_threshold_percent'] = grid_config['min_drop_threshold_percent']

    # 🛡️ 本金保护模式：添加本金保护参数
    if 'capital_protection_enabled' in grid_config:
        params['capital_protection_enabled'] = grid_config['capital_protection_enabled']
    if 'capital_protection_trigger_percent' in grid_config:
        params['capital_protection_trigger_percent'] = grid_config['capital_protection_trigger_percent']

    # 💰 止盈模式：添加止盈参数
    if 'take_profit_enabled' in grid_config:
        params['take_profit_enabled'] = grid_config['take_profit_enabled']
    if 'take_profit_percentage' in grid_config:
        params['take_profit_percentage'] = Decimal(
            str(grid_config['take_profit_percentage']))

    # 🔒 价格锁定模式：添加价格锁定参数
    if 'price_lock_enabled' in grid_config:
        params['price_lock_enabled'] = grid_config['price_lock_enabled']
    if 'price_lock_threshold' in grid_config:
        params['price_lock_threshold'] = Decimal(
            str(grid_config['price_lock_threshold']))
    if 'price_lock_start_at_threshold' in grid_config:
        params['price_lock_start_at_threshold'] = grid_config['price_lock_start_at_threshold']

    # 🎯 反手挂单参数：添加反手挂单格子距离参数
    reverse_dist = get('reverse_order_grid_distance')
    if reverse_dist is not None:
        params['reverse_order_grid_distance'] = int(reverse_dist)

    # 🛑 止损保护模式：添加止损保护参数
    if 'stop_loss_protection_enabled' in grid_config:
        params['stop_loss_protection_enabled'] = grid_config['stop_loss_protection_enabled']
    if 'stop_loss_trigger_percent' in grid_config:
        params['stop_loss_trigger_percent'] = Decimal(
            str(grid_config['stop_loss_trigger_percent']))
    if 'stop_loss_escape_timeout' in grid_config:
        params['stop_loss_escape_timeout'] = int(
            grid_config['stop_loss_escape_timeout'])
    if 'stop_loss_apr_threshold' in grid_config:
        params['stop_loss_apr_threshold'] = Decimal(
            str(grid_config['stop_loss_apr_threshold']))

    # 🧹 退出清理模式：添加退出清理参数
    if 'exit_cleanup_enabled' in grid_config:
        params['exit_cleanup_enabled'] = grid_config['exit_cleanup_enabled']

    # 🔧 保证金模式：添加保证金模式参数（Lighter交易所专用）
    if 'margin_mode' in grid_config:
        params['margin_mode'] = str(grid_config['margin_mode'])

    # 🔥 杠杆倍数：添加杠杆倍数参数（合约交易专用）
    if 'leverage' in grid_config:
        params['leverage'] = int(grid_config['leverage'])

    # 🔥 现货预留管理配置（仅现货需要）
    if 'spot_reserve' in grid_config:
        params['spot_reserve'] = grid_config['spot_reserve']

    # 🔥 健康检查容错配置（合併 defaults.yaml）
    pos_tolerance = get('position_tolerance')
    if pos_tolerance is not None:
        params['position_tolerance'] = pos_tolerance

    # 📸 健康检查快照次数配置
    if 'health_check_snapshot_count' in grid_config:
        params['health_check_snapshot_count'] = int(
            grid_config['health_check_snapshot_count'])

    # 🔥 健康檢查行為開關（合併 defaults.yaml）
    hc_adj = get('health_check_adjust_position_enabled')
    if hc_adj is not None:
        params['health_check_adjust_position_enabled'] = hc_adj

    # 🆕 活躍窗口參數（合併 defaults.yaml）
    aw_each = get('active_window_each_side')
    if aw_each is not None:
        params['active_window_each_side'] = int(aw_each)
    aw_interval = get('active_window_refresh_interval')
    if aw_interval is not None:
        params['active_window_refresh_interval'] = int(aw_interval)

    # 🆕 ws_only_mode 参数（合併 defaults.yaml）
    ws_only = get('ws_only_mode')
    if ws_only is not None:
        params['ws_only_mode'] = ws_only
    rest_backup = get('rest_backup_enabled')
    if rest_backup is not None:
        params['rest_backup_enabled'] = rest_backup

    # 🆕 limit_only 参数（合併 defaults.yaml）
    limit_only = get('limit_only')
    if limit_only is not None:
        params['limit_only'] = limit_only

    return GridConfig(**params)



async def auto_fill_precision(grid_config: GridConfig, exchange_adapter, logger=None):
    """
    自動從交易所獲取精度信息

    🔥 若 YAML 中省略了 quantity_precision / price_decimals，
    此函數會從 exchange.load_markets() 的 precision 字段自動填入。
    若 YAML 中已手動指定，則跳過（以 YAML 為準）。

    Args:
        grid_config: 網格配置對象（將被原地修改）
        exchange_adapter: 交易所適配器（需已連接並執行過 load_markets）
        logger: 可選的日誌記錄器
    """
    log = logger or print

    try:
        # 嘗試從 adapter 中獲取已加載的市場信息
        exchange = getattr(exchange_adapter, 'exchange', None)
        if exchange is None:
            return

        markets = getattr(exchange, 'markets', None)
        if not markets:
            return

        symbol = grid_config.symbol
        if symbol not in markets:
            if callable(log):
                log(f"   ⚠️ 交易對 {symbol} 未在 markets 中找到，使用默認精度")
            return

        market = markets[symbol]
        precision = market.get('precision', {})

        # 從交易所數據中提取精度
        exchange_qty_prec = precision.get('amount')
        exchange_price_prec = precision.get('price')

        # 只在未手動指定時才自動填入
        # 注意：GridConfig.__post_init__ 已將 None 替換為兜底默認值（3/2），
        # 因此這裡需要在 create_grid_config 中記錄是否是手動指定的
        filled = False
        if exchange_qty_prec is not None:
            grid_config.quantity_precision = int(exchange_qty_prec)
            filled = True
        if exchange_price_prec is not None:
            grid_config.price_decimals = int(exchange_price_prec)
            filled = True

        if filled:
            print(f"   ✅ 自動獲取精度: quantity={grid_config.quantity_precision}位, price={grid_config.price_decimals}位")

    except Exception as e:
        print(f"   ⚠️ 自動獲取精度失敗，使用配置/默認值: {e}")


def detect_market_type(symbol: str, exchange_name: str) -> ExchangeType:

    """
    根据交易对符号自动检测市场类型

    Args:
        symbol: 交易对符号
        exchange_name: 交易所名称

    Returns:
        ExchangeType: 市场类型（现货或永续合约）
    """
    symbol_upper = symbol.upper()
    exchange_lower = exchange_name.lower()

    # Hyperliquid 交易所
    if exchange_lower == "hyperliquid":
        # Hyperliquid符号格式：
        # - 现货: BTC/USDC (没有后缀)
        # - 永续: BTC/USDC:USDC (后缀:USDC)
        if ":USDC" in symbol_upper or ":PERP" in symbol_upper or ":SPOT" in symbol_upper:
            # 有后缀的情况
            if ":SPOT" in symbol_upper:
                return ExchangeType.SPOT
            else:
                return ExchangeType.PERPETUAL
        else:
            # 🔥 没有后缀 → 现货（Hyperliquid的现货格式）
            return ExchangeType.SPOT

    # Backpack 交易所
    elif exchange_lower == "backpack":
        if "_PERP" in symbol_upper or "PERP" in symbol_upper:
            return ExchangeType.PERPETUAL
        elif "_SPOT" in symbol_upper or "SPOT" in symbol_upper:
            return ExchangeType.SPOT
        else:
            # 默认为永续合约
            return ExchangeType.PERPETUAL

    # Lighter 交易所
    elif exchange_lower == "lighter":
        # Lighter是永续合约交易所，所有交易对都是永续合约
        # 符号格式：BTC-USD, ETH-USD, SOL-USD等
        return ExchangeType.PERPETUAL

    # 其他交易所默认为永续合约
    else:
        return ExchangeType.PERPETUAL


async def create_exchange_adapter(config_data: dict):
    """
    创建交易所适配器

    Args:
        config_data: 配置数据

    Returns:
        交易所适配器
    """
    import os
    from pathlib import Path
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None

    grid_config = config_data['grid_system']
    exchange_name = grid_config['exchange'].lower()
    symbol = grid_config['symbol']

    # 🔥 自动检测市场类型（现货 vs 永续合约）
    market_type = detect_market_type(symbol, exchange_name)

    print(f"   - 市场类型: {market_type.value}")

    # 優先加載本地 .env 檔案（如果存在）
    if load_dotenv:
        load_dotenv(Path(".env"), override=False)

    # 再優先加載私密檔案（若存在）以填充環境變數
    secrets_path = Path(__file__).parent / ".secrets" / "binance.env"
    if secrets_path.exists() and not os.getenv("BINANCE_API_KEY"):
        if load_dotenv:
            load_dotenv(secrets_path, override=False)
        else:
            try:
                for line in secrets_path.read_text().splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k and v and not os.getenv(k):
                            os.environ[k] = v
            except Exception:
                pass

    # 优先级：环境变量 > 交易所配置文件 > 空字符串
    api_key = os.getenv(f"{exchange_name.upper()}_API_KEY")
    api_secret = os.getenv(f"{exchange_name.upper()}_API_SECRET")
    wallet_address = os.getenv(
        f"{exchange_name.upper()}_WALLET_ADDRESS")  # 用于 Hyperliquid

    # 如果环境变量没有设置，尝试从交易所配置文件读取
    if not api_key or not api_secret:
        try:
            exchange_config_path = Path(
                f"config/exchanges/{exchange_name}_config.yaml")
            if exchange_config_path.exists():
                with open(exchange_config_path, 'r', encoding='utf-8') as f:
                    exchange_config_data = yaml.safe_load(f)

                auth_config = exchange_config_data.get(
                    exchange_name, {}).get('authentication', {})

                # 🔥 修复：不同交易所使用不同的认证方式
                if exchange_name == "hyperliquid":
                    # Hyperliquid 使用 private_key 作为主密钥
                    api_key = api_key or auth_config.get('private_key', "")
                    api_secret = api_secret or auth_config.get(
                        'private_key', "")  # 同一个密钥
                    wallet_address = wallet_address or auth_config.get(
                        'wallet_address', "")
                elif exchange_name == "lighter":
                    # Lighter 使用 API Key私钥和账户索引
                    api_config = exchange_config_data.get('api_config', {})
                    auth_config = api_config.get('auth', {})
                    api_key = api_key or auth_config.get(
                        'api_key_private_key', "")
                    api_secret = api_secret or auth_config.get(
                        'api_key_private_key', "")
                    # Lighter特殊配置将在创建适配器时单独处理
                else:
                    # 其他交易所使用标准的 api_key/api_secret
                    api_key = api_key or auth_config.get('api_key', "")
                    api_secret = api_secret or auth_config.get(
                        'private_key', "") or auth_config.get('api_secret', "")
                    wallet_address = wallet_address or auth_config.get(
                        'wallet_address', "")

                if api_key and api_secret:
                    print(f"   ✓ 从配置文件读取API密钥: {exchange_config_path}")
                    if exchange_name == "hyperliquid" and wallet_address:
                        print(
                            f"   ✓ 钱包地址: {wallet_address[:10]}...{wallet_address[-6:]}")
        except Exception as e:
            print(f"   ⚠️  无法读取交易所配置文件: {e}")

    # 如果仍然没有密钥，给出警告
    if not api_key or not api_secret:
        print(f"   ⚠️  警告：未找到API密钥配置")
        print(
            f"   提示：请设置环境变量或在 config/exchanges/{exchange_name}_config.yaml 中配置")

        # 🔥 交易所特殊配置提示
        if exchange_name == "hyperliquid":
            print(f"   💡 Hyperliquid 需要配置:")
            print(f"      - private_key: 钱包私钥")
            print(f"      - wallet_address: 钱包地址")
        elif exchange_name == "lighter":
            print(f"   💡 Lighter 需要配置:")
            print(f"      - api_key_private_key: API Key私钥")
            print(f"      - account_index: 账户索引")
            print(f"      - api_key_index: API Key索引（默认0）")

    # 创建交易所配置
    if exchange_name == "lighter":
        # Lighter需要特殊的配置方式
        try:
            lighter_config_path = Path("config/exchanges/lighter_config.yaml")
            if lighter_config_path.exists():
                with open(lighter_config_path, 'r', encoding='utf-8') as f:
                    lighter_config_data = yaml.safe_load(f)
                api_config = lighter_config_data.get('api_config', {})
                auth_config = api_config.get('auth', {})

                exchange_config = ExchangeConfig(
                    exchange_id="lighter",
                    name="Lighter",
                    exchange_type=market_type,
                    api_key="",  # Lighter适配器内部从配置文件读取
                    api_secret="",  # Lighter适配器内部从配置文件读取
                    testnet=api_config.get('testnet', False),
                    enable_websocket=True,
                    enable_auto_reconnect=True
                )
                print(f"   ✓ Lighter配置加载成功（适配器将自动读取API密钥）")
            else:
                print(f"   ⚠️  未找到Lighter配置文件: {lighter_config_path}")
                exchange_config = ExchangeConfig(
                    exchange_id="lighter",
                    name="Lighter",
                    exchange_type=market_type,
                    api_key="",
                    api_secret="",
                    testnet=False,
                    enable_websocket=True,
                    enable_auto_reconnect=True
                )
        except Exception as e:
            print(f"   ⚠️  加载Lighter配置失败: {e}")
            exchange_config = ExchangeConfig(
                exchange_id="lighter",
                name="Lighter",
                exchange_type=market_type,
                api_key="",
                api_secret="",
                testnet=False,
                enable_websocket=True,
                enable_auto_reconnect=True
            )
    else:
        # 其他交易所使用标准配置
        exchange_config = ExchangeConfig(
            exchange_id=exchange_name,
            name=exchange_name.capitalize(),
            exchange_type=market_type,  # 🔥 使用自动检测的市场类型
            api_key=api_key or "public_data_only",
            api_secret=api_secret or "public_data_only",
            wallet_address=wallet_address,  # Hyperliquid 需要
            testnet=False,
            enable_websocket=True,
            enable_auto_reconnect=True
        )

    # 使用工厂创建适配器
    factory = ExchangeFactory()
    adapter = factory.create_adapter(
        exchange_id=exchange_name,
        config=exchange_config
    )

    # 连接交易所
    await adapter.connect()

    return adapter


async def main(config_path: str = "config/grid/default_grid.yaml", debug: bool = False, no_ui: bool = False):
    """
    主函数

    Args:
        config_path: 配置文件路径
        debug: 是否启用DEBUG模式
        no_ui: 是否禁用UI界面
    """
    # 🔥 如果启用 DEBUG 模式，设置日志级别
    if debug:
        # 设置根日志级别为 DEBUG
        logging.getLogger().setLevel(logging.DEBUG)

        # 设置核心模块的日志级别为 DEBUG
        for module in ['core.services.grid', 'core.adapters.exchanges', 'ExchangeAdapter']:
            logging.getLogger(module).setLevel(logging.DEBUG)

        # 🔥 为 lighter_websocket 添加文件handler，确保调试日志能写入文件
        lighter_ws_logger = logging.getLogger(
            'core.adapters.exchanges.adapters.lighter_websocket')
        lighter_ws_logger.setLevel(logging.DEBUG)

        # 添加文件handler到 ExchangeAdapter.log
        from logging.handlers import RotatingFileHandler
        ws_handler = RotatingFileHandler(
            'logs/ExchangeAdapter.log',
            maxBytes=10*1024*1024,  # 10MB
            backupCount=3,
            encoding='utf-8'
        )
        ws_handler.setLevel(logging.DEBUG)
        ws_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        ws_handler.setFormatter(ws_formatter)
        lighter_ws_logger.addHandler(ws_handler)
        lighter_ws_logger.propagate = False  # 不传播到父logger，避免重复

        print("=" * 70)
        print("🔥 网格交易系统启动 - DEBUG 模式")
        print("=" * 70)
        print("⚠️  DEBUG模式已启用：将输出详细的调试信息")
        print("=" * 70)
    else:
        print("=" * 70)
        print("🎯 网格交易系统启动")
        print("=" * 70)

    logger = get_system_logger()

    try:

        # 1. 加载配置
        print("\n📋 步骤 1/6: 加载配置文件...")
        config_data = await load_config(config_path)
        grid_config = create_grid_config(config_data)
        print(f"✅ 配置加载成功")
        print(f"   - 交易所: {grid_config.exchange}")
        print(f"   - 交易对: {grid_config.symbol}")
        print(f"   - 网格类型: {grid_config.grid_type.value}")

        # 🔥 现货做空校验：现货市场只能做多，不能做空
        symbol = grid_config.symbol
        exchange_name = grid_config.exchange.lower()
        is_spot = False

        # 检测是否为现货交易对
        if exchange_name == "hyperliquid":
            is_spot = ":SPOT" in symbol.upper()
        elif exchange_name == "backpack":
            is_spot = "_SPOT" in symbol.upper() or "SPOT" in symbol.upper()

        # 如果是现货且选择了做空网格，拒绝启动
        if is_spot and grid_config.grid_type.value in ["short", "martingale_short", "follow_short"]:
            print(f"\n❌ 错误：现货市场不支持做空网格！")
            print(f"   - 当前交易对: {symbol} (现货)")
            print(f"   - 当前网格类型: {grid_config.grid_type.value} (做空)")
            print(f"   - 建议：请使用做多网格类型 (long, martingale_long, follow_long)")
            sys.exit(1)

        if is_spot:
            print(f"   ℹ️  现货市场：仅支持做多网格")

        # 🔥 价格移动网格：价格区间在运行时动态设置
        if grid_config.is_follow_mode():
            print(f"   - 价格区间: 动态跟随（运行时根据当前价格设置）")
        else:
            print(
                f"   - 价格区间: ${grid_config.lower_price:,.2f} - ${grid_config.upper_price:,.2f}")

        print(f"   - 网格间隔: ${grid_config.grid_interval}")
        print(f"   - 网格数量: {grid_config.grid_count}个")
        print(f"   - 订单数量: {grid_config.order_amount}")

        # 🔥 显示特殊模式参数
        if grid_config.is_martingale_mode():
            print(f"   - 马丁递增: {grid_config.martingale_increment} (每格递增)")
        if grid_config.is_follow_mode():
            print(f"   - 脱离超时: {grid_config.follow_timeout}秒")
            print(f"   - 脱离距离: {grid_config.follow_distance}格")

        # 2. 创建交易所适配器
        print("\n🔌 步骤 2/6: 连接交易所...")
        exchange_adapter = await create_exchange_adapter(config_data)
        print(f"✅ 交易所连接成功: {grid_config.exchange}")

        # 🔥 自動獲取精度信息（若 YAML 中省略了 quantity_precision / price_decimals）
        await auto_fill_precision(grid_config, exchange_adapter)

        # 3. 创建核心组件
        print("\n⚙️  步骤 3/6: 初始化核心组件...")

        # 创建策略
        strategy = GridStrategyImpl()
        print("   ✓ 网格策略已创建")

        # 创建执行引擎
        engine = GridEngineImpl(exchange_adapter)
        print("   ✓ 执行引擎已创建")

        # 🛑 啟動護欄：若磁碟熔斷狀態指示 IP 仍在 BAN 中，等待解封後再繼續
        # 詳見：data/.circuit_breaker_state.json（由 _parse_and_set_circuit 寫入）
        if engine._is_circuit_open():
            import time as _t
            from datetime import datetime as _dt
            if engine._api_circuit_until == float('inf'):
                print("\n🚨 偵測到永久熔斷狀態 (-2015): API Key 或 IP 白名單失效")
                print(f"   錯誤碼: {engine._last_api_error_code}")
                print("   請至 Binance 後台更新 IP 白名單或 API Key 後，")
                print("   刪除 data/.circuit_breaker_state.json，再重啟本程式")
                await exchange_adapter.disconnect()
                sys.exit(1)
            until_str = _dt.fromtimestamp(engine._api_circuit_until).strftime(
                "%Y-%m-%d %H:%M:%S")
            wait_total = engine._api_circuit_until - _t.time()
            print(f"\n⛔ 偵測到磁碟熔斷狀態：IP 仍在 BAN 中（直到 {until_str}）")
            print(f"   為避免再次擊中 API 加劇 BAN，啟動將等待 {wait_total:.0f} 秒...")
            while engine._is_circuit_open():
                remaining = engine._api_circuit_until - _t.time()
                if remaining <= 0:
                    break
                sleep_for = min(30.0, remaining)
                print(f"   ⏳ 等待解封：剩餘 {remaining:.0f} 秒...")
                await asyncio.sleep(sleep_for)
            print("✅ 磁碟熔斷狀態已過期，繼續啟動")

        # 创建网格状态
        grid_state = GridState()

        # 创建持仓跟踪器
        tracker = PositionTrackerImpl(grid_config, grid_state)
        print("   ✓ 持仓跟踪器已创建")

        # 🔥 创建预留管理器（仅现货）
        reserve_manager = None
        reserve_monitor = None

        if exchange_adapter.config.exchange_type == ExchangeType.SPOT:
            spot_reserve_config = getattr(grid_config, 'spot_reserve', None)

            if spot_reserve_config and spot_reserve_config.get('enabled', False):
                print("   ✓ 现货预留管理已启用")

                reserve_manager = SpotReserveManager(
                    reserve_config=spot_reserve_config,
                    exchange_adapter=exchange_adapter,
                    symbol=grid_config.symbol,
                    quantity_precision=grid_config.quantity_precision
                )

                # 创建监控器（稍后启动）
                reserve_monitor = ReserveMonitor(
                    reserve_manager=reserve_manager,
                    exchange_adapter=exchange_adapter,
                    symbol=grid_config.symbol,
                    check_interval=60
                )
                print("   ✓ 预留监控器已创建")

        # 4. 创建协调器
        print("\n🎮 步骤 4/6: 创建系统协调器...")
        coordinator = GridCoordinator(
            config=grid_config,
            strategy=strategy,
            engine=engine,
            tracker=tracker,
            grid_state=grid_state,
            reserve_manager=reserve_manager  # 🔥 传入预留管理器
        )
        print("✅ 协调器创建成功")

        # 🔥 启动前检查（仅现货且启用预留管理）
        if reserve_manager:
            print("\n🔍 启动前检查: 验证现货预留BTC...")
            if not await check_spot_reserve_on_startup(grid_config, exchange_adapter, reserve_manager):
                print("❌ 启动检查失败，系统退出")
                await exchange_adapter.disconnect()
                sys.exit(1)
            print("✅ 预留检查通过")

        # 5. 初始化并启动网格系统
        print("\n🚀 步骤 5/6: 启动网格系统...")
        print(f"   - 准备批量挂单：{grid_config.grid_count}个订单")

        # 🔥 价格移动网格：价格区间在启动后才设置
        if not grid_config.is_follow_mode():
            print(
                f"   - 覆盖价格区间：${grid_config.lower_price:,.2f} - ${grid_config.upper_price:,.2f}")
        else:
            print(f"   - 价格区间：动态跟随（将根据当前价格设置）")

        await coordinator.start()
        print("✅ 网格系统已启动")
        print(f"   - 已成功挂出{grid_config.grid_count}个订单")

        # 🔥 启动预留监控（在网格启动后）
        if reserve_monitor:
            await reserve_monitor.start()
            print("✅ 预留监控器已启动")

        # 🔥 价格移动网格：显示实际设置的价格区间
        if grid_config.is_follow_mode():
            print(
                f"   - 实际价格区间：${grid_config.lower_price:,.2f} - ${grid_config.upper_price:,.2f}")

        print(f"   - 所有网格已就位，等待成交...")

        # 6. 启动终端界面
        if not no_ui:
            print("\n🖥️  步骤 6/6: 启动监控界面...")
            terminal_ui = GridTerminalUI(coordinator)

            print("=" * 70)
            print("✅ 网格交易系统完全启动")
            print("=" * 70)
            print()

            # 运行终端界面
            await terminal_ui.run()
        else:
            print("\n🖥️  Headless模式: 跳过监控界面")
            print("=" * 70)
            print("✅ 网格交易系统完全启动 (后台运行中)")
            print("=" * 70)
            print()
            
            # Headless monitor to log stats periodically for the Web UI
            async def headless_monitor():
                import json
                from datetime import timedelta, datetime
                import decimal
                
                class JSONEncoder(json.JSONEncoder):
                    def default(self, obj):
                        if isinstance(obj, decimal.Decimal):
                            return float(obj)
                        if isinstance(obj, timedelta):
                            return str(obj)
                        if isinstance(obj, datetime):
                            return obj.isoformat()
                        return super().default(obj)
                        
                while True:
                    try:
                        stats = await coordinator.get_statistics()
                        stats_dict = stats.to_display_dict()
                        print(f"[STATS_JSON] {json.dumps(stats_dict, cls=JSONEncoder)}")
                    except Exception as e:
                        pass
                    await asyncio.sleep(2)
            
            # Keep running until cancelled
            try:
                await headless_monitor()
            except asyncio.CancelledError:
                pass

    except KeyboardInterrupt:
        print("\n\n⚠️  收到退出信号，正在停止系统...")

    except Exception as e:
        logger.error(f"❌ 系统错误: {e}", exc_info=True)
        print(f"\n❌ 系统错误: {e}")

    finally:
        # 清理资源
        print("\n🧹 正在清理资源...")
        try:
            # 🔥 检查event loop是否仍然可用
            try:
                loop = asyncio.get_running_loop()
                loop_available = not loop.is_closed()
            except RuntimeError:
                # 没有运行中的event loop
                loop_available = False
                print("   ⚠️  Event loop已关闭，跳过异步清理操作")

            if loop_available:
                # 🔥 退出清理：平仓和取消订单（如果启用）
                # 注意：terminal_ui已经在Live上下文中执行了cleanup_on_exit
                # 这里只在非KeyboardInterrupt的情况下执行（例如其他异常）
                if 'coordinator' in locals() and coordinator:
                    try:
                        # 🔥 只在非正常退出时调用清理（正常Ctrl+C已在terminal_ui中处理）
                        if not isinstance(sys.exc_info()[1], KeyboardInterrupt):
                            cleanup_result = await coordinator.cleanup_on_exit()
                    except Exception as cleanup_error:
                        print(f"\n❌ 退出清理异常: {cleanup_error}")
                        import traceback
                        traceback.print_exc()

                # 🔥 停止预留监控器
                if 'reserve_monitor' in locals() and reserve_monitor:
                    await reserve_monitor.stop()
                    print("\n   ✓ 预留监控器已停止")

                if 'coordinator' in locals():
                    await coordinator.stop()
                    print("   ✓ 网格系统已停止")

                if 'exchange_adapter' in locals():
                    await exchange_adapter.disconnect()
                    print("   ✓ 交易所已断开")
            else:
                # Event loop不可用，只执行同步清理
                print("   ℹ️  执行非异步资源清理...")
                # 这里可以添加一些不需要event loop的清理逻辑

            print("\n" + "=" * 70)
            print("✅ 系统已安全退出")
            print("=" * 70)

        except Exception as e:
            print(f"⚠️  清理过程出错: {e}")


def parse_arguments():
    """
    解析命令行参数

    Returns:
        argparse.Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(
        description='网格交易系统 - 支持多交易所的自动化网格交易',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 正常模式启动
  python3 run_grid_trading.py config/grid/lighter_btc_perp_long.yaml
  
  # DEBUG 模式启动（输出详细日志）
  python3 run_grid_trading.py config/grid/lighter_btc_perp_long.yaml --debug
  
  # 🔸 Backpack 交易所
  python3 run_grid_trading.py config/grid/backpack_capital_protection_long_btc.yaml
  
  # 🔹 Hyperliquid 交易所
  python3 run_grid_trading.py config/grid/hyperliquid_btc_perp_long.yaml
  
  # 🔶 Lighter 交易所
  python3 run_grid_trading.py config/grid/lighter_btc_perp_long.yaml --debug

支持的交易所:
  ✅ Backpack    - 永续合约（做多/做空）
  ✅ Hyperliquid - 永续合约（做多/做空）、现货（仅做多）
  ✅ Lighter     - 永续合约（做多/做空）

注意事项:
  1. 确保API密钥已正确配置
  2. 确保有足够的资金用于网格交易
  3. 建议先用小额资金测试
  4. ⚠️  现货市场只支持做多，不支持做空
  5. 网格系统会永久运行，除非手动停止
  6. 使用 Ctrl+C 或 Q 键安全退出系统
  7. DEBUG 模式会输出详细日志，适合排查问题
        """
    )

    parser.add_argument(
        'config',
        type=str,
        help='网格配置文件路径 (例如: config/grid/lighter_btc_perp_long.yaml)'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='启用DEBUG模式，输出详细的调试日志'
    )

    parser.add_argument(
        '--no-ui',
        action='store_true',
        help='Run in headless mode without TUI'
    )

    parser.add_argument(
        '--version',
        action='version',
        version='网格交易系统 v2.0.0'
    )

    return parser.parse_args()


if __name__ == "__main__":
    try:
        # 解析命令行参数
        args = parse_arguments()

        # 获取配置文件路径
        config_path = args.config

        # 检查配置文件是否存在
        if not Path(config_path).exists():
            print(f"❌ 配置文件不存在: {config_path}")
            print("\n使用 -h 或 --help 查看使用说明")
            sys.exit(1)

        # 如果启用 DEBUG 模式，显示提示
        if args.debug:
            print("\n" + "=" * 70)
            print("🔥 DEBUG 模式已启用")
            print("=" * 70)
            print("📝 将输出详细的调试日志，包括：")
            print("   - WebSocket 消息详情")
            print("   - REST API 调用参数")
            print("   - 订单匹配逻辑")
            print("   - 持仓和余额更新")
            print("=" * 70)
            print()

        # 获取并加锁
        lock_file_path = acquire_lock(config_path)

        import signal

        def _release_lock():
            """释放进程锁文件"""
            if lock_file_path and os.path.exists(lock_file_path):
                try:
                    os.remove(lock_file_path)
                except Exception:
                    pass

        try:
            # 🔥 使用显式 event loop 以支持 SIGTERM（IDE关闭窗口）信号处理
            # asyncio.run() 仅处理 KeyboardInterrupt (SIGINT/Ctrl+C)，
            # 而 IDE 关闭终端通常发送 SIGTERM，会导致 asyncio tasks 不清理就退出，
            # 从而产生"僵尸线程"继续向 Binance 发起 REST 请求。
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            main_task = None

            def _handle_exit_signal(signame: str):
                """统一信号处理：取消主任务，触发 finally 清理"""
                print(f"\n\n⚠️  收到 {signame} 信号，正在优雅退出（取消所有后台任务）...")
                if main_task and not main_task.done():
                    main_task.cancel()

            # 注册 SIGTERM（IDE 关闭）和 SIGINT（Ctrl+C）
            try:
                loop.add_signal_handler(
                    signal.SIGTERM,
                    lambda: _handle_exit_signal("SIGTERM")
                )
                loop.add_signal_handler(
                    signal.SIGINT,
                    lambda: _handle_exit_signal("SIGINT")
                )
            except NotImplementedError:
                # Windows 不支持 add_signal_handler，回退到默认行为
                pass

            try:
                main_task = loop.create_task(
                    main(config_path, debug=args.debug, no_ui=args.no_ui)
                )
                loop.run_until_complete(main_task)
            except asyncio.CancelledError:
                # 信号触发取消，属于正常退出
                print("\n👋 主任务已取消（信号退出）")
            finally:
                # 🔥 取消所有残留的后台 asyncio tasks（关键：防止 Binance API 继续被调用）
                pending = asyncio.all_tasks(loop)
                if pending:
                    print(f"   🧹 正在取消 {len(pending)} 个残留后台任务...")
                    for task in pending:
                        task.cancel()
                    # 等待所有 task 完成取消（最多5秒）
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()

        finally:
            _release_lock()

    except KeyboardInterrupt:
        print("\n👋 程序已退出")
    except SystemExit:
        # argparse 的 --help 或 --version 会触发 SystemExit
        pass
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

