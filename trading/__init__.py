"""Trading-Modul für t-bot-lokal.

Dieses Modul enthält die Kernkomponenten des Paper-Trading-Systems.
Die öffentliche API ist über ``__all__`` explizit festgelegt; interne
Django-/Channels-Infrastruktur (``admin``, ``apps``, ``middleware``,
``passphrase``, ``rate_limit``, ``urls`` usw.) ist bewusst nicht exportiert.

Kernkomponenten:
- trading_bot: Live-Trading-Bot mit WebSocket-Marktdaten
- backtesting: Backtesting-Engine für Strategieoptimierung
- market_data: Adapter für öffentliche Exchange-APIs
- indicators: Zentrale Indikatorberechnungen

Django-Komponenten:
- views: Django-Views für Web-Oberfläche
- models: Datenbankmodelle
- tasks: Celery-Tasks für asynchrone Verarbeitung
- forms: Django-Formulare für Eingabevalidierung

Hilfsfunktionen:
- symbols: Symbol-Kataloge und Validierung
- resource_optimizer: Ressourcen-Profiling für Backtests
- market_scanner: Marktchancen-Erkennung und Reports
- worker_status: Status der Celery-/Backtest-Worker
"""

import importlib as _importlib
from typing import Any as _Any

# Alphabetisch sortiert; die Gruppierung ist im Modul-Docstring beschrieben.
__all__ = [
    "backtesting",
    "forms",
    "indicators",
    "market_data",
    "market_scanner",
    "models",
    "resource_optimizer",
    "symbols",
    "tasks",
    "trading_bot",
    "views",
    "worker_status",
]

# Import-sichere Kern- und Hilfsmodule werden beim Paketimport gebunden, damit
# ``trading.indicators`` usw. sofort verfügbar sind und die IDE die öffentliche
# API zuverlässig anbietet.
from . import (
    backtesting,
    indicators,
    market_data,
    market_scanner,
    resource_optimizer,
    symbols,
)

# Diese Module greifen auf die Django-App-Registry zu (Modelle, ORM, Auth,
# Views, Celery-Tasks). ``trading/__init__.py`` wird von Django bereits beim
# Populieren der Apps importiert – ein hier platziertes ``from . import
# models`` würde deshalb mit ``AppRegistryNotReady`` abbrechen. Sie werden
# deshalb erst beim ersten öffentlichen Zugriff über ``__getattr__`` geladen
# (PEP 562) und anschließend im Paketnamespace gecacht.
_DJANGO_BOUND_MODULES = frozenset(
    {
        "trading_bot",
        "views",
        "models",
        "tasks",
        "forms",
        "worker_status",
    }
)


def __getattr__(name: str) -> _Any:
    if name in _DJANGO_BOUND_MODULES:
        module = _importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    # Nur die öffentliche API und bereits für die IDE sichtbare, gebundene
    # Submodule anzeigen; interne Import-Helfer (``_importlib``) bleiben
    # ausgeblendet.
    return sorted(set(__all__) | {name for name in globals() if not name.startswith("_")})
