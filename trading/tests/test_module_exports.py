"""Regression tests for the explicit public API of the ``trading`` package.

The finding is LOW tech debt (Prompt 20 / Security-Audit §4.5): without
``__all__`` and an explicit import policy, the public API of the package is
implicit and ``from trading import *`` accidentally leaks every Django /
Channels infrastructure module that happened to be imported into the package
namespace.

This module tests the production-safe contract:

* ``__all__`` lists every public submodule and no internal module.
* Import-safe modules (no Django model access) are bound eagerly.
* Django-bound modules (``models``, ``forms``, ``views``, ``tasks``,
  ``trading_bot``, ``worker_status``) are bound lazily through module-level
  ``__getattr__``. Eagerly importing them from ``trading/__init__.py`` would
  break Django's app population with ``AppRegistryNotReady``, because the
  package ``__init__`` is loaded before the app registry is ready.
* ``from trading import *`` only exports the public contract.
"""

from __future__ import annotations

import ast
import importlib
import types
import unittest
from pathlib import Path

import trading

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_PATH = _REPO_ROOT / "trading" / "__init__.py"

# The public contract requested by Prompt 20 / Audit §4.5.
PUBLIC_MODULES = frozenset(
    {
        "trading_bot",
        "backtesting",
        "market_data",
        "indicators",
        "views",
        "models",
        "tasks",
        "forms",
        "symbols",
        "resource_optimizer",
        "market_scanner",
        "worker_status",
    }
)

# Modules that can be imported without touching the Django app registry.
SAFE_MODULES = frozenset(
    {
        "backtesting",
        "indicators",
        "market_data",
        "market_scanner",
        "resource_optimizer",
        "symbols",
    }
)

# Modules that only load after Django has finished app population (models,
# forms, views, tasks, bot, worker status). They must never be imported
# eagerly from ``trading/__init__.py``.
DJANGO_BOUND_MODULES = PUBLIC_MODULES - SAFE_MODULES

# Django/Channels/implementation helpers that must stay out of the public API.
# A missing name here causes the test to fail, so new internals are not
# accidentally exported by a future ``__all__`` change.
INTERNAL_MODULES = frozenset(
    {
        "admin",
        "apps",
        "backtest_templates",
        "context_processors",
        "middleware",
        "monitoring",
        "passphrase",
        "rate_limit",
        "templatetags",
        "tests",
        "urls",
        "management",
    }
)


def _parse_init_source() -> ast.Module:
    return ast.parse(_INIT_PATH.read_text(encoding="utf-8"), filename=str(_INIT_PATH))


def _collected_import_names() -> set[str]:
    """Return the submodule names imported by ``from . import (...)`` in
    ``trading/__init__.py``."""
    names: set[str] = set()
    tree = _parse_init_source()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 1 or node.module is not None:
            continue
        for alias in node.names:
            if alias.asname is None:
                names.add(alias.name)
    return names


class ModuleExportsContractTests(unittest.TestCase):
    def test_module_docstring_exists(self) -> None:
        self.assertTrue(trading.__doc__, "trading module must have a docstring")
        self.assertGreater(len(trading.__doc__), 20)

    def test_all_defined_as_list(self) -> None:
        self.assertIsInstance(trading.__all__, list)
        self.assertTrue(trading.__all__, "__all__ must not be empty")
        self.assertEqual(len(trading.__all__), len(set(trading.__all__)))

    def test_all_contains_exactly_public_modules(self) -> None:
        self.assertEqual(set(trading.__all__), set(PUBLIC_MODULES))

    def test_all_does_not_contain_internal_modules(self) -> None:
        exported = set(trading.__all__)
        self.assertTrue(exported.isdisjoint(INTERNAL_MODULES))

    def test_safe_modules_are_bound_by_explicit_imports(self) -> None:
        imported = _collected_import_names()
        self.assertEqual(imported, set(SAFE_MODULES))
        self.assertFalse(SAFE_MODULES.isdisjoint(imported))

    def test_django_modules_are_not_imported_eagerly(self) -> None:
        # Eagerly importing Django models/forms/views/tasks during package
        # import breaks ``django.setup()`` with ``AppRegistryNotReady``.
        imported = _collected_import_names()
        self.assertTrue(imported.isdisjoint(DJANGO_BOUND_MODULES))

    def test_module_getattr_resolves_django_modules(self) -> None:
        self.assertTrue(hasattr(trading, "__getattr__"), "PEP 562 __getattr__ required")
        for name in DJANGO_BOUND_MODULES:
            with self.subTest(name=name):
                target = getattr(trading, name)
                self.assertIsInstance(target, types.ModuleType)
                self.assertIs(getattr(trading, name), target)

    def test_every_exported_name_is_a_binding_submodule(self) -> None:
        for name in trading.__all__:
            with self.subTest(name=name):
                target = importlib.import_module(f"trading.{name}")
                self.assertIsInstance(target, types.ModuleType)
                self.assertIs(getattr(trading, name), target)

    def test_star_import_exports_exactly_public_contract(self) -> None:
        namespace: dict[str, object] = {}
        # ``import *`` lässt sich ohne ``exec`` nicht deterministisch in ein
        # Test-Namespace einspeisen; es läuft gegen das untersuchte Paket.
        exec("from trading import *", namespace)  # noqa: S102
        exported = {name for name in namespace if not name.startswith("__")}
        self.assertEqual(exported, set(PUBLIC_MODULES))

    def test_init_does_not_use_import_star(self) -> None:
        tree = _parse_init_source()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                self.fail(f"trading/__init__.py uses import star: {ast.dump(node)}")

    def test_docstring_describes_public_components(self) -> None:
        # The docstring should name the components users can rely on.
        self.assertTrue(trading.__doc__)
        for name in sorted(PUBLIC_MODULES):
            self.assertIn(name, trading.__doc__)


if __name__ == "__main__":
    unittest.main()
