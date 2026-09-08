# t-bot-lokal

Django-/Channels-Anwendung für Krypto-**Paper Trading**, Marktdaten und Backtests. Orders werden simuliert, nicht an eine Börse gesendet.

Aktuelle Version: siehe [`VERSION`](VERSION) · Historie: [Changelog](docs/CHANGELOG.md)

## Lokal starten

```bash
scripts/setup_local.sh
```

Das Skript erzeugt private App-Secrets in `.env.local` (Modus 0600) und startet den lokalen Docker-Stack auf <http://localhost:8369/>. Ein bereits aktivierter Passphrase-Gate und bestehende Secrets bleiben beim erneuten Setup erhalten. Für manuelles Compose müssen `SECRET_KEY`, `PASSPHRASE` und `POSTGRES_PASSWORD` in der Env-Datei gesetzt sein; fehlende oder leere Werte brechen die Compose-Interpolation ab (keine Standard-Passwörter mehr im Repository). Schritt für Schritt, inklusive Host-Installation ohne Docker und Render-Deployment: [Lokale Entwicklungsumgebung](docs/operations/LOCAL_DEVELOPMENT.md).

## Sicherheit (Kurzform)

- **Produktion:** `DEBUG=False`, private `SECRET_KEY` und `PASSPHRASE`, `PASSPHRASE_GATE_ENABLED=True`. Fehlende/leere Secrets oder ein deaktivierter Gate verhindern den Start bei `DEBUG=False` **oder** auf Render.
- **Nur lokale Entwicklung:** Ohne `PASSPHRASE` erzeugen die Settings einen temporären Zufallswert mit WARNING in der Startkonsole. Das lokale Compose-Profil läuft mit `DEBUG=True` und standardmäßig ohne Gate – **kein Produktionsprofil**.
- Zustandsändernde Endpunkte sind POST-only und CSRF-geschützt; Auth-Endpunkte haben ein IP-Rate-Limit; API-Antworten senden `Cache-Control: no-store`. HTTP-Header-Härtung, Cookie-Flags, Session-Lebensdauer und Fehler-Disclosure sind pro Befund als Findings-Dokumente mit Tests und Prüfgrenzen dokumentiert.
- Einzelne Befunde, Releases und Nachprüfungen: [docs/findings/](docs/README.md#findings) · verbindlicher Gesamtstatus: [Security-Review](docs/security/SECURITY_REVIEW_2.4.4.md).
- API-Schlüssel werden **niemals** in der Datenbank gespeichert, sondern nur als Umgebungsvariablen in den laufenden Prozess injiziert.

## Dokumentation

Der vollständige Index aller Dokumente, Konventionen und Archive liegt in [docs/README.md](docs/README.md). Kurzpfad:

- [Benutzerhandbuch](docs/manual/MANUAL.md) (wird in der App unter `/help/` gerendert) · [Backtesting](docs/manual/BACKTESTING.md)
- [Lokale Entwicklung, Betrieb, QA, Render-Deployment](docs/operations/LOCAL_DEVELOPMENT.md) · [FAQ und Secret-Rotation](docs/operations/FAQ.md) · [Tailscale-Fernzugriff](docs/operations/REMOTE_ACCESS_TAILSCALE.md) · [Caddy-Reverse-Proxy](docs/operations/CADDY_PROXY.md)
- [Security-Review (aktuell)](docs/security/SECURITY_REVIEW_2.4.4.md) · [Findings](docs/README.md#findings) · [Architektur-Entscheidungen](docs/adr/ADR-0001-backtesting-worker-isolation.md) · [Changelog](docs/CHANGELOG.md)
- [Beitragen: Konventionen, Findings-Vorlage, Peer-Review-Patches](CONTRIBUTING.md) · Patch-Eingang: [`patches/`](patches/README.md)
