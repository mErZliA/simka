"""Fast, offline regression tests for economic rules, task resets and backups."""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from bot import db, game
from bot.backup import create_backup
from bot.utils import security
from bot.web import server


class UpgradeRuleTests(unittest.TestCase):
    def test_more_chance_never_gives_a_larger_rarity_jump(self):
        chances = list(range(game.WHEEL_MIN, game.WHEEL_MAX + 1))
        steps = [game.upgrade_steps(chance) for chance in chances]
        self.assertTrue(all(a >= b for a, b in zip(steps, steps[1:])))
        self.assertEqual([game.upgrade_steps(x) for x in (5, 10, 11, 25, 26, 45, 46, 70, 90)],
                         [4, 4, 3, 3, 2, 2, 1, 1, 1])


class InitDataTests(unittest.TestCase):
    @staticmethod
    def signed(auth_date: int, token: str) -> str:
        fields = {"auth_date": str(auth_date), "user": json.dumps({"id": 123456}, separators=(",", ":"))}
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)

    def test_valid_fresh_init_data(self):
        token = "unit-test-token"
        raw = self.signed(int(time.time()), token)
        with patch.object(security, "BOT_TOKEN", token), patch.object(security, "INITDATA_MAX_AGE", 86400), \
             patch.object(security, "INITDATA_FUTURE_SKEW", 60):
            self.assertEqual(security.verify_init_data(raw)["id"], 123456)

    def test_future_init_data_is_rejected(self):
        token = "unit-test-token"
        raw = self.signed(int(time.time()) + 3600, token)
        with patch.object(security, "BOT_TOKEN", token), patch.object(security, "INITDATA_MAX_AGE", 86400), \
             patch.object(security, "INITDATA_FUTURE_SKEW", 60):
            self.assertIsNone(security.verify_init_data(raw))

    def test_old_init_data_is_rejected(self):
        token = "unit-test-token"
        raw = self.signed(int(time.time()) - 100_000, token)
        with patch.object(security, "BOT_TOKEN", token), patch.object(security, "INITDATA_MAX_AGE", 3600), \
             patch.object(security, "INITDATA_FUTURE_SKEW", 60):
            self.assertIsNone(security.verify_init_data(raw))


class RateLimitTests(unittest.TestCase):
    def test_weighted_token_bucket_blocks_bursts(self):
        server._RATE_BUCKETS.clear()
        key = "unit-test-bucket"
        self.assertEqual(server._rate_limited(key, 2, 3), 0)
        retry = server._rate_limited(key, 2, 3)
        self.assertGreaterEqual(retry, 1)
        server._RATE_BUCKETS.pop(key, None)


class PeriodTaskTests(unittest.TestCase):
    def test_cadence_windows(self):
        now = int(time.time() * 1000)
        daily = next(t for t in server.TASKS if t["id"] == "s5")
        every_48 = next(t for t in server.TASKS if t["id"] == "sold")
        weekly = next(t for t in server.TASKS if t["id"] == "buy")
        for task, duration in ((daily, 86_400_000), (every_48, 172_800_000), (weekly, 604_800_000)):
            start, end = server._task_period(task, now)
            self.assertLessEqual(start, now)
            self.assertGreater(end, now)
            self.assertEqual(end-start, duration)
        start, _ = server._task_period(weekly, now)
        self.assertEqual(time.gmtime(start/1000).tm_wday, 0)  # weekly reset: Monday UTC

    def test_progress_and_claim_are_period_scoped_and_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "tasks.sqlite3", None
            try:
                uid = 12345
                db.task_progress_add(uid, "s5", 1000, 4)
                self.assertFalse(db.task_progress_claim(uid, "s5", 1000, 5))
                db.task_progress_add(uid, "s5", 1000, 1)
                self.assertTrue(db.task_progress_claim(uid, "s5", 1000, 5))
                self.assertFalse(db.task_progress_claim(uid, "s5", 1000, 5))
                self.assertEqual(db.task_progress_get(uid, "s5", 86_401_000),
                                 {"progress": 0, "claimed": False})
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_path


class BackupTests(unittest.TestCase):
    def test_sqlite_backup_is_integrity_checked_and_restorable(self):
        with tempfile.TemporaryDirectory() as temp:
            old_db_path, old_conn, old_backup_dir = db.DB_PATH, db._conn, __import__("bot.backup", fromlist=["BACKUP_DIR"]).BACKUP_DIR
            db.DB_PATH, db._conn = Path(temp) / "source.sqlite3", None
            backup_module = __import__("bot.backup", fromlist=["BACKUP_DIR"])
            backup_module.BACKUP_DIR = str(Path(temp) / "backups")
            try:
                db.connect()
                db.create_player(90817, username="backup-test")
                target = create_backup()
                with sqlite3.connect(target) as snapshot:
                    self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertIsNotNone(snapshot.execute("SELECT 1 FROM players WHERE user_id=90817").fetchone())
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_db_path
                backup_module.BACKUP_DIR = old_backup_dir


if __name__ == "__main__":
    unittest.main()
