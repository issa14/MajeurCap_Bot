"""
Module d'exécution (Testnet Binance) – v1.1
Utilise stop_loss_limit pour le stop-loss.
"""

import ccxt.async_support as ccxt_async
import logging
from config_loader import get_config

log = logging.getLogger(__name__)

def _get_binance_params():
    config = get_config()
    binance_cfg = config.get("binance_testnet", {})
    return {
        "api_key": binance_cfg.get("api_key", ""),
        "api_secret": binance_cfg.get("api_secret", ""),
        "testnet": binance_cfg.get("testnet", True)
    }

async def init_trading_exchange() -> ccxt_async.binance:
    """Initialise l'exchange avec les clés API (Testnet)."""
    params = _get_binance_params()
    exchange = ccxt_async.binance({
        "apiKey": params["api_key"],
        "secret": params["api_secret"],
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
        },
    })
    if params["testnet"]:
        exchange.set_sandbox_mode(True)
    log.info("Exchange Binance (Testnet) initialisé pour trading")
    return exchange


async def execute_signal(signal: dict, quantity: float) -> dict:
    """
    Passe un ordre de marché et place un stop-loss limit basé sur l'ATR.
    """
    exchange = await init_trading_exchange()
    try:
        symbol = signal["symbol"]
        direction = signal["direction"]
        side = "buy" if direction == "LONG" else "sell"
        
        # Récupération de l'ATR pour le calcul dynamique du prix limite
        # On prend un buffer de 15% de l'ATR comme marge de sécurité
        atr = signal.get("atr", 0)
        limit_buffer = (atr * 0.15) if atr > 0 else (signal["sl"] * 0.005)

        # 1. Ordre d'entrée au marché
        log.info(f"Envoi ordre {side} {quantity} {symbol}")
        entry_order = await exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=quantity,
        )
        log.info(f"Ordre d'entrée exécuté : {entry_order['id']}")

        # 2. Stop-loss (ordre stop_loss_limit)
        sl_side = "sell" if side == "buy" else "buy"
        sl_price = signal["sl"]
        
        # Calcul du prix limite dynamique
        if direction == "LONG":
            limit_price = round(sl_price - limit_buffer, 8)
        else:
            limit_price = round(sl_price + limit_buffer, 8)

        log.info(f"Placement SL dynamique : stopPrice={sl_price}, limitPrice={limit_price} (buffer ATR)")
        sl_order = await exchange.create_order(
            symbol=symbol,
            type="stop_loss_limit",
            side=sl_side,
            amount=quantity,
            price=limit_price,
            params={"stopPrice": sl_price}
        )
        log.info(f"Stop-loss placé : {sl_order['id']}")

        await exchange.close()
        return {"entry_order": entry_order, "sl_order": sl_order, "success": True}

    except Exception as e:
        log.error(f"Erreur exécution ordre : {e}")
        await exchange.close()
        return {"success": False, "error": str(e)}

async def update_sl_order(symbol: str, quantity: float, new_sl_price: float, direction: str, old_sl_order_id: str = None, atr: float = 0) -> dict:
    """
    Annule l'ancien stop-loss et en place un nouveau avec prix limite basé sur l'ATR.
    """
    exchange = await init_trading_exchange()
    try:
        # 1. Annulation de l'ancien ordre si présent
        if old_sl_order_id:
            try:
                log.info(f"Annulation ancien SL {old_sl_order_id} pour {symbol}")
                await exchange.cancel_order(old_sl_order_id, symbol)
            except Exception as e:
                log.warning(f"Impossible d'annuler l'ancien SL {old_sl_order_id}: {e}")

        # 2. Calcul du prix limite dynamique
        limit_buffer = (atr * 0.15) if atr > 0 else (new_sl_price * 0.005)
        sl_side = "sell" if direction == "LONG" else "buy"
        
        if direction == "LONG":
            limit_price = round(new_sl_price - limit_buffer, 8)
        else:
            limit_price = round(new_sl_price + limit_buffer, 8)

        log.info(f"Mise à jour SL dynamique : stopPrice={new_sl_price}, limitPrice={limit_price}")
        sl_order = await exchange.create_order(
            symbol=symbol,
            type="stop_loss_limit",
            side=sl_side,
            amount=quantity,
            price=limit_price,
            params={"stopPrice": new_sl_price}
        )
        log.info(f"Nouveau stop-loss placé : {sl_order['id']}")
        
        await exchange.close()
        return {"sl_order": sl_order, "success": True}

    except Exception as e:
        log.error(f"Erreur mise à jour SL : {e}")
        await exchange.close()
        return {"success": False, "error": str(e)}
