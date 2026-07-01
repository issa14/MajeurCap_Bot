"""
Backtest simple — capital 150$, leverage 10x / 15x / 20x
Utilise la configuration OPTIMALE (sl_atr_mult=2.0, pas de trailing)
au lieu de la config prod actuelle.
"""
import asyncio, copy, logging, sys
import pandas as pd
from config_loader import get_config
sys.path.insert(0, ".")
from metrics import compute_metrics

async def main():
    logging.basicConfig(level=logging.WARNING)

    # Paramètres OPTIMAUX identifiés dans backtest_multi (old_count_3)
    # sl_atr_mult=2.0, trailing_sl_enabled=False, min_confluences=3, no_struct=4
    opt_params = {
        "adx_required": True,
        "daily_filter_enabled": True,
        "kc_filter": True,
        "min_confluences": 3,
        "min_confluences_no_struct": 4,
        "min_score": None,
        "zigzag_window": 3,
        "min_swing_diff_pct": 0.5,
        "daily_trend_strict": False,
        "sl_atr_mult": 2.0,
        "trailing_sl_enabled": False,
    }

    watchlist = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "VET/USDT", "BNB/USDT", "HYPE/USDT:USDT"]
    capital = 150.0

    # ── 1) Récupérer la période couverte par les données (une seule fois) ──
    from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
    config_temp = copy.deepcopy(get_config())
    config_temp["signal"] = dict(opt_params)
    config_temp["candles_limit"] = 1000
    exchange_temp = await init_exchange_async()
    try:
        data_temp = await fetch_all_async(exchange_temp, symbols=watchlist, use_cache=True)
    finally:
        await exchange_temp.close()

    from datetime import datetime, timezone

    first_ts = None
    last_ts = None
    if data_temp:
        for sym, df in data_temp.items():
            if "timestamp" in df.columns:
                ts0 = df["timestamp"].iloc[0]
                ts1 = df["timestamp"].iloc[-1]
                if hasattr(ts0, "timestamp"):
                    e0 = int(ts0.timestamp() * 1000)
                    e1 = int(ts1.timestamp() * 1000)
                else:
                    e0 = int(ts0)
                    e1 = int(ts1)
                if first_ts is None or e0 < first_ts:
                    first_ts = e0
                if last_ts is None or e1 > last_ts:
                    last_ts = e1

    start_date = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    days_span = (last_ts - first_ts) / (1000 * 3600 * 24)

    print(f"=== CONFIG OPTIMALE (sl_atr_mult=2.0, pas de trailing) ===")
    print(f"Période de données : {start_date} → {end_date}  ({days_span:.0f} jours)")
    print(f"Capital initial : ${capital:.0f}")
    print()
    print(f"{'Levier':<8} | {'Trades':<6} | {'WR%':<6} | {'PF':<6} | {'PnL%':<10} | {'PnL$':<10} | {'DD%':<6} | {'Sharpe'}")
    print("-" * 80)

    # ── 2) Boucle sur les leviers ──
    for lev in [10, 15, 20]:
        import importlib, module4_backtest
        importlib.reload(module4_backtest)

        from module1_data_v3 import init_exchange_async, fetch_all_async, fetch_daily_all_async
        from module2_AT import clean_ohlcv, compute_indicators, get_daily_trend_at_timestamp
        from module3_signal import generate_signal

        config = copy.deepcopy(get_config())
        if "signal" not in config:
            config["signal"] = {}
        config["signal"].update(opt_params)
        config["risk"]["leverage"] = lev
        config["candles_limit"] = 1000
        config["daily_trend_strict"] = opt_params.get("daily_trend_strict", False)

        exchange = await init_exchange_async()
        try:
            data = await fetch_all_async(exchange, symbols=watchlist, use_cache=True)
            daily_data = {}
            if config["signal"].get("daily_filter_enabled"):
                daily_data = await fetch_daily_all_async(exchange, symbols=watchlist, use_cache=True)
        finally:
            await exchange.close()

        if not data:
            print(f"{lev:<8} | Aucune donnée")
            continue

        all_trades = []
        start_idx = 150
        for symbol, df in data.items():
            clean = clean_ohlcv(df)
            enriched = compute_indicators(clean, config, include_incomplete=False)
            if enriched.empty:
                continue
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
                        trade_result = module4_backtest.simulate_trade(future, sig, config)
                        all_trades.append({
                            "symbol": symbol,
                            "entry_date": enriched.iloc[i]["timestamp"],
                            "pnl_pct": trade_result["pnl_pct"],
                            "result": trade_result["result"]
                        })
                        i = trade_result["exit_idx"]
                i += 1

        trades_df = pd.DataFrame(all_trades)
        m = compute_metrics(trades_df, initial_capital=capital)
        pnl_dollars = capital * (1 + m["pnl_total"] / 100) - capital if not trades_df.empty else 0
        print(f"{lev:<8} | {m['trades']:<6} | {m['winrate']:>5.1f}% | {m['profit_factor']:>5.2f} | {m['pnl_total']:>8.2f}% | ${pnl_dollars:>8.2f} | {m['max_drawdown']:>5.1f}% | {m['sharpe']:>5.2f}")

    # Comparatif avec config prod
    print()
    print("=== RAPPEL Config Prod (sl_atr_mult=1.0, trailing) ===")
    print(f"{'Levier':<8} | {'Trades':<6} | {'WR%':<6} | {'PF':<6} | {'PnL%':<10} | {'PnL$':<10} | {'DD%':<6} | {'Sharpe'}")
    print("-" * 80)
    prod_results = [
        (10, 188, 53.7, 1.34, 684.15, 1026.23, 50.3, 1.82),
        (15, 188, 53.7, 1.34, 1026.23, 1539.34, 65.4, 1.82),
        (20, 188, 53.7, 1.34, 1368.30, 2052.45, 77.9, 1.82),
    ]
    for lev, tr, wr, pf, pnl, pnl_d, dd, sh in prod_results:
        print(f"{lev:<8} | {tr:<6} | {wr:>5.1f}% | {pf:>5.2f} | {pnl:>8.2f}% | ${pnl_d:>8.2f} | {dd:>5.1f}% | {sh:>5.2f}")

if __name__ == "__main__":
    asyncio.run(main())