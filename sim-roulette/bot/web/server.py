"""
HTTP API + раздача WebApp.

Аутентификация — по initData Telegram (HMAC). В DEV-режиме (DEV_MODE=1)
принимается payload с прямым uid, чтобы игру можно было тестировать
вне Telegram.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

import bot.db as db
import bot.game as game
from bot.config import ADMIN_IDS, BOT_TOKEN, CHANNEL, DEV_MODE, PORT, WEBAPP_DIR, WEBAPP_URL
from bot.utils.security import verify_init_data

log = logging.getLogger("simroulette.web")

CH_URL = f"https://t.me/{CHANNEL.lstrip('@')}"

# uid -> (item, timestamp) — номер, ожидающий решения «Оставить/Продать»
_pending: dict[int, dict] = {}
PENDING_TTL = 10 * 60 * 1000

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


def maintenance_state() -> dict:
    m = db.maintenance_get()
    now = now_ms()
    started = bool(m["active"]) and (not m["start_at"] or now >= m["start_at"])
    return {
        "active": bool(m["active"]),
        "start_at": m["start_at"],
        "end_at": m["end_at"],
        "message": m["message"],
        "started": started,
        "channel": CHANNEL,
        "channel_url": CH_URL,
        "seconds_left": max(0, (m["start_at"] - now) // 1000) if (m["active"] and not started) else 0,
    }


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


def state_payload(p: dict) -> dict:
    inv = db.player_numbers(p["user_id"])
    lvl = db.level_of(p["xp"])
    cur_xp, next_xp = db.xp_bounds(lvl)
    return {
        "player": p,
        "inventory": inv,
        "level": lvl,
        "level_xp": cur_xp,
        "next_level_xp": next_xp,
        "maintenance": maintenance_state(),
        "channel": CHANNEL,
        "channel_url": CH_URL,
        "now": now_ms(),
    }


# ================================================================ API
async def api_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def api_auth(request: web.Request) -> web.Response:
    payload = await read_json(request)
    uid = auth_uid(request, payload)
    if uid is None:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    profile = payload.get("profile") or {}
    p = ensure_player(uid, profile)
    return web.json_response({"ok": True, "player": p, "is_admin": p.get("is_admin", False)})


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
    data = payload.get("data") or {}

    if db.player_numbers(uid):
        return web.json_response({"ok": True, "imported": 0, "reason": "already"})

    imported = 0
    if isinstance(data.get("bal"), (int, float)) and data["bal"] > p["balance"]:
        p["balance"] = float(data["bal"])
    for key, out in (("coins", "coins"), ("xp", "xp"), ("streak", "streak"),
                     ("ref", "ref_count"), ("refBy", "ref_by")):
        if key in data and data[key] is not None:
            try:
                p[out] = int(data[key])
            except Exception:
                pass
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

    for it in (data.get("inv") or [])[:500]:
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
                    achievements=p["achievements"], stats=p["stats"], history=p["history"])
    return web.json_response({"ok": True, "imported": imported})


# ---------------------------------------------------------------- спин
async def api_spin(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth

    ms = maintenance_state()
    if ms["started"]:
        return web.json_response({"ok": False, "error": "maintenance"}, status=503)

    country = str(payload.get("country") or "RU")
    category = payload.get("category") or None
    if category and category not in game.R_BY_ID:
        category = None
    try:
        count = max(1, min(20, int(payload.get("count") or 1)))
    except Exception:
        count = 1
    use_coins = bool(payload.get("useCoins"))
    free = bool(payload.get("free"))

    if now_ms() < (p.get("instant_block_until") or 0) and str(payload.get("speed")) == "instant":
        return web.json_response({"ok": False, "error": "instant_ban"}, status=429)

    cost = game.spin_cost(category, count, free)
    if not free and p["balance"] + p.get("donate_balance", 0) < cost:
        return web.json_response({"ok": False, "error": "no_funds"}, status=402)

    # списываем: сначала донат-баланс, потом обычный
    if cost:
        from_donate = min(cost, p.get("donate_balance", 0))
        p["donate_balance"] = round(p.get("donate_balance", 0) - from_donate, 2)
        rest = cost - from_donate
        p["balance"] = round(p["balance"] - rest, 2)

    # удача: купон (luck_until) + коин за каждый спин
    boost = 0.0
    if p.get("luck_until", 0) > now_ms():
        boost += max(1, int(p.get("luck_mult", 1))) - 1
    coins_used = 0
    if use_coins and p.get("coins", 0) > 0:
        take = min(p["coins"], count)
        p["coins"] -= take
        coins_used = take
        boost += 0.5 * take
    luck_mult = 1 + boost

    p["stats"]["spins"] = p["stats"].get("spins", 0) + count
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
        if count > 1 and p.get("autosell") and item["r"] in ("common", "unc"):
            amt = game.to_units(item["p"], item["k"])
            p["balance"] = round(p["balance"] + amt, 2)
            p["stats"]["sold"] = p["stats"].get("sold", 0) + 1
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
    db.touch_player(uid, balance=p["balance"], coins=p["coins"], xp=p["xp"],
                    donate_balance=p["donate_balance"], free_at=p["free_at"],
                    stats=p["stats"], history=p["history"], achievements=p["achievements"])

    return web.json_response({
        "ok": True,
        "items": items,
        "coins_used": coins_used,
        "cost": cost,
        "luck_mult": round(luck_mult, 2),
        "balance": p["balance"],
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
    pend = _pending.get(uid)
    if not pend or now_ms() - pend["ts"] > PENDING_TTL:
        return web.json_response({"ok": False, "error": "nothing"}, status=404)
    item = pend["item"]
    action = str(payload.get("action") or "")
    earned = 0.0

    if action == "keep":
        db.add_number(uid, item)
        push_history(p, {"k": "keep", "num": item["num"]})
    elif action == "sell":
        earned = game.to_units(item["p"], item["k"])
        p["balance"] = round(p["balance"] + earned, 2)
        p["stats"]["sold"] = p["stats"].get("sold", 0) + 1
        add_xp(p, 2)
        push_history(p, {"k": "sell", "num": item["num"], "amt": earned})
    else:
        return web.json_response({"ok": False, "error": "bad_action"}, status=400)

    _pending.pop(uid, None)
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], xp=p["xp"], stats=p["stats"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "earned": earned, "balance": p["balance"]})


# ---------------------------------------------------------------- бонус / бесплатный спин
async def api_bonus(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
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
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    ms = maintenance_state()
    if ms["started"]:
        return web.json_response({"ok": False, "error": "maintenance"}, status=503)

    number_id = str(payload.get("number_id") or "")
    try:
        chance = int(payload.get("chance") or 45)
    except Exception:
        chance = 45
    steps = 1
    for u in game.UPGRADE:
        if u["p"] == chance:
            steps = u["s"]
            break
    num = db.get_number(number_id)
    if not num or num["player_id"] != uid:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    if num["r"] == "sec":
        return web.json_response({"ok": False, "error": "max"}, status=400)

    db.remove_number(number_id)
    p["stats"]["upg"] = p["stats"].get("upg", 0) + 1
    win, new_r = game.upgrade_result(num["r"], chance, steps)
    result_item = None
    if win:
        c = game.C_BY_ID.get(num["k"], game.COUNTRIES[0])
        rarity = game.R_BY_ID[new_r]
        digits = game.gen_digits(c["L"], new_r)
        taken = [n["num"] for n in db.player_numbers(uid)]
        new_num = game.format_number(c, digits)
        if new_num in taken:
            op_id = num["op"] or game.pick_operator(c["k"])["id"]
            result_item = game.gen_unique_number(c, op_id, rarity, taken)
        else:
            analysis = game.analyze_digits(digits)
            result_item = {
                "id": game.new_id(), "num": new_num, "k": c["k"], "r": new_r,
                "p": game.price_for(digits, rarity, c), "op": num["op"] or game.pick_operator(c["k"])["id"],
                "features": analysis["features"], "score": analysis["score"],
            }
        db.add_number(uid, result_item)
        add_xp(p, 15)
        push_history(p, {"k": "upwin", "num": result_item["num"],
                         "amt": game.to_units(result_item["p"], result_item["k"])})
    else:
        push_history(p, {"k": "uplose", "num": num["num"]})

    _check_achievements(p)
    db.touch_player(uid, xp=p["xp"], stats=p["stats"], history=p["history"],
                    achievements=p["achievements"])
    return web.json_response({"ok": True, "win": win, "item": result_item,
                              "old": num, "balance": p["balance"]})


# ---------------------------------------------------------------- продажа / массовые
async def api_sell(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    number_id = str(payload.get("number_id") or "")
    num = db.get_number(number_id)
    if not num or num["player_id"] != uid:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    amt = game.to_units(num["p"], num["k"])
    db.remove_number(number_id)
    p["balance"] = round(p["balance"] + amt, 2)
    p["stats"]["sold"] = p["stats"].get("sold", 0) + 1
    add_xp(p, 2)
    push_history(p, {"k": "bank", "num": num["num"], "amt": amt})
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], xp=p["xp"], stats=p["stats"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "earned": amt, "balance": p["balance"]})


async def api_mass_sell(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
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

    total = 0.0
    for nid in ids:
        n = db.get_number(nid)
        if not n:
            continue
        total += game.to_units(n["p"], n["k"])
        db.remove_number(nid)
    total = round(total, 2)
    p["balance"] = round(p["balance"] + total, 2)
    p["stats"]["sold"] = p["stats"].get("sold", 0) + len(ids)
    xp_ev = add_xp(p, min(20, len(ids) * 2))
    push_history(p, {"k": "mass_sell", "n": len(ids), "amt": total, "label": mode})
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], xp=p["xp"], stats=p["stats"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "count": len(ids), "earned": total,
                              "balance": p["balance"], "xp_events": xp_ev["events"]})


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
    ms = maintenance_state()
    if ms["started"]:
        return web.json_response({"ok": False, "error": "maintenance"}, status=503)
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
    ms = maintenance_state()
    if ms["started"]:
        return web.json_response({"ok": False, "error": "maintenance"}, status=503)
    listing_id = str(payload.get("listing_id") or "")
    lot = db.market_get(listing_id)
    if not lot:
        return web.json_response({"ok": False, "error": "gone"}, status=404)
    if lot["seller_id"] == uid:
        return web.json_response({"ok": False, "error": "own"}, status=400)
    num = db.get_number(lot["number_id"])
    if not num:
        return web.json_response({"ok": False, "error": "gone"}, status=404)
    cost = game.to_units(lot["price"], num["k"])
    if p["balance"] + p.get("donate_balance", 0) < cost:
        return web.json_response({"ok": False, "error": "no_funds"}, status=402)

    from_donate = min(cost, p.get("donate_balance", 0))
    p["donate_balance"] = round(p.get("donate_balance", 0) - from_donate, 2)
    p["balance"] = round(p["balance"] - (cost - from_donate), 2)

    got = db.market_buy(listing_id, uid)
    seller_id = got["seller_id"]
    seller_amount = round(lot["price"] * (1 - game.FEE), 2)
    db.inbox_add(seller_id, "money", seller_amount, num["k"],
                 text=f"Продажа {num['num']}")
    p["stats"]["bought"] = p["stats"].get("bought", 0) + 1
    add_xp(p, 5)
    push_history(p, {"k": "buy", "num": num["num"], "amt": cost})
    _check_achievements(p)
    db.touch_player(uid, balance=p["balance"], donate_balance=p["donate_balance"],
                    xp=p["xp"], stats=p["stats"], history=p["history"],
                    achievements=p["achievements"])
    return web.json_response({"ok": True, "paid": cost, "balance": p["balance"]})


async def api_market_accept_swap(request: web.Request) -> web.Response:
    """Принять обмен: отдать свой номер и забрать чужой."""
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    ms = maintenance_state()
    if ms["started"]:
        return web.json_response({"ok": False, "error": "maintenance"}, status=503)
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
    items = db.inbox_take(uid)
    earned = 0.0
    got_items = []
    for it in items:
        if it["kind"] == "money":
            amt = float(it["amount"])
            c = game.C_BY_ID.get(it["currency"])
            units = round(amt / c["b"], 2) if c else round(amt, 2)
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
        db.touch_player(uid, balance=p["balance"], history=p["history"])
    return web.json_response({"ok": True, "count": len(items), "earned": earned,
                              "items": got_items, "balance": p["balance"]})


# ---------------------------------------------------------------- задания / достижения
TASKS = [
    {"id": "s5",   "t": "Крутить 5 раз",              "field": "spins",  "n": 5,  "u": 30},
    {"id": "s25",  "t": "Крутить 25 раз",             "field": "spins",  "n": 25, "u": 150},
    {"id": "rare", "t": "Найти «Редкий» или круче",   "field": "rare",   "n": 1,  "u": 50},
    {"id": "ctry", "t": "Крутить в 4 странах",        "field": "ctry",   "n": 4,  "u": 80},
    {"id": "sold", "t": "Продать 3 номера",           "field": "sold",   "n": 3,  "u": 40},
    {"id": "lst",  "t": "Выставить номер",            "field": "listed", "n": 1,  "u": 30},
    {"id": "buy",  "t": "Купить номер",               "field": "bought", "n": 1,  "u": 30},
    {"id": "upg",  "t": "Сделать апгрейд",            "field": "upg",    "n": 1,  "u": 60},
]

ACHS = [
    {"id": "first",   "i": "🎯", "t": "Первый спин",           "c": lambda p, inv: p["stats"].get("spins", 0) >= 1},
    {"id": "x10",     "i": "🔟", "t": "10 спинов",             "c": lambda p, inv: p["stats"].get("spins", 0) >= 10},
    {"id": "x100",    "i": "💯", "t": "100 спинов",            "c": lambda p, inv: p["stats"].get("spins", 0) >= 100},
    {"id": "rare1",   "i": "🔷", "t": "Первый редкий",         "c": lambda p, inv: p["stats"].get("rare", 0) >= 1},
    {"id": "leg",     "i": "👑", "t": "Легендарный номер",     "c": lambda p, inv: any(game.R_INDEX.get(n["r"], 0) >= 5 for n in inv)},
    {"id": "sec",     "i": "🌌", "t": "Секретный номер",       "c": lambda p, inv: any(n["r"] == "sec" for n in inv)},
    {"id": "ops",     "i": "📡", "t": "3 разных оператора",     "c": lambda p, inv: len({n["op"] for n in inv}) >= 3},
    {"id": "rich",    "i": "💰", "t": "Богач",                    "c": lambda p, inv: p["balance"] >= 1000},
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
    claimed = p.get("claimed") or {}
    out = []
    for t in TASKS:
        field = t["field"]
        val = p["stats"].get(field, [])
        if field == "ctry":
            val = len(val or [])
        out.append({**t, "v": min(int(val or 0), t["n"]), "claimed": bool(claimed.get(t["id"]))})
    return web.json_response({"ok": True, "tasks": out, "achievements": p.get("achievements", []),
                              "all_achievements": [{"id": a["id"], "i": a["i"], "t": a["t"]} for a in ACHS]})


async def api_task_claim(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
    tid = str(payload.get("task_id") or "")
    task = next((t for t in TASKS if t["id"] == tid), None)
    if not task:
        return web.json_response({"ok": False, "error": "not_found"}, status=404)
    field = task["field"]
    val = p["stats"].get(field, [])
    if field == "ctry":
        val = len(val or [])
    claimed = p.get("claimed") or {}
    if int(val or 0) < task["n"] or claimed.get(tid):
        return web.json_response({"ok": False, "error": "locked"}, status=400)
    claimed[tid] = 1
    p["claimed"] = claimed
    p["balance"] = round(p["balance"] + task["u"], 2)
    add_xp(p, 10)
    push_history(p, {"k": "task", "amt": task["u"], "name": task["t"]})
    _check_achievements(p)
    db.touch_player(uid, claimed_tasks=claimed, balance=p["balance"], xp=p["xp"],
                    history=p["history"], achievements=p["achievements"])
    return web.json_response({"ok": True, "reward": task["u"], "balance": p["balance"]})


# ---------------------------------------------------------------- купоны
async def api_coupon(request: web.Request) -> web.Response:
    payload = await read_json(request)
    auth = _load_profile(request, payload)
    if not auth:
        return web.json_response({"ok": False, "error": "bad_init"}, status=403)
    uid, p = auth
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
    players = db.list_players()
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


# ---------------------------------------------------------------- техперерыв (игрок)
async def api_maintenance(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, **maintenance_state()})


# ================================================================ админка
async def _admin(request: web.Request, payload: dict) -> dict | None:
    auth = _load_profile(request, payload)
    if not auth:
        return None
    uid, p = auth
    if not p.get("is_admin"):
        return None
    return p


async def api_admin_maintenance(request: web.Request) -> web.Response:
    payload = await read_json(request)
    p = await _admin(request, payload)
    if not p:
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)
    action = str(payload.get("action") or "")
    now = now_ms()
    if action == "status":
        return web.json_response({"ok": True, **maintenance_state()})
    if action == "stop":
        db.maintenance_set(False, 0, 0, "", p["user_id"])
        return web.json_response({"ok": True, **maintenance_state()})

    # start: через N секунд либо в конкретное время (МСК)
    try:
        delay = int(payload.get("delay_sec") or 0)
    except Exception:
        delay = 0
    at_ms = 0
    if payload.get("at"):
        # время "HH:MM" — считаем по МСК (UTC+3)
        import datetime as dt
        hh, mm = str(payload["at"]).split(":")[:2]
        utc_now = dt.datetime.utcnow()
        msk_now = utc_now + dt.timedelta(hours=3)
        target = msk_now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if target <= msk_now:
            target += dt.timedelta(days=1)
        at_ms = int((target - dt.timedelta(hours=3) - utc_now).total_seconds() * 1000) + now
    elif delay > 0:
        at_ms = now + delay * 1000
    message = str(payload.get("message") or "Плановые технические работы")
    db.maintenance_set(True, at_ms, int(payload.get("end_at") or 0), message, p["user_id"])
    push_history(p, {"k": "maintenance", "name": message})
    db.touch_player(p["user_id"], history=p["history"])
    log.info("Техперерыв назначен: %s (через %s сек)", at_ms, delay)
    return web.json_response({"ok": True, **maintenance_state()})


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
        "maintenance": maintenance_state(),
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

    ms = maintenance_state()
    if ms["started"]:
        return web.json_response({"ok": False, "error": "maintenance"}, status=503)

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
    conn = _proxy_connector()
    try:
        from aiohttp import ClientSession
        async with ClientSession(connector=conn) as s:
            async with s.post(url, json=body, timeout=aiohttp_timeout()) as r:
                data = await r.json()
    except Exception as e:
        log.warning("sendInvoice error: %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=502)
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
    if not data.get("ok"):
        return web.json_response({"ok": False, "error": data.get("description", "invoice_failed")},
                                 status=502)
    invoice_url = data["result"].get("invoice_url")
    return web.json_response({"ok": True, "invoice_url": invoice_url, "sku": sku_id})


def _proxy_connector():
    """SOCKS5-коннектор (bot.config.PROXY) для запросов к api.telegram.org."""
    from bot.config import PROXY, proxy_url

    url = proxy_url()
    if not url:
        return None
    try:
        from aiohttp_socks import ProxyConnector
        return ProxyConnector.from_url(url, rdns=PROXY.get("rdns", True))
    except Exception as e:
        log.warning("Не удалось создать прокси-коннектор: %s", e)
        return None


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
    app = web.Application(client_max_size=2 * 1024 * 1024)

    # базовые
    app.router.add_get("/api/health", api_health)
    app.router.add_post("/api/auth", api_auth)
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

    # прогресс
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
