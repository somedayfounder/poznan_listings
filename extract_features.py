"""
Извлекает структурированные фичи из описаний объявлений через GPT-4o-mini.
Кэширует результаты в features_cache.json по ID объявления.
"""

import csv, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

DATA_DIR = Path(__file__).parent
CACHE_FILE = DATA_DIR / "features_cache.json"
GPT_TOKEN = os.environ.get("GPT_TOKEN", "")

PROMPT = """Проанализируй описание объявления о недвижимости и верни JSON с фичами.

Описание:
{description}

Верни ТОЛЬКО валидный JSON без пояснений:
{{
  "garage": true/false,
  "parking": true/false,
  "garden_m2": число или null,
  "terrace": true/false,
  "balcony": true/false,
  "smart_home": true/false,
  "gym_on_site": true/false,
  "new_condition": true/false,
  "needs_renovation": true/false,
  "quiet_street": true/false,
  "near_highway": true/false,
  "near_railway": true/false,
  "elevator": true/false,
  "storage_room": true/false,
  "electric_car_charger": true/false,
  "floor_heating": true/false,
  "high_ceiling": true/false,
  "panoramic_view": true/false,
  "water_view": true/false,
  "school_nearby": true/false,
  "kindergarten_nearby": true/false,
  "park_nearby": true/false,
  "forest_nearby": true/false,
  "supermarket_nearby": true/false,
  "concierge": true/false,
  "gated_community": true/false,
  "red_flags": ["список проблем если есть, иначе пустой массив"],
  "bonus_features": ["список значимых плюсов если есть, иначе пустой массив"]
}}
"""

FEATURE_SCORES = {
    # Парковка и хранение
    "garage":              +0.5,
    "parking":             +0.3,
    "storage_room":        +0.1,
    "electric_car_charger":+0.1,
    # Outdoor
    "garden_m2":           +0.4,   # если > 0
    "terrace":             +0.3,
    "balcony":             +0.1,
    # Инфраструктура ЖК
    "gym_on_site":         +0.2,
    "concierge":           +0.2,
    "gated_community":     +0.1,
    # Состояние и технологии
    "new_condition":       +0.4,
    "smart_home":          +0.2,
    "floor_heating":       +0.2,
    "high_ceiling":        +0.1,
    # Вид и природа
    "panoramic_view":      +0.2,
    "water_view":          +0.3,
    "forest_nearby":       +0.2,
    "park_nearby":         +0.1,
    # Тихость
    "quiet_street":        +0.2,
    # Инфраструктура рядом
    "school_nearby":       +0.1,
    "kindergarten_nearby": +0.1,
    "supermarket_nearby":  +0.1,
    "elevator":            +0.2,
    # Штрафы
    "needs_renovation":    -0.5,
    "near_highway":        -0.4,
    "near_railway":        -0.3,
}


def _fetch_description(url):
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urlopen(req, timeout=15).read().decode("utf-8", "replace")
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not m:
            return None
        ad = json.loads(m.group(1))["props"]["pageProps"].get("ad") or {}
        desc = ad.get("description", "")
        return re.sub(r"<[^>]+>", " ", desc)[:2000]
    except Exception:
        return None


def _call_gpt(description):
    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": PROMPT.format(description=description)}],
        "temperature": 0,
        "max_tokens": 600,
    }).encode()
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GPT_TOKEN}",
        },
    )
    resp = json.loads(urlopen(req, timeout=20).read())
    text = resp["choices"][0]["message"]["content"].strip()
    # Извлекаем JSON если GPT добавил лишний текст
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def feature_bonus(features):
    """Возвращает бонус/штраф к score от -1.5 до +1.5."""
    if not features:
        return 0.0
    score = 0.0
    for key, delta in FEATURE_SCORES.items():
        val = features.get(key)
        if val is True or (key == "garden_m2" and val and val > 0):
            score += delta
        elif val is False and delta > 0:
            pass  # отсутствие плюса не штрафуем
    return round(max(-1.5, min(1.5, score)), 2)


def run():
    if not GPT_TOKEN:
        print("GPT_TOKEN не задан — пропускаем extract_features")
        return

    rows = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    cache = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}

    need = [r for r in rows if r["id"] not in cache]
    print(f"Нужно обработать: {len(need)} из {len(rows)}")

    for i, r in enumerate(need):
        desc = _fetch_description(r["url"])
        if not desc:
            cache[r["id"]] = {}
            continue
        try:
            features = _call_gpt(desc)
            features["_bonus"] = feature_bonus(features)
            cache[r["id"]] = features
            print(f"  [{i+1}/{len(need)}] {r['id']} bonus={features['_bonus']:+.2f}")
        except Exception as e:
            print(f"  GPT error {r['id']}: {e}")
            cache[r["id"]] = {}
        time.sleep(0.5)

        if (i + 1) % 50 == 0:
            CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))

    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
    print(f"Готово. Кэш: {len(cache)} записей")


if __name__ == "__main__":
    run()
