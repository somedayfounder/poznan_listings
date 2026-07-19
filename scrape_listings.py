#!/usr/bin/env python3
"""
Шаг 1 ежедневного пайплайна:
1. Запускает otodom_listings.py → listings_latest.csv
2. Докачивает фото и координаты для новых объявлений (coords_cache.json)
3. Определяет новые ID → сохраняет pending_notify.json
"""
import csv, json, os, re, subprocess, sys, time
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

DATA_DIR = Path(__file__).parent
SEEN_FILE    = DATA_DIR / "seen_ids.json"
PENDING_FILE = DATA_DIR / "pending_notify.json"


def _cfg():
    token = os.environ.get("TG_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if token and chat_id:
        return token, int(chat_id)
    cfg = json.loads((DATA_DIR / "tg_config.json").read_text())
    return cfg["token"], cfg["chat_id"]

TOKEN, CHAT_ID = _cfg()


def tg_send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": "true"}).encode()
    try:
        urlopen(Request(url, data=data), timeout=10)
    except Exception as e:
        print(f"TG error: {e}")


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    latest = DATA_DIR / "listings_latest.csv"
    if latest.exists():
        return set(r["id"] for r in csv.DictReader(open(latest, encoding="utf-8-sig")))
    return set()


def run():
    today = date.today().isoformat()
    print(f"=== {today} ===")
    tg_send(f"⏳ <b>Квартиры</b>: запуск {today}…")

    seen = load_seen()
    print(f"Известно объявлений: {len(seen)}")

    # 1. Парсим
    print("Запуск парсера...")
    r = subprocess.run([sys.executable, str(DATA_DIR / "otodom_listings.py")],
                       capture_output=True, text=True, cwd=DATA_DIR)
    if r.returncode != 0:
        tg_send(f"❌ Ошибка парсера:\n{r.stderr[-500:]}")
        sys.exit(1)
    print(r.stdout[-300:])

    rows_all = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    cache_file = DATA_DIR / "coords_cache.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    need_coords = sum(1 for row in rows_all if row["id"] not in cache)
    tg_send(f"📋 <b>Спарсили:</b> {len(rows_all)} объявлений, нужно загрузить: {need_coords}")

    # 2. Докачиваем координаты и фото
    if need_coords:
        tg_send(f"🌐 Загружаем {need_coords} страниц для координат и фото…")
    subprocess.run([sys.executable, "-c", f"""
import csv, re, json, time
from pathlib import Path
from urllib.request import Request, urlopen
HEADERS = {{'User-Agent': 'Mozilla/5.0'}}
cache_file = Path('coords_cache.json')
cache = json.loads(cache_file.read_text()) if cache_file.exists() else {{}}
rows = list(csv.DictReader(open('listings_latest.csv', encoding='utf-8-sig')))
fetched = 0
for r in rows:
    rid = r['id']
    if rid in cache:
        r.update(cache[rid])
        continue
    entry = {{}}
    try:
        req = Request(r['url'], headers=HEADERS)
        html = urlopen(req, timeout=15).read().decode('utf-8','replace')
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        ad = json.loads(m.group(1))['props']['pageProps'].get('ad') or {{}}
        coords = (ad.get('location') or {{}}).get('coordinates') or {{}}
        if coords.get('latitude'):
            entry['lat'] = round(coords['latitude'], 5)
            entry['lon'] = round(coords['longitude'], 5)
        images = ad.get('images') or []
        def _u(i): return i.get('large') or i.get('medium') or ''
        urls = [_u(i) for i in images[1:4] if _u(i)] or [_u(i) for i in images[0:1] if _u(i)]
        if urls:
            entry['photo_url'] = ','.join(urls)
    except: pass
    cache[rid] = entry
    r.update(entry)
    fetched += 1
    if fetched % 50 == 0:
        cache_file.write_text(json.dumps(cache))
        print(f'  coords: {{fetched}} fetched')
    time.sleep(0.3)
cache_file.write_text(json.dumps(cache))
fields = list(rows[0].keys())
for extra in ['photo_url','drive_ratusz_km','drive_tram_km','drive_tram_name','drive_rail_km','drive_rail_name']:
    if extra not in fields: fields.append(extra)
import csv as c2
with open('listings_latest.csv','w',newline='',encoding='utf-8-sig') as f:
    w = c2.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader(); w.writerows(rows)
print(f'coords done, fetched={{fetched}}')
"""], cwd=DATA_DIR, capture_output=True, text=True)

    # 3. Определяем новые ID
    rows_all = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    all_ids = set(r["id"] for r in rows_all)
    new_ids = all_ids - seen

    # Сохраняем фото для новых
    photo_map = {r["id"]: r.get("photo_url", "") for r in rows_all if r["id"] in new_ids}

    PENDING_FILE.write_text(json.dumps({
        "new_ids": sorted(new_ids),
        "all_ids": sorted(all_ids),
        "photo_map": photo_map,
        "date": today,
    }, ensure_ascii=False))

    print(f"Новых объявлений: {len(new_ids)}, всего: {len(all_ids)}")


if __name__ == "__main__":
    run()
