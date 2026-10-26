"""
HTTP API + раздача WebApp.

Аутентификация — по initData Telegram (HMAC). В DEV-режиме (DEV_MODE=1)
принимается payload с прямым uid, чтобы игру можно было тестировать
вне Telegram.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path

from aiohttp import web

import bot.db as db
import bot.game as game
import bot.fx as fx
from bot.config import APP_ENV, ADMIN_IDS, BOT_TOKEN, CHANNEL, DEV_MODE, PORT, WEBAPP_DIR, WEBAPP_URL
from bot.utils.security import verify_init_data
from bot.utils.subscription import check_subscription
from bot.utils.notify import notify_maintenance

log = logging.getLogger("simroulette.web")

CH_URL = f"https://t.me/{CHANNEL.lstrip('@')}"

# uid -> (item, timestamp) — номер, ожидающий решения «Оставить/Продать»
_pending: dict[int, dict] = {}
PENDING_TTL = 10 * 60 * 1000

# Small per-process token buckets for expensive write endpoints.
_RATE_BUCKETS: dict[str, deque] = {}
_RATE_WINDOW = 60.0


def _rate_limited(key: str, weight: int, capacity: int) -> int:
    now = time.monotonic()
    bucket = _RATE_BUCKETS.setdefault(key, deque())
    while bucket and now - bucket[0][0] >= _RATE_WINDOW:
        bucket.popleft()
    used = sum(entry[1] for entry in bucket)
    weight = max(1, int(weight))
    if used + weight > capacity:
        return max(1, int(_RATE_WINDOW - (now - bucket[0][0]))) if bucket else 1
    bucket.append((now, weight))
    if len(_RATE_BUCKETS) > 5000:
        for old_key, q in list(_RATE_BUCKETS.items()):
            while q and now - q[0][0] >= _RATE_WINDOW:
                q.popleft()
            if not q:
                _RATE_BUCKETS.pop(old_key, None)
    return 0


@web.middleware
async def request_error_middleware(request: web.Request, handler):
    request_id = uuid.uuid4().hex[:12]
    try:
        response = await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        # Never log request bodies, initData, or tokens.
        log.exception("Unhandled request error id=%s method=%s path=%s",
                      request_id, request.method, request.path)
        response = web.json_response({"ok": False, "error": "internal_error",
                                      "request_id": request_id}, status=500)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


async def _backup_start(app: web.Application) -> None:
    from bot.backup import backup_loop
    app["backup_task"] = asyncio.create_task(backup_loop(), name="sqlite-backup-loop")


async def _backup_stop(app: web.Application) -> None:
    task = app.get("backup_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# ---------------------------------------------------------------- товары VIP-магазина
SKUS = [
    {"sku": "luck2",  "title": "Удача ×2",   "desc": "+100% к шансу редких номеров на 30 минут",
     "stars": 25,  "icon": "🍀"},
    {"sku": "luck4",  "title": "Удача ×4",   "desc": "+300% к шансу редких номеров на 30 минут",
     "stars": 75,  "icon": "🍀"},
    {"sku": "luck10", "title": "Удача ×10",  "desc": "Максимальная удача на 15 минут",
     "stars": 250, "icon": "🍀"},
    {"sku": "coins",  "title": "Коины удачи", "desc": "+10 коинов (каждый даёт +15% к редкости)",
     "stars": 50,  "icon": "🪙"},
    {"sku": "free",   "title": "Бесплатный спин", "desc": "Мгновенно сбрасывает кулдаун бесплатного спина",
     "stars": 10,  "icon": "⚡"},
    {"sku": "donate", "title": "Донат-баланс", "desc": "+100 единиц баланса (тратится первым)",
     "stars": 100, "icon": "💎"},
]
SKU_BY_ID = {s["sku"]: s for s in SKUS}


# ---------------------------------------------------------------- утилиты
def now_ms() -> int:
    return int(time.time() * 1000)


async def read_json(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


def auth_uid(request: web.Request, payload: dict) -> int | None:
    """Возвращает user_id или None, если авторизация не пройдена."""
    init_data = payload.get("initData") or ""
    if init_data:
        user = verify_init_data(init_data)
        if user and user.get("id"):
            return int(user["id"])
        return None
    if DEV_MODE and payload.get("uid"):
        try:
            return int(payload["uid"])
        except Exception:
            return None
    return None


def ensure_player(user_id: int, profile: dict | None = None) -> dict:
    row = db.get_player(user_id)
    is_new = row is None
    if row:
        p = db.player_dict(row)
        if profile:
            upd = {k: profile.get(k) for k in ("username", "first_name", "last_name", "photo_url")
                   if profile.get(k)}
            if upd:
                db.touch_player(user_id, **upd)
                p.update(upd)
    else:
        p = db.create_player(user_id, **(profile or {}))
        if user_id in ADMIN_IDS or user_id in db.admin_list():
            db.admin_add(user_id)
            p["is_admin"] = True
    # One-time migration: old common units were denominated as 100 RUB each.
    db.wallets_initialize(user_id, is_new_player=is_new, legacy_balance=p.get("balance", 100))
    p = db.player_dict(db.get_player(user_id))
    p["wallets"] = db.wallets_get(user_id)
    # админы из .env всегда в списке админов
    if user_id in ADMIN_IDS and not p.get("is_admin"):
        db.admin_add(user_id)
        p["is_admin"] = True
    return p


def _load_profile(request: web.Request, payload: dict) -> tuple[int, dict] | None:
    uid = auth_uid(request, payload)
    if uid is None:
        return None
    profile = {}
    if DEV_MODE and payload.get("profile"):
        profile = payload["profile"]
    return uid, ensure_player(uid, profile)


def viewer_is_admin(user_id: int | None) -> bool:
    """Проверка прав ТОЛЬКО на сервере: .env-админы + таблица admins."""
    if not user_id:
        return False
    if user_id in ADMIN_IDS:
        return True
    row = db.get_player(user_id)
    return bool(row and row["is_admin"]) or db.is_admin(user_id)


def viewer_of(request: web.Request, payload: dict) -> tuple[int | None, bool]:
    """(uid, is_admin) для произвольного запроса, в т.ч. неавторизованного."""
    uid = auth_uid(request, payload)
    return uid, viewer_is_admin(uid)


SUBSCRIPTION_EXEMPT_PATHS = {
    "/api/health", "/api/auth", "/api/subscription/check",
}


def _subscription_required_response() -> web.Response:
    return web.json_response({
        "ok": False,
        "error": "subscription_required",
        "channel": CHANNEL,
        "channel_url": CH_URL,
    }, status=403)


async def channel_access_allowed(user_id: int, *, force_check: bool = False) -> bool:
    """Разработка и админы не блокируются; игрокам требуется членство в канале."""
    if DEV_MODE or viewer_is_admin(user_id):
        return True
    return await check_subscription(user_id, force=force_check)


@web.middleware
async def subscription_middleware(request: web.Request, handler):
    """Закрывает весь игровой API для неподписанных пользователей."""
    path = request.path
    if not path.startswith("/api/") or path in SUBSCRIPTION_EXEMPT_PATHS:
        return await handler(request)

    payload = {**dict(request.query), **(await read_json(request))}
    uid = auth_uid(request, payload)
    if uid is None:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    if not await channel_access_allowed(uid):
        return _subscription_required_response()
    return await handler(request)


def _msk_hhmm_ms(value: str) -> int:
    """'23:30' по МСК -> epoch ms (ближайшее будущее)."""
    hh, mm = str(value).split(":")[:2]
    utc_now = dt.datetime.now(dt.timezone.utc)
    msk_now = utc_now + dt.timedelta(hours=3)
    target = msk_now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if target <= msk_now:
        target += dt.timedelta(days=1)
    return now_ms() + int((target - msk_now).total_seconds() * 1000)


def maintenance_state(user_id: int | None = None, is_admin: bool = False) -> dict:
    """Состояние техперерыва для конкретного зрителя.

    blocked=True означает «этому пользователю игра закрыта». Администраторы
    продолжают играть в обычном режиме (по решению владельца проекта), при этом
    видят специальный баннер о том, что игроки сейчас не играют.
    """
    m = db.maintenance_get()
    now = now_ms()

    # Ленивое авто-открытие: даже если сервер простаивал, первый же запрос
    # снимет перерыв, у которого истёк end_at.
    if m["active"] and m["end_at"] and now >= m["end_at"]:
        db.maintenance_set(False, 0, 0, "", 0)
        db.maintenance_log_add("auto_end", m["message"], m["start_at"], m["end_at"], 0)
        closed = dict(m)
        m = db.maintenance_get()
        try:
            asyncio.get_running_loop().create_task(
                notify_maintenance("auto_end", closed["message"], closed["start_at"], closed["end_at"])
            )
        except RuntimeError:
            pass

    active = bool(m["active"])
    started = active and (not m["start_at"] or now >= m["start_at"])
    seconds_left = max(0, (m["start_at"] - now) // 1000) if (active and not started) else 0
    ends_in = max(0, (m["end_at"] - now) // 1000) if (active and m["end_at"]) else 0
    span = (m["end_at"] - max(m["start_at"], m["created_at"])) if (active and m["end_at"]) else 0
    progress = max(0, min(100, int((now - max(m["start_at"], m["created_at"])) / span * 100))) if span > 0 else 0

    return {
        "id": m["created_at"],
        "active": active,
        "start_at": m["start_at"],
        "end_at": m["end_at"],
        "message": m["message"],
        "started": started,
        "blocked": bool(started and not is_admin),
        "is_admin": bool(is_admin),
        "seconds_left": seconds_left,
        "ends_in": ends_in,
        "progress": progress,
        "channel": CHANNEL,
        "channel_url": CH_URL,
        "now": now,
    }


def maintenance_guard(p: dict | None) -> web.Response | None:
    """Единая точка блокировки игровых действий. None = можно продолжать."""
    uid = p["user_id"] if p else None
    ms = maintenance_state(uid, viewer_is_admin(uid))
    if not ms["blocked"]:
        return None
    return web.json_response(
        {"ok": False, "error": "maintenance", "maintenance": ms}, status=503
    )


def push_history(p: dict, ev: dict) -> None:
    hist = p.get("history") or []
    hist.insert(0, {**ev, "ts": now_ms()})
    p["history"] = hist[:150]


def add_xp(p: dict, amount: int) -> dict:
    """Начислить опыт, обработать повышение уровня. Возвращает события."""
    before = db.level_of(p["xp"])
    p["xp"] = p.get("xp", 0) + amount
    after = db.level_of(p["xp"])
    events = []
    if after > before:
        reward = (after + 1) * 25
        p["balance"] = p.get("balance", 0) + reward
        push_history(p, {"k": "lvl", "lvl": after + 1, "amt": reward})
        events.append({"type": "level", "level": after + 1, "reward": reward})
    return {"events": events, "new_level": after}


def limits_payload() -> dict:
    return {
        "spin_x": game.SPIN_X,
        "category_cost": game.CATEGORY_COST,
        "category_level": game.CATEGORY_LEVEL,
        "wheel_min": game.WHEEL_MIN,
        "wheel_max": game.WHEEL_MAX,
        "wheel_steps": game.WHEEL_STEP_BANDS,
        "free_cd": game.FREE_CD,
    }


def state_payload(p: dict) -> dict:
    inv = db.player_numbers(p["user_id"])
    lvl = db.level_of(p["xp"])
    cur_xp, next_xp = db.xp_bounds(lvl)
    p["wallets"] = db.wallets_get(p["user_id"])
    return {
        "player": p,
        "wallets": p["wallets"],
        "inventory": inv,
        "level": lvl,
        "level_xp": cur_xp,
        "next_level_xp": next_xp,
        "maintenance": maintenance_state(p["user_id"], viewer_is_admin(p["user_id"])),
        "limits": limits_payload(),
        "channel": CHANNEL,
        "channel_url": CH_URL,
        "now": now_ms(),
    }


def _fallback_accounting_rates() -> dict[str, float]:
    """Old game pricing ratios used only for totals if the live FX provider is down."""
    return {fx.CURRENCY_BY_COUNTRY[c["k"]]: float(c["b"]) / 100.0 for c in game.COUNTRIES}


async def _wallet_rates(user_id: int) -> tuple[dict[str, float], dict | None]:
    try:
        snapshot = await fx.get_rates()
        db.wallets_finish_starter_grant(user_id, snapshot["rates"])
        return snapshot["rates"], snapshot
    except fx.FXUnavailable:
        return _fallback_accounting_rates(), None


def _wallet_value_units(amount: float, currency: str, rates: dict[str, float]) -> float:
    return round(fx.rub_value(amount, currency, rates) / 100.0, 2)


def _wallet_settlement(balance_before: float, balance_after: float,
                       native_deltas: list[dict], rates: dict[str, float]) -> list[dict]:
    """Add any legacy reward/adjustment to RUB while preserving FX wallet movements."""
    units_from_wallets = sum(_wallet_value_units(float(d["amount"]), d["currency"], rates)
                             for d in native_deltas)
    residual_rub = round((float(balance_after) - float(balance_before) - units_from_wallets) * 100, 2)
    result = list(native_deltas)
    if abs(residual_rub) >= 0.005:
        result.append({"currency": "RUB", "amount": residual_rub})
    return result


# ================================================================ API
async def api_currencies(request: web.Request) -> web.Response:
    payload = {**await read_json(request), **dict(request.query)}
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, _ = auth
    try:
        snapshot = await fx.get_rates()
        db.wallets_finish_starter_grant(uid, snapshot["rates"])
    except fx.FXUnavailable as exc:
        return web.json_response({"ok": False, "error": "fx_unavailable", "detail": str(exc),
                                  "wallets": db.wallets_get(uid)}, status=503)
    return web.json_response({"ok": True, "currencies": fx.CURRENCIES,
                              "wallets": db.wallets_get(uid),
                              "balance": db.get_player(uid)["balance"], "rates": snapshot["rates"],
                              "base": "RUB", "updated_at": snapshot["updated_at"],
                              "source": snapshot["source"], "stale": bool(snapshot.get("stale"))})


async def api_currency_convert(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    limited = _rate_limited(f"fx:{uid}", 1, 10)
    if limited:
        return web.json_response({"ok": False, "error": "rate_limited", "retry_in": limited}, status=429)
    source, target = str(payload.get("from") or ""), str(payload.get("to") or "")
    try:
        raw_amount = payload.get("amount")
        amount = 0 if isinstance(raw_amount, bool) else fx.currency_round(float(raw_amount), source)
    except (TypeError, ValueError):
        amount = 0
    if source not in fx.CURRENCY_CODES or target not in fx.CURRENCY_CODES or source == target:
        return web.json_response({"ok": False, "error": "bad_currency"}, status=400)
    if not (0.01 <= amount <= 1_000_000_000):
        return web.json_response({"ok": False, "error": "bad_amount"}, status=400)
    try:
        snapshot = await fx.get_rates()
        rates = snapshot["rates"]
        db.wallets_finish_starter_grant(uid, rates)
        p["balance"] = float(db.get_player(uid)["balance"])
        output = fx.convert(amount, source, target, rates)
        if output <= 0:
            return web.json_response({"ok": False, "error": "amount_too_small"}, status=400)
        delta_units = (fx.rub_value(output, target, rates) - fx.rub_value(amount, source, rates)) / 100.0
    except fx.FXUnavailable:
        return web.json_response({"ok": False, "error": "fx_unavailable"}, status=503)
    old_balance = float(p.get("balance", 0))
    deltas = [{"currency": source, "amount": -amount}, {"currency": target, "amount": output}]
    ok = db.touch_player(uid, balance=round(old_balance + delta_units, 2), wallet_deltas=deltas)
    if not ok:
        return web.json_response({"ok": False, "error": "no_funds",
                                  "wallets": db.wallets_get(uid)}, status=402)
    return web.json_response({"ok": True, "from": source, "to": target,
                              "debited": round(amount, 2), "credited": output,
                              "rate": round(output / amount, 8),
                              "wallets": db.wallets_get(uid),
                              "balance": db.get_player(uid)["balance"],
                              "updated_at": snapshot["updated_at"], "source": snapshot["source"]})


async def api_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "maintenance": maintenance_state()})


async def api_subscription_check(request: web.Request) -> web.Response:
    payload = await read_json(request)
    uid = auth_uid(request, payload)
    if uid is None:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    subscribed = await channel_access_allowed(uid, force_check=True)
    return web.json_response({"ok": True, "subscribed": subscribed,
                              "channel": CHANNEL, "channel_url": CH_URL})


async def api_auth(request: web.Request) -> web.Response:
    payload = await read_json(request)
    uid = auth_uid(request, payload)
    if uid is None:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    if not await channel_access_allowed(uid):
        return _subscription_required_response()
    profile = payload.get("profile") or {}
    p = ensure_player(uid, profile)
    fx_updated = None
    try:
        snapshot = await fx.get_rates()
        db.wallets_finish_starter_grant(uid, snapshot["rates"])
        fx_updated = snapshot["updated_at"]
        p = ensure_player(uid, profile)
    except fx.FXUnavailable:
        pass  # Login remains available; the foreign starter grant completes when rates recover.
    is_admin = viewer_is_admin(uid)
    p["wallets"] = db.wallets_get(uid)
    return web.json_response({
        "ok": True,
        "player": p,
        "wallets": db.wallets_get(uid),
        "fx_updated_at": fx_updated,
        "is_admin": is_admin,
        "maintenance": maintenance_state(uid, is_admin),
        "limits": limits_payload(),
    })


async def api_state(request: web.Request) -> web.Response:
    payload = {**await read_json(request), **dict(request.query)}
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    db.touch_player(uid, last_seen=now_ms())
    p = db.player_dict(db.get_player(uid))
    return web.json_response({"ok": True, **state_payload(p)})


async def api_import(request: web.Request) -> web.Response:
    """Перенос старого сохранения из localStorage в БД (первый вход)."""
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    data = payload.get("data") or {}

    # Импорт разрешён ровно один раз и только на пустой аккаунт.
    if p.get("imported") or db.player_numbers(uid):
        return web.json_response({"ok": True, "imported": 0, "reason": "already"})

    cap = game.IMPORT_MAX
    imported = 0
    if isinstance(data.get("bal"), (int, float)) and data["bal"] > p["balance"]:
        p["balance"] = min(float(data["bal"]), float(cap["balance"]))
    for key, out in (("coins", "coins"), ("xp", "xp"), ("streak", "streak"),
                     ("ref", "ref_count")):
        if key in data and data[key] is not None:
            try:
                val = int(data[key])
                limit = cap.get(out, cap["xp"])
                p[out] = max(0, min(val, limit))
            except Exception:
                pass
    if data.get("refBy") is not None:
        p["ref_by"] = str(data["refBy"])[:64]
    for key, out in (("soundOn", "sound_on"),):
        if key in data:
            p[out] = bool(data[key])
    for key, out in (("sSpd", "spin_speed"), ("sThm", "spin_theme"), ("sAnim", "spin_anim"),
                     ("uSpd", "up_speed"), ("uThm", "up_theme"), ("uAnim", "up_anim")):
        stg = data.get("stg") or {}
        if stg.get(key):
            p[out] = stg[key]
    if "useCoins" in (data.get("stg") or {}):
        p["use_coins"] = bool(data["stg"]["useCoins"])
    if "autosell" in (data.get("stg") or {}):
        p["autosell"] = bool(data["stg"]["autosell"])

    for it in (data.get("inv") or [])[:cap["numbers"]]:
        if not isinstance(it, dict) or not it.get("num") or not it.get("r") or not it.get("k"):
            continue
        try:
            price = float(it.get("p") or 0)
        except Exception:
            price = 0.0
        if price <= 0:
            continue
        c = game.C_BY_ID.get(it["k"])
        if not c or it["r"] not in game.R_BY_ID:
            continue
        r = game.R_BY_ID[it["r"]]
        price = min(price, c["b"] * r["m"] * 1.2)
        db.add_number(uid, {
            "id": str(it.get("id") or game.new_id())[:40],
            "num": str(it["num"])[:40],
            "k": it["k"], "r": it["r"], "op": it.get("op", "") or game.pick_operator(it["k"])["id"],
            "p": round(price, 2),
        })
        imported += 1

    p["achievements"] = list(data.get("ach") or p.get("achievements") or [])
    p["stats"] = data.get("t") or p.get("stats") or {}
    p["history"] = [
        {**h, "ts": int(h.get("ts") or now_ms())}
        for h in (data.get("hist") or [])[:150] if isinstance(h, dict)
    ]
    db.touch_player(uid, balance=p["balance"], coins=p["coins"], xp=p["xp"],
                    streak=p["streak"], ref_count=p["ref_count"], ref_by=p["ref_by"],
                    sound_on=p["sound_on"], spin_speed=p["spin_speed"], spin_theme=p["spin_theme"],
                    spin_anim=p["spin_anim"], up_speed=p["up_speed"], up_theme=p["up_theme"],
                    up_anim=p["up_anim"], use_coins=p["use_coins"], autosell=p["autosell"],
                    achievements=p["achievements"], stats=p["stats"], history=p["history"],
                    imported=True)
    return web.json_response({"ok": True, "imported": imported})


# ---------------------------------------------------------------- спин
async def api_spin(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth

    if (resp := maintenance_guard(p)) is not None:
        return resp
    try:
        rate_weight = min(20, max(1, int(payload.get("count") or 1)))
    except (TypeError, ValueError):
        rate_weight = 1
    limited = _rate_limited(f"spin:{uid}", rate_weight, 120)
    if limited:
        return web.json_response({"ok": False, "error": "rate_limited", "retry_in": limited}, status=429)

    country = str(payload.get("country") or "RU")
    if country not in game.C_BY_ID:
        country = "RU"
    rates, _fx_snapshot = await _wallet_rates(uid)
    p["balance"] = float(db.get_player(uid)["balance"])
    balance_before = p["balance"]
    wallet_deltas: list[dict] = []
    currency = fx.CURRENCY_BY_COUNTRY[country]
    category = payload.get("category") or None
    if category and category not in game.R_BY_ID:
        category = None
    try:
        count = max(1, min(20, int(payload.get("count") or 1)))
    except Exception:
        count = 1
    use_coins = bool(payload.get("useCoins"))
    free = bool(payload.get("free"))

    # Бесплатный спин: серверный кулдаун + жёсткие ограничения.
    # Раньше можно было слать free=true бесконечно и с category="sec"
    # получать гарантированные секретные номера даром.
    if free:
        elapsed = now_ms() - (p.get("free_at") or 0)
        if elapsed < game.FREE_CD:
            return web.json_response({
                "ok": False, "error": "free_cd",
                "retry_in": max(1, (game.FREE_CD - elapsed) // 1000),
            }, status=429)
        count = 1
        category = None
        use_coins = False

    # Категории выше «эпик» открываются по уровню.
    need_lvl = game.CATEGORY_LEVEL.get(category or "", 0)
    if need_lvl and db.level_of(p["xp"]) < need_lvl:
        return web.json_response({
            "ok": False, "error": "level_locked", "need": need_lvl,
        }, status=403)

    if now_ms() < (p.get("instant_block_until") or 0) and str(payload.get("speed")) == "instant":
        return web.json_response({"ok": False, "error": "instant_ban"}, status=429)

    cost = game.spin_cost(category, count, free)
    currency_cfg = game.C_BY_ID[country]
    cost_native = round(cost * currency_cfg["b"], 2)
    donor_native = min(cost_native, p.get("donate_balance", 0) * 100 * rates[currency])
    debit_native = round(cost_native - donor_native, 2)
    if not free and db.wallet_get(uid, currency) + 0.005 < debit_native:
        return web.json_response({"ok": False, "error": "no_funds", "currency": currency,
                                  "wallet": db.wallet_get(uid, currency), "need": debit_native}, status=402)

    # Донат-баланс считается RUB-эквивалентом, обычная сумма списывается
    # с кошелька валюты выбранной страны.
    if cost:
        donor_used = donor_native / (100 * rates[currency]) if donor_native else 0
        p["donate_balance"] = round(max(0, p.get("donate_balance", 0) - donor_used), 2)
        p["balance"] = round(p["balance"] - _wallet_value_units(debit_native, currency, rates), 2)
        if debit_native:
            wallet_deltas.append({"currency": currency, "amount": -debit_native})

    # удача: купон (luck_until) + коин за каждый спин
    boost = 0.0
    if p.get("luck_until", 0) > now_ms():
        boost += max(1, int(p.get("luck_mult", 1))) - 1
    coins_used = 0
    if use_coins and p.get("coins", 0) > 0:
        # Не больше 3 коинов за один запрос: иначе ×20 спинов превращали
        # удачу в гарантированный «Секретный».
        take = min(p["coins"], count, 3)
        p["coins"] -= take
        coins_used = take
        boost += 0.35 * take
    luck_mult = min(1 + boost, game.LUCK_MAX)

    p["stats"]["spins"] = p["stats"].get("spins", 0) + count
    _record_task_activity(uid, "spins", count)
    if country not in p["stats"].get("ctry", []):
        p["stats"].setdefault("ctry", []).append(country)
    push_history(p, {"k": "spin", "price": cost, "n": count} if cost else {"k": "freespin", "n": count})
    if free:
        p["free_at"] = now_ms()
    xp_gain = (1 if free else 3) * count
    xp_events = add_xp(p, xp_gain)

    taken = [n["num"] for n in db.player_numbers(uid)]
    items = []
    sold_total = 0.0
    kept = 0
    for _ in range(count):
        item = game.make_spin(country, category, luck_mult, taken)
        taken.append(item["num"])
        if game.R_INDEX[item["r"]] >= 2:
            p["stats"]["rare"] = p["stats"].get("rare", 0) + 1
            _record_task_activity(uid, "rare", 1)
        if count > 1 and p.get("autosell") and item["r"] in ("common", "unc"):
            amt = _wallet_value_units(item["p"], currency, rates)
            p["balance"] = round(p["balance"] + amt, 2)
            wallet_deltas.append({"currency": currency, "amount": float(item["p"])})
            p["stats"]["sold"] = p["stats"].get("sold", 0) + 1
            _record_task_activity(uid, "sold", 1)
            sold_total += amt
            add_xp(p, 2)
            push_history(p, {"k": "bank", "num": item["num"], "amt": amt, "auto": True})
            item["sold"] = True
        elif count == 1:
            pass  # одиночный спин — ждёт решения игрока
        else:
            db.add_number(uid, item)
            kept += 1
            push_history(p, {"k": "keep", "num": item["num"]})
        items.append(item)

    if count == 1:
        _pending[uid] = {"item": items[0], "ts": now_ms()}
    if sold_total:
        push_history(p, {"k": "mass_sell", "n": count - kept, "amt": round(sold_total, 2)})
        add_xp(p, min(20, count * 2))

    _check_achievements(p)
    deltas = _wallet_settlement(balance_before, p["balance"], wallet_deltas, rates)
    db.touch_player(uid, balance=p["balance"], wallet_deltas=deltas,
                    coins=p["coins"], xp=p["xp"], donate_balance=p["donate_balance"],
                    free_at=p["free_at"], stats=p["stats"], history=p["history"],
                    achievements=p["achievements"])

    return web.json_response({
        "ok": True,
        "items": items,
        "coins_used": coins_used,
        "cost": cost,
        "luck_mult": round(luck_mult, 2),
        "balance": p["balance"],
        "wallets": db.wallets_get(uid),
        "currency": currency,
        "cost_native": cost_native,
        "donate_balance": p.get("donate_balance", 0),
        "xp_events": xp_events["events"],
    })


async def api_decide(request: web.Request) -> web.Response:
    """Решение по одиночному спину: keep | sell."""
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    pend = _pending.get(uid)
    if not pend or now_ms() - pend["ts"] > PENDING_TTL:
        return web.json_response({"ok": False, "error": "nothing"}, status=404)
    item = pend["item"]
    action = str(payload.get("action") or "")
    rates, _fx_snapshot = await _wallet_rates(uid)
    p["balance"] = float(db.get_player(uid)["balance"])
    balance_before = p["balance"]
    native_deltas: list[dict] = []
    earned = 0.0

    if action == "keep":
        db.add_number(uid, item)
        push_history(p, {"k": "keep", "num": item["num"]})
    elif action == "sell":
        currency = fx.CURRENCY_BY_COUNTRY.get(item["k"], "RUB")
        native_amount = round(float(item["p"]), 2)
        earned = _wallet_value_units(native_amount, currency, rates)
        p["balance"] = round(p["balance"] + earned, 2)
        native_deltas.append({"currency": currency, "amount": native_amount})
        p["stats"]["sold"] = p["stats"].get("sold", 0) + 1
        _record_task_activity(uid, "sold", 1)
        add_xp(p, 2)
        push_history(p, {"k": "sell", "num": item["num"], "amt": earned})
    else:
        return web.json_response({"ok": False, "error": "bad_action"}, status=400)

    _pending.pop(uid, None)
    _check_achievements(p)
    deltas = _wallet_settlement(balance_before, p["balance"], native_deltas, rates)
    db.touch_player(uid, balance=p["balance"], wallet_deltas=deltas, xp=p["xp"],
                    stats=p["stats"], history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "earned": earned, "balance": p["balance"],
                              "wallets": db.wallets_get(uid)})


# ---------------------------------------------------------------- бонус / бесплатный спин
async def api_bonus(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    today = time.strftime("%Y-%m-%d")
    if p.get("last_day") == today:
        return web.json_response({"ok": False, "error": "already"}, status=409)
    import datetime as _dt
    yest = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    p["streak"] = (p.get("streak", 0) + 1) if p.get("last_day") == yest else 1
    p["last_day"] = today
    mult = 1 + min(0.5, (p["streak"] - 1) * 0.1)
    reward = round(game.BONUS_X * mult)
    p["balance"] = round(p["balance"] + reward, 2)
    xp_ev = add_xp(p, 20)
    push_history(p, {"k": "bonus", "amt": reward})
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], xp=p["xp"], streak=p["streak"],
                    last_day=today, history=p["history"],
                    achievements=p["achievements"])
    return web.json_response({"ok": True, "reward": reward, "streak": p["streak"],
                              "balance": p["balance"], "xp_events": xp_ev["events"]})


# ---------------------------------------------------------------- апгрейд
async def api_upgrade(request: web.Request) -> web.Response:
    """Колесо апгрейда.

    Игрок выбирает номер и процентную ставку (сектор победы на колесе).
    Сервер сам решает исход и возвращает угол остановки стрелки — клиент
    только проигрывает анимацию, поэтому накрутить результат нельзя.
    """
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp

    number_id = str(payload.get("number_id") or "")
    chance = game.clamp_chance(payload.get("chance", payload.get("bet", 45)))
    steps = game.upgrade_steps(chance)

    num = db.get_number(number_id)
    if not num or num["player_id"] != uid:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    if num["r"] == "sec":
        return web.json_response({"ok": False, "error": "max"}, status=400)

    # Апгрейд бесплатный: игрок ставит только номер и процент шанса.
    # Баланс и кошельки не проверяются и не списываются.
    db.remove_number(number_id)   # при провале номер сгорает
    p["stats"]["upg"] = p["stats"].get("upg", 0) + 1
    _record_task_activity(uid, "upg", 1)
    win, new_r = game.upgrade_result(num["r"], chance, steps)
    landing = game.wheel_landing(chance, win)

    result_item = None
    if win:
        c = game.C_BY_ID.get(num["k"], game.COUNTRIES[0])
        rarity = game.R_BY_ID[new_r]
        digits = game.gen_digits(c["L"], new_r)
        taken = [n["num"] for n in db.player_numbers(uid)]
        new_num = game.format_number(c, digits)
        op_id = num["op"] or game.pick_operator(c["k"])["id"]
        if new_num in taken:
            result_item = game.gen_unique_number(c, op_id, rarity, taken)
        else:
            analysis = game.analyze_digits(digits)
            result_item = {
                "id": game.new_id(), "num": new_num, "k": c["k"], "r": new_r,
                "p": game.price_for(digits, rarity, c), "op": op_id,
                "features": analysis["features"], "score": analysis["score"],
            }
        db.add_number(uid, result_item)
        add_xp(p, 15)
        push_history(p, {"k": "upwin", "num": result_item["num"],
                         "chance": chance, "amt": game.to_units(result_item["p"], result_item["k"])})
    else:
        push_history(p, {"k": "uplose", "num": num["num"], "chance": chance})

    _check_achievements(p)
    db.touch_player(uid, xp=p["xp"], stats=p["stats"], history=p["history"],
                    achievements=p["achievements"], balance=p["balance"])
    return web.json_response({
        "ok": True,
        "win": win,
        "item": result_item,
        "old": num,
        "balance": p["balance"],
        "wallets": db.wallets_get(uid),
        "donate_balance": p.get("donate_balance", 0),
        "chance": chance,
        "steps": steps,
        "wheel": {"chance": chance, "win": win, "landing": landing},
    })


# ---------------------------------------------------------------- продажа / массовые
async def api_sell(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    number_id = str(payload.get("number_id") or "")
    num = db.get_number(number_id)
    if not num or num["player_id"] != uid:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    rates, _fx_snapshot = await _wallet_rates(uid)
    p["balance"] = float(db.get_player(uid)["balance"])
    balance_before = p["balance"]
    currency = fx.CURRENCY_BY_COUNTRY.get(num["k"], "RUB")
    native_amount = round(float(num["p"]), 2)
    amt = _wallet_value_units(native_amount, currency, rates)
    db.remove_number(number_id)
    p["balance"] = round(p["balance"] + amt, 2)
    p["stats"]["sold"] = p["stats"].get("sold", 0) + 1
    _record_task_activity(uid, "sold", 1)
    add_xp(p, 2)
    push_history(p, {"k": "bank", "num": num["num"], "amt": amt})
    _check_achievements(p)
    deltas = _wallet_settlement(balance_before, p["balance"],
                                [{"currency": currency, "amount": native_amount}], rates)
    db.touch_player(uid, balance=p["balance"], wallet_deltas=deltas, xp=p["xp"],
                    stats=p["stats"], history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "earned": amt, "balance": p["balance"],
                              "wallets": db.wallets_get(uid), "currency": currency,
                              "earned_native": native_amount})


async def api_mass_sell(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    mode = str(payload.get("mode") or "")
    inv = db.player_numbers(uid)
    if not inv:
        return web.json_response({"ok": False, "error": "empty"}, status=400)

    ids = []
    if mode == "common":
        ids = [n["id"] for n in inv if n["r"] in ("common", "unc")]
    elif mode == "worst":
        top = {n["id"] for n in sorted(inv, key=lambda x: -x["p"])[:5]}
        ids = [n["id"] for n in inv if n["id"] not in top]
    elif mode == "all":
        ids = [n["id"] for n in inv]
    if not ids:
        return web.json_response({"ok": False, "error": "nothing"}, status=400)

    rates, _fx_snapshot = await _wallet_rates(uid)
    p["balance"] = float(db.get_player(uid)["balance"])
    balance_before = p["balance"]
    total = 0.0
    native_totals: dict[str, float] = {}
    for nid in ids:
        n = db.get_number(nid)
        if not n:
            continue
        currency = fx.CURRENCY_BY_COUNTRY.get(n["k"], "RUB")
        native = round(float(n["p"]), 2)
        native_totals[currency] = native_totals.get(currency, 0.0) + native
        total += _wallet_value_units(native, currency, rates)
        db.remove_number(nid)
    total = round(total, 2)
    p["balance"] = round(p["balance"] + total, 2)
    p["stats"]["sold"] = p["stats"].get("sold", 0) + len(ids)
    _record_task_activity(uid, "sold", len(ids))
    xp_ev = add_xp(p, min(20, len(ids) * 2))
    push_history(p, {"k": "mass_sell", "n": len(ids), "amt": total, "label": mode})
    _check_achievements(p)
    native_deltas = [{"currency": code, "amount": amount} for code, amount in native_totals.items()]
    deltas = _wallet_settlement(balance_before, p["balance"], native_deltas, rates)
    db.touch_player(uid, balance=p["balance"], wallet_deltas=deltas, xp=p["xp"],
                    stats=p["stats"], history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "count": len(ids), "earned": total,
                              "earned_rub": round(total * 100, 2),
                              "balance": p["balance"], "wallets": db.wallets_get(uid),
                              "xp_events": xp_ev["events"]})


# ---------------------------------------------------------------- рынок (мультиплеер)
async def api_market(request: web.Request) -> web.Response:
    listings = db.market_listings(120)
    sellers = {l["seller"] for l in listings}
    names = {}
    for sid in sellers:
        row = db.get_player(sid)
        if row:
            pl = db.player_dict(row)
            names[str(sid)] = pl.get("first_name") or pl.get("username") or "Игрок"
    return web.json_response({"ok": True, "listings": listings, "names": names,
                              "channel": CHANNEL})


async def api_market_list(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    number_id = str(payload.get("number_id") or "")
    num = db.get_number(number_id)
    if not num or num["player_id"] != uid or num["status"] != "inv":
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    try:
        price = float(payload.get("price") or 0)
    except Exception:
        price = 0
    if price <= 0:
        return web.json_response({"ok": False, "error": "bad_price"}, status=400)
    kind = str(payload.get("kind") or "sale")
    if kind not in ("sale", "swap"):
        kind = "sale"
    db.market_add(game.new_id(), uid, number_id, round(price, 2), kind)
    p["stats"]["listed"] = p["stats"].get("listed", 0) + 1
    _record_task_activity(uid, "listed", 1)
    add_xp(p, 3)
    push_history(p, {"k": "list", "num": num["num"]})
    _check_achievements(p)
    db.touch_player(uid, xp=p["xp"], stats=p["stats"], history=p["history"],
                    achievements=p["achievements"])
    return web.json_response({"ok": True})


async def api_market_unlist(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    listing_id = str(payload.get("listing_id") or "")
    lot = db.market_get(listing_id)
    if not lot or lot["seller_id"] != uid:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    db.market_remove(listing_id)
    push_history(p, {"k": "unlist"})
    db.touch_player(uid, history=p["history"])
    return web.json_response({"ok": True})


async def api_market_buy(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    listing_id = str(payload.get("listing_id") or "")
    lot = db.market_get(listing_id)
    if not lot:
        return web.json_response({"ok": False, "error": "gone"}, status=404)
    if lot["seller_id"] == uid:
        return web.json_response({"ok": False, "error": "own"}, status=400)
    num = db.get_number(lot["number_id"])
    if not num:
        return web.json_response({"ok": False, "error": "gone"}, status=404)
    rates, _fx_snapshot = await _wallet_rates(uid)
    p["balance"] = float(db.get_player(uid)["balance"])
    balance_before = p["balance"]
    currency = fx.CURRENCY_BY_COUNTRY.get(num["k"], "RUB")
    cost_native = round(float(lot["price"]), 2)
    cost_units = _wallet_value_units(cost_native, currency, rates)
    donor_native = min(cost_native, p.get("donate_balance", 0) * 100 * rates[currency])
    debit_native = round(cost_native - donor_native, 2)
    if db.wallet_get(uid, currency) + 0.005 < debit_native:
        return web.json_response({"ok": False, "error": "no_funds", "currency": currency,
                                  "need": debit_native, "wallet": db.wallet_get(uid, currency)}, status=402)
    donor_used = donor_native / (100 * rates[currency]) if donor_native else 0
    p["donate_balance"] = round(max(0, p.get("donate_balance", 0) - donor_used), 2)
    p["balance"] = round(p["balance"] - _wallet_value_units(debit_native, currency, rates), 2)

    got = db.market_buy(listing_id, uid)
    if not got:
        return web.json_response({"ok": False, "error": "gone"}, status=409)
    seller_id = got["seller_id"]
    seller_amount = round(lot["price"] * (1 - game.FEE), 2)
    db.inbox_add(seller_id, "money", seller_amount, num["k"],
                 text=f"Продажа {num['num']}")
    p["stats"]["bought"] = p["stats"].get("bought", 0) + 1
    _record_task_activity(uid, "bought", 1)
    add_xp(p, 5)
    push_history(p, {"k": "buy", "num": num["num"], "amt": cost_units})
    _check_achievements(p)
    native_deltas = ([{"currency": currency, "amount": -debit_native}] if debit_native else [])
    deltas = _wallet_settlement(balance_before, p["balance"], native_deltas, rates)
    db.touch_player(uid, balance=p["balance"], wallet_deltas=deltas,
                    donate_balance=p["donate_balance"], xp=p["xp"], stats=p["stats"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "paid": _wallet_value_units(cost_native, currency, rates),
                              "paid_native": cost_native, "currency": currency,
                              "balance": p["balance"], "wallets": db.wallets_get(uid)})


async def api_market_accept_swap(request: web.Request) -> web.Response:
    """Принять обмен: отдать свой номер и забрать чужой."""
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    listing_id = str(payload.get("listing_id") or "")
    my_number_id = str(payload.get("number_id") or "")
    lot = db.market_get(listing_id)
    if not lot or lot["kind"] != "swap":
        return web.json_response({"ok": False, "error": "gone"}, status=404)
    if lot["seller_id"] == uid:
        return web.json_response({"ok": False, "error": "own"}, status=400)
    mine = db.get_number(my_number_id)
    if not mine or mine["player_id"] != uid or mine["status"] != "inv":
        return web.json_response({"ok": False, "error": "not_found"}, status=404)

    seller = lot["seller_id"]
    db.market_buy(listing_id, uid)          # чужой номер → мне
    db.transfer_number(my_number_id, seller)  # мой номер → ему

    push_history(p, {"k": "swap", "num": mine["num"]})
    _check_achievements(p)
    db.touch_player(uid, history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "got": lot["number_id"], "gave": my_number_id})


async def api_inbox(request: web.Request) -> web.Response:
    payload = {**await read_json(request), **dict(request.query)}
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    rates, _fx_snapshot = await _wallet_rates(uid)
    p["balance"] = float(db.get_player(uid)["balance"])
    balance_before = p["balance"]
    items = db.inbox_take(uid)
    earned = 0.0
    got_items = []
    native_totals: dict[str, float] = {}
    for it in items:
        if it["kind"] == "money":
            amt = round(float(it["amount"]), 2)
            stored_currency = str(it["currency"] or "")
            currency = fx.CURRENCY_BY_COUNTRY.get(stored_currency, stored_currency)
            if currency not in fx.CURRENCY_CODES:
                currency = "RUB"
            native_totals[currency] = native_totals.get(currency, 0.0) + amt
            units = _wallet_value_units(amt, currency, rates)
            p["balance"] = round(p["balance"] + units, 2)
            earned += units
            push_history(p, {"k": "inbox", "amt": units})
        elif it["kind"] == "item" and it.get("number_id"):
            num = db.get_number(it["number_id"])
            if num:
                db.add_number(uid, {**num, "id": game.new_id(), "player_id": uid, "status": "inv"})
                got_items.append(num["num"])
                push_history(p, {"k": "got_item", "num": num["num"]})
    if items:
        native_deltas = [{"currency": code, "amount": amount} for code, amount in native_totals.items()]
        deltas = _wallet_settlement(balance_before, p["balance"], native_deltas, rates)
        db.touch_player(uid, balance=p["balance"], wallet_deltas=deltas, history=p["history"])
    return web.json_response({"ok": True, "count": len(items), "earned": earned,
                              "items": got_items, "balance": p["balance"],
                              "wallets": db.wallets_get(uid)})


# ---------------------------------------------------------------- задания / достижения
TASKS = [
    {"id": "s5", "t": "Сделать 5 спинов", "metric": "spins", "n": 5, "u": 30, "cadence": "daily"},
    {"id": "rare", "t": "Найти «Редкий» или круче", "metric": "rare", "n": 1, "u": 50, "cadence": "daily"},
    {"id": "ctry", "t": "Сделать 12 спинов", "metric": "spins", "n": 12, "u": 80, "cadence": "48h"},
    {"id": "sold", "t": "Продать 3 номера", "metric": "sold", "n": 3, "u": 40, "cadence": "48h"},
    {"id": "upg", "t": "Сделать апгрейд", "metric": "upg", "n": 1, "u": 60, "cadence": "48h"},
    {"id": "s25", "t": "Сделать 25 спинов", "metric": "spins", "n": 25, "u": 150, "cadence": "weekly"},
    {"id": "lst", "t": "Выставить номер на рынок", "metric": "listed", "n": 1, "u": 30, "cadence": "weekly"},
    {"id": "buy", "t": "Купить номер на рынке", "metric": "bought", "n": 1, "u": 30, "cadence": "weekly"},
]
TASK_CADENCE_MS = {"daily": 24 * 60 * 60 * 1000,
                   "48h": 48 * 60 * 60 * 1000,
                   "weekly": 7 * 24 * 60 * 60 * 1000}
TASK_CADENCE_LABEL = {"daily": "Каждые 24 часа", "48h": "Каждые 48 часов",
                      "weekly": "Каждую неделю"}


def _task_period(task: dict, now: int | None = None) -> tuple[int, int]:
    now = now or now_ms()
    cadence = task["cadence"]
    if cadence == "weekly":
        current = dt.datetime.fromtimestamp(now / 1000, tz=dt.timezone.utc)
        monday = current.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=current.weekday())
        start = int(monday.timestamp() * 1000)
    else:
        period = TASK_CADENCE_MS[cadence]
        start = (now // period) * period
    return start, start + TASK_CADENCE_MS[cadence]


def _record_task_activity(user_id: int, metric: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    now = now_ms()
    for task in TASKS:
        if task["metric"] == metric:
            start, _ = _task_period(task, now)
            db.task_progress_add(user_id, task["id"], start, amount)


ACHS = [
    {"id": "first",   "i": "🎯", "t": "Первый спин",           "c": lambda p, inv: p["stats"].get("spins", 0) >= 1},
    {"id": "x10",     "i": "🔟", "t": "10 спинов",             "c": lambda p, inv: p["stats"].get("spins", 0) >= 10},
    {"id": "x100",    "i": "💯", "t": "100 спинов",            "c": lambda p, inv: p["stats"].get("spins", 0) >= 100},
    {"id": "rare1",   "i": "🔷", "t": "Первый редкий",         "c": lambda p, inv: p["stats"].get("rare", 0) >= 1},
    {"id": "leg",     "i": "👑", "t": "Легендарный номер",     "c": lambda p, inv: any(game.R_INDEX.get(n["r"], 0) >= 5 for n in inv)},
    {"id": "sec",     "i": "🌌", "t": "Секретный номер",       "c": lambda p, inv: any(n["r"] == "sec" for n in inv)},
    {"id": "ops",     "i": "📡", "t": "3 разных оператора",     "c": lambda p, inv: len({n["op"] for n in inv}) >= 3},
    {"id": "rich",    "i": "💰", "t": "Богач",                    "c": lambda p, inv: p["balance"] >= 2500},
    {"id": "lvl5",    "i": "⭐", "t": "Уровень 5",             "c": lambda p, inv: db.level_of(p["xp"]) >= 5},
    {"id": "streak7", "i": "🔥", "t": "Стрик 7 дней",          "c": lambda p, inv: p.get("streak", 0) >= 7},
    {"id": "trader",  "i": "🤝", "t": "10 сделок на рынке",    "c": lambda p, inv: p["stats"].get("sold", 0) + p["stats"].get("bought", 0) >= 10},
]


def _check_achievements(p: dict) -> None:
    inv = db.player_numbers(p["user_id"])
    p.setdefault("achievements", [])
    for a in ACHS:
        if a["id"] in p["achievements"]:
            continue
        try:
            ok = a["c"](p, inv)
        except Exception:
            ok = False
        if ok:
            p["achievements"].append(a["id"])
            reward = 20 + len(p["achievements"]) * 5
            p["balance"] = round(p["balance"] + reward, 2)
            push_history(p, {"k": "ach", "amt": reward, "name": a["t"]})


async def api_tasks(request: web.Request) -> web.Response:
    payload = {**await read_json(request), **dict(request.query)}
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    now = now_ms()
    out = []
    for task in TASKS:
        start, reset_at = _task_period(task, now)
        state = db.task_progress_get(uid, task["id"], start)
        out.append({**task, "v": min(state["progress"], task["n"]),
                    "claimed": state["claimed"], "period_start": start,
                    "reset_at": reset_at, "resets_in": max(0, (reset_at-now)//1000),
                    "cadence_label": TASK_CADENCE_LABEL[task["cadence"]]})
    return web.json_response({"ok": True, "tasks": out, "achievements": p.get("achievements", []),
                              "all_achievements": [{"id": a["id"], "i": a["i"], "t": a["t"]} for a in ACHS]})


async def api_task_claim(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    tid = str(payload.get("task_id") or "")
    task = next((t for t in TASKS if t["id"] == tid), None)
    if not task:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    period_start, _ = _task_period(task)
    if not db.task_progress_claim(uid, tid, period_start, task["n"]):
        return web.json_response({"ok": False, "error": "locked"}, status=400)
    p["balance"] = round(p["balance"] + task["u"], 2)
    add_xp(p, 10)
    push_history(p, {"k": "task", "amt": task["u"], "name": task["t"]})
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], xp=p["xp"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "reward": task["u"], "balance": p["balance"]})


# ---------------------------------------------------------------- купоны
async def api_coupon(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    if (resp := maintenance_guard(p)) is not None:
        return resp
    code = str(payload.get("code") or "").strip()
    coupon = db.coupon_get(code)
    if not coupon:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    ok, msg = db.coupon_activate(coupon, uid)
    if not ok:
        return web.json_response({"ok": False, "error": msg}, status=400)

    reward_txt = ""
    if coupon["kind"] == "money":
        amt = float(coupon["value"])
        p["balance"] = round(p["balance"] + amt, 2)
        reward_txt = f"+{amt:g}"
        push_history(p, {"k": "bonus", "amt": amt, "name": f"Купон {code}"})
    elif coupon["kind"] == "luck":
        mult = int(coupon["value"])
        p["luck_mult"] = mult
        p["luck_until"] = now_ms() + 60 * 60 * 1000
        reward_txt = f"удача ×{mult} на 60 мин"
        push_history(p, {"k": "bonus", "amt": 0, "name": f"Купон удачи ×{mult}"})
    elif coupon["kind"] == "donate":
        amt = float(coupon["value"])
        p["donate_balance"] = round(p.get("donate_balance", 0) + amt, 2)
        reward_txt = f"+{amt:g} доната"
        push_history(p, {"k": "bonus", "amt": amt, "name": f"Купон {code}"})
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], luck_mult=p["luck_mult"],
                    luck_until=p["luck_until"], donate_balance=p["donate_balance"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "reward": reward_txt,
                              "reward_kind": coupon["kind"],
                              "reward_amt": float(coupon["value"] or 0),
                              "balance": p["balance"],
                              "donate_balance": p["donate_balance"],
                              "luck_until": p["luck_until"], "luck_mult": p["luck_mult"]})


# ---------------------------------------------------------------- настройки
SETTINGS_KEYS = ["sound_on", "vibration", "effects", "theme", "spin_speed", "spin_anim",
                 "spin_theme", "up_speed", "up_anim", "up_theme", "use_coins", "autosell"]


async def api_settings(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    upd = {}
    for k in SETTINGS_KEYS:
        if k in payload:
            upd[k] = payload[k]
    # алиасы из старого формата
    alias = {"sSpd": "spin_speed", "sThm": "spin_theme", "sAnim": "spin_anim",
             "uSpd": "up_speed", "uThm": "up_theme", "uAnim": "up_anim",
             "soundOn": "sound_on", "useCoins": "use_coins"}
    for a, k in alias.items():
        if a in payload:
            upd[k] = payload[a]
    if upd:
        db.touch_player(uid, **upd)
    return web.json_response({"ok": True, "settings": {k: upd.get(k, p[k]) for k in SETTINGS_KEYS}})


# ---------------------------------------------------------------- рейтинг
async def api_rating(request: web.Request) -> web.Response:
    # Администраторы не участвуют ни в одном рейтинге, включая рейтинг номеров.
    admin_ids = set(ADMIN_IDS) | set(db.admin_list())
    players = [pl for pl in db.list_players()
               if not pl.get("is_admin") and pl["user_id"] not in admin_ids]
    by_money = sorted(players, key=lambda x: -(x["balance"] or 0))[:15]
    top_numbers = []
    for pl in players:
        nums = db.player_numbers(pl["user_id"])
        if not nums:
            continue
        best = max(nums, key=lambda n: (n["p"] or 0) / max(1, game.C_BY_ID.get(n["k"], {"b": 1})["b"]))
        top_numbers.append({"name": pl.get("first_name") or pl.get("username") or "Игрок",
                            "user_id": pl["user_id"], "item": best})
    top_numbers.sort(key=lambda x: -(x["item"]["p"] or 0) / max(1, game.C_BY_ID.get(x["item"]["k"], {"b": 1})["b"]))
    return web.json_response({
        "ok": True,
        "money": [{"name": pl.get("first_name") or pl.get("username") or "Игрок",
                   "user_id": pl["user_id"], "balance": round(pl["balance"] or 0)}
                  for pl in by_money],
        "numbers": top_numbers[:15],
        "channel": CHANNEL,
    })


# ---------------------------------------------------------------- инбокс-дренаж
async def api_friends(request: web.Request) -> web.Response:
    payload = {**await read_json(request), **dict(request.query)}
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    return web.json_response({"ok": True, "ref_count": p.get("ref_count", 0),
                              "friends": p.get("friends", []),
                              "ref_link": f"https://t.me/?startapp=ref_{uid}"})


# ---------------------------------------------------------------- техперерыв (персональный)
async def api_maintenance(request: web.Request) -> web.Response:
    """Состояние перерыва для того, кто спрашивает.

    Если авторизоваться не удалось — считаем обычным игроком ( block=started ).
    Админ получает blocked=false и продолжает играть.
    """
    payload = {**dict(request.query)}
    try:
        payload.update(await read_json(request) or {})
    except Exception:
        pass
    uid, is_admin = viewer_of(request, payload)
    return web.json_response({"ok": True, **maintenance_state(uid, is_admin)})


# ================================================================ админка
async def _admin(request: web.Request, payload: dict) -> dict | None:
    auth = _load_profile(request, payload)
    if not auth:
        return None
    uid, p = auth
    if not viewer_is_admin(uid):
        return None
    p["is_admin"] = True
    return p


async def api_admin_maintenance(request: web.Request) -> web.Response:
    """Управление техперерывом: status / start / stop.

    start принимает:
      delay_sec   — закрыть через N секунд (0 = сразу),
      at          — 'HH:MM' по МСК (имеет приоритет над delay_sec),
      duration_min— открыть автоматически через N минут после закрытия,
      open_at     — 'HH:MM' по МСК, когда открыть,
      end_at      — epoch ms (прямое указание),
      message     — текст для игроков.
    """
    payload = await read_json(request)
    p = await _admin(request, payload)
    if not p:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    action = str(payload.get("action") or "")
    now = now_ms()
    uid = p["user_id"]

    if action == "status":
        return web.json_response({"ok": True, **maintenance_state(uid, True)})

    if action == "stop":
        prev = db.maintenance_get()
        db.maintenance_set(False, 0, 0, "", uid)
        db.maintenance_log_add("stop", prev["message"], prev["start_at"], prev["end_at"], uid)
        asyncio.create_task(notify_maintenance("end", prev["message"], prev["start_at"], prev["end_at"]))
        log.info("Техперерыв завершён админом %s", uid)
        return web.json_response({"ok": True, **maintenance_state(uid, True)})

    # ---- старт ----
    try:
        delay = int(payload.get("delay_sec") or 0)
    except Exception:
        delay = 0
    at_ms = _msk_hhmm_ms(payload["at"]) if payload.get("at") else (now + delay * 1000 if delay > 0 else 0)

    try:
        end_at = int(payload.get("end_at") or 0)
    except Exception:
        end_at = 0
    if not end_at and payload.get("open_at"):
        end_at = _msk_hhmm_ms(payload["open_at"])
    if not end_at:
        try:
            dur_min = int(payload.get("duration_min") or 0)
        except Exception:
            dur_min = 0
        if dur_min > 0:
            end_at = (at_ms or now) + dur_min * 60 * 1000
    if end_at and end_at <= (at_ms or now):
        return web.json_response({"ok": False, "error": "bad_end_at"}, status=400)

    message = str(payload.get("message") or "Плановые технические работы")
    db.maintenance_set(True, at_ms, end_at, message, uid)
    db.maintenance_log_add("start", message, at_ms, end_at, uid)
    push_history(p, {"k": "maintenance", "name": message})
    db.touch_player(uid, history=p["history"])
    asyncio.create_task(notify_maintenance(
        "scheduled" if at_ms > now else "start", message, at_ms, end_at
    ))
    log.info("Техперерыв назначен: start=%s end=%s через %s сек", at_ms, end_at, delay)
    return web.json_response({"ok": True, **maintenance_state(uid, True)})


async def api_admin_coupons(request: web.Request) -> web.Response:
    payload = await read_json(request)
    p = await _admin(request, payload)
    if not p:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    action = str(payload.get("action") or "list")

    if action == "create":
        import random as _r
        import string as _s
        kind = str(payload.get("kind") or "money")
        try:
            value = float(payload.get("value") or 0)
        except Exception:
            value = 0
        if kind not in ("money", "luck", "donate") or value <= 0:
            return web.json_response({"ok": False, "error": "bad_data"}, status=400)
        code = str(payload.get("code") or "").strip().upper()
        if not code:
            code = "".join(_r.choice(_s.ascii_uppercase + _s.digits) for _ in range(8))
        expires_at = int(payload.get("expires_at") or 0)
        max_act = int(payload.get("max_activations") or 0)
        try:
            c = db.coupon_create(code, kind, value, str(payload.get("label") or ""),
                                 expires_at, max_act, p["user_id"])
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
        return web.json_response({"ok": True, "coupon": c})

    if action == "toggle":
        db.coupon_set_active(int(payload.get("coupon_id") or 0), bool(payload.get("active", True)))
        return web.json_response({"ok": True})

    return web.json_response({"ok": True, "coupons": db.coupon_list()})


async def api_admin_stats(request: web.Request) -> web.Response:
    payload = {**await read_json(request), **dict(request.query)}
    p = await _admin(request, payload)
    if not p:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    players = db.list_players()
    total_numbers = len(db.connect().execute("SELECT id FROM numbers").fetchall())
    total_balance = sum(pl["balance"] or 0 for pl in players)
    top = sorted(players, key=lambda x: -(x["balance"] or 0))[:10]
    return web.json_response({
        "ok": True,
        "players": len(players),
        "numbers": total_numbers,
        "market": len(db.market_listings(1000)),
        "coupons": len(db.coupon_list()),
        "total_balance": round(total_balance, 2),
        "top": [{"name": pl.get("first_name") or pl.get("username") or "?",
                 "balance": round(pl["balance"] or 0), "xp": pl["xp"]} for pl in top],
        "admins": db.admin_list(),
        "maintenance": maintenance_state(p["user_id"], True),
        "maintenance_log": db.maintenance_log_list(8),
        "admin_grants": db.admin_grant_list(12),
    })


async def api_admin_grant(request: web.Request) -> web.Response:
    """Выдать баланс существующему игроку по его Telegram username.

    В базе баланс хранится в общих единицах, но сумма от админа вводится
    в валюте указанной страны и пересчитывается по внутреннему курсу игры.
    """
    payload = await read_json(request)
    admin = await _admin(request, payload)
    if not admin:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    limited = _rate_limited(f"grant:{admin['user_id']}", 1, 10)
    if limited:
        return web.json_response({"ok": False, "error": "rate_limited", "retry_in": limited}, status=429)

    username = str(payload.get("username") or "").strip().lstrip("@").lower()
    if not (5 <= len(username) <= 32) or not all(ch.isalnum() or ch == "_" for ch in username):
        return web.json_response({"ok": False, "error": "bad_username"}, status=400)

    country_id = str(payload.get("country") or "")
    country = game.C_BY_ID.get(country_id)
    if not country:
        return web.json_response({"ok": False, "error": "bad_country"}, status=400)
    try:
        amount = round(float(payload.get("amount") or 0), 2)
    except (TypeError, ValueError):
        amount = 0
    if not (0 < amount <= 1_000_000_000):
        return web.json_response({"ok": False, "error": "bad_amount"}, status=400)

    target = db.find_player_by_username(username)
    if not target:
        return web.json_response({"ok": False, "error": "player_not_found"}, status=404)
    ensure_player(target["user_id"])
    rates, _fx_snapshot = await _wallet_rates(target["user_id"])
    currency_code = fx.CURRENCY_BY_COUNTRY[country_id]
    units = _wallet_value_units(amount, currency_code, rates)
    if units <= 0:
        return web.json_response({"ok": False, "error": "amount_too_small"}, status=400)
    try:
        balance = db.admin_grant_balance(
            admin["user_id"], target["user_id"], currency_code, amount, units
        )
    except ValueError:
        return web.json_response({"ok": False, "error": "player_not_found"}, status=404)

    return web.json_response({
        "ok": True,
        "username": target.get("username") or username,
        "user_id": target["user_id"],
        "country": country_id,
        "currency": country["cur"],
        "amount": amount,
        "amount_units": units,
        "balance": balance,
    })


async def api_admin_admins(request: web.Request) -> web.Response:
    payload = await read_json(request)
    p = await _admin(request, payload)
    if not p:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    action = str(payload.get("action") or "list")
    try:
        target = int(payload.get("user_id") or 0)
    except Exception:
        target = 0
    if action == "add" and target:
        db.admin_add(target, p["user_id"])
    elif action == "remove" and target:
        db.admin_remove(target)
    return web.json_response({"ok": True, "admins": db.admin_list()})


# ================================================================ Telegram Stars
async def api_skus(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "skus": SKUS})


async def api_stars_order(request: web.Request) -> web.Response:
    """Создаёт инвойс через Bot API и возвращает URL для tg.openInvoice()."""
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    sku_id = str(payload.get("sku") or "")
    sku = SKU_BY_ID.get(sku_id)
    if not sku:
        return web.json_response({"ok": False, "error": "bad_sku"}, status=400)

    if (resp := maintenance_guard(p)) is not None:
        return resp

    secret = f"simroul:{uid}:{sku_id}:{now_ms()}"
    body = {
        "chat_id": uid,
        "title": f"SIM Roulette — {sku['title']}",
        "description": sku["desc"],
        "payload": secret,
        "provider_token": "",
        "currency": "XTR",
        "prices": [{"label": sku["title"], "amount": sku["stars"]}],
        "start_parameter": f"buy_{sku_id}",
        "need_name": False, "need_phone_number": False,
        "need_email": False, "need_shipping_address": False,
        "is_flexible": False,
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendInvoice"
    try:
        from aiohttp import ClientSession
        async with ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp_timeout()) as r:
                data = await r.json()
    except Exception as e:
        log.warning("sendInvoice error: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=502)
    if not data.get("ok"):
        return web.json_response({"ok": False, "error": data.get("description", "invoice_failed")},
                                 status=502)
    invoice_url = data["result"].get("invoice_url")
    return web.json_response({"ok": True, "invoice_url": invoice_url, "sku": sku_id})


def aiohttp_timeout():
    from aiohttp import ClientTimeout
    return ClientTimeout(total=15)


async def apply_purchase(user_id: int, sku_id: str, payload: str) -> tuple[bool, str]:
    """Начисление товара после успешной оплаты Stars (вызывается из бота)."""
    sku = SKU_BY_ID.get(sku_id)
    if not sku:
        return False, "unknown sku"
    row = db.get_player(user_id)
    if not row:
        ensure_player(user_id)
        row = db.get_player(user_id)
    p = db.player_dict(row)
    db.purchase_add(user_id, sku_id, sku["stars"], payload)

    if sku_id == "luck2":
        p["luck_mult"], p["luck_until"] = 2, now_ms() + 30 * 60 * 1000
    elif sku_id == "luck4":
        p["luck_mult"], p["luck_until"] = 4, now_ms() + 30 * 60 * 1000
    elif sku_id == "luck10":
        p["luck_mult"], p["luck_until"] = 10, now_ms() + 15 * 60 * 1000
    elif sku_id == "coins":
        p["coins"] = p.get("coins", 0) + 10
    elif sku_id == "free":
        p["free_at"] = 0
    elif sku_id == "donate":
        p["donate_balance"] = round(p.get("donate_balance", 0) + 100, 2)
    else:
        return False, "unknown sku"

    push_history(p, {"k": "stars", "name": sku["title"], "amt": sku["stars"]})
    db.touch_player(user_id, luck_mult=p["luck_mult"], luck_until=p["luck_until"],
                    coins=p["coins"], free_at=p["free_at"], donate_balance=p["donate_balance"],
                    history=p["history"])
    return True, sku["title"]


# ================================================================ статика
async def index(request: web.Request) -> web.Response:
    path = Path(WEBAPP_DIR) / "index.html"
    if not path.exists():
        return web.Response(text="WebApp не найден", status=404)
    return web.FileResponse(path)


async def start_server() -> web.AppRunner:
    db.connect()
    app = web.Application(client_max_size=2 * 1024 * 1024,
                          middlewares=[request_error_middleware, subscription_middleware])
    if APP_ENV == "production":
        app.on_startup.append(_backup_start)
        app.on_cleanup.append(_backup_stop)

    # базовые
    app.router.add_get("/api/health", api_health)
    app.router.add_post("/api/auth", api_auth)
    app.router.add_post("/api/subscription/check", api_subscription_check)
    app.router.add_route("*", "/api/state", api_state)
    app.router.add_post("/api/import", api_import)

    # игра
    app.router.add_post("/api/spin", api_spin)
    app.router.add_post("/api/decide", api_decide)
    app.router.add_post("/api/bonus", api_bonus)
    app.router.add_post("/api/upgrade", api_upgrade)
    app.router.add_post("/api/sell", api_sell)
    app.router.add_post("/api/mass_sell", api_mass_sell)

    # рынок
    app.router.add_get("/api/market", api_market)
    app.router.add_post("/api/market/list", api_market_list)
    app.router.add_post("/api/market/unlist", api_market_unlist)
    app.router.add_post("/api/market/buy", api_market_buy)
    app.router.add_post("/api/market/accept_swap", api_market_accept_swap)
    app.router.add_post("/api/inbox", api_inbox)

    # валюты и прогресс
    app.router.add_get("/api/currencies", api_currencies)
    app.router.add_post("/api/currencies/convert", api_currency_convert)
    app.router.add_route("*", "/api/tasks", api_tasks)
    app.router.add_post("/api/tasks/claim", api_task_claim)
    app.router.add_get("/api/rating", api_rating)
    app.router.add_route("*", "/api/friends", api_friends)
    app.router.add_post("/api/coupon", api_coupon)
    app.router.add_post("/api/settings", api_settings)

    # техперерыв
    app.router.add_get("/api/maintenance", api_maintenance)

    # админка
    app.router.add_route("*", "/api/admin/maintenance", api_admin_maintenance)
    app.router.add_route("*", "/api/admin/coupons", api_admin_coupons)
    app.router.add_route("*", "/api/admin/stats", api_admin_stats)
    app.router.add_route("*", "/api/admin/admins", api_admin_admins)
    app.router.add_route("*", "/api/admin/grant", api_admin_grant)

    # Stars
    app.router.add_get("/api/skus", api_skus)
    app.router.add_post("/api/stars/order", api_stars_order)

    # статика
    app.router.add_get("/", index)
    app.router.add_static("/static", WEBAPP_DIR, show_index=False)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP-сервер запущен на порту %s", PORT)
    return runner
