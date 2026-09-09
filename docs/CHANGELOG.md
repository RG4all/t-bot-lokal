# Changelog

Alle relevanten Änderungen dieses Projekts werden hier dokumentiert. Das Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [2.5.0] – 2026-09-09

Bugfix-, Robustheits- und Wartbarkeits-Release; umgesetzt aus dem [umfassenden Code-Review vom 2026-09-09](security/CODE_REVIEW_2026-09-09.md). Commit-Bereich ab Review-Grundlage `727d3ee`; jeder Befund (K1–K4, W1–W10, O1–O9) ist ein eigener Merge-fähiger Commit mit Regressionstest, rot am Ausgangsstand. 370 Django-/Python-Tests und die Shell-Suite sind grün; neu im Gate: gepinntes Ruff und mypy.

### Added

- **Readiness-Endpunkt `/readyz/`:** prüft die Datenbankverbindung und antwortet ohne sie mit `503` und `Retry-After: 5`, ohne Exception-Details nach außen; vom Passphrase-Gate ausgenommen. Damit unterscheidet die App erstmals „Prozess lebt" (`/health/`) von „Traffic bereit". [Review O6]
- **Pflegekommando `prune_history`:** begrenzt TradingLogs je Konfiguration und ErrorLogs global auf die Settings-Grenzen (`MAX_TRADING_LOGS_PER_CONFIG`, `MAX_ERROR_LOGS`, `ERROR_LOG_RETENTION_DAYS`, alle per Env überschreibbar), zusätzlich Altersfrist für gelöschte/Info-Fehler; ID-basierte 1000er-Löschbatches wie der Bot-Trim, `--dry-run` inklusive. [Review O5]
- **Mypy im Quality-Gate:** der in `pyproject.toml` dokumentierte Scope `trading/views.py` läuft jetzt als CI-Step mit exakt den lokal referenzierten Pins (`mypy==1.18.2`, `django-stubs==5.2.7`). [Review O2]

### Changed

- **Portfolio-Snapshots gecacht:** `_portfolio_snapshot` (zwei Queries je Symbol) liegt 2 s prozesslokal in einer gedeckelten LRU; Dashboard-Polling über `info_api`/`logs_api` teilt sich denselben Stand, `logs_api` leitet offene Symbole aus dem Snapshot ab statt pro Symbol zu fragen. Zustandsändernde POSTs (Verkauf, Kill-Switch, Reset, Edit, Löschung) invalidieren gezielt, das UI bleibt sofort frisch. [Review W2]
- **Analyse-Datenzugriff:** `analyse_view` lädt statt bis zu 20.000 kompletter ORM-Objekte nur noch `(timestamp, price)`-Paare via `values_list` (Limit als Konstante `_MAX_ANALYSIS_ROWS`). [Review W3]
- **Hilfe-Cache vereinfacht:** der zweite, von Hand gepflegte Cache-Layer um `_render_manual` samt Monkey-Patch von `cache_clear` ist entfallen; `@lru_cache` ist die einzige Schicht und die native `cache_clear()`/`cache_info()`-API gilt uneingeschränkt. [Review O3]
- **Bot-Start außerhalb des Manager-Locks:** Konstruktion (State-Recovery, Exchange-Setup) blockiert Status-Polls nicht mehr; ein `starting`-Sentinel verhindert Doppelstarts und `restart_bot` übernimmt über `_ensure_started` den Start, falls ein paralleler Builder das Sentinel hielt. [Review W5]
- **CI-Ruff gepinnt:** `ruff==0.16.6` statt `pip install ruff` – Gate-Ergebnisse hängen nicht mehr am Veröffentlichungsdatum des Linters. [Review O1]

### Removed

- **Totcode:** `market_data.validate_symbols_async` (gab immer `[]` zurück), `resource_optimizer.ResourceSnapshotCache` (nie instanziiert) und der einmalige Migrationsbefehl `clear_api_keys` sind gelöscht. [Review O7]

### Fixed

- **K1 – Deaktivierte Bots handelten weiter:** `main_loop` wertet `is_running=False` nach dem Konfigurations-Refresh selbst aus (maximal ein Zyklus bis zum sauberen Ende), `restart_bot` prüft die Fahne unmittelbar vorher gegen die DB. [Nachweis BUG-22](findings/BUG-22-selfstop-deactivated-bots.md)
- **K2 – State-Restart war fail-open:** kann die Trade-Historie nicht zuverlässig geladen werden (DB-Ausfall während des Starts), bricht der Bot sichtbar ab statt mit leerem Portfolio doppelt zu investieren. [Nachweis BUG-23](findings/BUG-23-restore-state-fail-closed.md)
- **K3 – Unbegrenztes Wachstum der Scanner-Caches:** `_CACHE` und `_MARKET_CAP_CACHE` sind gedeckelte LRUs (32/16 Einträge) statt TTL-only-Dicts; der OOM-Hebel gegen den langlebigen Webprozess ist beseitigt. [Nachweis PERF-24](findings/PERF-24-scanner-cache-lru.md)
- **K4 – Undeklarierte Laufzeitabhängigkeit:** `aiohttp` ist jetzt direkt in `requirements.txt` gepinnt (`==3.14.3`) und hängt nicht mehr allein an ccxts Transitivität. [Nachweis CODE-25](findings/CODE-25-requirements-aiohttp-pin.md)
- **Ungültige `config_id` lieferte 500er:** JSON-APIs antworten auf nicht-numerische Werte mit `400` und fester Meldung, die Dashboard-URL mit `404`; fehlende Parameter behalten ihr bisheriges Verhalten. Log- und ErrorLog-Rauschen durch reine Eingabefehler entfällt. [Review W1; Troubleshooting im FAQ]
- **DB-Ausfall blockierte alle Bots bis ~90 s:** `db_safe(suppress=True)`-Schreibpfade laufen nicht mehr in die koordinierte Reconnect-Schleife, sondern öffnen nur den Circuit und übergeben an die RAM-Puffer; Recovery bleibt beim einen Konfigurationspfad. [Review W4]
- **`open_symbols`-Härtung:** die Positions-Schlüssel werden vor dem Sortieren explizit kopiert statt über das live von der Bot-Loop mutierte Dict zu iterieren (Semantik unabhängig von Interpreter-Details). [Review W6]
- **Beat-Scheduling blockierte Backtest-Slots:** `schedule_backtests` läuft in der eigenen Queue `scheduling`, der Worker abonniert `backtest,scheduling` (Entrypoint und Render-Beispiel); ein hängender Schedule-Laufruf verhungert keine Backtests mehr. [Review W7]
- **Reports ohne Obergrenze:** PDF-/HTML-Reports arbeiten auf den neuesten 10.000 Trades (`_MAX_REPORT_ROWS`) und weisen Kappungen mit sichtbarem Hinweis samt Verweis auf den vollständigen CSV-Export aus. [Review W8]
- **Irreführende Konfigurationsfelder:** `leverage` validiert 1–10 statt `>= 0`, `trade_direction` ist auf `long`/`short` beschränkt, beide Hilfetexte benennen ehrlich, dass die Engine die Werte nur aufzeichnet; `has_live_credentials` heißt im Formular „Live-API-Schlüssel konfiguriert" (Migration `0015`). [Review W9]
- **`?refresh=1` ohne Bremse:** erzwungene Scanner-Refreshes sind je Börse und Marktart auf 20 s gedrosselt; innerhalb des Fensters bedient der letzte Scan – ohne jeden Cache-Info wird dagegen immer gescannt. [Review W10]
- **Countdown auf der Wallclock:** die Startverzögerung nutzt eine monotone Deadline; NTP-Sprünge und suspend/resume verkürzen oder verlängern sie nicht mehr. `started_at` bleibt epoch-basiert fürs Display. [Review O4]
- **Manueller Verkauf meldete Timeout als Fehlschlag:** nach 10 s Wartezeit (jetzt Konstante `MANUAL_SELL_TIMEOUT_SECONDS`) bleibt der Auftrag in der Bot-Queue; API und Frontend unterscheiden `ok`/`delayed`, ein erfolgreicher Verkauf wurde als Fehlschlag gemeldet und der ErrorLog mit falschem Critical gefüllt. [Review O8]
- **Kombinationszähler am Randwert:** `_combination_count` folgt exakt der Decimal-Rasteriteration des Backtest-Tasks statt eines Float-Nachbaus mit Epsilon (0.0→0.3 bei Schritt 0.1 zählte 3 statt 4 und unterschritt das Hard-Limit). [Review O9]

### Security

- Die 500er- und Timeout-Pfade oben sind zugleich Information-Disclosure- und Self-DoS-Hebel gewesen: ORM-Diagnosen landeten im Log, billige Retry-/Refresh-Klicks trafen die eigenen Rate-Limits. Feste Fehlermeldungen, Drosseln und gedeckelte Caches schließen das; `/readyz/` gibt ausschließlich den Status nach außen.


## [2.4.20] – 2026-09-09

### Dokumentation und Wartbarkeit (Repo-Restrukturierung)

- **Struktur:** `docs/` ist nach Dokumenttyp getrennt: `manual/` (App-Hilfe, Backtesting-Kapitel), `operations/` (lokales Setup, FAQ, Tailscale, Caddy), `findings/` (13 Audit-Tickets plus Pflichtvorlage `TEMPLATE.md`), `security/` (aktuelles Review), `adr/` (neu: ADR-0001 aus der Backtesting-Studie) und `archive/` (überholte Dokumente mit Lesezugriff-Banner). Die Umzugsphase-Commits sind reine `git mv`-Renames.
- **Duplikate:** `docs/README.md` war ein zweites Produkt-README (≈41 % Überlappung); einzigartige Inhalte (Host-/manuelle Installation, Passphrase-Matrix, Render-Blueprint + Free-Einschränkungen, QA-Befehle) wanderten nach `docs/operations/LOCAL_DEVELOPMENT.md`. `docs/README.md` ist jetzt der Index. Das Root-README trägt keine hartcodierte Versionszahl mehr.
- **Links:** ein kaputter Anker gefixt (`PERF-21#ci-und-auslieferung` → `#auslieferung-und-pruefgrenzen`); 37 GitHub-only-Deep-Links auf CHANGELOG-Anker zu reinen Dateilinks zurückgebaut; 162 relative Links an die neue Struktur angepasst; zuvor verwaiste Dokumente (Tailscale, Caddy, Peer-Reviews) sind jetzt im Index bzw. Archiv erreichbar.
- **Code-Referenzen:** `trading/views.py` rendert das Handbuch von `docs/manual/MANUAL.md` (alte Pfade bleiben als Fallback); Kommentare in `scripts/setup_local.sh`, `tests/test_compose_security.sh` und sechs Test-Docstrings auf Archiv-/Findingspfade nachgezogen.
- **Namensregeln:** `SEC-06-rule-lifecycle-authz.md` → `SEC-06-content-type-nosniff.md` (Dateiname entspricht dem im Dokument erklärten Befund).
- **Werkzeuge:** neuer Doku-Wächter `scripts/check_docs.py` (Links, Anker, Orphans, Namenskonventionen, Duplikat-/Versions-Hinweise), ab diesem Release aktiver CI-Qualitäts-Gate `.github/workflows/quality.yml` (nähere Angaben im Abschnitt „CI-Aktivierung“ unten), `CONTRIBUTING.md` (Struktur-/Duplikat-/Namensregeln, Audit-Zyklen) und `patches/` als Peer-Review-Patch-Eingang (`inbox/` → `accepted/`/`done/`, Review-Vorlage).
- **Konfigurations-Template:** `config.template` heißt jetzt `config.template.env` (Endung = Format); `Dockerfile`-COPY, `.gitignore`-Negation und beide Shell-Prüfungen (`test_compose_security.sh`, `test_config_generation.sh`) nachgezogen; historische CHANGELOG-/Fundstellen-Texte bleiben bewusst im Wortlaut.
- **Changelog-Pflege:** Die Einleitung („Alle relevanten Änderungen …“) steht wieder direkt unter der `# Changelog`-Überschrift; der bislang als `[Unreleased]` geführte Restrukturierungsstand wird mit diesem Release ausgeliefert.

### CI-Aktivierung und Release-Versionierung

- **CI-Workflow aktiv:** Die GitHub-App besitzt seit diesem Release die `workflows`-Push-Berechtigung; die versionierte CI-Definition liegt jetzt als `.github/workflows/quality.yml` und läuft bei **jedem Push und Pull Request** automatisch: Doku-Wächter, Django-Systemcheck, Migrations- und Statik-Prüfung, 330 Django-/Python-Tests, Ruff, 8/8 Shell-Testgruppen, ShellCheck und `pip check`. Die bis dahin gepflegte Referenzkopie `ci/quality.yml` samt `ci/README.md` (Zwei-Kopien-Pflege „bis die App-Rechte erweitert werden“) ist damit aufgelöst – die aktive Workflow-Datei ist die einzige Quelle; `CONTRIBUTING.md` und `docs/README.md` zeigen darauf.
- **Erster realer CI-Lauf (Befund und Fix):** Der erste automatische Lauf deckte einen zuvor unsichtbaren ShellCheck-Befund auf: Die per apt installierte ShellCheck-Version (0.9.0 auf Ubuntu 24.04) meldet in `install.sh` `SC2119`/`SC2120` (`detect_distro` deklariert den optionalen Parameter nur für Tests, der Hauptablauf rief ohne Argument auf); die lokal dokumentierte ShellCheck 0.11.0 meldet das Muster nicht mehr. Fix: Der Hauptablauf übergibt den Standardpfad jetzt explizit (`detect_distro /etc/os-release`) – damit ist das Skript unter ShellCheck 0.9.0 **und** 0.11.0 befundfrei. Zusätzlich pinnt der Workflow ShellCheck über `shellcheck-py==0.11.0.1` (kein apt mehr): deterministische Prüfversion, konsistent mit den bisherigen Release-Nachweisen („ShellCheck 0.11.0“), und kein `apt-get`-Schritt im Lauf.
- **Release-Versionierung:** Zentrale `VERSION` auf **2.4.20** erhöht; `/health/`, Fußzeile und `/help/` melden ab diesem Release **2.4.20**. Der Changelog-Absatz „CI und Auslieferung“ der älteren Findings-Dokumente (kein versionierter GitHub-Actions-Workflow bis 2.4.19) bleibt als releasezeitlicher Nachweis bewusst im Wortlaut erhalten.
- Keine Änderung an Django-Laufzeit-Code, Settings, Modellen oder Migrationen; einzige Skriptänderung ist der explizite Standardpfad in `install.sh` (eine Zeile, siehe oben). `/help/`-Auslieferung, Tests und Docker-Stack unverändert gültig (330 Django-/Python-Tests und 8/8 Shell-Tests grün vor und nach der Umstellung). Bestehende Konfigurationen, laufende Bots und gespeicherte Backtests bleiben unverändert gültig. Auslieferung über [PR #28](https://github.com/RG4all/t-bot-lokal/pull/28) (Restrukturierung) und [PR #29](https://github.com/RG4all/t-bot-lokal/pull/29) (Versionierung + CI-Aktivierung).

## [2.4.19] – 2026-09-08

### Dokumentation und Konsistenz

- **Reines Dokumentations-Release:** Der Code-Stand ist unverändert zu 2.4.18 (330 Django-/Python-Tests). Zentrale `VERSION` auf **2.4.19** erhöht; Root-/docs-README, Handbuch, lokale Versionsangabe und der Konfigurationsstand aktualisiert. `/health/` meldet ab diesem Release **2.4.19**.
- **Audit-Status in `SECURITY_AUDIT.md` nachgezogen:** Die Abschnitte §2.1 (Rate-Limiting), §2.2 (ALLOWED_HOSTS-Wildcard) und §2.3 (CSP) waren als offene Befunde formuliert, obwohl die Umsetzungen bereits in **2.4.1–2.4.3** ausgeliefert wurden – die Audit-Datei entstand am selben Tag wie diese Fixes und wurde seither nur für jüngere Releases gepflegt. Sie tragen jetzt denselben Statusaufbau wie die übrigen Abschnitte („Fixed in X.Y.Z“ mit Umgesetzt-/Nachweis-Block, historischer Befund bleibt nachvollziehbar). Ebenfalls ergänzt: **§4.6 „Fixed in 2.4.18“** mit Verweis auf [PERF-21](findings/PERF-21-info-api-db-aggregation.md) und [PR #26](https://github.com/RG4all/t-bot-lokal/pull/26) – PR #26 hatte den Changelog, aber nicht die Audit-Datei aktualisiert. Kopfzeile §2.5 nennt jetzt korrekt die letzte Nachprüfung in 2.4.16 (der Absatz dazu stand bereits im Text).
- **Empfehlungs- und Checklisten-Status in `SECURITY_AUDIT.md` vervollständigt:** §5-Tabelle markiert die inzwischen umgesetzten Maßnahmen 1–3, 5, 6 und 12 mit Release-Bezug und ergänzt Zeile 14 für §4.6; die Komplett-Checkliste (§6) hakt Rate-Limiting, ALLOWED_HOSTS, CSP, Race-Condition, CSV-Export, HSTS-Produktionskonfiguration und DB-Aggregation ab (inkl. Doppeleintrag Cache-Control bereinigt).
- **Prompt-Status in `ARENA_AI_PROMPTS.md` ergänzt:** Die Prompts 1–3 erhalten Statusblöcke (Fixed in 2.4.1/2.4.2/2.4.3 mit Verweisen auf [Changelog](CHANGELOG.md), Testdateien und Umsetzungsabweichungen); die Zusammenfassungstabelle markiert alle umgesetzten Prompts 1–12, 14, 17–21 einheitlich mit ✅-Release. Der fehlerhafte Changelog-Anker `#242418--2026-09-08` (Prompts-Datei und PERF-21-Nachweis) ist zu `#2418--2026-09-08` korrigiert.
- **READMEs vervollständigt:** Root-`README.md` und `docs/README.md` enthalten jetzt den fehlenden Versions-Bullet für 2.4.18 (info_api-Kennzahlen per DB-Aggregation, [PERF-21](findings/PERF-21-info-api-db-aggregation.md)); das Root-README verlinkt das PERF-21-Finding in der Dokumentationsliste, `docs/README.md` nimmt die neuen Regressionstest-Module `test_module_exports` (2.4.17) und `test_info_api_metrics` (2.4.18) in den gezielten Testbefehl auf.
- Keine Code-, Settings-, Modell- oder Migrationsänderung, keine neuen Umgebungsvariablen. Bestehende Konfigurationen, laufende Bots und gespeicherte Backtests bleiben unverändert gültig; nach dem Deploy `/health/` auf **2.4.19** prüfen. Auslieferung über [PR #27](https://github.com/RG4all/t-bot-lokal/pull/27).

## [2.4.18] – 2026-09-08

### Wartung und Code-Qualität

- **info_api-Kennzahlen per DB-Aggregation (InfoApiMemoryOptimization, MEDIUM – Performance, Security-Audit §4.6):** `info_api` in `trading/views.py` lud bis zu `_MAX_LOG_ROWS` (2.000) `TradingLog`-Zeilen in den Speicher und durchlief sie in `calculate_performance_metrics()` spaltenweise in Python, um die Performance-Kennzahlen zu ermitteln. Bei großen Konfigurationen ist das ein unnötiger RAM- und CPU-Aufwand im Web-Prozess. Neu berechnet `_calculate_metrics_from_db(config, limit=_MAX_LOG_ROWS)` die Zähler und Summen über `django.db.models.aggregate()` direkt im DBMS; `info_api` ruft diese Funktion statt `calculate_performance_metrics()` auf. Die Equity-/Kassenkurve benötigt weiterhin die einzelnen Zeilen, allein die Kennzahlen werden nicht mehr in Python über alle Logs materialisiert.
- **Root Cause geschlossen, nicht nur die Symptombehebung der Prompt-Vorlage:** Die im Prompt skizzierte Variante `avg_profit=Sum("pl_nominal") / Count("id")` ist im gepinnten Django 5.2.17 **fachlich falsch** – PostgreSQL führt diese Division als Ganzzahldivision aus und liefert verkehrte Kennzahlen; zudem wäre das Ergebnis nicht backend-unabhängig (SQLite in der Testumgebung teilt dagegen im Gleitkomma). Die umgesetzte Funktion aggregiert nur Zähler, Summen, `Max` und `Min` auf der Datenbank und führt die Skalarmathematik (Quotienten, Rundung) bewusst in Python, sodass das Ergebnis bitgenau zu `calculate_performance_metrics()` bleibt und auf beiden Datenbanken identisch ist.
- **Fensterbegrenzung erhalten:** `aggregate()` wertet das angewandte `[:limit]`-Slice aus, sodass ausschließlich die jüngsten `_MAX_LOG_ROWS` Logs zählen – exakt das Fenster, das `info_api` bisher über `_latest_rows()` an `calculate_performance_metrics()` übergab. Die API liefert damit weiterhin dieselben Daten wie vor dem Fix. `calculate_performance_metrics()` bleibt als dokumentierte, listenbasierte Hilfsfunktion erhalten (sie ist weiterhin Teil des im Type-Hint-Vertrag aus Prompt 19 verankerten öffentlichen Hilfsfunktions-Sets) und wird durch `info_api` nicht mehr verwendet.
- Keine neue Laufzeit-Abhängigkeit, kein neues Architekturmuster, keine Settings-, Model- oder Migrationsänderung, keine geänderte URL, Response oder API-Form. Es wird **keine behobene Sicherheitslücke behauptet** – der Befund ist eine Performance-/Ressourcen-Schwäche (Speicher- und CPU-Last im Web-Prozess), kein Exploit.

### Tests und Qualitätssicherung

- **10 neue Regressionstests** in `trading/tests/test_info_api_metrics.py` (alle über den echten Middleware-Stack mit deaktiviertem Passphrase-Gate, analog zu `test_cache_control`): Verhaltensgleichheit der DB-Aggregation mit `calculate_performance_metrics()` über dasselbe Fenster (leere Historie, nur Käufe, nur Gewinn-Verkäufe, nur Verlust-Verkäufe, gemischt); Fensterbegrenzung – mit 2.600 Logs (600 alte Gewinne, 2.000 neue Verluste) liefert die Aggregation `win_rate == 0` und nicht die Gesamthistorie; `limit`-Parameter wird respektiert; `info_api` liefert dieselben Kennzahlen wie vor dem Fix (inkl. verschachteltem `metrics`-Objekt).
- **Angriffs- und Randvektoren (Rot→Grün):** Ein echter Negativtest patcht `calculate_performance_metrics` auf einen Abbruch; vor dem Fix lieferte `info_api` dadurch HTTP 500, nach dem Fix ignoriert `info_api` den Patch und liefert HTTP 200 – das beweist, dass die Metrik nicht mehr über den Python-Pfad berechnet wird. Division-durch-Null-Vektoren sind abgedeckt: nur Gewinne bzw. nur Verluste ergeben `risk_reward == 0` und `profit_factor == 0` ohne Exception, die Antwort enthält keine `None`-Werte.
- **330 Django-/Python-Tests** (10 neue + 320 bestehende) bestanden; Systemcheck, Migrationsprüfung (`makemigrations --check`), `collectstatic` und `pip check` lokal bestanden. Docker-/Compose-Laufzeitprüfungen mangels Docker übersprungen; `pyright`/mypy nicht ausgeführt (Node nicht eingerichtet, django-stubs nicht in `requirements.txt`), das Kriterium der statischen Prüfung ist über den ast-basierten Type-Hint-Test (`test_view_type_hints`) erfüllt, der `calculate_performance_metrics` weiterhin mit der verankerten Signatur prüft.
- Auslieferung mit ausdrücklich freigegebenen lokalen Prüfnachweisen: kein versionierter GitHub-Actions-Anwendungstestworkflow vorhanden; [CI-Freigabe und Prüfgrenzen](findings/PERF-21-info-api-db-aggregation.md#auslieferung-und-prüfgrenzen) sind dokumentiert. Auslieferung über [PR #26](https://github.com/RG4all/t-bot-lokal/pull/26).

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.18** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält weiterhin keine separate Paketversion.
- Audit §4.6 und Prompt 21 sind **Fixed**; [Finding mit Root Cause, Testnachweis, Negativkontrolle und Prüfgrenzen](findings/PERF-21-info-api-db-aggregation.md) ergänzt. [ARENA_AI_PROMPTS.md](archive/ARENA_AI_PROMPTS_2026-09-07.md) trägt den Status für Prompt 21.
- Keine neuen Umgebungsvariablen, keine Migration. Nach dem Deploy `/health/` auf **2.4.18** prüfen. Bestehende Konfigurationen, laufende Bots und gespeicherte Backtests bleiben unverändert gültig.

## [2.4.17] – 2026-09-08

### Wartung und Code-Qualität

- **Explizite öffentliche Exporte für das Trading-Modul (MissingAllExports, LOW – Tech Debt, Security-Audit §4.5):** `trading/__init__.py` definiert jetzt `__all__` mit den zwölf öffentlichen Submodulen und dokumentiert die öffentliche API im Modul-Docstring. Ohne diese Festlegung war der öffentliche Vertrag implizit: `from trading import *` exportierte nach dem Laden interner Module auch `admin`, `apps`, `middleware`, `monitoring`, `passphrase`, `rate_limit`, `urls` usw. Die interne Django-/Channels-Infrastruktur bleibt bewusst außerhalb der öffentlichen API.
- **Root Cause geschlossen, nicht nur die Symptombehebung der Prompt-Vorlage:** Die im Prompt vorgeschlagene Eager-Import-Variante (`from . import models, views, tasks, ...` direkt in `__init__.py`) wäre für dieses Django-Paket nicht lauffähig, weil `trading/__init__.py` bereits beim Populieren der Apps durch `django.setup()` geladen wird, bevor die App-Registry bereit ist – die Django-Module enden dann mit `AppRegistryNotReady`. Der Fix trennt deshalb bewusst in zwei Policy-Stufen: **Import-sichere Module** (`backtesting`, `indicators`, `market_data`, `market_scanner`, `resource_optimizer`, `symbols`) werden sofort gebunden; **Django-gebundene Module** (`trading_bot`, `views`, `models`, `tasks`, `forms`, `worker_status`) werden per modul-Level-`__getattr__` (PEP 562) erst beim ersten öffentlichen Zugriff geladen und anschließend im Paketnamespace gecacht. `from trading import *` und direkte Submodul-Importe funktionieren unverändert.
- Keine neue Laufzeit-Abhängigkeit, kein neues Architekturmuster, keine Settings-, Model- oder Migrationsänderung, keine geänderte URL, Response oder API-Form. Es wird **keine behobene Sicherheitslücke behauptet** – der Stern-Import war ein Qualitäts-/Wartbarkeitsproblem, kein nachgewiesener Exploit.

### Tests und Qualitätssicherung

- **11 neue Regressionstests** in `trading/tests/test_module_exports.py`: `__all__` als expliziter Vertrag (exakt die zwölf öffentlichen Module, keine internen Module), Modul-Docstring, explizit gebundene Import-sichere Module, keine Eager-Importe der Django-Module (verhindert `AppRegistryNotReady`), PEP-562-Auflösung der Django-Module, `from trading import *` exportiert exakt den öffentlichen Vertrag und kein `import *` im `__init__`. **Rot → grün:** Gegen den Ausgangsstand (leeres `__init__.py`) scheitern sie mit 4 Failures und 4 Errors; nach dem Fix sind alle grün.
- **Angriffs- und Regressionsvektoren:** Stern-Import nach dem Laden interner Module leakt keine `admin`/`middleware`/`passphrase`/`urls` mehr; ein neues öffentliches Modul ohne `__all__`-Eintrag fällt automatisch auf; ein versehentlich exportiertes internes Modul fällt über den Mengenvergleich auf.
- **320 Django-/Python-Tests** (11 neue + 309 bestehende) bestanden; Systemcheck, Migrationsprüfung, `collectstatic`, `pip check` und Ruff bestanden. Docker-/Compose-Laufzeitprüfungen mangels Docker übersprungen; `pyright` nicht ausgeführt (Node nicht eingerichtet).
- Auslieferung mit ausdrücklich freigegebenen lokalen Prüfnachweisen: kein versionierter GitHub-Actions-Anwendungstestworkflow vorhanden; [CI-Freigabe und Prüfgrenzen](findings/CODE-20-module-exports.md#ci-und-auslieferung) sind dokumentiert. Auslieferung über [PR #25](https://github.com/RG4all/t-bot-lokal/pull/25).

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.17** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält weiterhin keine separate Paketversion.
- Audit §4.5 und Prompt 20 sind **Fixed**; [Finding mit Root Cause, Testnachweis, Negativkontrolle und Prüfgrenzen](findings/CODE-20-module-exports.md) ergänzt.
- Keine neuen Umgebungsvariablen, keine Migration. Nach dem Deploy `/health/` auf **2.4.17** prüfen. Bestehende Konfigurationen, laufende Bots und gespeicherte Backtests bleiben unverändert gültig.

## [2.4.16] – 2026-09-08

### Wartung und Code-Qualität

- **Type-Hints und Docstrings für die Views (MissingTypeHintsViews, LOW – Tech Debt, Security-Audit §4.4):** `trading/views.py` enthielt in 1.830 Zeilen und 65 Funktionen **keine einzige Type-Annotation** und bis auf sechs Ausnahmen keine Docstrings. Ab 2.4.16 sind **alle 65 Top-Level-Funktionen** vollständig annotiert (Parameter und Rückgabewert) und **alle 39 öffentlichen Views** dokumentiert; komplexe Views mit `Args`/`Returns`/`Raises`. Die in Prompt 19 geforderten Signaturen sind exakt umgesetzt – `_portfolio_snapshot(config: Configuration) -> dict[str, Any]`, `_realized_profit(config: Configuration) -> Decimal`, `_cash_flow(log: TradingLog) -> Decimal`, `_cash_series(...) -> list[dict[str, Any]]`, `calculate_performance_metrics(...) -> dict[str, float]`, `health_view(request: HttpRequest) -> JsonResponse`, `home(...) -> HttpResponseRedirect` sowie `login_view`, `config_view` und `dashboard_view` mit `HttpResponse`. Nackte `list`/`dict`-Angaben der Vorlage sind zu Elementtypen präzisiert, weil ein untypisierter Container dem Prüfer nichts liefert. Der Decorator `no_cache_json` erhält `ParamSpec`/`TypeVar` und erhält damit die Signatur der zehn dekorierten API-Views, statt sie auf `Any` zu verwischen.
- **Eigentliche Root Cause geschlossen:** Nicht die fehlende Annotation war das Problem, sondern dass **kein statischer Prüfer** die Datei analysieren konnte. `pyproject.toml` enthält jetzt einen `[tool.mypy]`-Block (Ziel `trading/views.py`, Plugin `mypy_django_plugin.main`, `django_settings_module`); noch nicht annotierte Module stehen in einem `ignore_errors`-Override, dessen Scope mit jedem weiteren Annotationsschritt schrumpft. **mypy und django-stubs sind bewusst nicht in `requirements.txt`** – reine Entwicklungswerkzeuge, die Laufzeit-Pins des Containers bleiben unverändert.
- **Drei beim Annotieren aufgedeckte, bisher latente Randpfade gehärtet.** Keiner war im ausgelieferten Stand erreichbar, es wird **keine behobene Schwachstelle behauptet**; geschlossen wird jeweils der Weg zu einer künftigen Regression: (1) Neu `_authenticated_user(request) -> User` ersetzt **26 rohe ORM-Filter** auf dem untypisierten `request.user` (`User | AnonymousUser`) – fällt bei einer späteren Änderung `@login_required` weg, endet der Request mit 403 statt mit einem Filter auf `AnonymousUser`; die bestehenden Decorator bleiben unverändert erhalten. (2) Das Passphrase-Gate weist ein leeres Secret jetzt explizit ab, weil `constant_time_compare("", "")` **True** ergibt; die Settings erzwingen seit 2.4.4 ohnehin eine gesetzte `PASSPHRASE`. (3) `_equity_svg` überspringt Kurvenpunkte ohne `equity`-Wert bewusst, statt über einen `TypeError` im umgebenden `except` zu landen.
- Keine neue Laufzeit-Abhängigkeit, kein neues Architekturmuster, keine Settings-, Model- oder Migrationsänderung, keine geänderte URL, Response oder API-Form.

### Tests und Qualitätssicherung

- **30 neue Regressionstests** in `trading/tests/test_view_type_hints.py` (sechs Klassen): die zehn geforderten Signaturen exakt über `typing.get_type_hints()`, vollständige Annotation **jeder** Top-Level-Funktion (neue Views ohne Annotation fallen automatisch auf), `request: HttpRequest`, Response-Typen, Docstring-Pflicht mit Mindestlänge, Benutzerauflösung, leeres Gate-Secret, SVG-Robustheit und ein echter mypy-Lauf. **Rot → grün:** Gegen den Ausgangsstand `3c6eec2` scheitern sie mit **179 Failures und 42 Errors**.
- **Angriffsvektoren abgedeckt:** fremde `config_id` auf `/dashboard/` **und** `/api/info/<id>/` bleibt 404 (keine fremden Portfoliodaten), anonymer Request endet mit `PermissionDenied`, leere Gate-Passphrase erzeugt keine `passphrase_verified`-Session, ein Symbol mit Markup wird im Equity-SVG escaped.
- **Negativkontrolle der Typprüfung:** Eine nur im Prüflauf gesetzte Mutation (`_cash_flow` mit Rückgabetyp `str`) erzeugte 2 mypy-Fehler; die Prüfung ist nachweislich wirksam und kein leerer Erfolgslauf. Die Mutation ist nicht Bestandteil des Repositorys.
- **Zwei Bestandstests angepasst,** die die untypisierte Signatur als String suchten (`test_cache_control`, `test_session_invalidate`). Beide prüfen jetzt signaturunabhängig per Regex weiter, ohne an Schärfe zu verlieren – Parametername und die Prüfung auf `logout(request)`/`request.session.flush()` bleiben verbindlich.
- **309 Django-/Python-Tests** (30 neue + 279 bestehende), **mypy ohne Befund**, **8/8 Shell-Testgruppen**, Ruff 0.16.6, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` lokal bestanden. Docker-/Compose-Laufzeitprüfungen mangels Docker übersprungen; `pyright` nicht ausgeführt (Node nicht eingerichtet), das Kriterium ist über mypy erfüllt.
- **SEC-05- und SEC-06-Nachprüfung:** Alle 11 CSRF-Cookie-Tests (`CSRF_COOKIE_HTTPONLY = True`) und 10 nosniff-Tests (`SECURE_CONTENT_TYPE_NOSNIFF = True`) bestehen unverändert. Ein Smoke-Test mit tatsächlich geladenen Produktions-/Render-Settings bestätigt `nosniff` auf `/health/`, `/gate/`, dem `/dashboard/`-Redirect und der WhiteNoise-Auslieferung sowie `HttpOnly`+`Secure` am CSRF-Cookie. Beide bleiben **Fixed**.
- Auslieferung mit ausdrücklich freigegebenen lokalen Prüfnachweisen: kein versionierter GitHub-Actions-Anwendungstestworkflow vorhanden. Dependency Graph ist kein Anwendungstestnachweis; [CI-Freigabe und Prüfgrenzen](findings/CODE-19-view-type-hints.md#ci-und-auslieferung) sind dokumentiert. Auslieferung über [PR #24](https://github.com/RG4all/t-bot-lokal/pull/24).

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.16** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält Lint- und ab jetzt Typprüfungs-Konfiguration, weiterhin keine separate Paketversion.
- Audit §4.4 und Prompt 19 sind **Fixed**; [Finding mit Fix-Commit, Negativkontrolle und Prüfgrenzen](findings/CODE-19-view-type-hints.md) ergänzt. [SEC-06](findings/SEC-06-content-type-nosniff.md) trägt die Nachprüfung für 2.4.16.
- **Prüfgrenze:** Die Typprüfung deckt bewusst nur `trading/views.py` ab; die übrigen Module bleiben unannotiert und ausgenommen (bei einem Probelauf ohne Override wurden dort 6 Altbefunde sichtbar). Annotationen sind keine Laufzeitprüfung – sie wirken nur, wenn mypy tatsächlich läuft; deshalb greift der zusätzliche `ast`-basierte Test auch ohne installiertes mypy.
- Keine neuen Umgebungsvariablen, keine Migration. Nach dem Deploy `/health/` auf **2.4.16** prüfen. Bestehende Konfigurationen, laufende Bots und gespeicherte Backtests bleiben unverändert gültig.

## [2.4.15] – 2026-09-08

### Wartung und Code-Qualität

- **Duplizierte Indikator-Logik entfernt (DuplicatedIndicatorLogic, MEDIUM – Code Quality, Security-Audit §4.3):** Die Berechnung von NDA, DeltaDelta und Acceleration existierte zweimal – quantisiert in `Backtesting.calculate_indicators()` (`trading/backtesting.py`, Zeilen 14–51) und ungerundet in `TradingBot.calculate_and_store()` (`trading/trading_bot.py`, Zeilen 577–591). Neu ist **`trading/indicators.py`** als einzige Quelle der Arithmetik: `compute_indicator_values(prices, idx, *, rounding=…)` liefert den vollständigen Snapshot inklusive der `DataLog`-Nebenwerte `current_da`/`previous_da`/`dva`, `calculate_trading_indicators(prices, idx)` die quantisierte Strategie-Stufe `(acceleration, deltadelta, nda)`, `build_indicator_rows(prices)` die indexgleiche Vorabberechnung einer Preisreihe und `EIGHT_PLACES` die gemeinsame Rundungskonstante. `backtesting.py`, `trading_bot.py`, beide Raster-Durchläufe in `tasks.py` und `scripts/backtest_resource_probe.py` delegieren dorthin; `Backtesting.calculate_indicators()` bleibt als dünner Kompatibilitäts-Wrapper erhalten.
- **Latenter Defekt mit behoben:** Beide Kopien hatten **keine Index-Validierung**. `calculate_indicators(prices, 1)` griff über die Listendefinition auf `prices[-1]` zu und lieferte für `100, 101, 102` stillschweigend `(-1.5, -0.5, 1.0)` statt zu scheitern. Die zentrale Funktion prüft jetzt `idx >= 2` und `idx < len(prices)` (bei einer Preisreihe mit fester Länge) und weist `TypeError` für Nicht-Sequenzen aus.
- **Zwei Präzisionsstufen, eine Implementierung:** Der Live-Bot rechnet weiterhin mit ungerundeten Rohwerten und rundet erst beim Schreiben (`_bounded`), Backtests quantisieren jede Zwischenstufe auf 8 Nachkommastellen (`ROUND_HALF_UP`). Der Unterschied ist auf einen `rounding`-Hook reduziert und im Modul-Docstring begründet. Ein Deduplizierungs-Refactoring darf keine laufenden Schwellwertentscheidungen und keine gespeicherte `DataLog`-Historie verschieben; die Vereinheitlichung der Präzision bleibt eine separate, bewusste Entscheidung.
- Die Rundungskonstante `_EIGHT_PLACES` war zweimal definiert (`backtesting.py`, `trading_bot.py`), das Vorabberechnungsmuster `[None, None] + [… ]` viermal kopiert (`backtesting.py`, `tasks.py` zweimal, Ressourcen-Probe). Beides ist zugunsten der zentralen Definitionen entfallen. Keine neue Abhängigkeit, kein neues Architekturmuster, keine Settings-, Model- oder Migrationsänderung, keine API-Änderung für Views, Celery-Tasks und Reports.

### Tests und Qualitätssicherung

- **30 neue Regressionstests** in `trading/tests/test_indicators.py`: von Hand nachgerechnete Referenzwerte, `ROUND_HALF_UP` auf der 9. Nachkommastelle, Nullstellen-Absicherungen (Vorpreis `0`/`None`, `previous_nda == 0`), Index-Grenzfälle, Nicht-Decimal-Eingaben, dokumentierter Präzisionsunterschied Rohwert/quantisiert, strukturelle Prüfung, dass keine Formel und kein Vorabberechnungsmuster mehr in den Aufrufern steht, plus Bitgenauigkeit der `DataLog`-Zeile und der an `check_trading` übergebenen Rohwerte. **Rot → grün:** Am Ausgangsstand scheiterte das Modul am Import, die Index- und Duplikatsprüfungen waren rot; die Bot-Verhaltenstests sind als Charakterisierungstests gegen eine still veränderte Präzision gedacht.
- **Numerische Reproduktion:** deterministischer Alt-/Neu-Vergleich über 3.000 Preisreihen (Null-Vorpreise, `None`-Preise, Float-/String-Mischtypen, Extremwerte um 10³⁰) – **16.693 Vergleichsfälle, 0 Abweichungen**; Backtest-Reports einer 2.000-Punkte-Reihe mit Defekt-Ticks über drei Schwellwert-Raster sind vor und nach dem Refactoring **byte-identisch** (Endkapital, Rendite, Trades, Gebühren, Drawdown, Equity-Kurve).
- **279 Django-/Python-Tests** (30 neue + 249 bestehende), **8/8 Shell-Testgruppen**, Ruff 0.16.6, ShellCheck 0.11.0, Systemcheck, Migrationsprüfung, `collectstatic`, `pip check` und die Ressourcen-Probe lokal bestanden. Docker-/Compose-Laufzeitprüfungen mangels Docker übersprungen.
- **SEC-05-Nachprüfung:** Alle 11 CSRF-Cookie-Tests (`CSRF_COOKIE_HTTPONLY = True`) bestehen unverändert; SEC-05 bleibt Fixed.
- Auslieferung mit ausdrücklich freigegebenen lokalen Prüfnachweisen: kein versionierter GitHub-Actions-Anwendungstestworkflow vorhanden. Dependency Graph ist kein Anwendungstestnachweis; [CI-Freigabe und Prüfgrenzen](findings/CODE-18-indicator-dedup.md#ci-und-auslieferung) sind dokumentiert.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.15** erhöht; Root-/docs-README, Handbuch, Backtesting-Kapitel und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält nur Lint-Konfiguration und keine separate Paketversion.
- Audit §4.3 und Prompt 18 sind **Fixed**; [Finding mit Fix-Commit und Prüfgrenzen](findings/CODE-18-indicator-dedup.md) ergänzt. Handbuch §6 und Backtesting-Kapitel verweisen jetzt auf `trading/indicators.py` als Maß aller Formeln und erklären die beiden Präzisionsstufen.
- Keine neuen Umgebungsvariablen, keine Migration. Nach dem Deploy `/health/` auf **2.4.15** prüfen. Bestehende `DataLog`-Zeilen und laufende Bot-Konfigurationen bleiben gültig; Backtests müssen nicht neu gestartet werden, ihre Ergebnisse sind reproduzierbar.

## [2.4.14] – 2026-09-08

### Performance

- **DB-Trim als Batch-Delete (DbTrimBatchDelete, MEDIUM – Performance, Security-Audit §4.2):** `db_trim_datalog()` in `trading/trading_bot.py` löschte alle Alt-Einträge einer Konfiguration/Symbol-Kombination in **einem einzigen DELETE-Statement**. Bei 20.000+ Zeilen pro Symbol hielt diese eine lang laufende Schreibtransaktion die Datenbank für andere Bots und Requests gesperrt. Ab 2.4.14 löscht die Funktion in **1000er-Schritten**: Je Durchgang liest sie nur die nächsten maximal 1000 betroffenen IDs (`ORDER BY id LIMIT 1000`) und löscht genau diese in einer eigenen, sofort committeten Transaktion (`id__in`). Sobald keine Alt-Zeile mehr übrig ist, bricht die Schleife ab. Es entsteht keine Sperre mehr über sämtliche Alt-Einträge; Behaltenslogik, Symbol-/Konfigurations-Scoping, Aufrufrhythmus und `@db_safe`-Fehlerbehandlung bleiben unverändert.
- **Korrektur des historischen Lösungsvorschlags:** Die in Prompt 17 und Audit §4.2 skizzierte Variante `queryset[:1000].delete()` ist mit Django 5.2.17 nicht ausführbar (Django lehnt `LIMIT` direkt auf `.delete()` mit `TypeError` ab). Die umgesetzte ID-Chargen-Variante erreicht dieselbe Batch-Wirkung über die unterstützte ORM-API. Zusätzlich behandelt die Funktion `max_rows < 1` als sicheres No-op statt einer `ValueError`-Exception beim Slicing.
- Keine neue Abhängigkeit, keine Migration, keine Settings-Änderung; `pyproject.toml` enthält nur Lint-Konfiguration und keine separate Paketversion.

### Tests und Qualitätssicherung

- **7 neue Regressionstests** in `trading/tests/test_db_trim_datalog.py`: Behaltenslogik (nur die neuesten `max_rows` Zeilen bleiben), Grenzfälle (genau/weniger als `max_rows`), Scoping auf Konfiguration+Symbol, Chargengrößen-Nachweis per Transaktions-Probe (`[1000, 1000, 500]` bei 2.500 Alt-Zeilen), Abbruch bei leeren Deletes und No-op bei ungültigem `max_rows`. **Rot → grün:** Am Ausgangsstand fand der Batch-Nachweis genau eine Transaktion über alle 2.500 Zeilen (`[2500]`), und negatives `max_rows` endete mit `ValueError: Negative indexing is not supported.`
- **249 Django-/Python-Tests** (7 neue + 242 bestehende), **8/8 Shell-Testgruppen**, Ruff, ShellCheck, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` lokal bestanden. Docker-/Compose-Laufzeitprüfungen mangels Docker übersprungen.
- **SEC-05-Nachprüfung:** Alle 11 CSRF-Cookie-Tests (`CSRF_COOKIE_HTTPONLY = True`) bestehen unverändert; SEC-05 bleibt Fixed.
- Auslieferung mit ausdrücklich freigegebenen lokalen Prüfnachweisen: kein versionierter GitHub-Actions-Anwendungstestworkflow vorhanden. Dependency Graph ist kein Anwendungstestnachweis; [CI-Freigabe und Prüfgrenzen](findings/PERF-17-db-trim-batch-delete.md#ci-und-auslieferung) sind dokumentiert.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.14** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert.
- Audit §4.2 und Prompt 17 sind **Fixed**; [Finding mit Fix-Commit und Prüfgrenzen](findings/PERF-17-db-trim-batch-delete.md) ergänzt.
- Keine neuen Umgebungsvariablen oder Migrationen. Nach dem Deploy `/health/` auf **2.4.14** prüfen; Datenbank-Historien werden ab dem nächsten regulären Trim-Zyklus batchweise gekürzt, ein manueller Eingriff ist nicht nötig.

## [2.4.13] – 2026-09-08

### Sicherheit und Bug-Fixes

- **BUG-14 / CsvEchoNotTrueStream (LOW, Security-Audit §3.3):** Der Trading-CSV-Export im geprüften Stand **2.4.12** verwendete einen eigenen Echo-Adapter. Ab **2.4.13** ersetzt ein wiederverwendeter `io.StringIO`-Textpuffer die Klasse `_CsvEcho`. CSV-Inhalte werden mit `getvalue()` ausgelesen, statt vom Rückgabeverhalten des Adapters abzuhängen. Das bisherige Echo-Muster war für `csv.writer` gültig; der Fix verbessert die Standardkompatibilität, ohne eine nachgewiesene Sicherheitslücke zu behaupten.
- Der synchrone Generator leert den Puffer vor jeder Datenzeile vollständig und schließt ihn bei Ende, Fehler oder Response-Abbruch. UTF-8-BOM, zwölf Spalten, Zeitstempel-/ID-Sortierung, `iterator(chunk_size=1000)`, Dateiname und Eigentümerprüfung bleiben unverändert. Die bestehende ASGI-Anpassung synchroner Iteratoren ist nicht Gegenstand dieses Fixes.

### Tests und Qualitätssicherung

- **15 neue Regressionstests** in `trading/tests/test_report_csv.py`: Standard-Textpuffer, zeilenweise Ausgabe ohne Datenreste, verzögerter Datenbankzugriff über die 1.000er-Grenze, Präzision, Zeitzone, CSV-Sonderzeichen, Pufferfreigabe sowie Login-, Methoden-, Eigentümer- und Dateinamensgrenzen. **Rot → grün:** fünf Tests am Ausgangsstand fehlgeschlagen (sieben Assertions). Negativkontrollen für fehlendes Zurücksetzen, Kürzen und Schließen des Puffers werden erkannt.
- **242 Django-/Python-Tests**, **8/8 Shell-Testgruppen**, Ruff, ShellCheck, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` lokal bestanden. Docker-/Compose-Laufzeitprüfungen mangels Docker übersprungen.
- Auslieferung mit ausdrücklich freigegebenen lokalen Prüfnachweisen: kein versionierter GitHub-Actions-Anwendungstestworkflow vorhanden. Dependency Graph ist kein Anwendungstestnachweis; [CI-Freigabe und Prüfgrenzen](findings/BUG-14-csv-echo-true-stream.md#ci-und-auslieferung) sind dokumentiert.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.13** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält nur Lint-Konfiguration, keine separate Paketversion.
- Audit §3.3 und Prompt 14 sind **Fixed**; [Finding mit Fix-Commit](findings/BUG-14-csv-echo-true-stream.md) ergänzt.
- Keine neuen Abhängigkeiten, Umgebungsvariablen oder Migrationen. Nach dem Deploy `/health/` auf **2.4.13** prüfen; CSV-Importe müssen nicht angepasst werden.

## [2.4.12] – 2026-09-08

### Bug-Fixes

- **Race Condition beim Bot-Start/Stop behoben:** `TradingBotManager.start_bot()` rief unter gehaltenem Manager-Lock die öffentliche Methode `is_running()` auf, die denselben Lock intern erneut erwarb. Mit `threading.RLock` entstand kein Deadlock, aber die verschachtelte Acquisition war fehleranfällig (echter Deadlock bei einem nicht-reentranten Lock) und unnötig. Neu ist `_is_running_unlocked()` als lock-freie interne Prüfung; `is_running()`, `start_bot()` und `stop_bot()` nutzen sie unter genau einem Lock. Tote Thread-Referenzen werden atomar entfernt, statt einen zweiten Paper-Bot für dieselbe Konfiguration zu starten.
- Externe Aufrufer (Views, Status-API) bleiben bei der öffentlichen, lockenden `is_running()`-API. Keine neue Runtime-Abhängigkeit, keine Migration, keine API-Änderung.

### Tests und Qualitätssicherung

- Neu `trading/tests/test_bot_start_stop.py` (16 Tests) plus eine Quellcode-Prüfung in `TradingBotTests`: Lock-Trennung, Verschachtelungstiefe 1, Deadlock-Negativkontrolle mit `threading.Lock`, gleichzeitige Starts derselben und verschiedener Konfigurationen, Aufräumen beendeter Threads. **Rot → grün:** 7 Tests schlugen am Ausgangsstand fehl (fehlender Helper, `max_depth == 2`, Deadlock auf nicht-reentrantem Lock).
- **227 Django-/Python-Tests** (17 neue + 210 bestehende), **8/8 Shell-Testgruppen**, Ruff, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.12** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält nur Lint-Konfiguration und keine separate Paketversion.
- Audit §3.1 und Prompt 12 sind **Fixed**; [Finding mit Fix-Commit](findings/BUG-12-race-condition-bot-start-stop.md) ergänzt.
- Keine neuen Umgebungsvariablen oder Migrationen. Nach dem Deploy `/health/` auf 2.4.12 prüfen; laufende Bots verhalten sich für Aufrufer unverändert, doppelte Threads derselben Konfiguration entstehen nicht mehr durch verschachtelte Lock-Prüfung.

## [2.4.11] – 2026-09-08

### Sicherheit

- **SEC-12 – Keine Standard-Passwörter im Compose-Setup:** `docker-compose.yml` verlangt `SECRET_KEY`, `PASSPHRASE` und `POSTGRES_PASSWORD` als Pflichtwerte (`${VAR:?...}`). Fehlende oder leere Secrets brechen die Compose-Interpolation mit einer klaren Fehlermeldung ab; der frühere öffentliche Datenbank-Standard-Passwort-Fallback (`${POSTGRES_PASSWORD:-...}` in `DATABASE_URL` und im Postgres-Service) ist entfernt. Ein vergessenes Env-File startet nicht mehr still mit öffentlich bekannten Zugangsdaten.
- `scripts/setup_local.sh` erzeugt für neue Setups ein zufälliges, privat gehaltenes `POSTGRES_PASSWORD` in `.env.local` (Modus 0600) statt des öffentlichen Defaults und bewahrt bestehende Werte beim Retuning. Erkennt das Skript den früher öffentlichen Standard-Wert, weist es auf die Rotation inklusive `--reset-db` hin, ohne den Wert erneut zu veröffentlichen (SHA-256-Vergleich).
- Beide Env-Beispiele (`SECRET_KEY`/`PASSPHRASE`/`POSTGRES_PASSWORD`), `config.template` und `install.sh` enthalten keine benutzbaren oder öffentlich bekannten Secret-Werte mehr; `POSTGRES_PASSWORD=` bleibt bewusst als leerer Platzhalter mit Erzeugungshinweis. Nicht-Secrets (`POSTGRES_USER`, `POSTGRES_DB`) behalten ihre lokalen Defaults.
- Keine neue Runtime-Abhängigkeit, keine Migration und keine Änderung an App-Verhalten oder Settings; bestehende `.env.local`-Dateien funktionieren unverändert weiter.

### Tests und Qualitätssicherung

- Neue Shell-Testgruppe `tests/test_compose_security.sh` (25 Assertions): Pflicht-Interpolation für alle drei Secrets, keine `${VAR:-...}`-Fallbacks, keine öffentlichen Standard-Passwörter in den 13 ausgelieferten Konfigurations-/Skriptdateien, leere Platzhalter in den Env-Beispielen; optional prüft sie mit Docker, dass `docker compose config` ohne Secrets scheitert und mit Secrets auflöst (ohne Docker übersprungen).
- `tests/test_setup_local.sh` erweitert (Rot → grün): zufälliges DB-Passwort statt öffentlichem Standard, Erhalt beim Retuning, unterschiedliche Passwörter pro Setup. Vor dem Fix schlugen 3 von 9 Assertions in dieser Gruppe und 7 Assertions in der neuen Compose-Gruppe fehl.
- **8/8 Shell-Testgruppen** (inkl. neuer Gruppe), **210 Django-/Python-Tests**, Ruff, ShellCheck, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden.
- Auslieferung mit lokalen Prüfnachweisen ohne neuen GitHub-Actions-Testworkflow: Der GitHub-App fehlt die Berechtigung für Workflow-Änderungen. Kein erfolgreicher GitHub-Anwendungstest-CI-Lauf wird behauptet; Docker/Compose steht für den Interpolationstest lokal nicht zur Verfügung, die Prüfung ist dort statisch ([SEC-12](findings/SEC-12-docker-default-passwords.md)).

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.11** erhöht; Root-/docs-README, Handbuch, lokale Entwicklungsdoku und FAQ aktualisiert. `pyproject.toml` enthält nur Lint-Konfiguration und keine separate Paketversion.
- Audit §2.12 und Prompt 11 sind **Fixed**; [SEC-12 mit Fix-Commit und Prüfgrenzen](findings/SEC-12-docker-default-passwords.md) ergänzt.
- Upgrade ohne Datenverlust: bestehende `.env.local`-Dateien bleiben nutzbar. Wer noch das frühere öffentliche DB-Passwort verwendet, rotiert es wie in der [FAQ](operations/FAQ.md#8-passwoerter-aendern--secret-rotation) beschrieben (`--reset-db` löscht die lokale Datenbank). Manuelles Compose benötigt jetzt zwingend gesetzte `SECRET_KEY`-, `PASSPHRASE`- und `POSTGRES_PASSWORD`-Werte.

## [2.4.10] – 2026-09-08

### Sicherheit

- **SEC-10 – Information Disclosure behoben:** Im geprüften Stand 2.4.9 konnten technische Fehlerdetails in Benutzerantworten erscheinen. Ab 2.4.10 verwenden Bot-Start/Validierung, Verkaufsaktionen, Marktdaten-/Worker-APIs, Formulare und Reportfehler generische Meldungen. Diagnosen bleiben mit Traceback in geschützten Logs; auch Teilfehler und gespeicherte Backtest-Fehler werden nicht als technische Benutzer-Meldungen ausgegeben.
- **Diagnose-Log abgesichert:** Technische Meldungen, Typen und Details sind im Fehler-Log nur noch für Staff-Konten unter Beibehaltung der Eigentümerprüfung sichtbar. Normale Konten erhalten eine generische Meldung mit Referenz und können eigene Einträge weiterhin filtern und erledigen. Die Einschränkung schützt auch bestehende Einträge ohne Datenmigration.
- Fehler beim zusätzlichen Speichern eines Diagnose-Log-Eintrags werden separat geloggt und verdrängen nicht die sichere Antwort. Bestehende Statuscodes und erfolgreiche Handels-/Reportantworten bleiben erhalten; PDF-Renderfehler werden ebenfalls kontrolliert mit 503 beantwortet.
- **SEC-05 erneut geprüft:** Alle 11 CSRF-Cookie-Tests bestanden; eine gezielte Negativkontrolle mit deaktiviertem `HttpOnly` wird erkannt. HttpOnly bleibt zusätzliche Cookie-Härtung, kein allgemeiner XSS-Schutz.

### Tests und Qualitätssicherung

- 25 neue Regressionstests in `trading/tests/test_error_disclosure.py`: Rot am Ausgangsstand, grün mit Fix. Abdeckung umfasst Flash-Cookies, HTTP-/JSON-Antworten, Fehlertypen und Exception-Ketten, Teilausfälle, Log-Persistenzfehler, alte Diagnosedaten sowie Eigentümer-/Staff-/CSRF-Grenzen.
- **210 Django-/Python-Tests**, 7/7 Shell-Testgruppen, Ruff, ShellCheck, Systemcheck, Migrationsprüfung, `collectstatic`, `pip check` und `pip-audit` lokal bestanden.
- Auslieferung mit ausdrücklich genehmigten lokalen Prüfnachweisen, ohne neuen GitHub-Actions-Testworkflow: Der GitHub-App fehlt die Berechtigung für Workflow-Änderungen. Kein erfolgreicher GitHub-Anwendungstest-CI-Lauf wird behauptet; [CI-Ausnahme und Prüfgrenzen](findings/SEC-10-information-disclosure.md#ci-und-auslieferung) sind dokumentiert.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.10** erhöht; Root-/docs-README, Handbuch und lokale Versionsangabe aktualisiert. `pyproject.toml` enthält nur Lint-Konfiguration und keine separate Paketversion.
- Audit §2.10 und Prompt 10 sind **Fixed**; [SEC-10 mit Fix-Commit und SEC-05-Nachprüfung](findings/SEC-10-information-disclosure.md) ergänzt.
- Keine neue Runtime-Abhängigkeit, keine Datenbankmigration und keine neuen Umgebungsvariablen. Server-Logs privat halten; nach dem Deploy `/health/` auf 2.4.10 und die getrennte Fehler-Log-Anzeige für normale/Staff-Konten prüfen.

## [2.4.9] – 2026-09-08

### Sicherheit

- **SEC-09 – Permissions-Policy-Header:** Die neue zentrale Einstellung `SECURE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"` in `trading_bot_project/settings.py` deaktiviert den Zugriff auf Kamera, Mikrofon und Geolokation für alle Origins. Da Django selbst keinen `Permissions-Policy`-Header erzeugt, setzt die neue Middleware `trading.middleware.PermissionsPolicyMiddleware` den Header aus dieser Einstellung auf jeder Antwort.
- Die Middleware ist **direkt nach der `SecurityMiddleware`** registriert und erfasst damit auch Fehlerantworten (400/403/404/405/429/500/503), Redirects (302), Streaming/Downloads und von WhiteNoise beantwortete statische Dateien. Ein leerer Policy-Wert lässt Antworten unverändert.
- Keine neue Runtime-Abhängigkeit, keine Migration und keine API-/Nutzdatenänderung.
- **SEC-05 nachgeprüft:** `CSRF_COOKIE_HTTPONLY = True` bleibt unverändert aktiv; alle 11 CSRF-Cookie-Tests laufen weiterhin grün.

### Tests

- Neu `trading/tests/test_permissions_policy.py` (11 Tests): explizite Settings in der DEBUG-/Render-Matrix, Middleware-Reihenfolge, synchrone/asynchrone Responses, HTML/JSON, Gate-Redirects, Fehlerantworten, WhiteNoise GET/HEAD/304 sowie die Negativkontrolle, dass ein leerer Policy-Wert keinen Header erzeugt.
- Rot → grün: Ohne die registrierte Middleware schlugen die Settings-Matrix und sämtliche Header-Asserts fehl (17 fehlgeschlagene Assertions); mit dem Fix sind alle 11 Tests grün.
- Lokale Validierung: **185 Django-/Python-Tests** (11 neue + 174 bestehende), Ruff, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.9** erhöht; Sicherheitsabschnitte in beiden READMEs und im Handbuch ergänzt.
- Befund §2.9 im Security-Audit und Prompt 9 in `ARENA_AI_PROMPTS.md` als **Fixed** markiert; [Finding-Nachweis SEC-09](findings/SEC-09-permissions-policy.md) ergänzt.
- Keine neuen Umgebungsvariablen oder Migrationen erforderlich. Antworten eines vorgeschalteten Reverse-Proxys/CDNs werden von Django nicht automatisch mit dem Header versehen; dort bei Bedarf eine entsprechende Konfiguration ergänzen.

## [2.4.8] – 2026-09-07

### Sicherheit

- **SEC-08 – Cache-Control-Header für alle API-Endpunkte:** Der neue Decorator `no_cache_json` in `trading/views.py` setzt auf jeder API-Antwort `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` sowie `Pragma: no-cache`. Damit speichern Browser und zwischengeschaltete Proxies/CDNs keine benutzerbezogenen Handels-, Portfolio-, Log- oder Marktdaten mehr zwischen. Der Header gilt auch für von den Views erzeugte Fehlerantworten (z. B. 400/503).
- **Betroffene Endpunkte:** `/api/info/`, `/api/bot/status/`, `/api/logs/`, `/api/data_logs/`, `/api/trades/`, `/api/symbols/`, `/api/market-opportunities/` (und `/api/top-movers/`), `/api/backtesting/status/`, `/api/backtesting/estimate/`, `/api/resources/`.
- Keine neue Runtime-Abhängigkeit, keine Migration und keine API-/Nutzdatenänderung; die Header ergänzen lediglich die bestehenden Antworten.
- **SEC-05 nachgeprüft:** `CSRF_COOKIE_HTTPONLY = True` bleibt unverändert aktiv; alle 11 CSRF-Cookie-Tests laufen weiterhin grün.

### Tests

- Neu `trading/tests/test_cache_control.py` (9 Tests): Quellcode-Prüfungen, dass der Decorator existiert, die erwarteten Header-Werte setzt und auf alle zehn API-Views angewendet ist; Integrationstests über den echten Middleware-Stack für 200-, 400- und 503-Antworten sowie die unveränderte eigentümerbezogene 404-Autorisierung.
- Rot → grün: Vor dem Fix fehlte der Decorator vollständig; die Quellcode-Prüfungen und sämtliche Header-Asserts schlugen fehl.
- Lokale Validierung: **174 Django-/Python-Tests** (9 neue + 165 bestehende), Ruff, Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.8** erhöht; Sicherheitsabschnitte in beiden READMEs und im Handbuch ergänzt.
- Befund §2.8 im Security-Audit und Prompt 8 in `ARENA_AI_PROMPTS.md` als **Fixed** markiert; [Finding-Nachweis SEC-08](findings/SEC-08-cache-control-api.md) ergänzt.
- Keine neuen Umgebungsvariablen oder Migrationen erforderlich. Antworten eines vorgeschalteten Reverse-Proxys/CDNs werden von Django nicht automatisch mit den Headern versehen; dort bei Bedarf eine entsprechende no-cache-Konfiguration ergänzen.

## [2.4.7] – 2026-09-07

### Sicherheit

- **SEC-07 – Session-Lebensdauer reduziert und Logout invalidiert die Session:** `SESSION_COOKIE_AGE` wurde von 12 auf **8 Stunden** (28.800 s) herabgesetzt und `SESSION_EXPIRE_AT_BROWSER_CLOSE = True` explizit gesetzt. Die Einstellungen gelten DEBUG-/Render-unabhängig und verkleinern das Fenster für die Wiederverwendung eines gestohlenen signierten Session-Cookies.
- **`request.session.flush()` in `logout_view`:** Die Abmeldung (POST `/logout/`) leert nun alle Session-Daten und rotiert den Session-Key unmittelbar. Bisher wurde nur `logout(request)` aufgerufen; bei `signed_cookies` konnten Restdaten und der bisherige Cookie-Inhalt theoretisch bis zum Ablauf weiterverwendet werden. Nach Logout ist der Benutzer anonym und muss sich – wie auch nach dem Gate-Neustart – neu authentifizieren.
- Keine neue Runtime-Abhängigkeit, keine Migration und keine API-Änderung. Bereits laufende Sitzungen bleiben bis zur nächsten Abmeldung oder dem Browser-Schluss gültig.

### Tests

- Neu `trading/tests/test_session_invalidate.py` (11 Tests): explizite Settings-Werte inkl. Source-Scan, DEBUG-/Render-Matrix, Ablauf bei Browser-Schluss, wirksame Anonymisierung nach POST-Logout, Reset/Umleitung, erzwungene CSRF-Prüfung (`Client(enforce_csrf_checks=True)`), POST-Only für Logout und Quellcodeprüfung gegen Regressionen des `flush()`-Aufrufs.
- Rot → grün: Vor dem Fix fehlte `SESSION_EXPIRE_AT_BROWSER_CLOSE`, `SESSION_COOKIE_AGE` betrug 12 Stunden und `logout_view` enthielt kein `request.session.flush()`. Alle drei Konfigurationstests schlugen entsprechend fehl.
- Lokale Validierung: **165 Django-/Python-Tests** (11 neue + 154 bestehende), Ruff, Systemcheck, Migrationsprüfung und `pip check` bestanden.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.7** erhöht; relevante Sicherheitsabschnitte in beiden READMEs aktualisiert.
- Befund §2.7 im Security-Audit und Prompt 7 in `ARENA_AI_PROMPTS.md` als **Fixed** markiert; [Finding-Nachweis SEC-07](findings/SEC-07-session-lifetime-invalidation.md) ergänzt.
- Keine neuen Umgebungsvariablen oder Migrationen erforderlich. Nach dem Deploy die Session-Einstellungen über den öffentlichen HTTPS-Endpunkt (bzw. den vorgeschalteten Proxy) prüfen; Cookie-Flags wie `HttpOnly` und `SameSite` bleiben unverändert wirksam.

## [2.4.6] – 2026-09-07

### Sicherheit

- **SEC-06 – nosniff explizit aktiviert:** `SECURE_CONTENT_TYPE_NOSNIFF = True` in `trading_bot_project/settings.py` gilt unabhängig von DEBUG/Render. Die bereits an erster Stelle registrierte `SecurityMiddleware` setzt `X-Content-Type-Options: nosniff` auch auf Fehlerantworten, Redirects, Downloads und WhiteNoise-Antworten. Der Header schützt vor unerwünschter MIME-Typ-Interpretation, insbesondere bei Skript-/Stylesheet-Ressourcen.
- **Präzisierung des Vorzustands:** Django 5.2.17 aktivierte nosniff bereits per Default. Behoben ist die fehlende explizite Projektkonfiguration, nicht ein nachgewiesener fehlender Header im bisherigen Standard-Stack. Keine neue Runtime-Abhängigkeit und keine API-/Datenbankänderung.
- **SEC-05 nachgeprüft:** `CSRF_COOKIE_HTTPONLY = True` bleibt seit 2.4.5 aktiv. Login-Tests lesen jetzt tatsächlich den maskierten Token aus dem Formularfeld statt aus dem Cookie. Kommentare und Dokumentation grenzen den Schutz korrekt ab: HttpOnly verhindert das Lesen des Cookies, nicht das Lesen des DOM-Tokens bei XSS.

### Tests

- 10 neue nosniff-Tests: explizite Settings in der DEBUG-/Render-Matrix, Middleware-Reihenfolge, synchrone/asynchrone Responses, HTML/JSON, 301/302/304 sowie 400/403/404/405/429/500/503, nicht ausführbare MIME-Typen trotz aktiver Inhaltssyntax und manipulierter Request-Header, Streaming und WhiteNoise GET/HEAD/Cache-Antworten.
- SEC-05 auf 11 Tests erweitert: Cookie-Flags aus real geladenen lokalen/Produktions-Settings, echter Formular-Token-Login, Ablehnung fremder Origins, fremder Tokens und fehlender Cookies.
- Rot → grün: Ohne explizite nosniff-Einstellung scheitert die neue Settings-Matrix in allen vier Kombinationen; Response-Tests waren dank Django-Default schon grün. Zusätzliche Testprozess-Mutationen mit deaktiviertem nosniff bzw. HttpOnly werden erkannt.
- Lokale Validierung: 154 Django-/Python-Tests und 7/7 Shell-Testgruppen bestanden; Ruff, ShellCheck, Produktions-Deploy-Checks, Migrationsprüfung, `collectstatic` und `pip check` erfolgreich. Kein neuer GitHub-Actions-Testworkflow eingeführt; Docker-/PDF-Laufzeitprüfungen bleiben außerhalb des lokalen Nachweises.

### Dokumentation und Upgrade

- Zentrale `VERSION` auf **2.4.6** erhöht; aktuelle Versionsangaben und Sicherheitsabschnitte in beiden READMEs, Handbuch und lokaler Anleitung aktualisiert. `pyproject.toml` enthält nur Ruff-Konfiguration, keine separate Paketversion.
- Audit §2.6 und Prompt 6 als **Fixed** dokumentiert; [Finding-Nachweis mit Fix-Commit und SEC-05-Nachprüfung](findings/SEC-06-content-type-nosniff.md) ergänzt. Der vorgegebene Finding-Dateiname wird dort ausdrücklich dem nosniff-Finding zugeordnet, nicht einem anderen Autorisierungsbefund.
- Keine neuen Umgebungsvariablen oder Migrationen erforderlich. Nach dem Deploy den Header auch über den tatsächlichen Reverse-Proxy/CDN prüfen; außerhalb von Django erzeugte Antworten benötigen dort eine entsprechende Header-Konfiguration.

## [2.4.5] – 2026-09-07

### Sicherheit

- **CSRF-Cookie mit HttpOnly:** `CSRF_COOKIE_HTTPONLY = True` in `trading_bot_project/settings.py` (nach der Session-Cookie-Konfiguration). Das `csrftoken`-Cookie wird seither mit dem `HttpOnly`-Flag gesetzt und ist damit nicht mehr über JavaScript (`document.cookie`) lesbar. Präzisierung aus der Nachprüfung 2.4.6: Das verhindert nur den Cookie-Zugriff; bei XSS bleibt das DOM-Token zugänglich. Die Einstellung ist bewusst unabhängig vom `DEBUG`-Modus aktiv, da `HttpOnly` auch über plain HTTP unproblematisch ist und in Produktion `CSRF_COOKIE_SECURE` ergänzend gilt.
- **Kein Funktionsverlust:** Django liest das Cookie serverseitig aus; die Templates liefern das Token über `{% csrf_token %}` als verstecktes Formularfeld an. Die eigenen App-Skripte (z. B. Dashboard-Actions) lesen das Token aus dem Formularfeld und sind nicht betroffen. CSRF-Prüfung und Login-Fluss bleiben unverändert wirksam.

### Tests

- Neu `trading/tests/test_csrf_cookie.py` (7 Tests): Präsenz und explizite Quellcodewerte der Einstellung, DEBUG-/Produktions-Matrix, `HttpOnly`-Flag auf dem Draht (exakte `Set-Cookie`-Serialisierung), unveränderte Token-Auslieferung, Ablehnung von POSTs ohne Token (403) und vollständiger Login-Fluss mit erzwungener CSRF-Prüfung (`Client(enforce_csrf_checks=True)`). Die ersten vier reproduzierten den Vorzustand ohne den Fix (rot → grün).

### Dokumentation

- Befund `SECURITY_AUDIT.md` §2.5 als behoben markiert; Prompt 5 in `ARENA_AI_PROMPTS.md` mit Status versehen; Cookie-Härtung in beiden READMEs dokumentiert.

## [2.4.4] – 2026-09-07

### Sicherheit

- **Passphrase ohne öffentlichen Fallback:** Lokale Settings generieren mit `secrets.token_urlsafe(32)` einen temporären Wert und melden ihn als WARNING. Auf Render oder bei `DEBUG=False` führen fehlende/leere (auch reine Leerraum-)Secrets zum `RuntimeError`; dort wird kein Zufalls-Secret geloggt. Explizite Passphrasen bleiben unverändert.
- **Signierte Gate-Sessions:** Auch der lokale `SECRET_KEY` ist bei fehlender Konfiguration zufällig statt öffentlich. Gate-Nachweise sind HMAC-SHA256-gebunden an die aktuelle Passphrase und den Signierschlüssel. Alte boolesche Freigaben sowie Freigaben vor einer Rotation werden abgelehnt; WebSocket-Verbindungen prüfen zusätzlich den Gate.
- **Produktions-Gate bleibt aktiv:** Das Basis-Docker-Image deaktiviert ihn nicht mehr. Render setzt ihn explizit aktiv; `PASSPHRASE_GATE_ENABLED=False` ist auf Render bzw. bei `DEBUG=False` ein Startfehler. Lokales Compose bleibt ein ausdrücklich isoliertes DEBUG-Profil mit optionalem Gate.
- **Setup-Secrets:** Compose verlangt `SECRET_KEY` und `PASSPHRASE`. Installer/Tuner erzeugen private Passphrasen, erhalten vorhandene Werte und schreiben Secrets-Dateien mit restriktiven Berechtigungen. Retuning respektiert einen bereits aktivierten Gate.
- **Auth-Rate-Limit:** Ungeprüftes `X-Forwarded-For` kann keine neuen Zähler mehr erzeugen. Optionale, explizite Proxy-Allowlist mit Auswertung von rechts; atomare Prüfung/Reservierung; begrenzter Speicher mit globalem Ablauf inaktiver Einträge. Admin-Login mitgeschützt, weiterhin fünf POSTs je IP/15 Minuten/Prozess.
- **Keine neue Runtime-Abhängigkeit.** Vorhandene Pins unverändert; Dependency-Audit und kontextbezogene Bandit-Auswertung im Security-Review dokumentiert.

### Bug-Fixes und Wartbarkeit

- Unicode-Passphrasen lösen keinen `TypeError` mehr aus; timing-sicherer Vergleich ohne unerwartetes Trimmen. Passphrase-POSTs und Vergleichsvariablen sind für Django-Fehlerberichte als sensibel markiert.
- Bestehende CSP blockiert die eigenen Template-Skripte nicht mehr: frische Request-Nonces statt pauschalem `unsafe-inline`, Event-Listener statt Inline-Handler. Die Backtest-Laufzeitschätzung verwendet localeunabhängige JavaScript-Zahlen.
- Fehlende Migration `0014_configuration_leverage_trade_direction` ergänzt die bereits im Modell vorhandenen Felder. Veraltete Formular-Testdaten korrigiert; kein neuer Trading-Algorithmus.
- Render-Vorlagen referenzieren den Integrationsbranch `tbot.local` statt eines veralteten Arbeitsbranches. Redundante Ignore-Regeln und unbenutzte Test-Imports entfernt.

### Tests und Dokumentation

- Startmatrix für lokale Entwicklung/Produktion/Render, Generierung und Logging, Gate/CSRF/Unicode, Rotation und WebSocket-Autorisierung, Rate-Limit-Parallelität/Proxy-Spoofing, CSP-Nonces und Setup-Idempotenz regressionsgetestet.
- Beide READMEs, Env-Beispiele, Konfigurationstemplate, Handbuch/API-Zugriff, lokale Anleitung, FAQ, historische Review-Verweise und Prompt-Status aktualisiert. [Security-Review 2.4.4](security/SECURITY_REVIEW_2.4.4.md) enthält nach Priorität bewertete Befunde, False Positives, Testprotokoll und Prüfgrenzen.

### Upgrade-Hinweise

- Vor dem Deploy private `SECRET_KEY`/`PASSPHRASE` für **alle** App-Prozesse setzen und den Produktions-Gate aktiv lassen. Zuvor öffentliche Default-Secrets ersetzen. Nur lokale Entwicklung darf temporäre Werte aus der Konsole verwenden.
- `python manage.py migrate --noinput` ausführen. Das Docker-Entrypoint erledigt Migrationen beim Start; bestehende Konfigurationen erhalten `leverage=1` und `trade_direction=long`.
- Benutzer müssen den Gate nach dem Upgrade erneut freigeben. Signierschlüssel-Rotation invalidiert zusätzlich Login-Cookies. App-Secret-Rotation erfordert **kein Löschen von DB-Volumes**.
- Proxy-Peer-IPs/CIDRs für `RATE_LIMIT_TRUSTED_PROXIES` prüfen. Ohne Allowlist teilen sich Proxy-Clients dessen IP-Limit. Mehrere Web-Prozesse/Instanzen benötigen ein zusätzliches gemeinsames Rate-Limit.

## [2.4.3] – 2026-09-07

### Sicherheitsfix: Content-Security-Policy (CSP)

- **Neue Abhängigkeit `django-csp==3.8`** (in `requirements.txt`) implementiert die Content-Security-Policy über die Django-Middleware.
- **CSPMiddleware** wurde in `MIDDLEWARE` nach der `SecurityMiddleware` registriert; die App `csp` ist in `INSTALLED_APPS`.
- **9 CSP-Direktiven** strikt auf lokale Ressourcen ausgerichtet: `CSP_DEFAULT_SRC = ("'self'",)`, `CSP_SCRIPT_SRC = ("'self'",)`, `CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")`, `CSP_IMG_SRC = ("'self'", "data:")`, `CSP_FONT_SRC`, `CSP_CONNECT_SRC`, `CSP_FRAME_ANCESTORS`, `CSP_BASE_URI`, `CSP_FORM_ACTION`. Es werden keine externen Domains erlaubt.
- **Zweck:** Verhindern von XSS-Angriffen über Inline-/externe Skripte trotz `mark_safe()`-Markdown-Rendering.
- **Tests:** Neu `trading/tests/test_csp.py` (12 Tests) validiert die Einstellungen, präsente CSP-Header in Responses und das Fehlen externer Domains.

## [2.4.2] – 2026-09-07

### Sicherheitsfix: ALLOWED_HOSTS-Wildcard entfernt

- Der Entwicklungspfad `if DEBUG: ALLOWED_HOSTS.append("*")` wurde durch eine **explizite Liste lokaler Hosts** ersetzt (`localhost`, `127.0.0.1`, `tbot.local`, `[::1]`).
- **Zweck:** Verhindert durch den Wildcard-Eintrag mögliche **Host-Header-Injection**, Cache-Poisoning und CSRF-Bypass in der lokalen Entwicklung.
- Der Quellcode-Scan stellt sicher, dass `"*"` nicht mehr bedingt ergänzt wird.
- **Tests:** Neu `trading/tests/test_settings.py` (6 Tests) prüft das Fehlen des Wildcards, die vorhandenen lokalen Hosts sowie einen Source-Scan gegen `append("*")`.

## [2.4.1] – 2026-09-07

### Sicherheitsfix: Rate-Limiting auf Auth-Endpunkte

- **Neue Middleware `trading.rate_limit.RateLimitMiddleware`** implementiert IP-basiertes Rate-Limiting auf Auth-Endpunkte (Login, Passphrase-Gate, Registrierung).
- **Regeln:** maximal 5 POST-Versuche pro IP innerhalb eines 15-Minuten-Fensters (900 Sekunden); bei Überschreitung HTTP `429` mit `Retry-After`-Header.
- Thread-sichere In-Memory-Speicherung über `threading.Lock`; `X-Forwarded-For` wird für Proxy-Setups berücksichtigt; GET-Anfragen werden nicht limitiert.
- Middleware ist in `MIDDLEWARE` nach `AuthenticationMiddleware` registriert (`trading.rate_limit.RateLimitMiddleware`).
- **Zweck:** Verhindert Brute-Force-Angriffe und Credential-Stuffing auf das Passphrase-Gate, Login und Registrierung.
- **Tests:** Neu `trading/tests/test_rate_limit.py` (11 Tests) deckt Limit, Retry-After, GET-Ausnahme, X-Forwarded-For, Counter-Reset, unabhängige IPs und einen Integrationstest ab.

## [2.4.0] – 2026-08-23

### Help-Seite, adaptive Backtests und geprüfte Marktvorlagen

- Der Hilfe-Link im Menü zeigt zuverlässig das gecachte Handbuch. Die Markdown-Kompilierung wird mit einem thread-sicheren Einmal-Cache geschützt; alle Fragment-Ziele der integrierten Hilfe wurden geprüft.
- Neues ausführliches Kapitel [`backtesting.md`](manual/BACKTESTING.md) mit Formeln, Templates, Ressourcenbudget, Laufzeitschätzung, Datenqualität und Risikohinweisen.
- Docker startet lokal ohne Passphrase-Gate und verwendet den Standard-Port `8369`. Compose übernimmt nun auch die ermittelten Speichergrenzen.
- `trading.resource_optimizer` liest CPU, RAM, cgroup-Limits und freien Speicher und leitet daraus sichere Preispunkt-, Raster- und Kombinationsgrenzen ab. Hard-Limits werden im Formular und im Worker erneut geprüft.
- Backtesting bietet Schnellprüfung, Ausgewogen und Feinoptimierung, eine variable Preispunktzahl, ein variables Kombinations-Hard-Limit und eine sichtbare Laufzeitschätzung.
- Der öffentliche Markt-Scanner erstellt pro Exchange bis zu fünf Gainer und Loser nach Volatilität, Volumen-Ausreißer, Orderbuch-Tiefe, Volumen/Marktkapitalisierung und konservativer Utility-Prüfung. Vier UI-Schieberegler steuern diese Schwellen; fehlende Fundamental-, Volumen- oder beidseitige Orderbuchdaten führen weiterhin zwingend zum Ausschluss.
- Binance lädt 24h-Ticker kompakt ohne überlange `symbols=[...]`-URL und validiert Spot- sowie aktive Perpetual-Futures-Symbole gegen die maßgeblichen Exchange-Kataloge. Bitunix nutzt die dokumentierten Spot-/Futures-Marktdatenpfade statt des nicht vorhandenen Spot-Ticker-Endpunkts.
- Backtest-Ergebnisse enthalten Profit pro Markt, Brutto-Gewinn/-Verlust, Gebühren, Profit-Faktor, maximalen Drawdown, durchschnittliche Trade-Dauer, zeitgestempelte Trades und Mark-to-Market-Equity-Kurven. Nutzergebundene Exporte stehen als A4-Querformat-PDF, eigenständiges HTML und erweitertes UTF-8-CSV bereit.
- Zusätzliche Python-, Django- und Shell-Tests decken Ressourcenheuristik, Cache, Marktfilter, Exchange-Adapter, Symbolkataloge, Backtest-Berichte, Exporte, Formulare und lokale Start-/Portkonfiguration ab.

## [2.3.2] – 2026-09-06

### Sicherheitsarchitektur: API-Schlüssel aus der Datenbank entfernt

- **API-Schlüssel werden nicht mehr im Django-Datenbankmodell gespeichert.** Die Felder `api_key` und `secret_key` wurden aus dem `Configuration`-Modell entfernt. Stattdessen werden sie als Umgebungsvariablen `EXCHANGE_API_KEY` und `EXCHANGE_SECRET_KEY` beim Container-Start injiziert.
- **Neues Feld `has_live_credentials`** (BooleanField) im `Configuration`-Modell signalisiert, ob Live-Handel aktiviert ist, ohne die tatsächlichen Schlüssel zu speichern.
- **Migration 0013** entfernt die alten Datenbankfelder und fügt `has_live_credentials` hinzu.
- **Management-Befehl `clear_api_keys`**: Einmaliger Befehl zum Bereinigen eventuell noch vorhandener Klartext-API-Schlüssel aus der Datenbank: `python manage.py clear_api_keys`
- **Formular aktualisiert**: `ConfigurationForm` enthält `api_key`/`secret_key` nicht mehr; dafür den Schalter `has_live_credentials`.
- **TradingBot aktualisiert**: Liest API-Schlüssel aus `os.environ.get("EXCHANGE_API_KEY")` und `os.environ.get("EXCHANGE_SECRET_KEY")` statt aus `self.config.api_key`.
- **Docker-Konfiguration aktualisiert**: `docker-compose.yml`, `.env.example` und `.env.local` enthalten die neuen Umgebungsvariablen.
- **Sicherheitsverbesserung**: API-Schlüssel existieren nun nur im Arbeitsspeicher des laufenden Prozesses und nie auf der Festplatte der Datenbank.

## [2.3.1] – 2026-08-22

### Indikator-Konsistenz, Tooltip-System und Behebung des /help/ 500-Fehlers

- **Fehlerbehebung `/help/` (500 Server Error):** `_render_manual` prüft nun robust Pfade (`docs/MANUAL.md` und `MANUAL.md`) mit Fallback. Das gerenderte HTML wird mittels `@lru_cache(maxsize=1)` im Speicher gehalten, sodass keine wiederkehrende CPU- oder I/O-Last entsteht.
- **Indikatoren-Mapping & einheitliche Benennung:**
  - `div_DVA_prev_NDA_threshold_buy` ist in Konfiguration und Dashboard jetzt präzise als **„Beschleunigung (DVA / prev NDA) – Kaufschwelle“** benannt und referenziert direkt die Parameter `Beschleunigung – von / bis / Schritt` (`acc_from`, `acc_to`, `acc_steps`) im Backtesting-Modul.
  - `deltadelta_threshold_buy` ist einheitlich als **„DeltaDelta (geglättetes Momentum) – Kaufschwelle“** benannt (Backtesting: `deltadelta_from`, `deltadelta_to`, `deltadelta_steps`).
  - `nda_threshold_buy` ist einheitlich als **„NDA (normalisierte Preisänderung) – Kaufschwelle“** benannt (Backtesting: `nda_from`, `nda_to`, `nda_steps`).
- **Interaktive Info- und Hover-Elemente:**
  - Jedes bearbeitbare Eingabefeld in Konfigurationsformularen, Dashboard, Backtesting, Login, Registrierung, Passphrase-Gate und Analyse verfügt über ein interaktives `ⓘ`-Symbol mit Bootstrap 5-Tooltip sowie `title`-Attributen.
  - Formular-Hilfetexte (`form-text`) und Feldlabels wurden umfassend überarbeitet und präzisiert.
- **Handbuch-Aktualisierung (`MANUAL.md`):**
  - Vollständige Zuordnungsmatrix zwischen Datenbankfeldern, Live-Formularen und Backtesting-Suchräumen.
  - Detaillierte mathematische Formeln und Bedeutungen für NDA, DVA, Beschleunigung, DeltaDelta und MVD.
- **Diagramm- und Ergebnis-Labels:**
  - Plotly-Chart-Traces und Backtesting-Ergebnis-Strings zeigen jetzt lesbare, aussagekräftige Indikatornamen an.
- **Testabdeckung:** Unit- und Integrationstests für Handbuch-Rendering, Formular-Labels, Help-Texte, Indikatoren und Backtest-Zuordnung erweitert.

## [2.3.0] – 2026-08-22

### Universelles Build-/Install-Skript und automatische Hardware-Optimierung

- `install.sh` ist das neue Distributions-agnostische Build- und Installationsskript. Es erkennt die Linux-Distribution über `/etc/os-release` (inkl. Fallbacks) und wählt automatisch den passenden Paketmanager: `apt` für Debian/Ubuntu, `pacman` für Arch/Manjaro, `dnf`/`yum` für RHEL/CentOS/Fedora/Rocky/Alma/Amazon Linux (mit automatischer EPEL-Aktivierung, wo Redis benötigt wird).
- Vollständiges Paket-Mapping für Redis, Python 3, Build-Toolchain, PostgreSQL-Header und die nativen WeasyPrint/Pango/Harfbuzz-Laufzeitbibliotheken pro Paketmanager.
- Redis wird als lokaler Service eingerichtet (systemd/OpenRC/`service`), beim Start aktiviert, per `redis-cli PING` auf Verfügbarkeit geprüft und bei Fehlschlag automatisch neu gestartet.
- Modi `--mode=auto|host|container` und Profile `--profile=full|runtime`; `auto` erkennt Docker/Podman/LXC-Container über `/.dockerenv`, `/proc/1/cgroup` und `/run/.containerenv`. Im Container-Modus wird kein sudo benötigt und keine Service-Verwaltung versucht.
- Privilegien-Prüfung: Nicht-root-Ausführung im Host-Modus re-exec'd sich selbst mit `sudo -E` und Array-Argumenten (kein Word-Splitting).
- `hardware-test.sh` vermisst CPU (`nproc`, `/proc/cpuinfo`, `uname -m`), RAM (`/proc/meminfo` mit `free`-Fallback) und Disk-I/O (64-MB-`dd`-Schreib-/Lesetest mit portabler `1M`-Blockgröße) und berechnet daraus eine Heuristik für Redis `maxmemory`/`maxmemory-policy`/`io-threads`, `BOT_DB_WORKERS`, `DB_POOL_SIZE`, `WEB_CONCURRENCY`, `CELERY_WORKER_MAX_MEMORY_PER_CHILD` sowie die Docker-Compose-CPU-Limits. Ausgaben in `env` (sourcbar), `json` und `text`.
- Render.com-Simulation ist in der erzeugten `config/local.env` standardmäßig deaktiviert (`RENDER=False`, `RENDER_SIMULATION=False`), kann über `--render-simulation` oder nachträglich in der Config aktiviert werden, ohne das lokale Setup zu beeinträchtigen.
- Docker-Integration: Beim `docker compose up --build` führt ein neuer One-Shot-`tuner`-Service (`docker/tuner-entrypoint.sh`) `hardware-test.sh` aus und schreibt `tuning.env` in ein gemeinsam genutztes Volume. Redis (`docker/redis-entrypoint.sh`) startet mit den automatisch berechneten Flags; Web/Worker/Beat-Sourcen (`docker/load-tuning.sh`) übernehmen die empfohlenen Werte. Alle App-Services warten auf `tuner: service_completed_successfully`.
- `Dockerfile` führt im Build `install.sh --mode=container --profile=runtime` aus, mit sicherem apt-Fallback für restriktive Build-Umgebungen.
- `config.template` dokumentiert alle optimierbaren Variablen mit Defaults und Wertebereichen.
- Test-Suite unter `tests/` mit Runner, Test-Helfer und Mock-Binaries: sechs Test-Dateien decken Distro-Erkennung (sechs `/etc/os-release`-Fixtures), Paketmanager-Aufrufe (apt/pacman/dnf/yum-Mocks), Redis-Verfügbarkeit, Hardware-Heuristik (1/4/16 GB-Szenarien) und Konfigurationsgenerierung (Mode 600, Idempotenz, Secret-Key-Länge, deaktivierte Render-Simulation) ab. `tests/distro_smoke_test.sh` baut optionale Debian/Arch/Fedora-Docker-Images, wenn Docker lokal verfügbar ist.
- ShellCheck 0.11.0 ist fehlerfrei für alle Produktiv- und Testskripte; `set -euo pipefail`, Input-Validierung, `mktemp`-Temporärdateien mit `trap`-Cleanup, keine Hardcoded-Credentials.
- `README.md` um Abschnitte zur automatischen Installation, Hardware-Optimierung, Render-Simulation, Docker-Tuning-Fluss und Test-Ausführung erweitert.
- `PEER_REVIEW.md` dokumentiert das Selbst-Review mit Checklisten für Sicherheit, Performance, Kompatibilität und Code-Qualität sowie das vollständige Paket-Mapping.
## [2.3.0] – 2026-08-21

### Distributions- und hardwareunabhängiges Local Setup

- Automatischer Systeminstaller für apt, pacman, dnf/yum, zypper und apk einschließlich Debian/Ubuntu, Arch, Fedora/RHEL-Derivate, openSUSE und Alpine.
- Native Architekturerkennung für amd64, arm64, arm/v7, ppc64le und s390x.
- Ein-Schritt-Setup `scripts/setup_local.sh` installiert bei Bedarf Docker, führt Hardwaretests durch, baut Images und startet den isolierten Stack.
- Hardwareprobe misst CPU-Hashrate, RAM, freien Datenträger und fsync-Schreibrate; daraus werden CPU-/RAM-Grenzen, Redis-Maxmemory und PostgreSQL-Cachewerte generiert.
- Render-Free-Simulation ist standardmäßig deaktiviert und nur über `--render-free-simulation` aktivierbar.
- Compose-PostgreSQL erhält begrenzte Verbindungen und hardwareabhängige Cacheparameter; Redis läuft mit hardwareabhängigem `maxmemory`.
- Sicherheitsmodus 0600 für generierte `.env.local`, bestehende Secrets werden beim Retuning beibehalten.
- Dry-Run-Tests für alle Paketmanager-Familien und deterministische Unit-Tests für Hardwareprofile ergänzt.

## [2.2.0] – 2026-08-21

### Produktionsreifes lokales Backtesting-Setup

- `docker-compose.yml` mit getrennten Services für Daphne/Web/Bot, Celery-Backtest-Worker, Redis und PostgreSQL; optionaler Beat-Scheduler über Compose-Profil.
- `.env.docker.example`, lokale Resource-Limits und optional `WEB_CPUS=0.10` zur Render-Free-Simulation.
- Zentrale `celery_config.py`: Queue `backtest`, Concurrency 1, Prefetch 1, Child-Recycling, 384-MB-Limit, Soft-/Hard-Limits, Late ACK und Worker-Lost-Requeue.
- Redis-Prioritäten: zukünftige Bot-/Default-Tasks Priorität 9, Backtests Priorität 0.
- Worker-Status mit Redis-/Ping-Prüfung, Fünf-Sekunden-Cache und sicherem lokalen Fallback.
- Authentifizierter `/api/backtesting/status/`-Endpoint mit Workerstatus sowie Web-Heartbeat, Peak-RSS, Threadzahl und Scheduler-Lag.
- Backtest-Resultate enthalten Dauer, Peak-RSS, Kombinationen und Preispunktmetriken; strukturierte `event=backtest.*`-Logs.
- Kooperative Pause/Cancel-DB-Prüfung auf ungefähr 100 Checks pro Task gedrosselt.
- `LOCAL_DEVELOPMENT.md`, Worker-/Beat-Entrypoints und vollständige Start-, Ausfall- und Verifikationsanleitung.

## [2.1.0] – 2026-08-21

### Lokale Analyse, modernes UI und isolierte Backtesting-Architektur

- Analyse verwendet lokale DataLogs statt Binance-REST-OHLCV. Damit entstehen keine Analyse-API-Requests, kein Request-Weight und kein HTTP-418-Ban; SMA-5/15 wird aus lokalen Zeit-Buckets berechnet.
- Navigation vollständig modernisiert; Backtesting ist direkt im Hauptmenü erreichbar, Hilfe sitzt rechts neben Benutzer und Logout.
- Hilfeseite erhielt kontrastreiche Pygments-Codehervorhebung, bessere Typografie, Spacing, Tabellen und Druckansicht.
- Backtesting-Auswahlseite für alle Konfigurationen ergänzt.
- Backtesting-Formular um benannte Indikatorbereiche, Beschreibungen, Trade-Betrag, Take Profit, Stop Loss, Gebühr, Preispunkt-Limit, Live-Kombinationszähler und ausklappbare Hilfe erweitert.
- Machbarkeitsstudie dokumentiert: Render Free kann harte Prozessisolation nicht garantieren. Lokale Backtests sind dort deaktiviert; Produktion nutzt Redis plus separaten Celery-Worker.
- Celery Queue `backtest`, Concurrency 1, Prefetch 1, Child-Recycling, 384-MB-Limit, Soft-/Hard-Time-Limits und Graceful Degradation konfiguriert.
- Ressourcen-Probe in separatem Prozess: 100 Kandidaten × 5.000 Punkte, 17,41 MB Peak-RSS, 0,267 s, stabiler 20,21-ms-Eltern-Heartbeat.
- `BACKTESTING_STUDY.md`, `render.worker.example.yaml` und `scripts/backtest_resource_probe.py` hinzugefügt.

## [2.0.4] – 2026-08-21

### Connection-Pool-Fix und autonomer Botbetrieb

- Root Cause bestätigt: Free-Postgres war nicht primär wegen DNS offline, sondern durch zu viele parallele Client-Verbindungen (`remaining connection slots are reserved for SUPERUSER`).
- Sämtliche Bot-ORM-Aufrufe laufen über einen eigenen Executor mit standardmäßig genau einem Worker. Direkte Verbindungen verwenden `CONN_MAX_AGE=0` und werden nicht mehr in vielen Thread-Locals festgehalten.
- Ein optionales `DATABASE_POOL_URL` wird auf bezahlten Render-Datenbanken bevorzugt; Free-Postgres unterstützt Render-PgBouncer nicht und bleibt deshalb bewusst bei streng begrenzten Direktverbindungen.
- Lokale Backtests werden serialisiert, um weitere parallele DB-Verbindungen und Free-Tier-Last zu vermeiden.
- Der ungeprüfte automatisch abgeleitete externe Host-Fallback wurde entfernt; ein Fallback ist nur noch explizit konfigurierbar.
- Bei DB-Ausfall laufen Marktdaten und Strategie mit der letzten validierten Konfiguration weiter. Nicht speicherbare TradingLogs werden bis zur Recovery geordnet im RAM gepuffert und anschließend atomar nachgeschrieben.
- Neue Hilfe-Seite `/help/` rendert das vollständige `MANUAL.md` mit Inhaltsverzeichnis, Tabellen, Codeblöcken und Druckansicht.
- Manual um Connection-Pool-Diagnose, autonomen Hintergrundbetrieb und Grenzen des RAM-Journals erweitert.

## [2.0.3] – 2026-08-20

### DB-Circuit-Breaker und Free-Tier-Lastreduktion

- Nach fünf koordinierten Fehlversuchen öffnet ein globaler DB-Circuit-Breaker für fünf Minuten. Wartende Bot-Operationen brechen sofort ab, statt nacheinander neue lange Reconnect-Serien zu starten.
- DB-Ausfallmeldungen aus HTTP-Middleware und Bot-Threads werden zeitlich gedrosselt.
- Konfigurationen werden im Bot nur noch alle 30 Sekunden neu aus PostgreSQL geladen statt in jedem Marktzyklus.
- DataLogs werden pro Symbol standardmäßig höchstens alle 10 Sekunden persistiert; die Trading-Auswertung läuft weiterhin im konfigurierten Intervall.
- Dashboard-Polling wurde auf 10 Sekunden reduziert. Dies senkt Query-, Schreib- und Netzwerkdruck auf Free-Postgres erheblich.
- Die externe PostgreSQL-Verbindung bleibt ein Fallback, kann aber einen tatsächlich gestoppten/defekten Datastore naturgemäß nicht ersetzen.

## [2.0.2] – 2026-08-20

### Datenbank-Failover, Request-Circuit-Breaker und Kontostandskorrektur

- PostgreSQL nutzt eine libpq-Hostliste: Render-Private-DNS bleibt primär, der TLS-geschützte externe Frankfurt-Hostname dient als automatischer Fallback.
- Signierte Cookie-Sessions entkoppeln Passphrase und Login-Sitzung von kurzfristigen DB-DNS-Störungen.
- Dashboard-Requests pausieren nach DB-503 lokal mit exponentiellem Backoff; nur ein Recovery-Probe-Request wird zugelassen. Dadurch endet die API-503-Dauerschleife.
- Gleichzeitige Bot-DB-Reconnects werden pro Prozess koordiniert.
- Der verfügbare Kontostand zieht offene Positionen und Kaufgebühren sofort ab. Zusätzlich zeigt die UI gebundenes Kapital, Gesamtequity, unrealisierten P/L und Anzahl offener Positionen.
- Neue TradingLogs speichern nach einem Buy den korrekten Cash-Snapshot.
- Reports enthalten nun vollständige Konfiguration, Cash-/Equity-Daten und offene Positionen; Exportbuttons und zentrale UI-Felder erhielten Hover-Erklärungen.
- Der angemeldete Benutzername bleibt in der Navigation sichtbar.

## [2.0.1] – 2026-08-20

### Render-Postgres-Verfügbarkeits-Hotfix

- Kurzzeitige `connection refused`-/DNS-Ausfälle liefern im Web statt eines internen 500-Fehlers eine verständliche HTTP-503-Seite beziehungsweise JSON-Antwort mit `Retry-After`.
- Passphrase-/Session-Zugriffe behandeln einen Datenbankausfall explizit; `/health/` bleibt unabhängig erreichbar.
- Bot-Reconnects werden pro Prozess koordiniert, sodass mehrere aktive Bots PostgreSQL nach einem Ausfall nicht gleichzeitig mit parallelen Reconnect-Schleifen belasten.
- Die bereits vorhandenen libpq-Keepalives, Connect-Timeouts und exponentiellen Reconnects bleiben aktiv.

## [2.0.0] – 2026-08-20

### Major-Update: Reporting, Betriebssicherheit und Exchange-UX

#### Hinzugefügt

- Aussagekräftige PDF-Dateinamen im Format `username_exchange_config-id_timestamp.pdf`.
- Downloadbare HTML- und CSV-Trading-Reports mit identischem Namensschema.
- Serverseitige Trading-Log-Pagination mit exakt 100 Einträgen pro Seite.
- Kill-Switch zum sofortigen Schließen aller offenen Paper-Positionen mit frisch abgerufenen Marktpreisen, doppelter Browserbestätigung und serverseitiger Doppelbestätigung.
- Deutliche rote Hervorhebung fehlerhafter Konfigurationsfelder inklusive feldbezogener Fehlermeldungen.
- Bitunix-Integration für öffentliche Spot- und Futures-Marktdaten. Futures-Ticker werden gebündelt abgerufen; bei fehlender/unverfügbarer API wird die Konfiguration sicher abgelehnt und kein Request-Loop gestartet.
- Kontextabhängige Symbol-Autovervollständigung für Exchange sowie Spot/Futures. Vorschläge sind zwischengespeichert und nur beratend; beim Speichern bleibt die Live-Validierung verbindlich.
- `CHANGELOG.md`, `MANUAL.md` und maschinenlesbare `VERSION`.
- Tests für Reportexporte, Pagination, Kill-Switch, Bitunix, Autocomplete und Formularfehler.

#### Geändert

- Dashboard-Aktionsbereich konsolidiert: PDF, HTML, CSV, Kill-Switch und Log-Reset sind klar gruppiert.
- Trading-Log wird neueste-zuerst dargestellt und belastet Browser/Server nicht mehr mit der gesamten Historie.
- Konfigurationsformulare verwenden ein gemeinsames, wartbares Template und Bootstrap-Validierungsstile.
- Symbolkataloge werden für 15 Minuten pro Exchange/Markt gecacht, um API-Last zu begrenzen.

#### Sicherheit

- Alle Exporte, Symbolvorschläge und Kill-Switch-Aufrufe sind authentifiziert und benutzerbezogen autorisiert.
- Kill-Switch und weitere Zustandsänderungen bleiben POST-/CSRF-geschützt.
- Dateinamenbestandteile werden gegen problematische Zeichen bereinigt.
- CSV wird streamend erzeugt und skaliert auch bei großen Logbeständen.

## [1.3.0] – 2026-08-20

### Stabilität von Datenbank und Marktdaten

- Persistenter Binance-`miniTicker`-WebSocket statt REST-Polling; dadurch kein REST-Request-Weight und keine Verlängerung von HTTP-418-Bans.
- Automatische WebSocket-Wiederverbindung mit Backoff, Endpunkt-Fallback und proaktivem 23-Stunden-Reconnect.
- Exakter Binance-`banned until`-Timestamp wird bei HTTP-Fallbackfehlern respektiert.
- BitMart-V3-Marktdatenadapter als Ersatz für den aus CCXT entfernten BitMart-Adapter.
- Live-Symbolvalidierung beim Speichern und Aktivieren einer Konfiguration.
- PostgreSQL-Reconnect mit DNS-Erkennung, Jitter, Keepalive und `wait_for_database` beim Containerstart.
- Erweitertes Fehler-Log mit Schweregrad, Exception-Typ, technischen Details, Filtern, Pagination und Erledigt-Status.

## [1.2.0] – 2026-08-19

### Trading- und Backtesting-Korrekturen

- Gebühren werden beim Kauf und Verkauf korrekt berücksichtigt.
- Verkaufsmenge entspricht der tatsächlich gekauften Menge.
- Offene Paper-Positionen und realisierter P/L werden nach Neustarts aus dem Trading-Log wiederhergestellt.
- Kapital, Equity, Drawdown, Sharpe, Win-Rate, Profit-Faktor und Risk/Reward korrigiert.
- Stop-Loss im Backtesting ergänzt; Verkäufe verwenden den tatsächlichen Kurs statt eines festen Take-Profit-Werts.
- Backtests akzeptieren Decimal-/Float-Schwellenwerte konsistent, begrenzen Kombinationen und speichern nur das beste Ergebnis pro Symbol.
- Pause/Resume/Cancel sowie unterbrochene lokale Tasks stabilisiert.
- Daten- und Fehler-Log-Wachstum begrenzt bzw. dedupliziert.

## [1.1.0] – 2026-08-19

### Render-Deployment

- Docker-Deployment mit unprivilegiertem Benutzer und WeasyPrint-Systembibliotheken.
- Eindeutiges `docker-entrypoint.sh`: Datenbank abwarten, migrieren, Daphne per `exec` starten.
- Render Blueprint mit PostgreSQL, generierten Secrets, Frankfurt-Region und öffentlichem Health-Check.
- WhiteNoise/Manifest-Staticfiles, sichere Proxy-/Cookie-/HSTS-Einstellungen und ASGI-WebSocket-Originprüfung.
- In-Memory-Fallbacks für Channels/Celery auf einer kostenlosen Einzelinstanz.

## [1.0.0] – 2026-08-18

### Initiale Plattform

- Django-Anwendung mit Benutzerregistrierung, Login und Passphrase-Gate.
- Konfigurierbarer Paper-Trading-Bot für mehrere Exchanges und Symbole.
- Dashboard, Trading- und Daten-Logs, Plotly-Charts und PDF-Reports.
- Parametrisierte Backtests mit Fortschritts-WebSocket.
- Technische Strategieindikatoren: DA, NDA, vorherige NDA, DVA/Beschleunigung, DeltaDelta und MVD.

[2.0.0]: https://github.com/Kryschuuu/t-bot/compare/4b38bd9...HEAD
[1.3.0]: https://github.com/Kryschuuu/t-bot/commit/4b38bd9
