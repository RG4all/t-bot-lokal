#!/usr/bin/env bash
# ============================================================================
# tests/test_package_managers.sh
#
# Mock-Tests: stellt sicher, dass install.sh je nach Distribution die
# korrekten Paketmanager-Operationen (update + install mit passenden Paketen)
# aufruft, ohne echte Installation durchzufuehren.
# ============================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=tests/test_helper.sh
. "${SCRIPT_DIR}/test_helper.sh"

MOCK_BIN="${FIXTURES_DIR:-${SCRIPT_DIR}/fixtures}/mock-bin"
export MOCK_PM_LOG="${TESTS_TMPDIR}/pm.log"
: > "${MOCK_PM_LOG}"

# Sicherstellen, dass die Mock-Binaries ausfuehrbar sind.
chmod +x "${MOCK_BIN}"/* 2>/dev/null || true

# PATH mit Mocks vorne
export PATH="${MOCK_BIN}:${PATH}"

# shellcheck source=install.sh
. "${REPO_ROOT}/install.sh"

ASSUME_YES=1
MODE="container"
PROFILE="full"   # volle Paketliste, auch Build-Tools
INSTALL_LOG="${TESTS_TMPDIR}/install.log"
: > "${INSTALL_LOG}"

# ---------- apt ----------
PKG_MANAGER="apt"; DISTRO_ID="debian"
pm_update "apt"
pm_install "apt" redis-server python3 build-essential
assert_file_exists "${MOCK_PM_LOG}" "apt: Logdatei existiert"

apt_log="$(cat "${MOCK_PM_LOG}")"
assert_contains "${apt_log}" "apt-get update"         "apt: Update-Aufruf geloggt"
assert_contains "${apt_log}" "apt-get install"        "apt: Install-Aufruf geloggt"
assert_contains "${apt_log}" "redis-server"           "apt: redis-server enthaelt"
assert_contains "${apt_log}" "build-essential"        "apt: build-essential enthaelt"

: > "${MOCK_PM_LOG}"

# ---------- pacman ----------
PKG_MANAGER="pacman"; DISTRO_ID="arch"
pm_update "pacman"
pm_install "pacman" redis python base-devel
pacman_log="$(cat "${MOCK_PM_LOG}")"
assert_contains "${pacman_log}" "pacman -Sy"          "pacman: Sync-Update geloggt"
assert_contains "${pacman_log}" "pacman -S"           "pacman: Install geloggt"
assert_contains "${pacman_log}" "base-devel"          "pacman: base-devel enthaelt"

: > "${MOCK_PM_LOG}"

# ---------- dnf ----------
PKG_MANAGER="dnf"; DISTRO_ID="fedora"
pm_update "dnf"
pm_install "dnf" redis python3 gcc
dnf_log="$(cat "${MOCK_PM_LOG}")"
assert_contains "${dnf_log}" "dnf -y makecache"       "dnf: makecache geloggt"
assert_contains "${dnf_log}" "dnf -y install"         "dnf: install geloggt"
assert_contains "${dnf_log}" "python3"                "dnf: python3 enthaelt"

: > "${MOCK_PM_LOG}"

# ---------- yum ----------
PKG_MANAGER="yum"; DISTRO_ID="centos"
pm_update "yum"
pm_install "yum" redis
yum_log="$(cat "${MOCK_PM_LOG}")"
assert_contains "${yum_log}" "yum -y makecache"       "yum: makecache geloggt"
assert_contains "${yum_log}" "yum -y install"         "yum: install geloggt"

: > "${MOCK_PM_LOG}"

# ---------- zypper ----------
PKG_MANAGER="zypper"; DISTRO_ID="opensuse"
pm_update "zypper"
pm_install "zypper" redis
zypper_log="$(cat "${MOCK_PM_LOG}")"
assert_contains "${zypper_log}" "zypper"              "zypper: aufgerufen"
assert_contains "${zypper_log}" "refresh"             "zypper: refresh geloggt"
assert_contains "${zypper_log}" "install"             "zypper: install geloggt"
assert_contains "${zypper_log}" "redis"               "zypper: redis enthaelt"

: > "${MOCK_PM_LOG}"

# ---------- apk ----------
PKG_MANAGER="apk"; DISTRO_ID="alpine"
pm_update "apk"
pm_install "apk" redis
apk_log="$(cat "${MOCK_PM_LOG}")"
assert_contains "${apk_log}" "apk update"             "apk: update geloggt"
assert_contains "${apk_log}" "apk add"                "apk: add geloggt"
assert_contains "${apk_log}" "redis"                  "apk: redis enthaelt"

# ---------- package_list Vollstaendigkeit ----------
# Fuer jedes Profil und jeden Paketmanager muss mindestens redis UND python
# (Host) bzw. die Laufzeit-Bibliotheken (Container) enthalten sein.
for pm in apt pacman dnf yum zypper apk; do
  full_pkgs="$(package_list "${pm}" "full")"
  rt_pkgs="$(package_list "${pm}" "runtime")"

  case "${pm}" in
    apt)
      assert_contains "${full_pkgs}"    "redis-server" "package_list(${pm}, full): redis-server"
      assert_contains "${full_pkgs}"    "python3"      "package_list(${pm}, full): python3"
      assert_contains "${rt_pkgs}" "libpango-1.0-0" "package_list(${pm}, runtime): pango"
      ;;
    pacman)
      assert_contains "${full_pkgs}"    "redis"     "package_list(${pm}, full): redis"
      assert_contains "${full_pkgs}"    "python"    "package_list(${pm}, full): python"
      assert_contains "${rt_pkgs}" "pango"     "package_list(${pm}, runtime): pango"
      ;;
    dnf|yum)
      assert_contains "${full_pkgs}"    "redis"     "package_list(${pm}, full): redis"
      assert_contains "${full_pkgs}"    "python3"   "package_list(${pm}, full): python3"
      assert_contains "${rt_pkgs}" "pango"     "package_list(${pm}, runtime): pango"
      ;;
    zypper)
      assert_contains "${full_pkgs}"    "redis"     "package_list(${pm}, full): redis"
      assert_contains "${full_pkgs}"    "python3"   "package_list(${pm}, full): python3"
      assert_contains "${rt_pkgs}" "libpango-1_0-0" "package_list(${pm}, runtime): pango"
      ;;
    apk)
      assert_contains "${full_pkgs}"    "redis"     "package_list(${pm}, full): redis"
      assert_contains "${full_pkgs}"    "python3"   "package_list(${pm}, full): python3"
      assert_contains "${rt_pkgs}" "pango"     "package_list(${pm}, runtime): pango"
      ;;
  esac

  # Runtime darf keine Build-Tools enthalten.
  if [[ "${rt_pkgs}" == *"build-essential"* || "${rt_pkgs}" == *"base-devel"*
        || "${rt_pkgs}" == *"build-base"* || "${rt_pkgs}" == *"gcc"* ]]; then
    fail "package_list(${pm}, runtime): sollte keine Build-Tools enthalten"
  else
    pass "package_list(${pm}, runtime): keine Build-Tools"
  fi
done

# Leere Paketliste in pm_install soll ohne Fehler und ohne Aufruf beendet werden.
: > "${MOCK_PM_LOG}"
pm_install "apt"
empty_size="$(wc -c <"${MOCK_PM_LOG}")"
assert_eq "0" "${empty_size}" "pm_install mit leerer Liste macht nichts"

finish_test
