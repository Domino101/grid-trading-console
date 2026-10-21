# 交易所憑證設定目錄 (`config/exchanges/`)

> 🔒 **本目錄只追蹤 `*.example.yaml` 範本。真實金鑰檔 `<exchange>_config.yaml` 已列入 `.gitignore`，永遠不會被 commit。**
> 背景與原因請見 `docs/PUBLIC_RELEASE_AUDIT_20261021.md`（公開前安全審查報告）。

## 使用方式

程式碼以固定檔名讀取設定（例如 `run_grid_trading.py`、`core/adapters/exchanges/adapters/lighter.py`、`hyperliquid_base.py`、`o1exchange_base.py`、`edgex.py`），檔案不存在時會自動略過並改用環境變數，不會當機。

```bash
# 在每一台主機上，從範本複製出真實設定檔（該檔名已被 gitignore）
cp config/exchanges/binance_config.example.yaml    config/exchanges/binance_config.yaml
cp config/exchanges/lighter_config.example.yaml    config/exchanges/lighter_config.yaml
cp config/exchanges/hyperliquid_config.example.yaml config/exchanges/hyperliquid_config.yaml
# ... 依需要的交易所類推，再填入真實金鑰
```

金鑰載入優先順序（以 `run_grid_trading.py` 為準）：

1. 環境變數 `<EXCHANGE>_API_KEY` / `<EXCHANGE>_API_SECRET`（以及 `_PASSPHRASE`、`_WALLET_ADDRESS`），通常放在專案根目錄 `.env`（已 gitignore）。
2. `config/exchanges/<exchange>_config.yaml` 內的 `authentication` 區段。
3. 都沒有 → 以空字串啟動，只能使用公開行情 / PAPER 模式。

## 規則

- **不要**把 `*.example.yaml` 改名回 `*_config.yaml` 後 commit；也**不要**在範本裡放任何真實值（包含 `account_index`、錢包地址）。
- 範本中的 `1111...`、`0x...`、空字串都是佔位符。
- 若懷疑金鑰曾進入 git 歷史，先到交易所後台**撤銷並重新產生**，再處理 git；撤銷金鑰永遠比清理歷史快。
