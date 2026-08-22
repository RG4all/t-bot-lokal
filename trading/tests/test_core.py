import asyncio
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.db import OperationalError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from trading.backtesting import Backtesting
from trading.forms import BacktestForm, ConfigurationForm
from trading.market_data import (
    BinancePublicMarketData,
    BitMartPublicMarketData,
    BitunixPublicMarketData,
    MarketDataConnectionError,
    SymbolValidationError,
    _ban_timestamp,
)
from trading.middleware import DatabaseAvailabilityMiddleware
from trading.models import BacktestTask, Configuration, DataLog, ErrorLog, TradingLog
from trading.symbols import get_available_symbols
from trading.tasks import dispatch_task, run_backtest
from trading.trading_bot import TradingBot, _close_db_circuit, db_safe


class BacktestingTests(TestCase):
    def test_simulation_accepts_float_thresholds_and_accounts_for_both_fees(self):
        capital, report = Backtesting.simulate_trading_detailed(
            [Decimal(100), Decimal(101), Decimal(103), Decimal(110)],
            -100.0,
            -100.0,
            -100.0,
            {
                "start_capital": Decimal(100),
                "trade_amount": Decimal(10),
                "take_profit": Decimal(1),
                "stop_loss": Decimal(50),
                "fee_percentage": Decimal("0.1"),
            },
        )
        self.assertEqual(report["num_buys"], 1)
        self.assertEqual(report["num_sells"], 1)
        self.assertGreater(capital, Decimal(100))
        sell = next(trade for trade in report["trades"] if trade["type"] == "sell")
        self.assertGreater(sell["profit_nominal"], 0)


class FormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("form-user", password="a-secure-test-pass")
        validator = patch("trading.forms.validate_exchange_symbols")
        self.validate_exchange_symbols = validator.start()
        self.addCleanup(validator.stop)

    def configuration_data(self, **overrides):
        data = {
            "name": "Test",
            "exchange": "binance",
            "market": "spot",
            "symbols": "btc/usdt, ETH/USDT,btc/usdt",
            "start_capital": "100",
            "trade_amount": "10",
            "sales_stop_threshold": "10",
            "take_profit": "1",
            "stop_loss": "1",
            "fee": "0.1",
            "api_key": "",
            "secret_key": "",
            "countdown": "0",
            "time_interval": "2",
            "div_DVA_prev_NDA_threshold_buy": "0",
            "deltadelta_threshold_buy": "0",
            "nda_threshold_buy": "0",
        }
        data.update(overrides)
        return data

    def test_configuration_normalizes_symbols(self):
        form = ConfigurationForm(self.configuration_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["symbols"], "BTC/USDT,ETH/USDT")

    def test_configuration_rejects_overspending(self):
        form = ConfigurationForm(
            self.configuration_data(start_capital="10", trade_amount="10", fee="1")
        )
        self.assertFalse(form.is_valid())
        self.assertIn("trade_amount", form.errors)
        self.assertIn("is-invalid", form.fields["trade_amount"].widget.attrs["class"])

    def test_configuration_reports_exchange_specific_invalid_symbols(self):
        self.validate_exchange_symbols.side_effect = SymbolValidationError(
            "BitMart", ["KAITO/USDT"]
        )
        form = ConfigurationForm(self.configuration_data(exchange="bitmart", symbols="KAITO/USDT"))
        self.assertFalse(form.is_valid())
        self.assertIn("KAITO/USDT", form.errors["symbols"][0])
        self.assertIn("BitMart", form.errors["symbols"][0])

    def test_backtest_rejects_zero_step_and_excessive_range(self):
        data = {
            "acc_from": 0,
            "acc_to": 100,
            "acc_steps": 0,
            "nda_from": 0,
            "nda_to": 100,
            "nda_steps": 0.01,
            "deltadelta_from": 0,
            "deltadelta_to": 100,
            "deltadelta_steps": 0.01,
        }
        form = BacktestForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("acc_steps", form.errors)

    def test_configuration_and_backtest_indicator_labels_are_aligned(self):
        config_form = ConfigurationForm()
        backtest_form = BacktestForm()

        # Prüfe, dass die Beschleunigung in beiden Formularen konsistent benannt ist
        self.assertIn("Beschleunigung", config_form.fields["div_DVA_prev_NDA_threshold_buy"].label)
        self.assertIn("DVA", config_form.fields["div_DVA_prev_NDA_threshold_buy"].label)
        self.assertIn("Beschleunigung", backtest_form.fields["acc_from"].label)

        # Prüfe DeltaDelta
        self.assertIn("DeltaDelta", config_form.fields["deltadelta_threshold_buy"].label)
        self.assertIn("DeltaDelta", backtest_form.fields["deltadelta_from"].label)

        # Prüfe NDA
        self.assertIn("NDA", config_form.fields["nda_threshold_buy"].label)
        self.assertIn("NDA", backtest_form.fields["nda_from"].label)

        # Prüfe Tooltip / Help_Text
        self.assertTrue(bool(config_form.fields["div_DVA_prev_NDA_threshold_buy"].help_text))
        self.assertTrue(bool(backtest_form.fields["acc_from"].help_text))
        self.assertEqual(
            config_form.fields["div_DVA_prev_NDA_threshold_buy"].widget.attrs.get("title"),
            config_form.fields["div_DVA_prev_NDA_threshold_buy"].help_text,
        )


class MarketDataAdapterTests(TestCase):
    def test_binance_uses_one_websocket_for_all_symbols(self):
        provider = BinancePublicMarketData("spot")

        class FakeWebSocket:
            closed = False

            def __init__(self):
                self.messages = iter(
                    [
                        SimpleNamespace(
                            type=aiohttp.WSMsgType.TEXT,
                            data='{"data":{"s":"BTCUSDT","c":"123.45"}}',
                        ),
                        SimpleNamespace(
                            type=aiohttp.WSMsgType.TEXT,
                            data='{"data":{"s":"ETHUSDT","c":"45.67"}}',
                        ),
                    ]
                )

            async def receive(self):
                try:
                    return next(self.messages)
                except StopIteration:
                    await asyncio.sleep(1)
                    return SimpleNamespace(type=aiohttp.WSMsgType.PING, data="")

            async def close(self):
                self.closed = True

        async def fake_connect(symbols):
            provider._symbol_by_compact = {
                "BTCUSDT": "BTC/USDT",
                "ETHUSDT": "ETH/USDT",
            }
            provider._websocket = FakeWebSocket()

        provider._connect = fake_connect
        result = async_to_sync(provider.fetch_tickers_async)(["BTC/USDT", "ETH/USDT"])
        async_to_sync(provider.close)()
        self.assertEqual(result["BTC/USDT"]["last"], "123.45")
        self.assertEqual(result["ETH/USDT"]["last"], "45.67")

    def test_binance_reconnects_after_transient_websocket_close(self):
        provider = BinancePublicMarketData("spot")
        calls = 0

        async def fake_fetch(symbols, timeout_seconds):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise MarketDataConnectionError("Verbindung geschlossen")
            return {"BTC/USDT": {"last": "123"}}

        provider._fetch_once = fake_fetch
        provider._disconnect = AsyncMock()
        with patch("trading.market_data.asyncio.sleep", new=AsyncMock()):
            result = async_to_sync(provider.fetch_tickers_async)(["BTC/USDT"])
        self.assertEqual(calls, 2)
        self.assertEqual(result["BTC/USDT"]["last"], "123")

    def test_binance_ban_timestamp_is_parsed_from_error(self):
        self.assertEqual(
            _ban_timestamp("IP banned until 1787181287549. Please use WebSocket Streams"),
            1787181287.549,
        )

    def test_bitmart_adapter_validates_then_reads_v3_ticker(self):
        provider = BitMartPublicMarketData("spot")

        def fake_json(url, **kwargs):
            if url == provider.symbols_url:
                return {"code": 1000, "data": {"symbols": ["BTC_USDT"]}}
            return {"code": 1000, "data": {"symbol": "BTC_USDT", "last": "321.00"}}

        provider._json = fake_json
        result = provider.fetch_tickers(["BTC/USDT"])
        provider.close()
        self.assertEqual(result["BTC/USDT"]["last"], "321.00")

    def test_binance_autocomplete_differs_between_spot_and_futures(self):
        spot = get_available_symbols("binance", "spot")
        futures = get_available_symbols("binance", "futures")
        self.assertNotIn("1000PEPE/USDT", spot)
        self.assertIn("1000PEPE/USDT", futures)

    def test_bitunix_futures_uses_validated_batch_ticker(self):
        provider = BitunixPublicMarketData("futures")

        def fake_json(url, **kwargs):
            if url.endswith("/trading_pairs"):
                return {
                    "code": 0,
                    "data": [{"symbol": "BTCUSDT", "symbolStatus": "OPEN"}],
                }
            return {
                "code": 0,
                "data": [{"symbol": "BTCUSDT", "lastPrice": "65432.1"}],
            }

        provider._json = fake_json
        result = provider.fetch_tickers(["BTC/USDT"])
        provider.close()
        self.assertEqual(result["BTC/USDT"]["last"], "65432.1")


class DatabaseAvailabilityMiddlewareTests(TestCase):
    def tearDown(self):
        _close_db_circuit()

    @override_settings(DB_CIRCUIT_BREAKER_SECONDS=30)
    def test_db_safe_opens_circuit_and_suppresses_followup_writes(self):
        calls = 0

        @db_safe(max_retries=1, base_delay=0.1, max_delay=0.1)
        def failing_read():
            nonlocal calls
            calls += 1
            raise OperationalError("connection refused")

        with self.assertRaises(OperationalError):
            failing_read()
        self.assertEqual(calls, 2)

        @db_safe(max_retries=1, suppress=True)
        def blocked_write():
            raise AssertionError("Offener Circuit darf die Funktion nicht aufrufen")

        self.assertIsNone(blocked_write())

    def test_database_outage_returns_json_503_for_api(self):
        def unavailable(request):
            raise OperationalError("connection refused")

        request = RequestFactory().get("/api/info/1/")
        response = DatabaseAvailabilityMiddleware(unavailable)(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "10")
        self.assertIn("database_temporarily_unavailable", response.content.decode())


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class ViewSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="owner-test-password")
        self.other = User.objects.create_user("other", password="other-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Inactive config",
            symbols="BTC/USDT",
            countdown=0,
        )
        self.client.force_login(self.user)

    def test_help_page_renders_manual(self):
        response = self.client.get(reverse("help"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Benutzer- und Indikatorhandbuch")
        self.assertContains(response, "Verbindungsmodell ab Version 2.0.4")
        self.assertContains(response, "Beschleunigung (Acceleration)")
        self.assertContains(response, "div_DVA_prev_NDA_threshold_buy")

    def test_dashboard_displays_aligned_indicator_fields_and_tooltips(self):
        response = self.client.get(reverse("dashboard"), {"config_id": self.config.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beschleunigung")
        self.assertContains(response, "DeltaDelta")
        self.assertContains(response, "NDA")
        self.assertContains(response, "field-info-icon")
        self.assertContains(response, 'data-bs-toggle="tooltip"')

    def test_backtesting_form_displays_tooltips_and_aligned_labels(self):
        response = self.client.get(reverse("backtesting_form", args=[self.config.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beschleunigung")
        self.assertContains(response, "DeltaDelta")
        self.assertContains(response, "NDA")
        self.assertContains(response, "field-info-icon")
        self.assertContains(response, 'data-bs-toggle="tooltip"')

    def test_inactive_configuration_is_visible_on_dashboard(self):
        response = self.client.get(reverse("dashboard"), {"config_id": self.config.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inactive config")
        self.assertContains(response, "Deaktiviert")

    def test_state_changes_reject_get(self):
        for name in ("config_activate", "config_deactivate", "reset_log"):
            response = self.client.get(reverse(name, args=[self.config.id]))
            self.assertEqual(response.status_code, 405)
        response = self.client.get(reverse("manual_sell", args=[self.config.id]))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        BACKTEST_LOCAL_FALLBACK_ENABLED=True,
    )
    def test_backtesting_status_reports_local_fallback_and_runtime_heartbeat(self):
        response = self.client.get(reverse("backtesting_status_api"), {"refresh": "1"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["backtesting"]["mode"], "local-fallback")
        self.assertTrue(payload["backtesting"]["available"])
        self.assertIn("peak_rss_mb", payload["web_runtime"])
        self.assertTrue(payload["web_runtime"]["responsive"])

    def test_backtest_control_is_owner_scoped(self):
        foreign_config = Configuration.objects.create(
            user=self.other,
            name="Foreign",
            symbols="BTC/USDT",
        )
        task = BacktestTask.objects.create(
            configuration=foreign_config,
            symbol="BTC/USDT",
        )
        response = self.client.post(
            reverse("control_backtest", args=[task.id]),
            {"action": "cancel"},
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse("generate_backtest_pdf", args=[task.id]))
        self.assertEqual(response.status_code, 404)

    def test_analysis_uses_local_data_without_binance_rest(self):
        now = timezone.now()
        for index in range(20):
            row = DataLog.objects.create(
                configuration=self.config,
                symbol="BTC/USDT",
                price=100 + index,
                min_price=100 + index,
                max_price=100 + index,
            )
            DataLog.objects.filter(id=row.id).update(timestamp=now - timedelta(minutes=20 - index))
        response = self.client.get(
            reverse("analyse"),
            {"symbols": "BTC/USDT", "timeframe": "1m"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0 externe API-Requests")
        self.assertContains(response, "Berechnung erfolgreich")
        self.assertNotContains(response, "418")

    def test_data_api_rejects_unknown_symbol(self):
        response = self.client.get(
            reverse("data_logs_api"),
            {"config_id": self.config.id, "symbol": "ETH/USDT"},
        )
        self.assertEqual(response.status_code, 400)

    def create_trading_logs(self, count):
        for index in range(count):
            TradingLog.objects.create(
                configuration=self.config,
                symbol="BTC/USDT",
                action="buy" if index % 2 == 0 else "sell",
                price=Decimal(100 + index),
                amount=Decimal("0.1"),
                fee_amount=Decimal("0.01"),
                pl_nominal=Decimal(index % 3 - 1),
                pl_relative=Decimal(0),
                total_pl=Decimal(index),
                current_capital=Decimal(100 + index),
                tank=Decimal(index),
                order_id=f"test-{index}",
            )

    def test_info_api_deducts_open_position_from_available_cash(self):
        TradingLog.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            action="buy",
            price=Decimal(100),
            amount=Decimal("0.1"),
            fee_amount=Decimal("0.01"),
            pl_nominal=Decimal(0),
            pl_relative=Decimal(0),
            total_pl=Decimal(0),
            current_capital=Decimal("89.99"),
            tank=Decimal(0),
            order_id="open-buy",
        )
        response = self.client.get(reverse("info_api", args=[self.config.id])).json()
        self.assertEqual(response["available_cash"], 89.99)
        self.assertEqual(response["invested_capital"], 10.01)
        self.assertEqual(response["open_position_count"], 1)

    def test_trading_log_api_is_paginated_by_100(self):
        self.create_trading_logs(205)
        first = self.client.get(reverse("logs_api", args=[self.config.id]), {"page": 1}).json()
        third = self.client.get(reverse("logs_api", args=[self.config.id]), {"page": 3}).json()
        self.assertEqual(len(first["results"]), 100)
        self.assertEqual(first["pagination"]["pages"], 3)
        self.assertEqual(first["pagination"]["count"], 205)
        self.assertEqual(len(third["results"]), 5)

    def test_csv_and_html_reports_use_descriptive_filename(self):
        self.create_trading_logs(1)
        with (
            patch(
                "trading.views._build_report_context",
                return_value={"config": self.config, "logs": []},
            ),
            patch("trading.views._pdf_response", return_value=HttpResponse()) as pdf_response,
        ):
            self.client.get(reverse("generate_report", args=[self.config.id]))
        self.assertRegex(pdf_response.call_args.args[3], r"owner_binance_\d+_\d{8}_\d{6}\.pdf")

        csv_response = self.client.get(reverse("generate_report_csv", args=[self.config.id]))
        self.assertEqual(csv_response.status_code, 200)
        disposition = csv_response["Content-Disposition"]
        self.assertRegex(disposition, r"owner_binance_\d+_\d{8}_\d{6}\.csv")
        with patch(
            "trading.views._build_report_context",
            return_value={"config": self.config, "logs": []},
        ):
            html_response = self.client.get(reverse("generate_report_html", args=[self.config.id]))
        self.assertEqual(html_response.status_code, 200)
        self.assertRegex(
            html_response["Content-Disposition"],
            r"owner_binance_\d+_\d{8}_\d{6}\.html",
        )

    def test_kill_switch_requires_double_confirmation(self):
        url = reverse("kill_switch", args=[self.config.id])
        self.assertEqual(self.client.post(url, {"confirm1": "LIQUIDATE"}).status_code, 400)
        with patch(
            "trading.views.bot_manager.kill_switch",
            return_value={"sold": ["BTC/USDT"], "errors": {}},
        ) as kill_switch:
            response = self.client.post(
                url,
                {"confirm1": "LIQUIDATE", "confirm2": "LIQUIDATE"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sold"], ["BTC/USDT"])
        kill_switch.assert_called_once_with(self.config.id)

    def test_symbol_autocomplete_uses_exchange_and_market(self):
        with patch(
            "trading.views.get_available_symbols",
            return_value=("BTC/USDT", "ETH/USDT"),
        ) as catalog:
            response = self.client.get(
                reverse("symbol_suggestions_api"),
                {"exchange": "bitunix", "market": "futures", "q": "BT"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["suggestions"], ["BTC/USDT"])
        catalog.assert_called_once_with("bitunix", "futures")

    def test_error_log_is_owner_scoped_and_can_resolve_entries(self):
        own_error = ErrorLog.objects.create(
            configuration=self.config,
            severity="warning",
            source="test.source",
            exception_type="TestError",
            message="Eigener Fehler",
            details={"symbol": "BTC/USDT"},
        )
        foreign_config = Configuration.objects.create(
            user=self.other,
            name="Foreign errors",
            symbols="BTC/USDT",
        )
        foreign_error = ErrorLog.objects.create(
            configuration=foreign_config,
            source="foreign",
            message="Fremder Fehler",
        )
        response = self.client.get(reverse("error_log"))
        self.assertContains(response, "Eigener Fehler")
        self.assertNotContains(response, "Fremder Fehler")
        response = self.client.post(reverse("error_log_resolve", args=[own_error.id]))
        self.assertRedirects(response, reverse("error_log"))
        own_error.refresh_from_db()
        self.assertTrue(own_error.resolved)
        response = self.client.post(reverse("error_log_resolve", args=[foreign_error.id]))
        self.assertEqual(response.status_code, 404)


class GateTests(TestCase):
    @override_settings(PASSPHRASE="correct", PASSPHRASE_GATE_ENABLED=True)
    def test_gate_does_not_redirect_to_external_next_url(self):
        response = self.client.post(
            reverse("passphrase_gate") + "?next=https://evil.example/",
            {"passphrase": "correct", "next": "https://evil.example/"},
        )
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)


@override_settings(AUTOSTART_BOTS=False)
class TradingBotTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("bot-user", password="bot-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Bot",
            symbols="BTC/USDT",
            start_capital=Decimal(100),
            trade_amount=Decimal(10),
            fee=Decimal("0.1"),
            countdown=0,
        )

    def test_trade_is_buffered_and_position_kept_during_db_outage(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        with patch(
            "trading.trading_bot.db_create_tradinglog_safe",
            new=AsyncMock(return_value=None),
        ):
            async_to_sync(bot.execute_trade)("BTC/USDT", "buy")
        self.assertIn("BTC/USDT", bot.positions)
        self.assertEqual(len(bot.pending_trading_logs), 1)
        self.assertEqual(bot.pending_trading_logs[0]["action"], "buy")

    def test_kill_switch_uses_fresh_ticker_and_closes_every_position(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy")
        bot.fetch_tickers = AsyncMock(return_value={"BTC/USDT": {"last": "105"}})
        result = async_to_sync(bot.liquidate_all_positions)()
        self.assertEqual(result, {"sold": ["BTC/USDT"], "errors": {}})
        self.assertEqual(bot.positions, {})
        self.assertEqual(TradingLog.objects.filter(action="sell").count(), 1)

    def test_buy_and_sell_keep_amount_and_include_buy_fee(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy")
        buy = TradingLog.objects.get(action="buy")
        self.assertEqual(
            buy.current_capital,
            self.config.start_capital - (buy.amount * buy.price + buy.fee_amount),
        )
        bot.price_buffer["BTC/USDT"] = [Decimal(110)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "sell")
        sell = TradingLog.objects.get(action="sell")
        self.assertEqual(sell.amount, buy.amount)
        expected = sell.amount * (sell.price - buy.price) - buy.fee_amount - sell.fee_amount
        self.assertEqual(sell.pl_nominal, expected.quantize(Decimal("0.00000001")))
        self.assertEqual(sell.current_capital, self.config.start_capital + sell.pl_nominal)


@override_settings(AUTOSTART_BOTS=False, PASSPHRASE_GATE_ENABLED=False)
class BacktestTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("backtest-user", password="backtest-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Backtest",
            symbols="BTC/USDT",
            countdown=0,
        )
        self.client.force_login(self.user)
        for price in (100, 101, 103, 110):
            DataLog.objects.create(
                configuration=self.config,
                symbol="BTC/USDT",
                price=price,
                min_price=price,
                max_price=price,
            )

    def test_celery_backtest_resource_guards_are_configured(self):
        from django.conf import settings

        self.assertEqual(
            settings.CELERY_TASK_ROUTES["trading.tasks.run_backtest"]["queue"], "backtest"
        )
        self.assertEqual(settings.CELERY_WORKER_CONCURRENCY, 1)
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)
        self.assertLessEqual(settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD, 384_000)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        BACKTEST_LOCAL_FALLBACK_ENABLED=False,
    )
    def test_dispatch_refuses_local_production_backtest(self):
        with self.assertRaisesRegex(RuntimeError, "separaten Celery-Worker"):
            dispatch_task(run_backtest, self.config.id, {}, ["BTC/USDT"], 1)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        BACKTEST_LOCAL_FALLBACK_ENABLED=False,
    )
    def test_backtesting_page_degrades_without_worker(self):
        response = self.client.post(
            reverse("backtesting_form", args=[self.config.id]),
            {
                "acc_from": 0,
                "acc_to": 0,
                "acc_steps": 1,
                "nda_from": 0,
                "nda_to": 0,
                "nda_steps": 1,
                "deltadelta_from": 0,
                "deltadelta_to": 0,
                "deltadelta_steps": 1,
                "trade_amount": 10,
                "take_profit": 1,
                "stop_loss": 1,
                "fee": 0.1,
                "max_price_points": 500,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "separaten Celery-Worker")
        self.assertEqual(BacktestTask.objects.count(), 0)

    def test_run_backtest_completes_and_serializes_decimals(self):
        params = {
            "acc_from": -100,
            "acc_to": -100,
            "acc_steps": 1,
            "nda_from": -100,
            "nda_to": -100,
            "nda_steps": 1,
            "deltadelta_from": -100,
            "deltadelta_to": -100,
            "deltadelta_steps": 1,
        }
        task = BacktestTask.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            parameters=params,
        )
        run_backtest.run(self.config.id, params, ["BTC/USDT"], task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.progress, 100)
        self.assertIn("BTC/USDT", task.result["symbol_results"])
        self.assertEqual(task.result["metrics"]["combinations"], 1)
        self.assertGreater(task.result["metrics"]["peak_rss_mb"], 0)
