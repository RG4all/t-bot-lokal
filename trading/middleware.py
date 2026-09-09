import logging
import threading
import time
from urllib.parse import urlencode

from django.conf import settings
from django.db import OperationalError, close_old_connections
from django.db.utils import InterfaceError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from .passphrase import is_passphrase_verified

logger = logging.getLogger("trading")
_DB_LOG_LOCK = threading.Lock()
_DB_LAST_LOG_AT = 0.0


def _log_database_outage(message, exception):
    global _DB_LAST_LOG_AT
    now = time.monotonic()
    with _DB_LOG_LOCK:
        if now - _DB_LAST_LOG_AT < 60:
            return
        _DB_LAST_LOG_AT = now
    logger.warning(message, exception)


def database_unavailable_response(request):
    close_old_connections()
    if request.path.startswith("/api/"):
        response = JsonResponse(
            {
                "error": "database_temporarily_unavailable",
                "message": "Die Datenbank ist vorübergehend nicht erreichbar. Bitte erneut versuchen.",
            },
            status=503,
        )
    else:
        response = HttpResponse(
            """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Datenbank vorübergehend nicht erreichbar</title>
<style>body{font-family:system-ui;background:#f8f9fa;margin:0;padding:3rem;color:#212529}
main{max-width:42rem;margin:auto;background:white;padding:2rem;border-radius:.75rem;
box-shadow:0 2px 12px #0002}h1{color:#b02a37}a{display:inline-block;padding:.6rem 1rem}</style></head>
<body><main><h1>Dienst vorübergehend eingeschränkt</h1>
<p>PostgreSQL ist momentan nicht erreichbar. Der Bot versucht die Verbindung
automatisch mit Backoff wiederherzustellen. Bitte warte kurz und lade die Seite erneut.</p>
<a href="">Erneut versuchen</a></main></body></html>""",
            status=503,
            content_type="text/html; charset=utf-8",
        )
    response["Retry-After"] = "10"
    return response


class DatabaseAvailabilityMiddleware:
    """Wandelt kurzzeitige Render-Postgres-Ausfälle in klare HTTP 503 um."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            return self.get_response(request)
        except (OperationalError, InterfaceError) as exc:
            _log_database_outage(
                "HTTP-Anfrage wegen Datenbankausfall mit 503 beantwortet: %s",
                exc,
            )
            return database_unavailable_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, (OperationalError, InterfaceError)):
            _log_database_outage("View wegen Datenbankausfall mit 503 beantwortet: %s", exception)
            return database_unavailable_response(request)
        return None


class PermissionsPolicyMiddleware:
    """Setzt den Permissions-Policy-Header auf jeder HTTP-Antwort.

    Django erzeugt selbst keinen Permissions-Policy-Header. Die Middleware
    liest den Wert live aus ``settings.SECURE_PERMISSIONS_POLICY`` und
    beschränkt damit den Zugriff auf nicht benötigte Browser-APIs (Kamera,
    Mikrofon, Geolokation). Sie ist direkt nach der SecurityMiddleware
    registriert, damit auch Fehler-, Redirect- und WhiteNoise-Antworten den
    Header tragen. Ein leerer Policy-Wert lässt die Antwort unverändert.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = getattr(settings, "SECURE_PERMISSIONS_POLICY", "")
        if policy:
            response["Permissions-Policy"] = policy
        return response


class PassphraseGateMiddleware:
    """Schützt HTTP-Endpunkte bis zur Passphrase-Freigabe der Session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "PASSPHRASE_GATE_ENABLED", True):
            return self.get_response(request)

        gate_path = reverse("passphrase_gate")
        health_path = reverse("health")
        # Readiness haelt Composition/Render zurueck, bevor die Session-Pruefung
        # einen 503 in eine Gate-Weiterleitung verwandeln darf.
        readiness_path = reverse("readiness")
        static_url = settings.STATIC_URL or "/static/"
        exempt = (
            request.path == gate_path
            or request.path == health_path
            or request.path == readiness_path
            or request.path.startswith(static_url)
        )
        if exempt:
            return self.get_response(request)
        try:
            verified = is_passphrase_verified(request.session)
        except (OperationalError, InterfaceError) as exc:
            _log_database_outage(
                "Passphrase-Session wegen Datenbankausfall nicht lesbar: %s",
                exc,
            )
            return database_unavailable_response(request)
        if verified:
            return self.get_response(request)

        query = urlencode({"next": request.get_full_path()})
        return redirect(f"{gate_path}?{query}")
