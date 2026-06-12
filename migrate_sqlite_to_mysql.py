# migrate_sqlite_to_mysql.py
# ---------------------------------------------------------------------------
# ONE-TIME migration: copies every row from the local SQLite database
# (pokemon_cards.db, next to this script) into the MySQL database named
# by the MYSQL_* variables in .env.
#
# Safe to re-run: rows are REPLACEd, never duplicated. The SQLite file
# is only ever READ — nothing is deleted.
#
# HOW TO RUN (PythonAnywhere console, venv active, after .env has the
# MYSQL_* lines):
#     python migrate_sqlite_to_mysql.py
# ---------------------------------------------------------------------------

import os
import sqlite3

import pymysql
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SQLITE_PATH = os.path.join(BASE_DIR, "pokemon_cards.db")

# Import the MySQL schema from the app itself, so the two can never
# drift apart. (Importing pokemon_api would start the whole app; we
# only want the schema constant, so we read it via a tiny exec of the
# relevant assignment — simplest robust approach: redefine it here and
# keep it in sync is fragile, so instead we pull it from the module
# WITHOUT executing the module: we parse the file for MYSQL_SCHEMA.)
import ast

with open(os.path.join(BASE_DIR, "pokemon_api.py")) as f:
    tree = ast.parse(f.read())
MYSQL_SCHEMA = None
for node in tree.body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if getattr(target, "id", None) == "MYSQL_SCHEMA":
                MYSQL_SCHEMA = ast.literal_eval(node.value)
assert MYSQL_SCHEMA, "could not find MYSQL_SCHEMA in pokemon_api.py"

TABLES = {
    "cards": [
        "card_id",
        "game",
        "card_name",
        "set_name",
        "card_number",
        "rarity",
        "market_price",
        "small_image",
        "large_image",
        "last_updated",
    ],
    "watchlist": [
        "user_id",
        "card_id",
        "card_json",
        "added_at",
        "condition",
        "quantity",
        "target_price",
        "game",
    ],
    "price_snapshots": ["card_id", "snapshot_date", "market_price"],
    "sets": [
        "game",
        "set_id",
        "set_name",
        "total",
        "printed_total",
        "release_date",
        "last_synced",
    ],
}


def quote(col: str) -> str:
    return f"`{col}`"


def main() -> None:
    print(f"Reading from: {SQLITE_PATH}")
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    dst = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        autocommit=False,
    )
    print(
        f"Writing to:   MySQL '{os.environ['MYSQL_DATABASE']}' "
        f"on {os.environ['MYSQL_HOST']}"
    )

    cursor = dst.cursor()
    for statement in MYSQL_SCHEMA:
        cursor.execute(statement)
    dst.commit()
    print("Schema ready.")

    for table, columns in TABLES.items():
        rows = src.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
        if not rows:
            print(f"{table:16s} 0 rows (nothing to copy)")
            continue
        placeholders = ", ".join(["%s"] * len(columns))
        collist = ", ".join(quote(c) for c in columns)
        sql = f"REPLACE INTO {table} ({collist}) VALUES ({placeholders})"
        copied = 0
        for row in rows:
            cursor.execute(sql, tuple(row))
            copied += 1
            if copied % 500 == 0:
                dst.commit()
        dst.commit()

        count = None
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"{table:16s} copied {copied:5d} rows -> MySQL now holds {count}")

    src.close()
    dst.close()
    print()
    print("Migration complete. Reload the website and check /health says")
    print('"database": "mysql".')


if __name__ == "__main__":
    main()
