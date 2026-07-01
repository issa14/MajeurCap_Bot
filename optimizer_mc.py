"""
Optimiseur Monte Carlo anti-overfitting
Teste 36 combinaisons de paramètres avec validation Monte Carlo (50k iter × 2 fenêtres).
Score basé sur la ROBUSTESSE (prob gain, P(DD>50%), Sharpe P5), pas sur le PnL.

Usage : python optimizer_mc.py
Durée estimée : ~20 minutes
"""

import asyncio
import itertools
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
    "BTC/USDT", "ETH/USDT", "HYPE/USDT", "SUI/USDT",
    "LINK/USDT", "BNB/USDT", "SOL/USDT", "VET/USDT"
]

WINDOWS = [
    ("recent",      None),
    ("historical",  int((datetime.now(timezone.utc) - timedelta(days=395)).timestamp() * 1000)),
]

# ── Grille de paramètres (36 combinaisons) ──────────────────────────────────
PARAM_GRID = {
    "min_confluences":            [3, 4, 5],
    "min_confluences_no_struct":  [3, 4],
    "sl_atr_mult":                [1.0, 1.5, 2.0],
    "kc_filter":                  [True, False],
}

FIXED_PARAMS = {
    "adx_required":               True,
    "daily_filter_enabled":       True,
    "min_score":                  None,
    "zigzag_window":              3,
    "min_swing_diff_pct":         0.5,
    "daily_trend_strict":         False,
    "tp1_rr":                     1.2,
    "tp2_rr":                     2.0,
    "trailing_sl_enabled":        False,
}


def bootstrap_worker(args: tuple) -> list:
    """Worker Monte Carlo (multiprocessing)."""
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
    """Lance n_iter Monte Carlo en parallèle. Retourne array (n_iter, 3)."""
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


def compute_score(mc_results: np.ndarray) -> dict:
    """Calcule le score de robustesse à partir des résultats Monte Carlo."""
    pnls = mc_results[:, 0]
    dds = mc_results[:, 1]
    sharpes = mc_results[:, 2]

    prob_gain = (pnls > 0).mean() * 100
    prob_dd_50 = (dds > 50).mean() * 100
    sharpe_p5 = np.percentile(sharpes, 5)
    sharpe_median = np.percentile(sharpes, 50)
    pnl_p5 = np.percentile(pnls, 5)
    pnl_median = np.percentile(pnls, 50)
    dd_median = np.percentile(dds, 50)

    # Score composite : on veut prob_gain élevé, DD faible, Sharpe élevé
    score = prob_gain + (100 - prob_dd_50) + (sharpe_p5 * 100)

    return {
        "prob_gain_pct": round(prob_gain, 1),
        "prob_dd_gt_50_pct": round(prob_dd_50, 1),
        "sharpe_p5": round(sharpe_p5, 3),
        "sharpe_median": round(sharpe_median, 3),
        "pnl_p5": round(pnl_p5, 2),
        "pnl_median": round(pnl_median, 2),
        "dd_median": round(dd_median, 2),
        "score": round(score, 1),
    }


def generate_combinations(grid: dict) -> list:
    """Génère toutes les combinaisons de la grille."""
    keys = list(grid.keys())
    values = list(grid.values())
    for combo in itertools.product(*values):
        yield dict(zip(keys, combo))


def format_params(params: dict) -> str:
    """Formate les paramètres pour affichage compact."""
    return (f"mc={params['min_confluences']} "
            f"nos={params['min_confluences_no_struct']} "
            f"sl={params['sl_atr_mult']} "
            f"kc={'Y' if params['kc_filter'] else 'N'}")


async def evaluate_config(params: dict, idx: int, total: int) -> dict:
    """Évalue une config : backtest + Monte Carlo sur 2 fenêtres."""
    best = {**FIXED_PARAMS, **params}
    result = {
        "params": best,
        "params_label": format_params(params),
        "scores": {},
    }

    for window_name, since_ts in WINDOWS:
        try:
            trades_df = await run_single_backtest(best, symbols=WATCHLIST, since=since_ts)
            if trades_df.empty:
                result["scores"][window_name] = None
                continue

            n_trades = len(trades_df)
            trades_pnl = trades_df["pnl_pct"].values.astype(np.float64)

            mc_results = run_monte_carlo(
                trades_flat=trades_pnl,
                n_trades=n_trades,
                n_iter=N_ITERATIONS,
                n_workers=N_WORKERS,
            )

            score = compute_score(mc_results)
            score["n_trades"] = n_trades
            result["scores"][window_name] = score
        except Exception as e:
            print(f"  ⚠ Erreur {window_name}: {e}")
            result["scores"][window_name] = None

    return result


async def main():
    logging.basicConfig(level=logging.WARNING)
    base_config = get_config()
    capital = base_config.get("risk", {}).get("capital", 1000)
    leverage = base_config.get("risk", {}).get("leverage", 5)

    combinations = list(generate_combinations(PARAM_GRID))
    total = len(combinations)

    t0 = time.time()

    print(f"\n{'=' * 80}")
    print(f"  OPTIMISEUR MONTE CARLO ANTI-OVERFITTING")
    print(f"  {total} combinaisons × {N_ITERATIONS:,} iter × {len(WINDOWS)} fenêtres = "
          f"{total * N_ITERATIONS * len(WINDOWS):,} simulations")
    print(f"  {N_WORKERS} workers | Levier {leverage}x | {len(WATCHLIST)} paires")
    print(f"  Scoring : prob_gain + (100 − P(DD>50%)) + (Sharpe_P5 × 100)")
    print(f"  Meilleure config = max(min(score_recent, score_historical))")
    print(f"{'=' * 80}\n")

    all_results = []
    best_combo = None
    best_min_score = -999999

    for idx, params in enumerate(combinations, 1):
        label = format_params(params)
        elapsed = time.time() - t0
        eta = (elapsed / idx) * (total - idx) if idx > 0 else 0
        print(f"[{idx:>2}/{total}] {label:<30s} "
              f"({time.strftime('%H:%M:%S', time.gmtime(elapsed))} écoulé, "
              f"ETA {time.strftime('%H:%M:%S', time.gmtime(eta))})", end=" ", flush=True)

        t_cfg = time.time()
        result = await evaluate_config(params, idx, total)
        cfg_time = time.time() - t_cfg

        all_results.append(result)

        # Détermine le score minimum entre les deux fenêtres (anti-overfitting)
        scores_ok = []
        win_scores = {}
        for w in WINDOWS:
            wn = w[0]
            if result["scores"].get(wn):
                win_scores[wn] = result["scores"][wn]["score"]
                scores_ok.append(result["scores"][wn]["score"])

        if len(scores_ok) == 2:
            min_score = min(scores_ok)
            recent_score = win_scores.get("recent", 0)
            hist_score = win_scores.get("historical", 0)

            recent = result["scores"]["recent"]
            hist = result["scores"]["historical"]
            trades_r = recent["n_trades"] if recent else 0
            trades_h = hist["n_trades"] if hist else 0

            print(f"→ min_score={min_score:>7.1f}  "
                  f"(recent={recent_score:.0f}  hist={hist_score:.0f})  "
                  f"trades r={trades_r} h={trades_h}  "
                  f"({cfg_time:.1f}s)")

            if min_score > best_min_score:
                best_min_score = min_score
                best_combo = result
        else:
            missing = [w[0] for w in WINDOWS if not result["scores"].get(w[0])]
            print(f"→ SKIP (pas de trades sur {', '.join(missing)})")

    total_elapsed = time.time() - t0
    print(f"\n{'=' * 80}")
    print(f"  OPTIMISATION TERMINÉE en {time.strftime('%H:%M:%S', time.gmtime(total_elapsed))}")
    print(f"{'=' * 80}")

    # ── Tri et affichage du top 10 ───────────────────────────────────────────
    scored = []
    for r in all_results:
        scores_ok = [r["scores"][w[0]]["score"] for w in WINDOWS
                     if r["scores"].get(w[0])]
        if len(scores_ok) == 2:
            r["min_score"] = min(scores_ok)
            r["score_recent"] = r["scores"]["recent"]["score"]
            r["score_historical"] = r["scores"]["historical"]["score"]
            scored.append(r)

    scored.sort(key=lambda x: x["min_score"], reverse=True)

    print(f"\n  🏆 TOP 10 — Classement par robustesse (min_score)\n")
    col_w = 35
    print(f"{'#':<3} {'Paramètres':<{col_w}} {'min_score':>9} {'score_r':>8} {'score_h':>8} "
          f"{'Gain%_h':>7} {'DD50%_h':>7} {'ShP5_h':>7}")
    print("-" * (3 + col_w + 9 + 8 + 8 + 7 + 7 + 7 + 5))

    for i, r in enumerate(scored[:10], 1):
        hist = r["scores"]["historical"]
        flag = " ⭐" if i == 1 else ""
        print(f"{i:<3} {r['params_label']:<{col_w}} {r['min_score']:>9.1f} "
              f"{r['score_recent']:>8.0f} {r['score_historical']:>8.0f} "
              f"{hist['prob_gain_pct']:>5.1f}% {hist['prob_dd_gt_50_pct']:>5.1f}% "
              f"{hist['sharpe_p5']:>7.3f}{flag}")

    # ── Config gagnante détaillée ───────────────────────────────────────────
    if best_combo:
        print(f"\n{'=' * 80}")
        print(f"  ⭐ CONFIG GAGNANTE")
        print(f"{'=' * 80}")
        print(f"  Paramètres : {best_combo['params_label']}")
        print(f"  Score min  : {best_min_score:.1f}")
        print()

        for w in ["recent", "historical"]:
            if best_combo["scores"].get(w):
                s = best_combo["scores"][w]
                print(f"  ── {w} ({s['n_trades']} trades) ──")
                print(f"    Score           : {s['score']:.1f}")
                print(f"    Prob gain       : {s['prob_gain_pct']:.1f}% {'✅' if s['prob_gain_pct'] >= 95 else '❌'}")
                print(f"    P(DD > 50%)     : {s['prob_dd_gt_50_pct']:.1f}% {'✅' if s['prob_dd_gt_50_pct'] <= 10 else '❌'}")
                print(f"    Sharpe P5       : {s['sharpe_p5']:.3f} {'✅' if s['sharpe_p5'] >= 0.5 else '❌'}")
                print(f"    Sharpe médian   : {s['sharpe_median']:.3f}")
                print(f"    PnL médian      : {s['pnl_median']:+.2f}%")
                print(f"    PnL P5          : {s['pnl_p5']:+.2f}%")
                print(f"    DD médian       : {s['dd_median']:.2f}%")
                print()

        print(f"  Paramètres complets pour config.yaml :")
        print(f"    signal:")
        for k, v in best_combo["params"].items():
            print(f"      {k}: {v}")

    # ── Sauvegarde ──────────────────────────────────────────────────────────
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_combinations": total,
        "n_iterations": N_ITERATIONS,
        "n_workers": N_WORKERS,
        "watchlist": WATCHLIST,
        "leverage": leverage,
        "elapsed_seconds": round(total_elapsed, 1),
        "param_grid": {k: [str(v) for v in vals] for k, vals in PARAM_GRID.items()},
        "top_scored": [],
        "winner": None,
    }

    for r in scored[:10]:
        entry = {
            "params_label": r["params_label"],
            "min_score": r["min_score"],
            "score_recent": r["score_recent"],
            "score_historical": r["score_historical"],
            "params": r["params"],
            "scores": r["scores"],
        }
        output["top_scored"].append(entry)

    if best_combo:
        output["winner"] = {
            "params_label": best_combo["params_label"],
            "min_score": best_min_score,
            "params": best_combo["params"],
            "scores": best_combo["scores"],
        }

    report_path = "optimizer_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n  Rapport sauvegardé : {report_path}")


if __name__ == "__main__":
    asyncio.run(main())