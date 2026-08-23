import csv
import io
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from trading.market_scanner import MarketScannerError, MarketScannerFilterError
from trading.models import BacktestTask, Configuration
from trading.views import _equity_svg


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class ScannerInterfaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("scanner-user", password="scanner-test-password")
        self.client.force_login(self.user)

    def test_configuration_page_contains_all_adjustable_scanner_sliders(self):
        response = self.client.get(reverse("config"))
        self.assertEqual(response.status_code, 200)
        for slider_id in (
            "scanner-volatility",
            "scanner-volume-spike",
            "scanner-market-cap",
            "scanner-orderbook",
        ):
            self.assertContains(response, f'id="{slider_id}"')
            self.assertContains(response, 'type="range"')
        self.assertContains(response, "Fundamental- und Liquiditätsdaten bleiben verpflichtend")

    def test_scanner_api_forwards_all_slider_values(self):
        payload = {
            "exchange": "binance",
            "market": "spot",
            "volatility_threshold": "12.5",
            "volume_spike_multiple": "2.25",
            "min_volume_market_cap_ratio": "0.15",
            "min_orderbook_depth_ratio": "0.0025",
        }
        with patch(
            "trading.views.scan_market_opportunities",
            return_value={"gainers": [], "losers": [], "filters": {}},
        ) as scanner:
            response = self.client.get(reverse("market_opportunities_api"), payload)
        self.assertEqual(response.status_code, 200)
        scanner.assert_called_once_with(
            "binance",
            "spot",
            refresh=False,
            volatility_threshold="12.5",
            volume_spike_multiple="2.25",
            min_volume_market_cap_ratio="0.15",
            min_orderbook_depth_ratio="0.0025",
        )

    def test_invalid_filters_are_400_and_upstream_failures_are_503(self):
        url = reverse("market_opportunities_api")
        with patch(
            "trading.views.scan_market_opportunities",
            side_effect=MarketScannerFilterError("Ungültige Scanner-Parameter"),
        ):
            response = self.client.get(url, {"exchange": "binance", "market": "spot"})
        self.assertEqual(response.status_code, 400)

        with patch(
            "trading.views.scan_market_opportunities",
            side_effect=MarketScannerError("Exchange nicht erreichbar"),
        ):
            response = self.client.get(url, {"exchange": "binance", "market": "spot"})
        self.assertEqual(response.status_code, 503)

        with patch(
            "trading.market_scanner.scan_all_market_opportunities",
            return_value={"exchanges": {}, "errors": {"binance": "offline"}},
        ):
            response = self.client.get(url, {"exchange": "all", "market": "spot"})
        self.assertEqual(response.status_code, 503)


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class BacktestReportExportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("report-user", password="report-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Report",
            symbols="BTC/USDT",
            start_capital=100,
        )
        self.task = BacktestTask.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            status="completed",
            parameters={
                "acc_from": -1,
                "acc_to": 1,
                "acc_steps": 1,
                "nda_from": -1,
                "nda_to": 1,
                "nda_steps": 1,
                "deltadelta_from": -1,
                "deltadelta_to": 1,
                "deltadelta_steps": 1,
            },
            result={
                "start_time": "2026-08-23T10:00:00+00:00",
                "end_time": "2026-08-23T10:03:00+00:00",
                "duration": "0:03:00",
                "metrics": {"combinations": 27},
                "global_results": {
                    "total_profit": "2.5",
                    "return_percentage": "2.5",
                    "gross_profit": "3.0",
                    "gross_loss": "0.5",
                    "total_fees": "0.2",
                    "average_profit": "2.5",
                    "profit_factor": "6",
                    "max_drawdown_percentage": "1.2",
                    "average_trade_duration_points": 2,
                    "average_trade_duration_seconds": 120,
                    "total_trades": 1,
                    "trades_per_symbol": {"BTC/USDT": 1},
                    "profit_per_market": {"BTC/USDT": "2.5"},
                },
                "symbol_results": {
                    "BTC/USDT": {
                        "best_capital": "102.5",
                        "best_thresholds": {
                            "acc_threshold": "-1",
                            "nda_threshold": "-1",
                            "deltadelta_threshold": "-1",
                        },
                        "optimized_thresholds_str": "Beschleunigung: -1",
                        "report": {
                            "net_profit": "2.5",
                            "return_percentage": "2.5",
                            "gross_profit": "3.0",
                            "gross_loss": "0.5",
                            "total_fees": "0.2",
                            "average_profit": "2.5",
                            "profit_factor": "6",
                            "max_drawdown_percentage": "1.2",
                            "average_trade_duration_points": 2,
                            "average_trade_duration_seconds": 120,
                            "num_buys": 1,
                            "num_sells": 1,
                            "win_rate": "100",
                            "equity_curve": [
                                {
                                    "index": 0,
                                    "timestamp": "2026-08-23T10:00:00+00:00",
                                    "equity": "100",
                                },
                                {
                                    "index": 3,
                                    "timestamp": "2026-08-23T10:03:00+00:00",
                                    "equity": "102.5",
                                },
                            ],
                            "trades": [
                                {
                                    "type": "sell",
                                    "index": 3,
                                    "timestamp": "2026-08-23T10:03:00+00:00",
                                    "entry_timestamp": "2026-08-23T10:01:00+00:00",
                                    "duration_points": 2,
                                    "duration_seconds": 120,
                                    "price": "103",
                                    "fee": "0.1",
                                    "capital_before": "90",
                                    "capital_after": "102.5",
                                    "profit_nominal": "2.5",
                                    "profit_percentage": "3",
                                }
                            ],
                        },
                    }
                },
            },
        )
        self.client.force_login(self.user)

    def test_html_export_is_landscape_and_contains_metrics_curve_and_trades(self):
        response = self.client.get(reverse("generate_backtest_html", args=[self.task.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertContains(response, "size: A4 landscape")
        self.assertContains(response, "Profit pro Markt")
        self.assertContains(response, "Equity-Kurve (mark-to-market)")
        self.assertContains(response, "2026-08-23T10:03:00+00:00")
        self.assertContains(response, '<svg class="equity-chart"', html=False)

    def test_csv_export_has_utf8_bom_summary_and_trade_details(self):
        response = self.client.get(reverse("generate_backtest_csv", args=[self.task.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0][0], "Datensatz")
        self.assertTrue(any(row[0] == "Zusammenfassung" and row[1] == "BTC/USDT" for row in rows))
        self.assertTrue(any(row[0] == "Trade" and row[2] == "sell" for row in rows))
        self.assertTrue(
            any(
                row[0] == "Equity" and row[1] == "BTC/USDT" and row[3] == "3" and row[11] == "102.5"
                for row in rows
            )
        )
        self.assertIn("120", "\n".join(",".join(row) for row in rows))

    def test_all_backtest_exports_are_owner_scoped(self):
        other_user = User.objects.create_user("other-report-user", password="other-password")
        self.client.force_login(other_user)
        for route in (
            "generate_backtest_pdf",
            "generate_backtest_html",
            "generate_backtest_csv",
        ):
            response = self.client.get(reverse(route, args=[self.task.id]))
            self.assertEqual(response.status_code, 404, route)

    def test_equity_svg_escapes_labels_and_ignores_non_finite_values(self):
        svg = str(
            _equity_svg(
                '<script>alert("x")</script>',
                [
                    {"index": 0, "timestamp": "<start>", "equity": "100"},
                    {"index": 1, "timestamp": "end", "equity": "nan"},
                    {"index": 2, "timestamp": "end", "equity": "101"},
                ],
            )
        )
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertIn("&lt;start&gt;", svg)
        self.assertIn("width:100%", svg)
