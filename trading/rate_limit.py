"""
IP-basiertes Rate-Limiting für Auth-Endpunkte.

Schützt Login, Passphrase-Gate und Registrierung vor Brute-Force-Angriffen
und Credential-Stuffing. Speichert Versuche in-memory – für Multi-Worker-
Setup sollte Redis verwendet werden.

Implementierung basierend auf ARENA_AI_PROMPTS.md Prompt 1.
"""

import logging
import threading
import time
from collections import defaultdict

from django.http import JsonResponse

logger = logging.getLogger("trading")


class RateLimitMiddleware:
    """IP-basiertes Rate-Limiting für Auth-Endpunkte.

    Schützt POST-Anfragen auf geschützten Pfaden vor Brute-Force-Angriffen.
    Speichert Versuche in-memory (thread-safe mit threading.Lock).

    Für Multi-Worker-Setup (z.B. Gunicorn mit mehreren Workers) sollte
    Redis oder ein anderes gemeinsames Backend verwendet werden.
    """

    # Klassen-Variablen für geteilte State über alle Middleware-Instanzen
    _attempts: dict[str, list[float]] = defaultdict(list)
    _lock = threading.Lock()

    # Konfiguration
    MAX_ATTEMPTS = 5  # Maximale Versuche pro IP
    WINDOW_SECONDS = 900  # 15 Minuten Zeitfenster
    PROTECTED_PATHS = (
        "/login/",
        "/gate/",
        "/register/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Verarbeitet eine Anfrage und wendet Rate-Limiting an.

        Nur POST-Anfragen auf geschützten Pfaden werden gezählt.
        GET-Anfragen werden niemals limitiert.
        """
        if request.method == "POST" and request.path in self.PROTECTED_PATHS:
            client_ip = self._get_client_ip(request)
            if self._is_rate_limited(client_ip):
                logger.warning(
                    "Rate-Limit überschritten für IP %s auf Pfad %s",
                    client_ip,
                    request.path,
                )
                return JsonResponse(
                    {
                        "error": "Zu viele Versuche. Bitte 15 Minuten warten.",
                    },
                    status=429,
                    headers={"Retry-After": str(self.WINDOW_SECONDS)},
                )
            self._record_attempt(client_ip)
        return self.get_response(request)

    def _is_rate_limited(self, ip: str) -> bool:
        """Prüft ob die IP das Rate-Limit überschritten hat.

        Entfernt automatisch abgelaufene Einträge aus dem Speicher.
        """
        now = time.time()
        with self._lock:
            # Alte Einträge die außerhalb des Zeitfensters liegen entfernen
            self._attempts[ip] = [
                t
                for t in self._attempts[ip]
                if now - t < self.WINDOW_SECONDS
            ]
            return len(self._attempts[ip]) >= self.MAX_ATTEMPTS

    def _record_attempt(self, ip: str) -> None:
        """Zeichnet einen fehlgeschlagenen Versuch auf."""
        with self._lock:
            self._attempts[ip].append(time.time())

    @staticmethod
    def _get_client_ip(request) -> str:
        """Extrahiert die Client-IP unter Berücksichtigung von Proxys.

        Unterstützt X-Forwarded-For Header für Reverse-Proxy-Setup.
        """
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            # X-Forwarded-For kann mehrere IPs enthalten (kommagetrennt)
            # Die erste IP ist der ursprüngliche Client
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "0.0.0.0")
