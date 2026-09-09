# SEC-07 – Session-Lifetime-Härtung und explizite Invalidierung bei Logout

- **Finding:** `SessionInvalidateMissing` (Prompt 7 / Security-Audit §2.7)
- **Status:** **Fixed**
- **Release:** **2.4.7** · **Datum:** 2026-09-07
- **Ursprüngliche Einstufung:** MEDIUM – Security
- **Betroffene Dateien:** `trading_bot_project/settings.py`, `trading/views.py`

## Befund und Root Cause

Im Ausgangsstand 2.4.6 galten für signierte Cookie-Sessions folgende Einstellungen:

```python
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_AGE = 60 * 60 * 12  # 12 Stunden
# SESSION_EXPIRE_AT_BROWSER_CLOSE fehlte → Default False
```

Die `logout_view` rief nur `logout(request)` auf, aber nicht `request.session.flush()`.

**Auswirkung:**

1. Das Session-Cookie war 12 Stunden gültig – ein gestohlenes Cookie hätte in diesem Zeitfenster wiederverwendet werden können, auch wenn der Benutzer den Browser geschlossen hatte.
2. Signierte Cookies sind HMAC-geschützt (nicht manipulierbar), können aber bei einem kompromittierten Client oder einer anderweitig bekannt gewordenen `SECRET_KEY` für Session-Replay missbraucht werden.
3. Bei der Abmeldung wurde die Session nicht explizit geleert/rotiert. Bei `signed_cookies` bewirkt `logout()` zwar das Entfernen der `_auth_user_id`, Restdaten (insbesondere die Passphrase-Freigabe) verblieben aber im Cookie und die Key-Rotation unterblieb.

## Fix und Umfang

### 1. Session-Lebensdauer in `trading_bot_project/settings.py`

```python
# Session-Lebensdauer auf 8 Stunden beschränkt, um das Fenster bei gestohlenen
# signierten Cookies zu verkleinern. Davor waren es 12 Stunden.
SESSION_COOKIE_AGE = 60 * 60 * 8
# Session-Cookie läuft ab, wenn der Browser geschlossen wird – verhindert
# dauerhafte Wiederverwendung eines gestohlenen Cookies über Browserneustarts.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
```

- Die Einstellungen sind DEBUG-/Render-unabhängig. Sie ergänzen die bereits vorhandenen Cookie-Flags (`SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SAMESITE = "Lax"`, in Produktion `SESSION_COOKIE_SECURE = True`).
- Keine neue Middleware, keine Runtime-Abhängigkeit, keine Migration. Bereits bestehende Sessions verhalten sich beim nächsten Response wie bisher – die neuen Defaults greifen für neu ausgestellte Cookies.

### 2. Explizite Session-Invalidierung in `trading/views.py`

```python
@login_required
@require_POST
def logout_view(request):
    logout(request)
    # Explizite Session-Invalidierung: leert alle Session-Daten und rotiert
    # den Session-Key. Bei signed_cookie-Sessions ist dies entscheidend, da
    # ein gestohlenes Cookie sonst bis zum Ablauf weiterverwendet werden könnte.
    request.session.flush()
    return redirect("login")
```

- `request.session.flush()` löscht die Session-Daten vollständig und rotiert den Session-Key. Bei signierten Cookies wird dadurch auch die Passphrase-Freigabe (`passphrase_verified`) mit entfernt – nach Logout muss der Gate bei erneutem Zugriff auf geschützte Seiten neu freigegeben werden. Dies ist das erwartete Verhalten nach einer expliziten Abmeldung.
- Die View bleibt `@login_required` und `@require_POST`; CSRF-Schutz und die 302-Weiterleitung auf `/login/` bleiben erhalten.

## Testnachweis

### Rot → grün und Negativkontrollen

Neu `trading/tests/test_session_invalidate.py` mit 11 Tests:

- **Settings-Tests (6):** `SESSION_COOKIE_AGE = 28.800` (8 h) und `SESSION_EXPIRE_AT_BROWSER_CLOSE = True` sind explizit im Quellcode vorhanden; der alte 12-Stunden-Wert ist nicht mehr vorhanden. Die Einstellungen werden in allen vier DEBUG-/Render-Kombinationen aus einer isolierten Settings-Ladung bestätigt.
- **Integrationstests (5, mit `Client(enforce_csrf_checks=True)` und aktivem Passphrase-Gate):** POST-Logout leitet auf `/login/` um; danach ist der Benutzer anonym – `/dashboard/` liefert keinen 200 OK mehr, sondern leitet auf Gate oder Login um. GET `/logout/` gibt wie bisher 405. Der Quellcode von `logout_view` wird auf das Vorhandensein von `request.session.flush()` geprüft. Eine Regression des Werts auf >8 Stunden wird durch einen eigenen Test blockiert.

Ausgangsstand vor dem Fix: Alle drei Settings-/Code-Asserts (`SESSION_COOKIE_AGE != 12 h`, `SESSION_EXPIRE_AT_BROWSER_CLOSE is True`, `request.session.flush()` in `logout_view`) schlugen fehl. Mit dem Fix sind alle 11 Tests grün.

### Lokale Validierung

Python **3.11.2**, Django **5.2.17**, Ruff **0.16.6**:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False PASSPHRASE=test-passphrase SECRET_KEY=test-secret-key
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python manage.py test trading.tests.test_session_invalidate trading.tests.test_content_type_nosniff trading.tests.test_csrf_cookie --noinput
python manage.py collectstatic --noinput
python -m pip check
```

- **165 Django-/Python-Tests** (11 neue + 154 bestehende) bestanden.
- Systemcheck, Migrationsprüfung und `collectstatic` ohne Warnungen. `pip check` ohne Konflikte.
- Keine Code-Änderungen außerhalb `settings.py`, `views.py`, dem neuen Testmodul, Version, Changelog, READMEs und diesem Finding-Dokument.

### Abgrenzung und Prüfgrenzen

- Signierte Cookie-Sessions bleiben bewusst der gewählte Session-Backend (`signed_cookies`): Sie entkoppeln Login und Passphrase-Gate von kurzzeitig nicht erreichbaren Render-Free-Postgres-Datenbanken (siehe Changelog 2.0.2). Die Härtung verkleinert das Zeitfenster für Replay, ändert aber nicht das Backend.
- `SESSION_EXPIRE_AT_BROWSER_CLOSE` ist abhängig von der Browser-Implementierung (einige Browser stellen Sessions nach einem Neustart wieder her). Zusammen mit der 8-Stunden-Grenze und dem expliziten `flush()` bei Logout ist dies eine Defense-in-Depth-Maßnahme, keine absolute Schutzgarantie.
- Eine Rotation bei Passwort-Änderung (`update_session_auth_hash`) ist ein separater Befund (Audit §2.7 nennt dies als zusätzliche Empfehlung). Er ist nicht Teil dieses Fixes, da die App derzeit keine eingebaute Passwort-Änderungs-View anbietet.
- Lokal fehlen Docker und Pango; daher kein Container-/PDF-Nachweis. Cookie-Flags sollten nach dem Produktions-Deploy über den tatsächlichen HTTPS-Endpunkt geprüft werden.

## Auslieferung

- **Commit-Message:** `fix(security): reduce session lifetime and invalidate on logout`
- Keine neuen Umgebungsvariablen oder Datenbankmigrationen. Kein neues Deployment-Geheimnis erforderlich.
- Nach dem Deploy: Sitzungscookie-Lebensdauer und `Expires`/`Max-Age`-Werte im Browser bzw. über den öffentlichen Endpunkt prüfen; Logout überprüfen.
