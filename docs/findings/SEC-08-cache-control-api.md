# SEC-08 – Cache-Control-Header für API-Endpunkte

- **Finding:** `MissingCacheControlAPI` (Prompt 8 / Security-Audit §2.8)
- **Status:** **Fixed**
- **Release:** **2.4.8** · **Datum:** 2026-09-07
- **Ursprüngliche Einstufung:** MEDIUM – Security
- **Betroffene Dateien:** `trading/views.py`

## Befund und Root Cause

Im Ausgangsstand 2.4.7 setzte keiner der JSON-API-Endpunkte Cache-Header:

```python
@login_required
@require_GET
def info_api(request, config_id):
    # ...
    return JsonResponse({...})
```

**Auswirkung:**

1. Browser und zwischengeschaltete Proxies/CDNs dürfen Antworten ohne `Cache-Control` heuristisch zwischenspeichern. Benutzerbezogene Handels-, Portfolio-, Log- und Marktdaten konnten dadurch gespeichert und später unabhängig vom Server-Zustand erneut ausgeliefert werden – etwa auf einem geteilten Client oder nach Logout, wenn der aktuelle Kontostand längst ein anderer ist.
2. Betroffen waren insbesondere `/api/info/`, `/api/bot/status/`, `/api/logs/`, `/api/data_logs/` und `/api/trades/`, aber auch die weiteren `/api/…`-Endpunkte (Symbolvorschläge, Marktchancen, Backtesting-Status/-Schätzung, Server-Ressourcen), die teils benutzerbezogene oder betriebliche Daten liefern.

## Fix und Umfang

### Decorator `no_cache_json` in `trading/views.py`

```python
def no_cache_json(view_func):
    """Setzt Cache-Control- und Pragma-Header für API-Responses.

    ...
    """
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        return response

    return wrapped
```

- `no-store` verbietet jede Speicherung der Antwort; `no-cache` erzwingt eine erneute Validierung; `must-revalidate, max-age=0` verhindern veraltete Kopien. `Pragma: no-cache` deckt zusätzlich ältere HTTP/1.0-Zwischenstufen ab.
- Der Decorator wird als **innerster** Decorator (unter `@login_required` und `@require_GET`) angewendet. Er erfasst damit jede von der View erzeugte Antwort – einschließlich Fehlerantworten wie 400/503, die sonst ebenfalls gecacht werden könnten.

### Anwendung auf alle zehn API-Views

| View | Endpunkt |
|---|---|
| `info_api` | `/api/info/<config_id>/` |
| `bot_status_api` | `/api/bot/status/` |
| `logs_api` | `/api/logs/<config_id>/` |
| `data_logs_api` | `/api/data_logs/` |
| `trades_api` | `/api/trades/` |
| `symbol_suggestions_api` | `/api/symbols/` |
| `market_opportunities_api` | `/api/market-opportunities/` (+ Alias `/api/top-movers/`) |
| `backtesting_status_api` | `/api/backtesting/status/` |
| `backtesting_estimate_api` | `/api/backtesting/estimate/` |
| `server_resources_api` | `/api/resources/` |

Keine neue Middleware, keine Runtime-Abhängigkeit, keine Migration und keine API-/Nutzdatenänderung.

## SEC-05-Nachprüfung

**Status: Fixed seit 2.4.5**, in 2.4.6 und erneut in 2.4.8 geprüft. Ursprünglicher Fix: [PR #13](https://github.com/RG4all/t-bot-lokal/pull/13).

`CSRF_COOKIE_HTTPONLY = True` bleibt unverändert und DEBUG-/Render-unabhängig aktiv. Das `csrftoken`-Cookie wird mit `HttpOnly` gesetzt; die App-Skripte beziehen das Token weiterhin aus dem `{% csrf_token %}`-Formularfeld. Alle **11 CSRF-Cookie-Tests** (`trading/tests/test_csrf_cookie.py`) laufen zusammen mit den neuen Cache-Control-Tests grün. Die Abgrenzung bleibt unverändert: `HttpOnly` verhindert nur den direkten Cookie-Zugriff, nicht das Lesen des DOM-Tokens bei XSS; CSP, Escaping und CSRF-/Origin-Prüfungen bleiben erforderlich.

## Testnachweis

### Rot → grün und Negativkontrollen

Neu `trading/tests/test_cache_control.py` mit 9 Tests:

- **Quellcode-Tests (3):** Der `no_cache_json`-Decorator ist in `trading/views.py` definiert, setzt die erwarteten Header-Werte und ist auf **alle zehn** API-Views angewendet (Decorator-Zeile unmittelbar über jeder `def`).
- **Integrationstests (6, über den echten Middleware-Stack):** Erfolgsantworten (200) aller Endpunkte sowie Fehlerantworten (400/503) tragen `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` und `Pragma: no-cache`. Die eigentümerbezogene Autorisierung fremder Config-IDs (404) bleibt unverändert.

Ausgangsstand vor dem Fix: Der Decorator fehlte vollständig; die Quellcode-Prüfungen und alle Header-Asserts schlugen fehl. Mit dem Fix sind alle 9 Tests grün.

### Lokale Validierung

Python **3.11.2**, Django **5.2.17**, Ruff **0.16.6**:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False PASSPHRASE=test-passphrase SECRET_KEY=test-secret-key
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python manage.py test trading.tests.test_cache_control trading.tests.test_content_type_nosniff trading.tests.test_csrf_cookie trading.tests.test_session_invalidate --noinput
python manage.py collectstatic --noinput
python -m pip check
```

- **174 Django-/Python-Tests** (9 neue + 165 bestehende) bestanden.
- Systemcheck, Migrationsprüfung und `collectstatic` ohne Warnungen. `pip check` ohne Konflikte.
- Keine Code-Änderungen außerhalb `views.py`, dem neuen Testmodul, Version, Changelog, READMEs/Handbuch und diesem Finding-Dokument.

### Abgrenzung und Prüfgrenzen

- Der Decorator erfasst jede Antwort, die von der dekorierten View selbst zurückgegeben wird (200/400/503-JSON). Antworten, die ohne View-Aufruf entstehen – etwa `Http404` aus `get_object_or_404` oder die DB-503 aus `DatabaseAvailabilityMiddleware` – tragen die Header nicht. Sie enthalten keine sensiblen Handelsdaten und liegen außerhalb des hier behandelten Schwachstellenpfads.
- Die Header schützen vor App- und Middleware-seitigem Caching in Browser/Proxy/CDN. Sie ersetzen keine Transportverschlüsselung (HSTS/HTTPS), keine CSP und keine Autorisierung; die bestehenden Maßnahmen bleiben unverändert wirksam.
- Antworten eines vorgeschalteten Reverse-Proxys/CDNs, die nicht von Django erzeugt werden, benötigen dort eine eigene no-cache-Konfiguration. Lokal fehlen Docker und ein produktiver Proxy; die Header sollten nach dem Deploy über den tatsächlichen HTTPS-Endpunkt geprüft werden.
- Die Header-Prüfungen sind HTTP-Response-Tests, kein Browser-End-to-End-Test.

## Auslieferung

- **Commit-Message:** `fix(security): add cache-control headers to API endpoints`
- Keine neuen Umgebungsvariablen oder Datenbankmigrationen. Kein neues Deployment-Geheimnis erforderlich.
- Nach dem Deploy: `Cache-Control`- und `Pragma`-Header über den öffentlichen Endpunkt (bzw. den vorgeschalteten Proxy) prüfen; für außerhalb von Django erzeugte Antworten eine entsprechende Proxy-/CDN-Konfiguration ergänzen.
