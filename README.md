# t-bot-lokal

Django-/Channels-Anwendung für Krypto-**Paper Trading**, Marktdaten und Backtests. Orders werden simuliert, nicht an eine Börse gesendet.

Aktuelle Version: **2.4.18** ([`VERSION`](VERSION)).

## Lokal starten

```bash
scripts/setup_local.sh
```

Das Skript erzeugt private App-Secrets in `.env.local` (Modus 0600) und startet den lokalen Docker-Stack auf <http://localhost:8369/>. Ein bereits aktivierter Passphrase-Gate und bestehende Secrets bleiben beim erneuten Setup erhalten. Für manuelles Compose müssen `SECRET_KEY`, `PASSPHRASE` und `POSTGRES_PASSWORD` in der Env-Datei gesetzt sein; fehlende oder leere Werte brechen die Compose-Interpolation ab (keine Standard-Passwörter mehr im Repository).

## Sicherheit

- **Produktion:** `DEBUG=False`, private `SECRET_KEY` und `PASSPHRASE`, `PASSPHRASE_GATE_ENABLED=True`. Fehlende/leere Secrets oder ein deaktivierter Gate verhindern den Start bei `DEBUG=False` **oder** auf Render.
- **Nur lokale Entwicklung:** Ohne `PASSPHRASE` erzeugen die Settings mit `secrets.token_urlsafe(32)` einen temporären Wert und zeigen ihn als WARNING in der Startkonsole. Nicht in öffentliche Logs übernehmen. Ein fehlender lokaler `SECRET_KEY` wird ebenfalls zufällig erzeugt, aber nicht ausgegeben.
- Für mehrere Prozesse und stabile Sessions beide Werte explizit setzen. Eine Passphrase-Rotation macht alte Gate-Freigaben ungültig; der Gate ersetzt nicht den Benutzer-Login.
- Das lokale Compose-Profil läuft mit `DEBUG=True` und standardmäßig ohne Gate. Es ist **kein Produktionsprofil**; vor Team-/Netzwerkzugriff den Gate einschalten und private Secrets konfigurieren.
- **HTTP-Header (ab 2.4.6 explizit):** `SECURE_CONTENT_TYPE_NOSNIFF = True` setzt `X-Content-Type-Options: nosniff` unabhängig von DEBUG/Render. Die vorhandene `SecurityMiddleware` erfasst auch Fehlerantworten, Downloads und durch WhiteNoise ausgelieferte statische Dateien. Der Fix verlässt sich nicht mehr nur auf den Django-Default.
- **Cookies (ab 2.4.5, zuletzt in 2.4.16 nachgeprüft):** Session- und CSRF-Cookie werden mit `HttpOnly` gesetzt und sind nicht über `document.cookie` lesbar. Das CSRF-Token wird über `{% csrf_token %}` als verstecktes Formularfeld ausgegeben und bleibt dort für Skripte derselben Origin zugänglich. `HttpOnly` ist zusätzliche Cookie-Härtung, kein allgemeiner XSS-Schutz.
- **Session-Lebensdauer und Logout-Invalidierung (ab 2.4.7):** Die Session-Cookie-Lebensdauer ist auf 8 Stunden beschränkt und läuft ab, wenn der Browser geschlossen wird (`SESSION_EXPIRE_AT_BROWSER_CLOSE = True`). POST `/logout/` ruft `request.session.flush()` auf, sodass alle Session-Daten unmittelbar ungültig werden und der Session-Key rotiert. Details: [SEC-07](docs/SEC-07-session-lifetime-invalidation.md).
- **Cache-Control für API-Endpunkte (ab 2.4.8):** Alle JSON-API-Endpunkte (`/api/...`) setzen `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` und `Pragma: no-cache`. Browser und Proxies/CDNs speichern damit keine benutzerbezogenen Handels-, Portfolio-, Log- oder Marktdaten zwischen. Details: [SEC-08](docs/SEC-08-cache-control-api.md).
- **Permissions-Policy (ab 2.4.9):** Die Middleware `PermissionsPolicyMiddleware` setzt `Permissions-Policy: camera=(), microphone=(), geolocation=()` aus `SECURE_PERMISSIONS_POLICY` – damit sind Kamera, Mikrofon und Geolokation für alle Origins deaktiviert, auch auf Fehler- und WhiteNoise-Antworten. Das Projekt benötigt diese APIs nicht. Details: [SEC-09](docs/SEC-09-permissions-policy.md).
- **Fehlermeldungen (ab 2.4.10):** Bot-Aktionen, Marktdaten-/Worker-APIs, Konfigurationsformulare und Reports geben keine technischen Exception-Texte mehr aus. Diagnosen werden mit Traceback serverseitig geloggt. Im Fehler-Log sehen normale Konten generische Einträge mit Referenz; technische Details benötigen zusätzlich zur Eigentümerschaft `is_staff`. Bereits gespeicherte Fehler sind ebenfalls geschützt. Details: [SEC-10](docs/SEC-10-information-disclosure.md).
- **Keine Standard-Passwörter im Docker-Setup (ab 2.4.11):** `docker-compose.yml` verlangt `SECRET_KEY`, `PASSPHRASE` und `POSTGRES_PASSWORD` als Pflichtwerte (`${VAR:?...}`). Fehlende oder leere Secrets brechen den Compose-Start mit klarem Fehler ab; ein öffentlich bekanntes DB-Standard-Passwort existiert nicht mehr. `scripts/setup_local.sh` erzeugt das lokale Datenbank-Passwort zufällig und privat in `.env.local` und bewahrt es beim Retuning. Details: [SEC-12](docs/SEC-12-docker-default-passwords.md).
- **Thread-sicherer Bot-Start/Stop (ab 2.4.12):** `TradingBotManager` prüft den Laufzustand ohne verschachtelte Lock-Acquisition. Gleichzeitige Start- und Status-Aufrufe erzeugen keinen zweiten Trading-Thread für dieselbe Konfiguration. Details: [BUG-12](docs/BUG-12-race-condition-bot-start-stop.md).
- **CSV-Kompatibilität (ab 2.4.13):** Der Trading-CSV-Export verwendet `io.StringIO` statt eines eigenen Echo-Adapters. Der synchrone Generator puffert jeweils nur eine CSV-Zeile und schließt den Puffer auch bei Abbruch oder Fehler; Format und Eigentümerprüfung bleiben unverändert. Details und ASGI-Prüfgrenzen: [BUG-14](docs/BUG-14-csv-echo-true-stream.md).
- **DB-Trim in Batches (ab 2.4.14):** Das Aufräumen alter DataLog-Einträge (`db_trim_datalog`) löscht in 1000er-Schritten statt in einem einzelnen DELETE über alle Alt-Einträge. Jede Charge läuft in einer eigenen kurzen Transaktion, dadurch bleiben Datenbank-Locks auch bei 20.000+ Zeilen pro Symbol kurz. Details und Prüfgrenzen: [PERF-17](docs/PERF-17-db-trim-batch-delete.md).
- **Eine Indikatorquelle für Bot und Backtest (ab 2.4.15):** NDA, DeltaDelta und Acceleration werden ausschließlich in `trading/indicators.py` berechnet; Backtesting, Bot, Celery-Tasks und die Ressourcen-Probe delegieren dorthin. Damit kann eine Formeländerung nicht mehr nur in einem der beiden Pfade landen. Zusätzlich abgesichert: ein Index unterhalb von 2 oder außerhalb der Preisreihe liefert einen `IndexError` statt eines stillschweigend falschen Werts. Bestehende Backtest-Ergebnisse und `DataLog`-Zeilen bleiben bitgenau reproduzierbar. Details: [CODE-18](docs/CODE-18-indicator-dedup.md).
- **Statisch geprüfte Views (ab 2.4.16):** `trading/views.py` ist vollständig typannotiert und über einen `[tool.mypy]`-Block in `pyproject.toml` reproduzierbar prüfbar (mypy und django-stubs bleiben Entwicklungswerkzeuge außerhalb von `requirements.txt`). Alle ORM-Filter laufen über `_authenticated_user()`: Fällt bei einer künftigen Änderung ein `@login_required` weg, endet der Request mit 403 statt mit einem Filter auf `AnonymousUser`. Zusätzlich gehärtet: ein leeres Gate-Secret erzeugt keine Freigabe mehr, und defekte Equity-Punkte kippen die Reportausgabe nicht. Es wird keine behobene Schwachstelle behauptet – alle drei Pfade waren im ausgelieferten Stand nicht erreichbar. Details und Prüfgrenzen: [CODE-19](docs/CODE-19-view-type-hints.md).
- **Explizite öffentliche Exporte (ab 2.4.17):** `trading/__init__.py` macht die öffentliche API über `__all__` und eine explizite Import-/Lazy-Load-Policy sichtbar. Ohne diese Festlegung exportierte `from trading import *` nach dem Laden interner Module versehentlich auch Django-Infrastruktur (`admin`, `middleware`, `passphrase` usw.). Import-sichere Module (`backtesting`, `indicators`, `market_data`, `market_scanner`, `resource_optimizer`, `symbols`) werden sofort gebunden; die Django-Module (`models`, `forms`, `views`, `tasks`, `trading_bot`, `worker_status`) werden per PEP-562-`__getattr__` erst beim Zugriff geladen. Ein naives `from . import models` in `__init__.py` bräche die Django-App-Population mit `AppRegistryNotReady` ab. Details und Prüfgrenzen: [CODE-20](docs/CODE-20-module-exports.md).

## Dokumentation

- [Installation, Tests und Render-Deployment](docs/README.md)
- [Lokales Docker-Setup](docs/LOCAL_DEVELOPMENT.md) · [FAQ und Secret-Rotation](docs/FAQ.md)
- [Benutzerhandbuch und API-Endpunkte](docs/MANUAL.md) · [Backtesting](docs/backtesting.md)
- [Security-Review 2.4.4, Befunde und Prüfgrenzen](docs/SECURITY_REVIEW_2.4.4.md)
- [SEC-06: nosniff-Fix sowie SEC-05-/SEC-06-Nachprüfung](docs/SEC-06-rule-lifecycle-authz.md)
- [SEC-07: Session-Lifetime- und Logout-Invalidierung](docs/SEC-07-session-lifetime-invalidation.md)
- [SEC-08: Cache-Control-Header für API-Endpunkte](docs/SEC-08-cache-control-api.md)
- [SEC-09: Permissions-Policy-Header](docs/SEC-09-permissions-policy.md)
- [SEC-10: Sichere Fehlermeldungen und SEC-05-Nachprüfung](docs/SEC-10-information-disclosure.md)
- [SEC-12: Keine Standard-Passwörter im Compose-Setup](docs/SEC-12-docker-default-passwords.md)
- [PERF-17: Batch-Delete für den DataLog-Trim](docs/PERF-17-db-trim-batch-delete.md)
- [CODE-18: Gemeinsame Indikatorberechnung für Bot und Backtest](docs/CODE-18-indicator-dedup.md)
- [CODE-19: Type-Hints und Docstrings der Views](docs/CODE-19-view-type-hints.md)
- [CODE-20: Explizite Modul-Exports](docs/CODE-20-module-exports.md)
- [Changelog](docs/CHANGELOG.md) · [Aktuelle Version](VERSION)
