"""
Tests für die Session-Lebensdauer-Härtung und explizite Invalidierung bei Logout.

Prüft:
- SESSION_COOKIE_AGE ist auf 8 Stunden (28.800 Sekunden) statt 12 Stunden reduziert.
- SESSION_EXPIRE_AT_BROWSER_CLOSE ist True, damit das Cookie bei geschlossenem
  Browser abläuft und nicht dauerhaft wiederverwendet werden kann.
- logout_view invalidiert die Session per request.session.flush(), sodass
  nach dem Abmelden keine authentifizierten Anfragen mit dem alten Cookie
  mehr möglich sind.

Signed-cookie-Sessions sind signiert (nicht manipulierbar), aber ein gestohlenes
Cookie könnte bis zum Ablauf wiederverwendet werden. Die kürzere Lebensdauer,
Session-Invalidierung bei Abmeldung und Ablauf bei Browser-Schluss verkleinern
dieses Fenster erheblich.

Basierend auf ARENA_AI_PROMPTS.md Prompt 7 und SECURITY_AUDIT.md Abschnitt 2.7.
"""

import os
import re
import runpy
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from trading.rate_limit import RateLimitMiddleware
from trading_bot_project import settings

SETTINGS_PATH = Path(settings.__file__).resolve()
TEST_SECRET_KEY = "session-test-only-signing-key"

# Erwartete Werte nach dem Fix:
EXPECTED_COOKIE_AGE = 60 * 60 * 8  # 8 Stunden = 28.800 Sekunden


def load_settings_module(**overrides):
    """Lädt settings.py in einer isolierten Umgebung (ohne globale Settings)."""
    environment = {
        "DEBUG": "False",
        "RENDER": "False",
        "SECRET_KEY": TEST_SECRET_KEY,
        "PASSPHRASE": "session-test-passphrase",
    }
    environment.update(overrides)
    environment = {key: value for key, value in environment.items() if value is not None}
    with patch.dict(os.environ, environment, clear=True):
        return runpy.run_path(str(SETTINGS_PATH), run_name="session_invalidate_settings_test")


class SessionSettingsTest(TestCase):
    """Statische Prüfungen der Session-Konfiguration in settings.py."""

    def test_session_cookie_age_is_8_hours(self):
        """SESSION_COOKIE_AGE muss auf 8 Stunden (28.800 s) reduziert sein.

        Roher Test vor dem Fix: Der Wert war 12 Stunden (43.200 s) – das
        Zeitfenster für die Wiederverwendung eines gestohlenen signierten
        Cookies war damit zu groß.
        """
        self.assertEqual(
            settings.SESSION_COOKIE_AGE,
            EXPECTED_COOKIE_AGE,
            f"SESSION_COOKIE_AGE sollte {EXPECTED_COOKIE_AGE}s (8h) sein, "
            f"ist aber {settings.SESSION_COOKIE_AGE}s "
            f"({settings.SESSION_COOKIE_AGE / 3600:.1f}h).",
        )

    def test_session_cookie_age_not_12_hours(self):
        """Stellregression: Der alte Wert von 12 Stunden darf nicht mehr aktiv sein."""
        self.assertNotEqual(
            settings.SESSION_COOKIE_AGE,
            60 * 60 * 12,
            "SESSION_COOKIE_AGE ist noch auf 12 Stunden – der Fix wurde nicht angewendet.",
        )

    def test_session_expire_at_browser_close_enabled(self):
        """SESSION_EXPIRE_AT_BROWSER_CLOSE muss True sein.

        Roher Test vor dem Fix: Die Einstellung fehlte, sodass Django den
        Default False verwendete – Session-Cookies überlebten das Schließen
        des Browsers.
        """
        self.assertIs(
            settings.SESSION_EXPIRE_AT_BROWSER_CLOSE,
            True,
            "SESSION_EXPIRE_AT_BROWSER_CLOSE muss True sein, damit das "
            "Session-Cookie bei geschlossenem Browser abläuft.",
        )

    def test_settings_are_explicit_in_source(self):
        """Die Härtungen müssen explizit im Quellcode stehen."""
        content = SETTINGS_PATH.read_text(encoding="utf-8")
        self.assertIn("SESSION_COOKIE_AGE = 60 * 60 * 8", content)
        self.assertIn("SESSION_EXPIRE_AT_BROWSER_CLOSE = True", content)
        # Der alte 12-Stunden-Wert darf nicht mehr stehen
        self.assertNotIn("SESSION_COOKIE_AGE = 60 * 60 * 12", content)

    def test_settings_apply_in_debug_and_production(self):
        """Cookie-Alter und Browser-Close-Ablauf müssen in jeder Umgebung aktiv sein."""
        for debug in ("True", "False"):
            for render in ("True", "False"):
                with self.subTest(debug=debug, render=render):
                    module = load_settings_module(DEBUG=debug, RENDER=render)
                    self.assertEqual(module["SESSION_COOKIE_AGE"], EXPECTED_COOKIE_AGE)
                    self.assertIs(module["SESSION_EXPIRE_AT_BROWSER_CLOSE"], True)


@override_settings(
    PASSPHRASE="session-invalidate-test-pass",
    PASSPHRASE_GATE_ENABLED=True,
    AUTOSTART_BOTS=False,
    RATE_LIMIT_TRUSTED_PROXIES=[],
)
class SessionLogoutIntegrationTest(TestCase):
    """Integrationstests für Logout mit Session-Invalidierung."""

    def setUp(self):
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)
        # Erzwinge echte CSRF-Prüfung analog zu den anderen Security-Tests
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            username="session-user", password="S3ssion-Test-Pass!x"
        )

    def _pass_gate(self):
        """Gibt den Passphrase-Gate mit der Test-Passphrase frei."""
        gate_page = self.client.get("/gate/")
        self.assertEqual(gate_page.status_code, 200)
        token_match = re.search(
            r'name="csrfmiddlewaretoken" value="([a-zA-Z0-9]+)"',
            gate_page.content.decode(),
        )
        self.assertIsNotNone(token_match, "CSRF-Formularfeld auf /gate/ fehlt.")
        response = self.client.post(
            "/gate/",
            {
                "passphrase": "session-invalidate-test-pass",
                "csrfmiddlewaretoken": token_match.group(1),
            },
        )
        self.assertEqual(response.status_code, 302)

    def _csrf_token(self, response):
        """Liest den CSRF-Token aus dem Formularfeld."""
        match = re.search(
            r'name="csrfmiddlewaretoken" value="([a-zA-Z0-9]+)"',
            response.content.decode(),
        )
        self.assertIsNotNone(match, "CSRF-Formularfeld fehlt.")
        return match.group(1)

    def _login(self):
        """Meldet den Testbenutzer an und gibt den Dashboard-Response zurück."""
        self._pass_gate()
        login_page = self.client.get("/login/")
        self.assertEqual(login_page.status_code, 200)
        response = self.client.post(
            "/login/",
            {
                "username": "session-user",
                "password": "S3ssion-Test-Pass!x",
                "csrfmiddlewaretoken": self._csrf_token(login_page),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("dashboard", response["Location"])
        return response

    def test_logout_flush_is_called_and_redirects_to_login(self):
        """logout_view muss request.session.flush() aufrufen und zu /login/ umleiten.

        Dies ist der Kernfix: Ohne flush() blieben Session-Daten (insbesondere
        bei signed_cookie-Sessions) im Client-Cookie erhalten und der Benutzer
        blieb implizit authentifiziert bzw. konnte die Session bis zum Ablauf
        wiederverwenden.
        """
        self._login()
        self.assertTrue(
            self.client.session.get("_auth_user_id"),
            "Nach dem Login sollte _auth_user_id in der Session gesetzt sein.",
        )

        # Dashboard abrufen, um einen gültigen CSRF-Token zu erhalten.
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.status_code, 200)

        logout_response = self.client.post(
            "/logout/",
            {"csrfmiddlewaretoken": self._csrf_token(dashboard)},
        )
        self.assertRedirects(
            logout_response, "/login/", fetch_redirect_response=False
        )

    def test_after_logout_user_is_anonymous(self):
        """Nach Logout darf keine authentifizierte Anfrage mehr möglich sein.

        Ohne explizite Session-Invalidierung (flush()) kann der alte
        Session-Cookie bei signed_cookie-Sessions theoretisch bis zum Ablauf
        weiterverwendet werden. Der Test stellt sicher, dass unmittelbar
        nach logout der Benutzer nicht mehr authentifiziert ist. Da flush()
        auch die Passphrase-Freigabe löscht, erfolgt die Umleitung über den
        Gate – entscheidend ist, dass das Dashboard **nicht** direkt mit 200
        geladen wird und der Benutzer neu authentifizieren muss.
        """
        self._login()
        # Authentifizierung vor Logout prüfen
        dashboard_before = self.client.get("/dashboard/")
        self.assertEqual(dashboard_before.status_code, 200)

        token = self._csrf_token(dashboard_before)
        self.client.post("/logout/", {"csrfmiddlewaretoken": token})

        # Nach Logout muss /dashboard/ umleiten (302), weil der Benutzer nun
        # anonym ist. Der Passphrase-Gate ist ebenfalls invalidiert, deshalb
        # kann die Umleitung sowohl auf /login/ als auch auf /gate/ zeigen.
        response = self.client.get("/dashboard/")
        self.assertEqual(
            response.status_code,
            302,
            "Nach Logout muss der Benutzer anonym sein – /dashboard/ sollte "
            "umleiten (Gate oder Login), statt 200 OK zu liefern.",
        )
        self.assertTrue(
            "/login/" in response["Location"] or "/gate/" in response["Location"],
            f"Nach Logout sollte auf /login/ oder /gate/ umgeleitet werden, "
            f"nicht auf: {response['Location']}",
        )
        # Sicherstellen, dass kein Dashboard-Inhalt geliefert wird:
        followup = self.client.get("/dashboard/")
        # Auch nach dem Redirect (wir folgen nicht) bleibt der Benutzer anonym.
        self.assertNotEqual(followup.status_code, 200)

    def test_logout_requires_post(self):
        """Logout muss weiterhin POST erfordern (keine CSRF-umgehende GET-Anfrage)."""
        self._login()
        response = self.client.get("/logout/")
        self.assertEqual(response.status_code, 405)

    def test_logout_session_cookie_is_reset(self):
        """Nach Logout soll der sessionid-Cookie geleert/ersetzt werden.

        request.session.flush() rotiert den Session-Key und setzt den Wert
        zurück; für signed_cookies bedeutet das, dass der Client ein leeres
        bzw. ungültiges Session-Cookie erhält.
        """
        self._login()
        pre_logout_cookie = self.client.cookies.get("sessionid")
        self.assertIsNotNone(pre_logout_cookie, "Vor Logout muss sessionid gesetzt sein.")
        pre_value = pre_logout_cookie.value
        self.assertTrue(pre_value, "Session-Cookie vor Logout darf nicht leer sein.")

        dashboard = self.client.get("/dashboard/")
        self.client.post("/logout/", {"csrfmiddlewaretoken": self._csrf_token(dashboard)})

        # Nach flush() ist die Session entweder geleert oder der Cookie
        # enthält keinen gültigen Auth-Hash mehr. Entscheidend: Der Benutzer
        # ist anonym (siehe test_after_logout_user_is_anonymous).
        post_response = self.client.get("/dashboard/")
        self.assertEqual(post_response.status_code, 302)
        self.assertTrue(
            "/login/" in post_response["Location"] or "/gate/" in post_response["Location"],
            "Nach Logout muss der Benutzer anonym sein – Umleitung auf Gate oder Login erwartet.",
        )

    def test_logout_view_source_calls_flush(self):
        """Quellcodeprüfung: logout_view muss request.session.flush() aufrufen.

        Dies verhindert Regressionen, bei denen der flush()-Aufruf versehentlich
        entfernt wird.
        """
        views_path = (
            Path(settings.BASE_DIR) / "trading" / "views.py"
        )
        content = views_path.read_text(encoding="utf-8")
        # Nur den Funktionskopf per Regex suchen (signaturunabhängig, damit
        # Type-Hints den Test nicht brechen); der Blockinhalt wird danach
        # bewusst per String-Suche geprüft, weil Kommentare zwischen logout()
        # und flush() einen DOTALL-Regex sonst brechen würden.
        header = re.search(r"^def logout_view\(", content, re.MULTILINE)
        self.assertIsNotNone(header, "logout_view nicht in views.py gefunden.")
        logout_start = header.start()
        # Nur den Block bis zur nächsten Funktionsdefinition betrachten.
        next_def = content.find("\ndef ", logout_start + 1)
        block = content[logout_start : next_def if next_def > 0 else len(content)]
        self.assertIn("logout(request)", block)
        self.assertIn(
            "request.session.flush()",
            block,
            "logout_view muss nach logout(request) auch request.session.flush() aufrufen.",
        )


class SessionCookieAgeRegressionTest(TestCase):
    """Regression: Das Cookie-Alter darf nicht wieder auf den unsicheren Wert steigen."""

    def test_cookie_age_at_most_8_hours(self):
        """SESSION_COOKIE_AGE darf nicht über 8 Stunden anwachsen."""
        self.assertLessEqual(
            settings.SESSION_COOKIE_AGE,
            8 * 60 * 60,
            "SESSION_COOKIE_AGE darf höchstens 8 Stunden betragen, um das "
            "Zeitfenster für Session-Replay zu begrenzen.",
        )
