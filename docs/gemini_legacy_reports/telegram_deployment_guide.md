# 🤖 Crypto Opinion Bot - Telegram Deployment & Quickstart Guide

This guide describes the complete workflow to deploy and control the **Crypto Opinion Bot** in **Shadow (Dry-Run) Mode** with real-time **Telegram Remote Control Room** integration.

---

## 🛠️ Step-by-Step Telegram Bot Setup

To connect the bot to your Telegram app for real-time monitoring and manual approvals, follow these two simple steps:

### 1. Create a Telegram Bot via `@BotFather`
1. Open Telegram and search for **@BotFather**.
2. Start a chat and send the command `/newbot`.
3. Follow the prompts to set a name and username for your bot.
4. Save the generated **HTTP API Token** (e.g., `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`). This is your `TELEGRAM_BOT_TOKEN`.

### 2. Retrieve Your Chat ID via `@userinfobot`
To ensure only **you** can control the bot, it filters commands by your Telegram Chat ID:
1. Search for **@userinfobot** on Telegram.
2. Start a chat with it. It will instantly reply with your `Id` (e.g., `987654321`). This is your `TELEGRAM_CHAT_ID`.

---

## ⚙️ Environment Configuration

Open the `.env` file at the root of the project (`/usr/local/lib/crypto-opnion-bot/.env`) and update the Telegram credentials:

```env
# ==============================================================================
# Telegram Telemetry & Remote Control Room Configuration
# ==============================================================================
TELEGRAM_BOT_TOKEN="your_telegram_bot_token_here"
TELEGRAM_CHAT_ID="your_telegram_chat_id_here"
```

> [!IMPORTANT]
> The Master Key `BOT_MASTER_KEY` is already pre-configured to decrypt the sandbox security keys in-memory. Keep the other options (`POLYMARKET_USE_SANDBOX=true` and `DERIBIT_USE_SANDBOX=true`) active to run in risk-free **Shadow Mode**.

---

## 🚀 Running the Shadow Bot

We have created a helper script to easily spin up the bot in continuous scanning mode. 

### Start the Bot
Run the following script from the project root:
```bash
./scripts/start_shadow_bot.sh
```

### What Happens in Shadow Mode?
* **Real-time Ingestion**: The bot streams live tickers and order book states from **Polymarket** and **Deribit/Binance**.
* **Precise Math Alignment**: Every 100ms, it aligns the prices, fits the options implied volatility smile, solves risk-neutral probability, and calculates the net arbitrage spread.
* **Paper Execution**: High-fidelity simulated trades are placed, recorded in the local portfolio/ledger database, and reported to your Telegram channel!

---

## 💬 Telegram Control Room Commands

Once the bot is running, you can send these commands in your Telegram chat to monitor or control it dynamically:

| Command | Action | Example Response |
| :--- | :--- | :--- |
| `/status` | Query system status, heartbeats, loss limits, and circuit breakers. | `📶 System State: 🟢 ACTIVE` |
| `/portfolio` | Check current mock paper positions, PnL, and Delta Greeks. | `📊 Active Positions: 1` |
| `/opportunities` | Pull the top aligned arbitrage opportunities from the live buffer. | `#1 BTC Strike $70000 Net Spread: +9.82%` |
| `/set_spread [val]` | Dynamically change the minimum threshold spread required to trade. | `🟢 Min Net Spread updated to 0.8%` |
| `/set_size [val]` | Dynamically change the max trade size (USD). | `🟢 Max Position Size updated to $50.0 USD` |
| `/set_mode [auto\|manual]` | Toggle between auto trading and human-in-the-loop approvals. | `🟢 Execution Mode updated to MANUAL` |
| `/panic_shutdown` | Instant panic switch to halt all threads, clear locks, and exit. | `🛑 EMERGENCY SHUTDOWN INITIATED` |

### 📸 Manual Approval Workflow Example
If you set `/set_mode manual`, the bot will send an interactive card when an arbitrage opportunity is found:

```
🔍 [OPPORTUNITY DETECTED] BTC-20260522-$95000
━━━━━━━━━━━━━━━━━━━━
Leg A (Polymarket): $0.45 (45.00%)
Leg B (Deribit Option): Prob 49.82% (IV: 52.4%)
------------------------------------
Net Spread: 🟢 +4.20%
━━━━━━━━━━━━━━━━━━━━
[ ✅ 允許交易 ]   [ ❌ 忽略機會 ]
```
* Click **[✅ 允許交易]** to authorize immediate double-leg execution.
* Click **[❌ 忽略機會]** to discard the opportunity.

---

## 🛡️ Recent Bugfixes and System Improvements

We analyzed the execution pipeline and fixed two critical bugs in the data feed:
1. **Heartbeat Config Alignment**: Fixed `AttributeError` where the CEX feed polling loop tried to access the non-existent `self.settings.risk` field. Updated to correctly reference `self.settings.system.heartbeat_threshold_ms`.
2. **REST Client Asynchrony**: Standardized `CexFeed` to use `ccxt.async_support` instead of synchronous `ccxt`. This ensures `fetch_tickers()` and `close()` execute as proper non-blocking coroutines, enabling ultra-smooth graceful shutdown without any thread blocks or warnings.
