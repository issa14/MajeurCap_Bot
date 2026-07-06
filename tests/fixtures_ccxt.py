"""tests.fixtures_ccxt — Payloads ccxt bruts reproduisant les bugs déjà rencontrés.

Chaque fixture correspond à un incident réel documenté dans le projet. Si un de
ces tests échoue après une modification, c'est qu'une régression a été
réintroduite — ne jamais supprimer une fixture sans comprendre le bug qu'elle
couvre.
"""


def order_ccxt_4_5_64_stop_market():
    """Bug #1 + #2 : ordre SL réel tel que retourné par ccxt 4.5.64 sur futures.

    - order["type"] est collapsé en "market" (PAS "stop_market")
    - order["stopPrice"] est absent (None) — seul triggerPrice existe
    - le type réel Binance est dans info["type"]
    """
    return {
        "id": "123456",
        "symbol": "BNB/USDT:USDT",
        "status": "open",
        "type": "market",
        "stopPrice": None,
        "triggerPrice": 590.5,
        "amount": 0.5,
        "reduceOnly": True,
        "info": {
            "type": "STOP_MARKET",
            "stopPrice": "590.50000000",
            "reduceOnly": "true",
        },
    }


def order_legacy_stop_price_only():
    """Ordre d'une version ccxt antérieure ou d'un autre exchange : stopPrice
    au niveau racine, pas de triggerPrice."""
    return {
        "id": "789012",
        "symbol": "BTC/USDT:USDT",
        "status": "open",
        "type": "stop",
        "stopPrice": 61000.0,
        "amount": 0.01,
        "reduceOnly": True,
        "info": {"type": "STOP_MARKET", "stopPrice": "61000.00"},
    }


def order_malformed_no_price():
    """Ordre sans aucun prix de déclenchement exploitable (payload corrompu ou
    ordre limit classique passé par erreur à get_stop_price)."""
    return {
        "id": "000000",
        "symbol": "SOL/USDT:USDT",
        "status": "open",
        "type": "limit",
        "stopPrice": None,
        "triggerPrice": None,
        "amount": 1.0,
        "reduceOnly": False,
        "info": {"type": "LIMIT"},
    }


def order_take_profit_market():
    """TP réel tel que retourné par ccxt 4.5.64 — même piège que le SL."""
    return {
        "id": "555555",
        "symbol": "ETH/USDT:USDT",
        "status": "open",
        "type": "market",
        "stopPrice": None,
        "triggerPrice": 3450.0,
        "amount": 0.2,
        "reduceOnly": True,
        "info": {
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": "3450.00000000",
            "reduceOnly": "true",
        },
    }