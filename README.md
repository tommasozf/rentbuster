# RentBuster

Scrapes Pararius and rent-buster.nl for Amsterdam (or any Dutch city) rental listings, calculates the legal maximum rent using the WWS (Woningwaarderingsstelsel) points system, and sends you a notification whenever a listing is asking more than it's legally allowed to.

No paid APIs, no subscriptions. The only optional paid piece is a Gemini call per new listing to read the ad text, which costs cents per month.

---

## How it works

1. **Scrapes** Pararius (Playwright + stealth, ~450 listings) and rent-buster.nl (plain HTTP, ~220 listings) every 2–5 minutes
2. **Filters** by property type, room count, and rent ceiling to focus on apartments that could realistically be regulated
3. **Looks up WOZ value** via the free public Kadaster API (PDOK geocoding + LV-WOZ)
4. **Calculates WWS points** using the 2026 Huurcommissie rules — surface area, WOZ, energy label, outdoor space, kitchen, bathroom, heating (see [How WWS points are calculated](#how-wws-points-are-calculated) below)
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

The bot answers within a few seconds while the scraper runs (it long-polls Telegram in the background); with `run --once` commands are handled once at the start of the run.

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

## Profiles — the one file to tune

**All scoring assumptions live in the profile YAML** (`profiles/amsterdam.yaml`). This is the single file to edit if you want more or fewer alerts, or if you want to adjust how aggressively the calculator estimates unknown values.

```yaml
search:
  city: amsterdam
  max_rent: 3500               # EUR/month ceiling for search
  max_rooms: 4                 # skip 5+ room apartments

wws:
  bustable_only: true
  min_savings: 50              # ignore listings with savings < €50/mo
  min_confidence: ""           # "" = no filter; "MEDIUM" = skip LOW/VERY_LOW
  min_bust_score: 0            # skip alerts below this score
  retention_days: 90           # auto-delete old listings after this many days

  # ── These control how unknown values are filled in ──
  default_energy_label: D      # assumed when listing has no label (A→G)
  default_woz_per_m2: 6000     # EUR/m² when Kadaster lookup fails
  default_outdoor_points: 2    # balcony presence bonus
  default_kitchen_points: 10   # standard fitted kitchen
  default_bathroom_points: 12  # toilet + shower + basin + bath
  default_heating_points: 2    # per heated room
```

**Want fewer false positives?** Raise `default_energy_label` to `C` or `B`, raise `min_savings`, set `min_confidence: MEDIUM`, or set `min_bust_score: 200`. Only listings that are clearly bustable even with generous assumptions will trigger an alert.

**Want to catch everything?** Lower `default_energy_label` to `E` or `F`, lower `min_savings` to `0`, and clear `min_confidence`. You'll get more alerts but some may not hold up.

Copy and edit the profile for other cities:

```yaml
name: rotterdam
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
  default_energy_label: D
  default_woz_per_m2: 3500     # Rotterdam has lower WOZ values
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

## How WWS points are calculated

The [WWS (Woningwaarderingsstelsel)](https://www.huurcommissie.nl/huurders/sociale-huurwoning/huurprijscheck) is the Dutch point system that determines the maximum legal rent for an apartment. Below **187 points** the apartment is "regulated" and rent is capped; at 187+ it's "free market" and the landlord can charge anything.

Each listing's WWS score is built from these components:

| Component | How we get it | Points range | When unknown |
|---|---|---|---|
| **Surface area** | From listing (m²) | 1 pt / m² | 0 pts, flagged |
| **Energy label** | Detail page, rent-buster.nl, or Platform.targeting JS | A++++ = +58 … G = −15 | Assumed **D** (+11 pts) |
| **WOZ value** | Kadaster API (free, public) | Two-part formula, capped at 33% of total | Estimated at €6,000/m² for Amsterdam |
| **Outdoor space** | LLM extraction from ad text, or default | Varies | +2 pts (balcony presence bonus) |
| **Kitchen** | LLM extraction from ad text, or default | Varies | +10 pts (standard fitted kitchen) |
| **Bathroom** | LLM extraction from ad text, or default | Varies | +12 pts (toilet + shower + basin + bath) |
| **Heating** | LLM extraction from ad text, or default | Per heated room | +2 pts × number of rooms |

### Energy label: the biggest unknown

Energy labels have the largest swing of any single component — a label A adds **37 points** while a label G *subtracts* 15. When we don't know the label (common for Pararius listings where the detail page couldn't be fetched), we assume **D** (+11 pts) as a conservative middle ground.

Because this assumption can be 26+ points off, notifications for listings with an unknown energy label also show a **"If label A"** line — what the points and max rent would be if the real label turns out to be A. If that pushes the listing over 187 points (free market), you know the bustability depends heavily on what the actual label is.

**Full energy label table (apartments):**

| Label | Points |
|-------|--------|
| A++++ | +58 |
| A+++ | +53 |
| A++ | +48 |
| A+ | +43 |
| A | +37 |
| B | +30 |
| C | +15 |
| D | +11 |
| E | −4 |
| F | −9 |
| G | −15 |

### WOZ value lookup

The WOZ (Waardering Onroerende Zaken) is the government-assessed property value, updated yearly. We look it up for free via two public APIs:

1. **PDOK locatieserver** — geocodes the address to a BAG nummeraanduiding ID
2. **Kadaster LV-WOZ API** — returns the WOZ value for that ID

If the exact unit has no WOZ data (common for split apartments), we retry with just the house number (the "sibling" fallback — uses a neighboring unit's value). If that also fails, we estimate conservatively:

| City | Estimated WOZ per m² |
|------|---------------------|
| Amsterdam | €6,000 |
| Utrecht | €4,500 |
| Den Haag | €3,800 |
| Rotterdam | €3,500 |
| Other | €3,500 |

Estimated WOZ values are flagged in notifications. The actual WOZ can be checked at [wozwaardeloket.nl](https://www.wozwaardeloket.nl).

### WOZ cap (33% rule)

WOZ points may make up at most **33%** of the total WWS score. This only applies to apartments that would reach 187+ points without the cap. If applying the cap drags a listing below 187 points, it's set to exactly 186 (still regulated). Small new-builds (< 40 m², built 2018–2022, in Amsterdam/Utrecht) are exempt from this floor.

### When rent-buster.nl data is available

Many listings appear on both Pararius and rent-buster.nl. When rent-buster.nl has pre-computed WWS points for a listing, **we use their score directly** instead of recalculating — they have better access to cadastral data (build year, monument status, exact region), so their points are usually quite accurate. These listings show as HIGH confidence with the flag `rb_points_used`.

### Confidence levels

| Level | Meaning | Multiplier |
|-------|---------|-----------|
| **HIGH** | All key data present (verified WOZ, known energy label, or rb data) | 1.0× |
| **MEDIUM** | Minor assumptions (estimated WOZ, assumed energy label) | 0.8× |
| **LOW** | One critical value missing (no WOZ from Kadaster, no surface area) | 0.5× |
| **VERY LOW** | Multiple critical values missing | 0.3× |

### 187-point threshold

- **Below 187 points** → regulated ("sociale huur"). Rent is capped at the amount from the [Huurcommissie's Bijlage 3 table](https://www.huurcommissie.nl/huurders/sociale-huurwoning/huurprijscheck), updated every January.
- **187 points or above** → free market ("vrije sector"). The landlord can charge whatever the market bears.

The table currently in use is valid from 1 January 2026.

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

### Checking the scoring

rent-buster.nl publishes its own points and max rent for every ad. To see how far our calculator is from theirs (and whether a change to `wws.py` helped):

```bash
uv run python scripts/benchmark_rentbuster_nl.py --pages 10
```

---

## Further reading

- [Huurcommissie Huurprijscheck](https://www.huurcommissie.nl/huurders/sociale-huurwoning/huurprijscheck) — official WWS calculator
- [WOZ Waardeloket](https://www.wozwaardeloket.nl) — look up any property's WOZ value
- [rent-buster.nl](https://rent-buster.nl) — the source we cross-reference for pre-computed WWS scores

<!-- Add Reddit/community links here -->

---

## Legal

This tool is for personal, non-commercial use. It scrapes public listing data at a polite rate (one cycle every 2–5 minutes). The WWS calculation is an estimate — for an actual Huurcommissie case, verify the points yourself using the [official Huurprijscheck](https://www.huurcommissie.nl/huurders/sociale-huurwoning/huurprijscheck).

Not affiliated with Pararius, rent-buster.nl, or Huurcommissie.
