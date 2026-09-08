"""Regressionstests für die DB-Aggregation der info_api-Kennzahlen (Prompt 21).

Der Ausgangsstand (`info_api`) lud bis zu ``_MAX_LOG_ROWS`` (2.000) Logs in den
Speicher und durchlief sie in ``calculate_performance_metrics`` spaltenweise in
Python, um die Performance-Kennzahlen zu ermitteln. Bei großen Konfigurationen
ist das ein unnötiger RAM- und CPU-Aufwand im Web-Prozess.

Der Fix berechnet Zähler und Summen über ``django.db.models.aggregate()``
direkt im DBMS (``_calculate_metrics_from_db``); ``info_api`` ruft diese
Funktion statt ``calculate_performance_metrics`` auf. Die Equity-/Kassenkurve
benötigt weiterhin die einzelnen Zeilen, allein die Kennzahlen werden nicht
mehr in Python über alle Logs materialisiert.

Die Tests folgen dem Rot→Grün-Prinzip:

* Ein echter Negativtest patcht ``calculate_performance_metrics`` (die
  Python-Implementierung) so, dass sie kracht. Vor dem Fix rief ``info_api``
  diese Funktion auf und lieferte 500; nach dem Fix ignoriert ``info_api`` den
  Patch und liefert 200 – das beweist, dass die Metrik nicht mehr über den
  Python-Pfad berechnet wird.
* Verhaltensgleichheit: Die DB-Aggregation liefert exakt dieselben Werte wie
  ``calculate_performance_metrics`` über dasselbe Fenster, inklusive der
  Fensterbegrenzung auf die jüngsten ``_MAX_LOG_ROWS`` Logs.
* Angriffs-/Randvektoren: leere Historie, nur Käufe, nur Gewinn-Verkäufe, nur
  Verlust-Verkäufe und gemischte Verläufe – keine Division durch Null, keine
  ``None``-Werte in der Antwort.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from trading import views
from trading.models import Configuration, TradingLog

_PERF_KEYS = (
    "win_rate",
    "avg_profit",
    "total_wins",
    "total_losses",
    "avg_win",
    "avg_loss",
    "risk_reward",
    "profit_factor",
    "max_win",
    "max_loss",
)


@override_settings(
    PASSPHRASE_GATE_ENABLED=False,
    AUTOSTART_BOTS=False,
    CELERY_TASK_ALWAYS_EAGER=True,
    BACKTEST_LOCAL_FALLBACK_ENABLED=True,
)
class InfoApiMetricsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="owner-test-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Metrics config",
            symbols="BTC/USDT",
            countdown=0,
        )
        self.client.force_login(self.user)

    def _make_log(self, action, pl_nominal, when=None):
        """Legt einen TradingLog an; ``when`` setzt einen deterministischen Zeitstempel."""
        log = TradingLog.objects.create(
            configuration=self.config,
            symbol="BTC/USDT",
            action=action,
            price=Decimal(100),
            amount=Decimal("0.1"),
            fee_amount=Decimal("0.01"),
            pl_nominal=Decimal(pl_nominal),
            pl_relative=Decimal(0),
            total_pl=Decimal(pl_nominal),
            current_capital=Decimal(100),
            tank=Decimal(0),
            order_id="log",
        )
        if when is not None:
            TradingLog.objects.filter(id=log.id).update(timestamp=when)
        return log

    def _window_metrics_reference(self):
        """Python-Referenz: ``calculate_performance_metrics`` über das selbe Fenster."""
        window = views._latest_rows(self.config.logs.all(), views._MAX_LOG_ROWS)
        return views.calculate_performance_metrics(window)

    # ------------------------------------------------------------------ #
    # 1) Verhaltensgleichheit DB-Aggregation == Python-Referenz
    # ------------------------------------------------------------------ #
    def test_empty_history_yields_zero_metrics_without_error(self):
        metrics = views._calculate_metrics_from_db(self.config)
        self.assertEqual(metrics, {key: 0 for key in _PERF_KEYS})

    def test_db_aggregation_matches_python_reference_mixed(self):
        # 5 Gewinn-Verkäufe und 3 Verlust-Verkäufe, gemischt mit Käufen.
        base = timezone.now()
        for index, pl in enumerate((10, -4, 7, -2, 3, 0, -1, 5)):
            self._make_log("sell", pl, when=base + timezone.timedelta(minutes=index))
        for index in range(4):
            self._make_log("buy", 0, when=base + timezone.timedelta(minutes=100 + index))

        db_metrics = views._calculate_metrics_from_db(self.config)
        reference = self._window_metrics_reference()
        self.assertEqual(set(db_metrics), set(_PERF_KEYS))
        for key in _PERF_KEYS:
            with self.subTest(metric=key):
                self.assertEqual(db_metrics[key], reference[key])

    def test_db_aggregation_all_wins(self):
        base = timezone.now()
        for index in range(6):
            self._make_log("sell", index + 1, when=base + timezone.timedelta(minutes=index))
        db_metrics = views._calculate_metrics_from_db(self.config)
        reference = self._window_metrics_reference()
        self.assertEqual(db_metrics, reference)
        self.assertEqual(db_metrics["total_wins"], 6)
        self.assertEqual(db_metrics["total_losses"], 0)
        self.assertEqual(db_metrics["win_rate"], 100.0)
        # Keine Verluste -> risk_reward und profit_factor sind 0 (keine Div/0).
        self.assertEqual(db_metrics["risk_reward"], 0)
        self.assertEqual(db_metrics["profit_factor"], 0)

    def test_db_aggregation_all_losses(self):
        base = timezone.now()
        for index in range(5):
            self._make_log("sell", -(index + 1), when=base + timezone.timedelta(minutes=index))
        db_metrics = views._calculate_metrics_from_db(self.config)
        reference = self._window_metrics_reference()
        self.assertEqual(db_metrics, reference)
        self.assertEqual(db_metrics["total_wins"], 0)
        self.assertEqual(db_metrics["total_losses"], 5)
        self.assertEqual(db_metrics["win_rate"], 0.0)
        # Keine Gewinne -> risk_reward und profit_factor sind 0 (keine Div/0).
        self.assertEqual(db_metrics["risk_reward"], 0)
        self.assertEqual(db_metrics["profit_factor"], 0)

    def test_only_buys_yields_zero_metrics(self):
        base = timezone.now()
        for index in range(10):
            self._make_log("buy", 0, when=base + timezone.timedelta(minutes=index))
        db_metrics = views._calculate_metrics_from_db(self.config)
        self.assertEqual(db_metrics, {key: 0 for key in _PERF_KEYS})

    # ------------------------------------------------------------------ #
    # 2) Fensterbegrenzung: aggregate() wertet das LIMIT aus
    # ------------------------------------------------------------------ #
    def test_metrics_are_window_bounded_not_full_history(self):
        """Nur die jüngsten ``_MAX_LOG_ROWS`` Logs dürfen zählen.

        Die ältesten 600 Logs sind alles Gewinne, die jüngsten 2000 alles
        Verluste. Die Fenster-Kennzahlen müssen ``win_rate == 0`` ergeben und
        dürfen nicht der Gesamthistorie (600 Gewinne + 2000 Verluste)
        entsprechen.
        """
        old = timezone.now() - timezone.timedelta(days=10)
        recent = timezone.now()
        for index in range(600):
            self._make_log("sell", 10, when=old + timezone.timedelta(minutes=index))
        for index in range(2000):
            self._make_log("sell", -5, when=recent + timezone.timedelta(minutes=index))

        db_metrics = views._calculate_metrics_from_db(self.config, views._MAX_LOG_ROWS)
        window_reference = self._window_metrics_reference()
        # DB-Aggregation stimmt mit der Fenster-Referenz überein.
        self.assertEqual(db_metrics, window_reference)
        self.assertEqual(db_metrics["win_rate"], 0)
        self.assertEqual(db_metrics["total_wins"], 0)
        self.assertEqual(db_metrics["total_losses"], 2000)

        # Gesamthistorie dagegen enthält 600 Gewinne – ein anderes Ergebnis.
        full_history = views.calculate_performance_metrics(
            list(self.config.logs.all().order_by("timestamp", "id"))
        )
        self.assertNotEqual(db_metrics["win_rate"], full_history["win_rate"])
        self.assertGreater(full_history["total_wins"], 0)

    def test_custom_limit_is_respected(self):
        base = timezone.now()
        # 50 alte Gewinne, 50 neue Verluste -> Limit=50 schneidet die Gewinne ab.
        for index in range(50):
            self._make_log("sell", 10, when=base - timezone.timedelta(minutes=1000 - index))
        for index in range(50):
            self._make_log("sell", -5, when=base + timezone.timedelta(minutes=index))
        db_metrics = views._calculate_metrics_from_db(self.config, limit=50)
        self.assertEqual(db_metrics["total_wins"], 0)
        self.assertEqual(db_metrics["total_losses"], 50)
        self.assertEqual(db_metrics["win_rate"], 0)

    # ------------------------------------------------------------------ #
    # 3) Negativtest (Rot->Grün): info_api nutzt nicht mehr den Python-Pfad
    # ------------------------------------------------------------------ #
    def test_info_api_no_longer_uses_python_metric_function(self):
        """``calculate_performance_metrics`` wird von ``info_api`` nicht mehr aufgerufen.

        Vor dem Fix rief ``info_api`` diese Funktion auf; ein Patchen auf einen
        Abbruch führte zu HTTP 500. Nach dem Fix kommt die Metrik aus der
        DB-Aggregation, der Patch ist wirkungslos und die Antwort ist 200.
        """
        base = timezone.now()
        for index, pl in enumerate((10, -4, 7)):
            self._make_log("sell", pl, when=base + timezone.timedelta(minutes=index))

        with patch.object(views, "calculate_performance_metrics", side_effect=RuntimeError("must not run")):
            response = self.client.get(reverse("info_api", args=[self.config.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Die gelieferten Kennzahlen müssen mit der DB-Aggregation übereinstimmen.
        reference = views._calculate_metrics_from_db(self.config)
        for key in _PERF_KEYS:
            with self.subTest(metric=key):
                self.assertEqual(payload["metrics"][key], reference[key])

    # ------------------------------------------------------------------ #
    # 4) info_api liefert dieselben Kennzahlen wie vor dem Fix
    # ------------------------------------------------------------------ #
    def test_info_api_metrics_equal_window_reference(self):
        base = timezone.now()
        pls = (12, -3, 8, -1, 4, 0, -2, 6, -5, 9)
        for index, pl in enumerate(pls):
            self._make_log("sell", pl, when=base + timezone.timedelta(minutes=index))
        for index in range(3):
            self._make_log("buy", 0, when=base + timezone.timedelta(minutes=100 + index))

        response = self.client.get(reverse("info_api", args=[self.config.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        reference = self._window_metrics_reference()

        self.assertEqual(payload["win_rate"], reference["win_rate"])
        self.assertEqual(payload["avg_profit_trade"], reference["avg_profit"])
        self.assertEqual(payload["avg_win"], reference["avg_win"])
        self.assertEqual(payload["avg_loss"], reference["avg_loss"])
        self.assertEqual(payload["risk_reward"], reference["risk_reward"])
        self.assertEqual(payload["profit_factor"], reference["profit_factor"])
        self.assertEqual(payload["biggest_win"], reference["max_win"])
        self.assertEqual(payload["biggest_loss"], reference["max_loss"])
        self.assertEqual(payload["profitable_sells"], reference["total_wins"])
        self.assertEqual(payload["unprofitable_sells"], reference["total_losses"])
        # Die verschachtelte ``metrics``-Struktur bleibt in Form und Inhalt identisch.
        self.assertEqual(payload["metrics"], reference)

    def test_info_api_buy_sell_counts_match_window(self):
        base = timezone.now()
        for index in range(30):
            action = "buy" if index % 3 == 0 else "sell"
            pl = 5 if action == "sell" and index % 2 == 0 else -2
            self._make_log(action, pl, when=base + timezone.timedelta(minutes=index))

        response = self.client.get(reverse("info_api", args=[self.config.id]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        window = views._latest_rows(self.config.logs.all(), views._MAX_LOG_ROWS)
        self.assertEqual(payload["buy_orders"], sum(log.action == "buy" for log in window))
        self.assertEqual(payload["sell_orders"], sum(log.action == "sell" for log in window))
