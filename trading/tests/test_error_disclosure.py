"""SEC-10: technische Fehler bleiben in Logs, nicht in Flash-/API-/Report-Meldungen.

Die Payload ist rein synthetisch. HTTP-Tests prüfen auch signierte (nicht
verschlüsselte) Message-Cookies und bereits gespeicherte Backtest-Fehler.
Technische Diagnose-Logs sind zusätzlich zur Eigentümerprüfung auf Staff begrenzt.
"""

import ast
import builtins
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.db import IntegrityError, InterfaceError, OperationalError
from django.forms.models import model_to_dict
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from redis.exceptions import RedisError

from trading import views
from trading.market_data import MarketDataError, SymbolValidationError
from trading.market_scanner import MarketScannerError, MarketScannerFilterError
from trading.models import BacktestTask, Configuration, ErrorLog
from trading.trading_bot import TradingBot

PRIVATE_MARKER = "SEC10_PRIVATE_DETAIL"
EXCEPTION_TEXT = (
    f"{PRIVATE_MARKER}: postgres://test-user:test-password@db.internal.invalid/trading\n"
    'Traceback: /srv/private/adapter.py:42 <script>alert("test-only")</script>'
)
BOT_START_MESSAGE = "Bot konnte nicht gestartet werden. Siehe Fehler-Log für Details."
SCANNER_MESSAGE = "Marktscanner vorübergehend nicht verfügbar. Bitte später erneut versuchen."
SELL_MESSAGE = "Verkauf fehlgeschlagen. Siehe Fehler-Log für Details."
KILL_MESSAGE = "Kill-Switch fehlgeschlagen. Siehe Fehler-Log für Details."
REPORT_MESSAGE = "Report konnte nicht erstellt werden. Bitte später erneut versuchen."


def assert_exception_logged(test, captured, exception):
    records = [record for record in captured.records if record.exc_info]
    test.assertTrue(records, "Technischer Fehler muss mit Traceback geloggt werden.")
    test.assertTrue(any(record.exc_info[1] is exception for record in records))
    test.assertTrue(any(record.exc_info[2] is not None for record in records))
    test.assertIn(PRIVATE_MARKER, "\n".join(captured.output))


class ErrorDisclosureSourceTests(SimpleTestCase):
    def test_no_direct_exception_in_message_or_response_sinks(self):
        """Erkennt f-Strings, str(exc), Formatierung und umbenannte Except-Variablen."""
        tree = ast.parse(Path(views.__file__).read_text(encoding="utf-8"))
        for handler in ast.walk(tree):
            if not isinstance(handler, ast.ExceptHandler) or not handler.name:
                continue
            for node in ast.walk(handler):
                if not isinstance(node, ast.Call):
                    continue
                is_message = (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "messages"
                )
                is_response = isinstance(node.func, ast.Name) and node.func.id in {
                    "HttpResponse",
                    "JsonResponse",
                }
                if is_message or is_response:
                    with self.subTest(line=node.lineno):
                        self.assertFalse(
                            any(
                                isinstance(child, ast.Name) and child.id == handler.name
                                for argument in (node.args[:1] if is_response else node.args)
                                for child in ast.walk(argument)
                            ),
                            "Exception-Text darf nicht direkt in Benutzerantworten fließen.",
                        )


@override_settings(
    PASSPHRASE_GATE_ENABLED=False,
    AUTOSTART_BOTS=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    BACKTEST_LOCAL_FALLBACK_ENABLED=True,
    SECURE_SSL_REDIRECT=False,
)
class ErrorDisclosureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("disclosure-owner")
        cls.other = User.objects.create_user("disclosure-other")
        cls.config = Configuration.objects.create(
            user=cls.user, name="Security test", symbols="BTC/USDT", countdown=0
        )

    def setUp(self):
        self.client.force_login(self.user)

    def assert_public_response(self, response):
        self.assertNotIn(PRIVATE_MARKER, response.content.decode())
        self.assertNotIn(PRIVATE_MARKER, str(dict(response.headers)))
        # Message-Cookies sind signiert/komprimiert, nicht verschlüsselt.
        if "messages" in response.cookies:
            decoded = CookieStorage(response.wsgi_request)._decode(
                response.cookies["messages"].value
            )
            self.assertNotIn(PRIVATE_MARKER, str(decoded))

    def assert_activation_failed(self, response):
        self.assertRedirects(response, reverse("config_list"), fetch_redirect_response=False)
        self.assert_public_response(response)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            [BOT_START_MESSAGE],
        )
        page = self.client.get(reverse("config_list"))
        self.assertContains(page, BOT_START_MESSAGE)
        self.assert_public_response(page)
        self.config.refresh_from_db()
        self.assertFalse(self.config.is_running)

    def test_bot_start_errors_are_generic_in_debug_and_production(self):
        for debug in (True, False):
            for exception_type in (RuntimeError, OperationalError, ValueError):
                exception = exception_type(EXCEPTION_TEXT)
                with (
                    self.subTest(debug=debug, exception=exception_type.__name__),
                    override_settings(DEBUG=debug),
                    patch("trading.views.validate_exchange_symbols"),
                    patch("trading.views.bot_manager.start_bot", side_effect=exception),
                    self.assertLogs("trading.views", level="WARNING") as logs,
                ):
                    response = self.client.post(reverse("config_activate", args=[self.config.id]))
                    self.assert_activation_failed(response)
                    assert_exception_logged(self, logs, exception)
                    entry = ErrorLog.objects.latest("id")
                    self.assertEqual(entry.configuration_id, self.config.id)
                    self.assertEqual(entry.message, EXCEPTION_TEXT)
                    self.assertEqual(entry.exception_type, exception_type.__name__)
                    self.assertIn(str(self.config.id), logs.records[0].getMessage())

    def test_activation_validation_errors_are_generic_and_logged(self):
        exceptions = (
            MarketDataError(EXCEPTION_TEXT),
            ValueError(EXCEPTION_TEXT),
            SymbolValidationError("test-only", [EXCEPTION_TEXT]),
        )
        for exception in exceptions:
            with (
                self.subTest(exception=type(exception).__name__),
                patch("trading.views.validate_exchange_symbols", side_effect=exception),
                patch("trading.views.bot_manager.start_bot") as start,
                self.assertLogs("trading.views", level="WARNING") as logs,
            ):
                response = self.client.post(reverse("config_activate", args=[self.config.id]))
                self.assert_activation_failed(response)
                start.assert_not_called()
                assert_exception_logged(self, logs, exception)
                self.assertEqual(ErrorLog.objects.latest("id").severity, "warning")

    def test_failed_error_log_write_does_not_replace_safe_activation_response(self):
        for target in ("validate_exchange_symbols", "bot_manager.start_bot"):
            for database_error in (OperationalError, InterfaceError):
                original = MarketDataError(EXCEPTION_TEXT)
                log_error = database_error(EXCEPTION_TEXT)
                with (
                    self.subTest(target=target, database_error=database_error.__name__),
                    patch("trading.views.validate_exchange_symbols"),
                    patch(f"trading.views.{target}", side_effect=original),
                    patch("trading.views.ErrorLog.objects.create", side_effect=log_error),
                    self.assertLogs("trading.views", level="WARNING") as logs,
                ):
                    response = self.client.post(reverse("config_activate", args=[self.config.id]))
                    self.assert_activation_failed(response)
                    assert_exception_logged(self, logs, original)
                    assert_exception_logged(self, logs, log_error)

    def test_real_log_integrity_failure_does_not_poison_request_transaction(self):
        create = ErrorLog.objects.create
        original = RuntimeError(EXCEPTION_TEXT)

        def invalid_log(**fields):
            # Echter DB-Fehler statt Mock-Exception: ohne Savepoint wäre die
            # äußere Request-/Test-Transaktion danach nicht mehr nutzbar.
            return create(**{**fields, "source": None})

        with (
            patch("trading.views.validate_exchange_symbols"),
            patch("trading.views.bot_manager.start_bot", side_effect=original),
            patch("trading.views.ErrorLog.objects.create", side_effect=invalid_log),
            self.assertLogs("trading.views", level="WARNING") as logs,
        ):
            response = self.client.post(reverse("config_activate", args=[self.config.id]))
        self.assert_activation_failed(response)
        assert_exception_logged(self, logs, original)
        self.assertTrue(any(record.exc_info[0] is IntegrityError for record in logs.records))
        self.assertFalse(ErrorLog.objects.exists())

    def test_successful_activation_keeps_existing_behavior(self):
        with (
            patch("trading.views.validate_exchange_symbols") as validate,
            patch("trading.views.bot_manager.start_bot") as start,
        ):
            response = self.client.post(reverse("config_activate", args=[self.config.id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [str(message) for message in get_messages(response.wsgi_request)],
            ["Bot wurde aktiviert."],
        )
        validate.assert_called_once_with(self.config.exchange, self.config.market, ["BTC/USDT"])
        start.assert_called_once()
        self.config.refresh_from_db()
        self.assertTrue(self.config.is_running)
        self.assertFalse(ErrorLog.objects.exists())

    def test_activation_access_and_csrf_checks_still_precede_backend_calls(self):
        url = reverse("config_activate", args=[self.config.id])
        with patch("trading.views.bot_manager.start_bot") as start:
            self.assertEqual(Client().post(url).status_code, 302)
            self.client.force_login(self.other)
            self.assertEqual(self.client.post(url).status_code, 404)
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.user)
            self.assertEqual(csrf_client.post(url).status_code, 403)
            self.assertEqual(csrf_client.get(url).status_code, 405)
            start.assert_not_called()

    def test_diagnostic_log_is_still_owner_scoped(self):
        entry = ErrorLog.objects.create(
            configuration=self.config, source="test", message=EXCEPTION_TEXT
        )
        page = self.client.get(reverse("error_log"))
        self.assertEqual(list(page.context["errors"]), [entry])
        self.assert_public_response(page)
        self.assertContains(page, f"Referenz #{entry.id}")
        self.client.force_login(self.other)
        self.assert_public_response(self.client.get(reverse("error_log")))
        response = self.client.post(reverse("error_log_resolve", args=[entry.id]))
        self.assertEqual(response.status_code, 404)
        self.assert_public_response(response)
        self.assertEqual(Client().get(reverse("error_log")).status_code, 302)

    def test_only_staff_can_read_owned_technical_log_details(self):
        ErrorLog.objects.create(
            configuration=self.config,
            source="test",
            message=EXCEPTION_TEXT,
            exception_type=PRIVATE_MARKER,
            details={"diagnostic": EXCEPTION_TEXT},
        )
        self.assert_public_response(self.client.get(reverse("error_log")))
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("error_log")), PRIVATE_MARKER)
        # Staff allein darf die Eigentümergrenze nicht umgehen.
        self.other.is_staff = True
        self.other.save(update_fields=["is_staff"])
        self.client.force_login(self.other)
        self.assert_public_response(self.client.get(reverse("error_log")))

    def test_chained_exception_details_stay_in_server_traceback(self):
        def start(config):
            try:
                raise OperationalError(EXCEPTION_TEXT)
            except OperationalError as cause:
                raise RuntimeError("Abbruch") from cause

        with (
            patch("trading.views.validate_exchange_symbols"),
            patch("trading.views.bot_manager.start_bot", side_effect=start),
            self.assertLogs("trading.views", level="WARNING") as logs,
        ):
            response = self.client.post(reverse("config_activate", args=[self.config.id]))
        self.assert_activation_failed(response)
        self.assertIn(PRIVATE_MARKER, "\n".join(logs.output))
        self.assertIsInstance(logs.records[0].exc_info[1].__cause__, OperationalError)
        self.assert_public_response(self.client.get(reverse("error_log")))

    def test_symbol_catalog_error_is_generic_and_logged(self):
        exception = MarketDataError(EXCEPTION_TEXT)
        with (
            patch("trading.views.get_available_symbols", side_effect=exception),
            self.assertLogs("trading.views", level="WARNING") as logs,
        ):
            response = self.client.get(
                reverse("symbol_suggestions_api"), {"exchange": "binance", "market": "spot"}
            )
        self.assertEqual(response.status_code, 503)
        self.assert_public_response(response)
        self.assertEqual(response.json()["suggestions"], [])
        self.assertIn("no-store", response["Cache-Control"])
        assert_exception_logged(self, logs, exception)

    def test_scanner_errors_are_generic_in_single_and_all_exchange_modes(self):
        for exchange, target in (
            ("binance", "trading.views.scan_market_opportunities"),
            ("all", "trading.market_scanner.scan_all_market_opportunities"),
        ):
            for exception_type, status in (
                (MarketScannerError, 503),
                (MarketScannerFilterError, 400),
            ):
                exception = exception_type(EXCEPTION_TEXT)
                with (
                    self.subTest(exchange=exchange, exception=exception_type.__name__),
                    patch(target, side_effect=exception),
                    self.assertLogs("trading.views", level="INFO") as logs,
                ):
                    response = self.client.get(
                        reverse("market_opportunities_api"),
                        {"exchange": exchange, "market": "spot"},
                    )
                    self.assertEqual(response.status_code, status)
                    self.assert_public_response(response)
                    self.assertEqual(
                        response.json()["error"],
                        SCANNER_MESSAGE if status == 503 else "Ungültige Scanner-Parameter.",
                    )
                    assert_exception_logged(self, logs, exception)

    def test_scanner_partial_and_total_outages_do_not_embed_exception_strings(self):
        exception = MarketScannerError(EXCEPTION_TEXT)
        for success in (False, True):

            def scan(exchange, *args, success=success, **kwargs):
                if success and exchange == "binance":
                    return {"gainers": ["BTC/USDT"], "losers": []}
                raise exception

            with (
                self.subTest(partial_success=success),
                patch("trading.market_scanner.scan_market_opportunities", side_effect=scan),
                self.assertLogs("trading.market_scanner", level="WARNING") as logs,
            ):
                response = self.client.get(
                    reverse("market_opportunities_api"), {"exchange": "all", "market": "spot"}
                )
            self.assertEqual(response.status_code, 200 if success else 503)
            self.assert_public_response(response)
            self.assertTrue(response.json()["errors"])
            if success:
                self.assertEqual(response.json()["exchanges"]["binance"]["gainers"], ["BTC/USDT"])
            assert_exception_logged(self, logs, exception)

    def test_bot_status_does_not_forward_raw_last_error_or_mutate_manager_state(self):
        for error in (None, EXCEPTION_TEXT):
            status = {"running": True, "last_error": error, "last_error_at": 123}
            with (
                self.subTest(error=bool(error)),
                patch("trading.views.bot_manager.status", return_value=status),
            ):
                response = self.client.get(reverse("bot_status_api"), {"config_id": self.config.id})
            self.assertEqual(response.status_code, 200)
            self.assert_public_response(response)
            self.assertEqual(response.json()["last_error_at"], 123)
            self.assertEqual(bool(response.json()["last_error"]), bool(error))
            self.assertEqual(status["last_error"], error)

    def test_automatic_restart_failure_is_logged_and_marks_bot_inactive(self):
        self.config.is_running = True
        self.config.save(update_fields=["is_running"])
        exception = RuntimeError(EXCEPTION_TEXT)
        with (
            patch("trading.views.bot_manager.is_running", return_value=False),
            patch("trading.views.bot_manager.start_bot", side_effect=exception),
            self.assertLogs("trading.views", level="WARNING") as logs,
        ):
            response = self.client.get(reverse("bot_status_api"), {"config_id": self.config.id})
        self.assertEqual(response.status_code, 200)
        self.assert_public_response(response)
        self.assertFalse(response.json()["is_running_flag"])
        self.config.refresh_from_db()
        self.assertFalse(self.config.is_running)
        assert_exception_logged(self, logs, exception)

    def test_manual_sell_and_kill_switch_errors_are_generic(self):
        for name, data, message in (
            ("manual_sell", {"symbol": "BTC/USDT"}, SELL_MESSAGE),
            ("kill_switch", {"confirm1": "LIQUIDATE", "confirm2": "LIQUIDATE"}, KILL_MESSAGE),
        ):
            for exception_type, status in ((ValueError, 400), (RuntimeError, 500)):
                exception = exception_type(EXCEPTION_TEXT)
                with (
                    self.subTest(action=name, exception=exception_type.__name__),
                    patch(f"trading.views.bot_manager.{name}", side_effect=exception),
                    self.assertLogs("trading.views", level="WARNING") as logs,
                ):
                    response = self.client.post(reverse(name, args=[self.config.id]), data)
                    self.assertEqual(response.status_code, status)
                    self.assert_public_response(response)
                    self.assertEqual(response.json(), {"status": "error", "message": message})
                    assert_exception_logged(self, logs, exception)

    def test_failed_error_log_write_keeps_safe_action_response(self):
        for name, data in (
            ("manual_sell", {"symbol": "BTC/USDT"}),
            ("kill_switch", {"confirm1": "LIQUIDATE", "confirm2": "LIQUIDATE"}),
        ):
            original = RuntimeError(EXCEPTION_TEXT)
            log_error = OperationalError(EXCEPTION_TEXT)
            with (
                self.subTest(action=name),
                patch(f"trading.views.bot_manager.{name}", side_effect=original),
                patch("trading.views.ErrorLog.objects.create", side_effect=log_error),
                self.assertLogs("trading.views", level="WARNING") as logs,
            ):
                response = self.client.post(reverse(name, args=[self.config.id]), data)
            self.assertEqual(response.status_code, 500)
            self.assert_public_response(response)
            assert_exception_logged(self, logs, original)
            assert_exception_logged(self, logs, log_error)

    def test_partial_liquidation_logs_exception_without_exposing_it_in_result(self):
        bot = TradingBot(self.config)
        bot.positions = {"BTC/USDT": object()}
        bot.fetch_tickers = AsyncMock(return_value={"BTC/USDT": {"last": "100"}})
        exception = RuntimeError(EXCEPTION_TEXT)
        bot.execute_trade = AsyncMock(side_effect=exception)
        bot._persist_error = AsyncMock()
        with (
            patch(
                "trading.views.bot_manager.kill_switch",
                side_effect=lambda config_id: async_to_sync(bot.liquidate_all_positions)(),
            ),
            self.assertLogs("trading", level="WARNING") as logs,
        ):
            response = self.client.post(
                reverse("kill_switch", args=[self.config.id]),
                {"confirm1": "LIQUIDATE", "confirm2": "LIQUIDATE"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "partial")
        self.assertEqual(response.json()["sold"], [])
        self.assertIn("BTC/USDT", response.json()["errors"])
        self.assert_public_response(response)
        assert_exception_logged(self, logs, exception)
        bot._persist_error.assert_awaited_once()
        self.assertFalse(bot.liquidating)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False, REDIS_URL="redis://localhost:6379/0")
    def test_worker_connection_error_is_generic_in_api(self):
        exception = RedisError(EXCEPTION_TEXT)
        with (
            patch("trading.worker_status.Redis.from_url", side_effect=exception),
            patch("trading.worker_status._STATUS_CACHE", None),
            self.assertLogs("trading.worker_status", level="WARNING") as logs,
        ):
            response = self.client.get(reverse("backtesting_status_api"), {"refresh": "1"})
        self.assertEqual(response.status_code, 200)
        self.assert_public_response(response)
        self.assertTrue(response.json()["backtesting"]["error"])
        assert_exception_logged(self, logs, exception)

    def test_legacy_failed_backtest_result_is_not_rendered_as_user_message(self):
        task = BacktestTask.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            status="failed",
            result={"error": EXCEPTION_TEXT},
        )
        response = self.client.get(reverse("backtesting_form", args=[self.config.id]))
        self.assertEqual(response.status_code, 200)
        self.assert_public_response(response)
        self.assertContains(response, "Backtest fehlgeschlagen.")
        task.refresh_from_db()
        self.assertEqual(task.result["error"], EXCEPTION_TEXT)

    def test_configuration_form_does_not_leak_upstream_errors(self):
        for view_name, args in (("config", []), ("config_edit", [self.config.id])):
            for exception_type in (MarketDataError, ValueError):
                exception = exception_type(EXCEPTION_TEXT)
                with (
                    self.subTest(view=view_name, exception=exception_type.__name__),
                    patch(
                        "trading.forms.validate_exchange_symbols", side_effect=exception
                    ) as validate,
                    self.assertLogs("trading.forms", level="WARNING") as logs,
                ):
                    response = self.client.post(
                        reverse(view_name, args=args), model_to_dict(self.config)
                    )
                self.assertEqual(response.status_code, 200)
                validate.assert_called_once()
                self.assert_public_response(response)
                self.assertContains(
                    response, "Die Exchange-Konfiguration konnte nicht verifiziert werden."
                )
                assert_exception_logged(self, logs, exception)

    def test_report_context_errors_are_generic_and_logged(self):
        for name in ("generate_report", "generate_report_html"):
            exception = RuntimeError(EXCEPTION_TEXT)
            with (
                self.subTest(view=name),
                patch("trading.views._build_report_context", side_effect=exception),
                self.assertLogs("trading.views", level="WARNING") as logs,
            ):
                response = self.client.get(reverse(name, args=[self.config.id]))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.content.decode(), REPORT_MESSAGE)
            assert_exception_logged(self, logs, exception)

    def test_pdf_import_errors_are_generic_and_logged(self):
        real_import = builtins.__import__
        for exception_type in (ImportError, OSError):
            exception = exception_type(EXCEPTION_TEXT)

            def import_module(name, *args, exception=exception, **kwargs):
                if name == "weasyprint":
                    raise exception
                return real_import(name, *args, **kwargs)

            with (
                self.subTest(exception=exception_type.__name__),
                patch("trading.views._build_report_context", return_value={}),
                patch("builtins.__import__", side_effect=import_module),
                self.assertLogs("trading.views", level="WARNING") as logs,
            ):
                response = self.client.get(reverse("generate_report", args=[self.config.id]))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.content.decode(), REPORT_MESSAGE)
            assert_exception_logged(self, logs, exception)

    def test_pdf_render_failure_is_safe_for_trading_and_backtest_exports(self):
        task = BacktestTask.objects.create(
            configuration=self.config, symbol="BTC/USDT", status="completed"
        )
        for name, object_id in (
            ("generate_report", self.config.id),
            ("generate_backtest_pdf", task.id),
        ):
            exception = RuntimeError(EXCEPTION_TEXT)
            html = Mock()
            html.return_value.write_pdf.side_effect = exception
            with (
                self.subTest(view=name),
                patch("trading.views._build_report_context", return_value={}),
                patch("trading.views.render_to_string", return_value="<p>Test report</p>"),
                patch.dict("sys.modules", {"weasyprint": SimpleNamespace(HTML=html)}),
                self.assertLogs("trading.views", level="WARNING") as logs,
            ):
                response = self.client.get(reverse(name, args=[object_id]))
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.content.decode(), REPORT_MESSAGE)
            assert_exception_logged(self, logs, exception)

    def test_successful_pdf_response_keeps_content_type_and_download_headers(self):
        expected_pdf = b"%PDF-1.7\nsec10-test-only"
        html = Mock()
        html.return_value.write_pdf.return_value = expected_pdf
        with (
            patch("trading.views._build_report_context", return_value={}),
            patch("trading.views.render_to_string", return_value="<p>Test report</p>"),
            patch.dict("sys.modules", {"weasyprint": SimpleNamespace(HTML=html)}),
        ):
            response = self.client.get(reverse("generate_report", args=[self.config.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected_pdf)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertRegex(response["Content-Disposition"], r'^attachment; filename=".+\.pdf"$')
        html.return_value.write_pdf.assert_called_once_with()
