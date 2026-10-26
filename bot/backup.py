"""Rotating SQLite backups using the online backup API (safe with WAL)."""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot import db
from bot.config import BACKUP_DIR, BACKUP_INTERVAL_HOURS, BACKUP_RETENTION_DAYS

log = logging.getLogger("simroulette.backup")
PREFIX = "simroulette-"


def create_backup(now: datetime | None = None) -> Path:
    """Create a consistent SQLite snapshot and prune expired snapshots."""
    now = now or datetime.now(timezone.utc)
    directory = Path(BACKUP_DIR)
    if not directory.is_absolute():
        directory = Path(__file__).resolve().parent.parent / directory
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    target = directory / f"{PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}.sqlite3"

    source = db.connect()
    destination = sqlite3.connect(target)
    try:
        with db._lock:
            source.backup(destination, pages=256, sleep=0.02)
        destination.commit()
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("backup integrity check failed")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except Exception:
        destination.close()
        target.unlink(missing_ok=True)
        raise
    else:
        destination.close()

    cutoff = now - timedelta(days=BACKUP_RETENTION_DAYS)
    for old in directory.glob(f"{PREFIX}*.sqlite3"):
        try:
            stamp = datetime.strptime(old.stem.removeprefix(PREFIX), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if stamp < cutoff:
                old.unlink(missing_ok=True)
        except (ValueError, OSError):
            continue
    return target


async def backup_loop() -> None:
    """Back up at startup and then periodically; failures do not stop the app."""
    while True:
        try:
            path = await asyncio.to_thread(create_backup)
            await asyncio.to_thread(db.task_progress_prune, int((datetime.now(timezone.utc)-timedelta(days=14)).timestamp()*1000))
            log.info("SQLite backup completed: %s", path.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("SQLite backup failed")
        await asyncio.sleep(BACKUP_INTERVAL_HOURS * 3600)
