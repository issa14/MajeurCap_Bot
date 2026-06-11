import ccxt
import asyncio
from config_loader import get_config

async def close_all():
    config = get_config()
    binance_cfg = config.get("binance_testnet", {})
    exchange = ccxt.binance({
        "apiKey": binance_cfg.get("api_key"),
        "secret": binance_cfg.get("api_secret"),
        "options": {"defaultType": "spot"},
    })
    exchange.set_sandbox_mode(True)
    
    try:
        positions = exchange.fetch_positions() # Note: ccxt spot might not support fetch_positions, might need fetch_balance or orders
        # Since this is Spot, we cancel all open orders and sell all non-USDT assets
        print("Annulation de tous les ordres ouverts...")
        exchange.cancel_all_orders()
        
        balance = exchange.fetch_balance()
        for asset, amount in balance['total'].items():
            if asset != 'USDT' and amount > 0:
                print(f"Vente de {amount} {asset}...")
                try:
                    exchange.create_market_sell_order(f"{asset}/USDT", amount)
                except Exception as e:
                    print(f"Impossible de vendre {asset}: {e}")
    finally:
        await exchange.close()

asyncio.run(close_all())
