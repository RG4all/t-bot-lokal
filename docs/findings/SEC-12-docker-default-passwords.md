# SEC-12 – Keine Standard-Passwörter im Compose-Setup

- **Finding:** `DockerDefaultPasswords` (Prompt 11 / Security-Audit §2.12)
- **Status:** **Fixed**
- **Release:** **2.4.11** · **Datum:** 2026-09-08
- **Einstufung:** LOW – Security
- **Geprüfter Ausgangsstand:** 2.4.10 / `907a375b4dec1beaa3f557955692beb9a83c2410`
- **Fix-Commit:** [`c5fac42` – `fix(security): remove default passwords from docker-compose`](https://github.com/RG4all/t-bot-lokal/commit/c5fac42)

## Befund und Root Cause

`docker-compose.yml` interpolierte App- und Datenbank-Secrets mit
`${VAR:-default}`-Fallbacks. Dadurch startete der lokale Stack auch ohne
gesetzte Env-Datei – still und mit öffentlich bekannten Zugangsdaten:

- `PASSPHRASE: ${PASSPHRASE:-local-t-bot}` (App-Gate, historischer Stand)
- `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-tbot-local-password}` – im
  Postgres-Service **und** in der daraus gebauten `DATABASE_URL`

Root Cause ist nicht der einzelne Default-Wert, sondern das Interpolations-
muster: Secrets durften optional sein, mit einem im öffentlichen Repository
liegenden Fallback. Jeder Leser der Public-Repo kannte damit Zugangsdaten
für Datenbank und Gate, und eine vergessene Konfiguration fiel lautlos auf
diese Werte zurück. `SECRET_KEY` und `PASSPHRASE` waren bereits seit 2.4.4
Pflichtwerte (`${VAR:?...}`); das gleiche Muster fehlte noch für
`POSTGRES_PASSWORD`.

## Fix und notwendiger Umfang

| Komponente | Änderung |
|---|---|
| `docker-compose.yml` | `POSTGRES_PASSWORD` ist Pflichtwert (`${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in the env file or run scripts/setup_local.sh}`) – sowohl im `postgres`-Service als auch in der `DATABASE_URL` des gemeinsamen App-Environment. Nur `POSTGRES_USER` und `POSTGRES_DB` behalten lokale Defaults; sie sind keine Secrets. |
| `scripts/setup_local.sh` | Erzeugt für neue Setups ein zufälliges, 48-stelliges `POSTGRES_PASSWORD` in `.env.local` (Modus 0600) statt des öffentlichen Defaults. Bestehende Werte bleiben beim Retuning erhalten (bestehende Postgres-Volumes akzeptieren nur ihr Init-Passwort). Erkennt das Skript den früher öffentlichen Wert, weist es auf die Rotation mit `--reset-db` hin – via SHA-256-Vergleich, ohne den Wert erneut zu veröffentlichen. |
| `.env.example` / `.env.docker.example` | Secrets stehen als **leere Platzhalter** mit Erzeugungshinweisen (`python -c "import secrets; ..."`). Unverändert kopierte Beispiele scheitern damit sicher beim Compose-Start statt öffentliche Werte zu setzen. |
| `config.template` / `install.sh` | Kommentierte `DATABASE_URL`-Beispiele verwenden einen generischen Platzhalter statt des früheren Standard-Passworts; `install.sh` schreibt weiterhin keine hartcodierten Secrets. |

`${VAR:?Fehlermeldung}` bricht die Compose-Interpolation ab, sobald die
Variable fehlt **oder leer ist**. Ein vergessenes Env-File liefert dadurch
eine klare Fehlermeldung statt eines Starts mit bekannten Zugangsdaten.

### Abweichungen vom historischen Lösungsvorschlag

- Der Vorschlag sah `POSTGRES_PASSWORD=change-me-to-a-secure-password` in
  `.env.example` vor. Ein solcher Platzhalter wäre ein benutzbarer,
  öffentlich sichtbarer Wert geblieben. Stattdessen halten die Beispiele
  Secrets als leere Zeilen fest – das unveränderte Kopieren schlägt fehl,
  wie bei `SECRET_KEY`/`PASSPHRASE` seit 2.4.4.
- Die vorgeschlagene Zeile allein (`POSTGRES_PASSWORD: ...` im
  Postgres-Service) hätte eine zweite, leicht übersehbare Fallback-Stelle
  offengelassen: die `DATABASE_URL` im App-Environment. Beide Stellen
  verwenden denselben Pflichtwert.
- Ergänzend wurde das Root-Cause-Umfeld geschlossen: das Setup-Skript, das
  den öffentlichen Default bisher aktiv in `.env.local` schrieb.

Keine neue Runtime-Abhängigkeit, keine Migration, keine Änderung an
`trading_bot_project/settings.py` oder App-Verhalten. Bestehende
`.env.local`-Dateien funktionieren unverändert weiter.

## Regressionstests

Neu: `tests/test_compose_security.sh` mit **25 Assertions**:

- Pflicht-Interpolation (`${VAR:?...}`) für `SECRET_KEY`, `PASSPHRASE` und
  `POSTGRES_PASSWORD` in `docker-compose.yml`.
- Keine `${VAR:-...}`-Default-Fallbacks mehr für die drei Secrets.
- Kein öffentliches Standard-Passwort in den 13 ausgelieferten
  Konfigurations-/Skriptdateien (Compose, Env-Beispiele, `config.template`,
  `install.sh`, `setup_local.sh`, Entrypoints). Findings-Dokumente in
  `docs/` zitieren die historischen Werte bewusst als Befund; die
  Negativkontrolle in `trading/tests/test_passphrase.py` prüft, dass die
  frühere Default-Passphrase am Gate **abgelehnt** wird.
- Leere Platzhalter für alle drei Secrets in beiden Env-Beispielen.
- Optional mit Docker: `docker compose config` scheitert ohne gesetzte
  Secrets und löst mit ihnen auf (ohne Docker übersprungen, wie in
  `tests/distro_smoke_test.sh`).

Erweitert: `tests/test_setup_local.sh` – zufälliges DB-Passwort statt
öffentlichem Standard, Erhalt beim Retuning, unterschiedliche Passwörter
pro Setup.

**Rot → grün:** Vor dem Fix schlugen 7 Assertions der neuen
Compose-Gruppe (fehlende Pflicht-Interpolation, vorhandene Fallbacks,
Default in Compose/`config.template`/`install.sh`/`setup_local.sh`,
konkreter Beispielwert) und 3 von 9 Assertions in
`test_setup_local.sh` fehl. Nach dem Fix sind alle grün. Die
`${VAR:?}`-Abbruchsemantik wurde zusätzlich mit der POSIX-Expansion
nachgestellt (Abbruch bei fehlender und bei leerer Variable, korrekte
Auflösung mit gesetztem Wert).

## Lokale Validierung

Python **3.11.2**, Django **5.2.17**, Ruff, ShellCheck **0.11.0**:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
./tests/run_tests.sh
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test --noinput
python -m pip check
```

- **8/8 Shell-Testgruppen** (bisher 7 + neue `test_compose_security`),
  **210 Django-/Python-Tests**, Ruff und ShellCheck ohne Befund.
- Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden.

## CI und Auslieferung

Wie in [SEC-10](SEC-10-information-disclosure.md#ci-und-auslieferung)
dokumentiert, ist im Repository kein GitHub-Actions-Anwendungstestworkflow
versioniert, weil die Arena-GitHub-App die `workflows`-Berechtigung fehlt.
Die Auslieferung erfolgt auf Basis der oben genannten lokalen Prüfnachweise;
ein erfolgreicher GitHub-Anwendungstest-CI-Lauf wird nicht behauptet.

**Prüfgrenze:** Docker steht in der lokalen Prüfumgebung nicht zur
Verfügung. Der Interpolationstest ist daher statisch (Quell- und
Musterprüfung) plus die optionale `docker compose config`-Prüfung der
Testgruppe, die auf Docker-Systemen automatisch greift. Die
Abbruchsemantik von `${VAR:?...}` ist POSIX-definiert und wurde außerhalb
des Containers nachgestellt. Ein vollständiger Containerlauf
(`scripts/setup_local.sh` bis zur Web-Health) bleibt Bestandteil des
üblichen lokalen Setup-Flows auf dem Zielrechner.

### Upgrade

- Bestehende `.env.local`-Dateien bleiben vollständig nutzbar; das Setup
  bewahrt `POSTGRES_PASSWORD` beim Retuning.
- Wer noch das frühere öffentliche DB-Passwort verwendet: Zeile
  `POSTGRES_PASSWORD` in `.env.local` leeren, `scripts/setup_local.sh`
  erneut ausführen (neuer Zufallswert) und danach
  `scripts/setup_local.sh --reset-db --yes` – das löscht die lokale
  Datenbank (siehe [FAQ](FAQ.md#8-passwoerter-aendern--secret-rotation)).
- Manuelles Compose benötigt jetzt zwingend gesetzte `SECRET_KEY`-,
  `PASSPHRASE`- und `POSTGRES_PASSWORD`-Werte in der Env-Datei
  (`.env` bzw. `--env-file`); Vorlage mit Hinweisen:
  [`.env.docker.example`](../.env.docker.example).
- Nach dem Deploy `/health/` auf **2.4.11** prüfen; Postgres und Redis
  veröffentlichen weiterhin keine Hostports.
