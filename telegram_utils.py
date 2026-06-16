import aiohttp
import logging

log = logging.getLogger("telegram")

async def send_telegram(text: str, config: dict, disable_notification: bool = False):
    """Envoi centralisé de messages Telegram."""
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
