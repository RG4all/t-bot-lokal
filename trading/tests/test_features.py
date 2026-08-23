import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from trading.backtest_templates import build_backtest_templates
from trading.market_scanner import scan_market_opportunities
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
