# t-bot-lokal

Django-/Channels-Anwendung für Krypto-**Paper Trading**, Marktdaten und Backtests. Orders werden simuliert, nicht an eine Börse gesendet.

## Lokal starten

```bash
scripts/setup_local.sh
```

Das Skript erzeugt private App-Secrets in `.env.local` (Modus 0600) und startet den lokalen Docker-Stack auf <http://localhost:8369/>. Ein bereits aktivierter Passphrase-Gate und bestehende Secrets bleiben beim erneuten Setup erhalten. Für manuelles Compose müssen `SECRET_KEY` und `PASSPHRASE` in der Env-Datei gesetzt sein.

## Sicherheit ab 2.4.4

- **Produktion:** `DEBUG=False`, private `SECRET_KEY` und `PASSPHRASE`, `PASSPHRASE_GATE_ENABLED=True`. Fehlende/leere Secrets oder ein deaktivierter Gate verhindern den Start bei `DEBUG=False` **oder** auf Render.
- **Nur lokale Entwicklung:** Ohne `PASSPHRASE` erzeugen die Settings mit `secrets.token_urlsafe(32)` einen temporären Wert und zeigen ihn als WARNING in der Startkonsole. Nicht in öffentliche Logs übernehmen. Ein fehlender lokaler `SECRET_KEY` wird ebenfalls zufällig erzeugt, aber nicht ausgegeben.
- Für mehrere Prozesse und stabile Sessions beide Werte explizit setzen. Eine Passphrase-Rotation macht alte Gate-Freigaben ungültig; der Gate ersetzt nicht den Benutzer-Login.
- Das lokale Compose-Profil läuft mit `DEBUG=True` und standardmäßig ohne Gate. Es ist **kein Produktionsprofil**; vor Team-/Netzwerkzugriff den Gate einschalten und private Secrets konfigurieren.
- **Cookies (ab 2.4.5):** Session- und CSRF-Cookie werden mit `HttpOnly` gesetzt. Das CSRF-Token ist damit nicht über JavaScript (`document.cookie`) lesbar; es wird den Seiten stattdessen über `{% csrf_token %}` als verstecktes Formularfeld ausgegeben.

## Dokumentation

- [Installation, Tests und Render-Deployment](docs/README.md)
- [Lokales Docker-Setup](docs/LOCAL_DEVELOPMENT.md) · [FAQ und Secret-Rotation](docs/FAQ.md)
- [Benutzerhandbuch und API-Endpunkte](docs/MANUAL.md) · [Backtesting](docs/backtesting.md)
- [Security-Review 2.4.4, Befunde und Prüfgrenzen](docs/SECURITY_REVIEW_2.4.4.md)
- [Changelog](docs/CHANGELOG.md) · [Aktuelle Version](VERSION)
