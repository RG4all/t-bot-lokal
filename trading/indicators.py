"""Zentrale Indikatorberechnungen für Trading-Strategien.

Dieses Modul enthält die Berechnung der drei Kernindikatoren:

- **NDA** (Normalisierte Preisänderung)
- **DeltaDelta** (geglättetes Momentum)
- **Acceleration** (Beschleunigung, ``DVA / vorherige NDA``)

Die Indikatoren werden sowohl vom Live-Trading-Bot (``trading_bot.py``) als
auch vom Backtesting-System (``backtesting.py``, ``tasks.py``) verwendet. Beide
Pfade teilen sich bewusst **dieselbe** Arithmetik an **einer** Stelle: Weichen
Formeln oder Rundung voneinander ab, optimiert ein Backtest Schwellwerte auf
eine Strategie, die der Bot in exakt dieser Kombination nie handelt – und
umgekehrt bleibt ein Fix in nur einer der beiden Kopien wirksam. Genau diese
Drift ist der eigentliche Schaden duplizierter Berechnungslogik.

Zwei Präzisionsstufen, eine Implementierung
--------------------------------------------

Historisch quantisiert das Backtesting jede Zwischenstufe auf 8
Nachkommastellen (``ROUND_HALF_UP``), während der Live-Bot ungerundet rechnet
und erst beim Schreiben in die ``DataLog``-Tabelle rundet (``_bounded``).
Weder ist die eine Reihenfolge finanzmathematisch "richtiger", noch darf ein
Deduplizierungs-Refactoring laufende Schwellwertentscheidungen oder
bestehende Datenlogs verschieben. Deshalb bietet das Modul beide Stufen aus
derselben Kernfunktion an:

- :func:`calculate_trading_indicators` – quantisiert, für Backtests, Reports
  und Plotdaten; Rückgabe ``(acceleration, deltadelta, nda)``.
- :func:`compute_indicator_values` – Rohwerte ohne Zwischenrundung für den
  Live-Bot, inklusive der Differenzen ``current_da``/``previous_da``, die
  ``DataLog`` zusätzlich speichert.

Der einzige Unterschied ist der ``rounding``-Hook. Alle Guards (fehlender
Vorpreis, Nullnenner, Indexbereich) gelten für beide Pfade identisch.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

# Dezimalgenauigkeit der Strategieindikatoren: 8 Nachkommastellen – passend zu
# den DataLog-Feldern (``decimal_places=8``) und den Ordergrößen des Bots.
# Zentral definiert, damit Backtesting, Bot und Persistierung nicht mehr drei
# Kopien derselben Konstanten pflegen.
EIGHT_PLACES = Decimal("0.00000001")


def to_decimal(value, default="0"):
    """Überführt Preise und Schwellwerte verlustfrei in ``Decimal``.

    ``None`` liefert ``default`` (übernommenes Verhalten der bisherigen
    Backtesting-Hilfsfunktion); alles andere wird über seine Stringdarstellung
    konvertiert, damit ein Binär-Float nicht als
    ``0.1000000000000000055511151231257827`` in der Berechnung landet.
    Ungültige Werte (z. B. ``"kein-preis"``) werfen wie bisher eine
    ``decimal.InvalidOperation`` statt stillschweigend 0 zu ergeben.
    """
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


class IndicatorValues(NamedTuple):
    """Vollständiger Indikator-Snapshot eines einzelnen Datenpunkts.

    Die Felder entsprechen 1:1 den ``DataLog``-Spalten, damit der Bot für die
    Persistierung keine zweite Berechnung anstellen muss.
    """

    current_price: Decimal
    current_da: Decimal
    nda: Decimal
    previous_da: Decimal
    previous_nda: Decimal
    dva: Decimal
    acceleration: Decimal
    deltadelta: Decimal


def _no_rounding(value):
    """Identität für den Bot-Pfad: gerundet wird erst beim Speichern."""
    return value


def _quantize(value):
    """Strategie-Präzision: 8 Nachkommastellen, kaufmännisch gerundet."""
    return value.quantize(EIGHT_PLACES, rounding=ROUND_HALF_UP)


def compute_indicator_values(prices, idx, *, rounding=_no_rounding):
    """Berechnet den vollständigen Indikator-Snapshot am Index ``idx``.

    Die Arithmetik läuft in präziser ``Decimal``-Rechnung; ``rounding`` wird auf
    **jede** Zwischenstufe angewendet (``_no_rounding`` = Rohwerte, ``_quantize``
    = Backtest-Präzision).

    Args:
        prices: Sequenz von Preisen (``Decimal``, Float oder String), mindestens
            ``idx + 1`` Elemente.
        idx: Index des zu berechnenden Datenpunkts; mindestens 2, weil Vor- und
            Vorvorpreis nötig sind.
        rounding: Aufrufbare Funktion ``Decimal -> Decimal`` für die
            Zwischenrundung. Standard: keine Rundung.

    Returns:
        IndicatorValues: ``current_price``, ``current_da``, ``nda``,
        ``previous_da``, ``previous_nda``, ``dva``, ``acceleration``,
        ``deltadelta``.

    Raises:
        IndexError: wenn ``idx < 2`` (kein Vorvorpreis), wenn ``idx`` außerhalb
            von ``prices`` liegt oder wenn die Reihe weniger als drei Werte hat.
            Ohne diese Prüfung würde ein negativer Index über die
            Listendefinition den letzten Preis einsetzen und einen
            unsichtbar falschen Indikatorwert liefern.
        TypeError: wenn ``prices`` keine Sequenz mit Länge ist.
        decimal.InvalidOperation: bei einem nicht als Zahl darstellbaren Preis.
    """
    try:
        size = len(prices)
    except TypeError as exc:
        raise TypeError("prices muss eine Sequenz mit fester Länge sein (list/tuple).") from exc
    if idx < 2:
        raise IndexError(
            f"Indikatoren sind ab Index 2 definiert (Vor- und Vorvorpreis nötig); idx={idx}."
        )
    if idx >= size:
        raise IndexError(f"idx={idx} liegt außerhalb der Preisreihe mit {size} Werten.")

    current_price = to_decimal(prices[idx])
    previous_price = to_decimal(prices[idx - 1])
    older_price = to_decimal(prices[idx - 2])

    # NDA: normalisierte Preisänderung in Prozent. Ist der Vorpreis 0 oder als
    # None belegt (dann hier 0), existiert keine Verhältniszahl: defensiv 0,
    # damit ein einzelner ungültiger Tick weder einen Bot-Zyklus noch einen
    # ganzen Backtest abbricht.
    current_da = current_price - previous_price
    nda = rounding(current_da / previous_price * Decimal(100)) if previous_price else Decimal(0)

    # Vorherige NDA: bezieht sich – wie in beiden Altimplementationen – auf
    # denselben Nenner previous_price, nicht auf older_price. Bewusst
    # dokumentiert, weil "P1 - P2 geteilt durch P2" die naheliegende, aber
    # hier gerade nicht gewollte Lesart wäre.
    previous_da = previous_price - older_price
    previous_nda = (
        rounding(previous_da / previous_price * Decimal(100)) if previous_price else Decimal(0)
    )

    # DVA: Differenz der beiden NDA-Werte (Momentum-Veränderung).
    dva = rounding(nda - previous_nda)

    # Acceleration: DVA relativ zum Ausgangsmomentum. Vorherige NDA == 0 ist
    # nicht relativierbar und liefert wie bisher 0.0 statt einer Division
    # durch Null.
    acceleration = rounding(dva / previous_nda) if previous_nda else Decimal(0)

    # DeltaDelta: geglättetes 2-Punkt-Momentum.
    deltadelta = rounding((nda + previous_nda) / Decimal(2))

    return IndicatorValues(
        current_price=current_price,
        current_da=current_da,
        nda=nda,
        previous_da=previous_da,
        previous_nda=previous_nda,
        dva=dva,
        acceleration=acceleration,
        deltadelta=deltadelta,
    )


def calculate_trading_indicators(prices, idx):
    """Berechnet die drei Strategieindikatoren am angegebenen Index.

    Quantisierte Variante für Backtesting, Reports und Plots. Der Live-Bot
    nutzt :func:`compute_indicator_values` mit Rohwerten (Modul-Docstring).

    Args:
        prices: Sequenz von Decimal-Preisen (mindestens ``idx + 1`` Elemente).
        idx: Index des zu berechnenden Datenpunkts (mindestens 2).

    Returns:
        tuple[Decimal, Decimal, Decimal]: ``(acceleration, deltadelta, nda)``.

    Raises:
        IndexError: siehe :func:`compute_indicator_values`.
        TypeError: siehe :func:`compute_indicator_values`.
    """
    values = compute_indicator_values(prices, idx, rounding=_quantize)
    return values.acceleration, values.deltadelta, values.nda


def build_indicator_rows(prices):
    """Berechnet die Indikatorzeilen einer kompletten Preisreihe vor.

    Das Ergebnis ist indexgleich zu ``prices``: Die ersten beiden Positionen
    sind ``None``, weil es für Index 0 und 1 keinen Vorvorpreis gibt und die
    Simulation dort erst bei Index 2 beginnt. Backtests berechnen die Zeilen
    genau einmal pro Preisreihe und wiederverwenden sie für alle
    Schwellwertkombinationen des Rasters.

    Args:
        prices: Sequenz von Preisen.

    Returns:
        list: ``None``-Platzhalter für ungenutzte Indizes, sonst die Tripel aus
        :func:`calculate_trading_indicators`.
    """
    return [None] * min(2, len(prices)) + [
        calculate_trading_indicators(prices, index) for index in range(2, len(prices))
    ]
