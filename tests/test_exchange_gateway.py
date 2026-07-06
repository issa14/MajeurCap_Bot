"""Tests de exchange.gateway avec un exchange ccxt mocké (aucun appel réseau)."""

import pytest
from unittest.mock import AsyncMock

from exchange.gateway import ExchangeGateway
from tests.fixtures_ccxt import order_ccxt_4_5_64_stop_market


@pytest.mark.asyncio
async def test_fetch_open_orders_converts_to_typed_orders():
    mock_exchange = AsyncMock()
    mock_exchange.fetch_open_orders.return_value = [order_ccxt_4_5_64_stop_market()]

    gateway = ExchangeGateway(mock_exchange)
    orders = await gateway.fetch_open_orders("BNB/USDT")

    assert len(orders) == 1
    assert orders[0].raw_type == "STOP_MARKET"
    assert orders[0].stop_price == 590.5
    mock_exchange.fetch_open_orders.assert_called_once_with("BNB/USDT:USDT")


@pytest.mark.asyncio
async def test_fetch_order_if_exists_returns_none_on_404():
    mock_exchange = AsyncMock()
    mock_exchange.fetch_order.side_effect = Exception("Order not found (-2013)")

    gateway = ExchangeGateway(mock_exchange)
    result = await gateway.fetch_order_if_exists("999", "BNB/USDT")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_order_if_exists_returns_none_if_no_id():
    gateway = ExchangeGateway(AsyncMock())
    result = await gateway.fetch_order_if_exists(None, "BNB/USDT")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_order_if_exists_typed_result():
    mock_exchange = AsyncMock()
    mock_exchange.fetch_order.return_value = order_ccxt_4_5_64_stop_market()

    gateway = ExchangeGateway(mock_exchange)
    result = await gateway.fetch_order_if_exists("123456", "BNB/USDT")

    assert result is not None
    assert result.raw_type == "STOP_MARKET"