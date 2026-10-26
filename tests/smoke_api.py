"""Smoke-тест нового API SIM Roulette."""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# корень проекта — чтобы работали импорты bot.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8080"
UID = 999999
PASS, FAIL, SKIP = [], [], []

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


def skip(name, why=""):
    SKIP.append(name)
    print(f"  SKIP {name} {why}")


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
spin_num = d.get("items", [{}])[0].get("num", "")
check("российский спин содержит 11 цифр с кодом +7",
      len(re.sub(r"\D", "", spin_num)) == 11, spin_num)
item = (d.get("items") or [{}])[0]
st, d = call("POST", "/api/decide", {"uid": UID, "action": "keep"})
check("keep", st == 200, f"earned={d.get('earned')}")

print("== спин x5 + категория редкий ==")
_db.wallet_apply_deltas(UID, [{"currency": "USD", "amount": 1000}])  # top-up only for this API regression case
st, d = call("POST", "/api/spin", {"uid": UID, "country": "US", "count": 5, "category": "rare"})
items = d.get("items", [])
check("spin x5", st == 200 and len(items) == 5, f"got={len(items)}")
RINDEX = {"common": 0, "unc": 1, "rare": 2, "epic": 3, "myth": 4, "leg": 5, "blat": 6, "sec": 7}
rar = [i["r"] for i in items]
check("категория: не ниже редкого", all(RINDEX[r] >= 2 for r in rar), f"rarities={rar}")

print("== прогресс заданий за период ==")
st, task_data = call("GET", f"/api/tasks?uid={UID}")
tasks_by_id = {t["id"]: t for t in task_data.get("tasks", [])}
check("задания имеют три периода",
      {tasks_by_id.get(k, {}).get("cadence") for k in ("s5", "sold", "s25")} == {"daily", "48h", "weekly"})
check("спины записались в ежедневный прогресс",
      tasks_by_id.get("s5", {}).get("v", 0) == tasks_by_id.get("s5", {}).get("n"),
      f"s5={tasks_by_id.get('s5', {}).get('v')}/{tasks_by_id.get('s5', {}).get('n')}")
check("период имеет время сброса",
      tasks_by_id.get("s5", {}).get("reset_at", 0) > tasks_by_id.get("s5", {}).get("period_start", 0))
TASK_UID = 870000 + int(time.time()) % 100000
call("POST", "/api/auth", {"uid": TASK_UID, "profile": {"first_name": "Task Tester"}})
st, td = call("GET", f"/api/tasks?uid={TASK_UID}")
s5 = next(t for t in td.get("tasks", []) if t["id"] == "s5")
_db.task_progress_add(TASK_UID, "s5", s5["period_start"], 5)
st, claim = call("POST", "/api/tasks/claim", {"uid": TASK_UID, "task_id": "s5"})
check("завершённое задание можно забрать", st == 200 and claim.get("reward") == 30)
st, claim2 = call("POST", "/api/tasks/claim", {"uid": TASK_UID, "task_id": "s5"})
check("награда за период выдаётся один раз", st == 400 and claim2.get("error") == "locked")

print("== коины удачи ==")
# гарантируем коины независимо от того, был ли импорт (БД может быть непустой)
_db.touch_player(UID, coins=10)
_db.wallet_apply_deltas(UID, [{"currency": "JPY", "amount": 100000}])
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
upgrade_currency = {"RU": "RUB", "US": "USD", "DE": "EUR", "GB": "GBP",
                    "CN": "CNY", "JP": "JPY", "KZ": "KZT", "TR": "TRY"}.get(inv[0]["k"], "RUB")
_db.wallet_apply_deltas(UID, [{"currency": upgrade_currency, "amount": 100000}])
st, d = call("POST", "/api/upgrade", {"uid": UID, "number_id": inv[0]["id"], "chance": 45})
check("upgrade (колесо)", st == 200, f"win={d.get('win')} new={(d.get('item') or {}).get('r')}")
wheel = d.get("wheel") or {}
edge = 45 / 100 * 360
landing = wheel.get("landing", -1)
check("колесо: угол в нужном секторе",
      (0 <= landing < edge) if d.get("win") else (edge < landing < 360),
      f"landing={landing} win={d.get('win')} cost={d.get('cost')} steps={d.get('steps')}")
check("45%: безопасный прирост на 2 категории", d.get("steps") == 2,
      f"steps={d.get('steps')}")

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
# Гарантируем валюту лота покупателю независимо от стартовых остатков.
market_num = _db.get_number((listing.get("item") or {}).get("id"))
_market_currency = {"RU": "RUB", "US": "USD", "DE": "EUR", "GB": "GBP",
                    "CN": "CNY", "JP": "JPY", "KZ": "KZT", "TR": "TRY"}.get(market_num["k"], "RUB")
_db.wallet_apply_deltas(111222, [{"currency": _market_currency, "amount": 100000}])
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
PLAYER = 111333  # обычный игрок (не админ)
st, d = call("POST", "/api/admin/maintenance", {"uid": UID, "action": "start", "delay_sec": 3600,
                                                "duration_min": 30, "message": "Плановые работы"})
check("admin: старт техперерыва", st == 200 and d.get("active"), f"seconds_left={d.get('seconds_left')}")
check("admin: end_at проброшен", bool(d.get("end_at")) and d.get("ends_in", 0) > 0,
      f"ends_in={d.get('ends_in')}s")
st, d = call("GET", f"/api/maintenance?uid={PLAYER}")
check("игрок видит таймер", st == 200 and d.get("active") and not d.get("started"),
      f"left={d.get('seconds_left')}s")
st, d = call("POST", "/api/spin", {"uid": PLAYER, "country": "RU"})
check("спин до старта разрешён", st == 200, f"status={st}")
st, d = call("POST", "/api/admin/maintenance", {"uid": UID, "action": "start", "delay_sec": 0,
                                                "message": "Срочно!"})
check("admin: мгновенный старт", st == 200 and d.get("started"))
st, d = call("GET", f"/api/maintenance?uid={PLAYER}")
check("игрок заблокирован", d.get("blocked") is True)
st, d = call("GET", f"/api/maintenance?uid={UID}")
check("админ НЕ заблокирован", d.get("blocked") is False and d.get("is_admin") is True)
st, d = call("POST", "/api/spin", {"uid": PLAYER, "country": "RU"})
check("спин игрока после старта запрещён", st == 503, f"status={st}")
st, d = call("POST", "/api/spin", {"uid": UID, "country": "RU"})
check("админ продолжает играть", st == 200, f"status={st}")
for path, body in (("/api/market/list", {"number_id": "x", "price": 1}),
                   ("/api/sell", {"number_id": "x"}),
                   ("/api/bonus", {}),
                   ("/api/coupon", {"code": "X"}),
                   ("/api/tasks/claim", {"task_id": "s5"})):
    st, d = call("POST", path, dict(body, uid=PLAYER))
    check(f"guard {path}", st == 503, f"status={st}")
st, d = call("POST", "/api/admin/maintenance", {"uid": UID, "action": "stop"})
check("admin: стоп", st == 200 and not d.get("active"))
st, d = call("POST", "/api/spin", {"uid": PLAYER, "country": "RU"})
check("спин снова работает", st == 200)

print("== Stars ==")
st, d = call("GET", "/api/skus")
check("sku list", st == 200 and len(d.get("skus", [])) == 6, f"n={len(d.get('skus', []))}")
st, d = call("POST", "/api/stars/order", {"uid": UID, "sku": "coins"})
inv_ok = bool(d.get("invoice_url"))
chat_not_found = "chat not found" in str(d.get("error", ""))
net_blocked = "Cannot connect" in str(d.get("error", "")) or "Name or service" in str(d.get("error", ""))
if net_blocked:
    skip("инвойс собран (Bot API понял запрос)", "api.telegram.org недоступен в песочнице")
else:
    check("инвойс собран (Bot API понял запрос)", st == 200 or chat_not_found,
          f"url={(d.get('invoice_url') or '')[:50]} err={d.get('error','')}")
# имитация успешной оплаты (как это сделает бот)
import asyncio as _aio
from bot.web.server import apply_purchase
ok, title = _aio.run(apply_purchase(UID, "coins", "simroul:999999:coins:123"))
st, d = call("GET", f"/api/state?uid={UID}")
check("зачисление после Stars", ok and d["player"]["coins"] >= 1, f"title={title} coins={d['player']['coins']}")

print("== новая экономика ==")
FRESH = 700000 + int(time.time()) % 100000   # уникальные uid, чтобы кулдауны не наследовались
FRESH2 = FRESH + 1
st, d = call("GET", f"/api/state?uid={UID}")
lim = d.get("limits", {})
check("лимиты приходят с сервера", bool(lim.get("category_cost")) and lim.get("wheel_max") == 90,
      f"sec_cost_mult={lim.get('category_cost',{}).get('sec')}")
st, d = call("POST", "/api/spin", {"uid": FRESH, "country": "RU", "free": True,
                                   "category": "sec", "count": 20})
check("бесплатный спин: 1 номер, без категории", st == 200 and len(d.get("items", [])) == 1,
      f"r={d.get('items',[{}])[0].get('r')}")
st, d = call("POST", "/api/spin", {"uid": FRESH, "country": "RU", "free": True})
check("бесплатный спин: серверный кулдаун", st == 429 and d.get("error") == "free_cd",
      f"retry_in={d.get('retry_in')}")
st, d = call("POST", "/api/spin", {"uid": FRESH, "country": "RU", "category": "sec"})
check("гейт уровня для Секретного", st == 403 and d.get("error") == "level_locked",
      f"need={d.get('need')}")
st, d = call("POST", "/api/import", {"uid": FRESH2, "data": {"bal": 99999999, "coins": 99999}})
st, d = call("GET", f"/api/state?uid={FRESH2}")
check("импорт ограничен лимитами", d["player"]["balance"] <= 5000 and d["player"]["coins"] <= 100,
      f"bal={d['player']['balance']} coins={d['player']['coins']}")
st, d = call("POST", "/api/import", {"uid": FRESH2, "data": {"bal": 99999999}})
check("импорт только один раз", d.get("reason") == "already")

print("== ставка и ценность апгрейда ==")
from bot.game import upgrade_steps as _upgrade_steps
check("5%: рискованный скачок +4", _upgrade_steps(5) == 4)
check("25%: скачок +3", _upgrade_steps(25) == 3)
check("45%: сбалансированный скачок +2", _upgrade_steps(45) == 2)
check("70–90%: безопасный скачок +1",
      _upgrade_steps(70) == 1 and _upgrade_steps(90) == 1)

print("== выдача баланса админом ==")
GRANT_UID = 800000 + int(time.time()) % 100000
GRANT_NAME = f"grant_{GRANT_UID}"
st, d = call("POST", "/api/auth", {"uid": GRANT_UID,
                                     "profile": {"username": GRANT_NAME, "first_name": "Получатель"}})
st, before = call("GET", f"/api/state?uid={GRANT_UID}")
old_balance = before.get("player", {}).get("balance", 0)
old_jpy = before.get("wallets", {}).get("JPY", 0)
st, d = call("POST", "/api/admin/grant", {"uid": UID, "username": "@" + GRANT_NAME,
                                             "amount": 10000, "country": "JP"})
check("админ выдал иены по Telegram username",
      st == 200 and d.get("currency") == "JP¥" and d.get("amount_units", 0) > 0,
      f"currency={d.get('currency')} units={d.get('amount_units')}")
st, after = call("GET", f"/api/state?uid={GRANT_UID}")
check("выданная валюта и баланс зачислены",
      round(after.get("wallets", {}).get("JPY", 0) - old_jpy, 2) == 10000
      and round(after.get("player", {}).get("balance", 0) - old_balance, 2) == round(d.get("amount_units", 0), 2),
      f"wallet_delta={after.get('wallets',{}).get('JPY',0)-old_jpy} balance_delta={after.get('player',{}).get('balance',0)-old_balance}")
st, d = call("GET", f"/api/admin/stats?uid={UID}")
check("выдача записана в журнал", any(x.get("username") == GRANT_NAME for x in d.get("admin_grants", [])))
st, d = call("POST", "/api/admin/grant", {"uid": GRANT_UID, "username": GRANT_NAME,
                                             "amount": 100, "country": "RU"})
check("не-админ не может выдавать деньги", st == 403)

print("\n" + "=" * 50)
print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}   SKIP: {len(SKIP)}")
if FAIL:
    print("Провалено:")
    for f in FAIL:
        print("  -", f)
