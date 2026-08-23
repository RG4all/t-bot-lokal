"""Tests für Bybit-/Bitunix-Fixes (Version 2.5.2) und Hilfe-Seiten (HTTP 200).

Diese Datei rekonstruiert die in der Peer-Review-Session gemeldeten Fehlerbilder
ohne echte HTTPS-Calls (Sandbox blockiert TLS) und verifiziert die Fixes:

- Bybit: eigener Public-Katalog (instruments-info) und Ticker-Adapter, statt nur CCXT,
  mit Pagination, Trading-Filter, Fehlercode-Behandlung und Fallback für Autocomplete.
- Bitunix: robustere Behandlung von leeren Listen, Fehlercodes, fehlenden Preisen,
  sowie Validierung vor Ticker-Abruf.
- Hilfe: /help/?doc=backtesting rendert die Backtesting-Doku, alle Seiten liefern 200.
- Trading-Bot: Bybit nutzt BybitPublicMarketData für Marktdaten.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from trading import strategy
from trading.market_data import (
    BinancePublicSymbolCatalog,
    BitMartPublicMarketData,
    BitunixPublicMarketData,
    BybitPublicMarketData,
    BybitPublicSymbolCatalog,
    MarketDataConnectionError,
    MarketDataError,
    SymbolValidationError,
    validate_exchange_symbols,
)
from trading.models import Configuration
from trading.symbols import get_available_symbols
from trading.trading_bot import TradingBot
from trading.views import _render_document


class BybitSymbolCatalogTests(SimpleTestCase):
    def _catalog(self):
        return BybitPublicSymbolCatalog("spot")

    def test_available_symbols_parses_trading_and_ignores_non_trading(self):
        catalog = self._catalog()
        catalog._json = lambda url, **kwargs: {
            "retCode": 0,
            "result": {
                "list": [
                    {"symbol": "BTCUSDT", "status": "Trading"},
                    {"symbol": "ETHUSDT", "status": "Trading"},
                    {"symbol": "XRPUSDT", "status": "Closed"},
                    {"symbol": "DOGEUSDT", "status": "Trading"},
                ],
                "nextPageCursor": "",
            },
        }
        symbols = catalog.available_symbols()
        self.assertIn("BTC/USDT", symbols)
        self.assertIn("ETH/USDT", symbols)
        self.assertNotIn("XRP/USDT", symbols)
        catalog.close()

    def test_available_symbols_handles_pagination(self):
        catalog = BybitPublicSymbolCatalog("futures")
        calls = []

        def fake_json(url, **kwargs):
            cursor = kwargs.get("params", {}).get("cursor", "")
            calls.append(cursor)
            if not cursor:
                return {
                    "retCode": 0,
                    "result": {
                        "list": [{"symbol": "BTCUSDT", "status": "Trading"}],
                        "nextPageCursor": "cursor123",
                    },
                }
            return {
                "retCode": 0,
                "result": {
                    "list": [{"symbol": "ETHUSDT", "status": "Trading"}],
                    "nextPageCursor": "",
                },
            }

        catalog._json = fake_json
        symbols = catalog.available_symbols()
        self.assertEqual(len(calls), 2)
        self.assertIn("BTC/USDT", symbols)
        self.assertIn("ETH/USDT", symbols)
        catalog.close()

    def test_available_symbols_raises_on_error_code(self):
        catalog = self._catalog()
        catalog._json = lambda url, **kwargs: {"retCode": 10001, "retMsg": "Invalid"}
        with self.assertRaises(MarketDataConnectionError) as ctx:
            catalog.available_symbols()
        self.assertIn("Bybit-Instruments", str(ctx.exception))
        catalog.close()

    def test_available_symbols_raises_on_empty(self):
        catalog = self._catalog()
        catalog._json = lambda url, **kwargs: {"retCode": 0, "result": {"list": [], "nextPageCursor": ""}}
        with self.assertRaises(MarketDataConnectionError):
            catalog.available_symbols()
        catalog.close()

    def test_validate_symbols_valid_and_invalid(self):
        catalog = self._catalog()
        catalog._json = lambda url, **kwargs: {
            "retCode": 0,
            "result": {"list": [{"symbol": "BTCUSDT", "status": "Trading"}], "nextPageCursor": ""},
        }
        catalog.validate_symbols(["BTC/USDT"])
        with self.assertRaises(SymbolValidationError) as ctx:
            catalog.validate_symbols(["FAKE/USDT"])
        self.assertIn("FAKE/USDT", str(ctx.exception))
        catalog.close()

    def test_invalid_market_raises(self):
        with self.assertRaises(ValueError):
            BybitPublicSymbolCatalog("invalid")

    def test_compact_to_canonical_handles_usdt(self):
        catalog = BybitPublicSymbolCatalog("spot")
        catalog._json = lambda url, **kwargs: {
            "retCode": 0,
            "result": {"list": [{"symbol": "1000PEPEUSDT", "status": "Trading"}], "nextPageCursor": ""},
        }
        symbols = catalog.available_symbols()
        self.assertIn("1000PEPE/USDT", symbols)
        catalog.close()


class BybitMarketDataTests(SimpleTestCase):
    def test_fetch_tickers_success(self):
        provider = BybitPublicMarketData("spot")

        def fake_json(url, **kwargs):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {"symbol": "BTCUSDT", "lastPrice": "50000"},
                        {"symbol": "ETHUSDT", "lastPrice": "3000"},
                    ]
                },
            }

        provider._json = fake_json
        result = provider.fetch_tickers(["BTC/USDT", "ETH/USDT"])
        self.assertEqual(result["BTC/USDT"]["last"], "50000")
        self.assertEqual(result["ETH/USDT"]["last"], "3000")
        provider.close()

    def test_fetch_tickers_raises_on_missing_price(self):
        provider = BybitPublicMarketData("spot")
        calls = {"count": 0}

        def fake_json(url, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                # First call: available_symbols via tickers -> contains BTC
                return {
                    "retCode": 0,
                    "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "50000"}]},
                }
            # Second call: actual fetch -> empty
            return {"retCode": 0, "result": {"list": []}}

        provider._json = fake_json
        with self.assertRaises(MarketDataConnectionError) as ctx:
            provider.fetch_tickers(["BTC/USDT"])
        self.assertIn("keine Preise", str(ctx.exception))
        provider.close()

    def test_fetch_tickers_raises_on_error_code(self):
        provider = BybitPublicMarketData("spot")

        def fake_json(url, **kwargs):
            # First call for validation: success
            if not hasattr(fake_json, "called"):
                fake_json.called = True
                return {
                    "retCode": 0,
                    "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "1"}]},
                }
            return {"retCode": 10001, "retMsg": "error"}

        provider._json = fake_json
        with self.assertRaises(MarketDataConnectionError):
            provider.fetch_tickers(["BTC/USDT"])
        provider.close()

    def test_available_symbols_from_tickers(self):
        provider = BybitPublicMarketData("futures")
        provider._json = lambda url, **kwargs: {
            "retCode": 0,
            "result": {"list": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]},
        }
        symbols = provider.available_symbols()
        self.assertIn("BTC/USDT", symbols)
        provider.close()

    def test_invalid_market_raises(self):
        with self.assertRaises(ValueError):
            BybitPublicMarketData("invalid")


class BitunixFixesTests(SimpleTestCase):
    def test_futures_available_symbols_success(self):
        provider = BitunixPublicMarketData("futures")
        provider._json = lambda url, **kwargs: {
            "code": 0,
            "data": [{"symbol": "BTCUSDT", "symbolStatus": "OPEN"}],
        }
        symbols = provider.available_symbols()
        self.assertIn("BTCUSDT", symbols)
        provider.close()

    def test_spot_available_symbols_handles_dict_wrapping(self):
        provider = BitunixPublicMarketData("spot")
        provider._json = lambda url, **kwargs: {
            "code": 0,
            "data": {"list": [{"symbol": "BTCUSDT", "isOpen": "1"}]},
        }
        symbols = provider.available_symbols()
        self.assertIn("BTCUSDT", symbols)
        provider.close()

    def test_available_symbols_raises_on_error_code(self):
        provider = BitunixPublicMarketData("futures")
        provider._json = lambda url, **kwargs: {"code": 10001, "msg": "fail"}
        with self.assertRaises(MarketDataConnectionError):
            provider.available_symbols()
        provider.close()

    def test_available_symbols_raises_on_empty(self):
        provider = BitunixPublicMarketData("futures")
        provider._json = lambda url, **kwargs: {"code": 0, "data": []}
        with self.assertRaises(MarketDataConnectionError):
            provider.available_symbols()
        provider.close()

    def test_validate_symbols_rejects_invalid(self):
        provider = BitunixPublicMarketData("futures")
        provider._json = lambda url, **kwargs: {
            "code": 0,
            "data": [{"symbol": "BTCUSDT", "symbolStatus": "OPEN"}],
        }
        with self.assertRaises(SymbolValidationError):
            provider.validate_symbols(["FAKE/USDT"])
        provider.close()

    def test_fetch_tickers_futures_success(self):
        provider = BitunixPublicMarketData("futures")

        def fake_json(url, **kwargs):
            if "trading_pairs" in url:
                return {"code": 0, "data": [{"symbol": "BTCUSDT", "symbolStatus": "OPEN"}]}
            return {"code": 0, "data": [{"symbol": "BTCUSDT", "lastPrice": "60000"}]}

        provider._json = fake_json
        result = provider.fetch_tickers(["BTC/USDT"])
        self.assertEqual(result["BTC/USDT"]["last"], "60000")
        provider.close()

    def test_fetch_tickers_spot_success(self):
        provider = BitunixPublicMarketData("spot")

        def fake_json(url, **kwargs):
            if "coin_pair" in url:
                return {"code": 0, "data": [{"symbol": "BTCUSDT", "isOpen": "1"}]}
            return {"code": 0, "data": {"lastPrice": "50000"}}

        provider._json = fake_json
        result = provider.fetch_tickers(["BTC/USDT"])
        self.assertEqual(result["BTC/USDT"]["last"], "50000")
        provider.close()

    def test_fetch_tickers_spot_raises_on_error(self):
        provider = BitunixPublicMarketData("spot")

        def fake_json(url, **kwargs):
            if "coin_pair" in url:
                return {"code": 0, "data": [{"symbol": "BTCUSDT", "isOpen": "1"}]}
            return {"code": 1, "msg": "fail"}

        provider._json = fake_json
        with self.assertRaises(MarketDataError):
            provider.fetch_tickers(["BTC/USDT"])
        provider.close()

    def test_fetch_tickers_spot_raises_on_missing_price(self):
        provider = BitunixPublicMarketData("spot")

        def fake_json(url, **kwargs):
            if "coin_pair" in url:
                return {"code": 0, "data": [{"symbol": "BTCUSDT", "isOpen": "1"}]}
            return {"code": 0, "data": {}}

        provider._json = fake_json
        with self.assertRaises(MarketDataConnectionError):
            provider.fetch_tickers(["BTC/USDT"])
        provider.close()

    def test_fetch_tickers_raises_on_missing(self):
        provider = BitunixPublicMarketData("futures")

        def fake_json(url, **kwargs):
            if "trading_pairs" in url:
                return {"code": 0, "data": [{"symbol": "BTCUSDT", "symbolStatus": "OPEN"}]}
            return {"code": 0, "data": []}

        provider._json = fake_json
        with self.assertRaises(MarketDataConnectionError):
            provider.fetch_tickers(["BTC/USDT"])
        provider.close()

    def test_invalid_market_raises(self):
        with self.assertRaises(ValueError):
            BitunixPublicMarketData("invalid")


class BitMartFixesTests(SimpleTestCase):
    def test_available_symbols_success(self):
        provider = BitMartPublicMarketData("spot")
        provider._json = lambda url, **kwargs: {"code": 1000, "data": {"symbols": ["BTC_USDT"]}}
        self.assertIn("BTC_USDT", provider.available_symbols())
        provider.close()

    def test_available_symbols_raises_on_error(self):
        provider = BitMartPublicMarketData("spot")
        provider._json = lambda url, **kwargs: {"code": 500, "message": "fail"}
        with self.assertRaises(MarketDataConnectionError):
            provider.available_symbols()
        provider.close()

    def test_fetch_tickers_success(self):
        provider = BitMartPublicMarketData("spot")

        def fake_json(url, **kwargs):
            if "symbols" in url:
                return {"code": 1000, "data": {"symbols": ["BTC_USDT"]}}
            return {"code": 1000, "data": {"last": "50000"}}

        provider._json = fake_json
        result = provider.fetch_tickers(["BTC/USDT"])
        self.assertEqual(result["BTC/USDT"]["last"], "50000")
        provider.close()


class BinanceFixesTests(SimpleTestCase):
    def test_available_symbols_filters_trading(self):
        provider = BinancePublicSymbolCatalog("spot")
        provider._json = lambda url: {
            "symbols": [
                {"status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT"},
                {"status": "BREAK", "baseAsset": "ETH", "quoteAsset": "USDT"},
            ]
        }
        self.assertEqual(provider.available_symbols(), {"BTC/USDT"})
        provider.close()

    def test_available_symbols_raises_on_invalid_payload(self):
        provider = BinancePublicSymbolCatalog("spot")
        provider._json = lambda url: {"symbols": None}
        with self.assertRaises(MarketDataConnectionError):
            provider.available_symbols()
        provider.close()


class ValidateExchangeSymbolsTests(SimpleTestCase):
    def test_binance_uses_catalog(self):
        with patch("trading.market_data.BinancePublicSymbolCatalog") as Mock:
            instance = Mock.return_value
            instance.validate_symbols.return_value = []
            validate_exchange_symbols("binance", "spot", ["BTC/USDT"])
            instance.validate_symbols.assert_called_once()

    def test_bybit_uses_catalog(self):
        with patch("trading.market_data.BybitPublicSymbolCatalog") as Mock:
            instance = Mock.return_value
            instance.validate_symbols.return_value = []
            validate_exchange_symbols("bybit", "spot", ["BTC/USDT"])
            instance.validate_symbols.assert_called_once()

    def test_bitunix_uses_provider(self):
        with patch("trading.market_data.BitunixPublicMarketData") as Mock:
            instance = Mock.return_value
            instance.validate_symbols.return_value = []
            validate_exchange_symbols("bitunix", "futures", ["BTC/USDT"])
            instance.validate_symbols.assert_called_once()

    def test_unknown_exchange_raises(self):
        with self.assertRaises(MarketDataError):
            validate_exchange_symbols("unknown", "spot", ["BTC/USDT"])


class SymbolAutocompleteBybitTests(SimpleTestCase):
    def test_bybit_fallback_on_connection_error(self):
        import trading.symbols as catalog
        from trading.market_data import MarketDataConnectionError as MDE

        catalog._CACHE.clear()
        catalog._FAILURE_CACHE.clear()
        with patch(
            "trading.symbols.BybitPublicSymbolCatalog.available_symbols",
            side_effect=MDE("offline"),
        ):
            symbols = catalog._load_symbols("bybit", "spot")
            # Fallback should return non-empty set
            self.assertTrue(len(symbols) > 0)
            self.assertIn("BTC/USDT", symbols)

    def test_get_available_symbols_caches(self):
        import trading.symbols as catalog
        catalog._CACHE.clear()
        catalog._FAILURE_CACHE.clear()
        with patch("trading.symbols._load_symbols", return_value={"BTC/USDT"}) as mock_load:
            first = get_available_symbols("binance", "spot")
            second = get_available_symbols("binance", "spot")
            self.assertEqual(first, second)
            mock_load.assert_called_once()

    def test_bybit_catalog_fallback_contains_major_pairs(self):
        import trading.symbols as catalog
        from trading.market_data import MarketDataConnectionError as MDE

        catalog._CACHE.clear()
        catalog._FAILURE_CACHE.clear()
        with patch(
            "trading.symbols.BybitPublicSymbolCatalog.available_symbols",
            side_effect=MDE("offline"),
        ):
            spot = catalog._load_symbols("bybit", "spot")
            futures = catalog._load_symbols("bybit", "futures")
        self.assertIn("BTC/USDT", spot)
        self.assertIn("BTC/USDT", futures)


class TradingBotBybitTests(SimpleTestCase):
    def test_setup_exchange_bybit_uses_public_adapter(self):
        config = SimpleNamespace(
            id=1,
            exchange="bybit",
            market="spot",
            symbols="BTC/USDT",
            api_key=None,
            secret_key=None,
            leverage=1,
            trade_direction="long",
        )
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.leverage = strategy.resolve_leverage("bybit", "spot", 1)
        exchange = bot._setup_exchange()
        self.assertIsInstance(exchange, BybitPublicMarketData)

    def test_setup_exchange_binance_uses_public_adapter(self):
        config = SimpleNamespace(
            id=1,
            exchange="binance",
            market="futures",
            symbols="BTC/USDT",
            api_key=None,
            secret_key=None,
            leverage=1,
            trade_direction="long",
        )
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.leverage = strategy.resolve_leverage("binance", "futures", 1)
        exchange = bot._setup_exchange()
        from trading.market_data import BinancePublicMarketData

        self.assertIsInstance(exchange, BinancePublicMarketData)

    def test_setup_exchange_bitunix_uses_public_adapter(self):
        config = SimpleNamespace(
            id=1,
            exchange="bitunix",
            market="futures",
            symbols="BTC/USDT",
            api_key=None,
            secret_key=None,
            leverage=5,
            trade_direction="long",
        )
        bot = TradingBot.__new__(TradingBot)
        bot.config = config
        bot.leverage = strategy.resolve_leverage("bitunix", "futures", 5)
        exchange = bot._setup_exchange()
        self.assertIsInstance(exchange, BitunixPublicMarketData)


@override_settings(AUTOSTART_BOTS=False, PASSPHRASE_GATE_ENABLED=False)
class HelpPagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("help-user", password="help-pass")
        self.client.force_login(self.user)
        Configuration.objects.create(user=self.user, name="Test", symbols="BTC/USDT")
        _render_document.cache_clear()

    def test_help_page_200(self):
        resp = self.client.get(reverse("help"))
        self.assertEqual(resp.status_code, 200)

    def test_help_with_doc_backtesting_200_and_contains_backtesting(self):
        resp = self.client.get(reverse("help") + "?doc=backtesting")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Backtesting")

    def test_help_with_unknown_doc_falls_back_to_manual(self):
        resp = self.client.get(reverse("help") + "?doc=unknown")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hilfe")

    def test_docs_backtesting_200(self):
        resp = self.client.get(reverse("documentation", args=["backtesting"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Backtesting-Dokumentation")

    def test_backtesting_index_200(self):
        resp = self.client.get(reverse("backtesting_index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Backtesting")

    def test_backtesting_form_200(self):
        config = Configuration.objects.first()
        resp = self.client.get(reverse("backtesting_form", args=[config.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dokumentation")

    def test_config_page_200(self):
        resp = self.client.get(reverse("config"))
        self.assertEqual(resp.status_code, 200)

    def test_config_list_200(self):
        resp = self.client.get(reverse("config_list"))
        self.assertEqual(resp.status_code, 200)

    def test_documentation_fragment_200(self):
        resp = self.client.get(reverse("documentation_fragment", args=["backtesting"]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("html", data)

    def test_help_page_without_login_200(self):
        self.client.logout()
        resp = self.client.get(reverse("help"))
        self.assertEqual(resp.status_code, 200)

    def test_docs_page_without_login_200(self):
        self.client.logout()
        resp = self.client.get(reverse("documentation", args=["backtesting"]))
        self.assertEqual(resp.status_code, 200)


@override_settings(AUTOSTART_BOTS=False, PASSPHRASE_GATE_ENABLED=False)
class DocumentationButtonDomTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("dom-user", password="dom-pass")
        self.client.force_login(self.user)
        self.config = Configuration.objects.create(user=self.user, name="DomTest", symbols="BTC/USDT")
        _render_document.cache_clear()

    def test_backtesting_form_contains_documentation_toggle_and_panel(self):
        resp = self.client.get(reverse("backtesting_form", args=[self.config.id]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('id="documentation-toggle"', content)
        self.assertIn('id="documentation-panel"', content)
        self.assertIn('data-fragment-url', content)
        self.assertIn(reverse("documentation_fragment", args=["backtesting"]), content)

    def test_backtesting_index_contains_documentation_toggle(self):
        resp = self.client.get(reverse("backtesting_index"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="documentation-toggle"')

    def test_help_contains_manual_content(self):
        resp = self.client.get(reverse("help"))
        self.assertContains(resp, "manual-content")

    def test_documentation_fragment_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("documentation_fragment", args=["backtesting"]))
        self.assertEqual(resp.status_code, 302)


class StrategyLeverageEdgeTests(SimpleTestCase):
    @override_settings(EXCHANGE_MAX_LEVERAGE={"bybit": 10})
    def test_bybit_leverage_capped(self):
        self.assertEqual(strategy.resolve_leverage("bybit", "futures", 20), Decimal(10))

    def test_spot_always_one(self):
        self.assertEqual(strategy.resolve_leverage("bybit", "spot", 10), Decimal(1))

    @override_settings(FUTURES_MAINTENANCE_MARGIN_RATE=0.005)
    def test_liquidation_price_long_and_short(self):
        long_price = strategy.liquidation_price(Decimal(100), Decimal(10), "long")
        short_price = strategy.liquidation_price(Decimal(100), Decimal(10), "short")
        self.assertLess(long_price, Decimal(100))
        self.assertGreater(short_price, Decimal(100))
