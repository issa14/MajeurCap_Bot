"""Tests de sync_engine.reconciliation.reconcile_sl_tp_orders avec exchange mocké."""

import pytest
from unittest.mock import AsyncMock

from sync_engine.reconciliation import reconcile_sl_tp_orders


def _identity_helpers():
    return dict(
        to_futures_symbol=lambda s: f"{s}:USDT",
        get_stop_price=lambda o: o.get("triggerPrice"),
        normalize_symbol=lambda s: s.split(":")[0],
    )


@pytest.mark.asyncio
async def test_sl_found_via_tier1_by_id():
    exchange = AsyncMock()
    exchange.fetch_order.return_value = {
        "id": "123", "status": "open", "triggerPrice": 590.0,
    }
    pos = {"sl_order_id": "123"}
    result = await reconcile_sl_tp_orders(
        pos=pos, db_sym="BNB/USDT", norm_sym="BNB/USDT",
        sl_price=590.0, tp1_price=0, tp2_price=0, exchange_qty=0.5,
        all_open_orders=[], exchange=exchange, **_identity_helpers(),
    )
    assert result["sl_found"] is True
    assert result["sl_order_id_to_save"] == "123"


@pytest.mark.asyncio
async def test_sl_found_via_tier2_price_fallback_when_id_missing():
    exchange = AsyncMock()
    exchange.fetch_order.side_effect = Exception("not found")
    open_orders = [{
        "id": "999", "symbol": "BNB/USDT:USDT", "amount": 0.5,
        "reduceOnly": True, "triggerPrice": 590.5,
        "info": {"type": "STOP_MARKET"},
    }]
    pos = {"sl_order_id": "123"}  # ID en DB mais introuvable sur l'exchange
    result = await reconcile_sl_tp_orders(
        pos=pos, db_sym="BNB/USDT", norm_sym="BNB/USDT",
        sl_price=590.0, tp1_price=0, tp2_price=0, exchange_qty=0.5,
        all_open_orders=open_orders, exchange=exchange, **_identity_helpers(),
    )
    assert result["sl_found"] is True
    assert result["sl_order_id_to_save"] == "999"


@pytest.mark.asyncio
async def test_tp1_hit_skips_lookup_entirely():
    """Si tp1_status == FILLED en DB, on ne doit même pas tenter de le chercher."""
    exchange = AsyncMock()
    pos = {"tp1_order_id": "456", "tp1_status": "FILLED"}
    result = await reconcile_sl_tp_orders(
        pos=pos, db_sym="BNB/USDT", norm_sym="BNB/USDT",
        sl_price=0, tp1_price=610.0, tp2_price=0, exchange_qty=0.5,
        all_open_orders=[], exchange=exchange, **_identity_helpers(),
    )
    assert result["tp1_hit"] is True
    assert result["tp1_found"] is False
    exchange.fetch_order.assert_not_called()


@pytest.mark.asyncio
async def test_nothing_found_returns_all_false():
    exchange = AsyncMock()
    exchange.fetch_order.side_effect = Exception("not found")
    pos = {}
    result = await reconcile_sl_tp_orders(
        pos=pos, db_sym="BNB/USDT", norm_sym="BNB/USDT",
        sl_price=590.0, tp1_price=610.0, tp2_price=630.0, exchange_qty=0.5,
        all_open_orders=[], exchange=exchange, **_identity_helpers(),
    )
    assert result["sl_found"] is False
    assert result["tp1_found"] is False
    assert result["tp2_found"] is False
