"""
Імпорт запчастин з CSV файлу Google Sheets у базу даних бота.

Використання:
    python import_csv.py <шлях_до_csv>

Приклад:
    python import_csv.py "Передні_Фари_-_Задні_фонарі.csv"
"""

import csv
import sqlite3
import sys
import os
from collections import defaultdict


def parse_csv(filepath: str) -> list[dict]:
    """Parse CSV and merge duplicate part numbers."""
    merged = defaultdict(lambda: {
        'part_number': '', 'car_brand': '', 'car_model': '',
        'description': '', 'price': None, 'quantity': 0,
        'condition': set(), 'market': None,
        'side': None, 'lamp_type': None, 'photo_id': None,
    })

    with open(filepath, encoding='utf-8') as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    # Find header row (contains "Номер")
    data_start = 2  # default: row 0 = title, row 1 = headers
    for i, row in enumerate(all_rows):
        if 'Номер' in row:
            data_start = i + 1
            break

    skipped = 0
    processed = 0

    for r in all_rows[data_start:]:
        # Skip empty rows
        if len(r) < 9 or not r[1].strip():
            skipped += 1
            continue

        num   = r[1].strip().upper()
        brand = r[2].strip() if len(r) > 2 else ''
        model = r[3].strip() if len(r) > 3 else ''
        desc  = r[4].strip() if len(r) > 4 else ''
        price = r[6].strip() if len(r) > 6 else ''
        qty   = r[8].strip() if len(r) > 8 else '0'

        def b(idx): return len(r) > idx and r[idx].strip().upper() == 'TRUE'

        is_new    = b(10)
        is_used   = b(11)
        is_repair = b(13)
        is_broken = b(14)
        is_eu     = b(15)
        is_usa    = b(16)

        key = (num, brand.lower(), model.lower())
        m = merged[key]
        m['part_number'] = num
        m['car_brand']   = brand
        m['car_model']   = model
        m['description'] = m['description'] or desc

        # Price — keep highest
        try:
            p = float(price)
            if m['price'] is None or p > m['price']:
                m['price'] = p
        except (ValueError, TypeError):
            pass

        # Quantity — sum
        try:
            m['quantity'] += int(qty)
        except (ValueError, TypeError):
            pass

        # Conditions — union
        if is_new:    m['condition'].add('Нова')
        if is_used:   m['condition'].add('Б/В')
        if is_repair: m['condition'].add('До роботи')
        if is_broken: m['condition'].add('Битий')

        # Market
        if is_eu and is_usa:
            m['market'] = 'EU, USA'
        elif is_eu:
            m['market'] = m.get('market') or 'EU'
        elif is_usa:
            m['market'] = m.get('market') or 'USA'

        processed += 1

    parts = list(merged.values())
    # Convert condition sets to strings
    for p in parts:
        p['condition'] = ', '.join(sorted(p['condition'])) or None

    print(f"  Оброблено рядків: {processed}")
    print(f"  Пропущено порожніх: {skipped}")
    print(f"  Унікальних запчастин після об'єднання: {len(parts)}")
    return parts


def import_to_db(parts: list[dict], db_path: str = 'warehouse.db'):
    """Insert parts into SQLite database."""
    if not os.path.exists(db_path):
        print(f"❌ База даних не знайдена: {db_path}")
        print("   Спочатку запустіть бота хоча б один раз щоб створити БД.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Ensure new columns exist
    cols = [r[1] for r in conn.execute("PRAGMA table_info(parts)").fetchall()]
    for col, typ in [('market','TEXT'), ('side','TEXT'), ('lamp_type','TEXT')]:
        if col not in cols:
            conn.execute(f"ALTER TABLE parts ADD COLUMN {col} {typ}")

    inserted = 0
    skipped  = 0

    for p in parts:
        if not p['part_number'] or not p['car_brand']:
            skipped += 1
            continue

        # Check duplicate
        exists = conn.execute(
            "SELECT id FROM parts WHERE UPPER(part_number)=? AND UPPER(car_brand)=?",
            (p['part_number'], p['car_brand'].upper())
        ).fetchone()

        if exists:
            # Update quantity and condition if already exists
            conn.execute(
                """UPDATE parts SET
                   quantity  = quantity + ?,
                   condition = CASE WHEN condition IS NULL THEN ? ELSE condition END,
                   updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (p['quantity'], p['condition'], exists['id'])
            )
            skipped += 1
        else:
            conn.execute(
                """INSERT INTO parts
                   (part_number, car_brand, car_model, description,
                    price, quantity, condition, market, side, lamp_type, photo_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    p['part_number'], p['car_brand'], p['car_model'],
                    p['description'] or None,
                    p['price'], p['quantity'],
                    p['condition'], p['market'],
                    p['side'], p['lamp_type'], p['photo_id'],
                )
            )
            inserted += 1

    conn.commit()
    conn.close()

    print(f"\n✅ Імпорт завершено!")
    print(f"   Додано нових: {inserted}")
    print(f"   Пропущено/оновлено дублікатів: {skipped}")


def main():
    if len(sys.argv) < 2:
        print("Використання: python import_csv.py <шлях_до_csv>")
        print('Приклад:      python import_csv.py "Передні_Фари_-_Задні_фонарі.csv"')
        sys.exit(1)

    csv_path = sys.argv[1]
    if not os.path.exists(csv_path):
        print(f"❌ Файл не знайдено: {csv_path}")
        sys.exit(1)

    print(f"📂 Читаю файл: {csv_path}")
    parts = parse_csv(csv_path)

    print(f"\n💾 Записую в базу даних...")
    import_to_db(parts)


if __name__ == '__main__':
    main()
