import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ["DATA_DIR"])
DSN = (
    f"host={os.environ['PG_HOST']} port={os.environ['PG_PORT']} "
    f"dbname={os.environ['PG_DB']} user={os.environ['PG_USER']} "
    f"password={os.environ['PG_PASSWORD']}"
)

INSERT_SQL = """
    INSERT INTO products (id, name, url_key, price, description, images)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
"""


def to_row(p: dict) -> tuple:
    """One product dict -> tuple in the column order of INSERT_SQL."""
    return (
        p["id"],
        p.get("name"),
        p.get("url_key"),
        p.get("price"),
        p.get("description"),
        Jsonb(p.get("images")),
    )


def count_rows(cur) -> int:
    cur.execute("SELECT COUNT(*) FROM products")
    return cur.fetchone()[0]


def main():
    files = sorted(DATA_DIR.glob("products_*.json"))
    print(f"Found {len(files)} files in {DATA_DIR}")

    broken, read_total = [], 0

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        before = count_rows(cur)

        for i, path in enumerate(files, start=1):
            try:
                products = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                print(f"[{i}/{len(files)}] SKIP {path.name}: broken JSON ({e})")
                broken.append(path.name)
                continue

            cur.executemany(INSERT_SQL, [to_row(p) for p in products])
            conn.commit()
            read_total += len(products)
            print(f"[{i}/{len(files)}] {path.name}: {len(products)} products")

        after = count_rows(cur)

    print("\n===== SUMMARY =====")
    print(f"Products read from files : {read_total}")
    print(f"New rows inserted        : {after - before}")
    print(f"Total rows in table      : {after}")
    print(f"Broken files skipped     : {broken or 'none'}")


if __name__ == "__main__":
    main()