#!/bin/sh
set -eu

# shellcheck source=docker/load-tuning.sh
. /app/docker/load-tuning.sh

log() { echo "[worker-entrypoint] $*" >&2; }

log "Warte auf Datenbank..."
python manage.py wait_for_database || {
  log "FEHLER: Datenbank ist nicht erreichbar. Siehe Web-Container-Logs fuer Details."
  log "  docker compose logs web"
  log "Abhilfe (loescht die lokale Datenbank):"
  log "  docker compose down -v && docker compose up --build -d"
  exit 1
}

log "Starte Celery-Worker (queues=backtest,scheduling, concurrency=1)..."
exec celery -A trading_bot_project worker \
  -Q backtest,scheduling \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency=1 \
  --prefetch-multiplier=1 \
  --max-tasks-per-child=1 \
  --max-memory-per-child="${CELERY_WORKER_MAX_MEMORY_PER_CHILD:-384000}"
