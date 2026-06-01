#!/usr/bin/env python3
"""
網格配置遷移腳本

將現有的完整 YAML 配置文件遷移為精簡版：
1. 移除已在 defaults.yaml 中定義的系統常數參數
2. 將 grid_interval 轉換為 grid_count
3. 移除可自動獲取的精度參數（quantity_precision / price_decimals）

用法：
    python scripts/migrate_configs.py [--dry-run] [--verify] [CONFIG_PATH ...]

    --dry-run   只顯示遷移結果，不實際寫入文件
    --verify    遷移後驗證 GridConfig 等價性
"""

import sys
import os
import yaml
import argparse
import copy
from pathlib import Path
from decimal import Decimal

# 添加項目根目錄到路徑
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# 可從 defaults.yaml 中獲取的系統常數參數
DEFAULTS_KEYS = {
    'active_window_each_side',
    'active_window_refresh_interval',
    'order_health_check_enabled',
    'order_health_check_interval',
    'health_check_adjust_position_enabled',
    'rest_position_query_interval',
    'ws_only_mode',
    'rest_backup_enabled',
    'limit_only',
    'reverse_order_grid_distance',
    'fee_rate',
    'position_tolerance',
}

# 可自動從交易所獲取的精度參數
AUTO_PRECISION_KEYS = {
    'quantity_precision',
    'price_decimals',
}


def load_defaults():
    """載入 defaults.yaml"""
    defaults_path = project_root / "config" / "grid" / "defaults.yaml"
    if defaults_path.exists():
        with open(defaults_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return data.get('grid_defaults', {})
    return {}


def migrate_config(config_path: str, dry_run: bool = False, verify: bool = False) -> dict:
    """
    遷移單個配置文件

    Args:
        config_path: 配置文件路徑
        dry_run: 是否只顯示結果不寫入
        verify: 是否驗證等價性

    Returns:
        遷移後的配置字典
    """
    defaults = load_defaults()

    with open(config_path, 'r', encoding='utf-8') as f:
        original = yaml.safe_load(f)

    original_copy = copy.deepcopy(original)
    grid = original['grid_system']

    removed = []
    converted = []

    # 1. 將 grid_interval 轉換為 grid_count
    if 'grid_interval' in grid and 'price_range' in grid:
        interval = Decimal(str(grid['grid_interval']))
        lower = Decimal(str(grid['price_range']['lower_price']))
        upper = Decimal(str(grid['price_range']['upper_price']))
        grid_count = int(abs(upper - lower) / interval)

        grid['grid_count'] = grid_count
        del grid['grid_interval']
        converted.append(f"grid_interval={interval} → grid_count={grid_count}")

    # 2. 移除與 defaults.yaml 相同的系統常數
    for key in list(grid.keys()):
        if key in DEFAULTS_KEYS:
            yaml_value = grid[key]
            default_value = defaults.get(key)

            # 只有當值與 defaults 完全相同時才移除
            if default_value is not None and _values_equal(yaml_value, default_value):
                del grid[key]
                removed.append(key)

    # 3. 移除可自動獲取的精度參數
    for key in list(grid.keys()):
        if key in AUTO_PRECISION_KEYS:
            del grid[key]
            removed.append(key)

    # 4. 移除已失效的註釋行（通過重新序列化）
    migrated = original

    # 顯示結果
    print(f"\n📄 {config_path}")
    if converted:
        print(f"   🔄 轉換: {', '.join(converted)}")
    if removed:
        print(f"   🗑️  移除 {len(removed)} 個參數: {', '.join(removed)}")
    else:
        print(f"   ✅ 無需遷移")

    # 寫入文件
    if not dry_run and (removed or converted):
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(migrated, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        print(f"   💾 已寫入")

    # 驗證等價性
    if verify and (removed or converted):
        _verify_equivalence(config_path, original_copy, migrated)

    return migrated


def _values_equal(a, b):
    """比較兩個值是否相等（處理類型差異）"""
    # 數值比較
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    # 字典比較
    if isinstance(a, dict) and isinstance(b, dict):
        return a == b
    # 布爾比較
    if isinstance(a, bool) and isinstance(b, bool):
        return a == b
    # 字串比較
    return str(a) == str(b)


def _verify_equivalence(config_path: str, original: dict, migrated: dict):
    """驗證遷移前後 GridConfig 等價性"""
    try:
        from run_grid_trading import create_grid_config

        config_orig = create_grid_config(original)
        config_migrated = create_grid_config(migrated)

        # 比較關鍵參數
        checks = [
            ('grid_count', config_orig.grid_count, config_migrated.grid_count),
            ('grid_interval', config_orig.grid_interval, config_migrated.grid_interval),
            ('order_amount', config_orig.order_amount, config_migrated.order_amount),
            ('ws_only_mode', config_orig.ws_only_mode, config_migrated.ws_only_mode),
            ('limit_only', config_orig.limit_only, config_migrated.limit_only),
            ('active_window_refresh_interval', config_orig.active_window_refresh_interval, config_migrated.active_window_refresh_interval),
        ]

        all_pass = True
        for name, orig_val, migrated_val in checks:
            if str(orig_val) != str(migrated_val):
                print(f"   ❌ 不等價: {name}: {orig_val} ≠ {migrated_val}")
                all_pass = False

        if all_pass:
            print(f"   ✅ 等價性驗證通過")
        else:
            print(f"   ⚠️ 等價性驗證失敗！")

    except Exception as e:
        print(f"   ⚠️ 無法驗證等價性: {e}")


def main():
    parser = argparse.ArgumentParser(description="網格配置遷移腳本")
    parser.add_argument('configs', nargs='*', help='要遷移的配置文件路徑')
    parser.add_argument('--dry-run', action='store_true', help='只顯示遷移結果，不寫入文件')
    parser.add_argument('--verify', action='store_true', help='遷移後驗證等價性')
    args = parser.parse_args()

    # 如果未指定配置，遷移所有 Binance neutral YAML
    config_paths = args.configs
    if not config_paths:
        grid_dir = project_root / "config" / "grid"
        config_paths = sorted(str(p) for p in grid_dir.glob("binance-neutral-*.yaml"))

    if not config_paths:
        print("❌ 沒有找到要遷移的配置文件")
        sys.exit(1)

    print(f"🔧 網格配置遷移工具")
    print(f"   模式: {'DRY-RUN (不寫入)' if args.dry_run else '實際遷移'}")
    print(f"   驗證: {'啟用' if args.verify else '禁用'}")
    print(f"   配置數量: {len(config_paths)}")

    for cp in config_paths:
        if not Path(cp).exists():
            print(f"\n⚠️ 跳過不存在的文件: {cp}")
            continue
        migrate_config(cp, dry_run=args.dry_run, verify=args.verify)

    print(f"\n{'=' * 60}")
    print(f"✅ 遷移完成！{'(DRY-RUN，未實際寫入)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
