#!/usr/bin/env bash
# ============================================================================
# tests/test_distro_detection.sh
#
# Testet die Distro-Erkennung von install.sh mit verschiedenen /etc/os-release
# Fixtures (Debian, Ubuntu, Arch, Fedora, Rocky, Amazon Linux, Alpine, openSUSE).
# ============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=tests/test_helper.sh
. "${SCRIPT_DIR}/test_helper.sh"

# install.sh als Bibliothek laden
# shellcheck source=install.sh
. "${REPO_ROOT}/install.sh"

FIXTURES="${FIXTURES_DIR:-${SCRIPT_DIR}/fixtures}/os-release"

run_detection_case() {
  local fixture="$1" expected_id="$2" expected_pm="$3" label="$4"
  detect_distro "${fixture}"
  assert_eq "${expected_id}" "${DISTRO_ID}"   "${label}: ID erkannt"
  assert_eq "${expected_pm}" "${PKG_MANAGER}" "${label}: Paketmanager erkannt"
}

# Debian -> apt
run_detection_case "${FIXTURES}/debian" "debian" "apt" "Debian 12"

# Ubuntu (ID_LIKE=debian) -> apt
run_detection_case "${FIXTURES}/ubuntu" "ubuntu" "apt" "Ubuntu 24.04"

# Arch -> pacman
run_detection_case "${FIXTURES}/arch" "arch" "pacman" "Arch Linux"

# Fedora -> dnf (dnf ist in der Mock-Umgebung nicht vorhanden, die
# Implementierung prueft auf dnf/yum, hier erwarten wir die ID-Zuordnung)
detect_distro "${FIXTURES}/fedora"
assert_eq "fedora" "${DISTRO_ID}" "Fedora 40: ID"
case "${PKG_MANAGER}" in
  dnf|yum) pass "Fedora 40: Paketmanager ist dnf oder yum (${PKG_MANAGER})" ;;
  *) fail "Fedora 40: unerwarteter Paketmanager '${PKG_MANAGER}'" ;;
esac

# Rocky Linux (ID_LIKE='rhel centos fedora') -> dnf/yum
detect_distro "${FIXTURES}/rocky"
assert_eq "rocky" "${DISTRO_ID}" "Rocky 9: ID"
case "${PKG_MANAGER}" in
  dnf|yum) pass "Rocky 9: Paketmanager ist dnf oder yum (${PKG_MANAGER})" ;;
  *) fail "Rocky 9: unerwarteter Paketmanager '${PKG_MANAGER}'" ;;
esac

# Amazon Linux 2023 (ID_LIKE=fedora) -> dnf/yum
detect_distro "${FIXTURES}/amzn"
assert_eq "amzn" "${DISTRO_ID}" "Amazon Linux 2023: ID"
case "${PKG_MANAGER}" in
  dnf|yum) pass "Amazon Linux: Paketmanager ist dnf oder yum (${PKG_MANAGER})" ;;
  *) fail "Amazon Linux: unerwarteter Paketmanager '${PKG_MANAGER}'" ;;
esac

# Alpine -> apk
run_detection_case "${FIXTURES}/alpine" "alpine" "apk" "Alpine 3.20"

# openSUSE Tumbleweed (ID_LIKE=opensuse suse) -> zypper
detect_distro "${FIXTURES}/opensuse"
case "${PKG_MANAGER}" in
  zypper) pass "openSUSE Tumbleweed: Paketmanager zypper" ;;
  *) fail "openSUSE Tumbleweed: unerwarteter Paketmanager '${PKG_MANAGER}'" ;;
esac

# Edge-Case: nicht vorhandene os-release-Datei auf einem Linux-System soll
# nicht crashen, sondern entweder Fallback verwenden oder mit klarer Meldung
# enden. Wir testen hier nur, dass detect_distro ohne Crash zurueckkehrt.
detect_distro "/nonexistent/os-release" >/dev/null 2>&1 || true
pass "detect_distro mit fehlender Datei crashed nicht"

# Sicherstellen, dass PRETTY_NAME/VERSION fuer Debian geparst werden.
detect_distro "${FIXTURES}/debian"
assert_match "Debian GNU/Linux 12" "${DISTRO_NAME}" "Debian: PRETTY_NAME geparst"
assert_eq "12" "${DISTRO_VERSION}" "Debian: VERSION_ID geparst"

finish_test
