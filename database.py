import sqlite3
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: str = "warehouse.db"):
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS parts (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    part_number TEXT    NOT NULL,
                    car_brand   TEXT    NOT NULL,
                    car_model   TEXT,
                    description TEXT,
                    price       REAL,
                    quantity    INTEGER NOT NULL DEFAULT 0,
                    condition   TEXT,
                    market      TEXT,
                    side        TEXT,
                    lamp_type   TEXT,
                    photo_id    TEXT,
                    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # index for fast lookups
            conn.execute("CREATE INDEX IF NOT EXISTS idx_number ON parts(part_number)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brand  ON parts(car_brand)")
            # migrate existing DB — add market column if missing
            cols = [r[1] for r in conn.execute("PRAGMA table_info(parts)").fetchall()]
            if "market" not in cols:
                conn.execute("ALTER TABLE parts ADD COLUMN market TEXT")
            if "side" not in cols:
                conn.execute("ALTER TABLE parts ADD COLUMN side TEXT")
            if "lamp_type" not in cols:
                conn.execute("ALTER TABLE parts ADD COLUMN lamp_type TEXT")
            conn.commit()

    # ── CRUD ─────────────────────────────────

    def find_duplicate(self, part_number: str, car_brand: str) -> Optional[Dict]:
        """Find existing part by number and brand (case-insensitive)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parts WHERE UPPER(part_number)=UPPER(?) AND UPPER(car_brand)=UPPER(?)",
                (part_number.strip(), car_brand.strip())
            ).fetchone()
            return dict(row) if row else None

    def merge_part(self, existing_id: int, new_data: Dict[str, Any]):
        """Update only empty/None fields and add quantity."""
        with self._connect() as conn:
            existing = dict(conn.execute(
                "SELECT * FROM parts WHERE id=?", (existing_id,)
            ).fetchone())
            updates = []
            values  = []
            # Fields that can be merged (fill empty)
            for field in ['car_model','description','price','condition','market','side','lamp_type','photo_id']:
                if not existing.get(field) and new_data.get(field):
                    updates.append(f"{field}=?")
                    values.append(new_data[field])
            # Quantity always adds up
            if new_data.get('quantity', 0):
                updates.append("quantity=quantity+?")
                values.append(new_data['quantity'])
            if updates:
                updates.append("updated_at=CURRENT_TIMESTAMP")
                values.append(existing_id)
                conn.execute(
                    f"UPDATE parts SET {', '.join(updates)} WHERE id=?", values
                )
                conn.commit()

    def add_part(self, data: Dict[str, Any]) -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO parts
                    (part_number, car_brand, car_model, description,
                     price, quantity, condition, market, side, lamp_type, photo_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get("part_number"),
                data.get("car_brand"),
                data.get("car_model"),
                data.get("description"),
                data.get("price"),
                data.get("quantity", 0),
                data.get("condition"),
                data.get("market"),
                data.get("side"),
                data.get("lamp_type"),
                data.get("photo_id"),
            ))
            conn.commit()
            return cur.lastrowid

    def get_by_id(self, part_id: int) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM parts WHERE id = ?", (part_id,)).fetchone()
            return dict(row) if row else None

    def get_all(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM parts ORDER BY car_brand, car_model, part_number"
            ).fetchall()
            return [dict(r) for r in rows]

    def search_by_number(self, number: str) -> List[Dict]:
        """Case-insensitive partial match on part number."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM parts WHERE UPPER(part_number) LIKE UPPER(?)",
                (f"%{number}%",)
            ).fetchall()
            return [dict(r) for r in rows]

    def search_by_car(self, brand: str, model: Optional[str] = None) -> List[Dict]:
        with self._connect() as conn:
            if model:
                rows = conn.execute(
                    """SELECT * FROM parts
                       WHERE UPPER(car_brand) LIKE UPPER(?)
                         AND UPPER(car_model) LIKE UPPER(?)
                       ORDER BY part_number""",
                    (f"%{brand}%", f"%{model}%")
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM parts
                       WHERE UPPER(car_brand) LIKE UPPER(?)
                       ORDER BY car_model, part_number""",
                    (f"%{brand}%",)
                ).fetchall()
            return [dict(r) for r in rows]

    def smart_search(self, query: str) -> List[Dict]:
        """Search by partial number, brand, model, or any combination."""
        q = f"%{query.strip()}%"
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM parts
                   WHERE UPPER(part_number) LIKE UPPER(?)
                      OR UPPER(car_brand)   LIKE UPPER(?)
                      OR UPPER(car_model)   LIKE UPPER(?)
                      OR UPPER(description) LIKE UPPER(?)
                   ORDER BY car_brand, car_model, part_number""",
                (q, q, q, q)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_known_brands(self) -> List[str]:
        """Return sorted list of all distinct car brands in DB."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT car_brand FROM parts WHERE car_brand != '' ORDER BY car_brand"
            ).fetchall()
            return [r[0] for r in rows if r[0]]

    def update_field(self, part_id: int, field: str, value: Any):
        allowed = {
            "part_number", "car_brand", "car_model",
            "description", "price", "quantity", "condition", "market", "side", "lamp_type", "photo_id"
        }
        if field not in allowed:
            raise ValueError(f"Field '{field}' is not allowed to update.")
        with self._connect() as conn:
            conn.execute(
                f"UPDATE parts SET {field} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (value, part_id)
            )
            conn.commit()

    def change_quantity(self, part_id: int, delta: int) -> int:
        """Add delta to quantity (can be negative). Returns new quantity."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE parts
                   SET quantity = MAX(0, quantity + ?),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (delta, part_id)
            )
            conn.commit()
            row = conn.execute("SELECT quantity FROM parts WHERE id = ?", (part_id,)).fetchone()
            return row["quantity"]

    def set_quantity(self, part_id: int, qty: int):
        self.update_field(part_id, "quantity", max(0, qty))

    def delete_part(self, part_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM parts WHERE id = ?", (part_id,))
            conn.commit()
