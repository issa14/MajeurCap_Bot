"""
Module 3 — Détection signal v6 (Injection de dépendances)
"""

import pandas as pd
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ─── Fibonacci basé sur les swings ────────────────────────────────────────────
def compute_fibonacci_from_swings(df: pd.DataFrame) -> dict:
    pivot_rows = df[df["pivot"] != 0]
    if len(pivot_rows) < 2:
        return {}

    last_pivots = pivot_rows.tail(2)
    if last_pivots.iloc[0]["pivot"] == last_pivots.iloc[1]["pivot"]:
        if len(pivot_rows) >= 3:
            for j in range(len(pivot_rows)-2, -1, -1):
                if pivot_rows.iloc[j]["pivot"] != last_pivots.iloc[1]["pivot"]:
                    last_pivots = pd.DataFrame([pivot_rows.iloc[j], last_pivots.iloc[1]])
                    break
            else: return {}
        else: return {}

    p1, p2 = last_pivots.iloc[0], last_pivots.iloc[1]
    trend = "bearish" if p1["pivot"] == 1 else "bullish"
    swing_high, swing_low = (p1["high"], p2["low"]) if trend == "bearish" else (p2["high"], p1["low"])

    diff = swing_high - swing_low
    if diff <= 0: return {}

    levels = {
        "0.0": swing_low if trend == "bullish" else swing_high,
        "0.382": round(swing_low + 0.382 * diff, 8) if trend == "bullish" else round(swing_high - 0.382 * diff, 8),
        "0.5":   round(swing_low + 0.5 * diff, 8)   if trend == "bullish" else round(swing_high - 0.5 * diff, 8),
        "0.618": round(swing_low + 0.618 * diff, 8) if trend == "bullish" else round(swing_high - 0.618 * diff, 8),
        "1.0":   swing_high if trend == "bullish" else swing_low,
    }
    return {"swing_high": round(swing_high, 8), "swing_low": round(swing_low, 8), "trend": trend, "levels": levels}

def detect_structure(df: pd.DataFrame, config: dict) -> dict:
    pivot_rows = df[df["pivot"] != 0].copy()
    min_pivots = config.get("signal", {}).get("min_pivots", 4)

    result = {"bos": None, "choch": None, "trend": "ranging", "last_high": None, "last_low": None, "pivots_count": len(pivot_rows)}
    if len(pivot_rows) < min_pivots: return result

    highs = pivot_rows[pivot_rows["pivot"] ==  1]["high"].values
    lows  = pivot_rows[pivot_rows["pivot"] == -1]["low"].values
    result["last_high"] = float(highs[-1]) if len(highs) > 0 else None
    result["last_low"]  = float(lows[-1])  if len(lows)  > 0 else None
    
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]: result["trend"] = "bullish"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]: result["trend"] = "bearish"

    close = df["close"].iloc[-1]
    if result["trend"] == "bullish":
        if close > highs[-2]:
            result["bos"] = "bullish"
        if close < lows[-1]:
            result["choch"] = "bearish"
    elif result["trend"] == "bearish":
        if close < lows[-2]:
            result["bos"] = "bearish"
        if close > highs[-1]:
            result["choch"] = "bullish"
    
    return result

def check_confluences(df, fibo, structure, direction, config: dict) -> list:
    last, close = df.iloc[-1], df.iloc[-1]["close"]
    sig_cfg = config.get("signal", {})
    rsi_long = tuple(sig_cfg.get("rsi_long_zone", [30, 45]))
    rsi_short = tuple(sig_cfg.get("rsi_short_zone", [55, 70]))
    kc_enabled = sig_cfg.get("kc_filter", True)

    # Fibo proximity dynamique basé sur ATR% (évite faux positifs sur BTC/ETH)
    atr_pct = last["atr"] / close * 100
    fibo_prox_max = sig_cfg.get("fibo_proximity_pct_max", 1.0)
    fibo_prox = min(atr_pct * sig_cfg.get("fibo_proximity_atr_mult", 0.5), fibo_prox_max)

    c = []
    if direction == "long":
        if structure.get("bos") == "bullish": c.append("BOS haussier")
        if structure.get("choch") == "bullish": c.append("CHoCH haussier (retournement)")
    else:
        if structure.get("bos") == "bearish": c.append("BOS baissier")
        if structure.get("choch") == "bearish": c.append("CHoCH baissier (retournement)")

    if fibo and "levels" in fibo:
        for k, lp in fibo["levels"].items():
            if abs(close - lp) / close * 100 <= fibo_prox:
                c.append(f"Niveau Fibo {k} ({lp:.4f})")
                break

    rsi = last["rsi"]
    if direction == "long" and rsi_long[0] <= rsi <= rsi_long[1]: c.append(f"RSI neutre-bas ({rsi:.1f})")
    elif direction == "short" and rsi_short[0] <= rsi <= rsi_short[1]: c.append(f"RSI neutre-haut ({rsi:.1f})")

    if kc_enabled:
        if direction == "long" and last["kc_lower"] <= close <= last["kc_mid"]: c.append("Dans KC (zone basse)")
        elif direction == "short" and last["kc_mid"] <= close <= last["kc_upper"]: c.append("Dans KC (zone haute)")

    if direction == "long":
        if last.get("ema_bullish"): c.append("EMA 20 > EMA 50")
        if last.get("above_ema200"): c.append("Au-dessus EMA 200")
    else:
        if not last.get("ema_bullish"): c.append("EMA 20 < EMA 50")
        if not last.get("above_ema200"): c.append("En-dessous EMA 200")

    if last.get("vol_surge"): c.append("Volume élevé")
    return c

def compute_levels(close, atr, direction, config: dict) -> dict:
    sig_cfg = config.get("signal", {})
    sl_mult = sig_cfg.get("sl_atr_mult", 1.5)
    tp1_rr = sig_cfg.get("tp1_rr", 1.5)
    tp2_rr = sig_cfg.get("tp2_rr", 2.5)

    sl_dist = atr * sl_mult
    if direction == "long":
        sl, tp1, tp2 = close - sl_dist, close + sl_dist * tp1_rr, close + sl_dist * tp2_rr
    else:
        sl, tp1, tp2 = close + sl_dist, close - sl_dist * tp1_rr, close - sl_dist * tp2_rr
    
    return {"entry": round(close, 6), "sl": round(sl, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6), 
            "sl_pct": round(sl_dist / close * 100, 2), "rr1": tp1_rr, "rr2": tp2_rr}

def generate_signal(symbol: str, df: pd.DataFrame, config: dict, daily_trend: Optional[dict] = None) -> Optional[dict]:
    sig_cfg = config.get("signal", {})
    adx_threshold = sig_cfg.get("adx_threshold", 20)
    adx_required = sig_cfg.get("adx_required", False)
    min_conf = sig_cfg.get("min_confluences", 3)
    min_conf_no_str = sig_cfg.get("min_confluences_no_struct", 4)
    min_pivots = sig_cfg.get("min_pivots", 4)

    if adx_required and df.iloc[-1].get("adx", 0) < adx_threshold: return None
    if daily_trend and daily_trend.get("trend") == "neutral": return None

    # Performance Fix: Use pre-computed pivots if available (avoid O(n^2) in backtest)
    if "pivot" in df.columns:
        df_z = df
    else:
        # Fallback pour le live si non inclus dans compute_indicators
        from module2_AT import compute_zigzag
        df_z = compute_zigzag(df, config)

    fibo = compute_fibonacci_from_swings(df_z)
    structure = detect_structure(df_z, config)
    threshold = min_conf if structure["pivots_count"] >= min_pivots else min_conf_no_str

    spot_only = config.get("execution", {}).get("spot_only", False)

    best_signal = None
    best_confluence_count = 0
    for direction in ["long", "short"]:
        # En mode spot_only, les SHORT sont impossibles (pas de vente à découvert)
        if spot_only and direction == "short":
            continue

        if daily_trend:
            if direction == "long" and daily_trend["trend"] != "bullish": continue
            if direction == "short" and daily_trend["trend"] != "bearish": continue

        confluences = check_confluences(df, fibo, structure, direction, config)
        if len(confluences) >= threshold and len(confluences) > best_confluence_count:
            levels = compute_levels(df.iloc[-1]["close"], df.iloc[-1]["atr"], direction, config)
            best_signal = {"symbol": symbol, "direction": direction.upper(), "confluences": confluences, 
                           "structure": structure, "fibo": fibo, "threshold": threshold, "atr": df.iloc[-1]["atr"], 
                           "adx": df.iloc[-1].get("adx", 0), **levels}
            best_confluence_count = len(confluences)

    return best_signal

def scan_all(analyzed: dict, config: dict) -> list:
    signals = []
    for symbol, res in analyzed.items():
        sig = generate_signal(symbol, res["df"], config, daily_trend=res.get("daily_trend"))
        if sig: signals.append(sig)
    return sorted(signals, key=lambda s: len(s["confluences"]), reverse=True)
