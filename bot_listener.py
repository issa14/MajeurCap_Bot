import asyncio
import logging
import aiohttp
from dashboard import get_dashboard_text
from config_loader import get_config

# ─── Configuration ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot_listener")

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
                    log.error(f"Telegram error: {await resp.text()}")
        else:
            async with aiohttp.ClientSession() as new_session:
                async with new_session.post(url, json=payload, timeout=10) as resp:
                    if resp.status != 200:
                        log.error(f"Telegram error: {await resp.text()}")
    except Exception as e:
        log.error(f"Failed to send Telegram message: {e}")

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

                    # Sécurité : On ne répond qu'à l'utilisateur autorisé (allowed_user_id)
                    # NE PAS comparer avec chat_id (= destination du bot, pas l'expéditeur)
                    if sender_id != allowed_user_id:
                        log.warning(f"Message rejeté — sender_id non autorisé : {sender_id}")
                        continue

                    if text.startswith("/db") or text.startswith("/dashboard"):
                        log.info("📥 Commande reçue : Dashboard")
                        await send_message("⌛ Génération du dashboard...", session=session)
                        db_text = await get_dashboard_text()
                        # Envoi du dashboard en monospace
                        formatted_text = f"```\n{db_text}\n```"
                        await send_message(formatted_text, parse_mode="MarkdownV2", session=session)
                    
                    elif text.startswith("/start"):
                        await send_message("👋 Bonjour ! Envoyez `/db` pour voir l'état du bot.", session=session)

            except Exception as e:
                log.error(f"Erreur polling : {e}")
                await asyncio.sleep(10)
            
            await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(poll_updates())
