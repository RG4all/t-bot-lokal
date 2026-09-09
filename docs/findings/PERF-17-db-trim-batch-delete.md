# PERF-17 – DB-Trim Batch-Delete

- **Finding:** `DbTrimBatchDelete` (Prompt 17 / Security-Audit §4.2)
- **Status:** **Fixed**
- **Release:** **2.4.14** · **Datum:** 2026-09-08
- **Einstufung:** MEDIUM – Performance (keine Sicherheitslücke)
- **Geprüfter Ausgangsstand:** 2.4.13 / `e84456f0b7dbd96029e7e9012572a652459b59d2`
- **Fix-Commit:** [`27333cf` – `perf(bot): implement batch delete for db_trim_datalog`](https://github.com/RG4all/t-bot-lokal/commit/27333cf)

## Befund und Root Cause

`db_trim_datalog()` in `trading/trading_bot.py` (historische Zeilen 176–183)
löscht alte `DataLog`-Einträge einer Konfiguration/Symbol-Kombination, sobald
der Bot beim periodischen Aufräumen die Grenze
`MAX_DATA_LOGS_PER_SYMBOL` (20.000) überschreitet:

```python
if cutoff_id is not None:
    queryset.filter(id__lte=cutoff_id).delete()
```

Der Aufrufer (`TradingBot._persist_data_log`, Aufruf nach jeweils
`DATA_LOG_CLEANUP_EVERY` gespeicherten Zeilen) übergibt ausschließlich
`settings.MAX_DATA_LOGS_PER_SYMBOL`. Überschreitet eine Symbol-Historie die
Grenze deutlich, entfernt **ein einzelnes DELETE-Statement** sämtliche
Alt-Einträge auf einmal – bei 20.000+ Zeilen eine lang laufende
Schreibtransaktion, die PostgreSQL-Zeilen- und Tabellensperren über die
gesamte Löschdauer hält und andere Bots/Requests (Web, weitere Konfigurationen)
blockieren kann. Ein Datenverlust oder eine korrupte Historie entsteht nicht;
der Befund ist eine Performance-/Verfügbarkeits-Schwäche (Lock-Dauer), kein
Sicherheitsproblem.

## Fix und Umfang

`db_trim_datalog()` löscht nun in **1000er-Schritten**, jede Charge in einer
eigenen, sofort committeten Transaktion:

1. Die Cutoff-ID (älteste zu behaltende Zeile) wird wie bisher bestimmt;
   ohne Überschreitung kehrt die Funktion sofort zurück.
2. Je Durchgang werden nur die nächsten maximal 1000 betroffenen IDs gelesen
   (`ORDER BY id LIMIT 1000` über den Primärschlüssel).
3. Genau diese IDs werden in einer eigenen `transaction.atomic()`-Charge über
   `id__in` gelöscht. Nach jeder Charge endet die Transaktion, die Sperren
   werden freigegeben.
4. Liefert die ID-Abfrage keine Zeile mehr, bricht die Schleife ab
   (Abbruch bei leeren Deletes, kein Endlos-Loop).

**Abweichung vom historischen Lösungsvorschlag (Prompt/Audit §4.2):** Die dort
skizzierte Variante `queryset[:1000].delete()` ist im gepinnten Django 5.2.17
**nicht ausführbar** – Django lehnt `LIMIT`/`OFFSET` direkt auf `.delete()`
mit `TypeError: Cannot use 'limit' or 'offset' with delete().` ab. Die
umgesetzte ID-Chargen-Variante erreicht dasselbe Ziel (höchstens 1000 Zeilen
je DELETE-Statement, kurze Transaktionen) mit der unterstützten ORM-API.

Zusätzlich behandelt die Funktion `max_rows < 1` als sicheres No-op: Ein
negativer Wert würde beim Slicing `[max_rows:max_rows + 1]` eine
`ValueError`-Exception auslösen. Die Aufrufer verwenden ausschließlich die
per `env_int(..., minimum=1_000)` abgesicherte Einstellung; der Guard
schützt zukünftige Aufrufpfade.

Unverändert bleiben: Cutoff-Logik (älteste `max_rows` Zeilen bleiben
erhalten), Scoping auf `configuration_id` + `symbol`, der
`@sync_to_async`-/`@db_safe`-Stack mit dem Bot-DB-Executor, Aufrufrhythmus
und alle übrigen Datenbankpfade. Keine neue Abhängigkeit, keine Migration,
keine Settings-Änderung.

## Regressionstests

Neu: `trading/tests/test_db_trim_datalog.py` mit **7 Tests**
(`TransactionTestCase`, weil `db_trim_datalog` über den Executor-Thread in
eigenen Transaktionen schreibt).

- Behaltenslogik: Nach dem Trim bleiben exakt die neuesten `max_rows` Zeilen
  der betroffenen Kombination übrig.
- Grenzfälle: exakt `max_rows` Zeilen bzw. weniger Zeilen als `max_rows`
  lösen keine Löschung aus.
- Scoping: Nur die überlaufende Konfiguration/Symbol-Kombination wird
  gekürzt; andere Symbole derselben Konfiguration und andere Konfigurationen
  bleiben unberührt (Schutz vor Fehl-Löschungen durch verwechselte IDs).
- **Batch-Nachweis:** Bei 2.500 zu löschenden Zeilen protokolliert ein
  Transaktions-Probe (ersetzt `transaction.atomic` im Bot-Modul und misst
  nach jeder äußersten Transaktionsgrenze die Rest-Zeilen) die Chargengrößen
  `[1000, 1000, 500]` – kein einzelner DELETE über alle Alt-Einträge, keine
  Charge größer als 1000.
- Abbruch bei leeren Deletes: Ein zweiter Trim-Aufruf ohne Alt-Einträge
  führt zu keiner weiteren Transaktion und keiner Exception.
- Negatives `max_rows` löst keine Exception mehr aus (sicheres No-op).

**Rot → grün:** Am Ausgangsstand 2.4.13 schlugen zwei Tests fehl: Der
Batch-Nachweis fand genau eine Transaktion über **alle 2.500 Zeilen**
(`[2500] != [1000, 1000, 500]`), und `max_rows=-5` endete mit
`ValueError: Negative indexing is not supported.` Mit dem Fix bestehen alle
7 Tests; die fünf Verhaltenstests (Behaltenslogik, Scoping, Grenzfälle)
waren am Ausgangsstand bereits grün – der Alt-Code löschte korrekt, nur zu
grob granular.

## SEC-05-Nachprüfung

Wie in den Vorgänger-Releases nachgeprüft: `CSRF_COOKIE_HTTPONLY = True`
bleibt DEBUG-/Render-unabhängig gesetzt. Alle **11 CSRF-Cookie-Tests** in
`trading/tests/test_csrf_cookie.py` bestehen unverändert; SEC-05 bleibt
**Fixed** (zuletzt nachgeprüft in 2.4.10, erneut in 2.4.14).

## Lokale Validierung

**Python 3.11.2**, **Django 5.2.17**, SQLite-Testdatenbank,
**Ruff**, **ShellCheck 0.11.0**; Abhängigkeiten unverändert aus
`requirements.txt`. Lokale Test-Secrets wurden explizit im Environment
gesetzt, nicht aus produktiven Zugangsdaten übernommen.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test trading.tests.test_db_trim_datalog --noinput
python manage.py test trading.tests.test_csrf_cookie --noinput   # SEC-05
python manage.py test --noinput
python -m pip check
./tests/run_tests.sh
```

- **249 Django-/Python-Tests** (7 neue + 242 bestehende) bestanden.
- **8/8 Shell-Testgruppen**, Ruff, ShellCheck, Systemcheck, Migrationsprüfung,
  `collectstatic` und `pip check` bestanden.
- Docker ist in der Prüfumgebung nicht verfügbar: Distro-Smoke-Tests und
  der optionale Compose-Laufzeittest wurden **übersprungen**, nicht als
  erfolgreiche Container-Integration gewertet.

## CI und Auslieferung

**Ausdrücklich genehmigte Ausnahme für dieses Release:** Der Auftraggeber
hat die PR-Erstellung mit erfolgreichen lokalen Prüfnachweisen und klar
benannten CI-Prüfgrenzen freigegeben.

Im Repository ist kein GitHub-Actions-Anwendungstestworkflow versioniert.
Die GitHub-Workflow-API führt nur die dynamische Integration **Dependency
Graph** (`dynamic/dependabot/update-graph`) aus; sie ersetzt keine
Anwendungstests. Ein erfolgreicher GitHub-Anwendungstest-CI-Lauf wird daher
nicht behauptet. Vor PR-Erstellung werden die vorhandenen Runs und Checks
für den veröffentlichten Session-Branch geprüft; ihr Stand wird im PR
angegeben. Es werden keine Checks umgangen oder als erfolgreich gesetzt.

Die Auslieferung bleibt auf `arena/01a08196-t-bot-lokal`; der PR richtet
sich nach `tbot.local`.

## Prüfgrenzen und Upgrade

- **Keine echte PostgreSQL-Lock-Messung:** Die Tests laufen auf SQLite.
  Nachgewiesen ist die Batch-Struktur (Chargen ≤ 1000 Zeilen, eigene
  Transaktion je Charge, Abbruch bei leeren Deletes). Dass dadurch die
  Lock-Dauer je DELETE-Statement auf PostgreSQL kurz bleibt, folgt aus der
  begrenzten Statement-Größe; eine Lastmessung gegen echtes PostgreSQL
  wurde hier nicht ausgeführt.
- **Kein Verhaltensunterschied für Aufrufer:** `db_trim_datalog` behält
  Signatur, Semantik (älteste Zeilen über der Grenze werden entfernt) und
  Fehlerbehandlung (`@db_safe(suppress=True)`). Der Aufrufrhythmus des Bots
  ändert sich nicht; eine Charge kann bei parallelen Schreibzugriffen
  geringfügig weniger als 1000 Zeilen umfassen, die Schleife endet trotzdem
  deterministisch, weil neue Zeilen immer IDs oberhalb der Cutoff-ID erhalten.
- Keine Konfigurations- oder Datenbankänderung. Nach dem Deploy `/health/`
  auf **2.4.14** prüfen. Datenbank-Historien werden erst beim nächsten
  regulären Trim-Zyklus (nach `DATA_LOG_CLEANUP_EVERY` neuen Zeilen je
  Symbol) batchweise gekürzt; ein manueller Eingriff ist nicht nötig.
