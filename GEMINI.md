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
- `dashboard.py`: Dashboard view for account equity and positions.
- `database.py`: SQLite abstraction layer for persistent storage.
- `positions.json.bak`: Legacy position storage (migrated to SQLite).
- `trading_bot.db`: SQLite database for active and historical trades.

## Building and Running
...
### Configuration
Edit `config.yaml` to set your:
- Watchlist symbols.
- Risk parameters (`capital`, `risk_per_trade`).
- **Trailing Stop-Loss settings** (`trailing_sl_enabled`, `trailing_sl_atr_mult`).
- **Backtest settings** (`fee_pct`, `slippage_pct`).
- Binance API keys (Testnet recommended).
- Telegram Bot token and Chat ID.

## Development Conventions

- **Modularity:** Keep logic separated into `moduleX` files.
- **Asynchronous Code:** Use `asyncio` and `ccxt.async_support` for all network-bound operations.
- **Logging:** Use the standard `logging` library. Logs are written to `bot.log`.
- **Error Handling:** Always wrap API calls and execution logic in try/except blocks. Execution now includes emergency exits if SL placement fails.
- **Type Hinting:** Use Python type hints for better code clarity.
- **State Management:** Active positions are tracked in `trading_bot.db` (SQLite). Use `DatabaseManager` for all CRUD operations.

## TODO / Institutional Roadmap

### Phase 3 : Intelligence Stratégique & Gestion du Risque (EN COURS)
- [ ] **Filtre de Marché Global :** Intégrer une vérification de la tendance BTC sur une unité de temps supérieure (Daily/4H) pour filtrer les alts.
- [ ] **Raffinage BOS/CHoCH :** Exiger une clôture (Close) au-delà du niveau pivot pour confirmer une cassure de structure, plutôt qu'une simple mèche.
- [ ] **Position Sizing Dynamique :** Ajuster le risque par trade en fonction de l'ADX (force de la tendance).

### Phase 4 : Excellence Opérationnelle & Monitoring
- [ ] **Mode Daemon :** Transformer le scan live en boucle infinie synchronisée sur les clôtures de bougies.
- [ ] **Signal Context Logging :** Enregistrer l'état complet du marché (indicateurs, structure) lors de chaque signal pour analyse post-mortem.
- [ ] **Support Binance Mainnet :** Préparer le passage en réel avec des garde-fous supplémentaires.

### Phase 5 : Dette Technique & Refactoring (Audit Juin 2026)
- [ ] **Async Integrity (bot_listener) :** Remplacer `requests` par `aiohttp` dans `bot_listener.py` pour éviter de bloquer l'event loop.
- [ ] **Config Hot Reload :** Implémenter `reload_config()` dans `config_loader.py` et déplacer le chargement de la config à l'intérieur de `run_scan()` dans `bot_telegram.py`.
- [ ] **Advanced Metrics :** Intégrer le calcul du Max Drawdown, du ratio de Sharpe et du ratio de Calmar dans `compute_metrics` (backtesting).

