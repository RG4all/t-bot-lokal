# `docker compose` bricht ab: „required variable … is missing a value"

> Betriebsthema: Env-Datei-Zuordnung der Compose-Kommandos im lokalen Stack.
> Kurzantwort auf „muss ich nach dem Setup neu laden?": **Nein.**
> Der Abbruch ist kein Defekt: Compose scheitert beim Parsen von
> `docker-compose.yml`, bevor es auch nur einen Container anfasst. Es wurde
> nichts verstümmelt und nichts halbfertig gestartet.

## 1. Symptom

**Jedes** `docker compose`-Kommando bricht sofort ab — auch reine Lesebefehle
wie `ps`, `logs`, `config` und sogar `restart`/`down`:

```console
$ docker compose ps
error while interpolating services.web.environment.SECRET_KEY:
required variable SECRET_KEY is missing a value: Set SECRET_KEY in the env file or run scripts/setup_local.sh
```

Die erste Zeile variiert je nach Compose-Version (ältere Versionen melden
zusätzlich `invalid interpolation format for services.…environment.… You may
need to escape any $ with another $`), die Kernaussage ist immer dieselbe:
eine `${VAR:?…}`-Interpolation findet keinen Wert. Betroffen sind im lokalen
Stack drei Variablen: `SECRET_KEY`, `PASSPHRASE`, `POSTGRES_PASSWORD`.

## 2. Ursache

Drei Fakten, die zusammen den Abbruch ergeben:

1. `docker-compose.yml` verlangt die privaten Werte hart — bewusst **ohne**
   Default-Fallback, damit kein öffentlich bekanntes Passwort mehr als
   Fallback dienen kann (siehe
   [SEC-12](../findings/SEC-12-docker-default-passwords.md)):

   ```yaml
   SECRET_KEY: ${SECRET_KEY:?Set SECRET_KEY in the env file or run scripts/setup_local.sh}
   PASSPHRASE: ${PASSPHRASE:?Set PASSPHRASE in the env file or run scripts/setup_local.sh}
   DATABASE_URL: postgresql://${POSTGRES_USER:-tbot}:${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD …}@postgres:5432/…
   ```

   `${VAR:?Meldung}` heißt: Wert fehlt **oder ist leer** → Abbruch mit genau
   dieser Meldung.

2. Compose lädt für die Interpolation automatisch **nur** `.env` im
   Projektverzeichnis. Reihenfolge der Quellen (höchste zuerst):
   Shell-Umgebung → `--env-file` → `.env` im Projektverzeichnis.
   `.env.local` kennt Compose von sich aus nicht.

3. `scripts/setup_local.sh` schreibt die Secrets nach `.env.local` (Mode
   `0600`, git-ignored) und ruft Compose intern selbst mit
   `--env-file .env.local` auf. In deiner interaktiven Shell fehlt dieser
   Verweis — deshalb sehen dieselben Kommandos dort „leere" Secrets.

Kurz: Das Setup übergibt die Datei explizit, deine Shell nicht. Kein
Kompositionsfehler, keine defekte Installation.

## 3. Muss ich nach dem Setup neu starten?

**Nein.** `scripts/setup_local.sh` führt `docker compose --env-file .env.local
up --build -d` selbst aus und wartet danach bis zu 90 Sekunden auf den
Health-Status von `web`. Ist der Stack dort `healthy`, läuft er mit dem
frischen Build; die danach fehlgeschlagenen manuellen Kommandos haben daran
nichts geändert, weil sie bereits beim Parsen abgebrochen sind.

Nachprüfen (mit der Datei, die das Setup benutzt):

```bash
docker compose --env-file .env.local ps
curl -i http://127.0.0.1:8369/health/
```

Nur nach Code-Änderungen (`git pull`, eigene Patches) ist ein Neubau nötig —
dann mit `--build`, siehe
[LOCAL_DEVELOPMENT](LOCAL_DEVELOPMENT.md#3-start).

## 4. Drei Workarounds (einen wählen)

### 4.1 Explizit übergeben — empfohlen

Nichts Neues anlegen, nichts verknüpfen; derselbe Aufruf, den das Setup
verwendet:

```bash
docker compose --env-file .env.local up -d
docker compose --env-file .env.local logs -f web
docker compose --env-file .env.local ps
```

`--env-file` ist ein globales Compose-Flag und steht **vor** dem Subkommando.
Bequem per Shell-Alias (in `~/.bashrc`/`~/.zshrc`, nur für dieses Repo sinnvoll):

```bash
alias dcomp='docker compose --env-file .env.local'
dcomp up -d --force-recreate web backtest-worker
```

### 4.2 Einmal verknüpfen — danach funktioniert nacktes `docker compose …`

```bash
ln -sf .env.local .env        # .env ist über .gitignore ausgeschlossen
docker compose ps             # funktioniert jetzt ohne Zusatzflag
```

Damit sind `.env` und `.env.local` dieselbe Datei — beide Pfade lesen
dieselben Werte, ein Auseinanderlaufen ist ausgeschlossen. Rückgängig mit
`rm .env` (löscht nur den Link, nicht die Zieldatei; `rm .env.local` **vor**
dem Entlinken wäre fatal). Wer stattdessen den manuellen Pfad aus
[FAQ-Abschnitt 1](FAQ.md#1-wie-starte-ich-den-stack) mit
`cp .env.docker.example .env` nutzt, sollte den Link vorher entfernen — zwei
getrennte Dateien mit verschiedenen `POSTGRES_PASSWORD`-Werten führen in den
Passwort-Mismatch aus [FAQ-Abschnitt 5](FAQ.md#5-container-starten-werden-aber-nicht-healthy-restart-loop).

### 4.3 Pro Terminal-Sitzung exportieren

```bash
set -a; . ./.env.local; set +a
docker compose up -d
```

Nützlich für ein einzelnes Fenster, aber mit Klarheit über die Nebenwirkung:
Die Secrets stehen danach im Prozess-Umbfeld der Shell und sind für jedes
Kindprogramm sowie über `env` sichtbar. Nicht in `.bashrc` einbauen, nicht auf
geteilten Systemen.

## 5. `restart` genügt nach Env-Änderungen nicht

`docker compose restart` startet nur den Prozess **im bestehenden Container**
neu — mit der Umgebung, die der Container beim Erzeugen erhalten hat. Eine
geänderte `.env.local` kommt so nicht an. Richtig ist Neuerstellen:

```bash
docker compose --env-file .env.local up -d --force-recreate web backtest-worker
# Scheduler nur, wenn das Profil genutzt wird:
docker compose --env-file .env.local --profile scheduler up -d --force-recreate scheduler
```

Trockencheck vorher — validiert die Datei, ohne Container anzufassen:

```bash
docker compose --env-file .env.local config -q            # still = gültig
docker compose --env-file .env.local config --environment # welche Werte Compose sieht
```

`POSTGRES_PASSWORD` ist die Ausnahme: Ein initialisiertes Postgres-Volume
behält das Passwort des ersten Laufs; dort hilft `--force-recreate` nicht,
sondern `scripts/setup_local.sh --reset-db --yes`
([FAQ-Abschnitt 5](FAQ.md#5-container-starten-werden-aber-nicht-healthy-restart-loop)).

## 6. Welches Kommando tut was

Alle Zeilen brauchen die Env-Datei, weil Compose **immer** zuerst
`docker-compose.yml` parst — auch beim reinen Lesen.

| Kommando | Wirkt auf | Env-Änderungen wirksam? |
|---|---|---|
| `config -q` / `config --environment` | nur Datei-/Variablenprüfung | — (Prüfwerkzeug) |
| `ps`, `logs`, `stats` | nichts, nur Anzeige | — |
| `up -d` | erzeugt fehlende Container neu, lässt unveränderte laufen | ja, für neu erzeugte Container |
| `up -d --build` | baut Images zusätzlich neu | ja; Pflicht nach `git pull` |
| `up -d --force-recreate <svc>` | erzwingt Neuerstellen der genannten Services | ja — der Weg für Env-Änderungen |
| `restart` | startet Prozesse im bestehenden Container neu | **nein** |
| `stop` / `start` | Container anhalten/weiterlaufen | nein |
| `down` | entfernt Container und Netzwerk (Daten bleiben im Volume) | — |
| `down -v` | entfernt zusätzlich Volumes | **löscht die lokale Datenbank** |
| `exec`, `cp` | Befehl/Datei im laufenden Container | nein (Container-Umgebung bleibt alt) |

Profile: `scheduler` ist per `--profile scheduler` auszublenden/einzublenden;
ohne das Flag fehlt der Service in `ps` und `up`, obwohl er definiert ist.

## 7. Was die Meldung **nicht** bedeutet

- **Nichts wurde zerstört.** Der Abbruch passiert vor jeder Containeraktion;
  laufende Container laufen weiter.
- **Kein Hinweis auf ein fehlerhaftes Setup.** `scripts/setup_local.sh` hat
  die Werte geschrieben — sie stehen in `.env.local`, nur nicht im
  Standard-Suchpfad deiner Shell.
- **Kein Git-/Merge-Problem.** `docker-compose.yml` ist unverändert korrekt;
  die `${VAR:?…}`-Form ist beabsichtigt.

Ein Sonderfall: `scripts/diagnose_local.sh` ruft `docker compose ps` ohne
`--env-file` auf und meldet bei fehlendem `.env` „docker compose ps konnte
nicht ausgefuehrt werden" — das ist dann dieselbe Ursache, kein zusätzliches
Problem. Mit `docker compose --env-file .env.local ps` prüfen.

## 8. Wenn `.env` und `.env.local` beide existieren

Die Shell-Umgebung gewinnt, danach `--env-file`, dann `.env`
([Abschnitt 2](#2-ursache)). Zwei Dateien mit unterschiedlichen
`POSTGRES_PASSWORD`-Werten sind die häufigste Ursache für einen
Passwort-Mismatch nach dem Start — deshalb: einen Pfad wählen, nicht mischen.
Gegenüberstellung der beiden Setup-Wege:
[FAQ-Abschnitt 7](FAQ.md#7-scriptssetuplocalsh-vs-docker-compose-up);
Rotation: [FAQ-Abschnitt 8](FAQ.md#8-passwoerter-aendern--secret-rotation).

## 9. Verwandte Dokumente

- [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md) — Setup, Start, Qualitätssicherung
- [FAQ.md](FAQ.md) — Troubleshooting, Secret-Rotation, häufige Irrtümer
- [SEC-12](../findings/SEC-12-docker-default-passwords.md) — warum es keinen
  Default-Passwort-Fallback gibt
- [REMOTE_ACCESS_TAILSCALE.md](REMOTE_ACCESS_TAILSCALE.md) — Fernzugriff; nutzt
  durchgehend `--env-file .env.local`
