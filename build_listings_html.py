import csv, json
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from score import score_from_jsrow, DISTRICT_SCORES, _DEFAULT_DISTRICT_SCORE, DISTRICT_DESCRIPTIONS, DISTRICT_SUMMARIES, DISTRICT_PROS, DISTRICT_CONS, _nuisance_penalty, _NUISANCE_SITES, _haversine, _noise_penalty

_RESCORE_FILE   = Path(__file__).parent.parent / "rescore_results.json"
_RESIDENT_FILE  = Path(__file__).parent.parent / "resident_scores.json"
_rescore   = json.loads(_RESCORE_FILE.read_text())  if _RESCORE_FILE.exists()  else {}
_residents = json.loads(_RESIDENT_FILE.read_text()) if _RESIDENT_FILE.exists() else {}
from extract_features import feature_bonus, CACHE_FILE as FEAT_CACHE
import json as _json
_feat_cache = _json.loads(FEAT_CACHE.read_text()) if FEAT_CACHE.exists() else {}
_SUPER_CACHE_FILE = Path("supermarkets_cache.json")
_super_cache = _json.loads(_SUPER_CACHE_FILE.read_text()) if _SUPER_CACHE_FILE.exists() else {}
_NOISE_CACHE_FILE = Path("noise_cache.json")
_noise_cache = _json.loads(_NOISE_CACHE_FILE.read_text()) if _NOISE_CACHE_FILE.exists() else {}

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

    lat = v(r, "lat", float)
    lon = v(r, "lon", float)
    row = {
        "id": r["id"], "type": r["type"], "title": r["title"],
        "area": area, "rooms": rooms_num, "floor": v(r, "floor"),
        "price": int(price) if price else None,
        "price_m2": int(price_m2) if price_m2 else None,
        "street": r["street"], "city": r["city"], "project": r["project"],
        "district": r.get("district", ""),
        "dist": dist, "dist_tram": dist_tram, "tram_tip": tram_tip,
        "dist_rail": dist_rail, "rail_tip": rail_tip, "url": r["url"],
        "lat": lat, "lon": lon,
    }
    feat = _feat_cache.get(r["id"], {})
    bonus = feat.get("_bonus", 0.0)
    super_key = f"{lat},{lon}" if lat and lon else None
    super_info = _super_cache.get(super_key, {}) if super_key else {}
    row["supermarket"] = super_info
    # nuisance: list of nearby problem sites for tooltip
    nuisance = []
    if lat and lon:
        for slat, slon, radius, penalty, name in _NUISANCE_SITES:
            dist_n = _haversine(lat, lon, slat, slon)
            if dist_n < radius:
                pen = round(penalty * (1 - dist_n / radius), 2)
                nuisance.append({"name": name, "dist_km": round(dist_n, 1), "penalty": pen})
    row["nuisance"] = nuisance
    noise = _noise_cache.get(f"{r.get('lat')},{r.get('lon')}", {}) if r.get("lat") and r.get("lon") else {}
    row["noise"] = noise
    base_score = score_from_jsrow(row)
    row["score"] = round(base_score, 1)
    row["base_score"] = round(base_score, 1)
    row["bonus"] = round(bonus, 2)
    row["features"] = {k: v for k, v in feat.items() if k != "_bonus"}
    d = row.get("district", "") or row.get("city", "")
    row["district_score"] = DISTRICT_SCORES.get(d, DISTRICT_SCORES.get(row.get("city",""), _DEFAULT_DISTRICT_SCORE))
    js_rows.append(row)

mx_dist = max((r["dist"] for r in js_rows if r["dist"]), default=60)
total = len(js_rows)
data_json = json.dumps(js_rows, ensure_ascii=False)

# Aggregate noise, nuisance and supermarket distances per district
from collections import defaultdict
_dist_noise = defaultdict(list)   # district -> [max_ldwn, ...]
_dist_nuisance = defaultdict(set) # district -> {nuisance_name, ...}
_dist_super = defaultdict(list)   # district -> [dist_km, ...]
for row in js_rows:
    d = row.get("district") or row.get("city") or ""
    if not d:
        continue
    if row.get("noise"):
        _dist_noise[d].append(max(row["noise"].values()))
    for n in (row.get("nuisance") or []):
        _dist_nuisance[d].add(n["name"])
    sup_dist = (row.get("supermarket") or {}).get("dist_km")
    if sup_dist is not None:
        _dist_super[d].append(sup_dist)

def _super_bonus(dists):
    if not dists:
        return 0.0, None
    mn = min(dists)
    if mn <= 0.5:   return 0.3, f"🛒 Супермаркет ≤500м (+0.3)"
    if mn <= 1.0:   return 0.2, f"🛒 Супермаркет ≤1км (+0.2)"
    if mn <= 2.0:   return 0.1, f"🛒 Супермаркет ≤2км (+0.1)"
    return 0.0, None

# Noise label thresholds (Lden dBA)
def _noise_tag(vals):
    if not vals:
        return None
    mx = max(vals)
    if mx >= 70: return ("🔊", f"Шум ≥70 дБА (GEOPOZ 2017)", "#991b1b")
    if mx >= 65: return ("🔊", f"Шум 65–70 дБА (GEOPOZ 2017)", "#c2410c")
    if mx >= 60: return ("🔊", f"Шум 60–65 дБА (GEOPOZ 2017)", "#854d0e")
    return None

# Nuisance short labels
_NUISANCE_SHORT = {
    "ITPOK Spalarnia": "🔥 Мусоросжигание",
    "EC Karolin (Veolia)": "🏭 Теплоэлектроцентраль",
    "Oczyszczalnia LOŚ Serbska (Aquanet)": "💧 Очистные сооружения",
    "Oczyszczalnia COŚ Koziegłowy (Aquanet)": "💧 Очистные сооружения",
    "Składowisko ZZO Suchy Las": "🗑 Полигон ТБО",
    "VW Antoninek (fabryka)": "🏭 Завод VW",
    "VW Odlewnia Wilda": "🏭 Литейный VW",
    "Luvena SA Luboń (zakład chemiczny)": "⚗️ Хим. завод",
}

# Build districts list sorted by score descending
districts_list = []
for k, v in DISTRICT_SCORES.items():
    noise_tag = _noise_tag(_dist_noise.get(k, []))
    nuisance_names = _dist_nuisance.get(k, set())
    nuisance_tags = [_NUISANCE_SHORT.get(n, n) for n in sorted(nuisance_names)]
    rs  = _rescore.get(k, {})
    res = _residents.get(k, {})
    # normalize resident rating to 1-10 scale
    r_rating = res.get("rating")
    r_scale  = res.get("scale") or 5
    r_norm   = round(r_rating / r_scale * 10, 1) if r_rating else None
    s_bonus, s_tag = _super_bonus(_dist_super.get(k, []))
    m = rs.get("manual"); g = rs.get("gpt")
    components = [x for x in [m, g, r_norm] if x is not None]
    base = round((sum(components) / len(components)), 1) if components else v
    districts_list.append({
        "name": k,
        "score": round(min(10.0, base + s_bonus), 1),
        "score_base": base,
        "super_bonus": s_bonus,
        "super_tag": s_tag,
        "score_manual": rs.get("manual"),
        "score_gpt": rs.get("gpt"),
        "score_residents": r_norm,
        "residents_source": res.get("source"),
        "summary": DISTRICT_SUMMARIES.get(k, ""),
        "pros": DISTRICT_PROS.get(k, []),
        "cons": DISTRICT_CONS.get(k, []),
        "desc": DISTRICT_DESCRIPTIONS.get(k, ""),
        "noise_tag": noise_tag,
        "nuisance_tags": nuisance_tags,
    })
districts_list.sort(key=lambda x: (-x["score"], x["name"]))
districts_json = json.dumps(districts_list, ensure_ascii=False)

html = open("listings_template.html").read()
html = (html
    .replace("__DATA__", data_json)
    .replace("__MX__", str(mx_dist))
    .replace("__TOTAL__", str(total))
    .replace("__DISTRICTS__", districts_json)
)

with open("listings_poznan.html", "w", encoding="utf-8") as f:
    f.write(html)
print(f"OK: listings_poznan.html ({total} строк)")
