"""
Scoring system for Poznań listings (0–10 scale).

Weights (base):
  price      25%  — ≤700k=10, 700–900k=10→8, 900k–1200k=8→0, >1200k=0
  area       25%  — 90–100m² ideal; <70 or >120 = 0
  transport  25%  — dist to tram (apt: ≤3km; house: ≤10km)
  rooms      15%  — 4=10, 5=8, 3=7, 6=7, others=3
  type       10%  — house=10, apt=8

Missing price or rooms: their weight is redistributed proportionally
among the remaining factors so the listing isn't penalised for lack of data.
"""


def _score_price(price):
    if price is None:
        return None  # signals "missing"
    if price <= 700_000:
        return 10.0
    if price <= 900_000:
        # 700k→10, 900k→8
        return 10.0 - 2.0 * (price - 700_000) / 200_000
    if price <= 1_200_000:
        # 900k→8, 1200k→0
        return max(0.0, 8.0 - 8.0 * (price - 900_000) / 300_000)
    return 0.0


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
        return None  # signals "missing"
    if rooms == 4:
        return 10.0
    if rooms == 5:
        return 8.0
    if rooms == 3:
        return 7.0
    if rooms == 6:
        return 7.0
    # ≤2 or 7+
    return 3.0


def _score_transport(tp, dist_tram, dist_center):
    d = dist_tram if dist_tram is not None else dist_center
    if d is None:
        return 5.0
    if tp == "dom":
        # house: ideal ≤5km, ok up to 10km
        if d <= 2:
            return 10.0
        if d <= 5:
            return 10.0 - 1.0 * (d - 2) / 3   # 2→10, 5→9
        if d <= 10:
            return max(6.0, 9.0 - 3.0 * (d - 5) / 5)  # 5→9, 10→6
        return max(0.0, 6.0 - 6.0 * (d - 10) / 5)     # 10→6, 15→0
    else:
        # apartment: interesting only up to 3km
        if d <= 1:
            return 10.0
        if d <= 3:
            return 10.0 - 3.0 * (d - 1) / 2   # 1→10, 3→7
        if d <= 5:
            return max(0.0, 7.0 - 7.0 * (d - 3) / 2)  # 3→7, 5→0
        return 0.0


def _score_type(tp):
    return 10.0 if tp == "dom" else 8.0


_BASE_W = {"price": 0.25, "area": 0.25, "transport": 0.25, "rooms": 0.15, "type": 0.10}


def compute_score(price, area, rooms, tp, dist_tram, dist_center):
    """All args are float|int|None except tp (str: 'dom' or 'mieszkanie').

    price=None or rooms=None → their weight is redistributed proportionally
    so the listing isn't unfairly penalised for missing data.
    """
    p_score = _score_price(price)
    r_score = _score_rooms(rooms)

    # Build list of (score, weight) for factors with data
    factors = [
        (_score_area(area),                             _BASE_W["area"]),
        (_score_transport(tp, dist_tram, dist_center),  _BASE_W["transport"]),
        (_score_type(tp),                               _BASE_W["type"]),
    ]
    if p_score is not None:
        factors.append((p_score, _BASE_W["price"]))
    if r_score is not None:
        factors.append((r_score, _BASE_W["rooms"]))

    total_w = sum(w for _, w in factors)
    s = sum(sc * w for sc, w in factors) / total_w
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
