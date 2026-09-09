"""O5: prune_history begrenzt TradingLogs je Konfiguration und das ErrorLog.

Ohne Pflege wachsen Trade-Historie und Fehlerlog unbegrenzt (nur DataLogs
trimmt der Bot selbst). Der Command loescht in Batches und unterraeumt
nie das Cap; geloeste/Info-Fehler haben zusaetzlich eine Altersfrist.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from trading.models import Configuration, ErrorLog, TradingLog


class PruneHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("o5-user", password="o5-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="O5",
            symbols="BTC/USDT",
            start_capital=Decimal(100),
            trade_amount=Decimal(10),
            fee=Decimal("0.1"),
        )

    def _create_trading_logs(self, count):
        TradingLog.objects.bulk_create(
            [
                TradingLog(
                    configuration=self.config,
                    symbol="BTC/USDT",
                    action="buy",
                    price=Decimal(100),
                    amount=Decimal(1),
                    fee_amount=Decimal(0),
                    pl_nominal=Decimal(0),
                    pl_relative=Decimal(0),
                    total_pl=Decimal(0),
                    current_capital=Decimal(100),
                    tank=Decimal(0),
                    order_id=f"o5-{i}",
                )
                for i in range(count)
            ]
        )

    def _out(self):
        from io import StringIO

        return StringIO()

    def test_dry_run_keeps_everything_but_reports_counts(self):
        self._create_trading_logs(7)
        out = self._out()
        call_command("prune_history", "--trading-logs-per-config", "5", "--dry-run", stdout=out)
        self.assertEqual(self.config.logs.count(), 7)
        self.assertIn("2 TradingLogs zu loeschen (dry-run)", out.getvalue())

    def test_trims_oldest_rows_and_keeps_newest(self):
        self._create_trading_logs(7)
        keep = list(self.config.logs.order_by("-id").values_list("id", flat=True)[:5])
        call_command("prune_history", "--trading-logs-per-config", "5", stdout=self._out())
        remaining = set(self.config.logs.values_list("id", flat=True))
        self.assertEqual(remaining, set(keep))

    def test_below_cap_rows_untouched(self):
        self._create_trading_logs(3)
        call_command("prune_history", "--trading-logs-per-config", "5", stdout=self._out())
        self.assertEqual(self.config.logs.count(), 3)

    def test_resolved_and_info_errors_expire_after_retention(self):
        old = timezone.now() - timedelta(days=40)
        resolved = ErrorLog.objects.create(
            source="t", message="alt", severity="error", resolved=True
        )
        info = ErrorLog.objects.create(source="t", message="alt", severity="info")
        fresh_open = ErrorLog.objects.create(source="t", message="neu", severity="critical")
        ErrorLog.objects.filter(id__in=[resolved.id, info.id, fresh_open.id]).update(timestamp=old)
        fresh_open_id = fresh_open.id
        # Der offene, schwere Fehler bleibt trotz Alter bestehen …
        call_command("prune_history", "--error-days", "30", "--error-logs", "50000", stdout=self._out())
        self.assertTrue(ErrorLog.objects.filter(id=fresh_open_id).exists())
        # … geloeste und Info-Eintraege nicht.
        self.assertFalse(ErrorLog.objects.filter(id__in=[resolved.id, info.id]).exists())

    def test_rejects_nonpositive_limits(self):
        with self.assertRaises(CommandError):
            call_command("prune_history", "--trading-logs-per-config", "0")
