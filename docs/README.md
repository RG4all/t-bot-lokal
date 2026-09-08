# t-bot-lokal

Django-/Channels-Anwendung für **Paper Trading**, Marktvisualisierung und parametrisierte Backtests. Der Bot simuliert Orders; er sendet keine echten Kauf- oder Verkaufsaufträge an eine Börse.

Binance-Kurse laufen über einen persistenten kombinierten WebSocket-Stream (kein REST-Polling/Request-Weight). BitMart Spot nutzt die aktuelle V3-Public-API; Bitunix Spot/Futures ist über öffentliche, defensiv gedrosselte Adapter integriert. Beim Speichern und Aktivieren werden alle Symbole live geprüft. Das Dashboard bietet paginierte Logs, PDF/HTML/CSV-Reports und einen doppelt bestätigten Kill-Switch.

Ausführliche Bedienung, Indikatorformeln und Betriebsanweisungen stehen in [`MANUAL.md`](MANUAL.md) und werden in der App unter `/help/` angezeigt. Das eigenständige, ausführliche Backtesting-Kapitel steht in [`backtesting.md`](backtesting.md). Die lokale Docker-Umgebung ist in [`LOCAL_DEVELOPMENT.md`](LOCAL_DEVELOPMENT.md) dokumentiert; das Review steht in [`LOCAL_SETUP_PEER_REVIEW.md`](LOCAL_SETUP_PEER_REVIEW.md). Die Backtesting-Machbarkeitsstudie mit Architekturdiagramm und Lastmessung steht in [`BACKTESTING_STUDY.md`](BACKTESTING_STUDY.md); [`render.worker.example.yaml`](../render.worker.example.yaml) ist die optionale Worker-Vorlage. Versionshistorie: [`CHANGELOG.md`](CHANGELOG.md). Aktuelle Version: **2.4.10** ([`VERSION`](../VERSION)). Security-Review mit Befunden und Prüfgrenzen: [`SECURITY_REVIEW_2.4.4.md`](SECURITY_REVIEW_2.4.4.md).

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
<http://localhost:8369/> erreichbar. Web, Worker, Redis und PostgreSQL
laufen als getrennte, ressourcenbegrenzte Services. Render-Free-Simulation
ist standardmäßig deaktiviert.

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

### Passphrase und Startkonfiguration

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

Das Setup speichert neue App-Secrets in `.env.local` statt sie zu loggen; bestehende Werte und ein eingeschalteter Gate bleiben beim Retuning erhalten. Manuelles Compose bricht bei leeren App-Secrets ab. Das Basis-Docker-Image deaktiviert den Gate nicht mehr; die lokale Ausnahme steht ausschließlich im Compose-Profil.

## Qualitätssicherung

Für Release 2.4.10 wurden die folgenden QA-Kommandos lokal ausgeführt. Im Repository ist kein GitHub-Actions-Anwendungstestworkflow versioniert; die Auslieferung erfolgt auf Basis ausdrücklich akzeptierter lokaler Prüfnachweise. Die Dependency-Graph-Integration allein ersetzt keine Anwendungstests. Testergebnisse, die genehmigte CI-Ausnahme und Prüfgrenzen stehen im [SEC-10-Nachweis](SEC-10-information-disclosure.md).

```bash
# Shell-Skripte linten und Test-Suite ausfuehren
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
./tests/run_tests.sh

# Distro-Smoke-Tests (benoetigt Docker, prueft apt/pacman/dnf)
./tests/distro_smoke_test.sh

# Django- / Python-Tests (lokale Testumgebung, kein Bot-Autostart)
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
# Gezielte Security-Regressionen einschließlich SEC-05 und SEC-10:
python manage.py test trading.tests.test_content_type_nosniff trading.tests.test_csrf_cookie trading.tests.test_session_invalidate trading.tests.test_cache_control trading.tests.test_permissions_policy trading.tests.test_error_disclosure --noinput
python manage.py collectstatic --noinput
python -m pip check
pip-audit -r requirements.txt   # wenn pip-audit installiert ist
```

## Render-Deployment

`render.yaml` definiert einen Docker-Web-Service und PostgreSQL in Frankfurt. Der Container installiert die für PDF-Reports benötigten Systembibliotheken, wartet beim Start mit DNS-/Connection-Backoff auf PostgreSQL, führt Migrationen aus und startet Daphne auf `$PORT`.

1. Den geprüften PR in den Integrationsbranch `tbot.local` übernehmen; beide Render-Vorlagen referenzieren diesen Branch.
2. In Render **New → Blueprint** wählen und dieses Repository verbinden.
3. Blueprint anwenden. `SECRET_KEY`, `PASSPHRASE` und `DATABASE_URL` werden automatisch erzeugt/verknüpft; `PASSPHRASE_GATE_ENABLED=True` ist explizit gesetzt.
4. Die erzeugte `PASSPHRASE` unter **t-bot-web → Environment** anzeigen und sicher aufbewahren.
5. Optional über die Render Shell einen Admin anlegen:
   ```bash
   python manage.py createsuperuser
   ```
6. `/health/`, Passphrase-Gate, Registrierung/Login, Dashboard und WebSocket-Fortschritt prüfen.
7. Bei einem Reverse-Proxy die tatsächlich vertrauenswürdigen Peer-IPs/CIDRs für `RATE_LIMIT_TRUSTED_PROXIES` ermitteln und im Service konfigurieren. Der Proxy muss `X-Forwarded-For` bereinigen oder die tatsächliche Peer-IP anhängen. Ohne Allowlist wird der Header ignoriert; hinter einem Proxy teilen sich sonst dessen Clients ein Rate-Limit. Keine pauschalen Netze wie `0.0.0.0/0` verwenden.
8. Für zusätzliche Web-Prozesse/Instanzen ein gemeinsames Auth-Rate-Limit am Edge/Proxy bzw. in einem geteilten Backend vorsehen; das App-Limit gilt pro Prozess.

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

- In Produktion (`DEBUG=False` oder Render) sind `SECRET_KEY`, `PASSPHRASE` und ein aktiver Gate Pflicht. `DEBUG=True` ohne Render ist ausdrücklich ein lokaler Entwicklungsmodus, keine automatische Erkennung einer sicheren Deployment-Umgebung.
- Zustandsändernde Endpunkte akzeptieren nur POST und sind CSRF-geschützt.
- **Auth-Endpunkte** (Login, Passphrase-Gate, Registrierung, Admin-Login) teilen ein IP-basiertes **Rate-Limit** von 5 POSTs / 15 min / Prozess, einschließlich erfolgreicher POSTs. Reservierung und Prüfung sind atomar; der Speicher ist begrenzt. Proxy-Header werden nur über eine explizite Allowlist vertraut.
- Die Anwendung setzt eine **Content-Security-Policy (CSP)** für lokale Ressourcen; nur mit einer frischen Request-Nonce markierte Template-Skripte dürfen inline laufen. Kein `unsafe-inline` für Skripte.
- **`X-Content-Type-Options: nosniff`** ist ab 2.4.6 mit `SECURE_CONTENT_TYPE_NOSNIFF = True` explizit und DEBUG-/Render-unabhängig konfiguriert. `SecurityMiddleware` bleibt an erster Stelle und erfasst auch Fehler, Redirects, Downloads und WhiteNoise-Antworten. Django 5.2 setzte den Header schon zuvor per Default; der Patch macht die Härtung explizit und regressionsgesichert. Details: [SEC-06 und SEC-05-Nachprüfung](SEC-06-rule-lifecycle-authz.md).
- **Session- und CSRF-Cookie sind `HttpOnly`** (CSRF-Cookie seit 2.4.5, zuletzt nachgeprüft in 2.4.10): Die Cookies sind nicht über `document.cookie` lesbar. Django liest das CSRF-Cookie serverseitig; Templates liefern das Token über `{% csrf_token %}` als verstecktes Formularfeld. Dieses DOM-Token bleibt für Skripte derselben Origin zugänglich: `HttpOnly` ist kein allgemeiner XSS-Schutz und ersetzt weder CSP noch CSRF-/Origin-Prüfungen. In Produktion gilt zusätzlich `CSRF_COOKIE_SECURE`.
- **Session-Lebensdauer und Logout-Invalidierung (ab 2.4.7):** `SESSION_COOKIE_AGE = 8h` (statt 12 h) und `SESSION_EXPIRE_AT_BROWSER_CLOSE = True` verkleinern das Replay-Fenster für gestohlene signierte Cookies. Die `logout_view` ruft `request.session.flush()` auf, sodass die Session sofort ungültig wird und der Key rotiert – nach Abmeldung ist der Benutzer anonym und der Gate muss ggf. erneut freigegeben werden. Details: [SEC-07](SEC-07-session-lifetime-invalidation.md).
- **Cache-Control für API-Endpunkte (ab 2.4.8):** Der Decorator `no_cache_json` setzt `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` und `Pragma: no-cache` auf alle JSON-API-Endpunkte – auch auf Fehlerantworten (400/503). Browser und Proxies/CDNs speichern damit keine benutzerbezogenen Handels-, Portfolio-, Log- oder Marktdaten zwischen. Details: [SEC-08](SEC-08-cache-control-api.md).
- **`Permissions-Policy` (ab 2.4.9):** Die Middleware `PermissionsPolicyMiddleware` (direkt nach der `SecurityMiddleware`) setzt `Permissions-Policy: camera=(), microphone=(), geolocation=()` aus der zentralen Einstellung `SECURE_PERMISSIONS_POLICY`. Kamera, Mikrofon und Geolokation sind damit für alle Origins deaktiviert – auch auf Fehler-, Redirect- und WhiteNoise-Antworten. Details: [SEC-09](SEC-09-permissions-policy.md).
- **Fehlermeldungen (ab 2.4.10):** Bot-Aktionen, Marktdaten-/Worker-APIs, Konfigurationsformulare und Reports geben keine technischen Exception-Texte mehr aus. Diagnosen werden mit Traceback serverseitig geloggt. Im Fehler-Log sehen normale Konten generische Einträge mit Referenz; technische Details benötigen zusätzlich zur Eigentümerschaft `is_staff`. Bereits gespeicherte Fehler sind ebenfalls geschützt. Details: [SEC-10](SEC-10-information-disclosure.md).
- `ALLOWED_HOSTS` enthält keinen Wildcard-Eintrag – auch im DEBUG-Modus werden nur explizite lokale Hosts akzeptiert (Schutz gegen Host-Header-Injection).
- Konfigurationen, Logs, Backtests, PDFs und WebSockets sind benutzerbezogen autorisiert. Gate-Freigaben sind HMAC-gebunden an die aktuelle Passphrase und den privaten Signierschlüssel; alte boolesche Gate-Cookies werden beim Upgrade abgelehnt. WebSockets prüfen bei Verbindungsaufbau sowohl Gate als auch Eigentümerschaft.
- API-Schlüssel werden **niemals** in der Datenbank gespeichert. Sie werden beim Container-Start als Umgebungsvariablen (`EXCHANGE_API_KEY`, `EXCHANGE_SECRET_KEY`) injiziert und existieren nur im Arbeitsspeicher des laufenden Prozesses. Für Live-Handel Umgebungsvariablen in `docker-compose.yml` oder über Render Secrets setzen.
