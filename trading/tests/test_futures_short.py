"""Tests für Futures-Hebel, Long-/Short-Logik und die Backtesting-Dokumentation.

Die Tests decken vier Bereiche ab:

1. ``StrategyMathTests`` – die reine Rechen- und Signallogik in
   ``trading.strategy`` (Hebelauflösung je Börse, Spiegelung der Indikatoren,
   Liquidationsschwellen).
2. ``LeverageConfigurationTests`` – Konfiguration, Formularvalidierung und
   Rückwärtskompatibilität bestehender Konfigurationen.
3. ``ShortTradingTests`` – Live-Bot und Backtest-Engine im Short- und
   Hebelbetrieb, inklusive Kapitalbindung und Liquidation.
4. ``BacktestingDocumentationTests`` – der Dokumentations-Button und die
   gerenderte Ausgabe von ``docs/backtesting.md``.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from trading import strategy
from trading.backtesting import Backtesting
from trading.forms import BacktestForm, ConfigurationForm
from trading.models import Configuration, TradingLog
from trading.trading_bot import TradingBot
from trading.views import DOCUMENTS, _render_document

BASE_CONFIG_POST = {
    "name": "Futures Test",
    "exchange": "binance",
    "market": "futures",
    "symbols": "BTC/USDT",
    "start_capital": "1000",
    "trade_amount": "100",
    "sales_stop_threshold": "0",
    "take_profit": "1",
    "stop_loss": "1",
    "fee": "0.1",
    "countdown": "0",
    "time_interval": "5",
    "div_DVA_prev_NDA_threshold_buy": "0",
    "deltadelta_threshold_buy": "0",
    "nda_threshold_buy": "0",
    "leverage": "10",
    "trade_direction": "both",
}


class StrategyMathTests(SimpleTestCase):
    @override_settings(
        EXCHANGE_LEVERAGE={"binance": 5, "bybit": 3},
        EXCHANGE_MAX_LEVERAGE={"binance": 20, "bybit": 10},
        DEFAULT_FUTURES_LEVERAGE=2,
    )
    def test_leverage_defaults_and_caps_are_exchange_specific(self):
        self.assertEqual(strategy.resolve_leverage("binance", "futures"), Decimal(5))
        self.assertEqual(strategy.resolve_leverage("bybit", "futures"), Decimal(3))
        # Unbekannte Börse fällt auf den globalen Standard zurück.
        self.assertEqual(strategy.resolve_leverage("kraken", "futures"), Decimal(2))
        # Konfigurationswert schlägt die Voreinstellung, wird aber gedeckelt.
        self.assertEqual(strategy.resolve_leverage("bybit", "futures", 7), Decimal(7))
        self.assertEqual(strategy.resolve_leverage("bybit", "futures", 99), Decimal(10))
        # Spot kennt keinen Hebel – unabhängig von der Konfiguration.
        self.assertEqual(strategy.resolve_leverage("binance", "spot", 20), Decimal(1))

    @override_settings(EXCHANGE_MAX_LEVERAGE={"binance": 20})
    def test_invalid_leverage_values_fall_back_safely(self):
        self.assertEqual(strategy.resolve_leverage("binance", "futures", "abc"), Decimal(1))
        self.assertEqual(strategy.resolve_leverage("binance", "futures", 0), Decimal(1))
        self.assertEqual(strategy.resolve_leverage("binance", "futures", None), Decimal(1))

    def test_short_signal_mirrors_momentum_but_not_acceleration(self):
        thresholds = (Decimal("0.1"), Decimal("0.2"), Decimal("0.2"))
        # Aufwärtsimpuls: Long-Signal, kein Short-Signal.
        self.assertEqual(
            strategy.entry_signal(
                Decimal("0.5"), Decimal("0.4"), Decimal("0.5"), thresholds, "both"
            ),
            strategy.LONG,
        )
        # Gespiegelter Abwärtsimpuls bei identischer, positiver Beschleunigung.
        self.assertEqual(
            strategy.entry_signal(
                Decimal("0.5"), Decimal("-0.4"), Decimal("-0.5"), thresholds, "both"
            ),
            strategy.SHORT,
        )
        # Nur-Long-Konfiguration darf im Abwärtsimpuls nicht auslösen.
        self.assertIsNone(
            strategy.entry_signal(
                Decimal("0.5"), Decimal("-0.4"), Decimal("-0.5"), thresholds, strategy.LONG
            )
        )
        # Eine negative Beschleunigung (abflauender Impuls) blockiert beide Seiten.
        self.assertIsNone(
            strategy.entry_signal(
                Decimal("-0.5"), Decimal("-0.4"), Decimal("-0.5"), thresholds, "both"
            )
        )

    def test_price_change_and_pnl_are_direction_aware(self):
        self.assertEqual(
            strategy.price_change_percent(Decimal(100), Decimal(110), strategy.LONG),
            Decimal(10),
        )
        self.assertEqual(
            strategy.price_change_percent(Decimal(100), Decimal(90), strategy.SHORT),
            Decimal(10),
        )
        self.assertEqual(
            strategy.gross_pnl(Decimal(100), Decimal(90), Decimal(2), strategy.SHORT),
            Decimal(20),
        )
        self.assertEqual(
            strategy.gross_pnl(Decimal(100), Decimal(90), Decimal(2), strategy.LONG),
            Decimal(-20),
        )

    @override_settings(FUTURES_MAINTENANCE_MARGIN_RATE=0.005)
    def test_liquidation_thresholds_scale_with_leverage(self):
        self.assertAlmostEqual(
            float(strategy.liquidation_move_percent(10)),
            9.95,
            places=6,
        )
        # Ohne Hebel ist die Schwelle praktisch unerreichbar; Spot bleibt
        # dadurch exakt wie bisher.
        self.assertAlmostEqual(float(strategy.liquidation_move_percent(1)), 99.5, places=6)
        self.assertTrue(strategy.is_liquidated(Decimal(-10), Decimal(10)))
        self.assertFalse(strategy.is_liquidated(Decimal(-9), Decimal(10)))
        self.assertAlmostEqual(
            float(strategy.liquidation_price(Decimal(100), Decimal(10), strategy.SHORT)),
            109.95,
            places=6,
        )

    def test_position_size_uses_margin_times_leverage(self):
        self.assertEqual(
            strategy.position_size(Decimal(100), Decimal(50), Decimal(1)),
            Decimal(2).quantize(Decimal("0.00000001")),
        )
        self.assertEqual(
            strategy.position_size(Decimal(100), Decimal(50), Decimal(10)),
            Decimal(20).quantize(Decimal("0.00000001")),
        )


@override_settings(AUTOSTART_BOTS=False, PASSPHRASE_GATE_ENABLED=False)
class LeverageConfigurationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("leverage-user", password="leverage-password")

    def _form(self, **overrides):
        """Formular ohne Live-Symbolprüfung (die Börse wird hier nicht getestet)."""
        data = dict(BASE_CONFIG_POST)
        data.update({key: value for key, value in overrides.items() if value is not None})
        for key, value in overrides.items():
            if value is None:
                data.pop(key, None)
        with patch("trading.forms.validate_exchange_symbols", return_value=None):
            form = ConfigurationForm(data)
            form.instance.user = self.user
            form.is_valid()
        return form

    @override_settings(EXCHANGE_MAX_LEVERAGE={"binance": 20, "bybit": 5})
    def test_leverage_limit_is_enforced_per_exchange(self):
        valid = self._form(leverage="20")
        self.assertTrue(valid.is_valid(), valid.errors)
        too_high = self._form(exchange="bybit", leverage="20")
        self.assertFalse(too_high.is_valid())
        self.assertIn("leverage", too_high.errors)
        self.assertIn("5x", too_high.errors["leverage"][0])

    def test_spot_rejects_leverage_and_short(self):
        form = self._form(market="spot", leverage="5", trade_direction="short")
        self.assertFalse(form.is_valid())
        self.assertIn("leverage", form.errors)
        self.assertIn("trade_direction", form.errors)

    @override_settings(EXCHANGE_MAX_LEVERAGE={"binance": 125})
    def test_stop_loss_beyond_liquidation_is_rejected(self):
        form = self._form(leverage="25", stop_loss="5")
        self.assertFalse(form.is_valid())
        self.assertIn("stop_loss", form.errors)
        # Knapp innerhalb der Liquidationsschwelle ist zulässig.
        self.assertTrue(self._form(leverage="25", stop_loss="3").is_valid())

    def test_existing_payloads_without_new_fields_stay_valid(self):
        form = self._form(market="spot", leverage=None, trade_direction=None)
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save(commit=False)
        self.assertEqual(config.leverage, 1)
        self.assertEqual(config.trade_direction, strategy.LONG)

    @override_settings(EXCHANGE_LEVERAGE={"bybit": 4}, EXCHANGE_MAX_LEVERAGE={"bybit": 10})
    def test_configuration_exposes_effective_leverage_and_direction(self):
        config = Configuration.objects.create(
            user=self.user,
            name="Bybit",
            exchange="bybit",
            market="futures",
            symbols="BTC/USDT",
            leverage=8,
            trade_direction="short",
        )
        self.assertEqual(config.effective_leverage, Decimal(8))
        self.assertEqual(config.effective_direction, "short")
        config.market = "spot"
        self.assertEqual(config.effective_leverage, Decimal(1))
        self.assertEqual(config.effective_direction, strategy.LONG)

    @override_settings(EXCHANGE_MAX_LEVERAGE={"binance": 20})
    def test_backtest_form_normalizes_direction_and_leverage(self):
        payload = {
            "acc_from": "0",
            "acc_to": "0",
            "acc_steps": "1",
            "nda_from": "0",
            "nda_to": "0",
            "nda_steps": "1",
            "deltadelta_from": "0",
            "deltadelta_to": "0",
            "deltadelta_steps": "1",
            "trade_amount": "50",
            "take_profit": "1",
            "stop_loss": "1",
            "fee": "0.1",
            "max_price_points": "500",
            "direction": "short",
            "leverage": "10",
        }
        form = BacktestForm(
            payload,
            start_capital=Decimal(1000),
            symbol_count=1,
            exchange="binance",
            market="futures",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["direction"], "short")
        self.assertEqual(form.cleaned_data["leverage"], 10)

        spot_form = BacktestForm(
            payload,
            start_capital=Decimal(1000),
            symbol_count=1,
            exchange="binance",
            market="spot",
        )
        self.assertFalse(spot_form.is_valid())
        self.assertIn("direction", spot_form.errors)


@override_settings(
    AUTOSTART_BOTS=False,
    PASSPHRASE_GATE_ENABLED=False,
    EXCHANGE_MAX_LEVERAGE={"binance": 125},
    FUTURES_MAINTENANCE_MARGIN_RATE=0.005,
)
class ShortTradingTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("short-user", password="short-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Short Bot",
            exchange="binance",
            market="futures",
            symbols="BTC/USDT",
            start_capital=Decimal(1000),
            trade_amount=Decimal(100),
            fee=Decimal("0.1"),
            countdown=0,
            leverage=10,
            trade_direction="short",
        )

    def test_short_position_profits_from_falling_prices(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy", direction="short")
        opening = TradingLog.objects.get(action="buy")
        # Margin 100 USDT × Hebel 10 = 1.000 USDT Nominal = 10 Kontrakte à 100.
        self.assertEqual(opening.direction, "short")
        self.assertEqual(opening.leverage, Decimal(10))
        self.assertEqual(opening.margin, Decimal(100))
        self.assertEqual(opening.amount, Decimal(10))
        self.assertEqual(opening.fee_amount, Decimal(1))
        # Gebunden ist nur Margin plus Gebühr, nicht das Nominalvolumen.
        self.assertEqual(bot._available_capital(), Decimal(1000) - Decimal(101))

        bot.price_buffer["BTC/USDT"] = [Decimal(95)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "sell")
        closing = TradingLog.objects.get(action="sell")
        expected = Decimal(10) * Decimal(5) - Decimal(1) - Decimal("0.95")
        self.assertEqual(closing.pl_nominal, expected.quantize(Decimal("0.00000001")))
        self.assertGreater(closing.pl_nominal, 0)
        self.assertEqual(closing.direction, "short")

    def test_short_position_loses_on_rising_prices(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy", direction="short")
        bot.price_buffer["BTC/USDT"] = [Decimal(105)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "sell")
        closing = TradingLog.objects.get(action="sell")
        self.assertLess(closing.pl_nominal, 0)

    def test_take_profit_and_stop_loss_are_mirrored_for_shorts(self):
        self.config.take_profit = Decimal(2)
        self.config.stop_loss = Decimal(2)
        self.config.save()
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy", direction="short")
        # Fallender Kurs schließt den Short mit Take-Profit.
        bot.price_buffer["BTC/USDT"] = [Decimal(97)]
        async_to_sync(bot.check_trading)(
            "BTC/USDT", Decimal(97), Decimal(0), Decimal(0), Decimal(0)
        )
        self.assertEqual(bot.positions, {})
        self.assertEqual(TradingLog.objects.filter(action="sell").count(), 1)

    def test_liquidation_closes_position_before_margin_is_lost(self):
        # Stop-Loss bewusst jenseits der Liquidationsschwelle (10x → 9,95 %).
        self.config.stop_loss = Decimal(50)
        self.config.take_profit = Decimal(50)
        self.config.save()
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy", direction="short")
        bot.price_buffer["BTC/USDT"] = [Decimal(111)]
        async_to_sync(bot.check_trading)(
            "BTC/USDT", Decimal(111), Decimal(0), Decimal(0), Decimal(0)
        )
        self.assertEqual(bot.positions, {})
        closing = TradingLog.objects.get(action="sell")
        self.assertLess(closing.pl_nominal, 0)

    def test_long_only_configuration_never_opens_a_short(self):
        self.config.trade_direction = strategy.LONG
        self.config.save()
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.check_trading)(
            "BTC/USDT", Decimal(100), Decimal(-1), Decimal(-1), Decimal(1)
        )
        self.assertEqual(bot.positions, {})

    def test_spot_configuration_refuses_short_orders(self):
        self.config.market = "spot"
        self.config.leverage = 1
        self.config.trade_direction = strategy.LONG
        self.config.save()
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        with self.assertRaises(ValueError):
            async_to_sync(bot.execute_trade)("BTC/USDT", "buy", direction="short")

    def test_restored_state_keeps_direction_and_leverage(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("BTC/USDT", "buy", direction="short")
        restored = TradingBot(self.config)
        position = restored.positions["BTC/USDT"]
        self.assertEqual(position["direction"], "short")
        self.assertEqual(position["leverage"], Decimal(10))
        self.assertEqual(position["margin"], Decimal(100))

    def test_spot_behaviour_is_unchanged_without_leverage(self):
        config = Configuration.objects.create(
            user=self.user,
            name="Spot Bot",
            exchange="binance",
            market="spot",
            symbols="ETH/USDT",
            start_capital=Decimal(100),
            trade_amount=Decimal(10),
            fee=Decimal("0.1"),
            countdown=0,
        )
        bot = TradingBot(config)
        bot.price_buffer["ETH/USDT"] = [Decimal(100)]
        async_to_sync(bot.execute_trade)("ETH/USDT", "buy")
        buy = TradingLog.objects.get(configuration=config, action="buy")
        self.assertEqual(buy.direction, strategy.LONG)
        self.assertEqual(buy.leverage, Decimal(1))
        self.assertEqual(buy.amount, Decimal("0.1"))
        self.assertEqual(
            buy.current_capital,
            config.start_capital - (buy.amount * buy.price + buy.fee_amount),
        )


class BacktestingEngineDirectionTests(SimpleTestCase):
    falling = [Decimal(100) - Decimal(i) for i in range(30)]
    rising = [Decimal(100) + Decimal(i) for i in range(30)]

    def _params(self, **overrides):
        params = {
            "start_capital": Decimal(1000),
            "trade_amount": Decimal(100),
            "take_profit": Decimal(2),
            "stop_loss": Decimal(2),
            "fee_percentage": Decimal("0.1"),
        }
        params.update(overrides)
        return params

    def test_short_simulation_earns_on_a_falling_series(self):
        capital, report = Backtesting.simulate_trading_detailed(
            self.falling,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(direction="short"),
        )
        self.assertGreater(capital, Decimal(1000))
        self.assertTrue(report["trades"])
        self.assertTrue(all(trade["direction"] == "short" for trade in report["trades"]))

    def test_long_only_simulation_does_not_trade_a_falling_series(self):
        _capital, report = Backtesting.simulate_trading_detailed(
            self.falling,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(direction="long"),
        )
        self.assertEqual(report["num_buys"], 0)

    def test_both_directions_trade_up_and_down_moves(self):
        series = self.rising + self.falling
        _capital, report = Backtesting.simulate_trading_detailed(
            series,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(direction="both"),
        )
        directions = {trade["direction"] for trade in report["trades"]}
        self.assertEqual(directions, {"long", "short"})

    def test_leverage_scales_result_and_binds_only_margin(self):
        _capital_1x, report_1x = Backtesting.simulate_trading_detailed(
            self.rising,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(leverage=Decimal(1)),
        )
        _capital_5x, report_5x = Backtesting.simulate_trading_detailed(
            self.rising,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(leverage=Decimal(5)),
        )
        self.assertGreater(report_5x["net_profit"], report_1x["net_profit"])
        first_buy = next(trade for trade in report_5x["trades"] if trade["type"] == "buy")
        # Gebunden wird die Margin (100) plus Gebühr auf 500 Nominal (0,5).
        self.assertEqual(first_buy["margin"], Decimal(100))
        self.assertEqual(first_buy["capital_before"] - first_buy["capital_after"], Decimal("100.5"))

    @override_settings(FUTURES_MAINTENANCE_MARGIN_RATE=0.005)
    def test_liquidation_closes_before_a_wide_stop_loss(self):
        crash = [Decimal(100), Decimal("100.5"), Decimal(101)] + [Decimal(50)] * 5
        _capital, report = Backtesting.simulate_trading_detailed(
            crash,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(leverage=Decimal(10), stop_loss=Decimal(90), take_profit=Decimal(90)),
        )
        sells = [trade for trade in report["trades"] if trade["type"] == "sell"]
        self.assertTrue(sells)
        # Der Verlust bleibt auf die eingesetzte Margin begrenzt.
        self.assertLess(report["net_profit"], 0)

    def test_default_parameters_stay_long_and_unleveraged(self):
        _capital, report = Backtesting.simulate_trading_detailed(
            self.rising,
            Decimal(-100),
            Decimal(0),
            Decimal(0),
            self._params(),
        )
        self.assertTrue(all(trade["direction"] == "long" for trade in report["trades"]))
        self.assertTrue(all(trade["leverage"] == Decimal(1) for trade in report["trades"]))


class FuturesMarketDataTests(SimpleTestCase):
    def test_futures_stream_has_a_second_endpoint_for_reconnects(self):
        from trading.market_data import BinancePublicMarketData

        futures = BinancePublicMarketData("futures")
        self.assertEqual(len(futures.websocket_base_urls), 2)
        self.assertTrue(all("fstream.binance.com" in url for url in futures.websocket_base_urls))
        spot = BinancePublicMarketData("spot")
        self.assertNotIn(spot.websocket_base_urls[0], futures.websocket_base_urls)

    def test_futures_exchange_options_request_isolated_linear_swaps(self):
        # Der Aufbau des ccxt-Clients erfolgt ohne Netzwerkzugriff.
        # Bybit nutzt seit 2.5.2 einen eigenen Public-Adapter für Marktdaten,
        # behält aber die isolierte, lineare Futures-Konfiguration für
        # das Setzen des Hebels über CCXT bei API-Schlüsseln.
        from trading.market_data import BybitPublicMarketData
        from trading.trading_bot import TradingBot

        config = SimpleNamespace(
            id=0,
            exchange="bybit",
            market="futures",
            symbols="BTC/USDT",
            api_key=None,
            secret_key=None,
            leverage=7,
            trade_direction="both",
        )
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.leverage = strategy.resolve_leverage("bybit", "futures", 7)
        exchange = bot._setup_exchange()
        # Bybit nutzt jetzt den Public-Adapter für Marktdaten
        if isinstance(exchange, BybitPublicMarketData):
            self.assertEqual(exchange.market, "futures")
            self.assertEqual(exchange.category, "linear")
        else:
            self.assertEqual(exchange.options["defaultType"], "swap")
            self.assertEqual(exchange.options["defaultSubType"], "linear")
            self.assertEqual(exchange.options["marginMode"], "isolated")
            self.assertEqual(exchange.options["leverage"], 7)

        # Für eine CCXT-basierte Börse (BingX) bleibt die alte CCXT-Prüfung gültig
        config_bingx = SimpleNamespace(
            id=0,
            exchange="bingx",
            market="futures",
            symbols="BTC/USDT",
            api_key=None,
            secret_key=None,
            leverage=7,
            trade_direction="both",
        )
        bot.config = config_bingx
        bot.leverage = strategy.resolve_leverage("bingx", "futures", 7)
        exchange_bingx = bot._setup_exchange()
        self.assertEqual(exchange_bingx.options["defaultType"], "swap")
        self.assertEqual(exchange_bingx.options["defaultSubType"], "linear")
        self.assertEqual(exchange_bingx.options["marginMode"], "isolated")
        self.assertEqual(exchange_bingx.options["leverage"], 7)


@override_settings(AUTOSTART_BOTS=False, PASSPHRASE_GATE_ENABLED=False)
class BacktestingDocumentationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("docs-user", password="docs-password")
        self.client.login(username="docs-user", password="docs-password")
        Configuration.objects.create(
            user=self.user,
            name="Doku",
            symbols="BTC/USDT",
        )
        _render_document.cache_clear()

    def test_backtesting_document_is_registered_and_rendered_like_the_manual(self):
        self.assertIn("backtesting", DOCUMENTS)
        html = _render_document("backtesting")
        self.assertIn("<h1", html)
        self.assertIn("Backtesting", html)

    def test_documentation_page_renders_backtesting_markdown(self):
        response = self.client.get(reverse("documentation", args=["backtesting"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Backtesting-Dokumentation")
        self.assertContains(response, "manual-content")

    def test_documentation_fragment_returns_rendered_html(self):
        response = self.client.get(reverse("documentation_fragment", args=["backtesting"]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["slug"], "backtesting")
        self.assertIn("<h1", payload["html"])

    def test_unknown_documents_are_rejected(self):
        self.assertEqual(
            self.client.get(reverse("documentation", args=["geheim"])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("documentation_fragment", args=["geheim"])).status_code,
            404,
        )

    def test_documentation_fragment_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("documentation_fragment", args=["backtesting"]))
        self.assertEqual(response.status_code, 302)

    def test_backtesting_pages_expose_the_documentation_button(self):
        for url in (
            reverse("backtesting_index"),
            reverse("backtesting_form", args=[Configuration.objects.first().id]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="documentation-toggle"')
                self.assertContains(
                    response, reverse("documentation_fragment", args=["backtesting"])
                )

    def test_manual_and_backtesting_documents_are_cached_separately(self):
        _render_document.cache_clear()
        first = _render_document("backtesting")
        manual = _render_document("manual")
        self.assertNotEqual(first, manual)
        self.assertEqual(_render_document("backtesting"), first)
        self.assertEqual(_render_document.cache_info().misses, 2)
