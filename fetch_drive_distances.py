#!/usr/bin/env python3
"""
Fetches real driving distances from each listing to:
  1. Nearest tram stop (from tram_stops.json)
  2. City hall (Ratusz) at 52.4082, 16.9335
  3. Nearest rail station (from rail_stations.json)

Uses Google Distance Matrix API in batches (max 25 destinations per request).
Saves to drive_cache.json: { "lat,lon": { "tram_km": ..., "tram_name": ..., "tram_dur_s": ...,
                                           "ratusz_km": ..., "ratusz_dur_s": ...,
                                           "rail_name": ..., "rail_km": ..., "rail_dur_s": ... } }
"""
import json, csv, math, time, urllib.request, urllib.parse
from pathlib import Path

import os
API_KEY = os.environ.get("GOOGLE_API_KEY", "")
if not API_KEY:
    raise SystemExit("ERROR: GOOGLE_API_KEY не задан")
RATUSZ = (52.4082, 16.9335)
K = 15       # tram candidates by haversine
K_RAIL = 5   # rail candidates by haversine

CACHE_FILE  = Path("drive_cache.json")
TRAM_FILE   = Path("tram_stops.json")
RAIL_FILE   = Path("rail_stations.json")
CSV_FILE    = Path("listings_latest.csv")

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = math.radians(lat2 - lat1); do = math.radians(lon2 - lon1)
    a = math.sin(dl/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(do/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def distance_matrix(origins, destinations, mode="driving"):
    """origins/destinations: list of "lat,lon" strings. Returns (distances_m, durations_s) matrices."""
    url = "https://maps.googleapis.com/maps/api/distancematrix/json?" + urllib.parse.urlencode({
        "origins":      "|".join(origins),
        "destinations": "|".join(destinations),
        "mode":         mode,
        "key":          API_KEY,
    })
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read())
    if data.get("status") != "OK":
        raise RuntimeError(f"API error: {data.get('status')} {data.get('error_message','')}")
    dists, durs = [], []
    for row in data["rows"]:
        d_row, t_row = [], []
        for el in row["elements"]:
            if el.get("status") == "OK":
                d_row.append(el["distance"]["value"])
                t_row.append(el["duration"]["value"])
            else:
                d_row.append(None)
                t_row.append(None)
        dists.append(d_row)
        durs.append(t_row)
    return dists, durs

def main():
    trams = json.loads(TRAM_FILE.read_text())
    rails = json.loads(RAIL_FILE.read_text()) if RAIL_FILE.exists() else []

    # validate coords (Poznań: lon 16-18, lat 52-53)
    for t in trams:
        assert 52 <= t["lat"] <= 53 and 16 <= t["lon"] <= 18, f"Bad tram coords: {t}"

    listings = []
    with open(CSV_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                assert 52 <= lat <= 53 and 16 <= lon <= 18, f"Bad listing coords: {lat},{lon}"
                listings.append({"id": row["id"], "lat": lat, "lon": lon})
            except (ValueError, KeyError, AssertionError):
                continue

    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
    # re-process entries that are missing rail data
    def _needs_walk(entry):
        cands = entry.get("tram_candidates") or []
        return not cands or "walk_s" not in cands[0]

    todo = [l for l in listings if
            f"{l['lat']},{l['lon']}" not in cache or
            "rail_dur_s" not in cache.get(f"{l['lat']},{l['lon']}", {}) or
            "tram_candidates" not in cache.get(f"{l['lat']},{l['lon']}", {}) or
            _needs_walk(cache.get(f"{l['lat']},{l['lon']}", {}))]
    print(f"Объявлений: {len(listings)}, в кеше: {len(cache)}, осталось: {len(todo)}")

    errors = 0
    for i, listing in enumerate(todo):
        lat, lon = listing["lat"], listing["lon"]
        key = f"{lat},{lon}"

        # k nearest tram stops by haversine
        ranked = sorted(trams, key=lambda t: haversine(lat, lon, t["lat"], t["lon"]))[:K]
        # k nearest rail stations by haversine
        ranked_rail = sorted(rails, key=lambda r: haversine(lat, lon, r["lat"], r["lon"]))[:K_RAIL] if rails else []

        origin = f"{lat},{lon}"
        tram_dests = [f"{t['lat']},{t['lon']}" for t in ranked]
        ratusz_dest = f"{RATUSZ[0]},{RATUSZ[1]}"
        rail_dests  = [f"{r['lat']},{r['lon']}" for r in ranked_rail]

        # all destinations in one request: K trams + ratusz + K_RAIL rail (driving)
        all_dests = tram_dests + [ratusz_dest] + rail_dests
        try:
            dists, durs = distance_matrix([origin], all_dests)
            d_row = dists[0]; t_row = durs[0]
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] ERROR driving {key}: {e}")
            errors += 1
            time.sleep(2)
            continue

        # walking distances to tram stops
        try:
            _, walk_durs = distance_matrix([origin], tram_dests, mode="walking")
            walk_row = walk_durs[0]
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] WARN walking {key}: {e}")
            walk_row = [None] * K

        # find best tram by min drive time
        best_idx, best_d, best_t = None, None, None
        for j, (d, t) in enumerate(zip(d_row[:K], t_row[:K])):
            if t is not None and (best_t is None or t < best_t):
                best_d = d; best_t = t; best_idx = j

        # sanity check: road dist >= haversine
        if best_idx is not None:
            hav_d = haversine(lat, lon, ranked[best_idx]["lat"], ranked[best_idx]["lon"]) * 1000
            if best_d < hav_d * 0.9:
                print(f"  SANITY WARN {key}: road {best_d}m < haversine {hav_d:.0f}m")

        ratusz_d = d_row[K]
        ratusz_t = t_row[K]

        # find best rail station by min drive time
        rail_start = K + 1
        best_rail_idx, best_rail_d, best_rail_t = None, None, None
        for j, (d, t) in enumerate(zip(d_row[rail_start:rail_start+K_RAIL], t_row[rail_start:rail_start+K_RAIL])):
            if t is not None and (best_rail_t is None or t < best_rail_t):
                best_rail_d = d; best_rail_t = t; best_rail_idx = j

        # сохраняем все кандидаты трамваев с пешеходным временем
        tram_candidates = []
        for j, (d, t) in enumerate(zip(d_row[:K], t_row[:K])):
            if d is not None and t is not None:
                cand = {
                    "name": ranked[j]["name"],
                    "km": round(d / 1000, 2),
                    "dur_s": t,
                }
                wt = walk_row[j] if j < len(walk_row) else None
                if wt is not None:
                    cand["walk_s"] = wt
                tram_candidates.append(cand)

        tram_walk_s = walk_row[best_idx] if best_idx is not None and best_idx < len(walk_row) else None

        entry = cache.get(key, {})
        entry.update({
            "tram_name":       ranked[best_idx]["name"] if best_idx is not None else None,
            "tram_km":         round(best_d / 1000, 2) if best_d is not None else None,
            "tram_dur_s":      best_t,
            "tram_walk_s":     tram_walk_s,
            "tram_candidates": tram_candidates,
            "ratusz_km":       round(ratusz_d / 1000, 2) if ratusz_d is not None else None,
            "ratusz_dur_s":    ratusz_t,
            "rail_name":       ranked_rail[best_rail_idx]["name"] if best_rail_idx is not None else None,
            "rail_km":         round(best_rail_d / 1000, 2) if best_rail_d is not None else None,
            "rail_dur_s":      best_rail_t,
        })
        cache[key] = entry

        if (i + 1) % 10 == 0 or i == len(todo) - 1:
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
            print(f"  [{i+1}/{len(todo)}] сохранено, ошибок: {errors}")

        time.sleep(0.05)  # ~20 req/s, well within limits

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    filled = sum(1 for v in cache.values() if v.get("tram_km") is not None)
    print(f"\nГотово: {filled}/{len(cache)} с данными, ошибок: {errors}")
    print(f"Кеш → {CACHE_FILE}")

if __name__ == "__main__":
    main()
