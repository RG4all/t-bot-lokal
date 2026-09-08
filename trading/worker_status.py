import logging
import threading
import time

from celery.exceptions import CeleryError
from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

from trading_bot_project.celery import app

logger = logging.getLogger(__name__)

_STATUS_LOCK = threading.Lock()
_STATUS_CACHE = None
_STATUS_CACHED_AT = 0.0
_STATUS_TTL = 5.0


def _remote_status():
    redis_ok = False
    workers = []
    error = None
    try:
        redis_ok = bool(
            Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1).ping()
        )
        if redis_ok:
            replies = app.control.inspect(timeout=1.5).ping() or {}
            workers = sorted(replies)
    except (RedisError, CeleryError, OSError):
        logger.exception("Backtest-Worker-Verbindung fehlgeschlagen")
        error = "Backtest-Worker vorübergehend nicht erreichbar."
    worker_ok = bool(workers)
    if worker_ok:
        mode = "celery-worker"
        available = True
        degraded = False
    elif not redis_ok and settings.BACKTEST_LOCAL_FALLBACK_ENABLED:
        mode = "local-fallback"
        available = True
        degraded = True
    else:
        # Redis can accept a task while no worker consumes it. Reject instead
        # of silently queueing forever or running a second local copy.
        mode = "worker-unavailable" if redis_ok else "unavailable"
        available = False
        degraded = True
    return {
        "available": available,
        "degraded": degraded,
        "mode": mode,
        "redis": redis_ok,
        "worker": worker_ok,
        "workers": workers,
        "local_fallback_enabled": settings.BACKTEST_LOCAL_FALLBACK_ENABLED,
        "error": error,
    }


def get_backtest_runtime_status(force=False):
    global _STATUS_CACHE, _STATUS_CACHED_AT
    now = time.monotonic()
    with _STATUS_LOCK:
        if not force and _STATUS_CACHE is not None and now - _STATUS_CACHED_AT < _STATUS_TTL:
            return dict(_STATUS_CACHE)
        if settings.CELERY_TASK_ALWAYS_EAGER:
            status = {
                "available": settings.BACKTEST_LOCAL_FALLBACK_ENABLED,
                "degraded": True,
                "mode": (
                    "local-fallback" if settings.BACKTEST_LOCAL_FALLBACK_ENABLED else "unavailable"
                ),
                "redis": False,
                "worker": False,
                "workers": [],
                "local_fallback_enabled": settings.BACKTEST_LOCAL_FALLBACK_ENABLED,
                "error": None,
            }
        else:
            status = _remote_status()
        status["checked_at"] = time.time()
        _STATUS_CACHE = status
        _STATUS_CACHED_AT = now
        return dict(status)
