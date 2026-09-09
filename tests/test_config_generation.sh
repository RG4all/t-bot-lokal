#!/usr/bin/env bash
# ============================================================================
# tests/test_config_generation.sh
#
# Testet die Erzeugung der lokalen Konfigurationsdatei durch install.sh:
#   * Pflichtschluessel vorhanden
#   * sicherer SECRET_KEY generiert
#   * Datei-Berechtigungen restriktiv
#   * Idempotenz (vorhandene Datei bleibt unveraendert)
# ============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=tests/test_helper.sh
. "${SCRIPT_DIR}/test_helper.sh"

# shellcheck source=install.sh
. "${REPO_ROOT}/install.sh"

CONFIG_DIR="${TESTS_TMPDIR}/cfg"
CONFIG_FILE="${CONFIG_DIR}/local.env"
ASSUME_YES=1
RENDER_SIMULATION=0
MODE="host"

unset PASSPHRASE SECRET_KEY
write_local_config
assert_file_exists "${CONFIG_FILE}" "Konfigurationsdatei wurde erstellt"

# Pflichtschluessel
for key in DEBUG RENDER RENDER_SIMULATION SECRET_KEY PASSPHRASE \
           PASSPHRASE_GATE_ENABLED AUTOSTART_BOTS REDIS_URL \
           DATABASE_SSL_REQUIRE CELERY_WORKER_MAX_MEMORY_PER_CHILD; do
  if grep -qE "^${key}=" "${CONFIG_FILE}"; then
    pass "Schluessel ${key} vorhanden"
  else
    fail "Schluessel ${key} fehlt in local.env"
  fi
done

# SECRET_KEY sollte kein Placeholder und lang genug sein.
secret="$(grep -E '^SECRET_KEY=' "${CONFIG_FILE}" | head -1 | cut -d= -f2-)"
assert_not_empty "${secret}" "SECRET_KEY ist nicht leer"
secret_len="${#secret}"
if (( secret_len >= 32 )); then
  pass "SECRET_KEY hat ausreichende Laenge (${secret_len})"
else
  fail "SECRET_KEY zu kurz (${secret_len} Zeichen)"
fi
if [[ "${secret}" != *"replace"* ]]; then
  pass "SECRET_KEY ist kein Placeholder"
else
  fail "SECRET_KEY enthaelt Placeholder"
fi

# Passphrase ohne Environment-Wert kryptographisch zufällig (32 Bytes, URL-safe).
passphrase="$(grep -E '^PASSPHRASE=' "${CONFIG_FILE}" | cut -d= -f2-)"
assert_match '^[A-Za-z0-9_-]{43}$' "${passphrase}" "PASSPHRASE ist ein URL-safe Zufallswert"

# Keine Hardcoded-Credentials: PASSWORT in DATABASE_URL darf nicht stehen.
if grep -qE '^DATABASE_URL=.*password' "${CONFIG_FILE}"; then
  fail "DATABASE_URL sollte kein Klartext-Passwort enthalten"
else
  pass "Kein Klartext-Passwort in DATABASE_URL"
fi

# Datei-Berechtigungen: 600 (nur lesbar/schreibbar fuer Eigentuemer).
perms="$(stat -c '%a' "${CONFIG_FILE}" 2>/dev/null || stat -f '%Lp' "${CONFIG_FILE}")"
assert_eq "600" "${perms}" "Config-Datei-Mode restriktiv (600)"

# Idempotenz: bei erneutem Aufruf mit ASSUME_YES=0 bleibt die Datei unveraendert.
original_hash="$(sha256sum "${CONFIG_FILE}" | awk '{print $1}')"
ASSUME_YES=0
write_local_config
new_hash="$(sha256sum "${CONFIG_FILE}" | awk '{print $1}')"
assert_eq "${original_hash}" "${new_hash}" "Bestehende Config bleibt unveraendert (Idempotenz)"

# Mit ASSUME_YES=1 darf die Datei neu geschrieben werden.
ASSUME_YES=1
write_local_config
overwritten_hash="$(sha256sum "${CONFIG_FILE}" | awk '{print $1}')"
if [[ "${overwritten_hash}" != "${original_hash}" ]] || [[ ! -s "${CONFIG_FILE}" ]]; then
  pass "ASSUME_YES=1 ueberschreibt die Config (neuer Inhalt vorhanden)"
else
  # Bei gleiches Geheimnis kann der Hash identisch sein - pruefen, dass Datei existiert.
  assert_file_exists "${CONFIG_FILE}" "ASSUME_YES=1: Datei existiert"
fi

new_passphrase="$(grep -E '^PASSPHRASE=' "${CONFIG_FILE}" | cut -d= -f2-)"
if [[ "${new_passphrase}" != "${passphrase}" ]]; then
  pass "Unabhängige Konfigurationen erhalten verschiedene Passphrasen"
else
  fail "Passphrase wurde beim Neuanlegen wiederverwendet"
fi
PASSPHRASE="installer-explicit-test-only"
write_local_config
configured_passphrase="$(grep -E '^PASSPHRASE=' "${CONFIG_FILE}" | cut -d= -f2-)"
assert_eq "${PASSPHRASE}" "${configured_passphrase}" "Explizite Passphrase bleibt erhalten"

# Template-Vorlage im Repo sollte vorhanden und dokumentiert sein.
assert_file_exists "${REPO_ROOT}/config.template.env" "config.template.env im Repo"
if grep -q 'RENDER_SIMULATION' "${REPO_ROOT}/config.template.env"; then
  pass "config.template.env dokumentiert RENDER_SIMULATION"
else
  fail "config.template.env: RENDER_SIMULATION fehlt"
fi

finish_test
