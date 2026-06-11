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
from module2_AT import clean_ohlcv, compute_indicators, get_daily_trend_at_timestamp
from module3_signal import generate_signal
from module4_backtest import simulate_trade

async def run_single_backtest(scenario_params: dict, symbols: list = None, start_idx: int = 150):
    base_config = get_config()
    config = copy.deepcopy(base_config)
    if "signal" not in config: config["signal"] = {}
    
    # Injection des paramètres du scénario
    config["signal"].update(scenario_params)
    config["candles_limit"] = 1000

    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, symbols=symbols, use_cache=True)
        daily_data = {}
        if config["signal"].get("daily_filter_enabled"):
            daily_data = await fetch_daily_all_async(exchange, symbols=symbols, use_cache=True)
    finally:
        await exchange.close()

    if not data: return pd.DataFrame()

    all_trades = []
    for symbol, df in data.items():
        clean = clean_ohlcv(df)
        enriched = compute_indicators(clean, config, include_incomplete=False)
        if enriched.empty: continue

        n = len(enriched)
        i = start_idx
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
                    all_trades.append({
                        "symbol": symbol,
                        "entry_date": enriched.iloc[i]["timestamp"],
                        "pnl_pct": trade_result["pnl_pct"],
                        "result": trade_result["result"]
                    })
                    i = trade_result["exit_idx"]
            i += 1
    return pd.DataFrame(all_trades)

def compute_metrics(trades_df):
    if trades_df.empty:
        return {"trades": 0, "winrate": 0, "profit_factor": 0, "pnl_total": 0, "max_drawdown": 0, "sharpe": 0}
    
    win = trades_df[trades_df["pnl_pct"] > 0]
    loss = trades_df[trades_df["pnl_pct"] <= 0]
    trades = len(trades_df)
    winrate = len(win) / trades * 100
    pnl_total = trades_df["pnl_pct"].sum()
    
    gross_win = win["pnl_pct"].sum()
    gross_loss = abs(loss["pnl_pct"].sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float('inf')

    cumulative = trades_df["pnl_pct"].cumsum()
    max_drawdown = (cumulative.cummax() - cumulative).max()
    
    # Sharpe annualisé (6 bougies 4h par jour * 365 jours = 2190 bougies/an)
    std = trades_df["pnl_pct"].std()
    
    # Sharpe ratio calculation: mean / std_dev * sqrt(number of trades)
    # Consistent approach for trade-based performance evaluation.
    sharpe = (trades_df["pnl_pct"].mean() / std * np.sqrt(len(trades_df))) if std > 0 else 0

    return {
        "trades": trades, "winrate": winrate, "profit_factor": profit_factor,
        "pnl_total": pnl_total, "max_drawdown": max_drawdown, "sharpe": sharpe
    }

async def main():
    logging.basicConfig(level=logging.WARNING)
    
    scenarios = [
        ("adx_only",    True,  False, False, 1.5, False),
        ("adx_sl2.0",   True,  False, False, 2.0, False),
        ("adx_trailing", True,  False, False, 1.5, True),
        ("adx_opti",    True,  False, False, 2.0, True),
    ]
    
    confluences_to_test = [3]
    watchlist = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ARB/USDT", "LINK/USDT", "SUI/USDT"]
    
    results = []

    print(f"{'Scénario':<15} | {'SL':<4} | {'Trail':<5} | {'Trades':<6} | {'WR%':<6} | {'PF':<6} | {'PnL%':<8} | {'DD%':<6} | {'Sharpe'}")
    print("-" * 90)

    for name, adx, daily, kc, sl_mult, trailing in scenarios:
        for conf in confluences_to_test:
            params = {
                "adx_required": adx,
                "daily_filter_enabled": daily,
                "kc_filter": kc,
                "min_confluences": conf,
                "zigzag_window": 3,
                "min_swing_diff_pct": 0.5,
                "daily_trend_strict": False,
                "sl_atr_mult": sl_mult,
                "trailing_sl_enabled": trailing
            }
            
            trades_df = await run_single_backtest(params, symbols=watchlist)
            m = compute_metrics(trades_df)
            
            res_entry = {
                "scenario": name,
                "min_confluences": conf,
                **m,
                "params": params
            }
            results.append(res_entry)
            
            print(f"{name:<15} | {sl_mult:<4} | {str(trailing):<5} | {m['trades']:<6} | {m['winrate']:>5.1f}% | {m['profit_factor']:>5.2f} | {m['pnl_total']:>7.2f}% | {m['max_drawdown']:>5.1f}% | {m['sharpe']:>5.2f}")

if __name__ == "__main__":
    asyncio.run(main())
