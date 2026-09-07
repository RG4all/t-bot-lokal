"""Regressionen für Zählung, Nebenläufigkeit und die Proxy-Vertrauensgrenze."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from trading.rate_limit import RateLimitMiddleware


@override_settings(RATE_LIMIT_TRUSTED_PROXIES=[])
class RateLimitMiddlewareTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = RateLimitMiddleware(lambda request: HttpResponse("OK"))
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)

    def test_sixth_post_is_blocked_on_every_auth_endpoint(self):
        for path in RateLimitMiddleware.PROTECTED_PATHS:
            with self.subTest(path=path):
                RateLimitMiddleware._attempts.clear()
                for _ in range(5):
                    self.assertEqual(self.middleware(self.factory.post(path)).status_code, 200)
                response = self.middleware(self.factory.post(path))
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response["Retry-After"], "900")

    def test_gets_and_unprotected_posts_do_not_consume_attempts(self):
        for _ in range(10):
            self.middleware(self.factory.get("/login/"))
            self.middleware(self.factory.post("/dashboard/"))
        self.assertFalse(RateLimitMiddleware._attempts)

    def test_client_ip_ignores_untrusted_forwarded_headers(self):
        for index in range(6):
            request = self.factory.post(
                "/gate/",
                REMOTE_ADDR="192.0.2.10",
                HTTP_X_FORWARDED_FOR=f"198.51.100.{index}",
            )
            self.assertEqual(self.middleware._get_client_ip(request), "192.0.2.10")
            response = self.middleware(request)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(RateLimitMiddleware._attempts), 1)

    def test_different_peer_ips_are_independent(self):
        for ip in ("192.0.2.10", "192.0.2.11"):
            for _ in range(5):
                response = self.middleware(self.factory.post("/gate/", REMOTE_ADDR=ip))
                self.assertEqual(response.status_code, 200)
            response = self.middleware(self.factory.post("/gate/", REMOTE_ADDR=ip))
            self.assertEqual(response.status_code, 429)

    @override_settings(RATE_LIMIT_TRUSTED_PROXIES=["10.0.0.0/8", "2001:db8:1::/48"])
    def test_trusted_proxy_chain_stops_before_client_supplied_hop(self):
        middleware = RateLimitMiddleware(lambda request: HttpResponse("OK"))
        for peer, forwarded, expected in (
            ("10.0.0.1", "198.51.100.99, 192.0.2.10, 10.0.0.2", "192.0.2.10"),
            ("2001:db8:1::1", "2001:db8:2::10", "2001:db8:2::10"),
            ("10.0.0.1", "spoofed, invalid", "10.0.0.1"),
            ("10.0.0.1", "", "10.0.0.1"),
            ("10.0.0.1", "1" * 2049, "10.0.0.1"),
        ):
            with self.subTest(peer=peer, forwarded=forwarded[:50]):
                request = self.factory.post(
                    "/login/", REMOTE_ADDR=peer, HTTP_X_FORWARDED_FOR=forwarded
                )
                self.assertEqual(middleware._get_client_ip(request), expected)

    def test_invalid_peer_addresses_share_a_fail_closed_bucket(self):
        for peer in ("", "invalid"):
            request = self.factory.post("/gate/", REMOTE_ADDR=peer)
            self.assertEqual(self.middleware._get_client_ip(request), "unknown")

    @override_settings(RATE_LIMIT_TRUSTED_PROXIES=["not-a-network"])
    def test_invalid_proxy_configuration_fails_explicitly(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "RATE_LIMIT_TRUSTED_PROXIES"):
            RateLimitMiddleware(lambda request: HttpResponse("OK"))

    @patch("trading.rate_limit.time.monotonic")
    def test_window_expires_including_inactive_ips(self, clock):
        clock.return_value = 1000.0
        for _ in range(5):
            self.middleware(self.factory.post("/gate/", REMOTE_ADDR="192.0.2.10"))
        self.assertEqual(
            self.middleware(self.factory.post("/gate/", REMOTE_ADDR="192.0.2.10")).status_code,
            429,
        )
        clock.return_value += self.middleware.WINDOW_SECONDS
        self.middleware(self.factory.post("/gate/", REMOTE_ADDR="192.0.2.11"))
        self.assertNotIn("192.0.2.10", RateLimitMiddleware._attempts)
        self.assertEqual(
            self.middleware(self.factory.post("/gate/", REMOTE_ADDR="192.0.2.10")).status_code,
            200,
        )

    @patch.object(RateLimitMiddleware, "MAX_TRACKED_IPS", 2)
    def test_full_map_blocks_new_ips_without_evicting_active_limits(self):
        for ip in ("192.0.2.10", "192.0.2.11"):
            self.middleware(self.factory.post("/gate/", REMOTE_ADDR=ip))
        response = self.middleware(self.factory.post("/gate/", REMOTE_ADDR="192.0.2.12"))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(RateLimitMiddleware._attempts), 2)
        self.assertEqual(
            self.middleware(self.factory.post("/gate/", REMOTE_ADDR="192.0.2.10")).status_code,
            200,
        )

    def test_parallel_requests_cannot_overrun_the_limit(self):
        barrier = Barrier(16)

        def post(_index):
            barrier.wait(timeout=10)
            return self.middleware(self.factory.post("/gate/")).status_code

        with ThreadPoolExecutor(max_workers=16) as pool:
            statuses = list(pool.map(post, range(16)))
        self.assertEqual(statuses.count(200), 5)
        self.assertEqual(statuses.count(429), 11)

    def test_script_prefix_does_not_bypass_the_limit(self):
        for _ in range(6):
            request = self.factory.post("/gate/", SCRIPT_NAME="/tbot")
            response = self.middleware(request)
        self.assertEqual(response.status_code, 429)


@override_settings(
    PASSPHRASE="rate-limit-test-only",
    PASSPHRASE_GATE_ENABLED=True,
    AUTOSTART_BOTS=False,
    RATE_LIMIT_TRUSTED_PROXIES=[],
)
class RateLimitIntegrationTest(SimpleTestCase):
    def setUp(self):
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)

    def test_gate_endpoint_rate_limited_in_real_middleware_stack(self):
        for _ in range(5):
            response = self.client.post("/gate/", {"passphrase": "wrong"})
            self.assertEqual(response.status_code, 200)
        response = self.client.post("/gate/", {"passphrase": "rate-limit-test-only"})
        self.assertEqual(response.status_code, 429)
        self.assertFalse(self.client.session.get("passphrase_verified"))

    def test_login_posts_are_counted_after_successful_gate(self):
        response = self.client.post("/gate/", {"passphrase": "rate-limit-test-only"})
        self.assertEqual(response.status_code, 302)
        # Leere Formulardaten brauchen keine DB; vier POSTs bleiben nach dem Gate übrig.
        for _ in range(4):
            self.assertEqual(self.client.post("/login/", {}).status_code, 200)
        self.assertEqual(self.client.post("/login/", {}).status_code, 429)
