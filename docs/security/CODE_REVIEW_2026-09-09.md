# Umfassender Code-Review · 2026-09-09

- **Stand:** `tbot.local` @ `727d3ee` (Version 2.4.20)
- **Umfang:** Alle Python-Module (`trading/`, `trading_bot_project/`, ~8.900 Zeilen Runtime-Code ohne Migrations/Tests), Templates, Celery-/Docker-Konfiguration, CI-Workflow, `requirements.txt`
- **Methodik:** Vollständige Lektüre der Kernmodule, statische Analyse (ruff, Bandit, `manage.py check --deploy`), vollständiger Testlauf (330 Tests, OK), gezielte Reproduktion von Verdachtsfällen in einer isolierten Django-Umgebung (Slice-Aggregation, `ValueError` bei ungültigen GET-Parametern), Review von Nebeneffekten in `install.sh`/`docker/`
- **Einordnung:** Die Codebasis ist insgesamt **reif und bewusst gehärtet** (Passphrase-Gate, CSRF-POST-only für Zustandsänderungen, IP-Rate-Limit mit Proxy-Allowlist, DB-Circuit-Breaker, Batch-Trimming, Ressourcen-Hard-Limits, Findings-Prozess mit Regressionstests). Die folgenden Befunde ergänzen die bestehenden Findings-Dokumente; sie sind nach Kritikalität priorisiert.

Bewertungslegenden: **K**ritisch · **W**ichtig · **O**ptimierung. „Aufwand“ schätzt die Umsetzung (S < 1 h, M < 1 Tag, L > 1 Tag).

---

## 1. Kritische Probleme (höchste Priorität)

### K1 · Bot-Thread respektiert `is_running=False` nicht – deaktivierte Bots können weiter handeln

**Dateien:** `trading/trading_bot.py` (`main_loop`, Zeilen 449–465), `trading/views.py` (`config_deactivate`, `bot_status_api`)

Der Bot aktualisiert die Konfiguration periodisch (`BOT_CONFIG_REFRESH_SECONDS`), prüft aber **niemals** `config.is_running` in der Loop. `Stop` läuft ausschließlich über `TradingBotManager.stop_bot()` → `bot.stop()` (Thread-Handle). Bei einer Restart-/Deactivate-Race bleibt ein lebender Bot mit `is_running=False` in der DB erhalten:

1. User speichert die laufende Konfiguration → `restart_bot()` startet einen Hintergrundthread (`wait_and_restart`), der nach `join()` der alten Bot-Referierung `prepare_and_start()` ausführt.
2. User klickt zeitgleich „deaktivieren“: `stop_bot()` trifft nur die **alte** (bereits sterbende) Referenz, DB schreibt `is_running=False`.
3. Der Restart-Thread hat `config.refresh_from_db()` **vor** dem DB-Write gelesen (`is_running=True`) und startet den Bot neu → **Bot handelt unbegrenzt weiter, obwohl deaktiviert**. `bot_status_api` startet sogar aggressiv nach, aber der umgekehrte Fall („sollte nicht laufen, läuft aber“) wird nirgends bereinigt.

Das ist kein rein kosmetisches Problem: Der Nutzer glaubt, der Bot sei aus (UI-Badge „Deaktiviert“), während Paper-Orders – und bei aktivierten Exchange-Keys derselbe Fetch-Pfad – weiterlaufen.

**Fix (robust unabhängig von der Race):** Im `main_loop` nach dem Config-Refresh selbst stoppen:

```python
# trading/trading_bot.py, in main_loop() nach:  self.config = await db_get_config(self.config_id)
if not self.config.is_running:
    logger.info("Bot %s: Deaktivierung erkannt; Loop beendet sauber", self.config_id)
    self.running = False
    break
```

Zusätzlich im `restart_bot`-Pfad vor `start_bot` ein letztes Mal die DB-Fahne prüfen (`Configuration.objects.filter(id=..., is_running=True).exists()`), um TOCTOU zu verkleinern. Ein Regressionstest kann die Sequenz „restart anstoßen → deaktivieren → Bot beobachtet Flag und beendet sich“ mit `countdown=0` und Fake-Exchange deterministisch abbilden.

**Warum wichtig:** Der zentrale Sicherheitsanker des Modells („UI sagt aus → Bot aus“) ist heute nur über die Manager-Locks garantiert, die bei Restart explizit **neben** dem Lock weiterlaufen (bewusst, um `join()` nicht zu blockieren). Ein selbstheilender Loop-Check macht die Deklarativ-Fahne `is_running` zur einzigen Wahrheitsquelle.

---

### K2 · State-Restart des Bots ist „fail-open“ – doppelte Kapitaleinsatz möglich

**Dateien:** `trading/trading_bot.py` (`_restore_state`, Zeilen 365–379; `db_restore_state` mit `@db_safe(suppress=True)`)

```python
def _restore_state(self):
    logs = db_restore_state(self.config_id) or []
    ...
    for log in logs:
        if log["action"] == "buy":
            self.positions[log["symbol"]] = {...}
```

`db_restore_state` ist mit `suppress=True` dekoriert: Bei offenem Circuit-Breaker oder erschöpften Reconnects liefert es **`None`** statt zu scheitern → `or []` → der Bot startet mit **leerer Positionsliste und `realized_pl = 0`**, obwohl die DB offene Positionen führt. `_available_capital()` rechnet dann ohne die gebundenen Mittel der historischen Positionen; die `check_trading`-Käufe in diesem Zustand verdoppeln faktisch das eingesetzte (simulierte) Kapital und die Verrechnungs-Basis aller folgenden Sells ist falsch. Das passiert genau dann, wenn die DB instabil ist – also unter Stress, nicht im Idealfall.

**Fix:** Fail-closed. Der Bot darf ohne verlässlichen State nicht starten:

```python
@db_safe(suppress=False)          # wirft nach erschöpften Reconnects
def db_restore_state(config_id):
    ...

def _restore_state(self):
    try:
        logs = db_restore_state(self.config_id)
    except (OperationalError, InterfaceError) as exc:
        raise RuntimeError(
            f"Zustands-Recovery für Bot {self.config_id} nicht möglich; Start verweigert"
        ) from exc
    ...
```

`start_bot()` (bzw. der Autostart in `apps.start_active_bots`) fängt den Fehler bereits und schreibt `ErrorLog` – der Bot wird dann beim nächsten `bot_status_api`-Poll automatisch erneut versucht. Alternativ: Start mit Retry-Backoff einplanen statt „stille null Positionen“.

**Warum wichtig:** Paper-Trading ist genau dann wertvoll, wenn die Simulation der Buchhaltung trustwürdig bleibt. Stille Rekonstruktionsfehler sind der teuerste Fehlertyp in Trading-Systemen (Order-Dupes, falscher P/L, unrevidierbare Historie).

---

### K3 · Ungebundenes Wachstum des Scanner-Caches → Memory-Leak im Langzeit-Webprozess

**Dateien:** `trading/market_scanner.py` (Zeilen 52–54, 830–839)

```python
_CACHE = {}                      # ← dict ohne Obergrenze, keine Eviction
...
with _CACHE_LOCK:
    _CACHE[key] = (now, result)  # key enthält 4 vom Nutzer gewählte floats
```

Der Cache-TTL steuert nur die **Verwendung**, nie das **Entfernen**: Jeder Eintrag bleibt prozesslebenslang im Dict. Der Schlüssel enthält die vier benutzergesteuerten Filter (Float-Fließkomma, praktisch unbegrenzte Wertemengen), und jeder Wert enthält bis zu ~120 Report-Zeilen inkl. `exclusion_reasons`. Ein einzelner Benutzer, der die Scanner-Slider bewegt (`/api/market-opportunities/?volatility_threshold=…`), füllt den Cache Zeile für Zeile – auf Render Free (512 MB) und im Docker-Stack (`WEB_MEMORY:-512m`) ist das ein schleichender OOM-Vektor für den gesamten Webprozess (Bots inklusive!).

**Fix (klein, sicher):** LRU mit harte Obergrenze + Opportune Eviction beim Schreiben:

```python
from collections import OrderedDict
_CACHE: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_CACHE_MAX_ENTRIES = 32

# beim Schreiben:
with _CACHE_LOCK:
    _CACHE[key] = (now, result)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)
```

Beim Lesen auf `_CACHE.get(key)` plus `move_to_end` (unter dem vorhandenen Lock) umstellen. Analog `_MARKET_CAP_CACHE` (Zeilen 451 ff.) absichern – der ist zwar durch die feste ID-Tupel-Obergrenze faktisch klein, eine einheitliche Strategie ist hier aber vorzuziehen.

**Warum wichtig:** Der einzige andere prozessinterne Cache mit Nutzerinput (`RateLimitMiddleware._attempts`) ist bereits explizit auf 10.000 IPs gedeckelt (`MAX_TRACKED_IPS`, fail-closed) – der Scanner-Cache ist der Ausreißer gegen die eigene Konvention des Projekts.

---

### K4 · `aiohttp` ist Import-Abhängigkeit, aber nicht in `requirements.txt` erklärt

**Dateien:** `trading/market_data.py` (Zeile 8: `import aiohttp`), `requirements.txt`

`aiohttp` wird für den Binance-WebSocket-Layer direkt importiert, ist aber nicht deklariert – es kommt aktuell nur **transitiv über `ccxt`** in die Umgebung (`pip show ccxt` → `Requires: ... aiohttp ...`). Sobald ccxt (z. B. bei einem Update) aiohttp optional stellt oder die Auflösung sich ändert, **crasht jeder Prozessstart mit `ModuleNotFoundError`** – Build-Check (`manage.py check`) und CI fallen darauf erst bei der nächsten ccxt-Bewegung rein. Pinning von ccxt allein genügt nicht, weil das Problem beim nächsten Dependency-Bump auftritt.

**Fix:**

```diff
 # requirements.txt
 ccxt==4.5.74
+aiohttp==3.12.15
```

(Version an die aktuell von ccxt mitgebrachte angleichen; `pip check` im CI fängt Inkompatibilitäten ohnehin ab.)

**Warum wichtig:** Explizite Abhängigkeiten für Module, die direkt importiert werden, sind Grundvoraussetzung reprozierbarer Produktion (die restlichen 19 Dependencies sind vorbildlich gepinnt – das ist ein Einzelfall-Ausreißer).

---

## 2. Wichtige Verbesserungen (mittlere Priorität)

### W1 · Ungültige `config_id`-GET-Parameter liefern 500 statt 400/404

**Dateien:** `trading/views.py` – `dashboard_view` (~Zeile 761), `data_logs_api` (991), `trades_api` (1024), `bot_status_api` (1162)

Reproduziert im Review (`get_object_or_404(Configuration, id="abc")`):

```
ValueError: Field 'id' expected a number but got 'abc'.   →  unbehandelt → HTTP 500
```

Alle vier Endpunkte übergeben den Rohtext aus `request.GET` direkt an `get_object_or_404`. Für URL-Parameter mit `<int:...>`-Converter macht Django das korrekt – bei Query-Parametern nicht. Ergebnis: Jede fehlerhafte/manipulierte URL erzeugt einen unnötigen 500-Interne-Server-Fehler (Log-Rauschen, Alarmierung, im Fehlerfall inkl. `ErrorLog`-Schreibversuchen durch Middleware).

**Fix (ein gemeinsamer Helper, z. B. in `views.py`):**

```python
from django.core.exceptions import ValidationError

def _config_id_or_404(request: HttpRequest) -> int | None:
    raw = (request.GET.get("config_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)          # IntegerField-Pfad: ValueError reicht als Guard
    except ValueError:
        raise Http404("config_id ist keine Zahl")
```

bzw. `get_object_or_404(...)` in `try/except (ValueError, ValidationError)` → `HttpResponseBadRequest`/404. Für die API-Endpunkte analog `JsonResponse({"error": ...}, status=400)`.

**Warum wichtig:** Robuste Eingabevalidierung ist auch ohne Sicherheitslücke produktionsrelevant: 500er auf Nutzer-Input verzerren Monitoring (Fehlerrate) und sind ein billiger Self-DoS-Hebel gegen die eigene Log-Pipeline.

### W2 · `info_api`/Dashboard: ~40+ Einzelabfragen pro Poll durch N+1 im Portfolio-Snapshot

**Dateien:** `trading/views.py` – `_portfolio_snapshot` (Zeilen ~278–328), `logs_api` (1197–1201)

Pro Symbol werden **zwei** separate Queries abgesetzt (letzter Trade, letzter Preis); bei 20 Symbolen sind das 40 Queries **pro Request**. Der Dashboard-JS-Poll ruft `info_api` + `logs_api` + 2 Chart-APIs alle 10 s → auf Render-Free-Postgres (wenige Connections, `conn_max_age=0`) unnötiger Dauerbeschuss. `logs_api` repliziert dieselbe N+1-Schleife zusätzlich (`open_symbols`-Fallback).

**Fix (ein Query statt 2·N):** „Latest row per group“ über ein subquery-basiertes Aggregat:

```python
from django.db.models import Max, Subquery, OuterRef

def _latest_rows_by_symbol(queryset, symbol_field="symbol"):
    latest_ids = (
        queryset.model.objects.filter(configuration_id=OuterRef("configuration_id"),
                                       **{symbol_field: OuterRef(symbol_field)})
        .order_by("-timestamp", "-id").values("id")[:1]
    )
    return queryset.filter(id=Subquery(latest_ids))
```

Pragmatischer (und auf SQLite/Postgres gleichermaßen wartbar): **ein** `values("symbol").annotate(...)`-Durchlauf oder – am einfachsten und wirksamsten – ein prozessinterner **2-Sekunden-TTL-Cache** auf `_portfolio_snapshot(config)`, keyed by `config.id` + letzter Log-ID. Die Werte sind ohnehin nur so frisch wie der Bot schreibt (`time_interval` ≥ 1 s). Analog für `logs_api`'s Fallback: `open_symbols` aus demselben Snapshot ableiten statt die Query-Schleife zu wiederholen.

**Warum wichtig:** Single-Instance-Deployment macht die Web-DB-Last zum primären Skalierungsengpass; die Dashboard-Aktualisierung muss mit wachsender Konfigurationsanzahl konstant bleiben (O(1) statt O(Symbole)).

### W3 · `analyse_view` materialisiert bis zu 200.000 ORM-Objekte im Webprozess

**Dateien:** `trading/views.py` – `analyse_view` (Zeilen ~2145 ff.)

```python
rows = list(DataLog.objects.filter(...)[:20_000])   # pro Symbol, bis zu 10 Symbole
...
for row in rows:
    bucket = int(row.timestamp.timestamp()) // bucket_size
    closes_by_bucket[bucket] = float(row.price)
```

Vollständige Modell-Instanzen mit 13 `Decimal`-Feldern werden geladen, um am Ende **pro Zeitbucket nur den letzten Preis** zu brauchen – und dabei sogar nur `timestamp` + `price`. Bei 10 Symbolen × 20k Zeilen = ~200k Objekte (Größenordnung >100 MB transient) im Daphne-Worker, der mit 512 MB Limit inkl. Bots laufen muss.

**Fix:** Projektion + Bucketing in die DB verlegen (Postgres):

```python
from django.db.models.functions import Trunc

rows = (
    DataLog.objects.filter(configuration_id=source_config_id, symbol=symbol)
    .annotate(bucket=Trunc("timestamp", timeframe))          # z. B. "hour"
    .values("bucket")
    .annotate(price=Max("price", order_by=("-timestamp", "-id")))  # letzter Abschluss
    .order_by("bucket")
)
```

(`Distinct`-bzw. Fenster-Funktion falls nötig; minimal-invasiv ist zunächst `values_list("timestamp", "flat=True")`-Variante + `.only("timestamp", "price")` – das eliminiert 90 % des Allokationsaufwands ohne SQL-Risiko.) Zusätzlich: 20k-Deckel ist hartkodiert – als Konstante neben `_MAX_API_ROWS` pflegen.

**Warum wichtig:** Ein View mit O(Zeilen×Symbole) Heap-Spitze ist der Kandidat Nr. 1 für OOM-Kills des Web-Containers, und OOMs töten hier auch alle laufenden Bot-Threads (Paper-Historie bleibt, Bot stoppt aber).

### W4 · `db_safe` hält den globalen Recovery-Lock über `time.sleep()` – alle Bots blockieren bis ~90 s

**Dateien:** `trading/trading_bot.py` – `db_safe` (Zeilen 78–140, Lock bei 101)

Bei einem DB-Ausfall führt der **erste** Fehler-Thread die koordinierte Reconnect-Sequenz aus (`sleep(1→2→4→8→16 s + Jitter)` × 5 Versuche) – und hält dabei `_DB_RECOVERY_LOCK`. Jeder andere Bot-DB-Aufruf (über den einen `_BOT_DB_EXECUTOR`-Thread, `BOT_DB_WORKERS=1`) läuft in die Warteschlange; die Event-Loops der Bots hängen an ihren `await db_*`-Calls. In dieser Zeit: keine Preisaktualisierung, Kill-Switch/Manual-Sell laufen in `future.result(timeout=…)`-Timeouts, `pending_trading_logs` füllen sich.

Die Circuit-Logik danach ist gut (Fail-fast), aber der erste Zyklus ist genau der Moment, in dem der Markt weiterläuft.

**Fix (klein):** Für `suppress=True`-Aufrufe (Buffer-/Write-Pfade) gar nicht in die Recovery-Schleife eintreten – sofort `None` zurück, Trade puffern, Recovery dem einen nicht-suppress-Aufruf (`db_get_config`) überlassen:

```python
except (InterfaceError, OperationalError) as exc:
    last_error = exc
    if suppress:                      # Schreib-/Bufferpfade: fail fast, puffern statt blockieren
        _open_db_circuit(last_error)  # Rest der Bot-Familie短路 über Circuit
        return None
    with _DB_RECOVERY_LOCK:
        ...                            # nur der Config-Pfad betreibt Recovery
```

Alternativ/ergänzend: `BOT_DB_WORKERS` auf 2 erhöhen (Write-Zweig und Recovery-Zweig trennen) – Kosten: 2 Postgres-Connections, auf dem Free-Tier vertretbar.

**Warum wichtig:** Failover-Verhalten unter Last ist die Kern-Eigenschaft des Long-Running-Teils der App. Die aktuelle Implementierung degradiert „DB kurz weg“ in „alle Bots + UI hängen bis zu anderthalb Minuten“, obwohl DataLog/TradingLog-Puffer exakt dafür gebaut sind, weiterzulaufen.

### W5 · `TradingBot.__init__` (inkl. DB-Restore) läuft unter dem Manager-Rock – Start serialisiert sich über alle Views

**Dateien:** `trading/trading_bot.py` – `TradingBotManager.start_bot` (868–881); `BUG-12`-Finding dokumentiert es bereits als „Abgrenzung“

```python
with self._lock:
    ...
    bot = TradingBot(config, on_exit=self._forget)   # ← _restore_state = DB-Fullscan der TradingLogs
    self.bots[config.id] = bot
```

Konstruktion inkl. State-Recovery (bis zu zehntausende Log-Zeilen!) und Exchange-Setup laufen **unter** `self._lock`. Jeder parallele `status()`/`open_symbols()`-Aufruf (Dashboard-Poll!) blockiert im ungünstigsten Fall dahinter. BUG-12 markiert das explizit als Nicht-Ziel – es bleibt aber der einzige Ort, an dem ein einzelner Bot-Start das gesamte Status-API lahmlegen kann.

**Fix:** Konstruieren außerhalb, Insertion innerhalb des Locks; Doppelstart-Schutz über ein „pending“-Sentinel:

```python
def start_bot(self, config):
    with self._lock:
        if self._is_running_unlocked(config.id):
            return self.bots[config.id]
        if config.id in self._starting:
            return None                      # oder warten lassen
        self._starting.add(config.id)
    try:
        bot = TradingBot(config, on_exit=self._forget)   # kein Lock hier
    finally:
        with self._lock:
            self._starting.discard(config.id)
            if self._is_running_unlocked(config.id):    # jemand anders hat inzwischen gestartet
                bot.stop()
                return self.bots[config.id]
            self.bots[config.id] = bot
    bot.start()
    return bot
```

**Warum wichtig:** Start-Races wurden mit BUG-12 adressiert; die Lock-Haltedauer ist der verbleibende Teil desselben Problems (Tail-Latency des Status-Endpunkts, der seinerseits Auto-Restarts triggert).

### W6 · `open_symbols` liest `bot.positions` aus dem View-Thread – potenzieller `RuntimeError`

**Dateien:** `trading/trading_bot.py` – `TradingBotManager.open_symbols` (Zeilen 957–960), Aufrufer `views.logs_api` (1196)

`sorted(bot.positions)` iteriert ein Dict, das der Event-Loop-Thread des Bots gleichzeitig mutiert (Execute-Trade via `manual_sell`/Kill-Switch/main loop). CPython kann dabei `RuntimeError: dictionary changed size during iteration` werfen – der Fehler wirft durch `logs_api` → 500 (HTML) auf einen JSON-Client.

**Fix (einzeilig):**

```python
def open_symbols(self, config_id):
    with self._lock:
        bot = self.bots.get(config_id)
        if bot and bot.is_alive():
            return sorted(list(bot.positions))   # ← Snapshot-Kopie, atomare Dict-Leseoperation
        return []
```

Noch sauberer: `asyncio.run_coroutine_threadsafe(lambda: list(bot.positions), bot.loop).result(timeout=2)` – identisch zu `manual_sell` und damit garantierter Single-Writer.

### W7 · Geplante Backtests hängen im `backtest`-Queue hinter laufenden Backtests; Beat-Task erzwingt Worker-Recyclings pro Minute

**Dateien:** `trading_bot_project/celery_config.py` (`task_routes` → `schedule_backtests` auf Queue `backtest`), `docker/worker-entrypoint.sh` (`-Q backtest`, `--concurrency=1`, `--max-tasks-per-child=1`), `trading_bot_project/celery.py` (`crontab()` = minütlich)

Zwei Effekte:

1. Ein 50-Minuten-Backtest blockiert den einzigen Worker **und damit den Scheduler-Task selbst** – alle `scheduled`-Backtests starten verspätet; „genau einmal zur geplanten Zeit“ gilt nur bei freiem Queue.
2. `max_tasks_per_child=1` lässt den Worker-Kindprozess nach **jedem** Beat-Tick neu forken (Django-Import ~1 s), 60×/h – CPU-Churn auf 0.5-CPU-Containern.

**Fix:** Eigenen Queue für Zeitplanung:

```python
"trading.tasks.schedule_backtests": {"queue": "scheduling"},
```

und Worker: `-Q backtest,scheduling` (Scheduling-Aufgaben sind kurz; Isolation des schweren Backtests bleibt). Wenn die Kind-Prozess-Recycling-Kosten stören, `schedule_backtests` auf einen separaten, schlanken Worker mit `--max-tasks-per-child=1000` legen. Alternativ dokumentiert der `run_scheduled_backtests`-Management-Command schon den Cron-Pfad – dann Beat ganz aus der Backtest-Queue nehmen.

**Warum wichtig:** Die Scheduler-Latenz ist ein funktionaler Bug (Feature „Backtest planen“ verfehlt die geplante Zeit), das Recycling ein Betriebskosten-Problem auf Free-Tier-Hardware.

### W8 · PDF-/HTML-Reports laden die komplette Trade-Historie unlimitiert

**Dateien:** `trading/views.py` – `_build_report_context` (Zeile ~1442: `logs = list(config.logs.all())`), Vergleich: alle API-Pfade deckeln mit `_MAX_LOG_ROWS`/`_MAX_API_ROWS`

Beim Trading alle 5 s können nach Wochen >100k `TradingLog`-Zeilen auflaufen. `generate_report` materialisiert **alle** in Python (inkl. Charts) und übergibt sie an WeasyPrint – der Request skaliert linear in Zeit und Speicher und kann den Web-Container (512 MB!) in den OOM treiben. Der CSV-Export ist korrekt gestreamt (`iterator(chunk_size=1000)`), PDF/HTML nicht.

**Fix:** Fenster ziehen (wie überall sonst) + `prefetch_related` vermeiden:

```python
logs = _latest_rows(config.logs.all(), _MAX_REPORT_ROWS)   # z. B. 10_000
context["history_truncated"] = not config.logs.filter(id__lt=logs[0].id).exists()
# und im Template einen Hinweis „Bericht auf die letzten N Trades begrenzt“ ausgeben
```

**Warum wichtig:** Reports sind genau die Funktion, die auf langen Live-Phasen ausgeführt wird; eine OOM tötet hier nicht nur den Request, sondern (Shared-Prozess) die laufenden Bots.

### W9 · Irreführende/deaktivierte Modellfelder: `leverage`, `trade_direction`, `has_live_credentials`

**Dateien:** `trading/models.py` (29–43, 118–123), `trading/forms.py` (129, 136–137)

Die drei Felder sind in Konfigurationsformular und Admin **editierbar**, haben aber **null** Auswirkung auf `trading_bot.py` (grep: keine Referenz). Konkret problematisch:

- `has_live_credentials` heißt im UI „Echtzeit-Handel aktiviert“ – es wird aber **nichts** live gesendet (Order-Code existiert nicht). Nutzer mit echten API-Keys können fälschlich annehmen, der Schalter löse Live-Trading aus – bei einem Tool mit API-Keys im Env ist die Erwartungsklarheit Sicherheitsfach (Gefahr → Verwirrung → Fehlentscheidungen).
- `trade_direction` ist ein `CharField(max_length=10)` **ohne `choices`** → Freitext (Validierung nur durch Länge).
- `leverage` validiert `MinValueValidator(0)`, obwohl Help-Text „1 = kein Hebel“ sagt; 0 ist unzulässig.

**Fix:** Kurzfristig ehrlich beschriften oder aus dem Formular entfernen (Model-Felder für zukünftige Nutzung bleiben):

```python
# forms.Meta: fields = [ ... ]   → "leverage", "trade_direction", "has_live_credentials" raus
# models.py:
trade_direction = models.CharField(max_length=10, choices=[("long", "Long"), ("short", "Short")], default="long")
leverage = models.IntegerField(validators=[MinValueValidator(1)], ...)
```

Falls die Felder bewusst als Platzhalter dienen: im Help-Text auf „derzeit wirkungslos (nur Datenbank-Attr)“ hinweisen – und die Admin-`fieldsets` entsprechend kommentieren.

### W10 · `?refresh=1` umgeht den Scanner-Cache ohne Throttling → Self-DoS / Exchange-Ban-Risiko

**Dateien:** `trading/views.py` – `market_opportunities_api` (Zeilen ~890–960), `trading/market_scanner.py` (TTL-Check Zeile 834: `and not refresh`)

`scan_all_market_opportunities` lädt pro Exchange Marktkatalog + Ticker + bis zu 20 Orderbücher **synchron im Request**; mit `exchange=all` summiert sich das auf zweistellige Sekunden. Der Refresh-Parameter hebt den 60-s-Cache pro Aufruf auf – ein klientenseitiger Loop (oder zwei Browser-Tabs mit Auto-Refactor auf Refresh) erzeugt Rate-Limit-Bans der **geteilten Client-IP** gegen Binance/Coingecko, die dann Bot-Validierung und Konfig-Seiten mitbetreffen (429/418 → `RateLimitError`-Backoff in `market_data`).

**Fix:** Minimaler Zwangsabstand für erzwungene Refreshes (prozesslokal reicht für Single-Instance):

```python
_LAST_FORCED_REFRESH: dict[tuple, float] = {}
MIN_REFRESH_SPACING = 20  # Sekunden

def _throttled_refresh(key):
    now = time.monotonic()
    last = _LAST_FORCED_REFRESH.get(key, 0)
    if now - last < MIN_REFRESH_SPACING:
        return False                     # ignoriere refresh, bediene aus altem Cache
    _LAST_FORCED_REFRESH[key] = now
    return True
```

**Warum wichtig:** Die Exchange-IP-Bans sind das, was den Betrieb des Bots selbst gefährdet (REST-Validierung teilt sich die IP) – Schutz gehört an die API-Grenze, nicht in die Nutzerdisziplin.

---

## 3. Empfohlene Optimierungen & Qualitätsmaßnahmen (niedrige Priorität)

### O1 · Linter-Version in CI pinnt nicht – Regelwerk-Ergebnisse driften unbemerkt

`.github/workflows/quality.yml`: `pip install -r requirements.txt ruff shellcheck-py==0.11.0.1` – **shellcheck ist gepinnt** (mit exzellenter Begründung im Kommentar), ruff nicht. Verifiziert: ruff 0.6.9 meldet `E402` in `trading_bot_project/asgi.py:13`, ruff 0.16.6 schweigt – d. h. Befunde existieren je nach CI-Zeitpunkt unterschiedlich; lokal reproduzierbar wird der Gate unzuverlässig, und ein ruff-Release kann den Gate über Nacht rot kippen. Fix: `ruff==<pin>` im Workflow (oder in einer `requirements-dev.txt`), `asgi.py` bei Gelegenheit mit `# noqa: E402` dokumentieren (Import nach `get_asgi_application()` ist dort gewollt).

### O2 · mypy-Konfiguration existiert, läuft aber in keinem Gate

`pyproject.toml` konfiguriert mypy+django-stubs scoped auf `trading/views.py` inkl. Overrides – ausgeführt wird es nirgends (nur Doku-Kommentar). Da genau dort die generischen Decorator-Typen (`no_cache_json`, `_ViewParams`) den Zweck haben, statische Prüfer sauber zu halten, ist das verschenktes Potenzial. Fix: Job in `quality.yml` ergänzen (`pip install mypy==1.18.2 django-stubs==5.2.7 && mypy`), Scope schrittweise erweitern (nächste Kandidaten: `trading_bot.py`, `tasks.py`).

### O3 · Redundante Doppel-Cachierung im Handbuch-Renderer

`views._render_manual` kombiniert `@lru_cache(maxsize=1)` **und** ein Modulglobales `_MANUAL_HTML` mit eigenem Lock – beide Schichten cachen dieselbe Zeichenkette. Das `cache_clear`-Monkey-Patching (`_render_manual.cache_clear = _clear_manual_cache  # type: ignore[method-assign]`) compensiert die Doppelung. Einfacher und identisch sicher: eine Schicht (Lock + Global) und `_render_manual =` ohne lru_cache, oder – für Testbarkeit schöner – ein explizites `manual_cache`-Objekt mit `get()/clear()`. Aktuell kein Bug, aber wartungsgefährlicher Zauber (die Kommentare erklären ihn – das ändert nichts daran, dass Weglassen beider Ebenen denselben Zweck erfüllt).

### O4 · Countdown nutzt Wanduhr `time.time()`

`TradingBot.__init__`: `self.start_time = time.time() + countdown*60`; Prüfung in `check_trading` mit `time.time()`. Ein NTP-Sprung (typisch auf VMs/Containern) verlängert oder überspringt den Count-Down. Alle anderen Timer im Projekt nutzen bereits korrekt `time.monotonic()` (z. B. `_last_config_refresh`). Umstellung: `self._start_deadline = time.monotonic() + countdown*60`. (Nur cosmetic-fest, weil Countdown rein kosmetisch Startverzögerung ist.)

### O5 · Retention: `TradingLog`/`ErrorLog` wachsen unlimitiert

`DataLog` wird beschnitten (`db_trim_datalog`, `MAX_DATA_LOGS_PER_SYMBOL`), `TradingLog` und `ErrorLog` nie. Bei 5-Sekunden-Intervallen mit vielen Fills pro Tag und ErrorLog-Churn (verschiedene Error-Keys werden **nicht** dedupliziert, nur identische Texte pro Key) sind Tabellen über Jahre beliebig groß – `logs_api`'s `COUNT(*)` pro Poll wird dadurch langsam, und `analyse_view`/Reports oben drauf. Vorschlag: analoges Batch-Trimming (z. B. `MAX_TRADING_LOGS_PER_CONFIG=200_000`, `MAX_ERRORLOG_ROWS=50_000`) im Task-Ökosystem oder als nächtlicher Management-Command; Plus: `ErrorLog` könnte `resolved AND severity='info'` nach 30 Tagen löschen.

### O6 · Health-Check prüft nicht die DB → LB/Compose markiert Container als „gesund“ bei DB-Ausfall

`health_view` liefert immer 200; der Compose-Healthcheck und Render nutzen genau diesen. Das ist als *Liveness* korrekt (Prozess lebt), aber als *Readiness* irreführend: Ein Container ohne DB hängt im „healthy“-Zustand und fängt Traffic ab. Vorschlag: `/health/` wie bisher (billig, für Restart-Politik) + `/readyz/` mit `connection.ensure_connection()` und `Retry-After`-Semantik; Render kann letzteres als Health-Pfad bekommen, Daphne-Reload-Politik bleibt beim Liveness-Endpunkt.

### O7 · „Dead-Code“-Kandidaten entfernen oder explizit verankern

Per grep nur an der Definitionsstelle referenziert: `BinancePublicMarketData.validate_symbols_async` (market_data.py), `tasks._historical_prices`, `market_scanner.get_top_gainers/get_top_losers`, `resource_optimizer.ResourceSnapshotCache`/`detect_server_resources`, und `clear_api_keys` (Management-Command, dessen Felder seit Migration 0013 nicht mehr existieren – er läuft nur noch im `except`-Zweig). Manche sind bewusst als Integrations-API dokumentiert – dann gehört ein Verweis in `CONTRIBUTING.md`/Docs daneben, sonst gilt die Repo-Konvention (Code, den nichts aufruft, wird entfernt). Der `clear_api_keys`-Command kann komplett weg (Datenbankschema erlaubt keine Keys mehr).

### O8 · `manual_sell`-Timeout und gepufferte Ausführung: UX-Klarheit

`future.result(timeout=10)` in `TradingBotManager.manual_sell` → bei DB-Stau wirft der View „Verkauf fehlgeschlagen“, obwohl der Sell später durchlauft (Puffer) und die Position schon geschlossen war. Der Nutzer klickt erneut → „Keine offene Position“ (400). Sauberer: Nach Timeout den Bot-State abfragen (`bot_manager.open_symbols`) und `{"status":"accepted_delayed"}` zurückgeben; das Frontend zeigt dann „wird nachgeliefert“ statt Fehler. Billig umzusetzen, verhindert Doppelklick-Panik im genauen Stressfall.

### O9 · Kleinigkeiten

- `tasks._parameter_values` zählt die Rasterwerte exakt per Decimal-Schleife (`while current <= end`), die View schätzt sie über `_combination_count` mit Float-Arithmetik plus `1e-9`-Epsilon. Weichen beide um eins ab, passiert genau das: Formular lässt den Start zu, der Task bricht mit „max. Kombinationen überschritten“ ab. Konsistent machen, indem die View dieselbe Decimal-Zählung verwendet (z. B. gemeinsamen Helper `len(parameter_values(...))` statt Formel) – billiger als ein unnötiger Task-Fehlschlag.
- `market_scanner._BitunixScannerExchange.fetch_tickers` (Spot) schläft `0.11 s` **pro Symbol sequenziell** im Request – bei 50+ zugelassenen Kandidaten >5 s nur an Sleeps; ein kleines `ThreadPoolExecutor(max_workers=2)` mit Rate-Token oder Vorab-Begrenzung auf 10 Kandidaten für Spot-Klines wäre freundlich.
- `docker-entrypoint.sh` migriert bei jedem Start `--noinput`: Bei Compose-Restarts von mehreren Containern gleichzeitig (nur `web` migriert – gut) ist die Sequenz fein; für Render sollte die Migration aus dem Web-Entrypoint in einen Pre-Detach-Step (`render.yaml` unterstützt `preDeployCommand`) – vermeidet Migrations-Races bei Rolling Deploys bezahlter Pläne.
- `requirements.txt` enthält `requests` **und** `ccxt` (das `requests` ohnehin zieht) – ok; für vollständige Deterministenz wäre `pip install --require-hashes` mit `pip-tools`/`uv lock` der Goldstandard.
- Testsuite: sehr ordentlich (330 Tests, inkl. Source-Pattern-Tests gegen die BUG-12-Rückfallmuster). Ergänzen: Regressionstests zu K1/K2/W1/W6 (je einer), die die hier gezeigten Fixes verankern; die Fundstellen-Nr. als Finding-ID-Konvention fortführen (`docs/findings/`-Template vorhanden).

---

## 4. Was bereits gut ist (bewusst festgehalten)

- **Sicherheits-Grundlinie:** CSRF-POST-only für Zustandsänderungen, `@sensitive_post_parameters`, `constant_time_compare` für die Passphrase, Session-Key-Rotation bei Login/Logout/Gate, HMAC-gebundenes Gate-Token (rotiert mit dem Secret), CSP mit Nonces + `no-store` auf allen JSON-Endpunkten, Fehler-Disclosure auf feste Benutzermeldungen reduziert (Diagnose → `ErrorLog`), Rate-Limit mit bewusster Proxy-Allowlist (kein blindes XFF-Vertrauen).
- **Data-Integrität:** durchgängig `Decimal` mit zentraler `_bounded`-Quantisierung; Indikator-Arithmetik in `trading/indicators.py` dedupliziert (Bot/Backtest teilen sich Guards/Formeln – genau die richtige Lösung für das Drift-Problem); Batch-löschender Trim statt Big-DROP; savepoint-geschütztes Error-Logging.
- **Robustheit:** Circuit-Breaker + koordinierter Reconnect, Trade-Puffer mit Vollschutz („kein Handel ohne Journal“), WebSocket-Endpoint-Rotation mit 23-h-Reconnect vor Binance-Timeout, pro-Bot-Fehlerdeduplizierung (15-min-Ident-Text-Filter), Backtest-Hard-Limits aus Hardware-Profil inkl. cgroup-Erkennung.
- **Prozesskultur:** Findings-Dokumente je Befund, ADR für Worker-Isolation, Doku-Wächter in CI, gepinnte Dependencies (bis K4), 330 grüne Tests, Shellcheck + eigene Shell-Testsuite.

## 5. Empfohlene Abarbeitungsreihenfolge

| # | Befund | Aufwand | Risiko ohne Fix |
|---|--------|---------|-----------------|
| 1 | K1 `is_running` in Bot-Loop | S | Deaktivierter Bot handelt weiter |
| 2 | K2 Fail-closed State-Restore | M | Doppeltes Kapital, verfälschte Historie |
| 3 | K3 LRU für Scanner-Cache | S | Schleichender OOM |
| 4 | K4 `aiohttp` pinnen | S | Bruch beim nächsten ccxt-Update |
| 5 | W1 400 bei ungültiger config_id | S | 500-Rauschen, Log-Missbrauch |
| 6 | W6 Positions-Snapshot | S | Sporadische 500er im Log-API |
| 7 | W2/W8 Portfolio-Cache + Report-Limit | M | DB-/RAM-Drift mit Historie |
| 8 | W4/W5 Recovery-Lock & Start-Lock | M | Multi-Bot-Hang bei DB-Stör |
| 9 | W7 Scheduler-Queue | S–M | Verpasste geplante Backtests |
| 10 | W3 `analyse_view` Project Pushdown | M | OOM-Kandidat |
| 11 | W9 Feld-Cleanup, W10 Refresh-Throttle | S | UX/Rate-Limit-Ban |
| 12 | O1/O2 CI-Hygiene (ruff-pin, mypy) | S | Gate-Drift |
| 13 | Übrige O-Punkte | S–M | Nach Pull |

Jeder Fix ist einzeln merge-bar; zu 1–3, 5, 6 empfehle ich Regressionstests direkt im selben Patch (Schema wie `docs/findings/TEMPLATE.md`, IDs `BUG-22` (K1), `BUG-23` (K2), `PERF-24` (K3), `CODE-25` (K4) usf., wenn die Zählung fortgeführt wird).

---

*Review durchgeführt gegen Stand 2026-09-09, Commit `727d3ee`. Verifikationsmaterial: vollständiger `manage.py test`-Lauf (330 OK), `ruff` (0.6.9/0.16.6), `bandit -ll` (keine unbehandelten Funde), reproduzierte `ValueError`-Pfade und Slice-Aggregat-Verhalten (`_calculate_metrics_from_db` ist korrekt – LIMIT wirkt in Django 5.2, getestet).*
