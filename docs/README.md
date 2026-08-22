# t-bot

Django-/Channels-Anwendung für **Paper Trading**, Marktvisualisierung und parametrisierte Backtests. Der Bot simuliert Orders; er sendet keine echten Kauf- oder Verkaufsaufträge an eine Börse.

Binance-Kurse laufen über einen persistenten kombinierten WebSocket-Stream (kein REST-Polling/Request-Weight). BitMart Spot nutzt die aktuelle V3-Public-API; Bitunix Spot/Futures ist über öffentliche, defensiv gedrosselte Adapter integriert. Beim Speichern und Aktivieren werden alle Symbole live geprüft. Das Dashboard bietet paginierte Logs, PDF/HTML/CSV-Reports und einen doppelt bestätigten Kill-Switch.

Ausführliche Bedienung, Indikatorformeln und Betriebsanweisungen stehen in [`MANUAL.md`](MANUAL.md) und werden in der App unter `/help/` angezeigt. Die lokale Docker-Umgebung ist in [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) dokumentiert; das Review steht in [`LOCAL_SETUP_PEER_REVIEW.md`](LOCAL_SETUP_PEER_REVIEW.md). Die Backtesting-Machbarkeitsstudie mit Architekturdiagramm und Lastmessung steht in [`BACKTESTING_STUDY.md`](BACKTESTING_STUDY.md); [`render.worker.example.yaml`](render.worker.example.yaml) ist die optionale Worker-Vorlage. Versionshistorie: [`CHANGELOG.md`](CHANGELOG.md). Aktuelle Version: [`VERSION`](VERSION).

## Docker Compose (empfohlen)

```bash
# Erkennt lokale Hardware, schreibt .env.local und startet den Stack:
scripts/setup_local.sh

# Auf frischem Linux zusätzlich Distribution/Paketmanager automatisch behandeln:
scripts/setup_local.sh --install-deps
```

Beim `--build` laeuft automatisch `install.sh` (Container-Modus), das die
zur Basis-Distribution passenden Laufzeit-Pakete installiert. Vor dem Start
von Redis, PostgreSQL und der App fuehrt der One-Shot-`tuner`-Service
`hardware-test.sh` aus und schreibt eine optimierte `tuning.env` in ein
gemeinsam genutztes Volume. Redis startet daraufhin mit automatisch
berechneten Werten fuer `maxmemory`, `maxmemory-policy` und `io-threads`;
Web/Worker/Beat uebernehmen die empfohlenen Werte fuer Worker-Threads,
Connection-Pools und Speicher-Limits. Danach ist die App unter
<http://localhost:8000/> erreichbar. Web, Worker, Redis und PostgreSQL
laufen als getrennte, ressourcenbegrenzte Services.
Danach: <http://localhost:8000/>. Web, Worker, Redis und PostgreSQL laufen als getrennte, automatisch dimensionierte Services. Render-Free-Simulation ist standardmäßig deaktiviert.

## Lokal ohne Docker starten

Das universelle Installationsskript erkennt die Linux-Distribution
(Debian/Ubuntu via apt, Arch via pacman, RHEL/CentOS/Fedora via dnf/yum),
installiert alle Systemabhaengigkeiten, richtet Redis als lokalen Service
ein und erzeugt eine lokale Konfigurationsdatei:

```bash
./install.sh                # automatische Erkennung Host/Container
# oder explizit:
./install.sh --mode=host --profile=full --yes
```

Anschliessend die erzeugte Konfiguration laden und die App starten:

```bash
set -a; . ./config/local.env; set +a
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py runserver
```

### Hardware-Optimierung

`hardware-test.sh` vermisst CPU, RAM und Disk-I/O und berechnet eine
empfohlene Konfiguration fuer Redis und den t-bot:

```bash
./hardware-test.sh                       # schreibt config/hardware.env + Bericht
./hardware-test.sh --format=json         # zusaetzlich maschinenlesbar
./hardware-test.sh --skip-disk-test      # schneller, ohne I/O-Benchmark
```

### Render.com-Simulation

Die Render.com-Simulation ist in der erzeugten `config/local.env`
standardmaessig **deaktiviert** (`RENDER_SIMULATION=False`), damit das
lokale Setup unbeeinflusst bleibt. Zum Aktivieren den Wert auf `True`
setzen und die Ressourcen-Limits (`*_CPUS`, ...) anpassen. Bei der
Installation kann sie direkt mit `./install.sh --render-simulation`
aktiviert werden.

### Manuelle Installation (alt)

Voraussetzungen: Python 3.12 und die nativen WeasyPrint-Bibliotheken (unter Debian insbesondere Pango/Harfbuzz).

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

## Qualitätssicherung

```bash
# Shell-Skripte linten und Test-Suite ausfuehren
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh tests/*.sh
./tests/run_tests.sh

# Distro-Smoke-Tests (benoetigt Docker, prueft apt/pacman/dnf)
./tests/distro_smoke_test.sh

# Django- / Python-Tests
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py collectstatic --noinput
pip-audit -r requirements.txt   # wenn pip-audit installiert ist
```

## Render-Deployment

`render.yaml` definiert einen Docker-Web-Service und PostgreSQL in Frankfurt. Der Container installiert die für PDF-Reports benötigten Systembibliotheken, wartet beim Start mit DNS-/Connection-Backoff auf PostgreSQL, führt Migrationen aus und startet Daphne auf `$PORT`.

1. Branch `arena/01a01bc6-t-bot` zu GitHub pushen.
2. In Render **New → Blueprint** wählen und dieses Repository verbinden.
3. Blueprint anwenden. `SECRET_KEY`, `PASSPHRASE` und `DATABASE_URL` werden automatisch erzeugt/verknüpft.
4. Die erzeugte `PASSPHRASE` unter **t-bot-web → Environment** anzeigen und sicher aufbewahren.
5. Optional über die Render Shell einen Admin anlegen:
   ```bash
   python manage.py createsuperuser
   ```
6. `/health/`, Passphrase-Gate, Registrierung/Login, Dashboard und WebSocket-Fortschritt prüfen.

### Einschränkungen des kostenlosen Render-Plans

- Der Web-Service schläft bei Inaktivität ein. Ein In-Process-Trading-Bot läuft daher **nicht garantiert 24/7**.
- Die kostenlose PostgreSQL-Datenbank läuft nach 30 Tagen ab und muss rechtzeitig ersetzt oder hochgestuft werden.
- Ohne `REDIS_URL` laufen Backtests in einem lokalen Hintergrund-Thread. Das funktioniert mit einer Instanz, überlebt aber keinen Prozessneustart.
- Geplante Backtests benötigen ohne Celery Beat einen externen Aufruf von:
  ```bash
  python manage.py run_scheduled_backtests
  ```
- Für dauerhaften Betrieb sind ein bezahlter Web-Service sowie Redis, Celery Worker und Celery Beat empfohlen.

## Sicherheit

- In Produktion sind `SECRET_KEY` und `PASSPHRASE` Pflichtvariablen.
- Zustandsändernde Endpunkte akzeptieren nur POST und sind CSRF-geschützt.
- Konfigurationen, Logs, Backtests, PDFs und WebSockets sind benutzerbezogen autorisiert.
- API-Schlüssel werden aktuell im Django-Datenbankfeld gespeichert. Für sensible echte Schlüssel sollte vor Nutzung zusätzlich Verschlüsselung auf Feldebene eingerichtet werden.
