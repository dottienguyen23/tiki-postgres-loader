"""Lab 02 - Load Tiki product JSON files into PostgreSQL, with error handling.

Lab 01: read products_*.json -> insert into `products` (idempotent).
Lab 02: handle errors when reading files and when working with the database.
"""

import json
import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg import errors
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("loader")

REQUIRED_VARS = ["DATA_DIR", "PG_HOST", "PG_PORT", "PG_DB", "PG_USER", "PG_PASSWORD"]

INSERT_SQL = """
    INSERT INTO products (id, name, url_key, price, description, images)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (id) DO NOTHING
"""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------
class ConfigError(Exception):
    """.env settings are missing or invalid -> the job cannot start."""


class BadFileError(Exception):
    """One input file cannot be used -> skip it, continue with the next one."""


# ---------------------------------------------------------------------------
# Part 1 - Config
# ---------------------------------------------------------------------------
def load_config() -> tuple[Path, str]:
    load_dotenv()

    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise ConfigError(f"Missing variables in .env: {', '.join(missing)}")

    data_dir = Path(os.environ["DATA_DIR"])
    if not data_dir.is_dir():
        raise ConfigError(f"DATA_DIR is not an existing folder: {data_dir}")

    dsn = (
        f"host={os.environ['PG_HOST']} port={os.environ['PG_PORT']} "
        f"dbname={os.environ['PG_DB']} user={os.environ['PG_USER']} "
        f"password={os.environ['PG_PASSWORD']}"
    )
    return data_dir, dsn


# ---------------------------------------------------------------------------
# Part 2 - Reading files
# ---------------------------------------------------------------------------
def read_products(path: Path) -> list:
    """Read one JSON file and return a list. Raise BadFileError if unusable."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BadFileError("file not found (deleted while running?)")
    except IsADirectoryError:
        raise BadFileError("is a folder, not a file")
    except PermissionError:
        raise BadFileError("no permission to read the file")
    except UnicodeDecodeError as e:
        raise BadFileError(f"not valid UTF-8 (byte position {e.start})")

    if not text.strip():
        raise BadFileError("file is empty")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BadFileError(f"broken JSON at line {e.lineno}, column {e.colno}: {e.msg}")

    if not isinstance(data, list):
        raise BadFileError(f"expected a JSON list, got {type(data).__name__}")

    return data


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


def to_rows(products: list, filename: str) -> tuple[list[tuple], int]:
    """Convert products to rows. Bad items are skipped (not the whole file)."""
    rows, bad = [], 0
    for idx, p in enumerate(products):
        if not isinstance(p, dict) or p.get("id") is None:
            log.warning("%s: item #%d skipped (not an object or no 'id')", filename, idx)
            bad += 1
            continue
        rows.append(to_row(p))
    return rows, bad


# ---------------------------------------------------------------------------
# Part 3/4 - Database
# ---------------------------------------------------------------------------
def count_rows(cur) -> int:
    cur.execute("SELECT COUNT(*) FROM products")
    return cur.fetchone()[0]


def main() -> int:
    # --- config -----------------------------------------------------------
    try:
        data_dir, dsn = load_config()
    except ConfigError as e:
        log.critical("CONFIG ERROR: %s", e)
        return 1

    files = sorted(data_dir.glob("products_*.json"))
    if not files:
        log.critical("No products_*.json files found in %s", data_dir)
        return 1
    log.info("Found %d files in %s", len(files), data_dir)

    skipped: list[tuple[str, str]] = []  # (file name, reason)
    read_total, bad_items = 0, 0

    # --- connect ----------------------------------------------------------
    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
    except psycopg.OperationalError as e:
        log.critical("Cannot connect to PostgreSQL: %s", str(e).strip())
        log.critical("Check: is the container running (docker ps)? Port/password in .env?")
        return 1

    # --- load -------------------------------------------------------------
    try:
        with conn, conn.cursor() as cur:  # closes the connection at the end
            before = count_rows(cur)

            for i, path in enumerate(files, start=1):
                prefix = f"[{i}/{len(files)}] {path.name}"

                # file errors -> skip this file, continue
                try:
                    products = read_products(path)
                except BadFileError as e:
                    log.warning("%s SKIP: %s", prefix, e)
                    skipped.append((path.name, str(e)))
                    continue

                rows, bad = to_rows(products, path.name)
                bad_items += bad
                if not rows:
                    log.warning("%s SKIP: no valid products", prefix)
                    skipped.append((path.name, "no valid products"))
                    continue

                # data errors -> rollback this file, continue
                try:
                    cur.executemany(INSERT_SQL, rows)
                    conn.commit()
                except (psycopg.DataError, psycopg.IntegrityError) as e:
                    conn.rollback()  # otherwise every later query fails
                    reason = f"DB rejected data ({type(e).__name__}): {str(e).splitlines()[0]}"
                    log.error("%s SKIP: %s", prefix, reason)
                    skipped.append((path.name, reason))
                    continue

                read_total += len(rows)
                log.info("%s: %d products", prefix, len(rows))

            after = count_rows(cur)

    # errors below stop the whole job
    except errors.UndefinedTable:
        log.critical("Table 'products' does not exist. Run sql/create_tables.sql first.")
        return 1
    except psycopg.OperationalError as e:
        log.critical("Lost connection to PostgreSQL: %s", str(e).strip())
        log.critical("Files committed before this point are kept. Rerun is safe.")
        return 1
    except psycopg.Error as e:
        log.critical("Unexpected database error (%s): %s", type(e).__name__, e)
        return 1

    # --- summary ------------------------------------------------------------
    print("\n===== SUMMARY =====")
    print(f"Files found              : {len(files)}")
    print(f"Files loaded             : {len(files) - len(skipped)}")
    print(f"Products read from files : {read_total}")
    print(f"Bad items skipped        : {bad_items}")
    print(f"New rows inserted        : {after - before}")
    print(f"Total rows in table      : {after}")
    print(f"Files skipped            : {len(skipped) or 'none'}")
    for name, reason in skipped:
        print(f"  - {name}: {reason}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.warning("Interrupted (Ctrl+C). Committed files are kept; rerun is safe.")
        sys.exit(130)