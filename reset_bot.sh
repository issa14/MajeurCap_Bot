#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# reset_bot.sh — Remet le bot à zéro et le relance proprement
#
# Actions :
#   1. Stoppe tout processus main.py en cours
#   2. Vide la DB (toutes les tables) sans supprimer le fichier
#   3. Supprime bot.log (et les rotations)
#   4. Relance python3 main.py --all en arrière-plan
#
# ⚠️  Ferme tes positions sur Binance AVANT de lancer ce script.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="$SCRIPT_DIR/trading_bot.db"
LOG_PATH="$SCRIPT_DIR/bot.log"
PIDFILE="$SCRIPT_DIR/.bot.pid"

cd "$SCRIPT_DIR"

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       MajeurCap_Bot — RESET & RESTART    ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Confirmation ──────────────────────────────────────────────────────────────
warn "Cette action va :"
echo "  • Stopper le bot en cours"
echo "  • VIDER toutes les tables de la DB (positions, logs, cooldowns)"
echo "  • Supprimer bot.log"
echo "  • Relancer le bot"
echo ""
echo -e "${RED}⚠️  Ferme d'abord toutes tes positions sur Binance manuellement !${NC}"
echo ""
read -rp "Continuer ? [oui/N] : " confirm
if [[ "$confirm" != "oui" ]]; then
    echo "Annulé."
    exit 0
fi
echo ""

# ── 1. Stopper le bot ─────────────────────────────────────────────────────────
info "Arrêt des processus main.py en cours..."
PIDS=$(pgrep -f "python3 main.py" 2>/dev/null || true)
if [[ -n "$PIDS" ]]; then
    echo "$PIDS" | xargs kill -SIGTERM 2>/dev/null || true
    sleep 2
    # Force si toujours vivant
    REMAINING=$(pgrep -f "python3 main.py" 2>/dev/null || true)
    if [[ -n "$REMAINING" ]]; then
        echo "$REMAINING" | xargs kill -SIGKILL 2>/dev/null || true
        warn "SIGKILL envoyé (processus récalcitrant)"
    fi
    success "Bot arrêté (PIDs: $PIDS)"
else
    info "Aucun processus main.py trouvé."
fi

# ── 2. Vider la DB ───────────────────────────────────────────────────────────
if [[ -f "$DB_PATH" ]]; then
    info "Vidage de la base de données : $DB_PATH"
    python3 - <<EOF
import sqlite3
from pathlib import Path

db_path = Path("$DB_PATH")
con = sqlite3.connect(db_path)
cur = con.cursor()

# Récupérer toutes les tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cur.fetchall()]

for table in tables:
    cur.execute(f"DELETE FROM {table}")
    print(f"  ✓ Table '{table}' vidée")

# Reset des auto-increment
cur.execute("DELETE FROM sqlite_sequence") if "sqlite_sequence" in tables else None

con.commit()
con.close()
print(f"  DB vidée : {sum(1 for _ in tables)} table(s)")
EOF
    success "DB vidée"
else
    warn "DB introuvable ($DB_PATH) — sera créée au démarrage du bot."
fi

# ── 3. Supprimer les logs ─────────────────────────────────────────────────────
info "Suppression des logs..."
for f in "$LOG_PATH" "$LOG_PATH".1 "$LOG_PATH".2 "$LOG_PATH".3 "$LOG_PATH".4 "$LOG_PATH".5; do
    if [[ -f "$f" ]]; then
        rm -f "$f"
        echo "  ✓ Supprimé : $f"
    fi
done
success "Logs supprimés"

# ── 4. Relancer le bot ───────────────────────────────────────────────────────
info "Démarrage du bot..."
nohup python3 main.py --all > /dev/null 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PIDFILE"
sleep 2

# Vérifier qu'il tourne encore
if kill -0 "$BOT_PID" 2>/dev/null; then
    success "Bot démarré (PID: $BOT_PID — enregistré dans .bot.pid)"
else
    echo -e "${RED}[ERREUR]${NC} Le bot s'est arrêté immédiatement. Vérifie bot.log."
    exit 1
fi

echo ""
echo "────────────────────────────────────────────"
echo "  Commandes utiles :"
echo "  Logs live  : tail -f bot.log"
echo "  Stopper    : kill \$(cat .bot.pid)"
echo "  Statut     : kill -0 \$(cat .bot.pid) && echo running || echo stopped"
echo "────────────────────────────────────────────"
echo ""
