"""Regressionstests für die zentralisierte Indikatorberechnung (Prompt 18).

Ausgangsbefund: Die Arithmetik für NDA, DeltaDelta und Acceleration war
zweimal implementiert – in ``trading/backtesting.py``
(``Backtesting.calculate_indicators``) und in ``trading/trading_bot.py``
(``TradingBot.calculate_and_store``). Beide Varianten unterschieden sich in
Rundung und Indexbehandlung. Duplizierte Finanzarithmetik driftet: Ein Fix im
Backtest wirkte nicht im Live-Bot und umgekehrt. Ein Index-Guard fehlte in
beiden Kopien, wodurch ``idx=1`` stillschweigend über die Listendefinition
``prices[-1]`` rechnete statt abzubrechen.

Die Tests prüfen deshalb vier Dinge:

1. **Arithmetik:** Exakte Dezimalwerte, ``ROUND_HALF_UP`` auf 8
   Nachkommastellen und alle Nullstellen-Absicherungen der geteilten Funktion.
2. **Grenzfälle:** Zu kleiner/zu großer Index, zu kurze Preisreihe,
   null-belegter Vorpreis, Nicht-Decimal-Eingaben (Float, String, ``None``).
3. **Kein Duplikat mehr:** Die Formeln stehen nur noch in
   ``trading/indicators.py``; Backtesting, Bot und Tasks leiten dorthin weiter.
4. **Unverändertes Verhalten:** Der Live-Bot schreibt weiterhin die
   ungerundeten Rohwerte in die ``DataLog``-Zeile und prüft seine Schwellwerte
   gegen dieselben Rohwerte wie vor dem Refactoring; der Backtest-Pfad bleibt
   bitgenau bei seiner quantisierten Präzision.
"""

import inspect
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from trading import backtesting as backtesting_module
from trading import indicators as indicators_module
from trading import tasks as tasks_module
from trading import trading_bot as trading_bot_module
from trading.backtesting import Backtesting
from trading.indicators import (
    EIGHT_PLACES,
    IndicatorValues,
    build_indicator_rows,
    calculate_trading_indicators,
    compute_indicator_values,
    to_decimal,
)
from trading.models import Configuration, DataLog
from trading.trading_bot import TradingBot

_EIGHT = Decimal("0.00000001")


def _quantize(value):
    return value.quantize(_EIGHT, rounding=ROUND_HALF_UP)


class IndicatorMathTests(SimpleTestCase):
    """Referenzwerte, Rundung und Absicherungen der geteilten Funktion."""

    def test_reference_values_for_known_price_series(self):
        """100 → 101 → 102.5 liefert die von Hand nachgerechneten Werte."""
        prices = [Decimal(100), Decimal(101), Decimal("102.5")]

        acceleration, deltadelta, nda = calculate_trading_indicators(prices, 2)

        # NDA: 1.5 / 101 * 100 = 1.485148514851…  -> 1.48514851
        # vorherige NDA: 1 / 101 * 100 = 0.9900990099… -> 0.99009901
        # DVA: 1.48514851 - 0.99009901 = 0.49504950
        # Beschleunigung: 0.49504950 / 0.99009901 = 0.4999999949… -> 0.49999999
        # DeltaDelta: (1.48514851 + 0.99009901) / 2 = 1.23762376
        self.assertEqual(acceleration, Decimal("0.49999999"))
        self.assertEqual(deltadelta, Decimal("1.23762376"))
        self.assertEqual(nda, Decimal("1.48514851"))

        values = compute_indicator_values(prices, 2, rounding=_quantize)
        self.assertIsInstance(values, IndicatorValues)
        self.assertEqual(values.current_price, Decimal("102.5"))
        self.assertEqual(values.current_da, Decimal("1.5"))
        self.assertEqual(values.previous_da, Decimal(1))
        self.assertEqual(values.previous_nda, Decimal("0.99009901"))
        self.assertEqual(values.dva, Decimal("0.49504950"))

    def test_quantize_uses_round_half_up_on_the_eighth_place(self):
        """Exakt 5 auf der 9. Nachkommastelle wird aufgerundet, nicht bankergerundet."""
        prices = [Decimal(100), Decimal(100), Decimal("100.000000005")]

        acceleration, deltadelta, nda = calculate_trading_indicators(prices, 2)

        # 0.000000005 % -> 0.00000001 (ROUND_HALF_UP); mit ROUND_HALF_EVEN wäre es 0.
        self.assertEqual(nda, Decimal("0.00000001"))
        self.assertEqual(deltadelta, Decimal("0.00000001"))
        # vorherige NDA ist 0 -> Beschleunigung bleibt defensiv 0.
        self.assertEqual(acceleration, Decimal(0))

    def test_precision_constant_is_centralised(self):
        self.assertEqual(EIGHT_PLACES, _EIGHT)
        self.assertIs(indicators_module.EIGHT_PLACES, EIGHT_PLACES)

    def test_zero_previous_price_never_divides(self):
        """Ein Vorpreis von 0 ist nicht definierbar und liefert neutrales 0."""
        prices = [Decimal(50), Decimal(0), Decimal(10)]

        acceleration, deltadelta, nda = calculate_trading_indicators(prices, 2)
        values = compute_indicator_values(prices, 2)

        self.assertEqual((acceleration, deltadelta, nda), (Decimal(0), Decimal(0), Decimal(0)))
        self.assertEqual(values.nda, Decimal(0))
        self.assertEqual(values.previous_nda, Decimal(0))
        self.assertEqual(values.dva, Decimal(0))
        self.assertEqual(values.acceleration, Decimal(0))
        # Die absolute Kursdifferenz bleibt auch ohne gültigen Nenner erhalten.
        self.assertEqual(values.current_da, Decimal(10))

    def test_zero_previous_nda_neutralises_acceleration_only(self):
        """Flacher Vorgänger: keine Division durch 0, NDA/DeltaDelta bleiben erhalten."""
        prices = [Decimal(100), Decimal(100), Decimal(101)]

        acceleration, deltadelta, nda = calculate_trading_indicators(prices, 2)

        self.assertEqual(acceleration, Decimal(0))
        self.assertEqual(deltadelta, Decimal("0.50000000"))
        self.assertEqual(nda, Decimal("1.00000000"))

    def test_index_below_two_is_rejected_instead_of_wrapping(self):
        """``idx < 2`` darf nicht über die Listendefinition auf ``prices[-1]`` zugreifen.

        Vor dem Fix rechnete ``calculate_indicators(prices, 1)`` stillschweigend
        mit dem letzten statt dem vorletzten Preis – ein unsichtbar falscher
        Indikatorwert statt eines Fehlers.
        """
        prices = [Decimal(100), Decimal(101), Decimal(102)]

        for index in (0, 1, -1, -3):
            with self.subTest(index=index):
                with self.assertRaises(IndexError):
                    calculate_trading_indicators(prices, index)
                with self.assertRaises(IndexError):
                    Backtesting.calculate_indicators(prices, index)

    def test_index_outside_the_series_is_rejected(self):
        prices = [Decimal(100), Decimal(101), Decimal(102)]

        with self.assertRaises(IndexError):
            calculate_trading_indicators(prices, 3)
        with self.assertRaises(IndexError):
            compute_indicator_values(prices, 99)

    def test_series_shorter_than_three_points_is_rejected(self):
        with self.assertRaises(IndexError):
            calculate_trading_indicators([Decimal(100), Decimal(101)], 2)

    def test_non_decimal_prices_are_converted_exactly(self):
        """Floats, Strings und ``None`` verhalten sich wie im bisherigen Code."""
        mixed = [100.0, "101", Decimal("102.5")]
        decimal_equivalent = [Decimal(100), Decimal(101), Decimal("102.5")]

        self.assertEqual(
            calculate_trading_indicators(mixed, 2),
            calculate_trading_indicators(decimal_equivalent, 2),
        )
        # ``None`` zählt wie im Altcode als 0, statt eine Exception auszulösen.
        self.assertEqual(to_decimal(None), Decimal(0))
        self.assertEqual(to_decimal(None, "1000"), Decimal(1000))
        self.assertEqual(to_decimal(0.1), Decimal("0.1"))
        self.assertEqual(to_decimal(Decimal("1.5")), Decimal("1.5"))

    def test_invalid_price_values_stay_invalid(self):
        """Ein nicht zahlbarer Preis wird nicht stillschweigend zu 0."""
        with self.assertRaises(ArithmeticError):
            calculate_trading_indicators([Decimal(100), Decimal(101), "kein-preis"], 2)

    def test_raw_snapshot_keeps_unrounded_values(self):
        """Der Bot-Pfad rundet bewusst nicht zwischen – dokumentierte Präzisionsstufe."""
        prices = [Decimal(100), Decimal(101), Decimal("102.5")]

        raw = compute_indicator_values(prices, 2)
        quantized = compute_indicator_values(prices, 2, rounding=_quantize)

        # Roh: (1.5/101 - 1/101) / (1/101) = 0.5 innerhalb der 28-stelligen
        # Division; quantisiert: 0.49504950 / 0.99009901 = 0.49999999.
        self.assertGreater(raw.acceleration, quantized.acceleration)
        self.assertEqual(_quantize(raw.acceleration), Decimal("0.50000000"))
        self.assertEqual(quantized.acceleration, Decimal("0.49999999"))

    def test_rounding_hook_is_applied_to_every_strategy_step(self):
        """Roharithmetik + Rundungshook ersetzen exakt den bisherigen Backtest-Code."""
        prices = [Decimal("97.5"), Decimal("99.25"), Decimal("101.7"), Decimal("100.4")]

        for index in range(2, len(prices)):
            with self.subTest(index=index):
                expected = calculate_trading_indicators(prices, index)
                values = compute_indicator_values(prices, index, rounding=_quantize)
                self.assertEqual(
                    (values.acceleration, values.deltadelta, values.nda),
                    expected,
                )

    def test_build_indicator_rows_is_aligned_with_the_price_index(self):
        prices = [Decimal("97.5"), Decimal("99.25"), Decimal("101.7"), Decimal("100.4")]

        rows = build_indicator_rows(prices)

        self.assertEqual(len(rows), len(prices))
        self.assertIsNone(rows[0])
        self.assertIsNone(rows[1])
        for index in range(2, len(prices)):
            self.assertEqual(rows[index], calculate_trading_indicators(prices, index))

    def test_build_indicator_rows_handles_short_series(self):
        prices = [Decimal(100), Decimal(101)]

        self.assertEqual(build_indicator_rows(prices), [None, None])
        self.assertEqual(build_indicator_rows([]), [])


class IndicatorDeduplicationTests(SimpleTestCase):
    """Die Indikatorarithmetik darf nur noch an einer Stelle stehen."""

    def test_backtesting_delegates_to_the_shared_function(self):
        prices = [Decimal(100), Decimal(101), Decimal("102.5")]

        self.assertIs(
            backtesting_module.calculate_trading_indicators,
            indicators_module.calculate_trading_indicators,
        )
        self.assertEqual(
            Backtesting.calculate_indicators(prices, 2),
            calculate_trading_indicators(prices, 2),
        )

    def test_bot_uses_the_shared_snapshot_builder(self):
        self.assertIs(
            trading_bot_module.compute_indicator_values,
            indicators_module.compute_indicator_values,
        )
        self.assertIs(trading_bot_module.EIGHT_PLACES, indicators_module.EIGHT_PLACES)

    def test_indicator_formulas_are_defined_only_in_indicators_module(self):
        """Keine der drei Formeln steht noch in Backtesting, Bot oder Tasks."""
        shared_markers = (
            "/ previous_price * Decimal(100)",
            "dva / previous_nda",
            "nda + previous_nda) / ",
        )
        legacy_markers = shared_markers + (
            "/ previous_price * 100",
            "dva / prev_nda",
            "previous_nda) / 2",
        )
        sources = {
            "trading/backtesting.py": inspect.getsource(backtesting_module),
            "trading/trading_bot.py": inspect.getsource(trading_bot_module),
            "trading/tasks.py": inspect.getsource(tasks_module),
        }
        for name, source in sources.items():
            for marker in legacy_markers:
                with self.subTest(file=name, marker=marker):
                    self.assertNotIn(marker, source)

        shared = inspect.getsource(indicators_module)
        for marker in shared_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, shared)

    def test_duplicated_precision_constants_are_gone(self):
        for module in (backtesting_module, trading_bot_module):
            with self.subTest(module=module.__name__):
                self.assertFalse(hasattr(module, "_EIGHT_PLACES"))

    def test_indicator_rows_are_precomputed_through_the_shared_helper(self):
        """Das ``[None, None] + […]``-Muster darf nicht mehr kopiert werden."""
        for module in (backtesting_module, tasks_module):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertNotIn("[None, None]", source)
                self.assertIn("build_indicator_rows", source)

    def test_compute_indicator_series_still_matches_the_shared_rows(self):
        prices = [Decimal(100), Decimal("100.5"), Decimal("101.25"), Decimal("100.75")]

        indices, acceleration, deltadelta, nda = Backtesting.compute_indicator_series(prices)
        rows = build_indicator_rows(prices)

        self.assertEqual(indices, list(range(2, len(prices))))
        for position, index in enumerate(indices):
            self.assertEqual(acceleration[position], rows[index][0])
            self.assertEqual(deltadelta[position], rows[index][1])
            self.assertEqual(nda[position], rows[index][2])

    def test_simulation_still_reports_the_shared_indicators(self):
        """Der Backtest nutzt die geteilten Werte und bleibt in den Ergebnissen stabil."""
        prices = [Decimal(100), Decimal(101), Decimal("102.5"), Decimal(104)]

        capital, report = Backtesting.simulate_trading_detailed(
            prices,
            Decimal("0.1"),
            Decimal("0.5"),
            Decimal("0.5"),
            {
                "start_capital": Decimal(1000),
                "trade_amount": Decimal(100),
                "take_profit": Decimal(5),
                "stop_loss": Decimal(100),
                "fee_percentage": Decimal("0.1"),
            },
        )

        self.assertEqual(report["num_buys"], 1)
        self.assertEqual(report["num_sells"], 1)
        self.assertEqual(report["num_trades"], 2)
        self.assertEqual(report["final_capital"], capital)


@override_settings(AUTOSTART_BOTS=False)
class LiveBotIndicatorTests(TransactionTestCase):
    """Der Live-Bot verhält sich nach dem Refactoring bitgenau wie vorher."""

    def setUp(self):
        self.user = User.objects.create_user("indicator-user", password="indicator-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Indikator-Bot",
            symbols="BTC/USDT",
            start_capital=Decimal(1000),
            trade_amount=Decimal(10),
            fee=Decimal("0.1"),
            countdown=0,
        )

    def test_calculate_and_store_writes_the_shared_indicator_row(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100), Decimal(110), Decimal(121)]

        async_to_sync(bot.calculate_and_store)("BTC/USDT")

        row = DataLog.objects.get(configuration=self.config, symbol="BTC/USDT")
        # Referenz: von Hand quantisierte Bot-Rohwerte (10 % und 9.09… %).
        self.assertEqual(row.price, Decimal("121.00000000"))
        self.assertEqual(row.max_price, Decimal("121.00000000"))
        self.assertEqual(row.min_price, Decimal("100.00000000"))
        self.assertEqual(row.current_da, Decimal("11.00000000"))
        self.assertEqual(row.nda, Decimal("10.00000000"))
        self.assertEqual(row.prev_da, Decimal("10.00000000"))
        self.assertEqual(row.prev_nda, Decimal("9.09090909"))
        self.assertEqual(row.dva, Decimal("0.90909091"))
        self.assertEqual(row.deltadelta, Decimal("9.54545455"))
        self.assertEqual(row.div_DVA_prev_NDA, Decimal("0.10000000"))
        self.assertEqual(row.mvd, Decimal("0.82644628"))

    def test_logged_row_matches_the_backtest_indicators(self):
        """Glatte Serie: Backtest und Bot liefern identische Indikatorwerte."""
        prices = [Decimal(100), Decimal(110), Decimal(121)]
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = list(prices)

        async_to_sync(bot.calculate_and_store)("BTC/USDT")

        row = DataLog.objects.get(configuration=self.config, symbol="BTC/USDT")
        acceleration, deltadelta, nda = Backtesting.calculate_indicators(prices, 2)
        self.assertEqual(row.nda, nda)
        self.assertEqual(row.deltadelta, deltadelta)
        self.assertEqual(row.div_DVA_prev_NDA, acceleration)

    def test_threshold_check_receives_the_unrounded_values(self):
        """check_trading sieht weiterhin die Rohwerte des Bots, nicht die 8dp-Werte."""
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100), Decimal(101), Decimal("102.5")]
        expected = compute_indicator_values(bot.price_buffer["BTC/USDT"], 2)
        bot.check_trading = AsyncMock()

        async_to_sync(bot.calculate_and_store)("BTC/USDT")

        bot.check_trading.assert_awaited_once_with(
            "BTC/USDT",
            expected.current_price,
            expected.nda,
            expected.deltadelta,
            expected.acceleration,
        )
        # Die Rohwerte sind bewusst nicht auf 8 Nachkommastellen quantisiert.
        self.assertNotEqual(expected.nda, _quantize(expected.nda))

    def test_extreme_ticker_price_does_not_break_the_cycle(self):
        """Ein von außen eingeschleuster Extrempreis bricht keinen Bot-Zyklus ab."""
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal("1e10"), Decimal("1e10"), Decimal("1e30")]
        bot.check_trading = AsyncMock()

        async_to_sync(bot.calculate_and_store)("BTC/USDT")

        # Kein Exception, die Schwellwertprüfung läuft mit dem Rohwert.
        self.assertGreater(bot.check_trading.await_args.args[2], Decimal("1e20"))

    def test_short_price_buffer_is_a_noop(self):
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100), Decimal(101)]
        bot.check_trading = AsyncMock()

        async_to_sync(bot.calculate_and_store)("BTC/USDT")

        self.assertEqual(DataLog.objects.filter(configuration=self.config).count(), 0)
        bot.check_trading.assert_not_called()

    def test_extreme_prices_raise_loudly_in_the_quantized_path(self):
        """Quantisierter Pfad: ein Extrempreis bleibt ein harter Fehler, kein 0.

        Ein still auf 0 gerundeter Indikatorwert würde im Backtest wie ein
        „kein Signal" aussehen und Ergebnisse verfälschen. Der Bot-Pfad ist
        davon nicht betroffen, weil er ohne Zwischenrundung rechnet.
        """
        prices = [Decimal("1e10"), Decimal("1e10"), Decimal("1e30")]

        with self.assertRaises(ArithmeticError):
            calculate_trading_indicators(prices, 2)
        values = compute_indicator_values(prices, 2)
        self.assertGreater(values.nda, Decimal("1e20"))
        # Der DataLog-Schreibpfad klemmt auf den Feldbereich statt zu scheitern.
        self.assertEqual(trading_bot_module._bounded(values.nda), Decimal("999999999999.99999999"))
        self.assertEqual(trading_bot_module._bounded(values.acceleration), Decimal(0))

    def test_sub_grid_price_is_rounded_on_write_only(self):
        """Unter der Rastergrenze liegt nur der DB-Wert, nicht der Vergleichswert."""
        bot = TradingBot(self.config)
        bot.price_buffer["BTC/USDT"] = [Decimal(100), Decimal(100), Decimal("100.000000005")]
        bot.check_trading = AsyncMock()

        async_to_sync(bot.calculate_and_store)("BTC/USDT")

        row = DataLog.objects.get(configuration=self.config, symbol="BTC/USDT")
        # NDA 0.000000005 % wird beim Schreiben auf 0.00000001 gerundet …
        self.assertEqual(row.nda, Decimal("0.00000001"))
        # … DeltaDelta bleibt aus den Rohwerten gebildet und rundet auf 0.
        self.assertEqual(row.deltadelta, Decimal(0))
        # Vorherige NDA ist 0, also bleibt die Beschleunigung defensiv 0.
        self.assertEqual(row.div_DVA_prev_NDA, Decimal(0))
        # Der Schwellwertvergleich sieht den ungerundeten Rohwert.
        self.assertEqual(
            bot.check_trading.await_args.args[2],
            Decimal("0.000000005"),
        )
