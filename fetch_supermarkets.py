#!/usr/bin/env python3
"""
Fetches supermarkets/hypermarkets from Overpass API for the Poznań metro area.
For each listing in listings_latest.csv finds the nearest store and distance.
Saves to supermarkets_cache.json:
  { "<lat>,<lon>": {"name": "Biedronka", "dist_km": 0.3, "type": "supermarket"} }
Also saves raw store list to supermarkets_raw.json.
"""
import json, csv, urllib.request, urllib.parse, math
from pathlib import Path

CACHE_FILE = Path("supermarkets_cache.json")
RAW_FILE   = Path("supermarkets_raw.json")
CSV_FILE   = Path("listings_latest.csv")

# Poznań metro bounding box (south, west, north, east)
BBOX = "51.9,16.6,52.6,17.2"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

QUERY = f"""
[out:json][timeout:60];
(
  node["shop"~"supermarket|hypermarket"]({BBOX});
  way["shop"~"supermarket|hypermarket"]({BBOX});
);
out center;
"""

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = math.radians(lat2 - lat1)
    do = math.radians(lon2 - lon1)
    a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(do/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def fetch_stores():
    print("Запрашиваю Overpass API...")
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data,
                                  headers={"User-Agent": "poznan-listings/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        result = json.loads(r.read())

    stores = []
    for el in result.get("elements", []):
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not lat or not lon:
            continue
        tags = el.get("tags", {})
        stores.append({
            "name": tags.get("name", tags.get("brand", "?")),
            "brand": tags.get("brand", ""),
            "type": tags.get("shop", "supermarket"),
            "lat": lat,
            "lon": lon,
        })
    print(f"Найдено магазинов: {len(stores)}")
    return stores

HYPER_BRANDS = {'auchan','carrefour','e.leclerc','leclerc','kaufland','makro','real','selgros'}
SUPER_BRANDS  = {'lidl','netto','intermarché','intermarche','spar','piotr i paweł','alma','stokrotka'}

def store_tier(s):
    brand = (s.get("brand") or s.get("name") or "").lower()
    if s.get("type") == "hypermarket" or any(b in brand for b in HYPER_BRANDS): return "hyper"
    if any(b in brand for b in SUPER_BRANDS): return "super"
    return "discount"

def main():
    # Load or fetch stores
    if RAW_FILE.exists():
        print(f"Загружаю кеш {RAW_FILE}")
        stores = json.loads(RAW_FILE.read_text())
    else:
        stores = fetch_stores()
        RAW_FILE.write_text(json.dumps(stores, ensure_ascii=False, indent=2))

    # Pre-bucket stores by tier
    by_tier = {"hyper": [], "super": [], "discount": []}
    for s in stores:
        by_tier[store_tier(s)].append(s)
    for t, lst in by_tier.items():
        print(f"  {t}: {len(lst)}")

    # Load listings
    listings = []
    with open(CSV_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                lat = float(row["lat"]); lon = float(row["lon"])
                listings.append((lat, lon))
            except (ValueError, KeyError):
                continue

    print(f"Пересчитываю кеш для {len(listings)} объявлений...")
    cache = {}
    for lat, lon in listings:
        key = f"{lat},{lon}"
        entry = {}
        for tier, lst in by_tier.items():
            best = min(lst, key=lambda s: haversine(lat, lon, s["lat"], s["lon"]), default=None)
            if best:
                entry[tier] = {"name": best["name"], "brand": best.get("brand",""),
                               "dist_km": round(haversine(lat, lon, best["lat"], best["lon"]), 3)}
        cache[key] = entry

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    print(f"Всего в кеше: {len(cache)}")

    # Stats
    dists = [v["dist_km"] for v in cache.values() if v.get("dist_km") is not None]
    if dists:
        within_500 = sum(1 for d in dists if d <= 0.5)
        within_1k  = sum(1 for d in dists if d <= 1.0)
        print(f"\nСтатистика: ≤500м: {within_500}, ≤1км: {within_1k}, всего: {len(dists)}")
        print(f"Среднее: {sum(dists)/len(dists):.2f} км, макс: {max(dists):.2f} км")

    # Top brands
    from collections import Counter
    brands = Counter(v.get("brand") or v.get("name","?") for v in cache.values() if v.get("name"))
    print("\nТоп сетей:", brands.most_common(10))

if __name__ == "__main__":
    main()
