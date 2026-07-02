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
        from unittest.mock import MagicMock, patch
        config = get_config()
        config["risk"]["trailing_sl_enabled"] = True
        config["risk"]["trailing_sl_activation_tp"] = 0
        config["risk"]["trailing_sl_atr_mult"] = 2.0
        trade_manager.db = MagicMock()
        async def mock_send_telegram(x, cfg):
            pass
        trade_manager.send_telegram = mock_send_telegram
        # Patcher les noms locaux dans trade_manager (créés par from X import Y)
        trade_manager.init_exchange_async = mock_init_exchange_async
        trade_manager.fetch_all_async = mock_fetch_all_async
        trade_manager.compute_indicators = mock_compute_indicators
        trade_manager.clean_ohlcv = lambda df: df  # passthrough identity
        
        # Initial position
        pos = {
            "id": 1,
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "entry": 100.0,
            "sl": 80.0,
            "tp1": 300.0, # Increased to avoid hitting TP1 before Trailing SL
            "tp2": 1000.0, 
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

    async def test_trailing_sl_logic_short(self):
        """Cas SHORT symétrique : le SL doit descendre avec le prix et déclencher
        la fermeture quand le prix remonte au-dessus du SL trailing."""
        import trade_manager
        from unittest.mock import MagicMock

        config = get_config()
        config["risk"]["trailing_sl_enabled"] = True
        config["risk"]["trailing_sl_activation_tp"] = 0
        config["risk"]["trailing_sl_atr_mult"] = 2.0

        trade_manager.db = MagicMock()

        async def mock_send_telegram(x, cfg):
            pass
        trade_manager.send_telegram = mock_send_telegram

        async def mock_fetch_short(exchange, symbols=None, use_cache=True):
            # Prix descendent de 200 à 100, dernier candle remonte à 125
            n = 100
            dates = pd.date_range(start="2023-01-01", periods=n, freq="4h", tz="UTC")
            prices = np.linspace(200, 100, n)
            prices[-1] = 125  # remontée qui doit toucher le SL
            df = pd.DataFrame({
                "timestamp": dates,
                "open": prices,
                "high": prices + 1,
                "low": prices - 1,
                "close": prices,
                "volume": [1000] * n,
                "atr": [10] * n
            })
            df["is_closed"] = True
            return {symbols[0]: df}

        trade_manager.init_exchange_async = mock_init_exchange_async
        trade_manager.fetch_all_async = mock_fetch_short
        trade_manager.compute_indicators = mock_compute_indicators
        trade_manager.clean_ohlcv = lambda df: df

        # Position SHORT : entrée à 200, SL initial à 220
        pos = {
            "id": 2,
            "symbol": "BTC/USDT",
            "direction": "SHORT",
            "entry": 200.0,
            "sl": 220.0,
            "tp1": 50.0,   # loin pour éviter TP1 avant trailing SL
            "tp2": 10.0,
            "quantity": 0.1,
            "entry_date": "2022-12-31T20:00:00Z",
            "status": "active",
            "partial_exit": False,
            "sl_order_id": "old_id_short"
        }

        # Prices descend jusqu'à 100 (n-2).
        # À close=100, SL = 100 + (10 * 2) = 120.
        # Au dernier candle, close=125, high=126 >= 120 → SL touché.

        updated_pos = await check_position(pos, config)

        self.assertEqual(updated_pos["status"], "closed")
        self.assertEqual(updated_pos["exit_reason"], "SL")
        self.assertLessEqual(updated_pos["exit_price"], 130.0)  # SL ≈ 120
        print(f"[SHORT] Final SL before exit: {updated_pos['sl']}")
        print(f"[SHORT] Exit Price: {updated_pos['exit_price']}")

if __name__ == "__main__":
    unittest.main()
