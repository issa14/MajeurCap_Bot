"""
Backtest multi-scénarios - Analyse Comparative (Indicateurs & Confluences)
"""

import asyncio
import pandas as pd
import numpy as np
import copy
import logging
import sys
from config_loader import get_config

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
from datetime import datetime, timezone, timedelta
from module2_AT import clean_ohlcv, compute_indicators, get_daily_trend_at_timestamp, get_daily_structure_at_timestamp
from module3_signal import generate_signal, generate_signal_mtf
from module4_backtest import simulate_trade
from metrics import compute_metrics


async def run_single_backtest(scenario_params: dict, symbols: list = None, start_idx: int = 150, since: int = None):
    """
    since : timestamp epoch ms optionnel. Si fourni, le backtest tourne sur une fenêtre
    historique commençant à cette date plutôt que sur les données les plus récentes.
    Utile pour valider qu\'une config ne sur-apprend pas à la période récente.
    """
    config = copy.deepcopy(get_config())
    if "signal" not in config: config["signal"] = {}
    
    # Injection des paramètres du scénario
    config["signal"].update(scenario_params)
    config["candles_limit"] = 1000
    # Propager daily_trend_strict à la racine (get_daily_trend_at_timestamp lit les deux niveaux)
    config["daily_trend_strict"] = scenario_params.get("daily_trend_strict", False)

    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, symbols=symbols, use_cache=(since is None), since=since)
        daily_data = {}
        if config["signal"].get("daily_filter_enabled"):
            # Le daily doit démarrer BIEN AVANT le since du 4h pour avoir assez d\'historique
            # (EMA200 a besoin de 200 bougies daily). Sans ce décalage, au début de la fenêtre
            # 4h testée il n\'y a pas assez de daily antérieur → "neutral" partout → 0 trades.
            daily_since = None
            if since is not None:
                from datetime import datetime, timezone, timedelta
                daily_since = since - int(timedelta(days=250).total_seconds() * 1000)
            daily_data = await fetch_daily_all_async(exchange, symbols=symbols, use_cache=(since is None), since=daily_since)
    finally:
        await exchange.close()

    if not data:
        return pd.DataFrame()

    all_trades = []
    for symbol, df in data.items():
        clean = clean_ohlcv(df)
        enriched = compute_indicators(clean, config, include_incomplete=False)
        if enriched.empty:
            continue

        n = len(enriched)
        i = start_idx
        cached_daily_structure = None
        cached_daily_candle_count = -1
        df_daily_for_symbol = daily_data.get(symbol) if daily_data else None

        while i < n - 10:
            daily_trend = None
            if config["signal"].get("daily_filter_enabled") and daily_data:
                daily_trend = get_daily_trend_at_timestamp(symbol, enriched.iloc[i]["timestamp"], daily_data, config)

            df_sub = enriched.iloc[:i+1]

            sig = generate_signal(symbol, df_sub, config, daily_trend=daily_trend)

            if sig:
                future = enriched.iloc[i+1:]
                if not future.empty:
                    trade_result = simulate_trade(future, sig, config)
                    leverage = config.get("risk", {}).get("leverage", 1)
                    all_trades.append({
                        "symbol": symbol,
                        "entry_date": enriched.iloc[i]["timestamp"],
                        "pnl_pct": trade_result["pnl_pct"] * leverage,
                        "result": trade_result["result"]
                    })
                    i = trade_result["exit_idx"]
            i += 1
    return pd.DataFrame(all_trades)

async def main():
    logging.basicConfig(level=logging.WARNING)
    base_config = get_config()
    
    # Old multi‑timeframe block removed

    # (compute_confluence_score : max théorique ~4.5 avec BOS, ~5.0 avec CHoCH)
    # Base fixe : daily_filter=true, kc_filter=true, sl_mult=2.0 (meilleur scénario connu)
    # Format : (name, min_conf, min_conf_no_struct)
    # Out-of-sample comparison: recent data vs historical window (~395 days ago)
    # Evaluate weighted confluence score (min_confluences ~2.5) against old integer count (min_confluences ~3)
    comparison_cases = [
        ("recent", None),
        ("historical", int((datetime.now(timezone.utc) - timedelta(days=395)).timestamp() * 1000)),
    ]

    # Comparaison watchlist actuelle vs watchlist proposée — config old_count_3 uniquement
    # (la config gagnante validée, pas besoin de refaire tous les scénarios).
    # watchlist_current : ancienne watchlist utilisée dans les backtests précédents
    # watchlist_proposed : nouvelle watchlist optimisée (VET/HYPE/ETH/ARB remplacés par DOGE/BNB,
    #                      réduite à 6 paires pour meilleure qualité de signal et diversification)
    config_candidates = [
        ("current_wl", 3, 3.5, None),
        ("proposed_wl", 3, 3.5, None),
    ]

    watchlist_current  = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ARB/USDT", "LINK/USDT", "SUI/USDT"]
    watchlist_proposed = ["BTC/USDT", "SOL/USDT", "BNB/USDT", "LINK/USDT", "SUI/USDT", "DOGE/USDT"]

    results = []

    print(f"{'Seuil':<12} | {'MinConf':<8} | {'NoStruct':<9} | {'MinScore':<7} | {'Trades':<6} | {'WR%':<6} | {'PF':<6} | {'PnL%':<8} | {'DD%':<6} | {'Sharpe'}")
    print("-" * 100)

    for case_name, since_ts in comparison_cases:
        for cfg_name, min_conf, min_conf_no_struct, min_score in config_candidates:
            params = {
                "adx_required": True,
                "daily_filter_enabled": True,
                "kc_filter": True,
                "min_confluences": min_conf,
                "min_confluences_no_struct": min_conf_no_struct,
                "min_score": min_score,
                "zigzag_window": 3,
                "min_swing_diff_pct": 0.5,
                "daily_trend_strict": False,
                "sl_atr_mult": 2.0,
                "trailing_sl_enabled": False,
            }
            wl = watchlist_proposed if cfg_name == "proposed_wl" else watchlist_current
            trades_df = await run_single_backtest(params, symbols=wl, since=since_ts)
            m = compute_metrics(trades_df, initial_capital=base_config.get("risk", {}).get("capital", 1000))
            scenario_name = f"{cfg_name}_{case_name}"
            res_entry = {
                "scenario": scenario_name,
                "min_confluences": min_conf,
                "min_score": min_score,
                **m,
                "params": params,
            }
            results.append(res_entry)
            print(f"{scenario_name:<12} | {min_conf:<8} | {min_conf_no_struct:<9} | {str(min_score):<7} | {m['trades']:<6} | {m['winrate']:>5.1f}% | {m['profit_factor']:>5.2f} | {m['pnl_total']:>7.2f}% | {m['max_drawdown']:>5.1f}% | {m['sharpe']:>5.2f}")

    # MTF scenarios block removed

if __name__ == "__main__":
    asyncio.run(main())
