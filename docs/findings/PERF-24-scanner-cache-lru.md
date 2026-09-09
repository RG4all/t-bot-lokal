# PERF-24 – Scanner-Caches wuchsen unbegrenzt: gedeckelte LRU statt TTL-only-Dict

- **Finding:** `K3` (Umfassender Code-Review 2026-09-09)
- **Status:** **Fixed**
- **Release:** **2.5.0** · **Datum:** 2026-09-09
- **Einstufung:** HIGH – Memory-Leak mit OOM-Risiko für den gesamten Webprozess
- **Geprüfter Ausgangsstand:** 2.4.20 / `727d3ee534b9f304f59a49b60bdaf7a1e5943ee7`
- **Fix-Commit:** `46b13bf` – `refactor(scanner): replace TTL-only dicts with bounded LRU caches`

## Befund und Root Cause

`market_scanner.py` führte zwei prozesslokale Caches (`_CACHE` für
Marktscans, `_MARKET_CAP_CACHE` für Marktkapitalisierungen) als schlichte
Dicts mit TTL-Prüfung **beim Lesen**. Die Scan-Schlüssel enthalten die
benutzergewählten Float-Filter des Scanner-Formulars
(`volatility_threshold`, `volume_spike_multiple`, …) – jede Kombination
legte einen neuen, nie evikierten Eintrag an. Veraltete Einträge wurden beim
Überschreiben ersetzt, aber kein Eintrag flog je raus, wenn sein Schlüssel
nicht erneut getroffen wurde. Auf Render Free (512 MB inkl. aller Bot-Threads)
skalierte das monoton mit der Zahl benutzter Filterkombinationen; der erste
OOM-Killer-Treffer beendet Webprozess **und** alle Bots.

## Fix und Umfang

Beide Caches sind jetzt `OrderedDict`-LRUs mit fester Obergrenze
(`_CACHE_MAX_ENTRIES = 32`, `_MARKET_CAP_CACHE_MAX_ENTRIES = 16`):

- Treffer setzen den Schlüssel per `move_to_end` nach hinten (aktive
  Kombinationen werden nicht verdrängt),
- writes `popitem(last=False)` eviktieren den ältesten Eintrag, solange das
  Limit überschritten ist,
- Locks bleiben auf die Dict-Zugriffe beschränkt (nie über Netzwerk-I/O),
- Schlüssel-Layout und TTL-Semantik (`60 s`/`300 s`) sind unverändert; die
  im Review angeregte Drossel erzwungener Refreshes ist separat als
  [W10/Befund im Review] umgesetzt (`_FORCE_MIN_INTERVAL_SECONDS`, 20 s pro
  Börse und Marktart).

## Regressionstests

`trading/tests/test_features.py` (ScannerCacheTests): Eviction auf das Limit
bei `Limit + 8` distincten Filterkombinationen, exakt ein Scan pro neuer
Kombination, TTL-Treffer ohne erneuten Exchange-Aufruf; LRU-Lebhaltung
aktiver Schlüssel. Die Refresh-Drossel testen die W10-Fälle im selben Modul
(zwei `refresh=True`-Aufrufe lösen einen Scan, eigener Fensterschlüssel pro
Exchange, Scan erneut nach Fensterablauf).

## Prüfgrenzen

Die Kappung ist pro Prozess; bei mehreren Web-Workern multipliziert sich der
Cache-Speicher (`worker_processes` in `Dockerfile` beachten). Absolute
Obergrenzen (Bytes statt Einträgen) sind bewusst nicht implementiert – die
Einträge sind klein, die Zahl der im UI erreichbaren Schlüsselkombinationen
ist durch das Scanner-Formular begrenzt.
