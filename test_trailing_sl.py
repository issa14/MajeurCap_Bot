import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from config_loader import get_config
from trade_manager import check_position

# Mocking the dependencies
class MockExchange:
    async def close(self):
        pass

async def mock_init_exchange_async():
    return MockExchange()

async def mock_fetch_all_async(exchange, symbols=None, use_cache=True):
    # Mock OHLCV data that moves up then down to hit trailing SL
    n = 100
    dates = pd.date_range(start="2023-01-01", periods=n, freq="4h", tz="UTC")
    
    # Prices go from 100 up to 200, then last one drops to 175
    prices = np.linspace(100, 200, n)
    prices[-1] = 175
    
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": prices + 1,
        "low": prices - 1,
        "close": prices,
        "volume": [1000] * n,
        "atr": [10] * n  # ATR of 10
    })
    df["is_closed"] = True
    return {symbols[0]: df}

def mock_compute_indicators(df, config, include_incomplete=True):
    # Already has ATR from mock_fetch
    df["rsi"] = 50
    df["ema_20"] = 110
    df["ema_50"] = 100
    df["ema_200"] = 90
    df["kc_upper"] = 120
    df["kc_mid"] = 110
    df["kc_lower"] = 100
    df["vol_ma20"] = 1000
    df["adx"] = 30
    df["above_ema200"] = True
    df["ema_bullish"] = True
    df["vol_surge"] = False
    return df

class TestTrailingSL(unittest.IsolatedAsyncioTestCase):

    async def test_trailing_sl_logic(self):
        import trade_manager
        config = get_config()
        # Inject mocks
        trade_manager.init_exchange_async = mock_init_exchange_async
        trade_manager.fetch_all_async = mock_fetch_all_async
        trade_manager.compute_indicators = mock_compute_indicators
        async def mock_send_telegram(x, cfg):
            print(f"TELEGRAM: {x}")
        trade_manager.send_telegram = mock_send_telegram
        
        # Initial position
        pos = {
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "entry": 100.0,
            "sl": 80.0,
            "tp1": 120.0,
            "tp2": 1000.0, # Very high TP2 to ensure we hit trailing SL
            "quantity": 0.1,
            "entry_date": "2022-12-31T20:00:00Z",
            "status": "active",
            "partial_exit": False,
            "sl_order_id": "old_id"
        }
        
        # Prices go up to 200 (at n-2). 
        # At price=200, SL should be 200 - (10 * 2) = 180.
        # At n-1, price drops to 175. This is <= 180, so it should hit SL.
        
        updated_pos = await check_position(pos, config)
        
        self.assertEqual(updated_pos["status"], "closed")
        self.assertEqual(updated_pos["exit_reason"], "SL")
        self.assertGreaterEqual(updated_pos["exit_price"], 170.0) # 180 should be the hit price
        print(f"Final SL before exit: {updated_pos['sl']}")
        print(f"Exit Price: {updated_pos['exit_price']}")

if __name__ == "__main__":
    unittest.main()
