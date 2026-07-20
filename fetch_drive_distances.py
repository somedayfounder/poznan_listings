#!/usr/bin/env python3
"""
Fetches routing data:
  - driving: OSRM table API (1 request per listing, not N) — 10 tram + 3 rail + ratusz
  - walking: ORS matrix API (1 request per listing, not N) — 5 nearest tram + 1 rail
Saves to drive_cache.json.
"""
import json, csv, math, time, urllib.request, urllib.parse, os
from pathlib import Path

ORS_KEY    = os.environ.get("OPENROUTE_KEY", "")
HEADERS    = {"User-Agent": "poznan-listings-bot/1.0", "Content-Type": "application/json"}
RATUSZ     = (52.4082, 16.9335)
K_DRIVE    = 10   # tram candidates for driving
K_WALK     = 5    # tram candidates for walking
K_RAIL     = 5    # rail candidates for driving
MAX_WALK_KM    = 3.0
MAX_ORS_PER_RUN = 1800  # ORS counts matrix as sources×destinations

CACHE_FILE = Path("drive_cache.json")
TRAM_FILE  = Path("tram_stops.json")
RAIL_FILE  = Path("rail_stations.json")
CSV_FILE   = Path("listings_latest.csv")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = math.radians(lat2 - lat1); do = math.radians(lon2 - lon1)
    a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(do/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def osrm_table(src_lat, src_lon, destinations):
    """
    OSRM table API: 1 HTTP request for all destinations at once.
    destinations: list of (lat, lon)
    Returns list of (distance_m, duration_s) or (None, None) per destination.
    """
    coords = f"{src_lon},{src_lat};" + ";".join(f"{lon},{lat}" for lat, lon in destinations)
    url = f"http://router.project-osrm.org/table/v1/driving/{coords}?sources=0&annotations=duration,distance"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "poznan-listings-bot/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=20).read())
            if data.get("code") == "Ok":
                durs = data["durations"][0][1:]   # skip self (index 0)
                dists = data["distances"][0][1:]
                return [(round(d) if d else None, round(t) if t else None)
                        for d, t in zip(dists, durs)]
        except Exception:
            if attempt < 2: time.sleep(2)
    return [(None, None)] * len(destinations)

def ors_matrix(src_lat, src_lon, destinations):
    """
    ORS matrix API: 1 HTTP request for all walk destinations.
    destinations: list of (lat, lon)
    Returns list of (duration_s, distance_m) or (None, None) per destination.
    Note: counts as len(destinations) ORS quota requests.
    """
    if not ORS_KEY or not destinations:
        return [(None, None)] * len(destinations)
    locations = [[src_lon, src_lat]] + [[lon, lat] for lat, lon in destinations]
    payload = json.dumps({
        "locations": locations,
        "sources": [0],
        "metrics": ["duration", "distance"],
    }).encode()
    url = "https://api.openrouteservice.org/v2/matrix/foot-walking"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=payload, headers={
                "Authorization": ORS_KEY,
                "Content-Type": "application/json",
                "User-Agent": "poznan-listings-bot/1.0",
            })
            data = json.loads(urllib.request.urlopen(req, timeout=20).read())
            durs  = data["durations"][0][1:]   # skip self
            dists = data["distances"][0][1:]
            return [(round(t) if t is not None else None,
                     round(d) if d is not None else None)
                    for t, d in zip(durs, dists)]
        except Exception:
            if attempt < 2: time.sleep(2)
    return [(None, None)] * len(destinations)

def ors_walk(lat1, lon1, lat2, lon2):
    """Single ORS walk call (for rail). Returns (duration_s, distance_m) or (None, None)."""
    if not ORS_KEY:
        return None, None
    url = "https://api.openrouteservice.org/v2/directions/foot-walking?" + urllib.parse.urlencode({
        "api_key": ORS_KEY, "start": f"{lon1},{lat1}", "end": f"{lon2},{lat2}",
    })
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "poznan-listings-bot/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=20).read())
            seg = data["features"][0]["properties"]["segments"][0]
            return round(seg["duration"]), round(seg["distance"])
        except Exception:
            if attempt < 2: time.sleep(2)
    return None, None

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

    SCHEMA_V = 5  # bump: tram candidate km = OSRM drive distance (not haversine)

    def needs_update(e):
        if e.get("schema_v", 0) < SCHEMA_V:
            return True
        cands = e.get("tram_candidates") or []
        return (not e.get("tram_dur_s") or not cands or
                "walk_s" not in cands[0] or not e.get("rail_dur_s") or
                e.get("rail_walk_s") is None)

    todo = [l for l in listings if needs_update(cache.get(f"{l['lat']},{l['lon']}", {}))]
    print(f"Объявлений: {len(listings)}, в кеше: {len(cache)}, осталось: {len(todo)}")

    errors = 0
    ors_used = 0
    for i, listing in enumerate(todo):
        if ors_used >= MAX_ORS_PER_RUN:
            print(f"  Достигнут лимит ORS ({MAX_ORS_PER_RUN} запросов), остановка до следующего запуска")
            break
        lat, lon = listing["lat"], listing["lon"]
        key = f"{lat},{lon}"
        entry = cache.get(key, {})

        ranked_trams = sorted(trams, key=lambda t: haversine(lat, lon, t["lat"], t["lon"]))
        drive_trams  = ranked_trams[:K_DRIVE]
        walk_trams   = [t for t in ranked_trams[:K_WALK] if haversine(lat, lon, t["lat"], t["lon"]) <= MAX_WALK_KM]
        ranked_rails = sorted(rails, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))[:K_RAIL]

        try:
            # 1 OSRM request: 10 trams + ratusz + 3 rails
            all_drive_dests = [(t["lat"], t["lon"]) for t in drive_trams] + \
                              [RATUSZ] + \
                              [(r["lat"], r["lon"]) for r in ranked_rails]
            drive_results = osrm_table(lat, lon, all_drive_dests)
            time.sleep(0.4)

            tram_results  = drive_results[:K_DRIVE]
            ratusz_result = drive_results[K_DRIVE]
            rail_results  = drive_results[K_DRIVE + 1:]

            tram_candidates = []
            for stop, (d, t) in zip(drive_trams, tram_results):
                drive_km = round(d / 1000, 2) if d else round(haversine(lat, lon, stop["lat"], stop["lon"]), 2)
                tram_candidates.append({"name": stop["name"], "km": drive_km, "dur_s": t, "walk_s": None})

            # 1 ORS matrix request for all walk trams
            if walk_trams:
                walk_dests = [(t["lat"], t["lon"]) for t in walk_trams]
                walk_results = ors_matrix(lat, lon, walk_dests)
                ors_used += len(walk_trams)  # counts as N quota requests
                time.sleep(1.1)
                for stop, (w_dur, w_dist) in zip(walk_trams, walk_results):
                    cand = next((c for c in tram_candidates if c["name"] == stop["name"]), None)
                    if cand:
                        cand["walk_s"] = w_dur
                        cand["walk_dist_m"] = w_dist

            best_tram   = min((c for c in tram_candidates if c["dur_s"]), key=lambda c: c["dur_s"], default=None)
            tram_walk_s = min((c["walk_s"] for c in tram_candidates if c.get("walk_s")), default=None)

            ratusz_d, ratusz_t = ratusz_result

            rail_cands = []
            for stop, (d, t) in zip(ranked_rails, rail_results):
                rail_cands.append({"stop": stop, "d": d, "t": t})
            best_rail = min((c for c in rail_cands if c["t"]), key=lambda c: c["t"], default=None)

            # 1 ORS call for best rail walk
            rail_walk_s = rail_walk_dist_m = None
            if best_rail and haversine(lat, lon, best_rail["stop"]["lat"], best_rail["stop"]["lon"]) <= MAX_WALK_KM:
                rail_walk_s, rail_walk_dist_m = ors_walk(lat, lon, best_rail["stop"]["lat"], best_rail["stop"]["lon"])
                ors_used += 1
                time.sleep(1.1)

            entry.update({
                "schema_v":           SCHEMA_V,
                "tram_name":          best_tram["name"] if best_tram else None,
                "tram_km":            best_tram["km"] if best_tram else None,
                "tram_dur_s":         best_tram["dur_s"] if best_tram else None,
                "tram_walk_s":        tram_walk_s,
                "tram_candidates":    tram_candidates,
                "ratusz_km":          round(ratusz_d / 1000, 2) if ratusz_d else None,
                "ratusz_dur_s":       ratusz_t,
                "rail_name":          best_rail["stop"]["name"] if best_rail else None,
                "rail_km":            round(best_rail["d"] / 1000, 2) if best_rail and best_rail["d"] else None,
                "rail_dur_s":         best_rail["t"] if best_rail else None,
                "rail_walk_s":        rail_walk_s,
                "rail_walk_dist_m":   rail_walk_dist_m,
            })
            cache[key] = entry

        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] ERROR {key}: {e}")
            errors += 1
            continue

        if (i + 1) % 10 == 0 or i == len(todo) - 1:
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
            print(f"  [{i+1}/{len(todo)}] сохранено, ошибок: {errors}, ORS: {ors_used}")

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    filled = sum(1 for v in cache.values() if v.get("tram_km") is not None)
    print(f"\nГотово: {filled}/{len(cache)} с данными, ошибок: {errors}")

if __name__ == "__main__":
    main()
