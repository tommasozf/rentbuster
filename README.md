# RentBuster

Scrapes Pararius and rent-buster.nl for Amsterdam (or any Dutch city) rental listings, calculates the legal maximum rent using the WWS (Woningwaarderingsstelsel) points system, and sends you a notification whenever a listing is asking more than it's legally allowed to.

No paid APIs, no subscriptions. The only optional paid piece is a Gemini call per new listing to read the ad text, which costs cents per month.

---

## How it works

1. **Scrapes** Pararius (Playwright + stealth, ~450 listings) and rent-buster.nl (plain HTTP, ~220 listings) every 2–5 minutes
2. **Filters** by property type, room count, and rent ceiling to focus on apartments that could realistically be regulated
3. **Looks up WOZ value** via the free public Kadaster API (PDOK geocoding + LV-WOZ)
4. **Calculates WWS points** using the 2025 Huurcommissie rules — surface area, WOZ, energy label, rooms, outdoor space, etc.
5. **Ranks listings** by bust score (savings x confidence x WOZ reliability)
6. **Notifies you** via Telegram, Discord, or any Apprise channel
7. **Stores everything** in Postgres — query your top listings any time via Telegram `/top` or CLI

rent-buster.nl listings come with pre-computed WWS points, WOZ values, and max rents already attached — those skip the lookup step entirely.

---

## Quick start

```bash
git clone <this-repo>
cd rentbuster
cp .env.example .env   # edit: add your Discord webhook or Telegram bot token; more on this below
docker compose up -d   # starts Postgres + scraper, creates DB automatically
docker compose logs -f rentbuster   # watch it find bustable apartments
```

That's it. Postgres, Playwright, Chromium — all bundled. See [DEPLOY.md](DEPLOY.md) for VPS setup.

**Test it first** (no notifications, no DB writes):

```bash
docker compose run --rm rentbuster run --once --dry-run
docker compose run --rm rentbuster -v run --once --dry-run   # debug logging; -v goes before the subcommand
```

---

## Querying top listings

Once the scraper has run at least once with a database, you can query the best bustable listings ranked by **bust score** (savings x confidence x WOZ reliability).

### CLI

```bash
python -m rentbuster top              # top 5
python -m rentbuster top --limit 15   # top 15
```

### Telegram

Send `/top` or `/top 10` to your bot. Requires an active subscription (see Telegram setup below).

### Discord

Discord uses webhooks (one-way, send-only), so it can't receive commands. Use the CLI or Telegram for interactive queries. Discord will still receive real-time alerts for every new bustable listing.

---

## Notifications setup

### Discord (easiest — alerts only)

1. Open the Discord server where you want alerts
2. Edit any channel → **Integrations** → **Webhooks** → **New Webhook**
3. Copy the webhook URL
4. In `.env`: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`

You'll get a rich embed with address, asking rent, legal max, savings, energy label, source, and a link to the listing.

### Telegram (recommended — alerts + interactive queries)

**Step 1 — create a bot:**

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Pick a name (e.g. "RentBuster") and a username (e.g. `myrb_bot`)
4. BotFather replies with a token like `7123456789:AAF...`

**Step 2 — configure:**

```bash
# In .env:
TELEGRAM_BOT_TOKEN=7123456789:AAF...
TELEGRAM_PASSWORD=somesecretword   # anything you like
```

**Step 3 — subscribe:**

Once the scraper is running, open your bot in Telegram and send:
```
/start somesecretword
```

**Available commands:**
- `/start <password>` — subscribe to alerts
- `/stop` — unsubscribe
- `/list` or `/list 10` (alias `/top`) — top bustable listings from the database
- `/detail <id>` — full details and WWS breakdown for one listing
- `/drop <id>` — hide a listing from your future alerts
- `/status` — last run time and listing counts
- `/filters` — the active search profile
- `/help` — list the commands

### Apprise (ntfy, Pushover, Slack, email, etc.)

Set `APPRISE_URLS` to a comma-separated list of [Apprise URLs](https://github.com/caronc/apprise/wiki):

```bash
# ntfy (free, self-hostable, great phone app)
APPRISE_URLS=ntfy://mytopic

# Pushover (paid, excellent mobile app)
APPRISE_URLS=pover://UserKey@AppKey

# Multiple channels at once
APPRISE_URLS=ntfy://mytopic,pover://UserKey@AppKey
```

---

## Configuration

Copy `.env.example` to `.env` and set what you need. Without a notification URL it just logs to stdout.
Search settings (city, rent ceiling, rooms, pages, minimum savings) are in the profile, see below.

| Variable | Default | Purpose |
|---|---|---|
| `PROFILE` | `amsterdam` | Search profile (see `profiles/`) |
| `DATABASE_URL` | — | Postgres URL. Required for persistence, `/top`, and Telegram |
| `PARARIUS_ENABLED` | `true` | Scrape Pararius (Playwright-based) |
| `PLAYWRIGHT_HEADLESS` | `true` | Set `false` to watch the browser |
| `FETCH_DETAILS` | `true` | Visit each listing's detail page for energy label etc. |
| `RENTBUSTER_NL_ENABLED` | `true` | Scrape rent-buster.nl (HTTP-based, faster) |
| `DETAIL_FETCH_DELAY` | `2.0` | Seconds to wait between Pararius detail pages |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook URL |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (from @BotFather) |
| `TELEGRAM_PASSWORD` | — | Password users send with `/start` to subscribe |
| `APPRISE_URLS` | — | Comma-separated Apprise URLs |
| `CHECK_INTERVAL_MIN` | `120` | Minimum seconds between scrape cycles |
| `CHECK_INTERVAL_MAX` | `300` | Maximum seconds between scrape cycles |

---

## Profiles

A profile sets the city, rent ceiling, room limits, and WWS defaults. The built-in `amsterdam` profile is at `profiles/amsterdam.yaml`:

```yaml
name: amsterdam
description: Find bustable apartments in Amsterdam

search:
  city: amsterdam
  max_rent: 3500           # EUR/month ceiling for search
  max_rooms: 4             # skip 5+ room apartments (too large to be regulated)
  property_types: [apartment, studio]
  pararius_max_pages: 15
  rentbuster_nl_max_pages: 23

wws:
  bustable_only: true
  min_savings: 50
```

Copy and edit it for other cities:

```yaml
name: rotterdam
description: Rotterdam bustable listings

search:
  city: rotterdam
  max_rent: 2500
  max_rooms: 4
  property_types: [apartment, studio]
  pararius_max_pages: 10
  rentbuster_nl_max_pages: 15

wws:
  bustable_only: true
  min_savings: 50
```

Pass it with `--profile rotterdam` or set `PROFILE=rotterdam` in `.env`.

---

## Database

Postgres is included in `docker-compose.yml` and the schema is created automatically on first run. The database stores:
- All scraped listings with WWS scores and bust rankings
- WOZ value cache (avoids re-querying Kadaster for known addresses)
- Telegram subscriber list
- Scrape run history

Without a database the scraper has no memory — it re-notifies about the same listings after a restart, and you can't use `/top`.

---

## Running without Docker (dev setup)

If you want to run the Python code directly instead of Docker:

```bash
uv sync                          # or: pip install .
playwright install chromium      # Pararius needs a real browser
docker compose up -d db          # just the Postgres container
cp .env.example .env             # edit with your keys
python -m rentbuster run         # runs continuously
```

---

## How bust score works

Each bustable listing gets a **bust score** that ranks how attractive it is to dispute:

```
bust_score = monthly_savings x confidence_multiplier x woz_multiplier
```

- **Monthly savings**: asking rent minus legal maximum (higher = bigger win)
- **Confidence multiplier**: HIGH = 1.0, MEDIUM = 0.8, LOW = 0.5, VERY_LOW = 0.3
- **WOZ multiplier**: 1.0 if WOZ is verified (Kadaster/rent-buster.nl), 0.7 if estimated

A listing asking EUR 2000/mo with a legal max of EUR 800/mo, HIGH confidence, and verified WOZ scores `1200 x 1.0 x 1.0 = 1200`. The higher the score, the more worth pursuing.

---

## Legal

This tool is for personal, non-commercial use. It scrapes public listing data at a polite rate (one cycle every 2–5 minutes). The WWS calculation is an estimate — for an actual Huurcommissie case, verify the points yourself using the [official Huurprijscheck](https://www.huurcommissie.nl/huurders/sociale-huurwoning/huurprijscheck).

Not affiliated with Pararius, rent-buster.nl, or Huurcommissie.
