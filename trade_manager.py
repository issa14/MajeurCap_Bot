"""
Trade Manager v2.0 (avec Risk Manager + Exécution Testnet)
"""

import json
import logging
import asyncio
import sys
import pandas as pd
import aiohttp
import os
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
from execution import execute_signal, update_sl_order
from config_loader import get_config

# ─── Logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger("trade_manager")

# ─── Configuration ───────────────────────────────────────────────────────────
POSITIONS_FILE = Path("positions.json")
EXIT_PARTIAL_TP1 = True

# ─── Telegram Asynchrone ──────────────────────────────────────────────────────
async def send_telegram(text: str, config: dict):
    tg_cfg = config.get("telegram", {})
    token = tg_cfg.get("token", "")
    chat_id = tg_cfg.get("chat_id", "")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    log.error(f"Erreur Telegram : {await resp.text()}")
    except Exception as e:
        log.error(f"Échec envoi Telegram : {e}")

# ─── Gestion des positions (Écriture Atomique) ───────────────────────────────
def load_positions() -> list:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"Erreur lecture positions.json : {e}")
        return []

def save_positions(positions: list):
    """Sauvegarde atomique pour éviter la corruption de fichier."""
    temp_file = POSITIONS_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(positions, f, indent=2, default=str)
        os.replace(temp_file, POSITIONS_FILE) # Opération atomique
    except Exception as e:
        log.error(f"Échec sauvegarde positions : {e}")
        if temp_file.exists():
            temp_file.unlink()

# ─── Vérification d'une position (break‑even, TP/SL) ─────────────────────────
async def check_position(pos: dict, config: dict) -> Optional[dict]:
    symbol = pos["symbol"]
    entry = pos["entry"]
    direction = pos["direction"]
    sl = pos["sl"]
    tp1 = pos["tp1"]
    tp2 = pos["tp2"]
    partial_exit_done = pos.get("partial_exit", False)

    # Config Trailing SL
    risk_cfg = config.get("risk", {})
    trailing_enabled = risk_cfg.get("trailing_sl_enabled", False)
    activation_tp = risk_cfg.get("trailing_sl_activation_tp", 1)
    trailing_atr_mult = risk_cfg.get("trailing_sl_atr_mult", 2.0)

    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, symbols=[symbol], use_cache=True)
    finally:
        await exchange.close()

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
                    asyncio.create_task(send_telegram(f"🟢 {symbol} TP1 atteint ! SL déplacé au break‑even.", config))
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
                    asyncio.create_task(send_telegram(f"🔴 {symbol} TP1 atteint ! SL déplacé au break‑even.", config))
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
            res = await update_sl_order(
                symbol=symbol,
                quantity=pos["quantity"],
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

    pos["sl"] = new_sl
    pos["partial_exit"] = partial_exit_done

    if exit_reason:
        pos["status"] = "closed"
        pos["exit_reason"] = exit_reason
        pos["exit_price"] = exit_price
        pos["exit_date"] = str(after_entry.iloc[-1]["timestamp"])
        pnl_pct = ((exit_price - entry) / entry * 100) if direction == "LONG" else ((entry - exit_price) / entry * 100)
        pos["pnl_pct"] = round(pnl_pct, 2)
        emoji = "✅" if pnl_pct > 0 else "❌"
        asyncio.create_task(send_telegram(f"{emoji} {symbol} {direction} clôturé ({exit_reason})\nPrix sortie : {exit_price}\nPnL : {pnl_pct:+.2f}%", config))
    elif partial_exit_done and EXIT_PARTIAL_TP1:
        pos["status"] = "tp1_hit"

    return pos

async def manage_positions():
    config = get_config()
    positions = load_positions()
    if not positions:
        log.info("Aucune position ouverte.")
        return

    updated = []
    for pos in positions:
        if pos.get("status") == "closed":
            continue
        updated_pos = await check_position(pos, config)
        if updated_pos is not None and updated_pos.get("status") != "closed":
            updated.append(updated_pos)

    save_positions(updated)
    log.info(f"Positions mises à jour : {len(updated)} ouvertes")

# ─── Ouverture de position (avec sizing et exécution automatique) ────────────
async def open_position(signal: dict, config: dict) -> dict:
    positions = load_positions()
    symbol = signal["symbol"]

    # 1. Vérifier doublon
    for p in positions:
        if p["symbol"] == symbol and p.get("status") != "closed":
            log.warning(f"Position déjà ouverte sur {symbol}")
            return {"success": False, "reason": "already_open"}

    # 2. Vérifier nombre de positions
    if not can_open_position(positions, config):
        max_pos = config.get("risk", {}).get("max_positions", 5)
        return {
            "success": False, 
            "reason": "Nombre max de positions", 
            "current": get_active_positions_count(positions), 
            "limit": max_pos
        }

    # 3. Vérifier l'exposition
    risk_cfg = config.get("risk", {})
    capital = risk_cfg.get("capital", 1000)
    max_exposure_pct = risk_cfg.get("max_exposure", 30.0)
    current_exp = get_current_exposure_pct(positions, capital)
    if current_exp >= max_exposure_pct:
        return {
            "success": False, 
            "reason": "Exposition maximale atteinte", 
            "current": f"{current_exp:.1f}%", 
            "limit": f"{max_exposure_pct:.1f}%"
        }

    quantity = calculate_position_size(signal, config, positions)
    if quantity <= 0:
        return {"success": False, "reason": "Taille de position nulle"}

    # Exécution automatique si activée
    auto_exec = config.get("execution", {}).get("auto_execute", False)
    sl_order_id = None
    if auto_exec:
        result = await execute_signal(signal, quantity)
        if not result["success"]:
            # Si l'ordre d'entrée a réussi mais pas le stop, on alerte et on continue
            if "entry_order" in result:
                asyncio.create_task(send_telegram(f"⚠️ {symbol} — Stop-loss non placé ! Entrée exécutée, à surveiller manuellement.", config))
            else:
                return {"success": False, "reason": "Échec exécution API"}
        else:
            sl_order_id = result.get("sl_order", {}).get("id")

    new_pos = {
        "symbol": symbol,
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "quantity": quantity,
        "entry_date": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "partial_exit": False,
        "sl_order_id": sl_order_id,
    }
    positions.append(new_pos)
    save_positions(positions)

    log.info(f"Nouvelle position ouverte : {symbol} {signal['direction']} qty={quantity}")
    return {"success": True, "quantity": quantity}


if __name__ == "__main__":
    asyncio.run(manage_positions())
