"""
Module 4 — Backtest v2 (gestion de position corrigée)
"""

import pandas as pd
import numpy as np
import logging
import asyncio
import sys
from pathlib import Path
from typing import Optional
from config_loader import get_config

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
from module2_AT import clean_ohlcv, compute_indicators, get_daily_trend_at_timestamp, compute_zigzag
from module3_signal import (
    compute_fibonacci_from_swings,
    detect_structure,
    check_confluences,
    compute_levels,
    generate_signal
)

# ─── Simulation d'un trade avec suivi bougie par bougie ────────────────────
def simulate_trade(df_future: pd.DataFrame, signal: dict, config: dict) -> dict:
    """
    Parcourt les bougies futures jusqu'à toucher SL, TP1 ou TP2.
    Inclut les frais et le slippage.
    """
    bt_cfg = config.get("backtest", {})
    fee_pct = bt_cfg.get("fee_pct", 0.1) / 100
    slippage_pct = bt_cfg.get("slippage_pct", 0.05) / 100

    entry_price = signal["entry"]
    # Appliquer le slippage à l'entrée
    if signal["direction"] == "LONG":
        entry_price *= (1 + slippage_pct)
    else:
        entry_price *= (1 - slippage_pct)
    
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    direction = signal["direction"]

    for idx, row in df_future.iterrows():
        high = row["high"]
        low = row["low"]
        
        exit_price = None
        result = None
        
        is_bullish = row["close"] >= row["open"]
        if direction == "LONG":
            if is_bullish:
                if high >= tp2:
                    result, exit_price = "TP2", tp2
                elif high >= tp1:
                    result, exit_price = "TP1", tp1
                elif low <= sl:
                    result, exit_price = "SL", sl
            else:
                if low <= sl:
                    result, exit_price = "SL", sl
                elif high >= tp2:
                    result, exit_price = "TP2", tp2
                elif high >= tp1:
                    result, exit_price = "TP1", tp1
        else:  # SHORT
            if is_bullish:
                if high >= sl:
                    result, exit_price = "SL", sl
                elif low <= tp2:
                    result, exit_price = "TP2", tp2
                elif low <= tp1:
                    result, exit_price = "TP1", tp1
            else:
                if low <= tp2:
                    result, exit_price = "TP2", tp2
                elif low <= tp1:
                    result, exit_price = "TP1", tp1
                elif high >= sl:
                    result, exit_price = "SL", sl

        if result:
            # Calcul PnL avec frais et slippage à la sortie
            if direction == "LONG":
                actual_exit = exit_price * (1 - slippage_pct)
                gross_pnl = (actual_exit - entry_price) / entry_price
            else:
                actual_exit = exit_price * (1 + slippage_pct)
                gross_pnl = (entry_price - actual_exit) / entry_price
            
            # Les frais sont appliqués sur la valeur de position à l'entrée et à la sortie
            net_pnl = gross_pnl - (fee_pct * 2)
            return {"result": result, "pnl_pct": net_pnl * 100, "exit_idx": idx}

    # Fin d'historique sans toucher aucun niveau
    last_close = df_future.iloc[-1]["close"]
    if direction == "LONG":
        actual_exit = last_close * (1 - slippage_pct)
        gross_pnl = (actual_exit - entry_price) / entry_price
    else:
        actual_exit = last_close * (1 + slippage_pct)
        gross_pnl = (entry_price - actual_exit) / entry_price
    
    net_pnl = gross_pnl - (fee_pct * 2)
    return {"result": "EOD", "pnl_pct": net_pnl * 100, "exit_idx": df_future.index[-1]}

def compute_metrics(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {
            "trades": 0, "winrate": 0, "profit_factor": 0, "pnl_total": 0,
            "avg_win": 0, "avg_loss": 0, "max_drawdown": 0, "sharpe": 0, "calmar": 0
        }

    win = trades_df[trades_df["pnl_pct"] > 0]
    loss = trades_df[trades_df["pnl_pct"] <= 0]
    trades = len(trades_df)
    winrate = len(win) / trades * 100 if trades else 0
    avg_win = win["pnl_pct"].mean() if not win.empty else 0
    avg_loss = loss["pnl_pct"].mean() if not loss.empty else 0
    pnl_total = trades_df["pnl_pct"].sum()
    profit_factor = abs(win["pnl_pct"].sum() / loss["pnl_pct"].sum()) if not loss.empty else float('inf')

    # Drawdown
    cumulative_pnl = trades_df["pnl_pct"].cumsum()
    running_max = cumulative_pnl.cummax()
    drawdown = running_max - cumulative_pnl
    max_drawdown = drawdown.max()

    # Sharpe (simplifié sur les trades)
    std_dev = trades_df["pnl_pct"].std()
    sharpe = (trades_df["pnl_pct"].mean() / std_dev * np.sqrt(trades)) if std_dev > 0 else 0

    # Calmar
    calmar = (pnl_total / max_drawdown) if max_drawdown > 0 else float('inf')

    return {
        "trades": trades,
        "winrate": winrate,
        "profit_factor": profit_factor,
        "pnl_total": pnl_total,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "calmar": calmar
    }

# ─── Backtest principal corrigé ─────────────────────────────────────────────
async def run_backtest(symbols: list = None, start_idx: int = 200, exclude_eod: bool = False):
    config = get_config()
    daily_filter_enabled = config.get("daily_filter_enabled", config.get("signal", {}).get("daily_filter_enabled", False))
    daily_data = {}
    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, symbols=symbols, use_cache=True)
        if daily_filter_enabled:
            daily_data = await fetch_daily_all_async(exchange, symbols=symbols, use_cache=True)
    finally:
        await exchange.close()

    if not data:
        print("Aucune donnée")
        return

    all_trades = []
    for symbol, df in data.items():
        clean = clean_ohlcv(df)
        enriched = compute_indicators(clean, config, include_incomplete=False)
        if enriched.empty:
            continue

        n = len(enriched)
        # On s'arrête un peu avant la fin pour avoir du "future"
        i = start_idx
        while i < n - 5:
            df_sub = enriched.iloc[:i+1].copy()
            
            daily_trend = None
            if daily_filter_enabled:
                daily_trend = get_daily_trend_at_timestamp(symbol, enriched.iloc[i]["timestamp"], daily_data, config)

            # Utilisation directe du générateur de signal centralisé
            signal = generate_signal(symbol, df_sub, config, daily_trend=daily_trend)
            
            if signal:
                future = enriched.iloc[i+1:]
                if not future.empty:
                    trade_result = simulate_trade(future, signal, config)
                    all_trades.append({
                        "symbol": symbol,
                        "entry_date": enriched.iloc[i]["timestamp"],
                        "direction": signal["direction"],
                        "entry": signal["entry"],
                        "sl": signal["sl"],
                        "tp1": signal["tp1"],
                        "tp2": signal["tp2"],
                        "result": trade_result["result"],
                        "pnl_pct": trade_result["pnl_pct"],
                        "exit_date": enriched.loc[trade_result["exit_idx"], "timestamp"] if trade_result["result"] != "EOD" else enriched.iloc[-1]["timestamp"],
                        "confluences": len(signal["confluences"]),
                        "structure": signal["structure"]["trend"]
                    })
                    i = trade_result["exit_idx"]
            i += 1

    if not all_trades:
        print("Aucun trade.")
        return

    trades_df = pd.DataFrame(all_trades)
    # Filtrer les EOD si demandé
    if exclude_eod:
        trades_df = trades_df[trades_df["result"] != "EOD"].copy()

    if trades_df.empty:
        print("Aucun trade après filtrage.")
        return

    metrics = compute_metrics(trades_df)

    print(f"\n{'='*60}")
    print(f"RÉSULTATS BACKTEST ({'hors EOD' if exclude_eod else 'tous trades'})")
    print(f"Période : {trades_df['entry_date'].min()} → {trades_df['entry_date'].max()}")
    print(f"Nombre de trades : {metrics['trades']}")
    print(f"Winrate : {metrics['winrate']:.1f}%")
    print(f"Gain moyen : {metrics['avg_win']:.2f}% | Perte moyenne : {metrics['avg_loss']:.2f}%")
    print(f"PnL total : {metrics['pnl_total']:.2f}%")
    print(f"Profit factor : {metrics['profit_factor']:.2f}")
    print(f"Max Drawdown : {metrics['max_drawdown']:.2f}%")
    print(f"Sharpe Ratio : {metrics['sharpe']:.2f}")
    print(f"Calmar Ratio : {metrics['calmar']:.2f}")
    print(f"\n--- Par symbole ---")
    for sym in trades_df["symbol"].unique():
        sub = trades_df[trades_df["symbol"] == sym]
        w = sub[sub["pnl_pct"] > 0]
        print(f"{sym:12s} trades: {len(sub):2d} | winrate: {len(w)/len(sub)*100:5.1f}% | PnL: {sub['pnl_pct'].sum():+.2f}%")

    return trades_df

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("backtest")
    asyncio.run(run_backtest(exclude_eod=False))  # Mettre True pour exclure les trades non clôturés