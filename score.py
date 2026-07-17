"""
Scoring system for Poznań listings (0–10 scale).

Weights:
  transport  30%  — dist to tram (apt: ≤3km; house: ≤10km)
  district   20%  — neighborhood quality (known issues/reputation)
  price      20%  — ≤700k=10, 700–900k=10→8, 900k–1200k=8→0, >1200k=0
  area       15%  — 90–100m² ideal; <70 or >120 = 0
  rooms      10%  — 4=10, 5=8, 3=7, 6=7, others=3
  type        5%  — house=10, apt=8

Missing price or rooms: weight redistributed proportionally so the
listing isn't penalised for lack of data.
"""

# District scores 1–10. Unlisted = 6 (neutral, no data).
# Sources: beesafe.pl, naszpoznan.com, gloswielkopolski.pl, wielkopolskaes.pl
DISTRICT_SCORES = {
    # === Внутренние районы Познани ===
    "Łacina":            8,  # лучший по безопасности, шопинг, спорт — но не исключительный
    "Sołacz":            8,  # семейный, зелёный, ухоженный
    "Umultowo":          8,  # экология 4.04/5, спорт 4.17/5
    "Strzeszyn":         8,  # самый ухоженный (4.2/5), тихий
    "Smochowice":        8,  # тихий, зелёный
    "Winogrady":         8,  # безопасность, зелень, трамвай
    "Piątkowo":          8,  # семейный, трамвай, UAM рядом
    "Winiary":           7,  # спокойный
    "Naramowice":        7,  # новый трамвай 2022, развивается
    "Morasko":           7,  # тихий, природный резерват рядом
    "Jeżyce":            7,  # атмосферный, но экология слабая (2.68/5)
    "Podolany":          7,  # нет ярких минусов
    "Spławie":           7,  # тихий пригород в черте города
    "Junikowo":          6,  # средний
    "Rataje":            6,  # Malta рядом, но бетонно
    "Dębiec":            6,  # средний
    "Ogrody":            6,  # средний
    "Górczyn":           5,  # мало зелени (Grunwald — худшая часть)
    "Szeląg":            5,  # промзона рядом
    "Żegrze":            5,  # монотонный
    "Wilda":             5,  # криминал, ночной шум
    "Łazarz":            4,  # безопасность, чистота, соцсвязи — низкие
    "Szczepankowo":      4,  # пробки, дорого (2.16/5 за стоимость жизни)
    "Stare Miasto":      4,  # шум, туристы, нет зелени (2.64/5 экология)
    "Centrum":           4,  # аналогично Stare Miasto
    "Śródka":            4,  # криминал, слабое освещение
    "Starołęka Mała":    4,  # переезд 8×/час, запущенность
    "Starołęka Wielka":  4,  # то же
    "Główna":            3,  # худший: безопасность 3.19, культура 1.95
    "Ławica":            2,  # шум самолётов, 55 млн zł штрафов за шум

    # === Пригороды с хорошей репутацией ===
    "Suchy Las":         8,  # тихий, зелёный северный пригород
    "Złotniki":          8,  # часть Suchy Las, хорошая
    "Puszczykowo":       8,  # лесная зона, экология
    "Kiekrz":            7,  # озеро, природа
    "Bolechówko":        7,  # тихий
    "Lusówko":           7,  # спокойный
    "Lusowo":            7,  # спокойный
    "Rokietnica":        7,  # хорошая инфраструктура для пригорода

    # === Пригороды со средней репутацией ===
    "Tarnowo Podgórne":  6,  # корки, но развитая инфраструктура
    "Przeźmierowo":      6,  # корки на въезде
    "Skórzewo":          6,  # корки
    "Dopiewo":           6,  # нормальный
    "Dopiewiec":         6,
    "Koziegłowy":        6,
    "Biedrusko":         6,  # военный полигон рядом — шум стрельб
    "Kobylniki":         6,
    "Zakrzewo":          6,
    "Jelonek":           6,
    "Siekierki Wielkie": 6,
    "Kamionki":          6,
    "Rabowice":          6,
    "Gowarzewo":         6,
    "Jasin":             6,
    "Gwiazdowo":         6,
    "Gruszczyn":         6,
    "Konarzewo":         6,
    "Kleszczewo":        6,
    "Czapury":           6,
    "Gołuski":           6,
    "Wiry":              6,
    "Sapowice":          6,
    "Szczytniki":        6,
    "Palędzie":          6,
    "Strykowo":          6,
    "Napachanie":        6,
    "Kórnik":            6,

    # === Проблемные пригороды ===
    "Komorniki":         5,  # слабый транспорт, грунтовые дороги
    "Starołęka Mała":    4,
    "Starołęka Wielka":  4,
}

_DEFAULT_DISTRICT_SCORE = 6  # нет данных — нейтрально


def _score_district(district, city):
    """Check district first, then city name."""
    if district and district in DISTRICT_SCORES:
        return float(DISTRICT_SCORES[district])
    if city and city in DISTRICT_SCORES:
        return float(DISTRICT_SCORES[city])
    return float(_DEFAULT_DISTRICT_SCORE)


def _score_price(price):
    if price is None:
        return None  # signals "missing"
    if price <= 700_000:
        return 10.0
    if price <= 900_000:
        # 700k→10, 900k→8
        return 10.0 - 2.0 * (price - 700_000) / 200_000
    if price <= 1_200_000:
        # 900k→8, 1200k→0
        return max(0.0, 8.0 - 8.0 * (price - 900_000) / 300_000)
    return 0.0


def _score_area(area):
    if area is None:
        return 5.0
    if area < 70 or area > 120:
        return 0.0
    if 90 <= area <= 100:
        return 10.0
    if area < 90:
        return 4.0 + 6.0 * (area - 70) / 20   # 70→4, 90→10
    return 10.0 - 3.0 * (area - 100) / 20      # 100→10, 120→7


def _score_rooms(rooms):
    if rooms is None:
        return None  # signals "missing"
    if rooms == 4:
        return 10.0
    if rooms == 5:
        return 8.0
    if rooms in (3, 6):
        return 7.0
    return 3.0  # ≤2 or 7+


def _score_transport(tp, dist_tram, dist_center):
    d = dist_tram if dist_tram is not None else dist_center
    if d is None:
        return 5.0
    if tp == "dom":
        if d <= 2:   return 10.0
        if d <= 5:   return 10.0 - 1.0 * (d - 2) / 3   # 2→10, 5→9
        if d <= 10:  return max(6.0, 9.0 - 3.0 * (d - 5) / 5)  # 5→9, 10→6
        return max(0.0, 6.0 - 6.0 * (d - 10) / 5)      # 10→6, 15→0
    else:
        if d <= 1:   return 10.0
        if d <= 3:   return 10.0 - 3.0 * (d - 1) / 2   # 1→10, 3→7
        if d <= 5:   return max(0.0, 7.0 - 7.0 * (d - 3) / 2)  # 3→7, 5→0
        return 0.0


def _score_type(tp):
    return 10.0 if tp == "dom" else 8.0


_BASE_W = {
    "transport": 0.25,
    "district":  0.35,
    "price":     0.18,
    "area":      0.12,
    "rooms":     0.07,
    "type":      0.03,
}


def compute_score(price, area, rooms, tp, dist_tram, dist_center, district=None, city=None):
    p_score = _score_price(price)
    r_score = _score_rooms(rooms)

    factors = [
        (_score_transport(tp, dist_tram, dist_center), _BASE_W["transport"]),
        (_score_district(district, city),               _BASE_W["district"]),
        (_score_area(area),                             _BASE_W["area"]),
        (_score_type(tp),                               _BASE_W["type"]),
    ]
    if p_score is not None:
        factors.append((p_score, _BASE_W["price"]))
    if r_score is not None:
        factors.append((r_score, _BASE_W["rooms"]))

    total_w = sum(w for _, w in factors)
    return round(sum(sc * w for sc, w in factors) / total_w, 1)


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _rooms(raw):
    if raw in (None, ""):
        return None
    if str(raw) == "7+":
        return 7
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def score_from_csv(r):
    return compute_score(
        price=_f(r.get("price_zl")),
        area=_f(r.get("area_m2")),
        rooms=_rooms(r.get("rooms")),
        tp=r.get("type", ""),
        dist_tram=_f(r.get("drive_tram_km")) or _f(r.get("dist_tram")),
        dist_center=_f(r.get("drive_ratusz_km")) or _f(r.get("dist_km")),
        district=r.get("district", ""),
        city=r.get("city", ""),
    )


def score_from_jsrow(r):
    return compute_score(
        price=r.get("price"),
        area=r.get("area"),
        rooms=_rooms(r.get("rooms")),
        tp=r.get("type", ""),
        dist_tram=r.get("dist_tram"),
        dist_center=r.get("dist"),
        district=r.get("district", ""),
        city=r.get("city", ""),
    )
