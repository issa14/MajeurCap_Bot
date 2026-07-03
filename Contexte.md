## 🧾 DPSK – Résumé du Projet (v2 — Juillet 2026)

- **Type :** Trading bot algorithmique multi-paires sur Futures
- **Objectif :** Automatiser une stratégie de trading sur Binance Futures (Testnet → Production) avec backtest, optimisation Monte‑Carlo, exécution réelle, supervision Telegram et dashboard web.
- **Exchange :** Binance Futures (via `ccxt`) — Demo/Testnet activé par défaut
- **Timeframe :** 4h (principal), données daily pour le filtre de tendance
- **Watchlist :** BTC, ETH, HYPE, XRP, LINK, BNB, SOL, VET (8 paires)


### Architecture & Modules (version réelle)

1. **`module1_data_v3.py`** — Récupération des données OHLCV
   - Connexion async à Binance Futures via `ccxt.async_support`
   - Cache fichier JSON avec TTL (240 min par défaut)
   - Fonctions : `init_exchange_async()`, `fetch_ohlcv_async()`, `fetch_all_async()`, `fetch_daily_all_async()`
   - Ajout de la colonne `is_closed` pour distinguer bougies en cours/terminées

2. **`module2_AT.py`** — Analyse technique
   - EMA (20, 50, 200), ATR, RSI, Keltner Channels, ADX
   - Zigzag causal (sans look-ahead bias) avec alternance stricte
   - Détection de structure de marché via `detect_structure()` (appel à `module3_signal`)
   - Support/Résistance par pivots, Volume Profile simplifié
   - Fonctions clés : `compute_indicators()`, `compute_zigzag()`, `clean_ohlcv()`

3. **`module3_signal.py`** — Génération de signaux
   - Scoring multi-facteurs : tendance (EMA + ADX), momentum (RSI), Keltner, volume
   - Détection de confluences (minimum 4 pour un signal valide — config gagnante Monte Carlo)
   - Fibonacci depuis les swings du zigzag
   - Filtres : daily trend, ADX threshold, range market
   - Fonctions : `generate_signal()`, `generate_signal_mtf()`, `scan_all()`

4. **`module4_backtest.py`** — Backtest & simulation
   - Simulation P&L avec frais, slippage, SL/TP configurables
   - Mode multi-timeframe, SL trailing/ATR
   - Variantes : `backtest_multi.py`, `backtest_sl_comparison.py`, `backtest_winner.py`

5. **`optimizer_mc.py`** — Optimisation Monte Carlo des hyperparamètres
   - Grille : min_confluences × sl_atr_mult × kc_filter × nos
   - Scoring anti-overfitting : max(min(score_recent, score_historical))
   - Export JSON des résultats

6. **`monte_carlo_validation.py`** — Validation Monte Carlo
   - Bootstrapping d'equity curve (50 000 simulations)
   - Probabilité de gain, Drawdown > 50%, Sharpe P5
   - Critères de validation stricts (≥95%, ≤10%, ≥0.5)

7. **`main.py`** — CLI asynchrone (point d'entrée)
   - Modes : `--live`, `--listen`, `--all`, `--backtest`, `--check`
   - PID lock anti-double-lancement (`.bot.pid`)
   - `run_live()` → `bot_telegram.main()` (pipeline complet)
   - `run_all()` → bot + listener Telegram en parallèle

8. **`bot_telegram.py`** — Orchestrateur principal
   - `main()` → boucle infinie de scan
   - `scan_all()` → Module 1 → Module 2 → Module 3 → Risk Manager → Exécution
   - `manage_positions()` → suivi des positions ouvertes (SL/TP/trailing)
   - Heartbeat périodique, reconciliation DB/Binance

9. **`trade_manager.py`** — Gestion des positions
   - `open_position()` — création d'ordre avec SL/TP
   - `check_position()` — suivi post-entrée (TP1 partiel, trailing SL, TP2, SL)
   - `manage_positions()` — boucle sur toutes les positions actives
   - Persistance JSON + DB

10. **`risk_manager.py`** — Gestion du risque
    - `calculate_position_size()` — taille selon capital et risk_per_trade
    - Vérifications : exposition max, nombre max de positions, daily loss limit
    - Circuit breaker si daily_loss_limit atteinte

11. **`execution.py`** — Passerelle Binance Futures
    - `init_trading_exchange()` — connexion avec clés API
    - `execute_signal()` — ordre MARKET avec SL/TP conditionnels
    - `update_sl_order()` — modification du SL (trailing)
    - `fetch_positions_pnl()` — P&L non réalisé depuis l'exchange

12. **`database.py`** — Base de données SQLite
    - Tables : signal_logs, positions (via JSON + DB)
    - `DatabaseManager` avec méthodes CRUD
    - `cleanup_old_records()` — purge automatique

13. **`dashboard.py` & `dashboard_api.py` & `dashboard.html`**
    - Backend : FastAPI (pas Flask)
    - Frontend : HTML responsive avec dark mode
    - Endpoints : `/api/equity`, `/api/positions`, `/api/performance`

14. **`bot_listener.py`** — Listener Telegram
    - Polling des commandes utilisateur
    - Commandes : /start, /status, /performance, /risk, /trades, /pnl, /help

15. **`telegram_utils.py`** — Utilitaires Telegram
    - `send_telegram()` — envoi de messages formatés HTML
    - Gestion des messages longs (découpage)

16. **`config_loader.py`** — Chargement YAML
    - `load_config()` / `get_config()` (avec cache)
    - `reload_config()` — rechargement à chaud

17. **`metrics.py`** — Métriques de performance
    - Sharpe, Sortino, Calmar, Max Drawdown, Win Rate, Profit Factor


### Flux d'exécution réel

```
main.py --all
  ├─ run_live()
  │   └─ bot_telegram.main()          ← boucle infinie
  │       ├─ module1: fetch_all_async() + fetch_daily_all_async()
  │       ├─ module2: compute_indicators() + detect_structure()
  │       ├─ module3: scan_all() → generate_signal() pour chaque paire
  │       ├─ risk_manager: calculate_position_size() + filtres
  │       ├─ execution: execute_signal() → ordre Binance
  │       └─ trade_manager: manage_positions() → suivi SL/TP/trailing
  └─ run_listener()
      └─ bot_listener: poll_updates() → commandes Telegram
```


### Configuration gagnante (Monte Carlo — Juillet 2026)

| Paramètre | Valeur |
|-----------|--------|
| `min_confluences` | **4** |
| `min_confluences_no_struct` | 4 |
| `kc_filter` | **true** |
| `sl_atr_mult` | 1.0 |
| `trailing_sl_enabled` | **false** |
| `daily_loss_limit` | -5% |
| `leverage` | 5x |
| `risk_per_trade` | 1% |
| `max_positions` | 5 |
| `max_exposure` | 30% |

Score de robustesse : 217.5 (vs 72.1 pour l'ancienne config min_conf=3)