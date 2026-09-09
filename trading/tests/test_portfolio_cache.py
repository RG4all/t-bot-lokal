"""W2: Portfolio-Snapshot-Cache – Entlastung des Dashboard-Pollings.

Der Snapshot pro Konfiguration kostet zwei Queries je Symbol und wird von
``info_api`` und ``logs_api`` beim 10-Sekunden-Polling mehrfach parallel
aufgebaut. Der kurze TTL-Cache zusammen mit der expliziten Invalidierung nach
zustandsaendernden POSTs ist der Kompromiss: DB-Entlastung ohne veraltetes UI
nach eigenen Aktionen.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from trading import views
from trading.models import Configuration, TradingLog


def _log(config, action, price, symbol="BTC/USDT", pl_nominal=0):
    return TradingLog.objects.create(
        configuration=config,
        symbol=symbol,
        action=action,
        price=Decimal(price),
        amount=Decimal(1),
        fee_amount=Decimal("0.1"),
        pl_nominal=Decimal(pl_nominal),
        pl_relative=Decimal(0),
        total_pl=Decimal(pl_nominal),
        current_capital=Decimal(100),
        tank=Decimal(0),
        order_id=f"paper_{action}_{price}",
    )


@override_settings(PASSPHRASE_GATE_ENABLED=False)
class PortfolioCacheTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("w2-user", password="w2-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="W2",
            symbols="BTC/USDT",
            start_capital=Decimal(100),
            trade_amount=Decimal(10),
            fee=Decimal("0.1"),
        )
        views._PORTFOLIO_CACHE.clear()
        self.addCleanup(views._PORTFOLIO_CACHE.clear)
        self.client.force_login(self.user)

    def test_snapshot_is_cached_within_ttl_and_refreshed_after_invalidate(self):
        _log(self.config, "buy", 100)
        first = views._portfolio_snapshot(self.config)
        # Neuer Trade, aber der Cache schlaegt zu, solange die TTL laeuft.
        _log(self.config, "sell", 110, pl_nominal=9)
        second = views._portfolio_snapshot(self.config)
        self.assertEqual(second, first)
        views._invalidate_portfolio_cache(self.config.id)
        third = views._portfolio_snapshot(self.config)
        self.assertNotEqual(third["realized_profit"], first["realized_profit"])
        self.assertEqual(third["realized_profit"], Decimal(9))

    def test_manual_sell_invalidates_cache_immediately(self):
        _log(self.config, "buy", 100)
        baseline = views._portfolio_snapshot(self.config)
        self.assertEqual(len(baseline["positions"]), 1)
        # Die Bot-Loop hat den Sell bereits geschrieben, als der POST eintraf.
        _log(self.config, "sell", 110, pl_nominal=9)
        with patch.object(views.bot_manager, "manual_sell", return_value=None):
            response = self.client.post(
                f"/api/manual_sell/{self.config.id}/", {"symbol": "BTC/USDT"}
            )
        self.assertEqual(response.status_code, 200)
        fresh = views._portfolio_snapshot(self.config)
        self.assertEqual(fresh["positions"], [], "View muss den Cache nach dem Verkauf verwerfen")
        self.assertNotEqual(fresh, baseline)

    def test_reset_log_invalidates_cache(self):
        _log(self.config, "buy", 100)
        stale = views._portfolio_snapshot(self.config)
        self.assertEqual(len(stale["positions"]), 1)
        response = self.client.post(f"/reset_log/{self.config.id}/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.config.logs.count(), 0)
        fresh = views._portfolio_snapshot(self.config)
        self.assertEqual(fresh["positions"], [])
        self.assertEqual(fresh["realized_profit"], Decimal(0))

    def test_logs_api_open_symbols_falls_back_to_snapshot(self):
        _log(self.config, "buy", 100)
        response = self.client.get(f"/api/logs/{self.config.id}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Es laeuft kein Bot -> der Snapshot liefert das offene Symbol.
        self.assertEqual(payload["open_symbols"], ["BTC/USDT"])
