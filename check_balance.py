import ccxt
from config_loader import get_config

# Récupération de la configuration
config = get_config()
# Correction du nom de clé pour correspondre à config.yaml
binance_cfg = config.get('binance_testnet', {})

print(f"DEBUG: binance_cfg chargés : {binance_cfg}")

# Initialisation basée sur la config existante
exchange = ccxt.binance({
    'apiKey': binance_cfg.get('api_key'),
    'secret': binance_cfg.get('api_secret'),
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True,
    }
})

# Activation propre du mode Demo Trading
exchange.enable_demo_trading(True)

try:
    # Récupération du solde
    balance = exchange.fetch_balance()
    watchlist = config.get('watchlist', [])
    # Extraire les assets de la watchlist (ex: BTC/USDT -> BTC)
    assets_in_watchlist = {s.split('/')[0] for s in watchlist} | {'USDT'}
    
    print("--- Vos Actifs (Watchlist + USDT) ---")
    
    filtered_free = {k: v for k, v in balance['free'].items() if k in assets_in_watchlist and v > 0}
    filtered_total = {k: v for k, v in balance['total'].items() if k in assets_in_watchlist and v > 0}
    
    print("Disponible :", filtered_free)
    print("Total :", filtered_total)

except Exception as e:
    print(f"Erreur lors de la connexion : {e}")
