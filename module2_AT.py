"""
Module 2 — Analyse technique v4 (Injection de dépendances)
"""

import pandas as pd
import numpy as np
import ta
import logging
from typing import Optional

log = logging.getLogger(__name__)

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

# ─── Batch ────────────────────────────────────────────────────────────────────
def analyze_all(data: dict, config: dict, include_incomplete: bool = False, daily_data: Optional[dict] = None) -> dict:
    """Analyse toutes les paires avec injection de config."""
    results = {}
    daily_filter_config = config.get("signal", {}).get("daily_filter_enabled", False)
    daily_strict = config.get("signal", {}).get("daily_trend_strict", True)

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

            if daily_filter_config and daily_data and symbol in daily_data:
                daily_trend = compute_daily_trend(daily_data[symbol], strict=daily_strict)
                res["daily_trend"] = daily_trend
                log.info(f"{symbol} — tendance daily : {daily_trend['trend']}")
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
