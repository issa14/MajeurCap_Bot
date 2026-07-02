"""
Dashboard API — expose les données du bot en JSON pour le dashboard web local.
Lancer avec : python3 dashboard_api.py
Puis ouvrir dashboard.html dans le navigateur.
"""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config_loader import get_config
from database import db
from trade_manager import check_circuit_breaker
from dashboard import (
    get_exchange,
    compute_account_equity,
    compute_unrealized_pnl,
    compute_exposure,
    compute_win_rate,
)
from execution import fetch_positions_pnl

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("dashboard_api")


async def _fetch_with_retry(coro_func, *args, attempts: int = 5, delay: float = 2.0):
    """
    Retry simple pour contourner un bug connu CCXT/Binance Demo où la connexion
    se ferme parfois en plein milieu d'une réponse (StreamReader non parsé).
    Voir https://github.com/ccxt/ccxt/issues/27544
    """
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_func(*args)
        except AttributeError as exc:
            if "StreamReader" in str(exc):
                last_exc = exc
                log.warning(f"Tentative {attempt}/{attempts} échouée (StreamReader bug Binance Demo), retry dans {delay}s")
                await asyncio.sleep(delay)
            else:
                raise
        except Exception as exc:
            last_exc = exc
            log.warning(f"Tentative {attempt}/{attempts} échouée ({exc}), retry dans {delay}s")
            await asyncio.sleep(delay)
    raise last_exc


app = FastAPI(title="MajeurCap_Bot Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local only — pas exposé publiquement
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/dashboard")
async def get_dashboard_data():
    """Retourne l'état complet du bot : equity, positions, performance, risque."""
    config = get_config()
    risk_cfg = config.get("risk", {})
    watchlist = config.get("watchlist", [])

    exchange = await get_exchange(config)
    try:
        balance = await _fetch_with_retry(exchange.fetch_balance)
    except Exception as exc:
        log.exception("Échec fetch_balance() après retries")
        await exchange.close()
        return {"error": f"fetch_balance failed after retries: {exc}"}

    try:
        raw_tickers = await _fetch_with_retry(exchange.fetch_tickers, watchlist)
    except Exception as exc:
        log.exception("Échec fetch_tickers() après retries")
        await exchange.close()
        return {"error": f"fetch_tickers failed after retries: {exc}"}

    try:
        # Normaliser les clés : CCXT retourne "SUI/USDT:USDT" (format futures perpetual)
        # mais le reste du code (DB, config, watchlist) utilise "SUI/USDT"
        tickers = {sym.split(":")[0]: t for sym, t in raw_tickers.items()}
        # Ensure ticker data includes active position symbols not in watchlist
        all_positions = db.get_all_positions()
        active_symbols = {p["symbol"] for p in all_positions if p.get("status") != "closed"}
        missing_symbols = list(active_symbols - set(tickers.keys()))
        if missing_symbols:
            extra_tickers = await exchange.fetch_tickers(missing_symbols)
            tickers.update(extra_tickers)
    except Exception as exc:
        log.exception("Échec normalisation tickers")
        await exchange.close()
        return {"error": f"ticker normalization failed: {exc}"}

    try:
        all_positions = db.get_all_positions()
        active_positions = {
            p["symbol"]: p for p in all_positions if p.get("status") != "closed"
        }
        closed_positions = [p for p in all_positions if p.get("status") == "closed"]

        usdt_cash, equity = compute_account_equity(balance, tickers)
        unrealized_pnl = compute_unrealized_pnl(active_positions, tickers)
        exposure_usd, expo_pct = compute_exposure(active_positions, tickers, equity)

        dd_jour = db.get_realized_pnl_today(initial_capital=equity)
        dd_limit = risk_cfg.get("daily_loss_limit", -5.0)
        max_expo = risk_cfg.get("max_exposure", 30)

        realized_pnl = sum(p.get("pnl_pct") or 0 for p in closed_positions)
        win_rate, wins, nb_trades = compute_win_rate(closed_positions)
        is_breached = await check_circuit_breaker(config)

        # Récupérer le PnL réel depuis Binance (inclut levier, funding, frais)
        binance_pnl = await fetch_positions_pnl()

        positions_out = []
        for sym, pos in active_positions.items():
            entry = pos.get("entry_price") or pos.get("entry") or 0
            sl = pos.get("sl_price") or pos.get("sl") or 0
            price = tickers.get(sym, {}).get("last", entry)
            sl_dist = abs(price - sl) / price * 100 if price > 0 else 0.0

            # Utiliser le PnL réel de Binance si disponible
            bpos = binance_pnl.get(sym)
            if bpos and bpos["pnl_pct"] is not None:
                pnl_pct = bpos["pnl_pct"]
                leverage = bpos["leverage"]
            else:
                # Fallback : calcul local (sans levier)
                pnl_pct = (
                    (price - entry) / entry * 100
                    if entry > 0 and pos["direction"] == "LONG"
                    else (entry - price) / entry * 100 if entry > 0 else 0.0
                )
                leverage = None

            positions_out.append({
                    "symbol": sym,
                    "direction": pos["direction"],
                    "entry": entry,
                    "current_quantity": pos.get("current_quantity") or pos.get("quantity"),
                    "tp1_status": pos.get("tp1_status", "PENDING"),
                    "tp2_status": pos.get("tp2_status", "PENDING"),
                    "partial_exit": bool(pos.get("partial_exit")),
                    "current_price": price,
                    "pnl_pct": round(pnl_pct, 2),
                    "leverage": round(leverage, 1) if leverage is not None else None,
                    "sl_distance_pct": round(sl_dist, 2),
                    "sl_warning": sl_dist < 1.0,
                })

        return {
            "status": "HALTED" if is_breached else "OPERATIONAL",
            "equity": {
                "cash_usdt": round(usdt_cash, 2),
                "total_equity": round(equity, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
            },
            "risk": {
                "daily_drawdown_pct": round(dd_jour, 2),
                "daily_drawdown_limit_pct": dd_limit,
                "exposure_pct": round(expo_pct, 2),
                "exposure_usd": round(exposure_usd, 2),
                "max_exposure_pct": max_expo,
            },
            "performance": {
                "realized_pnl_pct": round(realized_pnl, 2),
                "win_rate_pct": round(win_rate, 2),
                "wins": wins,
                "total_trades": nb_trades,
            },
            "active_positions": positions_out,
        }
    except Exception as exc:
        log.exception("Erreur dashboard API")
        return {"error": str(exc)}
    finally:
        await exchange.close()


@app.get("/api/history")
async def get_trade_history(limit: int = 30):
    """Retourne l'historique des positions fermées, plus récentes en premier."""
    all_positions = db.get_all_positions()
    closed = [p for p in all_positions if p.get("status") == "closed"]
    closed_sorted = sorted(closed, key=lambda p: p.get("exit_date") or "", reverse=True)
    return {"trades": closed_sorted[:limit]}


@app.get("/api/signals")
async def get_recent_signals(limit: int = 20):
    """Retourne les derniers signaux détectés, tradés ou rejetés."""
    return {"signals": db.get_recent_signals(limit)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
