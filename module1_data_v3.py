"""
Module 1 — Data Layer v3 (async + is_closed + config YAML + daily)
"""

import asyncio
import ccxt.async_support as ccxt_async
import pandas as pd
import json
import time
import logging
import os
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from config_loader import get_config

# ─── Logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

CACHE_DIR = Path("cache")

def _get_data_params():
    config = get_config()
    return {
        "watchlist": config.get("watchlist", ["BTC/USDT", "ETH/USDT"]),
        "timeframe": config.get("timeframe", "4h"),
        "candles_limit": config.get("candles_limit", 500),
        "cache_ttl_sec": config.get("cache_ttl_minutes", 240) * 60,
        "max_retries": config.get("max_retries", 3),
        "retry_delay_sec": config.get("retry_delay_seconds", 5),
        "request_pause": config.get("request_pause", 0.3)
    }

# ─── Cache atomique ───────────────────────────────────────────────────────────
def _cache_path(symbol: str, timeframe: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    safe_symbol = symbol.replace("/", "_")
    return CACHE_DIR / f"{safe_symbol}_{timeframe}.json"

def _cache_is_valid(path: Path) -> bool:
    if not path.exists():
        return False
    params = _get_data_params()
    return (time.time() - path.stat().st_mtime) < params["cache_ttl_sec"]

def _save_cache(symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    path = _cache_path(symbol, timeframe)
    records = df.copy()
    records["timestamp"] = records["timestamp"].astype(str)
    data = {
        "symbol": symbol,
        "timeframe": timeframe,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "ohlcv": records.to_dict(orient="records"),
    }
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)
    log.debug(f"Cache sauvegardé : {path.name}")

def _load_cache(symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
    path = _cache_path(symbol, timeframe)
    if not _cache_is_valid(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data["ohlcv"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    log.debug(f"Cache chargé pour {symbol}")
    return df

def _timeframe_to_timedelta(timeframe: str) -> timedelta:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    elif unit == 'w':
        return timedelta(weeks=value)
    else:
        raise ValueError(f"Timeframe non supporté : {timeframe}")

def _add_is_closed(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df.empty:
        return df
    delta = _timeframe_to_timedelta(timeframe)
    now = datetime.now(timezone.utc)
    df['is_closed'] = df['timestamp'].apply(lambda t: now >= (t + delta))
    return df

# ─── Initialisation exchange asynchrone ──────────────────────────────────────
async def init_exchange_async() -> ccxt_async.binance:
    exchange = ccxt_async.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    log.info("Exchange Binance initialisé (mode public, spot, async)")
    return exchange

# ─── Récupération OHLCV asynchrone ───────────────────────────────────────────
async def fetch_ohlcv_async(
    exchange: ccxt_async.binance,
    symbol: str,
    timeframe: Optional[str] = None,
    limit: Optional[int] = None,
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    params = _get_data_params()
    tf = timeframe or params["timeframe"]
    lim = limit or params["candles_limit"]

    if use_cache:
        cached = _load_cache(symbol, tf)
        if cached is not None:
            log.info(f"{symbol} — chargé depuis le cache")
            return _add_is_closed(cached, tf)

    for attempt in range(1, params["max_retries"] + 1):
        try:
            log.info(f"{symbol} — fetch Binance (tentative {attempt}/{params['max_retries']})")
            raw = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=lim)

            if not raw:
                log.warning(f"{symbol} — réponse vide")
                return None

            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.sort_values("timestamp").reset_index(drop=True)

            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)

            if len(df) < 50:
                log.warning(f"{symbol} — données insuffisantes ({len(df)} bougies)")
                return None

            df = _add_is_closed(df, tf)

            if use_cache:
                _save_cache(symbol, tf, df)

            log.info(
                f"{symbol} — {len(df)} bougies | "
                f"dernière : {df['timestamp'].iloc[-1]} | "
                f"close : {df['close'].iloc[-1]:.4f} | "
                f"closed : {df['is_closed'].iloc[-1]}"
            )
            return df

        except ccxt_async.NetworkError as e:
            log.warning(f"{symbol} — erreur réseau (tentative {attempt}) : {e}")
            if attempt < params["max_retries"]:
                await asyncio.sleep(params["retry_delay_sec"])
        except ccxt_async.ExchangeError as e:
            log.error(f"{symbol} — erreur exchange : {e}")
            return None
        except Exception as e:
            log.error(f"{symbol} — erreur inattendue : {e}")
            return None

    log.error(f"{symbol} — échec après {params['max_retries']} tentatives")
    return None

# ─── Fetch batch asynchrone ───────────────────────────────────────────────────
async def fetch_all_async(
    exchange: ccxt_async.binance,
    symbols: Optional[list] = None,
    timeframe: Optional[str] = None,
    use_cache: bool = True,
) -> dict:
    params = _get_data_params()
    syms = symbols if symbols is not None else params["watchlist"]
    tf = timeframe if timeframe is not None else params["timeframe"]

    log.info(f"=== Début fetch batch async — {len(syms)} paires ===")
    tasks = [fetch_ohlcv_async(exchange, symbol, tf, use_cache=use_cache) for symbol in syms]
    results_list = await asyncio.gather(*tasks)

    results = {}
    failed = []
    for symbol, df in zip(syms, results_list):
        if df is not None:
            results[symbol] = df
        else:
            failed.append(symbol)

    log.info(f"=== Fetch terminé : {len(results)} OK / {len(failed)} échec ===")
    if failed:
        log.warning(f"Paires en échec : {', '.join(failed)}")
    return results

def summarize(df: pd.DataFrame, symbol: str) -> None:
    last = df.iloc[-1]
    prev = df.iloc[-2]
    change_pct = ((last["close"] - prev["close"]) / prev["close"]) * 100
    arrow = "↑" if change_pct >= 0 else "↓"
    log.info(
        f"{symbol:12s} | close : {last['close']:>12.4f} | "
        f"{arrow} {abs(change_pct):.2f}% | "
        f"vol : {last['volume']:>14.2f} | "
        f"{len(df)} bougies | closed : {last['is_closed']}"
    )

# ─── Fetch daily ──────────────────────────────────────────────────────────────
async def fetch_daily_all_async(
    exchange: ccxt_async.binance,
    symbols: Optional[list] = None,
    use_cache: bool = True,
) -> dict:
    """Récupère les OHLCV en timeframe 1d pour toutes les paires."""
    params = _get_data_params()
    syms = symbols if symbols is not None else params["watchlist"]
    log.info(f"=== Début fetch daily async — {len(syms)} paires ===")
    tasks = [fetch_ohlcv_async(exchange, symbol, timeframe="1d", limit=200, use_cache=use_cache) for symbol in syms]
    results_list = await asyncio.gather(*tasks)

    results = {}
    for symbol, df in zip(syms, results_list):
        if df is not None:
            results[symbol] = df
    log.info(f"Fetch daily terminé : {len(results)} OK")
    return results

# ─── Test standalone ──────────────────────────────────────────────────────────
async def main_test():
    print("\n=== TEST MODULE 1 v3 ===\n")
    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, use_cache=False)
        print("\n─── Résumé des données ───")
        for sym, df in data.items():
            summarize(df, sym)
        params = _get_data_params()
        print(f"\n✓ {len(data)}/{len(params['watchlist'])} paires chargées")
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(main_test())