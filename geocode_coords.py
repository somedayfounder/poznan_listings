#!/usr/bin/env python3
"""
Уточняет координаты новых объявлений:
1. Читает описание со страницы объявления
2. GPT извлекает фактический адрес объекта
3. Nominatim (OpenStreetMap) возвращает точные координаты
4. Обновляет listings_latest.csv; аудит сохраняется в coords_override.json
"""
import csv, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from math import radians, sin, cos, sqrt, atan2

DATA_DIR = Path(__file__).parent
CSV_FILE = DATA_DIR / "listings_latest.csv"
OVERRIDE_FILE = DATA_DIR / "coords_override.json"
GPT_TOKEN = os.environ.get("GPT_TOKEN", "")

NOMINATIM_HEADERS = {"User-Agent": "poznan-listings-bot/1.0 (aliaksandrpaltaratski@gmail.com)"}

GPT_PROMPT = """Прочитай описание польского объявления о недвижимости и определи, где физически находится объект.

Рассуждай так:
- Если в тексте явно названа деревня, посёлок или пригород (wioska, miejscowość, wieś) — объект там, а не в городе рядом.
- Улица в описании может быть улицей застройщика в городе, а не адресом объекта — смотри на контекст.
- Если текст говорит "в деревне X рядом с Y" — объект в X, не в Y.
- city = фактическое место нахождения объекта (деревня/пригород если упомянуты, иначе город).
- street = улица только если она явно является адресом объекта, а не просто ориентиром.
- number = номер дома если указан в тексте (например "346", "12A"), иначе null.

Описание:
{description}

Верни ТОЛЬКО JSON (без пояснений):
{{
  "street": "ul. X если это реальный адрес объекта, иначе null",
  "number": "номер дома или null",
  "osiedle": "название ЖК/осьедля или null",
  "city": "фактический город/деревня где находится объект",
  "district": "район/dzielnica или null",
  "confidence": "high/medium/low"
}}"""


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = radians(lat2 - lat1); do = radians(lon2 - lon1)
    a = sin(dl/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(do/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def fetch_description(url):
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9",
        })
        html = urlopen(req, timeout=15).read().decode("utf-8", "replace")
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return None
        ad = json.loads(m.group(1))["props"]["pageProps"].get("ad") or {}
        desc = ad.get("description", "")
        return re.sub(r"<[^>]+>", " ", desc)[:3000]
    except Exception as e:
        print(f"    fetch error: {e}")
        return None


def gpt_extract_address(description):
    if not GPT_TOKEN:
        return {}
    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": GPT_PROMPT.format(description=description)}],
        "temperature": 0,
        "max_tokens": 200,
    }).encode()
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {GPT_TOKEN}"},
    )
    try:
        resp = json.loads(urlopen(req, timeout=20).read())
        text = resp["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(m.group(0)) if m else {}
    except Exception as e:
        print(f"    GPT error: {e}")
        return {}


def geocode(query, street=None, housenumber=None, city=None):
    params = {"format": "json", "addressdetails": 1, "limit": 1,
              "countrycodes": "pl", "accept-language": "pl"}
    if street and city:
        params["street"] = f"{housenumber} {street}" if housenumber else street
        params["city"] = city
    else:
        params["q"] = query
    url = "https://nominatim.openstreetmap.org/search?" + urlencode(params)
    try:
        req = Request(url, headers=NOMINATIM_HEADERS)
        resp = json.loads(urlopen(req, timeout=10).read())
        if resp:
            r = resp[0]
            addr = r.get("address", {})
            city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
            district = addr.get("suburb") or addr.get("quarter") or addr.get("neighbourhood")
            formatted = r.get("display_name", "")
            return round(float(r["lat"]), 6), round(float(r["lon"]), 6), formatted, city, district
    except Exception as e:
        print(f"    geocode error: {e}")
    return None, None, None, None, None


def geocode_photon(query):
    """Photon (komoot) — fallback когда Nominatim не нашёл.
    Принимаем только результаты типа street/place/building (не POI).
    """
    GOOD_KEYS = {"place", "highway", "boundary", "landuse"}
    GOOD_VALUES = {"residential", "street", "road", "city", "town", "village",
                   "suburb", "neighbourhood", "house", "apartments", "yes"}
    url = "https://photon.komoot.io/api?" + urlencode({
        "q": query, "limit": 5,
        "lat": "52.4", "lon": "16.9",
    })
    try:
        req = Request(url, headers={"User-Agent": "poznan-listings-bot/1.0"})
        data = json.loads(urlopen(req, timeout=10).read())
        for f in data.get("features", []):
            props = f.get("properties", {})
            key = props.get("osm_key", "")
            val = props.get("osm_value", "")
            if key not in GOOD_KEYS and val not in GOOD_VALUES:
                continue
            coords = f["geometry"]["coordinates"]
            city = props.get("city") or props.get("town") or props.get("village") or props.get("name")
            district = props.get("district") or props.get("suburb")
            formatted = ", ".join(filter(None, [
                props.get("name"), props.get("street"), props.get("housenumber"),
                props.get("city"), props.get("country")
            ]))
            return round(coords[1], 6), round(coords[0], 6), formatted, city, district
    except Exception as e:
        print(f"    photon error: {e}")
    return None, None, None, None, None



def build_query(addr):
    """Строим поисковый запрос для геокодирования."""
    city = addr.get("city") or "Poznań"
    street = addr.get("street")
    number = addr.get("number")
    if number in (None, "null", "none", ""):
        number = None
    osiedle = addr.get("osiedle")
    district = addr.get("district")
    if street:
        street_with_num = f"{street} {number}" if number else street
        return f"{street_with_num}, {city}, Polska"
    if osiedle:
        return f"{osiedle}, {city}, Polska"
    if district:
        return f"{district}, {city}, Polska"
    return None


def main():
    if not GPT_TOKEN:
        print("GPT_TOKEN не задан — пропускаем geocode_coords")
        return

    rows = list(csv.DictReader(open(CSV_FILE, encoding="utf-8-sig")))
    overrides = json.loads(OVERRIDE_FILE.read_text()) if OVERRIDE_FILE.exists() else {}

    need = [r for r in rows if r["id"] not in overrides and r.get("lat") and r.get("lon")]
    print(f"Объявлений: {len(rows)}, уже обработано: {len(overrides)}, к обработке: {len(need)}")

    updated = 0
    for i, r in enumerate(need):
        lid = r["id"]
        print(f"  [{i+1}/{len(need)}] {lid} {r.get('title','')[:50]}")

        desc = fetch_description(r["url"])
        if not desc:
            overrides[lid] = {"skipped": "no_description"}
            continue

        addr = gpt_extract_address(desc)
        query = build_query(addr)

        if not query or addr.get("confidence") == "low":
            overrides[lid] = {"skipped": "low_confidence", "addr": addr}
            print(f"    → нет адреса (confidence={addr.get('confidence')})")
            time.sleep(1.0)
            continue

        gpt_street = addr.get("street")
        gpt_number = addr.get("number") if addr.get("number") not in (None, "null", "none", "") else None
        gpt_city_q = addr.get("city") or "Poznań"
        new_lat, new_lon, formatted, geo_city, geo_district = geocode(
            query, street=gpt_street, housenumber=gpt_number, city=gpt_city_q
        )
        # fallback 1: Nominatim без номера дома
        if new_lat is None and gpt_number and gpt_street:
            fallback_query = f"{gpt_street}, {gpt_city_q}, Polska"
            new_lat, new_lon, formatted, geo_city, geo_district = geocode(fallback_query)
        # fallback 2: Photon
        if new_lat is None:
            time.sleep(0.3)
            new_lat, new_lon, formatted, geo_city, geo_district = geocode_photon(query)
            if new_lat is not None:
                print(f"    [photon] {formatted}")
        if new_lat is None:
            overrides[lid] = {"skipped": "geocode_failed", "query": query, "addr": addr}
            print(f"    → геокодинг не дал результата: {query}")
            time.sleep(0.3)
            continue

        # Если геокодер вернул не тот город что GPT определил — откатываемся к геокодингу города
        # Но не откатываемся если geo_district совпадает с gpt_city (Naramowice, Grunwald — районы Познани)
        gpt_city = (addr.get("city") or "").lower().strip()
        geo_city_norm = (geo_city or "").lower().strip()
        geo_dist_norm = (geo_district or "").lower().strip()
        city_in_district = gpt_city and geo_dist_norm and (gpt_city in geo_dist_norm or geo_dist_norm in gpt_city)
        if gpt_city and geo_city_norm and gpt_city not in geo_city_norm and geo_city_norm not in gpt_city and not city_in_district:
            city_query = f"{addr['city']}, Polska"
            print(f"    → геокодер вернул {geo_city!r} вместо {addr['city']!r}, пробуем только город: {city_query}")
            new_lat, new_lon, formatted, geo_city, geo_district = geocode(city_query)
            if new_lat is None:
                overrides[lid] = {"skipped": "geocode_failed", "query": city_query, "addr": addr}
                time.sleep(0.3)
                continue

        # Отклоняем если geocoder вернул только страну (совсем нет детализации)
        formatted_parts = [p.strip() for p in (formatted or "").split(",")]
        if len(formatted_parts) <= 1:
            overrides[lid] = {"skipped": "geocode_too_vague", "query": query, "formatted": formatted, "addr": addr}
            print(f"    → слишком общий результат: {formatted}")
            time.sleep(0.3)
            continue

        orig_lat = float(r["lat"])
        orig_lon = float(r["lon"])
        dist_m = haversine(orig_lat, orig_lon, new_lat, new_lon) * 1000

        overrides[lid] = {
            "orig_lat": orig_lat, "orig_lon": orig_lon,
            "new_lat": new_lat, "new_lon": new_lon,
            "dist_m": round(dist_m),
            "query": query, "formatted": formatted, "addr": addr,
            "geo_city": geo_city, "geo_district": geo_district,
            "corrected": dist_m > 200,
        }

        if dist_m > 200:
            print(f"    ✓ Скорректировано на {dist_m:.0f}м: {query} → {formatted} (район: {geo_district}, город: {geo_city})")
            updated += 1
        else:
            print(f"    ≈ Совпадает (Δ{dist_m:.0f}м): {formatted}")

        time.sleep(1.0)  # Nominatim требует не более 1 req/s

    # Применяем корректировки к CSV + копируем drive_cache на новые ключи
    if updated > 0:
        drive_cache_file = DATA_DIR / "drive_cache.json"
        drive_cache = json.loads(drive_cache_file.read_text()) if drive_cache_file.exists() else {}
        drive_copied = 0

        fieldnames = rows[0].keys()
        for r in rows:
            ov = overrides.get(r["id"])
            if ov and ov.get("corrected"):
                old_key = f"{ov['orig_lat']},{ov['orig_lon']}"
                new_key = f"{ov['new_lat']},{ov['new_lon']}"
                # Копируем кэш дистанций со старых координат на новые — не пересчитываем
                if old_key in drive_cache and new_key not in drive_cache:
                    drive_cache[new_key] = drive_cache[old_key]
                    drive_copied += 1
                r["lat"] = ov["new_lat"]
                r["lon"] = ov["new_lon"]
                if ov.get("geo_district"):
                    r["district"] = ov["geo_district"]
                if ov.get("geo_city"):
                    r["city"] = ov["geo_city"]

        if drive_copied:
            drive_cache_file.write_text(json.dumps(drive_cache, ensure_ascii=False, indent=2))
            print(f"Скопировано кэшей дистанций: {drive_copied}")

        with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"\nОбновлено координат: {updated}")

    OVERRIDE_FILE.write_text(json.dumps(overrides, ensure_ascii=False, indent=2))
    print(f"Аудит → {OVERRIDE_FILE}")


if __name__ == "__main__":
    main()
