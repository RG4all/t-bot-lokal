# SEC-10 – Technische Exception-Details aus Benutzer-Meldungen entfernt

- **Finding:** `InformationDisclosureErrors` (Prompt 10 / Security-Audit §2.10)
- **Status:** **Fixed**
- **Release:** **2.4.10** · **Datum:** 2026-09-08
- **Einstufung:** LOW – Security
- **Geprüfter Ausgangsstand:** 2.4.9 / `7b7304785beb86a70dfa2c53f4a6b048dd8b9fee`
- **Fix-Commit:** [`703fd81` – `fix(security): remove exception details from user messages`](https://github.com/RG4all/t-bot-lokal/commit/703fd8170904bb2e4cee08478196ea8a31e154da)

## Befund und Root Cause

Interne Exceptions wurden als bereits freigegebene Benutzer-Meldungen behandelt. Dadurch konnten technische Diagnosen in Flash-Cookies/-Meldungen, JSON-Fehlern und Reportantworten erscheinen. Die Prüfung der direkt verbundenen Ausgabewege fand außerdem Teilfehler-Dictionaries, Bot-/Worker-Status und gespeicherte Fehlertexte. Das per Browser erreichbare Diagnose-Log benötigte eine zusätzliche Berechtigungsgrenze: Nur Flash-Texte zu ändern, hätte denselben technischen Inhalt dort weiter offengelegt.

Es wird kein neuer Befund zu fremden Nutzerobjekten behauptet: Die bestehende Eigentümerprüfung war vorhanden und bleibt erhalten. Geprüft wurde der Stand 2.4.9; dies ist kein vollständiges neues Audit sämtlicher historischen Versionen oder aller übrigen Audit-Findings.

## Fix und notwendiger Umfang

| Komponente | Änderung |
|---|---|
| `trading/views.py` | Feste Bot-Start-, Verkaufs-, Scanner- und Reportmeldungen; keine Exception-Texte in Flash-/HTTP-/JSON-Ausgaben. Bot-Status maskiert `last_error`, ohne den internen Zustand zu verändern. Report-Boundaries behandeln auch Renderfehler. |
| `trading/views.py` – `_record_view_error` | Zusätzliche Diagnose-Persistenz mit Savepoint und DB-Fallback; der ursprüngliche Fehler wird vorher mit `logger.exception` geloggt. Ein Fehler beim Schreiben wird separat geloggt und verdrängt nicht die sichere Antwort. |
| `trading/forms.py` | Technische Fehler bei Exchange-Prüfungen werden geloggt und durch einen generischen Formularhinweis ersetzt. Fachliche `SymbolValidationError`-Hinweise bestehen nur aus Exchange-Bezeichnung und den betroffenen Eingabesymbolen und bleiben nutzbar. |
| `trading/market_scanner.py` | Pro Exchange abgefangene Fehler werden mit Traceback geloggt; die direkt ausgelieferten Teilergebnisse enthalten generische Fehlertexte. |
| `trading/trading_bot.py` | Teilfehler beim Kill-Switch werden vor ihrer sicheren Serialisierung mit Traceback geloggt. Erfolgreiche Verkäufe und verbleibende Positionen bleiben unverändert. |
| `trading/worker_status.py` | Worker-Verbindungsfehler werden geloggt, aber nur generisch an den Status-Endpunkt gegeben. |
| `trading/templates/trading/error_log.html` | Technische Log-Meldung, Quelle, Exception-Typ und Details benötigen `is_staff`, zusätzlich zur Eigentümerprüfung in der View. Normale Konten sehen eine generische Meldung und Referenznummer. Auch alte Einträge sind geschützt. |
| `trading/templates/trading/backtesting_form.html` | Bereits gespeicherte Task-Diagnosen werden nicht mehr als Benutzer-Meldung gerendert. Statusabhängige generische Hinweise ersetzen sie, ohne gespeicherte Daten zu löschen. |

### Verhalten und Berechtigungen

- Flash-Meldung beim fehlgeschlagenen Bot-Start: **„Bot konnte nicht gestartet werden. Siehe Fehler-Log für Details.“** Der konfigurierte Bot wird nicht als erfolgreich gestartet ausgegeben.
- HTTP-Statuscodes (400 für Eingabefehler, 500 für fehlgeschlagene Verkaufsaktionen, 503 bei nicht verfügbaren Diensten/Reports), JSON-Struktur, erfolgreiche Antworten und Redirect-Ziele bleiben erhalten. Bisher unkontrollierte PDF-Renderfehler werden ebenfalls mit 503 beantwortet.
- Normale Konten können eigene Fehler-Log-Einträge weiterhin filtern und erledigen. Zur Diagnose nennen sie dem Betreiber Referenz und Konfiguration. Staff-Rechte sind kein normaler Nutzerzugang und dürfen nicht vergeben werden, um diese Grenze zu umgehen.
- Staff allein erlaubt **keinen** Zugriff auf fremde Konfigurationen über `/errors/`. Betreiber verwenden für weitergehende Diagnose die geschützten Server-Logs bzw. die bestehende Django-Administration mit den erforderlichen Berechtigungen.
- Keine neue Runtime-Abhängigkeit, keine Migration, keine geänderte Bot-/Backtest-Architektur und keine neuen Umgebungsvariablen. `VERSION` ist die zentrale Versionsquelle; `pyproject.toml` enthält keine Paketversion.

## Regressionstests

Neu: `trading/tests/test_error_disclosure.py` mit **25 Tests**. Der bestehende Log-Test in `test_core.py` prüft die Eigentümergrenze weiterhin und erwartet jetzt für normale Konten eine Referenz statt technischer Inhalte.

- Flash-Message-Cookies werden dekodiert geprüft: Signatur/Kompression ist keine Verschlüsselung.
- Bot-Validierung und Bot-Start mit unterschiedlichen Exception-Typen, in DEBUG und Produktion; generische gerenderte Meldung, kein Erfolgssignal, Log mit Konfigurations-ID und Traceback.
- Exception-Ketten, synthetische technische/aktive Inhalte und paralleler Ausfall der Diagnose-Persistenz.
- JSON-Fehler für Symbole, Scanner, Verkaufsaktionen und Worker; Scanner-/Kill-Switch-Teilausfälle und Bot-Status.
- Alte Task-/Log-Einträge, normale/Staff-Konten und fremde Eigentümer; unveränderte Login-, POST-only- und CSRF-Grenzen.
- PDF-Bibliotheksimport, native Importfehler und Renderfehler; beide Reportformate und gemeinsam genutzter PDF-Pfad für Backtests.
- Statischer AST-Guard gegen direkte Exception-Weitergabe an Messages-/HTTP-/JSON-Payloads, ergänzend zu den Laufzeittests.

**Rot → grün:** Die neue Suite wurde vor dem Fix ausgeführt und zusätzlich gegen eine isolierte Kopie des Ausgangscommits geprüft. Dort werden die Offenlegungen und unkontrollierten PDF-Fehler reproduziert. Mit dem Fix sind alle 25 Tests grün; die vollständige Suite besteht mit **210 Tests** (185 bestehende + 25 neue).

## SEC-05-Nachprüfung

**Status: Fixed seit 2.4.5**, erneut in **2.4.10** geprüft. Ursprünglicher Fix: [PR #13](https://github.com/RG4all/t-bot-lokal/pull/13).

Alle **11 Tests** in `trading/tests/test_csrf_cookie.py` bestehen unverändert: explizite, DEBUG-/Render-unabhängige Einstellung, ausgelieferte HttpOnly-/Secure-Flags, Login mit maskiertem Formular-Token sowie fehlende/fremde Tokens, Cookies und nicht vertrauenswürdige Origins. Eine zusätzliche reine Testprozess-Mutation mit `CSRF_COOKIE_HTTPONLY=False` lässt die echte Cookie-Prüfung erwartungsgemäß scheitern; die Negativkontrolle erkennt die Regression.

Die Einstellung selbst wurde nicht verändert. HttpOnly schützt nur den direkten Cookie-Zugriff; das DOM-Token bleibt für Skripte derselben Origin zugänglich. Es ersetzt weder CSP noch Escaping oder CSRF-/Origin-Prüfungen.

## Lokale Validierung

Python **3.11.2**, Django **5.2.17**, Ruff **0.16.6**, ShellCheck **0.11.0**:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
# Ausschließlich isolierte Test-Secrets setzen, keine Deployment-Secrets verwenden.
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
bash tests/run_tests.sh
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test --noinput
python manage.py test trading.tests.test_error_disclosure trading.tests.test_csrf_cookie --noinput
python -m pip check
pip-audit -r requirements.txt
```

- **210 Django-/Python-Tests** und **7/7 Shell-Testgruppen** bestanden; Ruff und ShellCheck ohne Befund.
- Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden. `pip-audit -r requirements.txt`: keine bekannten Schwachstellen zum Prüfzeitpunkt.
- `check --deploy --fail-level WARNING`, Migrationen auf einer frischen isolierten SQLite-DB und `collectstatic` mit Produktions-/Render-Settings bestanden. HTTP-Smoke-Test: `/health/` meldet 2.4.10; `/gate/`, HttpOnly-/Secure-Cookie sowie nosniff/Permissions-Policy geprüft. Zusätzlich 77 gezielte Security-Tests grün.

## CI und Auslieferung

**Ausdrücklich genehmigte Ausnahme:** Der Auftraggeber hat nach dem Berechtigungsfehler die Auslieferung **ohne neuen GitHub-Testworkflow, mit erfolgreichen lokalen Prüfnachweisen** freigegeben. Ein erfolgreicher GitHub-Anwendungstest-CI-Lauf wird nicht behauptet.

Ein Workflow mit Python 3.12, Django-/Shell-Tests und nativem PDF-Smoke-Test war vorbereitet. GitHub verweigerte den Push, weil die Arena-GitHub-App keine `workflows`-Berechtigung besitzt. Der noch unveröffentlichte Workflow wurde nach ausdrücklicher Freigabe aus dem Release entfernt. Die zuvor allein vorhandene Dependency-Graph-Integration ist kein Anwendungstestnachweis.

Der Fix bleibt auf dem Session-Branch `arena/01a08029-t-bot-lokal`; der PR richtet sich nach `tbot.local`. Lokale Testergebnisse und verbleibende Laufzeitgrenzen werden im PR ausdrücklich ausgewiesen. Für künftige automatisierte Anwendungstests muss zuerst die entsprechende GitHub-App-Berechtigung bereitgestellt werden.

### Prüfgrenzen und Upgrade

- Lokal fehlen Docker und die nativen PDF-Bibliotheken; deren Installation sowie der Download einer Python-3.12-Runtime waren wegen Netzwerk-/TLS-Beschränkungen nicht möglich. PDF-Fehlerpfade sind über kontrollierte Bibliotheks-/Renderer-Mocks getestet, nicht durch einen lokalen nativen PDF- oder Containerlauf.
- HTTP-/Middleware-Tests sind kein Browser-End-to-End-Test und keine Prüfung des tatsächlich vorgeschalteten Reverse-Proxys. Produktion muss weiterhin mit `DEBUG=False` und privaten Secrets betrieben werden; Framework-Debugseiten gehören nicht in öffentlich erreichbare Deployments.
- Technische Diagnosen bleiben bewusst in geschützten Server-Logs und berechtigten Diagnoseansichten. Logzugriff und Staff-/Admin-Rechte restriktiv vergeben; diese Daten nicht veröffentlichen.
- Nach dem Deploy `/health/` auf **2.4.10** und die generische Meldungs-/Log-Anzeige eines normalen Kontos sowie die autorisierte Staff-Ansicht prüfen. Keine Datenmigration erforderlich; ältere gespeicherte Fehler werden bereits an der Ausgabestelle geschützt.
