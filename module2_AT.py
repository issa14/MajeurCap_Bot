"""
Module 2 — Analyse technique v4 (Injection de dépendances)
"""

import pandas as pd
import numpy as np
import ta
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ─── Zigzag alternant (Causal - Sans look-ahead) ──────────────────────────────
def compute_zigzag(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Calcule le zigzag de manière causale.
    Un pivot à l'index 'i' est identifié à l'instant 'i + window'.
    """
    df = df.copy()
    
    sig_cfg = config.get("signal", {})
    window = sig_cfg.get("zigzag_window", 5)
    min_diff_pct = sig_cfg.get("min_swing_diff_pct", 0.5)

    # 1. Identification causale des candidats pivots
    # Un point est un max/min local s'il est le plus haut/bas sur [i-window, i+window]
    # Pour être causal à l'instant 't', on regarde si t-window était un extremum sur [t-2*window, t]
    df['is_max'] = df['high'].rolling(window=window*2+1).apply(lambda x: x[window] == max(x), raw=True) == 1
    df['is_min'] = df['low'].rolling(window=window*2+1).apply(lambda x: x[window] == min(x), raw=True) == 1
    
    # On décale pour enregistrer le pivot à son index de occurrence réel (t - window)
    raw_pivots = np.zeros(len(df), dtype=int)
    for idx in df[df['is_max']].index:
        if idx >= window:
            raw_pivots[idx - window] = 1
            
    for idx in df[df['is_min']].index:
        if idx >= window:
            raw_pivots[idx - window] = -1
    
    # 2. Filtrage alternance (reste identique mais travaille sur des pivots retardés)
    pivots = np.zeros(len(df), dtype=int)
    last_pivot_type = 0
    last_pivot_price = None
    last_pivot_idx = None

    candidate_indices = np.where(raw_pivots != 0)[0]
    
    for i in candidate_indices:
        current_type = raw_pivots[i]
        # On récupère le prix à l'index réel du pivot (i est déjà l'index du pivot car identifié par rolling)
        current_price = df.iloc[i]["high"] if current_type == 1 else df.iloc[i]["low"]

        if last_pivot_type == 0:
            pivots[i] = current_type
            last_pivot_type, last_pivot_price, last_pivot_idx = current_type, current_price, i
        elif current_type != last_pivot_type:
            if abs(current_price - last_pivot_price) / last_pivot_price * 100 >= min_diff_pct:
                pivots[i] = current_type
                last_pivot_type, last_pivot_price, last_pivot_idx = current_type, current_price, i
        else:
            # Même type : on garde le plus extrême
            if (current_type == 1 and current_price > last_pivot_price) or \
               (current_type == -1 and current_price < last_pivot_price):
                pivots[last_pivot_idx] = 0
                pivots[i] = current_type
                last_pivot_price, last_pivot_idx = current_price, i

    df["pivot"] = pivots
    return df.drop(columns=['is_max', 'is_min'])

# ─── Nettoyage ───────────────────────────────────────────────────────────────
def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f"Colonnes manquantes : {missing}")

    df = df.copy()
    before = len(df)
    df = df[df["volume"] > 0]
    df = df[df["high"] >= df["low"]]
    df = df.dropna(subset=["open", "high", "low", "close"])

    if len(df) < before:
        log.warning(f"Nettoyage : {before - len(df)} lignes supprimées")
    if len(df) < 50:
        log.error(f"Données insuffisantes après nettoyage ({len(df)} bougies)")
        return pd.DataFrame()
    return df.reset_index(drop=True)

# ─── Indicateurs ─────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame, config: dict, include_incomplete: bool = False) -> pd.DataFrame:
    """Calcule les indicateurs en utilisant l'objet config injecté."""
    df = df.copy()

    # Paramètres depuis la config
    ema_fast = config.get("ema_fast", 20)
    ema_mid  = config.get("ema_mid", 50)
    ema_slow = config.get("ema_slow", 200)
    atr_period = config.get("atr_period", 10)
    rsi_period = config.get("rsi_period", 14)
    kc_ema_period = config.get("kc_ema_period", 20)
    kc_atr_period = config.get("kc_atr_period", 10)
    kc_mult = config.get("kc_mult", 2.0)
    vol_ma_period = config.get("vol_ma_period", 20)
    vol_surge_mult = config.get("vol_surge_mult", 1.2)
    adx_period = config.get("adx_period", 14)

    # 1. Filtrage des bougies non closes
    if not include_incomplete and "is_closed" in df.columns:
        before = len(df)
        df = df[df["is_closed"]].copy()
        if len(df) < before:
            log.debug(f"{before - len(df)} bougie(s) non close(s) exclue(s)")

    if df.empty:
        log.warning("Aucune bougie close disponible.")
        return df

    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    df["ema_20"]  = ta.trend.ema_indicator(close, window=ema_fast)
    df["ema_50"]  = ta.trend.ema_indicator(close, window=ema_mid)
    df["ema_200"] = ta.trend.ema_indicator(close, window=ema_slow)
    df["atr"]     = ta.volatility.average_true_range(high, low, close, window=atr_period)
    df["rsi"]     = ta.momentum.rsi(close, window=rsi_period)

    kc = ta.volatility.KeltnerChannel(
        high, low, close,
        window=kc_ema_period,
        window_atr=kc_atr_period,
        multiplier=kc_mult,
    )
    df["kc_upper"] = kc.keltner_channel_hband()
    df["kc_mid"]   = kc.keltner_channel_mband()
    df["kc_lower"] = kc.keltner_channel_lband()
    df["vol_ma20"] = vol.rolling(window=vol_ma_period).mean()

    adx_obj = ta.trend.ADXIndicator(high, low, close, window=adx_period)
    df["adx"] = adx_obj.adx()

    required_indicators = ["rsi", "atr", "kc_upper", "vol_ma20", "adx", "ema_20", "ema_50"]
    df = df.dropna(subset=required_indicators).reset_index(drop=True)
    df["ema_200"] = df["ema_200"].ffill().bfill()

    df["above_ema200"] = df["close"] > df["ema_200"]
    df["ema_bullish"]  = df["ema_20"] > df["ema_50"]
    df["vol_surge"]    = df["volume"] > (df["vol_ma20"] * vol_surge_mult)

    # Ajout du zigzag
    df = compute_zigzag(df, config)

    log.debug(f"Indicateurs calculés : {len(df)} bougies conservées")
    return df

# ─── Tendance daily ──────────────────────────────────────────────────────────
def compute_daily_trend(df_daily: pd.DataFrame, strict: bool = True) -> dict:
    """Détermine la tendance journalière."""
    if df_daily.empty:
        return {"trend": "neutral", "reason": "no data"}

    df = df_daily.copy()
    close = df["close"]
    df["ema_20"] = ta.trend.ema_indicator(close, window=20)
    df["ema_50"] = ta.trend.ema_indicator(close, window=50)
    df["ema_200"] = ta.trend.ema_indicator(close, window=200)
    last = df.iloc[-1]

    if pd.isna(last["ema_200"]):
        return {"trend": "neutral", "reason": "EMA200 not available"}

    above_200 = last["close"] > last["ema_200"]
    ema_bullish = last["ema_20"] > last["ema_50"]

    if strict:
        if above_200 and ema_bullish:
            trend = "bullish"
        elif not above_200 and not ema_bullish:
            trend = "bearish"
        else:
            trend = "neutral"
    else:
        trend = "bullish" if above_200 else "bearish"

    return {
        "trend": trend,
        "close": last["close"],
        "ema_200": last["ema_200"],
        "ema_20": last["ema_20"],
        "ema_50": last["ema_50"],
    }

def get_daily_trend_at_timestamp(symbol: str, timestamp, daily_data: dict, config: dict) -> dict:
    if not daily_data or symbol not in daily_data:
        return {"trend": "neutral", "reason": "no daily data"}
    
    df_daily = daily_data[symbol]
    # Slice daily data causally: only keep candles with timestamp < current 4h timestamp
    df_daily_sub = df_daily[df_daily["timestamp"] < timestamp]
    if len(df_daily_sub) < 50:
        return {"trend": "neutral", "reason": "not enough daily candles"}

    # Lire depuis signal.* en priorité (injection backtest/scénario), fallback racine, défaut False
    daily_strict = config.get("signal", {}).get("daily_trend_strict", config.get("daily_trend_strict", False))

    # Global Market Filter based on BTC trend
    btc_symbol = next((s for s in daily_data.keys() if "BTC/" in s), "BTC/USDT")
    btc_trend = None
    if symbol != btc_symbol and btc_symbol in daily_data:
        df_btc = daily_data[btc_symbol]
        df_btc_sub = df_btc[df_btc["timestamp"] < timestamp]
        if len(df_btc_sub) >= 50:
            btc_trend = compute_daily_trend(df_btc_sub, strict=daily_strict)
            
    symbol_trend = compute_daily_trend(df_daily_sub, strict=daily_strict)
    
    # Global Market Filter for altcoins based on BTC trend
    if symbol != btc_symbol and btc_trend:
        if btc_trend["trend"] == "bearish" and symbol_trend["trend"] == "bullish":
            symbol_trend["trend"] = "neutral"
            symbol_trend["reason"] = "BTC is bearish"
        elif btc_trend["trend"] == "neutral" and daily_strict:
            symbol_trend["trend"] = "neutral"
            symbol_trend["reason"] = "BTC is neutral"
            
    return symbol_trend



def get_daily_structure_at_timestamp(symbol: str, timestamp, daily_data: dict, config: dict) -> dict:
    """
    Calcule la structure complète (zigzag, BOS/CHoCH, Fibonacci) sur le daily,
    de manière causale : seules les bougies daily strictement antérieures au
    timestamp 4h courant sont utilisées.

    Réutilise compute_zigzag (ce module) et detect_structure /
    compute_fibonacci_from_swings (module3_signal) — pas de duplication de logique,
    juste appliqué à un dataframe daily au lieu du 4h.
    """
    empty_result = {
        "structure": {"bos": None, "choch": None, "trend": "ranging", "last_high": None, "last_low": None, "pivots_count": 0},
        "fibo": {},
    }

    if not daily_data or symbol not in daily_data:
        return empty_result

    df_daily = daily_data[symbol]
    df_daily_sub = df_daily[df_daily["timestamp"] < timestamp].copy()
    if len(df_daily_sub) < 50:
        return empty_result

    # Import local pour éviter une dépendance circulaire module2 <-> module3
    from module3_signal import detect_structure, compute_fibonacci_from_swings

    df_daily_sub = df_daily_sub.reset_index(drop=True)
    df_daily_z = compute_zigzag(df_daily_sub, config)

    structure = detect_structure(df_daily_z, config)
    fibo = compute_fibonacci_from_swings(df_daily_z)

    return {"structure": structure, "fibo": fibo}

# ─── Batch ────────────────────────────────────────────────────────────────────
def analyze_all(data: dict, config: dict, include_incomplete: bool = False, daily_data: Optional[dict] = None) -> dict:
    """Analyse toutes les paires avec injection de config."""
    results = {}

    daily_filter_config = config.get("signal", {}).get("daily_filter_enabled", False)
    # Lire depuis signal.* en priorité (injection backtest/scénario), fallback racine, défaut False
    daily_strict = config.get("signal", {}).get("daily_trend_strict", config.get("daily_trend_strict", False))

    for symbol, df in data.items():
        try:
            clean_df = clean_ohlcv(df)
            if clean_df.empty:
                log.error(f"{symbol} — données invalides, ignorée")
                continue
            enriched = compute_indicators(clean_df, config, include_incomplete)
            if enriched.empty:
                log.warning(f"{symbol} — aucun indicateur (pas assez de bougies closes ?)")
                continue

            res = {"df": enriched, "indicators_ok": True}

            if daily_filter_config and daily_data:
                # Utiliser get_daily_trend_at_timestamp() qui slice les données daily
                # de manière CAUSALE : seules les bougies daily strictement antérieures
                # au timestamp 4h courant sont utilisées. Contrairement à compute_daily_trend()
                # direct qui incluait la bougie daily incomplète (aujourd'hui) = look-ahead.
                last_4h_ts = enriched.iloc[-1]["timestamp"]
                if symbol in daily_data:
                    symbol_trend = get_daily_trend_at_timestamp(symbol, last_4h_ts, daily_data, config)
                else:
                    symbol_trend = {"trend": "neutral", "reason": "no daily data"}

                res["daily_trend"] = symbol_trend
                log.info(f"{symbol} — tendance daily : {symbol_trend['trend']} (raison: {symbol_trend.get('reason', 'normal')})")
            else:
                res["daily_trend"] = None

            results[symbol] = res
            log.info(f"{symbol} — analyse OK ({len(enriched)} bougies)")
        except Exception as e:
            log.error(f"{symbol} — erreur : {e}", exc_info=True)
    return results

if __name__ == "__main__":
    import sys
    import asyncio
    from config_loader import get_config
    from module1_data_v3 import init_exchange_async, fetch_all_async

    async def main():
        print("\n=== TEST MODULE 2 v4 (Injection) ===\n")
        config = get_config()
        exchange = await init_exchange_async()
        try:
            data = await fetch_all_async(exchange, use_cache=True)
            if not data:
                print("Aucune donnée.")
                return
            analyzed = analyze_all(data, config, include_incomplete=False)
            print(f"\n✓ {len(analyzed)} paires analysées\n")
        finally:
            await exchange.close()

    asyncio.run(main())
