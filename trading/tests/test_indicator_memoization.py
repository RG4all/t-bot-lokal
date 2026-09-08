"""Regressionen für begrenzte, threadsichere Indikator-Memoisierung."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, DivisionByZero, Inexact, InvalidOperation, localcontext
from unittest.mock import MagicMock, patch
from weakref import ref

from django.test import SimpleTestCase

from trading import backtesting
from trading.backtesting import Backtesting
from trading.tasks import run_backtest, simulate_candidate


class IndicatorMemoizationTests(SimpleTestCase):
    def setUp(self):
        # Auch am Ausgangsstand ausführbar: fehlende API erst im Test melden.
        if hasattr(Backtesting, "clear_indicator_cache"):
            Backtesting.clear_indicator_cache()
            self.addCleanup(Backtesting.clear_indicator_cache)
        self.prices = [Decimal(value) for value in (100, 101, 103, 107, 109)]

    def test_repeated_pair_skips_decimal_conversions(self):
        with patch.object(backtesting, "_decimal", wraps=backtesting._decimal) as convert:
            first = Backtesting.calculate_indicators(self.prices, 2)
            for _ in range(100):
                self.assertEqual(Backtesting.calculate_indicators(self.prices, 2), first)
            self.assertEqual(convert.call_count, 3)

    def test_clear_forces_recalculation(self):
        Backtesting.calculate_indicators(self.prices, 2)
        Backtesting.clear_indicator_cache()
        self.assertFalse(backtesting._indicator_cache)
        with patch.object(backtesting, "_decimal", wraps=backtesting._decimal) as convert:
            Backtesting.calculate_indicators(self.prices, 2)
            self.assertEqual(convert.call_count, 3)

    def test_lru_bound_and_hit_refresh(self):
        with patch.object(backtesting, "_INDICATOR_CACHE_MAXSIZE", 2):
            Backtesting.calculate_indicators(self.prices, 2)
            Backtesting.calculate_indicators(self.prices, 3)
            Backtesting.calculate_indicators(self.prices, 2)
            Backtesting.calculate_indicators(self.prices, 4)
            self.assertEqual(
                list(backtesting._indicator_cache),
                [
                    (id(self.prices), 2),
                    (id(self.prices), 4),
                ],
            )

    def test_distinct_lists_are_isolated_and_identity_is_retained(self):
        first = Backtesting.calculate_indicators(self.prices, 2)
        other = [Decimal(1), Decimal(0), Decimal(3)]
        self.assertEqual(Backtesting.calculate_indicators(other, 2), (Decimal(0),) * 3)
        self.assertEqual(Backtesting.calculate_indicators(self.prices, 2), first)
        entry = backtesting._indicator_cache[(id(self.prices), 2)]
        self.assertIs(entry[0], self.prices)  # Verhindert Wiederverwendung von id(prices).

    def test_parallel_same_pair_computes_once(self):
        with patch.object(backtesting, "_decimal", wraps=backtesting._decimal) as convert:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda _: Backtesting.calculate_indicators(self.prices, 2), range(100)
                    )
                )
            self.assertTrue(all(result == results[0] for result in results))
            self.assertEqual(convert.call_count, 3)

    def test_parallel_clear_and_compute_remain_consistent(self):
        expected = Backtesting.calculate_indicators(self.prices, 2)

        def work(index):
            if index % 3 == 0:
                Backtesting.clear_indicator_cache()
            return Backtesting.calculate_indicators(self.prices, 2)

        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(work, range(100))), [expected] * 100)
        Backtesting.clear_indicator_cache()
        self.assertFalse(backtesting._indicator_cache)

    def test_exceptions_are_not_cached(self):
        with self.assertRaises(IndexError):
            Backtesting.calculate_indicators(self.prices, 100)
        self.assertFalse(backtesting._indicator_cache)
        with localcontext() as context:
            context.prec = 2
            with self.assertRaises(InvalidOperation):
                Backtesting.calculate_indicators(self.prices, 2)
        self.assertFalse(backtesting._indicator_cache)

    def test_context_changes_do_not_reuse_incompatible_results(self):
        Backtesting.calculate_indicators(self.prices, 2)
        with localcontext() as context:
            context.prec = 2
            with self.assertRaises(InvalidOperation):
                Backtesting.calculate_indicators(self.prices, 2)
        with localcontext() as context:
            context.traps[Inexact] = True
            with self.assertRaises(Inexact):
                Backtesting.calculate_indicators(self.prices, 2)

    def test_cache_hit_preserves_decimal_signal_flags(self):
        with localcontext() as context:
            context.clear_flags()
            Backtesting.calculate_indicators(self.prices, 2)
            expected = dict(context.flags)
            self.assertTrue(expected[Inexact])
            context.clear_flags()
            Backtesting.calculate_indicators(self.prices, 2)
            self.assertEqual(dict(context.flags), expected)

    def test_simulation_reuses_original_price_identity(self):
        # Decimal-Konvertierung der Preise für Trades bleibt erlaubt, die
        # Indikatoren sollen aber nicht an einer neuen Listenkopie hängen.
        with patch.object(
            Backtesting, "calculate_indicators", wraps=Backtesting.calculate_indicators
        ) as calc:
            for _ in range(2):
                Backtesting.simulate_trading_detailed(self.prices, 0, 0, 0, {})
            self.assertTrue(all(call.args[0] is self.prices for call in calc.call_args_list))

    def test_replaced_price_does_not_return_stale_result(self):
        before = Backtesting.calculate_indicators(self.prices, 2)
        self.prices[2] = Decimal(110)
        after = Backtesting.calculate_indicators(self.prices, 2)
        self.assertNotEqual(before, after)
        self.assertEqual(after, Backtesting._calculate_indicators(self.prices, 2))
        del self.prices[2:]
        with self.assertRaises(IndexError):
            Backtesting.calculate_indicators(self.prices, 2)

    def test_eviction_and_clear_release_list_references(self):
        class Prices(list):
            pass

        with patch.object(backtesting, "_INDICATOR_CACHE_MAXSIZE", 1):
            prices = Prices(self.prices)
            reference = ref(prices)
            Backtesting.calculate_indicators(prices, 2)
            del prices
            self.assertIsNotNone(reference())
            Backtesting.calculate_indicators(self.prices, 2)
            self.assertIsNone(reference())
            prices = Prices(self.prices)
            reference = ref(prices)
            Backtesting.calculate_indicators(prices, 2)
            del prices
            Backtesting.clear_indicator_cache()
            self.assertIsNone(reference())

    def test_many_distinct_lists_cannot_exceed_bound(self):
        with patch.object(backtesting, "_INDICATOR_CACHE_MAXSIZE", 8):
            for _ in range(100):
                Backtesting.calculate_indicators(list(self.prices), 2)
                self.assertLessEqual(len(backtesting._indicator_cache), 8)

    def test_unrelated_decimal_flags_are_not_replayed(self):
        with localcontext() as context:
            context.flags[DivisionByZero] = True
            Backtesting.calculate_indicators(self.prices, 2)
            self.assertTrue(context.flags[DivisionByZero])
            context.clear_flags()
            Backtesting.calculate_indicators(self.prices, 2)
            self.assertFalse(context.flags[DivisionByZero])
            self.assertTrue(context.flags[Inexact])

    def test_simulation_still_accepts_iterators(self):
        self.assertEqual(
            Backtesting.simulate_trading_detailed(iter(self.prices), 0, 0, 0, {}),
            Backtesting.simulate_trading_detailed(self.prices, 0, 0, 0, {}),
        )

    def test_series_matches_original_decimal_formula(self):
        for prices in (self.prices, [Decimal(1)] * 3, [Decimal(1), Decimal(0), Decimal(3)]):
            for index in range(2, len(prices)):
                self.assertEqual(
                    Backtesting.calculate_indicators(prices, index),
                    Backtesting._calculate_indicators(prices, index),
                )


class IndicatorCacheTaskLifecycleTests(SimpleTestCase):
    def setUp(self):
        if hasattr(Backtesting, "clear_indicator_cache"):
            Backtesting.clear_indicator_cache()
            self.addCleanup(Backtesting.clear_indicator_cache)
        self.prices = [Decimal(100), Decimal(101), Decimal(103)]
        self.params = {
            f"{name}_{suffix}": value
            for name in ("acc", "nda", "deltadelta")
            for suffix, value in (("from", -100), ("to", -100), ("steps", 1))
        }

    def test_backtest_cleanup_on_all_exit_paths(self):
        for outcome in (
            "completed",
            "cancelled",
            "cancelled_late",
            "failed",
            "missing",
            "save_failed",
        ):
            with self.subTest(outcome=outcome):
                Backtesting.calculate_indicators(self.prices, 2)
                with (
                    patch("trading.tasks.BacktestTask.objects") as objects,
                    patch(
                        "trading.tasks._historical_data",
                        return_value={
                            "prices": self.prices,
                            "timestamps": [],
                        },
                    ),
                    patch("trading.tasks._simulate_candidate") as candidate,
                    patch("trading.tasks._collect_results", return_value={}),
                ):
                    task = MagicMock()
                    objects.select_related.return_value.get.return_value = task
                    objects.only.return_value.get.return_value.status = (
                        "cancelled" if outcome == "cancelled" else "running"
                    )
                    if outcome == "cancelled_late":
                        task.refresh_from_db.side_effect = lambda task=task, **_: setattr(
                            task, "status", "cancelled"
                        )
                    candidate.return_value = {
                        "final_capital": Decimal(1000),
                        "thresholds": {
                            f"{name}_threshold": -100 for name in ("acc", "nda", "deltadelta")
                        },
                    }
                    if outcome == "missing":
                        from trading.models import BacktestTask

                        objects.select_related.return_value.get.side_effect = (
                            BacktestTask.DoesNotExist
                        )
                    if outcome == "failed":
                        candidate.side_effect = RuntimeError("simulation failed")
                    if outcome == "save_failed":
                        task.save.side_effect = RuntimeError("save failed")
                        objects.filter.return_value.update.side_effect = RuntimeError(
                            "update failed"
                        )
                    if outcome in ("failed", "save_failed"):
                        with self.assertRaises(RuntimeError):
                            run_backtest.run(1, self.params, ["BTC/USDT"], 1)
                    else:
                        result = run_backtest.run(1, self.params, ["BTC/USDT"], 1)
                        if outcome == "completed":
                            self.assertIn("metrics", result)
                        elif outcome in ("cancelled", "cancelled_late"):
                            self.assertEqual(result, {"status": "cancelled"})
                        elif outcome == "missing":
                            self.assertEqual(result, {"error": "Backtest nicht gefunden"})
                    self.assertFalse(backtesting._indicator_cache)

    def test_legacy_candidate_cleanup_on_success_and_error(self):
        for fail in (False, True):
            with (
                self.subTest(fail=fail),
                patch("trading.tasks.Configuration.objects") as objects,
                patch(
                    "trading.tasks._historical_data",
                    return_value={
                        "prices": self.prices,
                        "timestamps": [],
                    },
                ),
                patch("trading.tasks._simulate_candidate", return_value={}) as candidate,
            ):
                Backtesting.calculate_indicators(self.prices, 2)
                objects.get.return_value.id = 1
                if fail:
                    candidate.side_effect = RuntimeError("simulation failed")
                simulate_candidate.run(1, {}, "BTC/USDT", 1, 0, 0, 0)
                self.assertFalse(backtesting._indicator_cache)
