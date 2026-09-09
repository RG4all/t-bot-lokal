"""Regressionstests für BUG-26, BUG-27 und CODE-28.

- BUG-26:  Bitunix Spot hat laut API-Dokumentation *kein* 24h-Volumen; der
           Markt-Scanner muss deshalb am absoluten Orderbuch-Tiefen-Boden
           messen statt alle Märkte still auszuschließen (Top-Gainer/Loser
           lieferten sonst nie ein Ergebnis).
- BUG-27:  Kursabruf-Timeouts durften keine leeren Fehler-Log-Einträge
           erzeugen (``str(asyncio.TimeoutError()) == ""``) und müssen wie
           Verbindungsstörungen mit Backoff behandelt werden.
- CODE-28: Fehler-Log, Dashboard-Status und APIs zeigen präzise,
           menschenlesbare, sichere Beschreibungen statt generischer Zeilen.

Die Bitunix-Payloads entsprechen der offiziellen API-Dokumentation
(https://www.bitunix.com/api-docs/spots/en_us/public/ und
https://www.bitunix.com/api-docs/futures/market/get_tickers.html).
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from trading import views
from trading.error_explanations import explain_error, has_explanation
from trading.market_data import (
    BitunixPublicMarketData,
    MarketDataConnectionError,
    MarketDataTimeoutError,
    SymbolValidationError,
)
from trading.market_scanner import (
    MIN_ABSOLUTE_ORDERBOOK_DEPTH_QUOTE,
    MarketScannerError,
    scan_market_opportunities,
)
from trading.models import Configuration, ErrorLog
from trading.trading_bot import TradingBot, _error_message_text, db_log_error

PRIVATE_MARKER = "BUGTEST_PRIVATE_DETAIL"


# ---------------------------------------------------------------------------
# BUG-26a: Bitunix-Adapter gegen die dokumentierten API-Formen
# ---------------------------------------------------------------------------
class BitunixDocumentedApiTests(TestCase):
    """Die echten Adapter mit den dokumentierten Response-Formen der Börse."""

    def test_spot_pair_list_uses_documented_base_quote_shape(self):
        provider = BitunixPublicMarketData("spot")
        provider._json = lambda _url, **_kw: {
            "code": "0",
            "msg": "Success",
            "data": [
                {"id": "1", "base": "BTC", "quote": "USDT", "isOpen": 1,
                 "precisions": ["8", "4"]},
                {"id": "2", "base": "ETH", "quote": "USDT", "isOpen": "1"},
                {"id": "3", "base": "DEAD", "quote": "USDT", "isOpen": 0},
            ],
        }
        try:
            self.assertEqual(provider.available_symbols(), {"BTCUSDT", "ETHUSDT"})
        finally:
            provider.close()

    @staticmethod
    def _spot_json_factory(pairs, price="65000.10"):
        """Dokumentierte Spot-Endpunkte, geroutet nach URL (wie in Production)."""

        def fake_json(url, **_kwargs):
            if url.endswith("/common/coin_pair/list"):
                return {"code": "0", "msg": "Success", "data": pairs}
            if url.endswith("/market/last_price"):
                return {"code": "0", "msg": "Success", "data": price}
            raise AssertionError(url)

        return fake_json

    def test_spot_pair_without_status_field_is_treated_as_closed(self):
        # Fail-closed: Ein unbekannter Pair-Status zählt nicht als handelbar.
        # Da keine aktiven Pairs übrig bleiben, schlägt der Katalog-Abruf
        # sichtbar fehl, statt leere/ungültige Symbole durchzulassen.
        provider = BitunixPublicMarketData("spot")
        provider._json = self._spot_json_factory(
            [{"id": "9", "base": "BTC", "quote": "USDT"}]
        )
        try:
            with self.assertRaises(MarketDataConnectionError):
                provider.available_symbols()
        finally:
            provider.close()

    def test_spot_last_price_is_a_bare_string_per_documentation(self):
        provider = BitunixPublicMarketData("spot")
        provider._json = self._spot_json_factory(
            [{"id": "1", "base": "BTC", "quote": "USDT", "isOpen": 1}]
        )
        try:
            result = provider.fetch_tickers(["BTC/USDT"])
            self.assertEqual(result, {"BTC/USDT": {"last": "65000.10"}})
            self.assertEqual(provider.validate_symbols(["BTC/USDT"]), [])
        finally:
            provider.close()

    def test_spot_invalid_symbol_is_rejected_before_pricing(self):
        provider = BitunixPublicMarketData("spot")
        provider._json = self._spot_json_factory(
            [{"id": "1", "base": "BTC", "quote": "USDT", "isOpen": 1}]
        )
        try:
            with self.assertRaises(SymbolValidationError) as ctx:
                provider.fetch_tickers(["ETH/USDT"])
            self.assertEqual(ctx.exception.symbols, ["ETH/USDT"])
        finally:
            provider.close()

    def test_spot_per_symbol_requests_are_paced(self):
        provider = BitunixPublicMarketData("spot")
        provider._json = self._spot_json_factory(
            [
                {"id": "1", "base": "BTC", "quote": "USDT", "isOpen": 1},
                {"id": "2", "base": "ETH", "quote": "USDT", "isOpen": 1},
                {"id": "3", "base": "SOL", "quote": "USDT", "isOpen": 1},
            ]
        )
        with patch("trading.market_data.time.sleep") as sleep:
            provider.fetch_tickers(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        # Drei Symbole -> zwei Pausen, damit die Request-Rate unter dem
        # dokumentierten 10 Requests/Sek/IP-Limit bleibt.
        self.assertEqual(sleep.call_count, 2)
        for call in sleep.call_args_list:
            self.assertGreaterEqual(call.args[0], 0.1)

    def test_futures_ticker_shape_without_percentage_field(self):
        provider = BitunixPublicMarketData("futures")

        def fake_json(url, **_kwargs):
            if url.endswith("/trading_pairs"):
                return {
                    "code": 0,
                    "msg": "Success",
                    "data": [
                        {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT",
                         "symbolStatus": "OPEN", "isApiSupported": True},
                    ],
                }
            return {
                "code": 0,
                "msg": "Success",
                "data": [
                    {"symbol": "BTCUSDT", "markPrice": "57892.1",
                     "lastPrice": "57891.2", "open": "55000", "last": "57891.2",
                     "quoteVol": "1200000", "baseVol": "20",
                     "high": "58000", "low": "54000"},
                ],
            }

        provider._json = fake_json
        try:
            result = provider.fetch_tickers(["BTC/USDT"])
            self.assertEqual(result["BTC/USDT"]["last"], "57891.2")
        finally:
            provider.close()


# ---------------------------------------------------------------------------
# BUG-26b: Bitunix Spot Markt-Scan liefert qualifizierte Märkte
# ---------------------------------------------------------------------------
def _bitunix_scan_fake_json(depth_quote, btc_change_pct=12.0, eth_change_pct=-12.0):
    """Dokumentierte Bitunix-Spot-Endpunkte; Klines ohne Volumenfelder."""

    def fake_json(url, **kwargs):
        params = kwargs.get("params") or {}
        if url.endswith("/common/coin_pair/list"):
            return {
                "code": "0",
                "msg": "Success",
                "data": [
                    {"id": "1", "base": "BTC", "quote": "USDT", "isOpen": 1,
                     "precisions": ["8"]},
                    {"id": "2", "base": "ETH", "quote": "USDT", "isOpen": 1,
                     "precisions": ["6"]},
                ],
            }
        if url.endswith("/market/kline/history"):
            symbol = str(params.get("symbol", "")).upper()
            step = btc_change_pct / 24 if symbol.startswith("BTC") else eth_change_pct / 24
            return {
                "code": "0",
                "msg": "Success",
                "data": [
                    {"symbol": symbol, "open": str(100 + step * hour),
                     "high": str(101 + step * hour), "low": str(99 + step * hour),
                     "close": str(100 + step * (hour + 1)),
                     "ts": f"2026-09-08T{hour:02d}:00:00Z"}
                    for hour in range(24)
                ],
            }
        if url.endswith("/market/depth"):
            return {
                "code": "0",
                "msg": "Success",
                "data": {
                    "bids": [{"price": "100", "volume": str(depth_quote / 200)}],
                    "asks": [{"price": "101", "volume": str(depth_quote / 200)}],
                },
            }
        raise AssertionError(url)

    return fake_json


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class BitunixSpotScannerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("bitunix-scanner-user")

    def setUp(self):
        import trading.market_scanner as scanner_module

        scanner_module._reset_process_state_for_tests()
        self.client.force_login(self.user)

    def _run_scan(self, depth_quote):
        from trading.market_scanner import _BitunixScannerExchange

        adapter = _BitunixScannerExchange("spot")
        adapter.provider._json = _bitunix_scan_fake_json(depth_quote)
        try:
            with patch(
                "trading.market_scanner._market_scanner_exchange",
                return_value=adapter,
            ):
                return scan_market_opportunities("bitunix", "spot", refresh=True)
        finally:
            adapter.close()

    def test_spot_scan_qualifies_movers_via_orderbook_depth(self):
        result = self._run_scan(MIN_ABSOLUTE_ORDERBOOK_DEPTH_QUOTE * 2)
        self.assertEqual([row["symbol"] for row in result["gainers"]], ["BTC/USDT"])
        self.assertEqual([row["symbol"] for row in result["losers"]], ["ETH/USDT"])
        row = result["rows"][0]
        self.assertIsNone(row["volume_spike"])
        self.assertIsNone(row["volume_ratio"])
        self.assertIsNone(row["quote_volume_24h"])
        self.assertIsNotNone(row["orderbook_depth_quote"])
        self.assertIn(
            "min_absolute_orderbook_depth_quote", result["filters"]
        )

    def test_spot_scan_excludes_thin_books_with_explanatory_reason(self):
        result = self._run_scan(MIN_ABSOLUTE_ORDERBOOK_DEPTH_QUOTE / 10)
        self.assertEqual(result["gainers"], [])
        self.assertEqual(result["losers"], [])
        for row in result["rows"]:
            self.assertFalse(row["eligible"])
            self.assertTrue(
                any(
                    "absoluten Mindestwert" in reason
                    for reason in row["exclusion_reasons"]
                )
            )
        self.assertTrue(result["excluded_sample"])
        self.assertIn("BTC/USDT", [item["symbol"] for item in result["excluded_sample"]])
        sample = next(
            item for item in result["excluded_sample"] if item["symbol"] == "BTC/USDT"
        )
        self.assertTrue(sample["reasons"])

    def test_scanner_api_returns_user_safe_reason_for_known_errors(self):
        error = MarketScannerError(
            f"intern {PRIVATE_MARKER}",
            user_message="Keine spot-Märkte mit Stablecoin-Quote gefunden.",
        )
        with patch("trading.views.scan_market_opportunities", side_effect=error):
            response = self.client.get(
                reverse("market_opportunities_api"),
                {"exchange": "bitunix", "market": "spot"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"],
            "Keine spot-Märkte mit Stablecoin-Quote gefunden.",
        )
        self.assertNotIn(PRIVATE_MARKER, response.content.decode())

    def test_scanner_api_keeps_generic_message_for_unsafe_errors(self):
        error = MarketScannerError(f"intern {PRIVATE_MARKER}")
        with patch("trading.views.scan_market_opportunities", side_effect=error):
            response = self.client.get(
                reverse("market_opportunities_api"),
                {"exchange": "bitunix", "market": "spot"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], views._SCANNER_ERROR)
        self.assertNotIn(PRIVATE_MARKER, response.content.decode())


# ---------------------------------------------------------------------------
# BUG-27: Kursabruf-Timeouts erzeugen beschreibbare, nie leere Meldungen
# ---------------------------------------------------------------------------
class BotTimeoutErrorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("timeout-user")
        cls.config = Configuration.objects.create(
            user=cls.user,
            name="Timeout test",
            exchange="bitunix",
            market="spot",
            symbols="BTC/USDT,ETH/USDT",
        )

    def test_empty_exceptions_get_a_fallback_description(self):
        fallback = _error_message_text(asyncio.TimeoutError(), "TimeoutError")
        self.assertTrue(fallback.strip())
        self.assertIn("TimeoutError", fallback)
        self.assertEqual(_error_message_text("Bereits beschrieben", "X"), "Bereits beschrieben")
        self.assertIn("Ausnahme ohne Meldungstext", _error_message_text("   ", ""))

    def test_timeout_error_is_a_connection_class_error(self):
        error = MarketDataTimeoutError("Bitunix", 5, 80)
        # Der Main-Loop verzweigt über MarketDataConnectionError; nur so
        # erhält ein Timeout den 5–300-s-Backoff statt 2 s.
        self.assertIsInstance(error, MarketDataConnectionError)
        self.assertIn("Bitunix", str(error))
        self.assertIn("5", str(error))
        self.assertIn("80 s", str(error))

    def test_fetch_tickers_converts_timeout_into_descriptive_error(self):
        bot = TradingBot(self.config)
        self.addCleanup(bot.stop)

        def hanging_fetch(_symbols):
            import time as _time

            _time.sleep(4)
            return {}

        bot.exchange.fetch_tickers = hanging_fetch
        bot._sync_fetch_timeout = lambda _count: 1.0

        async def run_fetch():
            bot.loop = asyncio.get_running_loop()
            return await bot.fetch_tickers(["BTC/USDT", "ETH/USDT"])

        with self.assertRaises(MarketDataTimeoutError) as ctx:
            async_to_sync(run_fetch)()
        message = str(ctx.exception)
        self.assertNotEqual(message.strip(), "")
        self.assertIn("Bitunix", message)
        self.assertIn("2", message)  # Anzahl der Symbole
        self.assertIn("1 s", message)  # Zeitbudget in der Meldung

    def test_db_log_error_never_stores_an_empty_message(self):
        raw = db_log_error.__wrapped__.__wrapped__
        raw(
            config_id=self.config.id,
            source="trading_bot.fetch_tickers",
            message=asyncio.TimeoutError(),
            severity="error",
            exception_type="TimeoutError",
        )
        entry = ErrorLog.objects.latest("id")
        self.assertTrue(entry.message.strip())
        self.assertIn("TimeoutError", entry.message)
        self.assertNotIn("SECRET", entry.message)

    def test_bot_run_catchall_keeps_non_empty_last_error(self):
        bot = TradingBot(self.config)
        self.addCleanup(bot.stop)
        bot.last_error = _error_message_text(asyncio.TimeoutError(), "TimeoutError")
        self.assertTrue(bot.last_error.strip())


# ---------------------------------------------------------------------------
# CODE-28: Präzise, sichere Fehler-Erklärungen in UI und APIs
# ---------------------------------------------------------------------------
def _sources_in_codebase():
    pattern = re.compile(r"['\"]((?:trading_bot|views|apps)\.[a-z_.]+)['\"]")
    sources = set()
    base = Path(__file__).resolve().parents[2]
    for name in ("trading/trading_bot.py", "trading/views.py", "trading/apps.py"):
        sources.update(pattern.findall((base / name).read_text(encoding="utf-8")))
    return sources


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class ErrorExplanationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("explanation-owner")
        cls.config = Configuration.objects.create(
            user=cls.user,
            name="Explanation test",
            exchange="bitunix",
            market="spot",
            symbols="BTC/USDT",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_every_source_written_into_error_logs_has_a_specific_explanation(self):
        sources = _sources_in_codebase()
        self.assertTrue(sources, "Quelle-Scan hat nichts gefunden – Muster prüfen.")
        missing = sorted(source for source in sources if not has_explanation(source))
        self.assertEqual(missing, [])

    def test_unknown_sources_get_an_honest_generic_explanation(self):
        for source in (None, "", "legacy.unknown_source", "trading_bot."):
            with self.subTest(source=source):
                explanation = explain_error(source)
                for key in ("what", "why", "do"):
                    self.assertTrue(explanation[key].strip())
                self.assertEqual(explanation["context"], [])

    def test_safe_context_only_exposes_whitelisted_facts(self):
        now_ts = datetime.now(timezone.utc).timestamp() + 3600
        explanation = explain_error(
            "trading_bot.fetch_tickers",
            {
                "exchange": "bitunix",
                "market": "spot",
                "symbols": ["BTC/USDT", "ETH/USDT"],
                "retry_at": now_ts,
                "leaked": f"postgres://{PRIVATE_MARKER}",
                "message": f"Traceback {PRIVATE_MARKER}",
            },
        )
        rendered = " ".join(explanation["context"])
        self.assertIn("Bitunix · Spot", rendered)
        self.assertIn("BTC/USDT", rendered)
        self.assertIn("Anfragesperre", rendered)
        self.assertNotIn(PRIVATE_MARKER, rendered)

    def test_non_staff_error_log_shows_explanation_not_raw_diagnostics(self):
        self.client.force_login(self.user)
        retry_at = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        entry = ErrorLog.objects.create(
            configuration=self.config,
            severity="error",
            source="trading_bot.fetch_tickers",
            message=f"{PRIVATE_MARKER} postgres://db.internal.invalid",
            exception_type="MarketDataTimeoutError",
            details={"exchange": "bitunix", "market": "spot",
                     "symbols": ["BTC/USDT"], "retry_at": retry_at},
        )
        page = self.client.get(reverse("error_log"))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertContains(page, f"Referenz #{entry.id}")
        self.assertContains(page, "Kurse von der Exchange nicht abrufen")
        self.assertContains(page, "Bitunix · Spot")
        self.assertContains(page, "betroffene Symbole: BTC/USDT")
        self.assertContains(page, "Anfragesperre")
        self.assertNotIn(PRIVATE_MARKER, body)
        self.assertNotIn("MarketDataTimeoutError", body)

    def test_staff_error_log_still_shows_raw_diagnostics(self):
        self.client.force_login(self.user)
        entry = ErrorLog.objects.create(
            configuration=self.config,
            severity="error",
            source="trading_bot.fetch_tickers",
            message=f"{PRIVATE_MARKER} postgres://db.internal.invalid",
        )
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        page = self.client.get(reverse("error_log"))
        self.assertContains(page, PRIVATE_MARKER)
        self.assertContains(page, entry.message)

    def test_bot_status_api_maps_known_prefixes_to_safe_messages(self):
        self.client.force_login(self.user)
        cases = {
            "Konfiguration ungültig: Ungültige/nicht gelistete Symbole bei Bitunix: BTC/USDT":
                "Die Handelspaare sind bei Bitunix nicht gültig",
            "Marktdaten: Kursabruf von Bitunix für 2 Symbol(e) lief nach 0 s nicht zu Ende.":
                "Kursdaten von Bitunix sind derzeit nicht verfügbar",
            "DB offline: 1 Trade-Logs gepuffert; Marktdaten und Paper-Handel laufen weiter":
                "Datenbank vorübergehend nicht erreichbar",
            "Symbolverarbeitung BTC/USDT: Exchange lieferte keinen Ticker":
                "Die Kursdaten eines Symbols konnten im letzten Zyklus nicht verarbeitet werden",
            f"irgendwas {PRIVATE_MARKER}":
                "Bot-Fehler aufgetreten. Details im Fehler-Log (Referenznummer verwenden).",
        }
        for internal, expected in cases.items():
            with (
                self.subTest(prefix=internal.split(":")[0]),
                patch(
                    "trading.views.bot_manager.status",
                    return_value={"running": True, "last_error": internal,
                                  "last_error_at": 123},
                ),
            ):
                response = self.client.get(
                    reverse("bot_status_api"), {"config_id": self.config.id}
                )
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(PRIVATE_MARKER, response.content.decode())
            self.assertIn(expected, response.json()["last_error"])

    def test_error_log_annotated_context_reaches_the_view(self):
        self.client.force_login(self.user)
        ErrorLog.objects.create(
            configuration=self.config,
            severity="warning",
            source="views.config_activate.validation",
            message="intern",
            details={"exchange": "bitunix", "market": "spot",
                     "symbols": ["BTC/USDT"]},
        )
        page = self.client.get(reverse("error_log"))
        self.assertEqual(len(page.context["annotated_errors"]), 1)
        item = page.context["annotated_errors"][0]
        self.assertIn("Exchange", item["explanation"]["what"])
        self.assertTrue(item["explanation"]["context"])


class ErrorExplanationSourceScanTests(SimpleTestCase):
    def test_source_pattern_covers_known_sources(self):
        sources = _sources_in_codebase()
        for known in (
            "trading_bot.fetch_tickers",
            "trading_bot.validate_symbols",
            "trading_bot.process_symbol",
            "trading_bot.main_loop",
            "trading_bot.kill_switch",
            "trading_bot.restart",
            "views.config_activate.validation",
            "views.config_activate",
            "views.bot_status_api",
            "views.manual_sell",
            "views.kill_switch",
            "apps.autostart",
        ):
            with self.subTest(source=known):
                self.assertIn(known, sources)
