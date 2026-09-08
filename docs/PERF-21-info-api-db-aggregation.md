# PERF-21 – info_api: Kennzahlen per DB-Aggregation

- **Finding:** `InfoApiMemoryOptimization` (Prompt 21 / Security-Audit §4.6)
- **Status:** **Fixed**
- **Release:** **2.4.18** · **Datum:** 2026-09-08
- **Einstufung:** MEDIUM – Performance (keine Sicherheitslücke)
- **Geprüfter Ausgangsstand:** 2.4.17 / `62af6cfa8b26f312ba5f5ca0191a0bb2ebddc8fe`
- **Fix-PR:** [PR #26 – `perf(views): optimize info_api with DB aggregation`](https://github.com/RG4all/t-bot-lokal/pull/26)

## Befund und Root Cause

`info_api()` in `trading/views.py` (historische Zeilen 677–762) liefert
Portfolio-Metriken, Performance-Kennzahlen, Equity- und Kassenkurve einer
Konfiguration. Für die Performance-Kennzahlen lud die View bis zu
`_MAX_LOG_ROWS` (2.000) `TradingLog`-Zeilen in den Speicher und übergab sie an
`calculate_performance_metrics()`:

```python
logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
metrics = calculate_performance_metrics(logs)
```

`calculate_performance_metrics()` materialisierte daraus mehrere Listen aus bis
zu 2.000 Gleitkommawerten (`sell_profits`, `wins`, `losses`) und durchlief die
Logs spaltenweise in Python, um `win_rate`, `avg_profit`, `avg_win`,
`avg_loss`, `risk_reward`, `profit_factor`, `max_win` und `max_loss` zu
berechnen. Bei großen Konfigurationen belastet das den Web-Prozess mit
unnötigem RAM (Tausende Zeilen im Speicher) und CPU (mehrfache Schleifendurchläufe
über alle Zeilen). Ein Datenverlust oder eine Korruptur entsteht nicht; der
Befund ist eine Performance-/Ressourcen-Schwäche (Speicher- und CPU-Last), kein
Sicherheitsproblem.

## Fix und Umfang

Neu berechnet `_calculate_metrics_from_db(config, limit=_MAX_LOG_ROWS)` die
Zähler und Summen über `django.db.models.aggregate()` direkt im DBMS:

1. Es wird das Fenster der jüngsten `limit` Logs gebildet
   (`config.logs.all().order_by("-timestamp", "-id")[:limit]`). `aggregate()`
   wertet das angewandte `LIMIT` aus, sodass ausschließlich dieser Ausschnitt
   zählt und nicht die gesamte Historie der Konfiguration.
2. In **einem** Aggregat-Query werden `wins`, `losses`, `total_pl`,
   `gross_profit`, `gross_loss` (jeweils gefiltert über `Q(action="sell", ...)`),
   `max_win` und `min_loss` (über `Max`/`Min` mit `action="sell"`) ermittelt.
   Statt Tausender Zeilen werden nur wenige Aggregatwerte übertragen und im
   Speicher gehalten.
3. Die Skalarmathematik (Quotienten, Rundung) erfolgt bewusst in Python über
   die aggregierten Werte – exakt wie `calculate_performance_metrics()`, sodass
   die API weiterhin dieselben Kennzahlen liefert.

`info_api()` ruft nun `_calculate_metrics_from_db(config, _MAX_LOG_ROWS)` statt
`calculate_performance_metrics(logs)`. Die Equity-/Kassenkurve benötigt
weiterhin die einzelnen Zeilen und lädt sie wie bisher über `_latest_rows()`.

**Abweichung vom historischen Lösungsvorschlag (Prompt/Audit §4.6):** Die dort
skizzierte Variante `avg_profit=Sum("pl_nominal") / Count("id")` ist im
gepinnten Django 5.2.17 **fachlich falsch** – PostgreSQL führt diese Division
als Ganzzahldivision aus und liefert verkehrte Kennzahlen; zudem wäre das
Ergebnis nicht backend-unabhängig (SQLite in der Testumgebung teilt dagegen im
Gleitkomma). Die umgesetzte Funktion aggregiert nur Zähler, Summen, `Max` und
`Min` auf der Datenbank und rechnet die Quotienten in Python, sodass das
Ergebnis bitgenau zu `calculate_performance_metrics()` bleibt.

Unverändert bleiben: das betrachtete Historienfenster (neueste `_MAX_LOG_ROWS`
Logs), die Response-Struktur von `info_api` (alle Felder inklusive des
verschachtelten `metrics`-Objekts), die Equity-/Kassenkurven, Owner-Prüfung
über `_authenticated_user()` und `get_object_or_404()`, Cache-Header
(`no_cache_json`) sowie alle übrigen Datenbankpfade. `calculate_performance_metrics()`
bleibt als dokumentierte, listenbasierte Hilfsfunktion erhalten (sie ist Teil
des im Type-Hint-Vertrag aus Prompt 19 verankerten öffentlichen
Hilfsfunktions-Sets) und wird von `info_api` nicht mehr aufgerufen. Keine neue
Abhängigkeit, keine Migration, keine Settings-Änderung.

## Regressionstests

Neu: `trading/tests/test_info_api_metrics.py` mit **10 Tests** (`TestCase`, über
den echten Middleware-Stack mit deaktiviertem Passphrase-Gate, analog zu
`test_cache_control`).

- Verhaltensgleichheit: Die DB-Aggregation liefert exakt dieselben Werte wie
  `calculate_performance_metrics()` über dasselbe Fenster (`_latest_rows(...)`),
  geprüft für leere Historie, nur Käufe, nur Gewinn-Verkäufe, nur
  Verlust-Verkäufe und gemischte Verläufe.
- Fensterbegrenzung: Bei 2.600 Logs (600 alte Gewinne, 2.000 neue Verluste)
  liefert `_calculate_metrics_from_db(config, _MAX_LOG_ROWS)` `win_rate == 0`
  und `total_wins == 0`, nicht die Gesamthistorie (600 Gewinne + 2.000
  Verluste). Damit ist bewiesen, dass `aggregate()` das `LIMIT` auswertet und
  nicht die gesamte Historie zählt. Ein separater Test bestätigt den `limit`-
  Parameter.
- **Negativkontrolle (Rot→Grün):** Ein Test patcht `calculate_performance_metrics`
  auf einen Abbruch. Vor dem Fix rief `info_api` diese Funktion auf und lieferte
  HTTP 500; nach dem Fix ignoriert `info_api` den Patch und liefert HTTP 200
  mit korrekten Kennzahlen – das beweist, dass die Metrik nicht mehr über den
  Python-Pfad berechnet wird.
- **Angriffs-/Randvektoren:** Division-durch-Null ist abgedeckt – nur Gewinne
  bzw. nur Verluste ergeben `risk_reward == 0` und `profit_factor == 0` ohne
  Exception, die Antwort enthält keine `None`-Werte; `info_api` liefert
  dieselben Kennzahlen wie vor dem Fix (inklusive verschachteltem
  `metrics`-Objekt und `buy_orders`/`sell_orders`).

## Auslieferung und Prüfgrenzen

**330 Django-/Python-Tests** (10 neu + 320 bestehend) bestanden; Systemcheck,
Migrationsprüfung (`makemigrations --check`), `collectstatic` und `pip check`
lokal bestanden. Docker-/Compose-Laufzeitprüfungen wurden mangels Docker
übersprungen; `pyright`/mypy wurden nicht ausgeführt (Node nicht eingerichtet,
django-stubs nicht in `requirements.txt`), das Kriterium der statischen Prüfung
ist über den ast-basierten Type-Hint-Test (`test_view_type_hints`) erfüllt, der
`calculate_performance_metrics` weiterhin mit der verankerten Signatur prüft.

**CI und Auslieferung:** Im Repository ist kein GitHub-Actions-Anwendungstestworkflow
versioniert. Für dieses Release wurde die Auslieferung mit erfolgreichen lokalen
Prüfnachweisen ausdrücklich freigegeben. Die Dependency-Graph-Integration allein
ersetzt keine Anwendungstests. Testergebnisse, Negativkontrolle und Prüfgrenzen:
siehe oben und [CHANGELOG.md](CHANGELOG.md#242418--2026-09-08).

**Prüfgrenze:** Die Equity-/Kassenkurve lädt weiterhin die einzelnen Zeilen des
Fensters (sie lässt sich nicht sinnvoll als reine SQL-Aggregation bilden); die
Optimierung betrifft ausschließlich die Performance-Kennzahlen. Bei
Konfigurationen mit deutlich mehr als `_MAX_LOG_ROWS` Logs verbleibt der
Speicherbedarf für die Kurven unverändert; die bisherige, zweite
Python-Schleife über alle Logs für die Metrik entfällt vollständig. Nach dem
Deploy `/health/` auf **2.4.18** prüfen.
