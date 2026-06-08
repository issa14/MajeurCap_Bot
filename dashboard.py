import asyncio
import logging
import json
from pathlib import Path
from tabulate import tabulate
from datetime import datetime
import ccxt.async_support as ccxt_async
from config_loader import get_config

# ─── Configuration ───────────────────────────────────────────────────────────
POSITIONS_FILE = Path("positions.json")

logging.basicConfig(level=logging.ERROR)
log = logging.getLogger("dashboard")

def load_local_positions():
    if not POSITIONS_FILE.exists():
        return []
    with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

async def get_exchange(config):
    binance_cfg = config.get("binance_testnet", {})
    exchange = ccxt_async.binance({
        "apiKey": binance_cfg.get("api_key", ""),
        "secret": binance_cfg.get("api_secret", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "spot"}
    })
    if binance_cfg.get("testnet", True):
        exchange.set_sandbox_mode(True)
    return exchange

async def get_dashboard_text():
    """Génère le texte du dashboard pour Telegram ou Terminal."""
    config = get_config()
    watchlist = config.get("watchlist", [])
    exchange = await get_exchange(config)
    
    try:
        tasks = [exchange.fetch_balance(), exchange.fetch_tickers(watchlist)]
        balance, tickers = await asyncio.gather(*tasks)
        local_positions = load_local_positions()
        active_positions = {p['symbol']: p for p in local_positions if p.get("status") != "closed"}

        # 1. WALLET
        bal_data = []
        total_unrealized_usd = 0
        total_assets_value_usd = 0

        for symbol in watchlist:
            asset = symbol.split('/')[0]
            total = balance['total'].get(asset, 0)
            current_price = tickers[symbol]['last'] if symbol in tickers else 0
            total_assets_value_usd += (total * current_price)
            
            pnl_str = "0"
            if symbol in active_positions:
                pos = active_positions[symbol]
                pnl_usd = (current_price - pos['entry']) * pos['quantity'] if pos['direction'] == "LONG" else (pos['entry'] - current_price) * pos['quantity']
                total_unrealized_usd += pnl_usd
                pnl_str = f"{pnl_usd:+.2f}"

            if total > 0 or symbol in active_positions:
                bal_data.append([asset, f"{total:.2f}", pnl_str])
        
        # 2. RÉSUMÉ
        closed_pos = [p for p in local_positions if p.get("status") == "closed"]
        realized_pnl = sum(p.get("pnl_pct", 0) for p in closed_pos)
        usdt_cash = balance['total'].get('USDT', 0)
        account_equity = usdt_cash + total_assets_value_usd

        # Construction du message final (Format Code pour garder l'alignement)
        output = f"📊 *DPSK DASHBOARD*\n_{datetime.now().strftime('%d/%m %H:%M:%S')}_\n\n"
        output += "```\n"
        output += tabulate(bal_data, headers=["Asset", "Qty", "PnL$"], tablefmt="simple")
        output += "\n```\n"
        output += f"💰 *Cash:* {usdt_cash:.2f} USDT\n"
        output += f"🚀 *Equity:* {account_equity:.2f} USD\n"
        output += f"📈 *PnL Latent:* {total_unrealized_usd:+.2f} USD\n"
        output += f"✅ *PnL Réalisé:* {realized_pnl:+.2f}%"
        
        return output

    except Exception as e:
        return f"❌ Erreur Dashboard : {str(e)}"
    finally:
        await exchange.close()

if __name__ == "__main__":
    # Si lancé directement, on affiche dans le terminal
    async def main():
        print(await get_dashboard_text())
    asyncio.run(main())
