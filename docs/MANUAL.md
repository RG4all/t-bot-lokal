# t-bot – Benutzer- und Indikatorhandbuch

**Version 2.4.14 · Stand 8. September 2026**

[TOC]

> t-bot ist eine experimentelle **Paper-Trading-Plattform**. Orders werden simuliert und nicht an eine Börse gesendet. Ergebnisse sind keine Anlageberatung und keine Garantie für zukünftige Entwicklungen.

## 1. Überblick

t-bot kombiniert:

- geschützten Mehrbenutzerzugriff mit Passphrase-Gate,
- Konfigurationen pro Exchange und Marktart,
- Live-Marktdaten für mehrere Symbole,
- regelbasiertes Paper Trading mit einstellbaren Indikatorschwellen,
- Dashboard, interaktive Multi-Y-Achsen-Charts, Kennzahlen und Trading-Log,
- PDF-, HTML- und CSV-Reports,
- speichereffizientes Parameter-Backtesting mit Raster-Optimierung,
- persistentes Fehler- und Diagnose-Log mit Filterung,
- interaktive Hover-/Info-Elemente (ⓘ) für alle bearbeitbaren Eingabefelder.

Jeder Benutzer sieht ausschließlich seine eigenen Konfigurationen, Logs, Backtests und Reports.

## 2. Erste Schritte

1. Passphrase auf der Zugangsseite eingeben.
2. Registrieren oder anmelden.
3. **Konfigurationen → Neue Konfiguration** öffnen.
4. Exchange, Markt, Symbole, Kapital und Strategieparameter eingeben.
5. Speichern. t-bot prüft alle Felder und validiert die Symbole live gegen die Börse.
6. Konfiguration aktivieren und das Dashboard öffnen.

Fehlerhafte Felder werden rot markiert. Eine Konfiguration wird bei nicht gelisteten Symbolen oder nicht erreichbarer Exchange nicht stillschweigend akzeptiert. Jedes bearbeitbare Eingabefeld verfügt über ein interaktives Info-Symbol (ⓘ) mit Tooltip-Hilfe.

### Passphrase-Gate ab Version 2.4.4

Die Produktions-Passphrase wird vom Betreiber privat als `PASSPHRASE` gesetzt (auf Render vom Blueprint erzeugt). Sie steht nicht im Repository und wird nicht von der App geloggt. Ohne sie oder ohne `SECRET_KEY` startet die App bei `DEBUG=False` bzw. auf Render nicht. Ein deaktivierter Gate ist dort ebenfalls ein Startfehler.

Nur lokale Entwicklung (`DEBUG=True`, kein Render) darf ohne konfigurierte Passphrase starten: `secrets.token_urlsafe(32)` erzeugt pro Laden der Settings einen neuen Wert, der als WARNING in der Startkonsole erscheint. Bei mehreren Prozessen/Autoreload können verschiedene Werte entstehen. Für stabile Sessions `SECRET_KEY` und `PASSPHRASE` explizit setzen; ein fehlender lokaler Signierschlüssel wird ebenfalls zufällig erzeugt, aber nicht ausgegeben. Die Setup-Skripte speichern private App-Secrets in der ignorierten Env-Datei (Modus 0600).

Nach einem Upgrade auf 2.4.4 oder einer Passphrase-Rotation muss der Gate erneut freigegeben werden. Der Session-Nachweis enthält keinen Klartext, sondern einen HMAC der aktuellen Passphrase unter dem privaten Signierschlüssel. Anmeldung und Eigentümerprüfung bleiben zusätzlich erforderlich. Das lokale Compose-Profil darf den Gate explizit abschalten, ist aber kein Produktionsprofil.

## 3. Konfigurationsfelder und Indikatoren-Mapping

### 3.1 Basis- und Kontofelder

> **Hinweis zu API-Schlüsseln:** API-Schlüssel werden **nicht** in der Datenbank gespeichert. Sie werden beim Start des Containers als Umgebungsvariablen `EXCHANGE_API_KEY` und `EXCHANGE_SECRET_KEY` übergeben. Der Bot liest sie einmal beim Start in den Arbeitsspeicher. Das Feld `has_live_credentials` in der Datenbank signalisiert nur, ob Live-Handel aktiviert ist.

Für Live-Handel:
```bash
# In docker-compose.yml oder .env.local setzen:
EXCHANGE_API_KEY=dein-api-key
EXCHANGE_SECRET_KEY=dein-secret-key
```

Für Paper-Trading: `has_live_credentials` auf `false` lassen (Standard) und keine Umgebungsvariablen setzen.

| Feld im Formular | Interner Name / Model | Bedeutung & Standardwert | Hover / Tooltip |
|---|---|---|---|
| **Name der Konfiguration** | `name` | Frei wählbare Bezeichnung (mindestens 3 Zeichen), z. B. `Binance Spot Top 5`. | Eindeutiger Name zur Unterscheidung im Dashboard. |
| **Börse (Exchange)** | `exchange` | Binance, BingX, Bybit, BitMart oder Bitunix. | Bestimmt den Marktdaten-Provider und das Verbindungsmodell. |
| **Marktart** | `market` | `Spot` (Kassamarkt) oder `Futures` (Derivate/Swaps). | Unterscheidet Handelsinstrumente und Ticker-Endpunkte. |
| **Handelspaare (Symbole)** | `symbols` | Kommagetrennte CCXT-Notation, z. B. `BTC/USDT, ETH/USDT`. | Autovervollständigung bietet passende Symbole; Live-Prüfung bei Save. |
| **Startkapital (USDT)** | `start_capital` | Virtuelles Anfangskapital (z. B. `100`). | Basis für Cash, Gesamt-Equity und Verlustgrenzen. |
| **Trade-Betrag pro Position (USDT)** | `trade_amount` | Virtueller Nominalbetrag je Kauforder (z. B. `10`). | Muss inkl. Kaufgebühr durch Startkapital gedeckt sein. |
| **Take Profit (%)** | `take_profit` | Prozentualer Kursgewinn ab Einstiegskurs zum automatischen Verkauf (z. B. `0.5 %`). | Löst Gewinnmitnahme bei Erreichen aus. |
| **Stop Loss (%)** | `stop_loss` | Prozentualer Kursverlust ab Einstiegskurs zur Verlustbegrenzung (z. B. `0.5 %`). | Löst Notverkauf bei Kursrückgang aus. |
| **Gesamtverlustgrenze / Sales Stop (%)** | `sales_stop_threshold` | Maximal zulässiger Gesamtverlust in % des Startkapitals; `0` = deaktiviert. | Globaler Schutz: stoppt neue Trades und schließt Positionen. |
| **Handelsgebühr je Order (%)** | `fee` | Simulierte Börsengebühr in Prozent (z. B. `0.1 %`), fällt bei Kauf und Verkauf an. | Wird realistisch vom Trade-P/L abgezogen. |
| **Start-Countdown (Minuten)** | `countdown` | Wartezeit nach Botstart in Minuten, bevor Käufe erlaubt sind. | Sammelt vorab Kursdaten zur Indikatorstabilisierung. |
| **Indikatoren nach Verkauf zurücksetzen** | `countdown_reset_indicators` | Leert den 10-Punkte-Preisbuffer nach einem Verkauf. | Verhindert Sofort-Wiedereinstiege auf altem Momentum. |
| **Auswertungsintervall (Sekunden)** | `time_interval` | Pause zwischen zwei Preisabfragen/Prüfzyklen (1 bis 300 s). | BitMart/Bitunix Spot min. 5 s für API-Schonung. |
| **Echtzeit-Handel aktiviert** | `has_live_credentials` | `true` oder `false`. | Nur auf `true` setzen, wenn `EXCHANGE_API_KEY` und `EXCHANGE_SECRET_KEY` als Umgebungsvariablen beim Container-Start uebergeben wurden. |

### 3.2 Indikatoren-Zuordnungsmatrix (Live-Konfiguration vs. Backtesting)

Die folgende Tabelle zeigt die exakte 1:1-Entsprechung zwischen den Feldern der **Live-Konfiguration / Dashboard-Schnellbearbeitung** und dem **Backtesting-Modul**:

| Indikator / Signal | Internes Model-Feld (DB) | Live-Konfiguration & Dashboard | Backtesting-Modul (Suchraum) | Mathematische Formel | Was bedeutet das? / Wofür steht das? |
|---|---|---|---|---|---|
| **Beschleunigung (Acceleration)** | `div_DVA_prev_NDA_threshold_buy` | **Beschleunigung (DVA / prev NDA) – Kaufschwelle** | `acc_from`, `acc_to`, `acc_steps` (**Beschleunigung – von / bis / Schritt**) | `DVA / vorherige_NDA`<br>mit `DVA = NDA - vorherige_NDA` | **Relative Momentum-Beschleunigung:** Misst die relative Änderungsrate des Momentums (Rate of Change der Kursänderungsrate). Ein Wert `> 0` signalisiert, dass der Kursanstieg an Dynamik gewinnt. |
| **DeltaDelta (geglättetes Momentum)** | `deltadelta_threshold_buy` | **DeltaDelta (geglättetes Momentum) – Kaufschwelle** | `deltadelta_from`, `deltadelta_to`, `deltadelta_steps` (**DeltaDelta – von / bis / Schritt**) | `(NDA + vorherige_NDA) / 2` | **Geglättetes 2-Punkt-Momentum:** Bildet den arithmetischen Mittelwert der aktuellen und vorangegangenen Preisänderung. Filtert kurzfristige Ausreißer und misst die mittlere kurzfristige Trendrichtung. |
| **NDA (normalisierte Preisänderung)** | `nda_threshold_buy` | **NDA (normalisierte Preisänderung) – Kaufschwelle** | `nda_from`, `nda_to`, `nda_steps` (**NDA – von / bis / Schritt**) | `((P0 - P1) / P1) * 100` | **Prozentuale Preisänderung:** Normalisierte Delta-Änderung vom aktuellen Preis `P0` zum Vorpreis `P1` in Prozent. Zeigt die unmittelbare prozentuale Kursbewegung an. |

> **Zusammenfassung:**
> - Was im Code `div_DVA_prev_NDA_threshold_buy` heißt, ist die Kaufschwelle für die **Beschleunigung** (`DVA / vorherige NDA`).
> - Im Backtesting-Modul wird genau dieser Wert über `Beschleunigung – von / bis / Schrittweite` (`acc_from`, `acc_to`, `acc_steps`) variiert und optimiert!
> - Im Dashboard und in der Konfiguration ist das Feld als **Beschleunigung (DVA / prev NDA) – Kaufschwelle** klar und eindeutig beschriftet.

---

## 4. Symbol-Autovervollständigung

Die Eingabehilfe wertet immer die aktuell gewählte Exchange und Marktart aus.

- Sie schlägt maximal 20 passende Symbole für den aktuellen Text nach dem letzten Komma vor.
- Ein Klick übernimmt das Symbol und bereitet den nächsten Eintrag vor.
- Kataloge werden 15 Minuten gecacht, um Börsen-APIs zu schonen.
- Binance verwendet eine kuratierte Liste liquider Paare, damit kein REST-Request-Weight entsteht.
- Die Vorschlagsliste ist **nicht** die Sicherheitsprüfung: Beim Speichern und Aktivieren folgt immer eine verbindliche Live-Validierung.

Beispiel: `BT` kann im Spot-Modus `BTC/USDT` vorschlagen; Futures kann zusätzlich kontraktspezifische Paare anbieten.

## 5. Marktdaten und Exchanges

### Binance

Ein persistenter kombinierter `miniTicker`-WebSocket liefert alle konfigurierten Symbole über eine einzige TCP-Verbindung. Das vermeidet REST-Polling und Request-Weight-Bans. Verbindungen werden mit Backoff, mehreren Endpunkten und einem proaktiven Reconnect vor der Binance-24-Stunden-Grenze erneuert.

### BingX und Bybit

Die öffentlichen CCXT-Marktkataloge und gebündelten Ticker-Endpunkte werden genutzt. CCXT-Rate-Limiting ist aktiviert.

### BitMart

Spot-Marktdaten laufen über die aktuelle öffentliche BitMart-V3-API. Symbole werden vorher gegen `/spot/v1/symbols` geprüft. BitMart Futures ist bewusst nicht freigeschaltet, solange kein zuverlässig getesteter öffentlicher Adapter vorhanden ist.

### Bitunix

- Futures: offizielle öffentliche Trading-Pair- und Batch-Ticker-Endpunkte von `fapi.bitunix.com`.
- Spot: öffentliche Spot-Pair- und Last-Price-Endpunkte; mindestens 5 Sekunden Intervall begrenzen Einzelabfragen defensiv.
- Keine privaten API-Schlüssel für Paper Trading.
- Ist ein öffentlicher Endpoint nicht verfügbar oder liefert keinen verifizierbaren Katalog, wird die Konfiguration sicher abgelehnt. Dadurch entsteht kein aggressiver Fehler-/Retry-Loop und kein API-Ban.

### Lokale Marktanalyse ohne Binance-REST

Die Seite **Analyse** verwendet ausschließlich die bereits vom Bot gestreamten und in `DataLog` gespeicherten Preise. Sie erzeugt daraus Zeit-Buckets (1m bis 1d) und berechnet SMA 5/15. Dadurch entstehen exakt null externe Analyse-Requests, kein Binance-Request-Weight und kein 418-IP-Ban. Analyse und laufender Binance-WebSocket des Bots sind technisch getrennt. Mindestens 16 lokale Intervalle werden benötigt; bei zu wenig Historie zeigt die Seite eine konkrete Sammelzeit-Meldung.

## 6. Indikatoren – exakte Berechnung und Formeln

Der Live-Bot hält je Symbol die letzten zehn Preise im RAM-Buffer. Für die Berechnung der Strategieindikatoren werden die jüngsten drei Preise verwendet:

- `P0`: aktueller Preis (jüngster Kurs)
- `P1`: vorheriger Preis (1 Zyklus davor)
- `P2`: Preis davor (2 Zyklen davor)

### 6.1 DA – absolute Delta-Änderung

```text
DA = P0 - P1
vorherige_DA = P1 - P2
```

DA zeigt die absolute Preisdifferenz in Kurseinheiten (z. B. USDT).

### 6.2 NDA – normalisierte Delta-Änderung

```text
NDA = (P0 - P1) / P1 × 100
vorherige_NDA = (P1 - P2) / P1 × 100
```

NDA drückt die Kursänderung prozentual bezogen auf den Basispreis aus. Beispiel: Ein Anstieg von 100 auf 101 ergibt exakt `+1.0 %`.

### 6.3 DVA und Beschleunigung (`div_DVA_prev_NDA`)

```text
DVA = NDA - vorherige_NDA
Beschleunigung = DVA / vorherige_NDA
```

- **DVA (Delta Value Acceleration):** Differenz zwischen aktuellem und vorigem prozentualen Momentum.
- **Beschleunigung (`div_DVA_prev_NDA`):** Setzt die Momentum-Veränderung ins Verhältnis zum Ausgangsmomentum.
- **Nullstellenabsicherung:** Ist `vorherige_NDA == 0`, setzt t-bot die Beschleunigung defensiv auf `0.0`, um eine Division durch Null sicher zu verhindern.

### 6.4 DeltaDelta

```text
DeltaDelta = (NDA + vorherige_NDA) / 2
```

Mittelwert aus aktuellem und vorigem normalisiertem Momentum zur Rauschreduktion.

### 6.5 MVD – Verhältnis Minimum zu Maximum

```text
MVD = Minimum(Preisbuffer) / Maximum(Preisbuffer)
```

MVD liegt bei positiven Preisen zwischen 0 und 1. Werte nahe 1 bedeuten eine enge Handelsspanne; kleinere Werte eine größere Spanne. MVD wird in `DataLog` protokolliert.

## 7. Kauf- und Verkaufslogik

Ein Kauf wird nur simuliert, wenn **alle** folgenden Bedingungen gleichzeitig erfüllt sind:

1. Der Start-Countdown ist abgelaufen (`start_countdown_over == True`).
2. Für dieses Symbol ist aktuell keine Position offen.
3. Genügend freies virtuelles Kapital für den Trade-Betrag plus Kaufgebühr ist verfügbar.
4. Die globale Verlustgrenze (`sales_stop_threshold`) ist nicht erreicht.
5. Alle drei Indikatorschwellen werden gleichzeitig überschritten:
   - `NDA > nda_threshold_buy`
   - `DeltaDelta > deltadelta_threshold_buy`
   - `Beschleunigung > div_DVA_prev_NDA_threshold_buy`

Eine offene Position wird automatisch geschlossen (Verkauf), wenn:
- Der Kursanstieg den **Take Profit (%)** erreicht oder überschreitet,
- Der Kursrückgang den **Stop Loss (%)** erreicht oder überschreitet, oder
- Die globale Gesamtverlustgrenze greift.

## 8. Kill-Switch

Der Button **„⚠ Alle Positionen schließen“**:

1. Verlangt zwei unabhängige Bestätigungsdialoge im Browser.
2. Ruft für alle offenen Positionen frische Marktpreise ab.
3. Blockiert währenddessen neue Käufe.
4. Erstellt für jede erfolgreiche Liquidation einen `sell`-TradingLog.
5. Meldet Teilerfolge und eventuelle Einzelfehler symbolgenau.

## 9. Dashboard, Kontostand und Trading-Log

### Kontostandslogik

Die Oberfläche trennt exakt:

```text
Verfügbarer Cash = Startkapital + realisierter P/L
                   - Summe(Einstiegswert + Kaufgebühr offener Positionen)

Netto-Marktwert offen = Summe(Menge × letzter Marktpreis - geschätzte Verkaufsgebühr)

Gesamtequity = verfügbarer Cash + Netto-Marktwert offen

Unrealisierter P/L = Netto-Marktwert offen - gebundenes Kapital
```

Direkt nach einem Kauf sinkt der **verfügbare Kontostand** um den gebundenen Positionswert plus Kaufgebühr. Die Gesamtequity bleibt dabei erhalten. Nach dem Verkauf fließt der Nettoerlös zurück in den Cash-Bestand; der vollständige Trade-P/L wird realisiert.

### Trading-Log

- 100 Einträge pro Seite mit Server-Paginierung.
- Grün: Buy-Orders.
- Rot: Sell-Orders.
- Gelber „Verkaufen“-Button für jede aktuell noch offene Position zum manuellen Schließen.

## 10. Reports

Im Dashboard stehen drei Exportformate bereit:

- **PDF**: Druckbarer Gesamtbericht mit Konfiguration, Cash, Equity, offenen Positionen, Kennzahlen, Trading-Log und eingebetteten Diagrammen.
- **HTML**: Eigenständige Reportdatei für Offline-Betrachtung im Browser.
- **CSV**: Vollständiger Trading-Export mit UTF-8-BOM für Excel und Tabellenkalkulation. Die zwölf Spalten und die chronologische Reihenfolge (Zeitstempel, dann ID) bleiben unverändert. Ab 2.4.13 erzeugt ein wiederverwendeter `io.StringIO`-Zeilenpuffer das CSV; Sonderzeichen und eingebettete Zeilenumbrüche werden weiterhin durch `csv.writer` maskiert. [Technische Details und Streaming-Prüfgrenzen](BUG-14-csv-echo-true-stream.md).

Dateinamenschema: `username_exchange_config-id_YYYYMMDD_HHMMSS.ext` (z. B. `anna_binance_5_20260820_184501.pdf`).

## 11. Backtesting

Das Backtesting-Modul optimiert die drei Kaufschwellen über konfigurierbare Suchraster:

- **Beschleunigung – von / bis / Schrittweite** (`acc_from`, `acc_to`, `acc_steps`)
- **NDA – von / bis / Schrittweite** (`nda_from`, `nda_to`, `nda_steps`)
- **DeltaDelta – von / bis / Schrittweite** (`deltadelta_from`, `deltadelta_to`, `deltadelta_steps`)
- **Trading-Parameter:** Trade-Betrag, Take Profit, Stop Loss, Gebühr und maximale historische Preispunkte.

Für jedes Symbol wird das Threshold-Set ermittelt, welches das höchste
Endkapital erzielt. Die Ergebnisansicht zeigt zusätzlich Profit pro Markt,
Brutto-Gewinn und -Verlust, Gebühren, Profit-Faktor, maximalen Drawdown,
Win-Rate, durchschnittliche Trade-Dauer, jeden zeitgestempelten Trade und eine
Mark-to-Market-Equity-Kurve. PDF-Seiten werden im A4-Querformat erzeugt, damit
die Tradespalten lesbar bleiben. Derselbe nutzergebundene Backtest kann als
PDF, eigenständiges HTML oder UTF-8-CSV einschließlich der Equity-Punkte
exportiert werden.

### Templates, Hardwarebudget und Laufzeit

Das Formular bietet die Templates **Schnellprüfung**, **Ausgewogen** und
**Feinoptimierung**. Sie füllen nur den neuen Auftrag relativ zu den aktuellen
Schwellen; die Live-Konfiguration wird nicht geändert. Die Felder
`Maximale historische Preispunkte`, `Maximale Rasterpunkte je Parameter` und
`Maximale Kombinationen (Hard-Limit)` sind frei kontrollierbar, werden aber
durch die erkannten CPU-, RAM- und
Speicherressourcen begrenzt. Der Worker prüft dieses Hard-Limit ein zweites
Mal. `BACKTEST_MAX_COMBINATIONS` und `BACKTEST_MAX_PRICE_POINTS` erlauben dem
Administrator, die absoluten Obergrenzen weiter zu reduzieren.

Die Seite zeigt eine grobe Laufzeit aus Symbolanzahl, Raster, Preispunkten und
CPU-Profil. Sie ist ausdrücklich nur eine Schätzung. `/api/resources/` liefert
den zugrunde liegenden Snapshot; Docker berücksichtigt cgroup-Limits statt
blind die Hostressourcen zu verwenden. Eine ausführliche Erklärung steht in
der Repository-Datei `docs/backtesting.md`; die App-Hilfe enthält die
wesentlichen Erläuterungen ebenfalls in diesem Abschnitt.

### Isolation und Ressourcenschonung

Auf Render Free ist die Backtest-Ausführung zum Schutz des Trading-Bots deaktiviert. Produktiv wird `REDIS_URL` mit einem separaten Celery-Worker genutzt. Lokal kann der serielle Fallback verwendet werden. Preispunkte und Rastergrößen werden überwacht, um Überlastung zu verhindern. Die effektive Obergrenze ist hardwareabhängig und standardmäßig höchstens 20.000 Kombinationen.

### Top-Gainer-/Loser-Konfiguration

Das Konfigurationsformular kann pro Exchange bis zu fünf qualifizierte Gainer und
Loser vorschlagen. Vier Schieberegler steuern den Mindest-24h-Ausschlag
(Standard 10 %), den Volumen-Ausreißer (1,5× Median), das Verhältnis von
Tagesvolumen zu Marktkapitalisierung (10 %) und die marktnahe Orderbuch-Tiefe
zum Tagesvolumen (0,1 %). Für die Tiefe zählen nur valide Bid- und Ask-Orders
innerhalb von ±2 % des Mittelkurses. Der Scanner verwendet bei Binance einen
kompakten Abruf aller 24h-Ticker und die aktuell dokumentierten Bitunix-Endpunkte.
Fehlende Marktkapitalisierung, 24h-Volumendaten oder ein nicht belastbares
Orderbuch führen immer zum Ausschluss und werden nicht günstig geschätzt. Das
Ergebnis ist keine Anlageempfehlung; volatile Märkte benötigen ein begrenztes
Risiko und eine passende Stop-Loss-Order.

### Zugriff auf HTTP-API und WebSockets

Die vorhandenen API-URLs und Nutzdatenformate bleiben in 2.4.4 unverändert. Ohne aktuelle Gate-Freigabe leiten geschützte HTTP-Endpunkte zuerst mit `302` auf `/gate/?next=…` um; `/health/` und statische Dateien bleiben ausgenommen. WebSocket-Verbindungen zu `/ws/backtest/<id>/` benötigen beim Aufbau zusätzlich zum angemeldeten Eigentümer einen aktuellen Gate-Nachweis; andernfalls werden sie vor Annahme mit Anwendungscode `4403` abgelehnt.

`POST /gate/`, `/login/`, `/register/` und `/admin/login/` teilen ein prozesslokales Limit von fünf POSTs je IP in 15 Minuten, auch für erfolgreiche POSTs. Der nächste POST liefert `429` mit `Retry-After: 900`. Hinter Proxys `RATE_LIMIT_TRUSTED_PROXIES` korrekt setzen; mehrere Web-Prozesse brauchen zusätzlich ein gemeinsames Limit. Siehe [Deployment-Anleitung](https://github.com/RG4all/t-bot-lokal/blob/tbot.local/docs/README.md).

Alle JSON-API-Endpunkte unter `/api/…` senden seit 2.4.8 die Header `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` und `Pragma: no-cache`. Browser und zwischengeschaltete Proxies/CDNs speichern die benutzerbezogenen Handels-, Portfolio-, Log- und Marktdaten damit nicht zwischen.

Seit 2.4.9 setzt jede HTTP-Antwort zusätzlich den Header `Permissions-Policy: camera=(), microphone=(), geolocation=()`. Kamera, Mikrofon und Geolokation sind damit für alle Origins deaktiviert; die Anwendung benötigt diese Browser-APIs nicht.

## 12. Fehler-Log und Betrieb

Das Fehler-Log kann nach Konfiguration, Schweregrad, Status und Quelle gefiltert werden. Gelöste Einträge können als erledigt markiert werden. Seit **2.4.10** erhalten normale Konten generische Meldungen mit einer Referenznummer statt technischer Diagnosen; bei anhaltenden Problemen diese Referenz und die Konfiguration dem Betreiber nennen.

Technische Log-Meldungen, Exception-Typen und Details sind nur für Betreiberkonten (`is_staff`) sichtbar, weiterhin ausschließlich für eigene Konfigurationen. Das gilt auch für alte Einträge. Betreiber können zusätzlich die geschützten Server-Logs bzw. die vorhandene Django-Administration mit den entsprechenden Berechtigungen verwenden. Staff-Rechte nicht an normale Nutzer vergeben, um die generische Anzeige zu umgehen.

Auch Flash-Meldungen, JSON-Fehler, PDF-/HTML-Reportfehler und gespeicherte Backtest-Fehler zeigen keine internen Exception-Texte mehr. Der ursprüngliche Fehler wird serverseitig mit Traceback geloggt; fällt die zusätzliche Speicherung im Fehler-Log aus, bleibt die generische Antwort erhalten. Fachliche Formularhinweise, etwa zu nicht gelisteten Symbolen aus der eigenen Eingabe, bleiben verfügbar. [Security-Nachweis SEC-10](SEC-10-information-disclosure.md).

Typische **technische Log-Meldungen für Betreiber**:

- `SymbolValidationError`: Paar ist nicht gelistet oder passt nicht zur Marktart.
- `RateLimitError`: Börse hat 418/429 geliefert; t-bot wartet den angegebenen Zeitpunkt oder Backoff ab.
- `WebSocketReconnectError`: interne Wiederverbindungen waren erfolglos.
- `OperationalError could not translate host name` oder `connection refused`: PostgreSQL beziehungsweise Render-Netzwerk ist nicht erreichbar.
- `remaining connection slots are reserved for roles with the SUPERUSER attribute`: Die direkten PostgreSQL-Client-Slots sind ausgeschöpft. Das ist kein DNS-Problem und war die tatsächliche Ursache der wiederkehrenden Ausfälle.

### Verbindungsmodell ab Version 2.0.4

1. Alle Bot-ORM-Operationen laufen über einen eigenen Executor mit standardmäßig **einem** Worker. Damit kann der Bot nicht mehr für jeden `sync_to_async`-Thread eine zusätzliche PostgreSQL-Verbindung öffnen.
2. Direkte Free-Postgres-Verbindungen verwenden `CONN_MAX_AGE=0`; Django schließt HTTP-, Task- und Bot-Verbindungen nach ihrer Arbeit, statt die knappen Slots 60 Sekunden pro Thread zu halten.
3. Lokale Backtests laufen nacheinander statt parallel.
4. Migrationen verwenden mit `USE_DIRECT_DATABASE_URL=True` die direkte URL.
5. `DATABASE_POOL_URL` wird nur verwendet, wenn bei einer bezahlten Render-Datenbank PgBouncer aktiviert wurde. Render-Managed-Pooling ist für Free-Datenbanken nicht verfügbar.
6. Ein alternativer DB-Host wird nur noch über `DATABASE_FALLBACK_HOST` verwendet; ein automatisch geratener Host ist entfernt.

Bei einem DB-Ausfall zeigt das Webinterface HTTP 503 statt einer internen Fehlerseite. Das bereits geöffnete Dashboard stoppt weitere API-Aufrufe lokal, verdoppelt die Wartezeit bis maximal 60 Sekunden und lässt jeweils nur einen Recovery-Test zu. Login-Zustand und HMAC-Gate-Nachweis liegen in signierten Cookie-Sessions und verursachen deshalb keine zusätzliche DB-Request-Schleife.

### Weiterhandel bei Frontend-/DB-Störung

Der Trading-Thread ist vom Browser unabhängig. Schließt der Nutzer das Dashboard oder ist nur das Frontend nicht erreichbar, laufen Preisstream und Strategie weiter. Ist PostgreSQL vorübergehend ausgefallen, arbeitet der Bot mit der letzten erfolgreich validierten Konfiguration weiter:

- Preisabfragen und Indikatorberechnung laufen weiter.
- DataLogs dürfen während der Störung ausfallen; sie sind nicht orderkritisch.
- Nicht speicherbare TradingLogs werden geordnet im RAM gepuffert.
- Nach DB-Recovery werden bis zu 100 gepufferte Einträge je Zyklus atomar nachgeschrieben.
- Der Dashboard-Status zeigt die Zahl der wartenden TradingLogs.
- Bei 1.000 ungepufferten Logs blockiert t-bot neue, nicht mehr sicher journalisierbare Trades statt still Daten zu verlieren.

Das RAM-Journal überlebt keinen kompletten Container-Neustart. Für garantierten 24/7-Betrieb sind deshalb ein externer Redis/Queue-Worker oder eine dauerhaft verfügbare Datenbank und ein bezahlter Render-Service erforderlich.

Für Bot-Threads greift zusätzlich ein globaler Circuit-Breaker: Nach fünf koordinierten Fehlversuchen werden weitere DB-Operationen fünf Minuten lang sofort verworfen. Danach führt genau ein Thread einen Recovery-Versuch aus. Konfigurationen werden höchstens alle 30 Sekunden neu geladen und DataLogs standardmäßig nur alle 10 Sekunden je Symbol geschrieben.

## 13. Integrierte Hilfe-Seite und Performance

Die Hilfe-Seite (`/help/`) rendert dieses Handbuch mit Inhaltsverzeichnis, formatierten Tabellen, Code-Highlighting und Druckansicht.

**Minimaler Ressourcenverbrauch:**
- Das gerenderte HTML wird mittels `@lru_cache(maxsize=1)` im Arbeitsspeicher gehalten.
- Die Markdown-Kompilierung erfolgt exakt **einmal** beim ersten Aufruf und erzeugt bei nachfolgenden Anfragen **nahezu 0 % CPU- und I/O-Last**.
- Die Pfadsuche prüft automatisch `docs/MANUAL.md` sowie `MANUAL.md` im Projektstamm.

## 14. Render-Hinweise

- Free-Web-Services schlafen bei Inaktivität ein; für 24/7-Betrieb empfiehlt sich ein bezahlter Service oder ein lokaler Container.
- `/health/` liefert leichtgewichtig `{"status": "ok", "version": "..."}` für Health-Checks.

## 15. Sicherheits- und Risikocheckliste

- Passphrase und Django `SECRET_KEY` niemals veröffentlichen. Temporäre lokale Gate-Werte erscheinen bewusst nur im Entwicklungs-WARNING; auch diese Logs privat halten. Produktions-Secrets müssen stabil und für alle Worker identisch sein.
- API-Schlüssel als Umgebungsvariablen (`EXCHANGE_API_KEY`, `EXCHANGE_SECRET_KEY`) beim Container-Start übergeben — niemals in der Datenbank speichern.
- Paper Trading simuliert Ausführungen – keine Garantie für reale Marktausführungen.
- Alle bearbeitbaren Felder vor dem Bot-Start per Info-Hover (ⓘ) und Backtest prüfen.
- Regelmäßige Backups der Datenbank durchführen.
- Die JSON-API-Endpunkte liefern no-cache-Header (`Cache-Control`/`Pragma`), damit keine Handelsdaten zwischengespeichert werden; ein vorgeschalteter Reverse-Proxy/CDN muss dies bei Bedarf zusätzlich absichern.
- Seit 2.4.9 ist der Zugriff auf Kamera, Mikrofon und Geolokation per `Permissions-Policy`-Header deaktiviert (`camera=(), microphone=(), geolocation=()`).

