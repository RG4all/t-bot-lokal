import os
import resource
import threading
import time


class RuntimeHeartbeat:
    """Ressourcenschonende Überwachung der Scheduler-Verzögerung im Web-/Bot-Prozess."""

    def __init__(self, interval=1.0):
        self.interval = interval
        self.started_at = time.time()
        self._last_tick = time.monotonic()
        self._max_lag = 0.0
        self._lock = threading.Lock()
        self._started = False

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._run, daemon=True, name="runtime-heartbeat").start()

    def _run(self):
        target = time.monotonic() + self.interval
        while True:
            time.sleep(max(0, target - time.monotonic()))
            now = time.monotonic()
            lag = max(0, now - target)
            with self._lock:
                self._last_tick = now
                self._max_lag = max(self._max_lag, lag)
            target = now + self.interval

    def snapshot(self):
        with self._lock:
            age = time.monotonic() - self._last_tick
            max_lag = self._max_lag
            # Fenster nach jeder Messung zurücksetzen, aktuelle Verzögerung behalten.
            self._max_lag = age
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "heartbeat_age_seconds": round(age, 3),
            "max_scheduler_lag_ms": round(max_lag * 1000, 2),
            "peak_rss_mb": round(rss_kb / 1024, 2),
            "threads": threading.active_count(),
            "responsive": age < self.interval * 3,
        }


runtime_heartbeat = RuntimeHeartbeat()
