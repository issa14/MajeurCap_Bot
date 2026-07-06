# MajeurCap_Bot

Bot de trading automatisé pour Binance Futures (perpetuals), timeframe 4H.
Génère des signaux LONG/SHORT via une logique de confluence ATR, pose les
ordres SL + TP1 + TP2 directement sur l'exchange, et gère les positions via
une boucle de réconciliation continue (`sync_all()`).

Développé avec CCXT (`ccxt.async_support`), SQLite pour la persistance, et
Telegram pour les alertes et le contrôle à distance.

## Stack technique

- **Exchange** : Binance Futures Demo (`enable_demo_trading`)
- **Librairies** : `ccxt` (≥4.5.64), `pandas`, `ta`, `asyncio`, `aiohttp`, `FastAPI`
- **Persistance** : SQLite (`trading_bot.db`)
- **Contrôle** : Telegram Bot API (`bot_telegram.py` + `bot_listener.py`) —
  commandes `/pnl`, `/closeall`
- **Dashboard** : `dashboard_api.py` (FastAPI) + `dashboard.html`

## Stratégie (configuration de production actuelle)

- **Watchlist** : BTC, ETH, HYPE, SUI, LINK, BNB, SOL, VET (paires USDT)
- **Confluences minimum** : 4 (le score pondéré sert uniquement à choisir la
  direction LONG/SHORT, pas de seuil d'entrée sur le score)
- **Stop-loss** : 1.0× ATR — **pas de trailing SL** (dégrade le Sharpe de 1.83
  à 0.31 en backtest, éliminé définitivement)
- **Take-profits** : TP1 à 1.2R, TP2 à 2.0R
- **Levier** : 5x

## Architecture
```

majeurcap/
├── trade_manager.py       # Orchestration : sync_all(), gestion des positions
├── execution.py           # Exécution des signaux, mise à jour des ordres SL
├── database.py            # Accès SQLite
├── config_loader.py       # Chargement de la config YAML
├── risk_manager.py         # Sizing, exposition
├── bot_telegram.py / bot_listener.py   # Alertes et commandes Telegram
├── dashboard_api.py / dashboard.html   # Dashboard de suivi
│
├── core/                  # Types et exceptions partagés
│   ├── types.py            # ExchangeOrder (utilisé) ; Position/Signal/
│   │                        # OrderIntent (définis, pas encore branchés — voir
│   │                        # "Reste à faire")
│   └── exceptions.py
│
├── exchange/               # Seul point de contact avec ccxt
│   ├── normalize.py         # get_stop_price(), get_raw_order_type() —
│   │                        # corrige les incohérences triggerPrice/stopPrice
│   │                        # et le collapse stop_market/take_profit_market → market
│   └── gateway.py           # ExchangeGateway (testé, pas encore branché dans
│                             # le chemin d'exécution live — voir "Reste à faire")
│
├── risk/
│   └── circuit_breaker.py   # Circuit breaker SL/TP à cooldown temporel
│                             # (remplace un ancien blocage permanent)
│
├── sync_engine/            # Logique de synchronisation extraite de sync_all()
│   ├── protection.py        # Recréation des ordres SL/TP manquants
│   ├── reconciliation.py    # Recherche des ordres existants (par ID puis par prix)
│   └── constants.py
│
└── tests/                  # Tests unitaires (exchange, circuit breaker, sync_engine)

```
## État du refactoring

`sync_all()` faisait ~590 lignes avant refactoring (une fonction unique
mélangeant appels ccxt, matching d'ordres, circuit breaker et alertes). Elle
fait aujourd'hui ~148 lignes — orchestration pure, la logique métier vit dans
des fonctions nommées et testées.

### Fait

- **`exchange/`** : bugs ccxt corrigés et couverts par tests — `triggerPrice`
  absent (fallback `stopPrice`/`info.stopPrice`), et le collapse de
  `stop_market`/`take_profit_market` en `"market"` sur les marchés futures.
- **`risk/circuit_breaker.py`** : le circuit breaker SL/TP se bloquait
  définitivement une fois le seuil d'échecs atteint (le reset ne pouvait
  survenir que sur succès de recréation, lui-même bloqué). Remplacé par un
  cooldown temporel (15 min) avec sortie garantie et ré-alerte périodique.
- **`sync_engine/protection.py`** et **`reconciliation.py`** : logique de
  recréation et de recherche d'ordres extraite de `sync_all()`, avec tests
  utilisant un exchange mocké.
- **Découpe complète de `sync_all()`** : les 4 cas (position absente de
  Binance, divergence DB↔Binance, réconciliation SL/TP, positions orphelines)
  sont désormais des fonctions isolées et testables (`_handle_position_missing_on_exchange`,
  `_align_db_with_exchange`, `reconcile_sl_tp_orders`, `_report_orphan_position`).

### Reste à faire

- **Déplacer les 3 fonctions encore dans `trade_manager.py`**
  (`_handle_position_missing_on_exchange`, `_align_db_with_exchange`,
  `_report_orphan_position`) vers `sync_engine/`, pour cohérence avec le Cas C
  déjà déplacé. Décision en attente — elles sont déjà isolées et testables
  même sans ce déplacement.
- **`core/types.py` : `Position`, `Signal`, `OrderIntent` sont définis mais
  non utilisés.** Seul `ExchangeOrder` est réellement branché (dans `exchange/`).
  Le reste du code (`trade_manager.py`, `sync_engine/`) continue de manipuler
  des dicts bruts pour les positions — la migration vers des types explicites
  n'a pas encore commencé.
- **`ExchangeGateway` n'est pas branché dans le chemin d'exécution réel.**
  Il est défini et testé (`exchange/gateway.py`), mais `sync_engine/` et
  `trade_manager.py` continuent d'appeler `exchange.fetch_open_orders()` /
  `exchange.fetch_order()` directement en ccxt brut plutôt que via la gateway.
- **`config/schema.py` en Pydantic** : jamais commencé. La configuration reste
  un dict YAML chargé sans validation de schéma.
- **`max_exposure` (250%) et `max_positions` (5)** : valeurs identifiées comme
  correctes pour le capital actuel, pas encore appliquées en config.
- **Nettoyage manuel des ordres orphelins** sur Binance Futures Demo
  (symbole `BNB/USDT:USDT`), accumulés pendant la fenêtre du bug de format de
  symbole — quota d'ordres stop (`-4045`) potentiellement encore affecté.
- **`monte_carlo_no_xrp_test.py`** préparé pour isoler la contribution de XRP
  à la dégradation du Sharpe observée en substitution SUI→XRP — résultat pas
  encore obtenu.
- **Déploiement Oracle Cloud** avec service `systemd` pour la reprise sur
  crash — pas encore fait.
- **Heartbeat Telegram périodique** (toutes les 4-6h pour les positions
  ouvertes) — conçu, statut d'implémentation non confirmé.

## Principes établis

- Le trailing SL est éliminé définitivement — combinaison structurellement
  nocive avec `sl_atr_mult: 1.0`, pas un problème de réglage.
- Le score pondéré ne sert qu'à choisir LONG vs SHORT, jamais de seuil d'entrée
  (utilisé comme seuil, il sous-performe le comptage brut de confluences).
- `min_confluences: 4` est le changement de config le plus impactant validé
  à ce jour (Sharpe 0.83 out-of-sample, robustesse ×3).
- Toujours contrôler le levier et les autres facteurs de confusion en
  comparant des résultats de backtest — un gain apparent peut être un artefact
  (ex. levier doublé plutôt qu'amélioration de stratégie).
- Toute correction appliquée via Gemini CLI est ré-auditée ligne à ligne avant
  d'être considérée comme terminée.
