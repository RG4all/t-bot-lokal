import logging
import resource
import threading
import time
import uuid
from datetime import datetime
from datetime import timezone as datetime_timezone
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError

from .backtesting import Backtesting
from .models import BacktestTask, Configuration, DataLog

logger = logging.getLogger(__name__)
_MAX_TOTAL_COMBINATIONS = 20_000
_MAX_BACKTEST_PRICE_POINTS = 5_000
_LOCAL_TASK_IDS = set()
_LOCAL_TASK_IDS_LOCK = threading.Lock()
_LOCAL_TASK_SEMAPHORE = threading.Semaphore(1)


class _EagerAsyncResultStub:
    def __init__(self, task_id):
        self.id = task_id


def local_task_is_active(task_id):
    with _LOCAL_TASK_IDS_LOCK:
        return task_id in _LOCAL_TASK_IDS


def _dispatch_local(task, args, kwargs):
    if not settings.BACKTEST_LOCAL_FALLBACK_ENABLED:
        raise RuntimeError(
            "Redis ist nicht erreichbar; separaten Celery-Worker konfigurieren "
            "oder lokalen Fallback ausdrücklich aktivieren."
        )
    synthetic_id = f"eager-{uuid.uuid4()}"
    with _LOCAL_TASK_IDS_LOCK:
        _LOCAL_TASK_IDS.add(synthetic_id)

    def run_in_thread():
        close_old_connections()
        try:
            with _LOCAL_TASK_SEMAPHORE:
                task.apply(
                    args=args,
                    kwargs=kwargs,
                    task_id=synthetic_id,
                    throw=True,
                )
        except Exception:
            logger.exception(
                "event=backtest.local_failed task=%s task_id=%s",
                task.name,
                synthetic_id,
            )
        finally:
            close_old_connections()
            with _LOCAL_TASK_IDS_LOCK:
                _LOCAL_TASK_IDS.discard(synthetic_id)

    threading.Thread(
        target=run_in_thread,
        daemon=True,
        name=f"task-{task.name}-{synthetic_id[-8:]}",
    ).start()
    logger.info("event=backtest.local_dispatched task=%s task_id=%s", task.name, synthetic_id)
    return _EagerAsyncResultStub(synthetic_id)


def dispatch_task(task, *args, force_local=False, **kwargs):
    """Celery first; local serial fallback only when explicitly enabled."""
    if force_local or getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return _dispatch_local(task, args, kwargs)
    try:
        return task.apply_async(args=args, kwargs=kwargs, queue="backtest", priority=0)
    except KombuOperationalError as exc:
        if not settings.BACKTEST_LOCAL_FALLBACK_ENABLED:
            raise RuntimeError(
                "Redis/Backtest-Worker ist nicht erreichbar. Task wurde nicht gestartet."
            ) from exc
        logger.warning("event=backtest.redis_unavailable fallback=local error=%s", exc)
        return _dispatch_local(task, args, kwargs)


def decimal_to_str(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: decimal_to_str(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [decimal_to_str(item) for item in value]
    return value


def _parameter_values(start, end, step):
    start = Decimal(str(start))
    end = Decimal(str(end))
    step = Decimal(str(step))
    if step <= 0 or start > end:
        raise ValueError("Ungültiger Parameterbereich")
    values = []
    current = start
    while current <= end:
        values.append(current)
        current += step
    return values


def _historical_prices(config_id, symbol, limit=_MAX_BACKTEST_PRICE_POINTS):
    limit = max(100, min(_MAX_BACKTEST_PRICE_POINTS, int(limit)))
    prices = list(
        DataLog.objects.filter(configuration_id=config_id, symbol=symbol)
        .order_by("-timestamp")
        .values_list("price", flat=True)[:limit]
    )
    prices.reverse()
    return prices


def _simulate_candidate(
    config,
    historical_prices,
    params,
    symbol,
    acc_threshold,
    nda_threshold,
    deltadelta_threshold,
    indicator_rows=None,
):
    final_capital, report = Backtesting.simulate_trading_detailed(
        historical_prices,
        acc_threshold,
        nda_threshold,
        deltadelta_threshold,
        {
            "start_capital": config.start_capital,
            "trade_amount": params.get("trade_amount", config.trade_amount),
            "take_profit": params.get("take_profit", config.take_profit),
            "stop_loss": params.get("stop_loss", config.stop_loss),
            "fee_percentage": params.get("fee", config.fee),
        },
        indicator_rows=indicator_rows,
    )
    return {
        "symbol": symbol,
        "thresholds": {
            "acc_threshold": acc_threshold,
            "nda_threshold": nda_threshold,
            "deltadelta_threshold": deltadelta_threshold,
        },
        "final_capital": final_capital,
        "report": report,
        "params": params,
    }


@shared_task(bind=True)
def simulate_candidate(
    self,
    config_id,
    params,
    symbol,
    task_id,
    acc_threshold,
    nda_threshold,
    deltadelta_threshold,
):
    """Kompatibler Einzelkandidaten-Task für externe Celery-Aufrufer."""
    del self, task_id
    try:
        config = Configuration.objects.get(id=config_id)
        prices = _historical_prices(
            config.id,
            symbol,
            params.get("max_price_points", _MAX_BACKTEST_PRICE_POINTS),
        )
        if len(prices) < 3:
            return {"symbol": symbol, "error": "Mindestens drei Preispunkte benötigt."}
        return _simulate_candidate(
            config,
            prices,
            params,
            symbol,
            acc_threshold,
            nda_threshold,
            deltadelta_threshold,
        )
    except Exception as exc:
        logger.exception("Simulation für %s fehlgeschlagen", symbol)
        return {"symbol": symbol, "error": str(exc)}


_THRESHOLD_LABELS = {
    "acc_threshold": "Beschleunigung (DVA/prev NDA)",
    "nda_threshold": "NDA (% Preisänderung)",
    "deltadelta_threshold": "DeltaDelta (Momentum)",
}


def _collect_results(results, task):
    symbol_results = {}
    errors = []
    for result in results:
        if not isinstance(result, dict) or "final_capital" not in result:
            if isinstance(result, dict) and result.get("error"):
                errors.append({"symbol": result.get("symbol"), "error": result["error"]})
            continue
        symbol_results.setdefault(result["symbol"], []).append(result)

    processed = {}
    for symbol, candidates in symbol_results.items():
        best = max(candidates, key=lambda item: Decimal(str(item["final_capital"])))
        thresholds = best["thresholds"]
        processed[symbol] = {
            "best_capital": decimal_to_str(best["final_capital"]),
            "best_thresholds": decimal_to_str(thresholds),
            "report": decimal_to_str(best["report"]),
            "optimized_thresholds_str": ", ".join(
                f"{_THRESHOLD_LABELS.get(key, key.removesuffix('_threshold'))}: {value}"
                for key, value in thresholds.items()
            ),
        }

    if not processed:
        raise ValueError("Keine verwertbaren Backtest-Ergebnisse: " + str(errors[:5]))

    total_profit = sum(
        Decimal(result["best_capital"]) - task.configuration.start_capital
        for result in processed.values()
    )
    trades_per_symbol = {
        symbol: result["report"]["num_sells"] for symbol, result in processed.items()
    }
    profit_per_symbol = {
        symbol: str(Decimal(result["best_capital"]) - task.configuration.start_capital)
        for symbol, result in processed.items()
    }
    global_results = {
        "total_profit": str(total_profit),
        "total_trades": sum(trades_per_symbol.values()),
        "trades_per_symbol": trades_per_symbol,
        "profit_per_symbol": profit_per_symbol,
    }
    return {
        "symbol_results": processed,
        "global_results": global_results,
        "errors": errors,
    }


@shared_task
def collect_results(results, task_id):
    task = BacktestTask.objects.select_related("configuration").get(id=task_id)
    try:
        task.result = _collect_results(results, task)
        task.status = "completed"
        task.progress = 100
    except Exception as exc:
        task.result = {"error": str(exc)}
        task.status = "failed"
        logger.exception("Ergebnissammlung für Backtest %s fehlgeschlagen", task_id)
    task.save()
    return task.result


@shared_task(bind=True)
def run_backtest(self, config_id, params, symbols, task_id):
    """Führt einen Backtest speicherschonend und mit kooperativer Pause aus."""
    started_at = timezone.now()
    started_perf = time.perf_counter()
    try:
        task = BacktestTask.objects.select_related("configuration").get(id=task_id)
        config = task.configuration
        task.celery_task_id = self.request.id or task.celery_task_id
        task.status = "running"
        task.progress = 0
        task.save(update_fields=["celery_task_id", "status", "progress"])

        ranges = (
            _parameter_values(params["acc_from"], params["acc_to"], params["acc_steps"]),
            _parameter_values(params["nda_from"], params["nda_to"], params["nda_steps"]),
            _parameter_values(
                params["deltadelta_from"],
                params["deltadelta_to"],
                params["deltadelta_steps"],
            ),
        )
        symbols = [symbol.strip() for symbol in symbols if symbol.strip()]
        total = len(symbols)
        for values in ranges:
            total *= len(values)
        if total <= 0 or total > _MAX_TOTAL_COMBINATIONS:
            raise ValueError(
                f"Ungültige Anzahl Kombinationen ({total}); maximal {_MAX_TOTAL_COMBINATIONS}."
            )

        logger.info(
            "event=backtest.started task_id=%s celery_id=%s config_id=%s symbols=%s combinations=%s",
            task_id,
            self.request.id,
            config_id,
            len(symbols),
            total,
        )
        prices_by_symbol = {
            symbol: _historical_prices(
                config_id,
                symbol,
                params.get("max_price_points", _MAX_BACKTEST_PRICE_POINTS),
            )
            for symbol in symbols
        }
        best_results = {}
        errors = []
        completed = 0
        last_progress = -1
        combinations_per_symbol = len(ranges[0]) * len(ranges[1]) * len(ranges[2])
        control_check_interval = max(10, total // 100)
        for symbol in symbols:
            prices = prices_by_symbol[symbol]
            if len(prices) < 3:
                errors.append({"symbol": symbol, "error": "Mindestens drei Preispunkte benötigt."})
                completed += combinations_per_symbol
                continue
            indicator_rows = [None, None] + [
                Backtesting.calculate_indicators(prices, index) for index in range(2, len(prices))
            ]
            for acc_threshold in ranges[0]:
                for nda_threshold in ranges[1]:
                    for deltadelta_threshold in ranges[2]:
                        if completed % control_check_interval == 0:
                            while True:
                                state = BacktestTask.objects.only("status").get(id=task_id).status
                                if state == "paused":
                                    time.sleep(0.25)
                                    continue
                                if state == "cancelled":
                                    return {"status": "cancelled"}
                                break
                        candidate = _simulate_candidate(
                            config,
                            prices,
                            params,
                            symbol,
                            acc_threshold,
                            nda_threshold,
                            deltadelta_threshold,
                            indicator_rows,
                        )
                        previous_best = best_results.get(symbol)
                        if (
                            previous_best is None
                            or candidate["final_capital"] > previous_best["final_capital"]
                        ):
                            best_results[symbol] = candidate
                        completed += 1
                        progress = min(99, int(completed / total * 100))
                        if progress != last_progress:
                            task.update_progress(progress)
                            last_progress = progress

        task.refresh_from_db(fields=["status"])
        if task.status == "cancelled":
            logger.info("event=backtest.cancelled task_id=%s completed=%s", task_id, completed)
            return {"status": "cancelled"}
        result = _collect_results([*best_results.values(), *errors], task)
        ended_at = timezone.now()
        duration_seconds = time.perf_counter() - started_perf
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        result.update(
            {
                "start_time": started_at.isoformat(),
                "end_time": ended_at.isoformat(),
                "duration": str(ended_at - started_at),
                "metrics": {
                    "duration_seconds": round(duration_seconds, 3),
                    "peak_rss_mb": round(peak_rss_mb, 2),
                    "combinations": total,
                    "symbols": len(symbols),
                    "price_points_total": sum(len(values) for values in prices_by_symbol.values()),
                    "price_points_max": max(
                        (len(values) for values in prices_by_symbol.values()), default=0
                    ),
                },
            }
        )
        task.result = result
        task.status = "completed"
        task.progress = 100
        task.save()
        task.update_progress(100)
        logger.info(
            "event=backtest.completed task_id=%s duration=%.3f peak_rss_mb=%.2f combinations=%s",
            task_id,
            duration_seconds,
            peak_rss_mb,
            total,
        )
        return result
    except BacktestTask.DoesNotExist:
        logger.error("event=backtest.missing task_id=%s", task_id)
        return {"error": "Backtest nicht gefunden"}
    except Exception as exc:
        logger.exception(
            "event=backtest.failed task_id=%s duration=%.3f error_type=%s",
            task_id,
            time.perf_counter() - started_perf,
            type(exc).__name__,
        )
        BacktestTask.objects.filter(id=task_id).update(
            status="failed",
            result={"error": str(exc)},
            completed_at=timezone.now(),
        )
        raise


@shared_task
def schedule_backtests():
    """Beansprucht und startet alle fälligen geplanten Backtests genau einmal."""
    now = timezone.now()
    due_ids = list(
        BacktestTask.objects.filter(
            status="scheduled",
            scheduled_start_time__lte=now,
        ).values_list("id", flat=True)
    )
    dispatched = 0
    for task_id in due_ids:
        claimed = BacktestTask.objects.filter(id=task_id, status="scheduled").update(
            status="pending",
            is_scheduled=False,
            scheduled_start_time=None,
        )
        if not claimed:
            continue
        task = BacktestTask.objects.get(id=task_id)
        symbols = [symbol.strip() for symbol in task.symbol.split(",") if symbol.strip()]
        celery_task = dispatch_task(
            run_backtest,
            task.configuration_id,
            task.parameters,
            symbols,
            task.id,
        )
        BacktestTask.objects.filter(id=task.id).update(celery_task_id=celery_task.id)
        dispatched += 1
    logger.info("%s geplante Backtests gestartet", dispatched)
    return {"dispatched": dispatched, "checked_at": datetime.now(datetime_timezone.utc).isoformat()}
