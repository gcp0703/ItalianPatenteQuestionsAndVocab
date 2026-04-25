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

```bash
cp backend/.env.example .env
```

Edit `.env` and set:
```
ANTHROPIC_API_KEY=<your-anthropic-api-key>
BACKFILL_DEFINITIONS=false
```

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
