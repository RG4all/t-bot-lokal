# t-bot – Benutzer- und Indikatorhandbuch

**Version 2.3.0 · Stand 21. August 2026**

[TOC]

> t-bot ist eine experimentelle **Paper-Trading-Plattform**. Orders werden simuliert und nicht an eine Börse gesendet. Ergebnisse sind keine Anlageberatung und keine Garantie für zukünftige Entwicklungen.

## 1. Überblick

t-bot kombiniert:

- geschützten Mehrbenutzerzugriff,
- Konfigurationen pro Exchange und Marktart,
- Live-Marktdaten für mehrere Symbole,
- regelbasiertes Paper Trading,
- Dashboard, Charts, Kennzahlen und Trading-Log,
- PDF-, HTML- und CSV-Reports,
- Parameter-Backtests,
- persistentes Fehler- und Diagnose-Log.

Jeder Benutzer sieht ausschließlich seine eigenen Konfigurationen, Logs, Backtests und Reports.

## 2. Erste Schritte

1. Passphrase auf der Zugangsseite eingeben.
2. Registrieren oder anmelden.
3. **Konfigurationen → Neue Konfiguration** öffnen.
4. Exchange, Markt, Symbole, Kapital und Strategieparameter eingeben.
5. Speichern. t-bot prüft die Felder und validiert die Symbole live gegen die Börse.
6. Konfiguration aktivieren und das Dashboard öffnen.

Fehlerhafte Felder werden rot markiert. Eine Konfiguration wird bei nicht gelisteten Symbolen oder nicht erreichbarer Exchange nicht stillschweigend akzeptiert.

## 3. Konfigurationsfelder

### Basis

| Feld | Bedeutung |
|---|---|
| Name | Frei wählbare Bezeichnung, mindestens drei Zeichen. |
| Exchange | Binance, BingX, Bybit, BitMart oder Bitunix. |
| Markt | `Spot` oder `Futures`. Nicht jede Exchange unterstützt in t-bot beide Varianten. |
| Symbole | Kommagetrennte CCXT-Schreibweise, z. B. `BTC/USDT, ETH/USDT`. |

### Kapital und Risiko

| Feld | Bedeutung |
|---|---|
| Startkapital | Virtuelles Anfangskapital. |
| Trade Amount | Virtueller Nominalbetrag je Position. Kaufgebühr muss zusätzlich gedeckt sein. |
| Take Profit | Prozentuale positive Kursänderung, bei der geschlossen wird. |
| Stop Loss | Prozentuale negative Kursänderung, bei der geschlossen wird. |
| Sales Stop Threshold | Maximaler realisierter Gesamtverlust relativ zum Startkapital; `0` deaktiviert die Grenze. |
| Fee | Simulierte Gebühr je Order. Kauf- und Verkaufsgebühr werden berücksichtigt. |

### Ausführung und Strategie

| Feld | Bedeutung |
|---|---|
| Countdown | Wartezeit nach Botstart in Minuten, bevor Käufe erlaubt sind. Preise werden bereits gesammelt. |
| Time Interval | Auswertungsabstand in Sekunden, 1 bis 300. Binance empfängt dazwischen weiter WebSocket-Daten. |
| Indikatoren zurücksetzen | Leert den 10-Punkte-Preisbuffer nach einem Verkauf. |
| Acceleration-Schwelle | Mindestwert von `DVA / vorherige NDA` für einen Kauf. |
| DeltaDelta-Schwelle | Mindestwert des geglätteten NDA-Momentums. |
| NDA-Schwelle | Mindestwert der aktuellen normalisierten Preisänderung. |

API-Key und Secret-Key müssen gemeinsam oder gar nicht angegeben werden. Die aktuelle Anwendung handelt ausschließlich auf Papier; öffentliche Marktdaten benötigen keine privaten Schlüssel.

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

Ein persistenter kombinierter `miniTicker`-WebSocket liefert alle konfigurierten Symbole. Das vermeidet REST-Polling und Request-Weight-Bans. Verbindungen werden mit Backoff, mehreren Endpunkten und einem proaktiven Reconnect vor der Binance-24-Stunden-Grenze erneuert.

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

## 6. Indikatoren – exakte Berechnung

Der Live-Bot hält je Symbol die letzten zehn Preise. Für die Berechnung werden die jüngsten drei Preise verwendet:

- `P0`: aktueller Preis
- `P1`: vorheriger Preis
- `P2`: Preis davor

### 6.1 DA – absolute Delta-Änderung

```text
DA = P0 - P1
vorherige_DA = P1 - P2
```

DA zeigt die absolute Kursbewegung. Bei unterschiedlich teuren Assets ist sie allein schlecht vergleichbar.

### 6.2 NDA – normalisierte Delta-Änderung

```text
NDA = (P0 - P1) / P1 × 100
vorherige_NDA = (P1 - P2) / P1 × 100
```

NDA drückt die Bewegung prozentual aus. Beispiel: von 100 auf 101 ergibt ungefähr `+1 %`.

### 6.3 DVA und Beschleunigung

```text
DVA = NDA - vorherige_NDA
Beschleunigung = DVA / vorherige_NDA
```

DVA misst, wie stark sich das normalisierte Momentum verändert. Die Division verstärkt Änderungen relativ zum vorherigen Momentum. Ist die vorherige NDA null, setzt t-bot die Beschleunigung defensiv auf null, um eine Division durch null zu verhindern.

**Hinweis für Fortgeschrittene:** Bei sehr kleinen vorherigen NDA-Werten kann der Quotient stark ausschlagen. Schwellen sollten deshalb per Backtest und nicht isoliert gewählt werden.

### 6.4 DeltaDelta

Die aktuelle Implementierung verwendet eine Glättung der aktuellen und vorherigen NDA:

```text
DeltaDelta = (NDA + vorherige_NDA) / 2
```

Der Name ist historisch; mathematisch handelt es sich hier um einen Zwei-Punkt-Mittelwert und nicht um eine reine zweite Ableitung. Ein positiver Wert zeigt überwiegend positives kurzfristiges Momentum.

### 6.5 MVD – Verhältnis Minimum zu Maximum

```text
MVD = Minimum(Preisbuffer) / Maximum(Preisbuffer)
```

MVD liegt bei positiven Preisen zwischen 0 und 1. Werte nahe 1 bedeuten eine enge Handelsspanne; kleinere Werte eine größere Spanne. MVD wird protokolliert, ist aktuell aber kein direktes Kaufkriterium.

## 7. Kauf- und Verkaufslogik

Ein Kauf wird nur simuliert, wenn:

1. der Start-Countdown abgelaufen ist,
2. noch keine Position für das Symbol offen ist,
3. genügend freies virtuelles Kapital vorhanden ist,
4. die globale Verlustgrenze nicht erreicht ist,
5. NDA, DeltaDelta und Beschleunigung jeweils über ihrer Kaufschwelle liegen.

Eine Position wird geschlossen, wenn Take Profit oder Stop Loss erreicht ist oder die globale Verlustgrenze greift.

### Beispiel

- Startkapital: 1.000 USDT
- Trade Amount: 100 USDT
- Fee: 0,1 % je Seite
- Einstieg: 100 USDT, Menge etwa 1
- Ausstieg: 102 USDT

Bruttobewegung: 2 USDT. Davon werden Kauf- und Verkaufsgebühr abgezogen. Der im Sell-Log ausgewiesene P/L ist deshalb kleiner als 2 USDT.

## 8. Kill-Switch

Der rote Button **„Alle Positionen schließen“**:

1. verlangt zwei unabhängige Bestätigungen,
2. ruft für alle offenen Positionen frische Marktpreise ab,
3. blockiert währenddessen neue Käufe,
4. erstellt für jede erfolgreiche Liquidation einen Sell-Log,
5. meldet Teilerfolge und Fehler symbolgenau.

Der Bot muss laufen, damit ein aktueller Preis sicher beschafft werden kann. Ein Fehler bei einem Symbol verhindert nicht die Liquidation der übrigen Symbole.

## 9. Dashboard, Kontostand und Trading-Log

### Kontostandslogik

Die Oberfläche trennt jetzt Begriffe, die zuvor fälschlich als ein einzelner „aktueller Kontostand“ behandelt wurden:

```text
Verfügbarer Cash = Startkapital + realisierter P/L
                   - Summe(Einstiegswert + Kaufgebühr offener Positionen)

Netto-Marktwert offen = Summe(Menge × letzter Marktpreis - geschätzte Verkaufsgebühr)

Gesamtequity = verfügbarer Cash + Netto-Marktwert offen

Unrealisierter P/L = Netto-Marktwert offen - gebundenes Kapital
```

Direkt nach einem Kauf sinkt deshalb der **verfügbare Kontostand** um Positionswert plus Kaufgebühr. Die Gesamtequity bleibt – abgesehen von Gebühren und Kursbewegung – in ähnlicher Höhe. Nach dem Verkauf fließt der Nettoerlös zurück in den Cash-Bestand; der vollständige Trade-P/L wird realisiert.

Beispiel: Start 1.000, Kauf 100, Kaufgebühr 0,10. Unmittelbar danach sind ungefähr 899,90 Cash verfügbar und 100,10 gebunden. Bei einem aktuellen Netto-Marktwert von 101 liegt die Equity bei ungefähr 1.000,90.

### Trading-Log

Das Trading-Log zeigt 100 Einträge pro Seite, neueste zuerst. **Neuere** und **Ältere** navigieren serverseitig durch die Historie. Dadurch bleibt die Seite auch bei großen Datenmengen schnell.

Farben:

- Grün: Buy
- Rot: Sell
- Gelber „Verkaufen“-Button: aktuell offene Position

Portfolio- und Performancefelder werden regelmäßig aktualisiert. Die Equity-Kurve zeigt realisiertes Kapital.

## 10. Reports

Im Dashboard stehen drei Exportformate bereit:

- **PDF**: druckbarer Gesamtbericht mit Konfiguration, Cash, Equity, offenen Positionen, Kennzahlen, Trading-Log und eingebetteten Diagrammen.
- **HTML**: eigenständige Reportdatei mit denselben Informationen für Browser und Archiv.
- **CSV**: maschinenlesbarer vollständiger Trading-Export mit Zeit, Symbol, Aktion, Preisen, Menge, Gebühren, Order-ID, P/L, Cash-Snapshot und Tank; UTF-8 mit BOM.

Die Exportbuttons und wichtigen Kontofelder besitzen Hover-Hinweise (`title`), die Zweck und Dateninhalt erklären.

Dateinamenschema:

```text
username_exchange_config-id_YYYYMMDD_HHMMSS.ext
```

Beispiel: `anna_binance_5_20260820_184501.pdf`.

## 11. Backtesting

Backtests variieren die drei Kaufschwellen über Von/Bis/Schrittweite. t-bot begrenzt Kombinationen und historische Punkte, um Speicher- und CPU-Überlastung zu vermeiden. Für jedes Symbol wird nur der beste Kandidat dauerhaft gespeichert.

Empfohlener Ablauf:

1. Mit groben Schritten einen kleinen Bereich testen.
2. Den besten Bereich mit kleineren Schritten verfeinern.
3. Gebühren, Take Profit und Stop Loss realistisch setzen.
4. Ergebnisse auf einem anderen Zeitraum gegenprüfen.

Backtests sind keine Prognose. Overfitting entsteht, wenn Parameter zu eng an eine einzige Historie angepasst werden.

### Produktionsbetrieb und Isolation

Auf Render Free ist die Backtest-Ausführung absichtlich deaktiviert: 0,1 CPU und 512 MB werden vom Web-/Bot-Prozess benötigt, und Free unterstützt keinen isolierten Background Worker. Ein lokaler Thread könnte die absolute Bot-Priorität nicht garantieren. Lokal kann der serielle Entwicklungsfallback aktiviert werden.

Produktiv benötigt Backtesting `REDIS_URL` und einen separaten Celery-Worker auf Queue `backtest` mit Concurrency 1, Prefetch 1, maximal einem Task pro Child und 384-MB-Child-Limit. Lokal startet `scripts/setup_local.sh` Web/Bot, Worker, Redis und PostgreSQL in getrennten Containern. Der Installer erkennt Debian/Ubuntu, Arch, Fedora/RHEL, openSUSE und Alpine sowie die CPU-Architektur. Ein Hardwaretest dimensioniert CPU, RAM, Redis und PostgreSQL automatisch; die Render-Free-Simulation bleibt ohne expliziten Schalter aus. Fällt Redis vollständig aus, ist der serielle lokale Fallback explizit erlaubt. Details zum Setup stehen in `LOCAL_DEVELOPMENT.md`; Architekturdiagramm und Messergebnisse in `BACKTESTING_STUDY.md`; `render.worker.example.yaml` ist die absichtlich nicht automatisch aktivierte Produktionsvorlage.

## 12. Fehler-Log und Betrieb

Das Fehler-Log kann nach Konfiguration, Schweregrad, Status und Quelle gefiltert werden. Technische Details enthalten Exchange, Markt, Symbol und Retry-Informationen. Gelöste Einträge können als erledigt markiert werden.

Typische Meldungen:

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

Bei einem DB-Ausfall zeigt das Webinterface HTTP 503 statt einer internen Fehlerseite. Das bereits geöffnete Dashboard stoppt weitere API-Aufrufe lokal, verdoppelt die Wartezeit bis maximal 60 Sekunden und lässt jeweils nur einen Recovery-Test zu. Login und Passphrase liegen in signierten Cookie-Sessions und verursachen deshalb keine zusätzliche DB-Request-Schleife.

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

## 13. Integrierte Hilfe-Seite

Unter **Hilfe** beziehungsweise `/help/` wird diese Datei direkt innerhalb der Anwendung gerendert. Die Seite enthält ein Inhaltsverzeichnis, formatierte Tabellen und Codebeispiele sowie eine Druckansicht. Dadurch bleibt die Dokumentation mit dem Repository identisch und muss nicht doppelt gepflegt werden.

## 14. Render-Hinweise

- Free-Web-Services schlafen bei Inaktivität ein; ein In-Process-Bot ist dort nicht garantiert 24/7 aktiv.
- Free-Postgres läuft nach 30 Tagen ab.
- Für produktiven Dauerbetrieb: bezahlter Web-Service, dauerhaftes PostgreSQL, Redis, separater Worker und Scheduler.
- `/health/` bleibt absichtlich leichtgewichtig und öffentlich für Render.

## 15. Sicherheits- und Risikocheckliste

- Passphrase und Django `SECRET_KEY` niemals veröffentlichen.
- Keine echten Exchange-Schlüssel verwenden, solange Feldverschlüsselung und ein echtes Order-Risikomodell nicht eingerichtet sind.
- Kill-Switch-Ergebnis und Fehler-Log nach jeder Notfallaktion prüfen.
- Parameter zuerst backtesten und mit kleinen virtuellen Beträgen beobachten.
- Datenbank sichern, bevor eine kostenlose Instanz abläuft.
