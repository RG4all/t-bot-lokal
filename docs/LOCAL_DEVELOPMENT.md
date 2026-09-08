# Lokale Entwicklungsumgebung

**Konfigurationsstand: 2.4.8** · [Security-Review und Upgrade](SECURITY_REVIEW_2.4.4.md)

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

## 2. Automatische Hardwareoptimierung

```bash
python3 scripts/tune_local_hardware.py --output .env.local
```

Der Test misst CPU-Hashrate, sequenzielle Schreibrate, RAM, freien Datenträger und Architektur. Daraus entstehen CPU-/RAM-Limits für alle Compose-Services, Redis-Maxmemory, PostgreSQL-Cachewerte, das lokale DataLog-Schreibintervall und ein sinnvoller Standardwert von 2.500 oder 5.000 Backtest-Preispunkten. Bestehende Secrets in `.env.local` bleiben erhalten; neue Dateien erhalten Modus 0600.

Die Render-Free-Simulation ist standardmäßig **aus**. Nur explizit aktivieren:

```bash
scripts/setup_local.sh --render-free-simulation
```

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
`SECRET_KEY` und `PASSPHRASE` sind ab 2.4.4 auch im lokalen Compose Pflichtwerte: Ohne sie bricht die Compose-Interpolation ab. `scripts/setup_local.sh` erzeugt private App-Secrets automatisch, erhält sie beim Retuning und respektiert ein bereits gesetztes `PASSPHRASE_GATE_ENABLED=True`. Auch `install.sh` verwendet keine bekannte Default-Passphrase mehr. `.env.local` ist durch `.gitignore` ausgeschlossen und darf nicht geteilt werden.

Der Gate ist ausschließlich im **lokalen DEBUG-Compose-Profil** standardmäßig aus. Für Teamzugriff `PASSPHRASE_GATE_ENABLED=True` setzen und private App-/DB-Secrets konfigurieren. Der bisherige lokale PostgreSQL-Default bleibt aus Kompatibilität mit bestehenden Volumes erhalten; PostgreSQL und Redis veröffentlichen keine Hostports. Dieses Profil niemals als öffentliche Produktionskonfiguration verwenden.

Ohne Docker ist der temporäre Entwicklungsmodus weiterhin möglich: Bei `DEBUG=True` und ohne Render generieren die Settings eine Passphrase mit `secrets.token_urlsafe(32)` und geben sie als WARNING in der Startkonsole aus. Auch ein fehlender lokaler Signierschlüssel wird zufällig erzeugt, aber nicht ausgegeben. Jeder Settings-Ladevorgang kann neue Werte liefern; für stabile Sessions und mehrere Prozesse beide App-Secrets explizit setzen. In Produktion (`DEBUG=False` oder Render) gibt es weder temporäre Secrets noch einen abschaltbaren Gate. Details: [Startmatrix](README.md#passphrase-und-startkonfiguration).

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

## 4. Render-Free-CPU lokal simulieren

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

## 5. Graceful Degradation testen

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

## 6. Ressourcen-Validation

```bash
python scripts/backtest_resource_probe.py
```

Akzeptanz:

- Exit-Code 0
- Peak-RSS < 384 MB
- 100 Kandidaten × 5.000 Preispunkte
- Eltern-Heartbeat ungefähr 20 ms und ohne große Ausreißer

Vollständige Studie: [`BACKTESTING_STUDY.md`](BACKTESTING_STUDY.md).

## 7. Optionaler Scheduler

```bash
docker compose --profile scheduler up -d scheduler
```

Celery Beat prüft jede Minute geplante Backtests. Für normale sofortige Backtests ist der Scheduler nicht nötig.

## 8. Debugging

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

## 9. Beenden und Zurücksetzen

```bash
docker compose down                 # Datenbank-Volume behalten
docker compose down -v              # lokale Daten vollständig löschen
```

## 10. Produktion auf Render

Render Free besitzt keinen isolierten Background-Worker. Deshalb bleibt `BACKTEST_LOCAL_FALLBACK_ENABLED=False` in Produktion. Für produktives Backtesting:

1. Redis/Render Key Value bereitstellen.
2. Web und Worker dieselbe `REDIS_URL` geben.
3. Bezahlten Worker anhand `render.worker.example.yaml` erstellen.
4. Worker-Queue `backtest`, Concurrency 1 und Memory-Child-Limit prüfen.
5. Optional Beat als separaten Service erstellen.
6. Erst kleinen Test ausführen und `/api/backtesting/status/` beobachten.
