# Lokale Entwicklungsumgebung

Aktuelle Version: [`VERSION`](../../VERSION) · [Security-Review und Upgrade](../security/SECURITY_REVIEW_2.4.4.md)

## 1. Voraussetzungen und automatische Installation

- mindestens 2 GB RAM
- freier TCP-Port 8369 (Web) und interne Docker-Netze
- Linux mit apt, pacman, dnf/yum, zypper oder apk; macOS/Windows verwenden Docker Desktop

Linux-Komplettsetup:

```bash
scripts/setup_local.sh --install-deps
```

Vorher gefahrlos prüfen:

```bash
scripts/install_system_dependencies.sh --dry-run
scripts/setup_local.sh --dry-run
```

`scripts/install_system_dependencies.sh` erkennt Distribution, Paketmanager und CPU-Architektur automatisch. Unterstützt werden Debian/Ubuntu, Arch/Manjaro, Fedora, RHEL/CentOS/Rocky/Alma, openSUSE und Alpine. Installiert werden Docker/Compose, Python-Werkzeuge sowie Pango/Harfbuzz/JPEG/PostgreSQL-Client für native Diagnose. Mit `--dry-run` werden nur die Befehle ausgegeben.

### Ohne Docker: Installation auf dem Host

`install.sh` erkennt die Linux-Distribution (apt, pacman, dnf/yum, zypper, apk), installiert Systemabhängigkeiten, richtet Redis als lokalen Service ein und erzeugt `config/local.env`:

```bash
./install.sh                # automatische Erkennung Host/Container
./install.sh --mode=host --profile=full --yes   # oder explizit
```

Anschließend die erzeugte Konfiguration laden und starten:

```bash
set -a; . ./config/local.env; set +a
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

### Ohne Docker und ohne Installer (manuell)

Voraussetzungen: Python ≥ 3.11 und die nativen WeasyPrint-Bibliotheken (unter Debian insbesondere Pango/Harfbuzz).

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Werte in .env setzen, dann in die Shell exportieren:
set -a; . ./.env; set +a
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

Django lädt `.env` nicht automatisch. Die Variablen müssen von der Shell, einem Prozessmanager oder einer IDE exportiert werden.

## 2. Automatische Hardwareoptimierung

```bash
python3 scripts/tune_local_hardware.py --output .env.local
```

Der Test misst CPU-Hashrate, sequenzielle Schreibrate, RAM, freien Datenträger und Architektur. Daraus entstehen CPU-/RAM-Limits für alle Compose-Services, Redis-Maxmemory, PostgreSQL-Cachewerte, das lokale DataLog-Schreibintervall und ein sinnvoller Standardwert von 2.500 oder 5.000 Backtest-Preispunkten. Bestehende Secrets in `.env.local` bleiben erhalten; neue Dateien erhalten Modus 0600.

Die Render-Free-Simulation ist standardmäßig **aus**. Nur explizit aktivieren:

```bash
scripts/setup_local.sh --render-free-simulation
```

Der Host-Modus von `hardware-test.sh` vermisst CPU, RAM und Disk-I/O direkt und schreibt `config/hardware.env` plus Bericht; `--format=json` liefert maschinenlesbare Werte, `--skip-disk-test` überspringt den I/O-Benchmark. Der Docker-`tuner`-Service ruft dasselbe Skript im Container auf.

## 3. Start

Ein-Schritt-Setup mit nativer Hardwareoptimierung:

```bash
scripts/setup_local.sh
```

Oder manuell:

```bash
cp .env.docker.example .env.local
# SECRET_KEY, PASSPHRASE und POSTGRES_PASSWORD privat setzen, dann:
chmod 600 .env.local
docker compose --env-file .env.local up --build -d
```

Beim Build laeuft `install.sh` im Container-Modus und installiert die zur
Basis-Image-Distribution passenden Laufzeit-Pakete. Vor Redis, PostgreSQL
und der App fuehrt der One-Shot-`tuner`-Service `hardware-test.sh` aus und
schreibt eine automatisch an die Host-Ressourcen angepasste `tuning.env` in
ein gemeinsam genutztes Volume. Redis startet daraufhin mit berechneten
Werten fuer `maxmemory`, `maxmemory-policy` und `io-threads`; Web, Worker
und Beat übernehmen die empfohlenen Werte fuer Worker-Threads,
Connection-Pools und Speicher-Limits.
`SECRET_KEY` und `PASSPHRASE` sind ab 2.4.4 auch im lokalen Compose Pflichtwerte, `POSTGRES_PASSWORD` ab 2.4.11: Ohne sie bricht die Compose-Interpolation ab. `scripts/setup_local.sh` erzeugt private App-Secrets und ein zufälliges lokales Datenbank-Passwort automatisch, erhält sie beim Retuning und respektiert ein bereits gesetztes `PASSPHRASE_GATE_ENABLED=True`. Auch `install.sh` verwendet keine bekannte Default-Passphrase mehr. `.env.local` ist durch `.gitignore` ausgeschlossen und darf nicht geteilt werden.

Der Gate ist ausschließlich im **lokalen DEBUG-Compose-Profil** standardmäßig aus. Für Teamzugriff `PASSPHRASE_GATE_ENABLED=True` setzen und private App-/DB-Secrets konfigurieren. PostgreSQL und Redis veröffentlichen keine Hostports. Dieses Profil niemals als öffentliche Produktionskonfiguration verwenden. Ein Postgres-Volume liest `POSTGRES_PASSWORD` nur beim ersten Initialisieren; danach erfordert eine Passwort-Änderung `scripts/setup_local.sh --reset-db` (löscht die lokale Datenbank, siehe [FAQ](FAQ.md#5-container-starten-werden-aber-nicht-healthy-restart-loop)).

Ohne Docker ist der temporäre Entwicklungsmodus weiterhin möglich: Bei `DEBUG=True` und ohne Render generieren die Settings eine Passphrase mit `secrets.token_urlsafe(32)` und geben sie als WARNING in der Startkonsole aus. Auch ein fehlender lokaler Signierschlüssel wird zufällig erzeugt, aber nicht ausgegeben. Jeder Settings-Ladevorgang kann neue Werte liefern; für stabile Sessions und mehrere Prozesse beide App-Secrets explizit setzen. In Produktion (`DEBUG=False` oder Render) gibt es weder temporäre Secrets noch einen abschaltbaren Gate. Details: [Passphrase-Matrix](#4-passphrase-und-startkonfiguration).

Gate-Freigaben werden nach einer Passphrase-Rotation automatisch ungültig. Für eine App-Secret-Rotation genügt das Neuerstellen der App-Container; dafür **keine DB-Volumes löschen**. Siehe [FAQ](FAQ.md#8-passwoerter-aendern--secret-rotation).

Services:

| Service | Aufgabe | Grenze |
|---|---|---|
| `tuner` | One-Shot: Hardware-Analyse und Tuning-Datei generieren | kurzlebig |
| `web` | Django, Daphne, WebSockets, TradingBot | hardwareabhängige CPU/RAM-Cgroup |
| `backtest-worker` | ausschließlich Queue `backtest` | eigene Cgroup, 384-MB-Celery-Child, Concurrency 1 |
| `redis` | Broker/Result Backend | hardwareabhängiges Maxmemory, keine lokale Persistenz |
| `postgres` | lokale persistente DB | max. 40 Verbindungen, abgestimmte Cachewerte |
| `scheduler` | optional Celery Beat | nur Profil `scheduler` |

Die automatisch berechneten Werte koennen eingesehen werden:

```bash
docker compose cp tuner:/tbot-runtime/hardware-report.txt - | less
docker compose exec web sh -c 'cat /tbot-runtime/tuning.env'
```

Status prüfen:

```bash
docker compose ps
docker compose logs -f web backtest-worker
curl http://localhost:8369/health/
docker compose exec backtest-worker celery -A trading_bot_project inspect ping --timeout 3
```

Admin anlegen:

```bash
docker compose exec web python manage.py createsuperuser
```

Anwendung: <http://localhost:8369/>

## 4. Passphrase und Startkonfiguration

Private Werte **getrennt** erzeugen und außerhalb von Git in der Env-Datei bzw. im Secret-Store speichern:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"  # SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"  # PASSPHRASE
```

| Umgebung | Verhalten bei fehlender/leerer `PASSPHRASE` | Gate deaktivierbar? |
|---|---|---|
| Lokal: `DEBUG=True`, `RENDER=False` bzw. nicht gesetzt | Temporärer Zufallswert mit 32 Zufallsbytes; WARNING mit Wert in der Startkonsole | Ja, nur explizit lokal |
| `DEBUG=False`, auch ohne Render | `RuntimeError`, kein Zufallswert und kein Secret-Log | Nein |
| Render (`RENDER=True`), auch mit `DEBUG=True` | `RuntimeError`, kein Zufallswert und kein Secret-Log | Nein |

Nur aus Leerraum bestehende Werte gelten als fehlend. Nichtleere explizite Passphrasen werden unverändert verglichen, einschließlich Unicode und Leerzeichen. Fehlende Signierschlüssel werden nur lokal zufällig erzeugt; Produktion benötigt einen expliziten `SECRET_KEY`.

Ohne gesetzte Secrets entstehen bei jedem Laden der Settings neue Werte: auch Management-Kommandos und der Entwicklungs-Autoreloader können jeweils andere Werte ausgeben. Für einen temporären Ein-Prozess-Test `python manage.py runserver --noreload` verwenden und nur den Wert dieses laufenden Servers eingeben. Für mehrere Worker und stabile Sessions **immer dieselben expliziten Secrets** verwenden. Lokale Startlogs mit der temporären Passphrase nicht teilen.

Das Setup speichert neue App-Secrets in `.env.local` statt sie zu loggen; bestehende Werte und ein eingeschalteter Gate bleiben beim Retuning erhalten. Manuelles Compose bricht bei leeren oder fehlenden App-Secrets sowie `POSTGRES_PASSWORD` ab. Das Basis-Docker-Image deaktiviert den Gate nicht mehr; die lokale Ausnahme steht ausschließlich im Compose-Profil.

## 5. Render-Free-CPU lokal simulieren

Bevorzugt direkt erzeugen:

```bash
scripts/setup_local.sh --render-free-simulation
```

Oder in `.env.local` manuell setzen:

```env
SIMULATE_RENDER_FREE=True
WEB_CPUS=0.10
WORKER_CPUS=0.50
```

Danach:

```bash
docker compose --env-file .env.local up -d --force-recreate web backtest-worker
```

Der Worker besitzt weiterhin eigene Ressourcen; der Web-/Bot-Prozess wird künstlich auf 0,1 CPU begrenzt.

## 6. Graceful Degradation testen

### Worker-Ausfall bei erreichbarem Redis

```bash
docker compose stop backtest-worker
```

`/backtesting/` muss `worker-unavailable` anzeigen und neue POSTs ablehnen. Solange Redis erreichbar ist, wird nicht lokal gestartet: Sonst könnte ein bereits eingereihtes Redis-Task später zusätzlich ausgeführt werden.

Worker wieder starten:

```bash
docker compose start backtest-worker
```

Der Status wechselt zu `celery-worker`.

### Redis-Ausfall

```bash
docker compose stop redis
```

Worker ebenfalls stoppen (`docker compose stop backtest-worker`), damit Redis keinen bereits angenommenen Task enthält. Ein neuer Backtest fällt dann lokal zurück. In Produktion (`BACKTEST_LOCAL_FALLBACK_ENABLED=False`) wird derselbe POST verständlich abgelehnt und erzeugt keinen Thread im Bot-Prozess.

### Worker-Crash

```bash
docker compose kill -s KILL backtest-worker
docker compose up -d backtest-worker
```

Daphne und TradingBot müssen durchgehend laufen. `acks_late` und `reject_on_worker_lost` sorgen bei Redis-Betrieb dafür, dass ein nicht bestätigter Task erneut zugestellt werden kann.

## 7. Ressourcen-Validation

```bash
python scripts/backtest_resource_probe.py
```

Akzeptanz:

- Exit-Code 0
- Peak-RSS < 384 MB
- 100 Kandidaten × 5.000 Preispunkte
- Eltern-Heartbeat ungefähr 20 ms und ohne große Ausreißer

Vollständige Studie: [`BACKTESTING_STUDY.md`](../adr/ADR-0001-backtesting-worker-isolation.md).

## 8. Optionaler Scheduler

```bash
docker compose --profile scheduler up -d scheduler
```

Celery Beat prüft jede Minute geplante Backtests. Für normale sofortige Backtests ist der Scheduler nicht nötig.

## 9. Debugging

```bash
# Strukturierte Backtest-Events
docker compose logs backtest-worker | grep 'event=backtest'

# Worker-Ressourcen
docker stats t-bot-local-backtest-worker-1 t-bot-local-web-1

# DB-Verbindungen
docker compose exec postgres psql -U tbot -d tbot -c \
  "select application_name, state, count(*) from pg_stat_activity group by 1,2 order by 3 desc;"
```

Der authentifizierte Endpoint `/api/backtesting/status/` liefert Worker-/Redis-Modus sowie Web-Peak-RSS, Threadzahl, Heartbeat-Alter und maximalen Scheduler-Lag.

## 10. Beenden und Zurücksetzen

```bash
docker compose down                 # Datenbank-Volume behalten
docker compose down -v              # lokale Daten vollständig löschen
```

## 11. Produktion auf Render

### Blueprint deployen

[`render.yaml`](../../render.yaml) definiert einen Docker-Web-Service und PostgreSQL in Frankfurt. Der Container installiert die für PDF-Reports benötigten Systembibliotheken, wartet beim Start mit DNS-/Connection-Backoff auf PostgreSQL, führt Migrationen aus und startet Daphne auf `$PORT`.

1. Den geprüften PR in den Integrationsbranch `tbot.local` übernehmen; beide Render-Vorlagen referenzieren diesen Branch.
2. In Render **New → Blueprint** wählen und dieses Repository verbinden.
3. Blueprint anwenden. `SECRET_KEY`, `PASSPHRASE` und `DATABASE_URL` werden automatisch erzeugt/verknüpft; `PASSPHRASE_GATE_ENABLED=True` ist explizit gesetzt.
4. Die erzeugte `PASSPHRASE` unter **t-bot-web → Environment** anzeigen und sicher aufbewahren.
5. Optional über die Render Shell einen Admin anlegen: `python manage.py createsuperuser`
6. `/health/`, Passphrase-Gate, Registrierung/Login, Dashboard und WebSocket-Fortschritt prüfen.
7. Bei einem Reverse-Proxy die tatsächlich vertrauenswürdigen Peer-IPs/CIDRs für `RATE_LIMIT_TRUSTED_PROXIES` ermitteln und im Service konfigurieren. Der Proxy muss `X-Forwarded-For` bereinigen oder die tatsächliche Peer-IP anhängen. Ohne Allowlist wird der Header ignoriert; hinter einem Proxy teilen sich sonst dessen Clients ein Rate-Limit. Keine pauschalen Netze wie `0.0.0.0/0` verwenden.
8. Für zusätzliche Web-Prozesse/Instanzen ein gemeinsames Auth-Rate-Limit am Edge/Proxy bzw. in einem geteilten Backend vorsehen; das App-Limit gilt pro Prozess.

### Einschränkungen des kostenlosen Render-Plans

- Der Web-Service schläft bei Inaktivität ein. Ein In-Process-Trading-Bot läuft daher **nicht garantiert 24/7**.
- Die kostenlose PostgreSQL-Datenbank läuft nach 30 Tagen ab und muss rechtzeitig ersetzt oder hochgestuft werden.
- Ohne `REDIS_URL` laufen Backtests in einem lokalen Hintergrund-Thread. Das funktioniert mit einer Instanz, überlebt aber keinen Prozessneustart.
- Geplante Backtests benötigen ohne Celery Beat einen externen Aufruf von `python manage.py run_scheduled_backtests`.
- Für dauerhaften Betrieb sind ein bezahlter Web-Service sowie Redis, Celery Worker und Celery Beat empfohlen.

### Produktives Backtesting auf Render

Render Free besitzt keinen isolierten Background-Worker. Deshalb bleibt `BACKTEST_LOCAL_FALLBACK_ENABLED=False` in Produktion. Für produktives Backtesting:

1. Redis/Render Key Value bereitstellen.
2. Web und Worker dieselbe `REDIS_URL` geben.
3. Bezahlten Worker anhand [`render.worker.example.yaml`](../../render.worker.example.yaml) erstellen.
4. Worker-Queue `backtest`, Concurrency 1 und Memory-Child-Limit prüfen.
5. Optional Beat als separaten Service erstellen.
6. Erst kleinen Test ausführen und `/api/backtesting/status/` beobachten.

## 12. Qualitätssicherung

Die folgenden Kommandos dienen der lokalen Nachprüfung vor jedem Release; Docker-Smoke-Tests, `mypy` und `pip-audit` sind zusätzliche, umgebungsabhängige Prüfungen. Release-Prüfnachweise stehen im [CHANGELOG](../CHANGELOG.md).

```bash
# Shell-Skripte linten und Test-Suite ausfuehren
# (ShellCheck 0.11.0, z. B. pip install shellcheck-py==0.11.0.1 – Distro-apt
# liefert je nach System 0.9.0 und meldet abweichende Befunde)
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
./tests/run_tests.sh

# Distro-Smoke-Tests (benoetigt Docker, prueft apt/pacman/dnf)
./tests/distro_smoke_test.sh

# Django-/Python-Tests (lokale Testumgebung, kein Bot-Autostart)
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput

# Dokumentations-Waechter (Links, Anker, Orphans, Konventionen)
python3 scripts/check_docs.py

# Statische Typprüfung der Views (Entwicklungswerkzeuge, nicht in requirements.txt):
pip install mypy==1.18.2 django-stubs==5.2.7 && mypy

# Abhängigkeiten und Statische Analyse
python -m pip check
pip-audit -r requirements.txt   # wenn pip-audit installiert ist
python scripts/backtest_resource_probe.py
python manage.py collectstatic --noinput
```
