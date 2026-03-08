#!/usr/bin/env python3
"""
Warsaw Flat Scraper
Scrapes Otodom, OLX, Gratka, Morizon for flats in Ochota & Włochy.
Scores each flat, tracks price changes, notifies via Discord.
"""

import json
import os
import time
import hashlib
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

DISCORD_WEBHOOK = os.environ["DISCORD_WEBHOOK"]
DB_FILE = Path("flats_db.json")

SCORE_NOTIFY_THRESHOLD = 130   # alert if score >= this
PRICE_DROP_NOTIFY_PCT  = 2     # alert if price drops >= 2%

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
}

# ── Scoring (mirrors your React app exactly) ──────────────────────────────────

PREMIUM_STREETS = {
    "Ochota": ["włodarzewska", "grójecka", "bitwy warszawskiej", "tarczyńska", "sękocińska", "szczęśliwicka"],
    "Włochy": ["włodarzewska", "popularowa", "1 sierpnia", "aleje jerozolimskie", "hynka"],
}

def score_flat(flat: dict) -> int:
    score = 100
    price, area = flat.get("price", 0), flat.get("area", 1)
    if not price or not area:
        return 0
    ppm2 = price / area

    # 1. Price/m² — 40 pts
    if ppm2 < 10000:   score += 40
    elif ppm2 < 12000: score += 28
    elif ppm2 < 14000: score += 14
    elif ppm2 < 16000: score += 4
    elif ppm2 < 18000: score -= 8
    elif ppm2 < 20000: score -= 18
    else:              score -= 30

    # 2. Location — 30 pts
    district = flat.get("district", "")
    street   = flat.get("street", "").lower()
    premium  = PREMIUM_STREETS.get(district, [])
    if any(s in street for s in premium): score += 30
    elif district == "Ochota":            score += 12
    elif district == "Włochy":            score += 8

    # 3. Czynsz — 20 pts
    czynsz = flat.get("czynsz", 0)
    if czynsz:
        if czynsz < 400:    score += 20
        elif czynsz < 600:  score += 13
        elif czynsz < 800:  score += 6
        elif czynsz < 1000: score -= 4
        elif czynsz > 1200: score -= 18
        else:               score -= 10

    # 4. Build year — 10 pts
    year = flat.get("year", 0)
    if year:
        if year >= 2000:   score += 10
        elif year >= 1990: score += 4
        elif year >= 1980: score += 0
        elif year < 1970:  score -= 8

    # 5. Area sweet spot ~50m²
    if 48 <= area <= 52:   score += 16
    elif 43 <= area <= 57: score += 10
    elif 38 <= area <= 63: score += 4
    elif area < 35 or area > 75: score -= 8

    return max(0, min(score, 200))

def score_label(score: int) -> str:
    if score >= 155: return "🌟 Exceptional"
    if score >= 130: return "✅ Very Good"
    if score >= 110: return "👍 Good"
    if score >= 90:  return "😐 Average"
    return "👎 Below avg"

# ── Database ──────────────────────────────────────────────────────────────────

def load_db() -> dict:
    if DB_FILE.exists():
        return json.loads(DB_FILE.read_text(encoding="utf-8"))
    return {}

def save_db(db: dict):
    DB_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")

def flat_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]

# ── Scrapers ──────────────────────────────────────────────────────────────────

def get_soup(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️  Failed to fetch {url}: {e}")
        return None

def parse_price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None

def parse_area(text: str) -> float | None:
    m = re.search(r"([\d,\.]+)\s*m", text or "")
    if m:
        return float(m.group(1).replace(",", "."))
    return None

def parse_year(text: str) -> int | None:
    m = re.search(r"(19|20)\d{2}", text or "")
    return int(m.group()) if m else None

# ── Otodom ────────────────────────────────────────────────────────────────────

OTODOM_SEARCHES = [
    ("Ochota",  "https://www.otodom.pl/pl/oferty/sprzedaz/mieszkanie/warszawa/ochota?distanceRadius=0&viewType=listing"),
    ("Włochy",  "https://www.otodom.pl/pl/oferty/sprzedaz/mieszkanie/warszawa/wlochy?distanceRadius=0&viewType=listing"),
]

def scrape_otodom() -> list[dict]:
    results = []
    for district, url in OTODOM_SEARCHES:
        print(f"  Otodom → {district}")
        soup = get_soup(url)
        if not soup:
            continue
        # Otodom renders via Next.js — grab JSON from __NEXT_DATA__
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if not script:
            continue
        try:
            data = json.loads(script.string)
            items = (data.get("props", {})
                        .get("pageProps", {})
                        .get("data", {})
                        .get("searchAds", {})
                        .get("items", []))
            for item in items:
                price = item.get("totalPrice", {}).get("value")
                area  = item.get("areaInSquareMeters")
                slug  = item.get("slug", "")
                listing_url = f"https://www.otodom.pl/pl/oferta/{slug}"
                results.append({
                    "source":   "Otodom.pl",
                    "district": district,
                    "title":    item.get("title", ""),
                    "street":   item.get("location", {}).get("address", {}).get("street", {}).get("name", ""),
                    "price":    int(price) if price else None,
                    "area":     float(area) if area else None,
                    "czynsz":   None,
                    "year":     None,
                    "url":      listing_url,
                })
        except Exception as e:
            print(f"    parse error: {e}")
        time.sleep(1.5)
    return results

# ── OLX ───────────────────────────────────────────────────────────────────────

OLX_SEARCHES = [
    ("Ochota", "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/warszawa/?search%5Bfilter_float_price%3Afrom%5D=300000&search%5Bdistrict_id%5D=171"),
    ("Włochy", "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/warszawa/?search%5Bfilter_float_price%3Afrom%5D=300000&search%5Bdistrict_id%5D=183"),
]

def scrape_olx() -> list[dict]:
    results = []
    for district, url in OLX_SEARCHES:
        print(f"  OLX → {district}")
        soup = get_soup(url)
        if not soup:
            continue
        # OLX embeds listing data in window.__PRERENDERED_STATE__
        script = soup.find("script", string=re.compile("window.__PRERENDERED_STATE__"))
        if not script:
            # fallback: parse listing cards
            cards = soup.select("div[data-cy='l-card']")
            for card in cards:
                title_el = card.select_one("h6")
                price_el = card.select_one("p[data-testid='ad-price']")
                link_el  = card.select_one("a")
                price = parse_price(price_el.text if price_el else "")
                area  = parse_area(title_el.text if title_el else "")
                href  = link_el["href"] if link_el else ""
                listing_url = href if href.startswith("http") else f"https://www.olx.pl{href}"
                results.append({
                    "source":   "OLX.pl",
                    "district": district,
                    "title":    title_el.text.strip() if title_el else "",
                    "street":   "",
                    "price":    price,
                    "area":     area,
                    "czynsz":   None,
                    "year":     None,
                    "url":      listing_url,
                })
            time.sleep(1.5)
            continue
        try:
            raw = re.search(r"window.__PRERENDERED_STATE__\s*=\s*(.+?);\s*</script>", str(script), re.DOTALL)
            if raw:
                state = json.loads(raw.group(1))
                listings = state.get("listing", {}).get("listing", {}).get("ads", [])
                for ad in listings:
                    price_obj = next((p for p in ad.get("params", []) if p.get("key") == "price"), None)
                    area_obj  = next((p for p in ad.get("params", []) if p.get("key") == "m"), None)
                    price = parse_price(price_obj.get("value", {}).get("label", "") if price_obj else "")
                    area  = parse_area(area_obj.get("value", {}).get("label", "") if area_obj else "")
                    results.append({
                        "source":   "OLX.pl",
                        "district": district,
                        "title":    ad.get("title", ""),
                        "street":   "",
                        "price":    price,
                        "area":     area,
                        "czynsz":   None,
                        "year":     None,
                        "url":      ad.get("url", ""),
                    })
        except Exception as e:
            print(f"    parse error: {e}")
        time.sleep(1.5)
    return results

# ── Gratka ────────────────────────────────────────────────────────────────────

GRATKA_SEARCHES = [
    ("Ochota", "https://gratka.pl/nieruchomosci/mieszkania/warszawa/ochota?transakcja=sprzedaz"),
    ("Włochy", "https://gratka.pl/nieruchomosci/mieszkania/warszawa/wlochy?transakcja=sprzedaz"),
]

def scrape_gratka() -> list[dict]:
    results = []
    for district, url in GRATKA_SEARCHES:
        print(f"  Gratka → {district}")
        soup = get_soup(url)
        if not soup:
            continue
        cards = soup.select("article.offer-item")
        for card in cards:
            title_el = card.select_one("h2.offer-item__title a") or card.select_one("a.offer-item__header-title")
            price_el = card.select_one(".offer-item__price")
            area_el  = card.select_one("[data-testid='surface']") or card.select_one(".parameters__value")
            link_el  = card.select_one("a[href]")
            results.append({
                "source":   "Gratka.pl",
                "district": district,
                "title":    title_el.text.strip() if title_el else "",
                "street":   "",
                "price":    parse_price(price_el.text if price_el else ""),
                "area":     parse_area(area_el.text if area_el else ""),
                "czynsz":   None,
                "year":     None,
                "url":      link_el["href"] if link_el else url,
            })
        time.sleep(1.5)
    return results

# ── Morizon ───────────────────────────────────────────────────────────────────

MORIZON_SEARCHES = [
    ("Ochota", "https://www.morizon.pl/mieszkania/warszawa/ochota/?ps%5Btransaction%5D=1"),
    ("Włochy", "https://www.morizon.pl/mieszkania/warszawa/wlochy/?ps%5Btransaction%5D=1"),
]

def scrape_morizon() -> list[dict]:
    results = []
    for district, url in MORIZON_SEARCHES:
        print(f"  Morizon → {district}")
        soup = get_soup(url)
        if not soup:
            continue
        cards = soup.select("div.propertyListItem") or soup.select("li.PropertyCard")
        for card in cards:
            title_el = card.select_one("h2 a") or card.select_one("a.property-url")
            price_el = card.select_one(".price") or card.select_one("[class*='price']")
            area_el  = card.select_one("[class*='area']") or card.select_one("[class*='size']")
            link_el  = card.select_one("a[href]")
            href = link_el["href"] if link_el else ""
            listing_url = href if href.startswith("http") else f"https://www.morizon.pl{href}"
            results.append({
                "source":   "Morizon.pl",
                "district": district,
                "title":    title_el.text.strip() if title_el else "",
                "street":   "",
                "price":    parse_price(price_el.text if price_el else ""),
                "area":     parse_area(area_el.text if area_el else ""),
                "czynsz":   None,
                "year":     None,
                "url":      listing_url,
            })
        time.sleep(1.5)
    return results

# ── Discord notifications ─────────────────────────────────────────────────────

SOURCE_COLORS = {
    "Otodom.pl":  0xFF6B35,
    "OLX.pl":     0x00A651,
    "Gratka.pl":  0x0057B7,
    "Morizon.pl": 0x8B3FDE,
}

def fmt_pln(n) -> str:
    if not n:
        return "?"
    return f"{int(n):,} PLN".replace(",", " ")

def send_discord(flat: dict, reason: str, score: int, old_price: int | None = None):
    ppm2 = int(flat["price"] / flat["area"]) if flat.get("price") and flat.get("area") else None
    fields = [
        {"name": "💰 Price",      "value": fmt_pln(flat.get("price")), "inline": True},
        {"name": "📐 Area",       "value": f"{flat.get('area', '?')} m²",  "inline": True},
        {"name": "📊 Price/m²",   "value": fmt_pln(ppm2),              "inline": True},
        {"name": "🏘️ District",   "value": flat.get("district", "?"),  "inline": True},
        {"name": "🛣️ Street",     "value": flat.get("street") or "?",  "inline": True},
        {"name": "🏗️ Year",       "value": str(flat.get("year") or "?"), "inline": True},
        {"name": "🧾 Czynsz/mo",  "value": fmt_pln(flat.get("czynsz")), "inline": True},
        {"name": "⭐ Score",       "value": f"{score}  {score_label(score)}", "inline": True},
    ]
    if old_price and flat.get("price"):
        drop = old_price - flat["price"]
        pct  = drop / old_price * 100
        fields.append({"name": "📉 Price drop", "value": f"-{fmt_pln(drop)} ({pct:.1f}%)", "inline": True})

    embed = {
        "title":       f"🏠 {flat.get('title', 'New listing')}",
        "description": reason,
        "url":         flat.get("url", ""),
        "color":       SOURCE_COLORS.get(flat.get("source", ""), 0x888888),
        "fields":      fields,
        "footer":      {"text": f"{flat.get('source', '')} • {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
    }
    payload = {"embeds": [embed]}
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        r.raise_for_status()
        print(f"    ✅ Discord notified: {flat.get('title', '')[:50]}")
    except Exception as e:
        print(f"    ❌ Discord failed: {e}")

def send_summary(new_count: int, drop_count: int, total_scraped: int):
    embed = {
        "title":       "📋 Daily Warsaw Flats Summary",
        "color":       0x00C896,
        "description": (
            f"Scrape complete at {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"**{total_scraped}** listings scanned\n"
            f"**{new_count}** new interesting flats (score ≥ {SCORE_NOTIFY_THRESHOLD})\n"
            f"**{drop_count}** price drops detected"
        ),
        "footer": {"text": "Ochota · Włochy | Otodom · OLX · Gratka · Morizon"},
    }
    requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print(f"\n🔍 Warsaw Flat Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    db = load_db()
    all_listings: list[dict] = []

    print("\n[Otodom]")
    all_listings += scrape_otodom()
    print("\n[OLX]")
    all_listings += scrape_olx()
    print("\n[Gratka]")
    all_listings += scrape_gratka()
    print("\n[Morizon]")
    all_listings += scrape_morizon()

    print(f"\n📦 Total scraped: {len(all_listings)} listings")
    print("─" * 55)

    new_count  = 0
    drop_count = 0

    for flat in all_listings:
        if not flat.get("price") or not flat.get("area"):
            continue

        fid   = flat_id(flat["url"])
        score = score_flat(flat)

        if fid not in db:
            # Brand new listing
            db[fid] = {
                **flat,
                "score":        score,
                "first_seen":   datetime.now().isoformat(),
                "last_seen":    datetime.now().isoformat(),
                "price_history": [{"date": datetime.now().strftime("%Y-%m"), "price": flat["price"]}],
            }
            if score >= SCORE_NOTIFY_THRESHOLD:
                print(f"  🆕 NEW [{score}] {flat.get('title', '')[:50]}")
                send_discord(flat, f"🆕 New listing — score **{score}**", score)
                new_count += 1
        else:
            # Existing — check price drop
            existing   = db[fid]
            old_price  = existing.get("price")
            new_price  = flat["price"]
            db[fid]["last_seen"] = datetime.now().isoformat()

            if old_price and new_price and new_price < old_price:
                drop_pct = (old_price - new_price) / old_price * 100
                if drop_pct >= PRICE_DROP_NOTIFY_PCT:
                    db[fid]["price"] = new_price
                    db[fid]["score"] = score
                    db[fid].setdefault("price_history", []).append(
                        {"date": datetime.now().strftime("%Y-%m"), "price": new_price}
                    )
                    print(f"  📉 DROP [{score}] {flat.get('title', '')[:50]} -{drop_pct:.1f}%")
                    send_discord(flat, f"📉 Price dropped **{drop_pct:.1f}%**", score, old_price)
                    drop_count += 1

    save_db(db)
    send_summary(new_count, drop_count, len(all_listings))

    print(f"\n✅ Done. New alerts: {new_count}, Price drops: {drop_count}")
    print(f"💾 Database: {len(db)} flats tracked\n")

if __name__ == "__main__":
    run()
