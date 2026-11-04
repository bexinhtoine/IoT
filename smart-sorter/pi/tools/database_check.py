"""Check cloud persistence and replay the durable parcel journal.

Run from pi/: python tools/database_check.py --config config.example.yaml
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sorter.config import load_config
from sorter.storage import Storage
from sorter.anomaly.store import AnomalyStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.example.yaml")
    parser.add_argument("--outbox", default=None, help="Override local pending journal path")
    args = parser.parse_args()
    cfg = load_config(args.config, validate_files=False)
    if cfg.storage.backend != "postgresql":
        parser.error("Choose a config with storage.backend=postgresql")
    # Opening Storage also replays the local parcel journal, if present.
    with Storage(cfg.storage.target, outbox_path=args.outbox or cfg.storage.db_path + ".outbox.db") as db:
        with AnomalyStore(cfg.storage.target):
            pass
        marker = str(uuid.uuid4())
        ident = db.insert_system_event("DATABASE_VERIFIED", marker)
    # A new connection proves the committed record survives disconnection.
    with Storage(cfg.storage.target) as db:
        row = db._conn.execute("SELECT detail FROM system_events WHERE id=?", (ident,)).fetchone()
        if not row or row["detail"] != marker:
            raise RuntimeError("Persistence verification failed")
        print(json.dumps({"backend": "postgresql", "schema": "iot_project", "schema_version": db.schema_version,
                          "packages": db.count_packages(), "counters": db.all_counters(),
                          "committed_write_read_verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
