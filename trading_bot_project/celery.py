import os

from celery import Celery
from celery.schedules import crontab

from .celery_config import CELERY_RUNTIME_CONFIG

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_bot_project.settings")

app = Celery("trading_bot_project")
# Broker/backend URLs and environment-specific switches come from Django settings.
app.config_from_object("django.conf:settings", namespace="CELERY")
# A single canonical policy prevents CLI arguments and settings drift.
app.conf.update(CELERY_RUNTIME_CONFIG)
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "check-scheduled-backtests": {
        "task": "trading.tasks.schedule_backtests",
        "schedule": crontab(),
        "options": {"queue": "scheduling", "priority": 0},
    },
}


@app.task(bind=True)
def debug_task(self):
    return {"request": repr(self.request)}
