"""
Backtest comparatif SL/TP — Calibration sl_atr_mult × tp1_rr
Objectif : trouver la combinaison qui maximise le Sharpe sans sacrifier le winrate,
en testant sur données récentes ET historiques (out-of-sample).

Usage : python3 backtest_sl_tp.py
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from config_loader import get_config
from backtest_multi import run_single_backtest
from metrics import compute_metrics

WATCHLIST = ["BTC/USDT", "ETH/USDT", "HYPE/USDT", "SUI/USDT", "LINK/USDT", "BNB/USDT", "SOL/USDT", "VET/USDT"]

# ── Matrice de scénarios ─────────────────────────────────────────────────────
# Format : (label, sl_atr_mult, tp1_rr, tp2_rr)
# Config prod actuelle : sl=2.0, tp1=1.5, tp2=2.5  → baseline
SCENARIOS = [
    # Baseline production
    ("PROD_baseline",   2.0, 1.5, 2.5),

    # Réduction SL uniquement (TP inchangé)
    ("sl1.5_tp1.5",     1.5, 1.5, 2.5),
    ("sl1.2_tp1.5",     1.2, 1.5, 2.5),
    ("sl1.0_tp1.5",     1.0, 1.5, 2.5),

    # Réduction SL + TP plus conservateur
    ("sl1.5_tp1.2",     1.5, 1.2, 2.0),
    ("sl1.2_tp1.2",     1.2, 1.2, 2.0),
    ("sl1.0_tp1.2",     1.0, 1.2, 2.0),

    # SL serré + TP 1:1 (breakeeven rapide)
    ("sl1.0_tp1.0",     1.0, 1.0, 2.0),
    ("sl1.2_tp1.0",     1.2, 1.0, 2.0),

    # SL très conservateur (grosse marge)
    ("sl1.8_tp1.5",     1.8, 1.5, 2.5),
]

# Fenêtres de test
WINDOWS = [
    ("recent",      None),
    ("historical",  int((datetime.now(timezone.utc) - timedelta(days=395)).timestamp() * 1000)),
]

BASE_PARAMS = {
    "adx_required":            True,
    "daily_filter_enabled":    True,
    "kc_filter":               True,
    "min_confluences":         3,
    "min_confluences_no_struct": 4,
    "min_score":               None,
    "zigzag_window":           3,
    "min_swing_diff_pct":      0.5,
    "daily_trend_strict":      False,
    "trailing_sl_enabled":     False,
}

async def main():
    logging.basicConfig(level=logging.WARNING)
    base_config = get_config()
    initial_capital = base_config.get("risk", {}).get("capital", 1000)

    col_w = 18
    hdr = (
        f"{'Scénario':<{col_w}} | {'Fenêtre':<10} | "
        f"{'Trades':<6} | {'WR%':<6} | {'PF':<5} | "
        f"{'PnL%':<8} | {'DD%':<6} | {'Sharpe':<7} | "
        f"{'sl_mult':<7} | {'tp1_rr':<6} | {'tp2_rr'}"
    )
    print("\n" + "=" * len(hdr))
    print("  BACKTEST CALIBRATION SL/TP  —  MajeurCap_Bot")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))

    all_results = []

    for window_name, since_ts in WINDOWS:
        print(f"\n── Fenêtre : {window_name} ──")
        for label, sl_mult, tp1_rr, tp2_rr in SCENARIOS:
            params = {
                **BASE_PARAMS,
                "sl_atr_mult": sl_mult,
                "tp1_rr":      tp1_rr,
                "tp2_rr":      tp2_rr,
            }
            trades_df = await run_single_backtest(params, symbols=WATCHLIST, since=since_ts)
            m = compute_metrics(trades_df, initial_capital=initial_capital)

            all_results.append({
                "label": label, "window": window_name,
                "sl_mult": sl_mult, "tp1_rr": tp1_rr, "tp2_rr": tp2_rr,
                **m
            })

            flag = "◄ PROD" if label == "PROD_baseline" else ""
            print(
                f"{label:<{col_w}} | {window_name:<10} | "
                f"{m['trades']:<6} | {m['winrate']:>5.1f}% | {m['profit_factor']:>4.2f} | "
                f"{m['pnl_total']:>7.2f}% | {m['max_drawdown']:>5.1f}% | {m['sharpe']:>6.2f} | "
                f"{sl_mult:<7} | {tp1_rr:<6} | {tp2_rr}  {flag}"
            )

    # ── Résumé : top 3 par Sharpe moyen (recent + historical) ───────────────
    print("\n" + "=" * len(hdr))
    print("  TOP 5 — Sharpe moyen (recent + historical)")
    print("=" * len(hdr))

    from collections import defaultdict
    sharpe_by_label = defaultdict(list)
    pnl_by_label    = defaultdict(list)
    dd_by_label     = defaultdict(list)
    for r in all_results:
        sharpe_by_label[r["label"]].append(r["sharpe"])
        pnl_by_label[r["label"]].append(r["pnl_total"])
        dd_by_label[r["label"]].append(r["max_drawdown"])

    ranked = sorted(
        sharpe_by_label.keys(),
        key=lambda k: sum(sharpe_by_label[k]) / len(sharpe_by_label[k]),
        reverse=True
    )

    print(f"{'#':<3} {'Scénario':<{col_w}} | {'Sharpe moy':<11} | {'PnL moy%':<9} | {'DD moy%'}")
    print("-" * 60)
    for i, label in enumerate(ranked[:5], 1):
        s_avg  = sum(sharpe_by_label[label]) / len(sharpe_by_label[label])
        pnl_avg = sum(pnl_by_label[label])   / len(pnl_by_label[label])
        dd_avg  = sum(dd_by_label[label])     / len(dd_by_label[label])
        flag = "  ◄ PROD" if label == "PROD_baseline" else ""
        print(f"{i:<3} {label:<{col_w}} | {s_avg:>10.2f} | {pnl_avg:>8.2f}% | {dd_avg:>6.1f}%{flag}")

    print("\nTerminé. Analyse les résultats et partage-les ici pour la suite.\n")

if __name__ == "__main__":
    asyncio.run(main())
