#!/usr/bin/env bash
# Deploy Quiz Patente B to production server.
#
# Frontend is built LOCALLY before push (the production VM has no Node.js).
# This script verifies that frontend/dist/ matches the current frontend source
# before pushing, then runs the remote pull + restart.
#
# Usage: ./scripts/deploy.sh

set -euo pipefail

SERVER_USER="azureuser"
SERVER_HOST="172.173.116.28"
SSH_KEY="$HOME/.ssh/ReportFolio_key.pem"
REMOTE_DIR="/home/azureuser/quizpatenteb"

echo "=== Local pre-flight: rebuild frontend so dist/ matches source ==="
(cd frontend && npm run build)

if ! git diff --quiet frontend/dist/ || [ -n "$(git ls-files --others --exclude-standard frontend/dist/)" ]; then
    echo "ERROR: frontend/dist/ has uncommitted changes after rebuild."
    echo "Commit and push them before deploying:"
    git status frontend/dist/
    exit 1
fi

echo "=== Pushing local main to origin ==="
git push origin main

echo "=== Deploying on $SERVER_HOST ==="
ssh -i "$SSH_KEY" "$SERVER_USER@$SERVER_HOST" bash -s <<'REMOTE'
set -euo pipefail
cd /home/azureuser/quizpatenteb

echo "--- Pulling latest code ---"
git pull --ff-only origin main

echo "--- Installing Python dependencies ---"
source .venv/bin/activate
pip install -e . --quiet

# No npm here: production VM has no Node.js. frontend/dist/ is committed
# in the repo, so `git pull` above already brought the latest bundle.

echo "--- Restarting service ---"
sudo systemctl restart quizpatenteb

echo "--- Waiting for service to start ---"
sleep 3

echo "--- Health check ---"
if curl -sf http://127.0.0.1:8500/api/health > /dev/null; then
    echo "Health check PASSED"
else
    echo "Health check FAILED"
    sudo journalctl -u quizpatenteb --no-pager -n 20
    exit 1
fi

echo "=== Deployment complete ==="
REMOTE
