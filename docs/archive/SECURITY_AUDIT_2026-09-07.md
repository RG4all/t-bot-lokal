# Code Review & Security Audit – t-bot-lokal

**Audit-Datum:** 07. September 2026  
**Reviewer:** Automated Security Audit  
**Scope:** Gesamte Codebasis (Django 5.2.17, Trading Bot, Backtesting, Docker-Infra)

---

> **Historischer Scan, nicht der aktuelle Freigabestatus.** Mehrere nachfolgende Aussagen beziehen sich auf Code vor 2.4.1–2.4.6 oder auf falsch eingeordnete Django-Defaults. Die ursprünglichen Befunde bleiben nachvollziehbar; die aktuelle, kontextbezogene Bewertung des Passphrase-Fixes und der mitgeprüften Pfade steht im [Security-Review 2.4.4](SECURITY_REVIEW_2.4.4.md). Daraus folgt keine vollständige Neubewertung aller historischen Performance-/Architekturvorschläge.
>
> **Statuspflege:** Jeder Befund der Abschnitte 2–6 trägt releaseweise seinen Umsetzungsstand („Fixed in X.Y.Z“) in der Abschnittsüberschrift sowie im Abschnittstext; der Stand ist zuletzt für **2.4.19** (08. September 2026) aktualisiert. Die historischen Beschreibungen und Lösungsvorschläge bleiben als Ausgangsbefund erhalten.

## 1. Executive Summary

Das Projekt **t-bot-lokal** ist eine Django-basierte Kryptocurrency Paper-Trading-Plattform mit integriertem Backtesting, Live-Bot-Steuerung und WebSocket-Marktdaten. Die Codebasis zeigt ein **solides Sicherheitsbewusstsein** mit mehreren schützenden Schichten (Passphrase-Gate, CSRF, Session-Cookies, DB-Circuit-Breaker). Es wurden jedoch mehrere Schwachstellen identifiziert:

| Schweregrad | Anzahl |
|-------------|--------|
| **Kritisch** | 0 |
| **Hoch** | 3 |
| **Mittel** | 5 |
| **Niedrig** | 4 |

**Wichtigste Befunde:**
1. **Debug-Modus mit wildcard-ALLOWED_HOSTS** in lokaler Entwicklung ermöglicht Host-Header-Injection
2. **Kein Rate-Limiting auf Auth-Endpunkte** (Login, Passphrase-Gate) – Brute-Force-Angriffe möglich
3. **Session-Forging über signed_cookies** ohne Session-Invalidate-Bibliothek bei Passphrase-Änderung
4. **Rate-Limits/Brute-Force-Schutz fehlt** auf allen Endpunkten
5. **Leider keine Content-Security-Policy (CSP)** konfiguriert

**Gesamtbewertung:** GOOD mit Handlungsbedarf bei den identifizierten Punkten.

---

## 2. Sicherheitslücken

### 2.1 HOCH – Kein Rate-Limiting auf Auth-Endpunkten – Fixed in 2.4.1

**Datei:** `trading/rate_limit.py` (neu), `trading_bot_project/settings.py` · **Status:** Fixed

**Umgesetzt in 2.4.1:** Die eigene Middleware `trading.rate_limit.RateLimitMiddleware`
begrenzt Auth-POSTs auf `/login/`, `/gate/`, `/register/` und `/admin/login/` auf
**5 Versuche pro IP und 15 Minuten pro Web-Prozess** – einschließlich erfolgreicher
POSTs. Prüfung und Reservierung sind unter `threading.Lock` atomar; der Speicher ist
auf 10.000 getrackte IPs begrenzt (`OrderedDict`). Bei Überschreitung folgt HTTP 429
mit `Retry-After`-Header. `X-Forwarded-For` wird nur von explizit über
`RATE_LIMIT_TRUSTED_PROXIES` (IPs/CIDRs) konfigurierten Proxys akzeptiert; ohne
Allowlist zählt `REMOTE_ADDR`. Registrierung in `MIDDLEWARE` nach
`AuthenticationMiddleware`.

**Nachweis:** 11 Regressionstests in `trading/tests/test_rate_limit.py` (Limit,
Retry-After, GET-Ausnahme, X-Forwarded-For, Counter-Reset, unabhängige IPs,
Integrationstest). Release-Dokumentation: [Changelog 2.4.1](CHANGELOG.md#241--2026-09-07).

*Historischer Befund (vor 2.4.1):* Das Passphrase-Gate, Login und Registrierung hatten keinerlei Brute-Force-Schutz. Ein Angreifer konnte beliebig viele Passwort-/Passphrase-Versuche starten.

**Auswirkung:** Credential-Stuffing, Passwort-Brute-Force, Account-Übernahme.

**Lösungsvorschlag (historisch):**

```python
# In settings.py hinzufügen:
from django_ratelimit.decorators import ratelimit

# Oder einfache Session-basierte Lösung:
# pip install django-axes
INSTALLED_APPS += ['axes']

# In settings.py:
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
```

Alternative ohne zusätzliche Dependencies – eigene Lösung:

```python
# trading/middleware.py
import time
from collections import defaultdict

class RateLimitMiddleware:
    _attempts = defaultdict(list)
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if request.path in ('/login/', '/gate/') and request.method == 'POST':
            ip = self._get_client_ip(request)
            now = time.time()
            self._attempts[ip] = [t for t in self._attempts[ip] if now - t < 900]
            if len(self._attempts[ip]) >= 5:
                return JsonResponse(
                    {"error": "Zu viele Versuche. Bitte 15 Minuten warten."},
                    status=429
                )
            self._attempts[ip].append(now)
        return self.get_response(request)
    
    @staticmethod
    def _get_client_ip(request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')
```

---

### 2.2 HOCH – DEBUG-Modus mit `ALLOWED_HOSTS = ["*"]` – Fixed in 2.4.2

**Datei:** `trading_bot_project/settings.py` · **Status:** Fixed

**Umgesetzt in 2.4.2:** Der DEBUG-Pfad `ALLOWED_HOSTS.append("*")` ist entfernt.
Es gilt eine explizite Liste lokaler Hosts (`localhost`, `127.0.0.1`, `tbot.local`,
`[::1]`); auf Render wird `RENDER_EXTERNAL_HOSTNAME` ergänzt, optional
`DJANGO_ALLOWED_HOSTS`. Ein Quellcode-Scan stellt sicher, dass `"*"` nicht mehr
bedingt ergänzt wird.

**Nachweis:** 6 Regressionstests in `trading/tests/test_settings.py` (kein Wildcard,
lokale Hosts vorhanden, Source-Scan gegen `append("*")`). Release-Dokumentation:
[Changelog 2.4.2](CHANGELOG.md#242--2026-09-07).

*Historischer Befund (vor 2.4.2):*

```python
DEBUG = env_bool("DEBUG", default=not env_bool("RENDER", False))
# ...
if DEBUG:
    ALLOWED_HOSTS.append("*")
```

**Auswirkung:** Host-Header-Injection, Cache-Poisoning, CSRF-Bypass über Host-Header. Ein Angreifer kann eine Anfrage mit beliebigem Host senden und Django-Session-Cookies oder CSRF-Tokens erhalten.

**Lösungsvorschlag (historisch):**

```python
# Niemals "*" verwenden – stattdessen explizite Liste:
if DEBUG:
    ALLOWED_HOSTS += ['localhost', '127.0.0.1', 'tbot.local', '[::1]']
```

---

### 2.3 HOCH – Kein Content-Security-Policy (CSP) Header – Fixed in 2.4.3

**Datei:** `trading_bot_project/settings.py`, `requirements.txt` · **Status:** Fixed

**Umgesetzt in 2.4.3:** `django-csp==3.8` ist in `requirements.txt`; die App `csp`
und `csp.middleware.CSPMiddleware` sind in `MIDDLEWARE` nach der
`SecurityMiddleware` registriert (seit 2.4.9 liegt die
`PermissionsPolicyMiddleware` dazwischen). Die neun CSP-Direktiven sind strikt auf lokale Ressourcen
ausgerichtet (`CSP_DEFAULT_SRC = ("'self'",)`, …); Skripte nur von `'self'` ohne
`unsafe-inline`, mit frischer Request-Nonce (`CSP_INCLUDE_NONCE_IN =
("script-src",)`) für markierte Template-Skripte. Externe Domains sind nicht
erlaubt – abweichend vom historischen Vorschlag wird auch Plotly/Bootstrap nicht
von CDNs geladen, sondern aus `/static/`.

**Nachweis:** 12 Regressionstests in `trading/tests/test_csp.py` (Einstellungen,
präsente CSP-Header, keine externen Domains). Release-Dokumentation:
[Changelog 2.4.3](CHANGELOG.md#243--2026-09-07).

*Historischer Befund (vor 2.4.3):* Es wurde weder `django-csp` noch ein manueller CSP-Header konfiguriert. Die Anwendung lädt externe Skripte (Plotly, Bootstrap) und rendert Markdown zu HTML (`mark_safe`), was bei fehlendem CSP zu XSS-Vektoren führt.

**Auswirkung:** Cross-Site-Scripting über injectetes Markdown oder kompromittierte CDN-Ressourcen.

**Lösungsvorschlag (historisch):**

```bash
pip install django-csp
```

```python
# settings.py
INSTALLED_APPS += ['csp']

MIDDLEWARE += ['csp.middleware.CSPMiddleware']

CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = ("'self'", "https://cdn.plot.ly")
CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
CSP_IMG_SRC = ("'self'", "data:")
CSP_FONT_SRC = ("'self'",)
CSP_CONNECT_SRC = ("'self'",)
CSP_FRAME_ANCESTORS = ("'self'",)
```

---

### 2.4 Passphrase-Fallback – behoben in 2.4.4

**Bestätigtes Risiko:** Die frühere öffentliche Default-Passphrase ermöglichte bei fehlender Konfiguration das Passieren des Gates. Ein zusätzlich öffentlicher `SECRET_KEY` erlaubte sogar das Fälschen der signierten Gate-Cookies; der Gate-Fix musste deshalb beide Pfade schließen. In der Render-Docker-Kombination war der Gate außerdem über das Basis-Image deaktiviert.

**Umgesetzt:** `secrets.token_urlsafe(32)` und Passphrase-WARNING nur bei lokalem `DEBUG=True` ohne Render. Bei `DEBUG=False` bzw. auf Render starten fehlende/leere Secrets oder ein deaktivierter Gate nicht. Signierschlüssel sind auch lokal privat; Compose und Installer verwenden keine öffentlichen App-Defaults mehr. Alte Gate-Cookies werden durch HMAC-gebundene Freigaben ungültig. Konfigurierte Secrets erscheinen nicht im Log.

**Priorität:** Hoch für die kombinierbaren Gate-/Signierschlüssel-Fehlkonfigurationen; kein automatischer Zugriff auf fremde Nutzerobjekte, deren Autorisierung zusätzlich gilt. Testnachweise, verbleibende Deployment-Anforderungen und False Positives stehen im [Nachreview](SECURITY_REVIEW_2.4.4.md).

---

### 2.5 CSRF-Cookie ohne HttpOnly – Fixed in 2.4.5, zuletzt nachgeprüft in 2.4.16

**Datei:** `trading_bot_project/settings.py` · **Ursprünglicher Fix:** [PR #13](https://github.com/RG4all/t-bot-lokal/pull/13)

**Vorzustand:** Das Projekt hatte nur `SESSION_COOKIE_HTTPONLY = True` gesetzt; das CSRF-Cookie war wegen Djangos `CSRF_COOKIE_HTTPONLY = False` über `document.cookie` lesbar.

**Umgesetzt:** `CSRF_COOKIE_HTTPONLY = True` steht unabhängig vom DEBUG-Modus nach der Session-Cookie-Konfiguration. In Produktion ergänzt `CSRF_COOKIE_SECURE` das Flag. Die App-Skripte lesen das Token aus dem `{% csrf_token %}`-Formularfeld und benötigen keinen Cookie-Zugriff.

**Korrektur der Risikobeschreibung:** HttpOnly verhindert nur den direkten Zugriff auf das Cookie. Skripte derselben Origin können weiterhin das DOM-Token lesen und authentifizierte Requests ausführen; der Fix ist zusätzliche Cookie-Härtung, kein allgemeiner XSS-Schutz oder Ersatz für CSP/CSRF-Prüfungen.

**Nachprüfung 2.4.6:** `trading/tests/test_csrf_cookie.py` enthält jetzt 11 Tests. Der erfolgreiche Login verwendet tatsächlich den maskierten Formular-Token. Produktions-/Render-Cookie-Flags, fehlende Tokens/Cookies, Tokens anderer Clients und fremde Origins sind geprüft. Eine Testprozess-Mutation mit `CSRF_COOKIE_HTTPONLY=False` wird erkannt. Siehe [Nachweis](SEC-06-rule-lifecycle-authz.md#sec-05-nachprüfung).

**Nachprüfung 2.4.8:** `CSRF_COOKIE_HTTPONLY = True` bleibt unverändert aktiv; alle 11 CSRF-Cookie-Tests laufen zusammen mit den neuen Cache-Control-Tests grün. Siehe [SEC-05-Nachprüfung im SEC-08-Nachweis](SEC-08-cache-control-api.md#sec-05-nachprüfung).

**Nachprüfung 2.4.9:** `CSRF_COOKIE_HTTPONLY = True` bleibt unverändert und DEBUG-/Render-unabhängig aktiv; alle 11 CSRF-Cookie-Tests laufen zusammen mit den neuen Permissions-Policy-Tests grün. Siehe [SEC-05-Nachprüfung im SEC-09-Nachweis](SEC-09-permissions-policy.md#sec-05-nachprüfung).

**Nachprüfung 2.4.10:** Alle 11 CSRF-Cookie-Tests erneut grün; eine Testprozess-Mutation mit deaktiviertem `CSRF_COOKIE_HTTPONLY` lässt die Cookie-Prüfung erwartungsgemäß scheitern. Settings, Formular-Token-Login, Produktions-/Render-Flags sowie Cookie-/Token-/Origin-Negativtests bleiben unverändert wirksam. [Nachweis SEC-10](SEC-10-information-disclosure.md#sec-05-nachprüfung).

**Nachprüfung 2.4.14:** `CSRF_COOKIE_HTTPONLY = True` bleibt unverändert und DEBUG-/Render-unabhängig aktiv; alle 11 CSRF-Cookie-Tests laufen zusammen mit den neuen DB-Trim-Batch-Tests grün. Siehe [SEC-05-Nachprüfung im PERF-17-Nachweis](PERF-17-db-trim-batch-delete.md#sec-05-nachprüfung).

**Nachprüfung 2.4.15:** `CSRF_COOKIE_HTTPONLY = True` bleibt unverändert gesetzt; die 11 CSRF-Cookie-Tests laufen zusammen mit den 30 neuen Indikator-Tests grün (279 Tests gesamt). Siehe [SEC-05-Nachprüfung im CODE-18-Nachweis](CODE-18-indicator-dedup.md#sec-05-nachprüfung).

**Nachprüfung 2.4.16:** Unverändert gesetzt; die 11 CSRF-Cookie-Tests und 10 nosniff-Tests laufen zusammen mit den 30 neuen Type-Hints-Tests grün (309 Tests gesamt). Ein Smoke-Test mit geladenen Produktions-/Render-Settings bestätigt `HttpOnly`+`Secure` am CSRF-Cookie und `nosniff` auf allen geprüften Pfaden. Siehe [SEC-05-/SEC-06-Nachprüfung im CODE-19-Nachweis](CODE-19-view-type-hints.md#sec-05--und-sec-06-nachprüfung).

---

### 2.6 X-Content-Type-Options: nosniff – Fixed in 2.4.6

**Datei:** `trading_bot_project/settings.py` · **Status:** Fixed (explizite Härtung)

**Korrigierte Bewertung:** Die ursprüngliche Aussage, `SECURE_CONTENT_TYPE_NOSNIFF` müsse in Django 5.2 erst aktiviert werden, war falsch: Das gepinnte Django 5.2.17 verwendet bereits `True` als Default, und die vorhandene `SecurityMiddleware` lieferte den Header im Standard-Stack schon aus. Bestätigt war die fehlende **explizite** Projektkonfiguration. Ein vorher fehlender Response-Header bzw. ein konkreter MIME-Sniffing-Exploit ist dadurch nicht belegt.

**Umgesetzt:** Nach dem umgebungsabhängigen Security-Block steht jetzt ausdrücklich:

```python
SECURE_CONTENT_TYPE_NOSNIFF = True
```

Die Einstellung ist DEBUG-/Render-unabhängig. `SecurityMiddleware` bleibt an erster Stelle, sodass auch Fehler, Redirects, Downloads und WhiteNoise-Antworten erfasst werden. `X_FRAME_OPTIONS` bleibt unverändert beim vorhandenen Django-Default `DENY`; zusätzliche Middleware ist nicht erforderlich.

**Nachweis:** 10 Regressionstests in `trading/tests/test_content_type_nosniff.py`; Settings-Matrix vor dem Fix rot, nach dem Fix grün. Eine bewusste Deaktivierung im Testprozess lässt die Header-Prüfungen scheitern. [Finding mit Fix-Commit, Prüfgrenzen und SEC-05-Nachprüfung](SEC-06-rule-lifecycle-authz.md).

---

### 2.7 MITTEL – Signed-Cookie-Sessions mit begrenztem Schutz – Fixed in 2.4.7

**Datei:** `trading_bot_project/settings.py` · **Status:** Fixed (Session-Lifetime und Logout-Invalidierung)

**Umgesetzt:** `SESSION_COOKIE_AGE = 60 * 60 * 8` (8 statt 12 Stunden) und `SESSION_EXPIRE_AT_BROWSER_CLOSE = True` sind explizit und DEBUG-/Render-unabhängig gesetzt. Die `logout_view` ruft nach `logout(request)` nun `request.session.flush()` auf, wodurch Session-Daten geleert und der Session-Key rotiert werden. Dies verkleinert das Replay-Fenster für gestohlene signierte Cookies und invalidiert die Sitzung unmittelbar bei Abmeldung.

**Verbleibende Abgrenzung:** Signierte Cookies enthalten weiterhin die Benutzer-ID in Base64; das ist bei diesem Backend keine Schwachstelle (der Inhalt ist signiert, nicht verschlüsselt, aber nicht manipulierbar). Eine Session-Rotation bei Passwort-Änderung (`update_session_auth_hash`) ist als zusätzliche Härtung sinnvoll, erfordert aber eine Passwort-Änderungs-View, die im Projekt derzeit nicht vorhanden ist und ist nicht Teil dieses Fixes.

**Nachweis:** 11 Regressionstests in `trading/tests/test_session_invalidate.py`; Settings-Matrix vor dem Fix rot, nach dem Fix grün. [Finding mit Fix-Commit, Prüfgrenzen](SEC-07-session-lifetime-invalidation.md).

---

### 2.8 MITTEL – Fehlende `Cache-Control`-Header für API-Endpunkte – Fixed in 2.4.8

**Datei:** `trading/views.py` · **Status:** Fixed

**Vorzustand:** API-Endpunkte wie `/api/info/`, `/api/bot/status/`, `/api/logs/`, `/api/data_logs/` und `/api/trades/` hatten keine Cache-Header. Browser und Proxies/CDNs konnten sensitive Handelsdaten zwischenspeichern und später unabhängig vom Server-Zustand erneut ausliefern.

**Lösungsvorschlag (umgesetzt):**

```python
from django.views.decorators.cache import never_cache

@login_required
@require_GET
@never_cache
def info_api(request, config_id):
    # ...
```

Oder als Decorator-Sammlung (im Projekt umgesetzt als `no_cache_json` in `trading/views.py`):

```python
def no_cache_json(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        return response
    return wrapped
```

**Umgesetzt:** Der Decorator `no_cache_json` wird als innerster Decorator auf alle zehn API-Views angewendet (`info_api`, `bot_status_api`, `logs_api`, `data_logs_api`, `trades_api`, `symbol_suggestions_api`, `market_opportunities_api`, `backtesting_status_api`, `backtesting_estimate_api`, `server_resources_api`). Dadurch erhalten auch von den Views erzeugte Fehlerantworten (z. B. 400/503) die Header. Von Django bzw. der Middleware erzeugte Antworten ohne View-Aufruf (z. B. 404 aus `get_object_or_404`, DB-503 aus `DatabaseAvailabilityMiddleware`) enthalten keine sensiblen Handelsdaten und sind von diesem Fix nicht betroffen; die Abgrenzung ist im [Nachweis](SEC-08-cache-control-api.md) dokumentiert.

**Nachweis:** 9 Regressionstests in `trading/tests/test_cache_control.py`; Quellcode- und Header-Prüfungen vor dem Fix rot, nach dem Fix grün. [Finding mit Fix-Nachweis und SEC-05-Nachprüfung](SEC-08-cache-control-api.md).

---

### 2.9 NIEDRIG – Fehlende `Permissions-Policy` / `Feature-Policy` – Fixed in 2.4.9

**Datei:** `trading_bot_project/settings.py`, `trading/middleware.py` · **Status:** Fixed

**Vorzustand:** Es wurde kein `Permissions-Policy`-Header gesetzt, der den Zugriff auf Browser-APIs (Kamera, Mikrofon, Geolokation) einschränkt. Die App benötigt diese APIs nicht; ohne Header hätte eine erfolgreiche Skript-Injektion die sensiblen APIs anfordern können.

**Lösungsvorschlag (umgesetzt):**

```python
# In settings.py nach den anderen Security-Headern:
SECURE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
```

**Umgesetzt:** Die zentrale Einstellung `SECURE_PERMISSIONS_POLICY` in `trading_bot_project/settings.py` deaktiviert Kamera, Mikrofon und Geolokation für alle Origins. Da Django selbst keinen Permissions-Policy-Header erzeugt, setzt die neue Middleware `trading.middleware.PermissionsPolicyMiddleware` (registriert direkt nach der `SecurityMiddleware`) den Header aus dieser Einstellung auf jeder Antwort – auch auf Fehler-, Redirect- und WhiteNoise-Antworten. Ein leerer Policy-Wert lässt Antworten unverändert. Der 301-SSL-Redirect entsteht direkt in der äußersten `SecurityMiddleware` und führt selbst keine Browser-APIs aus; die Abgrenzung ist im [Nachweis](SEC-09-permissions-policy.md) dokumentiert.

**Nachweis:** 11 Regressionstests in `trading/tests/test_permissions_policy.py`; Settings-Matrix und Header-Prüfungen vor dem Fix rot (17 fehlgeschlagene Assertions), nach dem Fix grün. [Finding mit Fix-Nachweis und SEC-05-Nachprüfung](SEC-09-permissions-policy.md).

---

### 2.10 NIEDRIG – Information Disclosure in Fehlermeldungen – Fixed in 2.4.10

**Dateien:** `trading/views.py` und die direkt beteiligten Formular-/Scanner-/Bot-/Worker-/Fehler-Templates · **Status:** Fixed

**Vorzustand:** Technische Exception-Texte gelangten in Flash-Meldungen, JSON- und Report-Antworten. Nur zwei Flash-Strings zu ersetzen hätte die parallelen Ausgabewege und die Anzeige gespeicherter Diagnosen nicht geschlossen.

**Umgesetzt:** Feste, kontextbezogene Benutzer-Meldungen statt technischer Exception-Texte; `logger.exception` erhält Diagnose und Traceback. Das gilt auch für Teilfehler, Bot-Status und gespeicherte Backtest-Fehler. Das Fehler-Log zeigt technische Inhalte nur noch Staff-Konten innerhalb der bestehenden Eigentümergrenze; normale Konten behalten generische Einträge mit Referenz. DB-Fehler beim zusätzlichen Persistieren eines Log-Eintrags verdecken nicht die ursprüngliche sichere Antwort. Keine neue Runtime-Abhängigkeit oder Migration.

**Nachweis:** 25 neue Regressionstests, am Ausgangsstand rot, nach dem Fix grün; 210 Tests insgesamt bestanden. [Finding, Fix-Commit, Prüfgrenzen und SEC-05-Nachprüfung](SEC-10-information-disclosure.md).

---

### 2.11 NIEDRIG – Kein HSTS-Preload für lokale Entwicklung

**Datei:** `trading_bot_project/settings.py` (Zeilen 140–154)

```python
if not DEBUG:
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True  # ← Gut!
```

Aber: Für `localhost`/`127.0.0.1` wird kein HTTPS erzwungen, was korrekt ist. Kein Handlungsbedarf.

---

### 2.12 NIEDRIG – Docker-Compose Standard-Passwörter – Fixed in 2.4.11

**Datei:** `docker-compose.yml` (Zeilen 11, 54)

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-tbot-local-password}
PASSPHRASE: ${PASSPHRASE:-local-t-bot}
```

**Auswirkung:** Standard-Passwörter sind in der Public-Repo sichtbar. Bei Vergessener Konfiguration sind die Datenbank und der Passphrase-Gate aktiv.

**Lösungsvorschlag:**

```yaml
# Keine Defaults für Secrets:
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}
PASSPHRASE: ${PASSPHRASE:?Set PASSPHRASE in .env}
```

**Umgesetzt (2.4.11):** `PASSPHRASE` (und `SECRET_KEY`) waren bereits seit 2.4.4 Pflichtwerte; ab 2.4.11 gilt dies auch für `POSTGRES_PASSWORD` – sowohl im `postgres`-Service als auch in der daraus gebauten `DATABASE_URL`. Fehlende oder leere Secrets brechen die Compose-Interpolation ab; öffentliche Defaults existieren in keiner ausgelieferten Konfigurations-/Skriptdatei mehr. `scripts/setup_local.sh` erzeugt das lokale DB-Passwort zufällig in `.env.local` und warnt beim Wiedererkennen des früher öffentlichen Werts vor fehlender Rotation.

**Nachweis:** Neue Shell-Testgruppe `tests/test_compose_security.sh` (25 Assertions) und erweiterte `tests/test_setup_local.sh`, vor dem Fix rot, danach grün. [Finding, Fix-Commit und Prüfgrenzen](SEC-12-docker-default-passwords.md).

---

## 3. Bugs & Funktionale Fehler

### 3.1 MITTEL – Race Condition in Bot-Start/Stop – Fixed in 2.4.12

**Datei:** `trading/trading_bot.py` · **Status:** Fixed

**Vorzustand:** `start_bot()` rief unter gehaltenem `self._lock` die öffentliche Methode `is_running()` auf, die denselben Lock intern erneut erwarb. Mit `threading.RLock` entstand kein Deadlock; ein nicht-reentrantes `Lock` würde hängen. Die verschachtelte Acquisition war ein Code-Smell und erschwerte die Trennung von interner und öffentlicher Laufzustandsprüfung.

**Umgesetzt:** Interne Methode `_is_running_unlocked()` prüft und räumt beendete Threads ohne Lock-Acquisition. `is_running()` umschließt sie mit dem Lock für externe Aufrufer. `start_bot()` und `stop_bot()` rufen die unlocked-Variante unter dem bereits gehaltenen Lock auf. Views bleiben bei der öffentlichen API.

**Nachweis:** 16 Regressionstests in `trading/tests/test_bot_start_stop.py`; 7 davon am Ausgangsstand rot (inkl. Deadlock auf `threading.Lock`). [Finding mit Fix-Commit](BUG-12-race-condition-bot-start-stop.md).

---

### 3.2 MITTEL – `db_restore_state` ist synchrone Funktion ohne Thread-Executor

**Datei:** `trading/trading_bot.py` (Zeilen 236–249)

```python
@db_safe(suppress=True)
def db_restore_state(config_id):
    return list(
        TradingLog.objects.filter(configuration_id=config_id)
        .order_by("timestamp", "id")
        .values(...)
    )
```

**Problem:** Diese Funktion wird im `TradingBot.__init__` aufgerufen, aber sie ist **keine async-fähige db_-Funktion** und wird **synchron** auf dem asyncio-Thread ausgeführt. Bei großen Tabellen kann dies den Bot-Thread blockieren.

**Lösungsvorschlag:**

```python
@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_restore_state(config_id):
    return list(
        TradingLog.objects.filter(configuration_id=config_id)
        .order_by("timestamp", "id")
        .values(...)
    )

# In TradingBot.__init__:
async def _restore_state(self):
    logs = await db_restore_state(self.config_id) or []
    # ...
```

---

### 3.3 NIEDRIG – CSV-Export mit Standard-Textpuffer – Fixed in 2.4.13

**Datei:** `trading/views.py` (`generate_report_csv`) · **Finding:** `CsvEchoNotTrueStream` · **Status:** Fixed

**Einordnung:** Der geprüfte Stand 2.4.12 nutzte `_CsvEcho.write()`, das den geschriebenen Text zurückgab, und reichte den Rückgabewert von `writerow()` direkt an den Response-Generator weiter. Das Echo-Muster ist mit `csv.writer` zulässig und erzeugte bereits gültiges CSV; eine ausnutzbare Sicherheitslücke oder ein allgemeiner Streaming-Defekt ist damit nicht nachgewiesen. Der Befund wird als LOW-Kompatibilitäts-Refactoring behoben.

**Fix:** `_CsvEcho` ist entfernt. Ein erst beim Lesen geöffneter `io.StringIO(newline="")`-Puffer wird im Generator wiederverwendet, vor jeder Datenzeile mit `seek(0)`/`truncate(0)` geleert und mit `getvalue()` ausgelesen. Der Context-Manager schließt ihn bei regulärem Ende, Fehler oder Schließen der Response. UTF-8-BOM, Spalten, Sortierung, `iterator(chunk_size=1000)`, Download-Header und Eigentümerprüfung bleiben unverändert.

**Nachweis:** 15 neue Regressionstests in `trading/tests/test_report_csv.py`; fünf davon am Ausgangsstand rot (sieben fehlgeschlagene Assertions), nach dem Fix grün. Insgesamt 242 Django-/Python-Tests bestanden. Gezielte Negativkontrollen erkennen fehlendes Zurücksetzen, Kürzen und Schließen des Puffers. [Finding mit Fix-Commit, CI-Freigabe und Prüfgrenzen](BUG-14-csv-echo-true-stream.md).

**Streaming-Grenze:** Der Generator bleibt synchron. Unter Django/ASGI kann er weiterhin vollständig konsumiert werden; dieses Refactoring ist kein End-to-End-True-Streaming-Fix für Daphne oder vorgeschaltete Proxies.

---

### 3.4 NIEDRIG – Fehlende Validierung von `config_id` in `bot_status_api`

**Datei:** `trading/views.py` (Zeilen 767–789)

```python
def bot_status_api(request):
    config_id = request.GET.get("config_id")
    if not config_id:
        return JsonResponse({"error": "config_id fehlt"}, status=400)
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
```

**Problem:** `config_id` wird nicht auf Ganzzahl-Format geprüft. Bei ungültigem Input (z.B. `"abc"`) gibt Django automatisch einen 404-Fehler zurück, aber die Fehlermeldung ist nicht benutzerfreundlich.

**Lösungsvorschlag:**

```python
if not config_id or not config_id.isdigit():
    return JsonResponse({"error": "config_id muss eine gültige Ganzzahl sein"}, status=400)
```

---

## 4. Optimierungsbedarf

### 4.1 PERFORMANCE – Indikatorberechnung kann memoisiert werden

**Datei:** `trading/backtesting.py` (Zeilen 13–51)

Die `calculate_indicators()`-Methode wird für jeden Backtest-Kandidaten neu berechnet. Bei 20.000 Kombinationen mit 5.000 Preispunkten sind das 100 Millionen Decimal-Operationen.

**Lösungsvorschlag:**

```python
# In Backtesting-Klasse:
_indicator_cache = {}

@classmethod
def calculate_indicators(cls, prices, idx):
    cache_key = (id(prices), idx)
    if cache_key in cls._indicator_cache:
        return cls._indicator_cache[cache_key]
    
    # ... Berechnung ...
    result = acceleration, deltadelta, current_nda
    cls._indicator_cache[cache_key] = result
    return result

@classmethod
def clear_indicator_cache(cls):
    cls._indicator_cache.clear()
```

---

### 4.2 PERFORMANCE – `db_trim_datalog` kann Datenbank-Lock verursachen – Fixed in 2.4.14

**Datei:** `trading/trading_bot.py` (historische Zeilen 176–183)

```python
@db_safe(suppress=True)
def db_trim_datalog(config_id, symbol, max_rows):
    queryset = DataLog.objects.filter(configuration_id=config_id, symbol=symbol)
    cutoff_id = (
        queryset.order_by("-id").values_list("id", flat=True)[max_rows : max_rows + 1].first()
    )
    if cutoff_id is not None:
        queryset.filter(id__lte=cutoff_id).delete()
```

**Problem:** Bei 20.000+ Zeilen pro Symbol kann der `DELETE`-Befehl lange dauern und Datenbank-Locks verursachen.

**Lösungsvorschlag:**

```python
# Batch-Delete mit LIMIT:
from django.db import connection

def db_trim_datalog(config_id, symbol, max_rows):
    cutoff_id = (
        DataLog.objects.filter(configuration_id=config_id, symbol=symbol)
        .order_by("-id")
        .values_list("id", flat=True)[max_rows : max_rows + 1]
        .first()
    )
    if cutoff_id is not None:
        # Batch-Delete in 1000er-Schritten
        while True:
            deleted = DataLog.objects.filter(
                configuration_id=config_id, symbol=symbol, id__lte=cutoff_id
            )[:1000].delete()[0]
            if deleted == 0:
                break
```

**Umgesetzt in 2.4.14 – Abweichung vom Lösungsvorschlag:** Die oben
skizzierte Variante `queryset[:1000].delete()` ist mit Django 5.2.17 nicht
ausführbar (`TypeError: Cannot use 'limit' or 'offset' with delete().`).
`db_trim_datalog()` liest stattdessen je Durchgang nur die nächsten maximal
1000 betroffenen IDs (`ORDER BY id LIMIT 1000`) und löscht genau diese in
einer eigenen, sofort committeten Transaktion über `id__in`. Der Abbruch
erfolgt, sobald keine Alt-Zeile mehr übrig ist. `max_rows < 1` wird als
sicheres No-op behandelt. Behaltenslogik, Scoping und Aufrufrhythmus bleiben
unverändert. **Nachweis:** 7 Regressionstests in
`trading/tests/test_db_trim_datalog.py`; am Ausgangsstand rot (eine
Transaktion über alle 2.500 Zeilen statt `[1000, 1000, 500]`;
`ValueError` bei negativem `max_rows`), mit Fix grün. Insgesamt
249 Django-/Python-Tests bestanden; SEC-05 mit allen 11 CSRF-Cookie-Tests
erneut nachgeprüft. [Finding mit Fix-Commit und Prüfgrenzen](PERF-17-db-trim-batch-delete.md).

---

### 4.3 CODE-QUALITÄT – Duplizierte Indikator-Logik – Fixed in 2.4.15

**Dateien:**
- `trading/backtesting.py` (historische Zeilen 14–51)
- `trading/trading_bot.py` (historische Zeilen 577–591)

Die Indikatorberechnung (NDA, DeltaDelta, Acceleration) war in beiden Dateien fast identisch implementiert, mit eigener Rundungs- und Indexbehandlung. **Umgesetzt in 2.4.15:** neues Modul `trading/indicators.py` ist die einzige Quelle der Arithmetik (`compute_indicator_values()` mit optionalem Rundungs-Hook, `calculate_trading_indicators()` für die quantisierte Backtest-Stufe, `build_indicator_rows()` für die Vorabberechnung, `EIGHT_PLACES` als gemeinsame Konstante). `backtesting.py`, `trading_bot.py`, `tasks.py` und `scripts/backtest_resource_probe.py` delegieren; `Backtesting.calculate_indicators` bleibt als dünner Kompatibilitäts-Wrapper. Beide Präzisionsstufen (Bot rechnet roh, Backtest quantisiert pro Zwischenstufe) bleiben bewusst erhalten – ein Deduplizierungs-Refactoring darf keine laufenden Schwellwertentscheidungen oder gespeicherten DataLog-Werte verschieben. Zusätzlich neu: Index-Guard `idx >= 2` und `idx < len(prices)`; vorher rechnete `idx=1` über die Listendefinition stillschweigend mit `prices[-1]`.

**Nachweis:** 30 Regressionstests in `trading/tests/test_indicators.py`, 16.693 deterministische Alt-/Neu-Vergleichsfälle ohne Abweichung, Backtest-Reports byte-identisch. [Finding mit Fix-Commit und Prüfgrenzen](CODE-18-indicator-dedup.md).

Historischer Lösungsvorschlag (unvollständig – die Bot-Nebenwerte `current_da`/`prev_da`/`dva` und die Rundungsstufe fehlten):

```python
# trading/indicators.py (neues Modul)
from decimal import ROUND_HALF_UP, Decimal

_EIGHT_PLACES = Decimal("0.00000001")

def calculate_trading_indicators(prices, idx):
    """Berechnet die drei Strategieindikatoren am angegebenen Index."""
    current_price = Decimal(str(prices[idx]))
    previous_price = Decimal(str(prices[idx - 1]))
    older_price = Decimal(str(prices[idx - 2]))
    
    current_da = current_price - previous_price
    nda = (
        (current_da / previous_price * Decimal(100)).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
        if previous_price else Decimal(0)
    )
    # ... usw.
    return acceleration, deltadelta, nda
```

Dann in beiden Dateien importieren:

```python
from .indicators import calculate_trading_indicators
```

---

### 4.4 TECHNOLOGISCHE SCHULD – Fehlende Type-Hints in Views – Fixed in 2.4.16

**Datei:** `trading/views.py`

Der historische Befund nannte „einige Funktionen"; tatsächlich enthielt die
Datei in 1.830 Zeilen und 65 Funktionen **keine einzige** Type-Annotation und
bis auf sechs Ausnahmen keine Docstrings:

```python
# Aktuell:
def _portfolio_snapshot(config):
    # ...

# Verbessert:
def _portfolio_snapshot(config: Configuration) -> dict[str, Any]:
    # ...
```

**Umgesetzt in 2.4.16:** Alle 65 Top-Level-Funktionen sind vollständig
annotiert (Parameter und Rückgabewert), alle 39 öffentlichen Views tragen einen
Docstring. `no_cache_json` erhält `ParamSpec`/`TypeVar` und erhält damit die
Signatur der zehn dekorierten API-Views. Die eigentliche Root Cause – dass kein
statischer Prüfer die Datei analysieren konnte – ist mit einem
`[tool.mypy]`-Block in `pyproject.toml` geschlossen (Ziel `trading/views.py`,
`mypy_django_plugin`, `django_settings_module`); mypy und django-stubs bleiben
bewusst Entwicklungswerkzeuge außerhalb von `requirements.txt`.

Beim Annotieren wurden drei latente Randpfade sichtbar und gehärtet – keiner
war im ausgelieferten Stand erreichbar, es wird keine behobene Schwachstelle
behauptet: 26 ORM-Filter liefen über das untypisierte `request.user`
(`User | AnonymousUser`) und nutzen jetzt `_authenticated_user()` als zweite
Schicht neben `@login_required`; das Passphrase-Gate weist ein leeres Secret
explizit ab (`constant_time_compare("", "")` ist `True`); `_equity_svg`
überspringt Punkte ohne `equity`-Wert, statt über einen `TypeError` zu laufen.
Details, Negativkontrolle und Prüfgrenzen: [CODE-19](CODE-19-view-type-hints.md).

---

### 4.5 TECHNOLOGISCHE SCHULD – Fehlende `__all__`-Exports – Fixed in 2.4.17

**Datei:** `trading/__init__.py`

Es gab kein `__all__` und keine explizite Import-Policy für das `trading`-Modul,
wodurch die öffentliche API unklar war: `from trading import *` exportierte nach
dem Laden interner Module auch `admin`, `apps`, `middleware`, `monitoring`,
`passphrase`, `rate_limit`, `urls` usw.

**Umgesetzt in 2.4.17:** `__all__` deklariert die zwölf öffentlichen Submodule
(`trading_bot`, `backtesting`, `market_data`, `indicators`, `views`, `models`,
`tasks`, `forms`, `symbols`, `resource_optimizer`, `market_scanner`,
`worker_status`); die interne Django-/Channels-Infrastruktur bleibt außerhalb
der öffentlichen API. Die im ursprünglichen Prompt vorgeschlagene Eager-Import-
Variante (`from . import models, views, tasks, ...`) wurde bewusst **nicht**
übernommen, weil das Paket-`__init__` von Django vor dem Abschluss der
App-Registry geladen wird und die Django-Module dann mit
`AppRegistryNotReady` abbrechen. Stattdessen werden import-sichere Module
direkt gebunden und Django-gebundene Module per modul-Level-`__getattr__`
(PEP 562) erst beim Zugriff geladen. Details, Negativkontrolle und Prüfgrenzen:
[CODE-20](CODE-20-module-exports.md).

---

### 4.6 PERFORMANCE – `info_api` lädt alle Logs in den Speicher – Fixed in 2.4.18

**Datei:** `trading/views.py` · **Status:** Fixed (siehe
[PERF-21](PERF-21-info-api-db-aggregation.md))

**Umgesetzt in 2.4.18:** `info_api` berechnet die Performance-Kennzahlen über die
neue `_calculate_metrics_from_db(config, limit=_MAX_LOG_ROWS)` direkt im DBMS –
`django.db.models.aggregate()` mit `Count`/`Sum`/`Max`/`Min` und `Q`-Filtern – statt
alle Logs des Fensters nach Python zu laden. Die Skalarmathematik (Quotienten,
Rundung) läuft bewusst in Python: eine reine `Sum("pl_nominal") / Count("id")`-
Division würde PostgreSQL als Ganzzahldivision ausführen und verkehrte Kennzahlen
liefern; das Ergebnis bleibt so backend-unabhängig (SQLite/PostgreSQL) und bitgenau
zu `calculate_performance_metrics()`. Fensterbegrenzung (`[:_MAX_LOG_ROWS]`),
API-Antwort und Equity-/Kassenkurve bleiben unverändert (die Kurve lädt weiterhin
die einzelnen Zeilen).

**Nachweis:** 10 Regressionstests in `trading/tests/test_info_api_metrics.py`
(Verhaltensgleichheit mit `calculate_performance_metrics()` inkl. Negativtest mit
gepatchter Python-Funktion, Fensterbegrenzung über 2.600 Logs, `limit`-Parameter);
insgesamt 330 Django-/Python-Tests grün. Fix-Commit
[`c7ee446`](https://github.com/RG4all/t-bot-lokal/commit/c7ee446) –
`perf(views): optimize info_api with DB aggregation`, Auslieferung über
[PR #26](https://github.com/RG4all/t-bot-lokal/pull/26). Release-Dokumentation:
[Changelog 2.4.18](CHANGELOG.md#2418--2026-09-08).

*Historischer Befund (vor 2.4.18):*

```python
def info_api(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
    portfolio = _portfolio_snapshot(config)
    metrics = calculate_performance_metrics(logs)
    # ...
```

**Problem:** Bei `MAX_LOG_ROWS = 2000` werden alle 2000 Zeilen in den Speicher geladen. Für große Konfigurationen kann dies zu hohem RAM-Verbrauch führen.

**Lösungsvorschlag (historisch):**

```python
# Aggregation auf DB-Ebene statt in Python:
def _realized_profit_fast(config):
    return config.logs.filter(action="sell").aggregate(
        total=Sum("pl_nominal")
    )["total"] or Decimal(0)

# Für Metriken: Aggregation mit Django ORM
def calculate_performance_metrics_fast(config, limit=2000):
    sell_logs = config.logs.filter(action="sell").order_by("-timestamp", "-id")[:limit]
    # ...
```

---

## 5. Recommendations – Priorisierte Maßnahmen

### Sofort umsetzen (P0 – Sicherheitskritisch)

| # | Maßnahme | Aufwand | Datei |
|---|----------|---------|-------|
| 1 | **Rate-Limiting auf Auth-Endpunkte – Fixed in 2.4.1 (§2.1)** | 2h | `rate_limit.py`, `settings.py` |
| 2 | **ALLOWED_HOSTS: Kein Wildcard – Fixed in 2.4.2 (§2.2)** | 5min | `settings.py` |
| 3 | **CSRF_COOKIE_HTTPONLY = True – Fixed in 2.4.5 (§2.5)** | 5min | `settings.py` |
| 4 | **Docker-Passwörter ohne Defaults setzen – Fixed in 2.4.11 (§2.12)** | 15min | `docker-compose.yml` |

### Kurzfristig umsetzen (P1 – 1–2 Wochen)

| # | Maßnahme | Aufwand | Datei |
|---|----------|---------|-------|
| 5 | **CSP-Header – Fixed in 2.4.3 (§2.3)** | 4h | `settings.py`, Middleware |
| 6 | **Cache-Control für API-Endpunkte – Fixed in 2.4.8 (§2.8)** | 2h | `views.py` |
| 7 | **Error-Messages ohne Exception-Details – Fixed in 2.4.10 (§2.10)** | 1h | `views.py` |
| 8 | **Indikator-Code deduplizieren – Fixed in 2.4.15 (§4.3)** | 3h | `indicators.py` (neu) |
| 9 | **`__all__`-Exports für Trading-Modul – Fixed in 2.4.17 (§4.5)** | 30min | `trading/__init__.py` |

### Mittelfristig umsetzen (P2 – 1–2 Monate)

| # | Maßnahme | Aufwand | Datei |
|---|----------|---------|-------|
| 10 | **db_restore_state async-fähig machen** | 2h | `trading_bot.py` |
| 11 | **Indikator-Memoisierung** für Backtesting | 4h | `backtesting.py` |
| 12 | **DB-Trim mit Batch-Delete – Fixed in 2.4.14 (§4.2)** | 2h | `trading_bot.py` |
| 13 | **Type-Hints für Views ergänzen – Fixed in 2.4.16 (§4.4)** | 3h | `views.py` |
| 14 | **info_api-Kennzahlen per DB-Aggregation – Fixed in 2.4.18 (§4.6)** | 3h | `views.py` |

---

## 6. Komplett-Checkliste

### Sicherheit

- [x] Rate-Limiting auf Login/Passphrase-Gate (ab 2.4.1, siehe §2.1; 5 POSTs / 15 min / IP / Prozess)
- [x] ALLOWED_HOSTS ohne Wildcard (ab 2.4.2, siehe §2.2)
- [x] CSRF_COOKIE_HTTPONLY = True (ab 2.4.5, siehe §2.5)
- [x] CSP-Header implementiert (ab 2.4.3, siehe §2.3; Nonce für Template-Skripte, keine externen Domains)
- [x] Cache-Control für API-Endpunkte (ab 2.4.8, siehe §2.8 und [SEC-08](SEC-08-cache-control-api.md))
- [x] Error-Messages ohne technische Details (ab 2.4.10; Diagnose-Log mit Staff-/Eigentümergrenze, siehe §2.10 und [SEC-10](SEC-10-information-disclosure.md))
- [x] Docker-Passwörter ohne Defaults (ab 2.4.11; `SECRET_KEY`/`PASSPHRASE`/`POSTGRES_PASSWORD` als `${VAR:?...}`-Pflichtwerte, zufälliges lokales DB-Passwort im Setup, siehe §2.12 und [SEC-12](SEC-12-docker-default-passwords.md))
- [x] Session-Lifetime auf 8 Stunden reduziert + SESSION_EXPIRE_AT_BROWSER_CLOSE + request.session.flush() bei Logout (ab 2.4.7, siehe §2.7 und [SEC-07](SEC-07-session-lifetime-invalidation.md))
- [ ] Session-Rotation bei Passwort-Änderung (erfordert Passwort-Änderungs-View)
- [x] X-Content-Type-Options: nosniff – explizit ab 2.4.6, siehe §2.6
- [x] Permissions-Policy für Kamera/Mikrofon/Geolokation (ab 2.4.9, siehe §2.9 und [SEC-09](SEC-09-permissions-policy.md))
- [x] HSTS-Header für Produktion korrekt (kein Handlungsbedarf, siehe §2.11)

### Code-Qualität

- [x] Indikator-Code dedupliziert (ab 2.4.15, siehe §4.3 und [CODE-18](CODE-18-indicator-dedup.md))
- [x] Type-Hints für Views (ab 2.4.16, siehe §4.4 und [CODE-19](CODE-19-view-type-hints.md))
- [x] `__all__` für Trading-Modul (ab 2.4.17, siehe §4.5 und [CODE-20](CODE-20-module-exports.md))
- [x] Bot-Start/Stop Race-Condition behoben (ab 2.4.12, siehe §3.1 und [BUG-12](BUG-12-race-condition-bot-start-stop.md))
- [x] CSV-Export mit `io.StringIO`-Puffer (ab 2.4.13, siehe §3.3 und [BUG-14](BUG-14-csv-echo-true-stream.md))
- [ ] db_restore_state async-fähig

### Performance

- [ ] Indikator-Memoisierung für Backtesting
- [x] DB-Trim mit Batch-Delete (ab 2.4.14, siehe §4.2 und [PERF-17](PERF-17-db-trim-batch-delete.md))
- [x] Aggregation auf DB-Ebene statt Python (ab 2.4.18, siehe §4.6 und [PERF-21](PERF-21-info-api-db-aggregation.md))

---

*Dieses Audit basiert auf der Code-Analyse vom 07. September 2026. Für kritische Schwachstellen wird eine erneute Prüfung nach Umsetzung der P0-Maßnahmen empfohlen. Die Statuspflege der Abschnitte 2–6 erfolgt releaseweise; Dokumentationsstand zuletzt aktualisiert für 2.4.19 (08. September 2026).*
