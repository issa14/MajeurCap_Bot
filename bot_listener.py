import asyncio
import html as _html
import logging
import aiohttp
from dashboard import get_dashboard_text
from config_loader import get_config
from bot_telegram import build_status_message

# ─── Configuration ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot_listener")

# ─── État de la confirmation /closeall ────────────────────────────────────────
# Stocke l'instant de la demande de confirmation par chat_id. Une fois confirmée
# avec /confirm_closeall, l'entrée est effacée. Timeout implicite : 60 secondes
# (effacé au prochain cycle de polling).
_pending_closeall: dict[str, float] = {}

CLOSEALL_TIMEOUT = 60  # secondes


def _get_tg_config():
    config = get_config()
    tg_cfg = config.get("telegram", {})
    token = tg_cfg.get("token", "")
    chat_id = str(tg_cfg.get("chat_id", ""))
    # allowed_user_id : ID Telegram de l'utilisateur autorisé à envoyer des commandes.
    # Distinct du chat_id (destination des messages). Fallback sur chat_id si absent.
    allowed_user_id = str(tg_cfg.get("allowed_user_id", chat_id))
    return token, chat_id, allowed_user_id


# ─── Fonctions API Telegram ──────────────────────────────────────────────────
async def send_message(text, parse_mode="Markdown", session=None):
    token, chat_id, _ = _get_tg_config()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}

    try:
        if session:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    log.error(f"Telegram error ({resp.status}): {await resp.text()}")
                return resp
        else:
            async with aiohttp.ClientSession() as new_session:
                async with new_session.post(url, json=payload, timeout=10) as resp:
                    if resp.status != 200:
                        log.error(f"Telegram error ({resp.status}): {await resp.text()}")
                    return resp
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")
        return None


async def poll_updates():
    """Boucle de polling pour écouter les messages Telegram."""
    last_update_id = 0
    log.info("📡 Bot Listener démarré. En attente de commandes sur Telegram...")

    token, chat_id, allowed_user_id = _get_tg_config()

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                if not token or not chat_id:
                    token, chat_id, allowed_user_id = _get_tg_config()
                    if not token or not chat_id:
                        await asyncio.sleep(10)
                        continue

                # Nettoyage des confirmations /closeall expirées
                now_ts = asyncio.get_event_loop().time()
                expired = [
                    cid for cid, ts in _pending_closeall.items()
                    if now_ts - ts > CLOSEALL_TIMEOUT
                ]
                for cid in expired:
                    del _pending_closeall[cid]

                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {"offset": last_update_id + 1, "timeout": 30}

                async with session.get(url, params=params, timeout=35) as resp:
                    if resp.status != 200:
                        log.error(f"Telegram polling error: {resp.status}")
                        await asyncio.sleep(5)
                        continue

                    data = await resp.json()

                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue

                for update in data.get("result", []):
                    last_update_id = update["update_id"]
                    message = update.get("message", {})
                    text = message.get("text", "")
                    sender_id = str(message.get("from", {}).get("id", ""))
                    msg_chat_id = str(message.get("chat", {}).get("id", ""))

                    # Sécurité : On ne répond qu'à l'utilisateur autorisé (allowed_user_id)
                    if sender_id != allowed_user_id:
                        log.warning(f"Message rejeté — sender_id non autorisé : {sender_id}")
                        continue

                    # Toute commande autre que /confirm_closeall annule une confirmation
                    # /closeall en attente pour ce chat (comportement annoncé au user).
                    if (
                        msg_chat_id in _pending_closeall
                        and not text.strip().startswith("/confirm_closeall")
                    ):
                        del _pending_closeall[msg_chat_id]
                        log.info(f"/closeall annulé (autre commande reçue) pour chat {msg_chat_id}")

                    # ── /db  /dashboard ──────────────────────────────────────
                    if text.startswith("/db") or text.startswith("/dashboard"):
                        log.info("📥 Commande reçue : Dashboard")
                        await send_message("⌛ Génération du dashboard...", session=session)
                        db_text = await get_dashboard_text()
                        safe = _html.escape(db_text)
                        formatted_text = f"<pre>{safe}</pre>"
                        await send_message(formatted_text, parse_mode="HTML", session=session)

                    # ── /status ──────────────────────────────────────────────
                    elif text.startswith("/status"):
                        log.info("📥 Commande reçue : Status")
                        msg = await build_status_message()
                        if msg:
                            await send_message(msg, parse_mode="HTML", session=session)
                        else:
                            await send_message("✅ Aucune position active en ce moment.", session=session)

                    # ── /pnl ─────────────────────────────────────────────────
                    elif text.startswith("/pnl"):
                        log.info("📥 Commande reçue : PnL")
                        await send_message("⌛ Calcul du PnL...", session=session)

                        from trade_manager import get_pnl_summary
                        config = get_config()
                        pnl = await get_pnl_summary(config)
                        pnl_text = _html.escape(pnl["telegram_text"])
                        await send_message(
                            f"<pre>{pnl_text}</pre>",
                            parse_mode="HTML",
                            session=session,
                        )

                    # ── /closeall (étape 1 : demande de confirmation) ────────
                    elif text.strip().startswith("/closeall"):
                        log.info("📥 Commande reçue : CloseAll (demande de confirmation)")

                        from trade_manager import load_positions
                        active = [p for p in load_positions() if p.get("status") != "closed"]
                        count = len(active)

                        if count == 0:
                            await send_message("✅ Aucune position active à fermer.", session=session)
                            _pending_closeall.pop(msg_chat_id, None)
                            continue

                        symbols = ", ".join(p["symbol"] for p in active)
                        _pending_closeall[msg_chat_id] = asyncio.get_event_loop().time()

                        await send_message(
                            f"⚠️ <b>ATTENTION</b> — Tu vas fermer <b>{count} position(s)</b> :\n"
                            f"<code>{_html.escape(symbols)}</code>\n\n"
                            f"Pour confirmer, réponds <code>/confirm_closeall</code> dans les "
                            f"<b>{CLOSEALL_TIMEOUT}s</b>.\n"
                            f"Toute autre commande annule la confirmation.",
                            parse_mode="HTML",
                            session=session,
                        )

                    # ── /confirm_closeall (étape 2 : exécution) ──────────────
                    elif text.strip().startswith("/confirm_closeall"):
                        log.info("📥 Commande reçue : Confirm CloseAll")

                        if msg_chat_id not in _pending_closeall:
                            await send_message(
                                "⏱️ Aucune demande de /closeall en attente (expirée ou jamais initiée). "
                                "Tape d'abord /closeall.",
                                session=session,
                            )
                            continue

                        # Vérifier le timeout
                        request_ts = _pending_closeall[msg_chat_id]
                        if asyncio.get_event_loop().time() - request_ts > CLOSEALL_TIMEOUT:
                            del _pending_closeall[msg_chat_id]
                            await send_message(
                                "⏱️ La demande de /closeall a expiré. Retape /closeall pour recommencer.",
                                session=session,
                            )
                            continue

                        # Exécuter la fermeture
                        del _pending_closeall[msg_chat_id]
                        await send_message("🔄 Fermeture en cours...", session=session)

                        from trade_manager import close_all_positions_async
                        config = get_config()
                        result = await close_all_positions_async(config)

                        summary = _html.escape(
                            f"✅ Fermeture terminée : {result['closed_count']}/"
                            f"{result['total']} position(s) fermée(s)."
                        )
                        if result.get("errors"):
                            err_list = ", ".join(result["errors"][:3])
                            summary += f"\n⚠️ {len(result['errors'])} erreur(s) : {_html.escape(err_list)}"
                        await send_message(summary, session=session)

                    # ── /start ───────────────────────────────────────────────
                    elif text.startswith("/start"):
                        await send_message(
                            "👋 Bonjour ! Commandes disponibles :\n"
                            "  /status   — Positions actives + PnL temps réel\n"
                            "  /pnl      — PnL réalisé & latent (récapitulatif)\n"
                            "  /db       — Dashboard complet\n"
                            "  /closeall — Fermer TOUTES les positions (confirmation requise)",
                            session=session,
                        )

            except Exception as e:
                log.error(f"Erreur polling : {e}")
                await asyncio.sleep(10)

            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(poll_updates())