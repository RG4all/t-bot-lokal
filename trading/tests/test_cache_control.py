"""Tests für die Cache-Control-Härtung der API-Endpunkte (``no_cache_json``).

Die API-Endpunkte liefern benutzerbezogene Handels-, Portfolio-, Log- und
Marktdaten. Ohne explizite Cache-Header können Browser und zwischengeschaltete
Proxies/CDNs diese Antworten zwischenspeichern und später einem anderen Nutzer
desselben Clients ausliefern. Der Decorator ``no_cache_json`` setzt deshalb
``Cache-Control: no-store, no-cache, must-revalidate, max-age=0`` sowie
``Pragma: no-cache`` auf jede von den Views erzeugte Antwort – auch auf
Fehlerantworten wie 400/503.

Die Quellcode-Tests prüfen bewusst die Datei ``trading/views.py``, damit eine
versehentliche Entfernung des Decorators oder einer Anwendung nicht unentdeckt
bleibt. Die Integrationstests laufen über den echten Middleware-Stack und
prüfen die ausgelieferten Header.

Basierend auf ARENA_AI_PROMPTS.md Prompt 8 und SECURITY_AUDIT.md Abschnitt 2.8.
"""

import re
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from trading import views
from trading.market_data import MarketDataError
from trading.models import Configuration

VIEWS_PATH = Path(views.__file__).resolve()

CACHE_CONTROL_VALUE = "no-store, no-cache, must-revalidate, max-age=0"

API_VIEWS = (
    "info_api",
    "bot_status_api",
    "logs_api",
    "data_logs_api",
    "trades_api",
    "symbol_suggestions_api",
    "market_opportunities_api",
    "backtesting_status_api",
    "backtesting_estimate_api",
    "server_resources_api",
)


def _decorators_above(source, view_name):
    """Liefert die Decorator-Zeilen unmittelbar über ``def <view_name>(``."""
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if re.match(rf"^def\s+{re.escape(view_name)}\s*\(", line):
            decorators = []
            cursor = index - 1
            while cursor >= 0 and (
                lines[cursor].lstrip().startswith("@")
                or not lines[cursor].strip()
                or lines[cursor].lstrip().startswith("#")
            ):
                if lines[cursor].lstrip().startswith("@"):
                    decorators.append(lines[cursor].strip())
                cursor -= 1
            return decorators
    raise AssertionError(f"View {view_name!r} nicht in views.py gefunden.")


class ApiCacheControlSourceTests(TestCase):
    """Statische Prüfungen des Decorators und seiner Anwendung im Quellcode."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = VIEWS_PATH.read_text(encoding="utf-8")

    def test_no_cache_json_decorator_exists(self):
        """Der Decorator muss im Quellcode definiert sein.

        Roher Test vor dem Fix: Es gab keinen ``no_cache_json``-Decorator und
        damit keinerlei Cache-Header auf den API-Endpunkten.
        """
        self.assertIn("def no_cache_json(view_func):", self.source)

    def test_decorator_sets_expected_header_values(self):
        """Die Header-Werte müssen explizit im Quellcode stehen."""
        self.assertIn(CACHE_CONTROL_VALUE, self.source)
        self.assertIn("response['Pragma'] = 'no-cache'", self.source)

    def test_decorator_is_applied_to_all_api_views(self):
        """Jede API-View muss mit @no_cache_json dekoriert sein.

        Roher Test vor dem Fix: Keine der Views trug den Decorator.
        """
        for view_name in API_VIEWS:
            with self.subTest(view=view_name):
                self.assertIn("@no_cache_json", _decorators_above(self.source, view_name))


@override_settings(
    PASSPHRASE_GATE_ENABLED=False,
    AUTOSTART_BOTS=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    BACKTEST_LOCAL_FALLBACK_ENABLED=True,
)
class ApiCacheControlHeaderTests(TestCase):
    """Header-Prüfungen über den echten Middleware-Stack."""

    def setUp(self):
        self.user = User.objects.create_user("cache-owner", password="owner-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Cache-Control config",
            symbols="BTC/USDT",
            countdown=0,
        )
        self.client.force_login(self.user)

    def assert_no_cache(self, response):
        self.assertEqual(response["Cache-Control"], CACHE_CONTROL_VALUE)
        self.assertEqual(response["Pragma"], "no-cache")

    def test_successful_api_responses_carry_no_cache_headers(self):
        """Alle API-Endpunkte liefern bei 200 die no-cache-Header aus.

        Angriffsszenario: Ein Browser oder Proxy speichert eine zuvor geladene
        Antwort (z. B. Portfoliostand, Trading-Log) und liefert sie später
        unabhängig vom Server-Zustand erneut aus. ``no-store`` verhindert genau
        dieses Zwischenspeichern sensibler Handelsdaten.
        """
        cases = {
            "info_api": self.client.get(reverse("info_api", args=[self.config.id])),
            "logs_api": self.client.get(reverse("logs_api", args=[self.config.id])),
            "bot_status_api": self.client.get(
                reverse("bot_status_api"), {"config_id": self.config.id}
            ),
            "data_logs_api": self.client.get(
                reverse("data_logs_api"),
                {"config_id": self.config.id, "symbol": "BTC/USDT"},
            ),
            "trades_api": self.client.get(
                reverse("trades_api"),
                {"config_id": self.config.id, "symbol": "BTC/USDT"},
            ),
            "backtesting_estimate_api": self.client.get(reverse("backtesting_estimate_api")),
            "backtesting_status_api": self.client.get(reverse("backtesting_status_api")),
            "server_resources_api": self.client.get(reverse("server_resources_api")),
        }
        for name, response in cases.items():
            with self.subTest(view=name):
                self.assertEqual(response.status_code, 200)
                self.assert_no_cache(response)

    def test_symbol_suggestions_api_carries_no_cache_headers(self):
        """Der Erfolgspfad der Symbolvorschläge liefert no-cache-Header."""
        with patch(
            "trading.views.get_available_symbols",
            return_value=("BTC/USDT", "ETH/USDT"),
        ):
            response = self.client.get(
                reverse("symbol_suggestions_api"),
                {"exchange": "binance", "market": "spot", "q": "BT"},
            )
        self.assertEqual(response.status_code, 200)
        self.assert_no_cache(response)

    def test_market_opportunities_api_carries_no_cache_headers(self):
        """Der Erfolgspfad der Marktchancen liefert no-cache-Header."""
        payload = {"gainers": ["BTC/USDT"], "losers": [], "exchanges": {}}
        with patch("trading.views.scan_market_opportunities", return_value=payload):
            response = self.client.get(
                reverse("market_opportunities_api"),
                {"exchange": "binance", "market": "spot"},
            )
        self.assertEqual(response.status_code, 200)
        self.assert_no_cache(response)

    def test_client_error_responses_carry_no_cache_headers(self):
        """Auch 400er-Fehlerantworten dürfen nicht gecacht werden.

        Der Decorator ist der innerste Decorator und erfasst damit jede von der
        View zurückgegebene Antwort, nicht nur Erfolgsfälle.
        """
        cases = {
            "symbol_suggestions_api_invalid_exchange": self.client.get(
                reverse("symbol_suggestions_api"), {"exchange": "invalid"}
            ),
            "market_opportunities_api_invalid_market": self.client.get(
                reverse("market_opportunities_api"), {"market": "invalid"}
            ),
            "data_logs_api_unknown_symbol": self.client.get(
                reverse("data_logs_api"),
                {"config_id": self.config.id, "symbol": "ETH/USDT"},
            ),
            "bot_status_api_missing_config": self.client.get(reverse("bot_status_api")),
        }
        for name, response in cases.items():
            with self.subTest(view=name):
                self.assertEqual(response.status_code, 400)
                self.assert_no_cache(response)

    def test_service_unavailable_response_carries_no_cache_headers(self):
        """Eine 503-Antwort (Marktdaten nicht erreichbar) wird nicht gecacht."""
        with patch(
            "trading.views.get_available_symbols",
            side_effect=MarketDataError("offline"),
        ):
            response = self.client.get(
                reverse("symbol_suggestions_api"),
                {"exchange": "binance", "market": "spot", "q": "BT"},
            )
        self.assertEqual(response.status_code, 503)
        self.assert_no_cache(response)

    def test_foreign_config_returns_404_without_leaking_data(self):
        """Fremde Config-IDs bleiben unsichtbar (404, eigentümerbezogen).

        Die Autorisierung über ``get_object_or_404`` greift unverändert; ein
        Angreifer kann über eine fremde Config-ID weder Daten lesen noch eine
        gecachte Kopie erzwingen. Die 404-Antwort selbst enthält keine
        sensiblen Handelsdaten.
        """
        response = self.client.get(reverse("info_api", args=[self.config.id + 9999]))
        self.assertEqual(response.status_code, 404)
