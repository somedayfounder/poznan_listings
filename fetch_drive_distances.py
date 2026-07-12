import csv, json, time
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from urllib.request import Request, urlopen

HEADERS = {'User-Agent': 'Mozilla/5.0'}
trams = json.loads(Path("tram_stops.json").read_text())
rails = json.loads(Path("rail_stations.json").read_text())

def hav(a1,o1,a2,o2):
    R=6371; dl=radians(a2-a1); do=radians(o2-o1)
    a=sin(dl/2)**2+cos(radians(a1))*cos(radians(a2))*sin(do/2)**2
    return R*2*atan2(sqrt(a),sqrt(1-a))

def osrm_drive(lat1, lon1, lat2, lon2):
    url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
    for attempt in range(3):
        try:
            req = Request(url, headers=HEADERS)
            d = json.loads(urlopen(req, timeout=10).read())
            if d.get("code") == "Ok":
                return round(d["routes"][0]["distance"] / 1000, 2)
        except Exception as e:
            if attempt == 2:
                return None
            time.sleep(1)
    return None

rows = list(csv.DictReader(open("listings_latest.csv", encoding="utf-8-sig")))
total = len(rows)
done = 0

for r in rows:
    if not r.get("lat") or not r.get("lon"):
        r["drive_tram_km"] = ""
        r["drive_tram_name"] = ""
        r["drive_rail_km"] = ""
        r["drive_rail_name"] = ""
        continue

    lat, lon = float(r["lat"]), float(r["lon"])

    # топ-5 трамваев по прямой → берём минимум по маршруту
    top_trams = sorted(trams, key=lambda t: hav(lat, lon, t["lat"], t["lon"]))[:5]
    best_tram = min(
        ((osrm_drive(lat, lon, t["lat"], t["lon"]), t["name"]) for t in top_trams),
        key=lambda x: x[0] if x[0] is not None else 999
    )
    r["drive_tram_km"] = best_tram[0] if best_tram[0] else ""
    r["drive_tram_name"] = best_tram[1] if best_tram[0] else ""

    # топ-3 ж/д по прямой → минимум по маршруту
    top_rails = sorted(rails, key=lambda s: hav(lat, lon, s["lat"], s["lon"]))[:3]
    best_rail = min(
        ((osrm_drive(lat, lon, s["lat"], s["lon"]), s["name"]) for s in top_rails),
        key=lambda x: x[0] if x[0] is not None else 999
    )
    r["drive_rail_km"] = best_rail[0] if best_rail[0] else ""
    r["drive_rail_name"] = best_rail[1] if best_rail[0] else ""

    done += 1
    if done % 50 == 0:
        print(f"  {done}/{total}")
    time.sleep(0.15)

print(f"Готово: {done}/{total}")

fields = ["id","type","title","area_m2","rooms","floor","price_zl","price_per_m2",
          "street","district","city","project","lat","lon","dist_km","dist_tram","tram_name",
          "drive_tram_km","drive_tram_name","drive_rail_km","drive_rail_name","url"]
with open("listings_latest.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
print("CSV обновлён")
# --- добавляем drive_ratusz_km ---
