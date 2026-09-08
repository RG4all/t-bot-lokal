# CODE-20 – Fehlende `__all__`-Exports im Trading-Modul

- **Finding:** `MissingAllExports` (Prompt 20 / Security-Audit §4.5)
- **Status:** **Fixed** (behoben und nachgeprüft)
- **Release:** **2.4.17** · **Datum:** 2026-09-08
- **Einstufung:** LOW – Tech Debt (keine nachgewiesene Sicherheitslücke; der Fix
  schließt einen impliziten öffentlichen API-Vertrag und einen latenten
  App-Populationsfehler)
- **Geprüfter Ausgangsstand:** 2.4.16 / `3e0f1ef` (Branch
  `arena/01a08207-t-bot-lokal`)
- **Fix-Commit:** [`2ae72d9`](https://github.com/RG4all/t-bot-lokal/commit/2ae72d9007b2d525138b7bf14f4122d37a5680a6)
  – `refactor: add __all__ exports to trading module`
- **Fix-PR:** [#25 – refactor: add __all__ exports to trading module](https://github.com/RG4all/t-bot-lokal/pull/25) (Ziel-Branch `tbot.local`)
- **Dokumentation des Releases:** [CHANGELOG 2.4.17](../CHANGELOG.md)

## Befund und Root Cause

`trading/__init__.py` war ein reiner Markerkommentar:

```python
# Diese Datei bleibt leer, sie markiert das Verzeichnis als Python-Modul.
```

Daraus ergaben sich zwei zusammenhängende Probleme:

1. **Impliziter öffentlicher API-Vertrag.** Ohne `__all__` ist die öffentliche
   API des Pakets nicht festgelegt. `from trading import *` exportiert deshalb
   alles, was zufällig im Paket-Namespace gelandet ist. Nachdem Tests oder
   Aufrufer interne Module geladen hatten, exportierte der Stern-Import auch
   `admin`, `apps`, `middleware`, `monitoring`, `passphrase`, `rate_limit`,
   `urls`, `templatetags` usw. — interne Django-/Channels-Infrastruktur, die
   kein öffentlicher Vertrag ist und bei Refactorings leicht mitwandert.
2. **Der naheliegende Prompt-Fix ist für dieses Django-Paket nicht sicher.**
   Die vorgeschlagene Lösung `from . import (trading_bot, models, views, ...)` in
   `trading/__init__.py` bricht die App-Population: Django lädt das
   Paket-`__init__` während `django.setup()` über `AppConfig.create()` auf,
   bevor die App-Registry bereit ist. `models`, `forms`, `views` und `tasks`
   importieren Django-Modelle und enden dann mit
   `django.core.exceptions.AppRegistryNotReady`. Der Root Cause ist also nicht
   nur das fehlende `__all__`, sondern das Fehlen einer **lauffähigen,
   expliziten Import-Policy** für ein Paket, dessen `__init__` vor der
   App-Registry geladen wird.

Es wurde **keine Sicherheitslücke nachgewiesen**. Der Stern-Import ist ein
Qualitäts-/Wartbarkeitsproblem, kein direkter Angriffspfad; der
`AppRegistryNotReady`-Fehler ist ein Latenzfehler, der nur eintritt, wenn man
die naive Prompt-Vorlage ungefragt übernimmt.

## Fix und Umfang

### 1. `__all__` als expliziter öffentlicher Vertrag

`trading/__init__.py` definiert jetzt `__all__` mit den **zwölf öffentlichen
Submodulen**:

```python
__all__ = [
    "trading_bot", "backtesting", "market_data", "indicators",
    "views", "models", "tasks", "forms",
    "symbols", "resource_optimizer", "market_scanner", "worker_status",
]
```

Die innere Django-/Channels-Infrastruktur (`admin`, `apps`, `middleware`,
`monitoring`, `passphrase`, `rate_limit`, `urls`, `templatetags`,
`backtest_templates`, `context_processors`, `management`, `tests`) ist bewusst
nicht exportiert.

### 2. Import-Policy: eager für sichere, lazy für Django-Module

- **Import-sicher:** `backtesting`, `indicators`, `market_data`,
  `market_scanner`, `resource_optimizer`, `symbols` greifen auf keine
  Django-App-Registry zu und werden direkt beim Paketimport gebunden
  (`from . import ...`). Sie stehen damit sofort für IDE und `import trading`
  bereit.
- **Django-gebunden:** `trading_bot`, `views`, `models`, `tasks`, `forms`,
  `worker_status` werden über das modul-Level-`__getattr__` (PEP 562) erst beim
  ersten öffentlichen Zugriff geladen. Der erste Zugriff cached das Modul im
  Paket-Namespace. `from trading import *` und `from trading import models`
  funktionieren unverändert, ohne die App-Population zu brechen.

Der Fix ist damit strikt **innerhalb** des bestehenden Moduls: keine neue
Laufzeit-Abhängigkeit, kein neues Framework/Architekturmuster, keine Settings-,
Model- oder Migrationsänderung, keine geänderte URL, Response oder API-Form.

## Testnachweis

### Rot → grün

Neu `trading/tests/test_module_exports.py` mit **11 Tests** in einer Klasse.
Gegen den Ausgangsstand (leeres `__init__.py` und bestehendes Verhalten)
scheitern sie mit **4 Failures und 4 Errors**: `__all__`, Docstring,
explizite Imports und der Stern-Import vertrag sind alle unbekannt; der
Stern-Import exportiert nachweislich interne Module. Nach dem Fix sind alle
11 grün. Zusätzlich läuft die komplette Django-Suite.

| Prüfung | Bedeutung |
|---|---|
| `__all__` als Liste, exakt die 12 öffentlichen Module | Exakter öffentlicher Vertrag, keine Lücken |
| Docstring vorhanden und beschreibt alle öffentlichen Komponenten | IDE-Doc und Verständlichkeit |
| Sicher-Importe sind explizit gebunden | Import-Policy eindeutig |
| Django-Module sind nicht eager importiert | Verhindert `AppRegistryNotReady` |
| `__getattr__` löst die Django-Module auf | öffentlicher Zugriff funktioniert weiterhin |
| `from trading import *` exportiert exakt den Vertrag | Kein Leak interner Module |
| `__init__` nutzt keinen `import *` | keine Selbst-Export-Schleife |
| kein internes Modul in `__all__` | zukünftige Interna fallen automatisch auf |

### Abgedeckte Angriffs- und Regressionsvektoren

- **Stern-Import-Leck:** Nach dem Laden interner Module exportiert `from
  trading import *` keine `admin`/`middleware`/`passphrase`/`urls` mehr.
- **App-Population:** Ein naiver Eager-Import der Django-Module würde
  `AppRegistryNotReady` auslösen; der Test `test_django_modules_are_not_imported_eagerly`
  hält diese Regel dauerhaft fest.
- **Vergessener Export:** Ein neues öffentliches, aber nicht in `__all__`
  aufgenommenes Modul fällt über den exakten Mengenvergleich auf.
- **Interna als öffentliche API:** Ein versehentlich exportiertes internes
  Modul fällt über `test_all_does_not_contain_internal_modules` auf.

## Lokale Validierung

**Python 3.11.2**, **Django 5.2.17**, SQLite-Testdatenbank, **Ruff 0.16.6**,
mypy/django-stubs für die Typprüfung aus dem CODE-19-Umfeld, Test-Secrets
ausschließlich im Environment.

```bash
export AUTOSTART_BOTS=False DEBUG=True RENDER=False
python manage.py test trading.tests.test_module_exports --noinput
python manage.py test --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
ruff check .
mypy
bash tests/run_tests.sh
python -m pip check
```

- **320 Django-/Python-Tests** (11 neue Export-Tests + 309 bestehende)
  bestanden.
- Systemcheck, Migrationsprüfung, `collectstatic`, `pip check`, Ruff und die
  Shell-Testgruppen bestanden.
- **Keine Node-/Docker-Laufzeitprüfung:** `pyright` nicht ausgeführt (Node
  nicht eingerichtet), Docker steht in der Prüfumgebung nicht zur Verfügung.

## CI und Auslieferung

**Ausdrücklich genehmigte Ausnahme, unverändert wie in 2.4.6–2.4.16:** Im
Repository ist **kein GitHub-Actions-Anwendungstestworkflow** versioniert; das
Verzeichnis `.github/` existiert nicht, und der GitHub-App fehlt die
`workflows`-Berechtigung. Die einzige dynamische Integration ist der
Dependency Graph (Abhängigkeits-Scan). Es wird **kein erfolgreicher
GitHub-CI-Lauf behauptet**; die Nachweise sind die dokumentierten lokalen
Läufe. Nach dem Push werden die Check-Runs für den HEAD-Commit geprüft.

## Prüfgrenzen und Upgrade

- **Kein Sicherheitsfix behauptet.** Der Befund ist Tech Debt; der Stern-Import
  war kein nachgewiesener Exploit. Geschlossen wurde der implizite öffentliche
  API-Vertrag und ein latenter App-Populationsfehler der naiven Prompt-Vorlage.
- **`__all__` kontrolliert nur Stern-Imports und IDE-Darstellung.** Direkte
  `from trading.models import ...` bleiben immer möglich; die
  Eigentümer- und Gate-Autorisierung ist von diesem Fix nicht berührt.
- **Eager-Import der sechs sicheren Module** macht den Paketimport von `ccxt`,
  `requests`, `aiohttp` nötig. Diese Abhängigkeiten sind bereits in
  `requirements.txt`; es wurde keine neue Abhängigkeit hinzugefügt.
- **Laufzeitumgebung:** lokal Python 3.11.2, Zielruntime im Docker-Image 3.12.7.
- Keine Migration, keine neue Umgebungsvariable, keine geänderte API. Nach dem
  Deploy `/health/` auf **2.4.17** prüfen. Bestehende Konfigurationen, laufende
  Bots und gespeicherte Backtests bleiben unverändert gültig.
