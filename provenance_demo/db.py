"""Small psycopg helpers."""

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import config

# Tables a provenance "sources" ref may point at (table:id). Whitelisted so the
# lineage tool never interpolates arbitrary identifiers into SQL.
SOURCE_TABLES = {"patients", "orders", "claims", "claim_summaries", "claim_risk_scores"}


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(config.database_url(), row_factory=dict_row) as conn:
        yield conn


def fetch_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with connect() as conn:
        return conn.execute(sql, params).fetchone()


def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def get_source_row(ref: str) -> dict[str, Any] | None:
    table, _, row_id = ref.partition(":")
    if table not in SOURCE_TABLES or not row_id.isdigit():
        raise ValueError(f"bad source ref: {ref!r}")
    return fetch_one(f"SELECT * FROM {table} WHERE id = %s", (int(row_id),))


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)
