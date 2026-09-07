"""
Tests für die CSRF-Cookie-Härtung (CSRF_COOKIE_HTTPONLY).

Stellt sicher, dass das CSRF-Token-Cookie mit dem HttpOnly-Flag gekennzeichnet
ist und damit nicht über JavaScript (``document.cookie``) ausgelesen werden
kann. Dies ist zusätzliche Cookie-Härtung, kein allgemeiner XSS-Schutz:
Skripte derselben Origin können das Token weiterhin aus dem DOM lesen.

Django liest das Cookie serverseitig aus; die Templates liefern das Token
unabhängig davon über ``{% csrf_token %}`` (verstecktes Formularfeld bzw.
Template-Kontext). Die eigenen App-Skripte (z. B. Dashboard) lesen das Token
aus dem Formularfeld und bleiben daher vollständig funktionsfähig.

Die Integrationstests laufen mit ``Client(enforce_csrf_checks=True)``, damit
die echte CSRF-Prüfung (statt des Test-Client-Shortcuts) validiert wird.

Basierend auf ARENA_AI_PROMPTS.md Prompt 5 und SECURITY_AUDIT.md Abschnitt 2.5.
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
TEST_SECRET_KEY = "test-only-signing-key-not-used-outside-isolated-tests"


def load_settings_module(**overrides):
    """Lädt settings.py in einer isolierten Umgebung (ohne globale Settings)."""
    environment = {"DEBUG": "False", "RENDER": "False", "SECRET_KEY": TEST_SECRET_KEY}
    environment.update(overrides)
    environment = {key: value for key, value in environment.items() if value is not None}
    with patch.dict(os.environ, environment, clear=True):
        return runpy.run_path(str(SETTINGS_PATH), run_name="csrf_cookie_settings_test")


class CsrfCookieSettingsTest(TestCase):
    """Statische Prüfungen der CSRF-Cookie-Konfiguration in settings.py."""

    def test_csrf_cookie_httponly_is_enabled(self):
        """CSRF_COOKIE_HTTPONLY muss True sein.

        Roher Test vor dem Fix: Die Einstellung fehlte, und Django
        verwendete den unsicheren Standardwert False – das Token war
        über document.cookie lesbar.
        """
        self.assertIs(settings.CSRF_COOKIE_HTTPONLY, True)

    def test_setting_is_explicit_in_source(self):
        """Die Härtung muss explizit im Quellcode stehen (keine implizite Annahme)."""
        content = SETTINGS_PATH.read_text(encoding="utf-8")
        self.assertIn("CSRF_COOKIE_HTTPONLY = True", content)
        self.assertNotIn("CSRF_COOKIE_HTTPONLY = False", content)

    def test_httponly_is_active_in_debug_and_production(self):
        """HttpOnly muss unabhängig vom DEBUG-Modus aktiv sein.

        Anders als CSRF_COOKIE_SECURE ist das HttpOnly-Flag über plain HTTP
        unproblematisch und darf deshalb nicht in einem `if not DEBUG:`-
        Block vergraben sein.
        """
        for debug in ("True", "False"):
            for render in ("True", "False"):
                with self.subTest(debug=debug, render=render):
                    module = load_settings_module(
                        DEBUG=debug, RENDER=render, PASSPHRASE="csrf-test-passphrase"
                    )
                    self.assertIs(module["CSRF_COOKIE_HTTPONLY"], True)


@override_settings(
    PASSPHRASE="csrf-cookie-test-only",
    PASSPHRASE_GATE_ENABLED=True,
    AUTOSTART_BOTS=False,
    RATE_LIMIT_TRUSTED_PROXIES=[],
)
class CsrfCookieIntegrationTest(TestCase):
    """Angriffsszenarien über den echten Middleware-Stack (Gate + CSRF)."""

    def setUp(self):
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)
        # Echte CSRF-Prüfung erzwingen – der Standard-Test-Client überspringt
        # sie andernfalls (enforce_csrf_checks=False).
        self.client = Client(enforce_csrf_checks=True)

    def _open_login_page(self):
        """Gate wie in Produktion freigeben und Login-Seite laden.

        1. GET /gate/        → csrftoken-Cookie wird gesetzt
        2. POST /gate/       → mit Token aus dem Formularfeld
        3. GET /login/       → Login-Formular mit verstecktem CSRF-Feld
        """
        gate_page = self.client.get("/gate/")
        self.assertEqual(gate_page.status_code, 200)
        self.assertIn("csrftoken", gate_page.cookies)

        gate_response = self.client.post(
            "/gate/",
            {
                "passphrase": "csrf-cookie-test-only",
                "csrfmiddlewaretoken": self._csrf_form_token(gate_page),
            },
        )
        self.assertEqual(gate_response.status_code, 302)

        response = self.client.get("/login/")
        self.assertEqual(response.status_code, 200)
        return response

    def _csrf_form_token(self, response):
        """Den maskierten DOM-Token lesen, genau wie die eigenen App-Skripte."""
        match = re.search(
            r'name="csrfmiddlewaretoken" value="([a-zA-Z0-9]+)"',
            response.content.decode(),
        )
        self.assertIsNotNone(match, "CSRF-Formularfeld fehlt.")
        return match.group(1)

    def _csrf_cookie_line(self, response):
        """Liefert die exakte Set-Cookie-Zeile für csrftoken, wie sie Django
        auf den Draht schreibt (response.cookies wird bei der Serialisierung
        in die Set-Cookie-Header umgesetzt)."""
        output = response.cookies.output(header="Set-Cookie:", sep="\r\n")
        for line in output.splitlines():
            if "csrftoken=" in line:
                return line
        self.fail("Kein Set-Cookie-Eintrag für csrftoken in der Response.")

    def test_csrf_cookie_is_set_with_httponly_flag(self):
        """Das csrftoken-Cookie muss auf dem Draht mit HttpOnly übertragen werden.

        Angriffsszenario: Ein XSS-Skript ruft document.cookie auf. Mit dem
        HttpOnly-Flag ist das csrftoken dort nicht enthalten und kann nicht
        über diesen Cookie-Zugriff ausgelesen werden. Das DOM-Token bleibt
        für JavaScript sichtbar; HttpOnly verhindert keine XSS-Angriffe.
        """
        response = self._open_login_page()
        self.assertIn("csrftoken", response.cookies)
        cookie = response.cookies["csrftoken"]
        self.assertTrue(
            cookie["httponly"],
            "Das csrf-token-Cookie ist ohne HttpOnly-Flag gesetzt und damit "
            "über document.cookie lesbar.",
        )
        # Zusätzliche Draht-Prüfung: exakte Set-Cookie-Zeile wie im Browser.
        self.assertIn("HttpOnly", self._csrf_cookie_line(response))

    def test_csrf_token_is_still_distributed_to_the_client(self):
        """HttpOnly darf die Token-Auslieferung nicht beeinträchtigen.

        Das Token muss weiterhin als Cookie ankommen und im Template als
        verstecktes Formularfeld vorliegen – sonst bricht die CSRF-Prüfung
        bei nachfolgenden POST-Anfragen ab.
        """
        response = self._open_login_page()
        cookie = response.cookies.get("csrftoken")
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie.value, "Das csrf-token-Cookie hat keinen Wert.")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_post_without_csrf_token_is_still_rejected(self):
        """CSRF-Schutz bleibt aktiv: POST ohne Token wird mit 403 abgewiesen."""
        self._open_login_page()
        response = self.client.post("/login/", {"username": "someone", "password": "irrelevant"})
        self.assertEqual(response.status_code, 403)

    def test_login_with_csrf_token_from_form_field_still_works(self):
        """Vollständiger Login-Fluss: Token aus dem Formularfeld (nicht aus
        document.cookie) wird akzeptiert und die Anmeldung gelingt."""
        User.objects.create_user(username="csrf-user", password="S3cure-Passwort!x")
        response = self._open_login_page()
        token = self._csrf_form_token(response)
        self.assertNotEqual(token, self.client.cookies["csrftoken"].value)
        response = self.client.post(
            "/login/",
            {"username": "csrf-user", "password": "S3cure-Passwort!x", "csrfmiddlewaretoken": token},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("dashboard", response["Location"])
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_cookie_flags_with_loaded_local_and_production_settings(self):
        for debug, render in (("True", "False"), ("False", "False"), ("False", "True")):
            module = load_settings_module(
                DEBUG=debug, RENDER=render, PASSPHRASE="csrf-test-passphrase"
            )
            cookie_settings = {
                name: module[name]
                for name in (
                    "DEBUG", "CSRF_COOKIE_HTTPONLY", "CSRF_COOKIE_SECURE", "SECURE_SSL_REDIRECT"
                )
            }
            with self.subTest(debug=debug, render=render), override_settings(**cookie_settings):
                response = Client(enforce_csrf_checks=True).get("/gate/", secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.cookies["csrftoken"]["httponly"])
                self.assertEqual(bool(response.cookies["csrftoken"]["secure"]), debug == "False")
                self.assertIn("HttpOnly", self._csrf_cookie_line(response))

    def test_valid_form_token_does_not_allow_untrusted_origin(self):
        response = self._open_login_page()
        response = self.client.post(
            "/login/",
            {"csrfmiddlewaretoken": self._csrf_form_token(response)},
            HTTP_ORIGIN="https://untrusted.invalid",
        )
        self.assertEqual(response.status_code, 403)

    def test_token_from_another_client_is_rejected(self):
        self._open_login_page()
        other_page = Client(enforce_csrf_checks=True).get("/gate/")
        self.assertNotEqual(
            self.client.cookies["csrftoken"].value, other_page.cookies["csrftoken"].value
        )
        response = self.client.post(
            "/login/", {"csrfmiddlewaretoken": self._csrf_form_token(other_page)}
        )
        self.assertEqual(response.status_code, 403)

    def test_form_token_without_corresponding_cookie_is_rejected(self):
        page = self.client.get("/gate/")
        response = Client(enforce_csrf_checks=True).post(
            "/gate/",
            {
                "passphrase": "csrf-cookie-test-only",
                "csrfmiddlewaretoken": self._csrf_form_token(page),
            },
        )
        self.assertEqual(response.status_code, 403)
