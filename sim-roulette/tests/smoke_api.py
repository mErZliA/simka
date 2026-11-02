"""Smoke-тест нового API SIM Roulette."""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

# корень проекта — чтобы работали импорты bot.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8080"
UID = 999999
PASS, FAIL = [], []

# тестовый юзер становится админом (для проверки админ-эндпоинтов)
import bot.db as _db  # noqa: E402
_db.connect()
_db.admin_add(UID)


def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name} {extra}")


print("== health ==")
st, d = call("GET", "/api/health")
check("health", st == 200 and d.get("ok"))

print("== auth ==")
st, d = call("POST", "/api/auth", {"uid": UID, "profile": {"first_name": "Тестер", "username": "tester"}})
check("auth", st == 200 and d.get("ok"), f"-> admin={d.get('player',{}).get('is_admin')}")

st, d2 = call("POST", "/api/auth", {})
check("auth reject без uid", st == 403)

print("== state ==")
st, d = call("GET", f"/api/state?uid={UID}")
check("state", st == 200 and "player" in d, f"balance={d.get('player',{}).get('balance')}")
player = d.get("player", {})

print("== import старого сейва ==")
st, d = call("POST", "/api/import", {"uid": UID, "data": {
    "bal": 500, "coins": 7, "xp": 120, "streak": 3,
    "inv": [{"id": "old1", "num": "+7 (999) 111-22-33", "k": "RU", "r": "rare",
             "p": 1500, "op": "mts"}],
    "hist": [{"k": "spin", "ts": 1700000000000}],
    "stg": {"sSpd": "fast", "useCoins": True},
}})
check("import", st == 200, f"imported={d.get('imported')}")
st, d = call("GET", f"/api/state?uid={UID}")
check("инвентарь после импорта", len(d.get("inventory", [])) >= 1,
      f"inv={len(d.get('inventory', []))} bal={d['player']['balance']} coins={d['player']['coins']}")

print("== спин x1 + решение ==")
st, d = call("POST", "/api/spin", {"uid": UID, "country": "RU", "count": 1})
check("spin x1", st == 200 and len(d.get("items", [])) == 1, f"item={d.get('items',[{}])[0].get('num')} r={d.get('items',[{}])[0].get('r')}")
item = (d.get("items") or [{}])[0]
st, d = call("POST", "/api/decide", {"uid": UID, "action": "keep"})
check("keep", st == 200, f"earned={d.get('earned')}")

print("== спин x5 + категория редкий ==")
st, d = call("POST", "/api/spin", {"uid": UID, "country": "US", "count": 5, "category": "rare"})
items = d.get("items", [])
check("spin x5", st == 200 and len(items) == 5, f"got={len(items)}")
RINDEX = {"common": 0, "unc": 1, "rare": 2, "epic": 3, "myth": 4, "leg": 5, "blat": 6, "sec": 7}
rar = [i["r"] for i in items]
check("категория: не ниже редкого", all(RINDEX[r] >= 2 for r in rar), f"rarities={rar}")

print("== коины удачи ==")
st, d = call("POST", "/api/spin", {"uid": UID, "country": "JP", "count": 2, "useCoins": True})
check("spin с коинами", st == 200 and d.get("coins_used") == 2, f"coins_used={d.get('coins_used')} luck={d.get('luck_mult')}")

print("== продажа номера ==")
st, d = call("GET", f"/api/state?uid={UID}")
inv = d.get("inventory", [])
check("инвентарь есть", len(inv) >= 1, f"n={len(inv)}")
st, d = call("POST", "/api/sell", {"uid": UID, "number_id": inv[0]["id"]})
check("sell", st == 200, f"earned={d.get('earned')}")

print("== апгрейд ==")
st, d = call("GET", f"/api/state?uid={UID}")
inv = d.get("inventory", [])
st, d = call("POST", "/api/upgrade", {"uid": UID, "number_id": inv[0]["id"], "chance": 45})
check("upgrade", st == 200, f"win={d.get('win')} new={ (d.get('item') or {}).get('r') }")

print("== рынок (мультиплеер) ==")
st, d = call("GET", f"/api/state?uid={UID}")
inv = d.get("inventory", [])
st, d = call("POST", "/api/market/list", {"uid": UID, "number_id": inv[0]["id"], "price": 999})
check("market list", st == 200)
st, d = call("GET", "/api/market")
check("market list виден другим", st == 200 and len(d.get("listings", [])) >= 1,
      f"lots={len(d.get('listings', []))}")
listing = (d.get("listings") or [{}])[0]
# второй игрок покупает
st, d = call("POST", "/api/auth", {"uid": 111222, "profile": {"first_name": "Покупатель"}})
st, d = call("POST", "/api/market/buy", {"uid": 111222, "listing_id": listing.get("id")})
check("покупка другим игроком", st in (200, 402), f"status={st} err={d.get('error')}")
if st == 402:  # не хватило денег — начислим и повторим
    st, d = call("POST", "/api/import", {"uid": 111222, "data": {"bal": 999999}})
    st, d = call("POST", "/api/market/buy", {"uid": 111222, "listing_id": listing.get("id")})
    check("покупка после пополнения", st == 200, f"paid={d.get('paid')}")
st, d = call("GET", "/api/market")
check("лот снят после покупки", all(l.get("id") != listing.get("id") for l in d.get("listings", [])))

print("== задания ==")
st, d = call("GET", f"/api/tasks?uid={UID}")
check("tasks", st == 200 and len(d.get("tasks", [])) >= 8, f"n={len(d.get('tasks', []))}")
done = [t for t in d.get("tasks", []) if t["v"] >= t["n"] and not t["claimed"]]
if done:
    st, d = call("POST", "/api/tasks/claim", {"uid": UID, "task_id": done[0]["id"]})
    check("claim task", st == 200, f"reward={d.get('reward')}")
else:
    check("claim task (некого — пропуск)", True)

print("== купоны ==")
import time as _time
uniq = str(int(_time.time()))[-6:]
st, d = call("POST", "/api/admin/coupons", {"uid": UID, "action": "create",
                                            "kind": "money", "value": 250, "code": f"TEST{uniq}"})
check("создание купона админом", st == 200, f"coupon={d.get('coupon',{}).get('code')} err={d.get('error')}")
code_money = d.get("coupon", {}).get("code")
st, d = call("POST", "/api/coupon", {"uid": UID, "code": code_money or "___"})
check("активация купона", st == 200, f"reward={d.get('reward')}")
st, d = call("POST", "/api/coupon", {"uid": UID, "code": code_money or "___"})
check("повторная активация запрещена", st == 400)
st, d = call("POST", "/api/admin/coupons", {"uid": UID, "action": "create",
                                            "kind": "luck", "value": 4, "max_activations": 1,
                                            "expires_at": 9999999999999})
check("купон удачи x4", st == 200, f"code={d.get('coupon',{}).get('code')}")
luck_code = d.get("coupon", {}).get("code")
st, d = call("POST", "/api/coupon", {"uid": 111222, "code": luck_code})
check("купон удачи активирован", st == 200, f"reward={d.get('reward')}")
st, d = call("POST", "/api/coupon", {"uid": UID, "code": luck_code})
check("лимит 1 активация сработал", st == 400)

print("== ежедневный бонус ==")
st, d = call("POST", "/api/bonus", {"uid": UID})
check("bonus (первый за день или уже взят)", st in (200, 409),
      f"reward={d.get('reward')} streak={d.get('streak')} err={d.get('error')}")
st, d = call("POST", "/api/bonus", {"uid": UID})
check("bonus второй раз запрещён", st == 409)

print("== настройки ==")
st, d = call("POST", "/api/settings", {"uid": UID, "sound_on": False, "vibration": True,
                                       "effects": False, "spin_theme": "neon", "theme": "dark"})
check("settings", st == 200, f"sound={d.get('settings',{}).get('sound_on')} thm={d.get('settings',{}).get('spin_theme')}")

print("== рейтинг ==")
st, d = call("GET", "/api/rating")
check("rating", st == 200 and len(d.get("money", [])) >= 1, f"players={len(d.get('money', []))}")

print("== админка: статистика ==")
st, d = call("GET", f"/api/admin/stats?uid={UID}")
check("admin stats", st == 200 and d.get("players", 0) >= 1, f"players={d.get('players')} nums={d.get('numbers')}")
st, d = call("GET", f"/api/admin/stats?uid=111222")
check("не-админ получает 403", st == 403)

print("== техперерыв ==")
st, d = call("POST", "/api/admin/maintenance", {"uid": UID, "action": "start", "delay_sec": 3600,
                                                "message": "Плановые работы"})
check("admin: старт техперерыва", st == 200 and d.get("active"), f"seconds_left={d.get('seconds_left')}")
st, d = call("GET", "/api/maintenance")
check("игрок видит таймер", st == 200 and d.get("active") and not d.get("started"),
      f"left={d.get('seconds_left')}s")
st, d = call("POST", "/api/spin", {"uid": UID, "country": "RU"})
check("спин до старта разрешён", st == 200, f"status={st}")
st, d = call("POST", "/api/admin/maintenance", {"uid": UID, "action": "start", "delay_sec": 0,
                                                "message": "Срочно!"})
check("admin: мгновенный старт", st == 200 and d.get("started"))
st, d = call("POST", "/api/spin", {"uid": UID, "country": "RU"})
check("спин после старта запрещён", st == 503, f"status={st}")
st, d = call("POST", "/api/market/list", {"uid": UID, "number_id": "x", "price": 1})
check("действия в техперерыве запрещены", st == 503)
st, d = call("POST", "/api/admin/maintenance", {"uid": UID, "action": "stop"})
check("admin: стоп", st == 200 and not d.get("active"))
st, d = call("POST", "/api/spin", {"uid": UID, "country": "RU"})
check("спин снова работает", st == 200)

print("== Stars ==")
st, d = call("GET", "/api/skus")
check("sku list", st == 200 and len(d.get("skus", [])) == 6, f"n={len(d.get('skus', []))}")
st, d = call("POST", "/api/stars/order", {"uid": UID, "sku": "coins"})
inv_ok = bool(d.get("invoice_url"))
chat_not_found = "chat not found" in str(d.get("error", ""))
check("инвойс собран (Bot API понял запрос)", st == 200 or chat_not_found,
      f"url={(d.get('invoice_url') or '')[:50]} err={d.get('error','')}")
# имитация успешной оплаты (как это сделает бот)
import asyncio as _aio
from bot.web.server import apply_purchase
ok, title = _aio.run(apply_purchase(UID, "coins", "simroul:999999:coins:123"))
st, d = call("GET", f"/api/state?uid={UID}")
check("зачисление после Stars", ok and d["player"]["coins"] >= 1, f"title={title} coins={d['player']['coins']}")

print("\n" + "=" * 50)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
if FAIL:
    print("Провалено:")
    for f in FAIL:
        print("  -", f)

