"""
Module d'exécution — Futures Demo Trading (Binance)
Utilise STOP_MARKET pour le stop-loss (futures, pas stop_loss_limit qui est spot-only).
Migration : set_sandbox_mode(True) → exchange.enable_demo_trading(True)
"""

import ccxt.async_support as ccxt_async
import logging
from config_loader import get_config

log = logging.getLogger(__name__)


def _get_binance_params():
    config = get_config()
    binance_cfg = config.get("binance_testnet", {})
    return {
        "api_key":    binance_cfg.get("api_key", ""),
        "api_secret": binance_cfg.get("api_secret", ""),
        "demo":       binance_cfg.get("testnet", True),
    }


async def init_trading_exchange() -> ccxt_async.binance:
    """
    Initialise l'exchange pour le trading futures en mode demo Binance.
    - defaultType: 'future'  → USDⓈ-M Perpetuals (BTCUSDT, ETHUSDT, …)
    - enable_demo_trading()  → redirige vers demo-fapi.binance.com (remplace l'ancien sandbox)
    """
    params = _get_binance_params()
    exchange = ccxt_async.binance({
        "apiKey":          params["api_key"],
        "secret":          params["api_secret"],
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",   # ← USDⓈ-M Perpetuals
        },
    })
    if params["demo"]:
        exchange.enable_demo_trading(True)   # ← remplace set_sandbox_mode(True)
    log.info("Exchange Binance Futures (Demo Trading) initialisé")
    return exchange


async def set_leverage(exchange: ccxt_async.binance, symbol: str, leverage: int) -> None:
    """Définit le levier pour un symbole avant d'ouvrir une position."""
    try:
        await exchange.set_leverage(leverage, symbol)
        log.info(f"{symbol} — levier défini à {leverage}x")
    except Exception as e:
        log.warning(f"{symbol} — impossible de définir le levier ({e}), on continue")


async def execute_signal(signal: dict, quantity: float) -> dict:
    """
    Ouvre une position futures (LONG ou SHORT) et place un STOP_MARKET.

    Différences vs spot :
    - SHORT = create_market_order side='sell' (vente à découvert, pas besoin d'avoir l'actif)
    - SL     = STOP_MARKET (exécuté au marché dès que stopPrice est touché)
    - Levier = configurable via config.yaml → risk.leverage (défaut: 1)
    """
    exchange = await init_trading_exchange()
    symbol   = signal["symbol"]
    direction = signal["direction"]
    side     = "buy" if direction == "LONG" else "sell"
    sl_side  = "sell" if direction == "LONG" else "buy"

    config   = get_config()
    leverage = config.get("risk", {}).get("leverage", 1)

    try:
        # 0. Définir le levier
        await set_leverage(exchange, symbol, leverage)

        # 1. Ordre d'entrée au marché
        log.info(f"Envoi ordre d'entrée futures {side} {quantity} {symbol} (levier {leverage}x)")
        entry_order = await exchange.create_market_order(
            symbol=symbol,
            side=side,
            amount=quantity,
            params={"reduceOnly": False},
        )
        log.info(f"Ordre d'entrée exécuté : {entry_order['id']} (Status: {entry_order['status']})")

        # 2. Stop-loss STOP_MARKET (futures — pas stop_loss_limit qui est spot-only)
        try:
            sl_price = signal["sl"]
            log.info(f"Placement SL STOP_MARKET : stopPrice={sl_price}, side={sl_side}")
            sl_order = await exchange.create_order(
                symbol=symbol,
                type="stop_market",           # ← type futures correct
                side=sl_side,
                amount=quantity,
                price=None,                   # STOP_MARKET n'a pas de limit price
                params={
                    "stopPrice":   sl_price,
                    "reduceOnly":  True,       # ← ferme uniquement, ne crée pas de nouvelle position
                    "closePosition": False,
                },
            )
            log.info(f"Stop-loss placé : {sl_order['id']}")
            return {"entry_order": entry_order, "sl_order": sl_order, "success": True}

        except Exception as sl_error:
            log.critical(
                f"FATAL: Entrée OK mais échec placement SL ({sl_error}). Sortie d'urgence !"
            )
            try:
                emergency_side  = sl_side   # même sens que le SL pour fermer
                emergency_exit  = await exchange.create_market_order(
                    symbol=symbol,
                    side=emergency_side,
                    amount=quantity,
                    params={"reduceOnly": True},
                )
                log.warning(f"Sortie d'urgence réussie : {emergency_exit['id']}")
                return {"success": False, "error": "SL_FAILED_EMERGENCY_EXIT", "entry_order": entry_order}
            except Exception as exit_error:
                log.critical(f"DANGER : Échec sortie d'urgence ! Position ouverte sans SL. {exit_error}")
                return {"success": False, "error": "SL_FAILED_EXIT_FAILED", "entry_order": entry_order}

    except ccxt_async.InsufficientFunds as e:
        log.error(f"Fonds insuffisants pour {symbol} : {e}")
        return {"success": False, "error": "INSUFFICIENT_FUNDS"}
    except ccxt_async.NetworkError as e:
        log.warning(f"Erreur réseau sur {symbol}, l'ordre est peut-être passé : {e}")
        return {"success": False, "error": "NETWORK_ERROR"}
    except Exception as e:
        log.error(f"Erreur exécution ordre {symbol} : {e}")
        return {"success": False, "error": str(e)}
    finally:
        await exchange.close()


async def update_sl_order(
    symbol: str,
    quantity: float,
    new_sl_price: float,
    direction: str,
    old_sl_order_id: str = None,
    atr: float = 0,
) -> dict:
    """
    Annule l'ancien stop-loss et en place un nouveau STOP_MARKET.
    Utilise un bloc finally pour garantir la fermeture de la connexion.
    """
    exchange = await init_trading_exchange()
    sl_side  = "sell" if direction == "LONG" else "buy"
    try:
        # 1. Annulation de l'ancien SL
        if old_sl_order_id:
            try:
                log.info(f"Annulation ancien SL {old_sl_order_id} pour {symbol}")
                await exchange.cancel_order(old_sl_order_id, symbol)
            except Exception as e:
                log.warning(f"Impossible d'annuler l'ancien SL {old_sl_order_id}: {e}")

        # 2. Nouveau STOP_MARKET
        log.info(f"Mise à jour SL STOP_MARKET : stopPrice={new_sl_price}")
        sl_order = await exchange.create_order(
            symbol=symbol,
            type="stop_market",
            side=sl_side,
            amount=quantity,
            price=None,
            params={
                "stopPrice":  new_sl_price,
                "reduceOnly": True,
            },
        )
        log.info(f"Nouveau stop-loss placé : {sl_order['id']}")
        return {"sl_order": sl_order, "success": True}

    except Exception as e:
        log.error(f"Erreur mise à jour SL : {e}")
        return {"success": False, "error": str(e)}

    finally:
        await exchange.close()   # ← toujours exécuté (fix bug fuite connexion)
