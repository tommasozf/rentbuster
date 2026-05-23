# RentBuster VPS Deployment Plan

Deploy RentBuster to a cheap VPS so it runs 24/7 with Postgres, Playwright, and LLM extraction.

## Architecture

Single box running `docker compose up`. Same setup as local, just on a server that's always on.

```
VPS (Hetzner CX22 — 2 vCPU, 4GB RAM, 40GB disk, €3.79/mo)
├── docker compose
│   ├── db        (postgres:15-alpine)
│   ├── rentbuster (Python + Playwright + Chromium)
│   └── dashboard  (optional, behind auth)
└── .env          (secrets: API keys, bot tokens, webhook URLs)
```

## VPS Provider: Hetzner

- **Plan**: CX22 — 2 vCPU, 4GB RAM, 40GB SSD — €3.79/mo
- **Why Hetzner**: Cheapest reliable EU provider, Amsterdam datacenter (low latency to Dutch sites), good peering
- **OS**: Ubuntu 24.04 LTS
- **Alternative**: Oracle Cloud free tier (ARM, 4 vCPU, 24GB RAM) — truly free but less reliable and more setup friction

## Step-by-step Setup

### 1. Create the VPS

1. Sign up at https://console.hetzner.cloud
2. Create a new project
3. Add your SSH key (from `~/.ssh/id_ed25519.pub` or generate one)
4. Create server:
   - Location: **Falkenstein** or **Helsinki** (cheapest) — Amsterdam is also fine
   - Image: **Ubuntu 24.04**
   - Type: **CX22** (shared vCPU, 4GB RAM)
   - SSH key: select yours
   - Name: `rentbuster`

### 2. Initial Server Setup

```bash
# SSH in
ssh root@<server-ip>

# Basic hardening
apt update && apt upgrade -y
adduser rentbuster --disabled-password
usermod -aG sudo rentbuster
cp -r ~/.ssh /home/rentbuster/.ssh
chown -R rentbuster:rentbuster /home/rentbuster/.ssh

# Install Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker rentbuster

# Enable unattended security updates
apt install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades

# Firewall: only SSH
ufw allow OpenSSH
ufw enable

# Switch to rentbuster user for everything else
su - rentbuster
```

### 3. Deploy the App

```bash
# Clone the repo
git clone git@github.com:tommasozf/rentbuster.git
cd rentbuster

# Create .env from example
cp .env.example .env
```

Then edit `.env` with the real values. The secrets to fill in:
- `DISCORD_WEBHOOK_URL` — your Discord webhook
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_PASSWORD` — subscriber password
- `GEMINI_API_KEY` — from Google AI Studio (paid Tier 1)
- `LLM_ENABLED=true`
- `LLM_MODEL=gemini-2.5-flash-lite`

### 4. Build and Start

```bash
# Build the image (first time takes ~5 min for Playwright/Chromium)
docker compose build

# Initialize the database
docker compose up -d db
docker compose run --rm rentbuster init-db

# Start everything
docker compose up -d

# Check logs
docker compose logs -f rentbuster
```

### 5. Verify It Works

```bash
# Check containers are healthy
docker compose ps

# Watch a full scrape cycle
docker compose logs -f rentbuster

# Check DB has listings
docker compose exec db psql -U rentbuster -c "SELECT count(*) FROM listings;"
docker compose exec db psql -U rentbuster -c "SELECT count(*) FROM listings WHERE wws_is_bustable;"
```

### 6. Auto-restart on Reboot

Docker's `restart: unless-stopped` in docker-compose.yml already handles this. Docker itself starts on boot by default on Ubuntu.

Verify:
```bash
sudo systemctl enable docker
```

### 7. Updates

When you push changes to the repo:

```bash
cd ~/rentbuster
git pull
docker compose build
docker compose up -d
```

### 8. Optional: Dashboard Access

If you want remote access to the dashboard:

```bash
# Option A: SSH tunnel (simplest, no public exposure)
# From your local machine:
ssh -L 3000:localhost:3000 rentbuster@<server-ip>
# Then open http://localhost:3000

# Option B: Caddy reverse proxy with auto-HTTPS (needs a domain)
# Install Caddy, point a domain to the VPS IP, configure:
# rentbuster.yourdomain.com {
#     reverse_proxy localhost:3000
#     basicauth {
#         admin $2a$14$... (caddy hash-password)
#     }
# }
```

### 9. Monitoring (Optional)

Simple uptime check — add to VPS crontab:

```bash
# Alert if rentbuster container dies
crontab -e
# Add:
*/5 * * * * docker inspect --format='{{.State.Running}}' rentbuster-rentbuster-1 | grep -q true || curl -s "https://discord.com/api/webhooks/YOUR_WEBHOOK" -H "Content-Type: application/json" -d '{"content":"RentBuster is down!"}'
```

## Cost Breakdown

| Item | Monthly Cost |
|------|-------------|
| Hetzner CX22 | €3.79 |
| Gemini API (Tier 1, ~400 calls/day) | ~€0.50 |
| Domain (optional) | ~€1/mo amortized |
| **Total** | **~€4-5/mo** |

## Dockerfile Notes

The existing `Dockerfile` is already production-ready:
- Multi-stage uv install with frozen lockfile
- Playwright Chromium installed in image
- Runs as non-root user
- The `llm` extra needs to be added to the build — update the Dockerfile `RUN uv sync` line:

```dockerfile
# Change this line:
RUN uv sync --frozen --no-dev
# To:
RUN uv sync --frozen --no-dev --extra llm
```

## Security Checklist

- [ ] SSH key auth only (disable password login in `/etc/ssh/sshd_config`)
- [ ] UFW firewall enabled (only port 22 open)
- [ ] `.env` file is `chmod 600` and in `.gitignore`
- [ ] Postgres only listens on Docker network (127.0.0.1 in docker-compose)
- [ ] Unattended security updates enabled
- [ ] Dashboard behind SSH tunnel or auth proxy (not exposed publicly)

## Rollback

If something breaks after an update:
```bash
git log --oneline -5          # find the last good commit
git checkout <commit>
docker compose build && docker compose up -d
```
