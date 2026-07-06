"""exchange.normalize — Normalisation des payloads ccxt bruts.

SEUL endroit du codebase qui a le droit de lire order["info"][...] ou de
gérer les incohérences triggerPrice/stopPrice. Toute nouvelle bizarrerie ccxt
découverte à l'avenir se corrige ICI, jamais dans execution/ ou risk/.

Historique des bugs corrigés ici :
- Bug #1 (03/07/2026) : triggerPrice vs stopPrice selon version ccxt.
- Bug #2 (03/07/2026) : order["type"] collapse stop_market/take_profit_market
  en "market" sur les marchés futures ; le vrai type est dans info["type"].
"""

from __future__ import annotations

from typing import Optional

from core.types import ExchangeOrder


def get_stop_price(order: dict) -> Optional[float]:
    """Extrait le prix de déclenchement d'un ordre ccxt brut.

    Vérifie triggerPrice (ccxt >= 4.x), stopPrice (versions antérieures / autres
    exchanges), puis info.stopPrice (payload brut Binance) en dernier recours.

    Retourne None si aucune valeur exploitable n'est trouvée — l'appelant doit
    gérer ce cas explicitement (ne PAS silencieusement traiter comme 0.0).
    """
    for key in ("triggerPrice", "stopPrice"):
        val = order.get(key)
        if val is not None:
            try:
                f = float(val)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
    info_val = (order.get("info") or {}).get("stopPrice")
    if info_val is not None:
        try:
            f = float(info_val)
            if f > 0:
                return f
        except (TypeError, ValueError):
            pass
    return None


def get_raw_order_type(order: dict) -> Optional[str]:
    """Retourne le type d'ordre RÉEL Binance (ex: "STOP_MARKET", "TAKE_PROFIT_MARKET").

    ccxt collapse order["type"] en "market" ou "limit" sur les marchés futures —
    ce champ ne doit JAMAIS être utilisé pour distinguer un stop d'un take-profit.
    Le type réel est dans order["info"]["type"] (payload brut Binance, en MAJUSCULES).

    Retourne None si absent (payload malformé) — l'appelant doit gérer ce cas.
    """
    raw_type = (order.get("info") or {}).get("type")
    if raw_type:
        return str(raw_type).upper()
    return None


def to_exchange_order(order: dict) -> ExchangeOrder:
    """Convertit un dict ccxt brut en ExchangeOrder typé.

    stop_price vaut 0.0 (pas None) dans l'ExchangeOrder si introuvable, pour
    rester compatible avec les comparaisons de tolérance existantes — mais
    get_stop_price() ci-dessus, utilisé en amont, permet de détecter le cas
    None avant d'atteindre cette conversion si l'appelant veut réagir dessus.
    """
    reduce_only = order.get("reduceOnly") is True or (
        (order.get("info") or {}).get("reduceOnly") == "true"
    )
    return ExchangeOrder(
        id=str(order.get("id", "")),
        symbol=order.get("symbol", ""),
        status=order.get("status", ""),
        raw_type=get_raw_order_type(order) or "",
        stop_price=get_stop_price(order) or 0.0,
        amount=float(order.get("amount", 0) or 0),
        reduce_only=reduce_only,
        raw=order,
    )


def normalize_symbol(symbol: str) -> str:
    """Retire le suffixe futures ':USDT' pour comparaison avec la DB.

    Identique à trade_manager._normalize_symbol — copié ici car exchange/
    ne doit pas importer trade_manager (dépendance inverse interdite).
    """
    return symbol.split(":")[0] if symbol else symbol


def to_futures_symbol(symbol: str) -> str:
    """Convertit un symbole DB ('BNB/USDT') en format Binance Futures ('BNB/USDT:USDT').

    Identique à trade_manager._to_futures_symbol — copié ici pour la même raison.
    """
    if symbol.endswith("/USDT") and ":USDT" not in symbol:
        return f"{symbol}:USDT"
    return symbol