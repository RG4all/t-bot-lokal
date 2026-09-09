"""Eigener DiscoverRunner: hermetisiert Prozess-Caches zwischen Testfaellen.

Views und Scanner halten Zustand in Modul-Globals (Portfolio-Snapshot-LRU;
Scanner-TTL/LRU-Caches inkl. Refresh-Drossel). Django rollt nur die DB
zurueck, und die Konfigurations-PKs werden je TestCase neu vergeben – ohne
Reset wuerde der Cache des einen Tests im naechsten mit „passender" pk
auftauchen und Tests haengen an der Ausfuehrungsreihenfolge (in der
CI-Suite sichtbar geworden, lokal zunachst durch Env-abhaengige
Reihenfolge kaschiert). Der Runner leert vor jedem einzelnen Testfall die
geteilten Caches; Klassen mit eigenen Cache-Erwartungen (z. B.
ScannerCacheTests) koennen weiterhin feiner sichern und wiederherstellen.
"""

import unittest

from django.test.runner import DiscoverRunner


def clear_shared_process_caches() -> None:
    """Leert den Portfolio-Snapshot-Cache und saemtlichen Scanner-Zustand."""
    from trading import views
    from trading.market_scanner import _reset_process_state_for_tests

    views._PORTFOLIO_CACHE.clear()
    _reset_process_state_for_tests()


class CacheClearingDiscoverRunner(DiscoverRunner):
    def get_resultclass(self):
        base = super().get_resultclass() or unittest.TextTestResult

        class _CacheClearingTestResult(base):
            def startTest(self, test):
                clear_shared_process_caches()
                super().startTest(test)

        return _CacheClearingTestResult
