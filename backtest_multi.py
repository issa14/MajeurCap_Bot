"""
Backtest multi-paramètres
Teste différentes combinaisons de paramètres et classe les résultats.
"""

import asyncio
import pandas as pd
import numpy as np
import itertools
import logging
import sys
import copy
from datetime import datetime
from pathlib import Path
from config_loader import get_config

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async
from module2_AT import clean_ohlcv, compute_indicators
from module3_signal import generate_signal
from module4_backtest import compute_zigzag_bt, simulate_trade

# ─── Backtest pour un jeu de paramètres donné ─────────────────────────────
async def run_single_backtest(params: dict, symbols: list = None, start_idx: int = 200):
    """
    Retourne un DataFrame contenant tous les trades pour ces paramètres.
    """
    base_config = get_config()
    # Créer une config spécifique pour ce run
    config = copy.deepcopy(base_config)
    if "signal" not in config:
        config["signal"] = {}
    config["signal"].update(params)

    exchange = await init_exchange_async()
    try:
        data = await fetch_all_async(exchange, symbols=symbols, use_cache=True)
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
        zigzag_window = params.get("zigzag_window", 5)
        end_test = n - zigzag_window - 10
        if end_test <= start_idx:
            continue

        i = start_idx
        while i < end_test:
            df_sub = enriched.iloc[:i+1].copy()
            if len(df_sub) < 50:
                i += 1
                continue

            # Utiliser generate_signal avec la config modifiée
            sig = generate_signal(symbol, df_sub, config)
            if sig:
                future = enriched.iloc[i+1:]
                if not future.empty:
                    trade_result = simulate_trade(future, sig)
                    all_trades.append({
                        "symbol": symbol,
                        "entry_date": enriched.iloc[i]["timestamp"],
                        "direction": sig["direction"],
                        "entry": sig["entry"],
                        "sl": sig["sl"],
                        "tp1": sig["tp1"],
                        "tp2": sig["tp2"],
                        "result": trade_result["result"],
                        "pnl_pct": trade_result["pnl_pct"],
                        "exit_date": enriched.loc[trade_result["exit_idx"], "timestamp"] if trade_result["result"] != "EOD" else enriched.iloc[-1]["timestamp"],
                        "confluences": len(sig["confluences"]),
                        "structure": sig["structure"]["trend"]
                    })
                    i = trade_result["exit_idx"]
            i += 1

    return pd.DataFrame(all_trades)

# ─── Analyse des résultats ─────────────────────────────────────────────────
def compute_metrics(trades_df):
    if trades_df.empty:
        return {"trades": 0, "winrate": 0, "profit_factor": 0, "pnl_total": 0, "avg_win": 0, "avg_loss": 0}
    win = trades_df[trades_df["pnl_pct"] > 0]
    loss = trades_df[trades_df["pnl_pct"] <= 0]
    trades = len(trades_df)
    winrate = len(win) / trades * 100 if trades else 0
    avg_win = win["pnl_pct"].mean() if not win.empty else 0
    avg_loss = loss["pnl_pct"].mean() if not loss.empty else 0
    pnl_total = trades_df["pnl_pct"].sum()
    profit_factor = abs(win["pnl_pct"].sum() / loss["pnl_pct"].sum()) if not loss.empty else float('inf')
    return {
        "trades": trades,
        "winrate": winrate,
        "profit_factor": profit_factor,
        "pnl_total": pnl_total,
        "avg_win": avg_win,
        "avg_loss": avg_loss
    }

# ─── Grille de paramètres à tester ──────────────────────────────────────────
param_grid = {
    "adx_required": [True],
    "adx_threshold": [20, 25, 30],
    "min_confluences": [3, 4],
    "zigzag_window": [5, 7],
    "min_swing_diff_pct": [0.5, 1.0],
    # On garde les autres fixes
    "rsi_long_zone": [(30, 55)],
    "rsi_short_zone": [(45, 70)],
    "sl_atr_mult": [1.5],
    "tp1_rr": [1.5],
    "tp2_rr": [2.5],
    "kc_filter": [True],
    "fibo_proximity_pct": [1.0],
}

# ─── Générer toutes les combinaisons ───────────────────────────────────────
keys = list(param_grid.keys())
combinations = list(itertools.product(*param_grid.values()))
total_runs = len(combinations)

async def main():
    logging.basicConfig(level=logging.WARNING)  # réduire le bruit pendant le backtest
    log = logging.getLogger("multi_backtest")
    print(f"=== BACKTEST MULTI-PARAMÈTRES ({total_runs} combinaisons) ===\n")

    results = []
    watchlist = ["BTC/USDT", "ETH/USDT", "ARB/USDT", "SUI/USDT", "LINK/USDT", "ADA/USDT", "VET/USDT"]  # sans GRT

    for idx, vals in enumerate(combinations, 1):
        params = dict(zip(keys, vals))
        # On applique les zones RSI comme tuples déjà
        trades_df = await run_single_backtest(params, symbols=watchlist)
        metrics = compute_metrics(trades_df)
        metrics["params"] = params
        results.append(metrics)
        print(f"[{idx}/{total_runs}] ADX_th={params['adx_threshold']} min_conf={params['min_confluences']} zigzag_w={params['zigzag_window']} swing_diff={params['min_swing_diff_pct']}  → trades={metrics['trades']} PnL={metrics['pnl_total']:.2f}% PF={metrics['profit_factor']:.2f}")

    # Classement par profit factor décroissant
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("profit_factor", ascending=False)
    
    print("\n\n🏆 CLASSEMENT (profit factor décroissant)")
    print("="*80)
    for i, row in results_df.iterrows():
        p = row["params"]
        print(f"PF={row['profit_factor']:.2f}  PnL={row['pnl_total']:.1f}%  trades={row['trades']}  WR={row['winrate']:.1f}%  "
              f"ADX={p['adx_threshold']} min_conf={p['min_confluences']} zigzag_w={p['zigzag_window']} swing_diff={p['min_swing_diff_pct']}")

    # Sauvegarder dans un CSV
    results_df.to_csv("backtest_multi_results.csv", index=False)
    print("\n✅ Résultats sauvegardés dans backtest_multi_results.csv")

if __name__ == "__main__":
    asyncio.run(main())