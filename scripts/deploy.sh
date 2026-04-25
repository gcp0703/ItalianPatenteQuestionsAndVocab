#!/usr/bin/env bash
# Deploy Quiz Patente B to production server
# Usage: ./scripts/deploy.sh

set -euo pipefail

SERVER_USER="azureuser"
SERVER_HOST="172.173.116.28"
SSH_KEY="$HOME/.ssh/ReportFolio_key.pem"
REMOTE_DIR="/home/azureuser/quizpatenteb"

echo "=== Deploying Quiz Patente B ==="

ssh -i "$SSH_KEY" "$SERVER_USER@$SERVER_HOST" bash -s <<'REMOTE'
set -euo pipefail
cd /home/azureuser/quizpatenteb

echo "--- Pulling latest code ---"
git pull origin main

echo "--- Installing Python dependencies ---"
source .venv/bin/activate
pip install -e . --quiet

echo "--- Building frontend ---"
cd frontend
npm install --silent
npm run build
cd ..

echo "--- Restarting service ---"
sudo systemctl restart quizpatenteb

echo "--- Waiting for service to start ---"
sleep 2

echo "--- Health check ---"
if curl -sf http://127.0.0.1:8500/api/quiz/topics > /dev/null; then
    echo "Health check PASSED"
else
    echo "Health check FAILED"
    sudo journalctl -u quizpatenteb --no-pager -n 20
    exit 1
fi

echo "=== Deployment complete ==="
REMOTE
