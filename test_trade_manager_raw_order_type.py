import trade_manager


def test_get_raw_order_type_prefers_binance_info_type_for_futures():
    order = {"type": "market", "info": {"type": "STOP_MARKET"}}
    assert trade_manager._get_raw_order_type(order) == "stop_market"


def test_get_raw_order_type_falls_back_to_unified_type_when_info_missing():
    order = {"type": "take_profit"}
    assert trade_manager._get_raw_order_type(order) == "take_profit"
