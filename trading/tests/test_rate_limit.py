"""
Tests für die Rate-Limit-Middleware auf Auth-Endpunkte.

Testet dass:
- 5 fehlgeschlagene Login-Versuche zu HTTP 429 führen
- Erfolgreiche Anfragen nicht gezählt werden
- GET-Anfragen nicht limitiert werden
- Retry-After Header gesetzt ist

Basierend auf ARENA_AI_PROMPTS.md Prompt 1.
"""

import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from trading.rate_limit import RateLimitMiddleware


def _get_response(request):
    """Dummy-View für Middleware-Tests."""
    from django.http import HttpResponse

    return HttpResponse("OK")


class RateLimitMiddlewareTest(TestCase):
    """Tests für die Rate-Limit-Middleware."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RateLimitMiddleware(_get_response)
        # Versuche zurücksetzen um Test-Isolation zu gewährleisten
        RateLimitMiddleware._attempts.clear()

    def test_rate_limit_exceeded_returns_429(self):
        """5 fehlgeschlagene Login-Versuche sollten HTTP 429 zurückgeben."""
        for i in range(5):
            request = self.factory.post("/login/", {"username": f"user{i}", "password": "wrong"})
            response = self.middleware(request)
            if i < 4:
                self.assertEqual(response.status_code, 200)

        # Der 5. Versuch sollte blockiert werden
        request = self.factory.post("/login/", {"username": "user5", "password": "wrong"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)

    def test_retry_after_header_is_set(self):
        """Bei Rate-Limit sollte Retry-After Header gesetzt sein."""
        # 5 fehlgeschlagene Versuche
        for _ in range(5):
            request = self.factory.post("/login/", {"password": "wrong"})
            self.middleware(request)

        request = self.factory.post("/login/", {"password": "wrong"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertEqual(response["Retry-After"], "900")

    def test_successful_requests_not_counted(self):
        """GET-Anfragen und erfolgreiche Anfragen werden nicht gezählt."""
        # GET-Anfragen auf geschützten Pfaden werden nicht limitiert
        for _ in range(10):
            request = self.factory.get("/login/")
            response = self.middleware(request)
            self.assertEqual(response.status_code, 200)

        # POST auf nicht-geschützten Pfaden werden nicht gezählt
        for _ in range(10):
            request = self.factory.post("/dashboard/")
            response = self.middleware(request)
            self.assertEqual(response.status_code, 200)

        # Jetzt sollte der Login noch funktionieren
        request = self.factory.post("/login/", {"password": "test"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_get_requests_not_limited(self):
        """GET-Anfragen werden niemals limitiert, auch nicht auf geschützten Pfaden."""
        for _ in range(100):
            request = self.factory.get("/login/")
            response = self.middleware(request)
            self.assertEqual(response.status_code, 200)

    def test_gate_endpoint_protected(self):
        """Der /gate/ Endpunkt ist ebenfalls durch Rate-Limiting geschützt."""
        for _ in range(5):
            request = self.factory.post("/gate/", {"passphrase": "wrong"})
            self.middleware(request)

        request = self.factory.post("/gate/", {"passphrase": "wrong"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)

    def test_register_endpoint_protected(self):
        """Der /register/ Endpunkt ist ebenfalls durch Rate-Limiting geschützt."""
        for _ in range(5):
            request = self.factory.post("/register/", {"username": "test"})
            self.middleware(request)

        request = self.factory.post("/register/", {"username": "test"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)

    def test_client_ip_from_x_forwarded_for(self):
        """Die Client-IP wird korrekt aus X-Forwarded-For extrahiert."""
        request = self.factory.post(
            "/login/",
            HTTP_X_FORWARDED_FOR="192.168.1.100, 10.0.0.1",
        )
        ip = RateLimitMiddleware._get_client_ip(request)
        self.assertEqual(ip, "192.168.1.100")

    def test_client_ip_from_remote_addr(self):
        """Die Client-IP wird von REMOTE_ADDR gelesen wenn kein X-Forwarded-For."""
        request = self.factory.post("/login/", REMOTE_ADDR="192.168.1.50")
        ip = RateLimitMiddleware._get_client_ip(request)
        self.assertEqual(ip, "192.168.1.50")

    def test_different_ips_independent(self):
        """Verschiedene IPs haben unabhängige Counter."""
        # IP 1: 4 Versuche
        for _ in range(4):
            request = self.factory.post(
                "/login/",
                HTTP_X_FORWARDED_FOR="10.0.0.1",
            )
            self.middleware(request)

        # IP 2: 4 Versuche
        for _ in range(4):
            request = self.factory.post(
                "/login/",
                HTTP_X_FORWARDED_FOR="10.0.0.2",
            )
            self.middleware(request)

        # Beide IPs sollten noch funktionieren (je 4 < 5)
        request = self.factory.post(
            "/login/",
            HTTP_X_FORWARDED_FOR="10.0.0.1",
        )
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

        request = self.factory.post(
            "/login/",
            HTTP_X_FORWARDED_FOR="10.0.0.2",
        )
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    @patch("trading.rate_limit.time.time")
    def test_counter_resets_after_window(self, mock_time):
        """Der Counter wird nach Ablauf des Zeitfensters zurückgesetzt."""
        # Zeit auf einen festen Wert setzen
        mock_time.return_value = 1000.0

        # 5 Versuche innerhalb des Fensters
        for _ in range(5):
            request = self.factory.post("/login/", {"password": "wrong"})
            self.middleware(request)

        # 6. Versuch sollte blockiert werden
        request = self.factory.post("/login/", {"password": "wrong"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 429)

        # Zeit 16 Minuten in die Zukunft setzen (Fenster abgelaufen)
        mock_time.return_value = 1000.0 + 16 * 60

        # Versuche sollten jetzt wieder erlaubt sein
        request = self.factory.post("/login/", {"password": "wrong"})
        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)


class RateLimitIntegrationTest(TestCase):
    """Integrations-Tests die die Middleware im Django-Stack testen."""

    def setUp(self):
        # Versuche zurücksetzen
        RateLimitMiddleware._attempts.clear()

    @override_settings(
        MIDDLEWARE=[
            "django.middleware.security.SecurityMiddleware",
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
            "trading.rate_limit.RateLimitMiddleware",
        ]
    )
    def test_login_endpoint_rate_limited_in_browser(self):
        """Testet Rate-Limiting am echten Login-Endpunkt via Django Test-Client."""
        self.client.post("/gate/", {"passphrase": "local-development-only"})
        for _ in range(5):
            self.client.post(
                "/login/",
                {"username": "nonexistent", "password": "wrong"},
                follow=True,
            )

        response = self.client.post(
            "/login/",
            {"username": "nonexistent", "password": "wrong"},
            follow=True,
        )
        # Sollte entweder 429 sein oder eine Fehlermeldung enthalten
        self.assertTrue(
            response.status_code == 429 or b"Zu viele" in response.content
        )
