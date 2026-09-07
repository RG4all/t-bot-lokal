"""Passphrase-Startmatrix und Gate-Regressionen ohne Reload globaler Settings."""

import os
import runpy
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import Client, SimpleTestCase, override_settings
from django.views.debug import SafeExceptionReporterFilter

from trading.passphrase import is_passphrase_verified, passphrase_session_token
from trading.rate_limit import RateLimitMiddleware
from trading_bot_project.consumers import BacktestConsumer

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "trading_bot_project" / "settings.py"
TEST_SECRET_KEY = "test-only-signing-key-not-used-outside-isolated-tests"


def load_settings(**overrides):
    environment = {"DEBUG": "True", "RENDER": "False", "SECRET_KEY": TEST_SECRET_KEY}
    environment.update(overrides)
    environment = {key: value for key, value in environment.items() if value is not None}
    with patch.dict(os.environ, environment, clear=True):
        return runpy.run_path(str(SETTINGS_PATH), run_name="passphrase_settings_test")


class PassphraseSettingsTests(SimpleTestCase):
    def test_missing_empty_and_whitespace_passphrases_generate_32_random_bytes(self):
        for value in (None, "", " \t\n"):
            with (
                self.subTest(value=value),
                patch(
                    "secrets.token_urlsafe", return_value="generated-test-passphrase"
                ) as generate,
                self.assertLogs("passphrase_settings_test", "WARNING") as logs,
            ):
                result = load_settings(PASSPHRASE=value)
            generate.assert_called_once_with(32)
            self.assertEqual(result["PASSPHRASE"], "generated-test-passphrase")
            self.assertTrue(result["PASSPHRASE_GATE_ENABLED"])
            self.assertEqual(len(logs.records), 1)
            self.assertIn("Generiert: generated-test-passphrase", logs.output[0])
            self.assertIn("Nur für lokale Entwicklung!", logs.output[0])

    def test_real_tokens_are_url_safe_and_change_on_fresh_settings_load(self):
        with self.assertLogs("passphrase_settings_test", "WARNING"):
            first = load_settings(DEBUG=None)["PASSPHRASE"]
            second = load_settings(DEBUG=None)["PASSPHRASE"]
        self.assertRegex(first, r"^[A-Za-z0-9_-]{43}$")
        self.assertRegex(second, r"^[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(first, second)

    def test_explicit_passphrase_is_preserved_and_never_logged(self):
        value = "  private-üñicode-passphrase  "
        for render, debug in (("False", "True"), ("False", "False"), ("True", "False")):
            with (
                self.subTest(render=render, debug=debug),
                patch("secrets.token_urlsafe") as generate,
                self.assertNoLogs("passphrase_settings_test", "WARNING"),
            ):
                result = load_settings(PASSPHRASE=value, RENDER=render, DEBUG=debug)
            self.assertEqual(result["PASSPHRASE"], value)
            generate.assert_not_called()

    def test_render_always_requires_passphrase_even_in_debug_or_with_gate_disabled(self):
        for render in ("True", "1", "yes", "ON", " true "):
            for value in (None, "", " \t"):
                for debug in (None, "True", "False"):
                    with (
                        self.subTest(render=render, value=value, debug=debug),
                        patch("secrets.token_urlsafe") as generate,
                        self.assertNoLogs("passphrase_settings_test", "WARNING"),
                        self.assertRaisesRegex(RuntimeError, "PASSPHRASE .*required on Render"),
                    ):
                        load_settings(
                            PASSPHRASE=value,
                            RENDER=render,
                            DEBUG=debug,
                            PASSPHRASE_GATE_ENABLED="False",
                        )
                    generate.assert_not_called()

    def test_non_render_production_never_generates_or_logs_a_passphrase(self):
        for value in (None, "", " \n"):
            with (
                self.subTest(value=value),
                patch("secrets.token_urlsafe") as generate,
                self.assertNoLogs("passphrase_settings_test", "WARNING"),
                self.assertRaisesRegex(RuntimeError, "PASSPHRASE .*DEBUG=False"),
            ):
                load_settings(PASSPHRASE=value, DEBUG="False")
            generate.assert_not_called()

    def test_generation_failure_does_not_fall_back_to_a_known_secret(self):
        with (
            patch("secrets.token_urlsafe", side_effect=OSError("entropy unavailable")),
            self.assertRaisesRegex(OSError, "entropy unavailable"),
        ):
            load_settings()

    def test_production_cannot_disable_the_gate(self):
        for render, debug in (("True", "True"), ("True", "False"), ("False", "False")):
            for flag in ("False", "0", "off", "", "typo"):
                with (
                    self.subTest(render=render, debug=debug, flag=flag),
                    self.assertRaisesRegex(RuntimeError, "PASSPHRASE_GATE_ENABLED must be True"),
                ):
                    load_settings(
                        PASSPHRASE="test-passphrase",
                        PASSPHRASE_GATE_ENABLED=flag,
                        RENDER=render,
                        DEBUG=debug,
                    )

    def test_local_debug_can_still_disable_the_gate(self):
        result = load_settings(PASSPHRASE="test-passphrase", PASSPHRASE_GATE_ENABLED="False")
        self.assertFalse(result["PASSPHRASE_GATE_ENABLED"])

    def test_production_requires_private_signing_key_to_prevent_cookie_forgery(self):
        for render, debug in (("True", "True"), ("True", "False"), ("False", "False")):
            for key in (None, "", " \t"):
                with (
                    self.subTest(render=render, debug=debug, key=key),
                    patch("secrets.token_urlsafe") as generate,
                    self.assertNoLogs("passphrase_settings_test", "WARNING"),
                    self.assertRaisesRegex(RuntimeError, "SECRET_KEY environment variable"),
                ):
                    load_settings(
                        SECRET_KEY=key, PASSPHRASE="test-passphrase", RENDER=render, DEBUG=debug
                    )
                generate.assert_not_called()

    def test_temporary_local_signing_keys_are_random_and_not_logged(self):
        with self.assertLogs("passphrase_settings_test", "WARNING") as logs:
            first = load_settings(SECRET_KEY=None, PASSPHRASE="configured-passphrase")
            second = load_settings(SECRET_KEY="", PASSPHRASE="configured-passphrase")
        self.assertRegex(first["SECRET_KEY"], r"^[A-Za-z0-9_-]{67}$")
        self.assertNotEqual(first["SECRET_KEY"], second["SECRET_KEY"])
        for output in logs.output:
            self.assertNotIn(first["SECRET_KEY"], output)
            self.assertNotIn(second["SECRET_KEY"], output)
            self.assertNotIn("configured-passphrase", output)

    def test_docker_image_defaults_do_not_disable_the_render_gate(self):
        # Die tatsächlichen ENV-Vorgaben des Images zusammen mit Render laden.
        dockerfile = (ROOT / "Dockerfile").read_text().replace("\\\n", " ")
        image_environment = {}
        for line in dockerfile.splitlines():
            if line.startswith("ENV "):
                image_environment.update(item.split("=", 1) for item in shlex.split(line[4:]))
        result = load_settings(
            **{**image_environment, "RENDER": "True", "DEBUG": "False", "PASSPHRASE": "test-only"}
        )
        self.assertTrue(result["PASSPHRASE_GATE_ENABLED"])
        self.assertRegex(
            (ROOT / "render.yaml").read_text(),
            r'key: PASSPHRASE_GATE_ENABLED\s+value: "True"',
        )


@override_settings(
    PASSPHRASE="gate-integration-test-only",
    PASSPHRASE_GATE_ENABLED=True,
    AUTOSTART_BOTS=False,
    RATE_LIMIT_TRUSTED_PROXIES=[],
    SECRET_KEY=TEST_SECRET_KEY,
)
class PassphraseGateTests(SimpleTestCase):
    def setUp(self):
        RateLimitMiddleware._attempts.clear()
        self.addCleanup(RateLimitMiddleware._attempts.clear)

    def test_login_registration_and_api_require_gate_verification(self):
        for path in ("/login/", "/register/", "/api/backtesting/status/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith("/gate/?next="))

    def test_public_legacy_passphrases_and_unicode_input_are_rejected(self):
        for value in ("local-development-only", "local-t-bot", "wrong", "falsch-🔒", ""):
            with self.subTest(value=value):
                response = self.client.post("/gate/", {"passphrase": value})
                self.assertContains(response, "Falsche Passphrase. Zugang verweigert.")
                self.assertFalse(self.client.session.get("passphrase_verified"))

    def test_generated_passphrase_can_unlock_gate_but_is_not_in_the_response(self):
        with self.assertLogs("passphrase_settings_test", "WARNING"):
            passphrase = load_settings()["PASSPHRASE"]
        with override_settings(PASSPHRASE=passphrase):
            response = self.client.post("/gate/", {"passphrase": passphrase})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], "/login/")
            self.assertTrue(self.client.session.get("passphrase_verified"))
            self.assertEqual(
                self.client.session["passphrase_verified"], passphrase_session_token()
            )
            self.assertNotIn(passphrase, repr(dict(self.client.session)))
            self.assertContains(self.client.get("/login/"), "Benutzername")

    @override_settings(PASSPHRASE="  gültige Passphrase 🔒  ")
    def test_explicit_unicode_passphrase_and_whitespace_are_compared_exactly(self):
        response = self.client.post("/gate/", {"passphrase": "gültige Passphrase 🔒"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.session.get("passphrase_verified"))
        response = self.client.post("/gate/", {"passphrase": "  gültige Passphrase 🔒  "})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get("passphrase_verified"))

    def test_passphrase_rotation_revokes_prior_gate_access(self):
        self.client.post("/gate/", {"passphrase": "gate-integration-test-only"})
        self.assertEqual(self.client.get("/login/").status_code, 200)
        with override_settings(PASSPHRASE="rotated-test-passphrase"):
            self.assertEqual(self.client.get("/login/").status_code, 302)
            # Die Gate-View darf die alte Session ebenfalls nicht einfach akzeptieren.
            self.assertContains(self.client.get("/gate/"), "NUR MIT PASSPHRASE")
            response = self.client.post("/gate/", {"passphrase": "rotated-test-passphrase"})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.client.get("/login/").status_code, 200)

    def test_legacy_boolean_and_modified_session_proofs_are_not_valid(self):
        for token in (True, False, None, "", "invalid-token"):
            with self.subTest(token=token):
                self.assertFalse(is_passphrase_verified({"passphrase_verified": token}))
        token = passphrase_session_token()
        self.assertTrue(is_passphrase_verified({"passphrase_verified": token}))
        self.assertNotEqual(token, "gate-integration-test-only")
        with override_settings(SECRET_KEY="rotated-test-signing-key"):
            self.assertFalse(is_passphrase_verified({"passphrase_verified": token}))

    def test_gate_post_is_csrf_protected(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post("/gate/", {"passphrase": "gate-integration-test-only"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(client.session.get("passphrase_verified"))
        client.get("/gate/")
        response = client.post(
            "/gate/",
            {"passphrase": "gate-integration-test-only"},
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 302)

    @override_settings(DEBUG=False)
    def test_error_reports_redact_submitted_passphrase(self):
        response = self.client.post("/gate/", {"passphrase": "submitted-private-value"})
        filtered = SafeExceptionReporterFilter().get_post_parameters(response.wsgi_request)
        self.assertEqual(filtered["passphrase"], "********************")

    def test_health_endpoint_remains_public_and_contains_no_secrets(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"status", "version"})
        self.assertNotContains(response, "gate-integration-test-only")
        self.assertNotContains(response, TEST_SECRET_KEY)


@override_settings(PASSPHRASE="websocket-test-only", PASSPHRASE_GATE_ENABLED=True)
class WebsocketGateTests(SimpleTestCase):
    def setUp(self):
        self.consumer = BacktestConsumer()
        self.consumer.scope = {
            "user": SimpleNamespace(is_authenticated=True, id=7),
            "session": {"passphrase_verified": passphrase_session_token()},
        }

    def test_authenticated_owner_needs_current_gate_proof(self):
        with patch("trading_bot_project.consumers.BacktestTask.objects.filter") as query:
            query.return_value.exists.return_value = True
            self.assertTrue(async_to_sync(self.consumer.user_can_access)(42))
            query.assert_called_once_with(id=42, configuration__user_id=7)
            query.reset_mock()
            with override_settings(PASSPHRASE="rotated-websocket-passphrase"):
                self.assertFalse(async_to_sync(self.consumer.user_can_access)(42))
            query.assert_not_called()

    def test_unverified_or_anonymous_connections_cannot_query_tasks(self):
        with patch("trading_bot_project.consumers.BacktestTask.objects.filter") as query:
            for session in ({}, {"passphrase_verified": True}):
                self.consumer.scope["session"] = session
                self.assertFalse(async_to_sync(self.consumer.user_can_access)(42))
            self.consumer.scope["user"].is_authenticated = False
            self.consumer.scope["session"] = {"passphrase_verified": passphrase_session_token()}
            self.assertFalse(async_to_sync(self.consumer.user_can_access)(42))
            query.assert_not_called()

    @override_settings(PASSPHRASE_GATE_ENABLED=False)
    def test_local_gate_opt_out_still_requires_ownership(self):
        self.consumer.scope["session"] = {}
        with patch("trading_bot_project.consumers.BacktestTask.objects.filter") as query:
            query.return_value.exists.return_value = False
            self.assertFalse(async_to_sync(self.consumer.user_can_access)(42))
            query.return_value.exists.return_value = True
            self.assertTrue(async_to_sync(self.consumer.user_can_access)(42))
