# Tiki Postgres Loader

Loads Tiki product data (JSON files from [tiki-product-scraper](https://github.com/dottienguyen23/tiki-product-scraper)) into a PostgreSQL database using Python.

| Lab    | Goal                                                            | Git tag  |
| ------ | --------------------------------------------------------------- | -------- |
| Lab 01 | Read all product JSON files and load them into Postgres         | `lab-01` |
| Lab 02 | Add exception handling for file reading and database operations | `lab-02` |

Changes from Lab 01 to Lab 02: [compare `lab-01...lab-02`](https://github.com/dottienguyen23/tiki-postgres-loader/compare/lab-01...lab-02)

## Tech stack

- Python 3.12, managed with [uv](https://docs.astral.sh/uv/)
- PostgreSQL 17 (Docker)
- psycopg 3, python-dotenv

## Project structure

```
├── src/load_to_postgres.py   # loader script
├── sql/create_tables.sql     # table schema
├── docs/                     # screenshots
├── .env.example              # config template
└── pyproject.toml / uv.lock  # dependencies
```

## How to run

1. Start Postgres:
   ```bash
   docker run --name tiki-postgres -e POSTGRES_USER=tiki -e POSTGRES_PASSWORD=tiki123 -e POSTGRES_DB=tiki -p 5433:5432 -v tiki_pgdata:/var/lib/postgresql/data -d postgres:17
   ```
2. Create the table:
   ```bash
   docker exec -i tiki-postgres psql -U tiki -d tiki < sql/create_tables.sql
   ```
3. Configure: `cp .env.example .env` and set your values (`DATA_DIR` = folder with `products_*.json`).
4. Install dependencies and run:
   ```bash
   uv sync
   uv run src/load_to_postgres.py
   echo $?   # exit code: 0 = success, 1 = failed
   ```

## Lab 01 – Load JSON into Postgres

### Design decisions

- `id` is the PRIMARY KEY and inserts use `ON CONFLICT (id) DO NOTHING`, so the loader is **idempotent**: re-running it never creates duplicates.
- Inserts are batched per file (`executemany` + one commit per file) instead of row by row.
- `images` is stored as `JSONB` to keep the list structure.
- Credentials live in `.env` (git-ignored), never in code.

### Results

- Files loaded: 51 (scraping in Project 02 is partial; re-running the loader after completing it adds only new products)
- Rows in `products`: 38,036
- Second run: 0 new rows inserted (idempotency confirmed)

![Load summary](docs/load_summary.png)

### Issues encountered

| Problem                             | Cause                                          | Fix                                               |
| ----------------------------------- | ---------------------------------------------- | ------------------------------------------------- |
| `docker` not found in WSL           | Docker Desktop WSL integration off             | Enabled in Settings → Resources → WSL integration |
| `permission denied ... docker.sock` | user not in `docker` group                     | `sudo usermod -aG docker $USER`                   |
| `password authentication failed`    | a local Postgres in WSL already used port 5432 | mapped Docker to port 5433                        |
| `.env` had CRLF line endings        | file created on Windows                        | converted to LF                                   |

## Lab 02 – Error handling

Lab 02 extends Lab 01 with exception handling for **file reading** and **database operations**.

### How errors are handled

The main rule: a problem with **one file** is skipped and reported; a problem that affects **the whole job** (config, database) stops it with a clear message and exit code 1.

| Error type                     | Behaviour                               | Example                                        |
| ------------------------------ | --------------------------------------- | ---------------------------------------------- |
| Config error                   | Stop, exit code 1                       | `DATA_DIR` not found, `PG_PASSWORD` missing    |
| Bad file                       | Skip file, continue, list it in summary | broken JSON, empty file, not UTF-8, not a list |
| Bad item                       | Skip only that product                  | product without `id`                           |
| Data rejected by DB            | `rollback()`, skip file, continue       | value too long / wrong type                    |
| DB unreachable / table missing | Stop, exit code 1                       | container stopped, wrong password              |
| Ctrl+C                         | Clean message, exit code 130            | committed files are kept, rerun is safe        |

### Exceptions caught

| Where         | Exceptions                                                                                                                                   |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Config        | custom `ConfigError`                                                                                                                         |
| Reading files | `FileNotFoundError`, `IsADirectoryError`, `PermissionError`, `UnicodeDecodeError`, `json.JSONDecodeError` → wrapped in custom `BadFileError` |
| Connecting    | `psycopg.OperationalError`                                                                                                                   |
| Inserting     | `psycopg.DataError`, `psycopg.IntegrityError` (with `conn.rollback()`)                                                                       |
| Whole job     | `psycopg.errors.UndefinedTable`, `psycopg.OperationalError`, `psycopg.Error`, `KeyboardInterrupt`                                            |

Other changes:

- `logging` instead of `print` for progress and errors (levels INFO / WARNING / ERROR / CRITICAL, with timestamps)
- `connect_timeout=5`, so the script fails fast instead of hanging
- Exit codes (`sys.exit(main())`), so a scheduler (cron, Airflow) can tell success from failure
- The summary now lists every skipped file with the reason

### Tests

Each error was triggered on purpose. Variables are overridden for one run only (`VAR=value command`), so `.env` is not changed:

```bash
DATA_DIR=/does/not/exist uv run python src/load_to_postgres.py   # 1. wrong DATA_DIR
PG_PASSWORD= uv run python src/load_to_postgres.py               # 2. missing password
PG_PASSWORD=wrong uv run python src/load_to_postgres.py          # 3. wrong password
echo '{"broken": ' > "$DATA_DIR/products_9998.json"              # 4. broken JSON
echo '{"id": 1}'   > "$DATA_DIR/products_9999.json"              # 5. JSON but not a list
docker stop tiki-postgres                                        # 6. database down
```

| #   | Test                  | Result                                | Exit code |
| --- | --------------------- | ------------------------------------- | --------- |
| 1   | Wrong `DATA_DIR`      | `CONFIG ERROR`, stops                 | 1         |
| 2   | Missing `PG_PASSWORD` | `CONFIG ERROR`, stops                 | 1         |
| 3   | Wrong password        | `Cannot connect to PostgreSQL`, stops | 1         |
| 4   | Broken JSON           | file skipped, others loaded           | 0         |
| 5   | JSON is a dict        | file skipped, others loaded           | 0         |
| 6   | DB container stopped  | `connection refused`, stops           | 1         |

Example – test 4 + 5 summary:

```
===== SUMMARY =====
Files found              : 53
Files loaded             : 51
Products read from files : 38036
Bad items skipped        : 0
New rows inserted        : 0
Total rows in table      : 38036
Files skipped            : 2
  - products_9998.json: broken JSON at line 2, column 1: Expecting value
  - products_9999.json: expected a JSON list, got dict
exit code: 0
```
