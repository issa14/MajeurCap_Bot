"""
Module 4 — Backtest v2 (gestion de position corrigée)
"""

import pandas as pd
import numpy as np
import logging
import asyncio
import sys
from config_loader import get_config

sys.path.insert(0, ".")
from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
from module2_AT import clean_ohlcv, compute_indicators, get_daily_trend_at_timestamp
from module3_signal import (
    compute_fibonacci_from_swings,
    detect_structure,
    check_confluences,
    compute_levels,
    generate_signal
)
from metrics import compute_metrics

# ─── Simulation d'un trade avec suivi bougie par bougie ────────────────────
def simulate_trade(df_future: pd.DataFrame, signal: dict, config: dict) -> dict:
    """
    Parcourt les bougies futures jusqu'à toucher SL, TP1 ou TP2.
    Inclut les frais et le slippage.

    Si trailing_sl_enabled=True (lu depuis config["signal"] ou config["risk"]) :
      - TP1 déclenche une sortie partielle 50% + SL déplacé au breakeven (entry)
      - Le trailing SL ATR-based s'active sur le 50% restant
      - PnL combiné = 0.5 × pnl_tp1 + 0.5 × pnl_final
      - Résultat "TSL" si le trailing SL est touché au-dessus du SL initial
    Si trailing_sl_enabled=False : comportement identique à l'ancienne version.

    Réplique fidèle de la logique trade_manager.py (manage_position).
    """
    bt_cfg   = config.get("backtest", {})
    fee_pct  = bt_cfg.get("fee_pct", 0.1) / 100
    slip_pct = bt_cfg.get("slippage_pct", 0.05) / 100
    leverage = config.get("risk", {}).get("leverage", 1)

    sig_cfg  = config.get("signal", {})
    risk_cfg = config.get("risk", {})
    trailing_enabled  = sig_cfg.get("trailing_sl_enabled",  risk_cfg.get("trailing_sl_enabled",  False))
    trailing_atr_mult = sig_cfg.get("trailing_sl_atr_mult", risk_cfg.get("trailing_sl_atr_mult", 2.0))

    direction   = signal["direction"]
    entry_price = signal["entry"]
    entry_price = entry_price * (1 + slip_pct) if direction == "LONG" else entry_price * (1 - slip_pct)

    sl  = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]

    partial_done   = False   # TP1 partiel déjà enregistré
    tp1_exit_price = None    # prix réel de sortie partielle à TP1 (après slippage)
    current_sl     = sl      # SL dynamique (se déplace après TP1)

    def _net(gross):
        """Applique frais (×2 : entrée + sortie) et levier au PnL brut."""
        return (gross - fee_pct * 2) * 100 * leverage

    for idx, row in df_future.iterrows():
        high    = row["high"]
        low     = row["low"]
        close   = row["close"]
        atr     = row.get("atr", 0) or 0
        bullish = close >= row["open"]

        if direction == "LONG":
            # ── Phase 1 : avant TP1 ──────────────────────────────────────────
            if not partial_done:
                if bullish:
                    if high >= tp2:
                        g = (tp2 * (1 - slip_pct) - entry_price) / entry_price
                        return {"result": "TP2", "pnl_pct": _net(g), "exit_idx": idx}
                    elif high >= tp1:
                        if trailing_enabled:
                            partial_done   = True
                            tp1_exit_price = tp1 * (1 - slip_pct)
                            current_sl     = entry_price   # breakeven
                        else:
                            g = (tp1 * (1 - slip_pct) - entry_price) / entry_price
                            return {"result": "TP1", "pnl_pct": _net(g), "exit_idx": idx}
                    elif low <= current_sl:
                        g = (current_sl * (1 - slip_pct) - entry_price) / entry_price
                        return {"result": "SL", "pnl_pct": _net(g), "exit_idx": idx}
                else:  # bearish
                    if low <= current_sl:
                        g = (current_sl * (1 - slip_pct) - entry_price) / entry_price
                        return {"result": "SL", "pnl_pct": _net(g), "exit_idx": idx}
                    elif high >= tp2:
                        g = (tp2 * (1 - slip_pct) - entry_price) / entry_price
                        return {"result": "TP2", "pnl_pct": _net(g), "exit_idx": idx}
                    elif high >= tp1:
                        if trailing_enabled:
                            partial_done   = True
                            tp1_exit_price = tp1 * (1 - slip_pct)
                            current_sl     = entry_price
                        else:
                            g = (tp1 * (1 - slip_pct) - entry_price) / entry_price
                            return {"result": "TP1", "pnl_pct": _net(g), "exit_idx": idx}

            # ── Phase 2 : trailing ATR sur 50% restant ───────────────────────
            if partial_done:
                if atr > 0:
                    atr_sl = round(close - atr * trailing_atr_mult, 8)
                    if atr_sl > current_sl:
                        current_sl = atr_sl
                if high >= tp2:
                    p1 = (tp1_exit_price - entry_price) / entry_price
                    p2 = (tp2 * (1 - slip_pct) - entry_price) / entry_price
                    return {"result": "TP2", "pnl_pct": _net(0.5 * p1 + 0.5 * p2), "exit_idx": idx}
                if low <= current_sl:
                    p1 = (tp1_exit_price - entry_price) / entry_price
                    p2 = (current_sl * (1 - slip_pct) - entry_price) / entry_price
                    label = "TSL" if current_sl > sl else "SL"
                    return {"result": label, "pnl_pct": _net(0.5 * p1 + 0.5 * p2), "exit_idx": idx}

        else:  # SHORT
            # ── Phase 1 ──────────────────────────────────────────────────────
            if not partial_done:
                if bullish:
                    if high >= current_sl:
                        g = (entry_price - current_sl * (1 + slip_pct)) / entry_price
                        return {"result": "SL", "pnl_pct": _net(g), "exit_idx": idx}
                    elif low <= tp2:
                        g = (entry_price - tp2 * (1 + slip_pct)) / entry_price
                        return {"result": "TP2", "pnl_pct": _net(g), "exit_idx": idx}
                    elif low <= tp1:
                        if trailing_enabled:
                            partial_done   = True
                            tp1_exit_price = tp1 * (1 + slip_pct)
                            current_sl     = entry_price
                        else:
                            g = (entry_price - tp1 * (1 + slip_pct)) / entry_price
                            return {"result": "TP1", "pnl_pct": _net(g), "exit_idx": idx}
                else:  # bearish
                    if low <= tp2:
                        g = (entry_price - tp2 * (1 + slip_pct)) / entry_price
                        return {"result": "TP2", "pnl_pct": _net(g), "exit_idx": idx}
                    elif low <= tp1:
                        if trailing_enabled:
                            partial_done   = True
                            tp1_exit_price = tp1 * (1 + slip_pct)
                            current_sl     = entry_price
                        else:
                            g = (entry_price - tp1 * (1 + slip_pct)) / entry_price
                            return {"result": "TP1", "pnl_pct": _net(g), "exit_idx": idx}
                    elif high >= current_sl:
                        g = (entry_price - current_sl * (1 + slip_pct)) / entry_price
                        return {"result": "SL", "pnl_pct": _net(g), "exit_idx": idx}

            # ── Phase 2 ──────────────────────────────────────────────────────
            if partial_done:
                if atr > 0:
                    atr_sl = round(close + atr * trailing_atr_mult, 8)
                    if atr_sl < current_sl:
                        current_sl = atr_sl
                if low <= tp2:
                    p1 = (entry_price - tp1_exit_price) / entry_price
                    p2 = (entry_price - tp2 * (1 + slip_pct)) / entry_price
                    return {"result": "TP2", "pnl_pct": _net(0.5 * p1 + 0.5 * p2), "exit_idx": idx}
                if high >= current_sl:
                    p1 = (entry_price - tp1_exit_price) / entry_price
                    p2 = (entry_price - current_sl * (1 + slip_pct)) / entry_price
                    label = "TSL" if current_sl < sl else "SL"
                    return {"result": label, "pnl_pct": _net(0.5 * p1 + 0.5 * p2), "exit_idx": idx}

    # ── EOD : fin d'historique sans toucher aucun niveau ─────────────────────
    last_close = df_future.iloc[-1]["close"]
    if direction == "LONG":
        actual_eod = last_close * (1 - slip_pct)
        if partial_done:
            p1    = (tp1_exit_price - entry_price) / entry_price
            p2    = (actual_eod - entry_price) / entry_price
            gross = 0.5 * p1 + 0.5 * p2
        else:
            gross = (actual_eod - entry_price) / entry_price
    else:
        actual_eod = last_close * (1 + slip_pct)
        if partial_done:
            p1    = (entry_price - tp1_exit_price) / entry_price
            p2    = (entry_price - actual_eod) / entry_price
            gross = 0.5 * p1 + 0.5 * p2
        else:
            gross = (entry_price - actual_eod) / entry_price

    net_pnl = gross - (fee_pct * 2)
    return {"result": "EOD", "pnl_pct": net_pnl * 100 * leverage, "exit_idx": df_future.index[-1]}

# ─── Backtest principal corrigé ─────────────────────────────────────────────
async def run_backtest(symbols: list = None, start_idx: int = 200, exclude_eod: bool = False):
    config = get_config()
    daily_filter_enabled = config.get("signal", {}).get("daily_filter_enabled", False)
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

    metrics = compute_metrics(trades_df, initial_capital=config.get("risk", {}).get("capital", 1000))

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
    print("\n--- Par symbole ---")
    for sym in trades_df["symbol"].unique():
        sub = trades_df[trades_df["symbol"] == sym]
        w = sub[sub["pnl_pct"] > 0]
        print(f"{sym:12s} trades: {len(sub):2d} | winrate: {len(w)/len(sub)*100:5.1f}% | PnL: {sub['pnl_pct'].sum():+.2f}%")

    return trades_df

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("backtest")
    asyncio.run(run_backtest(exclude_eod=False))  # Mettre True pour exclure les trades non clôturés