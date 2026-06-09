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

    def _init_db(self):
        """Initialise la table positions si elle n'existe pas."""
        with sqlite3.connect(self.db_path) as conn:
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
                    exit_price REAL,
                    exit_date TEXT,
                    exit_reason TEXT,
                    pnl_pct REAL
                )
            """)
            # Index unique pour éviter les doublons sur les positions actives
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_active_symbol 
                ON positions (symbol) WHERE status != 'closed'
            """)
            conn.commit()

    def insert_position(self, pos_data: dict) -> int:
        """Insère une nouvelle position et retourne son ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            query = """
                INSERT INTO positions (
                    symbol, direction, status, entry_price, entry_date, 
                    quantity, sl_price, tp1_price, tp2_price, 
                    partial_exit, sl_order_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cursor.execute(query, (
                pos_data["symbol"], pos_data["direction"], pos_data["status"],
                pos_data["entry"], pos_data["entry_date"], pos_data["quantity"],
                pos_data["sl"], pos_data["tp1"], pos_data["tp2"],
                pos_data.get("partial_exit", 0), pos_data.get("sl_order_id")
            ))
            conn.commit()
            return cursor.lastrowid

    def update_position(self, pos_id: int, updates: dict):
        """Met à jour une position existante."""
        if not updates:
            return
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Mapper les clés dict vers les noms de colonnes si nécessaire
            # On gère ici les cas spécifiques de trade_manager.py
            column_map = {
                "entry": "entry_price",
                "sl": "sl_price",
                "tp1": "tp1_price",
                "tp2": "tp2_price"
            }
            
            set_clause = []
            values = []
            for k, v in updates.items():
                col = column_map.get(k, k)
                set_clause.append(f"{col} = ?")
                values.append(v)
            
            values.append(pos_id)
            query = f"UPDATE positions SET {', '.join(set_clause)} WHERE id = ?"
            cursor.execute(query, values)
            conn.commit()

    def get_active_positions(self) -> list:
        """Retourne la liste des positions non clôturées."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions WHERE status != 'closed'")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_all_positions(self) -> list:
        """Retourne absolument toutes les positions (historique inclus)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM positions")
            return [dict(row) for row in cursor.fetchall()]

    def get_realized_pnl_today(self) -> float:
        """Calcule la somme des PnL réalisés (%) de la journée."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Utilisation de UTC pour la cohérence avec Binance
            today = datetime.now(timezone.utc).date().isoformat()
            
            # Somme des PnL pour les trades fermés aujourd'hui
            cursor.execute("""
                SELECT SUM(pnl_pct) FROM positions 
                WHERE status = 'closed' 
                AND date(exit_date) = ?
            """, (today,))
            result = cursor.fetchone()[0]
            
            # Le PnL est déjà en pourcentage, on retourne la somme des %
            return result if result is not None else 0.0

# Instance globale pour simplicité (ou à injecter)
db = DatabaseManager()
