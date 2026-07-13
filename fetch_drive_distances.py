import csv, json, time
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote

HEADERS = {'User-Agent': 'Mozilla/5.0'}
OSRM = "http://router.project-osrm.org"
RATUSZ = (52.4082, 16.9335)

trams = json.loads(Path("tram_stops.json").read_text())
rails = json.loads(Path("rail_stations.json").read_text())
cache_file = Path("routes_cache.json")
cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}


def hav(a1, o1, a2, o2):
    R = 6371
    dl = radians(a2 - a1); do = radians(o2 - o1)
    a = sin(dl/2)**2 + cos(radians(a1)) * cos(radians(a2)) * sin(do/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def osrm_table(sources, destinations):
    """Один запрос → матрица расстояний (км). sources/destinations — списки (lat, lon)."""
    coords = sources + destinations
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in coords)
    src_idx = ";".join(str(i) for i in range(len(sources)))
    dst_idx = ";".join(str(i) for i in range(len(sources), len(coords)))
    url = f"{OSRM}/table/v1/driving/{coord_str}?sources={src_idx}&destinations={dst_idx}&annotations=distance"
    for attempt in range(3):
        try:
            d = json.loads(urlopen(Request(url, headers=HEADERS), timeout=30).read())
            if d.get("code") == "Ok":
                # distances[i][j] в метрах
                return [[v / 1000 if v else None for v in row] for row in d["distances"]]
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(2)
    return None


def osrm_single(lat1, lon1, lat2, lon2):
    url = f"{OSRM}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    for attempt in range(3):
        try:
            d = json.loads(urlopen(Request(url, headers=HEADERS), timeout=10).read())
            if d.get("code") == "Ok":
                return round(d["routes"][0]["distance"] / 1000, 2)
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1)
    return None


rows = list(csv.DictReader(open("listings_latest.csv", encoding="utf-8-sig")))

# Разделяем на те, что уже в кэше, и новые
new_rows = [r for r in rows if r.get("lat") and r.get("lon") and r["id"] not in cache]
print(f"Всего: {len(rows)}, новых для маршрутов: {len(new_rows)}")

if new_rows:
    sources = [(float(r["lat"]), float(r["lon"])) for r in new_rows]

    # Ближайшие трамваи и ж/д по прямой для кандидатов
    top_trams_per_row = [
        sorted(trams, key=lambda t: hav(*src, t["lat"], t["lon"]))[:5]
        for src in sources
    ]
    top_rails_per_row = [
        sorted(rails, key=lambda s: hav(*src, s["lat"], s["lon"]))[:3]
        for src in sources
    ]

    # Уникальные ж/д остановки
    all_rail_set = {(s["lat"], s["lon"], s["name"]) for ss in top_rails_per_row for s in ss}
    rail_list = list(all_rail_set)
    rail_idx = {(s[0], s[1]): i for i, s in enumerate(rail_list)}
    rail_dests = [(s[0], s[1]) for s in rail_list]

    # Батч-запросы (OSRM ограничен ~500 точек в URL)
    BATCH = 200

    def batch_table(sources_batch, destinations):
        """Дробим sources на части по BATCH."""
        result = [None] * len(sources_batch)
        for start in range(0, len(sources_batch), BATCH):
            chunk = sources_batch[start:start + BATCH]
            mat = osrm_table(chunk, destinations)
            if mat:
                for i, row in enumerate(mat):
                    result[start + i] = row
            time.sleep(0.3)
        return result

    print("  Считаем расстояния до ж/д…")
    rail_mat = batch_table(sources, rail_dests) if rail_dests else []

    print("  Считаем расстояния до ратуши…")
    ratusz_mat = batch_table(sources, [RATUSZ])

    for i, r in enumerate(new_rows):
        src = sources[i]
        entry = {}

        # Ратуша
        entry["drive_ratusz_km"] = (ratusz_mat[i][0] if ratusz_mat and ratusz_mat[i] else None) or ""

        # Трамваи — haversine (пешком, прямая линия достаточно точна)
        nearest = min(top_trams_per_row[i], key=lambda t: hav(*src, t["lat"], t["lon"]))
        entry["drive_tram_km"] = round(hav(*src, nearest["lat"], nearest["lon"]), 2)
        entry["drive_tram_name"] = nearest["name"]

        # Ж/д
        best_rail_d, best_rail_n = None, ""
        if rail_mat and rail_mat[i]:
            for s in top_rails_per_row[i]:
                j = rail_idx.get((s["lat"], s["lon"]))
                if j is not None and rail_mat[i][j] is not None:
                    d = rail_mat[i][j]
                    if best_rail_d is None or d < best_rail_d:
                        best_rail_d, best_rail_n = d, s["name"]
        entry["drive_rail_km"] = best_rail_d or ""
        entry["drive_rail_name"] = best_rail_n

        cache[r["id"]] = entry

    cache_file.write_text(json.dumps(cache))
    print(f"Кэш обновлён: {len(cache)} записей")

# Применяем кэш ко всем строкам
for r in rows:
    entry = cache.get(r["id"], {})
    for k in ["drive_ratusz_km", "drive_tram_km", "drive_tram_name", "drive_rail_km", "drive_rail_name"]:
        if k not in r or not r[k]:
            r[k] = entry.get(k, "")

fields = ["id","type","title","area_m2","rooms","floor","price_zl","price_per_m2",
          "street","district","city","project","lat","lon","dist_km","dist_tram","tram_name",
          "photo_url","drive_ratusz_km","drive_tram_km","drive_tram_name","drive_rail_km","drive_rail_name","url"]
with open("listings_latest.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
print("CSV обновлён")
