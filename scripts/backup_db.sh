#!/usr/bin/env bash
# Run daily via cron: 0 3 * * * /opt/dietbot/scripts/backup_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p backups
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-botuser}" "${POSTGRES_DB:-dietbot}" \
    | gzip > "backups/backup_${TIMESTAMP}.sql.gz"

# Keep 14 days of local backups. For real durability, also copy these
# off-box (e.g. rclone to a free-tier object storage bucket).
find backups/ -name "*.sql.gz" -mtime +14 -delete
