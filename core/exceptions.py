"""core.exceptions — Exceptions typées du bot MajeurCap.

Remplace les échecs silencieux (return None, valeur par défaut 0.0) par des
erreurs explicites là où l'ambiguïté a coûté des jours de debug (bugs #1, #2).
"""


class MajeurCapError(Exception):
    """Base de toutes les exceptions du bot."""


class StopPriceMissing(MajeurCapError):
    """Aucun triggerPrice/stopPrice/info.stopPrice trouvé sur un ordre ccxt.

    Lever cette exception plutôt que de retourner 0.0 permet à l'appelant de
    décider explicitement quoi faire (recréer l'ordre, alerter), au lieu de
    propager silencieusement un stop_price de 0.0 dans un calcul de tolérance.
    """
    def __init__(self, order_id: str, symbol: str):
        self.order_id = order_id
        self.symbol = symbol
        super().__init__(f"Aucun stop price trouvé pour l'ordre {order_id} ({symbol})")


class OrderTypeUnresolved(MajeurCapError):
    """Le type d'ordre brut Binance (info.type) est absent ou vide."""
    def __init__(self, order_id: str):
        self.order_id = order_id
        super().__init__(f"Type d'ordre brut introuvable pour l'ordre {order_id}")


class CircuitBreakerBlocked(MajeurCapError):
    """Le circuit breaker bloque le trading. Distinct d'une erreur — c'est un
    état normal, mais on veut pouvoir le distinguer d'une exception exchange."""
    def __init__(self, reason: str, unblocks_at: str = None):
        self.reason = reason
        self.unblocks_at = unblocks_at
        super().__init__(reason)