#!/usr/bin/env python3
"""
Fetches routing data:
  - driving: OSRM (free, no key) — 10 tram + 3 rail + ratusz
  - walking: OpenRouteService foot-walking (OPENROUTE_KEY) — 5 nearest tram + 1 rail
Saves to drive_cache.json.
"""
import json, csv, math, time, urllib.request, urllib.parse, os
from pathlib import Path

ORS_KEY    = os.environ.get("OPENROUTE_KEY", "")
HEADERS    = {"User-Agent": "poznan-listings-bot/1.0"}
RATUSZ     = (52.4082, 16.9335)
K_DRIVE    = 10   # tram candidates for driving
K_WALK     = 5    # tram candidates for walking
K_RAIL     = 3    # rail candidates for driving
MAX_WALK_KM = 3.0 # не считаем пешком если дальше

CACHE_FILE = Path("drive_cache.json")
TRAM_FILE  = Path("tram_stops.json")
RAIL_FILE  = Path("rail_stations.json")
CSV_FILE   = Path("listings_latest.csv")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = math.radians(lat2 - lat1); do = math.radians(lon2 - lon1)
    a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(do/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def osrm_route(lat1, lon1, lat2, lon2):
    """OSRM driving. Returns (distance_m, duration_s) or (None, None)."""
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
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

def ors_walk(lat1, lon1, lat2, lon2):
    """OpenRouteService foot-walking. Returns duration_s or None."""
    if not ORS_KEY:
        return None
    url = "https://api.openrouteservice.org/v2/directions/foot-walking?" + urllib.parse.urlencode({
        "api_key": ORS_KEY,
        "start": f"{lon1},{lat1}",
        "end":   f"{lon2},{lat2}",
    })
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            seg = data["features"][0]["properties"]["segments"][0]
            return round(seg["duration"])
        except Exception:
            if attempt < 2: time.sleep(1)
    return None

def main():
    if not ORS_KEY:
        raise SystemExit("ERROR: OPENROUTE_KEY не задан")

    trams = json.loads(TRAM_FILE.read_text())
    rails = json.loads(RAIL_FILE.read_text()) if RAIL_FILE.exists() else []

    listings = []
    with open(CSV_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                assert 52 <= lat <= 53 and 16 <= lon <= 18
                listings.append({"id": row["id"], "lat": lat, "lon": lon})
            except (ValueError, KeyError, AssertionError):
                continue

    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}

    def needs_update(e):
        cands = e.get("tram_candidates") or []
        return (not e.get("tram_dur_s") or
                not cands or
                "walk_s" not in cands[0] or
                not e.get("rail_dur_s") or
                e.get("rail_walk_s") is None)

    todo = [l for l in listings if needs_update(cache.get(f"{l['lat']},{l['lon']}", {}))]
    print(f"Объявлений: {len(listings)}, в кеше: {len(cache)}, осталось: {len(todo)}")

    errors = 0
    for i, listing in enumerate(todo):
        lat, lon = listing["lat"], listing["lon"]
        key = f"{lat},{lon}"
        entry = cache.get(key, {})

        ranked_trams = sorted(trams, key=lambda t: haversine(lat, lon, t["lat"], t["lon"]))
        drive_trams  = ranked_trams[:K_DRIVE]
        walk_trams   = [t for t in ranked_trams[:K_WALK] if haversine(lat, lon, t["lat"], t["lon"]) <= MAX_WALK_KM]
        ranked_rails = sorted(rails, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))[:K_RAIL]

        try:
            # driving до трамваев
            tram_candidates = []
            for stop in drive_trams:
                hav_km = round(haversine(lat, lon, stop["lat"], stop["lon"]), 2)
                d, t = osrm_route(lat, lon, stop["lat"], stop["lon"]); time.sleep(0.35)
                tram_candidates.append({"name": stop["name"], "km": hav_km, "dur_s": t, "walk_s": None})

            # walking до ближайших трамваев через ORS
            for stop in walk_trams:
                w = ors_walk(lat, lon, stop["lat"], stop["lon"]); time.sleep(1.1)
                cand = next((c for c in tram_candidates if c["name"] == stop["name"]), None)
                if cand:
                    cand["walk_s"] = w

            best_tram  = min((c for c in tram_candidates if c["dur_s"]), key=lambda c: c["dur_s"], default=None)
            tram_walk_s = min((c["walk_s"] for c in tram_candidates if c.get("walk_s")), default=None)

            # ратуш
            ratusz_d, ratusz_t = osrm_route(lat, lon, *RATUSZ); time.sleep(0.35)

            # driving до жд
            rail_cands = []
            for stop in ranked_rails:
                d, t = osrm_route(lat, lon, stop["lat"], stop["lon"]); time.sleep(0.35)
                rail_cands.append({"stop": stop, "d": d, "t": t})

            best_rail = min((c for c in rail_cands if c["t"]), key=lambda c: c["t"], default=None)

            # walking до лучшей жд
            rail_walk_s = None
            if best_rail and haversine(lat, lon, best_rail["stop"]["lat"], best_rail["stop"]["lon"]) <= MAX_WALK_KM:
                rail_walk_s = ors_walk(lat, lon, best_rail["stop"]["lat"], best_rail["stop"]["lon"])
                time.sleep(1.1)

            entry.update({
                "tram_name":       best_tram["name"] if best_tram else None,
                "tram_km":         best_tram["km"] if best_tram else None,
                "tram_dur_s":      best_tram["dur_s"] if best_tram else None,
                "tram_walk_s":     tram_walk_s,
                "tram_candidates": tram_candidates,
                "ratusz_km":       round(ratusz_d / 1000, 2) if ratusz_d else None,
                "ratusz_dur_s":    ratusz_t,
                "rail_name":       best_rail["stop"]["name"] if best_rail else None,
                "rail_km":         round(best_rail["d"] / 1000, 2) if best_rail and best_rail["d"] else None,
                "rail_dur_s":      best_rail["t"] if best_rail else None,
                "rail_walk_s":     rail_walk_s,
            })
            cache[key] = entry

        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] ERROR {key}: {e}")
            errors += 1
            continue

        if (i + 1) % 10 == 0 or i == len(todo) - 1:
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
            print(f"  [{i+1}/{len(todo)}] сохранено, ошибок: {errors}")

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    filled = sum(1 for v in cache.values() if v.get("tram_km") is not None)
    print(f"\nГотово: {filled}/{len(cache)} с данными, ошибок: {errors}")

if __name__ == "__main__":
    main()
