# SEC-09 – Permissions-Policy-Header für nicht benötigte Browser-APIs

- **Finding:** `MissingPermissionsPolicy` (Prompt 9 / Security-Audit §2.9)
- **Status:** **Fixed**
- **Release:** **2.4.9** · **Datum:** 2026-09-08
- **Ursprüngliche Einstufung:** LOW – Security
- **Betroffene Dateien:** `trading_bot_project/settings.py`, `trading/middleware.py`

## Befund und Root Cause

Im Ausgangsstand 2.4.8 wurde kein `Permissions-Policy`-Header gesetzt. Damit stand der Zugriff auf Browser-APIs wie Kamera, Mikrofon und Geolokation nicht unter der Kontrolle der Anwendung, obwohl das Projekt diese APIs an keiner Stelle benötigt.

`Permissions-Policy` ist der Nachfolger von `Feature-Policy`. Ohne einen solchen Header könnten Browser die APIs ausführen, falls eine Schwachstelle in den eigenen oder eingebetteten Skripten sie anfordert. Zwei Punkte waren zu beachten:

1. Django erzeugt **keinen** `Permissions-Policy`-Header. `SecurityMiddleware` setzt nur `X-Content-Type-Options`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`, HSTS und HTTPS-Redirects. Eine reine Einstellung `SECURE_PERMISSIONS_POLICY` (wie im Prompt vorgeschlagen) wäre daher wirkungslos.
2. django-csp (`CSPMiddleware`) ist bereits aktiv, behandelt aber ausschließlich die Content-Security-Policy und keine `Permissions-Policy`.

## Fix und Umfang

### Zentrale Einstellung in `trading_bot_project/settings.py`

```python
# Nach den anderen Security-Headern (nach SECURE_CONTENT_TYPE_NOSNIFF):
SECURE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
```

Kamera, Mikrofon und Geolokation werden damit für **alle** Origins deaktiviert (`feature=()`). Die Einstellung gilt unabhängig von `DEBUG` und Render.

### Middleware `PermissionsPolicyMiddleware` in `trading/middleware.py`

```python
class PermissionsPolicyMiddleware:
    """Setzt den Permissions-Policy-Header auf jeder HTTP-Antwort.

    Django erzeugt selbst keinen Permissions-Policy-Header. Die Middleware
    liest den Wert live aus settings.SECURE_PERMISSIONS_POLICY und beschränkt
    damit den Zugriff auf nicht benötigte Browser-APIs (Kamera, Mikrofon,
    Geolokation). ...
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = getattr(settings, "SECURE_PERMISSIONS_POLICY", "")
        if policy:
            response["Permissions-Policy"] = policy
        return response
```

- Die Middleware liest den Wert **live pro Request** aus den Settings (`getattr` mit Default), sodass sie auf die zentrale Konfiguration reagiert und ein leerer Wert die Antwort unverändert lässt.
- Sie ist **direkt nach der `SecurityMiddleware`** registriert. Damit erreicht ihre Response-Phase auch Antworten, die innere Middleware erzeugt: Fehlerantworten (400/403/404/405/429/500/503), Redirects (302), Streaming/Downloads und von WhiteNoise beantwortete statische Dateien.
- Keine neue Runtime-Abhängigkeit, keine Migration und keine API-/Nutzdatenänderung. Die vorhandene `SecurityMiddleware` bleibt äußerste Middleware.

### Abgrenzung

Der `301`-SSL-Redirect entsteht direkt in der äußersten `SecurityMiddleware` (`SECURE_SSL_REDIRECT`) und durchläuft daher keine innere Middleware; er trägt keinen `Permissions-Policy`-Header. Ein Redirect führt selbst keine Browser-APIs aus und liefert keinen Inhalt aus. Antworten eines vorgeschalteten Reverse-Proxys/CDNs, die nicht von Django erzeugt werden, benötigen dort bei Bedarf eine eigene Header-Konfiguration.

## SEC-05-Nachprüfung

**Status: Fixed seit 2.4.5**, in 2.4.6, 2.4.8 und erneut in 2.4.9 geprüft. Ursprünglicher Fix: [PR #13](https://github.com/RG4all/t-bot-lokal/pull/13).

`CSRF_COOKIE_HTTPONLY = True` bleibt unverändert und DEBUG-/Render-unabhängig aktiv. Das `csrftoken`-Cookie wird mit `HttpOnly` gesetzt; die App-Skripte beziehen das Token weiterhin aus dem `{% csrf_token %}`-Formularfeld. Alle **11 CSRF-Cookie-Tests** (`trading/tests/test_csrf_cookie.py`) laufen zusammen mit den neuen Permissions-Policy-Tests grün. Die Abgrenzung bleibt unverändert: `HttpOnly` verhindert nur den direkten Cookie-Zugriff, nicht das Lesen des DOM-Tokens bei XSS; CSP, Escaping und CSRF-/Origin-Prüfungen bleiben erforderlich.

## Testnachweis

### Rot → grün und Negativkontrollen

Neu `trading/tests/test_permissions_policy.py` mit 11 Tests:

- **Settings-Tests (2):** `SECURE_PERMISSIONS_POLICY` ist in allen vier DEBUG-/Render-Kombinationen explizit auf `camera=(), microphone=(), geolocation=()` gesetzt (das Projektmodul wird per `runpy` isoliert geladen); die Middleware ist direkt nach der `SecurityMiddleware` registriert.
- **Integrationstests (8, über den echten Middleware-Stack):** HTML- und JSON-Antworten (200) in DEBUG und Produktion, asynchrone Antworten, Gate-Redirects (302), Fehlerantworten (400/403/404/405/429/500/503) sowie WhiteNoise GET/HEAD/304-Antworten tragen den erwarteten `Permissions-Policy`-Header. Eine reine Test-View kann den Header nicht abschalten.
- **Negativkontrolle (1):** Bei leerem `SECURE_PERMISSIONS_POLICY` setzt die Middleware keinen Header – der Fix reagiert damit nachweislich auf die zentrale Einstellung.

Ausgangsstand vor dem Fix: Ohne die registrierte Middleware schlugen die neue Settings-Matrix sowie sämtliche Header-Asserts fehl (**17 fehlgeschlagene Assertions**). Mit dem Fix sind alle 11 Tests grün.

### Lokale Validierung

Python **3.11.2**, Django **5.2.17**, Ruff **0.16.6**:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False PASSPHRASE=test-passphrase SECRET_KEY=test-secret-key
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python manage.py test trading.tests.test_permissions_policy trading.tests.test_content_type_nosniff trading.tests.test_csrf_cookie trading.tests.test_session_invalidate trading.tests.test_cache_control --noinput
python manage.py collectstatic --noinput
python -m pip check
```

- **185 Django-/Python-Tests** (11 neue + 174 bestehende) bestanden.
- Systemcheck, Migrationsprüfung und `collectstatic` ohne Warnungen. `pip check` ohne Konflikte.
- Keine Code-Änderungen außerhalb `settings.py`, `middleware.py`, dem neuen Testmodul, Version, Changelog, READMEs/Handbuch, Security-Audit, Prompt-Dokumentation und diesem Finding-Dokument.

### Prüfgrenzen

- Die Header-Prüfungen sind HTTP-Response-/Middleware-Tests, kein Browser-End-to-End-Test. Ob ein Browser die Richtlinie tatsächlich durchsetzt, hängt von dessen Implementierung ab; die Richtlinie entspricht dem W3C-Standard für `Permissions-Policy`.
- Der `301`-SSL-Redirect und separat von Django erzeugte Proxy-/CDN-Antworten liegen außerhalb der Middleware (siehe Abgrenzung oben).
- Im Repository ist kein GitHub-Actions-Testworkflow versioniert; die Prüfungen wurden lokal ausgeführt. Die Grenzen der lokalen Prüfung entsprechen denen der vorherigen Security-Nachweise.

## Auslieferung

- **Commit-Message:** `fix(security): add Permissions-Policy header`
- Keine neuen Umgebungsvariablen oder Datenbankmigrationen. Kein neues Deployment-Geheimnis erforderlich.
- Nach dem Deploy: `Permissions-Policy`-Header über den öffentlichen HTTPS-Endpunkt (bzw. den vorgeschalteten Proxy) prüfen; für außerhalb von Django erzeugte Antworten eine entsprechende Proxy-/CDN-Konfiguration ergänzen.
