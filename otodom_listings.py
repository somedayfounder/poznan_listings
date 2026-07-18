#!/usr/bin/env python3
"""
Otodom — квартиры и дома, первичный рынок, от 80 м², до 1 400 000 zł.
Познань (город) + познаньский повят.

Запуск:
  python3 otodom_listings.py
"""

import csv
import json
import os
import re
import time
from datetime import date
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _tg_safe(text):
    token = os.environ.get("TG_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        data = urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": "true"}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=data), timeout=10)
    except Exception as e:
        print(f"TG error: {e}")

BASE = "https://www.otodom.pl"
DATA_DIR = Path(__file__).parent
FILTERS = "areaMin=70&areaMax=120&priceMin=600000&priceMax=1200000"

SEARCH_URLS = [
    f"{BASE}/pl/wyniki/sprzedaz/mieszkanie,rynek-pierwotny/wielkopolskie/poznan/poznan/poznan?{FILTERS}",
    f"{BASE}/pl/wyniki/sprzedaz/mieszkanie,rynek-pierwotny/wielkopolskie/poznanski?{FILTERS}",
    f"{BASE}/pl/wyniki/sprzedaz/dom,rynek-pierwotny/wielkopolskie/poznan/poznan/poznan?{FILTERS}",
    f"{BASE}/pl/wyniki/sprzedaz/dom,rynek-pierwotny/wielkopolskie/poznanski?{FILTERS}",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

ROOMS_MAP = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4,
    "FIVE": 5, "SIX": 6, "SEVEN": 7, "MORE": "7+",
}

FLOOR_MAP = {
    "GROUND": 0, "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4,
    "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9,
    "TENTH": 10, "ABOVE_TENTH": "10+", "GARRET": "poddasze",
    "BASEMENT": "piwnica",
}

# Населённые пункты за пределами интереса (слишком далеко / не тот район)
EXCLUDED_CITIES = {"Komorniki", "Plewiska", "Robakowo", "Nowinki", "Wierzyce",
                   "Dachowa", "Rokietnica", "Murowana Goślina", "Bolechowo",
                   "Swarzędz", "Mosina", "Luboń", "Czerwonak"}

# Отдельные НП, которые на самом деле внутри Познани
POZNAN_SUBURBS = {"Smochowice", "Naramowice", "Strzeszyn", "Morasko",
                  "Szczepankowo", "Spławie", "Głuszyna", "Fabianowo"}

RATUSZ = (52.40832, 16.93361)
TRAM_STOPS_FILE = DATA_DIR / "tram_stops.json"
TRAMS = json.loads(TRAM_STOPS_FILE.read_text()) if TRAM_STOPS_FILE.exists() else []


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def fetch_page(url):
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="replace")
    m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError(f"No __NEXT_DATA__ at {url}")
    pp = json.loads(m.group(1))["props"]["pageProps"]
    sa = (pp.get("data") or {}).get("searchAds") or pp.get("searchAds") or {}
    return sa


def parse_item(item, estate_type, trams):
    addr = (item.get("location") or {}).get("address") or {}
    street = (addr.get("street") or {}).get("name", "")
    city = (addr.get("city") or {}).get("name", "")

    rev = ((item.get("location") or {}).get("reverseGeocoding") or {}).get("locations", [])
    district = ""
    if len(rev) >= 2:
        district = rev[-1].get("fullName", "").split(",")[0].strip()

    # НП внутри Познани, которые Otodom отдаёт как отдельный город
    if city in POZNAN_SUBURBS:
        district = city
        city = "Poznań"

    # координаты прямо из otodom
    coords = (item.get("location") or {}).get("coordinates") or {}
    lat = coords.get("latitude")
    lon = coords.get("longitude")

    dist_km = dist_tram = tram_name = None
    if lat and lon:
        dist_km = round(haversine(lat, lon, *RATUSZ), 1)
        if trams:
            nearest = min(trams, key=lambda t: haversine(lat, lon, t["lat"], t["lon"]))
            dist_tram = round(haversine(lat, lon, nearest["lat"], nearest["lon"]), 2)
            tram_name = nearest["name"]

    price = (item.get("totalPrice") or {}).get("value")
    price_m2 = (item.get("pricePerSquareMeter") or {}).get("value")
    rooms_raw = item.get("roomsNumber")
    floor_raw = item.get("floorNumber")

    return {
        "id": item["id"],
        "type": estate_type,
        "title": item.get("title", ""),
        "area_m2": item.get("areaInSquareMeters"),
        "rooms": ROOMS_MAP.get(rooms_raw, rooms_raw or ""),
        "floor": FLOOR_MAP.get(floor_raw, floor_raw or ""),
        "price_zl": price,
        "price_per_m2": round(price_m2) if price_m2 else None,
        "street": street,
        "district": district,
        "city": city,
        "project": item.get("developmentTitle", ""),
        "lat": round(lat, 5) if lat else None,
        "lon": round(lon, 5) if lon else None,
        "dist_km": dist_km,
        "dist_tram": dist_tram,
        "tram_name": tram_name,
        "url": f"{BASE}/pl/oferta/{item.get('slug', '')}",
    }


def get_all_listings():
    results = []
    seen_ids = set()

    type_map = {
        "mieszkanie,rynek-pierwotny": "mieszkanie",
        "dom,rynek-pierwotny": "dom",
    }

    for search_url in SEARCH_URLS:
        segment = search_url.split("/sprzedaz/")[1].split("/")[0]
        estate_type = type_map.get(segment, segment)
        region = "poznan" if "poznan/poznan/poznan" in search_url else "poznanski"
        label = f"{estate_type}/{region}"

        page = 1
        print(f"\n  [{label}]")
        while True:
            sep = "&" if "?" in search_url else "?"
            url = f"{search_url}{sep}page={page}"
            sa = None
            for attempt in range(3):
                try:
                    sa = fetch_page(url)
                    break
                except Exception as e:
                    print(f"    attempt {attempt+1} ERROR: {e}")
                    time.sleep(2)
            if sa is None:
                print("    SKIP")
                break

            items = sa.get("items", [])
            pagination = sa.get("pagination", {})
            total_pages = pagination.get("totalPages", 1)
            total_items = pagination.get("totalItems", "?")

            new_on_page = 0
            for item in items:
                if item["id"] in seen_ids:
                    continue
                if item.get("estate") == "INVESTMENT":
                    continue
                parsed = parse_item(item, estate_type, TRAMS)
                if parsed["city"] in EXCLUDED_CITIES:
                    continue
                seen_ids.add(item["id"])
                results.append(parsed)
                new_on_page += 1
                if len(results) % 100 == 0:
                    _tg_safe(f"🔄 Парсинг: собрано {len(results)} объявлений…")

            print(f"    page {page}/{total_pages} — {new_on_page} new (total={total_items})")

            if page >= total_pages or not items:
                break
            page += 1
            time.sleep(0.5)

    return results


FIELDS = [
    "id", "type", "title", "area_m2", "rooms", "floor",
    "price_zl", "price_per_m2", "street", "district", "city",
    "project", "lat", "lon", "dist_km", "dist_tram", "tram_name", "url",
]


def save_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Сохранено {len(rows)} строк → {path.name}")


def main():
    today = date.today().isoformat()
    print(f"\n=== Otodom listings — {today} ===")
    print(f"Фильтр: от 80 м², до 1 400 000 zł, первичный рынок\n")

    print(f"Трамвайные остановки: {len(TRAMS)}")
    print("Собираем объявления...")
    rows = get_all_listings()
    print(f"\nВсего собрано: {len(rows)}")

    rows.sort(key=lambda r: (r["dist_km"] or 999, r["price_zl"] or 0))

    out = DATA_DIR / f"listings_{today}.csv"
    save_csv(out, rows)
    save_csv(DATA_DIR / "listings_latest.csv", rows)


if __name__ == "__main__":
    main()
