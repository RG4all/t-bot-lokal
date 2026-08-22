# PEER_REVIEW.md - Selbst-Review der Build-/Install-Ueberarbeitung

**Datum:** 2026-08-21
**Autor:** Backend-Engineering (Cross-Platform-Deployment)
**Scope:** `install.sh`, `hardware-test.sh`, `config.template`, `docker/`,
`tests/`, `docker-compose.yml`, `Dockerfile`

---

## 1. Checkliste Sicherheit

| Kriterium | Status | Bewertung / Nachweis |
|---|---|---|
| Keine Hardcoded-Credentials | ✅ | `SECRET_KEY`/`PASSPHRASE` werden zur Laufzeit aus `/dev/urandom` generiert bzw. aus Env-Variablen gelesen. In `config.template` und `local.env` stehen nur Platzhalter/Default-Passphrasen fuer lokale Entwicklung. |
| Sichere Temp-Files | ✅ | `mktemp -t tbot-install.XXXXXXXXXX.log` und `trap ... EXIT` in `install.sh`; `mktemp` auch in `hardware-test.sh` und allen Tests. Dateien werden mit `chmod 600` (Config) bzw. `644` (nicht-sensitive Berichte) angelegt. |
| Input-Validierung | ✅ | `parse_args()` validiert `--mode` (`auto|host|container`) und `--profile` (`full|runtime`); unbekannte Optionen fuehren zu `die()` mit klarem Fehler. `env_int`/Integer-Pruefung in den Heuristiken. |
| `set -euo pipefail` | ✅ | In allen ausfuehrbaren Skripten aktiv. |
| Privilegien-Pruefung | ✅ | `ensure_privileges()` prueft `EUID` und nutzt `sudo -E` mit Array-Argumenten (kein Word-Splitting); Container-Modus benoetigt kein sudo. |
| Disk-Benchmark sicher | ✅ | Schreibt ausschliesslich in eine eigene `mktemp`-Datei, entfernt sie per `trap RETURN`; keine festen Pfade ausserhalb `TMPDIR`. |
| Redis/Bind-Adresse | ✅ | Redis im Compose nur intern; `REDIS_URL=redis://redis:6379/0`. Lokal wird der Default auf `127.0.0.1` gesetzt. |
| Kein `curl | bash` | ✅ | Es werden ausschliesslich Distributions-Paketquellen verwendet. |
| Shellcheck-Analyse | ✅ | `shellcheck` (v0.11.0) meldet 0 Warnungen/Fehler fuer alle Produktiv- und Testskripte. |
| Geheimnisse im Log | ✅ | `SECRET_KEY`/`PASSPHRASE` werden nicht geloggt; Paketmanager-Output geht in eine Datei mit `chmod 600`. |

## 2. Checkliste Performance / Ressourcen-Tuning

| Kriterium | Status | Bewertung / Nachweis |
|---|---|---|
| CPU-Erkennung | ✅ | `nproc`, `/proc/cpuinfo` (Modell, MHz), `uname -m`. |
| RAM-Messung | ✅ | `/proc/meminfo` (`MemTotal`, `MemAvailable`) mit Fallback auf `free`. |
| Disk-I/O-Test | ✅ | 256 MB `dd if=/dev/zero ... conv=fdatasync` (Schreiben), anschliessender Lesetest. Ueberspringbar mit `--skip-disk-test`. |
| Redis `maxmemory` | ✅ | 10 % des gesamten RAM, min. 32 MB, max. 1024 MB, zusaetzlich auf 50 % des *verfuegbaren* RAMs gedeckelt. |
| Redis `maxmemory-policy` | ✅ | `noeviction` als sicherer Broker-Default (keine stillschweigend verlorenen Jobs). |
| Redis `io-threads` | ✅ | 1 (<4 Kerne), 2 (4-7 Kerne), 4 (>=8 Kerne). |
| `BOT_DB_WORKERS` | ✅ | `nproc/2`, geklammert auf `[1, 8]`. |
| `DB_POOL_SIZE` | ✅ | `BOT_DB_WORKERS * 4`, geklammert auf `[4, 32]`. |
| `WEB_CONCURRENCY` | ✅ | `nproc/2`, geklammert auf `[1, 4]`. |
| `CELERY_WORKER_MAX_MEMORY_PER_CHILD` | ✅ | 192 MB (<2 GB), 384 MB (2-8 GB), 512 MB (8-16 GB), 768 MB (>=16 GB). |
| Compose-CPU-Limits | ✅ | Gestaffelt nach Kernanzahl fuer Web/Worker/Postgres/Redis/Scheduler. |
| Docker-Integration | ✅ | One-Shot-`tuner`-Service erzeugt `tuning.env`; Redis/Postgres/Web/Worker/Scheduler starten erst nach `service_completed_successfully`. |
| Idempotenz | ✅ | `install.sh` kann mehrfach laufen (Paketmanager `--needed`, Config bleibt bei `ASSUME_YES=0` unveraendert, `.venv` wird nur einmal erzeugt). |

## 3. Checkliste Kompatibilitaet

| Distribution | Paketmanager | Status | Testweg |
|---|---|---|---|
| Debian 12 (bookworm) | apt | ✅ | Unit-Tests mit os-release-Fixture; `install.sh` laeuft auf Debian-GNU/Linux 12 (Sandbox). |
| Ubuntu 24.04 | apt | ✅ | Fixture-basierter Unit-Test (`ID_LIKE=debian`). |
| Arch Linux | pacman | ✅ | Fixture; `tests/distro_smoke_test.sh` baut `archlinux:latest`. |
| Fedora 40 | dnf | ✅ | Fixture; `tests/distro_smoke_test.sh` baut `fedora:40`. |
| Rocky/Alma/RHEL 9 | dnf/yum + EPEL | ✅ | Fixture; `pm_update` aktiviert automatisch EPEL vor Redis-Installation. |
| Amazon Linux 2023 | dnf/yum | ✅ | Fixture (`ID_LIKE=fedora`). |
| Container (allgemein) | auto | ✅ | Erkennung ueber `/.dockerenv`, `/proc/1/cgroup`, `/run/.containerenv`. |

Die Paket-Mappings sind:

| Komponente | apt | pacman | dnf/yum |
|---|---|---|---|
| Redis | `redis-server`, `redis-tools` | `redis` | `redis` |
| Python | `python3`, `python3-dev`, `python3-pip`, `python3-venv` | `python`, `python-pip` | `python3`, `python3-devel`, `python3-pip` |
| Build-Tools | `build-essential` | `base-devel` | `gcc gcc-c++ make` |
| PostgreSQL-Headers | `libpq-dev` | `postgresql-libs` | `postgresql-devel` |
| WeasyPrint/Pango | `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`, `fonts-dejavu-core` | `pango`, `harfbuzz`, `dejavu-fonts` | `pango`, `harfbuzz`, `dejavu-sans-fonts` |
| Utilities | `ca-certificates`, `coreutils`, `gawk`, `grep`, `sed`, `procps`, `curl` | entsprechend | entsprechend |

## 4. Checkliste Code-Qualitaet

| Kriterium | Status | Bewertung |
|---|---|---|
| Shellcheck 0.11.0 | ✅ | 0 Warnungen/Fehler auf `install.sh`, `hardware-test.sh`, `docker/*.sh`, `docker-entrypoint.sh`, `tests/*.sh`, `tests/fixtures/mock-bin/*`. |
| Bash-Syntax-Pruefung | ✅ | `bash -n` fuer alle Skripte erfolgreich. |
| Modular / testbar | ✅ | Beide Skripte unterstuetzen `source`-Modus (`__INSTALL_SOURCED=1`, `__HARDWARE_TEST_SOURCED=1`), alle Funktionen sind isoliert testbar. |
| Inline-Kommentare | ✅ | Komplexe Logik (Heuristik, Fallback, sudo-Re-Exec) ist kommentiert; deutsche Kommentare passend zum bestehenden Projektstil. |
| README | ✅ | Abschnitt zu `install.sh`, `hardware-test.sh`, Docker-Tuning, Render-Simulation und Test-Ausfuehrung erweitert. |
| config.template | ✅ | Dokumentiert alle Schluessel mit Defaults und Wertebereichen. |
| Test-Suite | ✅ | 6 Test-Dateien, >30 Assertions, alle gruen (siehe Test-Protokoll unten). |
| Aussagekraeftige Fehlermeldungen | ✅ | `log_error`/`die` mit Kontext (Distro, Paketmanager, Dateipfade). |
| Exit-Codes | ✅ | `set -e` plus explizite `exit 1` in `die()`; Tests pruefen Nicht-Null bei Fehlern. |

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
- Distro-Erkennung mit sechs `/etc/os-release`-Fixtures (Debian, Ubuntu, Arch,
  Fedora, Rocky, Amazon Linux) und einem Edge-Case (fehlende Datei).
- Paketmanager-Aufrufe (apt, pacman, dnf, yum) mit Mock-Binaries, die
  Befehle und Argumente in eine Logdatei schreiben.
- Redis-PING-Pruefung und fehlgeschlagener Verbindungsaufbau.
- Hardware-Heuristik fuer 1 GB / 4 GB / 16 GB RAM und 1 / 4 / 8 Kerne.
- Konfigurationsdatei-Generierung, Datei-Mode (600), Idempotenz,
  Render-Simulation standardmaessig deaktiviert.
- ENV-/JSON-Ausgabeformate (JSON wird mit `python3 -m json.tool` validiert).

Zusaetzlich existiert `tests/distro_smoke_test.sh`, das mit lokalem Docker
echte Images auf `debian:bookworm-slim`, `archlinux:latest` und `fedora:40`
baut und `install.sh --mode=container` darin ausfuehrt. Das Skript wird ohne
Docker mit Exit-Code 0 (skipped) beendet, damit die Unit-Suite in begrenzten
Umgebungen nicht hart fehlschlaegt.

## 6. Docker-Integration im Detail

- `Dockerfile`: `install.sh --mode=container --profile=runtime --yes`
  wird waehrend des Builds ausgefuehrt und installiert die
  Laufzeitabhaengigkeiten ueber den erkannten Paketmanager. Ein
  `||`-Fallback auf die fruehere `apt-get`-Zeile sorgt dafuer, dass der Build
  auch in restriktiven Build-Umgebungen (Netzwerk-Sperren, kein sudo) nicht
  sofort scheitert.
- `docker/tuner-entrypoint.sh` ruft `hardware-test.sh` auf und schreibt
  `/tbot-runtime/tuning.env`.
- `docker/load-tuning.sh` wird von Web/Worker/Beat-Entrypoints gesourct und
  exportiert die Tuning-Variablen in die Umgebung.
- `docker/redis-entrypoint.sh` liest die Tuning-Datei und startet
  `redis-server` mit `--maxmemory`, `--maxmemory-policy` und (wenn >1)
  `--io-threads`.
- `docker-compose.yml`: Alle App-Services haben
  `depends_on: tuner: condition: service_completed_successfully` und hängen
  das `tuning_runtime`-Volume ein (Redis und App read-only).

## 7. Bekannte Einschraenkungen / Follow-ups

1. **EPEL unter aelteren RHEL/CentOS 7**: Falls `epel-release` nicht im
   Standard-Repo liegt, muss einmalig manuell nachgeholfen werden. Das
   Skript faengt den Fehler ab und gibt eine Warnung aus, bricht aber nicht ab.
2. **Disk-Benchmark im Container**: In ueberbuchten CI-Umgebungen koennen die
   MB/s-Werte niedrig ausfallen. Sie beeinflussen die Empfehlungen aktuell
   noch nicht (nur CPU/RAM), sind aber im Bericht dokumentiert.
3. **Docker-Smoke-Tests** erfordern lokal installiertes Docker und Netzwerk-
   Zugriff auf die Basis-Images. Sie sind als eigener Entrypoint
   (`tests/distro_smoke_test.sh`) von den Unit-Tests getrennt.
4. **Automatische Uebernahme von `DB_POOL_SIZE`** in die Django-App ist
   aktuell ueber die Env-Variable vorbereitet; falls die App einen festen
   Pool-Setting-Schluessel erwartet, kann er in `settings.py` ggf. ergaenzt
   werden (die bestehende `BOT_DB_WORKERS` wird bereits honoriert).
