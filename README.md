# Warsaw Flat Scraper 🏠

Scrapes Otodom, OLX, Gratka & Morizon daily for flats in **Ochota** and **Włochy**.  
Scores each listing and notifies you on **Discord** when something good appears.

---

## Setup (one-time, ~5 minutes)

### 1. Create a new GitHub repo

```bash
git init warsaw-scraper
cd warsaw-scraper
# copy scraper.py, requirements.txt, and .github/ folder here
git add .
git commit -m "init"
gh repo create warsaw-scraper --public --push
# or push manually to a repo you create on github.com
```

### 2. Get your Discord webhook URL

1. Open your Discord server
2. Go to **Server Settings → Integrations → Webhooks**
3. Click **New Webhook** → choose a channel → **Copy Webhook URL**

### 3. Add webhook to GitHub Secrets

1. Go to your repo on GitHub
2. **Settings → Secrets and variables → Actions → New repository secret**
3. Name: `DISCORD_WEBHOOK`
4. Value: paste your webhook URL
5. Click **Add secret**

### 4. Enable GitHub Actions

1. Go to the **Actions** tab in your repo
2. Click **"I understand my workflows, go ahead and enable them"**
3. Done — it will run every day at 9:00 AM Warsaw time automatically

### 5. Test it right now

1. Go to **Actions → Warsaw Flat Scraper → Run workflow**
2. Check your Discord channel within ~60 seconds

---

## How it works

| Step | What happens |
|------|-------------|
| Scrape | Fetches listings from all 4 sites for Ochota + Włochy |
| Score | Scores each flat (0–200) based on your priorities |
| Compare | Checks against the database for new listings & price drops |
| Notify | Sends Discord alerts for score ≥ 130 or price drop ≥ 2% |
| Summary | Sends a daily summary regardless |

## Scoring weights

| Factor | Weight | Sweet spot |
|--------|--------|-----------|
| Price/m² | 40 pts | < 12 000 PLN/m² |
| Location/street | 30 pts | Włodarzewska, Grójecka, etc. |
| Czynsz | 20 pts | < 600 PLN/mo |
| Build year | 10 pts | 2000 or newer |
| Area bonus | +16 pts | ~50 m² (48–52) |

## Tweaking thresholds

Edit `scraper.py` top section:

```python
SCORE_NOTIFY_THRESHOLD = 130   # lower = more alerts
PRICE_DROP_NOTIFY_PCT  = 2     # lower = more alerts
```

## Database

Listings are stored in `flats_db.json` (persisted via GitHub Actions cache between runs).  
Each entry tracks: price history, first seen, last seen, score.
