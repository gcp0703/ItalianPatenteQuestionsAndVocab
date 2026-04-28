# Deploying Quiz Patente B to Production

Production server: Azure VM at `172.173.116.28` (Ubuntu 22.04)
URL: `https://patenteb.eventhorizon.llc`

## Prerequisites

- SSH access: `ssh -i ~/.ssh/ReportFolio_key.pem azureuser@172.173.116.28`
- DNS A record for `patenteb.eventhorizon.llc` → `172.173.116.28`
- Python 3.10+, Node.js 18+, npm on the server

## Initial Server Setup

### 1. Clone the repository

```bash
ssh -i ~/.ssh/ReportFolio_key.pem azureuser@172.173.116.28

cd /home/azureuser
git clone <repo-url> quizpatenteb
cd quizpatenteb
```

### 2. Set up Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[claude]"
```

### 3. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

### 4. Configure environment

Copy the template to a secured location outside the repo and edit it:

```bash
sudo cp /home/azureuser/quizpatenteb/.env.example /etc/quizpatenteb.env
sudo chown root:azureuser /etc/quizpatenteb.env
sudo chmod 640 /etc/quizpatenteb.env
sudo nano /etc/quizpatenteb.env
```

Required values:
- `ANTHROPIC_API_KEY=sk-ant-...`
- `SESSION_SECRET=<32 random bytes hex>` — generate with
  `python3 -c 'import secrets; print(secrets.token_hex(32))'`
- `AUTH_TOKEN_PEPPER=<32 random bytes hex>` — same generator
- `ANTHROPIC_MONTHLY_USD_CAP=10` (or your preferred cap)
- `GMAIL_APP_PASSWORD=<gmail app password>` — generated at
  https://myaccount.google.com/apppasswords for the
  `eventhorizonpatenteb@gmail.com` account (requires 2FA enabled). Used to
  email new users their bearer token and to handle forgot-token requests.
  If empty, email is silently skipped (registration still returns the token
  in the API response).

Leave empty in production:
- `CORS_ORIGINS=` (SPA is served same-origin via nginx)
- `ADMIN_EMAIL=` (or set to the operator's email to enable admin endpoints)

The systemd unit's `EnvironmentFile=/etc/quizpatenteb.env` reads this file and
sets `QPB_LOAD_DOTENV=0`, so the application never falls back to a repo-root
`.env`. **Do not place secrets inside the repo working tree.**

### 5. Install nginx config

```bash
sudo cp deployment/nginx/patenteb.conf /etc/nginx/sites-available/patenteb
sudo ln -s /etc/nginx/sites-available/patenteb /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 6. Set up SSL with Certbot

```bash
sudo certbot --nginx -d patenteb.eventhorizon.llc
```

### 7. Install and start systemd service

```bash
sudo cp deployment/systemd/quizpatenteb.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable quizpatenteb
sudo systemctl start quizpatenteb
```

### 8. Verify

```bash
# Check service status
sudo systemctl status quizpatenteb

# Check API
curl -s http://127.0.0.1:8500/api/quiz/topics | python3 -m json.tool | head

# Check HTTPS
curl -s https://patenteb.eventhorizon.llc/api/quiz/topics | head
```

## nginx rate-limit zones

The zones used by the `limit_req` directives in `patenteb.conf` are defined in
a separate snippet that lives in nginx's `http {}` context. Install both:

```bash
sudo cp /home/azureuser/quizpatenteb/deployment/nginx/limit_req_zones.conf \
        /etc/nginx/conf.d/qpb-zones.conf
sudo cp /home/azureuser/quizpatenteb/deployment/nginx/patenteb.conf \
        /etc/nginx/sites-available/patenteb
sudo nginx -t  # MUST succeed before reload
sudo systemctl reload nginx
```

`patenteb.conf` includes the Certbot-managed SSL server block. If you ever
re-issue the certificate to a different domain, re-run
`sudo certbot --nginx -d <new-domain>` and copy the resulting
`/etc/nginx/sites-available/patenteb` back into the repo so future deploys
don't drop HTTPS.

Verify limiting works from an off-server host:

```bash
for i in $(seq 1 40); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    https://patenteb.eventhorizon.llc/api/health
done | sort | uniq -c
```

Expected: a mix of `200` and `429`. The `429` rows confirm nginx-layer
limiting is active (in addition to the slowapi-layer limits inside the app).

## Anthropic monthly hard cap (REQUIRED)

The application enforces a *soft* cap via `ANTHROPIC_MONTHLY_USD_CAP` in
`/etc/quizpatenteb.env` — it stops calling Claude when the in-memory monthly
total exceeds the cap. **A soft cap alone is not enough**: a worker restart
resets the counter, and a malformed env value silently disables it.

Set a *hard* cap in the Anthropic console too:
1. https://console.anthropic.com/settings/limits
2. Under "Spend limits", set a monthly USD cap on the QuizPatenteB API key.
3. Set the soft cap (`ANTHROPIC_MONTHLY_USD_CAP`) to ~50% of the hard cap so
   the app stops voluntarily before Anthropic forces a 429.

Use a dedicated API key for QuizPatenteB so rotation does not affect the
RePortfolio or OpenSesame backends sharing the same VM.

Per-call cost lines (`ANTHROPIC_CALL ... month_total_usd=$X`) appear in
`journalctl -u quizpatenteb` — grep for `ANTHROPIC_CALL` to audit spend.

## Migrating existing users to bearer-token auth (one-time, after auth deploy)

When the auth update is deployed for the first time, every existing entry in
`user_data/_users.json` lacks a `token_hash` and cannot log in. SSH to the VM
and run the migration CLI:

```bash
sudo -u azureuser bash -c '
  set -a
  source /etc/quizpatenteb.env
  set +a
  cd /home/azureuser/quizpatenteb
  QPB_USER_DATA_DIR=/home/azureuser/quizpatenteb/user_data \
    QPB_LOAD_DOTENV=0 \
    .venv/bin/python -m backend.scripts.mint_user_tokens > /tmp/qpb-tokens.tsv
'
```

The output `/tmp/qpb-tokens.tsv` contains `email<TAB>token` rows. Distribute
each token to its user out-of-band (email, SMS, paper). Then **shred the file**:

```bash
shred -u /tmp/qpb-tokens.tsv
```

Users log in via the SPA's "Ho già un token" form by entering their email and
token. The CLI is idempotent: re-running it only mints tokens for users that
still lack one.

## Deploying Updates

Run from your local machine:

```bash
./scripts/deploy.sh
```

Or manually on the server:

```bash
cd /home/azureuser/quizpatenteb
git pull origin main
source .venv/bin/activate
pip install -e ".[claude]"
cd frontend && npm install && npm run build && cd ..
sudo systemctl restart quizpatenteb
```

## Service Management

```bash
# View logs
sudo journalctl -u quizpatenteb -f

# Restart
sudo systemctl restart quizpatenteb

# Stop
sudo systemctl stop quizpatenteb

# Status
sudo systemctl status quizpatenteb
```

## Architecture

```
Internet (443) → nginx → patenteb.eventhorizon.llc
                         ├── /           → static files (frontend/dist/)
                         ├── /api/       → proxy to 127.0.0.1:8500
                         └── /img_sign/  → proxy to 127.0.0.1:8500
```

- Port 8500: QuizPatenteB (uvicorn)
- Port 8000: RePortfolio (coexists on same VM)
- Port 5432: PostgreSQL (RePortfolio only)

## Ports Summary

| Port | Service | Binding |
|------|---------|---------|
| 80   | nginx (HTTP→HTTPS redirect) | 0.0.0.0 |
| 443  | nginx (SSL termination) | 0.0.0.0 |
| 8000 | RePortfolio backend | 127.0.0.1 |
| 8500 | QuizPatenteB backend | 127.0.0.1 |
| 5432 | PostgreSQL | 127.0.0.1 |
