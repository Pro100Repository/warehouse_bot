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
                    photo_ids   TEXT,
                    embedding   TEXT,
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
            if "photo_ids" not in cols:
                conn.execute("ALTER TABLE parts ADD COLUMN photo_ids TEXT")
            if "embedding" not in cols:
                conn.execute("ALTER TABLE parts ADD COLUMN embedding TEXT")
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
                     price, quantity, condition, market, side, lamp_type, photo_id, photo_ids, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                data.get("photo_ids"),
                data.get("embedding"),
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
        """Search by partial number, brand, model, description, side or lamp_type.

        Supports multi-word queries: each token must match at least one field,
        and ALL tokens must match (AND logic between tokens, OR across fields).
        Example: "BMW 4" → finds parts where one field contains "BMW"
                           AND another field contains "4".
        Also tries full-phrase match first for best relevance.
        """
        tokens = query.strip().split()
        if not tokens:
            return []

        # Для кириличних полів (side, lamp_type) SQLite UPPER() не працює,
        # тому додатково готуємо токени з першою великою літерою
        def cyrillic_variants(token: str) -> list[str]:
            """Повертає варіанти токена для пошуку без UPPER(): оригінал + Title + lower."""
            return list({token, token.title(), token.lower(), token.upper()})


        # При пошуку кількох слів (напр. "BMW 4") номер деталі не враховуємо,
        # щоб уникнути хибних збігів по цифрах у номерах
        # UPPER() в SQLite працює лише для ASCII.
        # Латинські поля (part_number, market) — через UPPER().
        # Кириличні поля (car_brand, car_model, description, side, lamp_type)
        # — без UPPER(), замість цього cyrillic_variants() генерує всі регістри.
        if len(tokens) > 1:
            FIELDS = [
                "UPPER(COALESCE(market,''))",  # ASCII
                "COALESCE(car_brand,'')",       # кирилиця
                "COALESCE(car_model,'')",       # кирилиця
                "COALESCE(description,'')",     # кирилиця
                "COALESCE(side,'')",            # кирилиця
                "COALESCE(lamp_type,'')",       # кирилиця
            ]
        else:
            FIELDS = [
                "UPPER(part_number)",           # ASCII
                "UPPER(COALESCE(market,''))",   # ASCII
                "COALESCE(car_brand,'')",       # кирилиця
                "COALESCE(car_model,'')",       # кирилиця
                "COALESCE(description,'')",     # кирилиця
                "COALESCE(side,'')",            # кирилиця
                "COALESCE(lamp_type,'')",       # кирилиця
            ]

        with self._connect() as conn:
            # ── Score expression: count how many tokens match any field ──────
            score_parts = []
            params: list = []

            for token in tokens:
                field_checks_list = []
                for f in FIELDS:
                    if "UPPER(" in f:
                        field_checks_list.append(f"{f} LIKE ?")
                        params.append(f"%{token.upper()}%")
                    else:
                        variants = cyrillic_variants(token)
                        conds = " OR ".join([f"{f} LIKE ?"] * len(variants))
                        field_checks_list.append(f"({conds})")
                        params.extend(f"%{v}%" for v in variants)
                field_checks = " OR ".join(field_checks_list)
                score_parts.append(f"CASE WHEN ({field_checks}) THEN 1 ELSE 0 END")

            score_expr = " + ".join(score_parts)

            # ── WHERE: every token must match at least one field (AND) ───────
            where_clauses = []
            where_params: list = []
            for token in tokens:
                clause_parts = []
                for f in FIELDS:
                    if "UPPER(" in f:
                        clause_parts.append(f"{f} LIKE ?")
                        where_params.append(f"%{token.upper()}%")
                    else:
                        variants = cyrillic_variants(token)
                        conds = " OR ".join([f"{f} LIKE ?"] * len(variants))
                        clause_parts.append(f"({conds})")
                        where_params.extend(f"%{v}%" for v in variants)
                where_clauses.append(f"({' OR '.join(clause_parts)})")

            where_sql = " AND ".join(where_clauses)

            rows = conn.execute(
                f"""SELECT *, ({score_expr}) AS match_score
                    FROM parts
                    WHERE {where_sql}
                    ORDER BY match_score DESC, car_brand, car_model, part_number""",
                params + where_params
            ).fetchall()

            # Fallback: якщо AND між токенами нічого не знайшов —
            # шукаємо повну фразу як один рядок в description
            if not rows and len(tokens) > 1:
                phrase = query.strip()
                variants = cyrillic_variants(phrase)
                phrase_fields = ["COALESCE(description,'')", "COALESCE(car_model,'')", "COALESCE(car_brand,'')"]
                phrase_clauses = " OR ".join(
                    f"{f} LIKE ?" for f in phrase_fields for _ in variants
                )
                phrase_params = [f"%{v}%" for _ in phrase_fields for v in variants]
                rows = conn.execute(
                    f"""SELECT *, 1 AS match_score
                        FROM parts
                        WHERE {phrase_clauses}
                        ORDER BY car_brand, car_model, part_number""",
                    phrase_params
                ).fetchall()

            return [dict(r) for r in rows]

    def photo_search(self,
                     brand: Optional[str] = None,
                     model: Optional[str] = None,
                     lamp_type: Optional[str] = None,
                     side: Optional[str] = None,
                     part_number: Optional[str] = None) -> List[Dict]:
        """
        Flexible search for photo-based lookup.
        Each parameter is optional — matched with LIKE if provided.
        Results sorted by number of matched fields (best match first).
        """
        conditions = []
        params     = []

        if part_number:
            conditions.append("UPPER(part_number) LIKE UPPER(?)")
            params.append(f"%{part_number}%")
        if brand:
            conditions.append("UPPER(car_brand) LIKE UPPER(?)")
            params.append(f"%{brand}%")
        if model:
            conditions.append("UPPER(car_model) LIKE UPPER(?)")
            params.append(f"%{model}%")
        if lamp_type:
            conditions.append("UPPER(COALESCE(lamp_type,'')) LIKE UPPER(?)")
            params.append(f"%{lamp_type}%")
        if side:
            conditions.append("UPPER(COALESCE(side,'')) LIKE UPPER(?)")
            params.append(f"%{side}%")

        if not conditions:
            return []

        # Score = number of matched conditions (best match first)
        score_expr = " + ".join(
            f"CASE WHEN {c} THEN 1 ELSE 0 END" for c in conditions
        )
        where_clause = " OR ".join(conditions)

        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT *, ({score_expr}) AS match_score
                    FROM parts
                    WHERE {where_clause}
                    ORDER BY match_score DESC, car_brand, car_model, part_number""",
                params + params  # params used twice: for score_expr + for WHERE
            ).fetchall()
            return [dict(r) for r in rows]

    def get_parts_with_photos(self) -> List[Dict]:
        """Return all parts that have at least one photo."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, part_number, car_brand, car_model,
                          photo_id, photo_ids, embedding
                   FROM parts
                   WHERE photo_id IS NOT NULL
                   ORDER BY id"""
            ).fetchall()
            return [dict(r) for r in rows]

    def update_embedding(self, part_id: int, embedding_json: str):
        """Save embedding vector for a part."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE parts SET embedding=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (embedding_json, part_id)
            )
            conn.commit()

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
            "description", "price", "quantity", "condition", "market", "side", "lamp_type", "photo_id", "photo_ids", "embedding"
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
