# CODE-18 – Duplizierte Indikator-Logik

- **Finding:** `DuplicatedIndicatorLogic` (Prompt 18 / Security-Audit §4.3)
- **Status:** **Fixed** (behoben und nachgeprüft)
- **Release:** **2.4.15** · **Datum:** 2026-09-08
- **Einstufung:** MEDIUM – Code Quality (kein Sicherheitsbefund, keine ausnutzbare Schwachstelle)
- **Geprüfter Ausgangsstand:** 2.4.14 / `8c7f799ac1bd43efe2753561edf5b7229a8e02ab`
- **Fix-Commit:** [`e208ccc`](https://github.com/RG4all/t-bot-lokal/commit/e208ccc6720e0832b3ca47eb53b04a2fb4b2d809) – `refactor: extract indicator logic to shared module`
- **Fix-PR:** [#23 – refactor: extract indicator logic to shared module](https://github.com/RG4all/t-bot-lokal/pull/23) (Ziel-Branch `tbot.local`)
- **Dokumentation des Releases:** [CHANGELOG 2.4.15](CHANGELOG.md#2415--2026-09-08), [Handbuch §6.6](MANUAL.md), [Backtesting-Kapitel §2](backtesting.md)

## Befund und Root Cause

Die Arithmetik für die drei Strategieindikatoren NDA, DeltaDelta und
Acceleration existierte zweimal:

| Ort | Historische Zeilen | Präzisionsstufe | Nebenwerte |
|---|---|---|---|
| `Backtesting.calculate_indicators` (`trading/backtesting.py`) | 14–51 | jede Zwischenstufe auf 8 Nachkommastellen quantisiert (`ROUND_HALF_UP`) | keine |
| `TradingBot.calculate_and_store` (`trading/trading_bot.py`) | 577–591 | ungerundet, Rundung erst beim Speichern über `_bounded` | `current_da`, `previous_da`, `dva`, `mvd` für `DataLog` |

Beide Kopien berechneten dasselbe mit unterschiedlicher Syntax
(`* Decimal(100)` gegenüber `* 100`, `/ Decimal(2)` gegenüber `/ 2`). Dazu
zwei Kopien derselben Rundungskonstanten `_EIGHT_PLACES`
(`backtesting.py`, `trading_bot.py`), beide passend zu `decimal_places=8` in
`trading/models.py`, sowie ein viermal kopiertes Vorabberechnungsmuster
`[None, None] + [calculate_indicators(…) for index in range(2, len(prices))]`
(`backtesting.py`, `trading/tasks.py` an zwei Stellen,
`scripts/backtest_resource_probe.py`).

Der eigentliche Schaden ist die Drift, nicht die doppelte Zeilenzahl: Ein in
einer Kopie korrigierter Formelfehler, ein ergänzter Guard oder eine geänderte
Rundung wirkt nicht im anderen Pfad. Backtest-Optimierung und
Live-Entscheidung verwenden dieselben Schwellwerte – eine Drift würde dazu
führen, dass Nutzer Parameter auf eine Strategie optimieren, die der Bot in
genau dieser Kombination nie handelt. Der Backtest-Pfad liest seine Preise aus
der `DataLog`-Historie, der Bot aus dem RAM-Buffer; beide müssen deshalb
garantiert auf identischen Formeln rechnen.

Bei der Deduplizierung wurde ein latenter Defekt **beider** Kopien sichtbar:
eine fehlende Index-Validierung. `calculate_indicators(prices, 1)` griff über
die Listendefinition auf `prices[-1]` zu und lieferte für
`[100, 101, 102]` stillschweigend `(-1.5, -0.5, 1.0)` statt zu scheitern –
ein unsichtbar falscher Indikatorwert. Analog für `idx=0` und negative Indizes.

## Fix und Umfang

Neu ist `trading/indicators.py` als einzige Quelle der Indikatorarithmetik:

- `compute_indicator_values(prices, idx, *, rounding=…)` – Kernfunktion mit
  typunabhängiger Preis-Konvertierung (`to_decimal`), allen Guards und einem
  `IndicatorValues`-Snapshot, der auch die zusätzlichen `DataLog`-Nebenwerte
  enthält.
- `calculate_trading_indicators(prices, idx)` – quantisierte Strategie-Stufe,
  Rückgabe `(acceleration, deltadelta, nda)`.
- `build_indicator_rows(prices)` – indexgleiche Vorabberechnung einer
  Preisreihe; ersetzt das kopierte `[None, None] + […]`-Muster.
- `EIGHT_PLACES` – die 8-Nachkommastellen-Konstante, jetzt zentral definiert.

Anpassungen der Aufrufer:

| Datei | Änderung |
|---|---|
| `trading/backtesting.py` | `calculate_indicators` bleibt als dünner Kompatibilitäts-Wrapper bestehen und delegiert; `compute_indicator_series` und die Fallback-Vorabberechnung nutzen `build_indicator_rows`; `_decimal` und `_EIGHT_PLACES` sind durch die zentralen Helfer ersetzt |
| `trading/trading_bot.py` | `calculate_and_store` ruft `compute_indicator_values(prices, len(prices) - 1)` und verwendet den Snapshot für `DataLog` **und** `check_trading`; die eigenen Formelzeilen und `_EIGHT_PLACES` sind entfernt |
| `trading/tasks.py` | Vorabberechnung im Raster- und im Detail-Durchlauf über `build_indicator_rows` |
| `scripts/backtest_resource_probe.py` | dieselbe Helper-Funktion statt der Kopie |

### Bewusst erhalten: zwei Präzisionsstufen, eine Implementierung

Der historische Prompt sah vor, dass der Bot direkt
`calculate_trading_indicators()` nutzt. Das hätte das Verhalten an zwei
Stellen verändert: Die Live-Schwellwertvergleiche wären auf quantisierte Werte
umgestellt (bei Verhältniszahlen wie `DVA / vorherige NDA` mit Abweichungen bis
`9E-8`), und die `DataLog`-Felder `current_da`, `prev_da` und `dva` stehen in
der vorgeschlagenen Signatur gar nicht mehr zur Verfügung. Ein
Deduplizierungs-Refactoring darf weder laufende Handelsentscheidungen noch
bereits gespeicherte Historien verschieben.

Deshalb liefert das Modul beide Stufen aus **derselben** Kernfunktion; der
einzige Unterschied ist der `rounding`-Hook (`_no_rounding` für den Bot,
`_quantize` für Backtest, Reports und Plots). Die Trennung ist dokumentiert
(Modul-Docstring, Kommentar in `calculate_and_store`) und durch Tests fixiert,
damit eine spätere Vereinheitlichung eine bewusste Entscheidung bleibt.

Unverändert bleiben außerdem: alle `DataLog`-Felder, die `mvd`-Berechnung aus
dem Buffer, die Nullstellen-Absicherungen (`previous_price == 0` →
`Decimal(0)`; `previous_nda == 0` → Beschleunigung `0` statt Division durch
Null), die Ordergrößen-Rundung, die Backtest-API, Settings, Models und
Migrationen. Keine neue Abhängigkeit, kein neues Architekturmuster, keine
Änderung am Frontend.

## Numerische Reproduktion (Alt gegen Neu)

Zusätzlich zur Test-Suite lief ein deterministischer Vergleichsdurchlauf gegen
die wörtlichen Altversionen (deterministischer Pseudozufall, 3.000 Preisreihen
mit 3–11 Punkten, darin Null-Vorpreise, `None`-Preise, Float-/String-Mischtypen,
Extremwerte um 10³⁰ und Reihen mit 6 bis 8 Dezimalstellen):

- **16.693 Vergleichsfälle, 0 Abweichungen** – Backtest-Tripel und alle
  `DataLog`-Feldwerte des Bot-Pfads (einschließlich `_bounded`-Klemmung) sind
  zeichenweise identisch.
- **Backtest-Reports identisch:** eine 2.000-Punkte-Reihe mit Defekt-Ticks
  (`0`, `0.01`, sehr kleiner Preis) über drei Schwellwert-Raster, jeweils mit
  und ohne vorgerechnete Zeilen sowie mit `include_details=False`: Endkapital,
  Nettoergebnis, Rendite, Trade-Anzahlen, Gebühren, Drawdown, Profit-Faktor,
  die ersten Trades und Kopf/Ende der Equity-Kurve sind **byte-identisch** vor
  und nach dem Refactoring.
- Ressourcen-Probe (`scripts/backtest_resource_probe.py`) unverändert:
  `best_capital = 1000.2994001207792`.
- `NaN`- und unendliche Preise verhalten sich in beiden Stufen wie zuvor
  (quantisiert: `NaN` bzw. `InvalidOperation`); der Bot-Pfad bleibt
  Rohwertpfad. Eine nachträgliche Validierung wäre eine Verhaltensänderung und
  ist bewusst nicht Teil dieses Refactorings.

## Regressionstests

Neu: `trading/tests/test_indicators.py` mit **30 Tests** in drei Klassen.

`IndicatorMathTests` – Arithmetik und Guards:

- von Hand nachgerechnete Referenzwerte für `100 → 101 → 102.5`
  (`acceleration 0.49999999`, `deltadelta 1.23762376`, `nda 1.48514851`) plus
  die zugehörigen Snapshot-Felder `current_da`, `previous_da`, `previous_nda`,
  `dva`;
- `ROUND_HALF_UP` auf der 9. Nachkommastelle (mit `ROUND_HALF_EVEN` wäre das
  Ergebnis 0 statt `0.00000001`);
- Vorpreis `0` oder `None` → neutrale Indikatoren ohne Division durch Null,
  die absolute Kursdifferenz bleibt erhalten;
- `previous_nda == 0` → nur die Beschleunigung wird defensiv 0, NDA und
  DeltaDelta bleiben korrekt;
- Index-Validierung für `idx` in `0, 1, -1, -3`, für `idx` jenseits der Reihe
  und für Reihen mit nur zwei Werten (beide Einstiegspunkte);
- Nicht-Decimal-Eingaben (Float, String, `Decimal`), `to_decimal`-Defaults und
  fehlschlagende Ungültigeingaben;
- dokumentierter Präzisionsunterschied zwischen Rohwert und quantisiertem Wert
  (`0.5`-Verhältnis gegenüber `0.49999999`);
- der Rundungshook reproduziert an jeder Position exakt den bisherigen
  Backtest-Code;
- **Gegenprobe gegen beide Altversionen:** die historischen Formeln von
  `Backtesting.calculate_indicators` und `TradingBot.calculate_and_store` sind
  wörtlich im Test nachgebaut und werden über 60 deterministische Preispunkte
  mit der geteilten Funktion abgeglichen (Tripel *und* alle Rohwert-Felder);
  Tuples als Preisliste werden ebenfalls geprüft;
- `build_indicator_rows` ist indexgleich, führt `None`-Platzhalter nur für die
  Indizes 0 und 1 und bleibt bei kurzen und leeren Reihen korrekt.

`IndicatorDeduplicationTests` – strukturelle Sicherung gegen erneute
Duplizierung:

- `Backtesting.calculate_indicators` und der Bot nutzen die Objekte aus
  `trading.indicators` (Identität, nicht nur Gleichheit);
- die drei Formel-Marker kommen in `backtesting.py`, `trading_bot.py` und
  `tasks.py` nicht mehr vor, wohl aber genau einmal in `indicators.py`; die
  Alt-Schreibweisen des Bot-Zweigs sind ebenfalls verbannt;
- `_EIGHT_PLACES` existiert in keinem Aufrufer-Modul mehr;
- das Muster `[None, None] + […]` ist in `backtesting.py` und `tasks.py`
  entfernt;
- `compute_indicator_series` und die Reports/Plot-Simulation liefern weiterhin
  die Werte der geteilten Zeilen.

`LiveBotIndicatorTests` – Verhalten des Live-Pfads (`TransactionTestCase`, weil
der Bot über seinen DB-Executor in eigenen Transaktionen schreibt):

- die geschriebene `DataLog`-Zeile enthält exakt die erwartet quantisierten
  Bot-Rohwerte (`nda 10.00000000`, `prev_nda 9.09090909`, `dva 0.90909091`,
  `deltadelta 9.54545455`, `div_DVA_prev_NDA 0.10000000`, `mvd 0.82644628`);
- bei glatter Serie stimmen Bot-Zeile und `Backtesting.calculate_indicators`
  exakt überein (Parität beider Pfade);
- `check_trading` erhält nach wie vor die ungerundeten Rohwerte
  (AsyncMock-Assertion auf alle fünf Argumente);
- ein Buffer mit nur zwei Preisen ist ein No-op (kein Log, keine Prüfung);
- ein sub-Raster-Preis (`0.000000005`) wird erst beim Schreiben gerundet, der
  Vergleichswert bleibt roh;
- ein eingeschleuster Extrempreis (`1e30`) bricht keinen Bot-Zyklus ab
  (Klemmung auf den Feldbereich), während der quantisierte Pfad lautstark
  fehlschlägt, statt still 0 zu liefern.

**Mutationsnachweis (beigehaltene Prüfkräfte):** Zwei gezielte Mutationen der
geteilten Funktion wurden ausgeführt und von der Suite erkannt – (1) vorherige
NDA mit `older_price` statt `previous_price` als Nenner ließ
`test_matches_the_historical_reference_formulas` an praktisch jedem Index
fehlschlagen (z. B. `-1.78024392` gegen `-1.77132805`), (2) das Entfernen des
`idx >= 2`-Guards ließ `test_index_below_two_is_rejected_instead_of_wrapping`
in drei Teilfällen fehlschlagen. Beide Mutationen wurden zurückgenommen und
sind nicht Teil des ausgelieferten Codes.

**Rot → grün:** Vor dem Fix scheiterte das Modul bereits am Import
(`ImportError: cannot import name 'indicators' from 'trading'`). Die
Index-Tests dokumentieren den Altzustand direkt: `Backtesting.calculate_indicators`
lieferte für `idx=1` und `idx=0` Zahlenwerte statt einer Exception. Die
Strukturprüfungen schlugen wegen der Kopien in `backtesting.py`,
`trading_bot.py` und `tasks.py` fehl. Die Verhaltenstests des Bot-Pfads waren am
Ausgangsstand bereits grün – sie sind als Charakterisierungstests gegen eine
still veränderte Präzision gedacht, nicht als Nachweis des Alt-Fehlers.

## SEC-05-Nachprüfung

Wie in den Vorgänger-Releases nachgeprüft: `CSRF_COOKIE_HTTPONLY = True` bleibt
DEBUG-/Render-unabhängig in `trading_bot_project/settings.py` gesetzt, und alle
**11 CSRF-Cookie-Tests** in `trading/tests/test_csrf_cookie.py` bestehen
unverändert – zusammen mit den 30 neuen Indikator-Tests in einem Lauf. SEC-05
bleibt **Fixed** (ursprünglich 2.4.5, zuletzt in 2.4.14 nachgeprüft, erneut in
2.4.15). Das Refactoring berührt keine Header-, Cookie- oder
Autorisierungspfade; geändert sind ausschließlich Berechnungs- und
Vorabberechnungslogik. Nachweis: [SEC-06-Dokument](SEC-06-rule-lifecycle-authz.md).

## Lokale Validierung

**Python 3.11.2**, **Django 5.2.17**, SQLite-Testdatenbank, **Ruff 0.16.6**,
**ShellCheck 0.11.0**; Abhängigkeiten unverändert aus `requirements.txt`.
Lokale Test-Secrets wurden explizit im Environment gesetzt, keine produktiven
Zugangsdaten verwendet.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
python manage.py test trading.tests.test_indicators --noinput
python manage.py test trading.tests.test_csrf_cookie --noinput   # SEC-05
python manage.py test --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
python scripts/backtest_resource_probe.py
bash tests/run_tests.sh
python -m pip check
```

- **279 Django-/Python-Tests** (30 neue + 249 bestehende) bestanden.
- **8/8 Shell-Testgruppen**, Ruff (neue Dateien zusätzlich
  `ruff format`-geprüft), ShellCheck, Systemcheck, Migrationsprüfung,
  `collectstatic` und `pip check` bestanden.
- Mit isolierten Test-Secrets, `DEBUG=False` und `RENDER=True`:
  `check --deploy --fail-level WARNING` ohne Befunde.

## CI und Auslieferung

**Ausdrücklich genehmigte Ausnahme für dieses Release:** Wie in
2.4.6–2.4.14 erfolgt die Auslieferung mit den oben dokumentierten lokalen
Prüfnachweisen; es wird kein versionierter GitHub-Actions-Anwendungstestworkflow
eingeführt. Die Workflow-API führt nur die dynamische Integration
**Dependency Graph** (`dynamic/dependabot/update-graph`) aus – sie ersetzt keine
Anwendungstests. Nach dem Push wurde der Stand der Runs und Checks für den
Session-Branch geprüft: für den HEAD-Commit des Docs-Commits sind **keine
Check-Runs gemeldet** (`gh pr checks #23` → „no checks reported",
`check-runs` = 0), weil kein Anwendungstest-Workflow existiert. Es wird kein
erfolgreicher GitHub-CI-Lauf behauptet; alle Nachweise sind die oben
dokumentierten lokalen Läufe.

Auslieferung auf `arena/01a081b6-t-bot-lokal`; [PR #23](https://github.com/RG4all/t-bot-lokal/pull/23)
richtet sich an `tbot.local` und ist zum Zeitpunkt dieses Eintrags `OPEN` und
`MERGEABLE`.

## Prüfgrenzen und Upgrade

- **Kein Performance-Gewinn behauptet:** Die Vorabberechnung je Preisreihe
  existierte bereits; `build_indicator_rows` bündelt sie nur. Die separate
  Memoisierung über alle Rasterkandidaten hinweg ist Prompt 16 und bleibt offen
  (Audit §4.1).
- **Kein Backtest auf echten Marktdaten:** Die Paritäts- und
  Report-Vergleiche laufen auf deterministischen Serien bzw. einem einmaligen
  Prüfdurchlauf, nicht gegen eine Produktivdatenbank.
- **Laufzeitumgebung:** lokal Python 3.11.2, Zielruntime im Docker-Image
  3.12.7. Docker steht in der Prüfumgebung nicht zur Verfügung, daher kein
  Containerlauf und kein PDF-/Weasyprint-Nachweis (Pango fehlt). Es sind
  Einheiten- und Integrationstests, kein Browser-End-to-End-Test.
- **Verhaltensgleichheit** ist für die quantisierten Tripel und die
  `DataLog`-Werte nachgewiesen. Eine bewusste Vereinheitlichung der Präzision
  beider Pfade wäre eine separate, datenbankrelevante Entscheidung und wurde
  hier gerade nicht getroffen.
- Keine Konfigurations-, Settings- oder Datenbankänderung. Nach dem Deploy
  `/health/` auf **2.4.15** prüfen. Bestehende `DataLog`-Zeilen und laufende
  Bot-Konfigurationen bleiben gültig; Backtests müssen nicht neu gestartet
  werden, ihre Ergebnisse sind reproduzierbar.
