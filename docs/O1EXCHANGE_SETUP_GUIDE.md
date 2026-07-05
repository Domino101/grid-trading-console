# O1Exchange 网格交易配置指南

## 📋 目录
1. [概述](#概述)
2. [币安配置 vs O1Exchange 配置对比](#配置对比)
3. [配置文件详解](#配置文件详解)
4. [快速开始](#快速开始)
5. [注意事项](#注意事项)

---

## 概述

**币安配置不能直接通用**，O1Exchange 有以下特点：
- ✅ 现货 DEX 聚合器（不支持合约/杠杆）
- ✅ MEV 保护交易
- ✅ Permit2 签名（Gas 优化）
- ✅ 多网络支持（Base、BSC、Solana）
- ❌ 不支持 WebSocket（使用 REST 轮询）

---

## 配置对比

### 币安配置（永续合约）
```yaml
grid_system:
  exchange: binance
  symbol: ETH/USDC:USDC
  grid_type: neutral
  price_range:
    lower_price: 1800.0
    upper_price: 3000.0
  order_amount: 0.011
  leverage: 100              # ❌ O1Exchange 不需要
  margin_mode: cross         # ❌ O1Exchange 不需要
  grid_count: 170
```

### O1Exchange 配置（现货）
```yaml
grid_system:
  exchange: o1exchange
  symbol: ETH/USDC
  
  o1exchange:                # ✅ O1Exchange 特有参数
    network: base
    slippage_bps: 300
    enable_mev_protection: true
  
  grid_type: long            # ✅ 现货只能做多
  price_range:
    lower_price: 1800.0
    upper_price: 3000.0
  order_amount: 0.01
  grid_count: 170
```

---

## 配置文件详解

### 交易所配置 (`config/exchanges/o1exchange_config.yaml`)

```yaml
o1exchange:
  api:
    base_url: https://api.o1.exchange
    timeout: 30
    version: v2
  
  authentication:
    api_key: 'your_api_key_here'        # 🔑 O1Exchange API Key
    private_key: 'your_private_key'     # 🔑 钱包私钥（用于签名）
    wallet_address: '0x...'             # 📍 钱包地址
  
  network:
    default: base
    enabled:
      - base
      - bsc
      - solana
  
  trading:
    default_slippage_bps: 300
    enable_mev_protection: true
```

### 网格配置参数

| 参数 | 说明 | 币安 | O1Exchange |
|------|------|------|-----------|
| `exchange` | 交易所名称 | ✅ | ✅ |
| `symbol` | 交易对 | `ETH/USDC:USDC` | `ETH/USDC` |
| `grid_type` | 网格类型 | `neutral`/`long`/`short` | 仅 `long` |
| `leverage` | 杠杆 | ✅ | ❌ |
| `margin_mode` | 保证金模式 | ✅ | ❌ |
| `o1exchange.network` | 网络选择 | ❌ | ✅ |
| `o1exchange.slippage_bps` | 滑点 | ❌ | ✅ |
| `o1exchange.enable_mev_protection` | MEV保护 | ❌ | ✅ |

---

## 快速开始

### 1. 配置 API 密钥

编辑 `config/exchanges/o1exchange_config.yaml`：

```yaml
o1exchange:
  authentication:
    api_key: 'sk_xxx_yyy'           # 从 https://o1.exchange/api-trading 获取
    private_key: '0xabc...'         # 你的钱包私钥
    wallet_address: '0x123...'      # 你的钱包地址
```

### 2. 选择或创建网格配置

已有配置文件：
- `config/grid/o1exchange-neutral-spot-eth-usdc.yaml`
- `config/grid/o1exchange-long-spot-sol-usdc.yaml`

### 3. 运行网格交易

```bash
python3 run_grid_trading.py config/grid/o1exchange-neutral-spot-eth-usdc.yaml
```

---

## 注意事项

### ⚠️ 重要提醒

1. **只能做多**：O1Exchange 是现货市场，不支持做空
   - 网格类型仅支持：`long`、`martingale_long`、`follow_long`

2. **网络选择**：
   - Base：推荐，Gas 低
   - BSC：备选
   - Solana：支持但注意稳定性

3. **滑点设置**：
   - 稳定币对（ETH/USDC）：100-300 bps（1-3%）
   - 波动币种（SOL/USDC）：300-500 bps（3-5%）

4. **MEV 保护**：建议启用，避免三明治攻击

5. **WebSocket**：暂不支持，使用 REST 轮询

### 📊 推荐配置

| 交易对 | 价格区间 | 订单数量 | 网格数 | 滑点 |
|--------|---------|---------|--------|------|
| ETH/USDC | 1800-3000 | 0.01 | 170 | 300 |
| SOL/USDC | 100-160 | 0.5 | 120 | 500 |
| BTC/USDC | 50000-80000 | 0.001 | 150 | 300 |

---

## 故障排除

### 问题：提示现货不能做空
**解决**：将 `grid_type` 改为 `long`

### 问题：签名失败
**解决**：检查 `private_key` 和 `wallet_address` 是否匹配

### 问题：交易失败
**解决**：
1. 检查网络选择
2. 调整滑点设置
3. 确认钱包有足够余额和 Gas
