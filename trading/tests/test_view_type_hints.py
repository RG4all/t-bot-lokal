"""CODE-19: Type-Hints, Docstrings und die dabei geschlossenen Lücken in ``views.py``.

Der Ausgangsbefund (Prompt 19 / Security-Audit §4.4) ist Tech Debt: Ohne
Annotationen findet keine statische Prüfung die Views-Signaturen, und ein
falscher Rückgabetyp oder ein anonymer Benutzer in einem ORM-Filter fällt erst
zur Laufzeit auf.

Drei Testebenen:

1. **Signatur- und Docstring-Prüfung** über ``ast``/``inspect`` – erkennt
   entfernte Annotationen sofort und ist unabhängig davon, ob mypy im
   jeweiligen Prüfsystem installiert ist.
2. **Verhaltenstests** der beim Annotieren geschlossenen Pfade: Die
   Benutzerauflösung ``_authenticated_user`` weist anonyme Requests ab, statt
   mit ``AnonymousUser`` weiterzufiltern, und das Gate akzeptiert keine leere
   Passphrase.
3. **Angriffsnahe Regressionen**: fremde Konfigurationen bleiben unerreichbar,
   defekte Equity-Punkte kippen die Reportausgabe nicht.
"""

import ast
import inspect
import subprocess
import sys
import typing
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import PermissionDenied
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from trading import views
from trading.models import Configuration, TradingLog

VIEWS_PATH = Path(views.__file__).resolve()
REPO_ROOT = VIEWS_PATH.parent.parent

# Die im Auftrag ausdrücklich geforderten Signaturen (Prompt 19).
REQUIRED_SIGNATURES: dict[str, tuple[dict[str, object], object]] = {
    "_portfolio_snapshot": ({"config": Configuration}, dict[str, typing.Any]),
    "_realized_profit": ({"config": Configuration}, Decimal),
    "_cash_flow": ({"log": TradingLog}, Decimal),
    "_cash_series": (
        {"logs": list[TradingLog], "opening_cash": Decimal},
        list[dict[str, typing.Any]],
    ),
    "calculate_performance_metrics": ({"logs": list[TradingLog]}, dict[str, float]),
    "health_view": ({"request": HttpRequest}, JsonResponse),
    "home": ({"request": HttpRequest}, HttpResponseRedirect),
    "login_view": ({"request": HttpRequest}, HttpResponse),
    "config_view": ({"request": HttpRequest}, HttpResponse),
    "dashboard_view": ({"request": HttpRequest}, HttpResponse),
}

# Öffentliche Views: Type-Hints und Docstring sind Pflicht.
PUBLIC_VIEWS = (
    "health_view",
    "help_view",
    "home",
    "passphrase_gate_view",
    "register_view",
    "login_view",
    "logout_view",
    "config_view",
    "config_list_view",
    "config_edit_view",
    "config_activate",
    "config_deactivate",
    "config_delete",
    "dashboard_view",
    "reset_log",
    "symbol_suggestions_api",
    "market_opportunities_api",
    "server_resources_api",
    "backtesting_estimate_api",
    "data_logs_api",
    "trades_api",
    "info_api",
    "bot_status_api",
    "logs_api",
    "manual_sell_view",
    "kill_switch_view",
    "error_log_view",
    "error_log_resolve",
    "generate_report",
    "generate_report_html",
    "generate_report_csv",
    "backtesting_status_api",
    "backtesting_index",
    "backtesting_form",
    "control_backtest",
    "generate_backtest_pdf",
    "generate_backtest_html",
    "generate_backtest_csv",
    "analyse_view",
)

# Interne Hilfsfunktionen, die ebenfalls vollständig annotiert sein müssen.
HELPER_FUNCTIONS = (
    "_record_view_error",
    "_authenticated_user",
    "_safe_next_url",
    "_redirect_dashboard",
    "_latest_rows",
    "_symbols",
    "_realized_profit",
    "_cash_flow",
    "_portfolio_snapshot",
    "_cash_series",
    "calculate_performance_metrics",
    "_render_manual",
    "_clear_manual_cache",
    "_validated_start_time",
    "_figure_to_base64",
    "_pdf_response",
    "_report_filename",
    "_build_report_context",
    "_combination_count",
    "_equity_svg",
    "_backtest_result_rows",
    "_backtest_report_context",
    "_owned_backtest",
)


def _module_functions() -> dict[str, ast.FunctionDef]:
    """Liest alle Top-Level-Funktionen aus dem Quelltext von ``views.py``."""
    tree = ast.parse(VIEWS_PATH.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


class ViewSignatureAnnotationTests(SimpleTestCase):
    """Statische Prüfung der Annotationen; unabhängig von einer mypy-Installation."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.functions = _module_functions()

    def test_typing_import_present(self) -> None:
        """``typing.Any`` muss importiert sein (Validierungskriterium Prompt 19).

        Roter Test vor dem Fix: ``views.py`` importierte kein ``typing``.
        """
        source = VIEWS_PATH.read_text(encoding="utf-8")
        self.assertIn("from typing import Any", source)

    def test_required_signatures_match_prompt(self) -> None:
        """Die geforderten Funktionen tragen exakt die verlangten Typen.

        Geprüft werden die zur Laufzeit aufgelösten Annotationen, nicht der
        Quelltext: So fällt auch eine syntaktisch gültige, aber falsche
        Annotation auf.
        """
        for name, (parameters, return_type) in REQUIRED_SIGNATURES.items():
            with self.subTest(function=name):
                function = getattr(views, name)
                hints = typing.get_type_hints(inspect.unwrap(function))
                self.assertEqual(
                    hints.get("return"),
                    return_type,
                    f"{name} muss {return_type} zurückgeben.",
                )
                for parameter, expected in parameters.items():
                    self.assertEqual(
                        hints.get(parameter),
                        expected,
                        f"{name}({parameter}) muss als {expected} annotiert sein.",
                    )

    def test_all_public_views_are_fully_annotated(self) -> None:
        """Jede öffentliche View annotiert alle Parameter und den Rückgabewert."""
        for name in PUBLIC_VIEWS:
            with self.subTest(view=name):
                node = self.functions.get(name)
                self.assertIsNotNone(node, f"View {name} fehlt in views.py.")
                self.assertIsNotNone(node.returns, f"{name} hat keinen Rückgabetyp.")
                for argument in node.args.args + node.args.kwonlyargs:
                    self.assertIsNotNone(
                        argument.annotation,
                        f"{name}({argument.arg}) ist nicht annotiert.",
                    )

    def test_all_helper_functions_are_fully_annotated(self) -> None:
        """Auch die internen Hilfsfunktionen sind vollständig annotiert."""
        for name in HELPER_FUNCTIONS:
            with self.subTest(function=name):
                node = self.functions.get(name)
                self.assertIsNotNone(node, f"Hilfsfunktion {name} fehlt in views.py.")
                self.assertIsNotNone(node.returns, f"{name} hat keinen Rückgabetyp.")
                for argument in node.args.args + node.args.kwonlyargs:
                    self.assertIsNotNone(
                        argument.annotation,
                        f"{name}({argument.arg}) ist nicht annotiert.",
                    )

    def test_no_top_level_function_is_left_unannotated(self) -> None:
        """Keine Top-Level-Funktion in views.py bleibt ohne Rückgabetyp.

        Fängt neu hinzugefügte Views ab, die in den Listen oben fehlen.
        """
        missing = [
            name
            for name, node in self.functions.items()
            if node.returns is None
            or any(
                argument.annotation is None
                for argument in node.args.args + node.args.kwonlyargs
            )
        ]
        self.assertEqual(missing, [], f"Unannotierte Funktionen in views.py: {missing}")

    def test_request_parameter_is_typed_as_httprequest(self) -> None:
        """Views verwenden die Django-typische Signatur ``request: HttpRequest``."""
        for name in PUBLIC_VIEWS:
            with self.subTest(view=name):
                node = self.functions[name]
                first = node.args.args[0]
                self.assertEqual(first.arg, "request")
                self.assertEqual(ast.unparse(first.annotation), "HttpRequest")

    def test_response_annotations_match_runtime_types(self) -> None:
        """Angekündigte Response-Typen sind die tatsächlich möglichen Klassen."""
        allowed = (
            HttpResponse,
            HttpResponseRedirect,
            JsonResponse,
            StreamingHttpResponse,
        )
        for name in PUBLIC_VIEWS:
            with self.subTest(view=name):
                hints = typing.get_type_hints(inspect.unwrap(getattr(views, name)))
                self.assertIn(hints.get("return"), allowed)


class ViewDocstringTests(SimpleTestCase):
    """Docstring-Pflicht für die öffentlichen Views."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.functions = _module_functions()

    def test_every_public_view_has_a_docstring(self) -> None:
        """Roter Test vor dem Fix: Fast alle Views hatten keinen Docstring."""
        for name in PUBLIC_VIEWS:
            with self.subTest(view=name):
                docstring = ast.get_docstring(self.functions[name])
                self.assertTrue(docstring, f"{name} braucht einen Docstring.")
                self.assertGreaterEqual(
                    len(docstring.strip()),
                    20,
                    f"Docstring von {name} ist zu knapp, um den Zweck zu erklären.",
                )

    def test_portfolio_snapshot_documents_returned_keys(self) -> None:
        """Der Snapshot-Docstring nennt jeden zurückgegebenen Schlüssel.

        authoritativer Ort der Schluesseldoku ist der Builder; die
        cache-wickelnde ``_portfolio_snapshot`` verweist nur auf ihn.
        """
        docstring = inspect.getdoc(views._compute_portfolio_snapshot) or ""
        for key in (
            "cash",
            "realized_profit",
            "invested",
            "market_value",
            "equity",
            "unrealized_profit",
            "positions",
        ):
            with self.subTest(key=key):
                self.assertIn(key, docstring)

    def test_helper_functions_are_documented(self) -> None:
        """Auch die annotierten Hilfsfunktionen erklären ihren Zweck."""
        for name in HELPER_FUNCTIONS:
            with self.subTest(function=name):
                self.assertTrue(
                    ast.get_docstring(self.functions[name]),
                    f"Hilfsfunktion {name} braucht einen Docstring.",
                )


class AuthenticatedUserResolutionTests(TestCase):
    """``_authenticated_user`` ersetzt rohe ``request.user``-Filter."""

    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_returns_the_logged_in_user(self) -> None:
        """Der angemeldete Benutzer wird unverändert zurückgegeben."""
        user = User.objects.create_user("hints-owner", password="owner-test-password")
        request = self.factory.get("/dashboard/")
        request.user = user
        self.assertIs(views._authenticated_user(request), user)

    def test_anonymous_request_is_rejected(self) -> None:
        """Ein anonymer Request wird abgewiesen, statt weiterzufiltern.

        Angriffsvektor: Fällt ``@login_required`` bei einer späteren Änderung
        weg, würde ``Configuration.objects.filter(user=AnonymousUser())``
        andernfalls ungeprüft in die Datenbank laufen.
        """
        request = self.factory.get("/dashboard/")
        request.user = AnonymousUser()
        with self.assertRaises(PermissionDenied):
            views._authenticated_user(request)

    def test_no_view_filters_on_raw_request_user(self) -> None:
        """Kein ORM-Filter verwendet mehr direkt ``request.user``.

        Roter Test vor dem Fix: ``views.py`` filterte an 26 Stellen auf dem
        untypisierten ``request.user``.
        """
        source = VIEWS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("user=request.user", source)
        self.assertNotIn("configuration__user=request.user", source)


@override_settings(PASSPHRASE_GATE_ENABLED=True, AUTOSTART_BOTS=False)
class PassphraseGateEmptySecretTests(TestCase):
    """Beim Annotieren geschlossene Lücke: leeres Gate-Secret."""

    def test_empty_passphrase_setting_does_not_open_the_gate(self) -> None:
        """Ein leeres Secret darf keine Freigabe erzeugen.

        Roter Test vor dem Fix: ``constant_time_compare("", "")`` ist ``True``
        – eine leere ``PASSPHRASE`` hätte das Gate mit leerer Eingabe geöffnet.
        Die Settings erzwingen ein Secret; der Guard sichert den Pfad zusätzlich
        gegen eine spätere Fehlkonfiguration ab.
        """
        with override_settings(PASSPHRASE=""):
            response = self.client.post(reverse("passphrase_gate"), {"passphrase": ""})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("passphrase_verified", self.client.session)

    def test_correct_passphrase_still_opens_the_gate(self) -> None:
        """Die reguläre Freigabe funktioniert unverändert."""
        with override_settings(PASSPHRASE="gate-test-secret"):
            response = self.client.post(
                reverse("passphrase_gate"), {"passphrase": "gate-test-secret"}
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("passphrase_verified", self.client.session)


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class AnnotatedViewBehaviourTests(TestCase):
    """Die annotierten Views liefern die angekündigten Typen und bleiben abgesichert."""

    def setUp(self) -> None:
        self.owner = User.objects.create_user("hints-user", password="owner-test-password")
        self.attacker = User.objects.create_user("hints-other", password="other-test-password")
        self.config = Configuration.objects.create(
            user=self.owner,
            name="Hints Config",
            exchange="binance",
            market="spot",
            symbols="BTC/USDT",
            start_capital=Decimal(1000),
            trade_amount=Decimal(100),
            take_profit=Decimal(1),
            stop_loss=Decimal(1),
            fee=Decimal("0.1"),
        )

    def test_health_view_returns_jsonresponse_with_version(self) -> None:
        """``health_view`` liefert wie annotiert eine ``JsonResponse``."""
        response = self.client.get(reverse("health"))
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.json()["version"], settings.APP_VERSION)

    def test_home_redirects_for_anonymous_and_authenticated_users(self) -> None:
        """``home`` liefert in beiden Fällen eine Weiterleitung."""
        anonymous = self.client.get(reverse("home"))
        self.assertIsInstance(anonymous, HttpResponseRedirect)
        self.client.force_login(self.owner)
        authenticated = self.client.get(reverse("home"))
        self.assertIsInstance(authenticated, HttpResponseRedirect)
        self.assertIn(reverse("dashboard"), authenticated["Location"])

    def test_dashboard_renders_for_owner(self) -> None:
        """Das Dashboard des Eigentümers wird weiterhin gerendert."""
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard"), {"config_id": self.config.id})
        self.assertEqual(response.status_code, 200)

    def test_dashboard_of_foreign_configuration_stays_unreachable(self) -> None:
        """Angriffsvektor: fremde ``config_id`` bleibt ein 404."""
        self.client.force_login(self.attacker)
        response = self.client.get(reverse("dashboard"), {"config_id": self.config.id})
        self.assertEqual(response.status_code, 404)

    def test_info_api_of_foreign_configuration_stays_unreachable(self) -> None:
        """Angriffsvektor: die JSON-API gibt keine fremden Portfoliodaten preis."""
        self.client.force_login(self.attacker)
        response = self.client.get(reverse("info_api", args=[self.config.id]))
        self.assertEqual(response.status_code, 404)

    def test_dashboard_without_configuration_renders_empty_state(self) -> None:
        """Ohne Konfiguration greift der ``Configuration | None``-Zweig."""
        self.client.force_login(self.attacker)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["config"])

    def test_report_csv_streams(self) -> None:
        """``generate_report_csv`` liefert wie annotiert eine Streaming-Antwort."""
        self.client.force_login(self.owner)
        response = self.client.get(reverse("generate_report_csv", args=[self.config.id]))
        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertEqual(response.status_code, 200)

    def test_portfolio_snapshot_returns_documented_keys(self) -> None:
        """Der Snapshot liefert genau die dokumentierten Schlüssel."""
        snapshot = views._portfolio_snapshot(self.config)
        self.assertEqual(
            set(snapshot),
            {
                "cash",
                "realized_profit",
                "invested",
                "market_value",
                "equity",
                "unrealized_profit",
                "positions",
            },
        )
        self.assertIsInstance(snapshot["cash"], Decimal)
        self.assertIsInstance(snapshot["positions"], list)

    def test_cash_flow_and_metrics_match_annotated_types(self) -> None:
        """``_cash_flow`` liefert ``Decimal``, die Kennzahlen sind Zahlen."""
        log = TradingLog.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            action="sell",
            price=Decimal(100),
            amount=Decimal(1),
            fee_amount=Decimal("0.1"),
            pl_nominal=Decimal(5),
            pl_relative=Decimal(5),
            total_pl=Decimal(5),
            current_capital=Decimal(1005),
            tank=Decimal(5),
            order_id="hints-1",
        )
        self.assertIsInstance(views._cash_flow(log), Decimal)
        self.assertIsInstance(views._realized_profit(self.config), Decimal)
        metrics = views.calculate_performance_metrics([log])
        self.assertEqual(set(metrics) >= {"win_rate", "profit_factor"}, True)
        for key, value in metrics.items():
            with self.subTest(metric=key):
                self.assertIsInstance(value, (int, float))

    def test_cash_series_returns_documented_shape(self) -> None:
        """``_cash_series`` liefert ``{"t": str, "v": float}``-Punkte."""
        log = TradingLog.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            action="buy",
            price=Decimal(100),
            amount=Decimal(1),
            fee_amount=Decimal("0.1"),
            pl_nominal=Decimal(0),
            pl_relative=Decimal(0),
            total_pl=Decimal(0),
            current_capital=Decimal(900),
            tank=Decimal(0),
            order_id="hints-2",
        )
        series = views._cash_series([log], Decimal(1000))
        self.assertEqual(len(series), 1)
        self.assertIsInstance(series[0]["t"], str)
        self.assertIsInstance(series[0]["v"], float)


class EquitySvgRobustnessTests(SimpleTestCase):
    """Der beim Annotieren gefundene ``None``-Pfad in ``_equity_svg``."""

    def test_missing_equity_value_is_skipped(self) -> None:
        """Roter Test vor dem Fix: ``float(None)`` warf einen ``TypeError``.

        Der frühere ``except``-Block fing ihn zwar ab, verwarf damit aber jeden
        Punkt erst nach der Ausnahme. Jetzt wird der defekte Punkt bewusst
        übersprungen und die Kurve trotzdem gezeichnet.
        """
        svg = views._equity_svg(
            "BTC/USDT",
            [
                {"equity": None, "index": 0},
                {"equity": 100.0, "index": 1},
                {"equity": 110.0, "index": 2},
            ],
        )
        self.assertIn("<svg", svg)
        self.assertIn("polyline", svg)

    def test_empty_curve_returns_empty_string(self) -> None:
        """Ohne verwertbare Punkte entsteht kein SVG."""
        self.assertEqual(views._equity_svg("BTC/USDT", []), "")
        self.assertEqual(views._equity_svg("BTC/USDT", None), "")
        self.assertEqual(views._equity_svg("BTC/USDT", [{"equity": None}]), "")

    def test_symbol_is_escaped_in_the_label(self) -> None:
        """Angriffsvektor: ein Symbol darf kein Markup in das SVG einschleusen."""
        svg = views._equity_svg(
            '"><script>alert(1)</script>',
            [{"equity": 1.0, "index": 0}, {"equity": 2.0, "index": 1}],
        )
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)


class MypyConfigurationTests(SimpleTestCase):
    """Die Typprüfung ist reproduzierbar konfiguriert und läuft, wenn mypy da ist."""

    def test_pyproject_declares_mypy_configuration(self) -> None:
        """``pyproject.toml`` pinnt Ziel, Plugin und Settings-Modul."""
        content = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.mypy]", content)
        self.assertIn("mypy_django_plugin.main", content)
        self.assertIn('django_settings_module = "trading_bot_project.settings"', content)
        self.assertIn('files = ["trading/views.py"]', content)

    def test_mypy_reports_no_error_for_views(self) -> None:
        """mypy meldet keine Fehler in ``views.py`` (übersprungen ohne mypy).

        mypy ist ein reines Entwicklungswerkzeug und bewusst nicht in
        ``requirements.txt``; der Test überspringt sich, wenn es fehlt.
        """
        try:
            import mypy  # noqa: F401
            import mypy_django_plugin  # noqa: F401
        except ImportError:  # pragma: no cover - abhängig von der Umgebung
            self.skipTest("mypy/django-stubs nicht installiert.")
        result = subprocess.run(
            [sys.executable, "-m", "mypy"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"mypy meldet Fehler:\n{result.stdout}\n{result.stderr}",
        )
