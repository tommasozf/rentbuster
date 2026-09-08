# Deploying RentBuster

Everything runs from `docker compose` — Postgres, the scraper, and the Playwright/Chromium
browser are all bundled. On a fresh VPS this is the whole deployment:

```bash
git clone git@github.com:tommasozf/rentbuster.git
cd rentbuster
cp .env.example .env
nano .env               # fill in API keys / webhooks (see below)
docker compose up -d
docker compose logs -f rentbuster
```

That's it. `docker compose up -d` builds the image, starts Postgres with a persistent volume,
and starts the scraper in continuous mode. The database schema is created automatically on
first run — no separate `init-db` step needed.

## What to put in `.env`

Copy `.env.example` and fill in whichever notification/LLM integrations you want:

- `DISCORD_WEBHOOK_URL` — Discord webhook, if you want Discord alerts
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_PASSWORD` — Telegram bot + subscriber password
- `GEMINI_API_KEY`, `LLM_ENABLED=true`, `LLM_MODEL=gemini-2.5-flash-lite` — for LLM feature extraction
- `POSTGRES_PASSWORD` — set this to something real for a public server (defaults to `rentbuster`)

`DATABASE_URL` in `.env` is only used for running the CLI outside Docker (e.g. `rentbuster top`
on your laptop against a tunneled DB) — inside `docker compose`, the `rentbuster` service always
points at the `db` container regardless of what's in `.env`.

## Day-to-day operations

```bash
docker compose ps                    # container status
docker compose logs -f rentbuster    # watch scrape cycles / notifications
docker compose exec db psql -U rentbuster -c "SELECT count(*) FROM listings;"
python -m rentbuster top             # or: docker compose exec rentbuster python -m rentbuster top
```

## Updating

```bash
cd ~/rentbuster
git pull
docker compose up -d --build
```

## Choosing a VPS

Any small box works — RentBuster is lightweight except for the Chromium install (~400MB image
layer) and needs a couple GB of RAM headroom for the browser. A good cheap option:

- **Hetzner CX22** (2 vCPU, 4GB RAM, 40GB SSD, ~€3.79/mo), Ubuntu 24.04, Falkenstein/Helsinki
  for the lowest price or Amsterdam for lowest latency to Dutch listing sites.
- Install Docker with `curl -fsSL https://get.docker.com | sh`, then follow the steps above.

## Security checklist

- [ ] SSH key auth only (disable password login in `/etc/ssh/sshd_config`)
- [ ] Firewall only allows SSH in (`ufw allow OpenSSH && ufw enable`)
- [ ] `.env` is `chmod 600` and never committed (`.gitignore` already covers it)
- [ ] Postgres port is bound to `127.0.0.1` only (already the case in `docker-compose.yml`) —
      don't change this unless you know what you're doing

## Restart on reboot

`restart: unless-stopped` in `docker-compose.yml` already brings containers back after a crash
or host reboot, as long as the Docker daemon itself starts on boot (`sudo systemctl enable
docker` — on by default on Ubuntu).

## Rollback

```bash
git log --oneline -5      # find the last good commit
git checkout <commit>
docker compose up -d --build
```
