import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Import the core components to test
import bot_telegram
import trade_manager
import module1_data_v3
import execution
import module3_signal
import module2_AT

class TestIntegrationBot(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Override DB path for testing to isolate database operations
        from database import DatabaseManager
        import database
        self.db_file = Path("test_trading_bot.db")
        if self.db_file.exists():
            self.db_file.unlink()
        database.db = DatabaseManager(self.db_file)
        trade_manager.db = database.db

        # Setup temporary positions file
        self.positions_file = Path("test_positions.json")
        if self.positions_file.exists():
            self.positions_file.unlink()
        
        # Override trade_manager positions file
        trade_manager.POSITIONS_FILE = self.positions_file
        
        # Mock Telegram to avoid network calls
        self.telegram_patcher = patch("bot_telegram.send_telegram")
        self.mock_send_telegram = self.telegram_patcher.start()
        
        self.tm_telegram_patcher = patch("trade_manager.send_telegram")
        self.mock_tm_send_telegram = self.tm_telegram_patcher.start()

    async def asyncTearDown(self):
        if self.positions_file.exists():
            self.positions_file.unlink()
        if hasattr(self, 'db_file') and self.db_file.exists():
            try:
                self.db_file.unlink()
            except Exception:
                pass
        self.telegram_patcher.stop()
        self.tm_telegram_patcher.stop()

    def create_mock_data(self, n=300):
        """Creates mock OHLCV data that will trigger a LONG signal."""
        start_date = datetime.now(timezone.utc) - timedelta(days=50)
        dates = pd.date_range(start=start_date, periods=n, freq="4h", tz="UTC")
        
        prices = np.linspace(100, 150, n)
        prices[100] = 50   # L
        prices[150] = 200  # H
        prices[200] = 120  # HL
        prices[250] = 250  # HH
        prices[299] = 170 
        
        df = pd.DataFrame({
            "timestamp": dates,
            "open": prices,
            "high": prices + 1,
            "low": prices - 1,
            "close": prices,
            "volume": [1000] * n,
            "atr": [10] * n,
            "rsi": [40] * n,
            "adx": [35] * n,
            "ema_20": [150] * n,
            "ema_50": [130] * n,
            "ema_200": [100] * n,
            "kc_upper": [220] * n,
            "kc_mid": [200] * n,
            "kc_lower": [140] * n,
            "above_ema200": [True] * n,
            "ema_bullish": [True] * n,
            "vol_surge": [False] * n,
        })
        df["is_closed"] = True
        return df

    @patch("bot_telegram.init_exchange_async")
    @patch("bot_telegram.fetch_all_async")
    @patch("bot_telegram.fetch_daily_all_async")
    @patch("trade_manager.fetch_all_async")
    @patch("trade_manager.compute_indicators")
    @patch("bot_telegram.analyze_all")
    @patch("execution.init_trading_exchange")
    @patch("bot_telegram.reload_config")
    @patch("bot_telegram.get_config")
    @patch("trade_manager.get_config")
    async def test_full_scan_loop(self, mock_tm_get_config, mock_bot_get_config, mock_bot_reload_config, mock_exec_init, mock_bot_analyze, mock_tm_indicators, mock_tm_fetch, mock_fetch_daily, mock_fetch, mock_init):
        # 1. Setup Mocks
        mock_exchange = AsyncMock()
        mock_init.return_value = mock_exchange
        
        mock_exec_exchange = AsyncMock()
        mock_exec_init.return_value = mock_exec_exchange
        
        mock_data = self.create_mock_data()
        mock_fetch.return_value = {"BTC/USDT": mock_data}
        mock_tm_fetch.return_value = {"BTC/USDT": mock_data}
        mock_tm_indicators.return_value = mock_data
        mock_bot_analyze.return_value = {"BTC/USDT": {"df": mock_data, "indicators_ok": True, "daily_trend": None}}
        mock_fetch_daily.return_value = {"BTC/USDT": pd.DataFrame({"trend": ["bullish"]})}
        
        mock_exec_exchange.create_market_order.return_value = {"id": "order_123", "status": "closed"}
        mock_exec_exchange.create_order.return_value = {"id": "sl_123", "status": "open"}
        
        # Use MagicMock for synchronous methods of the AsyncMock exchange
        mock_exec_exchange.amount_to_precision = unittest.mock.MagicMock(return_value="0.001")
        mock_exec_exchange.price_to_precision = unittest.mock.MagicMock(return_value="100.0")
        mock_exec_exchange.market = unittest.mock.MagicMock(return_value={"precision": {"amount": 8, "price": 8}})
        
        mock_config = {
            "watchlist": ["BTC/USDT"],
            "telegram": {"token": "fake", "chat_id": "fake"},
            "execution": {"auto_execute": True},
            "risk": {
                "capital": 1000,
                "risk_per_trade": 1.0,
                "max_positions": 5,
                "max_exposure": 100.0,
                "trailing_sl_enabled": True,
                "trailing_sl_activation_tp": 1,
                "trailing_sl_atr_mult": 2.0
            },
            "signal": {
                "daily_filter_enabled": False,
                "kc_filter": False,
                "min_confluences": 1,
                "min_confluences_no_struct": 1,
                "adx_required": False,
                "tp2_rr": 1000.0,
                "min_pivots": 4
            },
            "cache_ttl_minutes": 0
        }
        
        mock_tm_get_config.return_value = mock_config
        mock_bot_get_config.return_value = mock_config
        mock_bot_reload_config.return_value = mock_config
        
        # ─── SCAN 1: Ouverture ───
        await bot_telegram.run_scan_cycle()
        self.assertTrue(self.mock_send_telegram.called)
        positions = trade_manager.load_positions()
        self.assertEqual(len(positions), 1)
        
        # ─── SCAN 2: TP1 + Trailing ───
        # Dans le flux réel, main() appelle manage_positions() ET run_scan_cycle() à chaque cycle.
        # C'est manage_positions() → check_position() qui met à jour le trailing SL.
        # Le test doit reproduire ce flux explicitement.
        new_timestamp = datetime.now(timezone.utc) + timedelta(minutes=10)
        new_candle = mock_data.iloc[-1].copy()
        new_candle["timestamp"] = new_timestamp
        new_candle["close"] = 210 # Trails to 190 (210 - 10*2 = 190)
        new_candle["high"] = 215
        new_candle["low"] = 205
        new_data = pd.concat([mock_data, pd.DataFrame([new_candle])], ignore_index=True)

        mock_fetch.return_value = {"BTC/USDT": new_data}
        mock_tm_fetch.return_value = {"BTC/USDT": new_data}
        mock_tm_indicators.return_value = new_data
        mock_bot_analyze.return_value = {"BTC/USDT": {"df": new_data, "indicators_ok": True, "daily_trend": None}}

        # manage_positions() → check_position() → trailing SL mis à jour
        # On mocke sync_all et init_exchange_async dans trade_manager pour éviter les appels réseau
        with patch("trade_manager.sync_all"), \
             patch("trade_manager.init_exchange_async", return_value=mock_exchange):
            await trade_manager.manage_positions()

        updated_positions = trade_manager.load_positions()
        self.assertEqual(len(updated_positions), 1)
        self.assertEqual(updated_positions[0]["sl"], 190.0)

        # ─── SCAN 3: Sortie SL ───
        final_timestamp = datetime.now(timezone.utc) + timedelta(minutes=20)
        final_candle = new_candle.copy()
        final_candle["timestamp"] = final_timestamp
        final_candle["close"] = 180 # Hits SL (190)
        final_candle["high"] = 185
        final_candle["low"] = 175
        final_data = pd.concat([new_data, pd.DataFrame([final_candle])], ignore_index=True)

        mock_fetch.return_value = {"BTC/USDT": final_data}
        mock_tm_fetch.return_value = {"BTC/USDT": final_data}
        mock_tm_indicators.return_value = final_data
        mock_bot_analyze.return_value = {"BTC/USDT": {"df": final_data, "indicators_ok": True, "daily_trend": None}}

        # manage_positions() → check_position() → SL touché → position fermée
        with patch("trade_manager.sync_all"), \
             patch("trade_manager.init_exchange_async", return_value=mock_exchange), \
             patch("trade_manager.cancel_exchange_orders"):
            await trade_manager.manage_positions()

        final_positions = trade_manager.load_positions()
        self.assertEqual(len(final_positions), 0)

if __name__ == "__main__":
    unittest.main()
