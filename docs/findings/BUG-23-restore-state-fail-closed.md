# BUG-23 – State-Restart fail-open: leerer DB-Ausfall startete Bot ohne Historie

- **Finding:** `K2` (Umfassender Code-Review 2026-09-09)
- **Status:** **Fixed**
- **Release:** **2.5.0** · **Datum:** 2026-09-09
- **Einstufung:** HIGH – doppelter Kapitaleinsatz durch „vergessene" Positionen
- **Geprüfter Ausgangsstand:** 2.4.20 / `727d3ee534b9f304f59a49b60bdaf7a1e5943ee7`
- **Fix-Commit:** `f0c8bba` – `fix(bot): fail closed when trade history cannot be restored`

## Befund und Root Cause

`TradingBot._restore_state()` baute Positionen und realisierten P/L aus den
`TradingLog`-Zeilen der Konfiguration wieder auf. Der Ladeaufruf
`db_get_tradinglogs_snapshot` war mit `@db_safe(suppress=True)` dekoriert:
Bei `OperationalError`/`InterfaceError` (Netzwerkstau, DB-Neustart während des
Container-Starts) lieferte er schweigend `[]`. Der Bot startete mit leerem
Portfolio – die tatsächlich offenen Positionen waren ihm unbekannt. Die
Strategie konnte dieselbe Summe erneut investieren; Stop-Loss/Take-Profit der
„vergessenen" Positionen wurden nicht mehr überwacht.

Fail-open ist hier die gefährlichere Richtung: Ein nicht startender Bot
verhindert Verluste, ein falsch startender verdoppelt den Einsatz.

## Fix und Umfang

- `db_get_tradinglogs_snapshot` wird jetzt mit `@db_safe()` (ohne `suppress`)
  ausgeführt; Fehler propagieren in `_restore_state()`.
- `_restore_state()` fängt `(OperationalError, InterfaceError)` und hebt sie in
  eine `RuntimeError("Trade-Historie konnte nicht zuverlässig
  wiederhergestellt werden …")` – der Bot-Thread endet sichtbar statt
  arglos zu handeln, der Fehler erscheint im Error-Log.
- Bewusst **nicht** geändert: das Verhalten während des laufenden Betriebs
  (Trade-Pufferung bei Ausfall bleibt fail-safe mit Nachschreiben) und der
  Fall „Konfiguration ohne Historie" (echter Erststart) – dort ist `[]`
  korrekt.

## Regressionstests

`trading/tests/test_core.py` (TradingBotTests):
`test_restore_state_fails_closed_on_unreachable_db` (Ausfall beim Snapshot →
Start bricht mit `RuntimeError` ab, kein Bot mit leerem Portfolio) und
`test_restore_state_loads_positions_and_realized_pl` (positiver
Rekonstruktionspfad bleibt bitgenau). Nachweis rot→grün: vorher lieferte der
suppressierte Aufruf `[]` und der Bot startete mit 0 Positionen.

## Prüfgrenzen

Die Wiederherstellung bleibt ein Snapshot-Laden; für Backtests/Manöver während
des Restore-Fensters übernimmt der Bot nichts. Ob der Trade-Puffer
(`pending_trading_logs`) nach Recovery vollständig nachgeschrieben wurde,
prüft der Test nicht gegen eine echte Postgres-Under-Load-Umgebung.
