"""
DPSK - Crypto Trading Bot
Point d'entrée principal (CLI)
"""

import argparse
import asyncio
import sys
import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Configuration du logging global
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("bot.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"),
    ],
)
log = logging.getLogger("DPSK")

# ─── PID‑file anti‑double‑lancement ───────────────────────────────────────────
PID_FILE = Path(".bot.pid")

def _pid_is_running(pid: int) -> bool:
    """Vérifie si un processus avec le PID donné est en cours d'exécution."""
    try:
        os.kill(pid, 0)  # signal 0 = test uniquement, ne kill pas
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False

def _acquire_pid_lock() -> bool:
    """
    Écrit le PID courant dans .bot.pid après avoir vérifié qu'aucune autre
    instance n'est déjà en cours. Retourne True si le lock a été acquis.
    """
    current_pid = os.getpid()

    if PID_FILE.exists():
        try:
            stored_pid = int(PID_FILE.read_text().strip() or "0")
        except (ValueError, FileNotFoundError):
            stored_pid = 0

        if stored_pid and stored_pid != current_pid and _pid_is_running(stored_pid):
            log.critical(
                f"Il y a déjà une instance du bot qui tourne (PID {stored_pid}). "
                f"Arrêtez‑la d'abord (`kill {stored_pid}`) ou attendez son arrêt."
            )
            return False

    PID_FILE.write_text(str(current_pid))
    return True

def _release_pid_lock() -> None:
    """Supprime le fichier .bot.pid si c'est bien notre PID qui est dedans."""
    try:
        if PID_FILE.exists():
            stored = PID_FILE.read_text().strip()
            if stored == str(os.getpid()):
                PID_FILE.unlink()
    except Exception:
        pass

async def run_live():
    """Lance une itération du bot de trading."""
    from bot_telegram import main as bot_main
    # Nettoyage DB au démarrage (signal_logs > 30 jours)
    from database import db
    db.cleanup_old_records(days=30)
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

async def run_all():
    """Lance simultanément le bot de trading et le listener Telegram."""
    log.info("🔥 Démarrage du bot COMPLET (Live Trading + Telegram Listener)...")
    await asyncio.gather(run_live(), run_listener())

def main():
    parser = argparse.ArgumentParser(description="DPSK Crypto Trading Bot CLI")
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true", help="Lancer un cycle de trading live")
    group.add_argument("--listen", action="store_true", help="Lancer le listener de commandes Telegram (polling)")
    group.add_argument("--all", action="store_true", help="Lancer le bot complet (live + listener)")
    group.add_argument("--backtest", action="store_true", help="Lancer le backtest multi-paramètres")
    group.add_argument("--check", action="store_true", help="Vérifier la connexion aux APIs")

    args = parser.parse_args()

    # Les modes longue durée nécessitent un lock pour éviter le double lancement
    needs_lock = args.live or args.listen or args.all
    if needs_lock and not _acquire_pid_lock():
        log.critical("Impossible d'acquérir le lock PID — bot déjà en cours d'exécution. Abandon.")
        sys.exit(1)

    try:
        if args.live:
            asyncio.run(run_live())
        elif args.listen:
            asyncio.run(run_listener())
        elif args.all:
            asyncio.run(run_all())
        elif args.backtest:
            asyncio.run(run_backtest())
        elif args.check:
            asyncio.run(run_check())
    except KeyboardInterrupt:
        log.info("Arrêt demandé par l'utilisateur.")
    except Exception as e:
        log.error(f"Erreur fatale : {e}", exc_info=True)
        sys.exit(1)
    finally:
        if needs_lock:
            _release_pid_lock()

if __name__ == "__main__":
    main()
