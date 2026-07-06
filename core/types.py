"""core.types — Types partagés du bot MajeurCap.

Toute donnée qui transite entre exchange/, risk/, execution/ passe par ces
dataclasses. Objectif : un champ manquant ou mal nommé lève une AttributeError
immédiate, au lieu de se propager silencieusement comme avec des dicts bruts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TpStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class ExchangeOrder:
    """Représentation normalisée d'un ordre ccxt, quelle que soit la version.

    Ne JAMAIS construire ce type ailleurs que dans exchange/normalize.py.
    """
    id: str
    symbol: str
    status: str
    raw_type: str          # type Binance réel (ex: "STOP_MARKET"), via info.type
    stop_price: float       # 0.0 si absent
    amount: float
    reduce_only: bool
    raw: dict                # payload ccxt brut, pour debug uniquement — ne pas parser ailleurs


@dataclass
class Position:
    """Miroir typé d'une ligne de la table SQLite `positions`."""
    id: Optional[int]
    symbol: str
    direction: str                  # "LONG" / "SHORT"
    status: PositionStatus
    entry_price: float
    entry_date: str
    quantity: float
    current_quantity: Optional[float]
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp1_status: TpStatus = TpStatus.PENDING
    tp2_status: TpStatus = TpStatus.PENDING
    partial_exit: bool = False
    sl_order_id: Optional[str] = None
    tp1_order_id: Optional[str] = None
    tp2_order_id: Optional[str] = None
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    pnl_pct: Optional[float] = None
    sl_sync_failures: int = 0
    tp_sync_failures: int = 0
    last_sl_alert_at: Optional[str] = None

    @classmethod
    def from_db_row(cls, row: dict) -> "Position":
        """Construit un Position depuis un dict issu de database.py (sqlite3.Row ou dict).

        Tolère les clés manquantes (colonnes ajoutées par migration ALTER TABLE)
        en retombant sur les valeurs par défaut du dataclass.
        """
        known_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in row.items() if k in known_fields}
        if "status" in filtered and not isinstance(filtered["status"], PositionStatus):
            filtered["status"] = PositionStatus(filtered["status"])
        if "tp1_status" in filtered and filtered["tp1_status"] is not None:
            filtered["tp1_status"] = TpStatus(filtered["tp1_status"])
        if "tp2_status" in filtered and filtered["tp2_status"] is not None:
            filtered["tp2_status"] = TpStatus(filtered["tp2_status"])
        return cls(**filtered)


@dataclass
class Signal:
    """Signal généré par le moteur de signal (module3_signal.py)."""
    symbol: str
    direction: str              # "LONG" / "SHORT"
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    confluences: int
    weighted_score: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class OrderIntent:
    """Ce que le bot veut faire poser sur l'exchange (avant envoi ccxt)."""
    symbol: str
    side: OrderSide
    order_type: str          # "stop_market" / "take_profit_market" / "market"
    quantity: float
    trigger_price: float
    reduce_only: bool = True