"""Copy an existing SQLite snapshot to an empty PostgreSQL application schema.

Source stays unchanged. All rows and counters are copied in one transaction.
Refuses to merge into nonempty destination tables; never overwrites cloud data.
"""
import argparse
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sorter.config import load_config
from sorter.storage import Storage
from sorter.anomaly.store import AnomalyStore

TABLES = ("packages", "used_codes", "rpc_audit", "security_events", "system_events", "counters", "anomaly_windows")


def migrate(source_path, target):
    source_path = Path(source_path).resolve(strict=True)
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")
    try:
        with AnomalyStore(target):
            pass
        with Storage(target) as dest:
            counts = {}
            with dest.transaction() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(731204503)")
                for table in TABLES:
                    if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                        raise RuntimeError(f"Destination {table} is not empty; import cancelled without changing data")
                for table in TABLES:
                    if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        counts[table] = 0
                        continue
                    rows = source.execute(f"SELECT * FROM {table}").fetchall()
                    allowed = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='iot_project' AND table_name=?", (table,)).fetchall()}
                    for row in rows:
                        data = {k: row[k] for k in row.keys() if k in allowed}
                        if table == "packages" and not data.get("record_uid"):
                            data["record_uid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, source_path.as_uri() + ":" + str(row["id"])))
                        columns = list(data)
                        conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(data.values()))
                    counts[table] = len(rows)
                    if rows and "id" in allowed:
                        conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), (SELECT MAX(id) FROM {table}), true)")
                dest.insert_system_event("SQLITE_IMPORTED", "Historical local data imported; " + str(counts))
            return counts
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.example.yaml")
    parser.add_argument("--sqlite", required=True)
    args = parser.parse_args()
    cfg = load_config(args.config, validate_files=False)
    if cfg.storage.backend != "postgresql":
        parser.error("Destination must be PostgreSQL")
    print(migrate(args.sqlite, cfg.storage.target))


if __name__ == "__main__":
    main()
