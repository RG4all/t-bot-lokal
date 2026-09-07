"""
Tests für die Content-Security-Policy (CSP) Header.

Stellt sicher dass CSP-Header korrekt gesetzt sind und keine
externen Domains erlaubt sind.

Basierend auf ARENA_AI_PROMPTS.md Prompt 3.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from trading_bot_project import settings


class CSPSettingsTest(TestCase):
    """Tests für die CSP-Konfiguration in settings.py."""

    def test_csp_default_src_only_self(self):
        """CSP_DEFAULT_SRC darf nur 'self' enthalten."""
        self.assertEqual(settings.CSP_DEFAULT_SRC, ("'self'",))

    def test_csp_script_src_only_self(self):
        """CSP_SCRIPT_SRC darf nur 'self' enthalten – keine externen Skripte."""
        self.assertEqual(settings.CSP_SCRIPT_SRC, ("'self'",))

    def test_csp_style_src_allows_unsafe_inline(self):
        """CSP_STYLE_SRC erlaubt 'self' und 'unsafe-inline' für Inline-Styles."""
        self.assertIn("'self'", settings.CSP_STYLE_SRC)
        self.assertIn("'unsafe-inline'", settings.CSP_STYLE_SRC)

    def test_csp_img_src_allows_data(self):
        """CSP_IMG_SRC erlaubt 'self' und data: URIs für Inline-Bilder."""
        self.assertIn("'self'", settings.CSP_IMG_SRC)
        self.assertIn("data:", settings.CSP_IMG_SRC)

    def test_csp_no_external_domains(self):
        """Keine externen Domains in irgendeiner CSP-Direktive erlaubt.

        Alles wird lokal aus /static/ geladen. Externe Domains könnten
        für XSS-Angriffe missbraucht werden.
        """
        all_directives = [
            settings.CSP_DEFAULT_SRC,
            settings.CSP_SCRIPT_SRC,
            settings.CSP_STYLE_SRC,
            settings.CSP_IMG_SRC,
            settings.CSP_FONT_SRC,
            settings.CSP_CONNECT_SRC,
            settings.CSP_FRAME_ANCESTORS,
            settings.CSP_BASE_URI,
            settings.CSP_FORM_ACTION,
        ]

        # Erlaubte Werte: 'self', 'unsafe-inline', 'data:', und leere Tupel
        allowed_values = {"'self'", "'unsafe-inline'", "data:"}

        for directive in all_directives:
            for value in directive:
                self.assertIn(
                    value,
                    allowed_values,
                    f"CSP-Direktive enthält unerwarteten Wert: {value}. "
                    f"Erwartet: {allowed_values}",
                )

    def test_csp_frame_ancestors_self(self):
        """CSP_FRAME_ANCESTORS verhindert Clickjacking."""
        self.assertEqual(settings.CSP_FRAME_ANCESTORS, ("'self'",))

    def test_csp_base_uri_self(self):
        """CSP_BASE_URI verhindert Base-Tag-Injection."""
        self.assertEqual(settings.CSP_BASE_URI, ("'self'",))

    def test_csp_form_action_self(self):
        """CSP_FORM_ACTION verhindert Form-Hijacking."""
        self.assertEqual(settings.CSP_FORM_ACTION, ("'self'",))


class CSPHeaderTest(TestCase):
    """Integrationstests die CSP-Header in HTTP-Responses prüfen."""

    def test_csp_header_present_on_health_endpoint(self):
        """CSP-Header sollte auf dem Health-Endpunkt vorhanden sein."""
        response = self.client.get("/health/")
        # CSP-Header kann entweder als Content-Security-Policy oder
        # Content-Security-Policy-Report-Only erscheinen
        csp_header = response.get("Content-Security-Policy", "")
        self.assertTrue(
            csp_header,
            "Content-Security-Policy Header fehlt in der Response. "
            "Prüfe ob CSPMiddleware korrekt registriert ist.",
        )

    def test_csp_script_src_restricts_to_self(self):
        """CSP-Header sollte script-src auf 'self' beschränken."""
        response = self.client.get("/health/")
        csp_header = response.get("Content-Security-Policy", "")
        self.assertIn("script-src 'self'", csp_header)

    def test_csp_no_external_script_domains(self):
        """CSP-Header sollte keine externen Script-Domains enthalten."""
        response = self.client.get("/health/")
        csp_header = response.get("Content-Security-Policy", "")
        # Bekannte externe Domains die NICHT erlaubt sein sollten
        external_domains = [
            "https:",
            "http:",
            "cdn.",
            "cdnjs.",
            "unpkg.",
            "jsdelivr.",
        ]
        for domain in external_domains:
            self.assertNotIn(
                domain,
                csp_header,
                f"CSP-Header enthält externe Domain: {domain}",
            )

    def test_csp_frame_ancestors_present(self):
        """CSP-Header sollte frame-ancestors 'self' enthalten."""
        response = self.client.get("/health/")
        csp_header = response.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'self'", csp_header)
