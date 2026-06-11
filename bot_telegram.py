"""
Telegram Bot final (v2.1) - Async Optimized
"""

import asyncio
import logging
import sys
import html
import aiohttp
import signal
from pathlib import Path

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
from module2_AT import analyze_all
from module3_signal import scan_all
from trade_manager import manage_positions, open_position, load_positions
from config_loader import get_config, reload_config
from logging.handlers import RotatingFileHandler

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

# ─── Fonctions Telegram Asynchrones ──────────────────────────────────────
async def send_telegram_message(text: str, disable_notification: bool = False):
    config = get_config()
    tg_cfg = config.get("telegram", {})
    token = tg_cfg.get("token", "")
    chat_id = tg_cfg.get("chat_id", "")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": disable_notification
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    log.error(f"Erreur Telegram : {await resp.text()}")
    except Exception as e:
        log.error(f"Échec envoi Telegram : {e}")

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
    config = reload_config()

    # 1. Gestion des positions existantes
    await manage_positions()

    # 2. Récupération des données
    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, use_cache=True)
        if not data:
            asyncio.create_task(send_telegram_message("⚠️ Aucune donnée récupérée."))
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

        for sig in signals:
            pair = sig["symbol"]
            
            # 1. Envoi systématique des détails du signal
            msg_detail = format_signal(sig)
            await send_telegram_message(msg_detail)
            
            # 2. Tentative d'ouverture
            result = await open_position(sig, config)
            
            if result["success"]:
                # Notification exécution réussie
                quantity = result.get("quantity", 0)
                msg_exec = f"✅ <b>Ordre exécuté</b> pour {pair}\nQuantité : <code>{quantity:.6f}</code>\nEntrée : <code>{sig['entry']}</code>"
                await send_telegram_message(msg_exec)
            else:
                reason = result.get("reason", "Inconnue")
                
                if reason == "already_open":
                    log.info(f"{pair} — signal ignoré (position déjà ouverte)")
                else:
                    # Enrichissement du message avec contexte technique si disponible
                    display_reason = reason
                    if reason == "ADX trop faible":
                        display_reason = f"ADX trop faible ({sig.get('adx', 0):.1f} < {config.get('signal', {}).get('adx_threshold')})"
                    
                    msg_reject = f"🚫 <b>Ordre non passé</b> pour {pair}\nRaison : {display_reason}"
                    if "current" in result:
                        msg_reject += f" ({result['current']} / {result['limit']})"
                    
                    await send_telegram_message(msg_reject)
            
            await asyncio.sleep(0.5)

    finally:
        await exchange.close()

async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    
    while not stop_event.is_set():
        await run_scan_cycle()
        # Scan toutes les 60s
        for _ in range(60):
            if stop_event.is_set(): break
            await asyncio.sleep(1)
    
    log.info("Signal d'arrêt reçu — arrêt propre du bot après le cycle en cours.")

if __name__ == "__main__":
    asyncio.run(main())
