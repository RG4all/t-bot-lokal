"""Menschenlesbare Fehler-Erklärungen für das Fehler-Log der Benutzeroberfläche.

Grenze (SEC-10): Die rohe Fehlermeldung (``ErrorLog.message``), der
Exception-Typ und die ``details``-Diagnose bleiben nur für
Betreiberkonten (``is_staff``) sichtbar. Regelmäßige Nutzer erhalten aus
diesem Modul pro Fehlerquelle eine serverseitig formulierte Erklärung:
**was** ist passiert, **warum** ist es passiert und **was** kann der Nutzer
tun. Die Texte sind bewusst reine App-Prosa – sie enthalten niemals
Exception-Texte, URLs, Pfade oder andere Serverdetails. Neue Fehlerquellen
müssen hier eine Erklärung erhalten; unbekannte Quellen bekommen einen
ehrlichen, generischen Text mit dem Hinweis auf die Referenznummer.
"""

from datetime import datetime, timezone
from typing import Any

# source -> (was, warum, was-tun)
_ERROR_EXPLANATIONS: dict[str, tuple[str, str, str]] = {
    "trading_bot.fetch_tickers": (
        (
            "Der Bot konnte die aktuellen Kurse von der Exchange nicht abrufen – "
            "oder nicht rechtzeitig."
        ),
        (
            "Die Exchange antwortet zu langsam, ist überlastet, hat eine "
            "Anfragesperre (Rate-Limit) ausgesprochen oder ist vom Server aus "
            "gerade nicht erreichbar."
        ),
        (
            "In der Regel nichts: Der Bot wiederholt den Abruf automatisch mit "
            "wachsender Wartezeit. Bleibt die Meldung länger als 30 Minuten "
            "bestehen, bitte Exchange, Marktart und Symbole prüfen oder die "
            "Referenznummer dem Betreiber nennen."
        ),
    ),
    "trading_bot.validate_symbols": (
        (
            "Ein oder mehrere Handelspaare sind bei der Exchange nicht (mehr) "
            "gelistet. Der Bot wurde deshalb gestoppt."
        ),
        (
            "Das Symbol ist fehlerhaft geschrieben, existiert bei dieser Exchange "
            "nicht oder ist für die gewählte Marktart (Spot/Futures) nicht "
            "verfügbar."
        ),
        (
            "Konfiguration bearbeiten, die betroffenen Symbole korrigieren (die "
            "Symbolvorschläge zeigen gültige Paare) und den Bot erneut starten."
        ),
    ),
    "trading_bot.process_symbol": (
        (
            "Der Bot konnte die Marktdaten eines Symbols in diesem Zyklus nicht "
            "verarbeiten."
        ),
        (
            "Beispielsweise hat die Exchange für dieses Symbol keinen Preis "
            "geliefert oder ein Preis war ungültig. Das heilt sich meist mit "
            "dem nächsten Kursupdate von selbst."
        ),
        (
            "Meist nichts: Der Bot läuft weiter und wertet die übrigen Symbole "
            "aus. Tritt die Meldung dauerhaft für dasselbe Symbol auf, bitte das "
            "Symbol in der Konfiguration korrigieren."
        ),
    ),
    "trading_bot.main_loop": (
        (
            "Der Bot ist in einem seiner Arbeitszyklen auf einen internen Fehler "
            "gestoßen."
        ),
        (
            "Unerwarteter Zustand in der Bot-Logik; die genaue Ursache ist "
            "serverseitig mit vollständiger Diagnose dokumentiert."
        ),
        (
            "Meist nichts: Der Bot setzt seinen Betrieb automatisch fort. "
            "Wiederholt sich die Meldung, bitte die Referenznummer dem Betreiber "
            "nennen."
        ),
    ),
    "trading_bot.kill_switch": (
        (
            "Der Not-Abbruch (Kill-Switch) konnte eine oder mehrere offene "
            "Positionen nicht zu Marktpreisen schließen."
        ),
        (
            "Die Exchange lieferte für die betreffende Position keinen Preis oder "
            "reagierte nicht."
        ),
        (
            "Kill-Switch erneut auslösen. Schließen sich Positionen weiterhin "
            "nicht, bitte die Referenznummer dem Betreiber nennen."
        ),
    ),
    "trading_bot.restart": (
        (
            "Der Neustart des Bots nach einer Änderung ist fehlgeschlagen."
        ),
        (
            "Beispielsweise war die Datenbank im Moment des Neustarts kurz nicht "
            "erreichbar oder die Konfiguration ist zwischenzeitlich deaktiviert "
            "worden."
        ),
        (
            "Prüfen, ob die Konfiguration in der Liste aktiv ist; falls nicht, "
            "den Bot dort erneut starten. Bei wiederholtem Fehlschlag bitte die "
            "Referenznummer dem Betreiber nennen."
        ),
    ),
    "apps.autostart": (
        (
            "Eine als „Bot läuft“ markierte Konfiguration konnte beim Serverstart "
            "nicht automatisch gestartet werden."
        ),
        (
            "Beispielsweise war die Exchange beim Prozessstart nicht erreichbar "
            "oder die Datenbank noch nicht bereit."
        ),
        (
            "Den Bot in der Konfigurationsliste erneut starten. Startet er dort "
            "dauerhaft nicht, bitte Exchange und Symbole prüfen."
        ),
    ),
    "views.config_activate.validation": (
        (
            "Beim Starten konnte die Exchange die Handelspaare dieser "
            "Konfiguration nicht prüfen. Der Bot wurde nicht gestartet."
        ),
        (
            "Die Exchange war im Moment der Prüfung nicht erreichbar oder die "
            "Paare sind dort nicht (mehr) gelistet."
        ),
        (
            "In einigen Minuten erneut starten. Bleibt der Fehler, sind die "
            "Paare vermutlich fehlerhaft oder nicht gelistet: Konfiguration "
            "bearbeiten, Symbole korrigieren (Vorschläge nutzen) und erneut "
            "starten."
        ),
    ),
    "views.config_activate": (
        (
            "Der Bot für diese Konfiguration konnte nicht gestartet werden."
        ),
        (
            "Beispielsweise war die Datenbank kurz nicht erreichbar oder das "
            "Einrichten des Exchange-Anschlugs schlug fehl. Die genaue Ursache "
            "ist serverseitig dokumentiert."
        ),
        (
            "Die Konfiguration in der Liste erneut starten. Startet sie "
            "wiederholt nicht, bitte die Referenznummer dem Betreiber nennen."
        ),
    ),
    "views.bot_status_api": (
        (
            "Der Bot lief nicht (z. B. nach einem Serverneustart), und der "
            "automatische Neustart aus dem Status-Endpunkt ist fehlgeschlagen. "
            "Die Konfiguration wurde auf „nicht aktiv“ gesetzt."
        ),
        (
            "Beispielsweise war die Datenbank im Moment des Neustarts nicht "
            "erreichbar."
        ),
        (
            "Den Bot in der Konfigurationsliste erneut starten. Bleibt das "
            "Problem bestehen, bitte die Referenznummer dem Betreiber nennen."
        ),
    ),
    "views.manual_sell": (
        (
            "Der manuelle Verkaufsaufruf konnte nicht ausgeführt werden."
        ),
        (
            "Der Bot läuft nicht, oder für das Symbol ist keine offene Position "
            "vorhanden."
        ),
        (
            "Prüfen, ob der Bot läuft und die Position im Dashboard offen ist, "
            "dann erneut versuchen."
        ),
    ),
    "views.kill_switch": (
        (
            "Der Not-Abbruch (Kill-Switch) konnte nicht ausgeführt werden."
        ),
        (
            "Der Bot läuft nicht oder die Exchange war nicht erreichbar, um "
            "Schließkurse für die offenen Positionen zu ermitteln."
        ),
        (
            "Zuerst den Bot starten, dann den Kill-Switch erneut auslösen."
        ),
    ),
}

_GENERIC_EXPLANATION: tuple[str, str, str] = (
    (
        "Bei dieser Funktion ist ein Fehler aufgetreten."
    ),
    (
        "Die konkrete Ursache ist serverseitig unter dieser Referenznummer "
        "dokumentiert; eine sichere Kurzform steht nicht zur Verfügung."
    ),
    (
        "Bei anhaltenden Problemen bitte Referenznummer und betroffene "
        "Konfiguration dem Betreiber nennen."
    ),
)

_EXCHANGE_LABELS = {
    "binance": "Binance",
    "bingx": "BingX",
    "bybit": "Bybit",
    "bitmart": "BitMart",
    "bitunix": "Bitunix",
}


def _safe_context(details: Any) -> list[str]:
    """Holt ausschließlich sichere Fakten aus der Diagnose für die Anzeige.

    Niemals freier Text aus ``details``: nur feste, app-eigene Werte –
    Exchange/Marktart (stammen aus der eigenen Konfiguration), die eigenen
    Symbole des Nutzers und der vom Bot gemeldete Ban-Zeitpunkt.
    """
    if not isinstance(details, dict):
        return []
    context: list[str] = []
    exchange = details.get("exchange")
    market = details.get("market")
    if exchange:
        label = _EXCHANGE_LABELS.get(str(exchange).strip().lower(), str(exchange))
        entry = str(label)
        if market in {"spot", "futures"}:
            entry += " · Spot" if market == "spot" else " · Futures"
        context.append(entry)
    symbols = details.get("invalid_symbols") or details.get("symbol") or details.get("symbols")
    if isinstance(symbols, str):
        symbols = [symbols]
    if isinstance(symbols, (list, tuple)) and symbols:
        shown = ", ".join(str(symbol) for symbol in list(symbols)[:5])
        context.append(f"betroffene Symbole: {shown}")
    retry_at = details.get("retry_at")
    if isinstance(retry_at, (int, float)) and not isinstance(retry_at, bool):
        try:
            retry_at = datetime.fromtimestamp(float(retry_at), tz=timezone.utc)
            context.append(
                f"Anfragesperre der Exchange bis ca. {retry_at:%d.%m.%Y %H:%M} Uhr (UTC)"
            )
        except (OverflowError, OSError, ValueError):
            pass
    return context


def explain_error(source: str | None, details: Any = None) -> dict[str, Any]:
    """Liefert die menschenlesbare Erklärung zu einer Fehlerquelle.

    Args:
        source: Stabile Herkunftskennung aus ``ErrorLog.source``.
        details: Optionaler, JSON-serialisierter Diagnosekontext; es werden
            ausschließlich die in ``_safe_context`` freigegebenen Felder
            angezeigt.

    Returns:
        dict mit den Schlüsseln ``what``, ``why``, ``do`` (Texte für die
        Anzeige) und ``context`` (Liste sicherer Kurzdaten, leer falls keine).
    """
    what, why, do = _ERROR_EXPLANATIONS.get((source or "").strip(), _GENERIC_EXPLANATION)
    return {"what": what, "why": why, "do": do, "context": _safe_context(details)}


def has_explanation(source: str | None) -> bool:
    """True, wenn für die Quelle eine konkrete (nicht generische) Erklärung existiert."""
    return (source or "").strip() in _ERROR_EXPLANATIONS
