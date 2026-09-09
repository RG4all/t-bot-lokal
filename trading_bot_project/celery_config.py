"""Central resource and reliability policy for isolated Celery workers."""

import os


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


BACKTEST_QUEUE = "backtest"
# W7: Der Scheduling-Task (Beat, 1x/min) faechert Backtests erst auf.
# Laege er selbst in "backtest", koennte ein haengender Schedule-Lauf die
# knappen Worker-Slots (concurrency=1, max-tasks-per-child=1) blockieren und
# echte Backtests verhungern lassen. Eigene Queue, gleicher Worker (beide
# Queues per -Q abonniert) -> Trennung ohne zusaetzlichen Dienst.
SCHEDULING_QUEUE = "scheduling"
BACKTEST_PRIORITY = 0
BOT_PRIORITY = 9

CELERY_RUNTIME_CONFIG = {
    # One disposable child is the isolation boundary for one backtest.
    "worker_concurrency": 1,
    "worker_prefetch_multiplier": 1,
    "worker_max_tasks_per_child": 1,
    "worker_max_memory_per_child": _env_int("CELERY_WORKER_MAX_MEMORY_PER_CHILD", 384_000),
    # Cooperative task code receives the soft signal first; hard-kill is last resort.
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
            "queue": SCHEDULING_QUEUE,
            "priority": BACKTEST_PRIORITY,
        },
    },
    # Redis emulates priorities with separate lists.
    "broker_transport_options": {
        "priority_steps": list(range(10)),
        "queue_order_strategy": "priority",
        "visibility_timeout": 4_200,
    },
    "result_expires": 86_400,
}
