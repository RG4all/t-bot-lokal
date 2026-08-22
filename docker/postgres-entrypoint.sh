#!/usr/bin/env sh
# ============================================================================
# docker/postgres-entrypoint.sh
#
# Umschliesst das offizielle postgres:17-Image-Entrypoint und setzt die von
# hardware-test.sh berechneten Tuneables ueber die PGOPTIONS-Umgebung, die
# das Standard-Entrypoint als postgres -c Argumente an den Server weitergibt.
# Damit werden max_connections, shared_buffers, effective_cache_size und
# work_mem an die Host-Ressourcen angepasst.
# ============================================================================
set -eu

TUNING_FILE="${TUNING_RUNTIME_DIR:-/tbot-runtime}/tuning.env"

# Sichere Defaults (passen zu kleinen Lokal-Setups).
POSTGRES_MAX_CONNECTIONS="${POSTGRES_MAX_CONNECTIONS:-40}"
POSTGRES_SHARED_BUFFERS_MB="${POSTGRES_SHARED_BUFFERS_MB:-32}"
POSTGRES_EFFECTIVE_CACHE_MB="${POSTGRES_EFFECTIVE_CACHE_MB:-128}"
POSTGRES_WORK_MEM_MB="${POSTGRES_WORK_MEM_MB:-4}"

if [ -r "${TUNING_FILE}" ]; then
  set -a
  # shellcheck source=/dev/null
  . "${TUNING_FILE}"
  set +a
  echo "[postgres] Tuning-Datei geladen: ${TUNING_FILE}" >&2
else
  echo "[postgres] Keine Tuning-Datei - verwende Defaults." >&2
fi

echo "[postgres] max_connections=${POSTGRES_MAX_CONNECTIONS} shared_buffers=${POSTGRES_SHARED_BUFFERS_MB}MB effective_cache_size=${POSTGRES_EFFECTIVE_CACHE_MB}MB work_mem=${POSTGRES_WORK_MEM_MB}MB" >&2

export PGOPTIONS=""

# Das offizielle Entrypoint-Skript akzeptiert postgres-Optionen als Argumente.
exec docker-entrypoint.sh postgres \
  -c "max_connections=${POSTGRES_MAX_CONNECTIONS}" \
  -c "shared_buffers=${POSTGRES_SHARED_BUFFERS_MB}MB" \
  -c "effective_cache_size=${POSTGRES_EFFECTIVE_CACHE_MB}MB" \
  -c "work_mem=${POSTGRES_WORK_MEM_MB}MB"
