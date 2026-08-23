import os

from celery import Celery
from celery.schedules import crontab

from .celery_config import CELERY_RUNTIME_CONFIG

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trading_bot_project.settings")

app = Celery("trading_bot_project")
# Broker-/Backend-URLs und umgebungsspezifische Schalter kommen aus den Django-Settings.
app.config_from_object("django.conf:settings", namespace="CELERY")
# Eine einzige verbindliche Richtlinie verhindert Abweichungen zwischen
# CLI-Argumenten und Settings.
app.conf.update(CELERY_RUNTIME_CONFIG)
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "check-scheduled-backtests": {
        "task": "trading.tasks.schedule_backtests",
        "schedule": crontab(),
        "options": {"queue": "backtest", "priority": 0},
    },
}


@app.task(bind=True)
def debug_task(self):
    return {"request": repr(self.request)}
