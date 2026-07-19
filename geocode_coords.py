#!/usr/bin/env python3
"""
Уточняет координаты новых объявлений:
1. Читает описание со страницы объявления
2. GPT извлекает фактический адрес объекта
3. Google Geocoding API возвращает точные координаты
4. Обновляет listings_latest.csv; аудит сохраняется в coords_override.json
"""
import csv, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote_plus
from math import radians, sin, cos, sqrt, atan2

DATA_DIR = Path(__file__).parent
CSV_FILE = DATA_DIR / "listings_latest.csv"
OVERRIDE_FILE = DATA_DIR / "coords_override.json"
GPT_TOKEN = os.environ.get("GPT_TOKEN", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

GPT_PROMPT = """Из описания польского объявления о недвижимости извлеки фактический адрес объекта.
Важно: адрес объекта (где он физически расположен), а не офиса застройщика.

Описание:
{description}

Верни ТОЛЬКО JSON (без пояснений):
{{
  "street": "название улицы (ul. X) или null если не указана явно",
  "osiedle": "название жилого комплекса/осьедля или null",
  "city": "город (обычно Poznań или пригород)",
  "district": "район/dzielnica если указан или null",
  "confidence": "high/medium/low — насколько уверен в адресе"
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


def geocode(query):
    if not GOOGLE_API_KEY:
        return None, None, None, None, None
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urlencode({
        "address": query,
        "key": GOOGLE_API_KEY,
        "language": "pl",
        "region": "pl",
        "components": "country:PL",
    })
    try:
        resp = json.loads(urlopen(url, timeout=10).read())
        if resp.get("status") == "OK" and resp["results"]:
            r = resp["results"][0]
            loc = r["geometry"]["location"]
            comps = {c["types"][0]: c["long_name"] for c in r["address_components"] if c["types"]}
            city = comps.get("locality") or comps.get("administrative_area_level_2")
            district = (comps.get("sublocality_level_1") or comps.get("sublocality") or
                        comps.get("neighborhood") or comps.get("sublocality_level_2"))
            return round(loc["lat"], 6), round(loc["lng"], 6), r["formatted_address"], city, district
    except Exception as e:
        print(f"    geocode error: {e}")
    return None, None, None, None, None


def build_query(addr):
    """Строим поисковый запрос для геокодирования."""
    city = addr.get("city") or "Poznań"
    street = addr.get("street")
    osiedle = addr.get("osiedle")
    district = addr.get("district")
    if street:
        return f"{street}, {city}, Polska"
    if osiedle:
        return f"{osiedle}, {city}, Polska"
    if district:
        return f"{district}, {city}, Polska"
    return None


def main():
    if not GPT_TOKEN:
        print("GPT_TOKEN не задан — пропускаем geocode_coords")
        return
    if not GOOGLE_API_KEY:
        print("GOOGLE_API_KEY не задан — пропускаем geocode_coords")
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
            time.sleep(0.3)
            continue

        new_lat, new_lon, formatted, geo_city, geo_district = geocode(query)
        if new_lat is None:
            overrides[lid] = {"skipped": "geocode_failed", "query": query, "addr": addr}
            print(f"    → геокодинг не дал результата: {query}")
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

        time.sleep(0.3)

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
