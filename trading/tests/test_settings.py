"""
Tests für die ALLOWED_HOSTS-Sicherheitskonfiguration.

Stellt sicher dass kein Wildcard (*) in ALLOWED_HOSTS verwendet wird
und dass nur explizite lokale Entwicklungshosts erlaubt sind.

Basierend auf docs/archive/ARENA_AI_PROMPTS_2026-09-07.md Prompt 2.
"""

from django.test import TestCase

from trading_bot_project import settings


class AllowedHostsTest(TestCase):
    """Tests für die ALLOWED_HOSTS-Konfiguration."""

    def test_no_wildcard_in_allowed_hosts(self):
        """ALLOWED_HOSTS darf keinen Wildcard (*) Eintrag enthalten.

        Ein Wildcard ermöglicht Host-Header-Injection, Cache-Poisoning
        und CSRF-Bypass. Dies ist ein kritischer Sicherheitsfehler.
        """
        self.assertNotIn("*", settings.ALLOWED_HOSTS)

    def test_localhost_allowed(self):
        """localhost muss in ALLOWED_HOSTS erlaubt sein."""
        self.assertIn("localhost", settings.ALLOWED_HOSTS)

    def test_127_00_01_allowed(self):
        """127.0.0.1 muss in ALLOWED_HOSTS erlaubt sein."""
        self.assertIn("127.0.0.1", settings.ALLOWED_HOSTS)

    def test_tbot_local_allowed(self):
        """tbot.local muss in ALLOWED_HOSTS erlaubt sein (lokaler Entwicklungshost)."""
        self.assertIn("tbot.local", settings.ALLOWED_HOSTS)

    def test_ipv6_loopback_allowed(self):
        """[::1] (IPv6 Loopback) muss in ALLOWED_HOSTS erlaubt sein."""
        self.assertIn("[::1]", settings.ALLOWED_HOSTS)

    def test_no_wildcard_even_in_debug(self):
        """Auch im DEBUG-Modus darf kein Wildcard verwendet werden.

        Das Defekt-Bild: ALLOWED_HOSTS.append("*") wurde durch
        explizite Hosts ersetzt. Dieser Test stellt sicher dass
        der Fix nicht versehentlich rückgängig gemacht wird.
        """
        # Prüfe den Quellcode von settings.py direkt
        settings_path = settings.__file__ if hasattr(settings, "__file__") else None
        if settings_path:
            with open(settings_path, "r") as f:
                content = f.read()
            # Sicherstellen dass kein ALLOWED_HOSTS.append("*") existiert
            self.assertNotIn(
                'ALLOWED_HOSTS.append("*")',
                content,
                "ALLOWED_HOSTS.append('*') darf nicht im Code vorhanden sein. "
                "Verwende explizite Hosts statt Wildcard.",
            )
