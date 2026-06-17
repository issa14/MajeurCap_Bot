# Dpsk - Crypto Trading Bot (Production Ready)

Dpsk is a modular, high-performance cryptocurrency trading bot designed for automated technical analysis, signal generation, and trade execution on Binance. Featuring a fully asynchronous architecture with built-in risk management and automatic safety mechanisms.

## Project Overview

- **Core Technology:** Python 3.14+, `asyncio`, `aiohttp`
- **APIs:** CCXT (Binance), Telegram Bot API.
- **Data Analysis:** Pandas, NumPy, Ta-lib.
- **Architecture:** Fully asynchronous, robust modular design.
- **Strategy:** Multi-indicator confluence (EMA, RSI, ATR, Zigzag) + Smart Money Concepts (BOS, CHoCH). Features dynamic Fibonacci proximity and ADX trend filtering.
- **Safety:** Circuit breaker (daily drawdown stop), Exchange-side SL & TP placement (survives crashes), Auto-cleanup of orphan orders, and Graceful Shutdown handling.

## Directory Structure

- `bot_telegram.py`: Main entry point. Optimised cycles: 60s position monitoring / 15min signal scan.
- `bot_listener.py`: Telegram command interface (polling).
- `trade_manager.py`: Position management, trailing SL, exchange order sync & cleanup.
- `execution.py`: Order placement (Binance Futures Demo/Mainnet). Supports SL + TP1 (50%) + TP2 (100%).
- `risk_manager.py`: Leverage-aware position sizing and circuit breaker logic.
- `module1_data_v3.py`: Async OHLCV fetching.
- `module2_AT.py`: Technical analysis (indicators + zigzag).
- `module3_signal.py`: SMC Signal logic (BOS, CHoCH, Fibo, KC, RSI). Best direction selection.
- `module4_backtest.py`: Backtesting framework with realistic fees/slippage.
- `backtest_multi.py`: Multi-scenario analysis with leverage and signal quality matrix.
- `config.yaml`: Global production configuration.
- `trading_bot.db`: SQLite database for persistent trade state (SQL schema for orders & positions).

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

### Running in Production (Linux/Systemd)
To ensure the bot restarts automatically on system boot or crash, deploy it as a systemd service:

1. Create `/etc/systemd/system/dpsk_bot.service` with your user details.
2. `sudo systemctl daemon-reload`
3. `sudo systemctl enable dpsk_bot`
4. `sudo systemctl start dpsk_bot`

## Development Conventions

- **Modularity:** Keep logic separated into `moduleX` files.
- **Asynchronous Code:** `asyncio` mandatory for network I/O.
- **Safety First:** All positions protected by SL + TP1 + TP2 orders placed directly on Binance.
- **Orphan Cleanup:** Automatic cancellation of exchange orders upon software-detected exit.
- **Logging:** Use standard `logging` with `RotatingFileHandler`.

## Roadmap Status (Updated)

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
