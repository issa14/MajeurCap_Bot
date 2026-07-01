"""
Trade Manager v2.0 (avec Risk Manager + Exécution Futures Demo)
"""

import json
import logging
import asyncio
import sys
import pandas as pd
import aiohttp
import ccxt.async_support as ccxt_async
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async
from module2_AT import clean_ohlcv, compute_indicators
from risk_manager import (
    calculate_position_size, 
    can_open_position, 
    get_active_positions_count,
    get_current_exposure_pct
)
from execution import execute_signal, update_sl_order, init_trading_exchange
from config_loader import get_config
from database import db
from telegram_utils import send_telegram

# ─── Logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger("trade_manager")

# ─── State global ────────────────────────────────────────────────────────────
_circuit_breaker_alerted: bool = False

# ─── Configuration ───────────────────────────────────────────────────────────
POSITIONS_FILE = Path("positions.json")

# ─── Migration JSON -> SQLite ────────────────────────────────────────────────
def migrate_json_to_sqlite():
    if not POSITIONS_FILE.exists():
        return
    
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            positions = json.load(f)
        
        if not positions:
            return

        log.info(f"Migration de {len(positions)} positions depuis JSON...")
        active_in_db = [p["symbol"] for p in db.get_active_positions()]
        
        for pos in positions:
            if pos["symbol"] not in active_in_db:
                db_pos = {
                    "symbol": pos["symbol"],
                    "direction": pos["direction"],
                    "status": pos.get("status", "active"),
                    "entry": pos["entry"],
                    "entry_date": pos["entry_date"],
                    "quantity": pos["quantity"],
                    "sl": pos["sl"],
                    "tp1": pos["tp1"],
                    "tp2": pos["tp2"],
                    "partial_exit": 1 if pos.get("partial_exit") else 0,
                    "sl_order_id": pos.get("sl_order_id")
                }
                db.insert_position(db_pos)
        
        POSITIONS_FILE.rename(POSITIONS_FILE.with_suffix(".json.bak"))
        log.info("Migration terminée avec succès. positions.json renommé en .bak")
    except Exception as e:
        log.error(f"Erreur lors de la migration JSON -> SQLite : {e}")

# ─── Gestion des positions (SQLite) ──────────────────────────────────────────
def load_positions() -> list:
    """Charge les positions actives depuis SQLite."""
    if POSITIONS_FILE.exists():
        migrate_json_to_sqlite()
    positions = db.get_active_positions()
    for pos in positions:
        pos["entry"] = pos.get("entry_price") if "entry_price" in pos else pos.get("entry")
        pos["sl"] = pos.get("sl_price") if "sl_price" in pos else pos.get("sl")
        pos["tp1"] = pos.get("tp1_price") if "tp1_price" in pos else pos.get("tp1")
        pos["tp2"] = pos.get("tp2_price") if "tp2_price" in pos else pos.get("tp2")
    return positions

# ─── Annulation des ordres exchange lors d'une clôture ───────────────────────
async def cancel_exchange_orders(symbol: str, pos: dict) -> None:
    """
    Annule tous les ordres ouverts liés à une position sur l'exchange (SL, TP1, TP2).
    Appelé systématiquement à la clôture pour éviter les ordres orphelins.
    Chaque annulation est indépendante — une erreur n'en bloque pas une autre.
    """
    auto_exec = get_config().get("execution", {}).get("auto_execute", False)
    if not auto_exec:
        return

    order_ids = {
        "SL":  pos.get("sl_order_id"),
        "TP1": pos.get("tp1_order_id"),
        "TP2": pos.get("tp2_order_id"),
    }

    exchange = await init_trading_exchange()
    try:
        for label, order_id in order_ids.items():
            if not order_id:
                continue
            try:
                await exchange.cancel_order(order_id, symbol)
                log.info(f"{symbol} — ordre {label} ({order_id}) annulé sur l'exchange")
            except Exception as e:
                log.warning(f"{symbol} — impossible d'annuler ordre {label} ({order_id}): {e}")
    finally:
        await exchange.close()

# ─── Vérification d'une position (break‑even, TP/SL) ─────────────────────────
async def check_position(pos: dict, config: dict, exchange=None) -> Optional[dict]:
    symbol = pos["symbol"]
    entry = pos.get("entry") or pos.get("entry_price")
    direction = pos["direction"]
    sl = pos.get("sl") or pos.get("sl_price")
    tp1 = pos.get("tp1") or pos.get("tp1_price")
    tp2 = pos.get("tp2") or pos.get("tp2_price")
    partial_exit_done = pos.get("partial_exit", False)
    
    EXIT_PARTIAL_TP1 = config.get("risk", {}).get("partial_exit_tp1", True)
    
    pos["entry_price"] = pos["entry"] = entry
    pos["sl_price"] = pos["sl"] = sl
    pos["tp1_price"] = pos["tp1"] = tp1
    pos["tp2_price"] = pos["tp2"] = tp2

    risk_cfg = config.get("risk", {})
    trailing_enabled = risk_cfg.get("trailing_sl_enabled", False)
    activation_tp = risk_cfg.get("trailing_sl_activation_tp", 1)
    trailing_atr_mult = risk_cfg.get("trailing_sl_atr_mult", 2.0)

    local_exchange = None
    if exchange is None:
        local_exchange = await init_exchange_async()
        exch_to_use = local_exchange
    else:
        exch_to_use = exchange

    try:
        data = await fetch_all_async(exch_to_use, symbols=[symbol], use_cache=True)
    finally:
        if local_exchange:
            await local_exchange.close()

    if not data or symbol not in data:
        return pos

    df_clean = clean_ohlcv(data[symbol])
    if df_clean.empty:
        return pos

    df_enriched = compute_indicators(df_clean, config, include_incomplete=True)
    if df_enriched.empty:
        return pos

    entry_date = pd.Timestamp(pos["entry_date"])
    after_entry = df_enriched[df_enriched["timestamp"] > entry_date]
    if after_entry.empty:
        return pos

    new_sl = sl
    exit_reason = None
    exit_price = None

    for idx, row in after_entry.iterrows():
        high = row["high"]
        low = row["low"]
        close = row["close"]
        atr = row.get("atr", 0)

        if direction == "LONG":
            if not partial_exit_done and high >= tp1:
                partial_exit_done = True
                if EXIT_PARTIAL_TP1:
                    new_sl = entry
                    half_qty = round((pos.get("current_quantity") or pos["quantity"]) * 0.5, 8)
                    asyncio.create_task(send_telegram(f"🟢 {symbol} TP1 atteint ! SL déplacé au break‑even.", config))
                    pos["sl_price"] = pos["sl"] = new_sl
                    pos["partial_exit"] = 1
                    pos["current_quantity"] = half_qty
                    pos["tp1_status"] = "FILLED"
                    clean_updates = {k: v for k, v in pos.items() if k not in {"id", "entry", "sl", "tp1", "tp2"}}
                    db.update_position(pos["id"], clean_updates)
                else:
                    exit_reason = "TP1"
                    exit_price = tp1
                    break
            
            if trailing_enabled and ((activation_tp == 0) or (activation_tp == 1 and partial_exit_done)):
                atr_sl = round(close - (atr * trailing_atr_mult), 8)
                if atr_sl > new_sl:
                    new_sl = atr_sl

            if high >= tp2:
                exit_reason = "TP2"
                exit_price = tp2
                break
            if low <= new_sl:
                exit_reason = "SL"
                exit_price = new_sl
                break
        else:  # SHORT
            if not partial_exit_done and low <= tp1:
                partial_exit_done = True
                if EXIT_PARTIAL_TP1:
                    new_sl = entry
                    half_qty = round((pos.get("current_quantity") or pos["quantity"]) * 0.5, 8)
                    asyncio.create_task(send_telegram(f"🔴 {symbol} TP1 atteint ! SL déplacé au break‑even.", config))
                    pos["sl_price"] = pos["sl"] = new_sl
                    pos["partial_exit"] = 1
                    pos["current_quantity"] = half_qty
                    pos["tp1_status"] = "FILLED"
                    clean_updates = {k: v for k, v in pos.items() if k not in {"id", "entry", "sl", "tp1", "tp2"}}
                    db.update_position(pos["id"], clean_updates)
                else:
                    exit_reason = "TP1"
                    exit_price = tp1
                    break

            if trailing_enabled and ((activation_tp == 0) or (activation_tp == 1 and partial_exit_done)):
                atr_sl = round(close + (atr * trailing_atr_mult), 8)
                if atr_sl < new_sl:
                    new_sl = atr_sl

            if low <= tp2:
                exit_reason = "TP2"
                exit_price = tp2
                break
            if high >= new_sl:
                exit_reason = "SL"
                exit_price = new_sl
                break

    if not exit_reason and new_sl != pos["sl"]:
        auto_exec = config.get("execution", {}).get("auto_execute", False)
        if auto_exec:
            # Quantité réelle restante sur l'exchange : si TP1 a déjà été touché (sortie
            # partielle de 50%), il ne reste que 50% de la quantité d'origine en position.
            remaining_qty = (pos.get("current_quantity") or pos["quantity"] * 0.5) if partial_exit_done else pos["quantity"]
            res = await update_sl_order(
                symbol=symbol,
                quantity=remaining_qty,
                new_sl_price=new_sl,
                direction=direction,
                old_sl_order_id=pos.get("sl_order_id"),
                atr=atr
            )
            if res["success"]:
                pos["sl_order_id"] = res["sl_order"]["id"]
                asyncio.create_task(send_telegram(f"🔄 {symbol} Trailing SL mis à jour : {new_sl}", config))
            else:
                log.error(f"Échec mise à jour SL sur exchange pour {symbol}")
                asyncio.create_task(send_telegram(
                    f"🚨 URGENT {symbol} — Échec mise à jour Trailing SL sur l'exchange ! "
                    f"La position peut être SANS stop-loss actif. Vérifier manuellement sur Binance.",
                    config
                ))

    pos["sl_price"] = pos["sl"] = new_sl
    pos["partial_exit"] = 1 if partial_exit_done else 0

    if exit_reason:
        # Annuler tous les ordres restants sur l'exchange (évite les ordres orphelins)
        asyncio.create_task(cancel_exchange_orders(symbol, pos))

        pos["status"] = "closed"
        pos["exit_reason"] = exit_reason
        if exit_reason == "TP2":
            pos["tp2_status"] = "FILLED"
        pos["exit_price"] = exit_price
        pos["exit_date"] = str(after_entry.iloc[-1]["timestamp"])
        
        # PnL pondéré : si une sortie partielle a eu lieu à TP1 (50% qty), on calcule
        # le PnL réel = 50% du PnL au prix TP1 + 50% du PnL au prix de sortie final.
        # Sinon (SL ou TP1 direct sans partial_exit), 100% au prix de sortie unique.
        full_qty = pos["quantity"]
        if pos.get("partial_exit") and exit_reason != "TP1":
            # current_quantity = remaining volume after TP1 (already updated in DB)
            remaining_qty = pos.get("current_quantity") or full_qty * 0.5
            tp1_qty = full_qty - remaining_qty
            if direction == "LONG":
                pnl_usd_tp1  = (tp1 - entry) * tp1_qty
                pnl_usd_rest = (exit_price - entry) * remaining_qty
            else:
                pnl_usd_tp1  = (entry - tp1) * tp1_qty
                pnl_usd_rest = (entry - exit_price) * remaining_qty
            pnl_usd = pnl_usd_tp1 + pnl_usd_rest
            pnl_pct = (pnl_usd / (entry * full_qty)) * 100 if entry * full_qty != 0 else 0
        else:
            pnl_usd = (exit_price - entry) * full_qty if direction == "LONG" else (entry - exit_price) * full_qty
            pnl_pct = ((exit_price - entry) / entry * 100) if direction == "LONG" else ((entry - exit_price) / entry * 100)
        
        pos["pnl_usd"] = round(pnl_usd, 4)
        pos["pnl_pct"] = round(pnl_pct, 2)
        
        emoji = "✅" if pnl_pct > 0 else "❌"
        asyncio.create_task(send_telegram(f"{emoji} {symbol} {direction} clôturé ({exit_reason})\nPrix sortie : {exit_price}\nPnL : {pnl_pct:+.2f}%", config))
    elif partial_exit_done and EXIT_PARTIAL_TP1:
        pos["status"] = "tp1_hit"

    clean_updates = {k: v for k, v in pos.items() if k not in {"id", "entry", "sl", "tp1", "tp2"}}
    db.update_position(pos["id"], clean_updates)
    return pos

def _normalize_symbol(symbol: str) -> str:
    """Normalise un symbole CCXT futures vers le format DB.
    Exemples : 'SUI/USDT:USDT' → 'SUI/USDT', 'BTC/USDT:USDT' → 'BTC/USDT'
    """
    return symbol.split(":")[0]


async def reconcile_positions_on_startup() -> None:
    """
    Compare les positions actives en DB avec les positions réelles sur Binance.
    Appelée UNE SEULE FOIS au démarrage du bot.

    Cas traités :
    - Position active en DB mais absente sur Binance → marquer closed (SL/TP déclenché hors surveillance)
    - Position sur Binance mais absente en DB → alerter Telegram (sans insertion en DB)

    Note : les symboles sont normalisés via _normalize_symbol() pour éviter les faux positifs
    entre le format DB ('SUI/USDT') et le format CCXT futures ('SUI/USDT:USDT').
    """
    config = get_config()
    auto_exec = config.get("execution", {}).get("auto_execute", False)
    if not auto_exec:
        log.info("reconcile_on_startup ignoré (auto_execute=False)")
        return

    log.info("=== Réconciliation démarrage : DB vs Binance ===")

    # 1. Positions actives en DB (clé = symbol normalisé)
    db_positions = {p["symbol"]: p for p in db.get_active_positions()}
    db_symbols_normalized = {_normalize_symbol(s): s for s in db_positions}

    # 2. Positions réelles sur Binance (clé = symbol normalisé)
    exchange = await init_trading_exchange()
    try:
        raw_positions = await exchange.fetch_positions()
        binance_by_normalized = {}
        for p in raw_positions:
            if p.get("contracts") and float(p["contracts"]) != 0:
                norm = _normalize_symbol(p["symbol"])
                binance_by_normalized[norm] = p
    except Exception as e:
        log.error(f"reconcile_on_startup : impossible de récupérer les positions Binance ({e})")
        # await exchange.close()  # Redundant; handled in finally
        return
    finally:
        await exchange.close()

    # 3. Cas A — Position active en DB mais absente sur Binance
    for norm_sym, db_sym in db_symbols_normalized.items():
        if norm_sym not in binance_by_normalized:
            pos = db_positions[db_sym]
            log.warning(
                f"RECONCILE {db_sym} — active en DB (id={pos['id']}) mais ABSENTE sur Binance. "
                f"Marquée closed (SL/TP probablement déclenché hors surveillance)."
            )
            db.update_position(pos["id"], {
                "status": "closed",
                "exit_reason": "RECONCILE_MISSING_ON_EXCHANGE",
                "exit_date": datetime.now(timezone.utc).isoformat(),
            })
            await send_telegram(
                f"⚠️ RECONCILE {db_sym} — Position active en DB mais introuvable sur Binance.\n"
                f"Marquée closed automatiquement (SL/TP déclenché hors surveillance du bot).\n"
                f"Entry: {pos.get('entry_price')} | Direction: {pos.get('direction')}",
                config
            )

    # 4. Cas B — Position sur Binance mais absente en DB → alerte SANS insertion
    # (pas d'insertion automatique pour éviter les doublons et les réouvertures en cascade)
    for norm_sym, bpos in binance_by_normalized.items():
        if norm_sym not in db_symbols_normalized:
            side = bpos.get("side", "")
            direction = "LONG" if side == "long" else "SHORT"
            entry_price = bpos.get("entryPrice") or bpos.get("info", {}).get("entryPrice", 0)
            contracts = float(bpos.get("contracts", 0))
            raw_symbol = bpos.get("symbol", norm_sym)
            log.warning(
                f"RECONCILE {norm_sym} — position ORPHELINE sur Binance "
                f"({direction} qty={contracts} entry={entry_price}) absente de la DB. "
                f"Alerte envoyée — fermeture manuelle requise."
            )
            await send_telegram(
                f"🚨 RECONCILE {norm_sym} — Position ORPHELINE détectée sur Binance !\n"
                f"Direction: {direction} | Qty: {contracts} | Entry: {entry_price}\n"
                f"⚠️ Non insérée en DB — fermer manuellement sur Binance.",
                config
            )

    log.info("=== Réconciliation terminée ===")

async def sync_all(config: dict, exchange=None) -> None:
    """
    Réconciliation unifiée DB ↔ Binance (positions + ordres SL/TP).

    Avec seulement 2 appels API (fetch_positions + fetch_open_orders),
    cette fonction :
    1. Compare les quantités DB vs Binance → met à jour la DB
    2. Détecte les positions disparues de Binance → marque closed
    3. Détecte les positions orphelines sur Binance (pas en DB) → alerte
    4. Vérifie chaque ordre SL/TP existant, recrée ceux qui manquent
    5. Détecte les ordres SL/TP FILLED sur Binance → marque la position closed
    """
    auto_exec = config.get("execution", {}).get("auto_execute", False)
    if not auto_exec:
        return

    db_positions = {p["symbol"]: p for p in db.get_active_positions() if p.get("status") != "closed"}
    if not db_positions:
        return

    own_exchange = False
    if exchange is None:
        own_exchange = True
        exchange = await init_trading_exchange()

    try:
        await exchange.load_markets()

        # ── 1. fetch_positions() : comparer les quantités ───────────────────
        try:
            raw_positions = await exchange.fetch_positions()
        except Exception as e:
            log.error(f"sync_all: fetch_positions échoué ({e})")
            return

        binance_by_norm = {}
        for p in raw_positions:
            if p.get("contracts") and float(p["contracts"]) != 0:
                norm = _normalize_symbol(p["symbol"])
                binance_by_norm[norm] = p

        # ── fetch_open_orders() : tous les ordres ouverts en un seul appel ──
        try:
            all_open_orders = await exchange.fetch_open_orders()
        except Exception as e:
            log.warning(f"sync_all: fetch_open_orders échoué ({e}), skip vérification ordres")
            all_open_orders = []

        # Indexer les ordres par ID pour lookup rapide
        open_by_id = {o["id"]: o for o in all_open_orders}

        # ── 2. Parcourir chaque position DB ─────────────────────────────────
        for db_sym, pos in db_positions.items():
            norm_sym = _normalize_symbol(db_sym)
            pos_id = pos["id"]
            direction = pos["direction"]
            sl_price  = pos.get("sl_price") or pos.get("sl") or 0
            tp1_price = pos.get("tp1_price") or pos.get("tp1") or 0
            tp2_price = pos.get("tp2_price") or pos.get("tp2") or 0
            db_qty    = pos.get("current_quantity") or pos.get("quantity") or 0

            # ── Cas A : position DB absente de Binance → closed ─────────
            if norm_sym not in binance_by_norm:
                log.warning(f"sync_all {db_sym} — absente de Binance, marquée closed")
                db.update_position(pos_id, {
                    "status": "closed",
                    "exit_reason": "RECONCILE_MISSING_ON_EXCHANGE",
                    "exit_date": datetime.now(timezone.utc).isoformat(),
                })
                asyncio.create_task(send_telegram(
                    f"⚠️ {db_sym} — Position fermée (disparue de Binance, SL/TP probablement déclenché hors surveillance).",
                    config
                ))
                continue

            bpos = binance_by_norm[norm_sym]

            # ── Cas B : comparaison quantité ────────────────────────────
            exchange_qty = float(bpos.get("contracts", 0))
            if db_qty > 0 and abs(exchange_qty - db_qty) > (0.01 * db_qty):
                db.update_position(pos_id, {"current_quantity": exchange_qty})
                log.info(f"sync_all {db_sym} — quantité corrigée: {db_qty} → {exchange_qty}")

                # Si la quantité est tombée à ~0, la position est fermée
                if exchange_qty < (db_qty * 0.05):
                    db.update_position(pos_id, {
                        "status": "closed",
                        "exit_reason": "RECONCILE_FULLY_CLOSED",
                        "exit_date": datetime.now(timezone.utc).isoformat(),
                    })
                    asyncio.create_task(send_telegram(
                        f"⚠️ {db_sym} — Position fermée (quantité résiduelle proche de zéro sur Binance).",
                        config
                    ))
                    continue

            # ── Cas C : vérifier / réparer les ordres SL, TP1, TP2 ─────
            order_specs = []

            # Toujours vérifier le SL
            order_specs.append({
                "label": "SL",
                "db_order_id": pos.get("sl_order_id"),
                "price": sl_price,
                "qty": exchange_qty,  # quantité réelle restante
                "ord_type": "stop_market",
            })

            # TP1 seulement si pas encore hit
            tp1_hit = pos.get("tp1_status") == "FILLED"
            if not tp1_hit and pos.get("tp1_order_id"):
                order_specs.append({
                    "label": "TP1",
                    "db_order_id": pos.get("tp1_order_id"),
                    "price": tp1_price,
                    "qty": exchange_qty * 0.5,
                    "ord_type": "take_profit_market",
                })

            # TP2 toujours vérifié (il peut avoir été annulé après TP1)
            if pos.get("tp2_order_id"):
                # Si TP1 a été hit, la qty restante est déjà réduite
                remaining_for_tp2 = exchange_qty  # après TP1, c'est la qty réelle
                order_specs.append({
                    "label": "TP2",
                    "db_order_id": pos.get("tp2_order_id"),
                    "price": tp2_price,
                    "qty": remaining_for_tp2,
                    "ord_type": "take_profit_market",
                })

            sl_side = "sell" if direction == "LONG" else "buy"

            for spec in order_specs:
                if spec["price"] <= 0 or spec["qty"] <= 0:
                    continue

                order_id = spec["db_order_id"]
                label = spec["label"]
                ord_exists = False
                ord_filled = False

                # Chercher l'ordre dans les open_orders par ID
                if order_id and order_id in open_by_id:
                    o = open_by_id[order_id]
                    status = o.get("status", "")
                    if status == "closed":
                        # L'ordre est filled → la position doit être marquée closed
                        ord_filled = True
                        log.warning(f"sync_all {db_sym} — {label} {order_id} est FILLED sur Binance !")
                    else:
                        ord_exists = True
                elif order_id:
                    # L'ordre n'est pas dans open_orders → peut être filled ou annulé
                    try:
                        o = await exchange.fetch_order(order_id, db_sym)
                        status = o.get("status", "")
                        if status == "closed":
                            ord_filled = True
                            log.warning(f"sync_all {db_sym} — {label} {order_id} est FILLED (fetch_order) !")
                        elif status in ("canceled", "expired"):
                            log.warning(f"sync_all {db_sym} — {label} {order_id} status={status}, recréation")
                        else:
                            ord_exists = True
                    except Exception:
                        log.warning(f"sync_all {db_sym} — {label} {order_id} introuvable, recréation")

                # ── Si l'ordre est FILLED, marquer la position closed ────
                if ord_filled:
                    # Déterminer le prix de sortie
                    exit_price = spec["price"]
                    exit_reason = label  # "SL", "TP1", ou "TP2"

                    # Annuler les autres ordres restants
                    asyncio.create_task(cancel_exchange_orders(db_sym, pos))

                    # Calculer le PnL
                    entry = pos.get("entry_price") or pos.get("entry") or 0
                    full_qty = pos.get("quantity") or 0
                    if direction == "LONG":
                        pnl_usd = (exit_price - entry) * full_qty
                        pnl_pct = ((exit_price - entry) / entry * 100) if entry != 0 else 0
                    else:
                        pnl_usd = (entry - exit_price) * full_qty
                        pnl_pct = ((entry - exit_price) / entry * 100) if entry != 0 else 0

                    db.update_position(pos_id, {
                        "status": "closed",
                        "exit_reason": f"RECONCILE_{exit_reason}_FILLED",
                        "exit_price": exit_price,
                        "exit_date": datetime.now(timezone.utc).isoformat(),
                        "pnl_usd": round(pnl_usd, 4),
                        "pnl_pct": round(pnl_pct, 2),
                    })
                    log.info(f"sync_all {db_sym} — Position fermée ({label} FILLED détecté sur Binance)")

                    emoji = "✅" if pnl_pct > 0 else "❌"
                    asyncio.create_task(send_telegram(
                        f"{emoji} {db_sym} {direction} clôturé DÉTECTÉ SUR BINANCE ({label})\n"
                        f"Prix sortie : {exit_price}\nPnL : {pnl_pct:+.2f}%",
                        config
                    ))
                    break  # Ne pas vérifier les autres ordres, la position est fermée

                # ── Si l'ordre n'existe pas, le recréer ─────────────────
                if not ord_exists:
                    try:
                        qty_precise = float(exchange.amount_to_precision(db_sym, spec["qty"]))
                        new_order = await exchange.create_order(
                            symbol=db_sym,
                            type=spec["ord_type"],
                            side=sl_side,
                            amount=qty_precise,
                            price=None,
                            params={"stopPrice": spec["price"], "reduceOnly": True},
                        )
                        col = f"{label.lower()}_order_id"
                        db.update_position(pos_id, {col: new_order["id"]})
                        log.info(f"sync_all {db_sym} — {label} recréé: {new_order['id']}")
                        asyncio.create_task(send_telegram(
                            f"🔧 {db_sym} — {label} recréé automatiquement sur Binance.",
                            config
                        ))
                    except Exception as e:
                        log.error(f"sync_all {db_sym} — échec recréation {label}: {e}")
                        if label == "SL":
                            asyncio.create_task(send_telegram(
                                f"🚨 URGENT {db_sym} — ÉCHEC recréation SL ! Position SANS stop-loss actif.",
                                config
                            ))

        # ── 3. Cas D : positions orphelines sur Binance (pas en DB) ─────────
        db_norm_set = {_normalize_symbol(s) for s in db_positions}
        for norm_sym, bpos in binance_by_norm.items():
            if norm_sym not in db_norm_set:
                side = bpos.get("side", "")
                direction = "LONG" if side == "long" else "SHORT"
                entry_price = bpos.get("entryPrice") or bpos.get("info", {}).get("entryPrice", 0)
                contracts = float(bpos.get("contracts", 0))
                log.warning(f"sync_all {norm_sym} — position ORPHELINE sur Binance ({direction} qty={contracts})")
                asyncio.create_task(send_telegram(
                    f"🚨 {norm_sym} — Position ORPHELINE sur Binance !\n"
                    f"Direction: {direction} | Qty: {contracts} | Entry: {entry_price}\n"
                    f"⚠️ Non insérée en DB — fermer manuellement.",
                    config
                ))

    finally:
        if own_exchange:
            await exchange.close()


async def manage_positions():
    config = get_config()
    positions = load_positions()
    if not positions:
        log.info("Aucune position ouverte.")
        return

    auto_exec = config.get("execution", {}).get("auto_execute", False)

    exchange = await init_exchange_async()
    try:
        for pos in positions:
            if pos.get("status") == "closed":
                continue
            await check_position(pos, config, exchange=exchange)

        # Sync unifiée DB ↔ Binance (remplace sync_position_with_exchange + verify_active_orders)
        if auto_exec:
            try:
                await sync_all(config, exchange=exchange)
            except Exception as e:
                log.error(f"sync_all — erreur inattendue : {e}", exc_info=True)
    finally:
        await exchange.close()

    log.info(f"Positions mises à jour : {len(db.get_active_positions())} ouvertes")

# ─── Ouverture de position (avec sizing et exécution automatique) ────────────
async def check_circuit_breaker(config: dict) -> bool:
    """Retourne True si le bot est bloqué (emergency stop)."""
    global _circuit_breaker_alerted
    risk_cfg = config.get("risk", {})
    daily_loss_limit = risk_cfg.get("daily_loss_limit", -5.0)

    realized_pnl_pct = db.get_realized_pnl_today()

    if realized_pnl_pct <= daily_loss_limit:
        msg = f"🚨 <b>EMERGENCY STOP</b> - Drawdown journalier atteint : {realized_pnl_pct:.2f}% (Seuil: {daily_loss_limit}%)"
        if not _circuit_breaker_alerted:
            await send_telegram(msg, config)
            log.critical(msg)
            _circuit_breaker_alerted = True
        return True

    _circuit_breaker_alerted = False
    return False

async def open_position(signal: dict, config: dict) -> dict:
    positions = load_positions()
    symbol = signal["symbol"]

    if await check_circuit_breaker(config):
        return {"success": False, "reason": "Circuit breaker déclenché"}

    for p in positions:
        if p["symbol"] == symbol and p.get("status") != "closed":
            log.warning(f"Position déjà ouverte sur {symbol}")
            return {"success": False, "reason": "already_open"}

    if not can_open_position(positions, config):
        max_pos = config.get("risk", {}).get("max_positions", 5)
        return {
            "success": False,
            "reason": "Nombre max de positions",
            "current": get_active_positions_count(positions),
            "limit": max_pos
        }

    risk_cfg = config.get("risk", {})
    
    # Capital live depuis l'exchange
    live_capital = risk_cfg.get("capital", 1000)
    try:
        exch_tmp = await init_trading_exchange()
        try:
            bal = await exch_tmp.fetch_balance()
            live_capital = bal["free"].get("USDT", live_capital)
            log.info(f"Capital live récupéré : {live_capital:.2f} USDT")
        finally:
            await exch_tmp.close()
    except Exception as e:
        log.warning(f"Impossible de récupérer le solde live, fallback config ({e})")

    max_exposure_pct = risk_cfg.get("max_exposure", 30.0)
    current_exp = get_current_exposure_pct(positions, live_capital)
    if current_exp >= max_exposure_pct:
        return {
            "success": False,
            "reason": "Exposition maximale atteinte",
            "current": f"{current_exp:.1f}%",
            "limit": f"{max_exposure_pct:.1f}%"
        }

    quantity = calculate_position_size(signal, config, positions, capital_override=live_capital)
    if quantity <= 0:
        return {"success": False, "reason": "Taille de position nulle"}

    auto_exec = config.get("execution", {}).get("auto_execute", False)
    sl_order_id  = None
    tp1_order_id = None
    tp2_order_id = None
    if auto_exec:
        result = await execute_signal(signal, quantity)
        if not result["success"]:
            if "entry_order" in result:
                asyncio.create_task(send_telegram(f"⚠️ {symbol} — Stop-loss non placé ! Entrée exécutée, à surveiller manuellement.", config))
            else:
                return {"success": False, "reason": "Échec exécution API"}
        else:
            sl_order_id  = result.get("sl_order",  {}).get("id")
            tp1_order_id = result.get("tp1_order", {}).get("id") if result.get("tp1_order") else None
            tp2_order_id = result.get("tp2_order", {}).get("id") if result.get("tp2_order") else None

    new_pos = {
        "symbol": symbol,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "quantity": quantity,
        "current_quantity": quantity,
        "entry_date": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "partial_exit": 0,
        "sl_order_id":  sl_order_id,
        "tp1_order_id": tp1_order_id,
        "tp2_order_id": tp2_order_id,
    }
    
    # Guard — évite le UNIQUE constraint error si deux cycles tentent d'ouvrir le même symbole
    existing = [p for p in db.get_active_positions() if p.get("symbol") == symbol and p.get("status") == "active"]
    if existing:
        log.warning(f"{symbol} — position déjà active en DB, insertion annulée (doublon évité)")
        return {"success": False, "reason": "position_already_active"}

    try:
        db.insert_position(new_pos)
    except Exception as db_error:
        # Les ordres sont DÉJÀ actifs sur l'exchange à ce stade (si auto_exec=True) — la position
        # devient orpheline pour le bot si on ne signale pas immédiatement ce cas. Alerte Telegram
        # avec toutes les infos nécessaires à une réparation manuelle en DB.
        log.error(f"{symbol} — ÉCHEC insertion DB après ouverture position : {db_error}")
        asyncio.create_task(send_telegram(
            f"🚨 URGENT {symbol} — Position ouverte sur l'exchange mais ÉCHEC d'enregistrement en DB !\n"
            f"Direction: {signal['direction']} | Qty: {quantity} | Entry: {signal['entry']}\n"
            f"SL: {signal['sl']} | TP1: {signal['tp1']} | TP2: {signal['tp2']}\n"
            f"SL order: {sl_order_id} | TP1 order: {tp1_order_id} | TP2 order: {tp2_order_id}\n"
            f"Cette position n'est PAS supervisée par le bot (pas de trailing, pas de détection "
            f"de clôture). Réparation manuelle en DB nécessaire.",
            config
        ))
        return {"success": False, "reason": "db_insert_failed_position_orphaned", "error": str(db_error)}

    log.info(f"Nouvelle position ouverte : {symbol} {signal['direction']} qty={quantity}")
    return {"success": True, "quantity": quantity}


if __name__ == "__main__":
    asyncio.run(manage_positions())
