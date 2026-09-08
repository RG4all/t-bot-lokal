"""Prompt 14 / Audit §3.3: StringIO-Vertrag und unveränderter CSV-Export.

Das bisherige Echo-Muster erzeugte bereits gültiges CSV. Die Vertragstests
werden erst mit dem Standardpuffer grün; die HTTP-/Inhaltstests sichern die
Refaktorierung gegen Datenreste, CSV-Strukturmanipulation und fremde Exporte ab.
"""

import csv
import io
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import OperationalError
from django.db.models.query import QuerySet
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from trading import views
from trading.models import Configuration, TradingLog

HEADER = [
    "timestamp",
    "symbol",
    "action",
    "price",
    "amount",
    "fee_amount",
    "order_id",
    "pl_nominal",
    "pl_relative",
    "total_pl",
    "current_capital",
    "tank",
]
BOM = b"\xef\xbb\xbf"


def csv_rows(content):
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


class CsvAdapterTests(SimpleTestCase):
    def test_legacy_echo_adapter_is_removed(self):
        self.assertFalse(hasattr(views, "_CsvEcho"))


@override_settings(
    PASSPHRASE_GATE_ENABLED=False,
    AUTOSTART_BOTS=False,
    SECURE_SSL_REDIRECT=False,
    TIME_ZONE="Europe/Berlin",
)
class TradingReportCsvTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("csv-owner")
        cls.other = User.objects.create_user("csv-other")
        cls.config = Configuration.objects.create(user=cls.user, name="CSV", symbols="BTC/USDT")

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse("generate_report_csv", args=[self.config.id])

    def make_log(self, **overrides):
        values = {
            "configuration": self.config,
            "symbol": "BTC/USDT",
            "action": "sell",
            "price": Decimal("12345.67890123"),
            "amount": Decimal("0.12345678"),
            "fee_amount": Decimal("0.00000001"),
            "order_id": "csv-order",
            "pl_nominal": Decimal("-1.23456789"),
            "pl_relative": Decimal("-0.01234567"),
            "total_pl": Decimal("3.45678901"),
            "current_capital": Decimal("98765.43210987"),
            "tank": Decimal(0),
        }
        values.update(overrides)
        return TradingLog(**values)

    def stream_response(self):
        # Direkte View: Der Testclient schließt Antworten nach Iterationsende
        # selbst und würde einen fehlenden Context-Manager im Generator verdecken.
        request = RequestFactory().get(self.url)
        request.user = self.user
        response = views.generate_report_csv(request, self.config.id)
        self.addCleanup(response.close)
        return response

    def writer_buffer(self, factory):
        factory.assert_called_once()
        buffer = factory.call_args.args[0]
        self.assertIsInstance(buffer, io.StringIO)
        return buffer

    def test_empty_report_has_bom_header_and_attachment_headers(self):
        response = self.client.get(self.url)
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertRegex(
            response["Content-Disposition"],
            rf'^attachment; filename="csv-owner_binance_{self.config.id}_\d{{8}}_\d{{6}}\.csv"$',
        )
        self.assertEqual(
            list(response.streaming_content),
            [BOM, (",".join(HEADER) + "\r\n").encode("utf-8")],
        )

    def test_rows_preserve_precision_local_timezone_and_stable_order(self):
        logs = [self.make_log(order_id=f"order-{index}") for index in range(3)]
        TradingLog.objects.bulk_create(logs)
        timestamp = datetime(2026, 9, 8, 10, 30, 15, tzinfo=dt_timezone.utc)
        TradingLog.objects.filter(configuration=self.config).update(timestamp=timestamp)
        TradingLog.objects.filter(pk=logs[2].pk).update(timestamp=timestamp - timedelta(hours=1))

        with timezone.override("Europe/Berlin"):
            rows = csv_rows(b"".join(self.stream_response().streaming_content))
        self.assertEqual(rows[0], HEADER)
        self.assertEqual([row[6] for row in rows[1:]], ["order-2", "order-0", "order-1"])
        self.assertEqual(rows[1][0], "2026-09-08T11:30:15+02:00")
        self.assertEqual(
            rows[2],
            [
                "2026-09-08T12:30:15+02:00",
                "BTC/USDT",
                "sell",
                "12345.67890123",
                "0.12345678",
                "1E-8",
                "order-0",
                "-1.23456789",
                "-0.01234567",
                "3.45678901",
                "98765.43210987",
                "0E-8",
            ],
        )

    def test_csv_delimiters_quotes_unicode_and_newlines_round_trip(self):
        payloads = [
            'order,"quoted"',
            'order\r\nforged,row,"value"',
            "order\nextra\rline",
            "Grüße 東京 🪙",
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                log = self.make_log(symbol='B"T,C/€', order_id=payload)
                log.save()
                rows = csv_rows(b"".join(self.stream_response().streaming_content))
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(rows[1]), len(HEADER))
                self.assertEqual(rows[1][1], log.symbol)
                self.assertEqual(rows[1][6], payload)
                log.delete()

    def test_long_row_followed_by_short_row_leaves_no_previous_content(self):
        long_id = 'private,"' + "x" * 90 + '\r\nend"'
        TradingLog.objects.bulk_create(
            [
                self.make_log(order_id=long_id),
                self.make_log(order_id=""),
            ]
        )
        chunks = list(self.stream_response().streaming_content)
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0], BOM)
        self.assertEqual(csv_rows(chunks[1]), [HEADER])
        self.assertEqual(len(csv_rows(chunks[2])), 1)
        self.assertEqual(csv_rows(chunks[2])[0][6], long_id)
        self.assertEqual(len(csv_rows(chunks[3])), 1)
        self.assertEqual(csv_rows(chunks[3])[0][6], "")
        self.assertLess(len(chunks[3]), len(chunks[2]))
        self.assertNotIn(b"private", chunks[3])
        self.assertEqual(b"".join(chunks).count(BOM), 1)

    def test_writer_uses_one_stringio_with_standard_write_returns(self):
        self.make_log().save()
        with patch("trading.views.csv.writer", wraps=csv.writer) as factory:
            response = self.stream_response()
            chunks = iter(response.streaming_content)
            self.assertEqual(next(chunks), BOM)
            self.assertEqual(csv_rows(next(chunks)), [HEADER])
            buffer = self.writer_buffer(factory)
            self.assertTrue(buffer.writable())
            self.assertTrue(buffer.seekable())
            self.assertEqual(buffer.write(""), 0)  # Standard-write liefert eine Zahl, kein CSV.
            row = next(chunks)
            self.assertEqual(buffer.getvalue(), row.decode("utf-8"))
            self.assertEqual(csv_rows(row)[0][6], "csv-order")
            self.assertEqual(list(chunks), [])
            factory.assert_called_once()
            self.assertTrue(buffer.closed)

    def test_queryset_is_lazy_batched_and_not_cached(self):
        TradingLog.objects.bulk_create(
            [self.make_log(order_id=f"batch-{index}") for index in range(1001)]
        )
        with patch.object(
            QuerySet, "iterator", autospec=True, side_effect=QuerySet.iterator
        ) as scan:
            response = self.stream_response()
            scan.assert_not_called()
            chunks = iter(response.streaming_content)
            with self.assertNumQueries(0):
                self.assertEqual(next(chunks), BOM)
                self.assertEqual(csv_rows(next(chunks)), [HEADER])
            scan.assert_not_called()
            with self.assertNumQueries(1):
                first = next(chunks)
            scan.assert_called_once()
            self.assertEqual(scan.call_args.kwargs, {"chunk_size": 1000})
            queryset = scan.call_args.args[0]
            self.assertIsNone(queryset._result_cache)
            with self.assertNumQueries(0):
                rest = list(chunks)
            self.assertEqual(len(rest), 1000)
            self.assertEqual(csv_rows(first)[0][6], "batch-0")
            self.assertEqual(csv_rows(rest[-1])[0][6], "batch-1000")
            self.assertTrue(all(len(csv_rows(chunk)) == 1 for chunk in rest))
            self.assertIsNone(queryset._result_cache)

    def test_unconsumed_response_allocates_no_buffer(self):
        with patch("trading.views.csv.writer", wraps=csv.writer) as factory:
            response = self.stream_response()
            factory.assert_not_called()
            response.close()
            factory.assert_not_called()

    def test_response_close_releases_buffer_mid_stream(self):
        self.make_log().save()
        for consumed in (1, 2, 3):
            with (
                self.subTest(consumed=consumed),
                patch("trading.views.csv.writer", wraps=csv.writer) as factory,
            ):
                response = self.stream_response()
                chunks = iter(response.streaming_content)
                for _ in range(consumed):
                    next(chunks)
                if consumed == 1:
                    factory.assert_not_called()
                else:
                    buffer = self.writer_buffer(factory)
                    self.assertFalse(buffer.closed)
                response.close()
                if consumed > 1:
                    self.assertTrue(buffer.closed)
                self.assertEqual(list(chunks), [])

    def test_buffer_is_closed_on_iteration_error(self):
        log = self.make_log()
        log.save()

        def failing_rows():
            yield log
            raise OperationalError("synthetic-csv-database-error")

        with (
            patch.object(QuerySet, "iterator", return_value=failing_rows()),
            patch("trading.views.csv.writer", wraps=csv.writer) as factory,
        ):
            chunks = iter(self.stream_response().streaming_content)
            next(chunks)  # BOM
            next(chunks)  # Header
            buffer = self.writer_buffer(factory)
            self.assertEqual(csv_rows(next(chunks))[0][6], log.order_id)
            with self.assertRaises(OperationalError):
                next(chunks)
            self.assertTrue(buffer.closed)
            self.assertEqual(list(chunks), [])  # Keine Fehlerdetails als CSV-Daten ausgeben.

    def test_owner_cannot_export_foreign_configuration(self):
        self.client.force_login(self.other)
        with patch("trading.views.csv.writer", wraps=csv.writer) as factory:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.streaming)
        factory.assert_not_called()

    def test_export_contains_only_requested_configuration(self):
        self.make_log(order_id="own-order").save()
        for user in (self.user, self.other):
            config = Configuration.objects.create(user=user, name="Excluded", symbols="BTC/USDT")
            self.make_log(configuration=config, order_id="excluded-order").save()
        response = self.client.get(self.url)
        self.addCleanup(response.close)
        rows = csv_rows(b"".join(response.streaming_content))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][6], "own-order")

    def test_anonymous_requests_redirect_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={self.url}",
            fetch_redirect_response=False,
        )
        self.assertFalse(response.streaming)

    def test_non_get_methods_are_rejected(self):
        for method in ("post", "put", "patch", "delete", "head", "options"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url)
                self.assertEqual(response.status_code, 405)
                self.assertFalse(response.streaming)

    def test_filename_sanitizes_untrusted_components(self):
        self.user.username = 'csv"\r\nX-Injected: yes'
        self.user.save(update_fields=["username"])
        self.config.exchange = '../bad"\r\nname'
        self.config.save(update_fields=["exchange"])
        response = self.client.get(self.url)
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response["Content-Disposition"],
            rf'^attachment; filename="csv-X-Injected-yes_bad-name_{self.config.id}_'
            r'\d{8}_\d{6}\.csv"$',
        )
        self.assertNotIn("X-Injected", response)
