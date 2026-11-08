"""Regressionstests für die Chart-Einbindung im Dashboard (BUG-29).

Ausgangsbefund: Die Markt-Charts zeigten im Browser dauerhaft
"WebGL is not supported by your browser - visit https://get.webgl.org",
obwohl WebGL auf der Maschine verfügbar war (Brave und Firefox).

Ursache: Die Charts liefen ausschließlich als ``scattergl``-Traces. Plotly
prüft dafür über die gebündelte regl-Bibliothek zwei WebGL1-Extensions
(``ANGLE_instanced_arrays``, ``OES_element_index_uint``) und bricht ohne
Rückfall ab. Fehlen sie — etwa in Brave 1.93, das einem WebGL1-Kontext den
WebGL2-Extensionsatz liefert, oder in Umgebungen ohne GPU — ersetzt Plotly den
Chart durch seinen ``.no-webgl``-Hinweis.

Diese Tests sichern die Template-Seite des Fixes: Plotly-3-konforme
Titelangabe, SVG-Fallback samt Erkennung und der sichtbare Hinweis für
Nutzer. Das Renderverhalten selbst ist Browser-Verhalten und wird hier nicht
simuliert (Prüfgrenze).
"""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from trading.models import Configuration


@override_settings(PASSPHRASE_GATE_ENABLED=False, AUTOSTART_BOTS=False)
class DashboardChartRenderingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("chart-owner", password="chart-owner-password")
        self.config = Configuration.objects.create(
            user=self.user,
            name="Chart-Konfiguration",
            symbols="BTC/USDT,ETH/USDT",
            countdown=0,
        )
        self.client.force_login(self.user)

    def _dashboard(self) -> str:
        response = self.client.get(reverse("dashboard"), {"config_id": self.config.id})
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_chart_title_uses_plotly3_title_text(self):
        """Plotly 3 verwirft ``title: "<string>"`` still – der Chart bliebe namenlos."""
        html = self._dashboard()
        self.assertIn("title: {text: symbol}", html)
        self.assertNotIn("title: symbol", html)

    def test_chart_falls_back_to_svg_without_webgl(self):
        """Fehlt WebGL, rendert das Dashboard ``scatter`` statt ``scattergl``."""
        html = self._dashboard()
        self.assertIn("detectWebglTraces", html)
        self.assertIn("const type = traceType();", html)
        # Kein hart verdrahteter WebGL-Trace-Typ mehr in den Chart-Daten.
        self.assertNotIn('type: "scattergl"', html)

    def test_webgl_probe_matches_plotly_regl_lookups(self):
        """Die Vorabprüfung fragt exakt die Namen ab, die regl kleingeschrieben prüft.

        ``getExtension`` ist laut WebGL-Spezifikation case-insensitiv, reale
        Browser (Brave 1.93) weichen davon ab; deshalb muss die Abfrage
        zeichengleich mit der von regl sein, sonst erkennt das Dashboard
        WebGL als nutzbar und Plotly fällt danach doch aus.
        """
        html = self._dashboard()
        self.assertIn('"angle_instanced_arrays"', html)
        self.assertIn('"oes_element_index_uint"', html)

    def test_failed_webgl_context_downgrades_chart_at_runtime(self):
        """Ein späterer WebGL-Ausfall (Kontextlimit, GPU-Reset) wird abgefangen."""
        html = self._dashboard()
        self.assertIn("downgradeChartIfWebglFailed", html)
        self.assertIn('element.querySelector(".no-webgl")', html)

    def test_dashboard_explains_svg_fallback_to_user(self):
        """Nutzer sehen einen erklärenden Hinweis statt einer WebGL-Fehlermeldung."""
        html = self._dashboard()
        self.assertIn('id="webgl-fallback-hint"', html)
        self.assertIn("Vereinfachte Darstellung", html)
