import base64
import csv
import logging
import math
import re
import threading
from collections import Counter, defaultdict
from decimal import Decimal
from functools import lru_cache
from io import BytesIO

import markdown
from celery.result import AsyncResult
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    BacktestForm,
    ConfigurationForm,
    DashboardConfigurationForm,
    LoginForm,
    RegistrationForm,
)
from .market_data import MarketDataError, SymbolValidationError, validate_exchange_symbols
from .models import BacktestTask, Configuration, DataLog, ErrorLog
from .monitoring import runtime_heartbeat
from .symbols import get_available_symbols
from .tasks import dispatch_task, local_task_is_active, run_backtest
from .trading_bot import bot_manager
from .worker_status import get_backtest_runtime_status

logger = logging.getLogger(__name__)
_PLOT_LOCK = threading.Lock()
_MAX_API_ROWS = 5_000
_MAX_LOG_ROWS = 2_000
_MAX_TOTAL_BACKTEST_COMBINATIONS = 20_000


def _safe_next_url(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return None


def _redirect_dashboard(config_id):
    return redirect(f"{reverse('dashboard')}?config_id={config_id}")


def _latest_rows(queryset, limit):
    rows = list(queryset.order_by("-timestamp", "-id")[:limit])
    rows.reverse()
    return rows


def _symbols(config):
    return [symbol.strip() for symbol in config.symbols.split(",") if symbol.strip()]


def _realized_profit(config):
    return config.logs.filter(action="sell").aggregate(total=Sum("pl_nominal"))["total"] or Decimal(
        0
    )


def _cash_flow(log):
    notional = log.amount * log.price
    return -(notional + log.fee_amount) if log.action == "buy" else notional - log.fee_amount


def _portfolio_snapshot(config):
    realized_profit = _realized_profit(config)
    invested = Decimal(0)
    market_value = Decimal(0)
    positions = []
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


def _cash_series(logs, opening_cash):
    cash = opening_cash
    series = []
    for log in logs:
        cash += _cash_flow(log)
        series.append({"t": log.timestamp.isoformat(), "v": float(cash)})
    return series


def calculate_performance_metrics(logs):
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


@require_GET
def health_view(request):
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})


@lru_cache(maxsize=1)
def _render_manual():
    candidates = (
        settings.BASE_DIR / "docs" / "MANUAL.md",
        settings.BASE_DIR / "MANUAL.md",
    )
    for candidate in candidates:
        if candidate.exists():
            source = candidate.read_text(encoding="utf-8")
            return markdown.markdown(
                source,
                extensions=["extra", "fenced_code", "tables", "toc", "sane_lists", "codehilite"],
                extension_configs={
                    "codehilite": {
                        "css_class": "codehilite",
                        "guess_lang": False,
                        "noclasses": False,
                    }
                },
                output_format="html5",
            )
    return "<p>Handbuchdatei <code>MANUAL.md</code> konnte nicht gefunden werden.</p>"


@require_GET
def help_view(request):
    return render(
        request,
        "trading/help.html",
        {"manual_html": mark_safe(_render_manual()), "version": settings.APP_VERSION},
    )


def home(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")


def passphrase_gate_view(request):
    requested_next = request.POST.get("next") or request.GET.get("next")
    next_url = _safe_next_url(request, requested_next)
    if request.session.get("passphrase_verified"):
        return redirect(next_url or "login")

    error = None
    if request.method == "POST":
        import secrets

        submitted = request.POST.get("passphrase", "").strip()
        if secrets.compare_digest(submitted, str(settings.PASSPHRASE)):
            request.session.cycle_key()
            request.session["passphrase_verified"] = True
            return redirect(next_url or "login")
        error = "Falsche Passphrase. Zugang verweigert."
    return render(
        request,
        "trading/passphrase_gate.html",
        {"error": error, "next": next_url or ""},
    )


def register_view(request):
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        logger.info("Benutzer %s wurde registriert", user.username)
        messages.success(request, "Registrierung erfolgreich. Bitte jetzt anmelden.")
        return redirect("login")
    return render(request, "trading/register.html", {"form": form})


def login_view(request):
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
def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def config_view(request):
    form = ConfigurationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        config = form.save(commit=False)
        config.user = request.user
        config.save()
        messages.success(request, "Konfiguration wurde erstellt.")
        return redirect("config_list")
    return render(request, "trading/config_form.html", {"form": form})


@login_required
def config_list_view(request):
    configs = Configuration.objects.filter(user=request.user).order_by("-id")
    return render(request, "trading/config_list.html", {"configs": configs})


@login_required
def config_edit_view(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
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
def config_activate(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    try:
        validate_exchange_symbols(config.exchange, config.market, _symbols(config))
    except (SymbolValidationError, MarketDataError, ValueError) as exc:
        logger.warning("Konfigurationsprüfung für %s fehlgeschlagen: %s", config.id, exc)
        ErrorLog.objects.create(
            configuration=config,
            severity="warning",
            source="views.config_activate.validation",
            exception_type=type(exc).__name__,
            message=str(exc)[:4000],
            details={
                "exchange": config.exchange,
                "market": config.market,
                "symbols": _symbols(config),
            },
        )
        messages.error(request, f"Bot nicht gestartet: {exc}")
        return redirect("config_list")
    try:
        bot_manager.start_bot(config)
    except Exception as exc:
        logger.exception("Bot-Start für Konfiguration %s fehlgeschlagen", config.id)
        ErrorLog.objects.create(
            configuration=config,
            severity="critical",
            source="views.config_activate",
            exception_type=type(exc).__name__,
            message=str(exc)[:4000],
            details={
                "exchange": config.exchange,
                "market": config.market,
                "symbols": _symbols(config),
            },
        )
        messages.error(request, f"Bot konnte nicht gestartet werden: {exc}")
    else:
        if not config.is_running:
            config.is_running = True
            config.save(update_fields=["is_running"])
        messages.success(request, "Bot wurde aktiviert.")
    return redirect("config_list")


@login_required
@require_POST
def config_deactivate(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    bot_manager.stop_bot(config)
    if config.is_running:
        config.is_running = False
        config.save(update_fields=["is_running"])
    messages.success(request, "Bot wurde deaktiviert.")
    return redirect("config_list")


@login_required
def config_delete(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
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
def dashboard_view(request):
    config_id = request.GET.get("config_id")
    if config_id:
        config = get_object_or_404(Configuration, id=config_id, user=request.user)
    else:
        config = Configuration.objects.filter(user=request.user).order_by("-id").first()

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
        "all_configs": Configuration.objects.filter(user=request.user).order_by("-id"),
    }
    return render(request, "trading/dashboard.html", context)


@login_required
@require_POST
def reset_log(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
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


def _validated_start_time(request):
    raw = request.GET.get("start_time")
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


@login_required
@require_GET
def symbol_suggestions_api(request):
    exchange = request.GET.get("exchange", "").strip().lower()
    market = request.GET.get("market", "").strip().lower()
    query = request.GET.get("q", "").strip().upper()
    valid_exchanges = dict(Configuration.EXCHANGE_CHOICES)
    valid_markets = dict(Configuration.MARKET_CHOICES)
    if exchange not in valid_exchanges or market not in valid_markets:
        return JsonResponse({"error": "Ungültige Exchange oder Marktart"}, status=400)
    try:
        symbols = get_available_symbols(exchange, market)
    except MarketDataError as exc:
        return JsonResponse({"error": str(exc), "suggestions": []}, status=503)
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
def data_logs_api(request):
    config = get_object_or_404(
        Configuration,
        id=request.GET.get("config_id"),
        user=request.user,
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
def trades_api(request):
    config = get_object_or_404(
        Configuration,
        id=request.GET.get("config_id"),
        user=request.user,
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
def info_api(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    logs = _latest_rows(config.logs.all(), _MAX_LOG_ROWS)
    portfolio = _portfolio_snapshot(config)
    metrics = calculate_performance_metrics(logs)

    window_realized = sum(
        (log.pl_nominal for log in logs if log.action == "sell"),
        Decimal(0),
    )
    realized_capital = config.start_capital + portfolio["realized_profit"] - window_realized
    equity = []
    for log in logs:
        if log.action == "sell":
            realized_capital += log.pl_nominal
        equity.append({"t": log.timestamp.isoformat(), "v": float(realized_capital)})

    window_cash_flow = sum((_cash_flow(log) for log in logs), Decimal(0))
    cash_curve = _cash_series(logs, portfolio["cash"] - window_cash_flow)

    peak = float(config.start_capital)
    max_drawdown = 0.0
    current_drawdown = 0.0
    sell_returns = []
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
def bot_status_api(request):
    config_id = request.GET.get("config_id")
    if not config_id:
        return JsonResponse({"error": "config_id fehlt"}, status=400)
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    if config.is_running and not bot_manager.is_running(config.id):
        try:
            bot_manager.start_bot(config)
        except Exception as exc:
            logger.exception("Automatischer Neustart für %s fehlgeschlagen", config.id)
            ErrorLog.objects.create(
                configuration=config,
                severity="critical",
                source="views.bot_status_api",
                exception_type=type(exc).__name__,
                message=str(exc)[:4000],
                details={"exchange": config.exchange, "market": config.market},
            )
            config.is_running = False
            config.save(update_fields=["is_running"])
    status = bot_manager.status(config.id)
    status["is_running_flag"] = config.is_running
    return JsonResponse(status)


@login_required
@require_GET
def logs_api(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
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
def manual_sell_view(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    symbol = request.POST.get("symbol", "").strip()
    if symbol not in _symbols(config):
        return JsonResponse({"status": "error", "message": "Ungültiges Symbol"}, status=400)
    try:
        bot_manager.manual_sell(config.id, symbol)
        return JsonResponse({"status": "ok"})
    except ValueError as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Manueller Verkauf für %s/%s fehlgeschlagen", config.id, symbol)
        ErrorLog.objects.create(
            configuration=config,
            severity="critical",
            source="views.manual_sell",
            exception_type=type(exc).__name__,
            message=str(exc)[:4000],
            details={"symbol": symbol, "exchange": config.exchange},
        )
        return JsonResponse(
            {"status": "error", "message": "Verkauf fehlgeschlagen."},
            status=500,
        )


@login_required
@require_POST
def kill_switch_view(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    if request.POST.get("confirm1") != "LIQUIDATE" or request.POST.get("confirm2") != "LIQUIDATE":
        return JsonResponse(
            {"status": "error", "message": "Doppelte Bestätigung fehlt."},
            status=400,
        )
    try:
        result = bot_manager.kill_switch(config.id)
    except ValueError as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Kill-Switch für Konfiguration %s fehlgeschlagen", config.id)
        ErrorLog.objects.create(
            configuration=config,
            severity="critical",
            source="views.kill_switch",
            exception_type=type(exc).__name__,
            message=str(exc)[:4000],
            details={"exchange": config.exchange, "symbols": _symbols(config)},
        )
        return JsonResponse(
            {"status": "error", "message": "Kill-Switch fehlgeschlagen. Siehe Fehler-Log."},
            status=500,
        )
    status = "ok" if not result["errors"] else "partial"
    return JsonResponse({"status": status, **result})


@login_required
@require_GET
def error_log_view(request):
    configs = Configuration.objects.filter(user=request.user).order_by("name")
    base_queryset = ErrorLog.objects.filter(configuration__user=request.user)
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
def error_log_resolve(request, error_id):
    error = get_object_or_404(
        ErrorLog,
        id=error_id,
        configuration__user=request.user,
    )
    error.resolved = request.POST.get("action") != "reopen"
    error.save(update_fields=["resolved"])
    return redirect("error_log")


def _figure_to_base64(figure, pyplot):
    buffer = BytesIO()
    figure.savefig(buffer, format="png", bbox_inches="tight")
    buffer.seek(0)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    pyplot.close(figure)
    return encoded


def _pdf_response(request, template, context, filename, disposition="attachment"):
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        logger.exception("PDF-Engine ist nicht verfügbar")
        return HttpResponse(f"PDF-Engine nicht verfügbar: {exc}", status=503)
    html_string = render_to_string(template, context, request=request)
    pdf = HTML(
        string=html_string,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


def _report_filename(config, extension):
    def safe_component(value):
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


def _build_report_context(config):
    logs = list(config.logs.all().order_by("timestamp", "id"))
    portfolio = _portfolio_snapshot(config)
    sell_logs = [log for log in logs if log.action == "sell"]
    symbol_counts = Counter(log.symbol for log in logs)
    symbol_profits = defaultdict(Decimal)
    daily_profits = defaultdict(Decimal)
    symbol_profit_counts = defaultdict(lambda: {"profit": 0, "loss": 0})
    cumulative_profit = Decimal(0)
    profit_times = []
    cumulative_values = []
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
def generate_report(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    try:
        context = _build_report_context(config)
    except RuntimeError as exc:
        logger.exception("Trading-Report konnte nicht erstellt werden")
        return HttpResponse(str(exc), status=503)
    return _pdf_response(
        request,
        "trading/report.html",
        context,
        _report_filename(config, "pdf"),
    )


@login_required
@require_GET
def generate_report_html(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    try:
        context = _build_report_context(config)
    except RuntimeError as exc:
        logger.exception("HTML-Report konnte nicht erstellt werden")
        return HttpResponse(str(exc), status=503)
    html = render_to_string("trading/report.html", context, request=request)
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_report_filename(config, "html")}"'
    return response


class _CsvEcho:
    def write(self, value):
        return value


@login_required
@require_GET
def generate_report_csv(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    queryset = config.logs.all().order_by("timestamp", "id")
    writer = csv.writer(_CsvEcho())

    def rows():
        yield "\ufeff"
        yield writer.writerow(
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
        for log in queryset.iterator(chunk_size=1000):
            yield writer.writerow(
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

    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_report_filename(config, "csv")}"'
    return response


def _combination_count(params, symbol_count):
    result = symbol_count
    for prefix in ("acc", "nda", "deltadelta"):
        start = params[f"{prefix}_from"]
        end = params[f"{prefix}_to"]
        step = params[f"{prefix}_steps"]
        result *= math.floor((end - start) / step + 1e-9) + 1
    return result


@login_required
@require_GET
def backtesting_status_api(request):
    return JsonResponse(
        {
            "backtesting": get_backtest_runtime_status(force=request.GET.get("refresh") == "1"),
            "web_runtime": runtime_heartbeat.snapshot(),
        }
    )


@login_required
@require_GET
def backtesting_index(request):
    configs = Configuration.objects.filter(user=request.user).order_by("-id")
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
def backtesting_form(request, config_id):
    config = get_object_or_404(Configuration, id=config_id, user=request.user)
    initial = {
        "acc_from": float(config.div_DVA_prev_NDA_threshold_buy) - 1,
        "acc_to": float(config.div_DVA_prev_NDA_threshold_buy) + 1,
        "acc_steps": 0.1,
        "nda_from": float(config.nda_threshold_buy) - 0.5,
        "nda_to": float(config.nda_threshold_buy) + 0.5,
        "nda_steps": 0.05,
        "deltadelta_from": float(config.deltadelta_threshold_buy) - 0.5,
        "deltadelta_to": float(config.deltadelta_threshold_buy) + 0.5,
        "deltadelta_steps": 0.05,
        "trade_amount": float(config.trade_amount),
        "take_profit": float(config.take_profit),
        "stop_loss": float(config.stop_loss),
        "fee": float(config.fee),
        "max_price_points": settings.BACKTEST_DEFAULT_PRICE_POINTS,
    }
    form = BacktestForm(
        request.POST or None,
        initial=initial,
        start_capital=config.start_capital,
    )
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
            symbols = _symbols(config)
            combinations = _combination_count(params, len(symbols))
            if combinations > _MAX_TOTAL_BACKTEST_COMBINATIONS:
                form.add_error(
                    None,
                    f"Mit allen Symbolen entstehen {combinations:,} Kombinationen; "
                    f"maximal {_MAX_TOTAL_BACKTEST_COMBINATIONS:,} sind erlaubt.",
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
    tasks_completed = BacktestTask.objects.filter(
        configuration=config,
        status__in=["completed", "failed", "cancelled"],
    ).order_by("-completed_at")[:10]
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
        },
    )


@login_required
@require_POST
def control_backtest(request, task_id):
    task = get_object_or_404(
        BacktestTask,
        id=task_id,
        configuration__user=request.user,
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


@login_required
@require_GET
def generate_backtest_pdf(request, task_id):
    task = get_object_or_404(
        BacktestTask.objects.select_related("configuration"),
        id=task_id,
        configuration__user=request.user,
    )
    if task.status != "completed":
        return HttpResponse("Backtest ist nicht abgeschlossen.", status=400)
    return _pdf_response(
        request,
        "trading/backtest_report.html",
        {"task": task, "config": task.configuration},
        f"backtest-{task.id}.pdf",
        disposition="inline",
    )


@login_required
@require_GET
def analyse_view(request):
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
            for config in Configuration.objects.filter(user=request.user)
            for symbol in _symbols(config)
        }
    )
    context = {
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

    analysis_results = []
    bucket_size = timeframe_seconds[timeframe]
    for symbol in symbols:
        source_config_id = (
            DataLog.objects.filter(configuration__user=request.user, symbol=symbol)
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
        closes_by_bucket = {}
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
