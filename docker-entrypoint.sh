#!/bin/sh
set -eu

# Automatisch generierte Hardware-Tuning-Werte laden (sofern verfuegbar).
# shellcheck source=docker/load-tuning.sh
. /app/docker/load-tuning.sh

log() { echo "[entrypoint] $*" >&2; }

log "Warte auf Datenbank..."
USE_DIRECT_DATABASE_URL=True python manage.py wait_for_database || {
  log "FEHLER: Datenbank ist nicht erreichbar."
  log ""
  log "Falls das Postgres-Volume bereits mit einem anderen Passwort"
  log "initialisiert wurde, stimmt POSTGRES_PASSWORD nicht mehr mit dem"
  log "Server ueberein. Postgres liest POSTGRES_PASSWORD nur beim ersten"
  log "Initialisieren eines leeren Datenverzeichnisses."
  log ""
  log "Abhilfe (loescht die lokale Datenbank):"
  log "  docker compose down -v"
  log "  docker compose up --build -d"
  log "bzw.: scripts/setup_local.sh --reset-db --yes"
  exit 1
}

log "Fuehre Migrationen aus..."
USE_DIRECT_DATABASE_URL=True python manage.py migrate --noinput

log "Starte Daphne auf Port ${PORT:-8369}..."
exec daphne -b 0.0.0.0 -p "${PORT:-8369}" trading_bot_project.asgi:application
