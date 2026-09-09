"""O6: /readyz/ unterscheidet "Prozess lebt" (health) von "Traffic bereit".

Ein Container mit lebendem Webprozess aber weggefallener Datenbank waere unter
/health/ "gruen", waehrend jede Nutzeraktion scheitert. Der Readiness-Check
haelt deshalb den Traffic zurueck (503 + Retry-After), bis ensure_connection
wieder durchlaeuft – ohne Interna preiszugeben und ohne den Gate-Redirect.
"""

from unittest.mock import patch

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings


class ReadinessViewTests(SimpleTestCase):
    def test_ready_returns_200_when_database_connectable(self):
        # SimpleTestCase stellt keine Test-DB bereit: die Verbindung wird
        # als erfolgreich gepatcht, der Handler-Pfad selbst ist der Pruefstand.
        with patch("trading.views.connection.ensure_connection", return_value=None):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ready")
        self.assertIn("version", payload)

    def test_database_error_maps_to_503_with_retry_after(self):
        with patch("trading.views.connection.ensure_connection", side_effect=OperationalError("down")):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "5")
        payload = response.json()
        self.assertEqual(payload, {"status": "unavailable"})
        # Diagnose bleibt im Log, nicht im Response-Body.
        self.assertNotIn("OperationalError", response.content.decode())

    @override_settings(PASSPHRASE_GATE_ENABLED=True, PASSPHRASE="readiness-test")
    def test_gate_does_not_shadow_readiness_status(self):
        """Vor der Freigabe darf /readyz/ nicht ins Gate umgeleitet werden."""
        with patch("trading.views.connection.ensure_connection", side_effect=OperationalError("down")):
            response = self.client.get("/readyz/")
        self.assertEqual(response.status_code, 503)
