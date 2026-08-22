#!/bin/sh
# ============================================================================
# docker/redis-entrypoint.sh
#
# Startet Redis mit den von hardware-test.sh berechneten Parametern aus
# /tbot-runtime/tuning.env (sofern verfuegbar). Andernfalls werden sichere
# Defaults verwendet. Dadurch passt sich der Redis-Container automatisch an
# die verfuegbaren Ressourcen der laufenden Umgebung an.
#
# POSIX-sh geschrieben, damit das Skript auch unter BusyBox ash im
# redis:alpine-Image laeuft (kein bash, keine Arrays, kein pipefail).
# ============================================================================
set -eu

TUNING_FILE="${TUNING_RUNTIME_DIR:-/tbot-runtime}/tuning.env"

# Sichere Defaults (passen zur bisherigen Konfiguration im Compose-File).
# REDIS_MAXMEMORY_MB kann ueber die Shell/.env gesetzt werden; der Tuner
# schreibt denselben Schluessel und ueberschreibt den Default dann.
# REDIS_MAXMEMORY (z.B. "96mb" aus der Compose-Default-Umgebung) wird
# als Fallback akzeptiert.
REDIS_MAXMEMORY_MB="${REDIS_MAXMEMORY_MB:-${REDIS_MAXMEMORY:-48}}"
REDIS_MAXMEMORY_POLICY="${REDIS_MAXMEMORY_POLICY:-noeviction}"
REDIS_IO_THREADS="${REDIS_IO_THREADS:-1}"

if [ -r "${TUNING_FILE}" ]; then
  set -a
  # shellcheck source=/dev/null
  . "${TUNING_FILE}"
  set +a
  echo "[redis] Tuning-Datei geladen: ${TUNING_FILE}" >&2
else
  echo "[redis] Keine Tuning-Datei gefunden - verwende Defaults." >&2
fi

# REDIS_MAXMEMORY aus der Compose-Default-Umgebung kann z.B. "96mb" sein;
# nicht-numerische Anteile (mb/MB/Leerzeichen) entfernen.
REDIS_MAXMEMORY_MB="$(echo "${REDIS_MAXMEMORY_MB}" | tr -cd '0-9')"
: "${REDIS_MAXMEMORY_MB:=48}"

# Policy gegen eine Allowlist pruefen, damit kein beliebiger String
# als redis-server-Argument landet.
case "${REDIS_MAXMEMORY_POLICY}" in
  noeviction|allkeys-lru|volatile-lru|allkeys-random|volatile-random|volatile-ttl|allkeys-lfu|volatile-lfu)
    ;;
  *)
    echo "[redis] Ungueltige maxmemory-policy '${REDIS_MAXMEMORY_POLICY}', nutze noeviction." >&2
    REDIS_MAXMEMORY_POLICY="noeviction"
    ;;
esac

# io-threads muss eine positive Ganzzahl sein.
case "${REDIS_IO_THREADS}" in
  ''|*[!0-9]*) REDIS_IO_THREADS=1 ;;
esac

echo "[redis] maxmemory=${REDIS_MAXMEMORY_MB}mb policy=${REDIS_MAXMEMORY_POLICY} io-threads=${REDIS_IO_THREADS}" >&2

# Konfigurations-Argumente fuer redis-server (POSIX: Positionsparameter
# statt Array). Sicherer Start: keine Persistenz (lokale Entwicklung).
set -- \
  --save "" \
  --appendonly no \
  --maxmemory "${REDIS_MAXMEMORY_MB}mb" \
  --maxmemory-policy "${REDIS_MAXMEMORY_POLICY}"

# io-threads lohnt sich nur, wenn > 1; ansonsten nicht setzen.
if [ "${REDIS_IO_THREADS}" -gt 1 ] 2>/dev/null; then
  set -- "$@" --io-threads "${REDIS_IO_THREADS}"
fi

# Das offizielle redis:*-Image setzt User/Rechte korrekt und startet
# redis-server als PID 1. Wir ersetzen nur die Kommandozeile.
exec redis-server "$@"
