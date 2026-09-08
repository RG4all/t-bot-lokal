# BUG-12 – Race Condition Bot-Start/Stop

- **Finding:** `RaceConditionBotStartStop` (Prompt 12 / Security-Audit §3.1)
- **Status:** **Fixed**
- **Release:** **2.4.12** · **Datum:** 2026-09-08
- **Einstufung:** MEDIUM – Bug (Nebenläufigkeit / Integrität des Paper-Bots)
- **Geprüfter Ausgangsstand:** 2.4.11 / `3e6ea9cd68c78d3306558c5bc55b6f698aa24818`
- **Fix-Commit:** siehe zugehörigen PR-Commit `fix(bot): resolve race condition in bot start/stop`

## Befund und Root Cause

`TradingBotManager.start_bot()` erwarb `self._lock` und rief darunter die
öffentliche Methode `is_running()` auf, die denselben Lock intern erneut
nahm:

```python
def start_bot(self, config):
    with self._lock:
        if self.is_running(config.id):  # is_running() acquire _lock intern
            return self.bots[config.id]
        ...
```

`self._lock` ist ein `threading.RLock`, daher entstand **kein Deadlock**.
Die verschachtelte Acquisition ist trotzdem die Root Cause:

- Ein späterer Wechsel auf `threading.Lock` würde `start_bot` hängen lassen
  (reproduziert: nicht-reentranter Lock, Timeout 2 s).
- Interne Prüfung und öffentliche API waren nicht getrennt; `stop_bot`
  räumte tote Referenzen nicht über dieselbe Prüfung ab.
- Unter Last (Activate plus `/api/bot/status/`-Polling) muss für dieselbe
  Konfiguration weiterhin genau ein lebender Trading-Thread existieren.
  Ein zweiter Thread würde Marktdaten doppelt abfragen und Paper-Orders
  verdoppeln.

Root Cause ist nicht „RLock statt Lock“, sondern dass eine bereits
lockende Methode eine zweite lockende Methode aufruft, statt eine
lock-freie interne Prüfung zu verwenden.

## Fix und notwendiger Umfang

| Komponente | Änderung |
|---|---|
| `TradingBotManager._is_running_unlocked` | Neue interne Prüfung ohne Lock. Darf nur unter gehaltenem `self._lock` laufen. Entfernt beendete Thread-Referenzen atomar. |
| `TradingBotManager.is_running` | Öffentliche API: `with self._lock: return self._is_running_unlocked(...)`. |
| `TradingBotManager.start_bot` | Ruft `_is_running_unlocked` unter dem bereits gehaltenen Lock; kein Re-Entry in `is_running()`. |
| `TradingBotManager.stop_bot` | Nutzt dieselbe unlocked-Prüfung unter dem Lock; tote Referenzen werden verworfen, ohne `stop()`/`join()` anzustoßen. Lebende Bots werden **außerhalb** des Locks gestoppt. |
| Externe Aufrufer | Unverändert `bot_manager.is_running(...)` in `trading/views.py` (Status-API). Keine direkte Nutzung der unlocked-Variante. |

Keine neue Runtime-Abhängigkeit, keine Migration, keine Änderung an
HTTP-APIs oder Settings. `self._lock` bleibt ein `RLock` (bestehende
Konvention); die Tests erzwingen dennoch Verschachtelungstiefe 1, damit
ein Wechsel auf `Lock` nicht still regressiert.

### Abgrenzung

- `TradingBot.__init__` (DB-Restore, Exchange-Setup) läuft weiterhin unter
  dem Manager-Lock. Das serialisiert Starts verschiedener Konfigurationen
  kurzzeitig, ist aber nicht Gegenstand dieses Findings.
- Ein Bot, der bereits `stop()` erhalten hat, aber dessen Thread noch
  `is_alive()` ist, gilt weiter als laufend. Ein sofortiger Neu-Start
  geht über `restart_bot()`, das zuerst `join()` abwartet.

## Regressionstests

Neu: `trading/tests/test_bot_start_stop.py` mit **16 Tests**.

Quellcode (am Ausgangsstand rot):

- `_is_running_unlocked` existiert und erwirbt den Lock nicht.
- `is_running()` umschließt die unlocked-Prüfung mit dem Lock.
- `start_bot()` / `stop_bot()` rufen nicht die öffentliche `is_running()`.
- Views nutzen nur die öffentliche API.

Laufzeit / Integrität:

- Lock-Verschachtelungstiefe 1 für Start, Status und Stop.
- Deadlock-Negativkontrolle mit `threading.Lock` (vor dem Fix Timeout).
- Idempotenter Start; 16 parallele Starts derselben Konfiguration erzeugen
  genau eine Instanz.
- Unabhängige Starts für verschiedene Konfigurationen.
- Tote Threads geben den Slot frei; `stop_bot` akzeptiert numerische IDs
  und räumt tote Referenzen ohne Join-Thread.

**Rot → grün:** 7 Tests schlugen am Stand 2.4.11 fehl (fehlender Helper,
`max_depth == 2` in `start_bot`, Deadlock auf nicht-reentrantem Lock,
`stop_bot` ohne unlocked-Prüfung und ohne Bereinigung toter Referenzen).
Nach dem Fix sind alle 16 plus die `TradingBotTests`-Quellcodeprüfung grün.
Bestehende `TradingBotTests` in `test_core.py` bleiben zusätzlich grün.

## Lokale Validierung

Python **3.11.2**, Django **5.2.17**, Ruff:

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
ruff check .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test --noinput
python manage.py test trading.tests.test_bot_start_stop trading.tests.test_core --noinput
python -m pip check
./tests/run_tests.sh
```

- **227 Django-/Python-Tests** (16 neue Manager-Tests + 1 Quellcode-Prüfung
  in `TradingBotTests` + 210 bestehende), **8/8 Shell-Testgruppen**,
  Ruff ohne Befund.
- Systemcheck, Migrationsprüfung, `collectstatic` und `pip check` bestanden.

## CI und Auslieferung

Wie in [SEC-10](SEC-10-information-disclosure.md#ci-und-auslieferung)
dokumentiert, ist im Repository kein GitHub-Actions-Anwendungstestworkflow
versioniert, weil der GitHub-App die `workflows`-Berechtigung fehlt.
Die Auslieferung erfolgt auf Basis der lokalen Prüfnachweise; ein
erfolgreicher GitHub-Anwendungstest-CI-Lauf wird nicht behauptet.

### Upgrade

Keine Konfigurations- oder Datenbankänderung. Nach dem Deploy `/health/`
auf **2.4.12** prüfen. Bereits laufende Bots müssen nicht neu gestartet
werden; das Locking gilt für nachfolgende Start-/Stop-/Status-Aufrufe.
