# Peer Review – Lokales Setup v2.3.0

## Scope

Review von Installer, Hardware-Tuner, Docker Compose, Celery-Isolation, Redis-/Worker-Fallback, Secrets, Monitoring und Testbarkeit.

## Befunde und umgesetzte Maßnahmen

| Risiko | Bewertung | Maßnahme |
|---|---|---|
| Backtest konkurriert mit Bot | kritisch | eigener Container, eigene CPU-/RAM-Cgroup, Queue `backtest`, Concurrency 1 |
| Worker zieht mehrere Tasks vor | hoch | Prefetch 1, Concurrency 1 |
| Speicherfragmentierung | hoch | max-tasks-per-child 1 |
| Worker-OOM | hoch | Celery Child 384 MB, Container standardmäßig 512–1024 MB |
| Redis online, Worker offline | hoch | POST wird abgelehnt; kein stilles Queueing und keine lokale Doppel-Ausführung |
| Redis vollständig offline | mittel/lokal | expliziter serieller Fallback, Semaphore 1 |
| Redis offline in Produktion | hoch | Fallback standardmäßig aus, POST wird abgelehnt |
| Kandidatenexplosion | kritisch | Formular und Task validieren 20.000 Gesamtsimulationen |
| Historienexplosion | hoch | maximal 5.000 Preispunkte je Symbol |
| Zu viele Pause-/Cancel-Queries | mittel | Kontrollintervall auf ungefähr 100 Prüfungen je Task gedrosselt |
| Secrets im Repository | kritisch | generierte `.env.local`, Modus 0600, durch Git ignoriert |
| Schwache Hardware überlastet | hoch | CPU-/RAM-/Disk-Probe und deterministische Profile |
| Render-Simulation wird versehentlich Standard | mittel | standardmäßig `False`, nur expliziter CLI-Schalter setzt 0,1 CPU |
| Distro-Lock-in | mittel | apt, pacman, dnf/yum, zypper, apk und fünf OCI-Architekturen |
| Fehlerhafte Shell-Skripte | hoch | `set -Eeuo pipefail`, Argumentvalidierung, Dry-Run, Syntaxprüfung |
| Fehlende Beobachtbarkeit | hoch | Worker-Ping, Redis-Ping, Runtime-Heartbeat, Peak-RSS, Taskdauer, strukturierte Logs |

## Sicherheitsreview

- Keine Hostports für Redis oder PostgreSQL; nur Web-Port wird veröffentlicht.
- Redis ist lokal ohne Persistenz und mit `noeviction` konfiguriert.
- PostgreSQL-Zugangsdaten werden aus nicht versionierter Env-Datei gelesen.
- Automatisch generierte Secrets nutzen `secrets.token_urlsafe`.
- Der Installer lädt keine beliebigen Remote-Shellskripte; er verwendet Paketquellen der Distribution beziehungsweise für RHEL-Derivate die offizielle Docker-CE-Repository-Datei.
- Zustandsändernde Webendpunkte bleiben CSRF- und Login-geschützt.

## Performance-Review

- Worker und Web besitzen getrennte Cgroups.
- PostgreSQL `max_connections=40` verhindert lokale Connection-Explosionen.
- Redis-Maxmemory und PostgreSQL-Cache werden an den Host-RAM angepasst.
- Der Hardware-Tuner reserviert bei kleinen Hosts konservative 512/512/256/64 MB für Web/Worker/Postgres/Redis.
- Ressourcenprobe blieb weit unter dem 384-MB-Child-Limit und der Eltern-Heartbeat responsiv.

## Residualrisiken

- Lokaler Fallback läuft absichtlich im Web-Prozess und bietet keine harte Isolation; er ist ausschließlich Entwicklungsmodus.
- Docker Desktop kann je nach globaler VM-Konfiguration strengere Grenzen als Compose setzen.
- Paketnamen können sich in zukünftigen Distribution-Releases ändern; `--dry-run` vor Installation wird empfohlen.
- Harte Garantie gegen Host-I/O-Contention ist nur mit getrennten physischen Hosts möglich. Compose isoliert CPU/RAM/Prozesse, teilt aber Datenträger und Kernel.

## Review-Fazit

Für lokale Entwicklung ist die Architektur einsatzfähig und defensiv dimensioniert. Der Standardpfad nutzt immer den separaten Worker. Degradation ist explizit und verhindert Doppel-Ausführung. Produktion bleibt bewusst von der lokalen Fallback-Option getrennt.
