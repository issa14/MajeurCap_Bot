"""
DPSK - Crypto Trading Bot
Point d'entrée principal (CLI)
"""

import argparse
import asyncio
import sys
import logging

# Configuration du logging global
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("DPSK")

async def run_live():
    """Lance une itération du bot de trading."""
    from bot_telegram import main as bot_main
    log.info("🚀 Démarrage du bot de trading Live (Bot Telegram)...")
    await bot_main()

async def run_listener():
    """Lance le listener de commandes Telegram."""
    from bot_listener import poll_updates
    log.info("📡 Démarrage du Listener de commandes Telegram...")
    await poll_updates()

async def run_backtest():
    """Lance le backtest multi-paramètres."""
    from backtest_multi import main as backtest_main
    log.info("📊 Démarrage du Backtest multi-paramètres...")
    await backtest_main()

async def run_check():
    """Vérifie la connexion à l'exchange."""
    from check_connection import run_check as connection_check
    log.info("🔍 Vérification de la connexion Binance...")
    await connection_check()

def main():
    parser = argparse.ArgumentParser(description="DPSK Crypto Trading Bot CLI")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true", help="Lancer un cycle de trading live")
    group.add_argument("--listen", action="store_true", help="Lancer le listener de commandes Telegram (polling)")
    group.add_argument("--backtest", action="store_true", help="Lancer le backtest multi-paramètres")
    group.add_argument("--check", action="store_true", help="Vérifier la connexion aux APIs")

    args = parser.parse_args()

    try:
        if args.live:
            asyncio.run(run_live())
        elif args.listen:
            asyncio.run(run_listener())
        elif args.backtest:
            asyncio.run(run_backtest())
        elif args.check:
            asyncio.run(run_check())
    except KeyboardInterrupt:
        log.info("Arrêt demandé par l'utilisateur.")
    except Exception as e:
        log.error(f"Erreur fatale : {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
