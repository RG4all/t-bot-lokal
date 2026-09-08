# SEC-06 – X-Content-Type-Options: nosniff

- **Finding:** `MissingContentTypeNosniff` (Prompt 6 / Security-Audit §2.6)
- **Status:** **Fixed** (behoben und in 2.4.15 zuletzt nachgeprüft)
- **Release:** **2.4.6** · **Nachprüfung:** 2026-09-07, zuletzt 2026-09-08 (2.4.15)
- **Ursprüngliche Einstufung:** MEDIUM – Security; nach Prüfung explizite Konfigurationshärtung, kein nachgewiesener fehlender Header im bisherigen Django-Standard-Stack.
- **Fix-Commit:** [`a2e6c4bd18fbcf1649efa1af6ec1822501f93958`](https://github.com/RG4all/t-bot-lokal/commit/a2e6c4bd18fbcf1649efa1af6ec1822501f93958) – `fix(security): enable X-Content-Type-Options nosniff`

**Zuordnung des Dateinamens:** Diese im Auftrag genannte Datei war im Ausgangsstand nicht vorhanden. Der Vorlagenname `rule-lifecycle-authz` passt nicht zum Finding in Prompt 6. Dieses Dokument verfolgt ausdrücklich den nosniff-Fix und die verlangte SEC-05-Nachprüfung; es erklärt keinen separaten Regel-Lifecycle-/Autorisierungsbefund für behoben. Die ursprünglichen Befunde stehen in [`SECURITY_AUDIT.md`](SECURITY_AUDIT.md) §2.5/§2.6 und [`ARENA_AI_PROMPTS.md`](ARENA_AI_PROMPTS.md) Prompt 5/6.

## Befund und Root Cause

`trading_bot_project/settings.py` enthielt keine explizite Einstellung `SECURE_CONTENT_TYPE_NOSNIFF`. Das Projekt verließ sich auf den Framework-Default. Die bereits an erster Stelle registrierte `django.middleware.security.SecurityMiddleware` verwendet in der gepinnten Version **Django 5.2.17** bereits standardmäßig `True` und lieferte `X-Content-Type-Options: nosniff` deshalb auch vor dem Patch aus.

Die pauschale Aussage im historischen Prompt, Django aktiviere diesen Header nicht standardmäßig, ist für diese Version falsch. Bestätigt und behoben ist die fehlende explizite Projektkonfiguration. Ohne wirksames nosniff könnten Browser ungeeignete MIME-Typen insbesondere bei Skript-/Stylesheet-Ressourcen interpretieren; ein konkreter vorbestehender Exploit wurde hier nicht nachgewiesen.

## Fix und Umfang

```python
# Nach dem umgebungsabhängigen Security-Header-Block:
SECURE_CONTENT_TYPE_NOSNIFF = True
```

- Die Einstellung gilt unabhängig von DEBUG und Render, auch bei lokalem HTTP.
- Die bestehende SecurityMiddleware bleibt äußerste Middleware. Keine neue Middleware, Runtime-Abhängigkeit, API-Änderung oder Migration.
- Die Tests prüfen nicht nur erfolgreiche HTML-/JSON-Views, sondern auch Redirects, Fehlerantworten, Streaming/Downloads und von WhiteNoise früh beantwortete statische Requests.
- MIME-Typ und Download-Disposition bleiben erhalten; vom Client gesendete Accept-/Header-Werte können den Response-Header nicht abschalten.
- `VERSION` ist die zentrale Versionsquelle für Settings, UI und `/health/`; die Release-Version ist 2.4.6. `pyproject.toml` enthält keine zusätzliche Paketversion.

## SEC-05-Nachprüfung

**Status: Fixed seit 2.4.5**, in 2.4.6 erneut geprüft. Ursprünglicher Fix: [PR #13](https://github.com/RG4all/t-bot-lokal/pull/13).

`CSRF_COOKIE_HTTPONLY = True` ist weiterhin explizit und DEBUG-/Render-unabhängig gesetzt. Die eigenen Dashboard-Skripte lesen den CSRF-Token aus dem Formularfeld, nicht aus `document.cookie`. Der bisher entsprechend benannte Login-Test las allerdings den Cookie-Wert: Er verwendet jetzt tatsächlich den maskierten DOM-Token, einschließlich der vorgeschalteten Gate-Freigabe.

Die jetzt **11 CSRF-Cookie-Tests** prüfen die expliziten Settings, serialisiertes `Set-Cookie` mit HttpOnly, die Secure-Flags aus real geladenen lokalen/Produktions-/Render-Settings, Token-Auslieferung und erfolgreichen Login. POSTs ohne Token, ohne passendes Cookie, mit dem Token eines anderen Clients oder mit einer fremden Origin werden bei erzwungener CSRF-Prüfung abgelehnt.

**Nachprüfung 2.4.15:** Auslöser war das Indikator-Refactoring (Prompt 18 / Audit §4.3, [CODE-18](CODE-18-indicator-dedup.md)); die Einstellungen blieben unverändert. `CSRF_COOKIE_HTTPONLY = True` steht weiterhin explizit und DEBUG-/Render-unabhängig außerhalb des nicht-DEBUG-Blocks (`trading_bot_project/settings.py`), und alle 11 CSRF-Cookie-Tests bestehen zusammen mit den 30 neuen Indikator-Tests (279 Tests gesamt) in [PR #23](https://github.com/RG4all/t-bot-lokal/pull/23). SEC-05 bleibt **Fixed**; der hier beschriebene nosniff-Befund bleibt ebenfalls **Fixed** (Fix-Commit [`a2e6c4bd`](https://github.com/RG4all/t-bot-lokal/commit/a2e6c4bd18fbcf1649efa1af6ec1822501f93958), Release 2.4.6).

**Wichtige Abgrenzung:** HttpOnly verhindert nur das direkte Lesen des Cookies. Der Formular-Token bleibt für Skripte derselben Origin im DOM sichtbar; XSS kann weiterhin authentifizierte Requests ausführen. Die frühere Zusicherung, das Token sei damit generell vor XSS-Exfiltration geschützt, wurde in Kommentaren, READMEs und Security-Dokumentation korrigiert. CSP, korrektes Escaping und die CSRF-/Origin-Prüfungen bleiben erforderlich.

## Testnachweis

### Rot → grün und Negativkontrollen

- Ausgangsstand `35da7ad`: Die neue nosniff-Settings-Matrix scheiterte in **allen vier DEBUG-/Render-Kombinationen**, weil die explizite Einstellung fehlte. Die Response-Prüfungen waren wegen des Django-Defaults bereits grün; ein fehlender Header wird nicht vorgetäuscht.
- Mit dem Fix bestehen alle **10 nosniff-Tests** und **11 CSRF-Cookie-Tests**.
- Eine ausschließlich im Testprozess gesetzte Mutation `SECURE_CONTENT_TYPE_NOSNIFF=False` erzeugte 20 fehlgeschlagene Header-Prüfungen; `CSRF_COOKIE_HTTPONLY=False` ließ den Cookie-Header-Test scheitern. Beide Mutationen wurden beendet und sind nicht Bestandteil der Projektkonfiguration.

### Lokale Validierung

Python **3.11.2**, unveränderte Runtime-Pins (Django **5.2.17**), Ruff **0.16.6**, ShellCheck **0.11.0**:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
python manage.py test trading.tests.test_content_type_nosniff trading.tests.test_csrf_cookie --noinput
python manage.py test --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
ruff check .
shellcheck install.sh hardware-test.sh docker-entrypoint.sh docker/*.sh scripts/*.sh tests/*.sh
bash tests/run_tests.sh
python -m pip check
```

- **154 Django-/Python-Tests**, darunter synchrone/asynchrone Header-Tests sowie 301/302/304, 400/403/404/405/429/500/503: bestanden.
- **7/7 Shell-Testgruppen**, Ruff, ShellCheck, Systemcheck, Migrationsprüfung und `pip check`: bestanden.
- Mit isolierten Test-Secrets, `DEBUG=False` und `RENDER=True`: `check --deploy --fail-level WARNING`, Migrationen auf frischer SQLite-DB und `collectstatic` erfolgreich. Keine produktiven Daten verwendet.
- Zusätzlicher Django-Client-Smoke-Test mit tatsächlich geladenen Produktions-/Render-Settings: `/health/`, `/gate/`, `/dashboard/`-Redirect und `/static/css/custom.css` liefern nosniff; `/health/` meldet 2.4.6 und das CSRF-Cookie trägt HttpOnly und Secure.

### Auslieferung und Prüfgrenzen

Auf ausdrückliche Freigabe erfolgt dieser Release mit den oben dokumentierten **lokalen Testnachweisen, ohne Einführung eines neuen GitHub-Actions-Testworkflows**. Der zunächst vorgesehene Workflow wurde vor der Auslieferung entfernt, da die GitHub-App keine Berechtigung für Workflow-Änderungen hat. Es wird kein erfolgreicher GitHub-Test-CI-Lauf behauptet. Die vorhandene automatische Dependency-Graph-Integration ersetzt keine Anwendungstests.

Lokal fehlen Docker und Pango; daher kein Container-/Distro- oder nativer PDF-Nachweis. Getestet wurde Python 3.11.2, nicht der Docker-Zielruntime 3.12.7. Die Header-Prüfungen sind HTTP-Response-/Middleware-Tests, kein Browser-End-to-End-Test oder Test eines produktiven Reverse-Proxys. Separat von Django erzeugte Proxy-/CDN-Antworten und der vorgeschaltete StaticFilesHandler des lokalen Django-Entwicklungsservers liegen außerhalb der Middleware; der getestete Produktionspfad für statische Dateien ist WhiteNoise. Vor produktiver Freigabe im Zielimage testen und nach dem Deploy den Header über den tatsächlichen öffentlichen HTTPS-Endpunkt prüfen; separat ausgelieferte Dateien/Fehlerantworten am Proxy entsprechend konfigurieren. Kein vollständiges neues Sicherheitsaudit der gesamten Anwendung.
