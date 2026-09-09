# BUG-14 – CsvEcho True-Stream

- **Finding:** `CsvEchoNotTrueStream` (Prompt 14 / Security-Audit §3.3)
- **Status:** **Fixed**
- **Release:** **2.4.13** · **Datum:** 2026-09-08
- **Einstufung:** LOW – Bug / Kompatibilitäts-Refactoring
- **Geprüfter Ausgangsstand:** 2.4.12 / `3fa5d75a4dc0a9207d1f89c9c0969a9f4dbae035`
- **Fix-Commit:** [`7158986` – `refactor(views): replace _CsvEcho with io.StringIO`](https://github.com/RG4all/t-bot-lokal/commit/715898635a7a51aa794e72069838648d7a8ea8f9)

## Befund und Einordnung

`generate_report_csv` verwendete einen eigenen `_CsvEcho`-Adapter, dessen
`write()` den geschriebenen Text zurückgab. Der CSV-Generator gab deshalb
den Rückgabewert von `csv.writer.writerow()` direkt an die Response weiter.
Ein Standard-Textpuffer liefert beim Schreiben dagegen eine Zeichenzahl;
ein bloßer Austausch des Adapters ohne Änderung der Ausgabe wäre falsch.

Das ursprüngliche Echo-Muster ist für `csv.writer` **zulässig** und erzeugte
bereits gültiges CSV. Der historische Audit-Text war insoweit unpräzise:
`csv.writer` verlangt ein Objekt mit `write()`, nicht zwingend die gesamte
`StringIO`-Schnittstelle. Es wurde keine ausnutzbare Sicherheitslücke und
kein fehlerhaftes CSV durch `_CsvEcho` nachgewiesen. Dieser Fix erfüllt den
gewünschten Wechsel zum Standard-Textpuffer, ohne diese Wirkung zu überhöhen.

## Fix und Umfang

- `_CsvEcho` vollständig aus `trading/views.py` entfernt; `io` kommt aus
  der Standardbibliothek, ohne neue Runtime-Abhängigkeit.
- Puffer und Writer werden erst im synchronen `rows()`-Generator nach dem
  UTF-8-BOM angelegt. Ein `io.StringIO(newline="")` wird je Export wiederverwendet.
- Nach jedem `writerow()` wird ausschließlich `buffer.getvalue()` ausgegeben,
  nicht die zurückgegebene Zeichenzahl.
- Vor jeder Datenzeile setzen `seek(0)` und `truncate(0)` Position und Inhalt
  zurück. So enthält der Puffer jeweils nur eine CSV-Zeile; kürzere Zeilen
  übernehmen keine Reste ihrer Vorgänger.
- Der Context-Manager schließt den Puffer bei vollständiger Iteration,
  bei einer Ausnahme sowie bei `response.close()` während des Exports.
  Wird die Response nicht gelesen oder schon nach dem BOM geschlossen,
  entsteht überhaupt kein Textpuffer.
- `queryset.iterator(chunk_size=1000)`, Sortierung nach `timestamp`/`id`,
  zwölf CSV-Spalten, UTF-8-BOM, CRLF-Zeilentrennung, lokale ISO-Zeitstempel,
  Decimal-Darstellung und Download-Dateiname bleiben erhalten.
- `login_required`, `require_GET` und die Eigentümerprüfung vor Beginn des
  Exports bleiben unverändert. Keine Migration, keine neuen Settings und
  keine Änderung der PDF-, HTML- oder Backtest-Exporte.

## Regressionstests

Neu: `trading/tests/test_report_csv.py` mit **15 Tests**.

- Entfernung des Echo-Adapters und Laufzeitnachweis eines einzigen
  wiederverwendeten `io.StringIO` mit Standard-`write()`-Rückgabewert.
- Leerer Export: exakt BOM und Header, korrekter MIME-Typ, Attachment und
  `nosniff` über den echten Middleware-Stack.
- Alle Spalten, Decimal-Präzision, lokale Zeitzone und stabile Reihenfolge
  bei identischen Zeitstempeln.
- Unicode, Kommas, Anführungszeichen und eingebettete CR-/LF-Zeichen:
  Roundtrip ohne zusätzliche logische CSV-Zeilen oder Spalten.
- Lange vor kurzer Datenzeile: keine kumulierten Daten oder Restzeichen.
- **1.001 Datensätze:** keine Log-Abfrage vor dem ersten Datensatz,
  `iterator(chunk_size=1000)`, kein QuerySet-Ergebniscache und weiterhin
  genau eine Datenzeile je Generator-Schritt.
- Freigabe bei normalem Ende, Abbruch nach BOM/Header/Datenzeile und
  Datenbank-Iterationsfehler; keine Fehlerdetails als CSV-Daten.
- Fremde Konfigurationen, weitere Konfigurationen desselben Eigentümers,
  anonyme Zugriffe, unerlaubte HTTP-Methoden und unzuverlässige
  Dateinamensbestandteile bleiben von fremden Exporten bzw. Headern getrennt.

**Rot → grün:** Am unveränderten Ausgangsstand schlugen fünf Tests mit
sieben fehlgeschlagenen Assertions fehl: Echo-Klasse vorhanden,
Writer zu früh erzeugt und kein Standard-Textpuffer. Die übrigen zehn
Inhalts-/Zugriffstests bestanden bereits; das bisher korrekte Exportformat
wird damit ausdrücklich nicht als Sicherheitslücke dargestellt.
Mit dem Fix bestehen alle 15 Tests.

Zusätzliche Negativkontrollen entfernen jeweils einzeln `seek(0)`,
`truncate(0)` oder den schließenden Context-Manager: Die Suite erkennt alle
drei Regressionen. Anschließend wurde der unveränderte Fix erneut grün
geprüft. Die Mutationen sind nicht Bestandteil des ausgelieferten Codes.

## Lokale Validierung

**Python 3.11.2**, **Django 5.2.17**, SQLite-Testdatenbank,
**Ruff 0.16.6**, **ShellCheck 0.11.0**; Abhängigkeiten unverändert aus
`requirements.txt`. Lokale Test-Secrets wurden explizit im Environment
gesetzt, nicht aus produktiven Zugangsdaten übernommen.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test trading.tests.test_report_csv --noinput
python manage.py test --noinput
python -m pip check
./tests/run_tests.sh
./tests/distro_smoke_test.sh
```

- **242 Django-/Python-Tests** (15 neue + 227 bestehende) bestanden.
- **8/8 Shell-Testgruppen**, Ruff, ShellCheck, Systemcheck, Migrationsprüfung,
  `collectstatic` und `pip check` bestanden.
- Docker ist in der Prüfumgebung nicht verfügbar: Distro-Smoke-Tests und
  der optionale Compose-Laufzeittest wurden **übersprungen**, nicht als
  erfolgreiche Container-Integration gewertet. Statische Compose-Tests
  sind in den acht erfolgreichen Shell-Testgruppen enthalten.

## CI und Auslieferung

**Ausdrücklich genehmigte Ausnahme für dieses Release:** Der Auftraggeber
hat die PR-Erstellung mit erfolgreichen lokalen Prüfnachweisen und klar
benannten CI-Prüfgrenzen freigegeben.

Im Repository ist kein GitHub-Actions-Anwendungstestworkflow versioniert.
Die GitHub-Workflow-API führt nur die dynamische Integration **Dependency
Graph** (`dynamic/dependabot/update-graph`). Sie ersetzt keine
Anwendungstests. Ein erfolgreicher GitHub-Anwendungstest-CI-Lauf wird daher
nicht behauptet. Vor PR-Erstellung werden die vorhandenen Runs und Checks
für den veröffentlichten Session-Branch geprüft; ihr Stand wird im PR
angegeben. Es werden keine Checks umgangen oder als erfolgreich gesetzt.

Die Auslieferung bleibt auf `arena/01a080c5-t-bot-lokal`; der PR richtet sich
nach `tbot.local`.

## Prüfgrenzen und Upgrade

- **Kein ASGI-True-Streaming-Fix:** Der Generator bleibt wie im Auftrag
  synchron. Django 5.2 konsumiert einen synchronen Iterator bei asynchroner
  Auslieferung über `StreamingHttpResponse.__aiter__` vollständig, bevor
  es die erzeugten Teile weiterreicht. Der Wechsel zu `StringIO` ändert
  das nicht. Der zeilenweise Puffer ist auf Generator-Ebene geprüft;
  End-to-End-Speichergrenzen unter Daphne/ASGI oder hinter Proxies werden
  nicht zugesichert und benötigen einen separaten Streaming-Fix.
- Python-3.12-/Docker-/Daphne- und echte PostgreSQL-Integration wurden hier
  nicht ausgeführt; die Tests nutzen Python 3.11 und SQLite.
- Ein Fehler nach begonnenem CSV-Stream schließt den Puffer und wird
  weitergereicht. Der bereits gestartete HTTP-Download kann nicht mehr in
  eine neue 503-Antwort umgewandelt werden; es werden weder Fehlertexte
  angehängt noch Fehler als erfolgreicher vollständiger Export verschluckt.
- CSV-Quoting erhält die Dateistruktur. Die fachlichen Zellwerte bleiben
  unverändert; eine neue Behandlung von Tabellenkalkulationsformeln ist
  nicht Bestandteil dieses Refactorings.
- Keine Konfigurations- oder Datenbankänderung. Nach dem Deploy `/health/`
  auf **2.4.13** prüfen und bei Bedarf einen eigenen CSV-Export laden.
  Bestehende CSV-Importe müssen nicht angepasst werden.
