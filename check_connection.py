import asyncio
import logging
import ccxt.async_support as ccxt_async
from config_loader import get_config

# Configuration du logging
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("check_connection")

async def run_check():
    # 1. Chargement de la configuration
    config = get_config()

    binance_cfg = config.get("binance_testnet", {})
    api_key = binance_cfg.get("api_key", "")
    api_secret = binance_cfg.get("api_secret", "")
    is_testnet = binance_cfg.get("testnet", True)

    if not api_key or not api_secret:
        log.error("API Key ou API Secret manquante dans le fichier config.yaml (section binance_testnet).")
        return

    # 2. Initialisation de l'exchange
    exchange = ccxt_async.binance({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
        },
    })
    
    if is_testnet:
        exchange.set_sandbox_mode(True)
        log.info("--- TESTNET BINANCE ACTIVÉ ---")
    else:
        log.info("--- MAINNET BINANCE ACTIVÉ (Prudence !) ---")

    try:
        # 3. Test fetch_balance
        log.info("Étape 1 : Tentative de récupération du solde (fetch_balance)...")
        balance = await exchange.fetch_balance()
        
        # Affichage des soldes non nuls pour plus de clarté
        non_zero_balances = {k: v for k, v in balance['total'].items() if v > 0}
        if non_zero_balances:
            log.info(f"✅ Connexion réussie ! Soldes trouvés : {non_zero_balances}")
        else:
            log.info("✅ Connexion réussie ! (Mais tous les soldes sont à 0)")

        # 4. Test fetch_orders (sur BTC/USDT par défaut)
        symbol = "BTC/USDT"
        log.info(f"Étape 2 : Tentative de récupération des ordres pour {symbol}...")
        orders = await exchange.fetch_orders(symbol, limit=5)
        
        log.info(f"✅ Succès ! Nombre d'ordres récents récupérés : {len(orders)}")
        
        if orders:
            last_order = orders[-1]
            log.info(f"Détail du dernier ordre : ID={last_order['id']}, Status={last_order['status']}, Side={last_order['side']}")
        else:
            log.info("Aucun historique d'ordre trouvé sur ce compte pour ce symbole.")

        log.info("\n🎉 Félicitations : La connexion API est parfaitement opérationnelle !")

    except Exception as e:
        log.error(f"❌ Échec de la vérification : {e}")
        log.error("Vérifiez vos clés API et assurez-vous que le mode Testnet est correctement configuré.")
    
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(run_check())
