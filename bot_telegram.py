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
from trade_manager import manage_positions, open_position, reconcile_positions_on_startup, verify_active_orders
from config_loader import get_config, reload_config
from database import db
from logging.handlers import RotatingFileHandler
from telegram_utils import send_telegram
from dashboard import get_exchange

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

async def build_status_message() -> str:
    """
    Construit un message de statut concis pour le heartbeat et la commande /status.
    Retourne une chaîne HTML listant les positions actives avec PnL temps réel.
    Retourne None si aucune position active (pas d'envoi inutile).
    """
    config = get_config()
    active_positions = [p for p in db.get_active_positions() if p.get("status") != "closed"]

    if not active_positions:
        return None

    # Récupérer les prix courants
    symbols = [p["symbol"] for p in active_positions]
    exchange = await get_exchange(config)
    try:
        raw_tickers = await exchange.fetch_tickers(symbols)
        tickers = {sym.split(":")[0]: t for sym, t in raw_tickers.items()}
    except Exception as e:
        log.warning(f"build_status_message: fetch_tickers échoué ({e})")
        tickers = {}
    finally:
        await exchange.close()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 <b>Status positions — {now_str}</b>\n"]

    total_pnl_usd = 0.0
    for pos in active_positions:
        symbol    = pos["symbol"]
        direction = pos["direction"]
        entry     = pos.get("entry_price") or pos.get("entry") or 0
        sl        = pos.get("sl_price") or pos.get("sl") or 0
        tp1       = pos.get("tp1_price") or pos.get("tp1") or 0
        tp2       = pos.get("tp2_price") or pos.get("tp2") or 0
        qty       = pos.get("current_quantity") or pos.get("quantity") or 0
        tp1_status = pos.get("tp1_status", "PENDING")

        price = tickers.get(symbol, {}).get("last", entry)

        if entry > 0:
            pnl_pct = (price - entry) / entry * 100 if direction == "LONG" else (entry - price) / entry * 100
        else:
            pnl_pct = 0.0

        pnl_usd = (price - entry) * qty if direction == "LONG" else (entry - price) * qty
        total_pnl_usd += pnl_usd

        sl_dist = abs(price - sl) / price * 100 if price > 0 else 0.0
        sl_warn = " ⚠️" if sl_dist < 1.0 else ""

        dir_emoji  = "🟢" if direction == "LONG" else "🔴"
        pnl_emoji  = "✅" if pnl_pct >= 0 else "❌"
        tp1_badge  = " <i>[TP1✓]</i>" if tp1_status == "FILLED" else ""

        lines.append(
            f"{dir_emoji} <b>{symbol}</b>{tp1_badge}\n"
            f"  Entry: <code>{entry}</code> → Now: <code>{price:.4f}</code>\n"
            f"  PnL: {pnl_emoji} <b>{pnl_pct:+.2f}%</b> ({pnl_usd:+.2f} USDT)\n"
            f"  SL: {sl_dist:.1f}%{sl_warn} | TP1: <code>{tp1}</code> | TP2: <code>{tp2}</code>"
        )

    pnl_total_emoji = "✅" if total_pnl_usd >= 0 else "❌"
    lines.append(f"\n{pnl_total_emoji} PnL latent total : <b>{total_pnl_usd:+.2f} USDT</b>")
    lines.append(f"📌 {len(active_positions)} position(s) active(s)")

    return "\n".join(lines)

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

            # Log du signal pour historique/dashboard (tradé ou non)
            try:
                db.insert_signal_log(
                    symbol=pair,
                    direction=sig.get("direction", ""),
                    entry=sig.get("entry"),
                    sl=sig.get("sl"),
                    tp1=sig.get("tp1"),
                    tp2=sig.get("tp2"),
                    confluences=sig.get("confluences", []),
                    traded=result.get("success", False),
                    reject_reason=result.get("reason") if not result.get("success") else None,
                )
            except Exception as log_error:
                log.warning(f"Échec log signal {pair}: {log_error}")

            if result["success"]:
                # Nouvelle position ouverte : TOUJOURS notifier, le cooldown ne s'applique
                # qu'au spam de signaux non tradés (rejetés), jamais à un trade réel.
                _signal_sent_at[pair] = now
                db.update_signal_cooldown(pair, now)
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

    POSITION_CHECK_INTERVAL = 60       # secondes — vérification des positions ouvertes
    SIGNAL_SCAN_INTERVAL    = 900      # secondes — scan signaux (toutes les 15 min)
    RECONCILE_INTERVAL      = 1800     # secondes — réconciliation DB↔Binance (30 min)
    VERIFY_ORDERS_INTERVAL  = 300      # secondes — vérification ordres SL/TP (5 min)

    config = get_config()
    heartbeat_minutes = config.get("telegram", {}).get("heartbeat_minutes", 300)
    HEARTBEAT_INTERVAL = heartbeat_minutes * 60  # conversion en secondes

    last_signal_scan   = 0.0  # force un scan immédiat au démarrage
    last_heartbeat     = asyncio.get_event_loop().time()
    last_reconcile     = asyncio.get_event_loop().time()  # déjà fait au startup, on attend le prochain cycle
    last_verify_orders = asyncio.get_event_loop().time()  # idem

    # Réconciliation unique au démarrage : DB vs Binance
    try:
        await reconcile_positions_on_startup()
    except Exception as e:
        log.error(f"Erreur reconcile_on_startup : {e}", exc_info=True)

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

        # 3. Réconciliation périodique DB↔Binance — toutes les 30 min
        if (now - last_reconcile) >= RECONCILE_INTERVAL:
            try:
                await reconcile_positions_on_startup()
                last_reconcile = asyncio.get_event_loop().time()
            except Exception as e:
                log.error(f"Erreur reconcile périodique : {e}", exc_info=True)

        # 4. Vérification des ordres SL/TP — toutes les 5 min
        if (now - last_verify_orders) >= VERIFY_ORDERS_INTERVAL:
            try:
                await verify_active_orders(config)
                last_verify_orders = asyncio.get_event_loop().time()
            except Exception as e:
                log.error(f"Erreur verify_active_orders : {e}", exc_info=True)

        # 5. Heartbeat — toutes les N heures si positions actives (0 = désactivé)
        if HEARTBEAT_INTERVAL > 0 and (now - last_heartbeat) >= HEARTBEAT_INTERVAL:
            try:
                msg = await build_status_message()
                if msg:
                    await send_telegram(msg, config)
                last_heartbeat = asyncio.get_event_loop().time()
            except Exception as e:
                log.error(f"Erreur heartbeat : {e}", exc_info=True)

        # Attendre 60s (interruptible par stop_event)
        for _ in range(POSITION_CHECK_INTERVAL):
            if stop_event.is_set():
                break
            await asyncio.sleep(1)

    log.info("Signal d'arrêt reçu — arrêt propre du bot après le cycle en cours.")

    # Cleanup : attendre les tasks asyncio pendantes (create_task fire-and-forget)
    # pour éviter les "Unclosed client session" sur les sessions aiohttp.
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        log.info(f"Attente de {len(pending)} task(s) pendante(s) avant arrêt...")
        await asyncio.gather(*pending, return_exceptions=True)
    log.info("Arrêt propre terminé.")

if __name__ == "__main__":
    asyncio.run(main())
