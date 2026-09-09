"""W1: ungültige ``config_id``-Query-Parameter liefern 400/404 statt 500.

Reproduzierter Ausgangsstand (Code-Review 2026-09-09): Die Views
``dashboard_view``, ``data_logs_api``, ``trades_api`` und ``bot_status_api``
übergaben den Rohtext aus ``request.GET`` direkt an ``get_object_or_404``.
Nicht-numerische Werte erzeugten dort einen ``ValueError`` aus dem ORM
(„Field 'id' expected a number …“) und damit einen unbehandelten 500er –
bei einem reinen Eingabefehler. Die API-Antworten dokumentieren jetzt 400
(mit fester Meldung ohne interne Details), die HTML-View antwortet 404.
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from trading.models import Configuration


@override_settings(PASSPHRASE_GATE_ENABLED=False)
class ConfigIdValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("w1-user", password="w1-test-password")
        self.other = User.objects.create_user("w1-other", password="w1-test-password")
        self.config = Configuration.objects.create(
            user=self.user, name="W1", symbols="BTC/USDT", countdown=0
        )
        self.client.force_login(self.user)

    def test_api_endpoints_reject_non_numeric_config_id_with_400(self):
        for path in ("/api/data_logs/", "/api/trades/", "/api/bot/status/"):
            with self.subTest(path=path):
                response = self.client.get(path, {"config_id": "abc"})
                self.assertEqual(response.status_code, 400)
                payload = response.json()
                self.assertEqual(payload["error"], "config_id ist keine Zahl")
                # Keine ORM-/Feld-Diagnose nach außen.
                self.assertNotIn("expected a number", str(payload))

    def test_api_endpoints_reject_missing_config_id_with_400(self):
        for path in ("/api/data_logs/", "/api/trades/", "/api/bot/status/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "config_id fehlt")

    def test_dashboard_non_numeric_config_id_is_404_not_500(self):
        response = self.client.get("/dashboard/", {"config_id": "<script>"})
        self.assertEqual(response.status_code, 404)

    def test_unknown_numeric_config_id_still_404(self):
        response = self.client.get("/api/bot/status/", {"config_id": str(self.config.id + 9999)})
        self.assertEqual(response.status_code, 404)

    def test_valid_requests_keep_working(self):
        response = self.client.get("/api/bot/status/", {"config_id": str(self.config.id)})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("running", payload)
        response = self.client.get(
            "/api/data_logs/", {"config_id": str(self.config.id), "symbol": "BTC/USDT"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        response = self.client.get("/dashboard/", {"config_id": str(self.config.id)})
        self.assertEqual(response.status_code, 200)
