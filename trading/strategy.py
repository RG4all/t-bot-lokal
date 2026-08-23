"""Zentrale Strategie-, Richtungs- und Hebelmathematik.

Dieses Modul ist die einzige Quelle der Wahrheit für

* die Auflösung des wirksamen Hebels je Börse und Markt,
* die Übersetzung der drei Indikatoren in Long-/Short-Einstiegssignale,
* die richtungsabhängige Kurs-, Ergebnis- und Liquidationsrechnung.

Live-Bot (``trading.trading_bot``) und Backtest (``trading.backtesting``)
importieren ausschließlich diese Funktionen. Damit ist per Konstruktion
sichergestellt, dass eine im Backtest optimierte Regel im Livebetrieb exakt
dieselbe Entscheidung erzeugt.

Vorzeichenkonvention der Indikatoren
------------------------------------
``NDA`` und ``DeltaDelta`` sind vorzeichenbehaftete Momentumgrößen: positiv im
Aufwärtsimpuls, negativ im Abwärtsimpuls. Sie werden für Short-Signale am
Nullpunkt gespiegelt (``-NDA`` bzw. ``-DeltaDelta``) und danach gegen dieselbe
Schwelle geprüft. Eine Konfiguration behält damit ihr kalibriertes
Momentum-Niveau, unabhängig von der Handelsrichtung.

``Beschleunigung = DVA / vorherige NDA`` ist dagegen bereits
richtungsneutral: Der Quotient ist positiv, wenn sich der bestehende Impuls
verstärkt – im Abwärtstrend sind Zähler und Nenner beide negativ. Er darf
deshalb **nicht** gespiegelt werden; das wäre der klassische Fehler beim
Nachrüsten von Short-Logik. Die Richtungsprüfung übernehmen NDA und
DeltaDelta.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

LONG = "long"
SHORT = "short"
BOTH = "both"

DIRECTION_CHOICES = (
    (LONG, "Nur Long (Kauf)"),
    (SHORT, "Nur Short (Leerverkauf)"),
    (BOTH, "Long und Short"),
)
VALID_DIRECTIONS = {LONG, SHORT, BOTH}

SPOT_LEVERAGE = Decimal(1)
_EIGHT_PLACES = Decimal("0.00000001")
# Absolute Obergrenze; keine unterstützte Börse erlaubt mehr.
ABSOLUTE_MAX_LEVERAGE = 125


def _decimal(value, default="0"):
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def normalize_exchange(exchange):
    return (exchange or "").strip().lower()


def max_leverage(exchange):
    """Maximal zulässiger Hebel der Börse laut Konfiguration."""
    limits = getattr(settings, "EXCHANGE_MAX_LEVERAGE", {}) or {}
    limit = limits.get(normalize_exchange(exchange), ABSOLUTE_MAX_LEVERAGE)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = ABSOLUTE_MAX_LEVERAGE
    return max(1, min(ABSOLUTE_MAX_LEVERAGE, limit))


def default_leverage(exchange):
    """Voreingestellter Hebel der Börse (bereits auf das Maximum begrenzt)."""
    defaults = getattr(settings, "EXCHANGE_LEVERAGE", {}) or {}
    fallback = getattr(settings, "DEFAULT_FUTURES_LEVERAGE", 1)
    value = defaults.get(normalize_exchange(exchange), fallback)
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(max_leverage(exchange), value))


def resolve_leverage(exchange, market, configured=None):
    """Wirksamer Hebel für Börse, Markt und optionale Konfiguration.

    * Spot kennt keinen Hebel und liefert immer ``1``.
    * Ohne (oder mit unbrauchbarem) Konfigurationswert greift die
      börsenspezifische Voreinstellung aus den Einstellungen.
    * Das Ergebnis ist stets auf das Börsenmaximum begrenzt.
    """
    if (market or "").strip().lower() != "futures":
        return SPOT_LEVERAGE
    if configured in (None, "", 0):
        value = default_leverage(exchange)
    else:
        try:
            value = int(Decimal(str(configured)))
        except (TypeError, ValueError, ArithmeticError):
            value = default_leverage(exchange)
    value = max(1, min(max_leverage(exchange), value))
    return Decimal(value)


def maintenance_margin_rate():
    """Erhaltungsmarge als Dezimalanteil (0.005 entspricht 0,5 %)."""
    rate = getattr(settings, "FUTURES_MAINTENANCE_MARGIN_RATE", 0.005)
    try:
        rate = Decimal(str(rate))
    except (TypeError, ArithmeticError):
        rate = Decimal("0.005")
    return max(Decimal(0), min(Decimal("0.5"), rate))


def normalize_direction(direction, market=None):
    """Bereinigt die Richtungsangabe; Spot lässt ausschließlich Long zu."""
    value = (direction or LONG).strip().lower()
    if value not in VALID_DIRECTIONS:
        value = LONG
    if market is not None and (market or "").strip().lower() != "futures":
        return LONG
    return value


def allows(direction, candidate):
    """Prüft, ob die konfigurierte Richtung eine konkrete Seite erlaubt."""
    direction = normalize_direction(direction)
    return direction == BOTH or direction == candidate


def direction_sign(direction):
    """+1 für Long, -1 für Short."""
    return Decimal(-1) if direction == SHORT else Decimal(1)


def directional_momentum(nda, deltadelta, direction):
    """Spiegelt die vorzeichenbehafteten Momentumgrößen für Shorts."""
    sign = direction_sign(direction)
    return _decimal(nda) * sign, _decimal(deltadelta) * sign


def entry_signal(acceleration, deltadelta, nda, thresholds, direction=LONG):
    """Liefert ``"long"``, ``"short"`` oder ``None`` für einen Messpunkt.

    ``thresholds`` ist eine Sequenz aus (Beschleunigung, NDA, DeltaDelta).
    Bei Richtung ``both`` hat Long Vorrang, wenn – rechnerisch praktisch
    ausgeschlossen – beide Seiten gleichzeitig ein Signal erzeugen.
    """
    acceleration = _decimal(acceleration)
    acc_threshold, nda_threshold, deltadelta_threshold = (
        _decimal(thresholds[0]),
        _decimal(thresholds[1]),
        _decimal(thresholds[2]),
    )
    if acceleration <= acc_threshold:
        return None
    for candidate in (LONG, SHORT):
        if not allows(direction, candidate):
            continue
        signed_nda, signed_deltadelta = directional_momentum(nda, deltadelta, candidate)
        if signed_nda > nda_threshold and signed_deltadelta > deltadelta_threshold:
            return candidate
    return None


def price_change_percent(entry_price, price, direction=LONG):
    """Kursbewegung ab Einstieg in Prozent, aus Sicht der Position.

    Long: steigende Kurse sind positiv. Short: fallende Kurse sind positiv.
    Take-Profit und Stop-Loss bleiben damit – wie in allen bestehenden
    Konfigurationen – reine Kursschwellen und sind hebelunabhängig.
    """
    entry_price = _decimal(entry_price)
    if not entry_price:
        return Decimal(0)
    return (_decimal(price) - entry_price) / entry_price * Decimal(100) * direction_sign(direction)


def roi_percent(price_change, leverage):
    """Rendite auf die eingesetzte Margin: Kursbewegung × Hebel."""
    return _decimal(price_change) * _decimal(leverage, "1")


def liquidation_move_percent(leverage, rate=None):
    """Adverse Kursbewegung in Prozent, ab der die Margin aufgebraucht ist.

    Isolierte Margin: Der Verlust erreicht die Erhaltungsmarge bei
    ``(1 - MMR) / Hebel``. Bei Hebel 1 liegt der Wert bei ~99,5 % und kann
    im Spot-Betrieb praktisch nie ausgelöst werden – die bisherige Logik
    bleibt dadurch unverändert.
    """
    leverage = _decimal(leverage, "1")
    if leverage <= 0:
        leverage = Decimal(1)
    rate = maintenance_margin_rate() if rate is None else _decimal(rate)
    return (Decimal(1) - rate) / leverage * Decimal(100)


def is_liquidated(price_change, leverage, rate=None):
    """True, sobald die adverse Kursbewegung die Margin aufzehrt."""
    return _decimal(price_change) <= -liquidation_move_percent(leverage, rate)


def liquidation_price(entry_price, leverage, direction=LONG, rate=None):
    """Kurs, an dem die Position rechnerisch liquidiert würde."""
    entry_price = _decimal(entry_price)
    move = liquidation_move_percent(leverage, rate) / Decimal(100)
    if direction == SHORT:
        return entry_price * (Decimal(1) + move)
    return max(Decimal(0), entry_price * (Decimal(1) - move))


def position_size(trade_amount, price, leverage):
    """Kontraktmenge aus Margin, Kurs und Hebel.

    ``trade_amount`` ist die eingesetzte Margin. Das Nominalvolumen beträgt
    ``Margin × Hebel``; bei Hebel 1 entspricht die Menge exakt der bisherigen
    Spot-Berechnung ``trade_amount / price``.
    """
    price = _decimal(price)
    if price <= 0:
        raise ValueError("Positionsgröße benötigt einen positiven Kurs")
    notional = _decimal(trade_amount) * _decimal(leverage, "1")
    return (notional / price).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)


def gross_pnl(entry_price, exit_price, amount, direction=LONG):
    """Rohergebnis der Position vor Gebühren."""
    return (
        (_decimal(exit_price) - _decimal(entry_price))
        * _decimal(amount)
        * direction_sign(direction)
    )
