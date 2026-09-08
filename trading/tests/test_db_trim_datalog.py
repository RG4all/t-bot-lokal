"""Regressionstests für das Batch-Delete in db_trim_datalog (Prompt 17).

Der Ausgangsstand löschte alle Alt-Einträge einer Konfiguration/Symbol-
Kombination in einem einzigen DELETE-Statement. Bei 20.000+ Zeilen kann
dies die Datenbank für andere Operationen lange sperren. Der Fix löscht
in 1000er-Schritten, jede Charge in einer eigenen kurzen Transaktion.

Die Tests prüfen das Behaltens-/Löschverhalten, die Begrenzung auf genau
die betroffene Konfiguration/Symbol-Kombination, den Abbruch bei leeren
Chargen (kein Endlos-Loop) sowie – über einen Transaktions-Probe – dass
wirklich in Batches von höchstens 1000 Zeilen gelöscht wird.
"""

from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.db import transaction
from django.test import TransactionTestCase

from trading import trading_bot
from trading.models import Configuration, DataLog
from trading.trading_bot import db_trim_datalog


class _BatchProbeContext:
    """Kontextmanager, der nach erfolgreichem Batch-Commit die Rest-Zeilen misst."""

    def __init__(self, probe, real_context):
        self._probe = probe
        self._real_context = real_context

    def __enter__(self):
        self._probe.depth += 1
        self._real_context.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        result = self._real_context.__exit__(exc_type, exc_value, traceback)
        self._probe.depth -= 1
        # Nur die äußerste Transaktionsgrenze zählt: Django verschachtelt in
        # QuerySet.delete() eine eigene atomic-Transaktion (savepoint=False).
        if exc_type is None and self._probe.depth == 0:
            remaining = DataLog.objects.filter(
                configuration_id=self._probe.config_id,
                symbol=self._probe.symbol,
            ).count()
            self._probe.remaining_after_batch.append(remaining)
        return result


class _BatchProbe:
    """Ersetzt transaction.atomic im Bot-Modul und protokolliert Batch-Größen.

    db_trim_datalog führt pro 1000er-Charge genau eine eigene Transaktion
    aus. Der Probe zählt, wie viele Zeilen nach jedem abgeschlossenen
    Batch-Commit noch vorhanden sind; daraus ergeben sich die tatsächlichen
    Chargengrößen. Damit ist nachweisbar, dass kein einzelner DELETE über
    alle Alt-Einträge läuft und keine Charge größer als 1000 Zeilen ist.
    """

    def __init__(self, config_id, symbol):
        # Original vor dem Patch festhalten: Nach patch.object zeigt
        # trading_bot.transaction.atomic bereits auf den Probe selbst.
        self._real_atomic = transaction.atomic
        self.config_id = config_id
        self.symbol = symbol
        self.depth = 0
        self.remaining_after_batch = []

    def __call__(self, *args, **kwargs):
        return _BatchProbeContext(self, self._real_atomic(*args, **kwargs))

    def batch_sizes(self, initial_total):
        values = [initial_total] + list(self.remaining_after_batch)
        return [values[index] - values[index + 1] for index in range(len(values) - 1)]


class DbTrimDatalogTests(TransactionTestCase):
    """Verhalten von db_trim_datalog mit echten DataLog-Zeilen (SQLite).

    db_trim_datalog läuft über den Bot-DB-Executor in einem eigenen Thread
    und schreibt in eigenen, sofort committeten Transaktionen. Deshalb gilt
    hier wie bei TradingBotTests (test_core.py): TransactionTestCase statt
    TestCase, damit die angelegten Zeilen für den Executor-Thread sichtbar
    sind und jede Test-Datenbank zwischen den Tests neu aufgesetzt wird.
    """

    def setUp(self):
        self.user = User.objects.create_user("trim-user", password="trim-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Trim-Config",
            symbols="BTC/USDT,ETH/USDT",
        )
        self.other_config = Configuration.objects.create(
            user=self.user,
            name="Other-Config",
            symbols="BTC/USDT",
        )

    def _create_rows(self, config, symbol, count, start_price=100):
        ids = []
        for index in range(count):
            row = DataLog.objects.create(
                configuration=config,
                symbol=symbol,
                price=Decimal(start_price + index),
                min_price=Decimal(start_price + index),
                max_price=Decimal(start_price + index),
            )
            ids.append(row.id)
        return ids

    def _remaining_ids(self, config, symbol):
        return list(
            DataLog.objects.filter(configuration=config, symbol=symbol)
            .order_by("id")
            .values_list("id", flat=True)
        )

    def test_trim_keeps_only_the_newest_max_rows(self):
        ids = self._create_rows(self.config, "BTC/USDT", 250)
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        self.assertEqual(
            self._remaining_ids(self.config, "BTC/USDT"),
            sorted(ids[-100:]),
        )

    def test_trim_with_exactly_max_rows_keeps_everything(self):
        ids = self._create_rows(self.config, "BTC/USDT", 100)
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        self.assertEqual(self._remaining_ids(self.config, "BTC/USDT"), ids)

    def test_trim_below_max_rows_is_a_noop(self):
        ids = self._create_rows(self.config, "BTC/USDT", 50)
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        self.assertEqual(self._remaining_ids(self.config, "BTC/USDT"), ids)

    def test_trim_scopes_deletes_to_configuration_and_symbol(self):
        btc_ids = self._create_rows(self.config, "BTC/USDT", 250)
        eth_ids = self._create_rows(self.config, "ETH/USDT", 30, start_price=500)
        other_ids = self._create_rows(self.other_config, "BTC/USDT", 25, start_price=900)
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        # Nur die überlaufende BTC/USDT-Kombination dieser Konfiguration wird gekürzt.
        self.assertEqual(self._remaining_ids(self.config, "BTC/USDT"), sorted(btc_ids[-100:]))
        self.assertEqual(self._remaining_ids(self.config, "ETH/USDT"), eth_ids)
        self.assertEqual(self._remaining_ids(self.other_config, "BTC/USDT"), other_ids)

    def test_trim_deletes_in_batches_of_at_most_1000_rows(self):
        ids = self._create_rows(self.config, "BTC/USDT", 2600)
        probe = _BatchProbe(self.config.id, "BTC/USDT")
        with patch.object(trading_bot.transaction, "atomic", probe):
            async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        sizes = probe.batch_sizes(initial_total=len(ids))
        # 2.500 Alt-Zeilen werden in 1.000 + 1.000 + 500 gelöscht, nicht in
        # einem einzigen DELETE über alle 2.500 Zeilen.
        self.assertEqual(sizes, [1000, 1000, 500])
        for size in sizes:
            self.assertGreaterEqual(size, 1)
            self.assertLessEqual(size, 1000)
        self.assertEqual(
            self._remaining_ids(self.config, "BTC/USDT"),
            sorted(ids[-100:]),
        )

    def test_trim_breaks_when_there_is_nothing_left_to_delete(self):
        self._create_rows(self.config, "BTC/USDT", 250)
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        self.assertEqual(len(self._remaining_ids(self.config, "BTC/USDT")), 100)
        # Zweiter Aufruf: keine Alt-Zeilen mehr, kein Batch, kein Endlos-Loop.
        probe = _BatchProbe(self.config.id, "BTC/USDT")
        with patch.object(trading_bot.transaction, "atomic", probe):
            async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 100)
        self.assertEqual(probe.remaining_after_batch, [])
        self.assertEqual(len(self._remaining_ids(self.config, "BTC/USDT")), 100)

    def test_trim_with_invalid_max_rows_is_a_safe_noop(self):
        ids = self._create_rows(self.config, "BTC/USDT", 50)
        # Negatives max_rows würde bei einem Slicing [max_rows:max_rows+1]
        # eine Exception auslösen; die Funktion muss das abfangen.
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", -5)
        async_to_sync(db_trim_datalog)(self.config.id, "BTC/USDT", 0)
        self.assertEqual(self._remaining_ids(self.config, "BTC/USDT"), ids)
