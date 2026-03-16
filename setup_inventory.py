#!/usr/bin/env python3
"""Create inventory.db with schema and seed data (README). Run once before processing invoices."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "inventory.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS inventory (item TEXT PRIMARY KEY, stock INTEGER)")
    cursor.executemany(
        "INSERT OR REPLACE INTO inventory (item, stock) VALUES (?, ?)",
        [
            ("WidgetA", 15),
            ("WidgetB", 10),
            ("GadgetX", 5),
            ("FakeItem", 0),
            ('SuperGizmo', 8),
            ('MegaSprocket', 10),
            ('WidgetC', 15),
        ],
    )
    conn.commit()
    conn.close()
    print(f"Created {DB_PATH}")


if __name__ == "__main__":
    main()
