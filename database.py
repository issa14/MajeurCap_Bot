import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("database")

DB_PATH = Path("trading_bot.db")

class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        """Helper pour la connexion SQLite."""
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        """Initialise la table positions si elle n'existe pas."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    status TEXT NOT NULL, -- active, tp1_hit, closed
                    entry_price REAL NOT NULL,
                    entry_date TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    sl_price REAL NOT NULL,
                    tp1_price REAL NOT NULL,
                    tp2_price REAL NOT NULL,
                    partial_exit BOOLEAN DEFAULT 0,
                    sl_order_id TEXT,
                    tp1_order_id TEXT,
                    tp2_order_id TEXT,
                    exit_price REAL,
                    exit_date TEXT,
                    exit_reason TEXT,
                    pnl_pct REAL,
                    pnl_usd REAL
                )
            """)
            
            # Migration : Ajouter pnl_usd si elle n'existe pas (si la table existait déjà)
            cursor.execute("PRAGMA table_info(positions)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'pnl_usd' not in columns:
                cursor.execute("ALTER TABLE positions ADD COLUMN pnl_usd REAL")
            if 'tp1_order_id' not in columns:
                cursor.execute("ALTER TABLE positions ADD COLUMN tp1_order_id TEXT")
            if 'tp2_order_id' not in columns:
                cursor.execute("ALTER TABLE positions ADD COLUMN tp2_order_id TEXT")

            # Index unique pour éviter les doublons sur les positions actives
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_active_symbol 
                ON positions (symbol) WHERE status != 'closed'
            """)

            # Table pour le cooldown des signaux (anti-spam Telegram)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signal_cooldowns (
                    symbol TEXT PRIMARY KEY,
                    last_sent_at TEXT NOT NULL
                )
            """)
            # Table pour enregistrer chaque signal détecté
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    entry REAL,
                    sl REAL,
                    tp1 REAL,
                    tp2 REAL,
                    confluences TEXT,
                    traded BOOLEAN DEFAULT 0,
                    reject_reason TEXT
                );
            """)
            conn.commit()

    def get_signal_cooldowns(self) -> dict:
        """Charge tous les cooldowns depuis la DB."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, last_sent_at FROM signal_cooldowns")
            rows = cursor.fetchall()
            # Convertir en dict {symbol: datetime}
            cooldowns = {}
            for symbol, last_sent_str in rows:
                try:
                    # On stocke en ISO format pour la simplicité
                    cooldowns[symbol] = datetime.fromisoformat(last_sent_str)
                except Exception:
                    continue
            return cooldowns


    def insert_signal_log(self, symbol: str, direction: str, entry: float, sl: float,
                           tp1: float, tp2: float, confluences: list,
                           traded: bool, reject_reason: str = None):
        """Enregistre un signal détecté (tradé ou rejeté) pour l'historique/dashboard."""
        import json
        from datetime import datetime, timezone
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signals_log
                    (symbol, direction, detected_at, entry, sl, tp1, tp2, confluences, traded, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                direction,
                datetime.now(timezone.utc).isoformat(),
                entry,
                sl,
                tp1,
                tp2,
                json.dumps(confluences),
                int(traded),
                reject_reason,
            ))
            conn.commit()

    def get_recent_signals(self, limit: int = 20) -> list:
        """Retourne les N derniers signaux détectés (tradés ou non)."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, direction, detected_at, entry, sl, tp1, tp2, confluences, traded, reject_reason
                FROM signals_log
                ORDER BY detected_at DESC
                LIMIT ?
            """, (limit,))
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def update_signal_cooldown(self, symbol: str, last_sent_at: datetime) -> None:
        """Met à jour ou insère un cooldown pour un symbole."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signal_cooldowns (symbol, last_sent_at)
                VALUES (?, ?)
                ON CONFLICT(symbol) DO UPDATE SET last_sent_at = excluded.last_sent_at
            """, (symbol, last_sent_at.isoformat()))
            conn.commit()

    def insert_position(self, pos_data: dict) -> int:
        # pass removed
        """Insère une nouvelle position et retourne son ID."""
        with self._connect() as conn:
            cursor = conn.cursor()
            query = """
                INSERT INTO positions (
                    symbol, direction, status, entry_price, entry_date, 
                    quantity, sl_price, tp1_price, tp2_price, 
                    partial_exit, sl_order_id, tp1_order_id, tp2_order_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cursor.execute(query, (
                pos_data["symbol"], pos_data["direction"], pos_data["status"],
                pos_data["entry"], pos_data["entry_date"], pos_data["quantity"],
                pos_data["sl"], pos_data["tp1"], pos_data["tp2"],
                pos_data.get("partial_exit", 0), pos_data.get("sl_order_id"),
                pos_data.get("tp1_order_id"), pos_data.get("tp2_order_id")
            ))
            conn.commit()
            return cursor.lastrowid

    def update_position(self, pos_id: int, updates: dict):
        """Met à jour une position existante en filtrant l'ID et les alias."""
        if not updates:
            return

        # Mapper les clés dict vers les noms de colonnes réels
        column_map = {
            "entry": "entry_price",
            "sl": "sl_price",
            "tp1": "tp1_price",
            "tp2": "tp2_price"
        }

        # On construit un dict propre pour éviter de mettre à jour la même colonne deux fois
        # (ex: 'sl' et 'sl_price' pointent vers la même colonne). 
        # La dernière valeur rencontrée dans 'updates' gagne.
        clean_updates = {}
        for k, v in updates.items():
            if k == "id":
                continue
            col = column_map.get(k, k)
            clean_updates[col] = v

        if not clean_updates:
            return

        with self._connect() as conn:
            cursor = conn.cursor()
            set_clause = [f"{col} = ?" for col in clean_updates.keys()]
            values = list(clean_updates.values())
            values.append(pos_id)

            query = f"UPDATE positions SET {', '.join(set_clause)} WHERE id = ?"
            cursor.execute(query, values)
            conn.commit()

    def get_active_positions(self) -> list:
        """Retourne la liste des positions non clôturées."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE status != 'closed'")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_all_positions(self) -> list:
        """Retourne absolument toutes les positions (historique inclus)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            return [dict(row) for row in cursor.fetchall()]

    def get_realized_pnl_today(self, initial_capital: float = 1000.0) -> float:
        """Calcule la somme des PnL réalisés (%) de la journée en pondérant par le capital."""
        with self._connect() as conn:
            cursor = conn.cursor()
            today = datetime.now(timezone.utc).date().isoformat()
            
            # Somme des PnL USD pour les trades fermés aujourd'hui
            cursor.execute("""
                SELECT SUM(pnl_usd) FROM positions 
                WHERE status = 'closed' 
                AND date(exit_date) = ?
            """, (today,))
            result = cursor.fetchone()[0]
            
            realized_pnl_usd = result if result is not None else 0.0
            
            # Conversion en pourcentage basé sur le capital initial
            return (realized_pnl_usd / initial_capital) * 100 if initial_capital != 0 else 0.0

# Instance globale pour simplicité (ou à injecter)
db = DatabaseManager()
