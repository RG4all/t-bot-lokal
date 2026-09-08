#!/usr/bin/env bash
# ============================================================================
# scripts/setup_local.sh - Ein-Schritt-Setup fuer die lokale t-bot-Umgebung
#
# Was das Skript macht:
#   1. Prueft, ob Docker/Compose verfuegbar ist, und bietet optional die
#      Installation ueber install.sh an (--install-deps).
#   2. Fuehrt hardware-test.sh aus, um .env.local mit optimierten Werten
#      fuer Redis, PostgreSQL und die Compose-CPU-Limits zu erzeugen.
#      Bereits vorhandene Secrets (SECRET_KEY, PASSPHRASE, POSTGRES_PASSWORD)
#      bleiben beim Retuning erhalten.
#   3. Legt beim ersten Lauf .env.local an (Mode 0600) und schreibt nie
#      hartcodierte oder oeffentlich bekannte Secrets; das lokale
#      DB-Passwort wird zufaellig erzeugt und beim Retuning beibehalten.
#   4. Baut die Images und startet den isolierten Stack mit
#      `docker compose up --build -d`.
#
# Nutzung:
#   scripts/setup_local.sh                       # Setup + Start
#   scripts/setup_local.sh --install-deps        # vorher auch Systempakete/Docker
#   scripts/setup_local.sh --no-up               # nur .env.local erzeugen, nicht starten
#   scripts/setup_local.sh --render-free-simulation
#   scripts/setup_local.sh --reset-db            # bestehendes Postgres-Volume
#                                                # zuruecksetzen (Password-Mismatch)
#   scripts/setup_local.sh --reset-db --yes      # ohne interaktive Nachfrage
#   scripts/setup_local.sh --dry-run             # nur geplante Aktionen anzeigen
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

ENV_LOCAL="${ENV_LOCAL:-.env.local}"
TUNING_ENV="${TUNING_ENV:-config/hardware.env}"
INSTALL_DEPS=0
DO_UP=1
RENDER_FREE=0
RESET_DB=0
FORCE_RESET_DB=0
DRY_RUN=0

# ANSI-Farben
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
  C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BOLD=""; C_RESET=""
fi

info()  { printf '%s[setup]%s %s\n' "${C_GREEN}" "${C_RESET}" "$*" >&2; }
warn()  { printf '%s[setup]%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
error() { printf '%s[setup]%s %s\n' "${C_RED}"  "${C_RESET}" "$*" >&2; }
die()   { error "$*"; exit 1; }

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

parse_args() {
  for arg in "$@"; do
    case "${arg}" in
      --install-deps) INSTALL_DEPS=1 ;;
      --no-up)        DO_UP=0 ;;
      --render-free-simulation|--render-simulation) RENDER_FREE=1 ;;
      --reset-db)     RESET_DB=1 ;;
      --yes|-y)       FORCE_RESET_DB=1 ;;
      --dry-run)      DRY_RUN=1 ;;
      -h|--help)      usage ;;
      *) die "Unbekannte Option: ${arg}" ;;
    esac
  done
}

have() { command -v "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------------------
# 1. Docker/Compose pruefen (optional installieren)
# ---------------------------------------------------------------------------
check_docker() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf 'dry-run: Docker/Compose-Check übersprungen\n'
    return 0
  fi
  if have docker && docker compose version >/dev/null 2>&1; then
    info "Docker und Compose v2 verfuegbar."
    return 0
  fi

  if [[ "${INSTALL_DEPS}" -eq 1 ]]; then
    warn "Docker/Compose nicht gefunden - starte install.sh (Host-Modus)..."
    ./install.sh --mode=host --profile=full --yes
    have docker || die "Docker war nach der Installation nicht im PATH. Bitte neu einloggen."
  else
    cat >&2 <<EOF
${C_BOLD}Docker Engine mit Compose v2 wird benoetigt.${C_RESET}
- Docker Desktop: https://docs.docker.com/desktop/
- Linux: https://docs.docker.com/engine/install/
Alternativ:
  scripts/setup_local.sh --install-deps   (versucht Systempakete zu installieren)
EOF
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# 2. Hardware-Analyse durchfuehren
# ---------------------------------------------------------------------------
run_hardware_test() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf 'dry-run: Hardware-Analyse -> %s\n' "${TUNING_ENV}"
    return 0
  fi
  info "Starte Hardware-Analyse -> ${TUNING_ENV}"
  mkdir -p "$(dirname "${TUNING_ENV}")"
  ./hardware-test.sh --env-out="${TUNING_ENV}" --format=text >&2
}

# ---------------------------------------------------------------------------
# 3. .env.local zusammenbauen (Secrets werden beibehalten)
# ---------------------------------------------------------------------------
read_existing_secret() {
  local key="$1"
  [[ -f "${ENV_LOCAL}" ]] || return 0
  local val
  val="$(grep -E "^${key}=" "${ENV_LOCAL}" | head -1 | cut -d= -f2- || true)"
  if [[ -n "${val}" ]]; then
    printf '%s' "${val}"
  fi
}

random_secret() {
  head -c 48 /dev/urandom 2>/dev/null | base64 | tr -d '/+=' | head -c 48
}

write_env_local() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf 'dry-run: .env.local würde mit WEB_PORT=8369 und PASSPHRASE_GATE_ENABLED=False erzeugt\n'
    return 0
  fi
  local secret_key passphrase gate_enabled pg_password web_port
  secret_key="$(read_existing_secret SECRET_KEY)"
  passphrase="$(read_existing_secret PASSPHRASE)"
  gate_enabled="$(read_existing_secret PASSPHRASE_GATE_ENABLED)"
  pg_password="$(read_existing_secret POSTGRES_PASSWORD)"
  web_port="$(read_existing_secret WEB_PORT)"

  [[ -z "${secret_key}" ]]  && secret_key="$(random_secret)"
  [[ -z "${passphrase}" ]]  && passphrase="$(random_secret)"
  [[ -z "${gate_enabled}" ]] && gate_enabled="False"
  # Zufälliges, privat gehaltenes Datenbank-Passwort statt eines öffentlichen
  # Defaults: docker-compose.yml verlangt POSTGRES_PASSWORD seit 2.4.11 als
  # Pflichtwert (${POSTGRES_PASSWORD:?...}). Bestehende Werte bleiben beim
  # Retuning erhalten; ein einmal initialisiertes Postgres-Volume akzeptiert
  # nur das Passwort aus dem ersten Lauf (siehe docs/operations/FAQ.md Abschnitt 5).
  [[ -z "${pg_password}" ]]  && pg_password="$(random_secret)"
  [[ -z "${web_port}" ]]     && web_port="8369"

  # Rotationshinweis, falls noch der frueher oeffentliche Standard verwendet
  # wird. Der Wert selbst steht seit SEC-12 bewusst nirgends mehr im
  # Repository; der SHA-256-Vergleich identifiziert ihn ohne erneute
  # Veroeffentlichung zuverlaessig.
  legacy_pg_default_sha256="e7013a6e80c208770b6a8a89806e2a2fd7344cdd1596555234254a6df4003236"
  if [[ "$(printf '%s' "${pg_password}" | sha256sum | cut -d' ' -f1)" == "${legacy_pg_default_sha256}" ]]; then
    warn "POSTGRES_PASSWORD entspricht dem frueher oeffentlichen Standard-Passwort."
    warn "Rotation empfohlen: 1) POSTGRES_PASSWORD-Zeile in ${ENV_LOCAL} leeren,"
    warn "2) dieses Skript erneut ausfuehren, 3) 'scripts/setup_local.sh --reset-db --yes'"
    warn "(loescht die lokale Datenbank, siehe docs/operations/FAQ.md Abschnitt 5)."
  fi

  info "Schreibe ${ENV_LOCAL} (Mode 0600, Secrets werden beibehalten)..."
  umask 077
  if [[ -f "${ENV_LOCAL}" ]]; then
    chmod 600 "${ENV_LOCAL}"
  fi
  {
    echo "# ============================================================================"
    echo "# t-bot-lokal .env.local - automatisch erzeugt von setup_local.sh"
    echo "# Nicht committen. Bereits vorhandene Werte fuer SECRET_KEY, PASSPHRASE und"
    echo "# POSTGRES_PASSWORD wurden beim Retuning beibehalten."
    echo "# ============================================================================"
    echo ""
    echo "SECRET_KEY=${secret_key}"
    echo "PASSPHRASE=${passphrase}"
    # Docker ist lokal standardmäßig ohne zusätzliche Passphrase-Ebene.
    echo "PASSPHRASE_GATE_ENABLED=${gate_enabled}"
    echo "AUTOSTART_BOTS=True"
    echo ""
    echo "POSTGRES_DB=tbot"
    echo "POSTGRES_USER=tbot"
    echo "POSTGRES_PASSWORD=${pg_password}"
    echo "WEB_PORT=${web_port}"
    echo ""
    echo "# --- Hardware-Tuning (Quelle: ${TUNING_ENV}) ---"
    if [[ -f "${TUNING_ENV}" ]]; then
      grep -vE '^(#|$)' "${TUNING_ENV}"
    fi
    echo ""
    echo "# --- Render-Free-Simulation (default aus) ---"
    if [[ "${RENDER_FREE}" -eq 1 ]]; then
      echo "RENDER=True"
      echo "RENDER_SIMULATION=True"
      echo "WEB_CPUS=0.10"
    else
      echo "RENDER=False"
      echo "RENDER_SIMULATION=False"
    fi
  } > "${ENV_LOCAL}"
  chmod 0600 "${ENV_LOCAL}"
  info "${ENV_LOCAL} geschrieben."
}

# ---------------------------------------------------------------------------
# 4. Ggf. altes Postgres-Volume zuruecksetzen
# ---------------------------------------------------------------------------
# Wenn ein frueherer Lauf bereits das Postgres-Volume initialisiert hat
# (z.B. mit dem Default-Passwort aus .env.docker.example oder mit einem
# aelteren, anderen POSTGRES_PASSWORD), akzeptiert der Server das neue
# Passwort aus .env.local nicht (Postgres liest POSTGRES_PASSWORD nur beim
# ersten Initialisieren eines leeren Datenverzeichnisses). Dann schlagen
# wait_for_database/Migrationen fehl und Web/Worker crashen in einer
# Restart-Schleife ("health: starting"). Diese Funktion erkennt die
# Situation und setzt das Volume nach expliziter Bestaetigung zurueck.
reset_db_if_needed() {
  [[ "${RESET_DB}" -eq 1 ]] || return 0

  if [[ "${FORCE_RESET_DB}" -ne 1 ]] && [[ -t 0 ]]; then
    warn "Das wird das lokale Postgres-Volume (t-bot-local_postgres_data)"
    warn "und ALLE lokalen Datenbankinhalte unwiderruflich loeschen."
    printf '%s[setup]%s Wirklich fortfahren? [j/N] ' "${C_YELLOW}" "${C_RESET}" >&2
    read -r answer
    case "${answer}" in
      j|J|y|Y|yes|YES) ;;
      *) die "Abgebrochen." ;;
    esac
  fi

  info "Stoppe Stack und entferne Postgres-Volume..."
  docker compose --env-file "${ENV_LOCAL}" rm -sf postgres 2>/dev/null || true
  docker volume rm -f t-bot-local_postgres_data 2>/dev/null || \
    docker volume rm -f tbotlocal_postgres_data 2>/dev/null || true
  info "Postgres-Volume zurueckgesetzt."
}

# ---------------------------------------------------------------------------
# 5. Stack starten und auf Web-Health warten
# ---------------------------------------------------------------------------
start_stack() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf 'Render-Free simulation=%s\n' "${RENDER_FREE}"
    printf 'docker compose --env-file %s up --build -d\n' "${ENV_LOCAL}"
    return 0
  fi
  [[ "${DO_UP}" -eq 1 ]] || { info "--no-up gesetzt - Stack wird nicht gestartet."; return 0; }

  # Compose liest .env automatisch; .env.local muss explizit eingebunden werden.
  info "Lade ${ENV_LOCAL} und starte 'docker compose up --build -d'..."
  set -a
  # shellcheck source=/dev/null
  . "./${ENV_LOCAL}"
  set +a
  docker compose --env-file "${ENV_LOCAL}" up --build -d

  info "Warte bis zu 90 Sekunden auf Web-Health..."
  local healthy=0
  local attempt
  for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18; do
    if docker inspect --format '{{.State.Health.Status}}' t-bot-local-web-1 2>/dev/null | grep -q '^healthy$'; then
      healthy=1; break
    fi
    sleep 5
  done
  # attempt nur fuer die Schleife benoetigt; Shellcheck-Zufriedenheit:
  : "${attempt:-1}"

  docker compose ps || true

  if [ "${healthy}" -ne 1 ]; then
    error "Web-Container wurde nicht healthy. Starte Diagnose..."
    scripts/diagnose_local.sh || true
    exit 1
  fi

  # Wenn der Container healthy ist, pruefen wir nochmal explizit vom Host aus.
  # Das unterscheidet "Container laeuft" von "Host kann erreichen".
  local host_http="000"
  local port="${WEB_PORT:-8369}"
  if command -v curl >/dev/null 2>&1; then
    host_http="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/health/" 2>/dev/null || true)"
    host_http="${host_http:-000}"
  elif command -v wget >/dev/null 2>&1; then
    if wget -q -T 5 -O /dev/null "http://127.0.0.1:${port}/health/" 2>/dev/null; then host_http="200"; fi
  fi

  case "${host_http}" in
    200|3*)
      info "Web ist healthy und vom Host erreichbar (HTTP ${host_http})."
      info "App: http://localhost:${port}/"
      info "Hinweis: Die Startseite liefert lokal standardmäßig 302 auf /login/; bei aktiviertem Gate zuerst auf /gate/."
      ;;
    *)
      error "Web-Container ist healthy, aber vom Host aus nicht erreichbar (HTTP ${host_http})."
      error "Das ist meistens ein Docker-Desktop-/WSL2-/Firewall-/Proxy-Problem, KEIN App-Fehler."
      error ""
      error "Detaillierte Diagnose:"
      scripts/diagnose_local.sh || true
      exit 1
      ;;
  esac
}

main() {
  parse_args "$@"
  # --no-up erzeugt nur .env.local und benoetigt kein laufendes Docker.
  if [[ "${DO_UP}" -eq 1 ]]; then
    check_docker
  else
    info "Docker-Check uebersprungen (--no-up)."
  fi
  run_hardware_test
  write_env_local
  reset_db_if_needed
  start_stack
}

# Beim Sourcen nur die testbaren Setup-Funktionen laden.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
