"""Tests de sync_engine.protection.enforce_protection avec un exchange mocké.

Couvre les scénarios déjà rencontrés en prod : création réussie, ordre déjà
présent (idempotence), cooldown actif (bug deadlock), et cleanup -4045.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from sync_engine.protection import enforce_protection


def _base_pos(**overrides):
    pos = {
        "sl_order_id": None, "tp1_order_id": "111", "tp2_order_id": "222",
        "sl_sync_failures": 0, "tp_sync_failures": 0,
        "sl_cooldown_until": None, "tp_cooldown_until": None,
        "last_sl_alert_at": None,
    }
    pos.update(overrides)
    return pos


def _mock_exchange(create_side_effect=None, open_orders=None):
    ex = AsyncMock()
    ex.fetch_open_orders.return_value = open_orders or []
    ex.amount_to_precision = MagicMock(return_value="0.5")
    if create_side_effect:
        ex.create_order.side_effect = create_side_effect
    else:
        ex.create_order.return_value = {"id": "999888"}
    return ex


@pytest.mark.asyncio
async def test_creates_missing_sl_successfully():
    with patch("sync_engine.protection.db") as mock_db, \
         patch("sync_engine.protection.send_telegram", new=AsyncMock()):
        exchange = _mock_exchange()
        await enforce_protection(
            pos=_base_pos(), pos_id=1, db_sym="BNB/USDT", direction="LONG",
            exchange_qty=0.5, sl_price=590.0, tp1_price=610.0, tp2_price=630.0,
            sl_found=False, tp1_found=True, tp2_found=True, tp1_hit=False,
            exchange=exchange, config={},
            to_futures_symbol=lambda s: f"{s}:USDT",
            get_stop_price=lambda o: o.get("triggerPrice"),
        )
        exchange.create_order.assert_called_once()
        mock_db.reset_sync_failure.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_skips_creation_when_sl_in_cooldown():
    """LE test critique : reproduit le scénario du deadlock BNB/USDT — vérifie
    qu'on ne tente PAS de recréer pendant le cooldown (mais qu'on continuerait
    après expiration, couvert par les tests de risk/circuit_breaker.py)."""
    with patch("sync_engine.protection.db") as mock_db, \
         patch("sync_engine.protection.send_telegram", new=AsyncMock()):
        exchange = _mock_exchange()
        future_cooldown = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        pos = _base_pos(sl_sync_failures=3, sl_cooldown_until=future_cooldown)
        await enforce_protection(
            pos=pos, pos_id=1, db_sym="BNB/USDT", direction="LONG",
            exchange_qty=0.5, sl_price=590.0, tp1_price=610.0, tp2_price=630.0,
            sl_found=False, tp1_found=True, tp2_found=True, tp1_hit=False,
            exchange=exchange, config={},
            to_futures_symbol=lambda s: f"{s}:USDT",
            get_stop_price=lambda o: o.get("triggerPrice"),
        )
        exchange.create_order.assert_not_called()


@pytest.mark.asyncio
async def test_skips_creation_when_order_already_exists():
    existing_sl = {
        "id": "555", "amount": 0.5, "reduceOnly": True,
        "info": {"type": "STOP_MARKET"}, "triggerPrice": 590.0,
    }
    with patch("sync_engine.protection.db") as mock_db, \
         patch("sync_engine.protection.send_telegram", new=AsyncMock()), \
         patch("sync_engine.protection.get_raw_order_type", return_value="STOP_MARKET"):
        exchange = _mock_exchange(open_orders=[existing_sl])
        await enforce_protection(
            pos=_base_pos(), pos_id=1, db_sym="BNB/USDT", direction="LONG",
            exchange_qty=0.5, sl_price=590.0, tp1_price=610.0, tp2_price=630.0,
            sl_found=False, tp1_found=True, tp2_found=True, tp1_hit=False,
            exchange=exchange, config={},
            to_futures_symbol=lambda s: f"{s}:USDT",
            get_stop_price=lambda o: o.get("triggerPrice"),
        )
        exchange.create_order.assert_not_called()
        mock_db.reset_sync_failure.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_failure_increments_and_sets_cooldown_at_threshold():
    with patch("sync_engine.protection.db") as mock_db, \
         patch("sync_engine.protection.send_telegram", new=AsyncMock()):
        mock_db.increment_sync_failure.return_value = 3  # atteint le seuil
        exchange = _mock_exchange(create_side_effect=Exception("network error"))
        await enforce_protection(
            pos=_base_pos(sl_sync_failures=2), pos_id=1, db_sym="BNB/USDT", direction="LONG",
            exchange_qty=0.5, sl_price=590.0, tp1_price=610.0, tp2_price=630.0,
            sl_found=False, tp1_found=True, tp2_found=True, tp1_hit=False,
            exchange=exchange, config={},
            to_futures_symbol=lambda s: f"{s}:USDT",
            get_stop_price=lambda o: o.get("triggerPrice"),
        )
        mock_db.set_sl_cooldown.assert_called_once()
        call_labels = [c.args[1] for c in mock_db.insert_reconcile_log.call_args_list]
        assert "SL_CIRCUIT_BREAKER" in call_labels
