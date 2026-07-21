#!/usr/bin/env python3
"""
Agentic research pipeline for a district that is new to our listings.

Steps:
  1. Find center coordinates from current listings
  2. Query OSM Overpass for supermarkets/hypermarkets within 5 km
  3. Check known nuisance sites (from score.py) for proximity
  4. Ask GPT to score + describe the district
  5. Patch score.py (DISTRICT_SCORES / DESCRIPTIONS / SUMMARIES / PROS / CONS)
  6. Update rescore_results.json with the GPT score
  7. Update supermarkets_cache.json for all listings in this district

Usage:
    python3 research_new_district.py "Szczepankowo"
    python3 research_new_district.py "Szczepankowo" "Głuszyna"  # multiple at once
"""
import csv, json, os, re, sys
from math import radians, sin, cos, sqrt, atan2
from pathlib import Path
from urllib.request import urlopen, Request
import time

DATA_DIR = Path(__file__).parent
LISTINGS_CSV  = DATA_DIR / "listings_latest.csv"
RESCORE_FILE  = DATA_DIR / "rescore_results.json"
SUPER_FILE    = DATA_DIR / "supermarkets_cache.json"
SCORE_PY      = DATA_DIR / "score.py"


# ── geo ───────────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dl = radians(lat2 - lat1); do = radians(lon2 - lon1)
    a = sin(dl/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(do/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def district_center(name):
    rows = list(csv.DictReader(open(LISTINGS_CSV, encoding="utf-8-sig")))
    pts = []
    for r in rows:
        if (r.get("district") == name or r.get("city") == name) and r.get("lat") and r.get("lon"):
            try:
                pts.append((float(r["lat"]), float(r["lon"])))
            except ValueError:
                pass
    if not pts:
        return None, None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


# ── OSM supermarkets ──────────────────────────────────────────────────────────

STORE_TIERS = {
    "hyper":    ["carrefour", "auchan", "kaufland", "real", "tesco", "e.leclerc", "leclerc", "piotr i paweł"],
    "discount": ["biedronka", "lidl", "aldi", "netto", "penny", "dino"],
}


def classify_store(name, brand, shop_tag):
    key = (name + " " + brand).lower()
    if shop_tag == "hypermarket":
        return "hyper"
    for tier, brands in STORE_TIERS.items():
        if any(b in key for b in brands):
            return tier
    return "super"


def overpass_supermarkets(lat, lon, radius_m=5000):
    query = f"""[out:json][timeout:30];
(
  node["shop"~"^(supermarket|hypermarket|discount)$"](around:{radius_m},{lat},{lon});
  way["shop"~"^(supermarket|hypermarket|discount)$"](around:{radius_m},{lat},{lon});
);
out center;"""
    req = Request("https://overpass-api.de/api/interpreter",
                  data=query.encode(), method="POST")
    req.add_header("User-Agent", "poznan_listings_bot/1.0")
    for attempt in range(3):
        try:
            time.sleep(2 + attempt * 3)  # 2s, 5s, 8s между попытками
            data = json.loads(urlopen(req, timeout=35).read())
            break
        except Exception as e:
            print(f"  Overpass error (attempt {attempt+1}): {e}")
            if attempt == 2:
                return {}
    else:
        return {}

    best = {}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name  = tags.get("name", "")
        brand = tags.get("brand", "") or tags.get("operator", "")
        shop  = tags.get("shop", "")
        elat  = el.get("lat") or (el.get("center") or {}).get("lat")
        elon  = el.get("lon") or (el.get("center") or {}).get("lon")
        if not (name and elat and elon):
            continue
        d = haversine(lat, lon, elat, elon)
        tier = classify_store(name, brand, shop)
        if tier not in best or d < best[tier]["dist_km"]:
            best[tier] = {"name": name, "brand": brand, "dist_km": round(d, 3)}
    return best


# ── nuisance check ────────────────────────────────────────────────────────────

def check_nuisances(lat, lon):
    from score import _NUISANCE_SITES
    hits = []
    for slat, slon, radius, penalty, name in _NUISANCE_SITES:
        d = haversine(lat, lon, slat, slon)
        if d < radius * 2:  # warn if within 2× penalty radius
            hits.append({"name": name, "dist_km": round(d, 2), "penalty_radius_km": radius})
    return hits


# ── GPT ───────────────────────────────────────────────────────────────────────

def gpt_research(district, super_entry, nuisances):
    token = os.environ.get("GPT_TOKEN") or os.environ.get("OPENAI_API_KEY")
    if not token:
        print("  No GPT token — skipping GPT scoring")
        return None

    store_lines = "\n".join(
        f"- {tier}: {v['name']} ({v['dist_km']} km)" for tier, v in super_entry.items()
    ) or "данных нет"

    nuis_lines = "\n".join(
        f"- {n['name']} ({n['dist_km']} km)" for n in nuisances
    ) or "нет"

    # Шаг 1: веб-поиск отзывов жителей через GPT-4o с web search
    search_prompt = f"""Найди актуальные отзывы и мнения жителей о районе/посёлке **{district}** (Познань или окрестности, Польша).

Ищи на этих источниках:
- naszpoznan.pl — форум и обсуждения
- forum.gazeta.pl — раздел Познань
- reddit.com/r/Poznan
- wykop.pl
- otodom.pl — отзывы о районах
- Google: site:naszpoznan.pl "{district}" OR site:forum.gazeta.pl "{district} Poznań"

Также найди:
- NPS (Net Promoter Score) района из опроса Otodom/IQS если есть
- Реальные маршруты ZTM (автобусы/трамваи) до центра Познани и время в пути
- Школы и детсады в районе

Дай подробный отчёт на русском: что пишут жители (цитаты), плюсы, минусы, транспорт, инфраструктура."""

    search_payload = json.dumps({
        "model": "gpt-4o-search-preview",
        "messages": [{"role": "user", "content": search_prompt}],
        "web_search_options": {},
    }).encode()

    search_req = Request("https://api.openai.com/v1/chat/completions",
                         data=search_payload, method="POST")
    search_req.add_header("Authorization", f"Bearer {token}")
    search_req.add_header("Content-Type", "application/json")

    research_text = ""
    try:
        resp = json.loads(urlopen(search_req, timeout=90).read())
        research_text = resp["choices"][0]["message"]["content"].strip()
        print(f"  Web research: {len(research_text)} chars")
    except Exception as e:
        print(f"  GPT web search error: {e} — falling back to knowledge only")

    # Шаг 2: синтез в структурированный JSON
    synth_prompt = f"""На основе исследования района **{district}** (Польша) сформируй итоговую оценку для покупателя жилья.

Данные из веб-поиска:
{research_text or "(веб-поиск недоступен — используй свои знания)"}

Дополнительные данные:
Ближайшие магазины (OSM): {store_lines}
Негативные объекты рядом: {nuis_lines}

Ответь строго в JSON (без markdown-блоков):
{{
  "score": 7.0,
  "nps": null,
  "summary": "2-3 предложения — суть района для покупателя",
  "pros": ["плюс1", "плюс2", "плюс3"],
  "cons": ["минус1", "минус2"],
  "description": "Детальное описание 5-8 предложений с конкретными фактами из найденных отзывов: маршруты автобусов/трамваев, время до центра, названия школ, парков, цитаты жителей если есть"
}}

score — от 1 до 10 с шагом 0.5, учитывая реальные отзывы жителей, NPS если найден, транспорт, инфраструктуру.
nps — число из опроса Otodom/IQS или null если не найдено."""

    synth_payload = json.dumps({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": synth_prompt}],
        "temperature": 0.2,
    }).encode()

    synth_req = Request("https://api.openai.com/v1/chat/completions",
                        data=synth_payload, method="POST")
    synth_req.add_header("Authorization", f"Bearer {token}")
    synth_req.add_header("Content-Type", "application/json")

    try:
        resp = json.loads(urlopen(synth_req, timeout=60).read())
        content = resp["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```json\s*|```\s*$", "", content, flags=re.MULTILINE).strip()
        return json.loads(content)
    except Exception as e:
        print(f"  GPT synthesis error: {e}")
        return None


# ── patch score.py ────────────────────────────────────────────────────────────

def _escape_py_str(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def patch_score_py(district, score, summary, pros, cons, description):
    src = SCORE_PY.read_text(encoding="utf-8")

    def insert_after_open(src, dict_name, new_entry):
        marker = f"{dict_name} = {{"
        idx = src.find(marker)
        if idx == -1:
            print(f"  WARNING: {dict_name} not found in score.py")
            return src
        insert_at = src.index("\n", idx) + 1
        return src[:insert_at] + new_entry + src[insert_at:]

    # DISTRICT_SCORES: simple float
    if f'"{district}"' not in src.split("DISTRICT_SCORES")[1].split("DISTRICT_DESCRIPTIONS")[0]:
        entry = f'    "{district}": {score},\n'
        src = insert_after_open(src, "DISTRICT_SCORES", entry)

    # DISTRICT_DESCRIPTIONS
    if f'"{district}"' not in src.split("DISTRICT_DESCRIPTIONS")[1].split("DISTRICT_SUMMARIES")[0]:
        entry = f'    "{district}": "{_escape_py_str(description)}",\n'
        src = insert_after_open(src, "DISTRICT_DESCRIPTIONS", entry)

    # DISTRICT_SUMMARIES
    if f'"{district}"' not in src.split("DISTRICT_SUMMARIES")[1].split("DISTRICT_PROS")[0]:
        entry = f'    "{district}": "{_escape_py_str(summary)}",\n'
        src = insert_after_open(src, "DISTRICT_SUMMARIES", entry)

    # DISTRICT_PROS
    if f'"{district}"' not in src.split("DISTRICT_PROS")[1].split("DISTRICT_CONS")[0]:
        pros_repr = "[" + ", ".join(f'"{_escape_py_str(p)}"' for p in pros) + "]"
        entry = f'    "{district}": {pros_repr},\n'
        src = insert_after_open(src, "DISTRICT_PROS", entry)

    # DISTRICT_CONS
    if f'"{district}"' not in src.split("DISTRICT_CONS")[1].split("\n}")[0]:
        cons_repr = "[" + ", ".join(f'"{_escape_py_str(c)}"' for c in cons) + "]"
        entry = f'    "{district}": {cons_repr},\n'
        src = insert_after_open(src, "DISTRICT_CONS", entry)

    tmp = SCORE_PY.with_suffix(".tmp")
    tmp.write_text(src, encoding="utf-8")
    tmp.replace(SCORE_PY)
    print(f"  score.py patched for {district}")


# ── update JSON caches ────────────────────────────────────────────────────────

def update_rescore(district, gpt_score):
    data = json.loads(RESCORE_FILE.read_text()) if RESCORE_FILE.exists() else {}
    existing = data.get(district, {})
    data[district] = {"manual": existing.get("manual"), "gpt": gpt_score}
    RESCORE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def update_supermarkets_cache(district, super_entry):
    cache = json.loads(SUPER_FILE.read_text()) if SUPER_FILE.exists() else {}
    rows = list(csv.DictReader(open(LISTINGS_CSV, encoding="utf-8-sig")))
    updated = 0
    for r in rows:
        if (r.get("district") == district or r.get("city") == district) and r.get("lat") and r.get("lon"):
            key = f"{r['lat']},{r['lon']}"
            if key not in cache:
                cache[key] = super_entry
                updated += 1
    SUPER_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    print(f"  supermarkets_cache: {updated} new entries for {district}")


# ── main ──────────────────────────────────────────────────────────────────────

def research(district_name):
    print(f"\n{'='*60}")
    print(f"Researching: {district_name}")

    lat, lon = district_center(district_name)
    if lat is None:
        print(f"  ERROR: no listings with coordinates for {district_name}")
        return False

    print(f"  Center: {lat:.5f}, {lon:.5f}")

    # OSM supermarkets
    print("  Querying OSM supermarkets...")
    super_entry = overpass_supermarkets(lat, lon)
    print(f"  Found tiers: {list(super_entry.keys())}")

    # Nuisances
    nuisances = check_nuisances(lat, lon)
    if nuisances:
        print(f"  Nuisances nearby: {[n['name'] for n in nuisances]}")

    # GPT
    print("  Asking GPT-4o...")
    gpt = gpt_research(district_name, super_entry, nuisances)

    if gpt:
        score    = float(gpt.get("score", 6.0))
        summary  = gpt.get("summary", "")
        pros     = gpt.get("pros", [])
        cons     = gpt.get("cons", [])
        desc     = gpt.get("description", "")
        print(f"  GPT score: {score}")
        patch_score_py(district_name, score, summary, pros, cons, desc)
        update_rescore(district_name, score)
    else:
        score = 6.0
        update_rescore(district_name, None)

    # Supermarkets cache
    if super_entry:
        update_supermarkets_cache(district_name, super_entry)

    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 research_new_district.py <district> [district2 ...]")
        sys.exit(1)
    for i, name in enumerate(sys.argv[1:]):
        if i > 0:
            time.sleep(3)
        research(name)
    print("\nDone.")
