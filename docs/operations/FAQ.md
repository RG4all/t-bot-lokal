# FAQ / How-Tos - t-bot-lokal

Antworten auf haeufige Fragen und Schritt-fuer-Schritt-Anleitungen fuer
Betrieb, Diagnose und Troubleshooting des lokalen Docker-Stacks.

> Kurzform fuer Ungeduldige:
> ```bash
> git pull
> scripts/setup_local.sh --reset-db --yes
> scripts/diagnose_local.sh
> curl -i http://127.0.0.1:8369/health/
> ```

---

## 1. Wie starte ich den Stack?

```bash
# 1. Empfohlen: Ein-Schritt-Setup (erkennt Hardware, schreibt .env.local, startet Stack)
scripts/setup_local.sh

# 2. Mit Installation von Docker/Systemabhaengigkeiten (frisches Linux):
scripts/setup_local.sh --install-deps

# 3. Manuell:
cp .env.docker.example .env       # PASSPHRASE/SECRET_KEY/POSTGRES_PASSWORD anpassen
docker compose up --build -d
```

Danach oeffnen: **<http://localhost:8369/>** (http, nicht https).

Nach dem Setup ist **kein Neustart nötig** - das Skript startet den Stack
selbst und wartet auf den Health-Status von `web`. Wer über Weg 1/2 startet,
muss manuellen `docker compose`-Kommandos die Env-Datei mitgeben
(`docker compose --env-file .env.local ps`), sonst brechen sie mit
„required variable SECRET_KEY is missing a value" ab:
[COMPOSE_ENV_FILE.md](COMPOSE_ENV_FILE.md).

## 2. Welche Container laufen sollen?

```bash
docker compose ps
```

Erwartet:

| Service | Status | Ports |
|---|---|---|
| `tuner` | `Exited (0)` | (kurzlebig; schreibt tuning.env) |
| `postgres` | `Up (healthy)` | 5432/tcp (nur intern) |
| `redis` | `Up (healthy)` | 6379/tcp (nur intern) |
| `web` | `Up (healthy)` | 0.0.0.0:8369->8369/tcp |
| `backtest-worker` | `Up (healthy)` | (nur intern) |
| `scheduler` | nur mit Profil `scheduler` | (nur intern) |

Dass `tuner` im Status `Exited` steht, ist **erwuenscht** (One-Shot-Container).

## 3. Warum sehe ich nur einen 302-Redirect auf `/gate/` oder `/login/`?

Der lokale Docker-Stack ist standardmäßig ohne Passphrase-Gate aktiviert. Dann
führt `/` direkt zu `/login/`; der normale Login bleibt davon unberührt. Ein
302 auf `/gate/` erscheint nur, wenn `PASSPHRASE_GATE_ENABLED=True` gesetzt
wurde. In Produktion ist ein aktiver Gate ab 2.4.4 zwingend; das lokale DEBUG-Compose-Profil ist nicht für öffentlich erreichbare Instanzen geeignet.

Test im Standardsetup:

```bash
curl -i http://localhost:8369/          # -> 302, Location: /login/
curl -i http://localhost:8369/health/   # -> 200 OK, JSON {"status":"ok"}
```

Zum Aktivieren des zusätzlichen Gates in der verwendeten `.env.local` bzw. `.env` `PASSPHRASE_GATE_ENABLED=True` setzen. Eine private Passphrase erzeugen:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Den Wert als `PASSPHRASE` in der privaten Env-Datei speichern, dann die App-Container neu erstellen. Compose verlangt ab 2.4.4 **auch bei deaktiviertem lokalem Gate** explizite App-Secrets. `scripts/setup_local.sh` erzeugt sie automatisch und schaltet einen bereits aktivierten Gate beim Retuning nicht mehr aus.

### Warum sehe ich eine generierte Passphrase im WARNING?

Nur lokale Settings (`DEBUG=True`, kein Render) erzeugen bei fehlender/leerer Passphrase einen temporären Zufallswert. Dieser erscheint in der Startkonsole und gilt nur für diesen Settings-Ladevorgang. Management-Kommandos und Autoreload können andere Werte ausgeben; für einen Ein-Prozess-Test `runserver --noreload` verwenden. Für dauerhaften Zugriff beide Secrets explizit setzen. Ein temporärer `SECRET_KEY` wird nie ausgegeben; konfigurierte Passphrasen werden ebenfalls nicht geloggt.

Auf Render oder bei `DEBUG=False` ist stattdessen ein `RuntimeError` beabsichtigt: `SECRET_KEY` und `PASSPHRASE` setzen und `PASSPHRASE_GATE_ENABLED=True` beibehalten. DEBUG nicht aktivieren, um diese Prüfung in Produktion zu umgehen.

## 4. `http://localhost:8369` antwortet nicht - Container sind aber healthy

Sofort-Checks:

```bash
scripts/diagnose_local.sh                       # automatische forensische Diagnose
curl -i http://127.0.0.1:8369/health/            # bevorzugt IP statt Namen verwenden
docker compose logs --tail 100 web
docker compose exec web python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8369/health/', timeout=3).read())"
```

Haeufige Ursachen und Abhilfen:

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `Connection refused` auf `localhost:8369`, Container aber `healthy` | Docker-Desktop/Engine fuerwartet den Port nicht auf den Host (WSL2, Remote-Docker, VPN, anderer Context) | `scripts/diagnose_local.sh` prueft das. Docker-Context mit `docker context use default` zuruecksetzen. |
| Browser "connection reset" / "nicht sicher" | Statt `http://` wurde `https://` aufgerufen | Explizit `http://127.0.0.1:8369/` aufrufen; HSTS fuer localhost im Browser loeschen. |
| curl geht, Browser nicht | HTTP-Proxy / `HTTP_PROXY`-Variable | `localhost,127.0.0.1` in `NO_PROXY` aufnehmen. |
| Port 8369 bereits belegt | Zweiter Server/anderes Compose-Projekt | `WEB_PORT=8001 docker compose up -d` oder `ss -ltnp \| grep 8369`. |
| Linux-Host mit `ufw`/`firewalld` aktiv | Firewall blockiert 8369/tcp auf dem Host | `sudo ufw allow 8369/tcp` bzw. `firewall-cmd --add-port=8369/tcp`. |
| Remote-Server / VM | Dienst lauscht zwar im Container, aber nicht auf der oeffentlichen IP des Hosts | `http://<server-ip>:8369/`; zusaetzlich `WEB_CPUS`/`WEB_PORT` in `.env.local` anpassen. |
| Docker Desktop WSL2-Integration | Ports werden nicht zu Windows weitergereicht | Docker Desktop -> Settings -> Resources -> WSL Integration fuer die Distro aktivieren; ggf. WSL-Distro neu starten. |

## 5. Container starten, werden aber nicht healthy (Restart-Loop)

```bash
docker compose logs -f web
```

Haeufigster Fall: **Postgres-Passwort-Mismatch** (Volume wurde mit einem
anderen Passwort initialisiert). Symptom in den Logs:

```
password authentication failed for user "tbot"
FATAL: password authentication failed for user "tbot"
```

Ursache: Das offizielle Postgres-Image liest `POSTGRES_PASSWORD` nur beim
**ersten** Initialisieren eines leeren Datenverzeichnisses. Ein spaeteres
Aendern der Umgebungsvariable aendert das Passwort im bestehenden Volume
**nicht**.

Seit 2.4.11 gilt zusaetzlich: `docker-compose.yml` verlangt
`POSTGRES_PASSWORD` als Pflichtwert ohne Default, und `setup_local.sh`
erzeugt fuer neue Setups ein zufälliges, privat gehaltenes Passwort in
`.env.local`. Wurde ein Volume mit dem frueher oeffentlichen Standard-Passwort
initialisiert und `.env.local` anschließend gelöscht, meldet `setup_local.sh`
beim Wiedererkennen dieses Werts einen Rotationshinweis.

Abhilfe (setzt die lokale Datenbank zurueck):

```bash
scripts/setup_local.sh --reset-db --yes
# oder manuell:
docker compose down -v
docker compose up --build -d
```

Dabei geht der Inhalt der **lokalen** Postgres-Datenbank verloren (nicht
Produktion). Fuer persistente Datensicherung:

```bash
docker compose exec postgres pg_dump -U tbot tbot > backup.sql
```

## 6. Redis-Container startet nicht

Wurde in `v2.3.0` auf POSIX-sh Entrypoint umgestellt. Wenn du eine alte
Version des Repositories benutzt:

```bash
git pull
docker compose up --build -d --force-recreate redis
docker compose logs redis
```

Korrekte Zeile im Log:

```
[redis] maxmemory=...mb policy=noeviction io-threads=...
```

## 7. `scripts/setup_local.sh` vs. `docker compose up`

| Setup | `.env` | `.env.local` | Hardware-Tuning |
|---|---|---|---|
| `docker compose up` | wird automatisch gelesen | nein | nur, wenn der `tuner`-Service laeuft |
| `scripts/setup_local.sh` | unberuecksichtigt | explizit via `--env-file` | wird vor dem Start in `.env.local` geschrieben |

Nicht beide gleichzeitig nutzen - es kann zu Passwort-Konflikten kommen
(siehe Punkt 5). Empfehlung: `scripts/setup_local.sh` als Standard.

Warum nackte `docker compose`-Kommandos ohne `--env-file` abbrechen, welche
drei Abhilfen es gibt und welches Kommando welche Wirkung hat:
[COMPOSE_ENV_FILE.md](COMPOSE_ENV_FILE.md).

## 8. Passwoerter aendern / Secret-Rotation

Für **App-Secrets** (`SECRET_KEY`, `PASSPHRASE`):

```bash
# Neue private Werte in derselben Env-Datei speichern:
${EDITOR:-nano} .env.local
# Environment-Änderungen übernehmen (restart allein reicht dafür nicht):
docker compose --env-file .env.local up -d --force-recreate web backtest-worker
# Falls der Scheduler verwendet wird, auch diesen neu erstellen:
# docker compose --env-file .env.local --profile scheduler up -d --force-recreate scheduler
```

Alle App-Prozesse müssen dieselben Secrets erhalten. Eine neue Passphrase widerruft alte Gate-Freigaben; ein neuer Signierschlüssel invalidiert zusätzlich die Django-Login-Cookies. Beim Upgrade auf 2.4.4 werden alte boolesche Gate-Freigaben bereits abgelehnt. Bei zuvor öffentlichen Default-Secrets beide Werte rotieren. Auf Render die Service-Secrets aktualisieren und alle betroffenen Services neu deployen.

**`POSTGRES_PASSWORD` lokal rotieren (ab 2.4.11):** Zeile in `.env.local`
leeren, `scripts/setup_local.sh` erneut ausführen (erzeugt einen neuen
Zufallswert) und danach `scripts/setup_local.sh --reset-db --yes`, damit das
Volume mit dem neuen Passwort initialisiert wird. Beide Schritte löschen die
lokale Datenbank; ohne Volume-Reset bleibt das alte Passwort aktiv und Web/
Worker laufen in einen Passwort-Mismatch (siehe Punkt 5).

**Kein Volume-Reset für App-Secrets.** Ein `POSTGRES_PASSWORD`-Wechsel erfordert eine separate Datenbank-Passwortänderung durch den DB-Administrator sowie die passende Verbindungskonfiguration. `--reset-db --yes` löscht lokale Daten und ist nur für ausdrücklich entbehrliche Entwicklungsdaten gedacht, nicht für Secret-Rotation in Produktion.

## 9. Render-Free-Simulation lokal testen

```bash
scripts/setup_local.sh --render-free-simulation --reset-db --yes
```

Setzt in `.env.local`:

```env
RENDER=True
RENDER_SIMULATION=True
WEB_CPUS=0.10
```

Zurueck zur normalen lokalen Konfiguration:

```bash
scripts/setup_local.sh --no-up
# RENDER=False in .env.local sicherstellen
docker compose up -d --force-recreate web backtest-worker
```

## 10. Logs und Status

```bash
docker compose logs -f web                       # App live
docker compose logs -f backtest-worker            # Celery
docker compose logs -f postgres redis             # Infrastruktur
docker compose exec postgres psql -U tbot -d tbot # DB-Shell
docker compose exec redis redis-cli info memory    # Redis-Speicher
docker compose exec web python manage.py shell     # Django-Shell
docker stats                                      # Live-Resourcen
```

## 11. Hardware-/Tuning-Werte anpassen

Die automatisch berechneten Werte stehen in `config/hardware.env` und im
Container unter `/tbot-runtime/tuning.env`:

```bash
docker compose cp tuner:/tbot-runtime/hardware-report.txt - | less
docker compose exec web cat /tbot-runtime/tuning.env
```

Einzelne Werte in `.env.local` ueberschreiben (z.B. `REDIS_MAXMEMORY_MB=64`)
und den Stack neu starten:

```bash
docker compose up -d --force-recreate redis web backtest-worker
```

## 12. Sauberes Zuruecksetzen

```bash
docker compose down -v       # Container + Volumes (Datenbank!) loeschen
docker image rm t-bot-local-app redis:7.4-alpine postgres:17-alpine  # optional Images
git clean -fdx               # ACHTUNG: entfernt alle unversionierten Dateien
```

## 13. Tests ausfuehren

```bash
# Shell-Test-Suite
bash tests/run_tests.sh

# Shellcheck
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh \
  tests/*.sh tests/fixtures/mock-bin/* scripts/setup_local.sh \
  scripts/install_system_dependencies.sh

# Distro-Smoke-Tests (erfordert Docker)
tests/distro_smoke_test.sh
```

## 14. Haeufige Irrtuemer

1. **`tuner` ist `Exited (0)`** - das ist beabsichtigt.
2. **Port-Spalte `8369/tcp` ohne `0.0.0.0:8369->`** = der Port ist nicht
   auf den Host veroeffentlicht. In `docker-compose.yml` muss unter `web`
   `ports: ["${WEB_PORT:-8369}:8369"]` stehen.
3. **`0.0.0.0` im Container** heisst "im Container-Netzwerk"; auf dem Host
   ist der Dienst ueber `localhost` erreichbar, solange Docker den Port
   forwardet.
4. **302 auf `/gate/`** ist kein Fehler, sondern die Passphrase-Schutz-
   middleware.
5. **`docker compose up` ohne `--build`** nutzt ein altes Image; nach
   `git pull` immer `--build` verwenden.
6. **Die Startseite braucht Datenbank/Redis**; `/health/` ist davon
   unabhaengig und der beste erste Erreichbarkeitstest.
7. **„required variable SECRET_KEY is missing a value"** ist kein Defekt:
   Compose lädt automatisch nur `.env`, das Setup schreibt `.env.local`.
   Abhilfe: [COMPOSE_ENV_FILE.md](COMPOSE_ENV_FILE.md).
8. **`docker compose restart` nach Env-Änderungen** übernimmt die neuen Werte
   nicht - es startet nur den Prozess im bestehenden Container neu. Richtig
   ist `up -d --force-recreate` ([COMPOSE_ENV_FILE.md](COMPOSE_ENV_FILE.md)).

## 15. Warum liefert der Gate HTTP 429 hinter einem Proxy?

Alle Auth-POSTs (auch erfolgreiche) teilen ein Limit von fünf Versuchen pro IP, 15 Minuten und Web-Prozess. Ohne `RATE_LIMIT_TRUSTED_PROXIES` wird ausschließlich `REMOTE_ADDR` verwendet. Hinter einem Reverse-Proxy können dadurch alle Nutzer denselben Zähler teilen. Nur tatsächlich kontrollierte Proxy-Peer-IPs/CIDRs eintragen und sicherstellen, dass dieser Proxy `X-Forwarded-For` bereinigt oder die tatsächliche Client-IP anhängt. Client-gelieferte Header alleine dürfen keine neue Identität erzeugen. Bei mehreren Web-Prozessen ein zusätzliches gemeinsames Limit einsetzen.

## 16. API-Antworten 400/404 und `/readyz/` verstehen (seit 2.5.0)

- `400 {"error": "config_id ist keine Zahl"}` auf `/api/logs/…` o. a. JSON-Endpunkten: die `config_id` im Query war nicht numerisch (manuell editierte URL, abgelaufener Lesezeichenpfad). Vor 2.5.0 beantwortete das die App mit einem 500er und Log-Rauschen; die Ursache bleibt reiner Eingabefehler – URL aus dem Dashboard neu aufrufen.
- `404`: die ID existiert nicht oder gehört einem anderen Konto. Bewusst kein Hinweis, welcher der beiden Fälle es ist.
- `400 {"error": "config_id fehlt"}` auf `/api/bot/status/`: Aufrufer muss `?config_id=<id>` mitschicken (das Dashboard tut das automatisch).
- `/readyz/` liefert dauerhaft `503`: Die Web-Datenbank ist nicht erreichbar; `Retry-After: 5` gibt das Prüfintervall vor. Der Container läuft weiter und `/health/` bleibt `200` – erst `200` auf `/readyz/` bedeutet „DB erreichbar, Traffic kann fließen". Diagnose: `docker compose logs web`, `docker compose exec postgres pg_isready`.

## 17. Fehler-Log bei Bitunix: „Referenz #N", leere Meldungen und Kursabruf-Timeouts (seit 2.5.1)

- **Jeder Eintrag zeigt jetzt eine Referenznummer** („Referenz #N"): das ist die ID des Eintrags im technischen Server-Log. Zu jedem Eintrag gehören drei Erklärungen – *Was ist passiert*, *Ursache*, *Was tun* – plus sichere Kurzdaten (Exchange, betroffene Symbole, Ban-Zeitpunkt). Rohe Meldungen und Tracebacks bleiben staff-only; bei anhaltenden Problemen Referenznummer und Konfiguration dem Betreiber nennen.
- **„Der Bot konnte die aktuellen Kurse von der Exchange nicht abrufen – oder nicht rechtzeitig":** die Exchange antwortet zu langsam, ist überlastet, hat eine Anfragesperre (Rate-Limit) ausgesprochen oder ist vom Server aus nicht erreichbar. Der Bot wiederholt automatisch mit wachsender Wartezeit (5–300 s). Vor 2.5.1 konnte genau dieser Fall einen **leeren** Eintrag hinterlassen (der interne Timeout-Exception-Typ hat keinen Text) und alle ~2 s gegen die Exchange hämmern; beides ist behoben ([BUG-27](../findings/BUG-27-empty-timeout-error-log.md)).
- **Bitunix-Spot-Konfiguration ohne Kurs/Scan-Ergebnis:** die dokumentierte Spot-Kline-API liefert keine 24h-Volumendaten. Der Marktscan misst dafür die absolute Orderbuch-Tiefe (mindestens 100.000 USDT innerhalb ±2 %); leere Vorlagen zeigen die nächsten Ausschlüsse mit Grund. Ist ein Pair ohne Statusfeld gelistet, wird die Konfiguration bewusst abgelehnt statt fehlerhafte Symbole freizugeben ([BUG-26](../findings/BUG-26-bitunix-spot-scanner-empty.md)).
- **„Ein oder mehrere Handelspaare sind bei der Exchange nicht (mehr) gelistet":** Symbol fehlerhaft, bei der Exchange nicht existent oder für die Marktart (Spot/Futures) nicht verfügbar – Konfiguration bearbeiten, Symbole über die Vorschläge korrigieren, Bot erneut starten.
