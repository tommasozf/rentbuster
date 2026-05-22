# RentBuster

Scrapes Pararius and rent-buster.nl for Amsterdam (or any Dutch city) rental listings, calculates the legal maximum rent using the WWS (Woningwaarderingsstelsel) points system, and sends you a notification whenever a listing is asking more than it's legally allowed to.

No AI, no paid APIs, no subscriptions. Everything runs free.

---

## How it works

1. **Scrapes** Pararius (Playwright + stealth) and rent-buster.nl (plain HTTP) every 2–5 minutes
2. **Looks up WOZ value** via the free public Kadaster API (PDOK geocoding → LV-WOZ)
3. **Calculates WWS points** using the 2025 Huurcommissie rules — surface area, WOZ, energy label, rooms, outdoor space, etc.
4. **Flags bustable listings** where asking rent > legal maximum (threshold: 187 points)
5. **Notifies you** via Telegram, Discord, or any Apprise channel

rent-buster.nl listings come with pre-computed WWS points and max rents already attached — those go straight through without needing a WOZ lookup.

---

## Quick start (local)

Requires Python 3.10+ and the `rentbuster` conda environment (or any venv with deps installed).

```bash
git clone <this-repo>
cd rentbuster

# Install deps (pick one)
conda activate rentbuster
# or: pip install .
# or: uv sync

# Install Playwright's Chromium (required for Pararius)
playwright install chromium

# Copy and fill in config
cp .env.example .env
# Edit .env: set at least one of DISCORD_WEBHOOK_URL or TELEGRAM_BOT_TOKEN

# One-shot dry run (no notifications, no DB writes — just shows what it finds)
python -m rentbuster -v run --once --dry-run

# Run continuously (checks every 2–5 minutes)
python -m rentbuster run
```

---

## Notifications setup

### Discord (easiest)

1. Open the Discord server where you want alerts
2. Edit any channel → **Integrations** → **Webhooks** → **New Webhook**
3. Copy the webhook URL
4. In `.env`: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`

You'll get a rich embed with address, asking rent, legal max, savings, energy label, and a link to the listing.

### Telegram (recommended for mobile)

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

That's it. Every bustable listing will land in your Telegram DMs. The password gates it so nobody else can subscribe.

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

Copy `.env.example` to `.env` and set what you need. Nothing is required — without a notification URL it just logs to stdout, and without `DATABASE_URL` it runs without persistence (re-notifies everything on restart).

| Variable | Default | Purpose |
|---|---|---|
| `PROFILE` | `amsterdam` | Search profile (see `profiles/`) |
| `DATABASE_URL` | — | Postgres URL. Required for persistence and Telegram subscribers |
| `PARARIUS_ENABLED` | `true` | Scrape Pararius (Playwright-based) |
| `PARARIUS_MAX_PAGES` | `5` | How many search result pages to scrape |
| `PLAYWRIGHT_HEADLESS` | `true` | Set `false` to watch the browser |
| `FETCH_DETAILS` | `true` | Visit each listing's detail page for energy label, postal code etc. |
| `RENTBUSTER_NL_ENABLED` | `true` | Scrape rent-buster.nl (HTTP-based, faster) |
| `RENTBUSTER_NL_MAX_PAGES` | `10` | Pages to scrape from rent-buster.nl |
| `WWS_BUSTABLE_ONLY` | `true` | Only notify on bustable listings |
| `WWS_MIN_SAVINGS` | `50` | Minimum €/month savings to trigger a notification |
| `DISCORD_WEBHOOK_URL` | — | Discord webhook URL |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (from @BotFather) |
| `TELEGRAM_PASSWORD` | — | Password users send with `/start` to subscribe |
| `APPRISE_URLS` | — | Comma-separated Apprise URLs |
| `CHECK_INTERVAL_MIN` | `120` | Minimum seconds between scrape cycles |
| `CHECK_INTERVAL_MAX` | `300` | Maximum seconds between scrape cycles |

---

## Profiles

A profile sets the city, max rent filter, and WWS defaults. The built-in `amsterdam` profile is at `profiles/amsterdam.yaml`. Copy and edit it for other cities:

```yaml
name: rotterdam
description: Rotterdam bustable listings under €2000/mo

search:
  city: rotterdam
  max_rent: 2000
  pararius_max_pages: 5
  rentbuster_nl_max_pages: 10

wws:
  bustable_only: true
  min_savings: 50
```

Pass it with `--profile rotterdam` or set `PROFILE=rotterdam` in `.env`.

---

## Running 24/7

Your PC needs to stay on, or run it on a server.

### Option A: tmux on your PC (simple)

```bash
tmux new -s rentbuster
conda activate rentbuster
python -m rentbuster run
# Ctrl+B, D  →  detach (keeps running)
# tmux attach -t rentbuster  →  re-attach later
```

### Option B: systemd on a VPS (reliable)

A Hetzner CAX11 (arm64, €3.79/mo) or CX22 (€4.35/mo) is plenty — the scraper needs ~500 MB RAM for Playwright.

```bash
# On the VPS, after cloning and installing deps:
sudo nano /etc/systemd/system/rentbuster.service
```

```ini
[Unit]
Description=RentBuster
After=network.target

[Service]
User=youruser
WorkingDirectory=/opt/rentbuster
EnvironmentFile=/opt/rentbuster/.env
ExecStart=/opt/rentbuster/.venv/bin/python -m rentbuster run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now rentbuster
sudo journalctl -fu rentbuster   # follow logs
```

### Option C: Docker

```bash
cp .env.example .env   # fill in your values
docker compose up -d db
docker compose run --rm rentbuster python -m rentbuster init-db
docker compose up -d rentbuster
docker compose logs -f rentbuster
```

The `docker-compose.yml` includes Postgres. For Pararius, Playwright needs its Chromium deps — the `Dockerfile` installs them.

---

## Database (optional)

Without `DATABASE_URL` the scraper works fine but has no memory — it will re-notify you about the same listings after a restart. With Postgres it tracks what it's seen, caches WOZ lookups, and is required for Telegram subscriber persistence.

```bash
# Start a local Postgres (Docker)
docker run -d --name pg \
  -e POSTGRES_PASSWORD=rentbuster \
  -e POSTGRES_USER=rentbuster \
  -e POSTGRES_DB=rentbuster \
  -p 5432:5432 postgres:16

# In .env:
DATABASE_URL=postgres://rentbuster:rentbuster@localhost:5432/rentbuster

# Bootstrap schema (run once)
python -m rentbuster init-db
```

---

## Legal

This tool is for personal, non-commercial use. It scrapes public listing data at a polite rate (one cycle every 2–5 minutes). The WWS calculation is an estimate — for an actual Huurcommissie case, verify the points yourself using the [official Huurprijscheck](https://www.huurcommissie.nl/huurders/sociale-huurwoning/huurprijscheck).

Not affiliated with Pararius, rent-buster.nl, or Huurcommissie.
