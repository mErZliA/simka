"""Fast, offline regression tests for economic rules, task resets and backups."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

from bot import db, game
from bot.backup import create_backup
from bot.utils.command_guard import CommandFloodGuard
from bot import fx
from bot.utils import security
from bot.web import server


class NumberFormattingTests(unittest.TestCase):
    def test_all_country_formats_preserve_every_generated_digit(self):
        for country in game.COUNTRIES:
            with self.subTest(country=country["k"]):
                digits = [(i + 1) % 10 for i in range(country["L"])]
                formatted = game.format_number(country, digits)
                actual = re.sub(r"\D", "", formatted)
                expected = re.sub(r"\D", "", country["code"]) + "".join(map(str, digits))
                self.assertEqual(actual, expected)
                self.assertEqual(len(actual), len(expected))

                item = game.gen_unique_number(country, "test", game.R_BY_ID["common"], [])
                self.assertEqual(
                    len(re.sub(r"\D", "", item["num"])),
                    len(re.sub(r"\D", "", country["code"])) + country["L"],
                )

    def test_russian_and_kazakh_numbers_have_eleven_digits_including_country_code(self):
        for country_id in ("RU", "KZ"):
            with self.subTest(country=country_id):
                country = game.C_BY_ID[country_id]
                number = game.format_number(country, [1, 2, 3, 4, 5, 6, 7, 8, 9, 0])
                self.assertEqual(re.sub(r"\D", "", number), "7" + "1234567890")
                self.assertEqual(len(re.sub(r"\D", "", number)), 11)


class UpgradeRuleTests(unittest.TestCase):
    def test_more_chance_never_gives_a_larger_rarity_jump(self):
        chances = list(range(game.WHEEL_MIN, game.WHEEL_MAX + 1))
        steps = [game.upgrade_steps(chance) for chance in chances]
        self.assertTrue(all(a >= b for a, b in zip(steps, steps[1:])))
        self.assertEqual([game.upgrade_steps(x) for x in (5, 10, 11, 25, 26, 45, 46, 70, 90)],
                         [4, 4, 3, 3, 2, 2, 1, 1, 0])

    def test_upgrade_price_never_decreases_and_90_percent_adds_ten_percent(self):
        self.assertEqual(game.upgrade_price(100_000, 90), 110_000)
        self.assertEqual(game.upgrade_price(230, 90), 253)
        prices = [game.upgrade_price(230, chance) for chance in range(5, 91, 5)]
        self.assertTrue(all(price >= 230 for price in prices))
        self.assertTrue(all(a >= b for a, b in zip(prices, prices[1:])))


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


class CommandFloodGuardTests(unittest.TestCase):
    def test_allows_short_burst_then_drops_until_refill(self):
        guard = CommandFloodGuard(capacity=3, refill_per_second=1)
        self.assertTrue(all(guard.allow(101, "/start", now=0) for _ in range(3)))
        self.assertFalse(guard.allow(101, "/help", now=0))
        self.assertTrue(guard.allow(101, "/app", now=1))

    def test_ignores_regular_messages_and_isolates_users(self):
        guard = CommandFloodGuard(capacity=1, refill_per_second=0.5)
        self.assertTrue(guard.allow(101, "hello", now=0))
        self.assertTrue(guard.allow(101, "/start", now=0))
        self.assertFalse(guard.allow(101, "/help", now=0))
        self.assertTrue(guard.allow(202, "/help", now=0))


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


class CurrencyWalletTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path, self.old_conn = db.DB_PATH, db._conn
        db.DB_PATH, db._conn = Path(self.temp.name) / "wallets.sqlite3", None
        self.uid = 77123
        db.create_player(self.uid)

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = self.old_conn
        db.DB_PATH = self.old_path
        self.temp.cleanup()

    def test_new_player_gets_40k_rub_and_15k_rub_equivalent_per_other_currency(self):
        rates = {"RUB": 1, "USD": .01, "EUR": .009, "GBP": .008,
                 "CNY": .07, "JPY": 1.5, "KZT": 5, "TRY": .4}
        self.assertTrue(db.wallets_initialize(self.uid, is_new_player=True,
                                              legacy_balance=100, rates=rates))
        wallets = db.wallets_get(self.uid)
        self.assertEqual(wallets["RUB"], 40000)
        self.assertEqual(set(wallets), fx.CURRENCY_CODES)
        for code in fx.CURRENCY_CODES - {"RUB"}:
            self.assertAlmostEqual(wallets[code] / rates[code], 15000, places=2)
        self.assertEqual(db.get_player(self.uid)["balance"], 1450)
        self.assertFalse(db.wallets_initialize(self.uid, is_new_player=True,
                                               legacy_balance=100, rates=rates))

    def test_legacy_wallet_migration_and_atomic_exchange(self):
        rates = {"RUB": 1, "USD": .01, "EUR": .009, "GBP": .008,
                 "CNY": .07, "JPY": 1.5, "KZT": 5, "TRY": .4}
        self.assertTrue(db.wallets_initialize(self.uid, is_new_player=False, legacy_balance=100))
        self.assertEqual(db.wallet_get(self.uid, "RUB"), 10000)
        self.assertEqual(db.wallet_get(self.uid, "USD"), 0)
        self.assertTrue(db.wallet_exchange(self.uid, "RUB", "USD", 1000, fx.convert(1000, "RUB", "USD", rates)))
        self.assertEqual(db.wallet_get(self.uid, "RUB"), 9000)
        self.assertEqual(db.wallet_get(self.uid, "USD"), 10)
        self.assertFalse(db.wallet_exchange(self.uid, "RUB", "EUR", 99999, 899.91))

    def test_live_rate_conversion_and_quote_validation(self):
        rates = {"RUB": 1, "USD": .01, "EUR": .009, "GBP": .008,
                 "CNY": .07, "JPY": 1.5, "KZT": 5, "TRY": .4}
        self.assertEqual(fx.convert(10, "USD", "RUB", rates), 1000)
        self.assertEqual(fx.convert(15000, "RUB", "JPY", rates), 22500)
        self.assertEqual(fx.rub_value(100, "USD", rates), 10000)
        payload = {"result": "success", "base_code": "RUB", "rates": rates,
                   "time_last_update_unix": int(time.time())}
        self.assertEqual(fx._validate_rates(payload)["rates"], rates)
        with self.assertRaises(ValueError):
            fx.convert(1, "BAD", "RUB", rates)


class CurrencyAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_starter_grant_completes_when_rates_arrive(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "currencies.sqlite3", None
            uid = 88110
            rates = {"RUB": 1, "USD": .01, "EUR": .009, "GBP": .008,
                     "CNY": .07, "JPY": 1.5, "KZT": 5, "TRY": .4}
            snapshot = {"rates": rates, "updated_at": int(time.time()), "source": "test"}
            try:
                db.create_player(uid)
                db.wallets_initialize(uid, is_new_player=True, legacy_balance=100)
                player = db.player_dict(db.get_player(uid))

                class Request:
                    query = {"uid": str(uid)}
                    async def json(self):
                        return {}

                with patch.object(server, "_load_profile", return_value=(uid, player)), \
                     patch.object(server.fx, "get_rates", new=AsyncMock(return_value=snapshot)):
                    response = await server.api_currencies(Request())
                data = json.loads(response.text)
                self.assertEqual(response.status, 200)
                self.assertEqual(db.get_player(uid)["balance"], 1450)
                self.assertEqual(db.wallet_get(uid, "RUB"), 40000)
                self.assertAlmostEqual(db.wallet_get(uid, "USD") / rates["USD"], 15000)
                self.assertEqual(data["source"], "test")
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_path

    async def test_exchange_endpoint_debits_and_credits_server_side(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "exchange.sqlite3", None
            uid = 88111
            rates = {"RUB": 1, "USD": .01, "EUR": .009, "GBP": .008,
                     "CNY": .07, "JPY": 1.5, "KZT": 5, "TRY": .4}
            snapshot = {"rates": rates, "updated_at": int(time.time()), "source": "test"}
            try:
                db.create_player(uid)
                db.wallets_initialize(uid, is_new_player=True, legacy_balance=100, rates=rates)
                player = db.player_dict(db.get_player(uid))
                server._RATE_BUCKETS.pop(f"fx:{uid}", None)

                class Request:
                    query = {}
                    async def json(self):
                        return {"uid": uid, "from": "RUB", "to": "USD", "amount": 1000}

                with patch.object(server, "_load_profile", return_value=(uid, player)), \
                     patch.object(server, "maintenance_guard", return_value=None), \
                     patch.object(server.fx, "get_rates", new=AsyncMock(return_value=snapshot)):
                    response = await server.api_currency_convert(Request())
                data = json.loads(response.text)
                self.assertEqual(response.status, 200)
                self.assertEqual(data["credited"], 10)
                self.assertEqual(db.wallet_get(uid, "RUB"), 39000)
                self.assertEqual(db.wallet_get(uid, "USD"), 160)
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_path


class SubscriptionGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsubscribed_player_is_blocked_before_game_api(self):
        class Request:
            path = "/api/spin"
            query = {}
            async def json(self):
                return {"initData": "signed-init-data"}

        handler_called = False
        async def handler(_request):
            nonlocal handler_called
            handler_called = True
            return server.web.json_response({"ok": True})

        with patch.object(server, "auth_uid", return_value=77101), \
             patch.object(server, "channel_access_allowed", new=AsyncMock(return_value=False)):
            response = await server.subscription_middleware(Request(), handler)
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(response.text)["error"], "subscription_required")
        self.assertFalse(handler_called)

    async def test_subscribed_player_reaches_game_api(self):
        class Request:
            path = "/api/spin"
            query = {}
            async def json(self):
                return {"initData": "signed-init-data"}

        async def handler(_request):
            return server.web.json_response({"ok": True})

        with patch.object(server, "auth_uid", return_value=77102), \
             patch.object(server, "channel_access_allowed", new=AsyncMock(return_value=True)):
            response = await server.subscription_middleware(Request(), handler)
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.text)["ok"])

    async def test_subscription_button_forces_fresh_channel_check(self):
        class Request:
            async def json(self):
                return {"initData": "signed-init-data"}

        checker = AsyncMock(return_value=True)
        with patch.object(server, "auth_uid", return_value=77104), \
             patch.object(server, "channel_access_allowed", new=checker):
            response = await server.api_subscription_check(Request())
        self.assertEqual(response.status, 200)
        self.assertTrue(json.loads(response.text)["subscribed"])
        checker.assert_awaited_once_with(77104, force_check=True)

    async def test_auth_does_not_create_player_before_subscription(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "subscription.sqlite3", None
            uid = 77103

            class Request:
                async def json(self):
                    return {"initData": "signed-init-data"}

            try:
                with patch.object(server, "auth_uid", return_value=uid), \
                     patch.object(server, "channel_access_allowed", new=AsyncMock(return_value=False)):
                    response = await server.api_auth(Request())
                self.assertEqual(response.status, 403)
                self.assertEqual(json.loads(response.text)["error"], "subscription_required")
                self.assertIsNone(db.get_player(uid))
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_path


class UpgradeAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_upgrade_is_free_even_when_player_has_no_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "free-upgrade.sqlite3", None
            uid = 88201
            item = {"id": "free-upgrade-number", "num": "+7 (900) 123-45-67",
                    "k": "RU", "r": "common", "p": 100, "op": "mts"}
            try:
                db.create_player(uid)
                db.wallets_initialize(uid, is_new_player=False, legacy_balance=100)
                db.touch_player(uid, balance=0)
                db.add_number(uid, item)
                player = db.player_dict(db.get_player(uid))

                class Request:
                    async def json(self):
                        return {"number_id": item["id"], "chance": 45}

                with patch.object(server, "_load_profile", return_value=(uid, player)), \
                     patch.object(server, "maintenance_guard", return_value=None), \
                     patch.object(game, "upgrade_result", return_value=(False, None)), \
                     patch.object(game, "wheel_landing", return_value=200):
                    response = await server.api_upgrade(Request())

                data = json.loads(response.text)
                self.assertEqual(response.status, 200)
                self.assertEqual(db.get_player(uid)["balance"], 0)
                self.assertTrue(all(amount == 0 for amount in db.wallets_get(uid).values()))
                self.assertEqual(data["balance"], 0)
                self.assertNotIn("cost", data)
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_path


    async def test_successful_90_percent_upgrade_preserves_rarity_and_adds_ten_percent(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "upgrade-value.sqlite3", None
            uid = 88202
            item = {"id": "upgrade-value-number", "num": "+7 (900) 123-45-67",
                    "k": "RU", "r": "rare", "p": 100_000, "op": "mts"}
            try:
                db.create_player(uid)
                db.wallets_initialize(uid, is_new_player=False, legacy_balance=100)
                db.add_number(uid, item)
                player = db.player_dict(db.get_player(uid))

                class Request:
                    async def json(self):
                        return {"number_id": item["id"], "chance": 90}

                with patch.object(server, "_load_profile", return_value=(uid, player)), \
                     patch.object(server, "maintenance_guard", return_value=None), \
                     patch.object(game, "upgrade_result", return_value=(True, "rare")), \
                     patch.object(game, "wheel_landing", return_value=100):
                    response = await server.api_upgrade(Request())

                data = json.loads(response.text)
                self.assertEqual(response.status, 200)
                self.assertTrue(data["win"])
                self.assertEqual(data["steps"], 0)
                self.assertEqual(data["item"]["r"], "rare")
                self.assertEqual(data["item"]["p"], 110_000)
            finally:
                if db._conn:
                    db._conn.close()
                db._conn = old_conn
                db.DB_PATH = old_path


class MarketListingTests(unittest.IsolatedAsyncioTestCase):
    async def test_player_can_list_number_at_custom_price(self):
        with tempfile.TemporaryDirectory() as temp:
            old_path, old_conn = db.DB_PATH, db._conn
            db.DB_PATH, db._conn = Path(temp) / "market-listing.sqlite3", None
            uid = 88203
            item = {"id": "market-custom-price", "num": "+7 (900) 123-45-67",
                    "k": "RU", "r": "rare", "p": 230, "op": "mts"}
            try:
                db.create_player(uid)
                db.add_number(uid, item)
                player = db.player_dict(db.get_player(uid))

                class Request:
                    async def json(self):
                        return {"number_id": item["id"], "price": 1_250_000, "kind": "sale"}

                with patch.object(server, "_load_profile", return_value=(uid, player)), \
                     patch.object(server, "maintenance_guard", return_value=None):
                    response = await server.api_market_list(Request())

                self.assertEqual(response.status, 200)
                listing = db.market_listings(10)
                self.assertEqual(len(listing), 1)
                self.assertEqual(listing[0]["price"], 1_250_000)
                self.assertEqual(db.get_number(item["id"])["status"], "market")
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
