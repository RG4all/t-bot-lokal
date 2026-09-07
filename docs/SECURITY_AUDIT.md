# Code Review & Security Audit – t-bot-lokal

**Audit-Datum:** 07. September 2026  
**Reviewer:** Automated Security Audit  
**Scope:** Gesamte Codebasis (Django 5.2.17, Trading Bot, Backtesting, Docker-Infra)

---

> **Historischer Scan, nicht der aktuelle Freigabestatus.** Mehrere nachfolgende Aussagen beziehen sich auf Code vor 2.4.1–2.4.6 oder auf falsch eingeordnete Django-Defaults. Die ursprünglichen Befunde bleiben nachvollziehbar; die aktuelle, kontextbezogene Bewertung des Passphrase-Fixes und der mitgeprüften Pfade steht im [Security-Review 2.4.4](SECURITY_REVIEW_2.4.4.md). Daraus folgt keine vollständige Neubewertung aller historischen Performance-/Architekturvorschläge.

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

### 2.1 HOCH – Kein Rate-Limiting auf Auth-Endpunkten

**Datei:** `trading/views.py` (Zeilen 250–306)

Das Passphrase-Gate, Login und Registrierung haben keinerlei Brute-Force-Schutz. Ein Angreifer kann beliebig viele Passwort-/Passphrase-Versuche starten.

**Auswirkung:** Credential-Stuffing, Passwort-Brute-Force, Account-Übernahme.

**Lösungsvorschlag:**

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

### 2.2 HOCH – DEBUG-Modus mit `ALLOWED_HOSTS = ["*"]`

**Datei:** `trading_bot_project/settings.py` (Zeilen 61, 71–72)

```python
DEBUG = env_bool("DEBUG", default=not env_bool("RENDER", False))
# ...
if DEBUG:
    ALLOWED_HOSTS.append("*")
```

**Auswirkung:** Host-Header-Injection, Cache-Poisoning, CSRF-Bypass über Host-Header. Ein Angreifer kann eine Anfrage mit beliebigem Host senden und Django-Session-Cookies oder CSRF-Tokens erhalten.

**Lösungsvorschlag:**

```python
# Niemals "*" verwenden – stattdessen explizite Liste:
if DEBUG:
    ALLOWED_HOSTS += ['localhost', '127.0.0.1', 'tbot.local', '[::1]']
```

---

### 2.3 HOCH – Kein Content-Security-Policy (CSP) Header

**Datei:** `trading_bot_project/settings.py`

Es wird weder `django-csp` noch ein manueller CSP-Header konfiguriert. Die Anwendung lädt externe Skripte (Plotly, Bootstrap) und rendert Markdown zu HTML (`mark_safe`), was bei fehlendem CSP zu XSS-Vektoren führt.

**Auswirkung:** Cross-Site-Scripting über injectetes Markdown oder kompromittierte CDN-Ressourcen.

**Lösungsvorschlag:**

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

### 2.5 CSRF-Cookie ohne HttpOnly – Fixed in 2.4.5, nachgeprüft in 2.4.6

**Datei:** `trading_bot_project/settings.py` · **Ursprünglicher Fix:** [PR #13](https://github.com/RG4all/t-bot-lokal/pull/13)

**Vorzustand:** Das Projekt hatte nur `SESSION_COOKIE_HTTPONLY = True` gesetzt; das CSRF-Cookie war wegen Djangos `CSRF_COOKIE_HTTPONLY = False` über `document.cookie` lesbar.

**Umgesetzt:** `CSRF_COOKIE_HTTPONLY = True` steht unabhängig vom DEBUG-Modus nach der Session-Cookie-Konfiguration. In Produktion ergänzt `CSRF_COOKIE_SECURE` das Flag. Die App-Skripte lesen das Token aus dem `{% csrf_token %}`-Formularfeld und benötigen keinen Cookie-Zugriff.

**Korrektur der Risikobeschreibung:** HttpOnly verhindert nur den direkten Zugriff auf das Cookie. Skripte derselben Origin können weiterhin das DOM-Token lesen und authentifizierte Requests ausführen; der Fix ist zusätzliche Cookie-Härtung, kein allgemeiner XSS-Schutz oder Ersatz für CSP/CSRF-Prüfungen.

**Nachprüfung 2.4.6:** `trading/tests/test_csrf_cookie.py` enthält jetzt 11 Tests. Der erfolgreiche Login verwendet tatsächlich den maskierten Formular-Token. Produktions-/Render-Cookie-Flags, fehlende Tokens/Cookies, Tokens anderer Clients und fremde Origins sind geprüft. Eine Testprozess-Mutation mit `CSRF_COOKIE_HTTPONLY=False` wird erkannt. Siehe [Nachweis](SEC-06-rule-lifecycle-authz.md#sec-05-nachprüfung).

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

### 2.7 MITTEL – Signed-Cookie-Sessions mit begrenztem Schutz

**Datei:** `trading_bot_project/settings.py` (Zeilen 157–163)

```python
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
```

**Auswirkung:** Signed Cookies sind gegen Manipulation geschützt, aber:
- Sie enthalten den Benutzernamen im Klartext (Base64-kodiert)
- Bei SECRET_KEY-Leak können Sessions gefälscht werden
- Kein Ablauf-Mechanismus bei Passwort-Änderung (Session-Invalidate fehlt)

**Lösungsvorschlag:**

```python
# Nach Passwort-Änderung/Abmeldung Session-Invalidate erzwingen:
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 Stunden (statt 12)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Bei Passwort-Änderung:
from django.contrib.auth import update_session_auth_hash
# In Passwort-Reset-View:
update_session_auth_hash(request, user)
```

---

### 2.8 MITTEL – Fehlende `Cache-Control`-Header für API-Endpunkte

**Datei:** `trading/views.py`

API-Endpunkte wie `/api/info/`, `/api/bot/status/`, `/api/logs/` haben keine Cache-Header. Browser und Proxies können sensitive Handelsdaten cachecen.

**Lösungsvorschlag:**

```python
from django.views.decorators.cache import never_cache

@login_required
@require_GET
@never_cache
def info_api(request, config_id):
    # ...
```

Oder als Decorator-Sammlung:

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

---

### 2.9 NIEDRIG – Fehlende `Permissions-Policy` / `Feature-Policy`

Es wird kein `Permissions-Policy`-Header gesetzt, der den Zugriff auf Browser-APIs (Kamera, Mikrofon, Geolokation) einschränkt.

**Lösungsvorschlag:**

```python
# Middleware oder in settings.py:
SECURE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
```

---

### 2.10 NIEDRIG – Potenzielle Information Disclosure in Error-Messages

**Datei:** `trading/views.py` (Zeilen 383, 894)

```python
messages.error(request, f"Bot konnte nicht gestartet werden: {exc}")
```

**Auswirkung:** Technische Exception-Details werden dem Benutzer angezeigt (z.B. Stacktraces, DB-Verbindungsfehler).

**Lösungsvorschlag:**

```python
messages.error(request, "Bot konnte nicht gestartet werden. Siehe Fehler-Log für Details.")
logger.exception("Bot-Start für Konfiguration %s fehlgeschlagen", config.id)
```

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

### 2.12 NIEDRIG – Docker-Compose Standard-Passwörter

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

---

## 3. Bugs & Funktionale Fehler

### 3.1 MITTEL – Race Condition in Bot-Start/Stop

**Datei:** `trading/trading_bot.py` (Zeilen 810–817)

```python
def start_bot(self, config):
    with self._lock:
        if self.is_running(config.id):  # ← is_running acquire lock intern
            return self.bots[config.id]
        bot = TradingBot(config, on_exit=self._forget)
        self.bots[config.id] = bot
        bot.start()
        return bot
```

**Problem:** `is_running()` acquire `_lock` intern, aber `self._lock` ist bereits gehalten → **Deadlock mit `threading.RLock()`**. Glücklicherweise löst `RLock()` dies auf, aber die verschachtelte Lock-Acquisition ist ein Code-Smell.

**Lösungsvorschlag:**

```python
def is_running(self, config_id):
    bot = self.bots.get(config_id)  # Ohne Lock – nur intern von start_bot aufgerufen
    if bot and bot.is_alive():
        return True
    if bot:
        self.bots.pop(config_id, None)
    return False

# is_running() für externe Aufrufe:
def is_running_external(self, config_id):
    with self._lock:
        return self.is_running(config_id)
```

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

### 3.3 NIEDRIG – `_CsvEcho`-Klasse wird nicht als True-Stream erkannt

**Datei:** `trading/views.py` (Zeilen 1136–1138)

```python
class _CsvEcho:
    def write(self, value):
        return value
```

**Problem:** `csv.writer` erwartet ein `StringIO`-ähnliches Objekt mit `write()`-Methode. Die `_CsvEcho`-Klasse funktioniert, aber der `StreamingHttpResponse` gibt Zeilen als Strings zurück, die im Browser gerendert werden. Das ist korrekt implementiert, aber die `_CsvEcho`-Klasse könnte `io.StringIO`-Schnittstelle verletzen.

**Keine kritische Auswirkung** – funktioniert in der Praxis.

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

### 4.2 PERFORMANCE – `db_trim_datalog` kann Datenbank-Lock verursachen

**Datei:** `trading/trading_bot.py` (Zeilen 176–183)

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

---

### 4.3 CODE-QUALITÄT – Duplizierte Indikator-Logik

**Dateien:**
- `trading/backtesting.py` (Zeilen 14–51)
- `trading/trading_bot.py` (Zeilen 577–591)

Die Indikatorberechnung (NDA, DeltaDelta, Acceleration) ist in beiden Dateien fast identisch implementiert.

**Lösungsvorschlag:**

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

### 4.4 TECHNOLOGISCHE SCHULD – Fehlende Type-Hints in Views

**Datei:** `trading/views.py`

Einige Funktionen haben keine vollständigen Type-Hints:

```python
# Aktuell:
def _portfolio_snapshot(config):
    # ...

# Verbessert:
def _portfolio_snapshot(config: Configuration) -> dict[str, Any]:
    # ...
```

**Lösungsvorschlag:** Schrittweise Type-Hints hinzufügen, beginnend mit den Views.

---

### 4.5 TECHNOLOGISCHE SCHULD – Fehlende `__all__`-Exports

**Datei:** `trading/__init__.py`

Es gibt kein `__all__` für das `trading`-Modul, was die öffentliche API unklar macht.

**Lösungsvorschlag:**

```python
# trading/__init__.py
__all__ = [
    'trading_bot',
    'backtesting',
    'market_data',
    'views',
    'models',
    'tasks',
    'forms',
]
```

---

### 4.6 PERFORMANCE – `info_api` lädt alle Logs in den Speicher

**Datei:** `trading/views.py` (Zeilen 677–762)

```python
def info_api(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
    portfolio = _portfolio_snapshot(config)
    metrics = calculate_performance_metrics(logs)
    # ...
```

**Problem:** Bei `MAX_LOG_ROWS = 2000` werden alle 2000 Zeilen in den Speicher geladen. Für große Konfigurationen kann dies zu hohem RAM-Verbrauch führen.

**Lösungsvorschlag:**

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
| 1 | **Rate-Limiting auf Auth-Endpunkte** implementieren | 2h | `views.py`, `settings.py` |
| 2 | **ALLOWED_HOSTS: Kein Wildcard** in DEBUG | 5min | `settings.py` |
| 3 | **CSRF_COOKIE_HTTPONLY = True** setzen | 5min | `settings.py` |
| 4 | **Docker-Passwörter ohne Defaults** setzen | 15min | `docker-compose.yml` |

### Kurzfristig umsetzen (P1 – 1–2 Wochen)

| # | Maßnahme | Aufwand | Datei |
|---|----------|---------|-------|
| 5 | **CSP-Header** implementieren | 4h | `settings.py`, Middleware |
| 6 | **Cache-Control für API-Endpunkte** setzen | 2h | `views.py` |
| 7 | **Error-Messages ohne Exception-Details** | 1h | `views.py` |
| 8 | **Indikator-Code deduplizieren** | 3h | `indicators.py` (neu) |

### Mittelfristig umsetzen (P2 – 1–2 Monate)

| # | Maßnahme | Aufwand | Datei |
|---|----------|---------|-------|
| 9 | **db_restore_state async-fähig machen** | 2h | `trading_bot.py` |
| 10 | **Indikator-Memoisierung** für Backtesting | 4h | `backtesting.py` |
| 11 | **DB-Trim mit Batch-Delete** | 2h | `trading_bot.py` |
| 12 | **Type-Hints für Views** ergänzen | 3h | `views.py` |

---

## 6: Komplett-Checkliste

### Sicherheit

- [ ] Rate-Limiting auf Login/Passphrase-Gate
- [ ] ALLOWED_HOSTS ohne Wildcard
- [x] CSRF_COOKIE_HTTPONLY = True
- [ ] CSP-Header implementiert
- [ ] Cache-Control für API-Endpunkte
- [ ] Error-Messages ohne technische Details
- [ ] Docker-Passwörter ohne Defaults
- [ ] Session-Invalidate bei Passwort-Änderung
- [ ] HSTS-Header für Produktion korrekt
- [x] X-Content-Type-Options: nosniff – explizit ab 2.4.6, siehe §2.6

### Code-Qualität

- [ ] Indikator-Code dedupliziert
- [ ] Type-Hints für Views
- [ ] `__all__` für Trading-Modul
- [ ] db_restore_state async-fähig
- [ ] Bot-Start/Stop Race-Condition behoben

### Performance

- [ ] Indikator-Memoisierung für Backtesting
- [ ] DB-Trim mit Batch-Delete
- [ ] Aggregation auf DB-Ebene statt Python
- [ ] Cache-Control Header

---

*Dieses Audit basiert auf der Code-Analyse vom 07. September 2026. Für kritische Schwachstellen wird eine erneute Prüfung nach Umsetzung der P0-Maßnahmen empfohlen.*
