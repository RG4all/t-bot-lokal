"""Permissions-Policy-Konfiguration und Header über den echten Middleware-Stack.

Die Tests prüfen bewusst das Projektmodul statt nur ``django.conf.settings``,
damit die explizite Härtung nicht durch Framework-Defaults oder zufällig
gesetzte Testwerte verdeckt wird. Django erzeugt selbst keinen
Permissions-Policy-Header; die Middleware in ``trading/middleware.py`` setzt
ihn aus ``settings.SECURE_PERMISSIONS_POLICY``.
"""

import os
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.db import OperationalError
from django.http import HttpResponse
from django.test import Client, SimpleTestCase, override_settings
from django.urls import path

from trading.rate_limit import RateLimitMiddleware
from trading_bot_project import settings as project_settings

EXPECTED_POLICY = "camera=(), microphone=(), geolocation=()"


def plain_view(request):
    """Nur Test-URL: einfache HTML-Antwort, die die Middleware passieren muss."""
    return HttpResponse("<p>permissions-policy-test</p>")


urlpatterns = [path("test-permissions-policy/", plain_view)]


class PermissionsPolicySettingsTests(SimpleTestCase):
    def test_setting_is_explicit_in_every_environment(self):
        settings_path = Path(project_settings.__file__).resolve()
        for debug in ("True", "False"):
            for render in ("True", "False"):
                environment = {
                    "DEBUG": debug,
                    "RENDER": render,
                    "SECRET_KEY": "test-only-signing-key-not-used-outside-isolated-tests",
                    "PASSPHRASE": "permissions-policy-settings-test-only",
                    "AUTOSTART_BOTS": "False",
                }
                with (
                    self.subTest(debug=debug, render=render),
                    patch.dict(os.environ, environment, clear=True),
                ):
                    module = runpy.run_path(str(settings_path), run_name="pp_settings_test")
                    self.assertEqual(module.get("SECURE_PERMISSIONS_POLICY"), EXPECTED_POLICY)

    def test_middleware_wraps_even_short_circuit_responses(self):
        # Direkt nach der SecurityMiddleware registriert, damit die
        # Response-Phase auch WhiteNoise-, Redirect- und Fehlerantworten
        # innerer Middleware erreicht.
        self.assertEqual(settings.MIDDLEWARE[0], "django.middleware.security.SecurityMiddleware")
        self.assertEqual(settings.MIDDLEWARE[1], "trading.middleware.PermissionsPolicyMiddleware")


@override_settings(
    PASSPHRASE="permissions-policy-header-test-only",
    PASSPHRASE_GATE_ENABLED=True,
    AUTOSTART_BOTS=False,
    SECURE_SSL_REDIRECT=False,
    RATE_LIMIT_TRUSTED_PROXIES=[],
)
class PermissionsPolicyHeaderTests(SimpleTestCase):
    def setUp(self):
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)
        self.client = Client(enforce_csrf_checks=True, raise_request_exception=False)

    def assert_policy(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.get("Permissions-Policy"), EXPECTED_POLICY)
        self.assertIn(
            f"Permissions-Policy: {EXPECTED_POLICY}".encode(),
            response.serialize_headers(),
        )

    def test_html_and_json_responses_in_debug_and_production(self):
        for debug in (True, False):
            with self.subTest(debug=debug), override_settings(DEBUG=debug):
                client = Client(enforce_csrf_checks=True)
                for url, content_type in (("/gate/", "text/html"), ("/health/", "application/json")):
                    with self.subTest(url=url):
                        response = client.get(url, secure=not debug)
                        self.assert_policy(response, 200)
                        self.assertEqual(response["Content-Type"].split(";")[0], content_type)

    async def test_async_response_has_policy(self):
        response = await self.async_client.get("/health/", secure=True)
        self.assert_policy(response, 200)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=True)
    def test_https_and_gate_redirects_have_policy(self):
        # Der 301-SSL-Redirect entsteht direkt in der äußersten
        # SecurityMiddleware und durchläuft daher keine innere Middleware; er
        # trägt keinen Permissions-Policy-Header. Ein Redirect führt selbst
        # keine Browser-APIs aus und liefert keinen Inhalt aus.
        response = self.client.get("/gate/")
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("https://"))
        # Der App-seitige Gate-Redirect (302) entsteht innerhalb der Kette und
        # trägt den Header.
        response = self.client.get("/dashboard/", secure=True)
        self.assert_policy(response, 302)
        self.assertTrue(response["Location"].startswith("/gate/?next="))

    @override_settings(DEBUG=False)
    def test_client_errors_have_policy(self):
        cases = (
            ("get", "/health/", {"HTTP_HOST": "untrusted.invalid"}, 400),
            ("post", "/gate/", {}, 403),
            ("get", "/static/permissions-policy-missing-file.txt", {}, 404),
            ("options", "/health/", {}, 405),
        )
        for method, url, headers, status in cases:
            with self.subTest(status=status):
                response = getattr(self.client, method)(url, secure=True, **headers)
                self.assert_policy(response, status)

    def test_rate_limit_response_has_policy(self):
        for _ in range(RateLimitMiddleware.MAX_ATTEMPTS):
            self.assert_policy(self.client.post("/gate/"), 403)
        self.assert_policy(self.client.post("/gate/"), 429)

    @override_settings(DEBUG=False)
    def test_application_and_database_errors_have_policy(self):
        for exception, status in ((RuntimeError("test failure"), 500), (OperationalError(), 503)):
            with (
                self.subTest(status=status),
                patch("trading.views.is_passphrase_verified", side_effect=exception),
            ):
                self.assert_policy(self.client.get("/gate/", secure=True), status)

    def test_whitenoise_get_head_and_conditional_responses_have_policy(self):
        # WhiteNoise beantwortet statische Requests vor den Views. Ein isoliertes
        # STATIC_ROOT prüft diesen Produktionspfad ohne collectstatic-Vorbedingung.
        with TemporaryDirectory() as static_root:
            files = {
                "plain.txt": (b"static permissions-policy test", "text/plain"),
                "app.js": (b"window.__static_test = true;", "text/javascript"),
            }
            for name, (body, _) in files.items():
                Path(static_root, name).write_bytes(body)
            with override_settings(
                DEBUG=False,
                STATIC_ROOT=static_root,
                WHITENOISE_AUTOREFRESH=False,
                WHITENOISE_USE_FINDERS=False,
            ):
                client = Client(enforce_csrf_checks=True)
                for name, (_, content_type) in files.items():
                    with self.subTest(file=name):
                        url = f"/static/{name}"
                        response = client.get(url, secure=True)
                        self.addCleanup(response.close)
                        self.assert_policy(response, 200)
                        self.assertEqual(response["Content-Type"].split(";")[0], content_type)
                        head = client.head(url, secure=True)
                        self.addCleanup(head.close)
                        self.assert_policy(head, 200)
                        cached = client.get(url, secure=True, HTTP_IF_NONE_MATCH=response["ETag"])
                        self.addCleanup(cached.close)
                        self.assert_policy(cached, 304)

    @override_settings(ROOT_URLCONF=__name__, PASSPHRASE_GATE_ENABLED=False)
    def test_view_cannot_disable_the_policy_header(self):
        response = self.client.get("/test-permissions-policy/")
        self.assert_policy(response, 200)

    @override_settings(SECURE_PERMISSIONS_POLICY="")
    def test_header_is_absent_when_policy_is_empty(self):
        # Negativkontrolle: Ohne Policy-Wert darf die Middleware keinen
        # Header setzen – der Fix reagiert damit auf die zentrale Einstellung.
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Permissions-Policy", response)
