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
from module1_data_v3 import init_exchange_async, fetch_all_async
from module2_AT import clean_ohlcv, compute_indicators
from module3_signal import (
    compute_fibonacci_from_swings,
    detect_structure,
    check_confluences,
    compute_levels
)

# ─── Zigzag backtest (sans lookahead) ───────────────────────────────────────
def compute_zigzag_bt(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Identique à la version backtest déjà présentée précédemment."""
    df = df.copy()
    
    sig_cfg = config.get("signal", {})
    window = sig_cfg.get("zigzag_window", 5)
    min_diff_pct = sig_cfg.get("min_swing_diff_pct", 0.5)

    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    pivots = np.zeros(n, dtype=int)
    last_type = 0
    last_price = None
    last_idx = None

    for i in range(window, n - window):
        h_slice = highs[i - window : i + window + 1]
        l_slice = lows[i - window : i + window + 1]
        if highs[i] == h_slice.max():
            current_type = 1
            current_price = highs[i]
        elif lows[i] == l_slice.min():
            current_type = -1
            current_price = lows[i]
        else:
            continue

        if last_type == 0:
            pivots[i] = current_type
            last_type = current_type
            last_price = current_price
            last_idx = i
        elif current_type != last_type:
            diff_pct = abs(current_price - last_price) / last_price * 100
            if diff_pct >= min_diff_pct:
                pivots[i] = current_type
                last_type = current_type
                last_price = current_price
                last_idx = i
        else:
            if current_type == 1 and current_price > last_price:
                pivots[last_idx] = 0
                pivots[i] = current_type
                last_price = current_price
                last_idx = i
            elif current_type == -1 and current_price < last_price:
                pivots[last_idx] = 0
                pivots[i] = current_type
                last_price = current_price
                last_idx = i

    df["pivot"] = pivots
    return df

def generate_signal_bt(symbol: str, df: pd.DataFrame, config: dict) -> Optional[dict]:
    if "is_closed" in df.columns and not df.iloc[-1]["is_closed"]:
        return None
    
    sig_cfg = config.get("signal", {})
    min_conf = sig_cfg.get("min_confluences", 3)
    min_conf_no_str = sig_cfg.get("min_confluences_no_struct", 4)
    min_pivots = sig_cfg.get("min_pivots", 4)

    df_z = compute_zigzag_bt(df, config)
    fibo = compute_fibonacci_from_swings(df_z)
    structure = detect_structure(df_z, config)
    last = df.iloc[-1]
    threshold = min_conf if structure["pivots_count"] >= min_pivots else min_conf_no_str

    best_signal = None
    best_count = 0
    for direction in ["long", "short"]:
        confluences = check_confluences(df, fibo, structure, direction, config)
        if len(confluences) >= threshold and len(confluences) > best_count:
            best_count = len(confluences)
            levels = compute_levels(last["close"], last["atr"], direction, config)
            best_signal = {
                "symbol": symbol, "direction": direction.upper(),
                "confluences": confluences, "structure": structure,
                "fibo": fibo, "threshold": threshold, **levels,
            }
    return best_signal

# ─── Simulation d'un trade avec suivi bougie par bougie ────────────────────
def simulate_trade(df_future: pd.DataFrame, signal: dict) -> dict:
    """
    Parcourt les bougies futures jusqu'à toucher SL, TP1 ou TP2.
    Retourne le résultat et l'indice de sortie (index global) si applicable.
    """
    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    direction = signal["direction"]

    for idx, row in df_future.iterrows():
        high = row["high"]
        low = row["low"]
        if direction == "LONG":
            if low <= sl:
                return {"result": "SL", "pnl_pct": (sl - entry) / entry * 100, "exit_idx": idx}
            if high >= tp1:
                return {"result": "TP1", "pnl_pct": (tp1 - entry) / entry * 100, "exit_idx": idx}
            if high >= tp2:
                return {"result": "TP2", "pnl_pct": (tp2 - entry) / entry * 100, "exit_idx": idx}
        else:  # SHORT
            if high >= sl:
                return {"result": "SL", "pnl_pct": (entry - sl) / entry * 100, "exit_idx": idx}
            if low <= tp1:
                return {"result": "TP1", "pnl_pct": (entry - tp1) / entry * 100, "exit_idx": idx}
            if low <= tp2:
                return {"result": "TP2", "pnl_pct": (entry - tp2) / entry * 100, "exit_idx": idx}

    # Fin d'historique sans toucher aucun niveau
    last_close = df_future.iloc[-1]["close"]
    pnl = (last_close - entry) / entry * 100 if direction == "LONG" else (entry - last_close) / entry * 100
    return {"result": "EOD", "pnl_pct": pnl, "exit_idx": df_future.index[-1]}

# ─── Backtest principal corrigé ─────────────────────────────────────────────
async def run_backtest(symbols: list = None, start_idx: int = 200, exclude_eod: bool = False):
    config = get_config()
    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, symbols=symbols, use_cache=True)
    finally:
        await exchange.close()

    if not data:
        print("Aucune donnée")
        return

    sig_cfg = config.get("signal", {})
    zigzag_window = sig_cfg.get("zigzag_window", 5)

    all_trades = []
    for symbol, df in data.items():
        clean = clean_ohlcv(df)
        enriched = compute_indicators(clean, config, include_incomplete=False)
        if enriched.empty:
            continue

        n = len(enriched)
        end_test = n - zigzag_window - 10
        if end_test <= start_idx:
            continue

        i = start_idx
        while i < end_test:
            df_sub = enriched.iloc[:i+1].copy()
            if len(df_sub) < 50:
                i += 1
                continue

            signal = generate_signal_bt(symbol, df_sub, config)
            if signal:
                # Bougies disponibles après l'entrée
                future = enriched.iloc[i+1:]
                if not future.empty:
                    trade_result = simulate_trade(future, signal)
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
                    # On saute les bougies jusqu'à la clôture du trade pour éviter les signaux superposés
                    i = trade_result["exit_idx"]  # i sera incrémenté en fin de boucle, donc on repart après la sortie
                else:
                    # Pas de futures, on ne peut pas trader
                    pass
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

    print(f"\n{'='*60}")
    print(f"RÉSULTATS BACKTEST ({'hors EOD' if exclude_eod else 'tous trades'})")
    print(f"Période : {trades_df['entry_date'].min()} → {trades_df['entry_date'].max()}")
    print(f"Nombre de trades : {len(trades_df)}")
    win = trades_df[trades_df["pnl_pct"] > 0]
    loss = trades_df[trades_df["pnl_pct"] <= 0]
    winrate = len(win) / len(trades_df) * 100 if len(trades_df) else 0
    print(f"Winrate : {winrate:.1f}%")
    avg_win = win["pnl_pct"].mean() if not win.empty else 0
    avg_loss = loss["pnl_pct"].mean() if not loss.empty else 0
    print(f"Gain moyen : {avg_win:.2f}% | Perte moyenne : {avg_loss:.2f}%")
    total_pnl = trades_df["pnl_pct"].sum()
    print(f"PnL total : {total_pnl:.2f}%")
    profit_factor = abs(win["pnl_pct"].sum() / loss["pnl_pct"].sum()) if not loss.empty else float('inf')
    print(f"Profit factor : {profit_factor:.2f}")
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