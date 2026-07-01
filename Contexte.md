# MajeurCap - Crypto Trading Bot (Production Ready)

MajeurCap is a modular, high-performance cryptocurrency trading bot designed for automated technical analysis, signal generation, and trade execution on Binance. Featuring a fully asynchronous architecture with built-in risk management and automatic safety mechanisms.

## Project Overview

- **Core Technology:** Python 3.14+, `asyncio`, `aiohttp`
- **APIs:** CCXT (Binance Futures Demo/Mainnet), Telegram Bot API.
- **Data Analysis:** Pandas, NumPy, ta (TA-Lib wrapper).
- **Architecture:** Fully asynchronous, robust modular design.
- **Strategy:** Multi-indicator confluence (EMA, RSI, ATR, Zigzag, Keltner Channels) + Smart Money Concepts (BOS, CHoCH). Features dynamic Fibonacci proximity, ADX trend filtering, and dynamic position sizing based on ADX.
- **Safety:** Circuit breaker (daily drawdown stop), Exchange-side SL & TP placement (survives crashes), Auto-cleanup of orphan orders, Graceful Shutdown handling, Position reconciliation on startup.

## Directory Structure

- `main.py`: CLI entry point. Modes: `--live` (trading cycle), `--listen` (Telegram listener), `--all` (both), `--backtest`, `--check`.
- `bot_telegram.py`: Main trading loop. Optimised cycles: 60s position monitoring / 15min signal scan. Heartbeat every N minutes. Signal deduplication via cooldown.
- `bot_listener.py`: Telegram command interface (polling). Commands: `/status`, `/db` (dashboard), `/start`.
- `trade_manager.py`: Position management, trailing SL (ATR-based), exchange order sync & cleanup, JSON→SQLite migration, startup reconciliation (DB vs Binance), periodic position sync with exchange.
- `execution.py`: Order placement (Binance Futures Demo/Mainnet). Supports entry market order + SL (STOP_MARKET) + TP1 (TAKE_PROFIT_MARKET 50%) + TP2 (TAKE_PROFIT_MARKET 100%). Stop-loss update for trailing.
- `risk_manager.py`: Leverage-aware position sizing with ADX dynamic multiplier, exposure cap, max positions limit, and capital-available check.
- `module1_data_v3.py`: Async OHLCV fetching (multi-timeframe support, 1h default + daily optional).
- `module2_AT.py`: Technical analysis (EMA, RSI, ATR, ADX, Keltner Channels, Zigzag pivot detection).
- `module3_signal.py`: SMC Signal logic (BOS, CHoCH, Fibonacci proximity, KC breakout, RSI divergence). Best direction selection by confluence score.
- `module4_backtest.py`: Backtesting framework with realistic fees/slippage.
- `backtest_multi.py`: Multi-scenario analysis with leverage and signal quality matrix.
- `backtest_sl_tp.py`: SL/TP optimisation backtesting.
- `backtest_leverage.py`: Leverage-specific backtest analysis.
- `dashboard.py`: Terminal dashboard (rich text) and Telegram Markdown dashboard. Metrics: equity, exposure, daily drawdown, win rate, unrealized PnL, active positions with SL distance.
- `dashboard_api.py`: FastAPI REST API (port 8000) serving JSON data for the web dashboard (`/api/dashboard`, `/api/history`, `/api/signals`). Includes CCXT Binance Demo retry logic for StreamReader bug.
- `dashboard.html`: HTML/CSS/JS web dashboard consuming `dashboard_api.py`.
- `database.py`: SQLite database manager. Tables: `positions` (active & history), `signal_cooldowns` (Telegram spam prevention), `signals_log` (signal history for dashboard). Auto-migration for missing columns.
- `config_loader.py`: YAML config loader with `load_config()`, `get_config()` (cached), `reload_config()` (forced).
- `telegram_utils.py`: Centralized Telegram message sender (`send_telegram`) with HTML parse mode and aiohttp.
- `metrics.py`: Performance metrics calculation (win rate, profit factor, Sharpe, Calmar, max drawdown) from a trades DataFrame.
- `init_equity.py`: Utility to compute and save initial equity (calls `dashboard.get_equity_data`).
- `check_connection.py`: Diagnostic tool to verify Binance API connection (balance + orders fetch).
- `check_balance.py`: CLI tool to display account balances filtered by watchlist assets.
- `close_all_positions.py`: Utility to cancel all orders and sell non-USDT assets (Spot).
- `reset_bot.sh`: Shell script to stop the bot, wipe the database, delete logs, and restart.
- `config.yaml`: Global production configuration (not tracked in git — see `config.yaml.example`).
- `trading_bot.db`: SQLite database for persistent trade state (schema for orders & positions).
- `test_indicators.py`, `test_integration.py`, `test_signal_generation.py`, `test_trailing_sl.py`: Unit/integration tests.

## Building and Running

### Prerequisites
- Python 3.14+
- Active virtual environment: `source env/bin/activate`

### Installation
```bash
pip install -r requirements.txt
```

### Running the Bot (Development)
- **Live Trading:** `python main.py --live`
- **Telegram Listener:** `python main.py --listen`
- **Full Bot:** `python main.py --all`
- **Backtest:** `python main.py --backtest`
- **Connection Check:** `python main.py --check`
- **Dashboard API:** `python dashboard_api.py` (then open `dashboard.html` in browser)

### Running in Production (Linux/Systemd)
To ensure the bot restarts automatically on system boot or crash, deploy it as a systemd service:

1. Create `/etc/systemd/system/dpsk_bot.service` with your user details.
2. `sudo systemctl daemon-reload`
3. `sudo systemctl enable dpsk_bot`
4. `sudo systemctl start dpsk_bot`

## Development Conventions

- **Modularity:** Keep logic separated into `moduleX` files.
- **Asynchronous Code:** `asyncio` mandatory for network I/O.
- **Safety First:** All positions protected by SL + TP1 + TP2 orders placed directly on Binance Futures.
- **Orphan Cleanup:** Automatic cancellation of exchange orders upon software-detected exit.
- **Logging:** Use standard `logging` with `RotatingFileHandler` (10 MB, 5 backups).
- **Config Hot-Reload:** Config is checked for file modification time each cycle and reloaded automatically.

## Roadmap Status (Updated)

### Phase 7: Dashboard API, Reconciliation & Resilience (DONE)
- [x] **Dashboard API (FastAPI):** REST endpoint (`/api/dashboard`, `/api/history`, `/api/signals`) for web dashboard consumption.
- [x] **Web Dashboard:** `dashboard.html` with interactive HTML/CSS/JS UI.
- [x] **Startup Reconciliation:** DB positions compared against real Binance positions on startup — missing positions closed, orphan positions alerted.
- [x] **Periodic Position Sync:** `sync_position_with_exchange()` detects manual quantity/price changes on Binance each cycle.
- [x] **Telegram Heartbeat:** Configurable periodic status message (active positions + PnL) via `heartbeat_minutes`.
- [x] **Signal Logging:** All detected signals (traded or rejected) logged to `signals_log` table and exposed via API.
- [x] **Telegram Utils Centralization:** `telegram_utils.py` for all Telegram message sending.
- [x] **ADX Dynamic Position Sizing:** Position size adjusts based on ADX trend strength (0.5×–1.5× multiplier).
- [x] **CCXT Demo Retry Logic:** Retry wrapper for `fetch_balance`/`fetch_tickers` to handle Binance Demo StreamReader bug.
- [x] **Emergency SL Failure Handling:** If SL placement fails after entry, emergency exit order is placed and alert sent.
- [x] **DB Insert Failure Guard:** If DB insert fails after exchange order placement, a detailed Telegram alert is sent with all order IDs for manual repair.
- [x] **Scripts:** `check_connection.py`, `check_balance.py`, `close_all_positions.py`, `init_equity.py`, `reset_bot.sh`.

### Phase 6: Precision & Resilience (DONE)
- [x] **Smart Money Concepts (SMC):** Implementation of BOS (Break of Structure) and CHoCH (Change of Character) confluences.
- [x] **Dynamic Fibonacci:** Proximity threshold now adapts to market volatility (ATR%).
- [x] **Exchange-Side Take Profits:** TP1 (50%) and TP2 (100%) placed as `TAKE_PROFIT_MARKET` orders.
- [x] **Cycle Optimisation:** Split monitoring (60s) from scanning (15m) to reduce API load.
- [x] **Leverage-Aware Sizing:** Correct quantity calculation for Futures (risk/leverage).
- [x] **Orphan Order Cleanup:** Systematic cancellation of SL/TP on Binance when a trade is closed in software.
- [x] **Optimized Signal Selection:** Evaluation of both directions to select the max confluence score.
- [x] **Backtest Matrix v2:** Automated comparison of signal quality axes (Daily filter, KC, Min Conf).

### Phase 5: Technical Debt, Stability & Final Audit (DONE)
- [x] Async Integrity (aiohttp migration).
- [x] Config Hot Reload (`reload_config`).
- [x] Advanced Metrics (Drawdown, Sharpe, Calmar).
- [x] O(n²) bottleneck fix in backtest.
- [x] Robust signal handling (look-ahead bias, orphan parameters, log rotation).
- [x] Graceful Shutdown (SIGTERM handling + SIGTERM handler).
- [x] Professional Dashboard monitoring (Liveness, Exposure, DD tracking).