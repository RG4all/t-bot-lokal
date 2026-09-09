#!/usr/bin/env bash
# Regressionen: lokale Secrets generieren, beim Retuning erhalten, Gate nie abschalten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=tests/test_helper.sh
. "${SCRIPT_DIR}/test_helper.sh"
# shellcheck source=scripts/setup_local.sh
. "${REPO_ROOT}/scripts/setup_local.sh"

ENV_LOCAL="${TESTS_TMPDIR}/.env.local"
TUNING_ENV="${TESTS_TMPDIR}/hardware.env"
write_env_local >"${TESTS_TMPDIR}/setup.stdout" 2>"${TESTS_TMPDIR}/setup.stderr"
passphrase="$(read_existing_secret PASSPHRASE)"
secret_key="$(read_existing_secret SECRET_KEY)"
pg_password="$(read_existing_secret POSTGRES_PASSWORD)"
assert_match '^[A-Za-z0-9_-]{48}$' "${passphrase}" "Setup generiert private Passphrase"
assert_match '^[A-Za-z0-9_-]{48}$' "${secret_key}" "Setup generiert privaten Signierschlüssel"
assert_match '^[A-Za-z0-9_-]{48}$' "${pg_password}" "Setup generiert privates Datenbank-Passwort"
if [[ "${pg_password}" == "tbot-local-password" ]]; then
  fail "Setup darf nicht das fruehere oeffentliche Standard-DB-Passwort setzen"
else
  pass "Setup setzt kein oeffentliches Standard-DB-Passwort"
fi
assert_eq "False" "$(read_existing_secret PASSPHRASE_GATE_ENABLED)" "Lokaler Gate bleibt opt-in"
perms="$(stat -c '%a' "${ENV_LOCAL}" 2>/dev/null || stat -f '%Lp' "${ENV_LOCAL}")"
assert_eq "600" "${perms}" "Env-Datei ist privat"
if grep -qF "${passphrase}" "${TESTS_TMPDIR}/setup.stdout" "${TESTS_TMPDIR}/setup.stderr"; then
  fail "Persistierte Passphrase darf nicht ins Setup-Log gelangen"
else
  pass "Persistierte Passphrase wird nicht geloggt"
fi

# Retuning muss einen bereits eingeschalteten Gate respektieren.
sed 's/^PASSPHRASE_GATE_ENABLED=False/PASSPHRASE_GATE_ENABLED=True/' "${ENV_LOCAL}" >"${TESTS_TMPDIR}/enabled.env"
mv "${TESTS_TMPDIR}/enabled.env" "${ENV_LOCAL}"
write_env_local
assert_eq "${passphrase}" "$(read_existing_secret PASSPHRASE)" "Retuning bewahrt Passphrase"
assert_eq "${secret_key}" "$(read_existing_secret SECRET_KEY)" "Retuning bewahrt Signierschlüssel"
assert_eq "${pg_password}" "$(read_existing_secret POSTGRES_PASSWORD)" "Retuning bewahrt Datenbank-Passwort"
assert_eq "True" "$(read_existing_secret PASSPHRASE_GATE_ENABLED)" "Retuning schaltet Gate nicht ab"

ENV_LOCAL="${TESTS_TMPDIR}/independent.env"
write_env_local
if [[ "$(read_existing_secret PASSPHRASE)" != "${passphrase}" ]]; then
  pass "Unabhängige Setups erhalten verschiedene Passphrasen"
else
  fail "Unabhängige Setups teilen dieselbe Passphrase"
fi
if [[ "$(read_existing_secret POSTGRES_PASSWORD)" != "${pg_password}" ]]; then
  pass "Unabhängige Setups erhalten verschiedene Datenbank-Passwörter"
else
  fail "Unabhängige Setups teilen dasselbe Datenbank-Passwort"
fi

# ---------------------------------------------------------------------------
# Hinweise muessen den realen Compose-Aufruf zeigen. Compose laedt fuer die
# Interpolation automatisch nur `.env`; die Secrets liegen in `.env.local`
# (docs/operations/COMPOSE_ENV_FILE.md). Hinweise ohne `--env-file` fuehren
# Anwender direkt in den Abbruch "required variable SECRET_KEY is missing".
# ---------------------------------------------------------------------------
ENV_LOCAL=".env.local"
DRY_RUN=1
dry_run_output="$(start_stack)"
DRY_RUN=0
assert_contains "${dry_run_output}" "docker compose --env-file .env.local up --build -d" \
  "Dry-run nennt den realen Compose-Aufruf inklusive --env-file"

bare_compose="$(
  grep -nE 'docker compose [a-z-]+' "${REPO_ROOT}/scripts/setup_local.sh" |
    grep -v 'docker compose version' |
    grep -v -- '--env-file' || true
)"
assert_eq "" "${bare_compose}" "Kein Compose-Aufruf im Setup ohne --env-file"

help_text="$(bash "${REPO_ROOT}/scripts/setup_local.sh" --help)"
assert_contains "${help_text}" "docker compose --env-file .env.local up --build -d" \
  "--help zeigt den Compose-Aufruf mit --env-file"
assert_contains "${help_text}" "docs/operations/COMPOSE_ENV_FILE.md" \
  "--help verweist auf den Compose-Env-Datei-Artikel"
if [[ "${help_text}" == *"set -euo pipefail"* ]]; then
  fail "Hilfetext darf keinen Shell-Code aus dem Skriptkoerper ausgeben"
else
  pass "Hilfetext bleibt auf den Kopf-Kommentar beschraenkt"
fi

finish_test
