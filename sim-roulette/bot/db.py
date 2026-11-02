"""
SQLite-хранилище SIM Roulette.

Таблицы:
  players            — игроки (Telegram-пользователи): баланс, опыт, настройки, статусы
  numbers            — номера (инвентарь игроков), привязаны к player_id
  market             — лоты на рынке (продажа номеров за баланс)
  offers             — предложения обмена номерами
  inbox              — входящие переводы (деньги/номеры) между игроками
  admins             — список администраторов
  levels             — пороги уровней (уровень → нужный опыт)
  coupons            — купоны (деньги, удача x2/x4/x10, донат-баланс)
  coupon_activations — история активаций купонов (для лимита активаций)
  maintenance        — режим технического перерыва (одна строка, id=1)
  purchases          — покупки за Telegram Stars
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "simroulette.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

# Пороги уровней: опыт, необходимый для перехода на level+1
LEVELS_XP = [0, 100, 250, 500, 900, 1500, 2400, 3600, 5200,
             7200, 9800, 13000, 17000, 22000, 28000, 35000]


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init(_conn)
    return _conn


def _init(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS players (
            user_id            INTEGER PRIMARY KEY,
            username           TEXT    DEFAULT '',
            first_name         TEXT    DEFAULT '',
            last_name          TEXT    DEFAULT '',
            photo_url          TEXT    DEFAULT '',
            balance            REAL    DEFAULT 100,
            coins              INTEGER DEFAULT 0,
            xp                 INTEGER DEFAULT 0,
            streak             INTEGER DEFAULT 0,
            last_day           TEXT    DEFAULT '',
            ref_count          INTEGER DEFAULT 0,
            ref_by             TEXT    DEFAULT '',
            is_admin           INTEGER DEFAULT 0,
            subscribed         INTEGER DEFAULT 0,
            banned             INTEGER DEFAULT 0,
            sound_on           INTEGER DEFAULT 1,
            vibration          INTEGER DEFAULT 1,
            effects            INTEGER DEFAULT 1,
            theme              TEXT    DEFAULT 'classic',
            spin_speed         TEXT    DEFAULT 'slow',
            spin_anim          TEXT    DEFAULT 'slide',
            spin_theme         TEXT    DEFAULT 'classic',
            up_speed           TEXT    DEFAULT 'normal',
            up_anim            TEXT    DEFAULT 'smooth',
            up_theme           TEXT    DEFAULT 'dial',
            use_coins          INTEGER DEFAULT 0,
            autosell           INTEGER DEFAULT 0,
            luck_until         INTEGER DEFAULT 0,
            luck_mult          INTEGER DEFAULT 1,
            donate_balance     REAL    DEFAULT 0,
            instant_block_until INTEGER DEFAULT 0,
            free_at            INTEGER DEFAULT 0,
            claimed_tasks      TEXT    DEFAULT '{}',
            achievements       TEXT    DEFAULT '[]',
            stats              TEXT    DEFAULT '{"spins":0,"rare":0,"sold":0,"upg":0,"listed":0,"bought":0,"ctry":[]}',
            ref_claims         TEXT    DEFAULT '{}',
            friends            TEXT    DEFAULT '[]',
            history            TEXT    DEFAULT '[]',
            created_at         INTEGER DEFAULT 0,
            last_seen          INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS numbers (
            id         TEXT PRIMARY KEY,
            player_id  INTEGER NOT NULL,
            rarity     TEXT    NOT NULL,
            country    TEXT    NOT NULL,
            operator   TEXT    DEFAULT '',
            num        TEXT    NOT NULL,
            price      REAL    NOT NULL,
            features   TEXT    DEFAULT '[]',
            score      INTEGER DEFAULT 0,
            status     TEXT    DEFAULT 'inv',   -- inv | market | swap
            created_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_numbers_player ON numbers(player_id);
        CREATE INDEX IF NOT EXISTS idx_numbers_status ON numbers(status);

        CREATE TABLE IF NOT EXISTS market (
            id         TEXT PRIMARY KEY,
            seller_id  INTEGER NOT NULL,
            number_id  TEXT    NOT NULL,
            price      REAL    NOT NULL,
            kind       TEXT    DEFAULT 'sale',  -- sale | swap
            created_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_market_seller ON market(seller_id);

        CREATE TABLE IF NOT EXISTS inbox (
            id         TEXT PRIMARY KEY,
            to_id      INTEGER NOT NULL,
            kind       TEXT    NOT NULL,        -- money | item
            amount     REAL    DEFAULT 0,
            currency   TEXT    DEFAULT '',
            number_id  TEXT    DEFAULT '',
            text       TEXT    DEFAULT '',
            created_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_inbox_to ON inbox(to_id);

        CREATE TABLE IF NOT EXISTS admins (
            user_id    INTEGER PRIMARY KEY,
            added_by   INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS levels (
            level      INTEGER PRIMARY KEY,
            xp_required INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS coupons (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            code             TEXT    NOT NULL UNIQUE,
            kind             TEXT    NOT NULL,   -- money | luck | donate
            value            REAL    NOT NULL,   -- деньги / множитель удачи / донат-баланс
            label            TEXT    DEFAULT '',
            expires_at       INTEGER DEFAULT 0,  -- 0 = без ограничения по времени
            max_activations  INTEGER DEFAULT 0,  -- 0 = без лимита
            used_count       INTEGER DEFAULT 0,
            active           INTEGER DEFAULT 1,
            created_by       INTEGER DEFAULT 0,
            created_at       INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS coupon_activations (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            coupon_id    INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            activated_at INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ca_coupon ON coupon_activations(coupon_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ca_unique ON coupon_activations(coupon_id, user_id);

        CREATE TABLE IF NOT EXISTS maintenance (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            active     INTEGER DEFAULT 0,
            start_at   INTEGER DEFAULT 0,   -- epoch ms: когда закрыть (0 = мгновенно)
            end_at     INTEGER DEFAULT 0,   -- epoch ms: когда открыть (0 = ручной выход)
            message    TEXT    DEFAULT '',
            created_by INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            sku        TEXT    NOT NULL,
            stars      INTEGER NOT NULL,
            payload    TEXT    DEFAULT '',
            created_at INTEGER DEFAULT 0
        );
        """
    )
    _seed_levels(c)
    c.commit()


def _seed_levels(c: sqlite3.Connection) -> None:
    if c.execute("SELECT COUNT(*) AS n FROM levels").fetchone()["n"]:
        return
    c.executemany(
        "INSERT OR IGNORE INTO levels(level, xp_required) VALUES (?, ?)",
        [(i, xp) for i, xp in enumerate(LEVELS_XP)],
    )
    c.commit()


# ---------------------------------------------------------------- утилиты
def now_ms() -> int:
    return int(time.time() * 1000)


def _jloads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def _jdumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- игроки
def get_player(user_id: int) -> sqlite3.Row | None:
    c = connect()
    with _lock:
        row = c.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    return row


def player_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("claimed_tasks", "stats", "ref_claims", "friends", "history"):
        d[k.replace("claimed_tasks", "claimed")] = _jloads(d.pop(k), {} if k in ("claimed_tasks", "stats", "ref_claims") else [])
    d["achievements"] = _jloads(d.get("achievements"), [])
    for b in ("is_admin", "subscribed", "banned", "sound_on", "vibration", "effects",
              "use_coins", "autosell"):
        d[b] = bool(d[b])
    return d


def touch_player(user_id: int, **fields) -> None:
    """Обновить поля игрока (принимает только известные колонки)."""
    if not fields:
        return
    allowed = {
        "username", "first_name", "last_name", "photo_url", "balance", "coins", "xp",
        "streak", "last_day", "ref_count", "ref_by", "is_admin", "subscribed", "banned",
        "sound_on", "vibration", "effects", "theme", "spin_speed", "spin_anim",
        "spin_theme", "up_speed", "up_anim", "up_theme", "use_coins", "autosell",
        "luck_until", "luck_mult", "donate_balance", "instant_block_until", "free_at",
        "claimed_tasks", "achievements", "stats", "ref_claims", "friends", "history",
        "last_seen",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("claimed_tasks", "achievements", "stats", "ref_claims", "friends", "history"):
            v = _jdumps(v)
        if isinstance(v, bool):
            v = int(v)
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    vals.append(user_id)
    c = connect()
    with _lock:
        c.execute(f"UPDATE players SET {', '.join(sets)} WHERE user_id=?", tuple(vals))
        c.commit()


def create_player(user_id: int, **profile) -> dict:
    now = now_ms()
    c = connect()
    with _lock:
        c.execute(
            """INSERT OR IGNORE INTO players
               (user_id, username, first_name, last_name, photo_url, created_at, last_seen)
               VALUES (?,?,?,?,?,?,?)""",
            (
                user_id,
                profile.get("username", ""),
                profile.get("first_name", ""),
                profile.get("last_name", ""),
                profile.get("photo_url", ""),
                now,
                now,
            ),
        )
        if profile:
            sets, vals = [], []
            for k in ("username", "first_name", "last_name", "photo_url"):
                if profile.get(k):
                    sets.append(f"{k}=?")
                    vals.append(profile[k])
            if sets:
                vals.append(user_id)
                c.execute(f"UPDATE players SET {', '.join(sets)} WHERE user_id=?", tuple(vals))
        c.commit()
        row = c.execute("SELECT * FROM players WHERE user_id=?", (user_id,)).fetchone()
    return player_dict(row)


def list_players() -> list[dict]:
    c = connect()
    with _lock:
        rows = c.execute("SELECT * FROM players").fetchall()
    return [player_dict(r) for r in rows]


def count_players() -> int:
    c = connect()
    return c.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]


# ---------------------------------------------------------------- уровни / опыт
def level_of(xp: int) -> int:
    lvl = 0
    rows = connect().execute("SELECT level, xp_required FROM levels ORDER BY level").fetchall()
    thresholds = {r["level"]: r["xp_required"] for r in rows} or {i: x for i, x in enumerate(LEVELS_XP)}
    max_lvl = max(thresholds)
    while lvl < max_lvl and xp >= thresholds.get(lvl + 1, 10**12):
        lvl += 1
    return lvl


def xp_bounds(level: int) -> tuple[int, int]:
    rows = connect().execute("SELECT level, xp_required FROM levels ORDER BY level").fetchall()
    th = {r["level"]: r["xp_required"] for r in rows} or {i: x for i, x in enumerate(LEVELS_XP)}
    return th.get(level, 0), th.get(level + 1, th.get(level, 0) + 1000)


# ---------------------------------------------------------------- номера
def add_number(player_id: int, item: dict) -> str:
    c = connect()
    with _lock:
        c.execute(
            """INSERT OR REPLACE INTO numbers
               (id, player_id, rarity, country, operator, num, price, features, score, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item["id"],
                player_id,
                item["r"],
                item["k"],
                item.get("op", ""),
                item["num"],
                float(item.get("p", 0)),
                _jdumps(item.get("features", [])),
                int(item.get("score", 0)),
                item.get("status", "inv"),
                now_ms(),
            ),
        )
        c.commit()
    return item["id"]


def remove_number(number_id: str) -> None:
    c = connect()
    with _lock:
        c.execute("DELETE FROM numbers WHERE id=?", (number_id,))
        c.execute("DELETE FROM market WHERE number_id=?", (number_id,))
        c.commit()


def get_number(number_id: str) -> dict | None:
    row = connect().execute("SELECT * FROM numbers WHERE id=?", (number_id,)).fetchone()
    return _number_dict(row) if row else None


def player_numbers(player_id: int) -> list[dict]:
    rows = connect().execute(
        "SELECT * FROM numbers WHERE player_id=? AND status='inv' ORDER BY created_at DESC",
        (player_id,),
    ).fetchall()
    return [_number_dict(r) for r in rows]


def _number_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "player_id": r["player_id"],
        "r": r["rarity"],
        "k": r["country"],
        "op": r["operator"],
        "num": r["num"],
        "p": r["price"],
        "features": _jloads(r["features"], []),
        "score": r["score"],
        "status": r["status"],
    }


# ---------------------------------------------------------------- рынок
def market_listings(limit: int = 100) -> list[dict]:
    c = connect()
    rows = c.execute(
        """SELECT m.id AS listing_id, m.seller_id, m.price, m.kind, m.created_at, n.*
           FROM market m JOIN numbers n ON n.id = m.number_id
           ORDER BY m.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        item = _number_dict(r)
        out.append({
            "id": r["listing_id"],
            "seller": r["seller_id"],
            "price": r["price"],
            "kind": r["kind"],
            "created_at": r["created_at"],
            "item": {k: item[k] for k in ("id", "r", "k", "op", "num", "p", "features", "score")},
        })
    return out


def market_add(listing_id: str, seller_id: int, number_id: str, price: float, kind: str = "sale") -> None:
    c = connect()
    with _lock:
        c.execute(
            "INSERT OR REPLACE INTO market (id, seller_id, number_id, price, kind, created_at) VALUES (?,?,?,?,?,?)",
            (listing_id, seller_id, number_id, price, kind, now_ms()),
        )
        c.execute("UPDATE numbers SET status=? WHERE id=?", ("market" if kind == "sale" else "swap", number_id))
        c.commit()


def market_remove(listing_id: str) -> dict | None:
    c = connect()
    with _lock:
        row = c.execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM market WHERE id=?", (listing_id,))
        c.execute("UPDATE numbers SET status='inv' WHERE id=?", (row["number_id"],))
        c.commit()
    return dict(row)


def market_get(listing_id: str) -> dict | None:
    row = connect().execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
    return dict(row) if row else None


def market_buy(listing_id: str, buyer_id: int) -> dict | None:
    """Снять лот с рынка и передать номер покупателю. Возвращает лот или None."""
    c = connect()
    with _lock:
        row = c.execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM market WHERE id=?", (listing_id,))
        c.execute(
            "UPDATE numbers SET player_id=?, status='inv' WHERE id=?",
            (buyer_id, row["number_id"]),
        )
        c.commit()
    return dict(row)


def transfer_number(number_id: str, to_player: int) -> bool:
    """Передать номер другому игроку (обмен на рынке)."""
    c = connect()
    with _lock:
        cur = c.execute(
            "UPDATE numbers SET player_id=?, status='inv' WHERE id=?",
            (to_player, number_id),
        )
        c.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------- inbox
def inbox_add(to_id: int, kind: str, amount: float = 0, currency: str = "",
              number_id: str = "", text: str = "") -> None:
    c = connect()
    with _lock:
        c.execute(
            "INSERT INTO inbox (id, to_id, kind, amount, currency, number_id, text, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (f"in_{now_ms()}_{to_id}", to_id, kind, amount, currency, number_id, text, now_ms()),
        )
        c.commit()


def inbox_take(user_id: int) -> list[dict]:
    c = connect()
    with _lock:
        rows = c.execute("SELECT * FROM inbox WHERE to_id=? ORDER BY created_at", (user_id,)).fetchall()
        if rows:
            c.executemany("DELETE FROM inbox WHERE id=?", [(r["id"],) for r in rows])
            c.commit()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- купоны
def coupon_create(code: str, kind: str, value: float, label: str = "",
                  expires_at: int = 0, max_activations: int = 0, created_by: int = 0) -> dict:
    c = connect()
    with _lock:
        c.execute(
            """INSERT INTO coupons (code, kind, value, label, expires_at, max_activations, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (code.upper(), kind, value, label, expires_at, max_activations, created_by, now_ms()),
        )
        c.commit()
        row = c.execute("SELECT * FROM coupons WHERE code=?", (code.upper(),)).fetchone()
    return dict(row)


def coupon_get(code: str) -> dict | None:
    row = connect().execute(
        "SELECT * FROM coupons WHERE code=?", (code.upper().strip(),)
    ).fetchone()
    return dict(row) if row else None


def coupon_activate(coupon: dict, user_id: int) -> tuple[bool, str]:
    """Активировать купон. Возвращает (успех, сообщение)."""
    cid = coupon["id"]
    c = connect()
    with _lock:
        if not coupon["active"]:
            return False, "Купон отключён"
        if coupon["expires_at"] and now_ms() > coupon["expires_at"]:
            return False, "Срок действия купона истёк"
        if coupon["max_activations"] and coupon["used_count"] >= coupon["max_activations"]:
            return False, "Достигнут лимит активаций"
        try:
            c.execute(
                "INSERT INTO coupon_activations (coupon_id, user_id, activated_at) VALUES (?,?,?)",
                (cid, user_id, now_ms()),
            )
        except sqlite3.IntegrityError:
            return False, "Вы уже активировали этот купон"
        c.execute("UPDATE coupons SET used_count=used_count+1 WHERE id=?", (cid,))
        c.commit()
    return True, "ok"


def coupon_list() -> list[dict]:
    rows = connect().execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def coupon_set_active(coupon_id: int, active: bool) -> None:
    c = connect()
    with _lock:
        c.execute("UPDATE coupons SET active=? WHERE id=?", (int(active), coupon_id))
        c.commit()


# ---------------------------------------------------------------- техперерыв
def maintenance_get() -> dict:
    row = connect().execute("SELECT * FROM maintenance WHERE id=1").fetchone()
    if not row:
        return {"active": 0, "start_at": 0, "end_at": 0, "message": "", "created_by": 0, "created_at": 0}
    return dict(row)


def maintenance_set(active: bool, start_at: int = 0, end_at: int = 0,
                    message: str = "", created_by: int = 0) -> dict:
    c = connect()
    with _lock:
        c.execute(
            """INSERT INTO maintenance (id, active, start_at, end_at, message, created_by, created_at)
               VALUES (1,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET active=excluded.active, start_at=excluded.start_at,
               end_at=excluded.end_at, message=excluded.message, created_by=excluded.created_by,
               created_at=excluded.created_at""",
            (int(active), start_at, end_at, message, created_by, now_ms()),
        )
        c.commit()
    return maintenance_get()


# ---------------------------------------------------------------- админы
def admin_add(user_id: int, added_by: int = 0) -> None:
    c = connect()
    with _lock:
        c.execute("INSERT OR IGNORE INTO admins (user_id, added_by, created_at) VALUES (?,?,?)",
                  (user_id, added_by, now_ms()))
        c.execute("UPDATE players SET is_admin=1 WHERE user_id=?", (user_id,))
        c.commit()


def admin_remove(user_id: int) -> None:
    c = connect()
    with _lock:
        c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        c.execute("UPDATE players SET is_admin=0 WHERE user_id=?", (user_id,))
        c.commit()


def admin_list() -> list[int]:
    rows = connect().execute("SELECT user_id FROM admins").fetchall()
    return [r["user_id"] for r in rows]


def is_admin(user_id: int) -> bool:
    return connect().execute(
        "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
    ).fetchone() is not None


# ---------------------------------------------------------------- покупки Stars
def purchase_add(user_id: int, sku: str, stars: int, payload: str = "") -> int:
    c = connect()
    with _lock:
        cur = c.execute(
            "INSERT INTO purchases (user_id, sku, stars, payload, created_at) VALUES (?,?,?,?,?)",
            (user_id, sku, stars, payload, now_ms()),
        )
        c.commit()
    return cur.lastrowid


def purchase_count(sku: str) -> int:
    return connect().execute(
        "SELECT COUNT(*) AS n FROM purchases WHERE sku=?", (sku,)
    ).fetchone()["n"]
