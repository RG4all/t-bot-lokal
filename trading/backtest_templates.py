"""Vorgefertigte, nachvollziehbare Backtesting-Szenarien."""

from __future__ import annotations

from decimal import Decimal

TEMPLATE_LABELS = {
    "quick": "Schnellprüfung",
    "balanced": "Ausgewogen",
    "deep": "Feinoptimierung",
}


def _number(value):
    return float(Decimal(str(value)))


def _range(value, radius, step):
    value = Decimal(str(value))
    radius = Decimal(str(radius))
    step = Decimal(str(step))
    return {
        "from": _number(value - radius),
        "to": _number(value + radius),
        "steps": _number(step),
    }


def build_backtest_templates(config, resource_profile=None):
    """Liefert UI-freundliche Templates, relativ zur aktuellen Konfiguration.

    Templates ändern niemals die Live-Konfiguration. Sie füllen lediglich das
    Suchraster des neuen Backtest-Auftrags aus. Das Limit des erkannten
    Ressourcenprofils wird dabei immer eingehalten.
    """
    default_points = getattr(resource_profile, "max_price_points", 5_000)
    default_combinations = getattr(resource_profile, "max_combinations", 20_000)
    current = {
        "acc": config.div_DVA_prev_NDA_threshold_buy,
        "nda": config.nda_threshold_buy,
        "deltadelta": config.deltadelta_threshold_buy,
    }
    definitions = {
        "quick": {
            "description": "Grober Überblick mit wenigen Rasterpunkten und kurzer Historie.",
            "radius": ("0.5", "0.25", "0.25"),
            "step": ("0.25", "0.125", "0.125"),
            "price_factor": 0.4,
        },
        "balanced": {
            "description": "Empfohlener Startpunkt für einen reproduzierbaren Vergleich.",
            "radius": ("1", "0.5", "0.5"),
            "step": ("0.1", "0.05", "0.05"),
            "price_factor": 1.0,
        },
        "deep": {
            "description": "Engmaschige Optimierung; nur mit ausreichend Daten und Zeit verwenden.",
            "radius": ("2", "1", "1"),
            "step": ("0.1", "0.05", "0.05"),
            "price_factor": 1.0,
        },
    }
    templates = []
    for name, definition in definitions.items():
        radii = definition["radius"]
        steps = definition["step"]
        values = {
            "template": name,
            "description": definition["description"],
            "acc": _range(current["acc"], radii[0], steps[0]),
            "nda": _range(current["nda"], radii[1], steps[1]),
            "deltadelta": _range(current["deltadelta"], radii[2], steps[2]),
            "trade_amount": _number(config.trade_amount),
            "take_profit": _number(config.take_profit),
            "stop_loss": _number(config.stop_loss),
            "fee": _number(config.fee),
            "max_price_points": max(100, int(default_points * definition["price_factor"])),
            "max_grid_points": max(2, int(getattr(resource_profile, "max_grid_points", 25))),
            "max_combinations": default_combinations,
        }
        templates.append({"name": name, "label": TEMPLATE_LABELS[name], **values})
    return templates


def template_choices():
    return [("", "Benutzerdefiniert")] + [(key, label) for key, label in TEMPLATE_LABELS.items()]
