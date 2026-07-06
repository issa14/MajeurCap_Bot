"""Tests de exchange.normalize — chaque test référence le bug qu'il couvre."""

import pytest

from exchange.normalize import (
    get_stop_price,
    get_raw_order_type,
    to_exchange_order,
    normalize_symbol,
    to_futures_symbol,
)
from tests.fixtures_ccxt import (
    order_ccxt_4_5_64_stop_market,
    order_legacy_stop_price_only,
    order_malformed_no_price,
    order_take_profit_market,
)


class TestGetStopPrice:
    def test_bug1_triggerprice_ccxt_4_5_64(self):
        """Bug #1 : stopPrice=None, doit retomber sur triggerPrice."""
        order = order_ccxt_4_5_64_stop_market()
        assert get_stop_price(order) == 590.5

    def test_legacy_stop_price_key(self):
        order = order_legacy_stop_price_only()
        assert get_stop_price(order) == 61000.0

    def test_malformed_order_returns_none(self):
        """Aucun prix exploitable → None, PAS 0.0 (échec silencieux interdit)."""
        order = order_malformed_no_price()
        assert get_stop_price(order) is None


class TestGetRawOrderType:
    def test_bug2_type_collapsed_to_market(self):
        """Bug #2 : order['type']='market' mais info.type='STOP_MARKET'."""
        order = order_ccxt_4_5_64_stop_market()
        assert order["type"] == "market"  # confirme le piège existe bien
        assert get_raw_order_type(order) == "STOP_MARKET"

    def test_take_profit_market_type(self):
        order = order_take_profit_market()
        assert get_raw_order_type(order) == "TAKE_PROFIT_MARKET"

    def test_missing_info_type_returns_none(self):
        order = {"id": "1", "info": {}}
        assert get_raw_order_type(order) is None


class TestToExchangeOrder:
    def test_full_conversion_sl(self):
        eo = to_exchange_order(order_ccxt_4_5_64_stop_market())
        assert eo.id == "123456"
        assert eo.raw_type == "STOP_MARKET"
        assert eo.stop_price == 590.5
        assert eo.reduce_only is True

    def test_missing_price_defaults_to_zero_not_none(self):
        """to_exchange_order() retourne 0.0 (compat tolérance existante),
        contrairement à get_stop_price() qui retourne None."""
        eo = to_exchange_order(order_malformed_no_price())
        assert eo.stop_price == 0.0


class TestSymbolConversion:
    def test_normalize_strips_futures_suffix(self):
        assert normalize_symbol("BNB/USDT:USDT") == "BNB/USDT"

    def test_normalize_passthrough_if_no_suffix(self):
        assert normalize_symbol("BNB/USDT") == "BNB/USDT"

    def test_to_futures_adds_suffix(self):
        assert to_futures_symbol("BNB/USDT") == "BNB/USDT:USDT"

    def test_to_futures_idempotent(self):
        assert to_futures_symbol("BNB/USDT:USDT") == "BNB/USDT:USDT"