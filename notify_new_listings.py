#!/usr/bin/env python3
"""
Ежедневный запуск:
1. Парсит объявления (otodom_listings.py)
2. Получает координаты с otodom (индивидуальные страницы)
3. Считает маршруты через OSRM
4. Сравнивает с предыдущим CSV — шлёт новые в Telegram
"""

import csv, json, re, subprocess, sys, time
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from score import score_from_csv

POZNAN_SUBURBS = {"Smochowice", "Naramowice", "Strzeszyn", "Morasko",
                  "Szczepankowo", "Spławie", "Głuszyna", "Fabianowo"}

DATA_DIR = Path(__file__).parent

def _cfg():
    token = os.environ.get("TG_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if token and chat_id:
        return token, int(chat_id)
    cfg = json.loads((DATA_DIR / "tg_config.json").read_text())
    return cfg["token"], cfg["chat_id"]

import os
TOKEN, CHAT_ID = _cfg()
SEEN_FILE = DATA_DIR / "seen_ids.json"


def _escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg_send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urlencode({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": "true"}).encode()
    urlopen(Request(url, data=data), timeout=10)


def tg_send_photo(photo_url, caption):
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    data = urlencode({"chat_id": CHAT_ID, "photo": photo_url,
                      "caption": caption, "parse_mode": "HTML"}).encode()
    urlopen(Request(url, data=data), timeout=15)


def tg_send_media_group(photo_urls, caption):
    media = []
    for i, u in enumerate(photo_urls[:3]):
        entry = {"type": "photo", "media": u}
        if i == 0:
            entry["caption"] = caption
            entry["parse_mode"] = "HTML"
        media.append(entry)
    url = f"https://api.telegram.org/bot{TOKEN}/sendMediaGroup"
    data = urlencode({"chat_id": CHAT_ID, "media": json.dumps(media)}).encode()
    urlopen(Request(url, data=data), timeout=15)


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    # первый запуск — считаем все текущие объявления уже виденными
    latest = DATA_DIR / "listings_latest.csv"
    if latest.exists():
        rows = list(csv.DictReader(open(latest, encoding="utf-8-sig")))
        return set(r["id"] for r in rows)
    return set()


def save_seen(ids):
    SEEN_FILE.write_text(json.dumps(sorted(ids)))


def fmt_dist(d):
    if d is None: return "?"
    try:
        d = float(d)
        return f"{d:.1f} км"
    except:
        return "?"


def run():
    today = date.today().isoformat()
    print(f"=== {today} ===")

    def tg_safe(text, label=""):
        try:
            tg_send(text)
        except Exception as e:
            print(f"TG error {label}: {e}")

    tg_safe(f"⏳ <b>Квартиры</b>: запуск {today}…", "start")

    seen = load_seen()
    print(f"Известно объявлений: {len(seen)}")

    # 1. Парсим
    print("Запуск парсера...")
    r = subprocess.run([sys.executable, str(DATA_DIR / "otodom_listings.py")],
                       capture_output=True, text=True, cwd=DATA_DIR)
    if r.returncode != 0:
        tg_safe(f"❌ Ошибка парсера:\n{r.stderr[-500:]}")
        return
    print(r.stdout[-300:])

    # Считаем сколько объявлений спарсили
    _rows_after_parse = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    _cache_file = DATA_DIR / "coords_cache.json"
    _cache = json.loads(_cache_file.read_text()) if _cache_file.exists() else {}
    _need_coords = sum(1 for row in _rows_after_parse if row["id"] not in _cache)
    tg_safe(f"📋 <b>Спарсили:</b> {len(_rows_after_parse)} объявлений, нужно загрузить: {_need_coords}", "parse")

    # 2. Координаты с otodom — только новые, кэшируем по id
    print("Получаем координаты...")
    if _need_coords:
        tg_safe(f"🌐 Загружаем {_need_coords} страниц для координат и фото…", "coords")
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
print(f'coords done, fetched={{fetched}}, cache size={{len(cache)}}')
"""], cwd=DATA_DIR, capture_output=True, text=True)

    tg_safe("🗺 Считаем маршруты…", "osrm")

    # 3. Маршруты OSRM
    print("Считаем маршруты...")
    subprocess.run([sys.executable, str(DATA_DIR / "fetch_drive_distances.py")],
                   capture_output=True, text=True, cwd=DATA_DIR)

    # 4. Читаем результат
    rows = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))

    # Страховочный фильтр: исключаем НП не из нашей зоны
    # Фильтр по расстоянию до трамвая: квартиры ≤3 км, дома ≤8 км
    def _f(v): return float(v) if v else None
    def _tram_ok(r):
        d = _f(r.get("drive_tram_km")) or _f(r.get("dist_tram"))
        if d is None: return False
        limit = 8.0 if r.get("type") == "dom" else 3.0
        return d <= limit
    rows = [r for r in rows if _tram_ok(r)]
    # Маппинг НП внутри Познани
    for r in rows:
        if r.get("city") in POZNAN_SUBURBS:
            r["district"] = r["city"]
            r["city"] = "Poznań"

    # Проверяем новые районы среди уже отфильтрованных объявлений
    from score import DISTRICT_SCORES
    known = set(DISTRICT_SCORES.keys())
    found_districts = set()
    for r in rows:
        city = r.get("city", "")
        district = r.get("district", "")
        if city == "Poznań" and district:
            found_districts.add(district)
        else:
            found_districts.add(city)
    new_districts = found_districts - known
    if new_districts:
        names = ", ".join(sorted(new_districts))
        tg_safe(f"⚠️ <b>Новые районы в листинге:</b> {names}\nНе найдены в score.py — нужно добавить оценку и описание вручную.", "new_districts")
    all_ids = set(r["id"] for r in rows)
    new_rows = [r for r in rows if r["id"] not in seen]
    for r in new_rows:
        r["_score"] = score_from_csv(r)
    new_rows.sort(key=lambda r: r["_score"], reverse=True)
    print(f"Новых объявлений: {len(new_rows)}")
    tg_safe(f"🏠 <b>Новых объявлений: {len(new_rows)}</b> (всего в базе: {len(rows)})", "new")

    # 5. Шлём в Telegram
    if not new_rows:
        pass  # уже сообщили выше
    else:
        for r in new_rows:
            price = f"{int(float(r['price_zl'])):,}".replace(",", " ") + " zł" if r.get("price_zl") else "цена не указана"
            area = f"{r['area_m2']} м²" if r.get("area_m2") else ""
            tp = "кв." if r["type"] == "mieszkanie" else "дом"
            # Для Познани показываем район, для пригородов — город
            city = r.get("city", "")
            district = r.get("district", "")
            location = district if (city == "Poznań" and district) else city
            dist_r = fmt_dist(r.get("drive_ratusz_km") or r.get("dist_km"))
            dist_t = fmt_dist(r.get("drive_tram_km") or r.get("dist_tram"))
            tram = r.get("drive_tram_name") or r.get("tram_name") or ""
            photos = [u for u in (r.get("photo_url") or "").split(",") if u]
            score = r.get("_score", 0)
            tram_min = round(int(float(r["drive_tram_dur_s"])) / 60) if r.get("drive_tram_dur_s") else None
            tram_line = f"🚊 Трамвай: {tram_min} мин ({tram})" if tram_min and tram else (f"🚊 Трамвай: {dist_t}" + (f" ({tram})" if tram else ""))
            tp_full = "Квартира" if r["type"] == "mieszkanie" else "Дом"
            caption = (
                f"<b>{score}/10</b>\n"
                f"{_escape(r['title'])}\n"
                f"📍 {location}\n"
                f"<b>{price}</b>  ·  {area}  ·  {tp_full}\n"
                f"{tram_line}  ·  🏛 Центр: {dist_r}\n"
                f"<a href=\"{r['url']}\">На Otodom →</a>"
            )
            try:
                if len(photos) >= 2:
                    tg_send_media_group(photos, caption)
                elif photos:
                    tg_send_photo(photos[0], caption)
                else:
                    tg_send(caption)
                time.sleep(0.3)
            except Exception as e:
                print(f"TG error: {e}")
                try:
                    tg_send(caption)
                except Exception as e2:
                    print(f"TG fallback error: {e2}")


    # 6. Обновляем seen
    save_seen(all_ids)
    print("Готово")
    tg_safe(f"✅ <b>Квартиры</b>: готово. Всего {len(rows)}, новых {len(new_rows)}", "finish")


if __name__ == "__main__":
    run()
