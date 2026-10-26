"""
Игровая логика SIM Roulette (серверная часть).

Всё, что влияет на экономику (редкость, генерация номера, цена), считается здесь,
чтобы клиент не мог накрутить результат через консоль.
"""
from __future__ import annotations

import random
import time

# ---------------------------------------------------------------- редкости
# w — базовый вес, m — множитель цены
RARITIES = [
    {"id": "common", "n": "Обычный",     "w": 51.58, "m": 1,    "c": "#9aa4b2"},
    {"id": "unc",    "n": "Необычный",   "w": 26.0, "m": 2,    "c": "#4ade80"},
    {"id": "rare",   "n": "Редкий",      "w": 12.0, "m": 5,    "c": "#38bdf8"},
    {"id": "epic",   "n": "Эпический",   "w": 6.0,  "m": 12,   "c": "#c084fc"},
    {"id": "myth",   "n": "Мифический",  "w": 2.8,  "m": 32,   "c": "#f472b6"},
    {"id": "leg",    "n": "Легендарный", "w": 1.1,  "m": 75,   "c": "#fbbf24"},
    {"id": "blat",   "n": "Блатной",     "w": 0.40, "m": 190,  "c": "#f87171"},
    {"id": "sec",    "n": "Секретный",   "w": 0.12, "m": 470,  "c": "#22d3ee"},
]
# Множители, с которыми цены считались ДО ребаланса (нужны для миграции БД)
LEGACY_M = {"common":1,"unc":2,"rare":5,"epic":15,"myth":50,"leg":150,"blat":500,"sec":2000}
R_BY_ID = {r["id"]: r for r in RARITIES}
R_INDEX = {r["id"]: i for i, r in enumerate(RARITIES)}

# ---------------------------------------------------------------- страны
COUNTRIES = [
    {"k": "RU", "name": "Россия",         "cur": "₽",   "b": 100, "L": 10, "code": "+7 ",
     "fmt": lambda s: f"({s[0:3]}) {s[3:6]}-{s[6:8]}-{s[8:10]}"},
    {"k": "US", "name": "США",            "cur": "$",   "b": 1.5, "L": 10, "code": "+1 ",
     "fmt": lambda s: f"({s[0:3]}) {s[3:6]}-{s[6:10]}"},
    {"k": "DE", "name": "Германия",       "cur": "€",   "b": 1.5, "L": 10, "code": "+49 ",
     "fmt": lambda s: f"{s[0:3]} {s[3:6]} {s[6:10]}"},
    {"k": "GB", "name": "Великобритания", "cur": "£",   "b": 1.2, "L": 10, "code": "+44 ",
     "fmt": lambda s: f"{s[0:4]} {s[4:7]} {s[7:10]}"},
    {"k": "CN", "name": "Китай",          "cur": "CN¥", "b": 10,  "L": 11, "code": "+86 ",
     "fmt": lambda s: f"{s[0:3]} {s[3:7]} {s[7:11]}"},
    {"k": "JP", "name": "Япония",         "cur": "JP¥", "b": 200, "L": 10, "code": "+81 ",
     "fmt": lambda s: f"{s[0:2]} {s[2:6]} {s[6:10]}"},
    {"k": "KZ", "name": "Казахстан",      "cur": "₸",   "b": 500, "L": 10, "code": "+7 ",
     "fmt": lambda s: f"({s[0:3]}) {s[3:6]}-{s[6:8]}-{s[8:10]}"},
    {"k": "TR", "name": "Турция",         "cur": "₺",   "b": 40,  "L": 10, "code": "+90 ",
     "fmt": lambda s: f"({s[0:3]}) {s[3:6]} {s[6:8]} {s[8:10]}"},
]
C_BY_ID = {c["k"]: c for c in COUNTRIES}

# ---------------------------------------------------------------- операторы
OPERATORS = {
    "RU": [{"id": "bel", "n": "Belime",   "s": "Bel", "c": "#ffcc00", "logo": "bee"},
           {"id": "mts", "n": "EmTeEs",   "s": "MTS", "c": "#e30611", "logo": "mts"},
           {"id": "mgf", "n": "MegaFone", "s": "MGF", "c": "#00b14f", "logo": "oval"},
           {"id": "tl2", "n": "TelTwo",   "s": "T2",  "c": "#111111", "logo": "num2"}],
    "US": [{"id": "att", "n": "AiTiTi",   "s": "ATT", "c": "#00a8e0", "logo": "globe"},
           {"id": "vrz", "n": "Verizone", "s": "VRZ", "c": "#cd040b", "logo": "check"},
           {"id": "tmb", "n": "TiMob",    "s": "TMB", "c": "#e20074", "logo": "tmob"}],
    "DE": [{"id": "tkm", "n": "TelKom",   "s": "TKM", "c": "#e20074", "logo": "telekom"},
           {"id": "vdf", "n": "VodaFone", "s": "VDF", "c": "#e60000", "logo": "quote"},
           {"id": "o2",  "n": "O-Too",    "s": "O2",  "c": "#0019a5", "logo": "o2"}],
    "GB": [{"id": "vdf", "n": "VodaFone", "s": "VDF", "c": "#e60000", "logo": "quote"},
           {"id": "o2",  "n": "O-Too",    "s": "O2",  "c": "#0019a5", "logo": "o2"},
           {"id": "epl", "n": "E-Plus",   "s": "E+",  "c": "#00a86b", "logo": "eplus"}],
    "CN": [{"id": "cmp", "n": "ChineMobil", "s": "CMB", "c": "#007b8a", "logo": "cmob"},
           {"id": "cuc", "n": "ChineUni",   "s": "CUC", "c": "#c8102e", "logo": "cuni"},
           {"id": "ctc", "n": "ChineTel",   "s": "CTC", "c": "#0f4c9f", "logo": "ctel"}],
    "JP": [{"id": "dcm", "n": "Docomo",    "s": "DCM", "c": "#e60012", "logo": "docomo"},
           {"id": "sbf", "n": "SooftBank", "s": "SBF", "c": "#a6a6a6", "logo": "soft"},
           {"id": "au",  "n": "Auu",       "s": "au",  "c": "#ff7a00", "logo": "au"}],
    "KZ": [{"id": "bel", "n": "Belime",   "s": "Bel", "c": "#ffcc00", "logo": "bee"},
           {"id": "act", "n": "ActiveK",  "s": "ACT", "c": "#00b14f", "logo": "active"},
           {"id": "tl2", "n": "TelTwo",   "s": "T2",  "c": "#111111", "logo": "num2"}],
    "TR": [{"id": "tcl", "n": "TurkCell",    "s": "TCL", "c": "#004a99", "logo": "turk"},
           {"id": "vdf", "n": "VodaFone",    "s": "VDF", "c": "#e60000", "logo": "quote"},
           {"id": "ttk", "n": "TurkTelKom",  "s": "TTK", "c": "#c8102e", "logo": "ttel"}],
}

# ---------------------------------------------------------------- константы
SPIN_X = 20            # стоимость одного спина в единицах баланса

# ---- стоимость прокрута по категории (множитель к SPIN_X) ----
# Чем выше категория — тем дороже прокрут. Множитель ≈ отношению средней
# ценности номеров категории к базовой (см. price_for): дешёвые категории
# остаются выгодными, дорогие — дорогой азарт со сопоставимыми шансами.
CATEGORY_COST = {
    "common": 1,
    "unc":    2,
    "rare":   4,
    "epic":   8,
    "myth":   16,
    "leg":    33,
    "blat":   69,
    "sec":    113,
}

# Высокие категории открываются по уровню — новичок не может купить джекпот.
CATEGORY_LEVEL = {"leg": 4, "blat": 7, "sec": 10}

# Лимиты переноса старого сохранения (защита от накрутки баланса)
IMPORT_MAX = {"balance": 5000, "coins": 100, "xp": 5000, "streak": 30,
              "ref_count": 100, "numbers": 200}

# Колесо апгрейда
WHEEL_MIN, WHEEL_MAX = 5, 90
WHEEL_STEP_BANDS = [
    {"max_chance": 10, "steps": 4},
    {"max_chance": 25, "steps": 3},
    {"max_chance": 45, "steps": 2},
    {"max_chance": 90, "steps": 1},
]
LUCK_MAX = 10


def spin_cost(category_id: str | None, count: int = 1, free: bool = False) -> float:
    """Цена спина в единицах баланса: зависит от выбранной категории."""
    if free:
        return 0
    mult = CATEGORY_COST.get(category_id or "", 1)
    return round(SPIN_X * mult * max(1, int(count)), 2)
BONUS_X = 50           # ежедневный бонус (база)
FREE_CD = 30 * 60 * 1000
FEE = 0.05             # комиссия рынка
UPGRADE = [  # шанс, прибавка ранга, цвет
    {"p": 45, "s": 1, "c": "#4ade80"},
    {"p": 30, "s": 2, "c": "#38bdf8"},
    {"p": 15, "s": 3, "c": "#c084fc"},
    {"p": 7,  "s": 4, "c": "#f472b6"},
]


def _rd() -> int:
    return random.randint(0, 9)


def new_id() -> str:
    return f"{int(time.time() * 1000):x}{random.randrange(16**6):06x}"


# ---------------------------------------------------------------- генерация цифр
def gen_digits(length: int, rarity_id: str) -> list[int]:
    a = [_rd() for _ in range(length)]
    if a[0] == 0:
        a[0] = 1 + _rd() % 9

    def tail(t):
        a[length - len(t):] = t

    def same(n):
        tail([_rd()] * n)

    def asc(n):
        s = _rd() % (11 - n)
        tail([s + i for i in range(n)])

    if rarity_id == "common":
        if a[length - 1] == a[length - 2]:
            a[length - 1] = (a[length - 1] + 1 + _rd() % 8) % 10
    elif rarity_id == "unc":
        same(2)
    elif rarity_id == "rare":
        same(3)
    elif rarity_id == "epic":
        same(4) if random.random() < .5 else asc(4)
    elif rarity_id == "myth":
        if random.random() < .5:
            same(5)
        else:
            x, y, z = _rd(), _rd(), _rd()
            tail([x, y, z, z, y, x])
    elif rarity_id == "leg":
        same(6) if random.random() < .6 else asc(7)
    elif rarity_id == "blat":
        same(7)
    elif rarity_id == "sec":
        if random.random() < .5:
            a[:] = [1 + _rd() % 9] * length
        else:
            a[:] = [(i + 1) % 10 for i in range(length)]
    return a


# ---------------------------------------------------------------- красота номера
def analyze_digits(dg: list[int]) -> dict:
    L = len(dg)
    features: list[dict] = []
    score = 0

    run, best_run, run_digit = 1, 1, dg[0]
    for i in range(1, L):
        if dg[i] == dg[i - 1]:
            run += 1
            if run > best_run:
                best_run, run_digit = run, dg[i]
        else:
            run = 1
    if best_run >= 3:
        w = min(45, (best_run - 2) * 12)
        score += w
        features.append({"t": f"{best_run}× «{run_digit}» подряд", "w": w})

    pal = L >= 4 and all(dg[i] == dg[L - 1 - i] for i in range(L // 2))
    if pal:
        score += 26
        features.append({"t": "Палиндром", "w": 26})

    asc = all((dg[i] - dg[i - 1] + 10) % 10 == 1 for i in range(1, L))
    desc = all((dg[i - 1] - dg[i] + 10) % 10 == 1 for i in range(1, L))
    if asc:
        score += 20
        features.append({"t": "Возрастающий ряд", "w": 20})
    if desc:
        score += 20
        features.append({"t": "Убывающий ряд", "w": 20})

    for plen in range(2, L // 2 + 1):
        if L % plen:
            continue
        if all(dg[i] == dg[i % plen] for i in range(L)):
            w = 18 if plen == 2 else (15 if plen == 3 else 10)
            score += w
            features.append({"t": f"Повтор блока ×{L // plen}", "w": w})
            break

    z = 0
    for i in range(L - 1, -1, -1):
        if dg[i] == 0:
            z += 1
        else:
            break
    if z >= 2:
        score += z * 4
        features.append({"t": f"{z} нулей в конце", "w": z * 4})

    c8, c7 = dg.count(8), dg.count(7)
    if c8 >= 2:
        score += c8 * 4
        features.append({"t": f"{c8}× «8» (удача)", "w": c8 * 4})
    if c7 >= 2:
        score += c7 * 3
        features.append({"t": f"{c7}× «7» (удача)", "w": c7 * 3})
    if all(d % 2 == 0 for d in dg):
        score += 9
        features.append({"t": "Только чётные", "w": 9})
    if all(d % 2 == 1 for d in dg):
        score += 9
        features.append({"t": "Только нечётные", "w": 9})
    if L >= 5 and dg[0] == dg[-1] and not pal:
        score += 6
        features.append({"t": "Первая = последняя", "w": 6})

    score = max(4, min(100, score))
    features.sort(key=lambda f: -f["w"])
    return {"score": score, "features": features}


def price_for(dg: list[int], rarity: dict, country: dict) -> float:
    score = analyze_digits(dg)["score"]
    # Раньше score доходил до 100 и давал ×9.3 к цене. Ограничиваем ×6.
    base = country["b"] * rarity["m"] * (0.8 + random.random() * 0.4)
    return round(base * (1 + min(score, 60) / 12), 2)


# ---------------------------------------------------------------- выпадение
def pick_rarity(luck_mult: float = 1, min_rarity_id: str | None = None) -> dict:
    """Выбор редкости. luck_mult — множитель удачи (купоны), min_rarity_id —
    нижняя граница категории (номера ниже неё не выпадают)."""
    luck = max(1.0, min(float(luck_mult or 1), LUCK_MAX))
    weights = []
    for r in RARITIES:
        idx = R_INDEX[r["id"]]
        # Линейный сдвиг весов к редким + жёсткий потолок: даже ×10 удача
        # не гарантирует «Секретный», а лишь заметно повышает шанс.
        x = (luck - 1) * idx * 0.62
        factor = 1 + 21 * x / (x + 21)   # монотонно, без резкого потолка
        w = r["w"] * factor
        if min_rarity_id and idx < R_INDEX.get(min_rarity_id, 0):
            w = 0.0
        weights.append(w)
    total = sum(weights)
    if total <= 0:
        return RARITIES[0]
    x = random.random() * total
    for r, w in zip(RARITIES, weights):
        x -= w
        if x < 0:
            return r
    return RARITIES[0]


def pick_operator(country_id: str) -> dict:
    arr = OPERATORS.get(country_id) or [{"id": "x", "n": "?", "s": "?", "c": "#888", "logo": "num2"}]
    return random.choice(arr)


def find_operator(country_id: str, op_id: str) -> dict:
    for o in OPERATORS.get(country_id, []):
        if o["id"] == op_id:
            return o
    return pick_operator(country_id)


def format_number(country: dict, digits: list[int]) -> str:
    return country["code"] + country["fmt"]("".join(str(d) for d in digits))


def gen_unique_number(country: dict, op_id: str, rarity: dict, taken: list[str]) -> dict:
    """Генерирует номер, которого ещё нет у игрока (и не в списке pending)."""
    for _ in range(60):
        digits = gen_digits(country["L"], rarity["id"])
        num = format_number(country, digits)
        if num not in taken:
            break
    else:
        digits = gen_digits(country["L"], rarity["id"])
        num = format_number(country, digits)
    analysis = analyze_digits(digits)
    return {
        "id": new_id(),
        "num": num,
        "f": "",           # флаг страны (клиент подставит)
        "k": country["k"],
        "r": rarity["id"],
        "p": price_for(digits, rarity, country),
        "op": op_id,
        "features": analysis["features"],
        "score": analysis["score"],
    }


def make_spin(country_id: str, category: str | None, luck_mult: float,
              taken_nums: list[str]) -> dict:
    """Один спин: возвращает номер + редкость."""
    country = C_BY_ID.get(country_id) or COUNTRIES[0]
    rarity = pick_rarity(luck_mult, category)
    op = pick_operator(country["k"])
    item = gen_unique_number(country, op["id"], rarity, taken_nums)
    item["rarity_color"] = rarity["c"]
    item["rarity_name"] = rarity["n"]
    item["op_short"] = op["s"]
    item["country_flag"] = _flag(country["k"])
    item["country_name"] = country["name"]
    item["country_code"] = country["code"]
    item["country_fmt"] = country["fmt"](item["num"][len(country["code"]):])
    return item


_FLAGS = {"RU": "🇷🇺", "US": "🇺🇸", "DE": "🇩🇪", "GB": "🇬🇧",
          "CN": "🇨🇳", "JP": "🇯🇵", "KZ": "🇰🇿", "TR": "🇹🇷"}


def _flag(k: str) -> str:
    return _FLAGS.get(k, "🏳")


def to_units(item_price: float, country_id: str) -> float:
    """Перевести цену номера в общие единицы баланса."""
    c = C_BY_ID.get(country_id)
    if not c:
        return float(item_price)
    return round(float(item_price) / c["b"], 2)


# ---------------------------------------------------------------- апгрейд
def clamp_chance(value) -> int:
    try:
        chance = int(value)
    except Exception:
        chance = 45
    return max(WHEEL_MIN, min(WHEEL_MAX, chance))


def upgrade_steps(chance: int) -> int:
    """Высокий шанс даёт небольшой прирост, низкий — редкий большой скачок."""
    chance = clamp_chance(chance)
    return next(band["steps"] for band in WHEEL_STEP_BANDS
                if chance <= band["max_chance"])


def upgrade_cost(chance: int, steps: int, rarity_id: str = "common") -> float:
    """Апгрейд теперь стоит денег: безопасная ставка дороже рискованной."""
    chance = clamp_chance(chance)
    idx = R_INDEX.get(rarity_id, 0)
    return round(SPIN_X * steps * (1 + chance / 50) * (1 + idx * 0.2), 2)


def wheel_landing(chance: int, win: bool) -> float:
    """Угла stop-позиции стрелки (градусы от верха по часовой).

    Сектор победы занимает `chance`% окружности, остальное — сгорание.
    Угол выбирается сервером, клиент лишь красиво докручивает колесо.
    """
    chance = clamp_chance(chance)
    edge = chance / 100 * 360
    if win:
        return round(random.uniform(1.5, max(2.5, edge - 1.5)), 2)
    return round(random.uniform(min(358.5, edge + 1.5), 358.5), 2)


def upgrade_result(number_rarity: str, chance: int, steps: int) -> tuple[bool, str | None]:
    """Исход апгрейда: (успех, новая_редкость | None)."""
    win = random.random() * 100 < clamp_chance(chance)
    if not win:
        return False, None
    idx = min(len(RARITIES) - 1, R_INDEX.get(number_rarity, 0) + steps)
    return True, RARITIES[idx]["id"]
