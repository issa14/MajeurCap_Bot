# 🔍 AUDIT COMPLET — MajeurCap_Bot (DPSK)
## Rapport d'expertise quant · Trading algorithmique · Juillet 2026

---

## 📋 SOMMAIRE

1. [Synthèse exécutive](#1-synthèse-exécutive)
2. [Vulnérabilités critiques](#2-vulnérabilités-critiques-)
3. [Architecture & Design](#3-architecture--design)
4. [Stratégie de trading & Logique métier](#4-stratégie-de-trading--logique-métier)
5. [Gestion des risques](#5-gestion-des-risques)
6. [Exécution & Trade management](#6-exécution--trade-management)
7. [Backtesting & Validation Monte Carlo](#7-backtesting--validation-monte-carlo)
8. [Base de données & Persistance](#8-base-de-données--persistance)
9. [Tests](#9-tests)
10. [Configuration & Secrets](#10-configuration--secrets)
11. [Intégration Telegram & Notifications](#11-intégration-telegram--notifications)
12. [Sécurité](#12-sécurité)
13. [Bugs & Edge cases](#13-bugs--edge-cases)
14. [Matrice des risques](#14-matrice-des-risques)
15. [Recommandations priorisées](#15-recommandations-priorisées)

---

## 1. SYNTHÈSE EXÉCUTIVE

**Projet audité :** DPSK — Bot de trading algorithmique sur Binance Futures  
**Version :** v2 (Juillet 2026)  
**Date d'audit :** 02/07/2026  
**Périmètre :** 25 fichiers source + configuration + tests + documentation  

### Note globale : **B+ (74/100)**

| Axe | Note | Commentaire |
|-----|------|-------------|
| Stratégie & Backtesting | 85/100 | Excellente rigueur méthodologique (Monte Carlo, OOS) |
| Gestion des risques | 78/100 | Circuit breaker solide, sizing ADX pertinent |
| Qualité du code | 72/100 | Modulaire mais imports directs, duplication modérée |
| Exécution & Trade mgmt | 80/100 | Réconciliation DB↔Exchange robuste, ordres conditionnels OK |
| Sécurité | 50/100 | 🔴 **Secrets en clair dans config.yaml** (seul vrai risque) |
| Tests | 55/100 | Couverture partielle, pas de tests unitaires isolés |
| Monitoring/Ops | 75/100 | Heartbeat, dashboard, logs en rotation |

### ✅ Points forts majeurs
- **Validation Monte Carlo exemplaire** — 50 000 itérations × 8 configurations × 2 fenêtres temporelles, avec out-of-sample strict. La méthodologie `max(min(recent, historical))` est un très bon garde-fou anti-overfitting.
- **Zigzag causal sans look-ahead** — L'implémentation est rigoureuse : identification retardée de `window` bougies, pas de contamination future.
- **Réconciliation DB ↔ Exchange** — `sync_all()` et `reconcile_positions_on_startup()` couvrent les cas de positions orphelines, ordres FILLED hors surveillance, et quantités désynchronisées. Très bon niveau de résilience opérationnelle.
- **Circuit breaker journalier** — Le `daily_loss_limit` à -5% avec récupération du capital live depuis l'exchange est une protection essentielle.
- **Scoring anti-corrélation** — `compute_confluence_score()` plafonne les groupes de facteurs corrélés (EMA + above_EMA200, RSI + position KC) pour éviter le double-comptage. Approche quant robuste.

### 🔴 Points critiques
- **Secrets exposés** (cf. section 2)
- **Incohérence de watchlist** entre les scripts de validation et la config live
- **Fuite mémoire asyncio** — `create_task()` non supervisées
- **Absence de tests unitaires** sur les fonctions cœur de la stratégie

---

## 2. VULNÉRABILITÉS CRITIQUES 🔴

### 2.1 SEC-001 : Clés API et token Telegram en clair dans config.yaml

**Fichier :** `config.yaml` (lignes 55-63)  
**Sévérité :** 🔴 **CRITIQUE**  
**Impact :** Compromission totale des fonds si le dépôt GitHub est public

Le fichier `config.yaml` contient :
- Token Telegram : `8850681867:AAH7sgq388sp73RU0gdAOb_bSFCAXLRcS-k`
- Clé API Binance Testnet : `iESwABzkMeguUUKhM7sNA4Ad2u8orHDZ6HAYf765LvJrTrVDV7bg2QcRtCVWBPaL`
- Secret API Binance Testnet : `J9vOJu8q3eYRfuG1ycVfE4tuP99anIA1ZNYhPIF9CQzzvAAcnknbGbcEIBRov0rl`

**Analyse Git :** Le remote est `https://github.com/issa14/MajeurCap_Bot.git`. Si ce dépôt est public, ces secrets sont exposés à quiconque. Bien que `.gitignore` contienne `config.yaml`, le fichier est listé dans les onglets VS Code ouverts — il est donc présent localement. La question est : **a-t-il déjà été commité ?**

Vérification rapide via `git log -- config.yaml` nécessaire immédiatement.

**Recommandation :**
1. Révoquer immédiatement ces clés sur Binance Testnet et régénérer le token Telegram
2. Vérifier l'historique Git : `git log --all -- config.yaml`
3. Si commité → utiliser `git filter-branch` ou `bfg-repo-cleaner` pour purger l'historique
4. Migrer vers variables d'environnement (`.env` + `python-dotenv`)
5. Ajouter `config.yaml` au `.gitignore` AVANT tout commit (déjà fait, mais vérifier qu'il n'est pas déjà tracké)

### 2.2 SEC-002 : Dashboard web sans authentification (local uniquement)

**Fichiers :** `dashboard_api.py`, `dashboard.py`  
**Sévérité :** 🟢 **BASSE**  
**Impact :** Aucun en l'état — le serveur n'écoute que sur localhost

`dashboard_api.py` l.205 : `uvicorn.run(app, host="127.0.0.1", port=8000)` — le serveur n'écoute QUE sur localhost. Aucune surface d'attaque réseau externe. Le dashboard expose les endpoints `/api/dashboard`, `/api/history`, `/api/signals` en lecture seule sans authentification, mais uniquement accessibles depuis la machine locale.

**Risque résiduel :** Si le poste est multi-utilisateur ou si le host est changé en `0.0.0.0`, les données deviennent exposées.

**Recommandation :** Si le dashboard doit être exposé sur le réseau, ajouter un middleware d'authentification (API key). En l'état, aucun risque.

---

## 3. ARCHITECTURE & DESIGN

### 3.1 Structure modulaire

Le projet suit un pipeline linéaire bien défini :

```
Module1 (Data) → Module2 (AT) → Module3 (Signal) → Risk Manager → Execution → Trade Manager
```

**Note : 8/10** — La séparation des responsabilités est claire. Chaque module a un rôle unique et bien défini.

### 3.2 Injection de dépendances vs imports directs

| Fichier | Imports problématiques |
|---------|----------------------|
| `module2_AT.py` (l.258) | `from module3_signal import detect_structure` — dépendance circulaire évitée par import local, mais fragile |
| `module4_backtest.py` (l.13-21) | Importe directement `module1`, `module2`, `module3` — couplage fort |
| `trade_manager.py` (l.17-28) | Importe 8 modules en top-level — rend le test unitaire impossible sans mocker |

**Note : 6/10** — Le code utilise majoritairement des imports directs plutôt que de l'injection de dépendances. Les tests (`test_integration.py`) doivent patcher massivement (`@patch(...)` × 10) pour fonctionner.

**Recommandation :** Refactorer vers un pattern de dépendances injectées (passer `exchange`, `config`, `db` en paramètres plutôt qu'importer des singletons globaux).

### 3.3 Gestion de l'asynchronisme

**Points positifs :**
- Utilisation cohérente de `ccxt.async_support` partout
- `asyncio.gather()` pour les appels parallèles (fetch_all_async)
- Boucle principale avec `asyncio.Event` pour l'arrêt propre

**Points négatifs :**
- **`asyncio.create_task()` non supervisées** — pattern "fire-and-forget" utilisé à 15+ reprises dans `trade_manager.py` et `bot_telegram.py`. Les exceptions dans ces tâches sont silencieuses (pas de `await`, pas de `add_done_callback`). Cela concerne l'envoi de messages Telegram, l'annulation d'ordres, les notifications de réconciliation.
  ```python
  # trade_manager.py:311 — exception silencieuse possible
  asyncio.create_task(cancel_exchange_orders(symbol, pos, config))
  ```
- **Pas de gestion de backpressure** — si 50+ `create_task` sont lancées rapidement (ex: boucle de heartbeat sur 40 paires), la pile asyncio peut saturer.

**Recommandation :** Remplacer `asyncio.create_task()` par un pattern avec gestion d'erreur explicite :
```python
task = asyncio.create_task(cancel_exchange_orders(...))
task.add_done_callback(lambda t: log.error(f"Task failed: {t.exception()}") if t.exception() else None)
```

### 3.4 Hot-reload de configuration

**Fichier :** `bot_telegram.py` (l.34-47)  
**Note : 9/10**

La fonction `reload_config_if_changed()` utilise `os.path.getmtime()` pour détecter les modifications sans polling excessif. C'est élégant et efficace. Un bémol : la config est rechargée à chaque cycle de 60s même si inchangée (appel à `get_config()` via `reload_config_if_changed()` dans `manage_positions()`).

### 3.5 Gestion du cache

**Fichier :** `module1_data_v3.py` (l.34-71)  
**Note : 7/10**

- Cache JSON avec TTL configurable (240 min par défaut)
- Écriture atomique via `os.replace(tmp, final)` — évite les corruptions
- Désactivation automatique du cache quand `since` est fourni (backtest)
- **Problème :** Pas de cache en mémoire (le JSON est re-lu à chaque appel même pendant le TTL). Pour 8 paires × 500 bougies, c'est ~500 opérations disque par cycle.

**Recommandation :** Ajouter un cache mémoire (dict Python) avec TTL en plus du cache disque.

### 3.6 PID lock anti-double-lancement

**Fichier :** `main.py` (l.26-67)  
**Note : 8/10**

Implémentation propre avec `os.kill(pid, 0)` pour vérifier qu'un process est vivant. Gère correctement le cas où le fichier PID persiste après un crash (vérifie que le PID est actif). Libération dans un bloc `finally`.

---

## 4. STRATÉGIE DE TRADING & LOGIQUE MÉTIER

### 4.1 Zigzag causal

**Fichier :** `module2_AT.py` (l.14-70)  
**Note : 9/10**

L'implémentation est rigoureuse :
1. Détection d'extremums sur fenêtre glissante `[i-2*window, i]`
2. Décalage de `window` pour enregistrer le pivot à son index réel
3. Filtrage d'alternance stricte (HIGH → LOW → HIGH...) avec seuil `min_swing_diff_pct` (0.5%)
4. Fusion des pivots consécutifs de même type (garde le plus extrême)

**Pas de look-ahead bias confirmé.** Le zigzag n'utilise que des données disponibles à l'instant `t`.

### 4.2 Scoring multi-facteurs

**Fichier :** `module3_signal.py` (l.118-195)  
**Note : 8/10**

Le `compute_confluence_score()` est une amélioration notable par rapport au simple comptage de `check_confluences()` :

| Facteur | Poids | Groupe |
|---------|-------|--------|
| BOS | 1.5 | Structure |
| CHoCH | 2.0 | Structure |
| EMA20>50 + above_EMA200 | 1.0 max | Tendance (plafonné) |
| RSI zone + position KC | 1.0 max | Momentum (plafonné) |
| Fibonacci proximité | 0.5 | Confirmation |
| Volume surge | 0.5 | Confirmation |
| **Max théorique** | **5.5** | |

Le plafonnement des groupes corrélés (tendance, momentum) est une bonne pratique quantitative. Le code est correct : `detect_structure()` (l.60-69) ne peut pas retourner BOS et CHoCH dans la même direction — le BOS est dans le sens de la tendance, le CHoCH dans le sens opposé. `compute_confluence_score()` vérifie chaque direction séparément, donc BOS et CHoCH sont bien mutuellement exclusifs pour une direction donnée.

### 4.3 Incohérence LONG/SHORT entre generate_signal() et generate_signal_mtf()

**🔶 BUG : Sélection du meilleur signal**

- `generate_signal()` (l.259) : utilise `score > best_score` (score pondéré)
- `generate_signal_mtf()` (l.314) : utilise `len(confluences) > best_confluence_count` (count brut)

Cette incohérence signifie que :
- En mode standard (4h uniquement), le choix LONG vs SHORT est basé sur le score pondéré
- En mode MTF (daily structure), le choix est basé sur le compte brut de confluences

Cela peut conduire à des décisions différentes pour un même setup. **Le MTF devrait aussi utiliser le score pondéré.**

### 4.4 Filtre daily trend

**Fichier :** `module2_AT.py` (l.161-230)  
**Note : 8/10**

- `get_daily_trend_at_timestamp()` implémente un slicing CAUSAL strict : seules les bougies daily antérieures au timestamp 4h sont utilisées. Pas de look-ahead.
- Filtre global BTC : si BTC est bearish, les altcoins ne peuvent pas être bullishly tradés.
- **Edge case :** Si `daily_data` contient un symbole comme `WBTC/USDT` qui contient "BTC/" mais n'est pas exactement `BTC/USDT`, le fallback `BTC/USDT` ne sera pas trouvé → BTC trend non appliqué (l.211-212).

### 4.5 Calcul des niveaux SL/TP

**Fichier :** `module3_signal.py` (l.197-210)  
**Note : 7/10**

```python
sl_dist = atr * sl_mult
if direction == "long":
    sl = close - sl_dist
    tp1 = close + sl_dist * tp1_rr
    tp2 = close + sl_dist * tp2_rr
```

La formule est correcte et cohérente avec le risk manager. Le SL est basé sur ATR × multiplicateur (1.0 en config gagnante), les TPs sont en ratio R:R par rapport au SL (1.2R et 2.0R).

---

## 5. GESTION DES RISQUES

### 5.1 Position sizing

**Fichier :** `risk_manager.py` (l.22-93)  
**Note : 7/10**

Formule :
```
position_size = (capital × risk_per_trade%) / (sl_pct × entry_price)
```

Cette formule est **mathématiquement correcte** pour le calcul de la taille de position en futures. Le risque monétaire est `capital × risk%`, converti en quantité via la distance SL%.

**🔶 PROBLÈME : La formule ignore le levier pour la marge mais l'applique correctement pour le risque.** Le commentaire l.74-77 explique bien la distinction — le levier n'affecte que la marge, pas le PnL. C'est correct.

**Dynamic sizing ADX** (l.38-52) :
```python
multiplier = max(0.5, min(1.5, adx / 30.0))
risk_per_trade_pct = base_risk * multiplier
```

- ADX=15 → risk = 0.5% (tendance faible, on réduit)
- ADX=30 → risk = 1.0% (neutre)
- ADX=45 → risk = 1.5% (tendance forte, on augmente)

C'est une approche conservative pertinente. Bornes [0.5, 1.5] bien choisies.

**⚠️ `safety_margin_pct` (config) et `max_position_size_pct` ne sont PAS utilisés dans `calculate_position_size()`** — ces paramètres de config sont définis (l.77-78) mais aucun code ne les lit. Valeurs mortes.

### 5.2 Circuit breaker

**Fichier :** `trade_manager.py` (l.871-893)  
**Note : 8/10**

```python
realized_pnl_pct = db.get_realized_pnl_today(initial_capital=capital)
if realized_pnl_pct <= daily_loss_limit:
    return True  # bloqué
```

- Utilise `capital_override` (capital live depuis l'exchange) pour un calcul correct du % de drawdown
- `_circuit_breaker_alerted` évite le spam Telegram
- Réinitialisé à chaque cycle si le PnL remonte au-dessus du seuil

**🔶 PROBLÈME : Le circuit breaker vérifie uniquement le PnL RÉALISÉ, pas le PnL latent.** Si le bot a 5 positions ouvertes avec -4% de perte latente chacune, le circuit breaker ne se déclenchera pas car aucun trade n'est encore fermé.

**Recommandation :** Ajouter le PnL non réalisé dans le calcul du daily drawdown, ou au moins un warning si `unrealized_pnl < daily_loss_limit`.

### 5.3 Vérifications pré-entrée

**Fichier :** `trade_manager.py` (l.895-943)  
**Note : 8/10**

Avant d'ouvrir une position, `open_position()` vérifie séquentiellement :
1. Circuit breaker
2. Position déjà existante sur ce symbole
3. Nombre max de positions (5)
4. Exposition maximale (30%)
5. Position size > 0

+ Guard anti-doublon DB (l.979-982) — vérifie une seconde fois dans la DB avant l'insertion, au cas où deux cycles concurrents tenteraient d'ouvrir le même symbole.

---

## 6. EXÉCUTION & TRADE MANAGEMENT

### 6.1 Ouverture de position

**Fichier :** `execution.py` (l.61-201)  
**Note : 8/10**

Flux d'ouverture :
```
1. set_leverage()
2. load_markets() + amount_to_precision()
3. create_market_order() → entrée
4. create_order(stop_market) → SL
5. create_order(take_profit_market) → TP1 (50% qty)
6. create_order(take_profit_market) → TP2 (50% qty)
```

**Points positifs :**
- Sortie d'urgence si le SL échoue (l.128-139) : ordre market immédiat pour fermer la position
- TP1/TP2 placés directement sur Binance → survivent aux redémarrages du bot
- Gestion du stepSize via `amount_to_precision()`

**Note :** `load_markets()` dans execution.py l.88 est dans un bloc `try/except Exception` global (l.83-199), donc les erreurs sont catchées correctement. Le `load_markets()` de module1 (l.107-109) loggue un warning en cas d'échec — l'appel suivant dans execution réessaiera implicitement.

### 6.2 Réconciliation DB ↔ Exchange

**Fichier :** `trade_manager.py` (l.563-837)  
**Note : 9/10** — Excellent niveau de robustesse

`sync_all()` gère :
- **Cas A :** Position DB absente de Binance → interroge l'historique des ordres pour déterminer la raison de sortie (SL, TP1, TP2)
- **Cas B :** Quantité désynchronisée → mise à jour DB, fermeture si qty ≈ 0
- **Cas C :** Ordres SL/TP manquants → recréation automatique
- **Cas D :** Positions orphelines sur Binance (pas en DB) → alerte sans insertion

`reconcile_positions_on_startup()` (l.359-446) fait la même chose au démarrage, avec une logique de détection avancée via `_detect_exit_from_binance()` qui compare les `stopPrice` des ordres filled avec les SL/TP1/TP2 connus.

**🔶 Limite :** `_detect_exit_from_binance()` (l.471-560) utilise `fetch_orders(limit=15)` — si plus de 15 ordres ont été passés depuis l'ouverture, l'ordre de sortie réel peut ne pas être dans les résultats.

### 6.3 Trailing stop logiciel

**Fichier :** `trade_manager.py` (l.158-350, `check_position()`)  
**Note : 7/10**

Le trailing SL logiciel est implémenté dans `check_position()` (appelé toutes les 60s) :
- Activation après TP1 (si `trailing_sl_activation_tp=1`) ou immédiate (si `=0`)
- SL = close ± ATR × `trailing_sl_atr_mult`
- Mise à jour UNIQUEMENT si le nouveau SL est meilleur que l'actuel

**🔶 PROBLÈME :** Le trailing SL logiciel (dans `check_position`) et le SL sur l'exchange (via `update_sl_order`) peuvent être désynchronisés :
- `check_position` met à jour le SL en mémoire et en DB
- `update_sl_order` (l.282-304) n'est appelé que si `auto_execute=True`
- Si l'appel à `update_sl_order` échoue, le SL en DB est mis à jour mais le SL sur l'exchange reste à l'ancienne valeur → **la position peut être liquidée au mauvais prix**

**Recommandation :** Si `update_sl_order` échoue, revenir à l'ancien SL en DB également.

### 6.4 Fermeture en masse

**Fichier :** `trade_manager.py` (l.1101-1198)  
**Note : 8/10**

`close_all_positions_async()` avec confirmation en deux étapes via Telegram (`/closeall` → `/confirm_closeall`). Bonne protection contre les accidents.

---

## 7. BACKTESTING & VALIDATION MONTE CARLO

### 7.1 Qualité du backtest

**Fichier :** `module4_backtest.py`, `backtest_multi.py`  
**Note : 8/10**

- Simulation bougie par bougie avec OHLC réels
- Slippage et frais inclus (0.05% et 0.1% configurables)
- Gestion causale stricte : le signal à `t` n'utilise que les bougies `≤ t`
- `_add_is_closed()` exclut la bougie en cours

**🔶 PROBLÈME : `_add_is_closed()` utilise `datetime.now()` (l.91-93) :**
```python
now = datetime.now(timezone.utc)
df['is_closed'] = df['timestamp'].apply(lambda t: now >= (t + delta))
```
En backtest, cette fonction est appelée pour déterminer si une bougie est "close". Mais `datetime.now()` est l'heure réelle d'exécution, pas l'heure simulée. Résultat : **les bougies récentes peuvent être incorrectement marquées comme non closes**, ce qui fausse le backtest sur les données les plus récentes.

**Recommandation :** Injecter un `as_of` timestamp dans le backtest pour rendre le comportement déterministe.

### 7.2 Validation Monte Carlo

**Fichier :** `monte_carlo_validation.py`  
**Note : 9/10** — Exemplaire

- **50 000 itérations** de bootstrapping par fenêtre
- **Parallélisation multiprocessing** (N_CPU workers)
- **2 fenêtres** : recent + historical (395 jours)
- **Validation out-of-sample** via `backtest_winner.py` sur fenêtre 2024-2025 jamais vue
- **Scoring anti-overfitting** : `max(min(score_recent, score_historical))` — force la config à être bonne sur les DEUX périodes

**Résultat clé :** La config `min_confluences=4` est la seule à passer 2/3 critères en historique (prob gain 98.7%, Sharpe P5 0.577). Le DD>50% à 39.5% reste le seul point faible, mais est protégé par le daily loss limit à -5%.

### 7.3 Incohérence de watchlist

**🔶 BUG :** `monte_carlo_validation.py` (l.30-33) utilise :
```python
WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "HYPE/USDT", "SUI/USDT",
    "LINK/USDT", "BNB/USDT", "SOL/USDT", "VET/USDT"
]
```

Alors que `config.yaml` (l.2-10) et `Contexte.md` listent :
```yaml
- BTC/USDT, ETH/USDT, HYPE/USDT, XRP/USDT, LINK/USDT, BNB/USDT, SOL/USDT, VET/USDT
```

**`SUI/USDT` est dans Monte Carlo mais PAS dans la config live. `XRP/USDT` est dans la config live mais PAS dans Monte Carlo.** Les résultats Monte Carlo ne sont donc pas représentatifs de la config réelle.

De plus, `backtest_multi.py` (l.121) utilise `"HYPE/USDT:USDT"` au lieu de `"HYPE/USDT"` — le suffixe `:USDT` peut causer des erreurs de parsing selon la version CCXT.

### 7.4 Optimiseur Monte Carlo

**Fichier :** `optimizer_mc.py`  
**Note : 8/10**

Grid search sur 36 combinaisons (3×2×3×2). Le scoring anti-overfitting est cohérent. Résultat : `min_confluences=4, sl_atr_mult=1.0, kc_filter=true` est optimal, confirmant la découverte manuelle.

---

## 8. BASE DE DONNÉES & PERSISTANCE

### 8.1 Schéma

**Fichier :** `database.py`  
**Note : 7/10**

- **Table `positions`** : 21 colonnes, bien normalisée pour le use case
- **Table `signals_log`** : historique des signaux (tradés ou non)
- **Table `signal_cooldowns`** : anti-spam Telegram
- **Index unique** : `idx_active_symbol` sur `(symbol) WHERE status != 'closed'` — empêche les doublons de positions actives

**🔶 PROBLÈME : `check_same_thread=False` (l.17)**
```python
return sqlite3.connect(self.db_path, check_same_thread=False)
```
Cette option est nécessaire car `DatabaseManager` est un singleton global utilisé depuis plusieurs coroutines asyncio. MAIS SQLite n'est pas thread-safe par défaut et les écritures concurrentes depuis des coroutines différentes peuvent causer des corruptions silencieuses.

**Recommandation :** Soit :
1. Utiliser `aiosqlite` pour une compatibilité asyncio native
2. Ajouter un `asyncio.Lock` autour de toutes les opérations d'écriture

### 8.2 Migrations

**Fichier :** `database.py` (l.50-62)  
**Note : 6/10**

Les migrations sont faites "à la main" via `PRAGMA table_info()` puis `ALTER TABLE ADD COLUMN`. Pas de système de version. Si une migration échoue à mi-chemin, l'état de la DB est incohérent.

**Recommandation :** Ajouter une table `schema_version` et numéroter les migrations.

### 8.3 Atomicité

**🔶 PROBLÈME :** Aucune utilisation de transactions explicites. Les opérations comme `insert_position()` + `update_position()` sont dans des appels séparés → pas d'atomicité. Si le bot crash entre l'insertion DB et l'exécution exchange, la DB contient une position qui n'existe pas sur l'exchange.

La réconciliation au démarrage (`reconcile_positions_on_startup`) atténue ce risque, mais ne l'élimine pas.

### 8.4 `cleanup_old_records()`

**Note : 8/10**

Appelée au démarrage du bot, supprime les `signals_log` et `signal_cooldowns` de plus de 30 jours. Évite la croissance illimitée de la DB.

---

## 9. TESTS

### 9.1 Couverture

| Fichier de test | Type | Modules couverts | Qualité |
|-----------------|------|-----------------|---------|
| `test_trailing_sl.py` | Intégration | `trade_manager.check_position()` | ⭐⭐⭐⭐ |
| `test_integration.py` | Intégration | `bot_telegram`, `trade_manager`, `module3` | ⭐⭐⭐ |
| `test_indicators.py` | — | À vérifier | — |
| `test_signal_generation.py` | — | À vérifier | — |

### 9.2 `test_trailing_sl.py`

**Note : 7/10**

- Teste LONG et SHORT avec trailing SL activé
- Mock correct des dépendances (`fetch_all_async`, `compute_indicators`, `send_telegram`)
- Vérifie que le SL trailing est déclenché au bon prix
- **Manque :** Test du cas TP1 partiel (50% sorti, 50% trailing)

### 9.3 `test_integration.py`

**Note : 7/10**

- Teste le flux complet : scan → ouverture → TP1/trailing → SL
- Utilise `unittest.IsolatedAsyncioTestCase` (bonne pratique pour tester du code async)
- Isole la DB avec un fichier temporaire
- Mock lourd : 10+ `@patch` décorateurs → le test est fragile et teste plus les mocks que le code réel

### 9.4 Absences critiques

| Module non testé | Risque |
|-----------------|--------|
| `generate_signal()` | Fonction cœur de la stratégie — zéro test unitaire |
| `simulate_trade()` | Simulation backtest — aucune validation des calculs de PnL |
| `calculate_position_size()` | Risk manager — pas de test des edge cases (sl_pct=0, capital=0) |
| `compute_confluence_score()` | Scoring — pas de test de la pondération/plafonnement |
| `sync_all()` | Réconciliation — pas de test avec positions simulées |
| `execute_signal()` | Exécution — pas de mock des réponses Binance |

**Note globale tests : 55/100** — Couverture insuffisante pour un bot qui gère de l'argent réel.

---

## 10. CONFIGURATION & SECRETS

### 10.1 config.yaml

**Note : 5/10**

- Structure YAML propre et bien commentée
- Tous les paramètres sont documentés
- Les valeurs par défaut sont dispersées dans le code (ex: `config.get("signal", {}).get("kc_filter", True)`) plutôt que centralisées

**🔶 PROBLÈME : Pas de validation de schéma.** Si un paramètre est mal orthographié (ex: `min_confluences` → `min_confluence`), il sera silencieusement ignoré et la valeur par défaut sera utilisée. Aucun warning.

**Recommandation :** Ajouter une validation avec `pydantic` ou `cerberus` au chargement.

### 10.2 config.yaml.example

**Note : 9/10** — Le fichier exemple masque correctement tous les secrets. Bonne documentation. Seul bémol : il n'est pas automatiquement synchronisé avec la structure réelle (les nouveaux paramètres comme `max_position_size_pct` pourraient être oubliés).

---

## 11. INTÉGRATION TELEGRAM & NOTIFICATIONS

### 11.1 Déduplication des signaux

**Fichier :** `bot_telegram.py` (l.27-29, l.220-274)  
**Note : 8/10**

- Cooldown par symbole (15 min par défaut)
- Distinction entre trade exécuté (toujours notifié) et signal rejeté (cooldown appliqué)
- Persistance du cooldown en DB → survit aux redémarrages

### 11.2 Heartbeat

**Fichier :** `bot_telegram.py` (l.95-184, `build_status_message()`)  
**Note : 7/10**

- Message consolidé avec PnL temps réel depuis Binance
- Fallback sur calcul local si l'API Binance échoue
- Indicateur visuel TP1✓ et warning SL proche (⚠️ si <1%)

**🔶 PROBLÈME :** `build_status_message()` crée un nouvel exchange à chaque appel (l.112) puis le ferme. C'est correct mais coûteux. Pourrait être mutualisé avec l'exchange déjà ouvert dans la boucle principale.

### 11.3 Commandes Telegram

**Fichier :** `bot_listener.py`  
**Note : 7/10**

Commandes supportées : `/start`, `/status`, `/pnl`, `/db`, `/closeall`, `/confirm_closeall`
- `/closeall` en deux étapes (confirmation) → bonne protection
- Timeout de 60s sur la confirmation
- Validation `allowed_user_id` → sécurité basique

**🔶 PROBLÈME :** `bot_listener.py` utilise sa propre fonction `send_message()` (l.34-55) différente de `telegram_utils.send_telegram()`. Duplication de code pour l'envoi Telegram. Deux implémentations à maintenir.

---

## 12. SÉCURITÉ

### Matrice des vulnérabilités

| ID | Vulnérabilité | Sévérité | Impact | Statut |
|----|--------------|----------|--------|--------|
| SEC-001 | Secrets en clair dans config.yaml | 🔴 CRITIQUE | Compromission des fonds | À corriger immédiatement |
| SEC-002 | Dashboard sans authentification | 🟢 BASSE | Aucun (localhost only) | RAS sauf exposition réseau |
| SEC-003 | Pas de rate-limiting sur les commandes Telegram | 🟡 MOYENNE | DoS par spam | À surveiller |
| SEC-004 | Logging des données sensibles | 🟡 MOYENNE | Fuite dans les logs | Vérifier |
| SEC-005 | Pas de validation des paramètres de config | 🟡 MOYENNE | Comportement imprévisible | À améliorer |
| SEC-006 | `check_same_thread=False` sur SQLite | 🟡 MOYENNE | Corruption DB possible | À corriger |

---

## 13. BUGS & EDGE CASES

### 13.1 BUG-001 : Incohérence LONG/SHORT MTF vs Standard
**Fichier :** `module3_signal.py` l.259 vs l.314  
**Impact :** Décisions LONG/SHORT différentes selon le mode. **Code mort en production :** `generate_signal_mtf()` n'est jamais appelée dans le flux live (bloc MTF commenté dans `backtest_multi.py` l.97, l.157). Aucun impact tant que le mode MTF n'est pas activé.  
**Criticité :** � BASSE (inactif en l'état, à corriger avant toute activation MTF)

### 13.2 BUG-002 : WATCHLIST mismatch Monte Carlo
**Fichier :** `monte_carlo_validation.py` l.30-33 vs `config.yaml`  
**Impact :** Résultats Monte Carlo non représentatifs de la config réelle  
**Criticité :** 🟠 HAUTE  

### 13.3 BUG-003 : `_add_is_closed()` non déterministe
**Fichier :** `module1_data_v3.py` l.91-93  
**Impact :** Backtest flou sur les données récentes  
**Criticité :** 🟡 MOYENNE  

### 13.4 BUG-004 : `safety_margin_pct` et `max_position_size_pct` non utilisés
**Fichier :** `config.yaml` l.77-78, `risk_manager.py`  
**Impact :** Paramètres de config ignorés silencieusement  
**Criticité :** 🟢 BASSE  

### 13.5 BUG-005 : Trailing SL désynchronisé si `update_sl_order` échoue
**Fichier :** `trade_manager.py` l.295-300  
**Impact :** DB mise à jour mais pas l'exchange → SL incorrect. **Non applicable en config gagnante actuelle** (`trailing_sl_enabled: false`). Une alerte Telegram critique est déjà envoyée en cas d'échec (l.300-304), et le SL exchange conserve son ancienne valeur (protection conservative).  
**Criticité :** � MOYENNE (nécessite trailing activé + échec réseau simultané)

### 13.6 BUG-006 : `HYPE/USDT:USDT` dans backtest_multi.py
**Fichier :** `backtest_multi.py` l.121  
**Impact :** Backtest multi-scénarios ignore HYPE ou utilise un mauvais symbole  
**Criticité :** 🟡 MOYENNE  

---

## 14. MATRICE DES RISQUES

| Risque | Probabilité | Impact | Mitigation existante | Niveau résiduel |
|--------|------------|--------|---------------------|-----------------|
| Secret exposé sur GitHub | Haute | Critique | .gitignore | 🔴 CRITIQUE |
| Perte financière (stratégie non robuste) | Faible | Critique | Monte Carlo + daily loss limit | 🟡 MOYEN |
| Corruption DB (concurrence asyncio) | Faible | Élevé | Reconciliation au démarrage | 🟡 MOYEN |
| SL non mis à jour sur l'exchange | Faible | Élevé | Alertes Telegram | 🟠 ÉLEVÉ |
| Erreur silencieuse (create_task) | Moyenne | Moyen | Logs | 🟡 MOYEN |
| Panne exchange Binance | Faible | Élevé | Retry + cache | 🟢 BAS |
| Slippage excessif | Moyenne | Moyen | Non mitigé | 🟠 ÉLEVÉ |

---

## 15. RECOMMANDATIONS PRIORISÉES

### 🔴 URGENT (avant toute mise en production)

1. **SEC-001 : Révoquer et sécuriser les secrets**
   - Révoquer immédiatement les clés API Binance exposées
   - Régénérer le token Telegram
   - Vérifier l'historique Git : `git log --all -- config.yaml`
   - Migrer vers `python-dotenv` + `.env`
   - Ajouter `.env` au `.gitignore`

2. **BUG-002 : Uniformiser la WATCHLIST**
   - Aligner `monte_carlo_validation.py` et `config.yaml` sur la même liste
   - Corriger `HYPE/USDT:USDT` → `HYPE/USDT` dans `backtest_multi.py`

3. **BUG-005 : Rollback du SL en DB si `update_sl_order` échoue** (si trailing SL activé un jour)

### 🟠 HAUTE PRIORITÉ (avant paper trading)

5. **Ajouter la gestion des tâches asyncio orphelines**
   - Remplacer `create_task()` par un pattern avec `add_done_callback()` + log d'erreur

6. **BUG-003 : Rendre `_add_is_closed()` déterministe en backtest**
   - Injecter un timestamp `as_of` au lieu de `datetime.now()`

7. **Ajouter un warning Telegram si PnL latent < daily_loss_limit**
   - Le circuit breaker sur le PnL réalisé uniquement est un choix valable (pratique standard). Ajouter un warning informatif si le PnL latent tombe sous le seuil, sans bloquer le bot.

8. **Tests : Couvrir `generate_signal()`, `simulate_trade()`, `calculate_position_size()`**
   - Minimum 3 tests unitaires par fonction critique

9. **Ajouter validation de schéma pour config.yaml**
   - Utiliser `pydantic` BaseModel pour typer et valider la configuration

### 🟡 PRIORITÉ MOYENNE (avant production)

10. **Corriger `check_same_thread=False`** → utiliser `aiosqlite` ou un `asyncio.Lock`
11. **Ajouter authentification sur le dashboard FastAPI**
12. **Supprimer `safety_margin_pct`/`max_position_size_pct` ou les implémenter**
13. **Unifier `send_message()` et `send_telegram()`** → une seule fonction d'envoi
14. **Cache mémoire pour les données OHLCV** → réduire les lectures disque
15. **Ajouter une table `schema_version`** pour les migrations DB

### 🟢 BASSE PRIORITÉ (améliorations continues)

16. **Refactorer vers l'injection de dépendances** pour faciliter les tests
17. **Ajouter rate-limiting sur les commandes Telegram**
18. **Retirer `generate_signal_mtf()` si non utilisé** (ou le tester)
19. **Documenter les edge cases du zigzag** (queues longues, gaps)
20. **Ajouter métriques de slippage réel vs simulé**

---

## Annexe A — Fichiers audités

| Fichier | Lignes | Rôle |
|---------|--------|------|
| `main.py` | 140 | Point d'entrée CLI |
| `module1_data_v3.py` | 272 | Récupération données OHLCV + cache |
| `module2_AT.py` | 332 | Analyse technique (EMA, ATR, RSI, KC, ADX, zigzag) |
| `module3_signal.py` | 328 | Génération de signaux + scoring |
| `module4_backtest.py` | 289 | Simulation de trades |
| `backtest_multi.py` | 160 | Backtest multi-scénarios |
| `backtest_sl_comparison.py` | — | Comparatif SL/trailing |
| `backtest_winner.py` | — | Backtest config gagnante |
| `monte_carlo_validation.py` | 304 | Validation Monte Carlo (50K itérations) |
| `optimizer_mc.py` | — | Optimisation par grille Monte Carlo |
| `risk_manager.py` | 103 | Position sizing + filtres |
| `execution.py` | 317 | Passerelle ordres Binance Futures |
| `trade_manager.py` | 1202 | Gestion des positions + réconciliation |
| `database.py` | 306 | SQLite (positions, signaux, cooldowns) |
| `bot_telegram.py` | 347 | Orchestrateur principal |
| `bot_listener.py` | 227 | Listener commandes Telegram |
| `telegram_utils.py` | 26 | Envoi messages Telegram |
| `config_loader.py` | 33 | Chargement YAML + cache |
| `config.yaml` | 83 | Configuration live |
| `config.yaml.example` | 86 | Configuration exemple |
| `dashboard.py` | 208 | Dashboard console + métriques |
| `dashboard_api.py` | — | API FastAPI |
| `dashboard.html` | — | Frontend web |
| `metrics.py` | 51 | Calcul Sharpe, Sortino, Calmar, etc. |
| `check_connection.py` | — | Test connexion Binance |
| `requirements.txt` | 28 | Dépendances Python |
| `test_trailing_sl.py` | 169 | Tests trailing SL |
| `test_integration.py` | 208 | Tests d'intégration |
| `Contexte.md` | 134 | Documentation projet |
| `Rapport.txt` | 433 | Rapport Monte Carlo |

**Total : ~4 800 lignes de code auditées**

---

## Annexe B — Scorecard détaillée

| Critère | Score | Poids | Pondéré |
|---------|-------|-------|---------|
| Stratégie & Backtesting | 85 | 25% | 21.3 |
| Gestion des risques | 78 | 20% | 15.6 |
| Exécution & Trade mgmt | 80 | 20% | 16.0 |
| Qualité du code | 72 | 15% | 10.8 |
| Sécurité | 50 | 10% | 5.0 |
| Tests | 55 | 5% | 2.8 |
| Monitoring/Ops | 75 | 5% | 3.8 |
| **TOTAL** | | | **75.2/100** |

---

*Audit réalisé le 02/07/2026 — Version 1.0*  
*Prochaine revue recommandée : après implémentation des correctifs urgents + paper trading 2 semaines*