"""Begrenztes, prozesslokales Rate-Limit für POSTs auf Auth-Endpunkte.

Für mehrere Web-Prozesse/Instanzen zusätzlich ein gemeinsames Limit am
Reverse-Proxy oder in einem geteilten Backend einsetzen. Forwarded-Header
werden nur von explizit konfigurierten, vertrauenswürdigen Proxys akzeptiert.
"""

import logging
import threading
import time
from collections import OrderedDict
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse

logger = logging.getLogger("trading")


class RateLimitMiddleware:
    """Zählt alle Auth-POSTs atomar, auch erfolgreiche Versuche."""

    _attempts: OrderedDict[str, list[float]] = OrderedDict()
    _lock = threading.Lock()

    MAX_ATTEMPTS = 5
    WINDOW_SECONDS = 900
    MAX_TRACKED_IPS = 10_000
    PROTECTED_PATHS = ("/login/", "/gate/", "/register/", "/admin/login/")

    def __init__(self, get_response):
        self.get_response = get_response
        try:
            self._trusted_proxies = tuple(
                ip_network(network.strip(), strict=False)
                for network in getattr(settings, "RATE_LIMIT_TRUSTED_PROXIES", ())
            )
        except ValueError as exc:
            raise ImproperlyConfigured(
                "RATE_LIMIT_TRUSTED_PROXIES must contain IP addresses or CIDR networks."
            ) from exc

    def __call__(self, request):
        if (
            request.method == "POST"
            and request.path_info in self.PROTECTED_PATHS
            and self._consume_attempt(self._get_client_ip(request))
        ):
            logger.warning("Auth-Rate-Limit erreicht auf Pfad %s", request.path_info)
            return JsonResponse(
                {"error": "Zu viele Versuche. Bitte 15 Minuten warten."},
                status=429,
                headers={"Retry-After": str(self.WINDOW_SECONDS)},
            )
        return self.get_response(request)

    def _consume_attempt(self, ip):
        """Prüft und reserviert unter demselben Lock; bei vollem Speicher sperren."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.WINDOW_SECONDS
            # Nach letztem akzeptierten Versuch sortiert: auch inaktive IPs
            # werden entfernt, ohne bei jedem Request die gesamte Map zu scannen.
            while self._attempts:
                oldest = next(iter(self._attempts))
                if self._attempts[oldest][-1] > cutoff:
                    break
                self._attempts.popitem(last=False)

            attempts = [t for t in self._attempts.get(ip, ()) if t > cutoff]
            if len(attempts) >= self.MAX_ATTEMPTS:
                return True
            if ip not in self._attempts and len(self._attempts) >= self.MAX_TRACKED_IPS:
                return True
            self._attempts[ip] = [*attempts, now]
            self._attempts.move_to_end(ip)
            return False

    def _get_client_ip(self, request):
        """Liest die Proxy-Kette von rechts bis zum ersten nicht vertrauten Hop.

        Der Proxy muss die tatsächliche Peer-IP anhängen oder den Header
        überschreiben. Ohne Allowlist zählt ausschließlich REMOTE_ADDR.
        """
        remote = request.META.get("REMOTE_ADDR", "")
        try:
            address = ip_address(remote)
        except ValueError:
            return "unknown"

        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if len(forwarded) > 2048:
            return str(address)
        for hop in reversed(forwarded.split(",")):
            if not any(address in network for network in self._trusted_proxies):
                break
            try:
                address = ip_address(hop.strip())
            except ValueError:
                return str(ip_address(remote))
        return str(address)
