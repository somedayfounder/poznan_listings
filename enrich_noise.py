"""
Downloads noise immission data from Poznań GIS (geopoz.poznan.pl) WFS
and computes max noise level (LDWN dBA) for each listing by coordinates.
Saves results to noise_cache.json: {"lat,lon": {"ldwn": 62, "sources": ["dr"]}}
"""

import csv, json
from pathlib import Path
from urllib.request import urlopen

DATA_DIR = Path(__file__).parent
CACHE_FILE = DATA_DIR / "noise_cache.json"

WFS_BASE = "https://wms2.geopoz.poznan.pl/geoserver/akustyka/wfs"

# Noise immission layers (2017), polygons, field LDWN (dBA day+evening+night)
# dr=road, tr=tram, ko=railway, prz=industrial
LAYERS = {
    "dr": "akustyka:v_v17_dr_imisja_ldwn_sql",
    "tr": "akustyka:v_v17_tr_imisja_ldwn_sql",
    "ko": "akustyka:v_v17_ko_imisja_ldwn_sql",
    "prz": "akustyka:v_v17_prz_imisja_ldwn_sql",
}


def fetch_layer(layer_name):
    url = (f"{WFS_BASE}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
           f"&TYPENAMES={layer_name}&OUTPUTFORMAT=application/json"
           f"&SRSNAME=EPSG:4326")
    print(f"  Fetching {layer_name}...", end=" ", flush=True)
    data = json.loads(urlopen(url, timeout=60).read())
    print(f"{len(data['features'])} features")
    return data["features"]


def point_in_polygon(px, py, poly_coords):
    """Ray casting for simple polygon (list of [lon,lat] pairs)."""
    inside = False
    n = len(poly_coords)
    j = n - 1
    for i in range(n):
        xi, yi = poly_coords[i]
        xj, yj = poly_coords[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def point_in_feature(lon, lat, feature):
    """Check if point is inside any ring of a MultiPolygon/Polygon feature."""
    geom = feature["geometry"]
    if geom is None:
        return False
    gtype = geom["type"]
    coords = geom["coordinates"]
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        return False
    for poly in polys:
        outer = poly[0]
        if point_in_polygon(lon, lat, outer):
            return True
    return False


def query_noise(lon, lat, all_features):
    """Return dict {source: max_ldwn} for given point."""
    result = {}
    for src, features in all_features.items():
        max_ldwn = None
        for feat in features:
            if point_in_feature(lon, lat, feat):
                ldwn = feat["properties"].get("LDWN")
                if ldwn is not None:
                    max_ldwn = max(max_ldwn or 0, ldwn)
        if max_ldwn is not None:
            result[src] = max_ldwn
    return result


def run():
    rows = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}

    need = [r for r in rows if r.get("lat") and r.get("lon") and f"{r['lat']},{r['lon']}" not in cache]
    print(f"Listings to process: {len(need)} (cached: {len(cache)})")
    if not need:
        print("All cached.")
        return

    print("Downloading noise layers...")
    all_features = {}
    for src, layer in LAYERS.items():
        try:
            all_features[src] = fetch_layer(layer)
        except Exception as e:
            print(f"  WARNING: failed to fetch {layer}: {e}")
            all_features[src] = []

    print(f"Computing noise for {len(need)} listings...")
    for i, r in enumerate(need):
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (ValueError, TypeError):
            continue
        key = f"{r['lat']},{r['lon']}"
        noise = query_noise(lon, lat, all_features)
        cache[key] = noise
        if noise:
            max_l = max(noise.values())
            print(f"  [{i+1}] {r.get('district') or r.get('city')} — {noise} → max {max_l} dBA")

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    print(f"Done. noise_cache.json: {len(cache)} entries")


if __name__ == "__main__":
    run()
