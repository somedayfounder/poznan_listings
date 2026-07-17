import csv, json
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from score import score_from_jsrow, DISTRICT_SCORES, _DEFAULT_DISTRICT_SCORE

trams = json.loads(Path("tram_stops.json").read_text())
stop_lines = json.loads(Path("stop_lines.json").read_text())
rails = json.loads(Path("rail_stations.json").read_text())

def hav(a1,o1,a2,o2):
    R=6371;dl=radians(a2-a1);do=radians(o2-o1)
    a=sin(dl/2)**2+cos(radians(a1))*cos(radians(a2))*sin(do/2)**2
    return R*2*atan2(sqrt(a),sqrt(1-a))

def fmt_d(d):
    return f"{int(d*1000)} м" if d < 1 else f"{d:.1f} км"

rows = list(csv.DictReader(open("listings_latest.csv", encoding="utf-8-sig")))

def v(r, k, t=str):
    val = r.get(k, "")
    if val == "" or val is None: return None
    try: return t(val)
    except: return None

js_rows = []
for r in rows:
    price = v(r, "price_zl", float)
    price_m2 = v(r, "price_per_m2", float)
    dist = v(r, "drive_ratusz_km", float) or v(r, "dist_km", float)
    area = v(r, "area_m2", float)
    rooms_raw = v(r, "rooms")
    rooms_num = None
    try:
        if rooms_raw and rooms_raw != "7+": rooms_num = int(rooms_raw)
        elif rooms_raw == "7+": rooms_num = 7
    except: pass
    dist_tram = v(r, "drive_tram_km", float)
    tram_name = r.get("drive_tram_name", "")
    dist_rail = v(r, "drive_rail_km", float)
    rail_name = r.get("drive_rail_name", "")

    tram_tip = rail_tip = None

    if r.get("lat") and r.get("lon"):
        lat, lon = float(r["lat"]), float(r["lon"])

        # тултип трамвая: ближайший по маршруту + ближайший на другой ветке
        if tram_name:
            lines1 = set(stop_lines.get(tram_name, []))
            tip_parts = [f"{tram_name} — {fmt_d(dist_tram)} по дороге (линии: {', '.join(sorted(lines1)) if lines1 else '?'})"]
            ranked = sorted(trams, key=lambda t: hav(lat, lon, t["lat"], t["lon"]))
            for t in ranked:
                if t["name"] == tram_name:
                    continue
                lines2 = set(stop_lines.get(t["name"], []))
                if lines2 and lines1 and not lines1.intersection(lines2):
                    d2 = round(hav(lat, lon, t["lat"], t["lon"]), 2)
                    tip_parts.append(f"{t['name']} — ~{fmt_d(d2)} прямая (линии: {', '.join(sorted(lines2))})")
                    break
            tram_tip = "\n".join(tip_parts)

        if rail_name:
            rail_tip = f"{rail_name} — {fmt_d(dist_rail)} по дороге"

    row = {
        "id": r["id"], "type": r["type"], "title": r["title"],
        "area": area, "rooms": rooms_num, "floor": v(r, "floor"),
        "price": int(price) if price else None,
        "price_m2": int(price_m2) if price_m2 else None,
        "street": r["street"], "city": r["city"], "project": r["project"],
        "district": r.get("district", ""),
        "dist": dist, "dist_tram": dist_tram, "tram_tip": tram_tip,
        "dist_rail": dist_rail, "rail_tip": rail_tip, "url": r["url"],
    }
    row["score"] = score_from_jsrow(row)
    d = row.get("district", "") or row.get("city", "")
    row["district_score"] = DISTRICT_SCORES.get(d, DISTRICT_SCORES.get(row.get("city",""), _DEFAULT_DISTRICT_SCORE))
    js_rows.append(row)

mx_dist = max((r["dist"] for r in js_rows if r["dist"]), default=60)
total = len(js_rows)
data_json = json.dumps(js_rows, ensure_ascii=False)

html = open("listings_template.html").read()
html = html.replace("__DATA__", data_json).replace("__MX__", str(mx_dist)).replace("__TOTAL__", str(total))

with open("listings_poznan.html", "w", encoding="utf-8") as f:
    f.write(html)
print(f"OK: listings_poznan.html ({total} строк)")
