const path = require("path");

const ROOT = process.env.PROJECT_ROOT || __dirname;
const PYTHON = process.env.PYTHON_BIN || path.join(ROOT, "venv", "bin", "python3");

module.exports = {
  apps: [
    {
      name: "multi-grid-engine",
      script: "run_multiple_grids.py",
      interpreter: PYTHON,
      cwd: ROOT,
      // Empty args enter hot-plug standby. This does NOT start five default
      // live grids. Pass explicit YAML paths if you want a boot-time set.
      args: process.env.MULTI_GRID_ARGS || "",
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 10,
      min_uptime: "60s",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: ROOT,
      },
      out_file: path.join(ROOT, "logs", "pm2-multi-grid.log"),
      error_file: path.join(ROOT, "logs", "pm2-multi-grid-error.log"),
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      merge_logs: true,
    },
    {
      name: "grid-volatility-scanner",
      script: "grid_volatility_scanner/run_scanner.py",
      interpreter: PYTHON,
      cwd: ROOT,
      args: process.env.SCANNER_ARGS || "--exchange lighter --no-ui --duration 86400",
      autorestart: true,
      restart_delay: 30000,
      max_restarts: 10,
      min_uptime: "60s",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: ROOT,
      },
      out_file: path.join(ROOT, "logs", "pm2-grid-vol-scanner.log"),
      error_file: path.join(ROOT, "logs", "pm2-grid-vol-scanner-error.log"),
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      merge_logs: true,
    },
  ],
};
