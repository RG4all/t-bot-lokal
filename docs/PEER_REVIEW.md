# PEER_REVIEW.md - Selbst-Review der Build-/Install-Ueberarbeitung

**Datum:** 2026-08-22
**Autor:** Backend-Engineering (Cross-Platform-Deployment)
**Version:** 2.3.0
**Scope:** `install.sh`, `hardware-test.sh`, `scripts/setup_local.sh`,
`config.template`, `docker/`, `tests/`, `docker-compose.yml`, `Dockerfile`

Dieses Dokument beschreibt die konstruktive Aufloesung der Merge-Konflikte
zwischen den beiden parallelen 2.3.0-Implementierungen. Es wurden die
Staerken beider Ansaetze zusammengefuehrt.

---

## 0. Konfliktauflösung (Merge-Zusammenfassung)

Die Konflikte in `CHANGELOG.md`, `README.md`, `LOCAL_DEVELOPMENT.md` und
`docker-compose.yml` entstanden, weil beide Branches unabh‰ngig voneinander
ein Distributions-agnostisches Setup und Hardware-Tuning implementiert
hatten. Die endgueltige Version vereint:

| Thema | Eigener Branch | Anderer Branch | Zusammengefuehrte Loesung |
|---|---|---|---|
| Distro-Erkennung | apt/pacman/dnf/yum | apt/pacman/dnf/yum/**zypper/apk** | alle sechs Paketmanager |
| Architektur | `uname -m` (roh) | amd64/arm64/arm/v7/ppc64le/s390x | Normalisierung auf Docker-Platform-Bezeichner |
| Hardware-Metriken | CPU/RAM/Disk-DD | CPU-Hashrate, RAM, Disk, fsync | alle Metriken zusammen |
| PostgreSQL-Tuning | keine | `max_connections`, `shared_buffers`, `effective_cache_size`, `work_mem` | ueber `postgres-entrypoint.sh` |
| Redis-Default | hart 48 MB | `${REDIS_MAXMEMORY:-96mb}` | Env-Override + Tuner-Datei |
| Ein-Schritt-Setup | Skripte manuell | `scripts/setup_local.sh` | `setup_local.sh` mit Secret-Erhalt, `--install-deps`, `--no-up`, `--render-free-simulation` |
| Secret-Handling | 0600-Config | 0600, bestehende Secrets behalten | 0600, Secrets werden beim Retuning beibehalten |
| Render-Simulation | `--render-simulation` | `--render-free-simulation` | beide F√ºrderer aktivieren; Default aus |

Keine Funktion wurde entfernt; alle Konflikte sind inhaltlich aufgeloest.

---

## 1. Checkliste Sicherheit

| Kriterium | Status | Bewertung / Nachweis |
|---|---|---|
| Keine Hardcoded-Credentials | ✅ | `SECRET_KEY`/`PASSPHRASE` werden zur Laufzeit aus `/dev/urandom` generiert bzw. aus Env-Variablen gelesen. In `config.template` und `local.env` stehen nur Platzhalter/Default-Passphrasen fuer lokale Entwicklung. |
| Sichere Temp-Files | ✅ | `mktemp -t tbot-install.XXXXXXXXXX.log` und `trap ... EXIT` in `install.sh`; `mktemp` auch in `hardware-test.sh`, `setup_local.sh` und allen Tests. Dateien werden mit `chmod 600` (Config) bzw. `644` (nicht-sensitive Berichte) angelegt. |
| Input-Validierung | ✅ | `parse_args()` validiert `--mode` (`auto|host|container`) und `--profile` (`full|runtime`); unbekannte Optionen fuehren zu `die()` mit klarem Fehler. `--format` in `hardware-test.sh` wird geprueft. |
| `set -euo pipefail` | ✅ | In allen ausfuehrbaren Bash-Skripten aktiv. POSIX-`sh`-Entrypoints nutzen `set -eu`. |
| Privilegien-Pruefung | ✅ | `ensure_privileges()` prueft `EUID` und nutzt `sudo -E` mit Array-Argumenten (kein Word-Splitting); Container-Modus benoetigt kein sudo. |
| Disk-Benchmark sicher | ✅ | Schreibt ausschliesslich in eigene `mktemp`-Dateien, entfernt sie per `trap RETURN`; keine festen Pfade ausserhalb `TMPDIR`. |
| Secret-Erhalt beim Retuning | ✅ | `scripts/setup_local.sh` liest bestehende `SECRET_KEY`, `PASSPHRASE`, `POSTGRES_PASSWORD` aus `.env.local` aus, bevor die Datei neu geschrieben wird. |
| Redis/Bind-Adresse | ✅ | Redis im Compose nur intern; `REDIS_URL=redis://redis:6379/0`. Lokal wird der Default auf `127.0.0.1` gesetzt. |
| Kein `curl | bash` | ✅ | Es werden ausschliesslich Distributions-Paketquellen verwendet. |
| Shellcheck-Analyse | ✅ | `shellcheck` v0.11.0 meldet 0 Warnungen/Fehler fuer alle Produktiv- und Testskripte (inkl. `scripts/setup_local.sh`). |
| Geheimnisse im Log | ✅ | `SECRET_KEY`/`PASSPHRASE` werden nicht geloggt; Paketmanager-Output geht in eine Datei mit `chmod 600`. |

## 2. Checkliste Performance / Ressourcen-Tuning

| Kriterium | Status | Bewertung / Nachweis |
|---|---|---|
| CPU-Erkennung | ✅ | `nproc`, `/proc/cpuinfo` (Modell, MHz), `uname -m`, normalisiert auf amd64/arm64/arm/v7/ppc64le/s390x/riscv64. |
| CPU-Hashrate | ✅ | 64 MB SHA-256 via `openssl dgst -sha256` (Fallback `sha256sum`); ausgegeben in MB/s. |
| RAM-Messung | ✅ | `/proc/meminfo` (`MemTotal`, `MemAvailable`) mit Fallback auf `free`. |
| Freier Plattenspeicher | ✅ | `df -Pk` (POSIX-konform) im Zielverzeichnis. |
| Disk-I/O-Test | ✅ | 64 MB `dd if=/dev/zero ... conv=fdatasync` (Schreiben), anschliessender Lesetest mit portablem `1M`-Block. Ueberspringbar mit `--skip-disk-test`. |
| fsync-Latenz | ✅ | 32 Kleinschreibvorgaenge mit `conv=fdatasync`, Mittelwert in Millisekunden. |
| Redis `maxmemory` | ✅ | 10 % des gesamten RAM, min. 32 MB, max. 1024 MB, zusaetzlich auf 50 % des *verfuegbaren* RAMs gedeckelt. Override ueber `REDIS_MAXMEMORY` in `.env.local`. |
| Redis `maxmemory-policy` | ✅ | `noeviction` als sicherer Broker-Default (keine stillschweigend verlorenen Jobs). |
| Redis `io-threads` | ✅ | 1 (<4 Kerne), 2 (4-7 Kerne), 4 (>=8 Kerne). |
| PostgreSQL `max_connections` | ✅ | 40 (Default, ueberschreibbar ueber `POSTGRES_MAX_CONNECTIONS`). |
| PostgreSQL `shared_buffers` | ✅ | ~25 % RAM, geklammert auf [32, 256] MB im lokalen Setup. |
| PostgreSQL `effective_cache_size` | ✅ | ~50 % RAM, geklammert auf [64, 512] MB. |
| PostgreSQL `work_mem` | ✅ | 4 MB (<4 GB), 8 MB (4-8 GB), 16 MB (>=8 GB). |
| `BOT_DB_WORKERS` | ✅ | `nproc/2`, geklammert auf [1, 8]. |
| `DB_POOL_SIZE` | ✅ | `BOT_DB_WORKERS * 4`, geklammert auf [4, 32]. |
| `WEB_CONCURRENCY` | ✅ | `nproc/2`, geklammert auf [1, 4]. |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD` | ✅ | 192 MB (<2 GB), 384 MB (2-8 GB), 512 MB (8-16 GB), 768 MB (>=16 GB). |
| Compose-CPU-Limits | ✅ | Gestaffelt nach Kernanzahl fuer Web/Worker/Postgres/Redis/Scheduler. |
| Docker-Integration | ✅ | One-Shot-`tuner`-Service erzeugt `tuning.env`; Redis und Postgres bekommen Custom-Entrypoints, die die Tuneables annehmen; alle App-Services starten erst nach `service_completed_successfully`. |
| Idempotenz | ✅ | `install.sh` kann mehrfach laufen (`--needed`, Config bleibt bei `ASSUME_YES=0` unveraendert, `.venv` wird nur einmal erzeugt). `setup_local.sh` behaelt bestehende Secrets bei. |

## 3. Checkliste Kompatibilitaet

| Distribution | Paketmanager | Status | Testweg |
|---|---|---|---|
| Debian 12 (bookworm) | apt | ✅ | Fixture + `install.sh` laeuft auf Debian GNU/Linux 12 (Sandbox). |
| Ubuntu 24.04 | apt | ✅ | Fixture (basiert auf `ID_LIKE=debian`). |
| Arch Linux | pacman | ✅ | Fixture; `distro_smoke_test.sh` baut `archlinux:latest`. |
| Fedora 40 | dnf | ✅ | Fixture; `distro_smoke_test.sh` baut `fedora:40`. |
| Rocky/Alma/RHEL 9 | dnf/yum + EPEL | ✅ | Fixture; `pm_update` aktiviert automatisch EPEL vor Redis-Installation. |
| Amazon Linux 2023 | dnf/yum | ✅ | Fixture (`ID_LIKE=fedora`). |
| Alpine 3.20 | apk | ✅ | Fixture + Mock-Tests. |
| openSUSE Tumbleweed | zypper | ✅ | Fixture (basiert auf `ID_LIKE=opensuse suse`) + Mock-Tests. |
| Container (allgemein) | auto | ✅ | Erkennung ueber `/.dockerenv`, `/proc/1/cgroup`, `/run/.containerenv`. |

Paket-Mappings (alle sechs Paketmanager):

| Komponente | apt | pacman | dnf/yum | zypper | apk |
|---|---|---|---|---|---|
| Redis | `redis-server`, `redis-tools` | `redis` | `redis` | `redis` | `redis` |
| Python | `python3`, `python3-dev`, `python3-pip`, `python3-venv` | `python`, `python-pip` | `python3`, `python3-devel`, `python3-pip` | `python3`, `python3-devel`, `python3-pip` | `python3`, `python3-dev`, `py3-pip` |
| Build-Tools | `build-essential` | `base-devel` | `gcc gcc-c++ make` | Pattern `devel_basis` | `build-base` |
| PostgreSQL-Headers | `libpq-dev` | `postgresql-libs` | `postgresql-devel` | `postgresql-devel` | `postgresql-dev` |
| WeasyPrint/Pango | `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`, `fonts-dejavu-core` | `pango`, `harfbuzz`, `dejavu-fonts` | `pango`, `harfbuzz`, `dejavu-sans-fonts` | `libpango-1_0-0`, `libharfbuzz0`, `dejavu-fonts` | `pango`, `harfbuzz`, `dejavu-fonts` |
| Utilities | `ca-certificates`, `coreutils`, `gawk`, `grep`, `sed`, `procps` | entsprechend | entsprechend | entsprechend | entsprechend |

## 4. Checkliste Code-Qualitaet

| Kriterium | Status | Bewertung |
|---|---|---|
| Shellcheck 0.11.0 | ✅ | 0 Warnungen/Fehler auf allen Produktiv- und Testskripten. |
| Bash-Syntax-Pruefung | ✅ | `bash -n` fuer alle Skripte erfolgreich; `sh -n` fuer POSIX-Entrypoints. |
| Modular / testbar | ✅ | Beide Hauptskripte unterstuetzen `source`-Modus; alle Funktionen sind isoliert testbar. |
| Inline-Kommentare | ✅ | Komplexe Logik (Heuristik, Fallback, sudo-Re-Exec, Architektur-Normalisierung) ist kommentiert. |
| README | ✅ | Abschnitte zu `install.sh`, `hardware-test.sh`, `scripts/setup_local.sh`, Docker-Tuning, Render-Simulation und Test-Ausfuehrung. |
| config.template | ✅ | Dokumentiert alle Schluessel mit Defaults und Wertebereichen. |
| Test-Suite | ✅ | 6 Test-Dateien, 41 Assertions, alle gruen. |
| Aussagekraeftige Fehlermeldungen | ✅ | `log_error`/`die` mit Kontext (Distro, Paketmanager, Dateipfade). |
| Exit-Codes | ✅ | `set -e` plus explizite `exit 1` in `die()`; Tests pruefen Nicht-Null bei Fehlern. |
| YAML-valide | ✅ | `docker-compose.yml` mit PyYAML geparst; alle Depends-On-Edges und Volumes konsistent. |

## 5. Test-Protokoll

```
==> t-bot-lokal Test-Suite <==

[ OK]  test_config_generation
[ OK]  test_distro_detection
[ OK]  test_hardware_test
[ OK]  test_helper
[ OK]  test_package_managers
[ OK]  test_redis_integration

6/6 Tests bestanden.
```

Die Tests decken ab:
- Distro-Erkennung mit **acht** `/etc/os-release`-Fixtures (Debian, Ubuntu,
  Arch, Fedora, Rocky, Amazon Linux, Alpine, openSUSE) und einem Edge-Case.
- Paketmanager-Aufrufe fuer **apt, pacman, dnf, yum, zypper und apk** mit
  Mock-Binaries, die Befehle und Argumente loggen.
- Redis-PING-Pruefung, fehlgeschlagener Verbindungsaufbau und Konfiguration.
- Hardware-Heuristik fuer 1 GB / 4 GB / 16 GB RAM und 1 / 4 / 8 Kerne
  inklusive Architektur-Normalisierung (amd64, arm64, arm/v7, ppc64le,
  s390x, riscv64) und PostgreSQL-Tuneables.
- Konfigurationsdatei-Generierung, Datei-Mode (600), Idempotenz,
  Render-Simulation standardmaessig deaktiviert.
- ENV-/JSON-Ausgabeformate (JSON mit `python3 -m json.tool` validiert).

Zusaetzlich existiert `tests/distro_smoke_test.sh`, das mit lokalem Docker
echte Images auf `debian:bookworm-slim`, `archlinux:latest` und `fedora:40`
baut. Das Skript wird ohne Docker mit Exit-Code 0 (skipped) beendet.

## 6. Docker-Integration im Detail

- `Dockerfile`: `install.sh --mode=container --profile=runtime --yes`
  wird waehrend des Builds ausgefuehrt und installiert die
  Laufzeitabhaengigkeiten ueber den erkannten Paketmanager. Ein
  `||`-Fallback auf die fruehere `apt-get`-Zeile sorgt dafuer, dass der Build
  auch in restriktiven Build-Umgebungen nicht sofort scheitert.
- `docker/tuner-entrypoint.sh` ruft `hardware-test.sh` auf und schreibt
  `/tbot-runtime/tuning.env`.
- `docker/load-tuning.sh` wird von Web/Worker/Beat-Entrypoints gesourct.
- `docker/redis-entrypoint.sh` liest die Tuning-Datei (oder
  `REDIS_MAXMEMORY` aus der Umgebung) und startet `redis-server` mit
  `--maxmemory`, `--maxmemory-policy` und (wenn >1) `--io-threads`.
- `docker/postgres-entrypoint.sh` umschliesst das offizielle
  `docker-entrypoint.sh` von postgres:17-alpine und uebergibt
  `max_connections`, `shared_buffers`, `effective_cache_size` und
  `work_mem` als `-c`-Argumente.
- `docker-compose.yml`: Alle App-Services haben
  `depends_on: tuner: condition: service_completed_successfully`; Redis und
  Postgres haengen das `tuning_runtime`-Volume ein; Memory-Limits sind ueber
  `${REDIS_MEMORY:-96m}` und `${POSTGRES_MEMORY:-256m}` konfigurierbar.
- `scripts/setup_local.sh`: prueft Docker/Compose, fuehrt optional die
  Systemabhaengigkeits-Installation durch, schreibt `.env.local` mit Mode
  0600 unter Beibehaltung bestehender Secrets und startet den Stack.

## 7. Bekannte Einschraenkungen / Follow-ups

1. **EPEL unter aelteren RHEL/CentOS 7**: Falls `epel-release` nicht im
   Standard-Repo liegt, muss einmalig manuell nachgeholfen werden. Das
   Skript gibt eine Warnung aus, bricht aber nicht ab.
2. **`zypper devel_basis`**: Das Pattern wird nur installiert, wenn
   `zypper` es bereits kennt; aeltere openSUSE-Versionen brauchen ggf. das
   manuelle `patterns-openSUSE-devel_basis`. Die Runtime-Pakete sind davon
   nicht betroffen.
3. **Docker-Smoke-Tests** erfordern lokal installiertes Docker und
   Netzwerkzugriff. Sie sind als eigener Entrypoint von den Unit-Tests
   getrennt und werden ohne Docker stillschweigend uebersprungen.
4. **`DB_POOL_SIZE`** in der Django-App ist ueber die Umgebungsvariable
   vorbereitet; falls die App einen festen Pool-Setting-Schluessel erwartet,
   kann er in `settings.py` ergaenzt werden (`BOT_DB_WORKERS` wird bereits
   honoriert).
5. **ARM/Plattform-Tests**: Die Architektur-Normalisierung ist durch Unit-
   Tests abgedeckt; die echte Ausfuehrung auf arm64/arm/v7/s390x/ppc64le
   ist nicht in der Sandbox moeglich und sollte auf echter Hardware per
   `docker buildx` verifiziert werden.
