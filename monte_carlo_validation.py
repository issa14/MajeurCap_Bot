"""
Monte Carlo Validation — sl1.0_no_trail + min_confluences=4 + kc_filter=false (combo)
Bootstrapping 50 000 iterations x 2 fenetres (recent + historical)
Parallelisation multiprocessing pour ETA optimise.
 
Objectif : valider la robustesse de la config avant deploiement en prod.
Distribution du PnL, DD, Sharpe, VaR, CVaR.
"""

import asyncio
import json
import logging
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from backtest_multi import run_single_backtest
from metrics import compute_metrics
from config_loader import get_config

N_ITERATIONS = 50_000
N_WORKERS = os.cpu_count() or 4

WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "HYPE/USDT", "XRP/USDT",
    "LINK/USDT", "BNB/USDT", "SOL/USDT", "VET/USDT"
]

BEST_PARAMS = {
    "adx_required":               True,
    "daily_filter_enabled":       True,
    "kc_filter":                  False,
    "min_confluences":            4,
    "min_confluences_no_struct":  4,
    "min_score":                  None,
    "zigzag_window":              3,
    "min_swing_diff_pct":         0.5,
    "daily_trend_strict":         False,
    "sl_atr_mult":                1.0,
    "tp1_rr":                     1.2,
    "tp2_rr":                     2.0,
    "trailing_sl_enabled":        False,
}

WINDOWS = [
    ("recent",      None),
    ("historical",  int((datetime.now(timezone.utc) - timedelta(days=395)).timestamp() * 1000)),
]


def bootstrap_worker(args: tuple) -> list:
    """Worker isole : genere chunk_size iterations Monte Carlo."""
    trades_flat, n_trades, chunk_size, seed = args
    rng = np.random.default_rng(seed)
    results = []
    for _ in range(chunk_size):
        sample = rng.choice(trades_flat, size=n_trades, replace=True)
        equity = (1 + sample.cumsum() / 100)
        running_max = np.maximum.accumulate(equity)
        dd = (running_max - equity) / running_max * 100
        max_dd = dd.max()
        pnl = sample.sum()
        std = sample.std()
        sharpe = (sample.mean() / std * np.sqrt(n_trades)) if std > 0 else 0.0
        results.append((pnl, max_dd, sharpe))
    return results


def run_monte_carlo(trades_flat: np.ndarray, n_trades: int, n_iter: int,
                    n_workers: int) -> np.ndarray:
    """Lance n_iter iterations Monte Carlo en parallele. Retourne array (n_iter, 3)."""
    chunk_size = n_iter // n_workers
    chunks = [chunk_size + (1 if i < n_iter % n_workers else 0)
              for i in range(n_workers)]
    seeds = [int(time.time() * 1e6) % (2**31) + i * 997 for i in range(n_workers)]
    args = [(trades_flat, n_trades, chunks[i], seeds[i]) for i in range(n_workers)]

    with mp.Pool(processes=n_workers) as pool:
        raw = pool.map(bootstrap_worker, args)

    all_results = []
    for chunk in raw:
        all_results.extend(chunk)
    return np.array(all_results, dtype=np.float64)


def compute_stats(results: np.ndarray, label: str) -> dict:
    """Calcule les stats descriptives a partir des resultats Monte Carlo."""
    pnls = results[:, 0]
    dds = results[:, 1]
    sharpes = results[:, 2]

    p5, p25, p50, p75, p95 = np.percentile(pnls, [5, 25, 50, 75, 95])
    dd_p50, dd_p95 = np.percentile(dds, [50, 95])
    sh_p5, sh_p50, sh_p95 = np.percentile(sharpes, [5, 50, 95])

    var_95 = p5
    cvar_95 = pnls[pnls <= var_95].mean() if (pnls <= var_95).any() else var_95

    prob_gain = (pnls > 0).mean() * 100
    prob_dd_30 = (dds > 30).mean() * 100
    prob_dd_50 = (dds > 50).mean() * 100

    return {
        "window": label,
        "n_iterations": len(pnls),
        "n_trades_real": 0,
        "pnl": {
            "min":     float(pnls.min()),
            "max":     float(pnls.max()),
            "p5":      float(p5),
            "p25":     float(p25),
            "median":  float(p50),
            "p75":     float(p75),
            "p95":     float(p95),
            "mean":    float(pnls.mean()),
            "std":     float(pnls.std()),
            "prob_gain_pct": round(prob_gain, 2),
            "var_95":  float(var_95),
            "cvar_95": float(cvar_95),
        },
        "max_drawdown": {
            "median":  float(dd_p50),
            "p95":     float(dd_p95),
            "prob_dd_gt_30_pct": round(prob_dd_30, 2),
            "prob_dd_gt_50_pct": round(prob_dd_50, 2),
        },
        "sharpe": {
            "p5":      float(sh_p5),
            "median":  float(sh_p50),
            "p95":     float(sh_p95),
        },
    }


def print_report(stats: dict, ref_metrics: dict = None):
    """Affiche le rapport formate dans le terminal."""
    n_iter = stats["n_iterations"]
    n_trades = stats["n_trades_real"]
    label = stats["window"]
    pnl = stats["pnl"]
    dd  = stats["max_drawdown"]
    sh  = stats["sharpe"]

    print(f"\n{'=' * 70}")
    print(f"  MONTE CARLO  {label} ({n_iter:,} iterations, {N_WORKERS} workers)")
    if ref_metrics:
        print(f"  Reel      trades={ref_metrics['trades']}  "
              f"PnL={ref_metrics['pnl_total']:+.2f}%  "
              f"Sharpe={ref_metrics['sharpe']:.2f}  "
              f"DD={ref_metrics['max_drawdown']:.1f}%")
    print('=' * 70)

    print(f"\n  PnL total simule (%)")
    print(f"    Median : {pnl['median']:>+10.2f}%     "
          f"P5 : {pnl['p5']:>+10.2f}%     "
          f"P95 : {pnl['p95']:>+10.2f}%")
    print(f"    Min    : {pnl['min']:>+10.2f}%     "
          f"Max  : {pnl['max']:>+10.2f}%")
    print(f"    Moyenne: {pnl['mean']:>+10.2f}%     "
          f"EType: {pnl['std']:>10.2f}%")
    print(f"    Probabilite de gain : {pnl['prob_gain_pct']:.1f}%")
    print(f"    VaR 95%  : {pnl['var_95']:>+8.2f}%     "
          f"CVaR 95% : {pnl['cvar_95']:>+8.2f}%")

    print(f"\n  Max Drawdown simule (%)")
    print(f"    Median  : {dd['median']:>7.2f}%     "
          f"P95       : {dd['p95']:>7.2f}%")
    print(f"    Probabilite DD > 30% : {dd['prob_dd_gt_30_pct']:.1f}%")
    print(f"    Probabilite DD > 50% : {dd['prob_dd_gt_50_pct']:.1f}%")

    print(f"\n  Sharpe ratio simule")
    print(f"    Median : {sh['median']:>7.2f}     "
          f"P5      : {sh['p5']:>7.2f}     "
          f"P95     : {sh['p95']:>7.2f}")
    print()


def print_final_verdict(all_stats: dict):
    """Affiche le verdict consolide et la recommandation."""
    print(f"\n{'=' * 70}")
    print("  VERDICT  sl1.0_no_trail + min_conf=4 + kc_filter=false")
    print('=' * 70)

    for window_label in ["recent", "historical"]:
        if window_label not in all_stats:
            continue
        s = all_stats[window_label]["pnl"]
        dd = all_stats[window_label]["max_drawdown"]
        sh = all_stats[window_label]["sharpe"]

        gain_ok   = s["prob_gain_pct"] >= 95
        dd_ok     = dd["prob_dd_gt_50_pct"] <= 10
        sharpe_ok = sh["p5"] >= 0.5

        status = "OK" if (gain_ok and dd_ok and sharpe_ok) else "ATTENTION"
        print(f"\n  {status} Fenetre '{window_label}'  "
              f"Prob gain {s['prob_gain_pct']:.0f}% | "
              f"P(DD>50%) {dd['prob_dd_gt_50_pct']:.0f}% | "
              f"Sharpe P5 {sh['p5']:.2f}")

    all_pass = all(
        all_stats.get(w, {}).get("pnl", {}).get("prob_gain_pct", 0) >= 95 and
        all_stats.get(w, {}).get("max_drawdown", {}).get("prob_dd_gt_50_pct", 100) <= 10
        for w in ["recent", "historical"] if w in all_stats
    )
    print(f"\n  {'=' * 50}")
    if all_pass:
        print("  RECOMMANDATION : config suffisamment robuste pour la prod.")
        print("     Tous les indicateurs de risque sont dans le vert.")
    else:
        print("  ATTENTION : certains indicateurs de risque sont hors limites.")
        print("     Analyser en detail avant deploiement.")
    print(f"\n  Rapport sauvegarde : monte_carlo_report.json\n")


async def main():
    logging.basicConfig(level=logging.WARNING)
    base_config = get_config()
    capital = base_config.get("risk", {}).get("capital", 1000)
    leverage = base_config.get("risk", {}).get("leverage", 5)

    t0 = time.time()
    all_stats = {}

    print(f"  Monte Carlo  sl1.0_no_trail + min_conf=4 + kc_filter=false")
    print(f"  {N_ITERATIONS:,} iterations x {len(WINDOWS)} fenetres = "
          f"{N_ITERATIONS * len(WINDOWS):,} simulations")
    print(f"  {N_WORKERS} workers (parallelisation multiprocessing)")
    print(f"  Levier {leverage}x  {len(WATCHLIST)} paires")
    print()

    for window_name, since_ts in WINDOWS:
        print(f"## Phase 1 : Backtest '{window_name}'")
        t1 = time.time()

        trades_df = await run_single_backtest(BEST_PARAMS, symbols=WATCHLIST, since=since_ts)

        if trades_df.empty:
            print(f"  X Aucun trade pour '{window_name}'. Skip.")
            continue

        ref = compute_metrics(trades_df, initial_capital=capital)
        n_trades = len(trades_df)

        trades_pnl = trades_df["pnl_pct"].values.astype(np.float64)
        elapsed = time.time() - t1
        print(f"  V {n_trades} trades reels  "
              f"PnL reel {ref['pnl_total']:+.2f}%  "
              f"Sharpe reel {ref['sharpe']:.2f} "
              f"({elapsed:.1f}s)")

        print(f"## Phase 2 : Simulation Monte Carlo ({N_ITERATIONS:,} iter)")
        t2 = time.time()

        mc_results = run_monte_carlo(
            trades_flat=trades_pnl,
            n_trades=n_trades,
            n_iter=N_ITERATIONS,
            n_workers=N_WORKERS,
        )

        elapsed = time.time() - t2
        print(f"  V {N_ITERATIONS:,} iterations en {elapsed:.1f}s "
              f"({N_WORKERS} workers)")

        stats = compute_stats(mc_results, window_name)
        stats["n_trades_real"] = n_trades
        stats["leverage"] = leverage
        all_stats[window_name] = stats

        print_report(stats, ref_metrics=ref)

    total_elapsed = time.time() - t0
    print(f"\n  Temps total : {total_elapsed:.1f}s")

    verdict = {
        "config": "sl1.0_no_trail_minconf4_nokc",
        "params": BEST_PARAMS,
        "watchlist": WATCHLIST,
        "leverage": leverage,
        "n_iterations_per_window": N_ITERATIONS,
        "n_workers": N_WORKERS,
        "total_simulations": N_ITERATIONS * len(WINDOWS),
        "elapsed_seconds": round(total_elapsed, 1),
        "windows": all_stats,
    }

    report_path = "monte_carlo_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(verdict, f, indent=2, ensure_ascii=False)
    print(f"  Rapport sauvegarde : {report_path}")

    print_final_verdict(all_stats)


if __name__ == "__main__":
    asyncio.run(main())
