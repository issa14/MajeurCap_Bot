# Dpsk - Crypto Trading Bot

Dpsk is a modular cryptocurrency trading bot designed for automated technical analysis, signal generation, and trade execution on Binance (currently configured for Testnet). It features a robust architecture with separate modules for data fetching, indicator calculation, and risk management.

## Project Overview

- **Core Technology:** Python 3.14+
- **APIs:** CCXT (Binance), Telegram Bot API.
- **Data Analysis:** Pandas, NumPy, Ta-lib (via `ta` library).
- **Architecture:** Modular design with asynchronous I/O.
- **Strategy:** Multi-indicator confluence (EMA, RSI, ATR, Keltner Channels) combined with market structure analysis (Zigzag, Fibonacci, BOS/CHoCH). Now includes an **Advanced ATR-based Trailing Stop-Loss**.

## Directory Structure

- `bot_telegram.py`: Main entry point for live trading and Telegram notifications.
- `trade_manager.py`: Manages open positions, handles TP/SL updates (including trailing SL), and persists state in `positions.json`.
- `execution.py`: Handles order placement, stop-loss management, and SL updates on Binance Testnet.
- `risk_manager.py`: Calculates position sizing based on account capital and risk per trade.
- `module1_data_v3.py`: Asynchronous data fetching and caching of OHLCV data.
- `module2_AT.py`: Technical analysis and indicator computations.
- `module3_signal.py`: Signal detection logic based on confluences and market structure.
- `module4_backtest.py` / `backtest_multi.py`: Backtesting framework for single and multiple symbols.
- `config.yaml`: Global configuration for symbols, timeframes, indicators, risk, and API keys.
- `cache/`: Local storage for cached OHLCV data to reduce API calls.
- `positions.json`: Persistent storage for active and closed trades.

## Building and Running

### Prerequisites
- Python 3.14+
- Active virtual environment: `source env/bin/activate`

### Installation
```bash
pip install -r requirements.txt
```

### Running the Bot

The bot now features a centralized CLI via `main.py`. You can use it to launch different parts of the system:

- **Check API Connection:**
  ```bash
  python main.py --check
  ```
- **Live Trading/Monitoring (Single Scan):**
  ```bash
  python main.py --live
  ```
- **Listen for Telegram Commands:**
  ```bash
  python main.py --listen
  ```
- **Backtesting (Parameter Optimization):**
  ```bash
  python main.py --backtest
  ```

Alternatively, you can still run individual scripts:
- **Direct Live Scan:** `python bot_telegram.py`
- **Single Backtest:** `python module4_backtest.py`
- **Dashboard View:** `python dashboard.py`

### Running Tests
Unit tests are provided for signal generation and indicator calculations:
```bash
python test_signal_generation.py
python test_indicators.py
python test_trailing_sl.py
python test_integration.py
python check_connection.py
python dashboard.py
python bot_listener.py
```

### Configuration
Edit `config.yaml` to set your:
- Watchlist symbols.
- Risk parameters (`capital`, `risk_per_trade`).
- **Trailing Stop-Loss settings** (`trailing_sl_enabled`, `trailing_sl_atr_mult`).
- Binance API keys (Testnet recommended).
- Telegram Bot token and Chat ID.

## Development Conventions

- **Modularity:** Keep logic separated into `moduleX` files.
- **Asynchronous Code:** Use `asyncio` and `ccxt.async_support` for all network-bound operations.
- **Logging:** Use the standard `logging` library. Logs are written to `bot.log`.
- **Error Handling:** Always wrap API calls and execution logic in try/except blocks to prevent bot crashes.
- **Type Hinting:** Use Python type hints for better code clarity and IDE support.
- **State Management:** Active positions are tracked in `positions.json`. Do not modify this file manually while the bot is running.

## TODO / Future Improvements
- [ ] Support for Binance Mainnet (with caution).
- [ ] Implement a GUI or web dashboard for monitoring.
- [ ] Add more complex strategy confluences (e.g. Volume Profile, Liquidity).
