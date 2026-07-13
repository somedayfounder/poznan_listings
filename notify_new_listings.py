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

EXCLUDED_CITIES = {"Komorniki", "Plewiska", "Robakowo", "Nowinki", "Wierzyce",
                   "Dachowa", "Rokietnica", "Murowana Goślina", "Bolechowo",
                   "Swarzędz", "Mosina", "Luboń", "Czerwonak"}
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

    try:
        tg_send(f"⏳ <b>Квартиры</b>: запуск {today}…")
    except Exception as e:
        print(f"TG start error: {e}")

    seen = load_seen()
    print(f"Известно объявлений: {len(seen)}")

    # 1. Парсим
    print("Запуск парсера...")
    r = subprocess.run([sys.executable, str(DATA_DIR / "otodom_listings.py")],
                       capture_output=True, text=True, cwd=DATA_DIR)
    if r.returncode != 0:
        tg_send(f"❌ Ошибка парсера:\n{r.stderr[-500:]}")
        return
    print(r.stdout[-300:])

    # 2. Координаты с otodom
    print("Получаем координаты...")
    subprocess.run([sys.executable, "-c", f"""
import csv, re, json, time
from urllib.request import Request, urlopen
HEADERS = {{'User-Agent': 'Mozilla/5.0'}}
rows = list(csv.DictReader(open('listings_latest.csv', encoding='utf-8-sig')))
for r in rows:
    if r.get('lat') and r.get('lon'): continue
    try:
        req = Request(r['url'], headers=HEADERS)
        html = urlopen(req, timeout=15).read().decode('utf-8','replace')
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        ad = json.loads(m.group(1))['props']['pageProps'].get('ad') or {{}}
        coords = (ad.get('location') or {{}}).get('coordinates') or {{}}
        if coords.get('latitude'):
            r['lat'] = round(coords['latitude'], 5)
            r['lon'] = round(coords['longitude'], 5)
        images = ad.get('images') or []
        urls = [img.get('large') or img.get('medium') or '' for img in images[:3] if img.get('large') or img.get('medium')]
        if urls:
            r['photo_url'] = ','.join(urls)
    except: pass
    time.sleep(0.3)
fields = list(rows[0].keys())
for extra in ['photo_url','drive_ratusz_km','drive_tram_km','drive_tram_name','drive_rail_km','drive_rail_name']:
    if extra not in fields: fields.append(extra)
import csv as c2
with open('listings_latest.csv','w',newline='',encoding='utf-8-sig') as f:
    w = c2.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader(); w.writerows(rows)
print('coords done')
"""], cwd=DATA_DIR, capture_output=True, text=True)

    # 3. Маршруты OSRM
    print("Считаем маршруты...")
    subprocess.run([sys.executable, str(DATA_DIR / "fetch_drive_distances.py")],
                   capture_output=True, text=True, cwd=DATA_DIR)

    # 4. Читаем результат
    rows = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    # Страховочный фильтр: исключаем НП не из нашей зоны
    rows = [r for r in rows if r.get("city") not in EXCLUDED_CITIES]
    # Маппинг НП внутри Познани
    for r in rows:
        if r.get("city") in POZNAN_SUBURBS:
            r["district"] = r["city"]
            r["city"] = "Poznań"
    all_ids = set(r["id"] for r in rows)
    new_rows = [r for r in rows if r["id"] not in seen]
    print(f"Новых объявлений: {len(new_rows)}")

    # 5. Шлём в Telegram
    if not new_rows:
        tg_send(f"📋 {today}: новых объявлений нет (всего {len(rows)})")
    else:
        tg_send(f"🏠 <b>{today}: {len(new_rows)} новых объявлений</b>")
        for r in new_rows[:30]:  # не больше 30 за раз
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
            caption = (
                f"<b>{_escape(r['title'])}</b>\n"
                f"{tp} · {area} · {price}\n"
                f"📍 {location}\n"
                f"🏛 до ратуши {dist_r} · 🚊 до трамвая {dist_t}"
                + (f" ({tram})" if tram else "") + "\n"
                f"<a href=\"{r['url']}\">Открыть →</a>"
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

        if len(new_rows) > 30:
            tg_send(f"... и ещё {len(new_rows)-30} объявлений")

    # 6. Обновляем seen
    save_seen(all_ids)
    print("Готово")
    try:
        tg_send(f"✅ <b>Квартиры</b>: готово. Всего {len(rows)}, новых {len(new_rows)}")
    except Exception as e:
        print(f"TG finish error: {e}")


if __name__ == "__main__":
    run()
