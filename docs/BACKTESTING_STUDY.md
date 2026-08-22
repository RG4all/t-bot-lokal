# Backtesting – Machbarkeitsstudie und Produktionsarchitektur

**Stand:** 21. August 2026 · **Version:** 2.3.0

## Executive Summary

Die harte Anforderung „Backtesting darf den Trading-Bot unter keinen Umständen beeinflussen“ ist auf **einer einzigen Render-Free-Webinstanz technisch nicht erfüllbar**. Die Instanz stellt nur 0,1 CPU und 512 MB RAM bereit; ein lokaler Thread oder Subprozess teilt exakt diese Limits mit Daphne, WebSockets und Bot-Threads. Render bietet für Background Worker keinen Free-Instanztyp.

Entscheidung:

- **Render Free:** Backtest-Ausführung in Produktion deaktiviert. Formulare, Historie und Resultate bleiben lesbar. Das garantiert Bot-Priorität.
- **Lokale Entwicklung:** serieller lokaler Fallback ist explizit erlaubt.
- **Produktion:** separater Celery-Worker mit Redis, eigener Render-Instanz, Concurrency 1, 384-MB-Child-Limit und eigener Queue `backtest`.

## 1. Ist-Architektur vor 2.1.0

Ohne `REDIS_URL` setzte Celery `task_always_eager=True`. `dispatch_task()` startete anschließend einen Daemon-Thread im Daphne-Prozess. Ein Semaphore begrenzte zwar auf einen Backtest, aber CPU, RAM, Python-GIL, DB-Verbindungen und Container-Limit blieben mit dem Trading-Bot geteilt.

Risiken:

- Kombinationen × Symbole × Preispunkte wachsen multiplikativ.
- Decimal-Simulation ist CPU-intensiv.
- Ein umfangreicher Report hält Trade-Listen im RAM.
- 512 MB reichen nicht für garantierte Isolation von Django, Matplotlib/WeasyPrint, CCXT und Backtesting.
- 0,1 CPU bedeutet, dass selbst ein speicherschonender Backtest die Event-Loop indirekt verlangsamen kann.

## 2. Ressourcenmodell

| Komponente | Free-Webinstanz | Isolierter Worker |
|---|---:|---:|
| CPU | 0,1 geteilt | eigene Instanz, mindestens 0,5 empfohlen |
| RAM | 512 MB geteilt | eigenes Limit |
| Prozess | Daphne + Bot + optionaler Task | nur Celery Child |
| Queue | In-Memory, nicht dauerhaft | Redis `backtest` |
| Crash-Auswirkung | kann Web/Bot beenden | nur Worker/Task betroffen |
| Harte Isolation | nein | ja |

## 3. Zielarchitektur

```text
Browser
   │ HTTP / WebSocket
   ▼
┌──────────────────────────┐
│ t-bot-web                │
│ Django + Daphne          │
│ TradingBot Threads       │◄──── höchste Priorität
│ KEINE Backtest-Simulation│
└────────────┬─────────────┘
             │ Task Message (queue=backtest, priority=0)
             ▼
       ┌───────────┐
       │ Redis     │
       └─────┬─────┘
             ▼
┌──────────────────────────┐
│ t-bot-backtest-worker    │
│ Celery concurrency=1     │
│ max-memory-child=384 MB  │
│ max-tasks-child=1        │
└────────────┬─────────────┘
             │ Result/Progress
             ▼
       PostgreSQL
```

Der Trading-Bot selbst ist kein Celery-Task. Deshalb konkurriert die Queue-Priorität nicht mit Marktdaten: Der Backtest-Worker ist physisch getrennt und kann den Bot-Prozess nicht verdrängen.

## 4. Schutzmechanismen

### Web-Service

- `BACKTEST_LOCAL_FALLBACK_ENABLED=False` auf Render.
- POST eines Backtests wird mit verständlicher Meldung abgelehnt, wenn weder Redis noch lokaler Entwicklungsmodus verfügbar ist.
- Kein `multiprocessing`, kein `subprocess` und kein Backtest-Thread in Produktion.

### Celery Worker

```text
queue: backtest
concurrency: 1
prefetch multiplier: 1
max tasks per child: 1
max memory per child: 384000 KB
soft time limit: 3600 s
hard time limit: 3900 s
acks late: true
reject on worker lost: true
```

Ein neues Child pro Task gibt fragmentierten Speicher nach jedem Backtest frei. Überschreitet ein Child das Memory-Limit, ersetzt Celery es kontrolliert.

### Algorithmische Grenzen

- maximal 20.000 Kandidat-Symbol-Simulationen pro Task,
- maximal 5.000 Preispunkte pro Symbol,
- Indikatoren einmal je Symbol vorab berechnet,
- nur das beste Ergebnis je Symbol im RAM behalten,
- kooperative Pause/Cancel-Prüfung,
- DB-Abfragen und Fortschritt gedrosselt.

## 5. Graceful Degradation

1. Ohne Worker: Formular sichtbar, Start deaktiviert, Bot läuft normal.
2. Worker-RAM-Limit erreicht: Worker-Child wird ersetzt; Web/Bot bleiben unberührt.
3. Redis nicht erreichbar: Task kann nicht dispatcht werden; Konfiguration/Trading bleiben aktiv.
4. PostgreSQL vorübergehend offline: Worker retryt/fehlschlägt; Bot nutzt seinen eigenen Circuit-Breaker und Marktstream.
5. Hohe Last: Concurrency 1 verhindert parallele Backtests.

## 6. Ressourcen-Simulation

`scripts/backtest_resource_probe.py` startet die Simulation in einem separaten OS-Prozess, setzt dort `RLIMIT_AS=384 MB` und reduziert die Prozesspriorität. Der Elternprozess misst gleichzeitig einen synthetischen Bot-Heartbeat.

Ausführung:

```bash
python scripts/backtest_resource_probe.py
```

Akzeptanzkriterien:

- Worker Exit-Code 0,
- Peak-RSS unter 384 MB,
- 100 Kandidaten auf 5.000 Preisen abgeschlossen,
- Elternprozess/Heartbeat bleibt responsiv,
- keine Django-/Postgres-Verbindung im Simulationsprozess erforderlich.

Gemessener Sandbox-Lauf (21.08.2026):

```json
{
  "candidates": 100,
  "price_points": 5000,
  "duration_seconds": 0.267,
  "peak_rss_mb": 17.41,
  "worker_exit_code": 0,
  "parent_heartbeat_max_gap_ms": 20.21
}
```

Der isolierte Standalone-Kern blieb deutlich unter 384 MB; der Eltern-Heartbeat blieb bei seinem normalen 20-ms-Intervall. Ein zweiter Lauf parallel zur vollständigen Django-Test-Suite benötigte 2,577 s, 16,95 MB Peak-RSS und zeigte weiterhin nur 20,51 ms maximale Heartbeat-Lücke. Diese Simulation belegt algorithmische Begrenzung und Prozessisolation im Test, ersetzt aber keinen Lasttest auf der tatsächlichen Render-Worker-Instanz.

## 7. Deployment

1. Web bleibt mit `render.yaml` unverändert auf Free oder wird separat hochgestuft.
2. Externes/bezahltes Redis bereitstellen.
3. `render.worker.example.yaml` als Vorlage verwenden.
4. Im Web **und** Worker dieselbe `REDIS_URL` setzen.
5. Worker mit Queue `backtest`, Concurrency 1 starten.
6. Optional separaten Celery-Beat-Service für geplante Tests starten.
7. Prüfen, dass `/backtesting/` „Celery-Worker ist verfügbar“ anzeigt.
8. Zuerst einen kleinen Test (<100 Kombinationen, 500 Punkte) ausführen.
9. Worker-RAM/CPU und Bot-Heartbeat beobachten.

## 8. Peer-Review-Ergebnis

- Ein lokaler Render-Free-Backtest wurde abgelehnt, weil er die absolute Bot-Priorität verletzt.
- Separater Worker + Redis ist das einzige im bestehenden Stack belastbar isolierte Modell.
- Celery-Eager bleibt ausschließlich lokales Entwicklerwerkzeug.
- Ressourcenlimits sind sowohl formularseitig, algorithmisch als auch auf Worker-Ebene vorhanden.
- Die Lösung verändert TradingBot-, Binance-WebSocket- und Dashboard-Prozesse nicht.

## 9. Lokale Plattformportabilität

`scripts/install_system_dependencies.sh` trennt Host-Bootstrap vom Debian-basierten Multi-Arch-Container. Der Hostinstaller erkennt apt, pacman, dnf/yum, zypper oder apk; Docker baut anschließend dasselbe reproduzierbare Image nativ für amd64, arm64, arm/v7, ppc64le oder s390x. Der Container bleibt dadurch unabhängig von den Paketnamen des Hosts.

`scripts/tune_local_hardware.py` misst CPU, RAM, Datenträger und Architektur. Die resultierende `.env.local` dimensioniert getrennte CPU-/RAM-Cgroups, Redis-Maxmemory, PostgreSQL-Cache, DataLog-Intervall und Standard-Preispunktzahl. Die Render-Free-Simulation ist ein expliziter Opt-in und beeinflusst normale lokale Profile nicht.
