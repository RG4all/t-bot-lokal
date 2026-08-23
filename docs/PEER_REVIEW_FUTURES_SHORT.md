# PEER_REVIEW_FUTURES_SHORT.md – Review: Futures-Hebel, Long/Short, Doku-Integration

**Datum:** 2026-08-23
**Autor:** Backend-Engineering (Trading-Core)
**Version:** 2.5.0
**Scope:** `trading/strategy.py` (neu), `trading/trading_bot.py`,
`trading/backtesting.py`, `trading/tasks.py`, `trading/forms.py`,
`trading/models.py` + Migration `0013`, `trading/views.py`, `trading/urls.py`,
`trading/admin.py`, Templates der Backtesting-Oberfläche,
`trading_bot_project/settings.py`, Konfigurationsdateien und Dokumentation.

---

## 1. Ziel und Vorgehen

Drei fachliche Ziele wurden umgesetzt und gegengeprüft:

1. Futures-Handel optimieren und den **Hebel je Börse konfigurierbar** machen.
2. Die gesamte Handelslogik auf **Short-Fähigkeit** prüfen und einseitige
   (nur-Long-)Stellen korrigieren.
3. Die **Backtesting-Dokumentation** (`docs/backtesting.md`) über eine
   Schaltfläche in der Backtesting-Oberfläche rendern.

Leitprinzip war: eine einzige Quelle der Wahrheit für Signal-, Richtungs- und
Hebelmathematik, damit Live-Bot und Backtest per Konstruktion identisch
entscheiden.

## 2. Audit: Wo war die Logik ausschließlich Long?

| Fundstelle | Befund vor der Änderung | Korrektur |
|---|---|---|
| `TradingBot.check_trading` | Einstieg nur bei `nda > Schwelle` etc.; Ausstieg über `(price - entry) / entry` | Signal über `strategy.entry_signal`, Ausstieg über `price_change_percent(direction)` |
| `TradingBot.execute_trade` | Ergebnis fest als `Erlös − Einsatz` | `strategy.gross_pnl` mit Richtungsvorzeichen; Margin, Hebel und Richtung werden journalisiert |
| `TradingBot._available_capital` | band den vollen Nominalwert | bindet Margin + Eröffnungsgebühr |
| `Backtesting.simulate_trading_detailed` | `should_buy` nur Long; Equity = `Cash + Menge × Kurs` | gemeinsames `entry_signal`; Equity = `Cash + Margin + schwebendes Ergebnis` |
| `Backtesting._close_position` | Erlösrechnung nur Long | Margin-Rückfluss + richtungsrichtiges Rohergebnis |
| `views._portfolio_snapshot` / `_cash_flow` | Marktwert und Cash-Flow nur Long, kein Hebel | richtungs- und hebelabhängig, exakte Paarbildung in `_cash_flows` |
| `create_plot_for_symbol`, Reports, CSV, Dashboard | keine Richtungsinformation | Richtung und Hebel in Chart, PDF/HTML-Report, CSV, Dashboard und API |

## 3. Fachliche Prüfung: Ist Short technisch und finanziell sinnvoll?

| Kriterium | Bewertung | Nachweis |
|---|---|---|
| Indikatorspiegelung korrekt | ✅ | `NDA`/`DeltaDelta` sind vorzeichenbehaftet und werden gespiegelt. Die Beschleunigung `DVA / vorherige NDA` ist richtungsneutral (Zähler und Nenner sind im Abwärtsimpuls beide negativ) und wird bewusst **nicht** gespiegelt – ein Spiegeln würde ihre Bedeutung umkehren. Test: `test_short_signal_mirrors_momentum_but_not_acceleration`. |
| Ergebnisrechnung | ✅ | `gross_pnl` mit Vorzeichen, Gebühren auf Ein- und Ausstiegsnominal. Tests: `test_short_position_profits_from_falling_prices`, `test_short_position_loses_on_rising_prices`. |
| TP/SL-Semantik | ✅ | Aus Sicht der Position gemessen; ein Short schließt beim Take-Profit unterhalb des Einstiegs. Test: `test_take_profit_and_stop_loss_are_mirrored_for_shorts`. |
| Short nur dort, wo möglich | ✅ | Spot kann nicht leerverkaufen: Formulare weisen ab, `execute_trade` wirft laut. Tests: `test_spot_rejects_leverage_and_short`, `test_spot_configuration_refuses_short_orders`. |
| Unbegrenztes Short-Risiko | ✅ dokumentiert und begrenzt | Stop-Loss plus simulierte Liquidation begrenzen den Verlust auf die Margin; Risikohinweis in `docs/backtesting.md` Abschnitt 3. |
| Nicht modellierte Kosten | ⚠️ bewusst offen | Funding-Rates und Slippage werden nicht simuliert. Das ist in `docs/backtesting.md` (Checkliste) ausdrücklich vermerkt, statt es stillschweigend schönzurechnen. |

## 4. Hebelmodell

```text
Margin         = Trade-Betrag
Nominalvolumen = Margin × Hebel
Menge          = Nominalvolumen / Einstiegskurs
Gebühr         = Nominalvolumen × Gebührensatz
ROI            = Kursbewegung × Hebel
Liquidation    = (1 − Erhaltungsmarge) / Hebel
```

* **Auflösungsreihenfolge:** Konfigurationswert → `EXCHANGE_LEVERAGE` je Börse
  → `DEFAULT_FUTURES_LEVERAGE`, immer begrenzt durch `EXCHANGE_MAX_LEVERAGE`.
* **Spot:** immer Hebel 1 – unabhängig von jeder Konfiguration.
* **Fehlertoleranz:** unbrauchbare Werte (Text, 0, `None`) führen nie zu einer
  Exception, sondern zum sicheren Standard (Test:
  `test_invalid_leverage_values_fall_back_safely`).
* **Börsenseite:** Bei hinterlegten API-Schlüsseln wird `set_leverage` je Symbol
  aufgerufen; Fehler werden nur protokolliert, damit Marktdaten und Simulation
  nicht an einer nicht unterstützten Hebel-API scheitern.
* **Risikogeländer:** Ein Stop-Loss jenseits der Liquidationsschwelle wird
  abgelehnt, weil er niemals auslösen könnte.

## 5. Rückwärtskompatibilität

| Risiko | Absicherung |
|---|---|
| Bestehende Konfigurationen | Neue Felder haben Standardwerte `leverage=1`, `trade_direction=long`; die Migration `0013` setzt sie für alle vorhandenen Zeilen. |
| Bestehende POST-Payloads ohne die neuen Felder | Beide Felder sind im Formular `required=False` und werden auf die sicheren Standardwerte normalisiert (Test: `test_existing_payloads_without_new_fields_stay_valid`). |
| Historische `TradingLog`-Zeilen ohne Margin | `_log_margin` und `TradingBot._restore_state` fallen auf den vollen Nominalwert zurück – exakt das bisherige Spot-Verhalten. |
| Bestehende Auswertungen | Die Aktion bleibt `buy`/`sell` (Eröffnung/Schließung); die Richtung steht in einer eigenen Spalte. Reports, CSV, Dashboard und APIs bleiben strukturkompatibel und wurden nur erweitert. |
| Rechenverhalten ohne Hebel | Bei Hebel 1 entspricht die Margin dem Nominalwert; alle 75 Bestandstests laufen unverändert grün. |

## 6. Dokumentations-Integration

* `views.DOCUMENTS` ist eine **Allowlist**: Nur registrierte Slugs sind
  erreichbar, ein Path-Traversal über die URL ist ausgeschlossen (Test:
  `test_unknown_documents_are_rejected`).
* Es wird dieselbe Markdown-Pipeline wie für die Hilfe verwendet
  (`extra`, `fenced_code`, `tables`, `toc`, `sane_lists`, `codehilite`) und
  dasselbe Layout (`_document_styles.html`).
* Der Cache ist zweistufig (LRU + thread-sicheres Dictionary) und je Dokument
  getrennt; ein Reset räumt beide Ebenen ab.
* Das Fragment `/api/docs/<slug>/` ist login-pflichtig, die Vollseite
  `/docs/<slug>/` verhält sich wie `/help/` (Passphrase-Gate greift identisch).
* Ohne JavaScript bleibt die Dokumentation über „Als Seite öffnen“ erreichbar.

## 7. Sicherheit

| Kriterium | Status | Bewertung |
|---|---|---|
| Keine neuen Schreib-Endpunkte | ✅ | Beide Dokument-Views sind `@require_GET`. |
| Eigentümerbindung | ✅ | Backtesting-Ansichten bleiben nutzergebunden (`get_object_or_404(..., user=request.user)`). |
| Eingabevalidierung | ✅ | Hebel per Model-Validator (1–125), Formular gegen das Börsenlimit, Worker/Bot rechnen zusätzlich mit `resolve_leverage`. |
| Keine echten Orders | ✅ | Es wird ausschließlich `set_leverage` aufgerufen – niemals `create_order`. |
| Fehlerpfade | ✅ | Liquidationen werden als `critical` im `ErrorLog` mit Kontext (Richtung, Hebel, Liquidationskurs) persistiert, entprellt über `_persist_error`. |

## 8. Tests

```text
110 Tests, davon 35 neu in trading/tests/test_futures_short.py
Ergebnis: OK (0 Fehler, 0 Failures)
ruff check .        -> All checks passed
ruff format --check -> nur zwei bereits vorher abweichende Bestandsdateien
```

Abgedeckt: Hebelauflösung und -deckelung je Börse, Fallbacks bei Fehlwerten,
Spiegelung der Indikatoren, richtungsabhängige Kurs-/Ergebnisrechnung,
Liquidationsschwellen, Formularvalidierung (Spot/Short, Hebelmaximum,
Stop-Loss vs. Liquidation), Bot-Trades in beide Richtungen, Zustandswiederher-
stellung, Backtest in Long/Short/beide Richtungen, Hebelwirkung auf Ergebnis
und Kapitalbindung, Futures-WebSocket-Endpunkte, ccxt-Optionen sowie die
gesamte Dokumentationsansicht.

## 9. Offene Punkte / bewusste Nichtziele

1. **Funding-Rates und Slippage** werden nicht simuliert (dokumentiert).
2. **Gestaffelte Erhaltungsmargen** großer Börsen sind auf eine einzige,
   konfigurierbare Rate vereinfacht; das ist konservativ, solange die Rate nicht
   zu niedrig gesetzt wird.
3. **Hedge-Modus** (Long und Short gleichzeitig im selben Symbol) ist bewusst
   nicht implementiert: Pro Symbol bleibt genau eine Position offen.

## 10. Nachlese 2.5.2 – Bybit-/Bitunix-Fehlerbilder und Hilfe-Alias

**Datum:** 2026-08-23  
**Version:** 2.5.2  
**Kontext:** Nach 2.5.0 meldete die Live-Vorschau vereinzelte 500er/Validierungsfehler
bei Bybit und Bitunix. Ausgehendes TLS ist in der Sandbox gesperrt, daher wurden die
Fehlerbilder exakt nachgebaut (gemockte HTTP-Payloads) und gegen die Fixes verifiziert.
Echte HTTPS-Calls bleiben vor dem Produktiveinsatz empfohlen.

### 10.1 Bybit – von reinem CCXT zu eigenem Public-Adapter

| Befund | Risiko | Fix |
|---|---|---|
| Bybit nutzte nur `ccxt.load_markets()` für Validierung und `fetch_ticker` für Preise. In der Sandbox (TLS gesperrt) schlug `load_markets()` mit `NetworkError` fehl und wurde als `MarketDataConnectionError` interpretiert – die Konfiguration ließ sich nicht aktivieren, obwohl die Börse nur temporär offline war. | Nutzer konnten Bybit-Konfigurationen nicht speichern/aktivieren; Autocomplete lieferte keine Vorschläge. | Neue Klassen `BybitPublicSymbolCatalog` ( `/v5/market/instruments-info` mit `nextPageCursor` ) und `BybitPublicMarketData` ( `/v5/market/tickers` ). Beide prüfen `retCode != 0`, filtern `status != Trading` und werfen bei leerer Liste eine verständliche `MarketDataConnectionError`. |
| Keine Pagination: Bybit listet >1000 Instrumente; ohne Cursor würden Symbole fehlen. | Validierung lehnte gültige Symbole ab. | Loop über `nextPageCursor` (max. 10 Seiten, Sicherheitslimit) sammelt alle Trading-Symbole. |
| Autocomplete ohne Fallback: Bei offline Bybit lieferte `get_available_symbols` einen 503, die Konfig-Seite blieb ohne Vorschläge. | UX-Einbruch in der Live-Vorschau. | `symbols._load_symbols` fängt `MarketDataConnectionError` für Bybit ab und liefert `_BYBIT_FALLBACK` (BTC/USDT, ETH/USDT, …). Die verbindliche Prüfung beim Speichern nutzt weiterhin keinen Fallback. |
| Trading-Bot nutzte CCXT für Bybit-Marktdaten, obwohl ein Public-REST ohne API-Keys ausreicht. | Zusätzliche Abhängigkeit von CCXT-Rate-Limits, inkonsistent zu Binance/Bitunix. | `TradingBot._setup_exchange` liefert für Bybit nun `BybitPublicMarketData`; Hebel-Setzung bleibt via CCXT (`set_leverage`, isoliert, linear) wenn Schlüssel vorhanden sind. |

### 10.2 Bitunix – robustere Fehlerpfade

| Befund | Fix |
|---|---|
| `available_symbols()` lieferte bei `code != 0` oder leerer `data`-Liste ein leeres Set, das später als „keine nutzbare Symbolliste“ gewertet wurde – die Fehlermeldung war korrekt, aber der Codepfad war schwer testbar und verwechselte „leere Liste“ mit „technisch offline“. | Explizite Prüfung `_successful` ( `code == 0` ), sowie leere-Liste-Check mit klarer `MarketDataConnectionError`. Tests: `test_available_symbols_raises_on_error_code`, `test_available_symbols_raises_on_empty`. |
| Spot-Ticker `/market/last_price` kann `data: null` oder `{}` liefern, wenn das Symbol gerade delistet wurde. Der alte Code griff auf `data.get(\"lastPrice\")` ohne None-Check zu und warf `AttributeError` statt `MarketDataConnectionError`. | `data.get(\"lastPrice\") if isinstance(data, dict) else data` plus expliziter None-Check mit verständlicher Fehlermeldung. Tests: `test_fetch_tickers_spot_raises_on_missing_price`. |
| Futures-Ticker `/tickers?symbols=…` liefert bei unbekanntem Symbol eine leere `data`-Liste, nicht einen Fehlercode. | Nach dem Parsen wird `missing = set(symbols).difference(prices)` geprüft und als `MarketDataConnectionError` gemeldet. Test: `test_fetch_tickers_raises_on_missing`. |
| Validierung erfolgte erst nach Ticker-Abruf, sodass ein ungültiges Symbol einen teuren Ticker-Call auslöste. | `fetch_tickers` ruft zuerst `validate_symbols` auf. |

### 10.3 Hilfe-Alias `/help/?doc=backtesting`

| Befund | Fix |
|---|---|
| `/help/` zeigte immer nur das Handbuch; `/help/?doc=backtesting` lieferte zwar 200, aber weiterhin das Handbuch statt der Backtesting-Doku. In der Live-Vorschau erwarteten Tester, dass der Query-Param die Doku umschaltet. | `help_view` liest `request.GET.get(\"doc\")`, normalisiert und prüft gegen `DOCUMENTS`-Allowlist. Unbekannte Werte fallen sicher auf `manual` zurück. Tests: `test_help_with_doc_backtesting_200_and_contains_backtesting`, `test_help_with_unknown_doc_falls_back_to_manual`. |

### 10.4 Seiten-Checks (Live-Vorschau)

Alle folgenden Seiten liefern mit eingeloggtem Nutzer HTTP 200 (ohne Login Redirect/200 wie vorgesehen):

- `/config/` – Konfigurationsformular
- `/help/` – Handbuch
- `/help/?doc=backtesting` – Backtesting-Doku via Alias
- `/docs/backtesting/` – eigenständige Doku-Seite
- `/backtesting/` – Index der Backtests
- `/api/docs/backtesting/` – Fragment für das Doku-Panel (login-pflichtig)

Verifiziert mit `Client().get()` in `HelpPagesTests` und `DocumentationButtonDomTests`.

### 10.5 Tests und Qualität

```text
166 Tests (56 neu in test_bybit_bitunix_fixes.py)
Ran 166 tests in ~32s
OK

ruff check . -> All checks passed
node --check static/js/bootstrap.bundle.min.js -> OK
node --check static/js/plotly-3.0.0.min.js -> OK
DOM-Test: documentation-toggle, documentation-panel, manual-content vorhanden
```

### 10.6 Nicht prüfbar hier und Empfehlung

Echte HTTPS-Calls zu `api.bybit.com`, `fapi.bitunix.com` und `openapi.bitunix.com`
sind in der Sandbox gesperrt (TLS-EOF). Die Fehlerbilder wurden daher mit gemockten
Payloads exakt nachgebaut und gegen die Fixes verifiziert. Vor dem Produktiveinsatz
bleibt ein manueller Abschlusstest gegen die Live-Börsen empfohlen:

1. Bybit Spot `BTC/USDT` und Futures `BTC/USDT` anlegen, Symbol-Autocomplete prüfen,
   Konfiguration speichern und Bot starten (Paper-Trading, ohne Keys).
2. Bitunix Spot und Futures analog testen, inkl. ungültigem Symbol `FAKE/USDT`
   (sollte als `SymbolValidationError` abgelehnt werden).
3. `/help/?doc=backtesting` und `/docs/backtesting/` im Browser öffnen,
   Doku-Button im Backtesting-Formular klicken und Fragment laden lassen.

### 10.7 Fazit

Die 2.5.2-Fixes schließen die Lücken zwischen CCXT-Only und Public-REST:

- Bybit und Bitunix haben jetzt deterministische, gemockt testbare Adapter
  mit klaren Fehlercodes, Pagination und Fallback-Vorschlägen.
- Die Hilfe-Seite ist als Alias flexibler und alle relevanten Seiten liefern
  in der Live-Vorschau 200.
- Die Gesamtzahl der Tests steigt von 110 auf 166, ohne bestehende Tests zu brechen.

