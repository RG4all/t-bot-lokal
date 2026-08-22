# Lokale Entwicklungsumgebung

## 1. Voraussetzungen

- Docker Engine mit Compose v2
- mindestens 2 GB freier RAM
- freie TCP-Ports 8000 (Web) und interne Docker-Netze

## 2. Konfiguration

```bash
cp .env.docker.example .env
```

Mindestens `SECRET_KEY`, `PASSPHRASE` und `POSTGRES_PASSWORD` in `.env` ändern. Die Datei `.env` ist durch `.gitignore` ausgeschlossen.

## 3. Start

```bash
docker compose up --build -d
```

Beim Build laeuft `install.sh` im Container-Modus und installiert die zur
Basis-Image-Distribution passenden Laufzeit-Pakete. Vor Redis, PostgreSQL
und der App fuehrt der One-Shot-`tuner`-Service `hardware-test.sh` aus und
schreibt eine automatisch an die Host-Ressourcen angepasste `tuning.env` in
ein gemeinsam genutztes Volume. Redis startet daraufhin mit berechneten
Werten fuer `maxmemory`, `maxmemory-policy` und `io-threads`; Web, Worker
und Beat uebernehmen die empfohlenen Werte fuer Worker-Threads,
Connection-Pools und Speicher-Limits.

Services:

| Service | Aufgabe | Grenze |
|---|---|---|
| `tuner` | One-Shot: Hardware-Analyse und Tuning-Datei generieren | kurzlebig |
| `web` | Django, Daphne, WebSockets, TradingBot | 512 MB, standardmaeßig 0,5 CPU |
| `backtest-worker` | ausschließlich Queue `backtest` | 512-MB-Container, 384-MB-Celery-Child, Concurrency 1 |
| `redis` | Broker/Result Backend | 96 MB (Default), hardwareabhaengiges Maxmemory |
| `postgres` | lokale persistente DB | 256 MB, max. 40 Verbindungen, abgestimmte Cachewerte |
| `scheduler` | optional Celery Beat | nur Profil `scheduler` |

Die automatisch berechneten Werte koennen eingesehen werden:

```bash
docker compose cp tuner:/tbot-runtime/hardware-report.txt - | less
docker compose exec web sh -c 'cat /tbot-runtime/tuning.env'
```

Alternativ das Ein-Schritt-Setup verwenden:

```bash
scripts/setup_local.sh             # .env.local erzeugen + Stack starten
scripts/setup_local.sh --no-up     # nur .env.local erzeugen
```

Status prüfen:

```bash
docker compose ps
docker compose logs -f web backtest-worker
curl http://localhost:8000/health/
docker compose exec backtest-worker celery -A trading_bot_project inspect ping --timeout 3
```

Admin anlegen:

```bash
docker compose exec web python manage.py createsuperuser
```

Anwendung: <http://localhost:8000/>

## 4. Render-Free-CPU lokal simulieren

In `.env`:

```env
WEB_CPUS=0.10
WORKER_CPUS=0.50
```

Danach:

```bash
docker compose up -d --force-recreate web backtest-worker
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

## 9. Troubleshooting

### Web/Worker bleibt `health: starting` oder ist nicht erreichbar

Wenn `docker compose up` erfolgreich laeuft, aber `http://localhost:8000/`
nicht antwortet und der Web-Container immer wieder neu startet, ist fast
immer das **Postgres-Passwort nicht konsistent** mit dem bereits
initialisierten Volume.

Ursache: Das offizielle Postgres-Image liest `POSTGRES_PASSWORD` nur beim
**ersten** Initialisieren eines leeren Datenverzeichnisses. Ein frueherer
Lauf mit `.env` (Default-Passwort `tbot-local-password`), mit `scripts/setup_local.sh`
(frueher zufaellig generiertes Passwort) oder mit einer aelteren Version
hat das Volume `postgres_data` bereits mit einem anderen Passwort
angelegt. Neue Werte in `.env`/`.env.local` aendern das Passwort im
bestehenden Volume **nicht**. Migrationen und `wait_for_database`
schlagen dann fehl; Web/Worker crashen in einer Restart-Schleife.

Diagnose:

```bash
docker compose logs --tail 80 web
# Suche nach:
#   password authentication failed for user "tbot"
#   FATAL: password authentication failed
```

Abhilfe (lokale Datenbank zuruecksetzen):

```bash
# Ueber das Setup-Skript (bestaetigt oder mit --yes):
scripts/setup_local.sh --reset-db --yes

# Oder manuell:
docker compose down -v
docker compose up --build -d
```

Das `-v` loescht das benannte Volume `t-bot-local_postgres_data`; beim
naechsten Start initialisiert Postgres mit dem aktuellen Passwort.

### Web-Healthcheck startet neu durch

Der Healthcheck trifft `/health/`. Wenn Daphne nach Migrationen 30-60
Sekunden braucht, bleibt der Status zunaechst `health: starting`. Das ist
normal; `start_period` ist auf 30 s (Web) bzw. 45 s (Worker) eingestellt.
Nach Ablauf sollte der Status zu `healthy` wechseln. Wenn nicht:

```bash
docker compose logs -f web
curl -i http://localhost:8000/health/
```

### Worker-Healthcheck schlaegt fehl

Der Worker-Healthcheck nutzt `celery -A trading_bot_project inspect ping`.
Voraussetzung ist, dass der Broker (Redis) erreichbar ist und der Worker
innerhalb von `start_period` (45 s) hochgefahren ist. Wenn der Worker
nicht startet, weil die DB nicht erreichbar ist, zuerst das Web-Problem
oben loesen.

### Ports bereits belegt

Wenn Port 8000 bereits belegt ist, mit `WEB_PORT=8001` in `.env` starten:

```bash
WEB_PORT=8001 docker compose up -d
```

## 10. Beenden und Zurücksetzen

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
