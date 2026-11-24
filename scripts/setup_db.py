"""Create the database named in DATABASE_URL (if missing), apply schema, seed sample data.

    uv run python scripts/setup_db.py [--reset]
"""

import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from provenance_demo import config  # noqa: E402

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def ensure_database(url: str) -> None:
    params = conninfo_to_dict(url)
    dbname = params.get("dbname")
    admin = make_conninfo(url, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            print(f"created database {dbname}")


def apply(url: str, reset: bool = False) -> None:
    ensure_database(url)
    with psycopg.connect(url) as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS claim_risk_scores, claim_summaries, claims, orders, patients CASCADE")
        conn.execute((SQL_DIR / "schema.sql").read_text())
        conn.execute((SQL_DIR / "seed.sql").read_text())
    print(f"schema + seed applied to {conninfo_to_dict(url).get('dbname')}")


if __name__ == "__main__":
    apply(config.database_url(), reset="--reset" in sys.argv)
