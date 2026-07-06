"""exchange.gateway — Point d'entrée unique pour les appels ccxt.

Toute interaction avec l'exchange (fetch, create, cancel) passe par ici.
Retourne des types core.types (ExchangeOrder), jamais des dicts ccxt bruts,
pour que risk/ et execution/ n'aient jamais besoin d'importer exchange.normalize
directement.
"""

from __future__ import annotations

from typing import List, Optional

from core.types import ExchangeOrder
from exchange.normalize import to_exchange_order, to_futures_symbol


class ExchangeGateway:
    """Wrapper typé autour d'une instance ccxt.async_support.

    Ne contient aucune logique métier (pas de matching, pas de tolérance de
    prix) — uniquement des appels réseau + conversion de types.
    """

    def __init__(self, exchange):
        self._exchange = exchange

    async def fetch_open_orders(self, symbol: str) -> List[ExchangeOrder]:
        """Récupère les ordres ouverts pour un symbole (format DB, ex: 'BNB/USDT')."""
        futures_symbol = to_futures_symbol(symbol)
        raw_orders = await self._exchange.fetch_open_orders(futures_symbol)
        return [to_exchange_order(o) for o in raw_orders]

    async def fetch_order_if_exists(
        self, order_id: Optional[str], symbol: str
    ) -> Optional[ExchangeOrder]:
        """fetch_order() avec gestion 404/erreur. Retourne None si absent.

        Comportement identique à trade_manager._fetch_order_if_exists — sera
        branché ici à l'étape 2.
        """
        if not order_id:
            return None
        futures_symbol = to_futures_symbol(symbol)
        try:
            raw = await self._exchange.fetch_order(str(order_id), futures_symbol)
            return to_exchange_order(raw)
        except Exception:
            return None