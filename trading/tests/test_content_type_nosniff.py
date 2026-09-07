"""Explizite nosniff-Konfiguration und Header über den echten Middleware-Stack.

Django 5.2 aktiviert nosniff bereits standardmäßig. Der Settings-Test prüft
bewusst das Projektmodul statt nur django.conf.settings, damit dieser Default
keine versehentliche Entfernung der expliziten Härtung verdeckt.
"""

import os
import runpy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.db import OperationalError
from django.http import HttpResponse, StreamingHttpResponse
from django.test import Client, SimpleTestCase, override_settings
from django.urls import path

from trading.rate_limit import RateLimitMiddleware
from trading_bot_project import settings as project_settings

UNTRUSTED_CONTENT = b"<!doctype html><script>window.__mime_sniffed = true;</script>"
DOWNLOAD_TYPES = {
    "text": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "binary": "application/octet-stream",
    "pdf": "application/pdf",
}


def untrusted_download(request, kind):
    """Nur Test-URL: aktive Syntax unter einem nicht ausführbaren MIME-Typ."""
    if kind == "csv":
        response = StreamingHttpResponse([UNTRUSTED_CONTENT], content_type=DOWNLOAD_TYPES[kind])
    else:
        response = HttpResponse(UNTRUSTED_CONTENT, content_type=DOWNLOAD_TYPES[kind])
    response["Content-Disposition"] = 'attachment; filename="untrusted-download"'
    return response


urlpatterns = [path("test-download/<str:kind>/", untrusted_download)]


class ContentTypeNosniffSettingsTests(SimpleTestCase):
    def test_setting_is_explicit_in_every_environment(self):
        settings_path = Path(project_settings.__file__).resolve()
        for debug in ("True", "False"):
            for render in ("True", "False"):
                environment = {
                    "DEBUG": debug,
                    "RENDER": render,
                    "SECRET_KEY": "test-only-signing-key-not-used-outside-isolated-tests",
                    "PASSPHRASE": "nosniff-settings-test-only",
                    "AUTOSTART_BOTS": "False",
                }
                with (
                    self.subTest(debug=debug, render=render),
                    patch.dict(os.environ, environment, clear=True),
                ):
                    module = runpy.run_path(str(settings_path), run_name="nosniff_settings_test")
                    self.assertIs(module.get("SECURE_CONTENT_TYPE_NOSNIFF"), True)

    def test_security_middleware_wraps_even_short_circuit_responses(self):
        self.assertEqual(settings.MIDDLEWARE[0], "django.middleware.security.SecurityMiddleware")


@override_settings(
    PASSPHRASE="nosniff-header-test-only",
    PASSPHRASE_GATE_ENABLED=True,
    AUTOSTART_BOTS=False,
    SECURE_SSL_REDIRECT=False,
    RATE_LIMIT_TRUSTED_PROXIES=[],
)
class ContentTypeNosniffHeaderTests(SimpleTestCase):
    def setUp(self):
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)
        self.client = Client(enforce_csrf_checks=True, raise_request_exception=False)

    def assert_nosniff(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.get("X-Content-Type-Options"), "nosniff")
        self.assertIn(b"X-Content-Type-Options: nosniff", response.serialize_headers())

    def test_html_and_json_responses_in_debug_and_production(self):
        for debug in (True, False):
            with self.subTest(debug=debug), override_settings(DEBUG=debug):
                client = Client(enforce_csrf_checks=True)
                for url, content_type in (("/gate/", "text/html"), ("/health/", "application/json")):
                    with self.subTest(url=url):
                        response = client.get(url, secure=not debug)
                        self.assert_nosniff(response, 200)
                        self.assertEqual(response["Content-Type"].split(";")[0], content_type)

    async def test_async_response_has_nosniff(self):
        response = await self.async_client.get("/health/", secure=True)
        self.assert_nosniff(response, 200)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=True)
    def test_https_and_gate_redirects_have_nosniff(self):
        response = self.client.get("/gate/")
        self.assert_nosniff(response, 301)
        self.assertTrue(response["Location"].startswith("https://"))
        response = self.client.get("/dashboard/", secure=True)
        self.assert_nosniff(response, 302)
        self.assertTrue(response["Location"].startswith("/gate/?next="))

    @override_settings(DEBUG=False)
    def test_client_errors_have_nosniff(self):
        cases = (
            ("get", "/health/", {"HTTP_HOST": "untrusted.invalid"}, 400),
            ("post", "/gate/", {}, 403),
            ("get", "/static/nosniff-missing-file.txt", {}, 404),
            ("options", "/health/", {}, 405),
        )
        for method, url, headers, status in cases:
            with self.subTest(status=status):
                response = getattr(self.client, method)(url, secure=True, **headers)
                self.assert_nosniff(response, status)

    def test_rate_limit_response_has_nosniff(self):
        for _ in range(RateLimitMiddleware.MAX_ATTEMPTS):
            self.assert_nosniff(self.client.post("/gate/"), 403)
        self.assert_nosniff(self.client.post("/gate/"), 429)

    @override_settings(DEBUG=False)
    def test_application_and_database_errors_have_nosniff(self):
        for exception, status in ((RuntimeError("test failure"), 500), (OperationalError(), 503)):
            with (
                self.subTest(status=status),
                patch("trading.views.is_passphrase_verified", side_effect=exception),
            ):
                self.assert_nosniff(self.client.get("/gate/", secure=True), status)

    @override_settings(ROOT_URLCONF=__name__, PASSPHRASE_GATE_ENABLED=False)
    def test_untrusted_downloads_keep_their_type_and_cannot_override_nosniff(self):
        for kind, content_type in DOWNLOAD_TYPES.items():
            with self.subTest(kind=kind):
                response = self.client.get(
                    f"/test-download/{kind}/",
                    HTTP_ACCEPT="application/javascript, text/html",
                    HTTP_X_CONTENT_TYPE_OPTIONS="unsafe",
                )
                self.addCleanup(response.close)
                self.assert_nosniff(response, 200)
                self.assertEqual(response["Content-Type"], content_type)
                self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
                body = b"".join(response.streaming_content) if response.streaming else response.content
                self.assertEqual(body, UNTRUSTED_CONTENT)

    def test_whitenoise_get_head_and_conditional_responses_have_nosniff(self):
        # WhiteNoise beantwortet statische Requests vor den Views. Ein isoliertes
        # STATIC_ROOT prüft diesen Produktionspfad ohne collectstatic-Vorbedingung.
        with TemporaryDirectory() as static_root:
            files = {
                "untrusted.txt": (UNTRUSTED_CONTENT, "text/plain"),
                "app.js": (b"window.__static_test = true;", "text/javascript"),
                "app.css": (b"body { color: black; }", "text/css"),
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
                        self.assert_nosniff(response, 200)
                        self.assertEqual(response["Content-Type"].split(";")[0], content_type)
                        head = client.head(url, secure=True)
                        self.addCleanup(head.close)
                        self.assert_nosniff(head, 200)
                        self.assertEqual(head["Content-Type"], response["Content-Type"])
                        cached = client.get(url, secure=True, HTTP_IF_NONE_MATCH=response["ETag"])
                        self.addCleanup(cached.close)
                        self.assert_nosniff(cached, 304)
