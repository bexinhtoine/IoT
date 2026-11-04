"""Connections for the application's fixed SQL statements (SQLite or PostgreSQL)."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path


class Record(dict):
    """Support both named columns and scalar row[0], like sqlite3.Row."""
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def record_factory(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Record(zip(names, values))


class PostgresConnection:
    """Small adapter for the SQL subset used by Storage and AnomalyStore.

    Values always remain bound parameters; never pass arbitrary SQL through here.
    Tables live in a private schema, outside Supabase's public REST API.
    """
    postgres = True

    def __init__(self, url):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict

        options = conninfo_to_dict(url)
        if options.get("sslmode", "require") not in ("require", "verify-ca", "verify-full"):
            raise ValueError("PostgreSQL requires TLS (sslmode=require or verify-full)")
        options.setdefault("sslmode", "require")
        options.setdefault("connect_timeout", "10")
        try:
            self.raw = psycopg.connect(**options, autocommit=True, row_factory=record_factory)
        except psycopg.Error as exc:
            raise ConnectionError("Cannot connect to PostgreSQL; check network, host and database credentials") from None
        try:
            with self.raw.transaction():
                self.raw.execute("SELECT pg_advisory_xact_lock(731204501)")
                self.raw.execute("CREATE SCHEMA IF NOT EXISTS iot_project")
                self.raw.execute("REVOKE ALL ON SCHEMA iot_project FROM PUBLIC")
            self.raw.execute("SET search_path TO iot_project")
            self.raw.execute("SET statement_timeout TO '15s'")
        except Exception:
            self.raw.close()
            raise

    def execute(self, sql, params=()):
        if sql == "BEGIN IMMEDIATE":
            sql = "BEGIN"
        if sql.startswith("CREATE TABLE"):
            sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            sql = sql.replace("id INTEGER PRIMARY KEY,", "id BIGSERIAL PRIMARY KEY,")
            sql = re.sub(r"\bINTEGER\b", "BIGINT", sql)
            sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
        sql = sql.replace("?", "%s")
        returns_id = bool(re.match(r"INSERT INTO (packages|rpc_audit|security_events|system_events|anomaly_windows)\b", sql))
        if returns_id:
            sql += " RETURNING id"
        cursor = self.raw.execute(sql, params or None)
        if sql.startswith("CREATE TABLE"):
            table = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", sql).group(1)
            # Defense in depth if schema exposure is changed in the dashboard.
            self.raw.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        return InsertResult(cursor) if returns_id else cursor

    def close(self):
        self.raw.close()


class InsertResult:
    def __init__(self, cursor):
        self.rowcount = cursor.rowcount
        row = cursor.fetchone()
        self.lastrowid = row[0] if row else None


def open_database(target):
    target = str(target)
    if target.startswith(("postgresql://", "postgres://")):
        return PostgresConnection(target)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
