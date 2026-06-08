import unittest
import pandas as pd
import numpy as np
from module2_AT import clean_ohlcv, compute_indicators, compute_daily_trend

class TestIndicators(unittest.TestCase):

    def create_mock_ohlcv(self, n=300):
        """Creates a minimal OHLCV DataFrame."""
        df = pd.DataFrame({
            "timestamp": pd.date_range(start="2023-01-01", periods=n, freq="4h"),
            "open": np.random.uniform(100, 200, n),
            "high": np.random.uniform(100, 200, n),
            "low": np.random.uniform(100, 200, n),
            "close": np.random.uniform(100, 200, n),
            "volume": np.random.uniform(100, 500, n)
        })
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
        return df

    def test_clean_ohlcv(self):
        df = self.create_mock_ohlcv(100)
        # Inject some bad data
        df.loc[0, "volume"] = 0
        df.loc[1, "high"] = 50
        df.loc[1, "low"] = 100 # high < low
        
        cleaned = clean_ohlcv(df)
        self.assertLess(len(cleaned), len(df))
        self.assertTrue((cleaned["volume"] > 0).all())
        self.assertTrue((cleaned["high"] >= cleaned["low"]).all())

    def test_compute_indicators(self):
        from config_loader import get_config
        config = get_config()
        # We need enough data for EMA 200 (at least 200 rows)
        df = self.create_mock_ohlcv(300)
        df["is_closed"] = True
        
        enriched = compute_indicators(df, config)
        
        required_cols = [
            "ema_20", "ema_50", "ema_200", "rsi", "atr", 
            "kc_upper", "kc_mid", "kc_lower", "adx",
            "above_ema200", "ema_bullish", "vol_surge"
        ]
        for col in required_cols:
            self.assertIn(col, enriched.columns)
            self.assertFalse(enriched[col].isnull().any(), f"Column {col} has NaNs")

    def test_compute_daily_trend(self):
        df = self.create_mock_ohlcv(300)
        # Force a bullish trend
        df["close"] = np.linspace(100, 200, 300)
        
        trend_info = compute_daily_trend(df, strict=True)
        # With linear increase, EMA 20 > EMA 50 > EMA 200 and price > EMA 200
        self.assertEqual(trend_info["trend"], "bullish")

if __name__ == "__main__":
    unittest.main()
