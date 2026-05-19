from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

import pandas as pd

DB_PATH = Path("data/travel_fares.sqlite")

FLIGHT_COLUMNS = {
    "source": "TEXT",
    "airline": "TEXT",
    "airline_code": "TEXT",
    "price_gbp": "REAL",
    "currency": "TEXT",
    "depart_at": "TEXT",
    "arrive_at": "TEXT",
    "duration": "TEXT",
    "stops": "INTEGER",
    "route": "TEXT",
    "cabin": "TEXT",
    "included_checked_bags": "INTEGER",
    "included_checked_weight_kg": "REAL",
    "estimated_baggage_extra_gbp": "REAL",
    "estimated_total_gbp": "REAL",
    "requested_checked_bags_total": "INTEGER",
    "requested_checked_bags_per_person": "INTEGER",
    "requested_checked_weight_kg": "REAL",
    "airline_max_checked_weight_kg": "REAL",
    "standard_checked_weight_kg": "REAL",
    "extra_bags_total": "INTEGER",
    "overweight_bags_total": "INTEGER",
    "baggage_feasible": "INTEGER",
    "baggage_policy": "TEXT",
    "baggage_note": "TEXT",
    "deep_link": "TEXT",
    "raw_json": "TEXT",
    "fetched_at": "TEXT",
}

RAIL_COLUMNS = {
    "source": "TEXT",
    "route_name": "TEXT",
    "price_gbp": "REAL",
    "duration": "TEXT",
    "changes": "INTEGER",
    "luggage_score": "INTEGER",
    "notes": "TEXT",
    "deep_link": "TEXT",
    "fetched_at": "TEXT",
}


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table});").fetchall()}
    for col, typ in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ};")


def init_db(conn: sqlite3.Connection) -> None:
    flight_cols_sql = ",\n            ".join(f"{col} {typ}" for col, typ in FLIGHT_COLUMNS.items())
    rail_cols_sql = ",\n            ".join(f"{col} {typ}" for col, typ in RAIL_COLUMNS.items())
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS flight_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {flight_cols_sql}
        );

        CREATE TABLE IF NOT EXISTS rail_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {rail_cols_sql}
        );
        """
    )
    _ensure_columns(conn, "flight_offers", FLIGHT_COLUMNS)
    _ensure_columns(conn, "rail_offers", RAIL_COLUMNS)
    conn.commit()


def replace_flights(conn: sqlite3.Connection, rows: Iterable[dict]) -> None:
    rows = list(rows)
    conn.execute("DELETE FROM flight_offers;")
    if rows:
        df = pd.DataFrame(rows)
        for col in FLIGHT_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[list(FLIGHT_COLUMNS)]
        df.to_sql("flight_offers", conn, if_exists="append", index=False)
    conn.commit()


def replace_rail(conn: sqlite3.Connection, rows: Iterable[dict]) -> None:
    rows = list(rows)
    conn.execute("DELETE FROM rail_offers;")
    if rows:
        df = pd.DataFrame(rows)
        for col in RAIL_COLUMNS:
            if col not in df.columns:
                df[col] = None
        df = df[list(RAIL_COLUMNS)]
        df.to_sql("rail_offers", conn, if_exists="append", index=False)
    conn.commit()


def load_table(conn: sqlite3.Connection, table: str) -> pd.DataFrame:
    try:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)
    except Exception:
        return pd.DataFrame()


def raw_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)
