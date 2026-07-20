#!/usr/bin/env python3
"""
Сравнивает данные из Google cache с результатами OSRM для выборки объявлений.
Запуск: python3 compare_osrm_vs_google.py [N=20]
"""
import json, math, time, urllib.request, sys, random
from pathlib import Path

HEADERS     = {"User-Agent": "poznan-listings-bot/1.0"}
SLEEP       = 0.35
MAX_WALK_KM = 3.0
N           = int(sys.argv[1]) if len(sys.argv) > 1 else 20

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = math.radians(lat2 - lat1); do = math.radians(lon2 - lon1)
    a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(do/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def osrm_route(lat1, lon1, lat2, lon2, profile="driving"):
    url = f"http://router.project-osrm.org/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}?overview=false"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if data.get("code") == "Ok":
                r = data["routes"][0]
                return round(r["distance"]), round(r["duration"])
        except Exception:
            if attempt < 2: time.sleep(1)
    return None, None

cache  = json.loads(Path("drive_cache.json").read_text())
trams  = json.loads(Path("tram_stops.json").read_text())
rails  = json.loads(Path("rail_stations.json").read_text())

# берём только записи с полными Google-данными
complete = {k: v for k, v in cache.items()
            if v.get("tram_dur_s") and v.get("ratusz_dur_s") and v.get("rail_dur_s")}

sample = random.sample(list(complete.items()), min(N, len(complete)))
print(f"Сравниваем {len(sample)} объектов (Google cache vs OSRM)\n")
print(f"{'Ключ':<22} {'Метрика':<20} {'Google':>8} {'OSRM':>8} {'Δ%':>6}")
print("-" * 70)

diffs = []
for key, gdata in sample:
    lat, lon = map(float, key.split(","))
    ranked_trams = sorted(trams, key=lambda t: haversine(lat, lon, t["lat"], t["lon"]))[:5]
    ranked_rails = sorted(rails, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))[:3]

    # OSRM: лучший трамвай по drive
    best_tram_t, best_tram_w = None, None
    for stop in ranked_trams:
        _, t = osrm_route(lat, lon, stop["lat"], stop["lon"], "driving"); time.sleep(SLEEP)
        if t and (best_tram_t is None or t < best_tram_t):
            best_tram_t = t
        if haversine(lat, lon, stop["lat"], stop["lon"]) <= MAX_WALK_KM:
            _, w = osrm_route(lat, lon, stop["lat"], stop["lon"], "foot"); time.sleep(SLEEP)
            if w and (best_tram_w is None or w < best_tram_w):
                best_tram_w = w

    # OSRM: ратуш
    _, ratusz_t = osrm_route(lat, lon, 52.4082, 16.9335); time.sleep(SLEEP)

    # OSRM: лучшая жд
    best_rail_t = None
    for stop in ranked_rails:
        _, t = osrm_route(lat, lon, stop["lat"], stop["lon"], "driving"); time.sleep(SLEEP)
        if t and (best_rail_t is None or t < best_rail_t):
            best_rail_t = t

    def pct(g, o):
        if g and o: return f"{(o-g)/g*100:+.0f}%"
        return "  —"

    def fmt(s): return f"{round(s/60)}м" if s else "—"

    short = key[:21]
    print(f"{short:<22} {'трамвай (авто)':<20} {fmt(gdata.get('tram_dur_s')):>8} {fmt(best_tram_t):>8} {pct(gdata.get('tram_dur_s'), best_tram_t):>6}")
    print(f"{'':<22} {'трамвай (пешком)':<20} {fmt(gdata.get('tram_walk_s')):>8} {fmt(best_tram_w):>8} {pct(gdata.get('tram_walk_s'), best_tram_w):>6}")
    print(f"{'':<22} {'ратуш (авто)':<20} {fmt(gdata.get('ratusz_dur_s')):>8} {fmt(ratusz_t):>8} {pct(gdata.get('ratusz_dur_s'), ratusz_t):>6}")
    print(f"{'':<22} {'жд (авто)':<20} {fmt(gdata.get('rail_dur_s')):>8} {fmt(best_rail_t):>8} {pct(gdata.get('rail_dur_s'), best_rail_t):>6}")
    print()

    for g, o in [(gdata.get('tram_dur_s'), best_tram_t),
                 (gdata.get('ratusz_dur_s'), ratusz_t),
                 (gdata.get('rail_dur_s'), best_rail_t)]:
        if g and o: diffs.append(abs(o - g) / g * 100)

if diffs:
    print(f"Средняя абсолютная погрешность OSRM vs Google: {sum(diffs)/len(diffs):.1f}%")
    print(f"Макс погрешность: {max(diffs):.1f}%")
