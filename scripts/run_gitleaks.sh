#!/usr/bin/env bash
# ============================================================================
# scripts/run_gitleaks.sh – gepinnter, fail-closed Secret-Scan für t-bot-lokal
#
# Warum CLI statt gitleaks/gitleaks-action:
#   RG4all ist eine Organisation. gitleaks-action@v2/v3 verlangt dort eine
#   GITLEAKS_LICENSE (kommerzieller Keygen-Check) und bricht ohne Secret ab.
#   Die freie gitleaks-CLI braucht keine Lizenz und scannt lokal wie in CI.
#
# Warum Version pin:
#   Ohne GITLEAKS_VERSION nutzt gitleaks-action einen hardcodierten Default
#   (aktuell 8.24.3). Neue Releases ändern Regelwerk und Allowlist-Semantik
#   still – Gate-Ergebnisse hängen dann vom Veröffentlichungsdatum ab.
#   Hier ist die Version die Single Source of Truth; CI und Entwickler sehen
#   denselben Stand (analog zu ruff==0.16.6 / shellcheck-py).
#
# Aufruf:
#   scripts/run_gitleaks.sh              # Historien-Scan des Repos (Exit 0/1)
#   scripts/run_gitleaks.sh --self-test  # Fail-closed-Selbsttest + Scan
#   scripts/run_gitleaks.sh --print-version
#   GITLEAKS_BIN=/pfad/gitleaks scripts/run_gitleaks.sh   # vorinstalliert
#
# Exit-Codes: 0 = sauber (bzw. Selbsttest ok), 1 = Leaks/Fehler/Selbsttest rot.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- Single Source of Truth -------------------------------------------------
readonly GITLEAKS_VERSION="8.30.1"
readonly GITLEAKS_BASE_URL="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}"
readonly GITLEAKS_CONFIG="${GITLEAKS_CONFIG:-${REPO_ROOT}/.gitleaks.toml}"

# SHA-256 aus dem offiziellen Release v8.30.1 (GitHub-Asset-Digest, 2026-09-14).
GITLEAKS_SHA256_linux_x64="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"
GITLEAKS_SHA256_linux_arm64="e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"
GITLEAKS_SHA256_darwin_x64="dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709"
GITLEAKS_SHA256_darwin_arm64="b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"

# Dummy NUR für den Selbsttest. GitHub-PAT-Form (ghp_ + 36 Zeichen).
# Hohe Entropie erforderlich: reine 'A'-Folgen filtert gitleaks per Entropy-
# Threshold still (Selbsttest würde Exit 0 statt 2 liefern → Gate tot).
# Absichtlich zweigeteilt, damit der Historien-Scan dieses Skript nicht meldet.
readonly SELFTEST_DUMMY_PREFIX='ghp_'
readonly SELFTEST_DUMMY_BODY='1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P7q8R'
readonly SELFTEST_DUMMY="${SELFTEST_DUMMY_PREFIX}${SELFTEST_DUMMY_BODY}"

MODE="scan"
for arg in "$@"; do
  case "${arg}" in
    --self-test) MODE="self-test" ;;
    --print-version) printf '%s\n' "${GITLEAKS_VERSION}"; exit 0 ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0
      ;;
    *)
      printf 'Unbekanntes Argument: %s (siehe --help)\n' "${arg}" >&2
      exit 1
      ;;
  esac
done

log() { printf 'gitleaks: %s\n' "$*" >&2; }
die() { printf 'gitleaks: FEHLER: %s\n' "$*" >&2; exit 1; }

sha256_of() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file}" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file}" | awk '{print $1}'
  else
    die "weder sha256sum noch shasum verfügbar – Integrität nicht prüfbar"
  fi
}

resolve_target() {
  local os arch
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "${os}" in
    linux) os="linux" ;;
    darwin) os="darwin" ;;
    *) die "Nicht unterstütztes OS: ${os}" ;;
  esac
  case "${arch}" in
    x86_64|amd64) arch="x64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) die "Nicht unterstützte Architektur: ${arch}" ;;
  esac
  printf '%s_%s' "${os}" "${arch}"
}

expected_sha() {
  local target="$1"
  case "${target}" in
    linux_x64) printf '%s' "${GITLEAKS_SHA256_linux_x64}" ;;
    linux_arm64) printf '%s' "${GITLEAKS_SHA256_linux_arm64}" ;;
    darwin_x64) printf '%s' "${GITLEAKS_SHA256_darwin_x64}" ;;
    darwin_arm64) printf '%s' "${GITLEAKS_SHA256_darwin_arm64}" ;;
    *) die "Keine Checksumme für Target ${target}" ;;
  esac
}

# Setzt GITLEAKS_PATH (kein Command-Substitution: die() muss die Shell beenden).
install_gitleaks() {
  if [[ -n "${GITLEAKS_BIN:-}" ]]; then
    [[ -x "${GITLEAKS_BIN}" ]] || die "GITLEAKS_BIN nicht ausführbar: ${GITLEAKS_BIN}"
    GITLEAKS_PATH="${GITLEAKS_BIN}"
    log "Nutze vorgegebenes Binary ${GITLEAKS_PATH}"
    return 0
  fi

  local target cache_root cache_dir archive url expected tmpdir tarball actual
  target="$(resolve_target)"
  expected="$(expected_sha "${target}")"

  if [[ -n "${RUNNER_TOOL_CACHE:-}" ]]; then
    cache_root="${RUNNER_TOOL_CACHE}/gitleaks"
  else
    cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}/t-bot-gitleaks"
  fi
  cache_dir="${cache_root}/${GITLEAKS_VERSION}/${target}"
  GITLEAKS_PATH="${cache_dir}/gitleaks"

  if [[ -x "${GITLEAKS_PATH}" ]]; then
    log "Cache-Treffer ${GITLEAKS_PATH}"
    return 0
  fi

  log "Lade gitleaks v${GITLEAKS_VERSION} (${target}) …"
  mkdir -p "${cache_dir}"
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/gitleaks-fetch.XXXXXX")"
  # shellcheck disable=SC2064
  trap 'rm -rf "'"${tmpdir}"'"' EXIT

  archive="gitleaks_${GITLEAKS_VERSION}_${target}.tar.gz"
  url="${GITLEAKS_BASE_URL}/${archive}"
  tarball="${tmpdir}/${archive}"

  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --retry-delay 2 -o "${tarball}" "${url}" \
      || die "Download fehlgeschlagen: ${url}"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "${tarball}" "${url}" \
      || die "Download fehlgeschlagen: ${url}"
  else
    die "weder curl noch wget verfügbar – gitleaks kann nicht geladen werden"
  fi

  actual="$(sha256_of "${tarball}")"
  if [[ "${actual}" != "${expected}" ]]; then
    die "SHA-256-Mismatch für ${archive}: erwartet ${expected}, erhalten ${actual}"
  fi

  tar -xzf "${tarball}" -C "${tmpdir}" gitleaks \
    || die "Archiv enthält kein gitleaks-Binary"
  mv -f "${tmpdir}/gitleaks" "${GITLEAKS_PATH}"
  chmod 0755 "${GITLEAKS_PATH}"
  rm -rf "${tmpdir}"
  trap - EXIT
  log "Installiert nach ${GITLEAKS_PATH}"
}

run_detect() {
  local extra=()
  if [[ "$#" -gt 0 ]]; then
    extra=("$@")
  fi
  set +e
  "${GITLEAKS_PATH}" detect \
    --source "${REPO_ROOT}" \
    --config "${GITLEAKS_CONFIG}" \
    --redact \
    --no-banner \
    --exit-code=2 \
    "${extra[@]}"
  local rc=$?
  set -e
  return "${rc}"
}

# Fail-closed-Selbsttest in isoliertem Temp-Verzeichnis (nicht im Repo-Tree):
# gitleaks --no-git MUSS Exit 2 liefern, sonst ist das Gate wirkungslos.
self_test() {
  local tmpdir leak_file out_log rc_all
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/gitleaks-selftest.XXXXXX")"
  leak_file="${tmpdir}/must_find_leak.py"
  out_log="${tmpdir}/out.log"

  cleanup_selftest() {
    rm -rf "${tmpdir}"
  }
  trap cleanup_selftest EXIT

  printf '# self-test MUST-FIND leak\nGITHUB_TOKEN = "%s"\n' \
    "${SELFTEST_DUMMY}" >"${leak_file}"

  log "Selbsttest: Dummy-PAT in isoliertem Temp-Dir muss auffallen …"
  set +e
  "${GITLEAKS_PATH}" detect \
    --source "${tmpdir}" \
    --no-git \
    --redact \
    --no-banner \
    --exit-code=2 \
    --verbose >"${out_log}" 2>&1
  rc_all=$?
  set -e

  if [[ "${rc_all}" -ne 2 ]]; then
    log "Selbsttest-Ausgabe (Auszug):"
    tail -n 80 "${out_log}" >&2 || true
    cleanup_selftest
    trap - EXIT
    die "Selbsttest FEHLGESCHLAGEN: Exit ${rc_all}, erwartet 2 (Leak). Gate ist nicht wirksam."
  fi

  log "Selbsttest OK – Exit 2 (Leak erkannt), Gate fail-closed."
  cleanup_selftest
  trap - EXIT
}

main() {
  log "start mode=${MODE} version-pin=${GITLEAKS_VERSION} root=${REPO_ROOT}"
  [[ -f "${GITLEAKS_CONFIG}" ]] || die "Config fehlt: ${GITLEAKS_CONFIG}"

  install_gitleaks
  log "Binary: ${GITLEAKS_PATH}"
  "${GITLEAKS_PATH}" version >&2 || true

  if [[ "${MODE}" == "self-test" ]]; then
    self_test
  fi

  log "Historien-Scan (fetch-depth=0 vorausgesetzt in CI) …"
  local rc=0
  run_detect || rc=$?
  if [[ "${rc}" -eq 0 ]]; then
    log "no leaks found"
    exit 0
  fi
  if [[ "${rc}" -eq 2 ]]; then
    log "LEAKS DETECTED – siehe Ausgabe oben (redactiert)"
    exit 1
  fi
  die "gitleaks brach mit unerwartetem Status ${rc} ab"
}

main
