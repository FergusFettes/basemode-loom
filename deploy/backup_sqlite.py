#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: backup_sqlite.py DATABASE BACKUP_DIR RETENTION_DAYS")
    source = Path(sys.argv[1]).resolve(strict=True)
    destination_dir = Path(sys.argv[2]).resolve()
    retention_days = int(sys.argv[3])
    if retention_days < 1:
        raise SystemExit("retention must be at least one day")

    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"grove-{stamp}.sqlite"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    destination.chmod(0o600)

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    for backup in destination_dir.glob("grove-*.sqlite"):
        modified = datetime.fromtimestamp(backup.stat().st_mtime, UTC)
        if backup != destination and modified < cutoff:
            backup.unlink()


if __name__ == "__main__":
    main()
