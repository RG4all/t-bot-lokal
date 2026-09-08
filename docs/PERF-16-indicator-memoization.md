# PERF-16 – IndicatorMemoization

- **Status:** Fixed
- **Kategorie:** MEDIUM – Performance / Ressourcenbegrenzung
- **Geprüfter Ausgangsstand:** 2.4.13 (`e84456f0b7dbd96029e7e9012572a652459b59d2`)
- **Behoben in:** 2.4.14 · **Datum:** 2026-09-08
- **Fix-Commit:** [`4ca66dc`](https://github.com/RG4all/t-bot-lokal/commit/4ca66dc71db2b2702ae75793cceaa26d6905e6fb)
  – `perf(backtesting): add indicator memoization cache`
- **Referenzen:** [Audit §4.1](SECURITY_AUDIT.md), [Prompt 16](ARENA_AI_PROMPTS.md)

## Befund und Einordnung

`Backtesting.calculate_indicators()` berechnete wiederholte Zugriffe auf
identische Preispunkte erneut mit Decimal. Der aktuelle `run_backtest()`
berechnete allerdings bereits einmal pro Symbol `indicator_rows` und reichte
sie an alle Rasterkandidaten weiter. Die historische Behauptung von
100 Millionen wiederholten Indikatorberechnungen bei 20.000 Kandidaten und
5.000 Preisen trifft auf den geprüften Stand nicht zu. Redundanz bestand
insbesondere beim Gewinner-Report sowie bei direkten wiederholten Aufrufen.
Dies ist ein Performance-Finding, keine nachgewiesene ausnutzbare Sicherheitslücke.

Das Dictionary aus dem historischen Lösungsvorschlag wäre kein LRU gewesen.
Ohne Lebenszeitbindung könnte `id(prices)` außerdem später zu einer anderen
Liste gehören. Ein unbegrenzter globaler Cache würde selbst ein neues
Ressourcenproblem schaffen.

## Fix

- Prozesslokales `OrderedDict` mit maximal **8.192 Einträgen**, Schlüssel
  **`(id(prices), idx)`** und `threading.Lock`. Treffer aktualisieren die
  LRU-Reihenfolge; bei Überschreitung wird der älteste Eintrag entfernt.
- Lookup, Berechnung und Einfügen liegen unter demselben Lock. Gleichzeitige
  Zugriffe berechnen denselben fehlenden Eintrag nur einmal; `clear` kann
  nicht von einer bereits begonnenen Berechnung nachträglich aufgehoben werden.
- Jeder Eintrag hält die ursprüngliche Preisliste stark referenziert. Dadurch
  kann Python deren Identität bis zur Eviction/Bereinigung nicht erneut vergeben.
  Die drei beteiligten Preisobjekte werden auf Identität geprüft, sodass auch
  ersetzte Listenelemente keine veralteten Ergebnisse liefern.
- Kontextabhängige Decimal-Ergebnisse werden nur bei passender Präzision,
  Rundung, Exponentengrenzen, Clamp und Trap-Konfiguration wiederverwendet.
  Cache-Treffer geben die Signal-Flags der ursprünglichen Berechnung wieder,
  ohne fremde Flags zu übernehmen oder bestehende Flags zu löschen.
  Ausnahmen werden nicht als Ergebnisse gespeichert.
- Unveränderte Formeln in `_calculate_indicators()`; keine Float-Konvertierung.
  Direkte Simulationen verwenden für Indikatoren die ursprüngliche Liste
  statt einer pro Kandidat neu angelegten Decimal-Listenkopie. Iteratoren
  werden weiterhin akzeptiert; vorberechnete `indicator_rows` bleiben erhalten.
- `clear_indicator_cache()` wird in **`finally`** von `run_backtest()` und
  dem kompatiblen Einzelkandidaten-Task `simulate_candidate()` aufgerufen:
  Erfolg, Abbruch, fehlender Task und Fehler einschließlich DB-Fehlern bei der
  Fehlerbehandlung geben den Cache frei.

Keine neue Abhängigkeit, Konfiguration oder Migration. `VERSION` ist die
zentrale Versionsquelle; `pyproject.toml` enthält keine separate Paketversion.

## Regressionstests: rot → grün

Neue Datei: `trading/tests/test_indicator_memoization.py`, **18 Tests**.
Die ersten 13 Tests wurden vor dem Fix ausgeführt. Wiederholte Zugriffe
lieferten **303 statt 3** `_decimal()`-Aufrufe, parallele Zugriffe **300 statt 3**;
Simulationen verwendeten neue Listenidentitäten. Tests für die neue Cache-API
scheiterten außerdem an den noch fehlenden Methoden/Attributen. Nach dem Fix
bestehen diese und die ergänzten Randfalltests:

- Treffer ohne erneute Decimal-Konvertierung, Formelgleichheit und Nullpreise;
- LRU-Reihenfolge, feste Kapazität auch bei vielen verschiedenen Listen,
  getrennte Listenidentitäten und Freigabe starker Referenzen;
- explizites Leeren, konkurrierende Treffer sowie konkurrierendes Leeren;
- ungültige Indizes, ersetzte/gekürzte Preise und nicht gecachte Ausnahmen;
- Decimal-Kontextwechsel, aktivierte Traps sowie korrekte Signal-Flags;
- wiederholte Simulationen und Iterator-Kompatibilität;
- Task-Bereinigung bei Erfolg, Abbruch, fehlendem Task, Simulationsfehlern
  und scheiternden Datenbank-Schreibzugriffen; kompatibler Einzelkandidaten-Task.

Zeitmessungen sind bewusst keine flakigen Unit-Test-Assertions. Der
Operationszähler ist der deterministische Performance-Regressionsnachweis.

## Messung

Reproduzierbar ohne Django/DB:

```bash
python scripts/benchmark_indicator_cache.py
```

Lokale Messung mit Python **3.11.2**, 5.000 Preispunkten, 20 Wiederholungen,
99.960 Aufrufen je Stichprobe und Median aus fünf Stichproben mit wechselnder
Messreihenfolge:

| Messwert | Ergebnis |
| --- | ---: |
| Erstmaliges Befüllen (4.998 Einträge) | 0,026768 s |
| Ungecachte Decimal-Referenz | 0,146597 s |
| Warmer LRU-Cache | 0,112221 s |
| Beschleunigung wiederholter Indikatoraufrufe | **1,31×** |

Der Benchmark prüft auch die vollständige Ergebnisgleichheit. Dies ist ein
**Mikrobenchmark**, keine zugesicherte Beschleunigung des gesamten Backtests.
Kalte Zugriffe sind durch Synchronisierung, Kontextprüfung und Speicherung
teurer; kurze Läufe mit wenigen Wiederholungen können langsamer werden.
Das bereits vorab berechnete Kandidatenraster profitiert nicht erneut pro
Kandidat. Bei mehr als 8.192 aktiven Paaren können LRU-Verdrängungen den Nutzen
reduzieren. Die Grenze zählt Einträge, nicht Bytes; referenzierte Preislisten
werden erst mit ihrem letzten Cache-Eintrag freigegeben. Die bestehenden
Backtest-Eingabegrenzen bleiben unverändert.

## Validierung und CI-Grenze

Lokal bestanden: **260 Django-/Python-Tests**, **8/8 Shell-Testgruppen**,
Ruff **0.16.6**, ShellCheck **0.11.0**, Django-Systemcheck,
`makemigrations --check --dry-run`, `collectstatic` und `pip check`.
Runtime-Abhängigkeiten unverändert aus `requirements.txt`, Django **5.2.17**.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
bash tests/run_tests.sh
python manage.py test --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python -m pip check
python scripts/benchmark_indicator_cache.py
```

Im Repository ist kein GitHub-Actions-Anwendungstestworkflow versioniert.
Die GitHub-API führt nur die dynamische **Dependency Graph**-Integration;
sie ersetzt keine Anwendungstests. Ein erfolgreicher Anwendungstest-CI-Lauf
wird daher nicht behauptet. Die Abfrage der Actions-Berechtigungen wurde von
GitHub mit HTTP 403 (`Resource not accessible by integration`) abgewiesen.
Der aktuelle Branch-/Check-Stand ist vor der PR-Erstellung zu prüfen; die
historische lokale CI-Ausnahme für 2.4.13 gilt nicht automatisch für dieses Release.

Nicht geprüft: Docker-/Compose-Laufzeit (Docker lokal nicht verfügbar),
PostgreSQL-/Celery-Mehrprozessbetrieb und End-to-End-Lastmessung. Lokale
Anwendungstests verwenden SQLite. Parallele Backtests im gleichen Prozess
können einander beim Leeren lediglich Cache-Treffer nehmen; Preisidentitäten
und Kontextprüfung verhindern Ergebnisvermischung. Andere Prozesse besitzen
unabhängige Caches. Hart beendete Worker geben ihren Speicher mit dem Prozess frei.

## Upgrade und Nutzung

Nach Deployment Web-/Celery-Prozesse neu starten und `/health/` auf **2.4.14**
prüfen. Direkte Python-Aufrufer außerhalb der Tasks müssen ihren Lauf so beenden:

```python
try:
    result = Backtesting.compute_indicator_series(prices)
finally:
    Backtesting.clear_indicator_cache()
```

Preislisten während eines Laufs nicht parallel verändern. Unterstützte
Preiswerte sind unveränderliche Decimal-/Zahlen-/Stringwerte; beliebige
in-place veränderliche Preisobjekte sind nicht Teil des Cache-Vertrags.
