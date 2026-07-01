"""
Backtest comparatif : sl_atr_mult 1.0 vs 2.0 × trailing SL
────────────────────────────────────────────────────────────
Matrice 2×2 : sl_atr_mult (1.0 / 2.0) × trailing_sl_enabled (True / False).

tp1_rr=1.2, tp2_rr=2.0 fixes dans tous les runs.
Le trailing SL est simulé par simulate_trade() dans module4_backtest.py
(sortie partielle 50% à TP1 + SL breakeven + trailing ATR-based).

Usage : python3 backtest_sl_comparison.py
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from config_loader import get_config
from backtest_multi import run_single_backtest
from metrics import compute_metrics

WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "HYPE/USDT", "SUI/USDT",
    "LINK/USDT", "BNB/USDT", "SOL/USDT", "VET/USDT"
]

# ── Matrice 2×2 : sl_atr_mult × trailing_sl ──────────────────────────────────
# Format : (label, sl_atr_mult, trailing_sl_enabled)
# tp1_rr=1.2, tp2_rr=2.0 fixes dans tous les runs.
SCENARIOS = [
    ("sl1.0_no_trail",  1.0, False),   # ◄ config prod actuelle
    ("sl1.0_trail",     1.0, True),    # prod SL + trailing activé
    ("sl2.0_no_trail",  2.0, False),   # SL large, pas de trailing
    ("sl2.0_trail",     2.0, True),    # SL large + trailing
]

# ── Fenêtres ─────────────────────────────────────────────────────────────────
WINDOWS = [
    ("recent",      None),
    ("historical",  int((datetime.now(timezone.utc) - timedelta(days=395)).timestamp() * 1000)),
]

# ── Paramètres fixes (identiques pour tous les runs) ─────────────────────────
BASE_PARAMS = {
    "adx_required":               True,
    "daily_filter_enabled":       True,
    "kc_filter":                  True,
    "min_confluences":            3,
    "min_confluences_no_struct":  4,
    "min_score":                  None,
    "zigzag_window":              3,
    "min_swing_diff_pct":         0.5,
    "daily_trend_strict":         False,
    # trailing_sl_enabled injecté par chaque scénario
}

async def main():
    logging.basicConfig(level=logging.WARNING)
    base_config = get_config()
    initial_capital = base_config.get("risk", {}).get("capital", 1000)
    leverage        = base_config.get("risk", {}).get("leverage", 5)

    col_w = 18
    hdr = (
        f"{'Scénario':<{col_w}} | {'Fenêtre':<10} | "
        f"{'Trades':<6} | {'WR%':<6} | {'PF':<5} | "
        f"{'PnL%':<8} | {'DD%':<6} | {'Sharpe':<7} | "
        f"{'sl_mult':<7} | {'tp1_rr':<6} | {'tp2_rr'}"
    )

    print("\n" + "=" * len(hdr))
    print(f"  BACKTEST sl_atr_mult 1.0 vs 2.0  —  MajeurCap_Bot  (levier {leverage}x, matrice 2×2 trailing SL)")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))

    all_results = []

    for window_name, since_ts in WINDOWS:
        print(f"\n── Fenêtre : {window_name} ──")
        for label, sl_mult, trailing in SCENARIOS:
            params = {
                **BASE_PARAMS,
                "sl_atr_mult":        sl_mult,
                "tp1_rr":             1.2,
                "tp2_rr":             2.0,
                "trailing_sl_enabled": trailing,
            }
            trades_df = await run_single_backtest(params, symbols=WATCHLIST, since=since_ts)
            m = compute_metrics(trades_df, initial_capital=initial_capital)

            all_results.append({
                "label": label, "window": window_name,
                "sl_mult": sl_mult, "trailing": trailing,
                **m
            })

            flag = "◄ PROD" if label == "sl1.0_no_trail" else ""
            print(
                f"{label:<{col_w}} | {window_name:<10} | "
                f"{m['trades']:<6} | {m['winrate']:>5.1f}% | {m['profit_factor']:>4.2f} | "
                f"{m['pnl_total']:>7.2f}% | {m['max_drawdown']:>5.1f}% | {m['sharpe']:>6.2f} | "
                f"sl={sl_mult} trail={'Y' if trailing else 'N'}  {flag}"
            )

    # ── Résumé comparatif ─────────────────────────────────────────────────────
    print("\n" + "=" * len(hdr))
    print("  COMPARAISON — Sharpe moyen (recent + historical)")
    print("=" * len(hdr))
    print(f"{'#':<3} {'Scénario':<{col_w}} | {'Sharpe moy':<11} | {'PnL moy%':<9} | {'DD moy%':<8} | {'WR moy%'}")
    print("-" * 70)

    from collections import defaultdict
    sharpe_by = defaultdict(list)
    pnl_by    = defaultdict(list)
    dd_by     = defaultdict(list)
    wr_by     = defaultdict(list)

    for r in all_results:
        sharpe_by[r["label"]].append(r["sharpe"])
        pnl_by[r["label"]].append(r["pnl_total"])
        dd_by[r["label"]].append(r["max_drawdown"])
        wr_by[r["label"]].append(r["winrate"])

    ranked = sorted(
        sharpe_by.keys(),
        key=lambda k: sum(sharpe_by[k]) / len(sharpe_by[k]),
        reverse=True
    )

    for i, label in enumerate(ranked, 1):
        s_avg   = sum(sharpe_by[label]) / len(sharpe_by[label])
        pnl_avg = sum(pnl_by[label])    / len(pnl_by[label])
        dd_avg  = sum(dd_by[label])     / len(dd_by[label])
        wr_avg  = sum(wr_by[label])     / len(wr_by[label])
        flag = "  ◄ PROD" if "1.0" in label else ""
        print(f"{i:<3} {label:<{col_w}} | {s_avg:>10.2f} | {pnl_avg:>8.2f}% | {dd_avg:>6.1f}%  | {wr_avg:>5.1f}%{flag}")

    # ── Verdict ───────────────────────────────────────────────────────────────
    best = ranked[0]
    other = ranked[1] if len(ranked) > 1 else None
    print("\n" + "=" * len(hdr))
    print("  VERDICT")
    print("=" * len(hdr))

    if best == "sl1.0_tp1.2":
        print("✓ sl_atr_mult=1.0 (config prod) reste supérieur en Sharpe moyen.")
        print("  Le rapport du 2026-06-30 était biaisé par le levier 10x — pas par le SL.")
        print("  Recommandation : maintenir sl_atr_mult=1.0 en prod.")
    else:
        s_best  = sum(sharpe_by[best])  / len(sharpe_by[best])
        s_other = sum(sharpe_by[other]) / len(sharpe_by[other]) if other else 0
        delta   = s_best - s_other
        if delta > 0.3:
            print(f"✓ {best} est supérieur avec un écart Sharpe moyen de +{delta:.2f}.")
            print("  Recommandation : envisager le passage à sl_atr_mult=2.0 en prod.")
        else:
            print(f"~ Écart faible ({delta:.2f} Sharpe) — différence non concluante.")
            print("  Recommandation : maintenir sl_atr_mult=1.0 (config validée antérieurement).")

    print("\nNote : trailing SL activé/désactivé selon le scénario (simulé dans simulate_trade()).\n")

if __name__ == "__main__":
    asyncio.run(main())
