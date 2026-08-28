const fs = require("fs");
const path = require("path");

const ROOT = process.env.PROJECT_ROOT || __dirname;
const PYTHON = process.env.PYTHON_BIN || path.join(ROOT, "venv", "bin", "python3");

// Read .env if present so PM2 env inherits variables
const envFile = path.join(ROOT, ".env");
const parsedEnv = {};
if (fs.existsSync(envFile)) {
  try {
    const lines = fs.readFileSync(envFile, "utf-8").split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const idx = trimmed.indexOf("=");
      if (idx > 0) {
        const k = trimmed.substring(0, idx).trim();
        const v = trimmed.substring(idx + 1).trim();
        parsedEnv[k] = v;
      }
    }
  } catch (e) {}
}

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
        ...parsedEnv,
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
    {
      name: "grid-web-ui",
      script: "web_ui/backend/main.py",
      interpreter: PYTHON,
      cwd: ROOT,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      min_uptime: "10s",
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONPATH: ROOT,
        ...parsedEnv,
      },
      out_file: path.join(ROOT, "logs", "pm2-web-ui.log"),
      error_file: path.join(ROOT, "logs", "pm2-web-ui-error.log"),
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      merge_logs: true,
    },
  ],
};
