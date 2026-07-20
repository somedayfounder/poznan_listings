import csv, json
from math import radians, sin, cos, sqrt, atan2, degrees

_RATUSZ = (52.4082, 16.9335)

def _bearing_label(lat2, lon2):
    lat1, lon1 = _RATUSZ
    dlon = radians(lon2 - lon1)
    rlat1, rlat2 = radians(lat1), radians(lat2)
    x = sin(dlon) * cos(rlat2)
    y = cos(rlat1) * sin(rlat2) - sin(rlat1) * cos(rlat2) * cos(dlon)
    b = (degrees(atan2(x, y)) + 360) % 360
    dirs = ["С","СВ","В","ЮВ","Ю","ЮЗ","З","СЗ"]
    return dirs[round(b / 45) % 8]
from pathlib import Path
from score import score_from_jsrow, DISTRICT_SCORES, _DEFAULT_DISTRICT_SCORE, DISTRICT_DESCRIPTIONS, DISTRICT_SUMMARIES, DISTRICT_PROS, DISTRICT_CONS, _nuisance_penalty, _NUISANCE_SITES, _haversine, _noise_penalty

_RESCORE_FILE   = Path(__file__).parent / "rescore_results.json"
_RESIDENT_FILE  = Path(__file__).parent / "resident_scores.json"
_rescore   = json.loads(_RESCORE_FILE.read_text())  if _RESCORE_FILE.exists()  else {}
_residents = json.loads(_RESIDENT_FILE.read_text()) if _RESIDENT_FILE.exists() else {}
from extract_features import feature_bonus, CACHE_FILE as FEAT_CACHE
import json as _json
_feat_cache = _json.loads(FEAT_CACHE.read_text()) if FEAT_CACHE.exists() else {}
_SUPER_CACHE_FILE = Path("supermarkets_cache.json")
_super_cache = _json.loads(_SUPER_CACHE_FILE.read_text()) if _SUPER_CACHE_FILE.exists() else {}

_DECAY = {"hyper": (0.3, 10.0), "super": (0.2, 5.0), "discount": (0.1, 2.0)}

def _store_tier(sup_entry):
    if not sup_entry: return None
    best_tier, best_val = None, 0.0
    for tier, (max_b, max_d) in _DECAY.items():
        d = (sup_entry.get(tier) or {}).get("dist_km")
        if d is not None and d < max_d:
            val = max_b * (1 - d / max_d)
            if val > best_val:
                best_val, best_tier = val, tier
    return best_tier

def _best_store_name(sup_entry, tier):
    return (sup_entry.get(tier) or {}).get("name", "")
_NOISE_CACHE_FILE = Path("noise_cache.json")
_noise_cache = _json.loads(_NOISE_CACHE_FILE.read_text()) if _NOISE_CACHE_FILE.exists() else {}
_DRIVE_CACHE_FILE = Path("drive_cache.json")
_drive_cache = _json.loads(_DRIVE_CACHE_FILE.read_text()) if _DRIVE_CACHE_FILE.exists() else {}

trams = json.loads(Path("tram_stops.json").read_text())
stop_lines = json.loads(Path("stop_lines.json").read_text())
rails = json.loads(Path("rail_stations.json").read_text())
_stop_coords = {t["name"]: (t["lat"], t["lon"]) for t in trams}
_stop_coords.update({r["name"]: (r["lat"], r["lon"]) for r in rails})

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
    area = v(r, "area_m2", float)
    rooms_raw = v(r, "rooms")
    rooms_num = None
    try:
        if rooms_raw and rooms_raw != "7+": rooms_num = int(rooms_raw)
        elif rooms_raw == "7+": rooms_num = 7
    except: pass
    # prefer Google Maps drive distances over haversine
    _dk = f"{r.get('lat')},{r.get('lon')}"
    _drive = _drive_cache.get(_dk, {})
    dist      = _drive.get("ratusz_km") or v(r, "drive_ratusz_km", float) or v(r, "dist_km", float)
    dist_min  = round(_drive["ratusz_dur_s"] / 60) if _drive.get("ratusz_dur_s") else None
    dist_tram  = _drive.get("tram_km") or v(r, "drive_tram_km", float)
    tram_min   = round(_drive["tram_dur_s"] / 60) if _drive.get("tram_dur_s") else None
    tram_walk_min = round(_drive["tram_walk_s"] / 60) if _drive.get("tram_walk_s") else None
    tram_name  = _drive.get("tram_name") or r.get("drive_tram_name", "")
    dist_rail = _drive.get("rail_km") or v(r, "drive_rail_km", float)
    rail_min  = round(_drive["rail_dur_s"] / 60) if _drive.get("rail_dur_s") else None
    rail_walk_min = round(_drive["rail_walk_s"] / 60) if _drive.get("rail_walk_s") else None
    rail_name = _drive.get("rail_name") or r.get("drive_rail_name", "")

    tram_tip = rail_tip = None
    tram_details = rail_details = None

    if r.get("lat") and r.get("lon"):
        lat, lon = float(r["lat"]), float(r["lon"])

        if tram_name:
            candidates = _drive.get("tram_candidates", [])

            # ближайшая пешком
            cands_with_walk = [c for c in candidates if c.get("walk_s") is not None]
            walk_stop = None
            if cands_with_walk:
                wb = min(cands_with_walk, key=lambda c: c["walk_s"])
                walk_stop = {"name": wb["name"], "walk_min": round(wb["walk_s"] / 60), "km": wb["km"]}
            elif tram_walk_min is not None:
                walk_stop = {"name": tram_name, "walk_min": tram_walk_min, "km": dist_tram}

            # остановки на авто: уникальные линии, сортировка по drive_s, макс 3
            drive_stops = []
            seen_lines = set()
            for cand in sorted(candidates, key=lambda c: c["dur_s"]):
                lines2 = set(stop_lines.get(cand["name"], []))
                if lines2 and not lines2.intersection(seen_lines):
                    sc = _stop_coords.get(cand["name"])
                    d = {"name": cand["name"], "min": round(cand["dur_s"] / 60), "km": cand["km"], "lines": sorted(lines2)}
                    if sc: d["dir"] = _bearing_label(sc[0], sc[1])
                    drive_stops.append(d)
                    seen_lines |= lines2
                if len(drive_stops) >= 3:
                    break

            # fallback: haversine если кандидатов нет
            if not candidates and tram_name:
                lines1 = set(stop_lines.get(tram_name, []))
                drive_stops = [{"name": tram_name, "min": tram_min, "km": dist_tram, "lines": sorted(lines1)}]
                ranked_hav = sorted(trams, key=lambda t: hav(lat, lon, t["lat"], t["lon"]))
                for t in ranked_hav:
                    if t["name"] == tram_name:
                        continue
                    lines2 = set(stop_lines.get(t["name"], []))
                    if lines2 and lines1 and not lines1.intersection(lines2):
                        d2 = round(hav(lat, lon, t["lat"], t["lon"]), 2)
                        drive_stops.append({"name": t["name"], "min": None, "km": d2, "lines": sorted(lines2)})
                        break

            tram_details = {"walk_stop": walk_stop, "drive_stops": drive_stops}
            tram_tip = " | ".join(f"{s['name']} {s['min']} мин" for s in drive_stops if s.get("min"))

        if rail_name:
            rail_details = {"name": rail_name, "min": rail_min, "km": dist_rail}
            rail_tip = f"{rail_name} — {fmt_d(dist_rail)} по дороге ({rail_min} мин)" if rail_min else f"{rail_name} — {fmt_d(dist_rail)} по дороге"

    lat = v(r, "lat", float)
    lon = v(r, "lon", float)
    row = {
        "id": r["id"], "type": r["type"], "title": r["title"],
        "area": area, "rooms": rooms_num, "floor": v(r, "floor"),
        "price": int(price) if price else None,
        "price_m2": int(price_m2) if price_m2 else None,
        "street": r["street"], "city": r["city"], "project": r["project"],
        "district": r.get("district", ""),
        "dist": dist, "dist_min": dist_min,
        "dist_tram": dist_tram, "tram_min": tram_min, "tram_tip": tram_tip, "tram_details": tram_details,
        "dist_rail": dist_rail, "rail_min": rail_min, "rail_tip": rail_tip, "rail_details": rail_details,
        "walk": min(filter(None, [tram_walk_min, rail_walk_min]), default=None),
        "walk_type": ("rail" if rail_walk_min and (tram_walk_min is None or rail_walk_min < tram_walk_min) else "tram") if (tram_walk_min or rail_walk_min) else None,
        "url": r["url"],
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
_dist_super = {}  # district -> best tier ("hyper"/"super"/"discount"/None)
for row in js_rows:
    d = row.get("district") or row.get("city") or ""
    if not d:
        continue
    if row.get("noise"):
        _dist_noise[d].append(max(row["noise"].values()))
    for n in (row.get("nuisance") or []):
        _dist_nuisance[d].add(n["name"])
    tier = _store_tier(row.get("supermarket"))
    priority = {"hyper": 3, "super": 2, "discount": 1, None: 0}
    if priority.get(tier, 0) > priority.get(_dist_super.get(d), 0):
        _dist_super[d] = tier

def _super_bonus(tier, sup_entry):
    if not tier or not sup_entry: return 0.0, None
    max_b, max_d = _DECAY[tier]
    d = (sup_entry.get(tier) or {}).get("dist_km")
    if d is None: return 0.0, None
    bonus = round(max_b * (1 - d / max_d), 2)
    name = _best_store_name(sup_entry, tier)
    label = {"hyper": "🏪 Гипермаркет", "super": "🛒 Супермаркет", "discount": "🛒 Дискаунтер"}[tier]
    return bonus, f"{label}{(' ('+name+')') if name else ''}"

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
    best_tier = _dist_super.get(k)
    # find a representative listing's supermarket entry for the name
    _rep = next((r for r in js_rows if (r.get("district") or r.get("city")) == k
                 and _store_tier(r.get("supermarket")) == best_tier), None)
    s_bonus, s_tag = _super_bonus(best_tier, _rep.get("supermarket") if _rep else None)
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
