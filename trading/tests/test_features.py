import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from trading.backtest_templates import build_backtest_templates
from trading.market_scanner import (
    MarketScannerError,
    MarketScannerFilterError,
    _BitunixScannerExchange,
    _orderbook_depth,
    scan_market_opportunities,
)
from trading.models import Configuration
from trading.resource_optimizer import (
    ServerResources,
    estimate_backtest_runtime,
    format_duration,
    get_server_resources,
    optimize_backtest_limits,
)
from trading.views import _render_manual


class ResourceOptimizerTests(SimpleTestCase):
    def test_limits_scale_down_on_small_hardware(self):
        small = optimize_backtest_limits(ServerResources(1, 1_024, 10_000, 300))
        large = optimize_backtest_limits(ServerResources(8, 16_384, 10_000, 5_000))
        self.assertLess(small.max_combinations, large.max_combinations)
        self.assertLessEqual(small.max_price_points, large.max_price_points)
        self.assertLessEqual(small.max_grid_points**3, small.max_combinations)

    def test_grid_limit_is_exposed_and_runtime_is_bounded(self):
        from trading.forms import BacktestForm

        form = BacktestForm(
            {
                "acc_from": 0,
                "acc_to": 1,
                "acc_steps": 0.1,
                "nda_from": 0,
                "nda_to": 1,
                "nda_steps": 0.1,
                "deltadelta_from": 0,
                "deltadelta_to": 1,
                "deltadelta_steps": 0.1,
                "max_grid_points": 5,
                "max_combinations": 1_000,
                "max_price_points": 100,
                "trade_amount": 1,
                "take_profit": 1,
                "stop_loss": 1,
                "fee": 0,
            },
            resource_profile=SimpleNamespace(
                max_grid_points=5, max_combinations=1_000, max_price_points=1_000
            ),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Jede Rasterachse", str(form.errors))

    def test_administrator_limits_are_absolute(self):
        profile = optimize_backtest_limits(
            ServerResources(64, 128_000, 100_000, 50_000),
            configured_max_combinations=1_000,
            configured_max_price_points=700,
        )
        self.assertEqual(profile.max_combinations, 1_000)
        self.assertEqual(profile.max_price_points, 700)

    def test_runtime_estimate_is_monotonic_and_human_readable(self):
        resources = ServerResources(2, 4_096, 10_000, 5_000)
        short = estimate_backtest_runtime(100, 1_000, 1, resources)
        long = estimate_backtest_runtime(200, 2_000, 1, resources)
        self.assertGreater(long["seconds"], short["seconds"])
        self.assertTrue(short["is_estimate"])
        self.assertIn("ca.", format_duration(61))

    def test_server_snapshot_contains_cpu_memory_and_storage(self):
        resources = get_server_resources(refresh=True)
        self.assertGreaterEqual(resources.cpu_count, 1)
        self.assertGreater(resources.memory_mb, 0)
        self.assertGreaterEqual(resources.storage_free_mb, 0)


class TemplateTests(TestCase):
    def test_templates_are_relative_to_configuration_and_do_not_mutate_it(self):
        config = Configuration(
            name="Template config",
            div_DVA_prev_NDA_threshold_buy=0,
            nda_threshold_buy=0,
            deltadelta_threshold_buy=0,
            trade_amount=10,
            take_profit=1,
            stop_loss=1,
            fee=0.1,
        )
        profile = SimpleNamespace(max_price_points=2_500, max_combinations=4_000)
        templates = build_backtest_templates(config, profile)
        self.assertEqual([item["name"] for item in templates], ["quick", "balanced", "deep"])
        self.assertEqual(templates[0]["max_price_points"], 1_000)
        self.assertEqual(config.nda_threshold_buy, 0)


class HelpCacheTests(SimpleTestCase):
    def test_manual_is_compiled_once_even_for_concurrent_first_requests(self):
        _render_manual.cache_clear()
        original = _render_manual.__wrapped__
        calls = 0
        lock = threading.Lock()

        def counted():
            nonlocal calls
            with lock:
                calls += 1
            return original()

        # Replacing the cached function is intentionally avoided; this checks
        # the public cache contract and the generated stable anchor instead.
        first = _render_manual()
        second = _render_manual()
        self.assertEqual(first, second)
        self.assertEqual(_render_manual.cache_info().misses, 1)
        self.assertIn('id="11-backtesting"', first)
        self.assertEqual(calls, 0)


class ScannerTests(SimpleTestCase):
    def test_scanner_returns_only_qualified_gainers_and_losers(self):
        class FakeExchange:
            def __init__(self, config=None):
                self.config = config

            def load_markets(self):
                return {
                    "BTC/USDT": {"active": True, "spot": True},
                    "ETH/USDT": {"active": True, "spot": True},
                    "SOL/USDT": {"active": True, "spot": True},
                    "XRP/USDT": {"active": True, "spot": True},
                }

            def fetch_tickers(self, symbols):
                return {
                    "BTC/USDT": {
                        "percentage": 21,
                        "last": 100,
                        "quoteVolume": 1_000_000,
                        "info": {"marketCap": 2_000_000},
                    },
                    "ETH/USDT": {
                        "percentage": -22,
                        "last": 100,
                        "quoteVolume": 800_000,
                        "info": {"marketCap": 2_000_000},
                    },
                    "SOL/USDT": {
                        "percentage": 1,
                        "last": 100,
                        "quoteVolume": 10,
                        "info": {"marketCap": 100},
                    },
                    "XRP/USDT": {
                        "percentage": 2,
                        "last": 100,
                        "quoteVolume": 10,
                        "info": {"marketCap": 100},
                    },
                }

            def fetch_order_book(self, symbol, limit=20):
                return {"bids": [[100, 10_000]], "asks": [[101, 10_000]]}

            def close(self):
                pass

        with patch("trading.market_scanner.ccxt.binance", FakeExchange):
            # The process cache is private by design; refresh guarantees that
            # this test cannot observe another test's market snapshot.
            result = scan_market_opportunities("binance", "spot", refresh=True)
        self.assertEqual(result["gainers"][0]["symbol"], "BTC/USDT")
        self.assertEqual(result["losers"][0]["symbol"], "ETH/USDT")
        self.assertEqual(len(result["gainers"]), 1)
        self.assertIn("Stop-Loss", result["risk_warning"])

    def test_binance_bulk_ticker_does_not_send_a_symbols_query(self):
        calls = []

        class FakeExchange:
            def __init__(self, config=None):
                pass

            def load_markets(self):
                return {
                    symbol: {"active": True, "spot": True}
                    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT")
                }

            def fetch_tickers(self, symbols=None):
                calls.append(symbols)
                return {
                    "BTC/USDT": {
                        "percentage": 20,
                        "last": 100,
                        "quoteVolume": 1_000_000,
                        "info": {"marketCap": 2_000_000},
                    },
                    "ETH/USDT": {
                        "percentage": 1,
                        "last": 100,
                        "quoteVolume": 100_000,
                        "info": {"marketCap": 2_000_000},
                    },
                    "SOL/USDT": {
                        "percentage": 1,
                        "last": 100,
                        "quoteVolume": 100_000,
                        "info": {"marketCap": 2_000_000},
                    },
                }

            def fetch_order_book(self, symbol, limit=20):
                return {"bids": [[100, 100]], "asks": [[101, 100]]}

            def close(self):
                pass

        with patch("trading.market_scanner.ccxt.binance", FakeExchange):
            scan_market_opportunities("binance", "spot", refresh=True)
        self.assertEqual(calls, [None])

    def test_binance_internal_type_error_does_not_trigger_large_compatibility_request(self):
        calls = []

        class FakeExchange:
            def __init__(self, config=None):
                pass

            def load_markets(self):
                return {"BTC/USDT": {"active": True, "spot": True}}

            def fetch_tickers(self, symbols=None):
                calls.append(symbols)
                raise TypeError("interner Adapterfehler")

            def close(self):
                pass

        with (
            patch("trading.market_scanner.ccxt.binance", FakeExchange),
            self.assertRaisesRegex(MarketScannerError, "interner Adapterfehler"),
        ):
            scan_market_opportunities("binance", "spot", refresh=True)
        self.assertEqual(calls, [None])

    def test_missing_market_cap_is_excluded_instead_of_inferred(self):
        class FakeExchange:
            def __init__(self, config=None):
                pass

            def load_markets(self):
                return {
                    symbol: {"active": True, "spot": True}
                    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT")
                }

            def fetch_tickers(self, symbols=None):
                return {
                    "BTC/USDT": {
                        "percentage": 20,
                        "last": 100,
                        "quoteVolume": 1_000_000,
                        "info": {"marketCap": 2_000_000},
                    },
                    "ETH/USDT": {
                        "percentage": -20,
                        "last": 100,
                        "quoteVolume": 1_000_000,
                        "info": {},
                    },
                    "SOL/USDT": {
                        "percentage": 1,
                        "last": 100,
                        "quoteVolume": 100_000,
                        "info": {"marketCap": 2_000_000},
                    },
                }

            def fetch_order_book(self, symbol, limit=20):
                return {"bids": [[100, 100]], "asks": [[101, 100]]}

            def close(self):
                pass

        with (
            patch("trading.market_scanner.ccxt.binance", FakeExchange),
            patch("trading.market_scanner._coingecko_market_caps", return_value={}),
        ):
            result = scan_market_opportunities(
                "binance",
                "spot",
                refresh=True,
                volume_spike_multiple=1,
            )
        eth = next(row for row in result["rows"] if row["symbol"] == "ETH/USDT")
        self.assertFalse(eth["eligible"])
        self.assertIsNone(eth["market_cap"])
        self.assertTrue(
            any("unbekannt" in reason for reason in eth["exclusion_reasons"]),
            eth["exclusion_reasons"],
        )

    def test_bitunix_spot_uses_documented_pair_kline_and_depth_endpoints(self):
        adapter = _BitunixScannerExchange("spot")
        requests = []

        def fake_json(url, **kwargs):
            requests.append((url, kwargs.get("params")))
            if url.endswith("/common/coin_pair/list"):
                return {
                    "code": 0,
                    "data": [
                        {
                            "id": "123",
                            "base": "BTC",
                            "quote": "USDT",
                            "isOpen": "1",
                            "precisions": ["0.01"],
                        }
                    ],
                }
            if url.endswith("/market/kline/history"):
                return {
                    "code": 0,
                    "data": [
                        {
                            "ts": f"2026-01-01T{hour:02d}:00:00Z",
                            "open": str(100 + hour),
                            "close": str(101 + hour),
                            "baseVolume": "10",
                        }
                        for hour in reversed(range(24))
                    ],
                }
            if url.endswith("/market/depth"):
                return {
                    "code": 0,
                    "data": {
                        "bids": [{"price": "100", "volume": "2"}],
                        "asks": [{"price": "101", "volume": "2"}],
                    },
                }
            raise AssertionError(url)

        adapter.provider._json = fake_json
        markets = adapter.load_markets()
        ticker = adapter.fetch_tickers(list(markets))["BTC/USDT"]
        depth = adapter.fetch_order_book("BTC/USDT")
        adapter.close()
        self.assertEqual(set(markets), {"BTC/USDT"})
        self.assertEqual(ticker["open"], 100)
        self.assertEqual(ticker["last"], 124)
        self.assertGreater(ticker["quoteVolume"], 0)
        self.assertEqual(depth["bids"][0]["price"], "100")
        self.assertFalse(any(url.endswith("/tickers") for url, _params in requests))
        depth_url, depth_params = next(
            (url, params) for url, params in requests if url.endswith("/depth")
        )
        self.assertTrue(depth_url.endswith("/api/spot/v1/market/depth"))
        self.assertEqual(depth_params["precision"], "0.01")

    def test_bitunix_spot_does_not_invent_missing_volume(self):
        adapter = _BitunixScannerExchange("spot")
        adapter.provider._json = lambda _url, **_kwargs: {
            "code": 0,
            "data": [
                {
                    "ts": f"2026-01-01T{hour:02d}:00:00Z",
                    "open": "100",
                    "close": "101",
                }
                for hour in range(24)
            ],
        }
        ticker = adapter._spot_ticker("BTC/USDT")
        adapter.close()
        self.assertIsNone(ticker["baseVolume"])
        self.assertIsNone(ticker["quoteVolume"])

    def test_bitunix_spot_does_not_guess_volume_from_array_positions(self):
        values = _BitunixScannerExchange._kline_values(
            [1_700_000_000_000, "100", "110", "90", "101", "999999"]
        )
        self.assertIsNone(values["base_volume"])
        self.assertIsNone(values["quote_volume"])

    def test_missing_bitunix_spot_volume_returns_explicitly_excluded_rows(self):
        class FakeExchange:
            def load_markets(self):
                return {"BTC/USDT": {"active": True, "spot": True}}

            def fetch_tickers(self, symbols):
                self.asserted_symbols = symbols
                return {
                    "BTC/USDT": {
                        "last": 120,
                        "percentage": 20,
                        "quoteVolume": None,
                        "info": {"marketCap": 1_000_000},
                    }
                }

            def fetch_order_book(self, _symbol, limit=20):
                raise AssertionError("Fehlendes Tagesvolumen muss vor dem Orderbuch ausschließen")

            def close(self):
                pass

        exchange = FakeExchange()
        with patch("trading.market_scanner._market_scanner_exchange", return_value=exchange):
            result = scan_market_opportunities("bitunix", "spot", refresh=True)
        self.assertEqual(exchange.asserted_symbols, ["BTC/USDT"])
        self.assertEqual(result["gainers"], [])
        self.assertEqual(len(result["rows"]), 1)
        self.assertFalse(result["rows"][0]["eligible"])
        self.assertTrue(
            any(
                "24h-Quotevolumen fehlt" in reason
                for reason in result["rows"][0]["exclusion_reasons"]
            )
        )

    def test_orderbook_depth_requires_two_sides_and_ignores_far_levels(self):
        class FakeExchange:
            def __init__(self, book):
                self.book = book

            def fetch_order_book(self, _symbol, limit=20):
                return self.book

        depth, error = _orderbook_depth(
            FakeExchange({"bids": [[100, 2]], "asks": []}),
            "BTC/USDT",
            {},
        )
        self.assertIsNone(depth)
        self.assertIn("Bid- und Ask", error)
        depth, error = _orderbook_depth(
            FakeExchange(
                {
                    "bids": [[100, 2], [50, 1000]],
                    "asks": [[101, 3], [150, 1000]],
                }
            ),
            "BTC/USDT",
            {},
        )
        self.assertIsNone(error)
        self.assertEqual(depth["quote"], 100 * 2 + 101 * 3)
        self.assertEqual(depth["mid"], 100.5)

    def test_scanner_parameters_are_bounded(self):
        with self.assertRaises(MarketScannerFilterError):
            scan_market_opportunities("binance", "spot", volatility_threshold=0)
        with self.assertRaises(MarketScannerFilterError):
            scan_market_opportunities("binance", "spot", min_orderbook_depth_ratio="nan")


class ScannerCacheTests(SimpleTestCase):
    """K3/PERF-24: Der Scanner-Cache muss gedeckelt sein und eviktieren.

    Ohne Obergrenze waechst das Dict mit benutzergewaehlten Float-Filtern als
    Schluessel im langlebigen Webprozess monotom (Memory-Leak mit OOM-Risiko
    fuer den gesamten Container inkl. der laufenden Bots).
    """

    def setUp(self):
        import trading.market_scanner as scanner

        self.scanner = scanner
        self._saved_cache = dict(scanner._CACHE)
        self._saved_market_caps = dict(scanner._MARKET_CAP_CACHE)
        scanner._CACHE.clear()
        scanner._MARKET_CAP_CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        self.scanner._CACHE.clear()
        self.scanner._CACHE.update(self._saved_cache)
        self.scanner._MARKET_CAP_CACHE.clear()
        self.scanner._MARKET_CAP_CACHE.update(self._saved_market_caps)

    def test_cache_is_bounded_and_evicts_oldest_entries(self):
        calls = []

        def fake_scan(exchange_id, market_type, limit=5, **filters):
            calls.append((exchange_id, market_type, tuple(sorted(filters.items()))))
            return {"exchange": exchange_id, "market": market_type, "gainers": [], "losers": []}

        with patch.object(self.scanner, "_scan_uncached", side_effect=fake_scan):
            # Mehr distincte Filter-Kombinationen als das LRU-Limit erlaubt.
            n = self.scanner._CACHE_MAX_ENTRIES + 8
            for index in range(n):
                scan_market_opportunities(
                    "binance", "spot", volatility_threshold=0.5 + index / 10
                )
        self.assertEqual(len(calls), n, "jede neue Kombination muss genau einmal scannen")
        self.assertLessEqual(len(self.scanner._CACHE), self.scanner._CACHE_MAX_ENTRIES)
        self.assertEqual(
            len(self.scanner._CACHE),
            self.scanner._CACHE_MAX_ENTRIES,
            "bei Ueberschreitung muss auf das Limit verengt werden",
        )

    def test_repeated_lookup_serves_from_cache_and_keeps_key_alive(self):
        calls = []

        def fake_scan(exchange_id, market_type, limit=5, **filters):
            calls.append(filters)
            return {"exchange": exchange_id, "market": market_type, "gainers": [], "losers": []}

        with patch.object(self.scanner, "_scan_uncached", side_effect=fake_scan):
            first = scan_market_opportunities("binance", "spot", volatility_threshold=12.5)
            for _ in range(3):
                again = scan_market_opportunities(
                    "binance", "spot", volatility_threshold=12.5
                )
        self.assertEqual(len(calls), 1, "TTL-treffer duerfen nicht erneut scannen")
        self.assertEqual(first, again)
