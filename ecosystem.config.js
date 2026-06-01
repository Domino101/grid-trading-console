module.exports = {
  apps: [
    {
      name: "grid-sol-usdc",
      script: "run_grid_trading.py",
      interpreter: "/root/crypto-trading-open/venv/bin/python3",
      cwd: "/root/crypto-trading-open",
      args: "config/grid/binance-neutral-perp-sol-usdc-100-160.yaml --no-ui",

      // 自動重啟設定
      autorestart: true,
      restart_delay: 30000,   // 崩潰後等 30 秒再重啟（避免 API 封 IP）
      max_restarts: 10,       // 10 分鐘內最多重啟 10 次
      min_uptime: "60s",      // 至少存活 60 秒才算正常啟動

      // 環境變數
      env: {
        PYTHONUNBUFFERED: "1",  // 確保 Python stdout 不緩衝，log 即時輸出
        PYTHONPATH: "/root/crypto-trading-open"
      },

      // Log 設定 - 輸出到專案 logs/ 目錄
      out_file: "/root/crypto-trading-open/logs/pm2-grid-sol-usdc.log",
      error_file: "/root/crypto-trading-open/logs/pm2-grid-sol-usdc-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      merge_logs: true,
    },
    {
      name: "multi-grid-engine",
      script: "run_multiple_grids.py",
      interpreter: "/root/crypto-trading-open/venv/bin/python3",
      cwd: "/root/crypto-trading-open",
      args: "", // 留空則默認運行預設的五個網格

      // 自動重啟設定
      autorestart: true,
      restart_delay: 30000,   // 崩潰後等 30 秒再重啟（避免 API 封 IP）
      max_restarts: 10,       // 10 分鐘內最多重啟 10 次
      min_uptime: "60s",      // 至少存活 60 秒才算正常啟動

      // 環境變數
      env: {
        PYTHONUNBUFFERED: "1",  // 確保 Python stdout 不緩衝，log 即時輸出
        PYTHONPATH: "/root/crypto-trading-open"
      },

      // Log 設定 - 輸出到專案 logs/ 目錄
      out_file: "/root/crypto-trading-open/logs/pm2-multi-grid.log",
      error_file: "/root/crypto-trading-open/logs/pm2-multi-grid-error.log",
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      merge_logs: true,
    }
  ]
};
