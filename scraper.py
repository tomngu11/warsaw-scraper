#!/usr/bin/env python3
"""
Warsaw Flat Scraper — Otodom JSON API edition
Scrapes Otodom's internal API for flats in Ochota & Włochy.
Scores each flat, tracks price changes, notifies via Discord.
"""

import json
import os
import time
import hashlib
from datetime import datetime
from pathlib import Path

import requests

# ── Config ────────────────────────────────────────────────────────────────────

DISCORD_WEBHOOK        = os.environ["DISCORD_WEBHOOK"]
DB_FILE                = Path("flats_db.json")
SCORE_NOTIFY_THRESHOLD = 130
PRICE_DROP_NOTIFY_PCT  = 2

# ── Otodom API ────────────────────────────────────────────────────────────────
# District IDs: Ochota = 39, Włochy = 44

OTODOM_API = "https://www.otodom.pl/api/offers/"

SEARCHES = [
    {"district": "Ochota", "subregion": "39"},
    {"district": "Włochy", "subregion": "44"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Referer": "https://www.otodom.pl/",
}

def fetch_otodom_page(district_id: str, page: int = 1) -> dict:
    params = {
        "distanceRadius": 0,
        "market": "ALL",
        "ownerTypeSingleSelect": "ALL",
        "viewType": "listing",
        "by": "DEFAULT",
        "direction": "DESC",
        "limit": 36,
        "page": page,
        "subregionId": district_id,
        "filterFloat_price.gte": 300000,
        "filterFloat_price.lte": 2000000,
        "category": "FLAT",
        "transactionType": "SELL",
    }
    try:
        r = requests.get(OTODOM_API, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  WARNING: Otodom API error (district {district_id}, page {page}): {e}")
        return {}

def fetch_otodom_listings(district: str, district_id: str) -> list:
    results = []
    for page in range(1, 4):
        print(f"  Otodom API -> {district} page {page}")
        data = fetch_otodom_page(district_id, page)
        items = data.get("items", [])
        if not items:
            print(f"    No items on page {page}, stopping.")
            break
        for item in items:
            loc    = item.get("location", {})
            addr   = loc.get("address", {})
            street = addr.get("street", {}).get("name", "") or ""

            price_obj = item.get("totalPrice") or item.get("price") or {}
            price     = price_obj.get("value") if isinstance(price_obj, dict) else price_obj

            area = item.get("areaInSquareMeters") or item.get("area")

            czynsz = None
            year   = None
            for ch in item.get("characteristics", []):
                k = ch.get("key", "")
                v = ch.get("value", "")
                if k in ("rent", "czynsz", "additional_costs"):
                    try: czynsz = int(float(str(v).replace(" ", "").replace(",", ".")))
                    except Exception: pass
                if k == "build_year":
                    try: year = int(v)
                    except Exception: pass

            slug = item.get("slug", "")
            url  = f"https://www.otodom.pl/pl/oferta/{slug}" if slug else ""
            imgs = item.get("images") or [{}]
            img  = imgs[0].get("large", "") if imgs else ""

            results.append({
                "source":   "Otodom.pl",
                "district": district,
                "title":    item.get("title", "").strip(),
                "street":   street,
                "price":    int(price) if price else None,
                "area":     float(area) if area else None,
                "czynsz":   czynsz,
                "year":     year,
                "url":      url,
                "image":    img,
            })
        total_pages = data.get("pagination", {}).get("totalPages", 1)
        if page >= total_pages:
            break
        time.sleep(1.2)
    return results

# ── Scoring ───────────────────────────────────────────────────────────────────

PREMIUM_STREETS = {
    "Ochota": ["wlodarzewska", "grOjecka", "bitwy warszawskiej", "tarczynska", "sekocinska", "szczesliwicka",
               "włodarzewska", "grójecka", "tarczyńska", "sękocińska", "szczęśliwicka"],
    "Wlochy": ["wlodarzewska", "popularowa", "1 sierpnia", "aleje jerozolimskie", "hynka",
               "włodarzewska"],
}

def score_flat(flat: dict) -> int:
    score = 100
    price = flat.get("price", 0)
    area  = flat.get("area", 1) or 1
    if not price: return 0
    ppm2 = price / area

    # 1. Price/m2 — 40pts
    if ppm2 < 10000:   score += 40
    elif ppm2 < 12000: score += 28
    elif ppm2 < 14000: score += 14
    elif ppm2 < 16000: score += 4
    elif ppm2 < 18000: score -= 8
    elif ppm2 < 20000: score -= 18
    else:              score -= 30

    # 2. Location — 30pts
    district = flat.get("district", "")
    street   = flat.get("street", "").lower()
    premium  = PREMIUM_STREETS.get(district, []) + PREMIUM_STREETS.get("Wlochy" if district == "Włochy" else district, [])
    if any(s.lower() in street for s in premium if s): score += 30
    elif district == "Ochota": score += 12
    elif district == "Włochy": score += 8

    # 3. Czynsz — 20pts
    czynsz = flat.get("czynsz") or 0
    if czynsz:
        if czynsz < 400:    score += 20
        elif czynsz < 600:  score += 13
        elif czynsz < 800:  score += 6
        elif czynsz < 1000: score -= 4
        elif czynsz > 1200: score -= 18
        else:               score -= 10

    # 4. Build year — 10pts (post-2000 = full marks)
    year = flat.get("year") or 0
    if year:
        if year >= 2000:   score += 10
        elif year >= 1990: score += 4
        elif year < 1970:  score -= 8

    # 5. Area sweet spot ~50m2
    if 48 <= area <= 52:         score += 16
    elif 43 <= area <= 57:       score += 10
    elif 38 <= area <= 63:       score += 4
    elif area < 35 or area > 75: score -= 8

    return max(0, min(score, 200))

def score_label(score: int) -> str:
    if score >= 155: return "EXCEPTIONAL"
    if score >= 130: return "VERY GOOD"
    if score >= 110: return "GOOD"
    if score >= 90:  return "AVERAGE"
    return "BELOW AVG"

# ── Database ──────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    return {}

def save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def flat_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

# ── Discord ───────────────────────────────────────────────────────────────────

def fmt_pln(n) -> str:
    if not n: return "?"
    return f"{int(n):,} PLN".replace(",", " ")

def send_discord_alert(flat: dict, reason: str, score: int, old_price=None):
    ppm2 = int(flat["price"] / flat["area"]) if flat.get("price") and flat.get("area") else None
    fields = [
        {"name": "Price",     "value": fmt_pln(flat.get("price")), "inline": True},
        {"name": "Area",      "value": f"{flat.get('area', '?')} m2", "inline": True},
        {"name": "Price/m2",  "value": fmt_pln(ppm2),              "inline": True},
        {"name": "District",  "value": flat.get("district", "?"),  "inline": True},
        {"name": "Street",    "value": flat.get("street") or "?",  "inline": True},
        {"name": "Year",      "value": str(flat.get("year") or "?"), "inline": True},
        {"name": "Czynsz/mo", "value": fmt_pln(flat.get("czynsz")), "inline": True},
        {"name": "Score",     "value": f"{score} — {score_label(score)}", "inline": True},
    ]
    if old_price and flat.get("price"):
        drop = old_price - flat["price"]
        pct  = drop / old_price * 100
        fields.append({"name": "Price drop", "value": f"-{fmt_pln(drop)} ({pct:.1f}%)", "inline": True})

    embed = {
        "title":       flat.get("title", "New listing")[:200],
        "description": reason,
        "url":         flat.get("url", "https://www.otodom.pl"),
        "color":       0xFF6B35,
        "fields":      fields,
        "footer":      {"text": f"Otodom.pl | {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
    }
    if flat.get("image"):
        embed["thumbnail"] = {"url": flat["image"]}

    try:
        r = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        r.raise_for_status()
        print(f"    Discord sent: {flat.get('title', '')[:50]}")
    except Exception as e:
        print(f"    Discord failed: {e}")
    time.sleep(0.5)

def send_summary(new_count: int, drop_count: int, total_scraped: int):
    embed = {
        "title":       "Daily Warsaw Flats Summary",
        "color":       0x00C896,
        "description": (
            f"Scrape complete: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"**{total_scraped}** listings scanned\n"
            f"**{new_count}** new interesting flats (score >= {SCORE_NOTIFY_THRESHOLD})\n"
            f"**{drop_count}** price drops detected"
        ),
        "footer": {"text": "Ochota + Wlochy | Otodom.pl"},
    }
    try:
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)
        print("  Summary sent to Discord.")
    except Exception as e:
        print(f"  Summary failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print(f"\nWarsaw Flat Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    db           = load_db()
    all_listings = []

    for s in SEARCHES:
        listings = fetch_otodom_listings(s["district"], s["subregion"])
        print(f"  -> {len(listings)} listings for {s['district']}")
        all_listings += listings

    print(f"\nTotal scraped: {len(all_listings)}")
    print("-" * 55)

    new_count  = 0
    drop_count = 0

    for flat in all_listings:
        if not flat.get("price") or not flat.get("area") or not flat.get("url"):
            continue

        fid   = flat_id(flat["url"])
        score = score_flat(flat)

        if fid not in db:
            db[fid] = {
                **flat,
                "score":         score,
                "first_seen":    datetime.now().isoformat(),
                "last_seen":     datetime.now().isoformat(),
                "price_history": [{"date": datetime.now().strftime("%Y-%m"), "price": flat["price"]}],
            }
            if score >= SCORE_NOTIFY_THRESHOLD:
                print(f"  NEW [{score}] {flat.get('title', '')[:50]}")
                send_discord_alert(flat, f"New listing — score **{score}**", score)
                new_count += 1
        else:
            old_price = db[fid].get("price")
            new_price = flat["price"]
            db[fid]["last_seen"] = datetime.now().isoformat()
            if old_price and new_price < old_price:
                drop_pct = (old_price - new_price) / old_price * 100
                if drop_pct >= PRICE_DROP_NOTIFY_PCT:
                    db[fid]["price"] = new_price
                    db[fid]["score"] = score
                    db[fid].setdefault("price_history", []).append(
                        {"date": datetime.now().strftime("%Y-%m"), "price": new_price}
                    )
                    print(f"  PRICE DROP [{score}] -{drop_pct:.1f}% {flat.get('title', '')[:40]}")
                    send_discord_alert(flat, f"Price dropped **{drop_pct:.1f}%**", score, old_price)
                    drop_count += 1

    save_db(db)
    send_summary(new_count, drop_count, len(all_listings))
    print(f"\nDone. New alerts: {new_count} | Price drops: {drop_count}")
    print(f"DB: {len(db)} flats tracked\n")

if __name__ == "__main__":
    run()
