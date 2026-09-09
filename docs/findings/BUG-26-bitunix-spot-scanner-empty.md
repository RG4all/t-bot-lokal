---
id: BUG-26
title: Bitunix-Spot-Marktscan lieferte nie qualifizierte Märkte
finding: Review 2026-09-09 (Prompt: Top-Gainer-/Loser-Vorlagen)
severity: HIGH
status: fixed
fixed_in: 2.5.1
audit_ref: docs/security/CODE_REVIEW_2026-09-09.md
tests: trading/tests/test_bitunix_error_handling.py
---

# BUG-26 – Bitunix-Spot-Marktscan lieferte nie qualifizierte Märkte

- **Finding:** Marktscanner für Bitunix Spot liefert dauerhaft leere Top-Gainer/-Loser-Listen
- **Status:** **Fixed** in 2.5.1
- **Release:** 2.5.1 · **Nachprüfung:** 2026-09-09
- **Ursprüngliche Einstufung:** HIGH – Funktionsausfall (Vorlagen/Marktscan); nicht Reproduzierbar „im Code-Test", weil die Unit-Tests dokumentierte Volumenfelder simulierten, die die echte Spot-API nicht liefert.

## Befund und Root Cause

Die Konfigurationsvorlagen (Top-Gainer/-Loser) scannten für **Bitunix Spot**
dauerhaft ohne Ergebnis – das Frontend zeigte stets „Keine Märkte erfüllen
derzeit alle konservativen Filter". Root Cause:

1. Die dokumentierte Bitunix-Spot-Public-API
   (`/api/spot/v1/market/kline/history`) enthält **keine 24h-Volumendaten**
   (nur `open/high/low/close/ts`). Der Scanner berechnet die 24h-Änderung
   korrekt aus den Klines, liefert aber `quoteVolume: null`.
2. Alle Volumen-Kriterien (Volumen-Ausreißer, Volumen/Marktkapitalisierung,
   relatives Orderbuch-Tiefen-Verhältnis) scheitern bei `null` – jede Zeile
   wurde mit „24h-Quotevolumen fehlt" ausgeschlossen. Das Orderbuch wurde
   deshalb gar nicht erst abgefragt (bewusste Abkürzung im alten Code).
3. Ergebnis: Bei Bitunix Spot konnte die Funktion **strukturell** nie ein
   Ergebnis liefern, während dieselben Filter bei Futures und anderen
   Exchanges (mit 24h-Tickern) funktioniert hätten.

Zusätzlich fehlte dem Scan eine Transparenz-Meldung, *warum* er leer ist –
die Vorlagen-UI zeigte nur die generische Leermeldung.

## Fix und Umfang

- `trading/market_scanner.py`:
  - Neue Konstante `MIN_ABSOLUTE_ORDERBOOK_DEPTH_QUOTE` (100.000 USDT):
    Wenn die Exchange kein 24h-Quotevolumen liefert, misst der
    Liquiditätstest die **absolute Orderbuch-Tiefe innerhalb von ±2 % des
    Mittelkurses** – echte, veröffentlichte Börse-Daten statt Schätzungen.
    Die Volumen-Kriterien bleiben in diesem Fall `null` („nicht ermittelt")
    statt still zu scheitern; sie werden im Frontend als „nicht ermittelt"
    ausgegeben.
  - Neue Antwort-Felder `excluded_sample` (bis zu 5 nächste Ausschlüsse mit
    Hauptgründen) und `filters.min_absolute_orderbook_depth_quote`.
  - `MarketScannerError.user_message`: serverseitig formulierte, für die API
    freigegebene Kurzbeschreibung bekannter Fehler (z. B. „Keine
    spot-Märkte … gefunden"); die generische `_SCANNER_ERROR`-Konstante
    bleibt Fallback – Exception-Texte fließen weiterhin nicht in die
    Antwort (SEC-10, Nachweis in `test_scanner_api_*`).
  - Orderbuch-Fehlergründe, die einen rohen Exception-Text tragen
    („Orderbuch nicht verfügbar: …"), werden für die Anzeige durch eine
    fixe Zeile ersetzt (`_safe_depth_reason`).
- `trading/market_data.py` (gleicher Befundkomplex, gleiche Exchange):
  - Fail-closed Pair-Status: Ein Spot-Pair **ohne** Statusfeld
    (`isOpen`/`symbolStatus`) zählt nicht mehr als handelbar, sondern wird
    aus dem Katalog geworfen – dadurch schlägt der Katalog-Abruf sichtbar
    fehl, statt ungültige Symbole durch die Validierung zu lassen.
  - `validate_symbols` liefert wie die anderen Adapter `[]` statt `None`.
  - Bitunix-Spot-Einzelabfragen (`last_price`) werden mit 0,11 s Abstand
    getaktet und bleiben damit auch bei 20 Symbolen unter dem
    dokumentierten 10 Requests/Sek/IP-Limit.
- Frontend (`_symbol_autocomplete_js.html`): Leeres Scan-Ergebnis zeigt
  jetzt die nächsten Ausschlüsse mit Grund; `volume_spike: null` wird als
  „nicht ermittelt" gerendert.

Nicht geändert: Futures-Pfad, die Filterlogik für Exchanges **mit**
24h-Volumen (BitMart, Binance, BingX, Bybit), die 15-s-Request-Timeouts und
die Refresh-Drossel (W10).

## Regressionstests

`trading/tests/test_bitunix_error_handling.py` (neue Module, 23 Tests) plus
angepasster Test in `test_features.py`:

- `BitunixDocumentedApiTests`: Adapter gegen die dokumentierten
  Response-Formen (Pair-Liste `base/quote/isOpen`, `last_price` als
  nackter String, Futures-Ticker ohne `percentage`-Feld), Fail-closed
  Status, Symbol-Pacing.
- `BitunixSpotScannerTests`: Scan mit echten Klines **ohne** Volumen liefert
  qualifizierte Gainer/Loser bei ausreichender Orderbuch-Tiefe; dünnes
  Buch wird mit erklärendem Grund ausgeschlossen; `excluded_sample`
  vorhanden; API 503 mit `user_message` bzw. generischer Fallback-Zeile.
- Rot→Grün: Der neue Scanner-Test fällt am Ausgangsstand mit „alle Zeilen
  ausgeschlossen" rot; der angepasste `test_features`-Test dokumentiert den
  alten (falschen) Anspruch „Orderbuch darf nicht abgefragt werden" und
  wird dem Fix angepasst.

## Prüfgrenzen

- Die echte Bitunix-API wurde aus der Review-Umgebung nicht erreichbar
  (kein ausgehender Exchange-Traffic); die Response-Formen stammen aus der
  offiziellen API-Dokumentation (Spot: `openapi.bitunix.com`, Futures:
  `fapi.bitunix.com`) und aus dem vorliegenden Adapter-Design. Falls die
  Börse in Zukunft 24h-Volumendaten an den Klines liefert, greift der
  Volumen-Pfad automatisch wieder vor dem Tiefen-Fallback.
- Der absolute Tiefen-Boden (100.000 USDT) ist eine bewusste,
  konservativ gewählte Konstante ohne eigenen UI-Slider; eine
  Anpassung erfordert eine Codeänderung (bewusste Entscheidung gegen
  einen zusätzlichen, missverständlichen Filter-Regler).
