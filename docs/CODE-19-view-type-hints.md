# CODE-19 – Fehlende Type-Hints in den Views

- **Finding:** `MissingTypeHintsViews` (Prompt 19 / Security-Audit §4.4)
- **Status:** **Fixed** (behoben und nachgeprüft)
- **Release:** **2.4.16** · **Datum:** 2026-09-08
- **Einstufung:** LOW – Tech Debt (kein Sicherheitsbefund; die dabei gefundenen
  Härtungen sind unten getrennt ausgewiesen und keine nachgewiesenen Exploits)
- **Geprüfter Ausgangsstand:** 2.4.15 / `3c6eec2898ac0246ecd5a3ef5402c7765c14d47f`
- **Fix-Commit:** [`8089c34`](https://github.com/RG4all/t-bot-lokal/commit/8089c34a0d70d68784a2cf7570b61c67277f3739) – `refactor(views): add type hints and docstrings`
- **Dokumentation des Releases:** [CHANGELOG 2.4.16](CHANGELOG.md#2416--2026-09-08)

## Befund und Root Cause

`trading/views.py` (1.830 Zeilen, 65 Funktionen) enthielt **keine einzige
Type-Annotation**. Weder Views noch Hilfsfunktionen deklarierten Parameter- oder
Rückgabetypen, und bis auf sechs Ausnahmen fehlten auch die Docstrings.

Die Wurzel ist nicht die fehlende Annotation an sich, sondern ihre Folge: Ohne
Annotationen kann **kein statischer Prüfer** die Datei sinnvoll analysieren. Ein
falscher Rückgabetyp, ein `None` in einem Rechenpfad oder ein anonymer Benutzer
in einem ORM-Filter fällt erst zur Laufzeit auf – im Fall der Views also erst
im Request eines echten Nutzers. Für ein Modul, das Portfoliostände, fremde
Konfigurationen und Report-Downloads autorisiert, ist das die eigentliche
technische Schuld.

Das bestätigt sich an drei beim Annotieren aufgedeckten Stellen, die alle
**vorher latent und nicht statisch auffindbar** waren:

| Stelle | Altzustand | Wirkung |
|---|---|---|
| 26 ORM-Filter (`user=request.user`, `configuration__user=request.user`) | `request.user` ist `User \| AnonymousUser` | Ohne `@login_required` würde mit `AnonymousUser` weitergefiltert statt abgewiesen |
| `passphrase_gate_view` | `constant_time_compare(submitted, settings.PASSPHRASE)` | Bei leerem Secret ist `constant_time_compare("", "")` **True** – das Gate hätte sich mit leerer Eingabe geöffnet |
| `_equity_svg` | `float(point.get("equity"))` | `None` löste einen `TypeError` aus, der nur vom umgebenden `except` aufgefangen wurde |

Keiner dieser Pfade ist im ausgelieferten Stand ausnutzbar: Alle betroffenen
Views tragen `@login_required`, und `trading_bot_project/settings.py` erzwingt
seit 2.4.4 eine gesetzte `PASSPHRASE` (auf Render und bei `DEBUG=False` als
harter `RuntimeError`, lokal als generiertes Zufalls-Secret). Es wird deshalb
**keine behobene Schwachstelle behauptet**. Geschlossen wird der Weg dorthin:
eine spätere Änderung, die einen Decorator entfernt oder ein leeres Secret
durchreicht, scheitert jetzt an einer expliziten Prüfung statt still
weiterzulaufen.

## Fix und Umfang

### 1. Annotationen und Docstrings

Alle **65 Top-Level-Funktionen** in `trading/views.py` sind vollständig
annotiert – Parameter und Rückgabewert. Die in Prompt 19 ausdrücklich
geforderten Signaturen sind exakt umgesetzt:

```python
def _portfolio_snapshot(config: Configuration) -> dict[str, Any]: ...
def _realized_profit(config: Configuration) -> Decimal: ...
def _cash_flow(log: TradingLog) -> Decimal: ...
def _cash_series(logs: list[TradingLog], opening_cash: Decimal) -> list[dict[str, Any]]: ...
def calculate_performance_metrics(logs: list[TradingLog]) -> dict[str, float]: ...
def health_view(request: HttpRequest) -> JsonResponse: ...
def home(request: HttpRequest) -> HttpResponseRedirect: ...
def login_view(request: HttpRequest) -> HttpResponse: ...
def config_view(request: HttpRequest) -> HttpResponse: ...
def dashboard_view(request: HttpRequest) -> HttpResponse: ...
```

`list` und `dict` sind gegenüber der Prompt-Vorlage präzisiert
(`list[TradingLog]` statt `list`), weil ein nacktes `list` dem Prüfer keinen
Elementtyp liefert und damit den Zweck der Annotation verfehlt.

Alle **39 öffentlichen Views** haben einen Docstring; komplexe Views
dokumentieren zusätzlich `Args`/`Returns`/`Raises`. Rein mechanische
Ein-Zeilen-Docstrings sind bewusst kurz gehalten, die sicherheitsrelevanten
Stellen ausführlicher.

Der Decorator `no_cache_json` erhält `ParamSpec`/`TypeVar`, damit die Signatur
der dekorierten View erhalten bleibt und die zehn API-Endpunkte hinter dem
Decorator nicht auf `Any` zurückfallen.

### 2. Zentrale Benutzerauflösung

Neu ist `_authenticated_user(request) -> User`. Sie macht die bisher nur
implizite Voraussetzung „läuft hinter `@login_required`" explizit und sichert
sie ab:

```python
def _authenticated_user(request: HttpRequest) -> User:
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        raise PermissionDenied
    return user
```

Alle 26 ORM-Lookups verwenden jetzt diese Funktion. Für authentifizierte
Requests ändert sich das Verhalten **nicht**; ein anonymer Request endet mit
403 statt mit einem Filter auf `AnonymousUser`. Die bestehenden
`@login_required`-Decorator bleiben unverändert erhalten – die Prüfung ist eine
zweite Schicht, kein Ersatz.

### 3. Zwei geschlossene Randpfade

- **Gate:** `expected = settings.PASSPHRASE or ""` und `if expected and
  constant_time_compare(...)`. Ein leeres Secret kann keine Freigabe mehr
  erzeugen. Der zeitkonstante Vergleich bleibt unverändert.
- **Equity-SVG:** Ein Punkt ohne `equity`-Wert wird explizit übersprungen,
  statt über einen `TypeError` im `except` zu landen. Die Kurve wird aus den
  verbleibenden Punkten weiterhin gezeichnet.

### 4. Reproduzierbare Typprüfung

`pyproject.toml` erhält einen `[tool.mypy]`-Block (Ziel `trading/views.py`,
Plugin `mypy_django_plugin.main`, `django_settings_module`). Noch nicht
annotierte Module stehen in einem `ignore_errors`-Override: Ihre Signaturen
werden weiterhin gelesen, eigene Altbefunde aber nicht gemeldet. Der Scope
schrumpft mit jedem weiteren Annotationsschritt.

**mypy und django-stubs sind bewusst nicht in `requirements.txt`.** Sie sind
reine Entwicklungswerkzeuge; die Laufzeit-Pins des Containers bleiben
unverändert. Der zugehörige Test überspringt sich, wenn mypy fehlt.

Keine neue Laufzeit-Abhängigkeit, kein neues Architekturmuster, keine Settings-,
Model- oder Migrationsänderung, keine geänderte URL, Response oder API-Form.

## Testnachweis

### Rot → grün

Neu `trading/tests/test_view_type_hints.py` mit **30 Tests** in sechs Klassen.
Gegen den Ausgangsstand `3c6eec2` (nur `views.py` und `pyproject.toml`
zurückgerollt, Tests unverändert) scheitern sie mit **179 Failures und
42 Errors**; mit dem Fix sind alle 30 grün.

| Klasse | Tests | Prüft |
|---|---|---|
| `ViewSignatureAnnotationTests` | 7 | `typing`-Import, die zehn geforderten Signaturen exakt (zur Laufzeit via `get_type_hints`), vollständige Annotation aller öffentlichen Views und Hilfsfunktionen, `request: HttpRequest`, Response-Typen |
| `ViewDocstringTests` | 3 | Docstring-Pflicht und Mindestlänge, dokumentierte Snapshot-Schlüssel |
| `AuthenticatedUserResolutionTests` | 3 | Rückgabe des angemeldeten Benutzers, `PermissionDenied` bei anonym, kein roher `request.user`-Filter mehr im Quelltext |
| `PassphraseGateEmptySecretTests` | 2 | Leeres Secret öffnet das Gate nicht; korrekte Passphrase funktioniert weiter |
| `AnnotatedViewBehaviourTests` | 11 | Laufzeittypen der Antworten, fremde `config_id` bleibt 404 (Dashboard **und** JSON-API), Leerzustand, CSV-Streaming, Snapshot-/Kennzahlformen |
| `EquitySvgRobustnessTests` | 3 | `None`-Punkt wird übersprungen, leere Kurve, Symbol-Escaping |
| `MypyConfigurationTests` | 2 | Konfiguration in `pyproject.toml`; mypy-Lauf ohne Befund |

Ein Test (`test_no_top_level_function_is_left_unannotated`) prüft nicht nur die
gelistete Auswahl, sondern **jede** Top-Level-Funktion – neue Views ohne
Annotation fallen damit automatisch auf.

### Abgedeckte Angriffsvektoren

- Fremde `config_id` auf `/dashboard/` und `/api/info/<id>/` → **404**, keine
  Portfoliodaten eines anderen Benutzers.
- Anonymer Request in der Benutzerauflösung → **PermissionDenied**.
- Leere Gate-Passphrase bei leerem Secret → **keine Freigabe**, keine
  `passphrase_verified`-Session.
- Symbol mit Markup in der Equity-Kurve → escaped, kein `<script>` im SVG.

### Negativkontrolle der Typprüfung

Eine ausschließlich im Prüflauf gesetzte Mutation (`_cash_flow` mit
Rückgabetyp `str` statt `Decimal`) erzeugte **2 mypy-Fehler**
(`return-value`, `operator`). Die Prüfung ist damit nachweislich wirksam und
kein leerer Erfolgslauf. Die Mutation wurde zurückgenommen und ist nicht
Bestandteil des Repositorys.

### Angepasste Bestandstests

Zwei bestehende Quellcode-Tests suchten die **untypisierte** Signatur als
String und wurden dadurch rot. Beide prüfen jetzt signaturunabhängig weiter,
ohne an Schärfe zu verlieren:

- `test_cache_control.test_no_cache_json_decorator_exists`: Regex auf
  `def no_cache_json(… view_func`; der Parametername bleibt verbindlich.
- `test_session_invalidate.test_logout_view_source_calls_flush`: Der
  Funktionskopf wird per Regex gefunden, die Prüfung auf `logout(request)` und
  `request.session.flush()` im Block bleibt unverändert.

## SEC-05- und SEC-06-Nachprüfung

Beide Findings wurden für dieses Release erneut geprüft und bleiben **Fixed**.

- **SEC-05 (`CSRF_COOKIE_HTTPONLY`)** – Fixed seit 2.4.5. Die Einstellung steht
  weiterhin explizit und DEBUG-/Render-unabhängig in
  `trading_bot_project/settings.py:222`. Alle **11 CSRF-Cookie-Tests** bestehen.
- **SEC-06 (`SECURE_CONTENT_TYPE_NOSNIFF`)** – Fixed seit 2.4.6. Die Einstellung
  steht unverändert in `settings.py:166`. Alle **10 nosniff-Tests** bestehen.

Ergänzend ein Smoke-Test mit tatsächlich geladenen Produktions-/Render-Settings
(`DEBUG=False`, `RENDER=True`, isolierte Test-Secrets):

| Pfad | Ergebnis |
|---|---|
| `/health/` | 200, Version **2.4.16**, `X-Content-Type-Options: nosniff` |
| `/gate/` | 200, nosniff, CSRF-Cookie mit **HttpOnly** und **Secure** |
| `/dashboard/` | 302 → `/gate/?next=/dashboard/`, nosniff |
| `/static/css/custom.css` | 200, nosniff (WhiteNoise-Pfad) |

Dieses Release berührt weder Header- noch Cookie-Konfiguration; geändert sind
Annotationen, Docstrings und die oben beschriebenen drei Randpfade. Die
Autorisierung wurde nicht gelockert, sondern um eine zweite Prüfschicht
ergänzt.

## Lokale Validierung

**Python 3.11.2**, **Django 5.2.17**, SQLite-Testdatenbank, **Ruff 0.16.6**,
**mypy 1.18.2**, **django-stubs 5.2.7**; Laufzeitabhängigkeiten unverändert aus
`requirements.txt`. Test-Secrets ausschließlich im Environment gesetzt, keine
produktiven Zugangsdaten.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
python manage.py test trading.tests.test_view_type_hints --noinput
python manage.py test trading.tests.test_csrf_cookie --noinput            # SEC-05
python manage.py test trading.tests.test_content_type_nosniff --noinput   # SEC-06
python manage.py test --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
ruff check .
pip install mypy==1.18.2 django-stubs==5.2.7 && mypy
bash tests/run_tests.sh
python -m pip check
```

- **309 Django-/Python-Tests** (30 neue + 279 bestehende) bestanden.
- **mypy**: `Success: no issues found in 1 source file`.
- **8/8 Shell-Testgruppen**, Ruff, Systemcheck, Migrationsprüfung,
  `collectstatic` und `pip check` bestanden.
- Mit isolierten Test-Secrets, `DEBUG=False` und `RENDER=True`:
  `check --deploy --fail-level WARNING` ohne Befunde.

## CI und Auslieferung

**Ausdrücklich genehmigte Ausnahme, unverändert wie in 2.4.6–2.4.15:** Im
Repository ist **kein GitHub-Actions-Anwendungstestworkflow** versioniert; das
Verzeichnis `.github/` existiert nicht. Die GitHub-App hat keine Berechtigung
für Workflow-Änderungen, deshalb wird auch in diesem Release keiner eingeführt.
Die einzige dynamische Integration ist **Dependency Graph**
(`dynamic/dependabot/update-graph`) – sie ist ein Abhängigkeits-Scan und
**ersetzt keine Anwendungstests**.

Es wird **kein erfolgreicher GitHub-CI-Lauf behauptet.** Alle Nachweise sind die
oben dokumentierten lokalen Läufe. Der Stand der Check-Runs wurde nach dem Push
geprüft und ist im PR vermerkt.

Auslieferung auf `arena/01a081dc-t-bot-lokal`; der PR richtet sich an
`tbot.local`.

## Prüfgrenzen und Upgrade

- **Kein Sicherheitsfix behauptet.** Der Befund ist Tech Debt. Die drei
  geschlossenen Randpfade sind Härtungen gegen künftige Regressionen; im
  ausgelieferten Stand war keiner erreichbar (siehe Root Cause).
- **Typprüfung nur für `trading/views.py`.** Die übrigen Module bleiben
  unannotiert und sind über `ignore_errors` ausgenommen. mypy meldet dort
  weiterhin nichts – das ist eine bewusste Scope-Grenze, kein Freibrief. Beim
  Probelauf ohne Override wurden 6 Altbefunde in vier anderen Modulen sichtbar
  (u. a. fehlende Cache-Annotationen in `symbols.py` und `market_scanner.py`);
  sie gehören in einen eigenen Annotationsschritt.
- **Annotationen sind keine Laufzeitprüfung.** Python erzwingt sie nicht; sie
  wirken nur, wenn mypy/pyright tatsächlich ausgeführt wird. Deshalb der
  zusätzliche `ast`-basierte Test, der auch ohne installiertes mypy greift.
- **`pyright` wurde nicht ausgeführt** (Node in der Prüfumgebung nicht
  eingerichtet). Das Validierungskriterium ist über mypy erfüllt.
- **Laufzeitumgebung:** lokal Python 3.11.2, Zielruntime im Docker-Image 3.12.7.
  Docker steht in der Prüfumgebung nicht zur Verfügung – kein Containerlauf,
  kein WeasyPrint-/PDF-Nachweis (Pango fehlt). Es sind Einheiten- und
  Integrationstests, kein Browser-End-to-End-Test.
- Keine Migration, keine neue Umgebungsvariable, keine geänderte API. Nach dem
  Deploy `/health/` auf **2.4.16** prüfen. Bestehende Konfigurationen, laufende
  Bots und gespeicherte Backtests bleiben unverändert gültig.
