import asyncio
import logging
import requests
from pathlib import Path
from dashboard import get_dashboard_text
from config_loader import get_config

# ─── Configuration ───────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot_listener")

def _get_tg_config():
    config = get_config()
    tg_cfg = config.get("telegram", {})
    return tg_cfg.get("token", ""), str(tg_cfg.get("chat_id", ""))

# ─── Fonctions API Telegram ──────────────────────────────────────────────────
def send_message(text):
    token, chat_id = _get_tg_config()
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

async def poll_updates():
    """Boucle de polling pour écouter les messages Telegram."""
    last_update_id = 0
    log.info("📡 Bot Listener démarré. En attente de commandes sur Telegram...")
    
    token, chat_id = _get_tg_config()

    while True:
        try:
            if not token or not chat_id:
                token, chat_id = _get_tg_config()
                if not token or not chat_id:
                    await asyncio.sleep(10)
                    continue

            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            resp = requests.get(url, params=params, timeout=35).json()

            if not resp.get("ok"):
                await asyncio.sleep(5)
                continue

            for update in resp.get("result", []):
                last_update_id = update["update_id"]
                message = update.get("message", {})
                text = message.get("text", "")
                sender_id = str(message.get("from", {}).get("id", ""))

                # Sécurité : On ne répond qu'à VOTRE Chat ID
                if sender_id != chat_id:
                    continue

                if text.startswith("/db") or text.startswith("/dashboard"):
                    log.info("📥 Commande reçue : Dashboard")
                    send_message("⌛ Génération du dashboard...")
                    db_text = await get_dashboard_text()
                    send_message(db_text)
                
                elif text.startswith("/start"):
                    send_message("👋 Bonjour ! Envoyez `/db` pour voir l'état du bot.")

        except Exception as e:
            log.error(f"Erreur polling : {e}")
            await asyncio.sleep(10)
        
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(poll_updates())
