# Arena.ai Agent Prompts – t-bot-lokal Security Audit

Generiert am: 07. September 2026  
Basiert auf: SECURITY_AUDIT.md  
Jeder Prompt ist eigenständig und PR-ready.

---

## Prompt 1: Rate-Limiting Auth-Endpunkte

**Prompt-Titel:** `[NoRateLimiting] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** HIGH – Security  
**Betroffene Dateien:** `trading/views.py` (Zeilen 250–306), `trading/middleware.py`

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet Django 5.2.17, Channels 4.3.2 und Celery 5.6.3.

AUFGABE: Implementiere Rate-Limiting auf alle Auth-Endpunkte (Login, Passphrase-Gate, 
Registrierung), um Brute-Force-Angriffe und Credential-Stuffing zu verhindern.

KONTEXT:
- Die Auth-Endpunkte befinden sich in trading/views.py: passphrase_gate_view (Zeile 250), 
  login_view (Zeile 283), register_view (Zeile 273)
- Es gibt KEINE Rate-Limits – ein Angreifer kann beliebig viele Versuche starten
- Das System verwendet signed_cookie-sessions (SESSION_ENGINE in settings.py)
- Die Middleware-Kette ist in settings.py MIDDLEWARE definiert

LÖSUNGSSCHRITTE:

1. ERSTELLE eine neue Datei trading/rate_limit.py mit einer IP-basierten Rate-Limit-Middleware:
   - Speichere Versuche in-memory (thread-safe mit threading.Lock)
   - Window: 15 Minuten (900 Sekunden)
   - Limit: 5 Versuche pro IP
   - Betroffene Pfade: /login/, /gate/, /register/
   - Bei Überschreitung: HTTP 429 mit Retry-After Header
   - Berücksichtige X-Forwarded-For Header für Proxy-Support

2. REGISTRIERE die Middleware in trading_bot_project/settings.py:
   - Füge 'trading.middleware.RateLimitMiddleware' zur MIDDLEWARE-Liste hinzu
   - Platziere sie NACH dem AuthenticationMiddleware

3. ERSTELLE Tests in trading/tests/test_rate_limit.py:
   - Teste dass 5 fehlgeschlagene Login-Versuche zu 429 führen
   - Teste dass erfolgreiche Anfragen nicht gezählt werden
   - Teste dass der Counter nach 15 Minuten zurückgesetzt wird
   - Teste dass GET-Anfragen nicht limitiert werden

CODE-STRUKTUR:

# trading/rate_limit.py
import time
import threading
from collections import defaultdict
from django.http import JsonResponse

class RateLimitMiddleware:
    """IP-basiertes Rate-Limiting für Auth-Endpunkte.
    
    Schützt Login, Passphrase-Gate und Registrierung vor Brute-Force-Angriffen.
    Speichert Versuche in-memory – für Multi-Worker-Setup sollte Redis verwendet werden.
    """
    
    _attempts = defaultdict(list)
    _lock = threading.Lock()
    MAX_ATTEMPTS = 5
    WINDOW_SECONDS = 900  # 15 Minuten
    PROTECTED_PATHS = ('/login/', '/gate/', '/register/')
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if request.method == 'POST' and request.path in self.PROTECTED_PATHS:
            client_ip = self._get_client_ip(request)
            if self._is_rate_limited(client_ip):
                return JsonResponse(
                    {"error": "Zu viele Versuche. Bitte 15 Minuten warten."},
                    status=429,
                    headers={"Retry-After": str(self.WINDOW_SECONDS)}
                )
            self._record_attempt(client_ip)
        return self.get_response(request)
    
    def _is_rate_limited(self, ip):
        """Prüft ob die IP das Rate-Limit überschritten hat."""
        now = time.time()
        with self._lock:
            # Alte Einträge entfernen
            self._attempts[ip] = [
                t for t in self._attempts[ip] 
                if now - t < self.WINDOW_SECONDS
            ]
            return len(self._attempts[ip]) >= self.MAX_ATTEMPTS
    
    def _record_attempt(self, ip):
        """Zeichnet einen fehlgeschlagenen Versuch auf."""
        with self._lock:
            self._attempts[ip].append(time.time())
    
    @staticmethod
    def _get_client_ip(request):
        """Extrahiert die Client-IP unter Berücksichtigung von Proxys."""
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')

VALIDIERUNGSKRITERIEN:
- [ ] Rate-Limit-Middleware existiert in trading/rate_limit.py
- [ ] Middleware ist in settings.py MIDDLEWARE registriert
- [ ] 5 fehlgeschlagene Login-Versuche → HTTP 429
- [ ] Retry-After Header ist gesetzt
- [ ] Erfolgreiche Anfragen werden nicht gezählt
- [ ] GET-Anfragen werden nicht limitiert
- [ ] Tests existieren und bestehen
- [ ] Code hat deutsche/englische Kommentare
- [ ] PR-Commit-Message: "feat(security): add rate limiting to auth endpoints"
```

---

## Prompt 2: ALLOWED_HOSTS Wildcard

**Prompt-Titel:** `[AllowedHostsWildcard] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** HIGH – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py` (Zeilen 61, 71–72)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet Django 5.2.17.

AUFGABE: Entferne den Wildcard-Eintrag "*" aus ALLOWED_HOSTS im DEBUG-Modus und 
ersetze ihn durch eine explizite Liste erlaubter Hosts.

KONTEXT:
- In settings.py Zeile 71-72 steht: if DEBUG: ALLOWED_HOSTS.append("*")
- Das ermöglicht Host-Header-Injection, Cache-Poisoning und CSRF-Bypass
- Erlaubte lokale Hosts: localhost, 127.0.0.1, tbot.local, [::1]
- RENDER_EXTERNAL_HOSTNAME wird dynamisch hinzugefügt

LÖSUNGSSCHRITTE:

1. MODIFIZIERE trading_bot_project/settings.py:
   - Ersetze ALLOWED_HOSTS.append("*") durch explizite lokale Hosts
   - Füge Kommentar hinzu, warum "*" gefährlich ist

2. ERSTELLE einen Test in trading/tests/test_settings.py:
   - Teste dass "*" nicht in ALLOWED_HOSTS enthalten ist
   - Teste dass localhost und 127.0.0.1 erlaubt sind

CODE-ÄNDERUNG:

# VORHER (settings.py Zeile 71-72):
if DEBUG:
    ALLOWED_HOSTS.append("*")

# NACHHER:
if DEBUG:
    # WICHTIG: Niemals "*" verwenden – ermöglicht Host-Header-Injection
    # Nur explizit lokale Entwicklungshosts erlauben
    ALLOWED_HOSTS += ['localhost', '127.0.0.1', 'tbot.local', '[::1]']

VALIDIERUNGSKRITERIEN:
- [ ] Kein "*" in ALLOWED_HOSTS (auch nicht bedingt)
- [ ] localhost und 127.0.0.1 sind erlaubt
- [ ] Kommentar erklärt warum kein Wildcard
- [ ] Test bestätigt dass kein Wildcard vorhanden
- [ ] PR-Commit-Message: "fix(security): replace ALLOWED_HOSTS wildcard with explicit list"
```

---

## Prompt 3: Content-Security-Policy

**Prompt-Titel:** `[NoCSPHeader] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** HIGH – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py`, `requirements.txt`

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet Django 5.2.17 und lädt externe Ressourcen 
 (Plotly, Bootstrap) sowie Markdown-HTML (mark_safe).

AUFGABE: Implementiere Content-Security-Policy (CSP) Header um XSS-Angriffe zu verhindern.

KONTEXT:
- Die Anwendung rendert Markdown zu HTML mit mark_safe() in views.py
- Externe Skripte: plotly-3.0.0.min.js, bootstrap.bundle.min.js
- Externe CSS: bootstrap.min.css, custom.css
- Kein CDN für Bootstrap/Plotly – alles wird lokal aus /static/ geladen
- django-csp Bibliothek ist empfohlen

LÖSUNGSSCHRITTE:

1. FÜGE django-csp zu requirements.txt hinzu:
   django-csp==3.8

2. REGISTRIERE die App in settings.py INSTALLED_APPS:
   'csp'

3. FÜGE die Middleware hinzu (nach SecurityMiddleware):
   'csp.middleware.CSPMiddleware'

4. KONFIGURIERE CSP-Direktiven in settings.py:
   CSP_DEFAULT_SRC = ("'self'",)
   CSP_SCRIPT_SRC = ("'self'",)
   CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
   CSP_IMG_SRC = ("'self'", "data:")
   CSP_FONT_SRC = ("'self'",)
   CSP_CONNECT_SRC = ("'self'",)
   CSP_FRAME_ANCESTORS = ("'self'",)
   CSP_BASE_URI = ("'self'",)
   CSP_FORM_ACTION = ("'self'",)

5. ERSTELLE Tests in trading/tests/test_csp.py:
   - Teste dass CSP-Header in Response vorhanden sind
   - Teste dass externe Domain nicht erlaubt ist

VALIDIERUNGSKRITERIEN:
- [ ] django-csp in requirements.txt vorhanden
- [ ] csp in INSTALLED_APPS
- [ ] CSPMiddleware in MIDDLEWARE
- [ ] CSP-Direktiven korrekt konfiguriert
- [ ] CSP-Header in Test-Response vorhanden
- [ ] Keine externen Domains in CSP erlaubt (alles lokal)
- [ ] PR-Commit-Message: "feat(security): add Content-Security-Policy headers"
```

---

## Prompt 4: Hardcoded Passphrase

**Prompt-Titel:** `[HardcodedPassphrase] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py` (Zeilen 384–391)

**Status: umgesetzt in 2.4.4.** Die Nachprüfung ergänzt den ursprünglichen Vorschlag um einen `DEBUG=False`-Startabbruch, private Signierschlüssel, einen aktiven Produktions-Gate und rotationsgebundene Session-Nachweise. Zufällige Secrets allein erkennen keine Produktionsumgebung. Siehe [Security-Review](SECURITY_REVIEW_2.4.4.md).

### Ursprünglicher Arena.ai Agenten-Prompt mit korrigiertem Zielcode

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Ersetze den hardcodierten Fallback-Passphrase durch eine dynamisch generierte 
Passphrase, um sicherzustellen dass der Passphrase-Gate in Produktion niemals umgangen 
werden kann.

KONTEXT:
- In settings.py Zeile 391 steht: PASSPHRASE = "local-development-only"
- Dieser Wert ist in der Public-Repository sichtbar
- Wenn PASSPHRASE Environment-Variable nicht gesetzt ist, ist der Gate wirkungslos
- Für lokale Entwicklung sollte eine zufällige Passphrase generiert werden

LÖSUNGSSCHRITTE:

1. ÄNDERE settings.py Zeile 384-391:
   - Ersetze den hardcodierten String durch secrets.token_urlsafe(32)
   - Füge Warning-Log hinzu damit Entwickler wissen welche Passphrase generiert wurde

2. AKTUALISIERE .env.example:
   - Füge Kommentar hinzu dass PASSPHRASE gesetzt werden muss

CODE-ÄNDERUNG:

# VORHER (settings.py):
PASSPHRASE = os.environ.get("PASSPHRASE")
if not PASSPHRASE:
    if env_bool("RENDER", False):
        raise RuntimeError("PASSPHRASE environment variable is required on Render.")
    PASSPHRASE = "local-development-only"

# NACHHER:
import secrets as _secrets

PASSPHRASE = os.environ.get("PASSPHRASE")
if not PASSPHRASE or not PASSPHRASE.strip():
    if env_bool("RENDER", False):
        raise RuntimeError("PASSPHRASE environment variable is required on Render.")
    if not DEBUG:
        raise RuntimeError("PASSPHRASE environment variable is required when DEBUG=False.")
    # Nur für lokale Entwicklung: neuer Wert bei jedem Laden der Settings.
    PASSPHRASE = _secrets.token_urlsafe(32)
    logger.warning(
        "PASSPHRASE nicht gesetzt. Generiert: %s – Nur für lokale Entwicklung!",
        PASSPHRASE
    )

VALIDIERUNGSKRITERIEN:
- [x] Kein hardcodierter Passphrase in settings.py
- [x] Dynamische Generierung mit secrets.token_urlsafe
- [x] Warning-Log wird ausgegeben
- [x] RuntimeError wird bei Render ohne PASSPHRASE geworfen
- [x] .env.example hat Kommentar
- [x] PR-Commit-Message: "fix(security): replace hardcoded passphrase with dynamic generation"
```

---

## Prompt 5: CSRF_COOKIE_HTTPONLY

**Prompt-Titel:** `[MissingCSRFCookieHttpOnly] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py` (Zeilen 162–163)

**Status: umgesetzt in 2.4.5.** `CSRF_COOKIE_HTTPONLY = True` steht in `settings.py` nach der Session-Cookie-Konfiguration und ist unabhängig vom `DEBUG`-Modus aktiv. Das `csrftoken`-Cookie wird mit `HttpOnly` gesetzt; die App-Skripte beziehen das Token unverändert aus dem `{% csrf_token %}`-Formularfeld. `trading/tests/test_csrf_cookie.py` (7 Tests mit erzwungener CSRF-Prüfung) reproduzierte den Vorzustand (rot) und sichert den Fix ab. Nachgeprüft in **2.4.6** mit nun 11 Tests, tatsächlich verwendetem Formular-Token und zusätzlichen Cookie-/Origin-Negativtests. HttpOnly schützt nur den Cookie-Zugriff, nicht allgemein vor XSS; das DOM-Token bleibt sichtbar. Siehe [Changelog](CHANGELOG.md), [Audit §2.5](SECURITY_AUDIT.md) und [Nachweis](SEC-06-rule-lifecycle-authz.md#sec-05-nachprüfung).

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Setze CSRF_COOKIE_HTTPONLY = True um zu verhindern dass das CSRF-Token 
über JavaScript (document.cookie) ausgelesen werden kann.

KONTEXT:
- SESSION_COOKIE_HTTPONLY = True ist bereits gesetzt (gut)
- CSRF_COOKIE_HTTPONLY FEHLT – das Token ist über JS lesbar
- Bei XSS-Anfällen könnte ein Angreifer das CSRF-Token stehlen

CODE-ÄNDERUNG:

# In settings.py nach SESSION_COOKIE_SAMESITE = "Lax" hinzufügen:
CSRF_COOKIE_HTTPONLY = True

VALIDIERUNGSKRITERIEN:
- [ ] CSRF_COOKIE_HTTPONLY = True in settings.py vorhanden
- [ ] CSRF-Cookie ist nicht über document.cookie lesbar
- [ ] PR-Commit-Message: "fix(security): set CSRF_COOKIE_HTTPONLY=True"
```

**Nachprüfung 2.4.10:** SEC-05 bleibt **Fixed**; alle 11 Tests bestanden erneut. Eine gezielte Testprozess-Mutation mit deaktiviertem HttpOnly wird erkannt. [Nachweis](SEC-10-information-disclosure.md#sec-05-nachprüfung).

---

## Prompt 6: X-Content-Type-Options

**Prompt-Titel:** `[MissingContentTypeNosniff] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py`

**Status: Fixed in 2.4.6.** `SECURE_CONTENT_TYPE_NOSNIFF = True` ist explizit und DEBUG-/Render-unabhängig gesetzt. Die vorhandene `SecurityMiddleware` bleibt an erster Stelle; 10 Regressionstests prüfen Settings und ausgelieferte Header einschließlich Fehlern, Downloads und WhiteNoise. **Korrektur des historischen Prompts:** Django 5.2.17 aktiviert nosniff schon per Default; es fehlte die explizite Projektkonfiguration, nicht der Header im geprüften Standard-Stack. [Finding und Fix-Commit](SEC-06-rule-lifecycle-authz.md), [Audit §2.6](SECURITY_AUDIT.md).

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Aktiviere den Security-Header X-Content-Type-Options: nosniff um zu 
verhindern dass Browser MIME-Sniffing durchführen.

KONTEXT:
- Django setzt X-Frame-Options: DENY standardmäßig über XFrameOptionsMiddleware
- SECURE_CONTENT_TYPE_NOSNIFF muss EXPLIZIT aktiviert werden
- Ohne nosniff können Browser MIME-Type erraten und gefährliche Dateien ausführen

CODE-ÄNDERUNG:

# In settings.py nach dem Security-Header-Block (Zeile ~154) hinzufügen:
SECURE_CONTENT_TYPE_NOSNIFF = True

VALIDIERUNGSKRITERIEN:
- [ ] SECURE_CONTENT_TYPE_NOSNIFF = True in settings.py vorhanden
- [ ] Response-Header enthält X-Content-Type-Options: nosniff
- [ ] PR-Commit-Message: "fix(security): enable X-Content-Type-Options nosniff"
```

---

## Prompt 7: Session-Invalidate

**Prompt-Titel:** `[SessionInvalidateMissing] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py` (Zeilen 157–163)

**Status: Fixed in 2.4.7.** `SESSION_COOKIE_AGE = 60 * 60 * 8` und `SESSION_EXPIRE_AT_BROWSER_CLOSE = True` sind DEBUG-/Render-unabhängig in `settings.py` gesetzt. `logout_view` in `trading/views.py` ruft nach `logout(request)` explizit `request.session.flush()` auf, sodass Session-Daten geleert und der Key rotiert werden. Die Passphrase-Freigabe wird dabei ebenfalls ungültig, sodass nach Logout eine erneute Gate-/Login-Authentifizierung erforderlich ist. **11 Regressionstests** in `trading/tests/test_session_invalidate.py` prüfen Settings, DEBUG-/Render-Matrix, Quellcode, POST-Logout-Anonymisierung, CSRF und POST-Only; bestehende 154 Tests bleiben grün. [Finding und Fix-Dokumentation](SEC-07-session-lifetime-invalidation.md), [Audit §2.7](SECURITY_AUDIT.md).

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Reduziere die Session-Lebensdauer und implementiere Session-Invalidate bei 
Abmeldung, um die Sicherheit der signed_cookie-sessions zu erhöhen.

KONTEXT:
- SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
- SESSION_COOKIE_AGE = 60 * 60 * 12 (12 Stunden – zu lang)
- Kein SESSION_EXPIRE_AT_BROWSER_CLOSE
- Bei Abmeldung wird Session nicht explizit invalidiert

LÖSUNGSSCHRITTE:

1. ÄNDERE settings.py:
   - Reduziere SESSION_COOKIE_AGE auf 8 Stunden
   - Setze SESSION_EXPIRE_AT_BROWSER_CLOSE = True

2. SICHERSTELLE dass logout_view die Session invalidiert:
   - In trading/views.py ist logout_view bereits mit @login_required und @require_POST
   - Füge request.session.flush() hinzu

CODE-ÄNDERUNGEN:

# In settings.py:
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 Stunden statt 12
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# In trading/views.py logout_view (Zeile 304-306):
@login_required
@require_POST
def logout_view(request):
    logout(request)
    request.session.flush()  # Session explizit invalidieren
    return redirect("login")

VALIDIERUNGSKRITERIEN:
- [ ] SESSION_COOKIE_AGE auf 8 Stunden reduziert
- [ ] SESSION_EXPIRE_AT_BROWSER_CLOSE = True gesetzt
- [ ] request.session.flush() in logout_view vorhanden
- [ ] PR-Commit-Message: "fix(security): reduce session lifetime and invalidate on logout"
```

---

## Prompt 8: Cache-Control API

**Prompt-Titel:** `[MissingCacheControlAPI] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Security  
**Betroffene Dateien:** `trading/views.py`

**Status: Fixed in 2.4.8.** Der Decorator `no_cache_json` in `trading/views.py` setzt `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` und `Pragma: no-cache` auf allen zehn API-Endpunkten (inklusive `symbol_suggestions_api`, `market_opportunities_api`, `backtesting_status_api`, `backtesting_estimate_api` und `server_resources_api`). Der Decorator ist als innerster Decorator platziert und erfasst damit auch Fehlerantworten (400/503). **9 Regressionstests** in `trading/tests/test_cache_control.py` prüfen Quellcode und ausgelieferte Header über den echten Middleware-Stack; bestehende 165 Tests bleiben grün. SEC-05 (`CSRF_COOKIE_HTTPONLY`) wurde erneut nachgeprüft. [Finding und Fix-Nachweis](SEC-08-cache-control-api.md), [Audit §2.8](SECURITY_AUDIT.md).

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Füge Cache-Control-Header zu allen API-Endpunkten hinzu um zu verhindern dass 
Browser und Proxies sensitive Handelsdaten cachecen.

KONTEXT:
- API-Endpunkte: /api/info/, /api/bot/status/, /api/logs/, /api/data_logs/, /api/trades/
- Diese enthalten sensitive Handelsinformationen
- Keine Cache-Header aktuell → Browser kann Daten cached speichern

LÖSUNGSSCHRITTE:

1. ERSTELLE einen Decorator in trading/views.py:
   - no_cache_json setzt Cache-Control und Pragma Header

2. WENDE den Decorator auf alle API-Views an:
   - info_api, bot_status_api, logs_api, data_logs_api, trades_api
   - symbol_suggestions_api, market_opportunities_api
   - backtesting_status_api, backtesting_estimate_api
   - server_resources_api

CODE-STRUKTUR:

# In trading/views.py ganz oben (nach imports):
from functools import wraps

def no_cache_json(view_func):
    """Decorator der Cache-Control Header für API-Responses setzt.
    
    Verhindert dass Browser und Proxies sensitive Handelsdaten cachecen.
    """
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        return response
    return wrapped

# Anwendung bei API-Views:
@login_required
@require_GET
@no_cache_json
def info_api(request, config_id):
    # ... bestehender Code ...

VALIDIERUNGSKRITERIEN:
- [ ] no_cache_json Decorator existiert
- [ ] Decorator auf alle API-Views angewendet
- [ ] Cache-Control Header in Response vorhanden
- [ ] Pragma: no-cache Header vorhanden
- [ ] PR-Commit-Message: "fix(security): add cache-control headers to API endpoints"
```

---

## Prompt 9: Permissions-Policy

**Prompt-Titel:** `[MissingPermissionsPolicy] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Security  
**Betroffene Dateien:** `trading_bot_project/settings.py`

**Status: Fixed in 2.4.9.** Die zentrale Einstellung `SECURE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"` in `trading_bot_project/settings.py` deaktiviert Kamera, Mikrofon und Geolokation. Die neue Middleware `trading.middleware.PermissionsPolicyMiddleware` (direkt nach der `SecurityMiddleware`) setzt den `Permissions-Policy`-Header aus dieser Einstellung auf jeder Antwort, da Django selbst keinen solchen Header erzeugt. **11 Regressionstests** in `trading/tests/test_permissions_policy.py` prüfen Settings und ausgelieferte Header über den echten Middleware-Stack; bestehende 174 Tests bleiben grün. SEC-05 (`CSRF_COOKIE_HTTPONLY`) wurde erneut nachgeprüft. [Finding und Fix-Nachweis](SEC-09-permissions-policy.md), [Audit §2.9](SECURITY_AUDIT.md).

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Füge Permissions-Policy Header hinzu um den Zugriff auf nicht benötigte 
Browser-APIs (Kamera, Mikrofon, Geolokation) einzuschränken.

KONTEXT:
- Das Projekt benötigt KEINE Kamera, Mikrofon oder Geolokation
- Permissions-Policy ist der Nachfolger von Feature-Policy
- Schützt vor potenziellen Angriffen über diese APIs

CODE-ÄNDERUNG:

# In settings.py nach den anderen Security-Headern:
SECURE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=()"

# Alternativ als eigene Middleware falls django-csp nicht verwendet wird:
# In trading/middleware.py:
class PermissionsPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        return response

VALIDIERUNGSKRITERIEN:
- [ ] Permissions-Policy Header in Response vorhanden
- [ ] Kamera, Mikrofon, Geolokation sind deaktiviert
- [ ] PR-Commit-Message: "fix(security): add Permissions-Policy header"
```

---

## Prompt 10: Information Disclosure

**Status: Fixed in 2.4.10.** Technische Fehler werden in `trading/views.py` und den direkt beteiligten Ausgabewegen nicht mehr in Flash-/JSON-/Report-Meldungen übernommen; `logger.exception` erhält die Diagnose. Teilfehler und gespeicherte Backtest-Fehler sind eingeschlossen. Das Fehler-Log zeigt technische Details nur Staff-Konten unter Beibehaltung der Eigentümerprüfung, auch für alte Einträge. 25 neue Regressionstests (Rot → grün), insgesamt 210 Django-/Python-Tests bestanden. SEC-05 wurde mit allen 11 Tests und einer HttpOnly-Negativkontrolle erneut geprüft. [Finding mit Fix-Commit und Prüfgrenzen](SEC-10-information-disclosure.md), [Audit §2.10](SECURITY_AUDIT.md). Der folgende Prompt beschreibt den historischen Ausgangsbefund.

**Prompt-Titel:** `[InformationDisclosureErrors] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Security  
**Betroffene Dateien:** `trading/views.py` (Zeilen 383, 894)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Security Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Entferne technische Exception-Details aus Benutzer-Fehlermeldungen um keine 
internen Systeminformationen preiszugeben.

KONTEXT:
- In views.py Zeile 383: messages.error(request, f"Bot konnte nicht gestartet werden: {exc}")
- In views.py Zeile 894: Similar pattern
- Exception-Details können DB-Verbindungsfehler, Stacktraces etc. enthalten
- Technische Details sollten nur im Log erscheinen, nicht beim Benutzer

LÖSUNGSSCHRITTE:

1. SUCHE alle Stellen in trading/views.py wo {exc} in messages.error verwendet wird
2. ERSETZE die Exception-Details durch generische Meldungen
3. STELLE SICHER dass die Exception trotzdem geloggt wird

CODE-ÄNDERUNGEN:

# VORHER (views.py Zeile ~383):
messages.error(request, f"Bot konnte nicht gestartet werden: {exc}")

# NACHHER:
messages.error(request, "Bot konnte nicht gestartet werden. Siehe Fehler-Log für Details.")
logger.exception("Bot-Start für Konfiguration %s fehlgeschlagen", config.id)

# VORHER (views.py Zeile ~894):
messages.error(request, f"Bot konnte nicht gestartet werden: {exc}")  # falls vorhanden

# NACHHER:
messages.error(request, "Bot konnte nicht gestartet werden. Siehe Fehler-Log für Details.")
logger.exception("Bot-Start fehlgeschlagen")

VALIDIERUNGSKRITERIEN:
- [ ] Keine {exc} in messages.error/django.messages
- [ ] Exception wird mit logger.exception geloggt
- [ ] Benutzer sieht nur generische Meldung
- [ ] PR-Commit-Message: "fix(security): remove exception details from user messages"
```

---

## Prompt 11: Docker-Passwörter

**Prompt-Titel:** `[DockerDefaultPasswords] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Security  
**Betroffene Dateien:** `docker-compose.yml` (Zeilen 11, 54)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior DevOps Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Ersetze die Standard-Passwörter in docker-compose.yml durch Pflicht-Werte 
(optional:) damit kein Standard-Passwort in der Public-Repo sichtbar ist.

KONTEXT:
- POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-tbot-local-password} (Zeile 54)
- PASSPHRASE: ${PASSPHRASE:-local-t-bot} (Zeile 11 in x-app-environment)
- Standard-Passwörter sind in der Public-Repo sichtbar
- Bei Vergessener Konfiguration sind Datenbank und Passphrase-Gate aktiv

CODE-ÄNDERUNG:

# VORHER (docker-compose.yml):
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-tbot-local-password}
PASSPHRASE: ${PASSPHRASE:-local-t-bot}

# NACHHER:
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}
PASSPHRASE: ${PASSPHRASE:?Set PASSPHRASE in .env}

# ERKLÄRUNG:
# ${VAR:?Fehlermeldung} wirft einen Fehler wenn die Variable nicht gesetzt ist
# Dies verhindert dass versehentlich Standard-Passwörter verwendet werden

AKTUALISIERE .env.example:
# KEINE Defaults für Secrets – diese müssen explizit gesetzt werden
POSTGRES_PASSWORD=change-me-to-a-secure-password
PASSPHRASE=change-me-to-a-secure-passphrase

VALIDIERUNGSKRITERIEN:
- [ ] Keine Standard-Passwörter in docker-compose.yml
- [ ] ${VAR:?...} Syntax für Secrets verwendet
- [ ] .env.example hat Platzhalter-Werte
- [ ] Docker-Compose schlägt fehl ohne .env-Datei
- [ ] PR-Commit-Message: "fix(security): remove default passwords from docker-compose"
```

---

## Prompt 12: Race Condition Bot-Start/Stop

**Prompt-Titel:** `[RaceConditionBotStartStop] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Bug  
**Betroffene Dateien:** `trading/trading_bot.py` (Zeilen 810–817)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet threading für Bot-Management.

AUFGABE: Behebe die Race Condition in TradingBotManager.start_bot() wo is_running() 
während des Lock-Haltens aufgerufen wird und zu verschachtelter Lock-Acquisition führt.

KONTEXT:
- In trading_bot.py Zeile 810-817:
  def start_bot(self, config):
      with self._lock:
          if self.is_running(config.id):  # is_running() acquire _lock intern!
              return self.bots[config.id]
          bot = TradingBot(config, on_exit=self._forget)
          self.bots[config.id] = bot
          bot.start()
          return bot
- is_running() (Zeile 801-808) verwendet self._lock
- self._lock ist ein RLock (Reentrant Lock) → kein Deadlock, aber Code-Smell
- Bei Thread-Konkurrenz kann dies zu unvorhersehbarem Verhalten führen

LÖSUNGSSCHRITTE:

1. ERSTELLE eine interne Methode _is_running_unlocked() die ohne Lock arbeitet
2. VERWENDE _is_running_unlocked() in start_bot() und stop_bot()
3. ERSTELLE eine öffentliche Methode is_running() die den Lock verwendet
4. AKTUALISIERE alle Aufrufer die is_running() extern nutzen

CODE-ÄNDERUNGEN:

# In trading/trading_bot.py TradingBotManager Klasse:

def _is_running_unlocked(self, config_id):
    """Interne Prüfung ob ein Bot läuft – OHNE Lock-Acquisition.
    
    Wird nur von Methoden aufgerufen die bereits self._lock halten.
    Vermeidet verschachtelte Lock-Acquisition und potenzielle Deadlocks.
    """
    bot = self.bots.get(config_id)
    if bot and bot.is_alive():
        return True
    if bot:
        # Bot-Referenz entfernen wenn Thread beendet
        self.bots.pop(config_id, None)
    return False

def is_running(self, config_id):
    """Öffentliche Methode – Thread-sichere Prüfung ob ein Bot läuft."""
    with self._lock:
        return self._is_running_unlocked(config_id)

def start_bot(self, config):
    """Startet einen TradingBot für die gegebene Konfiguration.
    
    Thread-sicher: Prüft zuerst ob bereits ein Bot läuft, startet dann 
    einen neuen Bot wenn nötig.
    """
    with self._lock:
        if self._is_running_unlocked(config.id):
            return self.bots[config.id]
        bot = TradingBot(config, on_exit=self._forget)
        self.bots[config.id] = bot
        bot.start()
        return bot

def stop_bot(self, config):
    """Stoppt den TradingBot für die gegebene Konfiguration.
    
    Thread-sicher: Stoppt den Bot und wartet auf Thread-Beendigung.
    """
    config_id = config.id if hasattr(config, "id") else int(config)
    bot = None
    with self._lock:
        bot = self.bots.get(config_id)
    if bot:
        bot.stop()
        threading.Thread(
            target=bot.join,
            kwargs={"timeout": 30},
            daemon=True,
            name=f"stop-bot-{config_id}",
        ).start()

VALIDIERUNGSKRITERIEN:
- [ ] _is_running_unlocked() existiert ohne Lock-Acquisition
- [ ] is_running() verwendet _is_running_unlocked() mit Lock
- [ ] start_bot() verwendet _is_running_unlocked() statt is_running()
- [ ] Keine verschachtelten Lock-Acquisitions mehr
- [ ] Tests bestehen (TradingBotTests in test_core.py)
- [ ] PR-Commit-Message: "fix(bot): resolve race condition in bot start/stop"
```

---

## Prompt 13: db_restore_state Async

**Prompt-Titel:** `[DbRestoreStateSync] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Bug  
**Betroffene Dateien:** `trading/trading_bot.py` (Zeilen 236–249, 289)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet asyncio mit sync_to_async für DB-Zugriffe.

AUFGABE: Mache db_restore_state() async-fähig damit der Bot-Thread bei großen Tabellen 
nicht blockiert wird.

KONTEXT:
- db_restore_state() (Zeile 236-249) ist eine synchrone Funktion
- Sie wird im TradingBot.__init__ (Zeile 289) synchron aufgerufen
- Andere db_-Funktionen (db_get_config, db_mark_bot_stopped, etc.) sind async mit 
  @sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
- Bei großen Tabellen (20.000+ Zeilen) kann der Aufruf blockieren

LÖSUNGSSCHRITTE:

1. FÜGE @sync_to_async Decorator zu db_restore_state() hinzu
2. MACHE _restore_state() zu einer async Methode
3. Rufe _restore_state() mit await auf
4. Passe den TradingBot.__init__ an

CODE-ÄNDERUNGEN:

# VORHER (trading_bot.py Zeile 236-249):
@db_safe(suppress=True)
def db_restore_state(config_id):
    return list(
        TradingLog.objects.filter(configuration_id=config_id)
        .order_by("timestamp", "id")
        .values(...)
    )

# NACHHER:
@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_restore_state(config_id):
    """Stellt den Bot-Zustand aus der Datenbank wieder her.
    
    Lädt alle TradingLogs für die gegebene Konfiguration und berechnet
    den aktuellen Portfolio-Zustand (offene Positionen, realisierter Gewinn).
    
    Thread-safe: Wird im BOT_DB_EXECUTOR ausgeführt um den asyncio-Thread 
    nicht zu blockieren.
    """
    return list(
        TradingLog.objects.filter(configuration_id=config_id)
        .order_by("timestamp", "id")
        .values(
            "symbol", "action", "price", "amount", "fee_amount", "pl_nominal"
        )
    )

# VORHER (TradingBot.__init__ Zeile 289):
self._restore_state()

# NACHHER:
# Da __init__ synchron ist, müssen wir den async-Aufruf koordinieren:
import asyncio

def __init__(self, config, on_exit=None):
    super().__init__(daemon=True, name=f"trading-bot-{config.id}")
    # ... andere Initialisierungen ...
    
    # State-Restoration wird im run() vor der Hauptschleife ausgeführt
    self._initial_state_restored = False

async def _restore_state(self):
    """Stellt den Bot-Zustand async aus der DB wieder her."""
    logs = await db_restore_state(self.config_id) or []
    self.realized_pl = sum(
        (log["pl_nominal"] or Decimal(0) for log in logs if log["action"] == "sell"),
        Decimal(0),
    )
    for log in logs:
        if log["action"] == "buy":
            self.positions[log["symbol"]] = {
                "price": log["price"],
                "amount": log["amount"],
                "buy_fee": log["fee_amount"],
            }
        elif log["action"] == "sell":
            self.positions.pop(log["symbol"], None)
    self._sync_symbols()
    self._initial_state_restored = True

async def main_loop(self):
    """Hauptschleife des TradingBots."""
    # Zuerst Zustand wiederherstellen
    if not self._initial_state_restored:
        await self._restore_state()
    
    while self.running:
        # ... bestehender Code ...

VALIDIERUNGSKRITERIEN:
- [ ] db_restore_state() hat @sync_to_async Decorator
- [ ] _restore_state() ist async Methode
- [ ] main_loop() ruft _restore_state() mit await auf
- [ ] Bot blockiert nicht bei großen Tabellen
- [ ] Tests bestehen
- [ ] PR-Commit-Message: "fix(bot): make db_restore_state async for non-blocking operation"
```

---

## Prompt 14: CsvEcho True-Stream

**Prompt-Titel:** `[CsvEchoNotTrueStream] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Bug  
**Betroffene Dateien:** `trading/views.py` (Zeilen 1136–1138)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Ersetze die benutzerdefinierte _CsvEcho-Klasse durch io.StringIO für 
bessere Kompatibilität mit csv.writer.

KONTEXT:
- _CsvEcho (Zeile 1136-1138) implementiert nur write() Methode
- csv.writer erwartet StringIO-ähnliches Objekt
- StreamingHttpResponse gibt Zeilen als Strings zurück
- Funktioniert, aber ist nicht ideal

CODE-ÄNDERUNG:

# VORHER (trading/views.py):
class _CsvEcho:
    def write(self, value):
        return value

# NACHHER:
import io

# _CsvEcho kann komplett entfernt werden – io.StringIO wird direkt verwendet

# In generate_report_csv:
def generate_report_csv(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    queryset = config.logs.all().order_by("timestamp", "id")

    def rows():
        yield "\ufeff"  # BOM für Excel-Kompatibilität
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        # Header-Zeile
        writer.writerow([
            "timestamp", "symbol", "action", "price", "amount",
            "fee_amount", "order_id", "pl_nominal", "pl_relative",
            "total_pl", "current_capital", "tank",
        ])
        yield buffer.getvalue()
        # Daten-Zeilen
        for log in queryset.iterator(chunk_size=1000):
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow([
                timezone.localtime(log.timestamp).isoformat(),
                log.symbol, log.action, log.price, log.amount,
                log.fee_amount, log.order_id, log.pl_nominal,
                log.pl_relative, log.total_pl, log.current_capital, log.tank,
            ])
            yield buffer.getvalue()

    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_report_filename(config, "csv")}"'
    return response

VALIDIERUNGSKRITERIEN:
- [ ] _CsvEcho-Klasse ist entfernt
- [ ] io.StringIO wird verwendet
- [ ] CSV-Export funktioniert korrekt
- [ ] Tests bestehen
- [ ] PR-Commit-Message: "refactor(views): replace _CsvEcho with io.StringIO"
```

---

## Prompt 15: config_id Validierung

**Prompt-Titel:** `[ConfigIdValidation] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Bug  
**Betroffene Dateien:** `trading/views.py` (Zeilen 767–789)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Füge explizite Ganzzahl-Validierung für config_id in bot_status_api hinzu 
statt sich auf Django's automatischen 404 zu verlassen.

KONTEXT:
- bot_status_api (Zeile 767-789) liest config_id aus GET-Parameter
- Aktuell: if not config_id: return 400
- Bei "abc" als config_id gibt Django automatisch 404
- Fehlermeldung ist nicht benutzerfreundlich

CODE-ÄNDERUNG:

# VORHER (trading/views.py):
@login_required
@require_GET
def bot_status_api(request):
    config_id = request.GET.get("config_id")
    if not config_id:
        return JsonResponse({"error": "config_id fehlt"}, status=400)
    config = get_object_or_404(Configuration, id=config_id, user=request.user)

# NACHHER:
@login_required
@require_GET
def bot_status_api(request):
    config_id = request.GET.get("config_id")
    if not config_id:
        return JsonResponse({"error": "config_id fehlt"}, status=400)
    if not config_id.isdigit():
        return JsonResponse(
            {"error": "config_id muss eine gültige Ganzzahl sein"},
            status=400
        )
    config = get_object_or_404(Configuration, id=config_id, user=request.user)

VALIDIERUNGSKRITERIEN:
- [ ] config_id.isdigit() Prüfung vorhanden
- [ ] Klare Fehlermeldung bei ungültigem config_id
- [ ] HTTP 400 Status-Code bei ungültigem Input
- [ ] PR-Commit-Message: "fix(views): add config_id validation in bot_status_api"
```

---

## Prompt 16: Indikator-Memoisierung

**Prompt-Titel:** `[IndicatorMemoization] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Performance  
**Betroffene Dateien:** `trading/backtesting.py` (Zeilen 13–51)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet Decimal für präzise Finanzberechnungen.

AUFGABE: Implementiere Memoisierung für die calculate_indicators() Methode um 
redundante Decimal-Operationen bei Backtesting zu vermeiden.

KONTEXT:
- calculate_indicators() (Zeile 14-51) wird für jeden Backtest-Kandidaten neu berechnet
- Bei 20.000 Kombinationen mit 5.000 Preispunkten = 100 Millionen Operationen
- Die Berechnung ist deterministisch für (prices, idx) Paare
- prices-Liste bleibt während eines Backtests unverändert

LÖSUNGSSCHRITTE:

1. ERSTELLE einen LRU-Cache für calculate_indicators()
2. VERWENDE id(prices) als Cache-Key (Liste bleibt unverändert)
3. STELLE SICHER dass der Cache zwischen Backtests geleert wird

CODE-ÄNDERUNGEN:

# In trading/backtesting.py:

# Cache-Dictionary für Indikator-Ergebnisse
_indicator_cache = {}
_indicator_cache_lock = threading.Lock()

@staticmethod
def calculate_indicators(prices, idx):
    """Berechnet die drei Strategieindikatoren am angegebenen Index.
    
    Die Berechnung ist deterministisch – Ergebnisse werden gecacht um 
    redundante Decimal-Operationen bei Backtesting zu vermeiden.
    
    Args:
        prices: Liste von Decimal-Preisen
        idx: Index des zu berechnenden Datenpunkts (mindestens 2)
    
    Returns:
        Tuple: (acceleration, deltadelta, current_nda)
    """
    # Cache-Key: (id der Liste, Index) – Liste bleibt während Backtests unverändert
    cache_key = (id(prices), idx)
    
    with _indicator_cache_lock:
        if cache_key in _indicator_cache:
            return _indicator_cache[cache_key]
    
    # Bestehende Berechnung...
    current_price = _decimal(prices[idx])
    previous_price = _decimal(prices[idx - 1])
    older_price = _decimal(prices[idx - 2])
    
    current_da = current_price - previous_price
    current_nda = (
        (current_da / previous_price * Decimal(100)).quantize(
            _EIGHT_PLACES, rounding=ROUND_HALF_UP
        )
        if previous_price
        else Decimal(0)
    )
    previous_da = previous_price - older_price
    previous_nda = (
        (previous_da / previous_price * Decimal(100)).quantize(
            _EIGHT_PLACES, rounding=ROUND_HALF_UP
        )
        if previous_price
        else Decimal(0)
    )
    dva = (current_nda - previous_nda).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
    acceleration = (
        (dva / previous_nda).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
        if previous_nda
        else Decimal(0)
    )
    deltadelta = ((current_nda + previous_nda) / Decimal(2)).quantize(
        _EIGHT_PLACES, rounding=ROUND_HALF_UP
    )
    
    result = (acceleration, deltadelta, current_nda)
    
    with _indicator_cache_lock:
        _indicator_cache[cache_key] = result
    
    return result

@classmethod
def clear_indicator_cache(cls):
    """Leert den Indikator-Cache – muss zwischen Backtests aufgerufen werden."""
    with _indicator_cache_lock:
        _indicator_cache.clear()

# In tasks.py run_backtest() am Ende der Funktion:
from .backtesting import Backtesting
# ... nach dem Backtest:
Backtesting.clear_indicator_cache()

VALIDIERUNGSKRITERIEN:
- [ ] Cache-Dictionary und Lock vorhanden
- [ ] Cache-Key using id(prices) und idx
- [ ] clear_indicator_cache() Methode vorhanden
- [ ] Cache wird zwischen Backtests geleert
- [ ] Performance-Besserung messbar
- [ ] PR-Commit-Message: "perf(backtesting): add indicator memoization cache"
```

---

## Prompt 17: DB-Trim Batch-Delete

**Prompt-Titel:** `[DbTrimBatchDelete] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Performance  
**Betroffene Dateien:** `trading/trading_bot.py` (Zeilen 176–183)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt使用t Django ORM für Datenbankzugriffe.

AUFGABE: Implementiere Batch-Delete für db_trim_datalog() um Datenbank-Locks bei 
großen Tabellen zu vermeiden.

KONTEXT:
- db_trim_datalog() (Zeile 176-183) löscht alte DataLog-Einträge
- Bei 20.000+ Zeilen kann der DELETE-Befehl lange dauern
- Ein einzelner DELETE kann Datenbank-Locks verursachen
- MAX_DATA_LOGS_PER_SYMBOL = 20.000 (in settings.py)

CODE-ÄNDERUNG:

# VORHER (trading_bot.py):
@db_safe(suppress=True)
def db_trim_datalog(config_id, symbol, max_rows):
    queryset = DataLog.objects.filter(configuration_id=config_id, symbol=symbol)
    cutoff_id = (
        queryset.order_by("-id").values_list("id", flat=True)[max_rows : max_rows + 1].first()
    )
    if cutoff_id is not None:
        queryset.filter(id__lte=cutoff_id).delete()

# NACHHER:
@db_safe(suppress=True)
def db_trim_datalog(config_id, symbol, max_rows):
    """Löscht alte DataLog-Einträge in Batches um Datenbank-Locks zu vermeiden.
    
    Bei großen Tabellen (20.000+ Zeilen) kann ein einzelner DELETE-Befehl
    die Datenbank für andere Operationen blockieren. Diese Funktion löscht
    in 1000er-Schritten um die Lock-Dauer zu minimieren.
    
    Args:
        config_id: ID der Trading-Konfiguration
        symbol: Handelspaar (z.B. "BTC/USDT")
        max_rows: Maximale Anzahl zu behaltender Zeilen
    """
    cutoff_id = (
        DataLog.objects.filter(configuration_id=config_id, symbol=symbol)
        .order_by("-id")
        .values_list("id", flat=True)[max_rows : max_rows + 1]
        .first()
    )
    if cutoff_id is None:
        return
    
    # Batch-Delete in 1000er-Schritten um Locks zu minimieren
    BATCH_SIZE = 1000
    while True:
        deleted_count, _ = DataLog.objects.filter(
            configuration_id=config_id,
            symbol=symbol,
            id__lte=cutoff_id,
        )[:BATCH_SIZE].delete()
        
        if deleted_count == 0:
            break

VALIDIERUNGSKRITERIEN:
- [ ] Batch-Delete in 1000er-Schritten implementiert
- [ ] Funktion bricht bei leeren Deletes ab
- [ ] Keine langen Database-Locks mehr
- [ ] Kommentar erklärt Batch-Strategie
- [ ] PR-Commit-Message: "perf(bot): implement batch delete for db_trim_datalog"
```

---

## Prompt 18: Indikator-Deduplizierung

**Prompt-Titel:** `[DuplicatedIndicatorLogic] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Code Quality  
**Betroffene Dateien:** `trading/backtesting.py` (Zeilen 14–51), `trading/trading_bot.py` (Zeilen 577–591)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Dedupliziere die Indikatorberechnung (NDA, DeltaDelta, Acceleration) die in 
backtesting.py und trading_bot.py fast identisch implementiert ist.

KONTEXT:
- backtesting.py Zeile 14-51: Backtesting.calculate_indicators()
- trading_bot.py Zeile 577-591: TradingBot.calculate_and_store()
- Beide berechnen dieselben Indikatoren mit leicht unterschiedlicher Syntax
- Führt zu Code-Duplizierung und Wartungsproblemen

LÖSUNGSSCHRITTE:

1. ERSTELLE ein neues Modul trading/indicators.py
2. VERVOLLSTÄNDIGE die calculate_trading_indicators() Funktion
3. ERSETZE den Code in backtesting.py durch Import
4. ERSETZE den Code in trading_bot.py durch Import
5. STELLE SICHER dass alle Tests bestehen

CODE-STRUKTUR:

# trading/indicators.py (NEU)
"""Zentrale Indikatorberechnungen für Trading-Strategien.

Dieses Modul enthält die Berechnung der drei Kernindikatoren:
- NDA (Normalisierte Preisänderung)
- DeltaDelta (geglättetes Momentum)
- Acceleration (Beschleunigung)

Die Indikatoren werden sowohl vom Live-Trading-Bot als auch vom 
Backtesting-System verwendet.
"""

from decimal import ROUND_HALF_UP, Decimal

# Konstante für die Dezimalgenauigkeit (8 Nachkommastellen)
_EIGHT_PLACES = Decimal("0.00000001")


def calculate_trading_indicators(prices, idx):
    """Berechnet die drei Strategieindikatoren am angegebenen Index.
    
    Die Berechnung erfolgt in präziser Decimal-Arithmetik um 
    Rundungsfehler bei Finanzberechnungen zu vermeiden.
    
    Args:
        prices: Liste von Decimal-Preisen (mindestens idx+1 Elemente)
        idx: Index des zu berechnenden Datenpunkts (mindestens 2)
    
    Returns:
        Tuple[Decimal, Decimal, Decimal]: (acceleration, deltadelta, nda)
    
    Raises:
        IndexError: Wenn idx < 2 oder idx >= len(prices)
        DivisionByZero: Wenn previous_price == 0 (sollte nicht vorkommen)
    """
    current_price = Decimal(str(prices[idx]))
    previous_price = Decimal(str(prices[idx - 1]))
    older_price = Decimal(str(prices[idx - 2]))
    
    # NDA: Normalisierte Preisänderung in Prozent
    current_da = current_price - previous_price
    nda = (
        (current_da / previous_price * Decimal(100)).quantize(
            _EIGHT_PLACES, rounding=ROUND_HALF_UP
        )
        if previous_price
        else Decimal(0)
    )
    
    # Vorherige NDA berechnen
    previous_da = previous_price - older_price
    previous_nda = (
        (previous_da / previous_price * Decimal(100)).quantize(
            _EIGHT_PLACES, rounding=ROUND_HALF_UP
        )
        if previous_price
        else Decimal(0)
    )
    
    # DVA: Differenz der NDA-Werte
    dva = (nda - previous_nda).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
    
    # Acceleration: Beschleunigung relativ zur vorherigen NDA
    acceleration = (
        (dva / previous_nda).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
        if previous_nda
        else Decimal(0)
    )
    
    # DeltaDelta: Geglättetes Momentum (Durchschnitt beider NDA-Werte)
    deltadelta = ((nda + previous_nda) / Decimal(2)).quantize(
        _EIGHT_PLACES, rounding=ROUND_HALF_UP
    )
    
    return acceleration, deltadelta, nda

# In trading/backtesting.py:
from .indicators import calculate_trading_indicators

class Backtesting:
    @staticmethod
    def calculate_indicators(prices, idx):
        """Wrapper für abwärtskompatibilität – delegiert an zentrale Funktion."""
        return calculate_trading_indicators(prices, idx)
    
    # ... restlicher Code bleibt gleich

# In trading/trading_bot.py:
from .indicators import calculate_trading_indicators

class TradingBot:
    async def calculate_and_store(self, symbol):
        prices = self.price_buffer[symbol]
        if len(prices) < 3:
            return
        
        current, previous, older = prices[-1], prices[-2], prices[-3]
        
        # Zentrale Indikatorberechnung verwenden
        acceleration, deltadelta, nda = calculate_trading_indicators(prices, len(prices) - 1)
        
        # ... restlicher Code bleibt gleich

VALIDIERUNGSKRITERIEN:
- [ ] trading/indicators.py existiert mit calculate_trading_indicators()
- [ ] backtesting.py importiert aus indicators.py
- [ ] trading_bot.py importiert aus indicators.py
- [ ] Kein duplizierter Indikator-Code mehr
- [ ] Alle Tests bestehen
- [ ] PR-Commit-Message: "refactor: extract indicator logic to shared module"
```

---

## Prompt 19: Type-Hints Views

**Prompt-Titel:** `[MissingTypeHintsViews] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Tech Debt  
**Betroffene Dateien:** `trading/views.py`

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet Django 5.2.17.

AUFGABE: Füge Type-Hints zu den wichtigsten Views und Hilfsfunktionen in 
trading/views.py hinzu um die Code-Qualität und Wartbarkeit zu verbessern.

KONTEXT:
- views.py enthält viele Funktionen ohne Type-Hints
- Django-typische Signaturen: (request: HttpRequest) -> HttpResponse
- Hilfsfunktionen benötigen Rückgabewerte-Typen
- Beginne mit den wichtigsten öffentlichen Funktionen

LÖSUNGSSCHRITTE:

1. FÜGE Import für typing hinzu:
   from typing import Any

2. FÜGE Type-Hints zu diesen Funktionen hinzu:
   - _portfolio_snapshot(config: Configuration) -> dict[str, Any]
   - _realized_profit(config: Configuration) -> Decimal
   - _cash_flow(log: TradingLog) -> Decimal
   - _cash_series(logs: list, opening_cash: Decimal) -> list[dict[str, Any]]
   - calculate_performance_metrics(logs: list) -> dict[str, float]
   - health_view(request: HttpRequest) -> JsonResponse
   - home(request: HttpRequest) -> HttpResponseRedirect
   - login_view(request: HttpRequest) -> HttpResponse
   - config_view(request: HttpRequest) -> HttpResponse
   - dashboard_view(request: HttpRequest) -> HttpResponse

3. FÜGE Documentstrings zu public Views hinzu

CODE-BEISPIEL:

from typing import Any
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseRedirect

def _portfolio_snapshot(config: Configuration) -> dict[str, Any]:
    """Erstellt einen Portfolio-Snapshot für die angegebene Konfiguration.
    
    Args:
        config: Trading-Konfiguration
    
    Returns:
        Dictionary mit Portfolio-Metriken:
        - cash: Verfügbares Kapital
        - realized_profit: Realisierter Gewinn
        - invested: Investiertes Kapital
        - market_value: Marktwert offener Positionen
        - equity: Gesamtes Eigenkapital
        - unrealized_profit: Unrealisierter Gewinn
        - positions: Liste offener Positionen
    """
    # ... bestehender Code

def health_view(request: HttpRequest) -> JsonResponse:
    """Health-Check-Endpunkt für Monitoring und Load-Balancer."""
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})

def home(request: HttpRequest) -> HttpResponseRedirect:
    """Leitet je nach Authentifizierungsstatus auf Dashboard oder Login weiter."""
    return redirect("dashboard" if request.user.is_authenticated else "login")

VALIDIERUNGSKRITERIEN:
- [ ] Type-Hints für alle public Views vorhanden
- [ ] Type-Hints für Hilfsfunktionen vorhanden
- [ ] Import für typing vorhanden
- [ ] Documentstrings für public Views vorhanden
- [ ] mypy oder pyright findet keine Fehler
- [ ] PR-Commit-Message: "refactor(views): add type hints and docstrings"
```

---

## Prompt 20: __all__ Exports

**Prompt-Titel:** `[MissingAllExports] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** LOW – Tech Debt  
**Betroffene Dateien:** `trading/__init__.py`

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal.

AUFGABE: Definiere __all__ für das trading Modul um die öffentliche API explizit 
zu machen und Exporte zu kontrollieren.

KONTEXT:
- trading/__init__.py ist aktuell leer oder hat nur Imports
- __all__ definiert welche Namen beim "from trading import *" exportiert werden
- Verbessert IDE-Unterstützung und verhindert unbeabsichtigte Imports

CODE-ÄNDERUNG:

# trading/__init__.py
"""Trading-Modul für t-bot-lokal.

Dieses Modul enthält die Kernkomponenten des Paper-Trading-Systems:
- trading_bot: Live-Trading-Bot mit WebSocket-Marktdaten
- backtesting: Backtesting-Engine für Strategieoptimierung
- market_data: Adapter für öffentliche Exchange-APIs
- views: Django-Views für Web-Oberfläche
- models: Datenbankmodelle
- tasks: Celery-Tasks für asynchrone Verarbeitung
- forms: Django-Formulare für Eingabevalidierung
- indicators: Zentrale Indikatorberechnungen
"""

__all__ = [
    # Kernkomponenten
    'trading_bot',
    'backtesting',
    'market_data',
    'indicators',
    
    # Django-Komponenten
    'views',
    'models',
    'tasks',
    'forms',
    
    # Hilfsfunktionen
    'symbols',
    'resource_optimizer',
    'market_scanner',
    'worker_status',
]

# Explizite Imports für bessere IDE-Unterstützung
from . import (
    trading_bot,
    backtesting,
    market_data,
    indicators,
    views,
    models,
    tasks,
    forms,
    symbols,
    resource_optimizer,
    market_scanner,
    worker_status,
)

VALIDIERUNGSKRITERIEN:
- [ ] __all__ ist definiert mit allen public Modulen
- [ ] Explizite Imports vorhanden
- [ ] Docstring für Modul vorhanden
- [ ] from trading import * funktioniert wie erwartet
- [ ] PR-Commit-Message: "refactor: add __all__ exports to trading module"
```

---

## Prompt 21: info_api Aggregation

**Prompt-Titel:** `[InfoApiMemoryOptimization] – Arena.ai Agent Prompt`  
**Severity & Kategorie:** MEDIUM – Performance  
**Betroffene Dateien:** `trading/views.py` (Zeilen 677–762)

### Der vollständige Arena.ai Agenten-Prompt

```
Du bist ein Senior Python Engineer für ein Django-basiertes Krypto-Paper-Trading-System 
 namens t-bot-lokal. Das Projekt verwendet Django ORM mit PostgreSQL.

AUFGABE: Optimiere info_api() um Aggregationen auf DB-Ebene durchzuführen statt 
alle Logs in den Speicher zu laden.

KONTEXT:
- info_api (Zeile 677-762) lädt MAX_LOG_ROWS = 2000 Zeilen in den Speicher
- calculate_performance_metrics() verarbeitet alle Zeilen in Python
- Bei großen Konfigurationen hoher RAM-Verbrauch
- Einzelne Metriken können effizienter mit Django ORM aggregate() berechnet werden

LÖSUNGSSCHRITTE:

1. ERSTELLE Hilfsfunktionen für DB-Aggregationen
2. ERSETZE calculate_performance_metrics() durch effizientere Version
3. STELLE SICHER dass die API weiterhin alle benötigten Daten liefert

CODE-ÄNDERUNGEN:

# In trading/views.py:

from django.db.models import Sum, Count, Case, When, F, Q

def _calculate_metrics_from_db(config: Configuration, limit: int = 2000) -> dict[str, Any]:
    """Berechnet Performance-Metriken direkt in der Datenbank.
    
    Verwendet Django ORM Aggregationen statt aller Zeilen in Python
    zu verarbeiten – signifikant effizienter für große Datensätze.
    
    Args:
        config: Trading-Konfiguration
        limit: Maximale Anzahl zu berücksichtigender Logs
    
    Returns:
        Dictionary mit Performance-Metriken
    """
    # Nur die neuesten logs für Metriken verwenden
    logs = config.logs.all().order_by("-timestamp", "-id")[:limit]
    
    # Aggregationen auf DB-Ebene
    stats = logs.aggregate(
        total_trades=Count("id"),
        buy_orders=Count("id", filter=Q(action="buy")),
        sell_orders=Count("id", filter=Q(action="sell")),
        total_pl=Sum("pl_nominal", filter=Q(action="sell")),
    )
    
    # Win/Loss berechnen
    sell_logs = logs.filter(action="sell")
    win_stats = sell_logs.aggregate(
        wins=Count("id", filter=Q(pl_nominal__gt=0)),
        losses=Count("id", filter=Q(pl_nominal__lte=0)),
        avg_profit=Sum("pl_nominal") / Count("id"),
        max_win=Max("pl_nominal"),
        min_loss=Min("pl_nominal"),
    )
    
    # Metriken zusammenbauen
    total_sells = (win_stats["wins"] or 0) + (win_stats["losses"] or 0)
    
    return {
        "buy_orders": stats["buy_orders"] or 0,
        "sell_orders": stats["sell_orders"] or 0,
        "total_wins": win_stats["wins"] or 0,
        "total_losses": win_stats["losses"] or 0,
        "win_rate": round((win_stats["wins"] or 0) / total_sells * 100, 2) if total_sells else 0,
        "avg_profit": round(float(win_stats["avg_profit"] or 0), 4),
        "max_win": round(float(win_stats["max_win"] or 0), 4),
        "max_loss": round(float(win_stats["min_loss"] or 0), 4),
    }

@login_required
@require_GET
@no_cache_json
def info_api(request: HttpRequest, config_id: int) -> JsonResponse:
    """API-Endpunkt für umfassende Trading-Informationen.
    
    Liefert Portfolio-Metriken, Performance-Statistiken, Equity-Kurve
    und Cash-Verlauf für die angegebene Konfiguration.
    """
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    
    # Aggregationen auf DB-Ebene für Metriken
    metrics = _calculate_metrics_from_db(config)
    
    # Portfolio-Snapshot
    portfolio = _portfolio_snapshot(config)
    
    # Für Equity-Kurve und Cash-Verlauf weiterhin Logs laden
    logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
    
    # ... restlicher Code für equity/cash Berechnung ...
    
    return JsonResponse({
        "current_capital": float(portfolio["cash"]),
        # ... andere Felder ...
        **metrics,
    })

VALIDIERUNGSKRITERIEN:
- [ ] _calculate_metrics_from_db() Funktion existiert
- [ ] Django ORM aggregate() wird verwendet
- [ ] Weniger Daten werden in den Speicher geladen
- [ ] API liefert dieselben Daten wie zuvor
- [ ] Performance-Besserung messbar
- [ ] Tests bestehen
- [ ] PR-Commit-Message: "perf(views): optimize info_api with DB aggregation"
```

---

## Zusammenfassung

| # | Prompt-Titel | Severity | Kategorie | Aufwand |
|---|--------------|----------|-----------|---------|
| 1 | NoRateLimiting | HIGH | Security | 2h |
| 2 | AllowedHostsWildcard | HIGH | Security | 30min |
| 3 | NoCSPHeader | HIGH | Security | 3h |
| 4 | HardcodedPassphrase | MEDIUM | Security | 30min |
| 5 | MissingCSRFCookieHttpOnly | MEDIUM | Security | 5min |
| 6 | MissingContentTypeNosniff | MEDIUM | Security | 5min |
| 7 | SessionInvalidateMissing | MEDIUM | Security | 1h |
| 8 | MissingCacheControlAPI | MEDIUM | Security | 2h |
| 9 | MissingPermissionsPolicy | LOW | Security | 30min |
| 10 | InformationDisclosureErrors | LOW | Security | 30min |
| 11 | DockerDefaultPasswords | LOW | Security | 15min |
| 12 | RaceConditionBotStartStop | MEDIUM | Bug | 2h |
| 13 | DbRestoreStateSync | MEDIUM | Bug | 2h |
| 14 | CsvEchoNotTrueStream | LOW | Bug | 30min |
| 15 | ConfigIdValidation | LOW | Bug | 15min |
| 16 | IndicatorMemoization | MEDIUM | Performance | 3h |
| 17 | DbTrimBatchDelete | MEDIUM | Performance | 2h |
| 18 | DuplicatedIndicatorLogic | MEDIUM | Code Quality | 3h |
| 19 | MissingTypeHintsViews | LOW | Tech Debt | 2h |
| 20 | MissingAllExports | LOW | Tech Debt | 30min |
| 21 | InfoApiMemoryOptimization | MEDIUM | Performance | 3h |

**Gesamtaufwand:** ~30 Stunden

Jeder Prompt ist eigenständig und kann direkt in Arena.ai verwendet werden.
