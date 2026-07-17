"""
Scoring system for Poznań listings (0–10 scale).

Weights:
  price      25%  — ≤700k ideal, 700–900k good, >900k penalty
  area       25%  — 90–100m² ideal; <70 or >120 = 0
  transport  25%  — dist to tram (apt: ≤3km; house: ≤8km)
  rooms      15%  — 4–5 ideal, 3 borderline, 6+ excess
  type       10%  — house > apartment
"""


def _score_price(price):
    if price is None:
        return 5.0
    if price <= 700_000:
        return 10.0
    if price <= 900_000:
        # 700k→10, 900k→8
        return 10.0 - 2.0 * (price - 700_000) / 200_000
    # 900k→8, 1_400k→0
    return max(0.0, 8.0 - 8.0 * (price - 900_000) / 500_000)


def _score_area(area):
    if area is None:
        return 5.0
    if area < 70 or area > 120:
        return 0.0
    if 90 <= area <= 100:
        return 10.0
    if area < 90:
        # 70→4, 90→10
        return 4.0 + 6.0 * (area - 70) / 20
    # 100→10, 120→7
    return 10.0 - 3.0 * (area - 100) / 20


def _score_rooms(rooms):
    if rooms is None:
        return 5.0
    if rooms <= 2:
        return 1.0
    if rooms == 3:
        return 6.0
    if rooms in (4, 5):
        return 10.0
    # 6+
    return 3.0


def _score_transport(tp, dist_tram, dist_center):
    d = dist_tram if dist_tram is not None else dist_center
    if d is None:
        return 5.0
    if tp == "dom":
        # house: up to 8km is fine
        if d <= 2:
            return 10.0
        if d <= 8:
            return 10.0 - 5.0 * (d - 2) / 6   # 2→10, 8→5
        return max(0.0, 5.0 - 5.0 * (d - 8) / 7)  # 8→5, 15→0
    else:
        # apartment: ideal ≤3km
        if d <= 1:
            return 10.0
        if d <= 3:
            return 10.0 - 2.0 * (d - 1) / 2   # 1→10, 3→8
        if d <= 8:
            return max(0.0, 8.0 - 8.0 * (d - 3) / 5)  # 3→8, 8→0
        return 0.0


def _score_type(tp):
    return 10.0 if tp == "dom" else 6.0


_W = {"price": 0.25, "area": 0.25, "transport": 0.25, "rooms": 0.15, "type": 0.10}


def compute_score(price, area, rooms, tp, dist_tram, dist_center):
    """All args are float|int|None except tp (str: 'dom' or 'mieszkanie')."""
    s = (
        _score_price(price) * _W["price"]
        + _score_area(area) * _W["area"]
        + _score_rooms(rooms) * _W["rooms"]
        + _score_transport(tp, dist_tram, dist_center) * _W["transport"]
        + _score_type(tp) * _W["type"]
    )
    return round(s, 1)


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _rooms(raw):
    if raw in (None, ""):
        return None
    if str(raw) == "7+":
        return 7
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def score_from_csv(r):
    """Score a row from listings_latest.csv (dict with string values)."""
    return compute_score(
        price=_f(r.get("price_zl")),
        area=_f(r.get("area_m2")),
        rooms=_rooms(r.get("rooms")),
        tp=r.get("type", ""),
        dist_tram=_f(r.get("drive_tram_km")) or _f(r.get("dist_tram")),
        dist_center=_f(r.get("drive_ratusz_km")) or _f(r.get("dist_km")),
    )


def score_from_jsrow(r):
    """Score a row from build_listings_html.py js_rows (dict with typed values)."""
    return compute_score(
        price=r.get("price"),
        area=r.get("area"),
        rooms=_rooms(r.get("rooms")),
        tp=r.get("type", ""),
        dist_tram=r.get("dist_tram"),
        dist_center=r.get("dist"),
    )
