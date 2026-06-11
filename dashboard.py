import asyncio
import logging
from datetime import datetime

import ccxt.async_support as ccxt_async

from config_loader import get_config
from database import db
from trade_manager import check_circuit_breaker

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.ERROR)
log = logging.getLogger("dashboard")

# ─── Exchange ─────────────────────────────────────────────────────────────────
async def get_exchange(config: dict) -> ccxt_async.binance:
    binance_cfg = config.get("binance_testnet", {})
    exchange = ccxt_async.binance({
        "apiKey": binance_cfg.get("api_key", ""),
        "secret": binance_cfg.get("api_secret", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })
    if binance_cfg.get("testnet", True):
        exchange.set_sandbox_mode(True)
    return exchange

# ─── Métriques ────────────────────────────────────────────────────────────────
def compute_win_rate(closed_positions: list) -> tuple[float, int, int]:
    """Retourne (win_rate_pct, nb_wins, nb_total)."""
    if not closed_positions:
        return 0.0, 0, 0
    wins = sum(1 for p in closed_positions if p.get("pnl_pct", 0) > 0)
    total = len(closed_positions)
    return (wins / total * 100), wins, total

def compute_unrealized_pnl(active_positions: dict, tickers: dict) -> float:
    """Calcule le PnL latent total en USD."""
    total = 0.0
    for sym, pos in active_positions.items():
        entry = pos.get("entry_price") or pos.get("entry") or 0
        qty   = pos.get("quantity", 0)
        price = tickers.get(sym, {}).get("last", entry)
        if entry <= 0 or qty <= 0 or price <= 0:
            continue
        raw_pnl = (price - entry) * qty
        total += raw_pnl if pos["direction"] == "LONG" else -raw_pnl
    return total

def compute_account_equity(balance: dict, tickers: dict) -> tuple[float, float]:
    """Retourne (usdt_cash, total_equity_usd)."""
    usdt_cash = balance["total"].get("USDT", 0.0)
    assets_value = 0.0
    for asset, amount in balance["total"].items():
        if asset == "USDT" or amount <= 0:
            continue
        price = tickers.get(f"{asset}/USDT", {}).get("last", 0)
        if price > 0:
            assets_value += amount * price
    return usdt_cash, usdt_cash + assets_value

def compute_exposure(active_positions: dict, tickers: dict, equity: float) -> tuple[float, float]:
    """Retourne (exposure_usd, exposure_pct)."""
    exposure_usd = sum(
        pos["quantity"] * tickers.get(sym, {}).get("last", pos.get("entry_price", 0))
        for sym, pos in active_positions.items()
    )
    exposure_pct = (exposure_usd / equity * 100) if equity > 0 else 0.0
    return exposure_usd, exposure_pct

# ─── Rendu lignes positions ────────────────────────────────────────────────────
def render_position_line(sym: str, pos: dict, tickers: dict) -> str:
    entry = pos.get("entry_price") or pos.get("entry") or 0
    sl    = pos.get("sl_price") or pos.get("sl") or 0
    price = tickers.get(sym, {}).get("last", entry)

    if entry > 0:
        pnl_pct = (
            (price - entry) / entry * 100
            if pos["direction"] == "LONG"
            else (entry - price) / entry * 100
        )
    else:
        pnl_pct = 0.0

    sl_dist  = abs(price - sl) / price * 100 if price > 0 else 0.0
    sl_warn  = " ⚠️" if sl_dist < 1.0 else ""
    dir_icon = "🟢" if pos["direction"] == "LONG" else "🔴"

    return (
        f"{dir_icon} {sym:<10} "
        f"Entry: {entry:>8.4f}  "
        f"Now: {price:>8.4f}  "
        f"PnL: {pnl_pct:>+6.1f}%  "
        f"SL: {sl_dist:>4.1f}%{sl_warn}"
    )

# ─── Dashboard principal ───────────────────────────────────────────────────────
async def get_dashboard_text() -> str:
    config   = get_config()
    risk_cfg = config.get("risk", {})
    watchlist = config.get("watchlist", [])

    exchange = await get_exchange(config)
    try:
        balance, tickers = await asyncio.gather(
            exchange.fetch_balance(),
            exchange.fetch_tickers(watchlist),
        )
    except Exception as exc:
        await exchange.close()
        return f"❌ Erreur réseau : {exc}"

    try:
        all_positions    = db.get_all_positions()
        active_positions = {
            p["symbol"]: p
            for p in all_positions
            if p.get("status") != "closed"
        }
        closed_positions = [p for p in all_positions if p.get("status") == "closed"]

        # ── Métriques ────────────────────────────────────────────────────────
        usdt_cash, equity       = compute_account_equity(balance, tickers)
        unrealized_pnl          = compute_unrealized_pnl(active_positions, tickers)
        exposure_usd, expo_pct  = compute_exposure(active_positions, tickers, equity)

        dd_jour      = db.get_realized_pnl_today()
        dd_limit     = risk_cfg.get("daily_loss_limit", -5.0)
        max_expo     = risk_cfg.get("max_exposure", 30)

        realized_pnl = sum(p.get("pnl_pct", 0) for p in closed_positions)
        win_rate, wins, nb_trades = compute_win_rate(closed_positions)

        is_breached  = await check_circuit_breaker(config)

        # ── En-tête ──────────────────────────────────────────────────────────
        status_icon  = "🚨" if is_breached else "✅"
        status_label = "HALTED" if is_breached else "OPERATIONAL"
        dd_alert     = "⚠️" if dd_jour <= dd_limit else ""

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "╔══════════════════════════════════════╗",
            f"║   📊  DPSK DASHBOARD PRO  —  {datetime.now().strftime('%H:%M:%S')}  ║",
            "╚══════════════════════════════════════╝",
            "",
            f"  {status_icon}  Status      : {status_label}",
            f"  🕒  Scan        : {now}",
            "",
            "─── COMPTE ─────────────────────────────",
            f"  💵  Cash USDT   : {usdt_cash:>10.2f} USD",
            f"  📦  Assets      : {equity - usdt_cash:>10.2f} USD",
            f"  🏦  Equity      : {equity:>10.2f} USD",
            f"  📈  PnL Latent  : {unrealized_pnl:>+10.2f} USD",
            "",
            "─── RISQUE ─────────────────────────────",
            f"  📉  DD Jour     : {dd_jour:>+6.1f}% / {dd_limit:.1f}%  {dd_alert}",
            f"  ⚖️  Exposition   : {expo_pct:>5.1f}%  / {max_expo:.0f}%",
            f"  💰  Expo USD    : {exposure_usd:>10.2f} USD",
            "",
            "─── PERFORMANCE ────────────────────────",
            f"  ✅  PnL Réalisé : {realized_pnl:>+6.2f}%",
            f"  🎯  Win Rate    : {win_rate:>5.1f}%  ({wins}/{nb_trades} trades)",
            "",
        ]

        # ── Positions actives ─────────────────────────────────────────────────
        if active_positions:
            lines.append("─── POSITIONS ACTIVES ──────────────────")
            for sym, pos in active_positions.items():
                lines.append("  " + render_position_line(sym, pos, tickers))
            lines.append("")
        else:
            lines.append("─── POSITIONS ACTIVES ──────────────────")
            lines.append("  (aucune position ouverte)")
            lines.append("")

        lines.append("════════════════════════════════════════")

        return "\n".join(lines)

    except Exception as exc:
        log.exception("Erreur inattendue dans le dashboard")
        return f"❌ Erreur Dashboard : {exc}"
    finally:
        await exchange.close()

# ─── Telegram (Markdown) ──────────────────────────────────────────────────────
async def get_dashboard_telegram() -> str:
    """Version Markdown pour Telegram (MarkdownV2 compatible)."""
    text = await get_dashboard_text()
    # Encapsule dans un bloc code pour un rendu monospace propre sur Telegram
    return f"```\n{text}\n```"

# ─── Entrypoint terminal ──────────────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        print(await get_dashboard_text())
    asyncio.run(main())
