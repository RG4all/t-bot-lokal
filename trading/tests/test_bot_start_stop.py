"""Tests für die Race Condition in TradingBotManager.start_bot/stop_bot.

Vor dem Fix rief ``start_bot()`` während gehaltenem ``self._lock`` die
öffentliche Methode ``is_running()`` auf, die denselben Lock intern erneut
erwirbt. Mit ``threading.RLock`` entsteht kein Deadlock, aber:

- die verschachtelte Lock-Acquisition ist ein Code-Smell und würde bei einem
  Wechsel auf ``threading.Lock`` hängen;
- die Laufzustandsprüfung ist nicht klar in eine lock-freie interne und eine
  lockende öffentliche API getrennt;
- gleichzeitige Start-/Status-Aufrufe (Activate + Polling von
  ``/api/bot/status/``) sollen für dieselbe Konfiguration niemals einen
  zweiten Trading-Thread erzeugen.

Die Quellcode-Tests reproduzieren den Vorzustand (rot ohne
``_is_running_unlocked``). Die Laufzeittests decken den Integritätspfad ab:
doppelte Paper-Bots derselben Konfiguration.

Basierend auf ARENA_AI_PROMPTS.md Prompt 12 und SECURITY_AUDIT.md Abschnitt 3.1.
"""

from __future__ import annotations

import inspect
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from trading import trading_bot as trading_bot_module
from trading import views
from trading.trading_bot import TradingBotManager

BOT_PATH = Path(trading_bot_module.__file__).resolve()
VIEWS_PATH = Path(views.__file__).resolve()

_PUBLIC_IS_RUNNING = re.compile(r"(?<!_)\bis_running\s*\(")


class _DepthLock:
    """RLock-Wrapper, der die Verschachtelungstiefe protokolliert."""

    def __init__(self):
        self._inner = threading.RLock()
        self.depth = 0
        self.max_depth = 0
        self.acquisitions = 0

    def acquire(self, blocking=True, timeout=-1):
        acquired = self._inner.acquire(blocking, timeout)
        if acquired:
            self.depth += 1
            self.acquisitions += 1
            self.max_depth = max(self.max_depth, self.depth)
        return acquired

    def release(self):
        self.depth -= 1
        self._inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class _FakeTradingBot:
    """Duck-Type ohne Event-Loop, Exchange oder DB – nur für den Manager."""

    instances = []

    def __init__(self, config, on_exit=None):
        self.config = config
        self.config_id = config.id
        self.on_exit = on_exit
        self.running = True
        self._alive = True
        self.started = False
        self.stopped = False
        self.join_calls = 0
        self.positions = {}
        self.pending_trading_logs = []
        self.started_at = time.time()
        self.last_error = None
        self.last_error_at = None
        self.last_success_at = None
        self.loop = None
        type(self).instances.append(self)

    def start(self):
        self.started = True
        self._alive = True
        self.running = True

    def is_alive(self):
        return self._alive

    def stop(self):
        self.stopped = True
        self.running = False

    def join(self, timeout=None):
        self.join_calls += 1
        self._alive = False
        if self.on_exit:
            self.on_exit(self.config_id, self)


def _method_source(cls, name):
    return inspect.getsource(getattr(cls, name))


class BotManagerSourceTests(SimpleTestCase):
    """Statische Prüfungen – am Ausgangsstand rot, nach dem Fix grün."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = BOT_PATH.read_text(encoding="utf-8")
        cls.views_source = VIEWS_PATH.read_text(encoding="utf-8")

    def test_unlocked_helper_exists_without_lock_acquisition(self):
        """``_is_running_unlocked`` darf den Manager-Lock nicht selbst nehmen.

        Roher Test vor dem Fix: Die Methode existierte nicht; ``is_running``
        und ``start_bot`` teilten sich keine lock-freie Prüfung.
        """
        self.assertIn("def _is_running_unlocked(self, config_id)", self.source)
        unlocked = _method_source(TradingBotManager, "_is_running_unlocked")
        self.assertNotIn("with self._lock", unlocked)
        self.assertNotIn(".acquire(", unlocked)
        self.assertIn("self.bots.get", unlocked)
        self.assertIn("is_alive()", unlocked)

    def test_public_is_running_uses_unlocked_helper_under_lock(self):
        """Die öffentliche API muss den Lock halten und dann lock-frei prüfen."""
        public = _method_source(TradingBotManager, "is_running")
        self.assertIn("with self._lock", public)
        self.assertIn("self._is_running_unlocked(", public)

    def test_start_bot_does_not_reenter_public_is_running(self):
        """``start_bot`` darf ``is_running()`` nicht unter gehaltenem Lock rufen.

        Roher Test vor dem Fix: ``if self.is_running(config.id)`` innerhalb
        von ``with self._lock`` – verschachtelte Lock-Acquisition.
        """
        start = _method_source(TradingBotManager, "start_bot")
        self.assertIn("with self._lock", start)
        self.assertIn("self._is_running_unlocked(", start)
        self.assertIsNone(_PUBLIC_IS_RUNNING.search(start))

    def test_stop_bot_uses_unlocked_helper_under_lock(self):
        """``stop_bot`` prüft den Laufzustand lock-frei unter dem bereits gehaltenen Lock."""
        stop = _method_source(TradingBotManager, "stop_bot")
        self.assertIn("with self._lock", stop)
        self.assertIn("self._is_running_unlocked(", stop)
        self.assertIsNone(_PUBLIC_IS_RUNNING.search(stop))

    def test_external_callers_use_public_is_running(self):
        """Views dürfen die interne unlocked-Variante nicht direkt aufrufen."""
        self.assertIn("bot_manager.is_running(", self.views_source)
        self.assertNotIn("bot_manager._is_running_unlocked(", self.views_source)


class BotManagerLockTests(SimpleTestCase):
    """Laufzeit- und Integritätsprüfungen des Bot-Managers."""

    def setUp(self):
        _FakeTradingBot.instances = []
        self.manager = TradingBotManager()
        self.config = SimpleNamespace(id=42)
        self.patcher = patch.object(trading_bot_module, "TradingBot", _FakeTradingBot)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_start_bot_lock_depth_is_one(self):
        """Start darf den Lock nicht verschachtelt erwerben.

        Vor dem Fix war ``max_depth == 2`` (start_bot + is_running).
        """
        depth_lock = _DepthLock()
        self.manager._lock = depth_lock
        self.manager.start_bot(self.config)
        self.assertEqual(depth_lock.depth, 0)
        self.assertEqual(depth_lock.max_depth, 1)

    def test_is_running_lock_depth_is_one(self):
        self.manager.start_bot(self.config)
        depth_lock = _DepthLock()
        self.manager._lock = depth_lock
        self.assertTrue(self.manager.is_running(self.config.id))
        self.assertEqual(depth_lock.max_depth, 1)

    def test_stop_bot_lock_depth_is_one(self):
        self.manager.start_bot(self.config)
        depth_lock = _DepthLock()
        self.manager._lock = depth_lock
        self.manager.stop_bot(self.config)
        self.assertEqual(depth_lock.max_depth, 1)

    def test_start_bot_does_not_deadlock_on_non_reentrant_lock(self):
        """Angriffspfad: RLock durch Lock ersetzt → alter Code hängt.

        Ein nicht-reentrant Lock reproduziert den Deadlock, den RLock nur
        verdeckt. Der Aufruf muss in unter zwei Sekunden zurückkehren.
        """
        self.manager._lock = threading.Lock()
        finished = threading.Event()
        error = []

        def run():
            try:
                self.manager.start_bot(self.config)
            except Exception as exc:  # pragma: no cover - nur Testdiagnose
                error.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(
            target=run, name="start-bot-non-reentrant", daemon=True
        )
        worker.start()
        self.assertTrue(
            finished.wait(timeout=2),
            "start_bot hat auf einem nicht-reentranten Lock blockiert",
        )
        worker.join(timeout=1)
        self.assertFalse(error)
        self.assertTrue(self.manager.is_running(self.config.id))

    def test_duplicate_start_returns_same_instance(self):
        first = self.manager.start_bot(self.config)
        second = self.manager.start_bot(self.config)
        self.assertIs(first, second)
        self.assertEqual(len(_FakeTradingBot.instances), 1)
        self.assertTrue(first.started)

    def test_concurrent_starts_do_not_spawn_duplicate_bots(self):
        """Gleichzeitige Activate-/Status-Aufrufe dürfen nur einen Bot starten.

        Angriffsszenario: mehrere Threads (Dashboard-Activate plus
        ``bot_status_api``-Polling) rufen ``start_bot`` für dieselbe
        Konfiguration. Zwei lebende Threads würden Marktdaten doppelt
        abfragen und Paper-Orders verdoppeln.
        """
        workers = 16
        barrier = threading.Barrier(workers)
        results = [None] * workers
        errors = []

        def run(index):
            try:
                barrier.wait(timeout=2)
                results[index] = self.manager.start_bot(self.config)
            except Exception as exc:  # pragma: no cover - nur Testdiagnose
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(index,), name=f"start-bot-{index}")
            for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        self.assertFalse(errors)
        self.assertEqual(len(_FakeTradingBot.instances), 1)
        self.assertTrue(all(result is results[0] for result in results))
        self.assertTrue(self.manager.is_running(self.config.id))

    def test_concurrent_starts_for_distinct_configs_stay_independent(self):
        other = SimpleNamespace(id=99)
        barrier = threading.Barrier(2)
        results = {}

        def run(config):
            barrier.wait(timeout=2)
            results[config.id] = self.manager.start_bot(config)

        threads = [
            threading.Thread(target=run, args=(self.config,)),
            threading.Thread(target=run, args=(other,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        self.assertEqual(len(_FakeTradingBot.instances), 2)
        self.assertIsNot(results[self.config.id], results[other.id])
        self.assertTrue(self.manager.is_running(self.config.id))
        self.assertTrue(self.manager.is_running(other.id))

    def test_dead_bot_is_forgotten_and_can_be_restarted(self):
        """Ein beendeter Thread darf den Slot nicht blockieren."""
        first = self.manager.start_bot(self.config)
        first._alive = False
        self.assertFalse(self.manager.is_running(self.config.id))
        self.assertNotIn(self.config.id, self.manager.bots)

        second = self.manager.start_bot(self.config)
        self.assertIsNot(first, second)
        self.assertTrue(self.manager.is_running(self.config.id))
        self.assertEqual(len(_FakeTradingBot.instances), 2)

    def test_stop_bot_stops_alive_bot_and_accepts_numeric_id(self):
        bot = self.manager.start_bot(self.config)
        self.manager.stop_bot(self.config.id)
        self.assertTrue(bot.stopped)
        bot.join()
        self.assertFalse(self.manager.is_running(self.config.id))

    def test_stop_bot_cleans_dead_reference_without_join_thread(self):
        bot = self.manager.start_bot(self.config)
        bot._alive = False
        before = threading.active_count()
        self.manager.stop_bot(self.config)
        self.assertFalse(bot.stopped)
        self.assertNotIn(self.config.id, self.manager.bots)
        # Kein zusätzlicher stop-bot-Join-Thread für bereits tote Referenzen.
        self.assertLessEqual(threading.active_count(), before + 1)

    def test_is_running_false_when_empty(self):
        self.assertFalse(self.manager.is_running(self.config.id))
