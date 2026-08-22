# Peer-Review der Backtesting- und 24/7-Architektur des T-Bot

Ich habe die von dir angegebene `BACKTESTING_STUDY.md` direkt aus dem Repository geprüft und die darin beschriebene Architektur gegen die aktuellen Free-Tier-Bedingungen von Render, Railway, Cloud Run, AWS Lambda, Cloudflare Workers und Oracle Cloud abgeglichen.

Der wichtigste Befund vorweg:

> **Redis ist für den T-Bot nicht deshalb unverzichtbar, weil Redis selbst besonders schnell ist, sondern weil die aktuelle Architektur eine zuverlässige Entkopplung zwischen Web/Trading-Prozess und Backtest-Worker benötigt.**
>
> **Für einen wirklich kostenlosen 24/7-Betrieb würde ich Redis nicht durch einen anderen externen Cache ersetzen. Ich würde die Architektur vielmehr so ändern, dass der Live-Trading-Prozess überhaupt keine externe Backtest-Queue benötigt.**

Für dein Ziel halte ich eine **lokale bzw. eigene Linux-Instanz mit SQLite + persistentem lokalen State + separatem Backtest-Prozess** für die beste Null-Euro-Lösung. Falls der T-Bot zwingend öffentlich im Internet laufen muss, ist **Oracle Always Free** derzeit interessanter als Render Free; Cloud Run/Lambda sind für einzelne Jobs brauchbar, aber nicht als permanenter Worker.

---

# 1. Was die aktuelle T-Bot-Architektur tatsächlich macht

Die vorliegende Studie beschreibt bereits sehr deutlich, warum der ursprüngliche Ansatz problematisch ist.

Die aktuelle Produktionsidee ist:

```text
                    ┌──────────────────────┐
                    │       Browser        │
                    └──────────┬───────────┘
                               │
                         HTTP / WebSocket
                               │
                               ▼
              ┌─────────────────────────────┐
              │         T-BOT WEB            │
              │                              │
              │ Django / Daphne              │
              │ TradingBot                   │
              │ WebSockets                   │
              │ Market Data                  │
              └──────────────┬──────────────┘
                             │
                       Celery Task
                             │
                             ▼
                       ┌───────────┐
                       │   Redis   │
                       │   Queue   │
                       └─────┬─────┘
                             │
                             ▼
              ┌─────────────────────────────┐
              │      BACKTEST WORKER        │
              │                              │
              │ Celery                      │
              │ concurrency = 1             │
              │ eigenes Memory-Limit        │
              └──────────────┬──────────────┘
                             │
                             ▼
                       PostgreSQL
```

Die Studie nennt als wesentliche Randbedingungen:

* Free-Webinstanz: **0,1 CPU**
* **512 MB RAM**
* TradingBot und Django/Daphne teilen sich diesen Container
* Backtests sind CPU- und speicherintensiv
* `Decimal`-Simulation ist teuer
* Trade-Listen/Reports können erheblichen RAM verbrauchen
* Kandidaten × Symbole × Preispunkte wachsen multiplikativ.

Das ist eine korrekte Diagnose.

---

# 2. Welche Rolle Redis tatsächlich spielt

Hier muss man zwischen **Cache** und **Message Broker** unterscheiden.

Im T-Bot ist Redis primär **kein klassischer Cache**.

Es erfüllt vielmehr diese Aufgaben:

### A. Task Queue

Der Web-Prozess sagt sinngemäß:

```text
"Führe Backtest X mit Parametern Y aus."
```

Der Task landet in Redis.

Der Worker holt ihn später ab.

Damit muss Django nicht selbst rechnen.

### B. Entkopplung

Das ist der entscheidende Punkt:

```text
Django/Trading
      │
      │ Task
      ▼
    Redis
      │
      ▼
Backtest Worker
```

Der Worker kann sterben, ohne dass Django bzw. der TradingBot ebenfalls beendet werden.

Die Studie beschreibt genau diese Isolation als Kernvorteil der Architektur.

### C. Zustandsübergabe

Redis kann zusätzlich:

* Queue-Zustand
* Task-ID
* Retry-Zustand
* Worker-Kommunikation
* Progress
* Resultate bzw. Result-Backend-Informationen

transportieren.

---

# 3. Was passiert ohne Redis?

Das Repository hat bereits einen Fallback:

```text
REDIS_URL nicht vorhanden
        │
        ▼
Celery eager mode
        │
        ▼
dispatch_task()
        │
        ▼
Daemon-Thread
        │
        ▼
Backtest im Daphne-Prozess
```

Das ist für Entwicklung akzeptabel.

Für deinen Live-Betrieb ist es **nicht akzeptabel**.

Die Studie benennt selbst die Probleme:

* CPU wird geteilt
* RAM wird geteilt
* Python-GIL wird geteilt
* DB-Verbindungen werden geteilt
* Container-Limits werden geteilt
* Event Loop kann beeinflusst werden.

Das ist ein wichtiger Punkt:

> **Ein Semaphore mit `max=1` macht aus einem Backtest keinen isolierten Worker.**

Es verhindert nur parallele Backtests.

---

# 4. Das eigentliche Problem ist nicht Redis

Das ist meiner Ansicht nach die wichtigste architektonische Korrektur.

Die Frage

> „Womit ersetzen wir Redis?“

ist eigentlich zu eng gestellt.

Die richtige Frage lautet:

> **„Braucht der Live-Trading-Prozess überhaupt einen Backtest-Broker?“**

Für den Live-Bot lautet meine Antwort:

**Nein.**

Backtesting und Live-Trading sind zwei völlig unterschiedliche Workloads.

```text
LIVE
────
Exchange
   ↓
Market Data
   ↓
Trading Engine
   ↓
Order Management


RESEARCH
────────
Historical Data
   ↓
Parameter Generator
   ↓
Backtest Engine
   ↓
Optimization
   ↓
Results
```

Sie sollten sich **nicht gegenseitig blockieren**.

---

# 5. Sekundentakt: Das eigentliche Ressourcenproblem

Nehmen wir zunächst ein realistisches Beispiel:

```text
5 Exchanges
10 Symbole / Exchange
1 Tick / Sekunde
```

Das sind bereits:

```text
5 × 10 = 50 Symbol-Streams
50 × 60 = 3.000 Abfragen / Minute
50 × 3.600 = 180.000 / Stunde
50 × 86.400 = 4.320.000 / Tag
```

Bei 10 Exchanges:

```text
10 × 10 × 86.400
= 8.640.000 Requests/Tag
```

Das ist der entscheidende Unterschied:

### HTTP-Polling

```text
GET ticker
GET ticker
GET ticker
...
```

erzeugt eine enorme Request-Last.

### WebSocket

```text
Exchange
   │
   │ dauerhafte Verbindung
   ▼
T-Bot
```

ist für diesen Zweck wesentlich besser.

---

# 6. Deshalb würde ich Sekundentakt nicht über REST realisieren

Für den Live-T-Bot:

**WebSocket bevorzugen.**

REST nur für:

* initialen Snapshot
* fehlende Daten
* Order-Status
* seltene Metadaten
* Fallback

Beispiel:

```text
Binance WebSocket ──┐
                    │
Kraken WebSocket ───┤
                    │
OKX WebSocket ──────┤
                    ├──► MarketDataHub
Bybit WebSocket ────┤
                    │
Coinbase WS ────────┘
                           │
                           ▼
                     In-Memory State
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
       Trading Engine               Persistence
                                      SQLite
```

Damit wird aus

```text
50 REST requests/sec
```

nicht notwendigerweise

```text
50 REST requests/sec
```

sondern eine Handvoll persistenter WebSocket-Verbindungen mit vielen Symbol-Subscriptions.

---

# 7. Was muss überhaupt gespeichert werden?

Für den Live-Betrieb würde ich drei Ebenen unterscheiden.

## Ebene 1 – RAM

Extrem schneller Zustand:

```python
market_state = {
    "binance:BTC/USDT": {
        "bid": ...,
        "ask": ...,
        "last": ...,
        "timestamp": ...,
    }
}
```

Hier gehören hin:

* letzter Preis
* Bid/Ask
* Spread
* Timestamp
* Volumen
* kurzfristige Indikatorzustände
* Connection State
* Exchange State

**Nicht** SQLite für jeden Tick.

---

## Ebene 2 – SQLite

Persistente Daten:

```text
market_ticks
ohlcv
trades
orders
bot_events
backtest_jobs
backtest_results
parameters
```

SQLite ist hierfür hervorragend geeignet, solange nicht mehrere Prozesse gleichzeitig massiv schreiben.

---

## Ebene 3 – historische Daten

Für Backtests:

```text
Parquet
CSV
SQLite
```

Ich würde langfristig **Parquet** für große historische Marktdaten bevorzugen.

Beispiel:

```text
data/
 ├── binance/
 │    ├── BTC_USDT/
 │    │    ├── 1s/
 │    │    └── 1m/
 │    └── ETH_USDT/
 │
 ├── kraken/
 │    └── BTC_USD/
 │
 └── okx/
```

Damit muss ein Backtest nicht die Live-Datenbank durchsuchen.

---

# 8. Variante A – alles im Hauptprozess

## Architektur

```text
T-Bot
 │
 ├── MarketData
 ├── Trading
 ├── Indicators
 ├── SQLite
 │
 └── Backtest Thread
```

### Vorteil

Extrem einfach.

Keine:

* Redis-Installation
* Celery-Broker
* externer Service
* Netzwerkabhängigkeit

### Nachteil

Für deinen Anspruch ungeeignet.

Ein Backtest kann:

```text
CPU
RAM
GC
GIL
Disk I/O
DB I/O
```

beeinflussen.

### Bewertung

| Kriterium     | Bewertung |
| ------------- | --------: |
| Kosten        |     ★★★★★ |
| Einfachheit   |     ★★★★★ |
| Isolation     |         ★ |
| 24/7-Live-Bot |         ❌ |
| Optimierung   |       ★★★ |
| Empfehlung    |         ❌ |

**24/7-Sicherheit gefährdet.**

---

# 9. Variante B – separater lokaler Prozess

Das ist wesentlich interessanter.

```text
                 ┌─────────────────┐
                 │    T-BOT        │
                 │                 │
Exchange ───────►│ Market Data     │
                 │ Trading         │
                 │ SQLite          │
                 └────────┬────────┘
                          │
                     Job File / DB
                          │
                          ▼
                 ┌─────────────────┐
                 │ BACKTEST        │
                 │ PROCESS         │
                 │                 │
                 │ CPU limit       │
                 │ RAM limit       │
                 │ nice priority   │
                 └─────────────────┘
```

Das ist für einen kostenlosen Self-Hosted-Betrieb meine bevorzugte Lösung.

### Kommunikation

Statt Redis:

```text
backtest_jobs
```

in SQLite.

Beispielsweise:

```text
id
status
parameters
created_at
started_at
finished_at
result_path
error
```

Der Worker fragt:

```sql
SELECT *
FROM backtest_jobs
WHERE status = 'queued'
ORDER BY created_at
LIMIT 1;
```

Danach:

```text
queued
  ↓
running
  ↓
completed
```

oder:

```text
running
  ↓
failed
```

---

# 10. Warum SQLite hier besser funktioniert als man zunächst denkt

SQLite muss **nicht** Redis ersetzen.

Es ersetzt lediglich:

> die dauerhafte Job-Persistenz.

Die Queue muss nicht ultraschnell sein.

Ein Backtest läuft möglicherweise:

```text
30 Sekunden
5 Minuten
30 Minuten
1 Stunde
```

Die Queue muss deshalb nicht 100.000 Tasks/s verarbeiten.

Eine SQLite-Tabelle mit:

```text
10
100
1000
```

Backtest-Jobs ist vollkommen ausreichend.

---

# 11. Aber: SQLite nicht für Tick-Daten missbrauchen

Das wäre ein Fehler.

Nicht:

```text
jeder Tick
   ↓
SQLite INSERT
```

bei:

```text
50–500 Streams
```

Sondern:

```text
Exchange
   ↓
RAM ring buffer
   ↓
Batch
   ↓
SQLite / Parquet
```

beispielsweise:

```text
RAM
  ↓
100 Ticks
  ↓
Batch INSERT
```

oder zeitbasiert:

```text
alle 1–5 Sekunden
```

---

# 12. Variante C – SQLite + separater Backtest-Prozess

Das wäre mein bevorzugtes Design:

```text
                    INTERNET
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
     Exchange A     Exchange B     Exchange C
        │              │              │
        └──────────────┼──────────────┘
                       ▼
              ┌─────────────────┐
              │   MarketDataHub │
              │                 │
              │ asyncio         │
              │ WebSockets      │
              └────────┬────────┘
                       │
                RAM Market State
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
    Trading Engine              Recorder
          │                         │
          ▼                         ▼
       Orders                    SQLite
                                      │
                                      ▼
                                Historical Data
                                      │
                                      ▼
                              Backtest Job Table
                                      │
                                      ▼
                           ┌────────────────────┐
                           │ Backtest Worker    │
                           │ eigener Prozess    │
                           └────────────────────┘
```

Der Worker kann beispielsweise mit:

```text
nice
ionice
ulimit
systemd
cgroups
```

begrenzt werden.

Damit bekommt der TradingBot Priorität.

---

# 13. Variante D – Render Free als Backtest-Worker

Hier ist die Antwort eindeutig:

## ❌ Nicht geeignet.

Die aktuellen Render-Bedingungen sind sogar ungünstiger als die ursprüngliche Studie teilweise suggeriert.

Render Free bietet:

* **512 MB RAM**
* **0,1 CPU**
* Free-Webservices schlafen nach **15 Minuten ohne eingehenden Traffic**
* Wake-up dauert ungefähr eine Minute
* lokales Dateisystem ist flüchtig
* Free ist ausdrücklich nicht für Production gedacht. ([Render][1])

Noch wichtiger:

> `free` ist bei Render aktuell **nicht für Background Worker verfügbar**. ([Render][2])

Damit ist:

```text
Render Free
    ↓
Celery Worker
```

keine saubere kostenlose Lösung.

### Bewertung

| Eigenschaft                |         Render Free |
| -------------------------- | ------------------: |
| 24/7                       |                   ❌ |
| Background Worker          |                   ❌ |
| 512 MB                     |                  ⚠️ |
| CPU                        |               ❌ 0,1 |
| Sleep                      |                   ❌ |
| persistenter lokaler State |                   ❌ |
| Backtesting                |                  ⚠️ |
| Live Trading               |                   ❌ |
| Empfehlung                 | **nicht verwenden** |

Ein Keep-Alive würde ich ebenfalls **nicht** als Lösung akzeptieren.

Es macht aus einem nicht zugesicherten Free-Service keinen zuverlässigen Produktionsserver.

---

# 14. Render Free als Webfrontend

Das ist dagegen möglich:

```text
Render Free
    │
    ▼
Django Dashboard
    │
    ▼
lokaler/anderer T-Bot
```

Aber auch hier muss man mit Cold Starts und der flüchtigen lokalen Speicherung leben. ([Render][1])

Für ein Dashboard:

**okay.**

Für:

```text
Exchange → Trading Engine → Orders
```

**nein.**

---

# 15. Variante E – Railway Free

Railway ist interessanter, aber nicht wirklich eine stabile Null-Euro-24/7-Plattform.

Aktuell gibt es:

```text
Free
$0
$1 monatliches Guthaben
```

Nach dem Trial beträgt das kostenlose Guthaben nur $1/Monat. Die Free-Ressourcen liegen bei maximal etwa:

```text
1 vCPU
0,5 GB RAM
```

pro Service. ([Railway Docs][3])

Das Problem ist nicht primär RAM.

Das Problem ist:

> **1 Dollar monatliches Rechenbudget reicht nicht für einen permanent laufenden Backtest-Worker.**

### Bewertung

| Kriterium           |              Railway |
| ------------------- | -------------------: |
| CPU                 |                  ★★★ |
| RAM                 |                   ★★ |
| 24/7                |                   ⚠️ |
| kostenlos dauerhaft |          ❌ praktisch |
| Worker              |                  ★★★ |
| Deployment          |                ★★★★★ |
| Backtesting         |                  ★★★ |
| Empfehlung          | ❌ für Null-Euro-24/7 |

Railway ist als **Test-/Staging-System** sinnvoller.

---

# 16. Variante F – AWS Lambda

Lambda ist für etwas anderes sehr gut.

Beispiel:

```text
Django
   │
   │ Backtest Job
   ▼
AWS Lambda
   │
   ▼
Result
```

Der Free Tier umfasst aktuell:

* 1 Mio. Requests/Monat
* 400.000 GB-Sekunden Compute/Monat. ([AWS-Dokumentation][4])

Aber Lambda ist **kein dauerhafter Worker**.

Es eignet sich für:

```text
Job
 ↓
Lambda
 ↓
Result
```

nicht für:

```text
Lambda
 ↓
24/7 Celery Worker
 ↓
Redis
 ↓
Backtests
```

Zusätzlich entstehen bei größeren Backtests:

* Cold Starts
* Laufzeitlimits
* Packaging-Probleme
* große Dependency-Bundles
* temporäres Filesystem
* komplexere Job-Orchestrierung

### Für T-Bot

**Parameteroptimierung in kleine unabhängige Jobs aufspalten:**

Ja.

**Backtest-Worker ersetzen:**

Nur bedingt.

---

# 17. Variante G – Google Cloud Run

Cloud Run ist für diesen Zweck interessanter.

Der aktuelle Free Tier beinhaltet bei request-basierter Abrechnung:

* 180.000 vCPU-Sekunden
* 360.000 GiB-Sekunden
* 2 Mio. Requests/Monat. ([Google Cloud][5])

Das klingt zunächst hervorragend.

Aber:

```text
Backtest
   ↓
Cloud Run
   ↓
CPU
   ↓
Ergebnis
```

ist etwas anderes als:

```text
permanenter Worker
```

Cloud Run eignet sich sehr gut für **stateless Job-Ausführung**.

Für deinen Parameter-Optimizer könnte man daraus sogar eine interessante Architektur bauen:

```text
Optimizer
   │
   ├── Job 1 ──► Cloud Run
   ├── Job 2 ──► Cloud Run
   ├── Job 3 ──► Cloud Run
   ├── Job 4 ──► Cloud Run
   └── Job N ──► Cloud Run
```

Aber dann braucht man zusätzlich:

* Job Storage
* Ergebnis-Storage
* Scheduling
* Retry
* Authentication
* Quotenüberwachung

Damit steigt die Komplexität erheblich.

### Bewertung

| Kriterium         |        Cloud Run |
| ----------------- | ---------------: |
| Einzeljob         |            ★★★★★ |
| Batch-Backtesting |             ★★★★ |
| 24/7 Worker       |               ★★ |
| Parameter-Sweep   |             ★★★★ |
| Einfachheit       |               ★★ |
| kostenlos         |               ⚠️ |
| Empfehlung        | sekundäre Option |

---

# 18. Variante H – Cloudflare Workers

Für deinen Backtester würde ich Cloudflare Workers praktisch ausschließen.

Free:

```text
100.000 Requests/Tag
10 ms CPU
128 MB RAM
```

und Queue-/Cron-Ausführungen haben ebenfalls harte Laufzeitgrenzen. ([Cloudflare Docs][6])

Ein Python-/Celery-Backtest mit `Decimal`, Indikatorberechnung und Parameter-Sweeps passt nicht in dieses Modell.

### Dafür sehr gut:

```text
API Gateway
Rate Limiter
Caching
WebSocket/API Proxy
Health Endpoint
```

Also:

```text
Cloudflare
     │
     ▼
T-Bot API
```

aber nicht:

```text
Cloudflare
     │
     ▼
DVA/NDA Parameter Optimizer
```

### Bewertung

**Live-API-Edge:** ★★★★

**Backtest:** ❌

---

# 19. Variante I – Oracle Cloud Always Free

Hier wird es für deinen konkreten Anwendungsfall interessant.

Oracle bietet aktuell Always-Free Compute.

Wichtig: Die früher häufig genannten

```text
4 OCPU / 24 GB
```

sind **nicht mehr die aktuelle Always-Free-Grenze**.

Aktuell dokumentiert Oracle:

```text
2 OCPU
12 GB RAM
```

für Ampere A1 Always Free. ([Oracle Docs][7])

Das ist trotzdem erheblich interessanter als:

```text
Render
0,1 CPU
512 MB
```

Du kannst dort einen normalen Linux-Server betreiben:

```text
Ubuntu
Docker
Python
Redis
SQLite
PostgreSQL
Celery
systemd
```

und insbesondere:

```text
t-bot
backtest-worker
```

als getrennte Prozesse/Container laufen lassen.

---

# 20. Das wäre meine kostenlose Cloud-Architektur

```text
                 INTERNET
                     │
              ┌──────▼──────┐
              │ Oracle VM   │
              │ 2 OCPU      │
              │ 12 GB RAM   │
              └──────┬──────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
        ▼            ▼            ▼
   t-bot-web     market-data   backtester
        │            │            │
        │            │            │
        │            ▼            │
        │       WebSockets        │
        │                         │
        └──────────┬──────────────┘
                   │
              SQLite/Postgres
```

Noch besser:

```text
              Oracle VM
                  │
       ┌──────────┴──────────┐
       │                     │
       ▼                     ▼
  LIVE CONTAINER       BACKTEST CONTAINER
       │                     │
       │                     │
       │               CPU limit
       │               RAM limit
       │               nice
       │               concurrency=1
       │
       ▼
  Market Data
```

Damit erhältst du echte Prozessisolation.

---

# 21. Aber auch Oracle hat einen Haken

„Always Free“ bedeutet nicht:

> SLA für professionelles Trading.

Oracle weist beispielsweise darauf hin, dass in manchen Regionen keine ausreichende Always-Free-Kapazität verfügbar sein kann. ([Oracle Docs][8])

Deshalb würde ich einen **lokalen Fallback** einbauen.

---

# 22. Noch besser: Self-Hosting

Für dein Projekt ist das vermutlich die technisch sauberste Lösung.

Du hast ohnehin Linux-Systeme.

Dann:

```text
Linux
 │
 ├── t-bot.service
 │
 ├── market-data.service
 │
 ├── backtest-worker.service
 │
 └── watchdog
```

Keine:

* Render-Abhängigkeit
* Railway-Abhängigkeit
* Lambda-Abhängigkeit
* Cloud-Queue
* Free-Tier-Limitierung

---

# 23. Redis kann dabei sogar komplett verschwinden

Das ist der interessante Teil.

Ich würde die Queue auf:

```text
SQLite
```

reduzieren.

Beispiel:

```sql
CREATE TABLE backtest_jobs (
    id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    parameters TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    result_path TEXT,
    error TEXT
);
```

Der Worker läuft als eigener Linux-Prozess.

```text
while True:

    job = claim_next_job()

    if job:
        run_backtest(job)

    sleep(1)
```

Damit hast du:

```text
Redis = entfernt
Celery = optional entfernt
RabbitMQ = nicht notwendig
```

---

# 24. Der entscheidende Trick: Atomic Job Claiming

Damit keine zwei Worker denselben Backtest starten:

```sql
BEGIN IMMEDIATE;

UPDATE backtest_jobs
SET status = 'running',
    started_at = CURRENT_TIMESTAMP
WHERE id = (
    SELECT id
    FROM backtest_jobs
    WHERE status = 'queued'
    ORDER BY created_at
    LIMIT 1
);

COMMIT;
```

Damit wird SQLite selbst zur kleinen persistenten Job-Queue.

Für **einen Worker** ist das mehr als ausreichend.

---

# 25. DVA/NDA-Optimierung sollte ebenfalls vom Live-Bot getrennt werden

Die Studie begrenzt bereits:

```text
20.000 Kandidaten
5.000 Preispunkte/Symbol
Indikatoren einmal vorab berechnen
nur bestes Ergebnis im RAM
concurrency=1
```

Das ist sinnvoll.

Ich würde das noch weiter strukturieren.

Nicht:

```text
Backtest
  ├─ DVA
  ├─ NDA
  ├─ DELTADELTA
  ├─ Beschleunigung
  ├─ EMA
  ├─ RSI
  └─ ...
```

für jeden Kandidaten neu berechnen.

Sondern:

```text
Historical Data
       │
       ▼
Indicator Precomputation
       │
       ├── DVA
       ├── NDA
       ├── DELTADELTA
       ├── acceleration
       ├── EMA
       ├── RSI
       └── ...
       │
       ▼
Parameter Matrix
       │
       ▼
Simulation
```

Das kann die CPU-Last massiv reduzieren.

---

# 26. Noch wichtiger: Parameteroptimierung ist ein Matrixproblem

Wenn beispielsweise:

```text
DVA:
10 Werte

NDA:
10 Werte

DELTADELTA:
10 Werte
```

entstehen:

```text
10 × 10 × 10
= 1.000 Kombinationen
```

Mit:

```text
20.000
```

Kandidaten wird das schnell teuer.

Deshalb:

### Stufe 1 – Grobsuche

```text
DVA 0–100
NDA 0–100
DD  0–100
```

gröbere Schritte.

### Stufe 2 – lokale Suche

Nur um die besten Regionen:

```text
DVA 37–44
NDA 61–68
DD  12–19
```

### Stufe 3 – Walk-forward

Dann erst:

```text
Training
Validation
Out-of-sample
```

Das ist für die Stabilität der Trading-Strategie wichtiger als immer mehr brute-force Kombinationen.

---

# 27. Ein weiterer wichtiger Punkt: Backtest-Daten und Live-Daten dürfen sich nicht vermischen

Ich würde strikt trennen:

```text
LIVE DATA
─────────
exchange websocket
      ↓
RAM
      ↓
SQLite / rolling storage


BACKTEST DATA
─────────────
historical dataset
      ↓
Parquet
      ↓
backtest worker
```

Der Backtester darf **niemals** während einer Live-Session große historische Datenmengen aus derselben Datenbank ziehen, wenn dadurch der Live-Bot blockiert werden kann.

---

# 28. SQLite WAL-Modus

Für deinen Aufbau würde ich SQLite mit:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
```

betreiben.

Dadurch können:

```text
Live-Bot → lesen/schreiben

Backtest Worker → lesen
```

wesentlich besser koexistieren.

Noch besser:

> Der Backtester bekommt möglichst eine **Read-Only-Kopie des historischen Datensatzes**.

Dann existiert praktisch keine Konkurrenz.

---

# 29. Das optimale Datenmodell

Ich würde drei Dateien verwenden:

```text
data/
├── live.db
├── backtest.db
└── historical/
    ├── binance/
    ├── kraken/
    ├── okx/
    └── ...
```

### `live.db`

```text
orders
trades
positions
bot_state
exchange_state
alerts
```

### `backtest.db`

```text
jobs
results
optimization_runs
parameter_sets
```

### Parquet

```text
historical market data
```

Damit gibt es praktisch keine DB-Blockade zwischen Live und Backtest.

---

# 30. Meine Bewertung aller Varianten

| Lösung                         |    Kosten |      24/7 | Isolation |  Backtest | Live-Trading |      Aufwand |
| ------------------------------ | --------: | --------: | --------: | --------: | -----------: | -----------: |
| Hauptprozess/Thread            |     ★★★★★ |         ❌ |         ★ |       ★★★ |            ❌ | sehr niedrig |
| SQLite + Thread                |     ★★★★★ |         ❌ |         ★ |       ★★★ |            ❌ |      niedrig |
| **SQLite + separater Prozess** | **★★★★★** | **★★★★★** |  **★★★★** | **★★★★★** |    **★★★★★** |   **mittel** |
| Redis + Celery                 |       ★★★ |     ★★★★★ |     ★★★★★ |     ★★★★★ |        ★★★★★ |       mittel |
| Render Free Worker             |     ★★★★★ |         ❌ |         — |         ❌ |            ❌ |       mittel |
| Render Free Web                |     ★★★★★ |         ❌ |        ★★ |        ★★ |            ❌ |      niedrig |
| Railway Free                   |       ★★★ |        ⚠️ |       ★★★ |       ★★★ |           ⚠️ |      niedrig |
| AWS Lambda                     |      ★★★★ |       ★★★ |      ★★★★ |      ★★★★ |            ❌ |         hoch |
| Cloud Run                      |      ★★★★ |       ★★★ |      ★★★★ |      ★★★★ |           ⚠️ |         hoch |
| Cloudflare Workers             |     ★★★★★ |     ★★★★★ |       ★★★ |         ❌ |           ★★ |       mittel |
| Oracle Always Free             |     ★★★★★ |      ★★★★ |     ★★★★★ |     ★★★★★ |         ★★★★ |       mittel |
| Eigener Linux-PC               |     ★★★★★ |     ★★★★★ |     ★★★★★ |     ★★★★★ |        ★★★★★ |       mittel |

---

# 31. Welche Ansätze gefährden die 24/7-Verfügbarkeit?

## 🔴 Nicht für den Live-Bot

**Render Free**

Wegen:

* Sleep
* Cold Start
* 0,1 CPU
* 512 MB
* kein kostenloser Background Worker. ([Render][1])

---

## 🔴 Nicht für den Backtest

**Cloudflare Workers Free**

Wegen:

* 10 ms CPU
* 128 MB RAM
* 100.000 Requests/Tag. ([Cloudflare Docs][6])

---

## 🟠 Nur bedingt

**Railway Free**

Technisch wesentlich brauchbarer, aber das kostenlose Monatskontingent von $1 macht einen dauerhaft laufenden Worker nicht zu einer belastbaren Null-Euro-Lösung. ([Railway Docs][3])

---

## 🟠 Speziallösung

**Lambda / Cloud Run**

Sehr gut für:

```text
einzelne Backtest-Jobs
```

aber nicht als Ersatz für einen permanenten Celery Worker.

---

# 32. Was ich konkret für den T-Bot bauen würde

Meine Zielarchitektur wäre:

```text
                         ┌────────────────────┐
                         │      EXCHANGES     │
                         │                    │
                         │ Binance            │
                         │ Kraken             │
                         │ OKX                │
                         │ Bybit              │
                         │ Coinbase           │
                         └─────────┬──────────┘
                                   │
                              WebSockets
                                   │
                                   ▼
                    ┌─────────────────────────┐
                    │     MARKET DATA HUB     │
                    │                         │
                    │ asyncio                 │
                    │ CCXT / native WS        │
                    │ reconnect               │
                    │ heartbeat               │
                    │ rate-limit              │
                    └────────────┬────────────┘
                                 │
                         In-Memory State
                                 │
                 ┌───────────────┼──────────────┐
                 │               │              │
                 ▼               ▼              ▼
              Trading         Recorder       Metrics
              Engine              │
                                  ▼
                              live.db
                                  
                                  
                       ┌──────────────────┐
                       │  BACKTEST QUEUE  │
                       │     SQLite       │
                       └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │ BACKTEST WORKER  │
                       │                  │
                       │ eigener Prozess  │
                       │ concurrency=1    │
                       │ RAM limit        │
                       │ CPU priority     │
                       └────────┬─────────┘
                                │
                                ▼
                           backtest.db
                                │
                                ▼
                            Results
```

---

# 33. Redis würde ich zunächst vollständig entfernen

Nicht unbedingt für immer.

Ich würde den Code so abstrahieren:

```python
class BacktestQueue:
    def submit(self, job):
        ...

    def claim(self):
        ...

    def complete(self, job):
        ...

    def fail(self, job):
        ...
```

Dann:

```text
SQLiteBacktestQueue
```

als erste Implementierung.

Später kann man problemlos hinzufügen:

```text
RedisBacktestQueue
```

Damit wird Redis zu einer **skalierbaren Option**, nicht zu einer Grundvoraussetzung.

---

# 34. Das hat einen weiteren Vorteil für deine Parameteroptimierung

Die Queue kann später Jobs erzeugen wie:

```text
optimization_run = 4711
```

und daraus:

```text
job 4711-001
job 4711-002
job 4711-003
...
job 4711-N
```

Der Worker arbeitet:

```text
job 1
 ↓
job 2
 ↓
job 3
 ↓
...
```

Später könnte man daraus mehrere Worker machen:

```text
worker-1
worker-2
worker-3
```

und **erst dann** wird Redis/RabbitMQ wirklich interessant.

---

# 35. Ein wichtiger Punkt zur vorhandenen Ressourcenmessung

Die Studie enthält einen interessanten Test:

```text
100 Kandidaten
5000 Preise
17,41 MB Peak-RSS
0,267 s
```

und einen Heartbeat mit ungefähr:

```text
20 ms
```

maximaler Lücke.

Das ist ein gutes Ergebnis für den **Backtest-Kern**.

Es beweist aber ausdrücklich nicht:

> „Der komplette T-Bot kann unter Produktionslast garantiert ungestört parallel backtesten.“

Die Studie sagt das selbst korrekt: Der Test ersetzt keinen echten Lasttest auf einer Render-Worker-Instanz.

Das sollte man bei der Architekturentscheidung berücksichtigen.

---

# 36. Mein endgültiges Ranking

## 🥇 Platz 1 — Self-Hosted Linux + SQLite + separater Backtest-Prozess

**Meine klare Empfehlung.**

```text
Kosten:       0 €
Komplexität:  mittel
Performance:  sehr gut
Isolation:    sehr gut
24/7:         sehr gut
```

Wenn deine Hardware dauerhaft laufen kann, ist das technisch die sauberste Lösung.

---

## 🥈 Platz 2 — Oracle Always Free + Docker

Für einen öffentlich erreichbaren Bot:

```text
Oracle VM
   │
   ├── t-bot
   ├── market-data
   └── backtest-worker
```

Die aktuell dokumentierten Always-Free-Ressourcen sind **2 OCPU + 12 GB RAM** für Ampere A1. ([Oracle Docs][7])

Das ist für den T-Bot wesentlich realistischer als Render Free.

---

## 🥉 Platz 3 — Redis + Celery beibehalten

Wenn du später:

```text
mehrere Backtest Worker
mehrere Optimierungsjobs
verteilte Berechnung
```

haben möchtest, ist die vorhandene Architektur weiterhin sehr gut.

Aber:

> **Redis ist dann ein Skalierungswerkzeug, nicht zwingend Teil der Minimalarchitektur.**

---

## Platz 4 — Cloud Run

Sehr interessant für:

```text
Parameter-Sweeps
Batch Jobs
sporadische Optimierung
```

aber unnötig komplex für den ersten stabilen T-Bot.

---

## Platz 5 — Lambda

Gut für einzelne Jobs, nicht als permanenter Backtest-Daemon.

---

## ❌ Render Free

Für deinen Anwendungsfall würde ich es ausdrücklich **nicht** einsetzen.

---

# 37. Die Architektur, die ich für den T-Bot letztlich wählen würde

Wenn das Ziel wirklich lautet:

> **0 € laufende Kosten + 24/7 + mehrere Exchanges + Sekundendaten + Live-Trading + Backtesting + DVA/NDA-Optimierung**

dann:

```text
                 ┌──────────────────────┐
                 │ Linux Host            │
                 │                      │
                 │ systemd              │
                 └──────────┬───────────┘
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
    t-bot.service     market-data.service  backtest.service
          │                 │                  │
          │                 │                  │
          ▼                 ▼                  ▼
     Trading Core       WebSockets        Parameter Search
          │                 │                  │
          └────────────┬────┘                  │
                       ▼                       │
                  live.db                     │
                                               │
                  historical/*.parquet ◄───────┘
                                               │
                                               ▼
                                        backtest.db
```

**Kein Redis.**

**Kein Celery notwendig.**

**Kein Render Worker.**

**Kein Serverless-Zwang.**

Und trotzdem bleibt die Architektur so abstrahiert, dass später:

```text
SQLite Queue
      ↓
Redis Queue
      ↓
Redis + Celery Cluster
```

möglich ist.

---

# 38. Eine zusätzliche Änderung würde ich unbedingt vornehmen

Ich würde die **Live-Datenversorgung von der Backtesting-Infrastruktur vollständig entkoppeln**.

Das heißt:

```text
CCXT / Exchange WS
        ↓
MarketDataHub
        ↓
TradingBot
```

und niemals:

```text
CCXT
 ↓
Celery
 ↓
Redis
 ↓
Trading
```

Der Live-Pfad muss möglichst kurz sein:

```text
Exchange
 ↓
WebSocket
 ↓
Parser
 ↓
Market State
 ↓
Strategy
 ↓
Risk
 ↓
Order
```

Backtesting befindet sich außerhalb dieses Pfades.

Damit kann der Backtest-Worker komplett ausfallen:

```text
Backtest Worker DEAD
        │
        X
        │
        ▼

TradingBot
    │
    ├── WebSocket
    ├── Strategy
    ├── Risk
    └── Orders

       läuft weiter
```

**Das ist für die 24/7-Anforderung wesentlich wichtiger als die Wahl zwischen Redis, SQLite oder einem anderen Queue-System.**

---

## Schlussurteil

Die vorhandene `BACKTESTING_STUDY.md` kommt bei der zentralen Frage zu Recht zu dem Ergebnis, dass **Backtesting nicht innerhalb derselben schwach dimensionierten Render-Free-Instanz wie der TradingBot ausgeführt werden darf**. Die Ressourcen- und Prozessisolation mit einem separaten Worker ist richtig.

Ich würde jedoch einen Schritt weitergehen:

> **Für einen kostenlosen T-Bot braucht es zunächst überhaupt keinen Redis-Server.**

Die sinnvollste Minimalarchitektur ist:

**WebSocket-basierter MarketDataHub → In-Memory-Live-State → SQLite/Parquet-Persistenz → separater Backtest-Prozess → SQLite Job Queue.**

Wenn der T-Bot später tatsächlich mehrere parallele Optimierungs-Worker benötigt, kann **Redis + Celery** wieder als zweite Ausbaustufe eingeführt werden.

Für **24/7-Live-Trading** würde ich dagegen weder Render Free noch Railway Free als kritischen Trading-Host akzeptieren. Render Free schläft und besitzt nur 0,1 CPU/512 MB; Railway Free ist durch sein monatliches Gratisguthaben begrenzt. ([Render][1])

**Meine Reihenfolge wäre daher:**

1. **Eigener Linux-Rechner → beste Null-Euro-Lösung**
2. **Oracle Always Free → beste kostenlose Cloud-Lösung**
3. **Redis + Celery → beste spätere Skalierung**
4. **Cloud Run → gute Option für ausgelagerte Batch-Optimierung**
5. **Lambda → Speziallösung für einzelne Jobs**
6. **Railway Free → Entwicklung/Staging**
7. **Render Free → Dashboard/Test, nicht Trading/Worker**
8. **Cloudflare Workers → Edge/API, nicht Backtesting**

[T-Bot BACKTESTING_STUDY.md](https://github.com/Kryschuuu/t-bot/blob/arena/01a01bc6-t-bot/BACKTESTING_STUDY.md?utm_source=chatgpt.com)

[1]: https://render.com/docs/free?utm_source=chatgpt.com "Deploy for Free – Render Docs"
[2]: https://render.com/docs/blueprint-spec?utm_source=chatgpt.com "Blueprint YAML Reference – Render Docs"
[3]: https://docs.railway.com/pricing/free-trial?utm_source=chatgpt.com "Free Trial | Railway Docs"
[4]: https://docs.aws.amazon.com/de_de/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.pdf?utm_source=chatgpt.com "AWS Leitfaden zur Entscheidungsfindung"
[5]: https://cloud.google.com/run/pricing?utm_source=chatgpt.com "Cloud Run pricing | Google Cloud"
[6]: https://developers.cloudflare.com/workers/platform/limits/?utm_source=chatgpt.com "Limits · Cloudflare Workers docs"
[7]: https://docs.oracle.com/de-de/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm?utm_source=chatgpt.com "Ressourcen vom Typ \"Immer kostenlos\""
[8]: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm?utm_source=chatgpt.com "Always Free Resources"
