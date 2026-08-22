#!/usr/bin/env bash
# ============================================================================
# tests/distro_smoke_test.sh
#
# Baut je ein minimales Docker-Image auf Debian, Arch, Fedora, Alpine und
# openSUSE und fuehrt darin install.sh im Container-Modus aus. Stellt sicher,
# dass die Distro-Erkennung und Paketinstallation auf allen Paketmanager-
# Familien (apt/pacman/dnf/apk/zypper) funktioniert.
#
# Benoetigt installiertes `docker` und wird ueblicherweise in CI ausgefuehrt.
# Wird ohne Docker mit Exit-Code 0 (skipped) beendet, damit die Test-Suite
# auf Systemen ohne Docker nicht hart fehlschlaegt.
# ============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "[SKIP] docker nicht verfuegbar - Distro-Smoke-Tests werden uebersprungen."
  exit 0
fi

SMOKE_DIR="$(mktemp -d -t tbot-distro.XXXXXXXXXX)"
# shellcheck disable=SC2064
trap 'rm -rf "${SMOKE_DIR}"' EXIT

run_case() {
  local base_image="$1" label="$2"
  local dockerfile="${SMOKE_DIR}/Dockerfile.${label}"

  cat > "${dockerfile}" <<EOF
FROM ${base_image}
WORKDIR /app
COPY install.sh /app/install.sh
RUN chmod +x /app/install.sh && /app/install.sh --mode=container --profile=runtime --yes
EOF

  echo "==> Smoke-Test: ${label} (${base_image})"
  if docker build --progress=plain -f "${dockerfile}" "${REPO_ROOT}" 2>&1 \
       | tee "${SMOKE_DIR}/${label}.log" | tail -20; then
    echo "[PASS] ${label}"
  else
    echo "[FAIL] ${label} (siehe ${SMOKE_DIR}/${label}.log)"
    return 1
  fi
}

failures=0
run_case "debian:bookworm-slim" "debian"  || failures=$((failures+1))
run_case "archlinux:latest"     "arch"    || failures=$((failures+1))
run_case "fedora:40"            "fedora"  || failures=$((failures+1))
run_case "alpine:3.20"          "alpine"  || failures=$((failures+1))
run_case "opensuse/tumbleweed"  "opensuse" || failures=$((failures+1))

if (( failures > 0 )); then
  echo "${failures} Distro-Smoke-Test(s) fehlgeschlagen."
  exit 1
fi
echo "Alle Distro-Smoke-Tests erfolgreich."
