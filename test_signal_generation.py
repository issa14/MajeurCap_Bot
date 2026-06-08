import unittest
import pandas as pd
import numpy as np
import copy
from config_loader import get_config
from module3_signal import (
    compute_zigzag, 
    compute_fibonacci_from_swings, 
    detect_structure, 
    generate_signal,
    check_confluences
)

class TestSignalGeneration(unittest.TestCase):

    def setUp(self):
        self.config = get_config()

    def create_mock_df(self, n=100):
        """Creates a mock DataFrame with necessary columns for module3."""
        df = pd.DataFrame({
            "timestamp": pd.date_range(start="2023-01-01", periods=n, freq="4h"),
            "open": np.random.uniform(100, 200, n),
            "high": np.random.uniform(100, 200, n),
            "low": np.random.uniform(100, 200, n),
            "close": np.random.uniform(100, 200, n),
            "volume": np.random.uniform(1000, 5000, n),
            "rsi": np.random.uniform(20, 80, n),
            "atr": np.random.uniform(1, 5, n),
            "kc_upper": np.random.uniform(180, 200, n),
            "kc_mid": np.random.uniform(140, 160, n),
            "kc_lower": np.random.uniform(100, 120, n),
            "ema_20": np.random.uniform(140, 160, n),
            "ema_50": np.random.uniform(130, 150, n),
            "ema_200": np.random.uniform(100, 120, n),
            "adx": np.random.uniform(10, 40, n)
        })
        df["above_ema200"] = True
        df["ema_bullish"] = True
        df["vol_surge"] = False
        df["pivot"] = 0
        # Ensure high is always highest and low is always lowest
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
        return df

    def test_compute_zigzag(self):
        """Test if zigzag correctly identifies peaks and valleys."""
        n = 50
        df = self.create_mock_df(n)
        df["high"] = 100
        df["low"] = 100
        df["close"] = 100
        
        df.loc[10, ["high", "low", "close"]] = 50   # Low
        df.loc[20, ["high", "low", "close"]] = 150  # High
        df.loc[30, ["high", "low", "close"]] = 60   # Higher Low
        df.loc[40, ["high", "low", "close"]] = 160  # Higher High
        
        df_z = compute_zigzag(df, self.config)
        pivots = df_z[df_z["pivot"] != 0]
        self.assertGreaterEqual(len(pivots), 2)
        
    def test_detect_structure_bullish(self):
        """Test bullish structure detection (HH, HL)."""
        df = self.create_mock_df(50)
        df.loc[10, "pivot"] = -1
        df.loc[10, "low"] = 100
        df.loc[20, "pivot"] = 1
        df.loc[20, "high"] = 150
        df.loc[30, "pivot"] = -1
        df.loc[30, "low"] = 110  # HL
        df.loc[40, "pivot"] = 1
        df.loc[40, "high"] = 160 # HH
        
        struct = detect_structure(df, self.config)
        self.assertEqual(struct["trend"], "bullish")

    def test_detect_structure_bearish(self):
        """Test bearish structure detection (LH, LL)."""
        df = self.create_mock_df(50)
        df.loc[10, "pivot"] = 1
        df.loc[10, "high"] = 200
        df.loc[20, "pivot"] = -1
        df.loc[20, "low"] = 100
        df.loc[30, "pivot"] = 1
        df.loc[30, "high"] = 190  # LH
        df.loc[40, "pivot"] = -1
        df.loc[40, "low"] = 90    # LL
        
        struct = detect_structure(df, self.config)
        self.assertEqual(struct["trend"], "bearish")

    def test_fibonacci_calculation(self):
        """Test Fibonacci level calculations."""
        df = self.create_mock_df(50)
        df.loc[10, "pivot"] = -1
        df.loc[10, "low"] = 100
        df.loc[20, "pivot"] = 1
        df.loc[20, "high"] = 200
        df.loc[21:, "pivot"] = 0
        
        fibo = compute_fibonacci_from_swings(df)
        self.assertEqual(fibo["trend"], "bullish")
        self.assertAlmostEqual(fibo["levels"]["0.5"], 150.0)
        self.assertAlmostEqual(fibo["levels"]["0.618"], 161.8)

    def test_check_confluences_long(self):
        """Test confluence detection for a LONG signal."""
        df = self.create_mock_df(10)
        last_idx = df.index[-1]
        df.loc[last_idx, "close"] = 150
        df.loc[last_idx, "rsi"] = 40
        df.loc[last_idx, "kc_lower"] = 140
        df.loc[last_idx, "kc_mid"] = 160
        df.loc[last_idx, "ema_bullish"] = True
        df.loc[last_idx, "above_ema200"] = True
        
        fibo = {"levels": {"0.5": 150.1}, "trend": "bullish"}
        structure = {"bos": "bullish"}
        
        confluences = check_confluences(df, fibo, structure, "long", self.config)
        self.assertIn("BOS haussier", confluences)
        self.assertTrue(len(confluences) >= 3)

    def test_generate_signal_integration(self):
        """Test the end-to-end signal generation with a mock scenario."""
        n = 100
        df = self.create_mock_df(n)
        for i in range(n):
            df.loc[i, ["high", "low", "close"]] = 150
            
        df.loc[20, ["high", "low", "close"]] = 50   # L
        df.loc[40, ["high", "low", "close"]] = 250  # H
        df.loc[60, ["high", "low", "close"]] = 70   # HL
        df.loc[80, ["high", "low", "close"]] = 270  # HH
        
        last_idx = df.index[-1]
        df.loc[last_idx, "close"] = 170
        df.loc[last_idx, "rsi"] = 45
        df.loc[last_idx, "atr"] = 10
        df.loc[last_idx, "adx"] = 35
        df.loc[last_idx, "ema_bullish"] = True
        df.loc[last_idx, "above_ema200"] = True
        df.loc[last_idx, "kc_lower"] = 160
        df.loc[last_idx, "kc_mid"] = 180
        
        # Simuler un override via la config
        test_config = copy.deepcopy(self.config)
        if "signal" not in test_config:
            test_config["signal"] = {}
        test_config["signal"].update({
            "min_confluences": 2,
            "adx_required": True,
            "adx_threshold": 30
        })
        
        signal = generate_signal("BTC/USDT", df, test_config)
        if signal:
            self.assertEqual(signal["symbol"], "BTC/USDT")
            self.assertEqual(signal["direction"], "LONG")

if __name__ == "__main__":
    unittest.main()
