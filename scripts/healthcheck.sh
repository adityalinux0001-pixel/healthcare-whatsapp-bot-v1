#!/usr/bin/env bash
# Run every 5 min via cron: */5 * * * * /opt/dietbot/scripts/healthcheck.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! curl -fs http://localhost:8000/health/ready > /dev/null; then
    echo "$(date): health check failed, restarting app + worker" >> healthcheck.log
    docker compose restart app worker
fi
