"""
Backtest de la config gagnante (min_confluences=4) sur 3 fenêtres
pour valider la robustesse et détecter l'overfitting.

Fenêtres :
  - recent         : données les plus récentes
  - historical     : −395 jours → aujourd'hui
  - out_of_sample  : −790 jours → −395 jours (jamais utilisée pour l'optimisation)

Usage : python backtest_winner.py
"""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, ".")
from backtest_multi import run_single_backtest
from metrics import compute_metrics
from config_loader import get_config

# ── Config gagnante ──────────────────────────────────────────────────────────
WINNER_PARAMS = {
    "adx_required":               True,
    "daily_filter_enabled":       True,
    "kc_filter":                  True,
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

WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "HYPE/USDT", "SUI/USDT",
    "LINK/USDT", "BNB/USDT", "SOL/USDT", "VET/USDT"
]

# ── 3 fenêtres : recent, historical, out-of-sample ───────────────────────────
now = datetime.now(timezone.utc)
WINDOWS = [
    ("recent",        None),
    ("historical",    int((now - timedelta(days=395)).timestamp() * 1000)),
    ("out_of_sample", int((now - timedelta(days=790)).timestamp() * 1000),
                      int((now - timedelta(days=395)).timestamp() * 1000)),
]


async def main():
    logging.basicConfig(level=logging.WARNING)
    base_config = get_config()
    capital = base_config.get("risk", {}).get("capital", 1000)
    leverage = base_config.get("risk", {}).get("leverage", 5)

    t0 = time.time()

    col_w = 16
    hdr = (
        f"{'Fenêtre':<{col_w}} | {'Trades':<7} | {'WR%':<7} | {'PF':<7} | "
        f"{'PnL%':<9} | {'DD%':<7} | {'Sharpe':<8} | {'Calmar':<7}"
    )
    sep = "-" * len(hdr)

    print(f"\n{'=' * len(hdr)}")
    print(f"  BACKTEST CONFIG GAGNANTE — min_confluences=4 (levier {leverage}x, 3 fenêtres)")
    print(f"{'=' * len(hdr)}")
    print(hdr)
    print(sep)

    # Pour la fenêtre out-of-sample, run_single_backtest n'accepte pas de "end_ts"
    # → on utilise "since" comme début de fenêtre et le backtest couvre jusqu'à aujourd'hui.
    # Pour out_of_sample, on lance le backtest avec since=-790j, puis on filtre
    # manuellement les trades pour ne garder que ceux entre -790j et -395j.
    all_trades = {}

    for window_name, since_ts, *rest in [
        ("recent",        None,                                 None),
        ("historical",    int((now - timedelta(days=395)).timestamp() * 1000), None),
        ("out_of_sample", int((now - timedelta(days=790)).timestamp() * 1000),
                          int((now - timedelta(days=395)).timestamp() * 1000)),
    ]:
        end_ts = rest[0] if rest else None
        print(f"\n── Backtest '{window_name}' ({time.strftime('%d/%m/%Y', time.gmtime(since_ts/1000)) if since_ts else 'recent'} → {'recent' if not end_ts else time.strftime('%d/%m/%Y', time.gmtime(end_ts/1000))}) ──")

        t1 = time.time()
        trades_df = await run_single_backtest(WINNER_PARAMS, symbols=WATCHLIST, since=since_ts)

        if trades_df.empty:
            print(f"  Aucun trade. Skip.")
            continue

        # ── Filtrer out-of-sample : ne garder que trades avant end_ts ────────
        if end_ts is not None:
            end_dt = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
            trades_df = trades_df[trades_df["entry_date"] < end_dt].copy()
            if trades_df.empty:
                print(f"  Aucun trade dans la fenêtre out-of-sample. Skip.")
                continue

        all_trades[window_name] = trades_df
        elapsed = time.time() - t1

        m = compute_metrics(trades_df, initial_capital=capital)

        # ── Affichage ligne ──────────────────────────────────────────────────
        print(f"{window_name:<{col_w}} | {m['trades']:<7} | {m['winrate']:>5.1f}% | "
              f"{m['profit_factor']:>5.2f} | {m['pnl_total']:>8.2f}% | "
              f"{m['max_drawdown']:>5.1f}% | {m['sharpe']:>7.2f} | {m['calmar']:>6.2f}")

        # ── Détail par symbole ───────────────────────────────────────────────
        print(f"    Par symbole :")
        for sym in sorted(trades_df["symbol"].unique()):
            sub = trades_df[trades_df["symbol"] == sym]
            w = sub[sub["pnl_pct"] > 0]
            wr_sym = len(w) / len(sub) * 100 if len(sub) else 0
            print(f"      {sym:<14s}  {len(sub):>3d} trades  WR {wr_sym:5.1f}%  "
                  f"PnL {sub['pnl_pct'].sum():>+8.2f}%  DD {sub['pnl_pct'].min():>+8.2f}%")

    # ── Comparaison cross-fenêtre ─────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  COMPARAISON CROSS-FENÊTRE")
    print(sep)
    print(f"{'Métrique':<20} | {'Recent':>10} | {'Historical':>10} | {'Out-of-sample':>14}")
    print("-" * 62)

    metrics_names = ["trades", "winrate", "profit_factor", "pnl_total", "max_drawdown", "sharpe", "calmar"]
    metrics_labels = ["Trades", "WR %", "Profit Factor", "PnL %", "Max DD %", "Sharpe", "Calmar"]

    for label, key in zip(metrics_labels, metrics_names):
        vals = []
        for w in ["recent", "historical", "out_of_sample"]:
            if w in all_trades:
                m = compute_metrics(all_trades[w], initial_capital=capital)
                v = m[key]
                if key in ("winrate",):
                    vals.append(f"{v:>9.1f}%")
                elif key in ("profit_factor", "sharpe", "calmar"):
                    vals.append(f"{v:>10.2f}")
                elif key == "pnl_total":
                    vals.append(f"{v:>+9.2f}%")
                elif key == "max_drawdown":
                    vals.append(f"{v:>9.1f}%")
                else:
                    vals.append(f"{v:>10}")
            else:
                vals.append(f"{'—':>10}")
        print(f"{label:<20} | {vals[0]} | {vals[1]} | {vals[2]}")

    # ── Verdict overfitting ───────────────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  VERDICT OVERFITTING")
    print(sep)

    if "out_of_sample" in all_trades and "recent" in all_trades and "historical" in all_trades:
        oos = compute_metrics(all_trades["out_of_sample"], initial_capital=capital)
        hist = compute_metrics(all_trades["historical"], initial_capital=capital)
        recent = compute_metrics(all_trades["recent"], initial_capital=capital)

        sharpe_drop = (hist["sharpe"] - oos["sharpe"]) / max(abs(hist["sharpe"]), 0.01) * 100
        dd_rise = (oos["max_drawdown"] - hist["max_drawdown"]) / max(oos["max_drawdown"], 0.01) * 100

        print(f"  Sharpe out-of-sample : {oos['sharpe']:.2f}")
        print(f"  Sharpe historical    : {hist['sharpe']:.2f}")
        print(f"  Dégradation Sharpe   : {sharpe_drop:.1f}% (historical → oos)")

        if oos["sharpe"] >= 0.5 and oos["pnl_total"] > 0:
            print(f"\n  ✅ La config reste rentable sur la fenêtre out-of-sample.")
            print(f"     Sharpe OOS={oos['sharpe']:.2f} ≥ 0.5 → PAS d'overfitting sévère.")
        elif oos["sharpe"] >= 0:
            print(f"\n  ⚠️  Sharpe OOS={oos['sharpe']:.2f} — positif mais faible.")
            print(f"     Overfitting léger possible, mais la config reste utilisable.")
        else:
            print(f"\n  ❌ Sharpe OOS={oos['sharpe']:.2f} — négatif !")
            print(f"     OVERFITTING CONFIRMÉ : la config ne survit pas hors échantillon.")
    else:
        print(f"  Impossible de calculer — une ou plusieurs fenêtres sans trades.")

    total_elapsed = time.time() - t0
    print(f"\n  Temps total : {total_elapsed:.1f}s\n")


if __name__ == "__main__":
    asyncio.run(main())