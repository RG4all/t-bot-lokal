import base64
import csv
import io
import logging
import math
import re
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from functools import lru_cache, wraps
from html import escape
from io import BytesIO
from typing import Any, ParamSpec, TypeVar

import markdown
from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError, InterfaceError, transaction
from django.db.models import Count, Max, Min, Model, Q, QuerySet, Sum
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from .backtest_templates import build_backtest_templates
from .forms import (
    BacktestForm,
    ConfigurationForm,
    DashboardConfigurationForm,
    LoginForm,
    RegistrationForm,
)
from .market_data import MarketDataError, SymbolValidationError, validate_exchange_symbols
from .market_scanner import (
    MarketScannerError,
    MarketScannerFilterError,
    scan_market_opportunities,
)
from .models import BacktestTask, Configuration, DataLog, ErrorLog, TradingLog
from .monitoring import runtime_heartbeat
from .passphrase import is_passphrase_verified, passphrase_session_token
from .resource_optimizer import (
    estimate_backtest_runtime,
    get_backtest_resource_profile,
    get_server_resources,
)
from .symbols import get_available_symbols
from .tasks import dispatch_task, local_task_is_active, run_backtest
from .trading_bot import bot_manager
from .worker_status import get_backtest_runtime_status

logger = logging.getLogger(__name__)
_PLOT_LOCK = threading.Lock()
_MANUAL_RENDER_LOCK = threading.Lock()
_MANUAL_HTML: str | None = None
_MAX_API_ROWS = 5_000
_MAX_LOG_ROWS = 2_000

# Typvariablen für generische Helfer und Decorator. ``_ViewParams`` erhält die
# vollständige Signatur der dekorierten View (inklusive URL-Parametern),
# ``_ResponseT`` den konkreten Response-Typ, damit ein Decorator die Typen der
# View nicht auf ``Any`` verwischt.
_ViewParams = ParamSpec("_ViewParams")
_ResponseT = TypeVar("_ResponseT", bound=HttpResponseBase)
_ModelT = TypeVar("_ModelT", bound=Model)

# Exception-Texte sind nicht vertrauenswürdig und können interne Details enthalten.
# Flash-/HTTP-Antworten verwenden feste Meldungen; Diagnosen bleiben in den Logs.
_BOT_START_ERROR = "Bot konnte nicht gestartet werden. Siehe Fehler-Log für Details."
_SELL_ERROR = "Verkauf fehlgeschlagen. Siehe Fehler-Log für Details."
_KILL_SWITCH_ERROR = "Kill-Switch fehlgeschlagen. Siehe Fehler-Log für Details."
_SCANNER_ERROR = "Marktscanner vorübergehend nicht verfügbar. Bitte später erneut versuchen."
_REPORT_ERROR = "Report konnte nicht erstellt werden. Bitte später erneut versuchen."


def _record_view_error(
    *,
    configuration: Configuration,
    source: str,
    exception: BaseException,
    severity: str,
    details: dict[str, Any],
) -> None:
    """Ergänzt das Diagnose-Log, ohne die sichere Fehlerantwort zu gefährden.

    Der Aufrufer loggt den ursprünglichen Fehler zuerst mit logger.exception.
    Ein DB-Ausfall beim zusätzlichen Persistieren darf ihn nicht verdecken.
    Der Savepoint schützt auch Aufrufer innerhalb einer bestehenden Transaktion.

    Args:
        configuration: Konfiguration, zu der der Fehler gehört.
        source: Stabiler Herkunftsschlüssel, z. B. ``views.manual_sell``.
        exception: Die aufgetretene Ausnahme; nur Typ und gekürzter Text werden
            gespeichert, niemals an die HTTP-Antwort weitergegeben.
        severity: Schweregrad gemäß ``ErrorLog.SEVERITY_CHOICES``.
        details: Zusätzlicher, JSON-serialisierbarer Diagnosekontext.
    """
    try:
        with transaction.atomic():
            ErrorLog.objects.create(
                configuration=configuration,
                severity=severity,
                source=source,
                exception_type=type(exception).__name__,
                message=str(exception)[:4000],
                details=details,
            )
    except (DatabaseError, InterfaceError):
        logger.exception("Fehler-Log für Konfiguration %s nicht speicherbar", configuration.id)


def no_cache_json(
    view_func: Callable[_ViewParams, _ResponseT],
) -> Callable[_ViewParams, _ResponseT]:
    """Setzt Cache-Control- und Pragma-Header für API-Responses.

    Die API-Endpunkte liefern benutzerbezogene Handels-, Portfolio- und
    Marktdaten. Ohne explizite Cache-Header können Browser und zwischen-
    geschaltete Proxies/CDNs diese Antworten zwischenspeichern und einem
    anderen Nutzer desselben Clients ausliefern. ``no-store`` verbietet jede
    Speicherung, ``no-cache`` erzwingt eine erneute Validierung und
    ``must-revalidate, max-age=0`` verhindern veraltete Kopien; ``Pragma``
    deckt zusätzlich ältere HTTP/1.0-Zwischenstufen ab.

    Der Decorator wird bewusst als innerster Decorator direkt über der View
    platziert, damit er jede von der View erzeugte Antwort erfasst –
    einschließlich Fehlerantworten (z. B. 400/503), die ohne Header sonst
    ebenfalls gecacht werden könnten.

    Die Typvariablen erhalten Parameter- und Response-Typ der dekorierten View,
    damit statische Prüfungen (mypy/pyright) hinter dem Decorator nicht auf
    ``Any`` zurückfallen.

    Args:
        view_func: Die zu dekorierende View.

    Returns:
        Die View mit identischer Signatur, deren Antwort die Header trägt.
    """

    @wraps(view_func)
    def wrapped(*args: _ViewParams.args, **kwargs: _ViewParams.kwargs) -> _ResponseT:
        response = view_func(*args, **kwargs)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        return response

    return wrapped


def _authenticated_user(request: HttpRequest) -> User:
    """Gibt den angemeldeten Benutzer eines geschützten Requests zurück.

    Alle Views, die Objekte über ``user=…`` bzw. ``configuration__user=…``
    filtern, laufen hinter ``@login_required``; ``request.user`` ist dort
    typseitig trotzdem ``User | AnonymousUser``. Die Funktion macht die
    Voraussetzung explizit und sichert sie zusätzlich ab: Geht der Decorator
    bei einer späteren Änderung verloren, wird der Request mit 403 abgewiesen,
    statt mit einem anonymen Benutzer weiterzufiltern und dabei einen
    Datenbankfehler oder eine unbeabsichtigte Trefferliste zu erzeugen.

    Args:
        request: Der aktuelle Request.

    Returns:
        Der angemeldete Benutzer.

    Raises:
        PermissionDenied: Wenn der Request nicht authentifiziert ist.
    """
    user = request.user
    if not isinstance(user, User) or not user.is_authenticated:
        raise PermissionDenied
    return user


def _safe_next_url(request: HttpRequest, candidate: str | None) -> str | None:
    """Gibt ``candidate`` nur zurück, wenn er auf denselben Host zeigt.

    Args:
        request: Der aktuelle Request; liefert Host und Schema für die Prüfung.
        candidate: Ungeprüfter Weiterleitungswunsch aus GET/POST.

    Returns:
        Das geprüfte Ziel oder ``None``, wenn es eine offene Weiterleitung wäre.
    """
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return None


def _redirect_dashboard(config_id: int) -> HttpResponseRedirect:
    """Leitet auf das Dashboard der angegebenen Konfiguration weiter."""
    return redirect(f"{reverse('dashboard')}?config_id={config_id}")


def _latest_rows(queryset: QuerySet[_ModelT], limit: int) -> list[_ModelT]:
    """Lädt die jüngsten ``limit`` Zeilen und gibt sie chronologisch zurück.

    Args:
        queryset: Zeitgestempeltes QuerySet (``timestamp``/``id``).
        limit: Obergrenze der geladenen Zeilen; begrenzt Speicher und Laufzeit.

    Returns:
        Liste der Objekte, aufsteigend nach Zeitstempel sortiert.
    """
    rows = list(queryset.order_by("-timestamp", "-id")[:limit])
    rows.reverse()
    return rows


def _symbols(config: Configuration) -> list[str]:
    """Zerlegt das Symbolfeld der Konfiguration in eine bereinigte Liste."""
    return [symbol.strip() for symbol in config.symbols.split(",") if symbol.strip()]


def _realized_profit(config: Configuration) -> Decimal:
    """Summiert den realisierten Gewinn aller Verkäufe der Konfiguration.

    Args:
        config: Trading-Konfiguration.

    Returns:
        Summe von ``pl_nominal`` über alle Verkaufs-Logs, ``Decimal(0)`` wenn
        noch kein Verkauf existiert.
    """
    return config.logs.filter(action="sell").aggregate(total=Sum("pl_nominal"))["total"] or Decimal(
        0
    )


def _cash_flow(log: TradingLog) -> Decimal:
    """Berechnet die Kassenwirkung eines einzelnen Trades.

    Args:
        log: Einzelner Trading-Log-Eintrag.

    Returns:
        Negativer Betrag inklusive Gebühr bei Käufen, positiver Nettoerlös
        nach Gebühr bei Verkäufen.
    """
    notional = log.amount * log.price
    return -(notional + log.fee_amount) if log.action == "buy" else notional - log.fee_amount


def _portfolio_snapshot(config: Configuration) -> dict[str, Any]:
    """Erstellt einen Portfolio-Snapshot für die angegebene Konfiguration.

    Args:
        config: Trading-Konfiguration.

    Returns:
        Dictionary mit Portfolio-Metriken:
        - ``cash``: Verfügbares Kapital (``Decimal``)
        - ``realized_profit``: Realisierter Gewinn (``Decimal``)
        - ``invested``: Investiertes Kapital (``Decimal``)
        - ``market_value``: Marktwert offener Positionen nach geschätzter
          Ausstiegsgebühr (``Decimal``)
        - ``equity``: Gesamtes Eigenkapital (``Decimal``)
        - ``unrealized_profit``: Unrealisierter Gewinn (``Decimal``)
        - ``positions``: Liste offener Positionen als ``dict``
    """
    realized_profit = _realized_profit(config)
    invested = Decimal(0)
    market_value = Decimal(0)
    positions: list[dict[str, Any]] = []
    for symbol in _symbols(config):
        latest_trade = config.logs.filter(symbol=symbol).order_by("-timestamp", "-id").first()
        if not latest_trade or latest_trade.action != "buy":
            continue
        entry_value = latest_trade.amount * latest_trade.price + latest_trade.fee_amount
        latest_price = (
            config.data_logs.filter(symbol=symbol)
            .order_by("-timestamp", "-id")
            .values_list("price", flat=True)
            .first()
            or latest_trade.price
        )
        estimated_exit_fee = latest_trade.amount * latest_price * config.fee / Decimal(100)
        net_market_value = latest_trade.amount * latest_price - estimated_exit_fee
        invested += entry_value
        market_value += net_market_value
        positions.append(
            {
                "symbol": symbol,
                "amount": latest_trade.amount,
                "entry_price": latest_trade.price,
                "latest_price": latest_price,
                "invested": entry_value,
                "market_value": net_market_value,
                "unrealized_pl": net_market_value - entry_value,
            }
        )
    cash = config.start_capital + realized_profit - invested
    return {
        "cash": cash,
        "realized_profit": realized_profit,
        "invested": invested,
        "market_value": market_value,
        "equity": cash + market_value,
        "unrealized_profit": market_value - invested,
        "positions": positions,
    }


def _cash_series(logs: list[TradingLog], opening_cash: Decimal) -> list[dict[str, Any]]:
    """Entwickelt den Kassenstand entlang der übergebenen Trades.

    Args:
        logs: Chronologisch aufsteigende Trading-Logs.
        opening_cash: Kassenstand vor dem ersten Eintrag in ``logs``.

    Returns:
        Liste aus ``{"t": ISO-Zeitstempel, "v": Kassenstand als float}``.
    """
    cash = opening_cash
    series: list[dict[str, Any]] = []
    for log in logs:
        cash += _cash_flow(log)
        series.append({"t": log.timestamp.isoformat(), "v": float(cash)})
    return series


def calculate_performance_metrics(logs: list[TradingLog]) -> dict[str, float]:
    """Berechnet Kennzahlen der abgeschlossenen Verkäufe.

    Args:
        logs: Trading-Logs; Käufe werden ignoriert.

    Returns:
        Dictionary mit ``win_rate``, ``avg_profit``, ``total_wins``,
        ``total_losses``, ``avg_win``, ``avg_loss``, ``risk_reward``,
        ``profit_factor``, ``max_win`` und ``max_loss``. Ohne Verkäufe sind
        alle Werte ``0``.
    """
    sell_profits = [float(log.pl_nominal) for log in logs if log.action == "sell"]
    wins = [profit for profit in sell_profits if profit > 0]
    losses = [profit for profit in sell_profits if profit <= 0]
    average_win = sum(wins) / len(wins) if wins else 0
    average_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "win_rate": round(len(wins) / len(sell_profits) * 100, 2) if sell_profits else 0,
        "avg_profit": round(sum(sell_profits) / len(sell_profits), 4) if sell_profits else 0,
        "total_wins": len(wins),
        "total_losses": len(losses),
        "avg_win": round(average_win, 4),
        "avg_loss": round(average_loss, 4),
        "risk_reward": round(average_win / abs(average_loss), 2) if average_loss else 0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else 0,
        "max_win": round(max(sell_profits), 4) if sell_profits else 0,
        "max_loss": round(min(sell_profits), 4) if sell_profits else 0,
    }


def _calculate_metrics_from_db(config: Configuration, limit: int = _MAX_LOG_ROWS) -> dict[str, float]:
    """Berechnet die Performance-Kennzahlen direkt in der Datenbank.

    ``calculate_performance_metrics`` materialisiert sämtliche Log-Zeilen in
    Python und durchläuft sie dort spaltenweise (mehrere Listen aus bis zu
    ``_MAX_LOG_ROWS`` Gleitkommawerten). Bei großen Konfigurationen belastet
    das den Web-Prozess unnötig. Diese Funktion überträgt das Zählen und
    Summieren an das DBMS: Statt Tausender Zeilen werden nur wenige
    Aggregatwerte übertragen und im Speicher gehalten.

    Die anschließende Skalarmathematik (Quotienten, Rundung) erfolgt bewusst
    in Python und nicht als ``Sum(...) / Count(...)``-Ausdruck im ORM. Eine
    reine DB-Division wird auf PostgreSQL als Ganzzahldivision ausgeführt und
    liefert damit verkehrte Werte; zudem bliebe das Ergebnis sonst nicht
    backend-unabhängig (SQLite in der Testumgebung, PostgreSQL in Produktion).
    So bleibt das Ergebnis bitgenau zu ``calculate_performance_metrics``.

    Args:
        config: Trading-Konfiguration, deren Logs ausgewertet werden.
        limit: Maximale Anzahl der jüngsten Logs, die in das Fenster
            einbezogen werden; begrenzt wie ``calculate_performance_metrics``
            das betrachtete Historienfenster auf die neuesten ``limit`` Zeilen.

    Returns:
        Dictionary mit ``win_rate``, ``avg_profit``, ``total_wins``,
        ``total_losses``, ``avg_win``, ``avg_loss``, ``risk_reward``,
        ``profit_factor``, ``max_win`` und ``max_loss`` – dieselben Schlüssel
        und Werte wie ``calculate_performance_metrics``.
    """
    # Fenster der jüngsten ``limit`` Logs. ``aggregate()`` wertet das
    # angewandte LIMIT aus, sodass ausschließlich dieser Ausschnitt zählt und
    # nicht die gesamte Historie der Konfiguration.
    window = config.logs.all().order_by("-timestamp", "-id")[:limit]
    aggregates = window.aggregate(
        wins=Count("id", filter=Q(action="sell", pl_nominal__gt=0)),
        losses=Count("id", filter=Q(action="sell", pl_nominal__lte=0)),
        total_pl=Sum("pl_nominal", filter=Q(action="sell")),
        gross_profit=Sum("pl_nominal", filter=Q(action="sell", pl_nominal__gt=0)),
        gross_loss=Sum("pl_nominal", filter=Q(action="sell", pl_nominal__lte=0)),
        max_win=Max("pl_nominal", filter=Q(action="sell")),
        min_loss=Min("pl_nominal", filter=Q(action="sell")),
    )

    wins = aggregates["wins"] or 0
    losses = aggregates["losses"] or 0
    total_sells = wins + losses
    total_pl = aggregates["total_pl"] or Decimal(0)
    gross_profit = aggregates["gross_profit"] or Decimal(0)
    gross_loss = aggregates["gross_loss"] or Decimal(0)
    max_win = aggregates["max_win"] or Decimal(0)
    min_loss = aggregates["min_loss"] or Decimal(0)

    average_win = gross_profit / wins if wins else Decimal(0)
    average_loss = gross_loss / losses if losses else Decimal(0)
    return {
        "win_rate": round(wins / total_sells * 100, 2) if total_sells else 0,
        "avg_profit": round(float(total_pl) / total_sells, 4) if total_sells else 0,
        "total_wins": wins,
        "total_losses": losses,
        "avg_win": round(float(average_win), 4),
        "avg_loss": round(float(average_loss), 4),
        "risk_reward": round(float(average_win) / abs(float(average_loss)), 2) if average_loss else 0,
        "profit_factor": round(float(gross_profit) / abs(float(gross_loss)), 2) if gross_loss else 0,
        "max_win": round(float(max_win), 4),
        "max_loss": round(float(min_loss), 4),
    }


@require_GET
def health_view(request: HttpRequest) -> JsonResponse:
    """Health-Check-Endpunkt für Monitoring und Load-Balancer."""
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})


@lru_cache(maxsize=1)
def _render_manual() -> str:
    """Kompiliert das vertrauenswürdige Handbuch einmal je Prozess.

    Der Lock ergänzt den LRU-Cache für den seltenen Fall zweier gleichzeitiger
    erster Requests: Auch dann wird Markdown nur genau einmal kompiliert.
    """
    global _MANUAL_HTML
    with _MANUAL_RENDER_LOCK:
        if _MANUAL_HTML is not None:
            return _MANUAL_HTML
        candidates = (
            settings.BASE_DIR / "docs" / "MANUAL.md",
            settings.BASE_DIR / "MANUAL.md",
        )
        for candidate in candidates:
            if candidate.exists():
                source = candidate.read_text(encoding="utf-8")
                _MANUAL_HTML = markdown.markdown(
                    source,
                    extensions=[
                        "extra",
                        "fenced_code",
                        "tables",
                        "toc",
                        "sane_lists",
                        "codehilite",
                    ],
                    extension_configs={
                        "codehilite": {
                            "css_class": "codehilite",
                            "guess_lang": False,
                            "noclasses": False,
                        }
                    },
                    output_format="html5",
                )
                return _MANUAL_HTML
        _MANUAL_HTML = "<p>Handbuchdatei <code>MANUAL.md</code> konnte nicht gefunden werden.</p>"
        return _MANUAL_HTML


# Beibehaltung der lru_cache-Kompatibilität für Tests/Management, einschließlich
# eines echten Reset des zweiten, thread-sicheren Cache-Layers.
_manual_lru_cache_clear = _render_manual.cache_clear


def _clear_manual_cache() -> None:
    """Leert LRU-Cache und den zusätzlichen, thread-sicheren Cache-Layer."""
    global _MANUAL_HTML
    with _MANUAL_RENDER_LOCK:
        _MANUAL_HTML = None
        _manual_lru_cache_clear()


# Bewusste Ersetzung der lru_cache-API: Tests und Management-Code rufen
# weiterhin ``_render_manual.cache_clear()`` auf und müssen dabei beide
# Cache-Layer leeren. Statische Prüfer kennen dieses Muster nicht.
_render_manual.cache_clear = _clear_manual_cache  # type: ignore[method-assign]


@require_GET
def help_view(request: HttpRequest) -> HttpResponse:
    """Zeigt das gerenderte Handbuch (``docs/MANUAL.md``) in der Oberfläche."""
    return render(
        request,
        "trading/help.html",
        {"manual_html": mark_safe(_render_manual()), "version": settings.APP_VERSION},
    )


def home(request: HttpRequest) -> HttpResponseRedirect:
    """Leitet je nach Authentifizierungsstatus auf Dashboard oder Login weiter."""
    return redirect("dashboard" if request.user.is_authenticated else "login")


@sensitive_post_parameters("passphrase")
@sensitive_variables("submitted")
def passphrase_gate_view(request: HttpRequest) -> HttpResponse:
    """Prüft die vorgeschaltete Zugangs-Passphrase.

    Vergleicht die Eingabe zeitkonstant, rotiert bei Erfolg den Session-Key und
    leitet nur auf geprüfte, hosteigene Ziele weiter.

    Args:
        request: GET zeigt das Formular, POST prüft die Passphrase.

    Returns:
        Formularantwort oder Weiterleitung nach erfolgreicher Freigabe.
    """
    requested_next = request.POST.get("next") or request.GET.get("next")
    next_url = _safe_next_url(request, requested_next)
    if is_passphrase_verified(request.session):
        return redirect(next_url or "login")

    error = None
    if request.method == "POST":
        # Byte-basierter, timing-sicherer Vergleich unterstützt auch Unicode.
        # Explizite Secrets werden weder hier noch in den Settings normalisiert.
        submitted = request.POST.get("passphrase", "")
        # Ein leeres/fehlendes Secret darf keine Freigabe erzeugen: Ohne diesen
        # Guard würde constant_time_compare("", "") den Gate-Schutz aufheben.
        expected = settings.PASSPHRASE or ""
        if expected and constant_time_compare(submitted, expected):
            request.session.cycle_key()
            request.session["passphrase_verified"] = passphrase_session_token()
            return redirect(next_url or "login")
        error = "Falsche Passphrase. Zugang verweigert."
    return render(
        request,
        "trading/passphrase_gate.html",
        {"error": error, "next": next_url or ""},
    )


def register_view(request: HttpRequest) -> HttpResponse:
    """Registriert einen neuen Benutzer und leitet zum Login weiter."""
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        logger.info("Benutzer %s wurde registriert", user.username)
        messages.success(request, "Registrierung erfolgreich. Bitte jetzt anmelden.")
        return redirect("login")
    return render(request, "trading/register.html", {"form": form})


def login_view(request: HttpRequest) -> HttpResponse:
    """Meldet einen Benutzer an.

    Args:
        request: GET zeigt das Formular, POST prüft die Zugangsdaten.

    Returns:
        Formularantwort mit Fehlern oder Weiterleitung auf ein geprüftes Ziel
        bzw. das Dashboard.
    """
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if user is not None:
            login(request, user)
            next_url = _safe_next_url(
                request,
                request.POST.get("next") or request.GET.get("next"),
            )
            return redirect(next_url or "dashboard")
        form.add_error(None, "Benutzername oder Passwort ist falsch.")
    return render(request, "trading/login.html", {"form": form})


@login_required
@require_POST
def logout_view(request: HttpRequest) -> HttpResponseRedirect:
    """Meldet den Benutzer ab und invalidiert die Session vollständig."""
    logout(request)
    # Explizite Session-Invalidierung: leert alle Session-Daten und rotiert
    # den Session-Key. Bei signed_cookie-Sessions ist dies entscheidend, da
    # ein gestohlenes Cookie sonst bis zum Ablauf weiterverwendet werden könnte.
    # flush() löscht serverseitig (was bei signed_cookies das Setzen eines
    # leeren/neuen Cookies und Entfernen der auth-bezogenen Session-Daten
    # bewirkt) und invalidiert den bisherigen Cookie-Inhalt durch Key-Rotation.
    request.session.flush()
    return redirect("login")


@login_required
def config_view(request: HttpRequest) -> HttpResponse:
    """Legt eine neue Trading-Konfiguration für den angemeldeten Benutzer an.

    Args:
        request: GET zeigt das Formular, POST speichert die Konfiguration.

    Returns:
        Formularantwort oder Weiterleitung auf die Konfigurationsliste.
    """
    form = ConfigurationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        config = form.save(commit=False)
        config.user = _authenticated_user(request)
        config.save()
        messages.success(request, "Konfiguration wurde erstellt.")
        return redirect("config_list")
    return render(request, "trading/config_form.html", {"form": form})


@login_required
def config_list_view(request: HttpRequest) -> HttpResponse:
    """Listet alle Konfigurationen des angemeldeten Benutzers."""
    configs = Configuration.objects.filter(user=_authenticated_user(request)).order_by("-id")
    return render(request, "trading/config_list.html", {"configs": configs})


@login_required
def config_edit_view(request: HttpRequest, config_id: int) -> HttpResponse:
    """Bearbeitet eine eigene Konfiguration und startet laufende Bots neu."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    form = ConfigurationForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        was_running = config.is_running
        config = form.save()
        if was_running:
            bot_manager.restart_bot(config)
        messages.success(request, "Konfiguration wurde aktualisiert.")
        return redirect("config_list")
    return render(
        request,
        "trading/config_edit.html",
        {"form": form, "config": config},
    )


@login_required
@require_POST
def config_activate(request: HttpRequest, config_id: int) -> HttpResponseRedirect:
    """Validiert die Symbole und startet den Bot der eigenen Konfiguration."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    try:
        validate_exchange_symbols(config.exchange, config.market, _symbols(config))
    except (SymbolValidationError, MarketDataError, ValueError) as exc:
        logger.exception("Konfigurationsprüfung für %s fehlgeschlagen", config.id)
        _record_view_error(
            configuration=config,
            severity="warning",
            source="views.config_activate.validation",
            exception=exc,
            details={
                "exchange": config.exchange,
                "market": config.market,
                "symbols": _symbols(config),
            },
        )
        messages.error(request, _BOT_START_ERROR)
        return redirect("config_list")
    try:
        bot_manager.start_bot(config)
    except Exception as exc:
        logger.exception("Bot-Start für Konfiguration %s fehlgeschlagen", config.id)
        _record_view_error(
            configuration=config,
            severity="critical",
            source="views.config_activate",
            exception=exc,
            details={
                "exchange": config.exchange,
                "market": config.market,
                "symbols": _symbols(config),
            },
        )
        messages.error(request, _BOT_START_ERROR)
    else:
        if not config.is_running:
            config.is_running = True
            config.save(update_fields=["is_running"])
        messages.success(request, "Bot wurde aktiviert.")
    return redirect("config_list")


@login_required
@require_POST
def config_deactivate(request: HttpRequest, config_id: int) -> HttpResponseRedirect:
    """Stoppt den Bot der eigenen Konfiguration."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    bot_manager.stop_bot(config)
    if config.is_running:
        config.is_running = False
        config.save(update_fields=["is_running"])
    messages.success(request, "Bot wurde deaktiviert.")
    return redirect("config_list")


@login_required
def config_delete(request: HttpRequest, config_id: int) -> HttpResponse:
    """Zeigt die Löschbestätigung (GET) und löscht die Konfiguration (POST)."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    if request.method == "POST":
        bot_manager.stop_bot(config)
        config.delete()
        messages.success(request, "Konfiguration wurde gelöscht.")
        return redirect("config_list")
    return render(
        request,
        "trading/config_confirm_delete.html",
        {"config": config},
    )


@login_required
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """Zeigt Portfolio, Kennzahlen und Trades einer eigenen Konfiguration.

    Args:
        request: Optionaler GET-Parameter ``config_id`` wählt die Konfiguration;
            ohne ihn wird die zuletzt angelegte verwendet. POST speichert die
            Strategieparameter des Dashboard-Formulars.

    Returns:
        Gerendertes Dashboard oder Weiterleitung nach dem Speichern.
    """
    user = _authenticated_user(request)
    config_id = request.GET.get("config_id")
    config: Configuration | None
    if config_id:
        config = get_object_or_404(Configuration, id=config_id, user=user)
    else:
        config = Configuration.objects.filter(user=user).order_by("-id").first()

    if not config:
        return render(
            request,
            "trading/dashboard.html",
            {"config": None, "all_configs": []},
        )

    form = DashboardConfigurationForm(request.POST or None, instance=config)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Strategieparameter wurden gespeichert.")
        return _redirect_dashboard(config.id)

    metric_logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
    display_logs = list(config.logs.all().order_by("-timestamp", "-id")[:100])
    portfolio = _portfolio_snapshot(config)
    context = {
        "config": config,
        "form": form,
        "logs": display_logs,
        "current_capital": portfolio["cash"],
        "tank": portfolio["realized_profit"],
        "invested_capital": portfolio["invested"],
        "account_equity": portfolio["equity"],
        "unrealized_profit": portfolio["unrealized_profit"],
        "open_position_count": len(portfolio["positions"]),
        "buy_orders": sum(log.action == "buy" for log in metric_logs),
        "sell_orders": sum(log.action == "sell" for log in metric_logs),
        "profitable_sells": sum(log.action == "sell" and log.pl_nominal > 0 for log in metric_logs),
        "unprofitable_sells": sum(
            log.action == "sell" and log.pl_nominal <= 0 for log in metric_logs
        ),
        "symbols": _symbols(config),
        "all_configs": Configuration.objects.filter(user=user).order_by("-id"),
    }
    return render(request, "trading/dashboard.html", context)


@login_required
@require_POST
def reset_log(request: HttpRequest, config_id: int) -> HttpResponseRedirect:
    """Setzt Trading-Log und simuliertes Portfolio der Konfiguration zurück."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    if config.is_running:
        bot_manager.restart_bot(config, before_start=lambda: config.logs.all().delete())
        messages.success(
            request,
            "Der Bot wird sauber neu gestartet; dabei werden Trading-Log und Portfolio zurückgesetzt.",
        )
    else:
        config.logs.all().delete()
        messages.success(request, "Trading-Log und simuliertes Portfolio wurden zurückgesetzt.")
    return _redirect_dashboard(config.id)


def _validated_start_time(request: HttpRequest) -> datetime | None:
    """Liest ``start_time`` aus der Query und macht ihn zeitzonenbewusst.

    Returns:
        Geparster Zeitpunkt oder ``None`` bei fehlendem/ungültigem Wert.
    """
    raw = request.GET.get("start_time")
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@login_required
@require_GET
@no_cache_json
def symbol_suggestions_api(request: HttpRequest) -> JsonResponse:
    """Liefert Symbolvorschläge für Exchange und Marktart.

    Die Vorschläge sind nur eine Eingabehilfe; verbindlich geprüft werden die
    Symbole erst beim Speichern bzw. Aktivieren der Konfiguration.
    """
    exchange = request.GET.get("exchange", "").strip().lower()
    market = request.GET.get("market", "").strip().lower()
    query = request.GET.get("q", "").strip().upper()
    valid_exchanges = dict(Configuration.EXCHANGE_CHOICES)
    valid_markets = dict(Configuration.MARKET_CHOICES)
    if exchange not in valid_exchanges or market not in valid_markets:
        return JsonResponse({"error": "Ungültige Exchange oder Marktart"}, status=400)
    try:
        symbols = get_available_symbols(exchange, market)
    except MarketDataError:
        logger.exception("Symbolkatalog für %s/%s nicht verfügbar", exchange, market)
        return JsonResponse(
            {
                "error": "Symbolvorschläge vorübergehend nicht verfügbar. Bitte später erneut versuchen.",
                "suggestions": [],
            },
            status=503,
        )
    suggestions = [symbol for symbol in symbols if not query or query in symbol][:20]
    return JsonResponse(
        {
            "exchange": exchange,
            "market": market,
            "query": query,
            "suggestions": suggestions,
            "authoritative_on_submit": True,
        }
    )


@login_required
@require_GET
@no_cache_json
def market_opportunities_api(request: HttpRequest) -> JsonResponse:
    """Liefert geprüfte Top-Mover für die Konfigurationsvorlage.

    Die Börsenabfrage bleibt auf diesen GET-Endpunkt begrenzt; keine API-Keys
    werden verwendet und die Antwort ist pro Exchange/Markt kurz gecacht.
    """
    exchange = request.GET.get("exchange", "").strip().lower()
    market = request.GET.get("market", "spot").strip().lower()
    if market not in {"spot", "futures"}:
        return JsonResponse({"error": "Ungültige Marktart"}, status=400)
    if exchange not in {"all", *dict(Configuration.EXCHANGE_CHOICES)}:
        return JsonResponse({"error": "Ungültige Exchange"}, status=400)
    # Rohwerte aus der Query; der Scanner konvertiert und begrenzt sie selbst
    # und meldet ungültige Eingaben als MarketScannerFilterError.
    filters: dict[str, Any] = {
        "volatility_threshold": request.GET.get("volatility_threshold", "10"),
        "volume_spike_multiple": request.GET.get("volume_spike_multiple", "1.5"),
        "min_volume_market_cap_ratio": request.GET.get(
            "min_volume_market_cap_ratio", "0.10"
        ),
        "min_orderbook_depth_ratio": request.GET.get(
            "min_orderbook_depth_ratio", "0.001"
        ),
    }
    if exchange == "all":
        try:
            from .market_scanner import scan_all_market_opportunities

            payload = scan_all_market_opportunities(
                market=market,
                refresh=request.GET.get("refresh") == "1",
                **filters,
            )
        except MarketScannerFilterError:
            logger.exception("Ungültige Marktscanner-Parameter")
            return JsonResponse({"error": "Ungültige Scanner-Parameter."}, status=400)
        except MarketScannerError:
            logger.exception("Marktscanner nicht verfügbar")
            return JsonResponse({"error": _SCANNER_ERROR}, status=503)
        if not payload.get("exchanges"):
            payload["error"] = "Keine Exchange lieferte einen belastbaren Scanner-Snapshot."
            return JsonResponse(payload, status=503)
        return JsonResponse(payload)
    if not exchange:
        return JsonResponse({"error": "exchange fehlt"}, status=400)
    try:
        payload = scan_market_opportunities(
            exchange,
            market,
            refresh=request.GET.get("refresh") == "1",
            **filters,
        )
    except MarketScannerFilterError:
        logger.exception("Ungültige Marktscanner-Parameter für %s/%s", exchange, market)
        return JsonResponse(
            {"error": "Ungültige Scanner-Parameter.", "gainers": [], "losers": []}, status=400
        )
    except MarketScannerError:
        logger.exception("Marktscanner nicht verfügbar für %s/%s", exchange, market)
        return JsonResponse({"error": _SCANNER_ERROR, "gainers": [], "losers": []}, status=503)
    return JsonResponse(payload)


@login_required
@require_GET
@no_cache_json
def server_resources_api(request: HttpRequest) -> JsonResponse:
    """Diagnose-Endpunkt für das erkannte CPU-/RAM-/Speicherprofil."""
    profile = get_backtest_resource_profile(
        get_server_resources(refresh=request.GET.get("refresh") == "1")
    )
    return JsonResponse(
        {"resources": profile.resources.as_dict(), "backtesting": profile.as_dict()}
    )


@login_required
@require_GET
@no_cache_json
def backtesting_estimate_api(request: HttpRequest) -> JsonResponse:
    """Berechnet eine Laufzeitschätzung ohne einen Backtest anzulegen."""
    profile = get_backtest_resource_profile()
    try:
        combinations = int(request.GET.get("combinations", "0"))
        price_points = int(request.GET.get("price_points", "0"))
        symbols = int(request.GET.get("symbols", "1"))
        if (
            min(combinations, price_points, symbols) < 0
            or symbols == 0
            or combinations > 100_000_000
            or price_points > 50_000
            or symbols > 100
        ):
            raise ValueError
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "combinations, price_points und symbols müssen Zahlen sein"}, status=400
        )
    estimate = estimate_backtest_runtime(combinations, price_points, symbols, profile.resources)
    estimate["within_hard_limit"] = combinations * symbols <= profile.max_combinations
    estimate["hard_limit"] = profile.max_combinations
    return JsonResponse(estimate)


@login_required
@require_GET
@no_cache_json
def data_logs_api(request: HttpRequest) -> JsonResponse:
    """Liefert Marktdatenpunkte eines Symbols der eigenen Konfiguration."""
    config = get_object_or_404(
        Configuration,
        id=request.GET.get("config_id"),
        user=_authenticated_user(request),
    )
    symbol = request.GET.get("symbol", "").strip()
    if symbol not in _symbols(config):
        return JsonResponse({"error": "Ungültiges Symbol"}, status=400)
    queryset = config.data_logs.filter(symbol=symbol)
    start_time = _validated_start_time(request)
    if start_time:
        queryset = queryset.filter(timestamp__gte=start_time)
    rows = _latest_rows(queryset, _MAX_API_ROWS)
    return JsonResponse(
        [
            {
                "timestamp": row.timestamp.isoformat(),
                "price": float(row.price),
                "deltadelta": float(row.deltadelta or 0),
                "div_DVA_prev_NDA": float(row.div_DVA_prev_NDA or 0),
                "nda": float(row.nda or 0),
            }
            for row in rows
        ],
        safe=False,
    )


@login_required
@require_GET
@no_cache_json
def trades_api(request: HttpRequest) -> JsonResponse:
    """Liefert die Trades eines Symbols der eigenen Konfiguration."""
    config = get_object_or_404(
        Configuration,
        id=request.GET.get("config_id"),
        user=_authenticated_user(request),
    )
    symbol = request.GET.get("symbol", "").strip()
    if symbol not in _symbols(config):
        return JsonResponse({"error": "Ungültiges Symbol"}, status=400)
    queryset = config.logs.filter(symbol=symbol)
    start_time = _validated_start_time(request)
    if start_time:
        queryset = queryset.filter(timestamp__gte=start_time)
    rows = _latest_rows(queryset, _MAX_API_ROWS)
    return JsonResponse(
        [
            {
                "timestamp": row.timestamp.isoformat(),
                "action": row.action,
                "price": float(row.price),
            }
            for row in rows
        ],
        safe=False,
    )


@login_required
@require_GET
@no_cache_json
def info_api(request: HttpRequest, config_id: int) -> JsonResponse:
    """Liefert Portfolio-, Kennzahl- und Kurvendaten für das Dashboard.

    Args:
        request: Authentifizierter Request; nur eigene Konfigurationen.
        config_id: Primärschlüssel der Konfiguration.

    Returns:
        JSON mit Kapital, offenen Positionen, Performance-Kennzahlen, Sharpe,
        Drawdown sowie Equity- und Kassenkurve.
    """
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
    portfolio = _portfolio_snapshot(config)
    # Performance-Kennzahlen per DB-Aggregation statt alle Logs in Python zu
    # durchlaufen – entlastet den Web-Prozess bei großen Konfigurationen
    # (siehe ``_calculate_metrics_from_db``). Die Equity-/Kassenkurve benötigt
    # weiterhin die einzelnen Zeilen.
    metrics = _calculate_metrics_from_db(config, _MAX_LOG_ROWS)

    window_realized = sum(
        (log.pl_nominal for log in logs if log.action == "sell"),
        Decimal(0),
    )
    realized_capital = config.start_capital + portfolio["realized_profit"] - window_realized
    equity: list[dict[str, Any]] = []
    for log in logs:
        if log.action == "sell":
            realized_capital += log.pl_nominal
        equity.append({"t": log.timestamp.isoformat(), "v": float(realized_capital)})

    window_cash_flow = sum((_cash_flow(log) for log in logs), Decimal(0))
    cash_curve = _cash_series(logs, portfolio["cash"] - window_cash_flow)

    peak = float(config.start_capital)
    max_drawdown = 0.0
    current_drawdown = 0.0
    sell_returns: list[float] = []
    previous_capital = float(config.start_capital)
    for point, log in zip(equity, logs):
        capital = point["v"]
        peak = max(peak, capital)
        current_drawdown = (peak - capital) / peak if peak > 0 else 0
        max_drawdown = max(max_drawdown, current_drawdown)
        if log.action == "sell":
            sell_returns.append(
                (capital - previous_capital) / previous_capital if previous_capital else 0
            )
            previous_capital = capital

    if len(sell_returns) > 1:
        mean = sum(sell_returns) / len(sell_returns)
        variance = sum((value - mean) ** 2 for value in sell_returns) / (len(sell_returns) - 1)
        standard_deviation = math.sqrt(variance)
        sharpe = mean / standard_deviation * math.sqrt(252) if standard_deviation else 0
    else:
        sharpe = 0

    buy_orders = sum(log.action == "buy" for log in logs)
    sell_orders = sum(log.action == "sell" for log in logs)
    return JsonResponse(
        {
            "current_capital": float(portfolio["cash"]),
            "available_cash": float(portfolio["cash"]),
            "invested_capital": float(portfolio["invested"]),
            "account_equity": float(portfolio["equity"]),
            "unrealized_profit": float(portfolio["unrealized_profit"]),
            "open_position_count": len(portfolio["positions"]),
            "open_positions": [
                {
                    key: float(value) if isinstance(value, Decimal) else value
                    for key, value in item.items()
                }
                for item in portfolio["positions"]
            ],
            "tank": float(portfolio["realized_profit"]),
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "profitable_sells": metrics["total_wins"],
            "unprofitable_sells": metrics["total_losses"],
            "win_rate": metrics["win_rate"],
            "avg_profit_trade": metrics["avg_profit"],
            "avg_win": metrics["avg_win"],
            "avg_loss": metrics["avg_loss"],
            "risk_reward": metrics["risk_reward"],
            "profit_factor": metrics["profit_factor"],
            "biggest_win": metrics["max_win"],
            "biggest_loss": metrics["max_loss"],
            "sharpe": round(sharpe, 3),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown": round(max_drawdown * 100, 2),
            "current_drawdown": round(current_drawdown * 100, 2),
            "equity": equity,
            "equity_timestamps": [point["t"] for point in equity],
            "equity_curve": [point["v"] for point in equity],
            "cash_timestamps": [point["t"] for point in cash_curve],
            "cash_curve": [point["v"] for point in cash_curve],
            "metrics": metrics,
        }
    )


@login_required
@require_GET
@no_cache_json
def bot_status_api(request: HttpRequest) -> JsonResponse:
    """Meldet den Laufzeitstatus des Bots und startet ihn bei Bedarf neu.

    Interne Fehlertexte werden bewusst durch eine feste Meldung ersetzt; die
    Diagnose bleibt im Fehler-Log.
    """
    config_id = request.GET.get("config_id")
    if not config_id:
        return JsonResponse({"error": "config_id fehlt"}, status=400)
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    if config.is_running and not bot_manager.is_running(config.id):
        try:
            bot_manager.start_bot(config)
        except Exception as exc:
            logger.exception("Automatischer Neustart für %s fehlgeschlagen", config.id)
            _record_view_error(
                configuration=config,
                severity="critical",
                source="views.bot_status_api",
                exception=exc,
                details={"exchange": config.exchange, "market": config.market},
            )
            config.is_running = False
            config.save(update_fields=["is_running"])
    status = dict(bot_manager.status(config.id))
    # last_error enthält interne Diagnosen, keine freigegebene Benutzer-Meldung.
    if status.get("last_error"):
        status["last_error"] = "Bot-Fehler aufgetreten. Siehe Fehler-Log für Details."
    status["is_running_flag"] = config.is_running
    return JsonResponse(status)


@login_required
@require_GET
@no_cache_json
def logs_api(request: HttpRequest, config_id: int) -> JsonResponse:
    """Liefert die paginierten Trades einer eigenen Konfiguration."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    paginator = Paginator(config.logs.all().order_by("-timestamp", "-id"), 100)
    page = paginator.get_page(request.GET.get("page", 1))
    open_symbols = set(bot_manager.open_symbols(config.id))
    if not open_symbols:
        for symbol in _symbols(config):
            latest = config.logs.filter(symbol=symbol).order_by("-timestamp", "-id").first()
            if latest and latest.action == "buy":
                open_symbols.add(symbol)

    results = [
        {
            "id": log.id,
            "timestamp": log.timestamp.isoformat(),
            "date": timezone.localtime(log.timestamp).date().isoformat(),
            "time": timezone.localtime(log.timestamp).strftime("%H:%M:%S"),
            "symbol": log.symbol,
            "action": log.action,
            "price": float(log.price),
            "fee_amount": float(log.fee_amount),
            "amount": float(log.amount),
            "order_id": log.order_id,
            "pl_nominal": float(log.pl_nominal),
            "pl_relative": float(log.pl_relative),
            "total_pl": float(log.total_pl),
            "current_capital": float(log.current_capital),
            "tank": float(log.tank),
        }
        for log in page.object_list
    ]
    return JsonResponse(
        {
            "results": results,
            "open_symbols": sorted(open_symbols),
            "pagination": {
                "page": page.number,
                "page_size": 100,
                "pages": paginator.num_pages,
                "count": paginator.count,
                "has_previous": page.has_previous(),
                "has_next": page.has_next(),
            },
        }
    )


@login_required
@require_POST
def manual_sell_view(request: HttpRequest, config_id: int) -> JsonResponse:
    """Verkauft eine offene Position manuell.

    Args:
        request: POST mit dem Feld ``symbol``.
        config_id: Primärschlüssel der eigenen Konfiguration.

    Returns:
        JSON-Status; Fehlermeldungen sind fest und enthalten keine internen
        Details.
    """
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    symbol = request.POST.get("symbol", "").strip()
    if symbol not in _symbols(config):
        return JsonResponse({"status": "error", "message": "Ungültiges Symbol"}, status=400)
    try:
        bot_manager.manual_sell(config.id, symbol)
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        logger.exception("Manueller Verkauf für %s/%s fehlgeschlagen", config.id, symbol)
        _record_view_error(
            configuration=config,
            severity="warning" if isinstance(exc, ValueError) else "critical",
            source="views.manual_sell",
            exception=exc,
            details={"symbol": symbol, "exchange": config.exchange},
        )
        return JsonResponse(
            {"status": "error", "message": _SELL_ERROR},
            status=400 if isinstance(exc, ValueError) else 500,
        )


@login_required
@require_POST
def kill_switch_view(request: HttpRequest, config_id: int) -> JsonResponse:
    """Liquidiert alle offenen Positionen nach doppelter Bestätigung."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    if request.POST.get("confirm1") != "LIQUIDATE" or request.POST.get("confirm2") != "LIQUIDATE":
        return JsonResponse(
            {"status": "error", "message": "Doppelte Bestätigung fehlt."},
            status=400,
        )
    try:
        result = bot_manager.kill_switch(config.id)
    except Exception as exc:
        logger.exception("Kill-Switch für Konfiguration %s fehlgeschlagen", config.id)
        _record_view_error(
            configuration=config,
            severity="warning" if isinstance(exc, ValueError) else "critical",
            source="views.kill_switch",
            exception=exc,
            details={"exchange": config.exchange, "symbols": _symbols(config)},
        )
        return JsonResponse(
            {"status": "error", "message": _KILL_SWITCH_ERROR},
            status=400 if isinstance(exc, ValueError) else 500,
        )
    status = "ok" if not result["errors"] else "partial"
    return JsonResponse({"status": status, **result})


@login_required
@require_GET
def error_log_view(request: HttpRequest) -> HttpResponse:
    """Zeigt das gefilterte Fehler-Log der eigenen Konfigurationen."""
    user = _authenticated_user(request)
    configs = Configuration.objects.filter(user=user).order_by("name")
    base_queryset = ErrorLog.objects.filter(configuration__user=user)
    queryset = base_queryset.select_related("configuration")

    config_id = request.GET.get("config_id", "")
    severity = request.GET.get("severity", "")
    state = request.GET.get("state", "open")
    source = request.GET.get("source", "").strip()
    if config_id.isdigit():
        queryset = queryset.filter(configuration_id=config_id)
    if severity in dict(ErrorLog.SEVERITY_CHOICES):
        queryset = queryset.filter(severity=severity)
    if state == "open":
        queryset = queryset.filter(resolved=False)
    elif state == "resolved":
        queryset = queryset.filter(resolved=True)
    if source:
        queryset = queryset.filter(source__icontains=source)

    page = Paginator(queryset, 100).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "errors": page.object_list,
        "configs": configs,
        "severity_choices": ErrorLog.SEVERITY_CHOICES,
        "filters": {
            "config_id": config_id,
            "severity": severity,
            "state": state,
            "source": source,
        },
        "total_count": base_queryset.count(),
        "open_count": base_queryset.filter(resolved=False).count(),
    }
    return render(request, "trading/error_log.html", context)


@login_required
@require_POST
def error_log_resolve(request: HttpRequest, error_id: int) -> HttpResponseRedirect:
    """Markiert einen eigenen Fehler-Log-Eintrag als erledigt oder offen."""
    error = get_object_or_404(
        ErrorLog,
        id=error_id,
        configuration__user=_authenticated_user(request),
    )
    error.resolved = request.POST.get("action") != "reopen"
    error.save(update_fields=["resolved"])
    return redirect("error_log")


def _figure_to_base64(figure: Any, pyplot: Any) -> str:
    """Rendert eine Matplotlib-Figur als Base64-PNG und schließt sie.

    ``figure`` und ``pyplot`` bleiben ``Any``: Matplotlib wird bewusst erst zur
    Laufzeit importiert, damit der Webprozess die Bibliothek nicht bei jedem
    Start laden muss.
    """
    buffer = BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    pyplot.close(figure)
    return encoded


def _pdf_response(
    request: HttpRequest,
    template: str,
    context: dict[str, Any],
    filename: str,
    disposition: str = "attachment",
) -> HttpResponse:
    """Rendert ein Template als PDF-Download.

    Args:
        request: Request für Template-Kontext und Basis-URL.
        template: Pfad des zu rendernden Templates.
        context: Template-Kontext.
        filename: Dateiname im ``Content-Disposition``-Header.
        disposition: ``attachment`` (Download) oder ``inline`` (Anzeige).

    Returns:
        PDF-Antwort oder eine 503-Antwort mit fester Fehlermeldung; Renderfehler
        können lokale Pfade enthalten und bleiben deshalb im Log.
    """
    try:
        from weasyprint import HTML

        html_string = render_to_string(template, context, request=request)
        pdf = HTML(
            string=html_string,
            base_url=request.build_absolute_uri("/"),
        ).write_pdf()
    except Exception:
        # Auch native Bibliotheks- und Renderfehler können lokale Pfade enthalten.
        logger.exception("PDF-Report konnte nicht erstellt werden")
        return HttpResponse(_REPORT_ERROR, status=503, content_type="text/plain; charset=utf-8")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _report_filename(config: Configuration, extension: str) -> str:
    """Bildet einen kollisionsarmen, dateisystemsicheren Reportnamen."""

    def safe_component(value: object) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._")
        return cleaned or "unknown"

    timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    return (
        "_".join(
            (
                safe_component(config.user.username),
                safe_component(config.exchange),
                str(config.id),
                timestamp,
            )
        )
        + f".{extension}"
    )


def _build_report_context(config: Configuration) -> dict[str, Any]:
    """Erzeugt Kennzahlen und Diagramme für den Trading-Report.

    Args:
        config: Trading-Konfiguration.

    Returns:
        Template-Kontext mit Trades, Portfolio-Werten und den als Base64-PNG
        eingebetteten Diagrammen.

    Raises:
        RuntimeError: Wenn die Diagramm-Engine (Matplotlib) fehlt.
    """
    logs = list(config.logs.all().order_by("timestamp", "id"))
    portfolio = _portfolio_snapshot(config)
    sell_logs = [log for log in logs if log.action == "sell"]
    symbol_counts = Counter(log.symbol for log in logs)
    symbol_profits: defaultdict[str, Decimal] = defaultdict(Decimal)
    daily_profits: defaultdict[str, Decimal] = defaultdict(Decimal)
    symbol_profit_counts: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"profit": 0, "loss": 0}
    )
    cumulative_profit = Decimal(0)
    profit_times: list[str] = []
    cumulative_values: list[float] = []
    for log in sell_logs:
        symbol_profits[log.symbol] += log.pl_nominal
        daily_profits[timezone.localtime(log.timestamp).date().isoformat()] += log.pl_nominal
        key = "profit" if log.pl_nominal > 0 else "loss"
        symbol_profit_counts[log.symbol][key] += 1
        cumulative_profit += log.pl_nominal
        profit_times.append(timezone.localtime(log.timestamp).strftime("%Y-%m-%d %H:%M"))
        cumulative_values.append(float(cumulative_profit))

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError(f"Diagramm-Engine nicht verfügbar: {exc}") from exc

    with _PLOT_LOCK:
        figure, axis = plt.subplots(figsize=(6, 4))
        axis.bar(list(symbol_counts), list(symbol_counts.values()), color="skyblue")
        axis.set_title("Trades pro Symbol")
        bar_chart = _figure_to_base64(figure, plt)

        figure, axis = plt.subplots(figsize=(8, 4))
        axis.plot(profit_times, cumulative_values, marker="o", color="green")
        axis.set_title("Kumulativer Profitverlauf")
        axis.tick_params(axis="x", rotation=45)
        profit_chart = _figure_to_base64(figure, plt)

        figure, axis = plt.subplots(figsize=(6, 4))
        axis.bar(
            list(symbol_profits),
            [float(value) for value in symbol_profits.values()],
            color="lightgreen",
        )
        axis.set_title("Profit pro Symbol")
        profit_symbol_chart = _figure_to_base64(figure, plt)

        figure, axis = plt.subplots(figsize=(8, 4))
        axis.bar(
            list(daily_profits),
            [float(value) for value in daily_profits.values()],
            color="lightcoral",
        )
        axis.set_title("Profit pro Tag")
        axis.tick_params(axis="x", rotation=45)
        profit_daily_chart = _figure_to_base64(figure, plt)

        chart_symbols = list(symbol_profit_counts)
        positions = list(range(len(chart_symbols)))
        figure, axis = plt.subplots(figsize=(8, 4))
        axis.bar(
            [position - 0.2 for position in positions],
            [symbol_profit_counts[symbol]["profit"] for symbol in chart_symbols],
            width=0.4,
            label="Profitabel",
            color="green",
        )
        axis.bar(
            [position + 0.2 for position in positions],
            [symbol_profit_counts[symbol]["loss"] for symbol in chart_symbols],
            width=0.4,
            label="Verlust",
            color="red",
        )
        axis.set_xticks(positions, chart_symbols)
        axis.legend()
        profitability_chart = _figure_to_base64(figure, plt)

    return {
        "config": config,
        "logs": logs,
        "generated_at": timezone.localtime(),
        "total_trades": len(logs),
        "buy_trades": sum(log.action == "buy" for log in logs),
        "sell_trades": len(sell_logs),
        "total_profit": cumulative_profit,
        "available_cash": portfolio["cash"],
        "invested_capital": portfolio["invested"],
        "account_equity": portfolio["equity"],
        "unrealized_profit": portfolio["unrealized_profit"],
        "open_positions": portfolio["positions"],
        "symbol_counts": dict(symbol_counts),
        "bar_chart": bar_chart,
        "profit_chart": profit_chart,
        "symbol_profits": dict(symbol_profits),
        "profit_symbol_chart": profit_symbol_chart,
        "daily_profits": dict(daily_profits),
        "profit_daily_chart": profit_daily_chart,
        "profitable_trades": sum(log.pl_nominal > 0 for log in sell_logs),
        "unprofitable_trades": sum(log.pl_nominal <= 0 for log in sell_logs),
        "symbol_profit_counts": dict(symbol_profit_counts),
        "profitability_chart": profitability_chart,
    }


@login_required
@require_GET
def generate_report(request: HttpRequest, config_id: int) -> HttpResponse:
    """Erzeugt den Trading-Report der eigenen Konfiguration als PDF."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    try:
        context = _build_report_context(config)
    except Exception:
        logger.exception("Trading-Report für Konfiguration %s konnte nicht erstellt werden", config.id)
        return HttpResponse(_REPORT_ERROR, status=503, content_type="text/plain; charset=utf-8")
    return _pdf_response(
        request,
        "trading/report.html",
        context,
        _report_filename(config, "pdf"),
    )


@login_required
@require_GET
def generate_report_html(request: HttpRequest, config_id: int) -> HttpResponse:
    """Erzeugt den Trading-Report der eigenen Konfiguration als HTML-Download."""
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    try:
        context = _build_report_context(config)
        html = render_to_string("trading/report.html", context, request=request)
    except Exception:
        logger.exception("HTML-Report für Konfiguration %s konnte nicht erstellt werden", config.id)
        return HttpResponse(_REPORT_ERROR, status=503, content_type="text/plain; charset=utf-8")
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_report_filename(config, "html")}"'
    return response


@login_required
@require_GET
def generate_report_csv(request: HttpRequest, config_id: int) -> StreamingHttpResponse:
    """Streamt alle Trades der eigenen Konfiguration als CSV.

    Die Zeilen werden erst beim Lesen erzeugt; der Textpuffer wird pro Zeile
    geleert und am Ende, bei Fehlern und bei abgebrochener Antwort geschlossen.
    """
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    queryset = config.logs.all().order_by("timestamp", "id")

    def rows() -> Iterator[str]:
        yield "\ufeff"
        # Erst beim Lesen öffnen; auch bei Stream-Abbruch oder Fehler schließen.
        with io.StringIO(newline="") as buffer:
            writer = csv.writer(buffer)
            writer.writerow(
                [
                    "timestamp",
                    "symbol",
                    "action",
                    "price",
                    "amount",
                    "fee_amount",
                    "order_id",
                    "pl_nominal",
                    "pl_relative",
                    "total_pl",
                    "current_capital",
                    "tank",
                ]
            )
            yield buffer.getvalue()
            for log in queryset.iterator(chunk_size=1000):
                # Nur die aktuelle Zeile puffern, ohne Reste längerer Vorgänger.
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow(
                    [
                        timezone.localtime(log.timestamp).isoformat(),
                        log.symbol,
                        log.action,
                        log.price,
                        log.amount,
                        log.fee_amount,
                        log.order_id,
                        log.pl_nominal,
                        log.pl_relative,
                        log.total_pl,
                        log.current_capital,
                        log.tank,
                    ]
                )
                yield buffer.getvalue()

    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_report_filename(config, "csv")}"'
    return response


def _combination_count(params: Mapping[str, Any], symbol_count: int) -> int:
    """Zählt die Rasterkombinationen eines Backtests über alle Symbole."""
    result = symbol_count
    for prefix in ("acc", "nda", "deltadelta"):
        start = params[f"{prefix}_from"]
        end = params[f"{prefix}_to"]
        step = params[f"{prefix}_steps"]
        result *= math.floor((end - start) / step + 1e-9) + 1
    return result


@login_required
@require_GET
@no_cache_json
def backtesting_status_api(request: HttpRequest) -> JsonResponse:
    """Meldet Verfügbarkeit, Ausführungsmodus und Ressourcenprofil des Backtestings."""
    profile = get_backtest_resource_profile(
        get_server_resources(refresh=request.GET.get("refresh") == "1")
    )
    return JsonResponse(
        {
            "backtesting": get_backtest_runtime_status(force=request.GET.get("refresh") == "1"),
            "resources": profile.resources.as_dict(),
            "resource_profile": profile.as_dict(),
            "web_runtime": runtime_heartbeat.snapshot(),
        }
    )


@login_required
@require_GET
def backtesting_index(request: HttpRequest) -> HttpResponse:
    """Zeigt je eigener Konfiguration eine Übersichtskarte der Backtests."""
    configs = Configuration.objects.filter(user=_authenticated_user(request)).order_by("-id")
    cards = []
    for config in configs:
        tasks = BacktestTask.objects.filter(configuration=config)
        cards.append(
            {
                "config": config,
                "running": tasks.filter(status__in=["pending", "running", "paused"]).count(),
                "completed": tasks.filter(status="completed").count(),
                "last_task": tasks.order_by("-created_at").first(),
            }
        )
    return render(
        request,
        "trading/backtesting_index.html",
        {"cards": cards, "runtime_status": get_backtest_runtime_status()},
    )


@login_required
def backtesting_form(request: HttpRequest, config_id: int) -> HttpResponse:
    """Plant und startet Backtests einer eigenen Konfiguration.

    Args:
        request: GET zeigt Formular und Aufgabenlisten, POST legt einen
            Backtest an bzw. plant ihn.
        config_id: Primärschlüssel der Konfiguration.

    Returns:
        Gerendertes Formular mit Aufgabenlisten oder Weiterleitung nach dem
        Anlegen eines Backtests.
    """
    config = get_object_or_404(Configuration, id=config_id, user=_authenticated_user(request))
    symbols = _symbols(config)
    resource_profile = get_backtest_resource_profile()
    initial = {
        # Konservativer Schnellstart: der Nutzer kann im Formular jederzeit
        # größere Bereiche wählen, sofern das Hardware-Hard-Limit es erlaubt.
        "acc_from": float(config.div_DVA_prev_NDA_threshold_buy) - 0.5,
        "acc_to": float(config.div_DVA_prev_NDA_threshold_buy) + 0.5,
        "acc_steps": 0.25,
        "nda_from": float(config.nda_threshold_buy) - 0.25,
        "nda_to": float(config.nda_threshold_buy) + 0.25,
        "nda_steps": 0.125,
        "deltadelta_from": float(config.deltadelta_threshold_buy) - 0.25,
        "deltadelta_to": float(config.deltadelta_threshold_buy) + 0.25,
        "deltadelta_steps": 0.125,
        "trade_amount": float(config.trade_amount),
        "take_profit": float(config.take_profit),
        "stop_loss": float(config.stop_loss),
        "fee": float(config.fee),
        "max_price_points": min(
            settings.BACKTEST_DEFAULT_PRICE_POINTS,
            resource_profile.max_price_points,
        ),
        "max_grid_points": max(
            2,
            int((resource_profile.max_combinations / max(1, len(symbols))) ** (1 / 3)),
        ),
        "max_combinations": resource_profile.max_combinations,
    }
    form = BacktestForm(
        request.POST or None,
        initial=initial,
        start_capital=config.start_capital,
        resource_profile=resource_profile,
        symbol_count=len(symbols),
    )
    backtest_templates = build_backtest_templates(config, resource_profile)
    runtime_status = get_backtest_runtime_status(force=request.method == "POST")
    if request.method == "POST" and form.is_valid():
        if not runtime_status["available"]:
            form.add_error(
                None,
                "Backtesting ist auf Render Free zum Schutz des Trading-Bots deaktiviert. "
                "REDIS_URL und einen separaten Celery-Worker konfigurieren.",
            )
        else:
            params = form.cleaned_data.copy()
            schedule_backtest = params.pop("schedule_backtest", False)
            scheduled_start_time = params.pop("scheduled_start_time", None)
            combinations = _combination_count(params, len(symbols))
            hard_limit = min(
                int(params.get("max_combinations", resource_profile.max_combinations)),
                resource_profile.max_combinations,
            )
            if combinations > hard_limit:
                form.add_error(
                    None,
                    f"Mit allen Symbolen entstehen {combinations:,} Kombinationen; "
                    f"das Hardwareprofil erlaubt höchstens {hard_limit:,}.",
                )
            else:
                status = "scheduled" if schedule_backtest else "pending"
                backtest_task = BacktestTask.objects.create(
                    configuration=config,
                    symbol=",".join(symbols),
                    parameters=params,
                    result={},
                    status=status,
                    is_scheduled=schedule_backtest,
                    scheduled_start_time=scheduled_start_time,
                )
                if not schedule_backtest:
                    celery_task = dispatch_task(
                        run_backtest,
                        config.id,
                        params,
                        symbols,
                        backtest_task.id,
                        force_local=runtime_status["mode"] == "local-fallback",
                    )
                    BacktestTask.objects.filter(id=backtest_task.id).update(
                        celery_task_id=celery_task.id
                    )
                return redirect("backtesting_form", config_id=config.id)

    if settings.CELERY_TASK_ALWAYS_EAGER:
        interrupted = BacktestTask.objects.filter(
            configuration=config,
            status__in=["pending", "running", "paused"],
            celery_task_id__startswith="eager-",
        )
        for interrupted_task in interrupted:
            if not local_task_is_active(interrupted_task.celery_task_id):
                interrupted_task.status = "failed"
                interrupted_task.result = {
                    "error": "Der lokale Backtest wurde durch einen Prozessneustart unterbrochen."
                }
                interrupted_task.save()

    tasks_running = BacktestTask.objects.filter(
        configuration=config,
        status__in=["pending", "running", "paused"],
    )
    tasks_scheduled = BacktestTask.objects.filter(
        configuration=config,
        status="scheduled",
    ).order_by("scheduled_start_time")
    tasks_completed = list(
        BacktestTask.objects.filter(
            configuration=config,
            status__in=["completed", "failed", "cancelled"],
        ).order_by("-completed_at")[:10]
    )
    for completed_task in tasks_completed:
        # Django-übliche Anreicherung einer Instanz für das Template; das
        # Attribut ist bewusst kein Modellfeld und wird nicht gespeichert.
        completed_task.report_results = _backtest_result_rows(  # type: ignore[attr-defined]
            completed_task
        )
    return render(
        request,
        "trading/backtesting_form.html",
        {
            "config": config,
            "form": form,
            "tasks_running": tasks_running,
            "tasks_scheduled": tasks_scheduled,
            "backtests": tasks_completed,
            "execution_available": runtime_status["available"],
            "runtime_status": runtime_status,
            "symbol_count": len(_symbols(config)),
            "execution_mode": runtime_status["mode"],
            "resource_profile": resource_profile.as_dict(),
            "server_resources": resource_profile.resources.as_dict(),
            "symbol_grid_limit": max(
                2, int((resource_profile.max_combinations / max(1, len(symbols))) ** (1 / 3))
            ),
            "backtest_templates": backtest_templates,
            "initial_runtime_estimate": estimate_backtest_runtime(
                _combination_count(initial, len(symbols)),
                initial["max_price_points"],
            ),
        },
    )


@login_required
@require_POST
def control_backtest(request: HttpRequest, task_id: int) -> HttpResponse:
    """Bricht einen eigenen Backtest ab, pausiert oder setzt ihn fort."""
    task = get_object_or_404(
        BacktestTask,
        id=task_id,
        configuration__user=_authenticated_user(request),
    )
    action = request.POST.get("action")
    if action == "cancel":
        task.cancel()
        if task.celery_task_id and not task.celery_task_id.startswith("eager-"):
            AsyncResult(task.celery_task_id).revoke(terminate=True)
    elif action == "pause":
        task.pause()
    elif action == "resume":
        task.resume()
    else:
        return JsonResponse({"status": "error", "message": "Ungültige Aktion"}, status=400)
    return redirect("backtesting_form", config_id=task.configuration_id)


def _equity_svg(symbol: str, curve: Sequence[Mapping[str, Any]] | None) -> str:
    """Builds a dependency-free inline SVG for browsers and WeasyPrint.

    Args:
        symbol: Marktsymbol; wird escaped in das SVG-Label übernommen.
        curve: Punkte der Equity-Kurve; defekte Punkte werden übersprungen.

    Returns:
        Als sicher markiertes SVG-Markup oder ein leerer String ohne Punkte.
    """
    points: list[tuple[int, float, Any]] = []
    for point in curve or []:
        try:
            raw_equity = point.get("equity")
            if raw_equity is None:
                continue
            value = float(raw_equity)
            index = int(point.get("index", len(points)))
        except (TypeError, ValueError, AttributeError):
            continue
        if math.isfinite(value):
            points.append((index, value, point.get("timestamp")))
    if not points:
        return ""
    width, height = 1000, 260
    left, right, top, bottom = 72, 20, 20, 42
    minimum = min(value for _index, value, _timestamp in points)
    maximum = max(value for _index, value, _timestamp in points)
    spread = maximum - minimum or max(abs(maximum) * 0.01, 1.0)
    minimum -= spread * 0.05
    maximum += spread * 0.05
    first_index, last_index = points[0][0], points[-1][0]
    index_spread = max(1, last_index - first_index)

    def coordinate(index: float, value: float) -> tuple[float, float]:
        x = left + (index - first_index) / index_spread * (width - left - right)
        y = top + (maximum - value) / (maximum - minimum) * (height - top - bottom)
        return x, y

    polyline = " ".join(
        f"{x:.2f},{y:.2f}" for x, y in (coordinate(index, value) for index, value, _ in points)
    )
    start_label = points[0][2] or f"Index {first_index}"
    end_label = points[-1][2] or f"Index {last_index}"
    svg = (
        f'<svg class="equity-chart" viewBox="0 0 {width} {height}" role="img" '
        f'style="width:100%;height:auto;max-height:260px" '
        f'aria-label="Equity-Kurve {escape(str(symbol), quote=True)}">'
        f'<rect width="{width}" height="{height}" fill="#fff"/>'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#6c757d"/>'
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#6c757d"/>'
        f'<polyline points="{polyline}" fill="none" stroke="#0d6efd" stroke-width="3"/>'
        f'<text x="8" y="{top+8}" font-size="14">{maximum:.2f}</text>'
        f'<text x="8" y="{height-bottom}" font-size="14">{minimum:.2f}</text>'
        f'<text x="{left}" y="{height-12}" font-size="12">{escape(str(start_label))}</text>'
        f'<text x="{width-right}" y="{height-12}" text-anchor="end" font-size="12">{escape(str(end_label))}</text>'
        "</svg>"
    )
    return mark_safe(svg)


def _backtest_result_rows(task: BacktestTask) -> list[dict[str, Any]]:
    """Bereitet die Symbolergebnisse eines Backtests für Templates auf."""
    rows: list[dict[str, Any]] = []
    symbol_results = (task.result or {}).get("symbol_results") or {}
    for symbol, result in symbol_results.items():
        report = result.get("report") or {}
        rows.append(
            {
                "symbol": symbol,
                "result": result,
                "report": report,
                "equity_svg": _equity_svg(symbol, report.get("equity_curve")),
            }
        )
    return rows


def _backtest_report_context(task: BacktestTask) -> dict[str, Any]:
    """Baut den Template-Kontext des Backtest-Reports."""
    return {
        "task": task,
        "config": task.configuration,
        "results": _backtest_result_rows(task),
        "global": (task.result or {}).get("global_results") or {},
        "generated_at": timezone.localtime(),
    }


def _owned_backtest(request: HttpRequest, task_id: int) -> BacktestTask:
    """Lädt einen Backtest und erzwingt dabei die Eigentümerprüfung.

    Raises:
        Http404: Wenn der Backtest nicht existiert oder einem anderen Benutzer
            gehört.
    """
    return get_object_or_404(
        BacktestTask.objects.select_related("configuration"),
        id=task_id,
        configuration__user=_authenticated_user(request),
    )


@login_required
@require_GET
def generate_backtest_pdf(request: HttpRequest, task_id: int) -> HttpResponse:
    """Zeigt den Report eines abgeschlossenen eigenen Backtests als PDF."""
    task = _owned_backtest(request, task_id)
    if task.status != "completed":
        return HttpResponse("Backtest ist nicht abgeschlossen.", status=400)
    return _pdf_response(
        request,
        "trading/backtest_report.html",
        _backtest_report_context(task),
        f"backtest-{task.id}.pdf",
        disposition="inline",
    )


@login_required
@require_GET
def generate_backtest_html(request: HttpRequest, task_id: int) -> HttpResponse:
    """Lädt den Report eines abgeschlossenen eigenen Backtests als HTML."""
    task = _owned_backtest(request, task_id)
    if task.status != "completed":
        return HttpResponse("Backtest ist nicht abgeschlossen.", status=400)
    html = render_to_string(
        "trading/backtest_report.html",
        _backtest_report_context(task),
        request=request,
    )
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="backtest-{task.id}.html"'
    return response


@login_required
@require_GET
def generate_backtest_csv(request: HttpRequest, task_id: int) -> HttpResponse:
    """Lädt Kennzahlen, Trades und Equity-Punkte eines Backtests als CSV."""
    task = _owned_backtest(request, task_id)
    if task.status != "completed":
        return HttpResponse("Backtest ist nicht abgeschlossen.", status=400)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="backtest-{task.id}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        [
            "Datensatz",
            "Markt",
            "Typ",
            "Index",
            "Zeitpunkt",
            "Einstiegszeitpunkt",
            "Dauer (Punkte)",
            "Dauer (Sekunden)",
            "Preis",
            "Gebühr",
            "Kapital vorher",
            "Kapital danach",
            "Profit nominal",
            "Profit %",
            "Bruttogewinn",
            "Bruttoverlust",
            "Profit-Faktor",
            "Max. Drawdown %",
            "Käufe",
            "Verkäufe",
            "Win Rate %",
            "Equity-Punkte",
        ]
    )
    global_result = (task.result or {}).get("global_results") or {}
    writer.writerow(
        [
            "Gesamt",
            "",
            "",
            "",
            (task.result or {}).get("end_time"),
            "",
            global_result.get("average_trade_duration_points"),
            global_result.get("average_trade_duration_seconds"),
            "",
            global_result.get("total_fees"),
            "",
            "",
            global_result.get("total_profit"),
            global_result.get("return_percentage"),
            global_result.get("gross_profit"),
            global_result.get("gross_loss"),
            global_result.get("profit_factor"),
            global_result.get("max_drawdown_percentage"),
            "",
            global_result.get("total_trades"),
            "",
            "",
        ]
    )
    for item in _backtest_result_rows(task):
        report = item["report"]
        writer.writerow(
            [
                "Zusammenfassung",
                item["symbol"],
                "",
                "",
                "",
                "",
                report.get("average_trade_duration_points"),
                report.get("average_trade_duration_seconds"),
                "",
                report.get("total_fees"),
                task.configuration.start_capital,
                item["result"].get("best_capital"),
                report.get("net_profit"),
                report.get("return_percentage"),
                report.get("gross_profit"),
                report.get("gross_loss"),
                report.get("profit_factor"),
                report.get("max_drawdown_percentage"),
                report.get("num_buys"),
                report.get("num_sells"),
                report.get("win_rate"),
                len(report.get("equity_curve") or []),
            ]
        )
        for trade in report.get("trades") or []:
            writer.writerow(
                [
                    "Trade",
                    item["symbol"],
                    trade.get("type"),
                    trade.get("index"),
                    trade.get("timestamp"),
                    trade.get("entry_timestamp"),
                    trade.get("duration_points"),
                    trade.get("duration_seconds"),
                    trade.get("price"),
                    trade.get("fee"),
                    trade.get("capital_before"),
                    trade.get("capital_after"),
                    trade.get("profit_nominal"),
                    trade.get("profit_percentage"),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                ]
            )
        for point in report.get("equity_curve") or []:
            row = [""] * 22
            row[0] = "Equity"
            row[1] = item["symbol"]
            row[3] = point.get("index")
            row[4] = point.get("timestamp")
            row[11] = point.get("equity")
            writer.writerow(row)
    return response

@login_required
@require_GET
def analyse_view(request: HttpRequest) -> HttpResponse:
    """Wertet lokal gesammelte Marktdaten mit einem SMA-Crossover aus.

    Es werden ausschließlich vom Bot gestreamte ``DataLog``-Zeilen des
    angemeldeten Benutzers verwendet; die Views rufen keine Börsen-API auf.

    Args:
        request: GET-Parameter ``symbols`` (max. 10) und ``timeframe``.

    Returns:
        Gerenderte Analyseseite; unzureichende Datenlagen werden je Symbol als
        Hinweis ausgewiesen.
    """
    user = _authenticated_user(request)
    symbols = [symbol.strip().upper() for symbol in request.GET.getlist("symbols") if symbol]
    timeframe = request.GET.get("timeframe", "1h")
    timeframe_seconds = {
        "1m": 60,
        "5m": 5 * 60,
        "15m": 15 * 60,
        "1h": 60 * 60,
        "4h": 4 * 60 * 60,
        "1d": 24 * 60 * 60,
    }
    available_symbols = sorted(
        {
            symbol
            for config in Configuration.objects.filter(user=user)
            for symbol in _symbols(config)
        }
    )
    context: dict[str, Any] = {
        "available_symbols": available_symbols,
        "selected_symbols": symbols,
        "selected_timeframe": timeframe,
        "analysis_source": "Lokale, vom Bot gestreamte DataLogs (keine Binance-REST-Requests)",
    }
    if not symbols:
        return render(request, "trading/analyse.html", context)
    if timeframe not in timeframe_seconds or len(symbols) > 10:
        context["error"] = "Ungültiger Zeitrahmen oder zu viele Symbole."
        return render(request, "trading/analyse.html", context)

    analysis_results: list[dict[str, Any]] = []
    bucket_size = timeframe_seconds[timeframe]
    for symbol in symbols:
        source_config_id = (
            DataLog.objects.filter(configuration__user=user, symbol=symbol)
            .order_by("-timestamp", "-id")
            .values_list("configuration_id", flat=True)
            .first()
        )
        if not source_config_id:
            analysis_results.append(
                {
                    "symbol": symbol,
                    "error": "Noch keine lokalen Bot-Marktdaten vorhanden.",
                }
            )
            continue
        source_config = Configuration.objects.get(id=source_config_id)
        rows = list(
            DataLog.objects.filter(configuration_id=source_config_id, symbol=symbol).order_by(
                "-timestamp", "-id"
            )[:20_000]
        )
        rows.reverse()
        closes_by_bucket: dict[int, float] = {}
        for row in rows:
            bucket = int(row.timestamp.timestamp()) // bucket_size
            closes_by_bucket[bucket] = float(row.price)
        closes = list(closes_by_bucket.values())
        if len(closes) < 16:
            analysis_results.append(
                {
                    "symbol": symbol,
                    "error": (
                        f"Nur {len(closes)} abgeschlossene {timeframe}-Intervalle vorhanden; "
                        "mindestens 16 erforderlich. Bot länger sammeln lassen oder kleineren "
                        "Zeitrahmen wählen."
                    ),
                    "source": f"{source_config.name} / {source_config.get_exchange_display()}",
                }
            )
            continue
        short_now = sum(closes[-5:]) / 5
        long_now = sum(closes[-15:]) / 15
        short_previous = sum(closes[-6:-1]) / 5
        long_previous = sum(closes[-16:-1]) / 15
        signal = "Neutral"
        if short_previous <= long_previous and short_now > long_now:
            signal = "Kaufen"
        elif short_previous >= long_previous and short_now < long_now:
            signal = "Verkaufen"
        analysis_results.append(
            {
                "symbol": symbol,
                "signal": signal,
                "source": f"{source_config.name} / {source_config.get_exchange_display()}",
                "candles": len(closes),
                "last_price": closes[-1],
                "sma_short": round(short_now, 8),
                "sma_long": round(long_now, 8),
            }
        )

    context["analysis_results"] = analysis_results
    return render(request, "trading/analyse.html", context)
