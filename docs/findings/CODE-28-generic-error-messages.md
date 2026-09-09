---
id: CODE-28
title: Generische Fehlermeldungen – menschlich lesbare Erklärungen im Fehler-Log
finding: Review 2026-09-09 (Prompt: Fehler UX, Info-/Hover-Elemente, Fehlerdokumentation)
severity: MEDIUM
status: fixed
fixed_in: 2.5.1
audit_ref: docs/security/CODE_REVIEW_2026-09-09.md
tests: trading/tests/test_bitunix_error_handling.py
---

# CODE-28 – Generische Fehlermeldungen, jetzt mit menschlich lesbaren Erklärungen

- **Finding:** Fehler-Log und Dashboard zeigten für Endnutzer nur generische
  Texte („Ein Fehler ist aufgetreten …"), die weder Ursache noch Lösung
  benannten
- **Status:** **Fixed** in 2.5.1
- **Release:** 2.5.1 · **Nachprüfung:** 2026-09-09
- **Ursprüngliche Einstufung:** MEDIUM – UX/Fehlerbehandlung; keine
  Funktionsauswirkung, aber kein nutzbares Selbst-Service für den Nutzer.

## Befund

Die SEC-10-Arbeit (2.4.x/2.5.0) hat korrekt verhindert, dass rohe
Exception-Texte in die API fließen. Die Konsequenz für Endnutzer war
jedoch: Jeder Fehler zeigte identisch „Ein Fehler ist aufgetreten. Details
nur für Administratoren." – ohne dass der Nutzer irgendetwas tun konnte.
Fehlten die Staff-Rechte, gab es zusätzlich keine Erklärung, *was* zu tun
ist. Das widerspricht dem Ziel „jeder Fehler ist explizit dokumentiert
(Was ist passiert / Warum / Was tun)".

## Fix und Umfang

- **Neu `trading/error_explanations.py`:** Kartenbasierte Erklärung für
  alle 12 ErrorLog-Quellen im Code
  (`trading_bot.fetch_tickers`, `trading_bot.validate_symbols`,
  `trading_bot.process_symbol`, `trading_bot.main_loop`,
  `trading_bot.kill_switch`, `trading_bot.restart`, `apps.autostart`,
  `views.config_activate`, `views.config_activate.validation`,
  `views.bot_status_api`, `views.manual_sell`, `views.kill_switch`):
  Was ist passiert / Ursache (inkl. typischer Ursachen) / Was tun.
  Zusätzlich:
  - Kontext-Picker pro Quelle (Exchange, Symbole, Aktion, Fehlertyp) –
    nur aus strukturierten `details`-Feldern bzw. einer **Whitelist** von
    stabilen, eigenen Meldungstexten/Präfixen.
  - `explain_error(source, details)` für die Templates und
    `has_explanation(source)` für den Quell-Scan-Test.
  - Sicherheit (SEC-10-Vertrag): Freigegebene Kontextwerte müssen der
    Whitelist entsprechen; rohe Exception-Texte, Tracebacks und
    Nicht-Whitelist-Strings erscheinen in der Ansicht **nicht**. Die
    rohe `details`/`message` bleibt wie bisher staff-only.
- **`trading/views.py`:** `error_log_view` liefert je Eintrag
  `explanation` (aus der Karte), dazu die Referenznummer; rohe
  Diagnose-Meldung + Traceback bleiben staff-only (SEC-10).
- **`trading/templates/trading/error_log.html`:** Jede Fehlerzeile zeigt
  zusätzlich „Referenz #N", die Erklärung (Was/Ursache/Was tun) und die
  sicheren Kontext-Details; Details-Spalte mit Hover-Tipp für
  Kontext-Zeilen.
- **`trading/trading_bot.py`:** Stabile Meldungstexte für den
  Symbol-Fehlerpfad (Präfix „Symbolverarbeitung {symbol}"), damit die
  Erklärungs-Karte und die sichere Dashboard-Kurzform zuverlässig
  funktionieren.

## Regressionstests

`trading/tests/test_bitunix_error_handling.py` (`ErrorExplanationTests`,
`ErrorExplanationSourceScanTests`):

- Jede der 12 Quellen liefert Erklärung + Referenznummer im Fehler-Log-
  Ausgang (HTML).
- Kontext-Auswahl: Symbole-Liste erscheint; rohe Exception-/Traceback-
  Texte erscheinen **nicht**; nicht-allowlist-Meldungen erscheinen nicht.
- Quell-Scan: `grep`-gleicher Test, dass alle in `trading_bot.py` /
  `market_scanner.py` benutzten `ErrorLog`-Quellen eine Erklärung haben.

## Prüfgrenzen

- Die Whitelist enthält die stabilen deutschen Meldungstexte der eigenen
  Fehlerklassen und Präfixe; ein neu eingeführter roher Python-Ausnahme-
  Text wird bewusst *nicht* freigegeben (er fällt in die „Server-Log"
  Angabe). Neue Fehlerklassen brauchen daher einen Eintrag in
  `error_explanations.py` – durch den Quell-Scan-Test erzwungen.
- Die Erklärung ist in der UI eine immer-sichtbare Zeile (kein Hover) –
  bewusste Entscheidung für Lesbarkeit auf Touch-Geräten; Hover-Tipps
  ergänzen die Details-Spalte zusätzlich.
