# MajeurCap_Bot — Rapport d'audit & points d'amélioration

**Date :** 11 juin 2026  
**Repo :** [github.com/issa14/MajeurCap_Bot](https://github.com/issa14/MajeurCap_Bot)  
**Statut :** Aucun bug bloquant — améliorations recommandées classées par priorité

---

## 🔴 Sécurité (priorité haute)

### 1. `update_position()` reçoit le dict complet avec des clés invalides
**Fichiers :** `database.py` · `trade_manager.py`

Le dict `pos` passé à `db.update_position()` contient tous les champs DB **plus** les alias normalisés (`entry`, `sl`, `tp1`, `tp2` ajoutés par `load_positions()`). Résultat : la colonne `id` se retrouve dans la clause `SET` :

```sql
UPDATE positions SET id=?, symbol=?, ..., entry_price=?, ..., entry_price=? WHERE id=?
```

- `id` est mis à jour (inutile et dangereux)
- `entry_price` est écrit deux fois (via `entry_price` ET via l'alias `entry` → `entry_price` du `column_map`)

SQLite tolère ça aujourd'hui, mais un changement de schéma ou de version peut provoquer un crash silencieux.

**Correction :** Filtrer le dict avant l'update — exclure `id` et les alias redondants.

```python
EXCLUDED_KEYS = {"id", "entry", "sl", "tp1", "tp2"}  # alias gérés par column_map
clean_updates = {k: v for k, v in updates.items() if k not in EXCLUDED_KEYS}
db.update_position(pos["id"], clean_updates)
```

---

### 2. Circuit breaker spamme Telegram à chaque tentative d'ouverture
**Fichier :** `trade_manager.py` · lignes 276–289

`check_circuit_breaker()` est appelé à chaque `open_position()`. Si le seuil de drawdown est atteint et que des signaux continuent d'être générés, l'alerte 🚨 `EMERGENCY STOP` est envoyée en boucle — le même problème que le spam de signaux corrigé aujourd'hui, mais pour l'emergency stop.

**Correction :** Ajouter un flag global ou le même mécanisme de cooldown que `_signal_sent_at`.

```python
_circuit_breaker_alerted = False

async def check_circuit_breaker(config: dict) -> bool:
    global _circuit_breaker_alerted
    ...
    if realized_pnl_pct <= daily_loss_limit:
        if not _circuit_breaker_alerted:
            await send_telegram(msg, config)
            _circuit_breaker_alerted = True
        return True
    _circuit_breaker_alerted = False  # reset si PnL remonte
    return False
```

---

## 🟠 Performance (priorité moyenne)

### 3. Une connexion exchange ouverte par position surveillée
**Fichier :** `trade_manager.py` · lignes 137–141

`manage_positions()` itère sur toutes les positions actives et appelle `check_position()` pour chacune. Chaque `check_position()` ouvre une connexion exchange indépendante :

```python
exchange = await init_exchange_async()   # ouverte
data = await fetch_all_async(exchange, symbols=[symbol], use_cache=True)
await exchange.close()                   # fermée
```

Avec 5 positions ouvertes = 5 connexions TCP + 5 fetches par cycle de 60s.

**Correction :** Ouvrir une seule connexion dans `manage_positions()` et la passer en paramètre.

```python
async def manage_positions():
    config = get_config()
    positions = load_positions()
    if not positions:
        return

    exchange = await init_exchange_async()
    try:
        for pos in positions:
            if pos.get("status") == "closed":
                continue
            await check_position(pos, config, exchange=exchange)
    finally:
        await exchange.close()
```

---

### 4. `config.yaml` relu à chaque cycle sans détection de changement
**Fichier :** `bot_telegram.py` · ligne 99

`reload_config()` est appelé à chaque itération de `run_scan_cycle()` — une lecture disque toutes les 60s. Utile pour le hot-reload, mais sans vérification du `mtime` du fichier.

**Correction (optionnelle) :** Comparer le `mtime` avant de relire.

```python
import os
_config_mtime = 0.0

def reload_config_if_changed() -> dict:
    global _config_mtime
    mtime = os.path.getmtime(CONFIG_PATH)
    if mtime != _config_mtime:
        _config_mtime = mtime
        return reload_config()
    return get_config()
```

---

## 🔵 Architecture (priorité normale)

### 5. `--live` et `--listen` ne peuvent pas tourner ensemble
**Fichier :** `main.py` · lignes 60–70

Les deux modes utilisent `asyncio.run()` séparés et sont mutuellement exclusifs. Pour avoir le bot de trading **et** les commandes Telegram (`/dashboard`) actifs simultanément, il faut deux terminaux ou deux services systemd distincts.

**Correction :** Ajouter un mode `--all` avec `asyncio.gather()`.

```python
group.add_argument("--all", action="store_true", help="Lancer le bot complet (live + listener)")

# Dans main() :
elif args.all:
    async def run_all():
        await asyncio.gather(run_live(), run_listener())
    asyncio.run(run_all())
```

---

### 6. `daily_filter_enabled` définie à deux endroits dans la config
**Fichiers :** `module2_AT.py` · `module4_backtest.py` · `backtest_multi.py` · `config_test.yaml`

Dans `config_test.yaml`, la clé est à la **racine** (`daily_filter_enabled: false`). Dans le code, certains modules lisent `config.get("signal", {}).get("daily_filter_enabled")`, d'autres font un double fallback :

```python
config.get("daily_filter_enabled", config.get("signal", {}).get("daily_filter_enabled", False))
```

Fragile : si on déplace la clé, certains modules ne liront plus la bonne valeur.

**Correction :** Standardiser sous `signal.daily_filter_enabled` dans la config et supprimer tous les fallbacks.

---

### 7. `compute_metrics()` dupliquée entre deux modules
**Fichiers :** `backtest_multi.py` · `module4_backtest.py`

Deux implémentations distinctes coexistent :
- `backtest_multi.py` : pas de Calmar ratio
- `module4_backtest.py` : avait un `NameError` sur `std_dev` (corrigé)

Toute amélioration future (ex: Sortino ratio) doit être faite deux fois.

**Correction :** Extraire `compute_metrics()` dans un module partagé `metrics.py` et l'importer dans les deux fichiers.

---

### 8. `_signal_sent_at` perdu au redémarrage du bot
**Fichier :** `bot_telegram.py` · lignes 22–25

Le cooldown anti-spam est stocké en mémoire vive. Si le bot redémarre (crash, déploiement), tous les cooldowns sont réinitialisés et les signaux peuvent respammer immédiatement après le redémarrage.

**Correction (optionnelle) :** Persister les timestamps dans SQLite.

```python
# Au démarrage : charger depuis DB
# À chaque mise à jour : écrire dans DB
# Table légère : symbol TEXT, last_sent TEXT
```

---

## ⚪ Qualité de code (priorité basse)

### 9. ~20 imports inutilisés
Signalés par `pyflakes`. Aucun impact runtime, mais bruit inutile.

| Fichier | Imports inutilisés |
|---|---|
| `bot_listener.py` | `Path` |
| `bot_telegram.py` | `Path`, `load_positions` |
| `module1_data_v3.py` | `yaml` |
| `module2_AT.py` | `sys` |
| `module3_signal.py` | `numpy as np` |
| `module4_backtest.py` | `Path`, `Optional`, `compute_zigzag`, 4 imports de module3 |
| `trade_manager.py` | `os` |
| `test_integration.py` | `asyncio`, `json`, `MagicMock`, 4 modules |
| `risk_manager.py` | — |

**Correction :** Une passe `autoflake --remove-all-unused-imports -i *.py`.

---

### 10. f-strings sans interpolation dans 3 fichiers

```python
# À remplacer par des strings ordinaires
f"config.yaml est requis."          # config_loader.py
f"\n--- Par symbole ---"            # module4_backtest.py
f"Exposition maximale dépassée…"    # risk_manager.py
```

---

### 11. Aucun test automatisé actif
Les fichiers `test_*.py` importent des modules inutilisés et sont partiellement vides. Pas de runner CI (pas de `.github/workflows/`).

**Correction (optionnelle) :** Ajouter un workflow GitHub Actions minimal.

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: pytest test_*.py -v
```

---

## Résumé priorisé

| # | Sujet | Priorité | Statut |
|---|---|---|---|
| 1 | `update_position` dict sale | 🔴 Haute | ✅ Fait |
| 2 | Circuit breaker spam | 🔴 Haute | ✅ Fait |
| 3 | Connexion exchange partagée | 🟠 Moyenne | ✅ Fait |
| 5 | Mode `--all` dans main.py | 🔵 Normale | ✅ Fait |
| 6 | `daily_filter_enabled` standardisé | 🔵 Normale | ✅ Fait |
| 7 | `compute_metrics` mutualisée | 🔵 Normale | ✅ Fait |
| 4 | Config hot-reload avec mtime | 🔵 Normale | ✅ Fait |
| 8 | Cooldown persisté en DB | ⚪ Basse | ✅ Fait |
| 9 | Imports inutilisés | ⚪ Basse | ✅ Fait |
| 10 | f-strings sans variables | ⚪ Basse | ✅ Fait |
| 11 | CI GitHub Actions | ⚪ Basse | ✅ Fait |
