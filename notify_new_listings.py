#!/usr/bin/env python3
"""
Шаг 6 ежедневного пайплайна (после геокодинга, дистанций, GPT):
- Читает pending_notify.json (список новых ID от scrape_listings.py)
- Фильтрует по расстоянию до трамвая
- Исследует новые районы
- Отправляет новые объявления в Telegram
"""
import csv, json, os, re, time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from score import score_from_csv, DISTRICT_SCORES, _DEFAULT_DISTRICT_SCORE

POZNAN_SUBURBS = {"Smochowice", "Naramowice", "Strzeszyn", "Morasko",
                  "Szczepankowo", "Spławie", "Głuszyna", "Fabianowo"}

DATA_DIR     = Path(__file__).parent
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


def fmt_dist(d):
    if d is None: return "?"
    try:
        return f"{float(d):.1f} км"
    except:
        return "?"


def save_seen(ids):
    SEEN_FILE.write_text(json.dumps(sorted(ids)))


def run():
    def tg_safe(text, label=""):
        try:
            tg_send(text)
        except Exception as e:
            print(f"TG error {label}: {e}")

    if not PENDING_FILE.exists():
        print("pending_notify.json не найден — нечего отправлять")
        return

    pending = json.loads(PENDING_FILE.read_text())
    new_ids  = set(pending["new_ids"])
    all_ids  = set(pending["all_ids"])
    photo_map = pending.get("photo_map", {})

    if not new_ids:
        tg_safe("✅ Новых объявлений нет", "nonew")
        save_seen(all_ids)
        return

    # Читаем обогащённый CSV (после geocode + drive + GPT)
    rows = list(csv.DictReader(open(DATA_DIR / "listings_latest.csv", encoding="utf-8-sig")))
    for r in rows:
        if r.get("city") in POZNAN_SUBURBS:
            r["district"] = r["city"]
            r["city"] = "Poznań"
        # Подставляем фото из кэша scraper-а (если в CSV нет)
        if not r.get("photo_url") and r["id"] in photo_map:
            r["photo_url"] = photo_map[r["id"]]

    _drive_cache_path = DATA_DIR / "drive_cache.json"
    _drive_cache = json.loads(_drive_cache_path.read_text()) if _drive_cache_path.exists() else {}

    def _f(v): return float(v) if v else None
    def _tram_ok(r):
        dk = f"{r.get('lat')},{r.get('lon')}"
        drv = _drive_cache.get(dk, {})
        d = drv.get("tram_km") or _f(r.get("drive_tram_km")) or _f(r.get("dist_tram"))
        if d is None: return False
        limit = 8.0 if r.get("type") == "dom" else 3.0
        return d <= limit

    new_rows = [r for r in rows if r["id"] in new_ids and _tram_ok(r)]
    if not new_rows:
        tg_safe("✅ Новые объявления не прошли фильтр по расстоянию до трамвая", "filtered")
        save_seen(all_ids)
        return

    tg_safe(f"✅ <b>{len(new_rows)}</b> новых прошли фильтр, проверяем районы…", "filtered_ok")

    # Новые районы → исследование
    from score import DISTRICT_SCORES as _DS
    known = set(_DS.keys())
    new_district_set = set()
    for r in new_rows:
        loc = r.get("district") if r.get("city") == "Poznań" and r.get("district") else r.get("city", "")
        if loc and loc not in known:
            new_district_set.add(loc)

    if new_district_set:
        names = ", ".join(sorted(new_district_set))
        tg_safe(f"🔎 Новые районы: <b>{names}</b> — запускаю исследование…", "new_districts")
        from research_new_district import research
        for i, d in enumerate(sorted(new_district_set)):
            if i > 0:
                time.sleep(3)
            try:
                research(d)
            except Exception as e:
                print(f"  research({d}) failed: {e}")
        import importlib, score as _score_mod
        importlib.reload(_score_mod)
        from score import score_from_csv as _sfc
    else:
        from score import score_from_csv as _sfc

    # Пересчитываем оценки
    for r in new_rows:
        r["_score"] = _sfc(r)
    new_rows.sort(key=lambda r: r["_score"], reverse=True)

    print(f"Новых объявлений к отправке: {len(new_rows)}")
    tg_safe(f"🏠 <b>Отправляю {len(new_rows)} объявлений</b>…", "sending")

    for r in new_rows:
        try:
            price = f"{int(float(r['price_zl'])):,}".replace(",", " ") + " zł" if r.get("price_zl") else "цена не указана"
        except (ValueError, TypeError):
            price = "цена не указана"
        area = f"{r['area_m2']} м²" if r.get("area_m2") else ""
        city = r.get("city", "")
        district = r.get("district", "")
        location = district if (city == "Poznań" and district) else city
        loc_key = district if district else city
        dist_sc = DISTRICT_SCORES.get(loc_key, _DEFAULT_DISTRICT_SCORE)
        location_str = f"{location} ({dist_sc}/10)"
        _dk = f"{r.get('lat')},{r.get('lon')}"
        _drv = _drive_cache.get(_dk, {})
        tram_min   = round(_drv["tram_dur_s"] / 60)   if _drv.get("tram_dur_s")   else None
        ratusz_min = round(_drv["ratusz_dur_s"] / 60) if _drv.get("ratusz_dur_s") else None
        dist_r_km  = _drv.get("ratusz_km") or r.get("drive_ratusz_km") or r.get("dist_km")
        tram       = _drv.get("tram_name") or r.get("drive_tram_name") or r.get("tram_name") or ""
        photos = [u for u in (r.get("photo_url") or "").split(",") if u]
        score = r.get("_score", 0)
        tram_line = (f"🚋 Трамвай: {tram_min} мин ({tram})" if tram_min and tram
                     else (f"🚋 Трамвай: {tram_min} мин" if tram_min else "🚋 Трамвай: нет данных"))
        center_str = f"{ratusz_min} мин ({fmt_dist(dist_r_km)})" if ratusz_min else fmt_dist(dist_r_km)
        tp_full = "Квартира" if r["type"] == "mieszkanie" else "Дом"
        caption = (
            f"<b>{score}/10</b>\n"
            f"{_escape(r['title'])}\n"
            f"📍 {location_str}\n"
            f"<b>{price}</b>  ·  {area}  ·  {tp_full}\n"
            f"{tram_line}\n"
            f"🏛 Центр: {center_str}\n"
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

    save_seen(all_ids)
    print("Готово")
    tg_safe(f"✅ <b>Квартиры</b>: готово. Всего {len(rows)}, новых {len(new_rows)}", "finish")


if __name__ == "__main__":
    run()
