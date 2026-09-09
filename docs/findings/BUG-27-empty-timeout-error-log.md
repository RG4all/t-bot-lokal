---
id: BUG-27
title: Kursabruf-Timeout erzeugte leere Fehler-Log-Einträge und 2-s-Retry
finding: Review 2026-09-09 (Prompt: Log-Fehler bitunix #3, Referenz #1, 09.09.2026 16:38:54)
severity: HIGH
status: fixed
fixed_in: 2.5.1
audit_ref: docs/security/CODE_REVIEW_2026-09-09.md
tests: trading/tests/test_bitunix_error_handling.py
---

# BUG-27 – Kursabruf-Timeout erzeugte leere Fehler-Log-Einträge und 2-s-Retry

- **Finding:** Fehler-Log-Eintrag mit leerer Meldung (im UI „Referenz #1"
  ohne nutzbare Information) bei Bitunix-Konfiguration
- **Status:** **Fixed** in 2.5.1
- **Release:** 2.5.1 · **Nachprüfung:** 2026-09-09
- **Ursprüngliche Einstufung:** HIGH – Fehlerbehandlung/Diagnostik; für den
  Nutzer unsichtbar, für den Betrieb schwer lokalisierbar.

## Befund und Root Cause

Im Betrieb (Konfiguration „bitunix", 09.09.2026) tauchte ein Fehler-Log-
Eintrag auf, der im UI (ohne Staff-Rechte) nur „Referenz #1" plus den
generischen Satz „Ein Fehler ist aufgetreten …" zeigte. Die wirkliche Kette:

1. Der Bot ruft für BitMart/Bitunix den Kurs synchron über
   `run_in_executor` ab und umhüllt den Aufruf mit
   `asyncio.wait_for(future, timeout=25)`.
2. Bei Bitunix Spot sind das je Zyklus 1 Pair-List- + N Last-Price-Requests
   (jeder mit 15-s-Einzel-Timeout). Schon zwei Requests, die gegen das
   Einzel-Timeout laufen, sprengen das starre 25-s-Gesamtbudget.
3. `asyncio.wait_for` wirft dann `asyncio.TimeoutError` – und
   **`str(asyncio.TimeoutError())` ist der leere String** (in Python 3.11
   Alias von `TimeoutError`, ohne Standardtext).
4. Der Main-Loop persistierte `str(exc)` direkt in den `ErrorLog` →
   **Eintrag mit leerer Meldung**. Ohne Staff-Rechte zeigt das UI nur die
   Referenznummer; auch mit Staff-Rechten stand in der Meldung nichts.
5. Der Backoff-Zweig klassifizierte den Timeout als „sonstiges"
   (`else: delay = 2`) – der Bot hämmerte also alle ~2 s gegen eine
   langsame/gesperrte Exchange und füllte das Log mit leeren Einträgen.

## Fix und Umfang

- `trading/market_data.py`: neuer Subtyp
  `MarketDataTimeoutError(MarketDataConnectionError)` mit fester,
  menschenlesbarer Meldung (Börse, Anzahl Symbole, Zeitbudget) – das
  Leere wird nicht mehr an die Fehlergrenze weitergereicht.
- `trading/trading_bot.py`:
  - `fetch_tickers()` konvertiert `asyncio.TimeoutError` in
    `MarketDataTimeoutError`. Das Gesamtbudget skaliert mit der Symbolzahl
    (Basis 20 s + 12 s je Symbol, Obergrenze 120 s), damit im Normalfall
    die Einzel-Requests mit ihrer eigenen Meldung abschließen und der
    Timeout nur noch das eigentliche „Börse blockiert/langsam" kennzeichnet.
    Der Binance-WebSocket-Pfad (90 s) und der Legacy-Einzel-Pfad (20 s je
    Symbol) werden identisch konvertiert.
  - `MarketDataTimeoutError` erbt von `MarketDataConnectionError` und
    bekommt damit automatisch den bestehenden 5–300-s-Backoff statt 2 s.
  - `_error_message_text()`-Fallback: `db_log_error`, `_persist_error`,
    der `run()`-Catchall und der Main-Loop setzen nie mehr eine leere
    Meldung – Ausnahmen ohne Text fallen auf
    „{Typ} ohne Meldungstext; die genaue Ursache ist nur im Server-Log
    (Traceback) dokumentiert" zurück.
  - Der per-Symbol-Verarbeitungsfehler bekommt das stabile Präfix
    „Symbolverarbeitung {symbol}: …" für die sichere Dashboard-Kurzform
    (siehe CODE-28).
- Keine Änderung an DB-Schema, API-Form oder Bot-Verhalten bei
  erfolgreichen Abrufen.

## Regressionstests

`trading/tests/test_bitunix_error_handling.py` (`BotTimeoutErrorTests`):

- `str(asyncio.TimeoutError()) == ""`-Fall: Fallback-Meldung nicht leer,
  benennt den Exception-Typ.
- `MarketDataTimeoutError` ist `MarketDataConnectionError` (Backoff-Vertrag)
  und enthält Börse/Symbolzahl/Budget.
- `TradingBot.fetch_tickers` mit hängendem Fetch: wirft
  `MarketDataTimeoutError` mit beschreibbarer Meldung (Bitunix, 2 Symbole,
  1 s Budget).
- `db_log_error` persistiert bei leerer Exception eine nicht-leere Meldung.

Rot→Grün: Der Fallback- und der Fetch-Timeout-Test fallen am Ausgangsstand
rot (leere Meldung bzw. nacktes `TimeoutError` ohne Exchange-Kontext).

## Prüfgrenzen

- Der exzeptionelle Eintrag „Referenz #1" des Betriebslogs war aus der
  Review-Umgebung nicht nachlesbar; die Kette ist anhand des Codepfads und
  des dokumentierten Bitunix-Verhaltens (Mehr-Request-Spot-Fetch + 15-s-
  Einzel-Timeouts) rekonstruiert. Andere Quellen desselben Eintrags
  (z. B. ein Rate-Limit-418 mit Meldung) wären vom UI her gleichartig
  aufgetreten; beide Fälle sind vom Fix abgedeckt (Rate-Limit-Zweig
  unverändert, Timeout-Zweig neu beschreibbar).
- Live-Verhalten gegen die echte Bitunix-API (Block-Dauer, Latenz) wurde
  nicht vermessen.
