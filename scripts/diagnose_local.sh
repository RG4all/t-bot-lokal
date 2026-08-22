#!/usr/bin/env bash
# ============================================================================
# scripts/diagnose_local.sh - Forensische Diagnose des lokalen Docker-Stacks
#
# Prueft systematisch alle Gruende, warum http://localhost:8000/ nicht
# erreichbar sein koennte, obwohl die Container "healthy" sind:
#   1. Docker/Compose verfuegbar?
#   2. Laufen die Container? Status?
#   3. Ist Port 8000 auf dem Host veroeffentlicht?
#   4. Lauscht Daphne innerhalb des Containers auf 0.0.0.0:8000?
#   5. Antwortet der /health/-Endpoint innerhalb des Containers?
#   6. Firewall/Proxy-Hinweise (HTTP_PROXY, NO_PROXY, ufw/firewalld)
#   7. WSL2/VM-Hinweise (localhost-Forwarding)
#   8. Doppelten .env/.env.local (unterschiedliche WEB_PORT)
#   9. Browser-Cache/HSTS/HTTPS-Weiterleitung
#
# Exit 0 = alles OK (oder nur Warnungen), Exit 1 = Problem(e) gefunden.
# Nutzung: scripts/diagnose_local.sh
# ============================================================================

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

if [ -t 1 ]; then
  C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BOLD=""; C_DIM=""; C_RESET=""
fi

PROBLEMS=0
WARNINGS=0

ok()    { printf '  %s[ OK ]%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
warn()  { printf '  %s[WARN]%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*"; WARNINGS=$((WARNINGS+1)); }
fail()  { printf '  %s[FAIL]%s %s\n' "${C_RED}" "${C_RESET}" "$*"; PROBLEMS=$((PROBLEMS+1)); }
info()  { printf '  %s[ .. ]%s %s\n' "${C_DIM}"  "${C_RESET}" "$*"; }
section() { printf '\n%s==> %s%s\n' "${C_BOLD}" "$*" "${C_RESET}"; }

have() { command -v "$1" >/dev/null 2>&1; }

# WEB_PORT aus .env/.env.local extrahieren
detect_web_port() {
  local p=""
  if [ -f .env.local ]; then
    p="$(grep -E '^WEB_PORT=' .env.local 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
  fi
  if [ -z "${p}" ] && [ -f .env ]; then
    p="$(grep -E '^WEB_PORT=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
  fi
  echo "${p:-8000}"
}

# ---------------------------------------------------------------------------
section "1. Docker / Compose"
if ! have docker; then
  fail "docker nicht im PATH."
else
  ok "docker: $(docker --version 2>&1)"
fi
if docker compose version >/dev/null 2>&1; then
  ok "compose v2: $(docker compose version --short 2>&1)"
else
  fail "docker compose v2 nicht verfuegbar."
fi

if docker info >/dev/null 2>&1; then
  ok "Docker-Daemon laeuft."
else
  fail "Docker-Daemon ist nicht erreichbar (kein 'docker info'). Docker Desktop/Engine starten."
fi

# ---------------------------------------------------------------------------
section "2. Container-Status"
WEB_PORT="$(detect_web_port)"
info "Erwarteter Host-Port: ${WEB_PORT}"

if docker compose ps >/dev/null 2>&1; then
  docker compose ps --format 'table {{.Name}}\t{{.Service}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | sed 's/^/  /'
  if docker compose ps web 2>/dev/null | grep -q "healthy"; then
    ok "web-Container ist healthy."
  else
    fail "web-Container ist NICHT healthy. Status:"
    docker compose ps web 2>/dev/null | sed 's/^/    /'
  fi
else
  warn "docker compose ps konnte nicht ausgefuehrt werden."
fi

# ---------------------------------------------------------------------------
section "3. Port-Publishing auf dem Host"
# docker port pruefen
WEB_CONTAINER="$(docker compose ps -q web 2>/dev/null | head -1)"
if [ -n "${WEB_CONTAINER}" ]; then
  PORT_LINE="$(docker port "${WEB_CONTAINER}" 2>/dev/null || true)"
  if [ -z "${PORT_LINE}" ]; then
    fail "Der web-Container veroeffentlicht KEINE Ports auf dem Host."
    fail "  docker-compose.yml muss 'ports: [\"${WEB_PORT}:8000\"]' enthalten."
  else
    printf '  %s\n' "${PORT_LINE}"
    if echo "${PORT_LINE}" | grep -q "0.0.0.0:${WEB_PORT}\|:::${WEB_PORT}"; then
      ok "Port ${WEB_PORT} ist auf 0.0.0.0 veroeffentlicht."
    else
      warn "Port ${WEB_PORT} nicht auf 0.0.0.0 veroeffentlicht."
    fi
  fi
else
  fail "web-Container laeuft nicht; Port-Pruefung uebersprungen."
fi

# Lauscht auf dem Host?
if have ss; then
  if ss -ltn 2>/dev/null | grep -q ":${WEB_PORT} "; then
    ok "Auf dem Host lauscht etwas auf TCP :${WEB_PORT}."
  else
    if have docker && docker info >/dev/null 2>&1; then
      warn "Kein Host-Prozess lauscht auf TCP :${WEB_PORT}. "
      warn "  Das kann bedeuten, dass Docker Desktop/Engine den Port nicht"
      warn "  auf den Host forwardet (WSL2/VM/Remote-Kontext)."
    fi
  fi
elif have netstat; then
  if netstat -ltn 2>/dev/null | grep -q ":${WEB_PORT} "; then
    ok "Host lauscht auf TCP :${WEB_PORT}."
  fi
fi

# ---------------------------------------------------------------------------
section "4. Daphne innerhalb des Containers"
if [ -n "${WEB_CONTAINER}" ]; then
  # Lauscht Daphne innerhalb des Containers auf 0.0.0.0:8000?
  LISTEN_INSIDE="$(docker exec "${WEB_CONTAINER}" sh -c 'ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null || cat /proc/net/tcp 2>/dev/null' || true)"
  if echo "${LISTEN_INSIDE}" | grep -qE ":1F40|0.0.0.0:8000|:::8000"; then
    # :1F40 = 8000 hex
    ok "Daphne lauscht INNERHALB des Containers auf 0.0.0.0:8000."
  else
    fail "Innerhalb des Containers lauscht nichts auf Port 8000."
    fail "  Letzte Logs:"
    docker logs --tail 20 "${WEB_CONTAINER}" 2>&1 | sed 's/^/    /'
  fi

  # /health/ innerhalb des Containers aufrufen
  HEALTH="$(docker exec "${WEB_CONTAINER}" python -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=3) as r:
        print(r.status)
except Exception as e:
    print('ERR:', e)
" 2>&1 || true)"
  if echo "${HEALTH}" | grep -q "^200$"; then
    ok "GET /health/ im Container liefert 200."
  else
    fail "GET /health/ im Container liefert: ${HEALTH}"
  fi
else
  warn "web-Container nicht gefunden; Container-Interne Pruefung uebersprungen."
fi

# ---------------------------------------------------------------------------
section "5. Host -> Container (localhost)"
HTTP_CODE=""
if have curl; then
  # curl schreibt bei Fehler bereits "000" via -w; kein weiterer Fallback noetig.
  HTTP_CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WEB_PORT}/health/" 2>/dev/null || true)"
  HTTP_CODE="${HTTP_CODE:-000}"
elif have wget; then
  if wget -q -T 5 -O /dev/null "http://127.0.0.1:${WEB_PORT}/health/" 2>/dev/null; then
    HTTP_CODE="200"
  else
    HTTP_CODE="000"
  fi
else
  HTTP_CODE="000"
  warn "Weder curl noch wget verfuegbar - Erreichbarkeit nicht testbar."
fi
case "${HTTP_CODE}" in
  200)
    ok "GET http://127.0.0.1:${WEB_PORT}/health/ -> 200 (erreichbar!)"
    ;;
  000|"")
    fail "Kann http://127.0.0.1:${WEB_PORT}/health/ NICHT erreichen (Connection refused/timeout)."
    ;;
  3*)
    ok "GET http://127.0.0.1:${WEB_PORT}/health/ -> ${HTTP_CODE} (Redirect - das ist OK, Dienst laeuft)."
    ;;
  *)
    warn "GET http://127.0.0.1:${WEB_PORT}/health/ -> HTTP ${HTTP_CODE}."
    ;;
esac

# Root-Pfad (sollte 302 zu /gate/ oder /login/ liefern)
ROOT_CODE=""
if have curl; then
  ROOT_CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null || true)"
fi
echo "  GET http://127.0.0.1:${WEB_PORT}/ -> ${ROOT_CODE:-000} (302 zu /gate/ oder /login/ ist normal)"

# ---------------------------------------------------------------------------
section "6. Umgebungsvariablen / Proxy / WSL"
if [ -n "${HTTP_PROXY:-}${HTTPS_PROXY:-${http_proxy:-}${https_proxy:-}}" ]; then
  warn "HTTP_PROXY/HTTPS_PROXY ist gesetzt. localhost muss in NO_PROXY stehen."
  echo "    NO_PROXY=${NO_PROXY:-<nicht gesetzt>}"
  case ",${NO_PROXY:-}," in
    *",localhost,"*|*",127.0.0.1,"*) ok "localhost/127.0.0.1 in NO_PROXY." ;;
    *) fail "localhost/127.0.0.1 FEHLT in NO_PROXY. Browser/curl versucht sonst, den Proxy zu kontaktieren." ;;
  esac
else
  ok "Kein HTTP-Proxy gesetzt."
fi

# WSL2-Hinweis
if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
  warn "WSL2 erkannt. Mit dem Docker-Desktop-WSL2-Backend wird localhost"
  warn "  normalerweise auf Windows weitergereicht. Klappt das nicht:"
  warn "    - IP des WSL-Adapters pruefen:  ip addr show eth0"
  warn "    - Oder in Docker Desktop: Settings -> Resources -> WSL Integration aktivieren."
  warn "    - Windows-Check:  curl http://localhost:${WEB_PORT}/health/"
fi

# Firewall
if have ufw && command -v ufw >/dev/null 2>&1; then
  if ufw status 2>/dev/null | grep -qi "Status: active"; then
    warn "ufw ist aktiv. Sicherstellen, dass TCP ${WEB_PORT} eingehend erlaubt ist:"
    warn "    sudo ufw allow ${WEB_PORT}/tcp"
  fi
fi
if have firewall-cmd && firewall-cmd --state 2>/dev/null | grep -q running; then
  warn "firewalld laeuft. Fuer lokalen Zugriff:"
  warn "    sudo firewall-cmd --add-port=${WEB_PORT}/tcp --permanent && sudo firewall-cmd --reload"
fi

# Docker context
if have docker; then
  CTX="$(docker context show 2>/dev/null || true)"
  if [ -n "${CTX}" ] && [ "${CTX}" != "default" ]; then
    warn "Aktiver Docker-Kontext: '${CTX}'. Nicht-default-Kontexte koennen"
    warn "  Port-Forwarding auf diesen Rechner deaktivieren (z.B. Remote-Docker)."
  fi
fi

# ---------------------------------------------------------------------------
section "7. Doppelte Konfigurationsdateien / Port-Konflikte"
if [ -f .env ] && [ -f .env.local ]; then
  PORT_ENV="$(grep -E '^WEB_PORT=' .env 2>/dev/null | head -1 | cut -d= -f2-)"
  PORT_LOCAL="$(grep -E '^WEB_PORT=' .env.local 2>/dev/null | head -1 | cut -d= -f2-)"
  if [ -n "${PORT_ENV}" ] && [ -n "${PORT_LOCAL}" ] && [ "${PORT_ENV}" != "${PORT_LOCAL}" ]; then
    warn ".env und .env.local definieren unterschiedliche WEB_PORTs:"
    warn "  .env      = ${PORT_ENV}"
    warn "  .env.local = ${PORT_LOCAL}"
    warn "  scripts/setup_local.sh nutzt --env-file .env.local (Port ${PORT_LOCAL})."
  else
    ok ".env und .env.local verwenden denselben WEB_PORT (${PORT_ENV:-8000})."
  fi
else
  ok "Nur eine Konfigurationsdatei vorhanden (.env oder .env.local)."
fi

# Ggf. anderer Webserver auf dem Port?
if have ss; then
  OTHER="$(ss -ltnp 2>/dev/null | grep ":${WEB_PORT} " | grep -v docker || true)"
  if [ -n "${OTHER}" ]; then
    warn "Ein Nicht-Docker-Prozess belegt Port ${WEB_PORT}:"
    printf '    %s\n' "${OTHER}"
  fi
fi

# ---------------------------------------------------------------------------
section "8. Browser-/Client-Hinweise"
cat <<EOF
  Beim Aufruf im Browser beachten:
    - URL: http://localhost:${WEB_PORT}/   (http, NICHT https)
    - Die Startseite liefert 302 auf /gate/ (Passphrase-Abfrage) oder
      /login/ (Anmeldung) - das ist normal und KEIN Fehler.
    - Bei HSTS/fehlerhaften HTTPS-Weiterleitungen:
        * Inkognito-Fenster nutzen,
        * http://127.0.0.1:${WEB_PORT}/ statt localhost,
        * Browser-Cache fuer localhost loeschen.
    - Wenn der Browser "connection reset" zeigt, im Terminal mit curl testen:
        curl -i http://127.0.0.1:${WEB_PORT}/health/
EOF

# ---------------------------------------------------------------------------
section "Ergebnis"
if [ "${PROBLEMS}" -gt 0 ]; then
  printf '%s%i Problem(e) gefunden.%s Bitte die [FAIL]-Zeilen oben beheben.\n' "${C_RED}" "${PROBLEMS}" "${C_RESET}"
  exit 1
elif [ "${WARNINGS}" -gt 0 ]; then
  printf '%s%i Warnung(en).%s Keine harten Fehler; Dienst sollte erreichbar sein.\n' "${C_YELLOW}" "${WARNINGS}" "${C_RESET}"
  exit 0
else
  printf '%sAlle Pruefungen erfolgreich.%s Der Dienst sollte unter http://localhost:%s/ erreichbar sein.\n' "${C_GREEN}" "${C_RESET}" "${WEB_PORT}"
  exit 0
fi
