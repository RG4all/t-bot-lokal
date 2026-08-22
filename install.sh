#!/usr/bin/env bash
# ============================================================================
# install.sh - Universelles Build- & Install-Skript fuer t-bot-lokal
#
# Distribution-agnostisch: erkennt Debian/Ubuntu (apt), Arch (pacman) und
# RHEL/CentOS/Fedora (dnf/yum) und installiert alle Abhaengigkeiten mit dem
# passenden Paketmanager. Richtet Redis als lokalen Service ein und erstellt
# eine Konfigurationsdatei, in der die Render.com-Simulation optional
# aktiviert werden kann.
#
# Das Skript ist idempotent: mehrfache Ausfuehrung ist sicher.
#
# Modi:
#   auto       (Standard) Container werden automatisch erkannt.
#   host       Erzwungene Host-Installation inkl. Redis-Service.
#   container  Container/Build-Kontext: keine Service-Verwaltung, kein sudo.
#
# Nutzung:
#   ./install.sh [--mode=auto|host|container] [--yes] [--no-redis]
#                [--render-simulation] [--config-dir=DIR] [--profile=full|runtime]
# ============================================================================

set -euo pipefail

# Damit das Skript fuer Unit-Tests gesourct werden kann, ohne main() auszufuehren.
if [[ "${__INSTALL_SOURCED:-0}" == "1" ]]; then
  __INSTALL_LIB_MODE__=1
fi

# ---------------------------------------------------------------------------
# Globals / Defaults
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_NAME SCRIPT_DIR

MODE="auto"
ASSUME_YES=0
INSTALL_REDIS=1
RENDER_SIMULATION=0
PROFILE="full"          # full | runtime
CONFIG_DIR="${SCRIPT_DIR}/config"
CONFIG_FILE=""

# Erkannte Werte (werden von detect_distro gesetzt)
DISTRO_ID=""
DISTRO_ID_LIKE=""
DISTRO_NAME=""
DISTRO_VERSION=""
PKG_MANAGER=""          # apt | pacman | dnf | yum

# Temporaeres Log-File (sicher angelegt, beim Exit entfernt)
INSTALL_LOG=""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_info()  { printf '\033[0;32m[INFO]\033[0m  %s\n' "$*" >&2; }
log_warn()  { printf '\033[0;33m[WARN]\033[0m  %s\n' "$*" >&2; }
log_error() { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; }
log_step()  { printf '\n\033[1;36m==>\033[0m \033[1m%s\033[0m\n' "$*" >&2; }

die() {
  log_error "$*"
  exit 1
}

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

is_container() {
  # Indikatoren fuer Docker/Podman/LXC/Container
  [[ -f /.dockerenv ]] && return 0
  grep -qaE 'docker|containerd|podman|lxc' /proc/1/cgroup 2>/dev/null && return 0
  [[ -f /run/.containerenv ]] && return 0
  return 1
}

setup_logfile() {
  INSTALL_LOG="$(mktemp -t tbot-install.XXXXXXXXXX.log)"
  chmod 600 "${INSTALL_LOG}"
  trap 'rm -f "${INSTALL_LOG}"' EXIT
  log_info "Installations-Log: ${INSTALL_LOG}"
}

run_logged() {
  # Fuehrt einen Befehl aus und schreibt Output ins Log.
  "$@" >>"${INSTALL_LOG}" 2>&1
}

# ---------------------------------------------------------------------------
# Parameter-Parsing
# ---------------------------------------------------------------------------
usage() {
  cat <<USAGE
${SCRIPT_NAME} - universelles Build- & Install-Skript fuer t-bot-lokal

Nutzung: ${SCRIPT_NAME} [OPTIONEN]

  --mode=MODE          auto|host|container (Standard: auto)
  --profile=PROFILE    full (Host, inkl. Build-Tools) | runtime (Container)
  --yes, -y            Keine interaktiven Nachfragen
  --no-redis           Redis nicht installieren/konfigurieren
  --render-simulation  Render.com-Simulation in der erzeugten Config aktivieren
  --config-dir=DIR     Verzeichnis fuer die lokale Konfiguration
  -h, --help           Diese Hilfe anzeigen
USAGE
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "${arg}" in
      --mode=*)            MODE="${arg#*=}" ;;
      --profile=*)         PROFILE="${arg#*=}" ;;
      --config-dir=*)      CONFIG_DIR="${arg#*=}" ;;
      --yes|-y)            ASSUME_YES=1 ;;
      --no-redis)          INSTALL_REDIS=0 ;;
      --render-simulation) RENDER_SIMULATION=1 ;;
      -h|--help)           usage; exit 0 ;;
      *)                   die "Unbekannte Option: ${arg}" ;;
    esac
  done

  case "${MODE}" in
    auto|host|container) ;;
    *) die "Ungueltiger --mode: ${MODE}" ;;
  esac
  case "${PROFILE}" in
    full|runtime) ;;
    *) die "Ungueltiger --profile: ${PROFILE}" ;;
  esac

  CONFIG_FILE="${CONFIG_DIR}/local.env"
}

# ---------------------------------------------------------------------------
# Privilegien
# ---------------------------------------------------------------------------
ensure_privileges() {
  if [[ "${MODE}" == "container" ]]; then
    # Im Container sind wir i.d.R. root; kein sudo noetig.
    return 0
  fi
  if [[ "${EUID}" -eq 0 ]]; then
    return 0
  fi
  if have sudo; then
    log_info "Erhoehe Privilegien via sudo (Passwort-Abfrage moeglich)..."
    local -a sudo_args
    sudo_args=(bash "${BASH_SOURCE[0]}" --mode="${MODE}" --profile="${PROFILE}" --config-dir="${CONFIG_DIR}")
    [[ "${ASSUME_YES}" -eq 1 ]]    && sudo_args+=(--yes)
    [[ "${INSTALL_REDIS}" -eq 0 ]] && sudo_args+=(--no-redis)
    [[ "${RENDER_SIMULATION}" -eq 1 ]] && sudo_args+=(--render-simulation)
    exec sudo -E "${sudo_args[@]}"
  fi
  die "Dieses Skript benoetigt Root-Rechte (sudo) im Host-Modus."
}

# ---------------------------------------------------------------------------
# Distro-Erkennung
# ---------------------------------------------------------------------------
# Liest /etc/os-release und extrahiert ID, ID_LIKE, PRETTY_NAME, VERSION_ID.
# Parameter 1: optionaler Pfad zu einer os-release-Datei (fuer Tests).
detect_distro() {
  local os_release="${1:-/etc/os-release}"
  local id="" id_like="" name="" version=""

  if [[ -r "${os_release}" ]]; then
    # shellcheck disable=SC1090
    . "${os_release}" 2>/dev/null || true
    id="${ID:-}"
    id_like="${ID_LIKE:-}"
    name="${PRETTY_NAME:-${NAME:-unknown}}"
    version="${VERSION_ID:-}"
  elif [[ "$(uname -s 2>/dev/null)" == "Linux" ]]; then
    # Fallback: aeltere Systeme ohne os-release
    if [[ -r /etc/redhat-release ]]; then
      id="rhel"; name="$(head -1 /etc/redhat-release)"
    elif [[ -r /etc/arch-release ]]; then
      id="arch"; name="Arch Linux"
    elif [[ -r /etc/debian_version ]]; then
      id="debian"; name="Debian $(cat /etc/debian_version)"
    fi
  fi

  DISTRO_ID="${id,,}"
  DISTRO_ID_LIKE="${id_like,,}"
  DISTRO_NAME="${name}"
  DISTRO_VERSION="${version}"

  case "${DISTRO_ID}" in
    debian|ubuntu|linuxmint|pop|neon|elementary|zorin|kali|raspbian)
      PKG_MANAGER="apt" ;;
    arch|manjaro|endeavouros|garuda|artix)
      PKG_MANAGER="pacman" ;;
    fedora|rhel|centos|rocky|almalinux|ol|amzn|scientific)
      if have dnf; then PKG_MANAGER="dnf"
      elif have yum; then PKG_MANAGER="yum"
      else PKG_MANAGER="dnf"
      fi ;;
    opensuse|opensuse-leap|opensuse-tumbleweed|suse|sles)
      PKG_MANAGER="zypper" ;;
    alpine)
      PKG_MANAGER="apk" ;;
    *)
      # Auch ID_LIKE beruecksichtigen
      if [[ " ${DISTRO_ID_LIKE} " == *" debian "* ]] || \
         [[ " ${DISTRO_ID_LIKE} " == *" ubuntu "* ]]; then
        PKG_MANAGER="apt"
      elif [[ " ${DISTRO_ID_LIKE} " == *" arch "* ]]; then
        PKG_MANAGER="pacman"
      elif [[ " ${DISTRO_ID_LIKE} " == *" rhel "* ]] || \
           [[ " ${DISTRO_ID_LIKE} " == *" fedora "* ]] || \
           [[ " ${DISTRO_ID_LIKE} " == *" centos "* ]]; then
        if have dnf; then PKG_MANAGER="dnf"; else PKG_MANAGER="yum"; fi
      elif [[ " ${DISTRO_ID_LIKE} " == *" suse "* ]] || \
           [[ " ${DISTRO_ID_LIKE} " == *" opensuse "* ]]; then
        PKG_MANAGER="zypper"
      elif [[ " ${DISTRO_ID_LIKE} " == *" alpine "* ]]; then
        PKG_MANAGER="apk"
      # Wenn keine ID passt, aber ein bekannter PM vorhanden ist: verwenden.
      elif have apt-get; then PKG_MANAGER="apt"
      elif have pacman;  then PKG_MANAGER="pacman"
      elif have dnf;     then PKG_MANAGER="dnf"
      elif have yum;     then PKG_MANAGER="yum"
      elif have zypper;  then PKG_MANAGER="zypper"
      elif have apk;     then PKG_MANAGER="apk"
      else
        PKG_MANAGER=""
      fi
      ;;
  esac

  [[ -n "${PKG_MANAGER}" ]] || die "Kein unterstuetzter Paketmanager erkannt (Distro: ${DISTRO_ID:-unbekannt})."
}

print_distro_banner() {
  log_step "System"
  log_info "Distribution : ${DISTRO_NAME} (${DISTRO_ID} ${DISTRO_VERSION})"
  log_info "Paketmanager : ${PKG_MANAGER}"
  log_info "Profil       : ${PROFILE}"
  log_info "Modus        : ${MODE}"
}

# ---------------------------------------------------------------------------
# Paketlisten
# ---------------------------------------------------------------------------
# Gibt die fuer aktuellen Paketmanager + Profil benoetigten Pakete auf stdout
# aus (genau ein Paket pro Zeile).
package_list() {
  local pm="$1" profile="$2"

  # Laufzeit-Abhaengigkeiten (in allen Profilen erforderlich)
  local -a runtime_pkgs=()
  # Host-/Vollinstallations-Pakete (Redis, Python, Build-Tools)
  local -a host_extra=()

  case "${pm}" in
    apt)
      runtime_pkgs=(
        ca-certificates
        coreutils
        fonts-dejavu-core
        gawk
        grep
        libharfbuzz-subset0
        libpango-1.0-0
        libpangoft2-1.0-0
        procps
        sed
      )
      host_extra=(
        build-essential
        curl
        libpq-dev
        python3
        python3-dev
        python3-pip
        python3-venv
        redis-server
        redis-tools
      )
      ;;
    pacman)
      runtime_pkgs=(
        ca-certificates
        coreutils
        dejavu-fonts
        gawk
        grep
        harfbuzz
        pango
        procps-ng
        sed
      )
      host_extra=(
        base-devel
        curl
        postgresql-libs
        python
        python-pip
        redis
      )
      ;;
    dnf|yum)
      runtime_pkgs=(
        ca-certificates
        coreutils
        dejavu-sans-fonts
        gawk
        grep
        harfbuzz
        pango
        procps-ng
        sed
      )
      host_extra=(
        gcc
        gcc-c++
        make
        curl
        postgresql-devel
        python3
        python3-devel
        python3-pip
        redis
      )
      ;;
    zypper)
      runtime_pkgs=(
        ca-certificates
        coreutils
        dejavu-fonts
        gawk
        grep
        libharfbuzz0
        libpango-1_0-0
        procps
        sed
      )
      host_extra=(
        curl
        postgresql-devel
        python3
        python3-devel
        python3-pip
        redis
      )
      # 'devel_basis' ist das aequivalent zu build-essential/base-devel und
      # wird als Pattern nach dem Paketblock installiert.
      ;;
    apk)
      runtime_pkgs=(
        ca-certificates
        coreutils
        dejavu-fonts
        gawk
        grep
        harfbuzz
        pango
        procps
        sed
      )
      host_extra=(
        build-base
        curl
        postgresql-dev
        python3
        python3-dev
        py3-pip
        redis
      )
      ;;
    *)
      die "package_list: unbekannter Paketmanager '${pm}'"
      ;;
  esac

  local pkg
  for pkg in "${runtime_pkgs[@]}"; do printf '%s\n' "${pkg}"; done
  if [[ "${profile}" == "full" ]]; then
    for pkg in "${host_extra[@]}"; do printf '%s\n' "${pkg}"; done
  fi
}

# ---------------------------------------------------------------------------
# Paketmanager-Operationen
# ---------------------------------------------------------------------------
pm_update() {
  local pm="$1"
  log_step "Paketquellen aktualisieren (${pm})"
  case "${pm}" in
    apt)
      DEBIAN_FRONTEND=noninteractive run_logged apt-get update -y
      ;;
    pacman)
      run_logged pacman -Sy --noconfirm --needed
      ;;
    dnf)
      run_logged dnf -y makecache
      # EPEL wird fuer Redis unter RHEL/CentOS benoetigt.
      if [[ "${DISTRO_ID}" =~ ^(rhel|centos|rocky|almalinux|ol|scientific)$ ]]; then
        if ! dnf -q repolist enabled 2>/dev/null | grep -qiE 'epel'; then
          log_info "Installiere EPEL-Repository (fuer Redis)..."
          run_logged dnf -y install epel-release || \
            log_warn "EPEL konnte nicht installiert werden; Redis ggf. nicht verfuegbar."
        fi
      fi
      ;;
    yum)
      run_logged yum -y makecache
      if [[ "${DISTRO_ID}" =~ ^(rhel|centos|rocky|almalinux|ol|scientific)$ ]]; then
        if ! yum -q repolist enabled 2>/dev/null | grep -qiE 'epel'; then
          log_info "Installiere EPEL-Repository (fuer Redis)..."
          run_logged yum -y install epel-release || true
        fi
      fi
      ;;
    zypper)
      run_logged zypper --non-interactive --gpg-auto-import-keys refresh
      ;;
    apk)
      run_logged apk update
      ;;
  esac
}

pm_install() {
  local pm="$1"; shift
  local -a pkgs=("$@")
  [[ "${#pkgs[@]}" -gt 0 ]] || return 0

  log_step "Installiere ${#pkgs[@]} Paket(e) via ${pm}"
  local pkg
  for pkg in "${pkgs[@]}"; do
    printf '  - %s\n' "${pkg}"
  done

  case "${pm}" in
    apt)
      DEBIAN_FRONTEND=noninteractive run_logged apt-get install -y \
        --no-install-recommends "${pkgs[@]}"
      ;;
    pacman)
      run_logged pacman -S --noconfirm --needed "${pkgs[@]}"
      ;;
    dnf)
      run_logged dnf -y install "${pkgs[@]}"
      ;;
    yum)
      run_logged yum -y install "${pkgs[@]}"
      ;;
    zypper)
      run_logged zypper --non-interactive install --no-recommends "${pkgs[@]}"
      # openSUSE-Build-Toolchain als Pattern (aquirivalent zu build-essential).
      if [[ "${PROFILE}" == "full" ]]; then
        run_logged zypper --non-interactive install -t pattern devel_basis || true
      fi
      ;;
    apk)
      run_logged apk add --no-cache "${pkgs[@]}"
      ;;
  esac
}

install_dependencies() {
  local -a pkgs=()
  while IFS= read -r pkg; do
    [[ -n "${pkg}" ]] && pkgs+=("${pkg}")
  done < <(package_list "${PKG_MANAGER}" "${PROFILE}")

  pm_update "${PKG_MANAGER}"
  pm_install "${PKG_MANAGER}" "${pkgs[@]}"
}

# ---------------------------------------------------------------------------
# Python-Abhaengigkeiten (nur Host-Modus)
# ---------------------------------------------------------------------------
install_python_requirements() {
  [[ "${MODE}" == "container" ]] && return 0
  [[ -f "${SCRIPT_DIR}/requirements.txt" ]] || { log_warn "keine requirements.txt gefunden"; return 0; }

  log_step "Python-Abhaengigkeiten"
  if [[ ! -d "${SCRIPT_DIR}/.venv" ]]; then
    log_info "Erstelle virtuelle Umgebung in .venv/"
    run_logged python3 -m venv "${SCRIPT_DIR}/.venv"
  fi
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/.venv/bin/activate"
  run_logged python -m pip install --upgrade pip
  run_logged pip install -r "${SCRIPT_DIR}/requirements.txt"
  deactivate
  log_info "Python-Abhaengigkeiten in .venv installiert."
}

# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
have_systemd() { [[ -d /run/systemd/system ]] && have systemctl; }
have_openrc()   { have rc-service; }

redis_service_name() {
  case "${PKG_MANAGER}" in
    apt)         echo "redis-server" ;;
    pacman|dnf|yum|zypper|apk) echo "redis" ;;
    *)           echo "redis" ;;
  esac
}

start_redis_service() {
  local svc; svc="$(redis_service_name)"

  if have_systemd; then
    log_info "Aktiviere/Starte Redis via systemd (${svc})..."
    systemctl enable "${svc}"  >>"${INSTALL_LOG}" 2>&1 || true
    systemctl restart "${svc}" >>"${INSTALL_LOG}" 2>&1 || \
      die "Redis konnte via systemctl nicht gestartet werden."
  elif have_openrc; then
    log_info "Aktiviere/Starte Redis via OpenRC (${svc})..."
    rc-update add "${svc}" default >>"${INSTALL_LOG}" 2>&1 || true
    rc-service "${svc}" restart   >>"${INSTALL_LOG}" 2>&1 || \
      die "Redis konnte via OpenRC nicht gestartet werden."
  elif have service; then
    log_info "Versuche Redis via 'service' zu starten..."
    service "${svc}" restart >>"${INSTALL_LOG}" 2>&1 || \
      log_warn "Konnte Redis nicht via 'service' starten (ggf. Container ohne Init)."
  else
    log_warn "Kein Init-System erkannt - Redis-Service nicht gestartet."
    return 0
  fi
}

wait_for_redis() {
  local url="${1:-redis://127.0.0.1:6379/0}"
  local host port
  host="$(printf '%s' "${url}" | sed -E 's#redis://([^:/]+).*#\1#')"
  port="$(printf '%s' "${url}" | sed -E 's#.*:([0-9]+).*#\1#')"
  port="${port:-6379}"

  log_info "Pruefe Redis-Erreichbarkeit unter ${host}:${port} ..."
  local attempt
  for attempt in $(seq 1 30); do
    if have redis-cli && redis-cli -h "${host}" -p "${port}" ping 2>/dev/null | grep -q PONG; then
      log_info "Redis ist bereit (Versuch ${attempt})."
      return 0
    fi
    sleep 1
  done
  return 1
}

ensure_redis() {
  [[ "${INSTALL_REDIS}" -eq 1 ]] || { log_info "Redis-Installation uebersprungen (--no-redis)."; return 0; }
  [[ "${MODE}" != "container" ]] || { log_info "Redis wird im Container-Modus nicht lokal installiert."; return 0; }
  have redis-cli || have redis-server || die "Redis wurde installiert, aber redis-cli/redis-server fehlen."

  log_step "Redis"
  start_redis_service || true
  if wait_for_redis "${REDIS_URL:-redis://127.0.0.1:6379/0}"; then
    log_info "Redis-PING erfolgreich."
  else
    log_warn "Redis antwortet nicht. Versuche einen Neustart..."
    start_redis_service || true
    wait_for_redis "${REDIS_URL:-redis://127.0.0.1:6379/0}" || \
      die "Redis konnte nicht gestartet werden. Siehe Log: ${INSTALL_LOG}"
  fi
}

# ---------------------------------------------------------------------------
# Konfigurationsdatei
# ---------------------------------------------------------------------------
# Erstellt die lokale Konfigurationsdatei. Die Render.com-Simulation ist
# standardmaessig deaktiviert und kann durch Setzen von RENDER_SIMULATION=True
# (oder die --render-simulation-Flag) aktiviert werden, ohne das lokale Setup
# zu beeintraechtigen.
write_local_config() {
  log_step "Konfiguration"
  mkdir -p "${CONFIG_DIR}"
  if [[ -f "${CONFIG_FILE}" ]] && [[ "${ASSUME_YES}" -ne 1 ]]; then
    log_info "Konfiguration existiert bereits: ${CONFIG_FILE} (unveraendert)"
    return 0
  fi

  local render_flag="False"
  [[ "${RENDER_SIMULATION}" -eq 1 ]] && render_flag="True"

  cat >"${CONFIG_FILE}" <<EOF
# ============================================================================
# t-bot-lokal - lokale Konfiguration (erzeugt von install.sh)
# Nicht committen - Datei ist in .gitignore enthalten.
# ============================================================================

# --- Render.com-Simulation --------------------------------------------------
# Standardmaessig DEAKTIVIERT, damit das lokale Setup unbeeinflusst bleibt.
# Zum Aktivieren auf True setzen und die Ressourcen-Limits unten anpassen.
RENDER=False
RENDER_SIMULATION=${render_flag}

# --- Kern-Konfiguration -----------------------------------------------------
DEBUG=True
SECRET_KEY=${SECRET_KEY:-$(head -c 48 /dev/urandom 2>/dev/null | base64 | tr -d '/+=' | head -c 48)}
PASSPHRASE=${PASSPHRASE:-local-t-bot}
PASSPHRASE_GATE_ENABLED=True
AUTOSTART_BOTS=True

# --- Datenbank / Redis (lokal) ---------------------------------------------
# Ohne DATABASE_URL wird SQLite verwendet. Fuer die lokale Compose-Stapel-
# verarbeitung werden die Werte in .env.docker.example gepflegt.
# DATABASE_URL=postgresql://tbot:tbot-local-password@localhost:5432/tbot
REDIS_URL=redis://127.0.0.1:6379/0
DATABASE_SSL_REQUIRE=False

# --- Optionale Ressourcen-Limits (nur bei RENDER_SIMULATION=True relevant) -
WEB_CPUS=0.50
WORKER_CPUS=0.50
POSTGRES_CPUS=0.50
REDIS_CPUS=0.20
SCHEDULER_CPUS=0.20
CELERY_LOG_LEVEL=INFO
CELERY_WORKER_MAX_MEMORY_PER_CHILD=384000
EOF

  chmod 600 "${CONFIG_FILE}"
  log_info "Lokale Konfiguration geschrieben: ${CONFIG_FILE}"
}

# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"

  if [[ "${MODE}" == "auto" ]]; then
    if is_container; then MODE="container"; else MODE="host"; fi
  fi

  # Profil an Modus anpassen, falls nicht explizit gesetzt.
  if [[ "${MODE}" == "container" ]] && [[ "${PROFILE}" == "full" ]]; then
    PROFILE="runtime"
  fi

  ensure_privileges
  setup_logfile
  detect_distro
  print_distro_banner
  install_dependencies
  install_python_requirements
  ensure_redis
  write_local_config

  log_step "Fertig"
  log_info "t-bot-lokal Installationsroutine abgeschlossen."
  log_info "Naechster Schritt (Host):"
  log_info "  source ${CONFIG_FILE} && python manage.py runserver"
  log_info "Naechster Schritt (Docker):"
  log_info "  docker compose up --build -d"
}

# Nur ausfuehren, wenn nicht als Bibliothek gesourct.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
