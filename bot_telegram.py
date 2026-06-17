"""
Telegram Bot final (v2.1) - Async Optimized
"""

import asyncio
import logging
import sys
import html
import aiohttp
import signal
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
from module2_AT import analyze_all
from module3_signal import scan_all
from trade_manager import manage_positions, open_position
from config_loader import get_config, reload_config
from database import db
from logging.handlers import RotatingFileHandler
from telegram_utils import send_telegram

# ─── Déduplication des signaux ───────────────────────────────────────────────
# Mémorise le dernier envoi Telegram par symbole pour éviter le spam
_signal_sent_at: dict[str, datetime] = db.get_signal_cooldowns()
# Le cooldown est lu depuis config.yaml → signal.cooldown_minutes (défaut 240)

# ─── Hot-Reload optimisé ─────────────────────────────────────────────────────
_config_mtime = 0.0

def reload_config_if_changed() -> dict:
    """Relit config.yaml uniquement si le fichier a été modifié sur le disque."""
    global _config_mtime
    config_path = "config.yaml"
    try:
        mtime = os.path.getmtime(config_path)
        if mtime != _config_mtime:
            _config_mtime = mtime
            log.info("Configuration modifiée détectée, rechargement...")
            return reload_config()
    except Exception as e:
        log.error(f"Erreur lors de la vérification du mtime de la config : {e}")
    
    return get_config()

# ─── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("bot.log", maxBytes=10 * 1024 * 1024, backupCount=5),
    ],
)
log = logging.getLogger("telegram_bot")

def format_signal(sig: dict) -> str:
    direction_emoji = "🟢 LONG" if sig["direction"] == "LONG" else "🔴 SHORT"
    has_struct = sig["structure"]["pivots_count"] >= 4
    quality = "⭐ Signal structuré" if has_struct else "⚠️ Sans structure (seuil renforcé)"

    symbol = html.escape(sig["symbol"])
    entry = html.escape(str(sig["entry"]))
    sl = html.escape(str(sig["sl"]))
    sl_pct = html.escape(str(sig["sl_pct"]))
    tp1 = html.escape(str(sig["tp1"]))
    tp2 = html.escape(str(sig["tp2"]))
    rr1 = html.escape(str(sig["rr1"]))
    rr2 = html.escape(str(sig["rr2"]))
    n_confl = html.escape(str(len(sig["confluences"])))
    threshold = html.escape(str(sig["threshold"]))
    trend = html.escape(sig["structure"]["trend"].upper())
    bos = html.escape(sig["structure"]["bos"] or "—")
    choch = html.escape(sig["structure"]["choch"] or "—")
    pivots = html.escape(str(sig["structure"]["pivots_count"]))

    text = f"{direction_emoji}  {symbol}  ({quality})\n"
    text += f"Entrée : <b>{entry}</b>\n"
    text += f"SL     : {sl} ({sl_pct}% | ATR×1.5)\n"
    text += f"TP1    : {tp1} (RR 1:{rr1})\n"
    text += f"TP2    : {tp2} (RR 1:{rr2})\n"

    if "quantity" in sig and sig["quantity"] is not None:
        text += f"📊 Quantité : {sig['quantity']:.6f}\n"

    text += f"\nConfluences ({n_confl}/{threshold} min) :\n"
    for c in sig["confluences"]:
        text += f"  ✓ {html.escape(c)}\n"
    text += f"\nStructure : {trend} | BOS: {bos} | CHoCH: {choch} | Pivots: {pivots}"
    return text

# ─── Boucle principale ──────────────────────────────────────────────────
async def run_scan_cycle():
    log.info("Début du scan...")
    config = reload_config_if_changed()

    # manage_positions() est appelé directement dans la boucle principale (main)
    # pour s'exécuter à chaque cycle de 60s indépendamment du scan signaux

    # 2. Récupération des données
    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, use_cache=True)
        if not data:
            await send_telegram("⚠️ Aucune donnée récupérée.", config)
            return

        daily_data = {}
        if config.get("signal", {}).get("daily_filter_enabled", False):
            daily_data = await fetch_daily_all_async(exchange, symbols=None)

        # 3. Analyse technique et génération des signaux
        analyzed = analyze_all(data, config, include_incomplete=False, daily_data=daily_data)
        signals = scan_all(analyzed, config)

        # 4. Traitement des signaux
        if not signals:
            log.info("Aucun signal ce cycle.")
            return

        now = datetime.now(timezone.utc)

        for sig in signals:
            pair = sig["symbol"]

            cooldown_min = config.get("signal", {}).get("cooldown_minutes", 240)
            last_sent = _signal_sent_at.get(pair)
            tg_on_cooldown = last_sent and (now - last_sent) < timedelta(minutes=cooldown_min)

            # Le trade est TOUJOURS tenté, indépendamment du cooldown Telegram
            result = await open_position(sig, config)

            if result["success"]:
                # Nouvelle position ouverte : on notifie et on mémorise
                _signal_sent_at[pair] = now
                db.update_signal_cooldown(pair, now)
                if not tg_on_cooldown:          # ← Telegram throttlé, pas le trade
                    msg_detail = format_signal(sig)
                    await send_telegram(msg_detail, config)
                    quantity = result.get("quantity", 0)
                    msg_exec = f"✅ <b>Ordre exécuté</b> pour {pair}\nQuantité : <code>{quantity:.6f}</code>\nEntrée : <code>{sig['entry']}</code>"
                    await send_telegram(msg_exec, config)

            elif result.get("reason") == "already_open":
                log.info(f"{pair} — signal ignoré (position déjà ouverte)")

            else:
                # Rejet légitime : notifier une fois (si pas en cooldown)
                if not tg_on_cooldown:
                    _signal_sent_at[pair] = now
                    db.update_signal_cooldown(pair, now)
                    await send_telegram(format_signal(sig), config)
                    
                    reason = result.get("reason", "Inconnue")
                    display_reason = reason
                    if reason == "ADX trop faible":
                        display_reason = f"ADX trop faible ({sig.get('adx', 0):.1f} < {config.get('signal', {}).get('adx_threshold')})"
                    
                    msg_reject = f"🚫 <b>Ordre non passé</b> pour {pair}\nRaison : {display_reason}"
                    if "current" in result:
                        msg_reject += f" ({result['current']} / {result['limit']})"
                    await send_telegram(msg_reject, config)

            await asyncio.sleep(0.5)

    finally:
        await exchange.close()

async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    POSITION_CHECK_INTERVAL = 60      # secondes — vérification des positions ouvertes
    SIGNAL_SCAN_INTERVAL    = 900     # secondes — scan signaux (toutes les 15 min)

    last_signal_scan = 0.0            # force un scan immédiat au démarrage

    while not stop_event.is_set():
        now = asyncio.get_event_loop().time()

        # 1. Gestion des positions — toujours (60s)
        try:
            config = reload_config_if_changed()
            await manage_positions()
        except Exception as e:
            log.error(f"Erreur manage_positions : {e}", exc_info=True)

        # 2. Scan signaux — seulement toutes les 15 min
        if (now - last_signal_scan) >= SIGNAL_SCAN_INTERVAL:
            try:
                await run_scan_cycle()
                last_signal_scan = asyncio.get_event_loop().time()
            except Exception as e:
                log.error(f"Erreur run_scan_cycle : {e}", exc_info=True)

        # Attendre 60s (interruptible par stop_event)
        for _ in range(POSITION_CHECK_INTERVAL):
            if stop_event.is_set():
                break
            await asyncio.sleep(1)

    log.info("Signal d'arrêt reçu — arrêt propre du bot après le cycle en cours.")

if __name__ == "__main__":
    asyncio.run(main())
