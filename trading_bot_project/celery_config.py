"""Zentrale Ressourcen- und Stabilitätsrichtlinie für isolierte Celery-Worker."""

import os


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BACKTEST_QUEUE = "backtest"
BACKTEST_PRIORITY = 0
BOT_PRIORITY = 9

CELERY_RUNTIME_CONFIG = {
    # Ein wegwerfbarer Child-Prozess ist die Isolationsgrenze eines Backtests.
    "worker_concurrency": 1,
    "worker_prefetch_multiplier": 1,
    "worker_max_tasks_per_child": 1,
    "worker_max_memory_per_child": _env_int("CELERY_WORKER_MAX_MEMORY_PER_CHILD", 384_000),
    # Kooperativer Task-Code erhält zuerst das Soft-Signal; der harte Abbruch
    # ist nur die letzte Rückfallebene.
    "task_soft_time_limit": 3_600,
    "task_time_limit": 3_900,
    "task_acks_late": True,
    "task_reject_on_worker_lost": True,
    "task_track_started": True,
    "task_default_queue": "default",
    "task_default_priority": BOT_PRIORITY,
    "task_routes": {
        "trading.tasks.run_backtest": {"queue": BACKTEST_QUEUE, "priority": BACKTEST_PRIORITY},
        "trading.tasks.simulate_candidate": {
            "queue": BACKTEST_QUEUE,
            "priority": BACKTEST_PRIORITY,
        },
        "trading.tasks.collect_results": {"queue": BACKTEST_QUEUE, "priority": BACKTEST_PRIORITY},
        "trading.tasks.schedule_backtests": {
            "queue": BACKTEST_QUEUE,
            "priority": BACKTEST_PRIORITY,
        },
    },
    # Redis bildet Prioritäten über getrennte Listen ab.
    "broker_transport_options": {
        "priority_steps": list(range(10)),
        "queue_order_strategy": "priority",
        "visibility_timeout": 4_200,
    },
    "result_expires": 86_400,
}
