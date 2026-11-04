# Sourcing Copilot

Personal trend, sourcing and pricing system for a **China → Turkmenistan** resale business.
It tells you **what to order, where to buy it cheapest at good quality, what to sell it for, how long it
takes to arrive, and how far you can trust the seller.**

Works immediately with realistic demo data and **no keys at all**. Every integration (Telegram bot,
Telegram channel reader, Claude AI, YouTube, vidIQ import) is already coded and switches on by itself
the moment you paste its key into `.env` and restart.

---

## 1. Run it (one command)

You need Python 3.10+ (or Docker).

```bash
cd sourcing-copilot
./run.sh            # Mac / Linux   (Windows: double-click run.bat)
# or
docker compose up -d --build
```

Open **http://localhost:8000**. On your phone (same Wi-Fi): `http://<your-computer-ip>:8000`, then in the
browser menu choose **Add to Home Screen** to install it as an app (PWA).

First start loads **demo data** (products from a real vidIQ run, offers, sellers, shipments, sales).
When you go live: **More → Settings → Clear demo data** (or `python -m app.cli clear-mock`). Only demo
rows are deleted; anything you entered is kept.

Run the tests:

```bash
pip install -r requirements.txt
python -m pytest -q          # 105 tests: pricing, scoring, filter, sales parser, bot, adapters, API
```

### Why this stack
* **Python FastAPI + SQLite (SQLAlchemy)**: one process, one file database, easy to read with basic
  Python, and moves to Postgres by changing `DATABASE_URL`.
* **Frontend: plain JavaScript PWA served by the same app**: no Node/npm build step, nothing else to
  install, fast on phones, works offline for the last data you saw.
* **APScheduler in-process** for refresh jobs; **Telegram bot by long polling**, so it needs no public URL.

---

## 2. Screens

| Screen | What it does |
|---|---|
| **Home: "What should I order this week?"** | 5–10 ranked cards: photo, Trend Score, best offer, landing cost, selling price, profit, delivery, seller score, one-line explanation; **Add to order / Find offers / Watch / Dismiss** |
| **Trends** | All product ideas with Trend Score and rising/peaking/fading. Tap any score to see exactly which signals drove it, with links to the videos/posts |
| **Product** | Score history, top 3 offers side by side + why #1 wins, Taobao/1688 search buttons with Chinese terms, photo-search tip, Ashgabat prices (Telegram + your own), Turkmen/Russian post captions, all signals |
| **Add offer** | Paste link + screenshots → AI fills title, price tiers, sales, rating, reviews, store info, weight, review praises/complaints (English). Every field editable; works manually without AI |
| **Order** | Basket with quantities/variants, total ¥ and TMT, weight, cargo, profit, arrival window; copy/share list (EN/RU/TK) or send it to your person via the bot; "mark as ordered" creates a shipment |
| **Sales** | Type `2 hamster 250` and press Log. Stock updates automatically; stock table; recent sales |
| **Shipments** | ordered → China warehouse → in transit → arrived → sold out, plus delivered / cash received. Arrival adds stock |
| **Weekly report** | What sold, real margin per product, what to reorder (with quantity), what to stop buying |
| **Sellers** | Reliability 0–100 with reasons; log delays/damage/wrong items to adjust it |
| **Import** | vidIQ JSON box, TikTok Creative Center / Pinterest paste box, run live sources now |
| **Filter review** | See what was thrown away as noise and fix mistakes |
| **Settings / Setup** | All pricing rules, delivery defaults, channels, AI budget, demo data; every integration with status and step-by-step key instructions; one-tap Telegram chat approval |

Currency toggle (TMT / CNY / USD) and dark/light mode are in the top bar.

---

## 3. How the numbers work

**Pricing** (all editable in Settings):
```
goods_tmt   = (item_price_cny + china_domestic_shipping_cny) * yuan_to_tmt      # 3.1
cargo_tmt   = weight_kg * cargo_tmt_per_kg                                       # 145, real weight
landing_tmt = (goods_tmt + cargo_tmt + fee_per_item_tmt) * (1 + loss_buffer)     # fee 0, buffer 5%
sell_tmt    = landing_tmt * markup                                               # 2.0, rounded to nearest 5 (<100) / 10
```
Missing weight is estimated from the product type and clearly marked *estimate*. Price tiers apply
automatically for the basket quantity. The **local price check** compares your price with prices seen
in Turkmen Telegram channels or entered by you, and warns when you're above the market.

**Trend Score (0–100)**: social reach of product videos (Turkish weighted 1.0, Russian 0.6) 30 ·
outlier strength 10 · independent creators/platforms 15 · Google Trends 10 · Ashgabat Telegram listings 10
· **your own sales 25 (strongest)**. Fewer than **2 independent signals** caps the score at 35
("one viral video is not a trend"). **Rising / peaking / fading** compares the last 2 weeks of video
activity with the 2 weeks before, Google Trends slope, score history and your sales.

**Value Score (offers)**: landing cost vs cheapest 35 · quality (rating, reviews, buyer photos, repeat
buyers, sales, complaint penalties) 40 · seller reliability 25.

**Seller score**: store age, total sales, rating, response rate, refunds/disputes, factory vs reseller,
then adjusted by your logged experience (delay −6, damage −10, wrong item −12, missing −15, good +3).

**Delivery**: dispatch + China transit + cargo from Settings defaults; after 2+ real shipments it
learns from your history and shows a range (e.g. "18 to 25 days").

**Product filter**: rules in Turkish/Russian/Turkmen/English keep only product-centred content
(unboxing, haul, "nereden aldın", "link bio'da", "Çin'den sipariş", kilo toys, claw machines…) and drop
lip syncs, dances, skits, food jokes. Unclear items go to Claude (cheap model) when the key is set.
Branded/licensed items (Labubu, Disney, Sanrio, Pokémon, anime…) get a **customs seizure warning**.

---

## 4. Keys: where to get them (all free) and where to paste them

Copy `.env.example` to `.env` (run.sh does it for you), paste keys, restart. The **Setup** screen shows
each one as connected / not connected with the same instructions.

| Key in `.env` | Get it | Unlocks |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram → **@BotFather** → `/newbot` → copy token | Bot: digest, alerts, sales logging, commands, order lists + status buttons for your person |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION` (optional) | https://my.telegram.org → API development tools → create app; then run `python -m app.cli telegram-login` and paste the printed `TELEGRAM_SESSION=` line | More history from local channels. Without it, public channels are still read via their free `t.me/s/` web page |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com → API keys (add ~$5 credit) | Screenshot reading, review summaries, smarter filter, Chinese search terms, explanations, TK/RU captions |
| `YOUTUBE_API_KEY` | https://console.cloud.google.com → new project → enable **YouTube Data API v3** → Credentials → API key | Daily scan of Turkish & Russian Shorts |
| `TREND_IMPORT_SECRET` | make one: `python -m app.cli secret` | Lets the weekly vidIQ task post straight into the app |
| `APP_PASSWORD`, `APP_SECRET_KEY` | choose a password; `python -m app.cli secret` | Required before putting the app online |

The app **never asks for your TikTok, Instagram or Telegram passwords**: only tokens/keys in `.env`.

### Linking the Telegram bot
1. Put `TELEGRAM_BOT_TOKEN` in `.env`, restart (or Setup → **Reconnect**).
2. Send `/start` to your bot. In the app: **More → Setup → Telegram bot → "Approve as me"**.
3. Your person sends `/start` too → tap **"My person"**. Everyone else is ignored.

Bot commands: `/top`, `/stock`, `/profit`, `/orders`, `/shipments`, `/help`, and messages like
`sold 2 hamster 250` (2 units at 250 TMT each; add `total` for a total price; Russian `продал` and
Turkmen `satdym` work too). Your person gets `/shipments` with **Arrived / Delivered / Cash received**
buttons; you are notified when they tap one. The weekly digest goes out Monday 08:03 UTC (Settings).

---

## 5. Weekly vidIQ scheduled task

vidIQ is connected to your Claude account, which the app cannot call directly, so a Claude scheduled
task does the searching:

1. Open `scheduled_task_prompt.md`, replace `{{APP_URL}}` and `{{TREND_IMPORT_SECRET}}`.
2. In Claude, create a **scheduled task** (weekly, e.g. Monday 07:40) with the vidIQ connector enabled,
   and paste the prompt.
3. It makes at most **4 calls (20 credits) a week**: ~80–100 of your 150 monthly credits. It checks
   your balance first (free) and never loops.
4. If the app isn't online yet, the task prints JSON instead: paste it into **More → Import → vidIQ JSON**.

You can also import a saved file: `python -m app.cli import-json results.json`.

---

## 6. Deploy (cheapest reliable option)

The app needs a machine that stays on (for the bot and scheduled jobs) and a disk that persists (for
SQLite). **Free "sleeping" hosts (Render free, etc.) are not suitable**: they sleep, stop the bot, and
wipe the disk.

**Recommended: Oracle Cloud "Always Free" VM ($0)**, or any €4/month VPS (Hetzner CX22) if Oracle won't
give you a VM in your region.

```bash
# on the VM (Ubuntu)
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
git clone <your repo> && cd <repo>/sourcing-copilot
cp .env.example .env && nano .env      # set APP_PASSWORD, APP_SECRET_KEY, keys
sudo docker compose up -d --build
```

**HTTPS without opening ports, free:** Cloudflare Tunnel
(`cloudflared tunnel --url http://localhost:8000` for a quick test URL, or a named tunnel on your own
domain). Then set `PUBLIC_URL=https://…` and `COOKIE_SECURE=true` in `.env`, restart, and use that URL in
the scheduled task. Alternative: Caddy reverse proxy (`caddy reverse-proxy --from your.domain --to :8000`).

**Backups:** everything is in `data/` (database + screenshots). Copy `data/copilot.db` somewhere weekly.

Update: `git pull && sudo docker compose up -d --build`.

---

## 7. Running costs

| Item | Cost |
|---|---|
| Hosting (Oracle Always Free / own PC) | $0 (or ~€4/month VPS) |
| YouTube, Google Trends, Telegram, exchange rate | $0 |
| vidIQ | free plan (150 credits) |
| Claude API: Haiku for filter/terms/captions, Sonnet only for screenshots | ~$1–3/month for ~50 offers + weekly imports; hard cap in Settings (default $5) |

Results are cached (same screenshot or text is never paid twice) and filter calls are batched.

---

## 8. Honest limits & fallbacks

* **Google Trends (pytrends)** is unofficial and often rate-limited (HTTP 429). The app retries after
  12 h and treats it as a minor signal. Turkmenistan rarely has enough search volume, so TM is usually empty.
* **Telegram web preview** works only for public channels that have previews enabled; some shops
  disable it → use the optional Telethon reader. Prices are found by regex (`150 TMT`, `bahasy 150`,
  `цена 150 ман`); odd formats are missed: add prices manually on the product page.
* **TikTok Creative Center / Pinterest Trends** have no free API → use the paste box.
* **Taobao / 1688** have no free API and scraping breaks their terms → search buttons + screenshots + AI.
  AI vision can misread numbers: blue fields came from AI, so check them before saving.
* **vidIQ free plan** is ~30 calls/month: the scheduled task is the main trend feed; YouTube fills the gaps.
* **Exchange rate API** shows official rates; the real Turkmen street rate differs a lot, so use it only to
  spot drift.

---

## 9. Handy commands

```bash
python -m app.cli clear-mock          # remove demo data
python -m app.cli seed                # reload demo data (empty DB only)
python -m app.cli run youtube         # run a source now (youtube | google_trends | telegram_channels | fx)
python -m app.cli recompute           # recompute trend scores
python -m app.cli import-json f.json  # import vidIQ JSON
python -m app.cli telegram-login      # one-time Telethon login
python -m app.cli secret              # random secret
```

Code map: `app/pricing.py` (pricing engine) · `app/scoring.py` (trend/value/seller scores) ·
`app/product_filter.py` (product vs noise) · `app/sales_parser.py` · `app/adapters/` (one file per data
source; add a paid source by subclassing `Adapter`) · `app/bot/` (Telegram) · `app/services.py` (business
logic) · `app/main.py` (API) · `app/static/` (the phone app).
