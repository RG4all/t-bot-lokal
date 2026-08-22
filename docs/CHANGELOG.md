# Changelog

Alle relevanten Änderungen dieses Projekts werden hier dokumentiert. Das Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [2.3.1] – 2026-08-22

### Indikator-Konsistenz, Tooltip-System und Behebung des /help/ 500-Fehlers

- **Fehlerbehebung `/help/` (500 Server Error):** `_render_manual` prüft nun robust Pfade (`docs/MANUAL.md` und `MANUAL.md`) mit Fallback. Das gerenderte HTML wird mittels `@lru_cache(maxsize=1)` im Speicher gehalten, sodass keine wiederkehrende CPU- oder I/O-Last entsteht.
- **Indikatoren-Mapping & einheitliche Benennung:**
  - `div_DVA_prev_NDA_threshold_buy` ist in Konfiguration und Dashboard jetzt präzise als **„Beschleunigung (DVA / prev NDA) – Kaufschwelle“** benannt und referenziert direkt die Parameter `Beschleunigung – von / bis / Schritt` (`acc_from`, `acc_to`, `acc_steps`) im Backtesting-Modul.
  - `deltadelta_threshold_buy` ist einheitlich als **„DeltaDelta (geglättetes Momentum) – Kaufschwelle“** benannt (Backtesting: `deltadelta_from`, `deltadelta_to`, `deltadelta_steps`).
  - `nda_threshold_buy` ist einheitlich als **„NDA (normalisierte Preisänderung) – Kaufschwelle“** benannt (Backtesting: `nda_from`, `nda_to`, `nda_steps`).
- **Interaktive Info- und Hover-Elemente:**
  - Jedes bearbeitbare Eingabefeld in Konfigurationsformularen, Dashboard, Backtesting, Login, Registrierung, Passphrase-Gate und Analyse verfügt über ein interaktives `ⓘ`-Symbol mit Bootstrap 5-Tooltip sowie `title`-Attributen.
  - Formular-Hilfetexte (`form-text`) und Feldlabels wurden umfassend überarbeitet und präzisiert.
- **Handbuch-Aktualisierung (`MANUAL.md`):**
  - Vollständige Zuordnungsmatrix zwischen Datenbankfeldern, Live-Formularen und Backtesting-Suchräumen.
  - Detaillierte mathematische Formeln und Bedeutungen für NDA, DVA, Beschleunigung, DeltaDelta und MVD.
- **Diagramm- und Ergebnis-Labels:**
  - Plotly-Chart-Traces und Backtesting-Ergebnis-Strings zeigen jetzt lesbare, aussagekräftige Indikatornamen an.
- **Testabdeckung:** Unit- und Integrationstests für Handbuch-Rendering, Formular-Labels, Help-Texte, Indikatoren und Backtest-Zuordnung erweitert.

## [2.3.0] – 2026-08-22

### Universelles Build-/Install-Skript und automatische Hardware-Optimierung

- `install.sh` ist das neue Distributions-agnostische Build- und Installationsskript. Es erkennt die Linux-Distribution über `/etc/os-release` (inkl. Fallbacks) und wählt automatisch den passenden Paketmanager: `apt` für Debian/Ubuntu, `pacman` für Arch/Manjaro, `dnf`/`yum` für RHEL/CentOS/Fedora/Rocky/Alma/Amazon Linux (mit automatischer EPEL-Aktivierung, wo Redis benötigt wird).
- Vollständiges Paket-Mapping für Redis, Python 3, Build-Toolchain, PostgreSQL-Header und die nativen WeasyPrint/Pango/Harfbuzz-Laufzeitbibliotheken pro Paketmanager.
- Redis wird als lokaler Service eingerichtet (systemd/OpenRC/`service`), beim Start aktiviert, per `redis-cli PING` auf Verfügbarkeit geprüft und bei Fehlschlag automatisch neu gestartet.
- Modi `--mode=auto|host|container` und Profile `--profile=full|runtime`; `auto` erkennt Docker/Podman/LXC-Container über `/.dockerenv`, `/proc/1/cgroup` und `/run/.containerenv`. Im Container-Modus wird kein sudo benötigt und keine Service-Verwaltung versucht.
- Privilegien-Prüfung: Nicht-root-Ausführung im Host-Modus re-exec'd sich selbst mit `sudo -E` und Array-Argumenten (kein Word-Splitting).
- `hardware-test.sh` vermisst CPU (`nproc`, `/proc/cpuinfo`, `uname -m`), RAM (`/proc/meminfo` mit `free`-Fallback) und Disk-I/O (64-MB-`dd`-Schreib-/Lesetest mit portabler `1M`-Blockgröße) und berechnet daraus eine Heuristik für Redis `maxmemory`/`maxmemory-policy`/`io-threads`, `BOT_DB_WORKERS`, `DB_POOL_SIZE`, `WEB_CONCURRENCY`, `CELERY_WORKER_MAX_MEMORY_PER_CHILD` sowie die Docker-Compose-CPU-Limits. Ausgaben in `env` (sourcbar), `json` und `text`.
- Render.com-Simulation ist in der erzeugten `config/local.env` standardmäßig deaktiviert (`RENDER=False`, `RENDER_SIMULATION=False`), kann über `--render-simulation` oder nachträglich in der Config aktiviert werden, ohne das lokale Setup zu beeinträchtigen.
- Docker-Integration: Beim `docker compose up --build` führt ein neuer One-Shot-`tuner`-Service (`docker/tuner-entrypoint.sh`) `hardware-test.sh` aus und schreibt `tuning.env` in ein gemeinsam genutztes Volume. Redis (`docker/redis-entrypoint.sh`) startet mit den automatisch berechneten Flags; Web/Worker/Beat-Sourcen (`docker/load-tuning.sh`) übernehmen die empfohlenen Werte. Alle App-Services warten auf `tuner: service_completed_successfully`.
- `Dockerfile` führt im Build `install.sh --mode=container --profile=runtime` aus, mit sicherem apt-Fallback für restriktive Build-Umgebungen.
- `config.template` dokumentiert alle optimierbaren Variablen mit Defaults und Wertebereichen.
- Test-Suite unter `tests/` mit Runner, Test-Helfer und Mock-Binaries: sechs Test-Dateien decken Distro-Erkennung (sechs `/etc/os-release`-Fixtures), Paketmanager-Aufrufe (apt/pacman/dnf/yum-Mocks), Redis-Verfügbarkeit, Hardware-Heuristik (1/4/16 GB-Szenarien) und Konfigurationsgenerierung (Mode 600, Idempotenz, Secret-Key-Länge, deaktivierte Render-Simulation) ab. `tests/distro_smoke_test.sh` baut optionale Debian/Arch/Fedora-Docker-Images, wenn Docker lokal verfügbar ist.
- ShellCheck 0.11.0 ist fehlerfrei für alle Produktiv- und Testskripte; `set -euo pipefail`, Input-Validierung, `mktemp`-Temporärdateien mit `trap`-Cleanup, keine Hardcoded-Credentials.
- `README.md` um Abschnitte zur automatischen Installation, Hardware-Optimierung, Render-Simulation, Docker-Tuning-Fluss und Test-Ausführung erweitert.
- `PEER_REVIEW.md` dokumentiert das Selbst-Review mit Checklisten für Sicherheit, Performance, Kompatibilität und Code-Qualität sowie das vollständige Paket-Mapping.
## [2.3.0] – 2026-08-21

### Distributions- und hardwareunabhängiges Local Setup

- Automatischer Systeminstaller für apt, pacman, dnf/yum, zypper und apk einschließlich Debian/Ubuntu, Arch, Fedora/RHEL-Derivate, openSUSE und Alpine.
- Native Architekturerkennung für amd64, arm64, arm/v7, ppc64le und s390x.
- Ein-Schritt-Setup `scripts/setup_local.sh` installiert bei Bedarf Docker, führt Hardwaretests durch, baut Images und startet den isolierten Stack.
- Hardwareprobe misst CPU-Hashrate, RAM, freien Datenträger und fsync-Schreibrate; daraus werden CPU-/RAM-Grenzen, Redis-Maxmemory und PostgreSQL-Cachewerte generiert.
- Render-Free-Simulation ist standardmäßig deaktiviert und nur über `--render-free-simulation` aktivierbar.
- Compose-PostgreSQL erhält begrenzte Verbindungen und hardwareabhängige Cacheparameter; Redis läuft mit hardwareabhängigem `maxmemory`.
- Sicherheitsmodus 0600 für generierte `.env.local`, bestehende Secrets werden beim Retuning beibehalten.
- Dry-Run-Tests für alle Paketmanager-Familien und deterministische Unit-Tests für Hardwareprofile ergänzt.

## [2.2.0] – 2026-08-21

### Produktionsreifes lokales Backtesting-Setup

- `docker-compose.yml` mit getrennten Services für Daphne/Web/Bot, Celery-Backtest-Worker, Redis und PostgreSQL; optionaler Beat-Scheduler über Compose-Profil.
- `.env.docker.example`, lokale Resource-Limits und optional `WEB_CPUS=0.10` zur Render-Free-Simulation.
- Zentrale `celery_config.py`: Queue `backtest`, Concurrency 1, Prefetch 1, Child-Recycling, 384-MB-Limit, Soft-/Hard-Limits, Late ACK und Worker-Lost-Requeue.
- Redis-Prioritäten: zukünftige Bot-/Default-Tasks Priorität 9, Backtests Priorität 0.
- Worker-Status mit Redis-/Ping-Prüfung, Fünf-Sekunden-Cache und sicherem lokalen Fallback.
- Authentifizierter `/api/backtesting/status/`-Endpoint mit Workerstatus sowie Web-Heartbeat, Peak-RSS, Threadzahl und Scheduler-Lag.
- Backtest-Resultate enthalten Dauer, Peak-RSS, Kombinationen und Preispunktmetriken; strukturierte `event=backtest.*`-Logs.
- Kooperative Pause/Cancel-DB-Prüfung auf ungefähr 100 Checks pro Task gedrosselt.
- `LOCAL_DEVELOPMENT.md`, Worker-/Beat-Entrypoints und vollständige Start-, Ausfall- und Verifikationsanleitung.

## [2.1.0] – 2026-08-21

### Lokale Analyse, modernes UI und isolierte Backtesting-Architektur

- Analyse verwendet lokale DataLogs statt Binance-REST-OHLCV. Damit entstehen keine Analyse-API-Requests, kein Request-Weight und kein HTTP-418-Ban; SMA-5/15 wird aus lokalen Zeit-Buckets berechnet.
- Navigation vollständig modernisiert; Backtesting ist direkt im Hauptmenü erreichbar, Hilfe sitzt rechts neben Benutzer und Logout.
- Hilfeseite erhielt kontrastreiche Pygments-Codehervorhebung, bessere Typografie, Spacing, Tabellen und Druckansicht.
- Backtesting-Auswahlseite für alle Konfigurationen ergänzt.
- Backtesting-Formular um benannte Indikatorbereiche, Beschreibungen, Trade-Betrag, Take Profit, Stop Loss, Gebühr, Preispunkt-Limit, Live-Kombinationszähler und ausklappbare Hilfe erweitert.
- Machbarkeitsstudie dokumentiert: Render Free kann harte Prozessisolation nicht garantieren. Lokale Backtests sind dort deaktiviert; Produktion nutzt Redis plus separaten Celery-Worker.
- Celery Queue `backtest`, Concurrency 1, Prefetch 1, Child-Recycling, 384-MB-Limit, Soft-/Hard-Time-Limits und Graceful Degradation konfiguriert.
- Ressourcen-Probe in separatem Prozess: 100 Kandidaten × 5.000 Punkte, 17,41 MB Peak-RSS, 0,267 s, stabiler 20,21-ms-Eltern-Heartbeat.
- `BACKTESTING_STUDY.md`, `render.worker.example.yaml` und `scripts/backtest_resource_probe.py` hinzugefügt.

## [2.0.4] – 2026-08-21

### Connection-Pool-Fix und autonomer Botbetrieb

- Root Cause bestätigt: Free-Postgres war nicht primär wegen DNS offline, sondern durch zu viele parallele Client-Verbindungen (`remaining connection slots are reserved for SUPERUSER`).
- Sämtliche Bot-ORM-Aufrufe laufen über einen eigenen Executor mit standardmäßig genau einem Worker. Direkte Verbindungen verwenden `CONN_MAX_AGE=0` und werden nicht mehr in vielen Thread-Locals festgehalten.
- Ein optionales `DATABASE_POOL_URL` wird auf bezahlten Render-Datenbanken bevorzugt; Free-Postgres unterstützt Render-PgBouncer nicht und bleibt deshalb bewusst bei streng begrenzten Direktverbindungen.
- Lokale Backtests werden serialisiert, um weitere parallele DB-Verbindungen und Free-Tier-Last zu vermeiden.
- Der ungeprüfte automatisch abgeleitete externe Host-Fallback wurde entfernt; ein Fallback ist nur noch explizit konfigurierbar.
- Bei DB-Ausfall laufen Marktdaten und Strategie mit der letzten validierten Konfiguration weiter. Nicht speicherbare TradingLogs werden bis zur Recovery geordnet im RAM gepuffert und anschließend atomar nachgeschrieben.
- Neue Hilfe-Seite `/help/` rendert das vollständige `MANUAL.md` mit Inhaltsverzeichnis, Tabellen, Codeblöcken und Druckansicht.
- Manual um Connection-Pool-Diagnose, autonomen Hintergrundbetrieb und Grenzen des RAM-Journals erweitert.

## [2.0.3] – 2026-08-20

### DB-Circuit-Breaker und Free-Tier-Lastreduktion

- Nach fünf koordinierten Fehlversuchen öffnet ein globaler DB-Circuit-Breaker für fünf Minuten. Wartende Bot-Operationen brechen sofort ab, statt nacheinander neue lange Reconnect-Serien zu starten.
- DB-Ausfallmeldungen aus HTTP-Middleware und Bot-Threads werden zeitlich gedrosselt.
- Konfigurationen werden im Bot nur noch alle 30 Sekunden neu aus PostgreSQL geladen statt in jedem Marktzyklus.
- DataLogs werden pro Symbol standardmäßig höchstens alle 10 Sekunden persistiert; die Trading-Auswertung läuft weiterhin im konfigurierten Intervall.
- Dashboard-Polling wurde auf 10 Sekunden reduziert. Dies senkt Query-, Schreib- und Netzwerkdruck auf Free-Postgres erheblich.
- Die externe PostgreSQL-Verbindung bleibt ein Fallback, kann aber einen tatsächlich gestoppten/defekten Datastore naturgemäß nicht ersetzen.

## [2.0.2] – 2026-08-20

### Datenbank-Failover, Request-Circuit-Breaker und Kontostandskorrektur

- PostgreSQL nutzt eine libpq-Hostliste: Render-Private-DNS bleibt primär, der TLS-geschützte externe Frankfurt-Hostname dient als automatischer Fallback.
- Signierte Cookie-Sessions entkoppeln Passphrase und Login-Sitzung von kurzfristigen DB-DNS-Störungen.
- Dashboard-Requests pausieren nach DB-503 lokal mit exponentiellem Backoff; nur ein Recovery-Probe-Request wird zugelassen. Dadurch endet die API-503-Dauerschleife.
- Gleichzeitige Bot-DB-Reconnects werden pro Prozess koordiniert.
- Der verfügbare Kontostand zieht offene Positionen und Kaufgebühren sofort ab. Zusätzlich zeigt die UI gebundenes Kapital, Gesamtequity, unrealisierten P/L und Anzahl offener Positionen.
- Neue TradingLogs speichern nach einem Buy den korrekten Cash-Snapshot.
- Reports enthalten nun vollständige Konfiguration, Cash-/Equity-Daten und offene Positionen; Exportbuttons und zentrale UI-Felder erhielten Hover-Erklärungen.
- Der angemeldete Benutzername bleibt in der Navigation sichtbar.

## [2.0.1] – 2026-08-20

### Render-Postgres-Verfügbarkeits-Hotfix

- Kurzzeitige `connection refused`-/DNS-Ausfälle liefern im Web statt eines internen 500-Fehlers eine verständliche HTTP-503-Seite beziehungsweise JSON-Antwort mit `Retry-After`.
- Passphrase-/Session-Zugriffe behandeln einen Datenbankausfall explizit; `/health/` bleibt unabhängig erreichbar.
- Bot-Reconnects werden pro Prozess koordiniert, sodass mehrere aktive Bots PostgreSQL nach einem Ausfall nicht gleichzeitig mit parallelen Reconnect-Schleifen belasten.
- Die bereits vorhandenen libpq-Keepalives, Connect-Timeouts und exponentiellen Reconnects bleiben aktiv.

## [2.0.0] – 2026-08-20

### Major-Update: Reporting, Betriebssicherheit und Exchange-UX

#### Hinzugefügt

- Aussagekräftige PDF-Dateinamen im Format `username_exchange_config-id_timestamp.pdf`.
- Downloadbare HTML- und CSV-Trading-Reports mit identischem Namensschema.
- Serverseitige Trading-Log-Pagination mit exakt 100 Einträgen pro Seite.
- Kill-Switch zum sofortigen Schließen aller offenen Paper-Positionen mit frisch abgerufenen Marktpreisen, doppelter Browserbestätigung und serverseitiger Doppelbestätigung.
- Deutliche rote Hervorhebung fehlerhafter Konfigurationsfelder inklusive feldbezogener Fehlermeldungen.
- Bitunix-Integration für öffentliche Spot- und Futures-Marktdaten. Futures-Ticker werden gebündelt abgerufen; bei fehlender/unverfügbarer API wird die Konfiguration sicher abgelehnt und kein Request-Loop gestartet.
- Kontextabhängige Symbol-Autovervollständigung für Exchange sowie Spot/Futures. Vorschläge sind zwischengespeichert und nur beratend; beim Speichern bleibt die Live-Validierung verbindlich.
- `CHANGELOG.md`, `MANUAL.md` und maschinenlesbare `VERSION`.
- Tests für Reportexporte, Pagination, Kill-Switch, Bitunix, Autocomplete und Formularfehler.

#### Geändert

- Dashboard-Aktionsbereich konsolidiert: PDF, HTML, CSV, Kill-Switch und Log-Reset sind klar gruppiert.
- Trading-Log wird neueste-zuerst dargestellt und belastet Browser/Server nicht mehr mit der gesamten Historie.
- Konfigurationsformulare verwenden ein gemeinsames, wartbares Template und Bootstrap-Validierungsstile.
- Symbolkataloge werden für 15 Minuten pro Exchange/Markt gecacht, um API-Last zu begrenzen.

#### Sicherheit

- Alle Exporte, Symbolvorschläge und Kill-Switch-Aufrufe sind authentifiziert und benutzerbezogen autorisiert.
- Kill-Switch und weitere Zustandsänderungen bleiben POST-/CSRF-geschützt.
- Dateinamenbestandteile werden gegen problematische Zeichen bereinigt.
- CSV wird streamend erzeugt und skaliert auch bei großen Logbeständen.

## [1.3.0] – 2026-08-20

### Stabilität von Datenbank und Marktdaten

- Persistenter Binance-`miniTicker`-WebSocket statt REST-Polling; dadurch kein REST-Request-Weight und keine Verlängerung von HTTP-418-Bans.
- Automatische WebSocket-Wiederverbindung mit Backoff, Endpunkt-Fallback und proaktivem 23-Stunden-Reconnect.
- Exakter Binance-`banned until`-Timestamp wird bei HTTP-Fallbackfehlern respektiert.
- BitMart-V3-Marktdatenadapter als Ersatz für den aus CCXT entfernten BitMart-Adapter.
- Live-Symbolvalidierung beim Speichern und Aktivieren einer Konfiguration.
- PostgreSQL-Reconnect mit DNS-Erkennung, Jitter, Keepalive und `wait_for_database` beim Containerstart.
- Erweitertes Fehler-Log mit Schweregrad, Exception-Typ, technischen Details, Filtern, Pagination und Erledigt-Status.

## [1.2.0] – 2026-08-19

### Trading- und Backtesting-Korrekturen

- Gebühren werden beim Kauf und Verkauf korrekt berücksichtigt.
- Verkaufsmenge entspricht der tatsächlich gekauften Menge.
- Offene Paper-Positionen und realisierter P/L werden nach Neustarts aus dem Trading-Log wiederhergestellt.
- Kapital, Equity, Drawdown, Sharpe, Win-Rate, Profit-Faktor und Risk/Reward korrigiert.
- Stop-Loss im Backtesting ergänzt; Verkäufe verwenden den tatsächlichen Kurs statt eines festen Take-Profit-Werts.
- Backtests akzeptieren Decimal-/Float-Schwellenwerte konsistent, begrenzen Kombinationen und speichern nur das beste Ergebnis pro Symbol.
- Pause/Resume/Cancel sowie unterbrochene lokale Tasks stabilisiert.
- Daten- und Fehler-Log-Wachstum begrenzt bzw. dedupliziert.

## [1.1.0] – 2026-08-19

### Render-Deployment

- Docker-Deployment mit unprivilegiertem Benutzer und WeasyPrint-Systembibliotheken.
- Eindeutiges `docker-entrypoint.sh`: Datenbank abwarten, migrieren, Daphne per `exec` starten.
- Render Blueprint mit PostgreSQL, generierten Secrets, Frankfurt-Region und öffentlichem Health-Check.
- WhiteNoise/Manifest-Staticfiles, sichere Proxy-/Cookie-/HSTS-Einstellungen und ASGI-WebSocket-Originprüfung.
- In-Memory-Fallbacks für Channels/Celery auf einer kostenlosen Einzelinstanz.

## [1.0.0] – 2026-08-18

### Initiale Plattform

- Django-Anwendung mit Benutzerregistrierung, Login und Passphrase-Gate.
- Konfigurierbarer Paper-Trading-Bot für mehrere Exchanges und Symbole.
- Dashboard, Trading- und Daten-Logs, Plotly-Charts und PDF-Reports.
- Parametrisierte Backtests mit Fortschritts-WebSocket.
- Technische Strategieindikatoren: DA, NDA, vorherige NDA, DVA/Beschleunigung, DeltaDelta und MVD.

[2.0.0]: https://github.com/Kryschuuu/t-bot/compare/4b38bd9...HEAD
[1.3.0]: https://github.com/Kryschuuu/t-bot/commit/4b38bd9
