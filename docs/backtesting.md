# Backtesting mit t-bot

Dieses Kapitel erklärt das Backtesting-Modul von t-bot, seine Annahmen und die
Grenzen der Ergebnisse. Es richtet sich an Menschen, die eine Strategie
reproduzierbar untersuchen möchten – nicht an eine automatische
Gewinnversprechung. t-bot führt im Backtest **keine echten Orders** aus.

> **Risikohinweis:** Historische Ergebnisse sind keine Garantie für die
> Zukunft. Kryptowährungen können schnell und vollständig an Wert verlieren.
> Ein Backtest ersetzt weder ein Risikobudget noch einen Stop-Loss-Plan, eine
> Prüfung der Börsenliquidität oder eine unabhängige Anlageentscheidung.

## 1. Was t-bot tatsächlich simuliert

Ein Auftrag verwendet die von einer Konfiguration gesammelten `DataLog`-Preise.
Für jedes Symbol werden höchstens die ausgewählten jüngsten Preispunkte in
zeitlicher Reihenfolge verarbeitet. Es wird keine Kurs-Historie von einer
Börse nachgeladen. Dadurch ist der Test nachvollziehbar und belastet keine
Börsen-REST-API, setzt aber voraus, dass der Bot zuvor lange genug Daten
gesammelt hat.

Für jede Kombination aus drei Kaufschwellen wird eine eigene Simulation
berechnet:

1. Ein Kauf wird ausgelöst, wenn alle drei Indikatoren ihre Schwelle
   **überschreiten** und genügend virtuelles Kapital vorhanden ist.
2. Die Position wird beim Take-Profit, beim Stop-Loss oder am Ende der
   Historie geschlossen.
3. Die Kauf- und Verkaufsgebühr wird jeweils abgezogen.
4. Das beste Endkapital wird pro Symbol ausgewählt. Bei gleichen Ergebnissen
   entscheidet die Reihenfolge des Rasters.

Ein Test ist also ein kontrollierter Vergleich von Regeln auf einem festen
Datensatz. Er ist keine Prognose-Engine und kein Beweis, dass ein Parameter
„optimal“ bleibt.

## 2. Die Indikatoren und ihre Zuordnung

Die Eingabefelder sind absichtlich gleich benannt wie die Live-Konfiguration.
Damit lässt sich ein Backtest-Ergebnis später nachvollziehbar in eine
Konfiguration übertragen.

| Backtest-Feld | Live-Feld | Berechnung |
| --- | --- | --- |
| Beschleunigung (`acc`) | `div_DVA_prev_NDA_threshold_buy` | `DVA / vorherige NDA` |
| NDA | `nda_threshold_buy` | `((P0 - P1) / P1) × 100` |
| DeltaDelta | `deltadelta_threshold_buy` | `(NDA + vorherige NDA) / 2` |

Dabei bedeuten `P0`, `P1` und `P2` der aktuelle, der vorherige und der davor
liegende Preis.

### NDA

```text
current_DA       = P0 - P1
current_NDA      = current_DA / P1 × 100
previous_DA      = P1 - P2
previous_NDA     = previous_DA / P1 × 100
```

Ist der Basispreis null, wird die Berechnung defensiv übersprungen. Eine
ungültige oder nicht positive Preisreihe darf keine Division durch null
verursachen.

### DVA und Beschleunigung

```text
DVA             = current_NDA - previous_NDA
Beschleunigung  = DVA / previous_NDA
```

Ist die vorherige NDA null, setzt t-bot die Beschleunigung auf null. Das ist
eine technische Schutzmaßnahme und keine Aussage über das Momentum.

### DeltaDelta

```text
DeltaDelta = (current_NDA + previous_NDA) / 2
```

Der Zwei-Punkt-Mittelwert glättet das Momentum etwas, entfernt aber weder
Marktrauschen noch Sprünge durch schlechte Ticks.

## 3. Suchraster richtig aufbauen

Für jeden der drei Indikatoren werden **Von**, **Bis** und **Schrittweite**
eingegeben. Die Anzahl der Werte eines Bereichs ist:

```text
Anzahl = floor((Bis - Von) / Schrittweite + Rundungstoleranz) + 1
```

Die Gesamtzahl über alle Symbole lautet:

```text
Gesamt = Anzahl_acc × Anzahl_nda × Anzahl_deltadelta × Anzahl_Symbole
```

`Von` muss kleiner oder gleich `Bis` sein; jede Schrittweite muss positiv
sein. Kleine Schrittweiten und große Bereiche wachsen deshalb sehr schnell.
Beispiel:

```text
Beschleunigung:  5 Werte
NDA:           11 Werte
DeltaDelta:    11 Werte
Symbole:        2
Gesamt:     5 × 11 × 11 × 2 = 1.210 Simulationen
```

### Empfohlener zweistufiger Prozess

1. **Schnellprüfung:** Ein grobes Raster und wenige Preispunkte zeigen, ob ein
   Bereich überhaupt interessant ist.
2. **Ausgewogene Prüfung:** Den besten Bereich enger und mit mehr historischen
   Punkten wiederholen.
3. **Feinoptimierung:** Nur den engen Bereich fein abstufen und anschließend
   auf einem noch nicht verwendeten Zeitraum bestätigen.

Ein sehr feines Raster auf demselben Datensatz findet leicht Zufallswerte, die
nur in genau dieser Historie gut aussehen. Das nennt man Overfitting.

## 4. Templates

Das Formular bietet drei Templates. Sie ändern die Live-Konfiguration nicht,
sondern füllen nur den neuen Auftrag aus.

### Schnellprüfung

- kleiner Bereich um die aktuellen Live-Schwellen,
- grobe Schritte,
- verkürzte Historie,
- geeignet für einen ersten Plausibilitätscheck.

### Ausgewogen

- mittlerer Suchbereich,
- feinere Schritte,
- die vom Hardwareprofil erlaubte Standard-Historie,
- guter Ausgangspunkt für einen Vergleich mehrerer Symbole.

### Feinoptimierung

- größerer Bereich und feine Schritte,
- deutlich mehr Kombinationen,
- nur nutzen, wenn die Laufzeit und die Datenmenge ausreichen.

Nach der Auswahl können alle Werte manuell geändert werden. Die Anzeige im
Formular berechnet die Anzahl der Simulationen erneut, sobald ein Feld
geändert wird.

## 5. Preispunktgröße und Speicher

`Maximale historische Preispunkte` legt fest, wie viele jüngste Punkte je
Symbol aus `DataLog` gelesen werden. `Maximale Rasterpunkte je Parameter`
begrenzt zusätzlich jede der drei Indikatorachsen. Der Wert ist ein
**Limit**, keine Garantie: Sind weniger Daten vorhanden, verarbeitet der Backtest nur diese
Daten. Für Indikatoren sind mindestens drei Punkte erforderlich.

Mehr Punkte erhöhen die historische Tiefe, aber auch:

- die Laufzeit jeder Parameterkombination,
- den Speicherbedarf beim Laden mehrerer Symbole,
- die Gefahr, dass sehr alte Marktphasen mit völlig anderen Bedingungen das
  Ergebnis dominieren.

Das Hardwareprofil begrenzt den Formularwert zusätzlich. Ein explizit gesetztes
`BACKTEST_MAX_PRICE_POINTS` kann die organisatorische Obergrenze weiter
reduzieren. Die Anwendung überschreibt dabei nie die sichere Untergrenze von
100 Punkten im Formular, wenn das Hardwareprofil korrekt ermittelt wurde.

## 6. Variable Raster- und Kombinationsgrenze

Das Feld `Maximale Kombinationen (Hard-Limit)` ist eine harte Obergrenze über
alle Symbole. Es ist keine Empfehlung und kann einen Auftrag ablehnen, bevor
er Ressourcen verbraucht. Das wirksame Limit ist stets das kleinste dieser
Werte:

1. das im Formular gesetzte Limit,
2. das per `BACKTEST_MAX_COMBINATIONS` administrativ erlaubte Limit,
3. das aktuelle Hardwarebudget,
4. die absolute Anwendungssicherheitsgrenze.

Ein Worker prüft das Limit nochmals. Eine manipulierte HTTP-Anfrage kann die
Formularprüfung daher nicht umgehen. Wird die Hardware knapp oder ändert sich
das Container-Limit, wird ein Auftrag lieber abgelehnt als der Trading-Bot
verdrängt.

Die angezeigte `Rastergröße` ist die ungefähre maximale Anzahl von Werten je
Dimension, abgeleitet aus der dritten Wurzel des Kombinationsbudgets. Sie ist
ein Orientierungspunkt. Entscheidend ist immer das Produkt der drei
Dimensionen und der Symbolanzahl.

## 7. Hardwareprofil und Laufzeitschätzung

t-bot liest beim ersten Bedarf CPU-Anzahl, RAM und freien Speicher. In Docker
werden cgroup-Limits vor den Hostwerten bevorzugt. Das verhindert, dass ein
Container mit acht sichtbaren Host-Kernen ein Budget für acht Kerne annimmt,
obwohl ihm nur ein Kern zugeteilt wurde.

Die Laufzeitschätzung berücksichtigt:

- Anzahl der Kombinationen,
- Anzahl der Preispunkte,
- Anzahl der Symbole,
- die effektive CPU-Menge.

Sie wird als **grobe Schätzung** dargestellt. Sie enthält keine Garantie für
eine Fertigstellungszeit, denn Python-Version, Datenbank, Prozesslast,
Decimal-Rechenzeit und Worker-Plan beeinflussen das Ergebnis. Ein Neustart,
ein pausierter Auftrag oder ein Celery-Worker-Ausfall kann die tatsächliche
Zeit zusätzlich verändern.

Die Diagnose-API `/api/resources/` zeigt den Snapshot. Dort sind insbesondere
`cpu_count`, `memory_mb`, `storage_free_mb`, `max_price_points` und
`max_combinations` sichtbar. Mit `?refresh=1` wird der Cache für Diagnosezwecke
neu gelesen. Das normale Formular verwendet den gecachten Snapshot und führt
keinen I/O-Benchmark bei jedem Seitenaufruf aus.

## 8. Ausführung und Isolation

Backtests laufen über die Celery-Queue `backtest`. Der Worker verwendet
standardmäßig einen Prozess beziehungsweise eine Aufgabe gleichzeitig,
Prefetch eins und ein neues Child nach jeder Aufgabe. Das begrenzt die
Auswirkung eines speicherintensiven Tests auf den Trading-Bot.

Wenn Redis und ein Worker verfügbar sind, ist die Queue der bevorzugte Weg.
Für lokale Entwicklung darf der serielle lokale Fallback aktiviert werden.
Dieser Fallback teilt sich CPU, RAM und Datenbankverbindungen mit dem
Webprozess und ist daher nicht für einen produktiven 24/7-Betrieb gedacht.

Ein Backtest kann im Formular:

- sofort gestartet,
- pausiert und fortgesetzt,
- abgebrochen oder
- für einen späteren Zeitpunkt geplant werden.

Pause und Abbruch sind kooperativ. Der Worker prüft den Zustand regelmäßig;
eine bereits laufende einzelne Simulation wird nicht mitten in jeder
Arithmetik unterbrochen.

## 9. Datenqualität und Reproduzierbarkeit

Vor dem Start sollte geprüft werden:

- Ist der Bot für jedes Symbol lange genug aktiv gewesen?
- Sind die Daten zeitlich geordnet und enthält der Zeitraum Lücken?
- Wurde in dieser Zeit ein Markt pausiert, delistet oder umbenannt?
- Sind Spot- und Futures-Daten nicht versehentlich vermischt?
- Stimmen Gebühren, Take-Profit und Stop-Loss mit der geplanten Nutzung
  überein?
- Ist ausreichend Kapital für Trade-Betrag **plus Kaufgebühr** vorhanden?

Ein Auftrag speichert seine Parameter im `BacktestTask`. Zusammen mit dem
Symbol, dem Erstellungszeitpunkt und dem verwendeten Datenbestand kann ein
Ergebnis damit nachvollzogen werden. Für einen belastbaren Vergleich sollten
Datenzeitraum, Gebühren, Preispunktlimit und Ausführungsumgebung dokumentiert
werden.

### Out-of-sample-Prüfung

Teile die Historie gedanklich oder technisch in mindestens zwei Zeiträume:

- **In-sample:** Parameter auswählen.
- **Out-of-sample:** Parameter unverändert auf einem späteren Zeitraum prüfen.

Erst wenn die Regel in beiden Abschnitten plausibel ist, sollte sie in einem
Paper-Trading-Lauf beobachtet werden. Bei einem Wechsel der Marktstruktur ist
eine weitere Prüfung nötig.

## 10. Interpretation der Ergebnisse

Das Modul zeigt pro Symbol Endkapital, Profit, Käufe/Verkäufe, Win-Rate,
Brutto-Gewinn und -Verlust, Gebühren, Profit-Faktor, maximalen Drawdown und die
durchschnittliche Trade-Dauer. Die globale Zusammenfassung weist Profit und
Trade-Anzahl pro Markt aus. Zeitgestempelte Trades und eine Mark-to-Market-
Equity-Kurve machen auch offene Kursschwankungen sichtbar. Die Ergebnisse sind
nutzergebunden als A4-Querformat-PDF, eigenständiges HTML und UTF-8-CSV mit
Trade- und Equity-Zeilen exportierbar. Diese Kennzahlen müssen gemeinsam gelesen werden:

- Eine hohe Win-Rate kann mit wenigen großen Verlusten einhergehen.
- Ein höheres Endkapital kann aus einem einzelnen Ausreißer stammen.
- Viele Trades erhöhen die Gebührenbelastung und die Empfindlichkeit für
  Slippage.
- Ein Backtestpreis ist idealisiert; echte Ausführung kann verzögert oder
  teilweise sein.
- Das Ergebnis bewertet nur die simulierte Einzelposition pro Symbol und ist
  nicht automatisch ein Portfoliooptimierer.

Zusätzlich sollte der maximale Drawdown, die Verteilung der Gewinne und die
Stabilität über mehrere Zeiträume geprüft werden. Eine Konfiguration, die nur
bei einer einzigen Schwelle oder einem einzigen Coin gewinnt, ist besonders
fragil.

## 11. Top-Gainer-/Loser-Vorlage und Risiko-Filter

Im Konfigurationsformular kann der öffentliche Markt-Scanner für die gewählte
Exchange und Marktart aufgerufen werden. Er zeigt bis zu fünf Gainer und fünf
Loser, aber nur nach diesen Prüfungen:

1. **Volatilität:** Der absolute 24-Stunden-Ausschlag muss standardmäßig
   größer als 10 % sein.
2. **Volumenspitze:** Das Quotevolumen muss standardmäßig mindestens das
   1,5-Fache des Medianvolumens der geprüften Märkte betragen.
3. **Liquidität:** Das Tagesvolumen muss standardmäßig mindestens 10 % der
   bekannten Marktkapitalisierung erreichen. Zusätzlich muss die sichtbare,
   marktnahe Orderbuchtiefe mindestens 0,1 % des Tagesvolumens betragen; nur
   valide Bid- und Ask-Orders innerhalb von ±2 % des Mittelkurses zählen.
4. **Fundamentalqualität:** Bekannte etablierte Utility-Assets oder explizite
   Utility-Metadaten werden akzeptiert. Fehlen diese Daten, wird der Markt
   nicht als sicher eingestuft.
5. **Frische Daten:** Ticker und Orderbuch stammen aus der aktuellen
   öffentlichen Abfrage; ein kurzer Cache verhindert unnötige API-Last.

Alle vier numerischen Schwellen sind im Formular per Schieberegler einstellbar.
Die API prüft zusätzlich sichere Wertebereiche. Binance wird ohne lange
`symbols=[...]`-Ticker-URL abgefragt; Bitunix verwendet die aktuell
dokumentierten Spot- beziehungsweise Futures-Pfade. Liefert der dokumentierte
Bitunix-Spot-Kline-Pfad kein Volumen, wird der Markt ausdrücklich als nicht
qualifiziert ausgeschlossen.

Eine fehlende Marktkapitalisierung, ein unbekanntes 24h-Volumen oder eine
fehlende Orderbuch-Tiefe wird nicht als null
Risiko interpretiert, sondern als Ausschlussgrund. Deshalb können weniger als
fünf Ergebnisse oder gar keine Ergebnisse erscheinen. Das ist beabsichtigt.

Die Vorlage ersetzt keine Fundamentalanalyse. Auch ein etabliertes Asset kann
in einem volatilen Markt stark fallen. Volatile Märkte benötigen ein vorher
festgelegtes Risiko, eine passende Stop-Loss-Strategie und eine Positionsgröße,
die einen Totalverlust des eingesetzten Betrags verkraftbar macht. Bei
Futures kommen Liquidations-, Funding- und Hebelrisiken hinzu.

## 12. Praktische Beispielkonfiguration

Angenommen, die Konfiguration enthält zwei Symbole und die aktuellen
Schwellen sind `Beschleunigung = 0`, `NDA = 0` und `DeltaDelta = 0`.
Ein kleiner erster Test kann sein:

```text
Beschleunigung:  Von -0,5 bis 0,5, Schritt 0,25   = 5 Werte
NDA:            Von -0,25 bis 0,25, Schritt 0,125 = 5 Werte
DeltaDelta:     Von -0,25 bis 0,25, Schritt 0,125 = 5 Werte
Symbole:        2
Preispunkte:    1.000
Kombinationen:  5 × 5 × 5 × 2 = 250
```

Danach kann der gefundene Bereich beispielsweise auf ±0,25 verkleinert und
mit 2.500 oder 5.000 Punkten erneut geprüft werden. Das zweite Ergebnis
sollte nicht blind in die Live-Konfiguration kopiert werden: zuerst Gebühren,
Stop-Loss, Datenqualität, Ausreißer und einen separaten Zeitraum kontrollieren.

## 13. Fehlersuche

### „Mindestens drei Preispunkte benötigt“

Der Bot hat für das Symbol noch keine ausreichende Historie gespeichert oder
das Preispunktlimit wurde zu klein gesetzt. Erst Daten sammeln und den Test
erneut starten.

### Das Hard-Limit wird überschritten

Bereiche verkleinern, Schrittweiten vergrößern, weniger Symbole gleichzeitig
testen oder die Preispunktzahl reduzieren. Das Limit absichtlich sehr hoch zu
setzen ist keine Performance-Optimierung; es kann den Worker und damit andere
Aufgaben blockieren.

### Keine Top-Mover erscheinen

Mindestens ein Filter ist nicht erfüllt oder die Börse liefert keine
Marktkapitalisierung beziehungsweise kein Orderbuch. Das Ergebnis ist
sicherer als eine erfundene Liste. Manuelle Symbole können weiterhin über die
autoritative Symbolprüfung der Exchange eingegeben werden.

### Worker oder Redis ist offline

Die Backtest-Seite zeigt den Ausführungsmodus. Lokal kann der serielle Fallback
explizit aktiviert werden. Für einen produktiven Betrieb Redis, einen
separaten Celery-Worker und gegebenenfalls Celery Beat bereitstellen. Ein
Prozessneustart kann einen lokalen Auftrag unterbrechen.

## 14. Checkliste vor dem produktiven Paper-Trading

- [ ] Test auf einem getrennten Zeitabschnitt wiederholt.
- [ ] Gebühren und Slippage konservativ angesetzt.
- [ ] Stop-Loss und maximale Positionsgröße festgelegt.
- [ ] Keine API-Schreibrechte nötig; Paper-Trading bleibt aktiv.
- [ ] Liquidität und Orderbuch der konkreten Börse selbst geprüft.
- [ ] Hard-Limit und Ressourcenprofil zur Maschine passend.
- [ ] Backtest-Ergebnis und Parameter dokumentiert.
- [ ] Verlustgrenze beziehungsweise Sales Stop in der Konfiguration gesetzt.

Ein guter Backtest ist ein Werkzeug zum Verstehen einer Strategie. Er ist
nicht der letzte Schritt vor einem Risiko, sondern ein Baustein in einem
kontrollierten Test- und Beobachtungsprozess.
