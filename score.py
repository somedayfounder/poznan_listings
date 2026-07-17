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
    "Podolany":          8,  # тихий, зелёный, озёра рядом, поезд до центра 9 мин — недооценён
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

# Short reason strings shown in the Districts tab
DISTRICT_DESCRIPTIONS = {
    "Łacina":            "Лучший по безопасности, шопинг, спорт — тихий и зелёный",
    "Sołacz":            "Семейный, ухоженный, много зелени, близко к паркам",
    "Umultowo":          "Высокая экология (4.04/5), спорт, тихий",
    "Strzeszyn":         "Самый ухоженный (4.2/5), тихий, зелёный лес рядом",
    "Smochowice":        "Тихий, зелёный, озёра рядом — хорошее место для семьи",
    "Winogrady":         "Безопасность, зелень, хорошее трамвайное сообщение",
    "Piątkowo":          "Семейный, трамвай, UAM рядом, развитая инфраструктура",
    "Podolany":          "Тихий, зелёный, озёра рядом, поезд до центра 9 мин — недооценён",
    "Winiary":           "Спокойный, зелёный, у границы Sołacz",
    "Naramowice":        "Новый трамвай (2022), активно развивается, чистый воздух",
    "Morasko":           "Тихий, природный резерват рядом, хорошая экология",
    "Jeżyce":            "Атмосферный, кафе, культура — но экология слабая (2.68/5)",
    "Spławie":           "Тихий пригород в черте города, мало трафика",
    "Junikowo":          "Средний район, нет явных минусов и плюсов",
    "Rataje":            "Malta и озеро рядом, но монотонная застройка",
    "Dębiec":            "Средний район без особых плюсов и минусов",
    "Ogrody":            "Средний, спокойный, без особых проблем",
    "Górczyn":           "Мало зелени, часть Grunwald — не лучший вариант",
    "Szeląg":            "Промзона рядом, ограниченная привлекательность",
    "Żegrze":            "Монотонная застройка, мало зелени",
    "Wilda":             "Криминал, ночной шум, исторический рабочий район",
    "Łazarz":            "Безопасность, чистота, соцсвязи — низкие оценки по всем параметрам",
    "Szczepankowo":      "Пробки на въезде, высокая стоимость жизни относительно качества",
    "Stare Miasto":      "Шум, туристы, нет зелени — плохая экология (2.64/5)",
    "Centrum":           "Аналогично Stare Miasto: шум, плотность, нет зелени",
    "Śródka":            "Криминал, слабое освещение, запущенные дворы",
    "Starołęka Mała":    "Ж/д переезд 8×/час, запущенность, промзона рядом",
    "Starołęka Wielka":  "То же что Starołęka Mała — переезд, промзона, запустение",
    "Główna":            "Худший по безопасности (3.19/5) и культуре (1.95/5)",
    "Ławica":            "Шум авиадвигателей — аэропорт рядом. 55 млн zł выплачено жителям в компенсации",
    "Suchy Las":         "Тихий зелёный северный пригород, хорошая репутация",
    "Złotniki":          "Часть агломерации Suchy Las, хорошая экология и тишина",
    "Puszczykowo":       "Лесная зона, уникальная экология, для тех кто ценит природу",
    "Kiekrz":            "Озеро Kiekrz рядом, природа, тихий",
    "Bolechówko":        "Тихий, без проблем, зелёный пригород",
    "Lusówko":           "Спокойный пригород с хорошим воздухом",
    "Lusowo":            "Спокойный пригород, небольшое комьюнити",
    "Rokietnica":        "Хорошая инфраструктура для пригорода, развивается",
    "Tarnowo Podgórne":  "Развитая инфраструктура, но пробки на въезде в Познань",
    "Przeźmierowo":      "Пробки на въезде, в остальном норм",
    "Skórzewo":          "Пробки, среднее расстояние до центра",
    "Dopiewo":           "Нормальный пригород, без явных проблем",
    "Dopiewiec":         "Тихий, без особых плюсов и минусов",
    "Koziegłowy":        "Средний пригород на севере",
    "Biedrusko":         "Военный полигон рядом — периодический шум выстрелов",
    "Kobylniki":         "Тихий небольшой населённый пункт",
    "Zakrzewo":          "Средний, близко к трассе",
    "Jelonek":           "Небольшой пригород без особых плюсов",
    "Siekierki Wielkie": "Тихий, но удалённый от инфраструктуры",
    "Kamionki":          "Средний пригород, без данных о проблемах",
    "Rabowice":          "Небольшой, тихий, среднее расстояние",
    "Gowarzewo":         "Средний, без явных плюсов и минусов",
    "Jasin":             "Тихий пригород в сторону аэропорта",
    "Gwiazdowo":         "Небольшой, ограниченная инфраструктура",
    "Gruszczyn":         "Тихий восточный пригород",
    "Konarzewo":         "Средний пригород без выраженных особенностей",
    "Kleszczewo":        "Тихий, дальний пригород",
    "Czapury":           "Тихий, на юге, рядом с Puszczykowo",
    "Gołuski":           "Небольшой, тихий",
    "Wiry":              "Тихий пригород без явных проблем",
    "Sapowice":          "Средний, небольшой",
    "Szczytniki":        "Небольшой пригород",
    "Palędzie":          "Тихий, рядом с Komorniki",
    "Strykowo":          "Средний пригород без особых данных",
    "Napachanie":        "Тихий, зелёный, небольшой",
    "Kórnik":            "Замок и озеро — красиво, но далеко от Познани (~20 км)",
    "Komorniki":         "Слабый общественный транспорт, грунтовые дороги в части района",
}


def _score_district(district, city):
    """Check district first, then city name."""
    if district and district in DISTRICT_SCORES:
        return float(DISTRICT_SCORES[district])
    if city and city in DISTRICT_SCORES:
        return float(DISTRICT_SCORES[city])
    return float(_DEFAULT_DISTRICT_SCORE)


def _score_price(price):
    if price is None:
        return 5.0  # neutral, not redistributed
    if price <= 600_000:
        return 10.0
    if price <= 800_000:
        return 10.0 - 2.0 * (price - 600_000) / 200_000   # 600k→10, 800k→8
    if price <= 1_100_000:
        return max(2.0, 8.0 - 6.0 * (price - 800_000) / 300_000)  # 800k→8, 1100k→2
    return 0.0


def _score_area(area):
    if area is None:
        return 5.0
    if area < 70 or area > 120:
        return 0.0
    if 85 <= area <= 105:
        return 10.0
    if area < 85:
        return 4.0 + 6.0 * (area - 70) / 15   # 70→4, 85→10
    return 10.0 - 3.0 * (area - 105) / 15      # 105→10, 120→7


def _score_rooms(rooms):
    if rooms is None:
        return 5.0  # neutral, not redistributed
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
        if d <= 0.5: return 10.0
        if d <= 1.5: return 10.0 - 2.0 * (d - 0.5)     # 0.5→10, 1.5→8
        if d <= 3:   return 8.0 - 2.0 * (d - 1.5) / 1.5  # 1.5→8, 3→6
        if d <= 5:   return max(0.0, 6.0 - 6.0 * (d - 3) / 2)  # 3→6, 5→0
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
    factors = [
        (_score_transport(tp, dist_tram, dist_center), _BASE_W["transport"]),
        (_score_district(district, city),               _BASE_W["district"]),
        (_score_price(price),                           _BASE_W["price"]),
        (_score_area(area),                             _BASE_W["area"]),
        (_score_rooms(rooms),                           _BASE_W["rooms"]),
        (_score_type(tp),                               _BASE_W["type"]),
    ]
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
