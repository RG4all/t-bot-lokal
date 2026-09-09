# BUG-22 – Deaktivierte Bots handelten weiter: `is_running`-Check im Main-Loop ergänzt

- **Finding:** `K1` (Umfassender Code-Review 2026-09-09)
- **Status:** **Fixed**
- **Release:** **2.5.0** · **Datum:** 2026-09-09
- **Einstufung:** HIGH – Kontrollverlust über den real handelnden Thread
- **Geprüfter Ausgangsstand:** 2.4.20 / `727d3ee534b9f304f59a49b60bdaf7a1e5943ee7`
- **Fix-Commit:** `223cda1` – `fix(bot): honor is_running in main loop to close deactivate/restart race`

## Befund und Root Cause

Der Bot-Thread lud seine Konfiguration alle `BOT_CONFIG_REFRESH_SECONDS` neu,
verwendete aber ausschließlich die Preis-/Strategiefelder. `is_running=False`
(Deaktivierung im Dashboard, API, Admin) beendete den Thread nur, wenn der
Stopper denselben Prozess erreichte; der Weg über die Datenbank – etwa ein in
einem anderen Worker ausgeführtes Deaktivieren – ließ den Thread einfach
weiterhandeln. Zusätzlich konstruierte `restart_bot()` den Bot neu, ohne die
Fahne unmittelbar vorher gegen die DB zu prüfen (TOCTOU zwischen Deaktivierung
und Re-Start).

Die Wirkung ist die Kernschutzfunktion der App betreffend: Ein Nutzer, der
seinen Bot „deaktiviert", verliert die Kontrolle über weitere Trades, bis der
Container neu startet.

## Fix und Umfang

1. `main_loop()` prüft nach jedem Config-Refresh `if not self.config.is_running:`
   und beendet den Loop sauber (Positionen bleiben erhalten, kein Verkauf
   erzwungen – das ist Sache des Kill-Switch).
2. `restart_bot()` prüft unmittelbar vor `start_bot`
   `Configuration.objects.filter(id=..., is_running=True).exists()`; der
   Neustart entfällt bei zwischenzeitlicher Deaktivierung.

Der Check ersetzt keine bestehenden Stop-Pfade (`stop_bot`), sondern schließt
nur das Datenbank-seitige Fenster. Keine API-, Modell- oder Migrationsfolgen.

## Regressionstests

`trading/tests/test_core.py` (TradingBotTests):
`test_main_loop_self_stops_when_config_is_deactivated` (funktional: echter
Thread, asyncio-Loop, erzwungener Refresh) und
`test_restart_bot_rechecks_running_flag_against_db` (Quell-Pin gegen
Regression des Guards). Nachweis rot→grün: vor dem Fix lief der Thread nach
`is_running=False` über mehrere Ticks weiter bzw. wurde durch den Restart
wieder erweckt.

## Prüfgrenzen

Das Zeitfenster bleibt auf maximal einen Refresh-Zyklus (Standard 30 s,
`BOT_CONFIG_REFRESH_SECONDS`) begrenzt; eine sofortige Unterbrechung mitten in
der Orderausführung ist bewusst nicht implementiert (Abbruchpunkt mitten in
`execute_trade` wäre gefährlicher als der kontrollierte Tick-Ende-Ausstieg).
Mehrprozess-Deployments ohne gemeinsame `is_running`-Quelle (rein lokale
`AUTOSTART_BOTS`-Zwillinge) sind nicht adressiert.
