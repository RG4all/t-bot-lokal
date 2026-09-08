#!/usr/bin/env bash
# ============================================================================
# tests/test_compose_security.sh
#
# Regressionen: Keine Standard-Passwoerter im Compose-/Setup-Surface (SEC-12).
#   * docker-compose.yml verlangt SECRET_KEY, PASSPHRASE und POSTGRES_PASSWORD
#     ueber die ${VAR:?...}-Interpolation (bricht ohne Env-Datei ab)
#   * keine ${VAR:-default}-Fallbacks fuer Secrets in docker-compose.yml
#   * die frueheren oeffentlichen Defaults erscheinen in keiner
#     ausgelieferten Konfigurations-/Skriptdatei (Findings-Dokumente in docs/findings
#     zitieren sie bewusst als historischen Befund)
#   * Env-Beispiele enthalten keine benutzbaren Secret-Werte, sondern leere
#     Platzhalter mit Erzeugungshinweis
#   * optional, falls Docker verfuegbar ist: `docker compose config` schlaegt
#     ohne gesetzte Secrets fehl und funktioniert mit ihnen
# ============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=tests/test_helper.sh
. "${SCRIPT_DIR}/test_helper.sh"

COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

assert_file_match() {
  local pattern="$1" file="$2" msg="${3:-Datei matched nicht}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if grep -qE -- "${pattern}" "${file}" 2>/dev/null; then
    pass "${msg}"
  else
    fail "${msg}: Muster='${pattern}' Datei='${file}'"
  fi
}

assert_file_not_match() {
  local pattern="$1" file="$2" msg="${3:-Unerwarteter Inhalt gefunden}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if grep -qE -- "${pattern}" "${file}" 2>/dev/null; then
    fail "${msg}: Datei='${file}'"
  else
    pass "${msg}"
  fi
}

# ---------------------------------------------------------------------------
# 1. ${VAR:?...} Pflicht-Interpolation fuer alle drei Secrets
# ---------------------------------------------------------------------------
for secret in SECRET_KEY PASSPHRASE POSTGRES_PASSWORD; do
  assert_file_match "\\\$\{${secret}\:\?" "${COMPOSE_FILE}" \
    "${secret} ist Pflichtwert (\${${secret}:?...}) in docker-compose.yml"
done

# ---------------------------------------------------------------------------
# 2. Keine ${VAR:-default}-Fallbacks fuer Secrets mehr (nicht-sortierte
#    Benutzervariables bleiben unberuehrt; nur Secrets sind Pflichtwerte)
# ---------------------------------------------------------------------------
for secret in SECRET_KEY PASSPHRASE POSTGRES_PASSWORD; do
  assert_file_not_match "\\\$\{${secret}\:-" "${COMPOSE_FILE}" \
    "kein Default-Fallback fuer ${secret} in docker-compose.yml"
done

# ---------------------------------------------------------------------------
# 3. Historische oeffentliche Defaults in keiner Auslieferungsdatei
# ---------------------------------------------------------------------------
delivery_files=(
  "docker-compose.yml"
  ".env.example"
  ".env.docker.example"
  "config.template"
  "install.sh"
  "scripts/setup_local.sh"
  "docker-entrypoint.sh"
  "docker/beat-entrypoint.sh"
  "docker/load-tuning.sh"
  "docker/postgres-entrypoint.sh"
  "docker/redis-entrypoint.sh"
  "docker/tuner-entrypoint.sh"
  "docker/worker-entrypoint.sh"
)
for file in "${delivery_files[@]}"; do
  assert_file_not_match "tbot-local-password|local-t-bot" "${REPO_ROOT}/${file}" \
    "kein Standard-Passwort in ${file}"
done

# ---------------------------------------------------------------------------
# 4. Env-Beispiele: leere Platzhalter statt benutzbarer Werte
# ---------------------------------------------------------------------------
for key in SECRET_KEY PASSPHRASE POSTGRES_PASSWORD; do
  for env_file in ".env.example" ".env.docker.example"; do
    value="$(grep -E "^${key}=" "${REPO_ROOT}/${env_file}" 2>/dev/null | head -1 | cut -d= -f2-)"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ -n "${value}" ]]; then
      fail "${env_file} setzt ${key} auf einen konkreten Wert"
    else
      pass "${env_file} haelt ${key} als leeren Platzhalter"
    fi
  done
done

# ---------------------------------------------------------------------------
# 5. Optional: Compose-Interpolation mit Docker pruefen
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  workdir="${TESTS_TMPDIR}/compose-proj"
  mkdir -p "${workdir}"
  # Ohne Env-Datei/Umgebung darf die Interpolation nicht durchlaufen.
  TESTS_RUN=$((TESTS_RUN + 1))
  if docker compose --project-directory "${workdir}" -f "${COMPOSE_FILE}" config >/dev/null 2>&1; then
    fail "docker compose akzeptiert fehlende Secrets"
  else
    pass "docker compose bricht ohne gesetzte Secrets ab"
  fi
  # Mit gesetzten Secrets muss die Konfiguration aufloesen.
  cat > "${workdir}/.env" <<'EOF'
SECRET_KEY=test-only-secret-key-for-compose-config
PASSPHRASE=test-only-passphrase-for-compose-config
POSTGRES_PASSWORD=test-only-db-password-for-compose-config
EOF
  TESTS_RUN=$((TESTS_RUN + 1))
  if docker compose --project-directory "${workdir}" -f "${COMPOSE_FILE}" config >/dev/null 2>&1; then
    pass "docker compose loest mit gesetzten Secrets auf"
  else
    fail "docker compose scheitert trotz gesetzter Secrets"
  fi
  rm -rf "${workdir}"
else
  printf '  [SKIP] Docker/Compose nicht verfuegbar - statische Pruefungen oben genuegen.\n'
fi

finish_test
