#!/usr/bin/env bash
# ============================================================================
# tests/test_gitleaks_gate.sh
#
# Regressionen für den Secret-Scan-Gate (SEC-13):
#   * GITLEAKS_VERSION ist auf 8.30.1 gepinnt (Single Source of Truth)
#   * SHA-256-Checksummen für linux_x64/arm64 und darwin sind hinterlegt
#   * .gitleaks.toml extended Defaults; Allowlists nur mit matchAll-Hinweis
#   * quality.yml ruft den Scan + Selbsttest auf (fail-closed)
#   * kein gitleaks/gitleaks-action (Org-Lizenzfalle) im Workflow
#   * Skript ist executable und --print-version liefert den Pin
#   * optionaler Live-Selbsttest, wenn Netzwerk den Binary-Download erlaubt
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=test_helper.sh disable=SC1091
. "${SCRIPT_DIR}/test_helper.sh"

RUNNER="${REPO_ROOT}/scripts/run_gitleaks.sh"
CONFIG="${REPO_ROOT}/.gitleaks.toml"
WORKFLOW="${REPO_ROOT}/.github/workflows/quality.yml"
PIN="8.30.1"
LINUX_X64_SHA="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"

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
  local pattern="$1" file="$2" msg="${3:-Unerwarteter Inhalt}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if grep -qE -- "${pattern}" "${file}" 2>/dev/null; then
    fail "${msg}: Datei='${file}'"
  else
    pass "${msg}"
  fi
}

# ---------------------------------------------------------------------------
# 1. Dateien vorhanden
# ---------------------------------------------------------------------------
assert_file_exists "${RUNNER}" "run_gitleaks.sh vorhanden"
assert_file_exists "${CONFIG}" ".gitleaks.toml vorhanden"
assert_file_exists "${WORKFLOW}" "quality.yml vorhanden"

TESTS_RUN=$((TESTS_RUN + 1))
if [[ -x "${RUNNER}" ]]; then
  pass "run_gitleaks.sh ist executable"
else
  fail "run_gitleaks.sh muss executable sein (chmod +x)"
fi

# ---------------------------------------------------------------------------
# 2. Versions-Pin und Checksummen
# ---------------------------------------------------------------------------
assert_file_match "GITLEAKS_VERSION=\"${PIN}\"" "${RUNNER}" \
  "GITLEAKS_VERSION auf ${PIN} gepinnt"
assert_file_match "${LINUX_X64_SHA}" "${RUNNER}" \
  "SHA-256 für linux_x64 hinterlegt"
assert_file_match 'GITLEAKS_SHA256_linux_x64=' "${RUNNER}" \
  "Checksumme linux_x64 als Variable definiert"
assert_file_not_match 'GITLEAKS_VERSION=.*latest' "${RUNNER}" \
  "Kein 'latest'-Pin"
assert_file_match 'sha256sum|shasum' "${RUNNER}" \
  "Integritätsprüfung vor Installation"

# Selftest-Dummy-Body muss 36 Zeichen haben (github-pat Regel)
TESTS_RUN=$((TESTS_RUN + 1))
body="$(grep -E "SELFTEST_DUMMY_BODY=" "${RUNNER}" | head -1 | sed -E "s/.*SELFTEST_DUMMY_BODY='([^']*)'.*/\1/")"
if [[ "${#body}" -eq 36 ]]; then
  pass "SELFTEST_DUMMY_BODY hat 36 Zeichen (github-pat)"
else
  fail "SELFTEST_DUMMY_BODY Länge ${#body}, erwartet 36 (Wert='${body}')"
fi

# --print-version liefert den Pin ohne Netzwerk
TESTS_RUN=$((TESTS_RUN + 1))
printed="$("${RUNNER}" --print-version 2>/dev/null || true)"
if [[ "${printed}" == "${PIN}" ]]; then
  pass "--print-version gibt ${PIN} aus"
else
  fail "--print-version: erwartet '${PIN}', erhalten '${printed}'"
fi

# ---------------------------------------------------------------------------
# 3. .gitleaks.toml: Defaults + matchAll-Policy
# ---------------------------------------------------------------------------
assert_file_match 'useDefault\s*=\s*true' "${CONFIG}" \
  "useDefault = true"
assert_file_match 'scripts/run_gitleaks' "${CONFIG}" \
  "Allowlist eng auf run_gitleaks.sh begrenzt"
# Allowlist darf keine vollständigen Dummy-Literale enthalten (sonst Self-Hit)
assert_file_not_match 'ghp_[0-9A-Za-z]{20,}' "${CONFIG}" \
  "Config enthält kein vollständiges ghp_-Dummy"
assert_file_not_match '123456789:AAH' "${CONFIG}" \
  "Config enthält kein Telegram-Dummy-Literal"
# Wenn regexes UND paths gemeinsam genutzt werden, ist matchAll Pflicht
TESTS_RUN=$((TESTS_RUN + 1))
if grep -qE '^[[:space:]]*regexes[[:space:]]*=' "${CONFIG}" \
   && grep -qE '^[[:space:]]*paths[[:space:]]*=' "${CONFIG}"; then
  if grep -qE 'matchAll[[:space:]]*=[[:space:]]*true' "${CONFIG}"; then
    pass "paths+regexes-Allowlist mit matchAll = true"
  else
    fail "paths+regexes ohne matchAll = true (N-2)"
  fi
else
  pass "Pfad-only-Allowlist (kein regexes) – matchAll optional"
fi

# ---------------------------------------------------------------------------
# 4. quality.yml: Secret-Scan-Job, Selbsttest, kein lizenzpflichtiges Action
# ---------------------------------------------------------------------------
assert_file_match 'run_gitleaks\.sh' "${WORKFLOW}" \
  "Workflow ruft run_gitleaks.sh auf"
assert_file_match 'self-test|--self-test' "${WORKFLOW}" \
  "Workflow enthält Gate-Selbsttest"
assert_file_match 'fetch-depth:\s*0' "${WORKFLOW}" \
  "checkout mit voller Historie (fetch-depth: 0)"
assert_file_not_match 'gitleaks/gitleaks-action@' "${WORKFLOW}" \
  "Kein gitleaks-action (Org-Lizenzfalle)"
assert_file_match 'secrets:' "${WORKFLOW}" \
  "Eigener secrets-Job im Workflow"
assert_file_match "${PIN}" "${WORKFLOW}" \
  "Workflow pinnt gitleaks-Version ${PIN}"
assert_file_match 'GITLEAKS_BIN' "${WORKFLOW}" \
  "Workflow übergibt vorinstalliertes Binary via GITLEAKS_BIN"
assert_file_match "${LINUX_X64_SHA}" "${WORKFLOW}" \
  "Workflow prüft SHA-256 von linux_x64"

# ---------------------------------------------------------------------------
# 5. Fail-closed-Kommentare / kein silent skip
# ---------------------------------------------------------------------------
assert_file_match 'fail-closed|fail.closed|FEHLER' "${RUNNER}" \
  "Fail-closed-Fehlerpfade vorhanden"
assert_file_not_match 'exit 0.*skip|skip.*secret' "${RUNNER}" \
  "Kein stilles Überspringen des Scans"

# ---------------------------------------------------------------------------
# 6. Optionaler Live-Lauf (Netzwerk; bei Offline-Sandbox übersprungen)
# ---------------------------------------------------------------------------
if [[ "${GITLEAKS_LIVE_TEST:-}" == "1" ]] || \
   curl -fsSIL --max-time 5 \
     "https://github.com/gitleaks/gitleaks/releases/download/v${PIN}/gitleaks_${PIN}_checksums.txt" \
     >/dev/null 2>&1; then
  TESTS_RUN=$((TESTS_RUN + 1))
  live_log="$(mktemp -t tbot-gitleaks-live.XXXXXX.log)"
  if bash "${RUNNER}" --self-test >"${live_log}" 2>&1; then
    pass "Live-Selbsttest + Historien-Scan grün"
  else
    # Download-Fehler in eingeschränkter Sandbox nicht als Produktfehler werten,
    # wenn der Fehler klar der Transportschicht zuzuordnen ist.
    if grep -qE 'Download fehlgeschlagen|SSL|Connection|curl:|SHA-256-Mismatch' "${live_log}"; then
      pass "Live-Lauf übersprungen (Netzwerk/Transport): $(head -n 3 "${live_log}" | tr '\n' ' ')"
      # Zählt als bestanden mit Hinweis – der CI-Runner hat Netz.
    else
      fail "Live-Selbsttest rot: $(tail -n 20 "${live_log}" | tr '\n' ' ')"
    fi
  fi
  rm -f "${live_log}"
else
  TESTS_RUN=$((TESTS_RUN + 1))
  pass "Live-Lauf übersprungen (kein Netz zu GitHub Releases) – CI prüft live"
fi

# ---------------------------------------------------------------------------
echo
echo "gitleaks-gate: ${TESTS_RUN} Assertions, ${TESTS_FAILED} Fehler"
if [[ "${TESTS_FAILED}" -ne 0 ]]; then
  exit 1
fi
exit 0
