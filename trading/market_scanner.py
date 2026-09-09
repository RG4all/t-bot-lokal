"""Öffentlicher Markt-Scanner für die Konfigurationsvorlagen.

Die Börse liefert die Marktdaten; t-bot trifft keine Kaufentscheidung. Die
Filter sind absichtlich konservativ: Ein Markt muss den einstellbaren
Mindest-Kursausschlag, einen Volumen-Ausreißer, ausreichende Orderbuch-Tiefe,
ein plausibles Volumen/Marktkapitalisierungs-Verhältnis und eine bekannte
Utility-/Fundamental-Einstufung haben. Fehlende Daten führen zum Ausschluss
statt zu einer optimistischen Annahme.
"""

from __future__ import annotations

import inspect
import logging
import math
import statistics
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import InvalidOperation

import ccxt
import requests

from .market_data import BitMartPublicMarketData, MarketDataError, PublicHTTPMarketData

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = ("binance", "bingx", "bybit", "bitmart", "bitunix")
_STABLE_QUOTES = {"USDT", "USDC", "BUSD", "FDUSD", "DAI", "EUR"}
# Fallback, wenn die Börse selbst keine Fundamental-Metadaten veröffentlicht.
# Das ist keine Anlagebewertung, sondern eine konservative Anti-Pump-Liste.
_ESTABLISHED_UTILITY_ASSETS = {
    "ADA",
    "AVAX",
    "BCH",
    "BNB",
    "BTC",
    "DOT",
    "ETH",
    "LINK",
    "LTC",
    "MATIC",
    "NEAR",
    "SOL",
    "TON",
    "TRX",
    "UNI",
    "XLM",
    "XRP",
}
# K3/PERF-24: gedeckelter LRU statt TTL-only-dict. Die Cache-Schluessel
# enthalten benutzergewaehlte Float-Filter; ohne Eviction wuechse das Dict
# im langlebigen Webprozess (512-MB-Limit inkl. Bots!) monoton. Der Lock wird
# bewusst nur fuer Dict-Zugriffe gehalten, nie ueber Netzwerk-I/O.
_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 60
_CACHE_MAX_ENTRIES = 32
# W10: refresh=True umging den TTL bisher pro Aufruf komplett. Mehrere Nutzer
# (oder UI-Retries) feuern damit ungehindert auf die rate-limitierten
# oeffentlichen Ticker-Endpunkte der Boersen. Erzwungene Scans werden je
# (Exchange, Marktart) gedrosselt; innerhalb des Fensters bedient der Scan den
# zuletzt bekannten – ggf. veralteten – Stand, statt einen Exchange-Aufruf zu
# starten. Ohne jegliche Cache-Info wird immer gescannt (Korrektheit vor
# Drosselung, der Fall verbraeumt nur das Zeitfenster).
_FORCE_MIN_INTERVAL_SECONDS = 20.0
_FORCE_THROTTLE_MAX_ENTRIES = 16
_LAST_FORCED: OrderedDict[tuple, float] = OrderedDict()
_FORCE_THROTTLE_LOCK = threading.Lock()


class MarketScannerError(MarketDataError):
    """Markt-Scanner konnte keinen belastbaren Snapshot erstellen."""


class MarketScannerFilterError(MarketScannerError):
    """Benutzereingabe für Markt oder Scanner-Schwellenwert ist ungültig."""


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return number if math.isfinite(number) else None


def _nested_number(*objects, keys):
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for key in keys:
            value = obj.get(key)
            result = _number(value)
            if result is not None:
                return result
        info = obj.get("info")
        if isinstance(info, dict):
            for key in keys:
                result = _number(info.get(key))
                if result is not None:
                    return result
    return None


def _base_asset(symbol):
    return str(symbol).split("/", 1)[0].split(":", 1)[0].upper()


def _quote_asset(symbol):
    parts = str(symbol).split("/")
    return parts[1].split(":", 1)[0].upper() if len(parts) > 1 else ""


def _canonical_compact(value):
    compact = str(value).replace("_", "").replace("/", "").upper()
    for quote in sorted(_STABLE_QUOTES, key=len, reverse=True):
        if compact.endswith(quote) and len(compact) > len(quote):
            return f"{compact[: -len(quote)]}/{quote}"
    return None


class _BitMartScannerExchange:
    """Kleiner read-only Adapter für die von CCXT entfernte BitMart-API."""

    symbols_url = "https://api-cloud.bitmart.com/spot/v1/symbols"
    tickers_url = "https://api-cloud.bitmart.com/spot/quotation/v3/tickers"
    books_url = "https://api-cloud.bitmart.com/spot/quotation/v3/books"

    def __init__(self, config=None):
        self.provider = BitMartPublicMarketData("spot")

    def load_markets(self):
        symbols = self.provider.available_symbols()
        return {
            canonical: {"active": True, "spot": True}
            for compact in symbols
            if (canonical := _canonical_compact(compact))
        }

    def fetch_tickers(self, symbols):
        payload = self.provider._json(self.tickers_url)
        data = payload.get("data") or []
        raw_rows = data.get("tickers") or [] if isinstance(data, dict) else data
        result = {}
        for raw in raw_rows:
            if isinstance(raw, dict):
                values = raw
                compact = values.get("symbol")
            elif isinstance(raw, (list, tuple)) and len(raw) >= 8:
                compact = raw[0]
                values = {
                    "last": raw[1],
                    "baseVolume": raw[2],
                    "quoteVolume": raw[3],
                    "open": raw[4],
                    "fluctuation": raw[7],
                }
            else:
                continue
            canonical = _canonical_compact(compact)
            if not canonical or canonical not in symbols:
                continue
            change = values.get("percentage", values.get("fluctuation"))
            change = _number(change)
            if change is not None and abs(change) <= 1:
                change *= 100
            result[canonical] = {
                "symbol": canonical,
                "last": values.get("last", values.get("last_price")),
                "percentage": change,
                "baseVolume": values.get("baseVolume", values.get("v_24h")),
                "quoteVolume": values.get("quoteVolume", values.get("qv_24h")),
                "info": values,
            }
        return result

    def fetch_order_book(self, symbol, limit=20):
        payload = self.provider._json(
            self.books_url,
            params={"symbol": symbol.replace("/", "_"), "limit": limit},
        )
        data = payload.get("data") or {}
        return {"bids": data.get("bids", []), "asks": data.get("asks", [])}

    def close(self):
        self.provider.close()


class _BitunixScannerExchange:
    """Read-only Adapter für die dokumentierten Bitunix-Public-Endpunkte.

    Futures stellt einen 24h-Batch-Ticker bereit. Die Spot-API hat dagegen
    ausdrücklich *keinen* ``/tickers``-Endpunkt. Für Spot werden deshalb die
    letzten 24 Stunden aus stündlichen Klines der ohnehin fundamental
    zulässigen Märkte berechnet. So wird weder der nicht existente Endpunkt
    aufgerufen noch die gesamte Pair-Liste mit Einzelrequests überzogen.
    """

    def __init__(self, market_type):
        self.market_type = market_type
        self.provider = PublicHTTPMarketData()
        self._spot_precisions = {}
        self.base = (
            "https://fapi.bitunix.com/api/v1/futures/market"
            if market_type == "futures"
            else "https://openapi.bitunix.com/api/spot/v1"
        )

    def _payload_rows(self, endpoint, **kwargs):
        payload = self.provider._json(endpoint, **kwargs)
        if str(payload.get("code", "0")) != "0":
            raise MarketDataError(
                f"Bitunix-API-Fehler {payload.get('code')}: "
                f"{payload.get('msg', payload.get('message', 'unbekannter Fehler'))}"
            )
        data = payload.get("data") or []
        if isinstance(data, dict):
            nested = (
                data.get("list")
                or data.get("records")
                or data.get("items")
                or data.get("klines")
                or data.get("data")
            )
            # Some Spot responses wrap rows, while the documentation also
            # permits one candle object. Handle both forms without inventing
            # fields that the endpoint did not return.
            data = nested if nested is not None else [data]
        return data if isinstance(data, list) else []

    def load_markets(self):
        endpoint = (
            f"{self.base}/trading_pairs"
            if self.market_type == "futures"
            else f"{self.base}/common/coin_pair/list"
        )
        result = {}
        for item in self._payload_rows(endpoint):
            if isinstance(item, str):
                compact, active, metadata = item, True, {}
            elif isinstance(item, dict):
                compact = item.get("symbol") or item.get("symbolName") or (
                    f"{item.get('base', '')}{item.get('quote', '')}"
                ) or item.get("id")
                active = str(item.get("symbolStatus", item.get("isOpen", "1"))).upper() in {
                    "1",
                    "OPEN",
                    "TRUE",
                }
                metadata = item.copy()
            else:
                continue
            canonical = _canonical_compact(compact) if compact else None
            if canonical and active:
                result[canonical] = {
                    **metadata,
                    "active": True,
                    "swap": self.market_type == "futures",
                    "spot": self.market_type == "spot",
                }
                if self.market_type == "spot":
                    precisions = metadata.get("precisions")
                    if isinstance(precisions, (list, tuple)) and precisions:
                        self._spot_precisions[canonical] = precisions[0]
                    else:
                        self._spot_precisions[canonical] = metadata.get("quotePrecision", 0)
        return result

    @staticmethod
    def _kline_values(item):
        if isinstance(item, dict):
            return {
                "timestamp": item.get(
                    "timestamp", item.get("time", item.get("ts", item.get("id")))
                ),
                "open": _number(item.get("open", item.get("o"))),
                "close": _number(item.get("close", item.get("c", item.get("last")))),
                "base_volume": _number(
                    item.get("baseVolume", item.get("baseVol", item.get("volume", item.get("vol"))))
                ),
                "quote_volume": _number(
                    item.get(
                        "quoteVolume",
                        item.get("quoteVol", item.get("turnover", item.get("amount"))),
                    )
                ),
            }
        if isinstance(item, (list, tuple)) and len(item) >= 5:
            return {
                "timestamp": _number(item[0]),
                "open": _number(item[1]),
                "close": _number(item[4]),
                # Der dokumentierte Spot-Kline-Array definiert keine
                # Volumenspalte. Zusätzliche Positionen werden daher nicht
                # erraten oder als Liquidität schöngerechnet.
                "base_volume": None,
                "quote_volume": None,
            }
        return None

    def _spot_ticker(self, symbol):
        compact = symbol.replace("/", "")
        rows = self._payload_rows(
            f"{self.base}/market/kline/history",
            params={"symbol": compact, "interval": "60", "limit": 24},
        )
        candles = [values for item in rows if (values := self._kline_values(item))]
        candles = [item for item in candles if item["open"] and item["close"]]
        if not candles:
            return None
        if all(item["timestamp"] is not None for item in candles):
            def sort_key(item):
                numeric = _number(item["timestamp"])
                return (0, numeric) if numeric is not None else (1, str(item["timestamp"]))

            candles.sort(key=sort_key)
        first, last = candles[0], candles[-1]
        observed_base_volume = [
            item["base_volume"] for item in candles if item["base_volume"] is not None
        ]
        base_volume = sum(observed_base_volume) if observed_base_volume else None
        quote_parts = []
        for item in candles:
            if item["quote_volume"] is not None:
                quote_parts.append(item["quote_volume"])
            elif item["base_volume"] is not None:
                # Converting an observed base volume at the observed close is
                # normalization, not a favourable substitute for missing data.
                quote_parts.append(item["base_volume"] * item["close"])
        quote_volume = sum(quote_parts) if quote_parts else None
        return {
            "symbol": symbol,
            "last": last["close"],
            "open": first["open"],
            "percentage": (last["close"] - first["open"]) / first["open"] * 100,
            "baseVolume": base_volume,
            "quoteVolume": quote_volume,
            "info": {"source": "24 stündliche Bitunix-Spot-Klines"},
        }

    def fetch_tickers(self, symbols=None):
        symbols = list(symbols or [])
        if self.market_type == "spot":
            # Unbekannte Assets würden später am Fundamentalfilter scheitern.
            # Nur diese kleine, konservative Teilmenge benötigt Kline-Requests.
            eligible_symbols = [
                symbol
                for symbol in symbols
                if _base_asset(symbol) in _ESTABLISHED_UTILITY_ASSETS
                and _quote_asset(symbol) in _STABLE_QUOTES
            ]
            result = {}
            errors = []
            for index, symbol in enumerate(eligible_symbols):
                if index:
                    time.sleep(0.11)  # dokumentiertes Limit: 10 Requests/Sekunde/IP
                try:
                    ticker = self._spot_ticker(symbol)
                except MarketDataError as exc:
                    errors.append(f"{symbol}: {exc}")
                    continue
                if ticker:
                    result[symbol] = ticker
            if not result and errors:
                raise MarketDataError(
                    "Bitunix-Spot-Klines konnten nicht geladen werden: " + "; ".join(errors[:3])
                )
            return result

        rows = self._payload_rows(
            f"{self.base}/tickers",
            params={"symbols": ",".join(symbol.replace("/", "") for symbol in symbols)},
        )
        result = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            canonical = _canonical_compact(item.get("symbol"))
            if canonical not in symbols:
                continue
            change = _number(item.get("percentage", item.get("price24hPcnt")))
            last = _number(item.get("lastPrice", item.get("last")))
            open_price = _number(item.get("open"))
            if change is None and last is not None and open_price:
                change = (last - open_price) / open_price * 100
            elif change is not None and abs(change) <= 1:
                change *= 100
            result[canonical] = {
                "symbol": canonical,
                "last": last,
                "open": open_price,
                "percentage": change,
                "baseVolume": item.get("baseVol", item.get("volume24h", item.get("volume"))),
                "quoteVolume": item.get(
                    "quoteVol", item.get("turnover24h", item.get("quoteVolume"))
                ),
                "info": item,
            }
        return result

    def fetch_order_book(self, symbol, limit=20):
        compact = symbol.replace("/", "")
        params = (
            {"symbol": compact, "limit": 15}
            if self.market_type == "futures"
            else {"symbol": compact, "precision": self._spot_precisions.get(symbol, 0)}
        )
        depth_endpoint = (
            f"{self.base}/depth"
            if self.market_type == "futures"
            else f"{self.base}/market/depth"
        )
        payload = self.provider._json(depth_endpoint, params=params)
        if str(payload.get("code", "0")) != "0":
            raise MarketDataError(
                f"Bitunix-Orderbuch fehlgeschlagen ({payload.get('code')}): {payload.get('msg')}"
            )
        data = payload.get("data") or {}
        return {"bids": data.get("bids", []), "asks": data.get("asks", [])}

    def close(self):
        self.provider.close()


def _market_scanner_exchange(exchange_id, market_type):
    if exchange_id == "bitmart":
        if market_type != "spot":
            raise MarketScannerError("BitMart bietet im t-bot nur Spot-Marktdaten")
        return _BitMartScannerExchange()
    if exchange_id == "bitunix":
        return _BitunixScannerExchange(market_type)
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise MarketScannerError(f"Exchange {exchange_id} unterstützt keinen öffentlichen Scanner")
    return exchange_class(
        {
            "enableRateLimit": True,
            "timeout": 15_000,
            "options": {"defaultType": "swap" if market_type == "futures" else "spot"},
        }
    )


_COINGECKO_IDS = {
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "BCH": "bitcoin-cash",
    "BNB": "binancecoin",
    "BTC": "bitcoin",
    "DOT": "polkadot",
    "ETH": "ethereum",
    "LINK": "chainlink",
    "LTC": "litecoin",
    "MATIC": "matic-network",
    "NEAR": "near",
    "SOL": "solana",
    "TON": "the-open-network",
    "TRX": "tron",
    "UNI": "uniswap",
    "XLM": "stellar",
    "XRP": "ripple",
}
# Gleiches Muster wie _CACHE (K3/PERF-24): gedeckelter LRU, da die Schluessel
# aus den auf der Exchange gefundenen Assets resultieren und nicht hart
# begrenzt sind.
_MARKET_CAP_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_MARKET_CAP_CACHE_LOCK = threading.Lock()
_MARKET_CAP_TTL_SECONDS = 300
_MARKET_CAP_MAX_ENTRIES = 16


def _coingecko_market_caps(bases):
    ids = sorted({_COINGECKO_IDS[base] for base in bases if base in _COINGECKO_IDS})
    if not ids:
        return {}
    key = tuple(ids)
    now = time.monotonic()
    with _MARKET_CAP_CACHE_LOCK:
        cached = _MARKET_CAP_CACHE.get(key)
        if cached is not None:
            _MARKET_CAP_CACHE.move_to_end(key)
            if now - cached[0] < _MARKET_CAP_TTL_SECONDS:
                return cached[1]
    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd", "include_market_cap": "true"},
            timeout=8,
            headers={"User-Agent": "t-bot-paper-trading/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
        values = {
            base: _number((payload.get(coin_id) or {}).get("usd_market_cap"))
            for base, coin_id in _COINGECKO_IDS.items()
            if coin_id in payload
        }
    except Exception:
        # Markt-Scanning bleibt verfügbar; fehlende Caps schließen nur die
        # betroffenen Zeilen aus. Kein externer Fehler wird verschleiert.
        values = {}
    with _MARKET_CAP_CACHE_LOCK:
        _MARKET_CAP_CACHE[key] = (now, values)
        _MARKET_CAP_CACHE.move_to_end(key)
        while len(_MARKET_CAP_CACHE) > _MARKET_CAP_MAX_ENTRIES:
            _MARKET_CAP_CACHE.popitem(last=False)
    return values


def _market_is_eligible(market, market_type):
    if market.get("active") is False:
        return False
    if market_type == "spot":
        return market.get("spot") is True or ("spot" not in market and market.get("type") == "spot")
    return market.get("swap") is True or market.get("future") is True


def _utility_check(symbol, ticker, market):
    base = _base_asset(symbol)
    info = {}
    for source in (market, ticker, ticker.get("info") if isinstance(ticker, dict) else None):
        if isinstance(source, dict):
            info.update(source)
    explicit = info.get("utility", info.get("hasUtility", info.get("fundamental_quality")))
    if isinstance(explicit, str):
        explicit = explicit.strip().lower() in {"1", "true", "yes", "utility", "established"}
    if explicit is True:
        return True, "Utility-Metadaten der Börse"
    if explicit is False:
        return False, "Fundamental-Metadaten stufen den Token nicht als Utility ein"
    if base in _ESTABLISHED_UTILITY_ASSETS:
        return True, "etabliertes Utility-Asset aus konservativer Allowlist"
    return False, "keine nachprüfbare Utility-/Fundamental-Einstufung"


def _orderbook_depth(exchange, symbol, ticker):
    try:
        orderbook = exchange.fetch_order_book(symbol, limit=20)
    except Exception as exc:
        # Ein defektes/inkomplettes Orderbuch darf den gesamten Scanner nicht
        # zu einem HTTP-500 machen; der Markt wird sauber ausgeschlossen.
        return None, f"Orderbuch nicht verfügbar: {exc}"
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []

    def valid_levels(rows):
        levels = []
        for level in rows[:20]:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price, amount = _number(level[0]), _number(level[1])
            elif isinstance(level, dict):
                price = _number(level.get("price", level.get("px")))
                amount = _number(level.get("amount", level.get("volume", level.get("qty"))))
            else:
                continue
            if price is not None and amount is not None and price > 0 and amount > 0:
                levels.append((price, amount))
        return levels

    bid_levels, ask_levels = valid_levels(bids), valid_levels(asks)
    if not bid_levels or not ask_levels:
        return None, "Orderbuch hat keine belastbaren Bid- und Ask-Seiten"
    best_bid = max(price for price, _amount in bid_levels)
    best_ask = min(price for price, _amount in ask_levels)
    if best_bid > best_ask:
        return None, "Orderbuch ist gekreuzt und damit nicht belastbar"
    mid = (best_bid + best_ask) / 2
    # Nur sofort marktnahe Liquidität ist eine belastbare Tiefe. Weit entfernte
    # Scheinorders dürfen die relative Orderbuchschwelle nicht erfüllen.
    lower, upper = mid * 0.98, mid * 1.02
    near_levels = [
        (price, amount)
        for price, amount in (*bid_levels, *ask_levels)
        if lower <= price <= upper
    ]
    depth = sum(price * amount for price, amount in near_levels)
    if depth <= 0:
        return None, "Keine Orderbuch-Tiefe innerhalb von ±2 % des Mittelkurses"
    # Der eigentliche Mindesttest erfolgt relativ zum beobachteten
    # 24h-Quotevolumen.
    return {"quote": depth, "mid": mid}, None


def _normalise_ticker(symbol, ticker, market):
    change = _nested_number(ticker, keys=("percentage", "change24h", "priceChangePercent"))
    last = _nested_number(ticker, keys=("last", "close", "lastPrice"))
    quote_volume = _nested_number(ticker, keys=("quoteVolume", "quote_volume", "volume24h"))
    base_volume = _nested_number(ticker, keys=("baseVolume", "base_volume"))
    market_cap = _nested_number(
        ticker,
        market,
        keys=("marketCap", "market_cap", "marketCapitalization", "circulatingMarketCap"),
    )
    if market_cap is None:
        circulating = _nested_number(
            ticker,
            market,
            keys=("circulatingSupply", "circulating_supply", "circulating"),
        )
        if circulating and last:
            market_cap = circulating * last
    return {
        "symbol": symbol,
        "base": _base_asset(symbol),
        "quote": _quote_asset(symbol),
        "change_24h": change,
        "last": last,
        "quote_volume_24h": quote_volume,
        "base_volume_24h": base_volume,
        "market_cap": market_cap,
        "market": market,
        "ticker": ticker,
    }


def _scan_uncached(
    exchange_id,
    market_type,
    limit=5,
    volatility_threshold=10.0,
    volume_spike_multiple=1.5,
    min_volume_market_cap_ratio=0.10,
    min_orderbook_depth_ratio=0.001,
):
    exchange = _market_scanner_exchange(exchange_id, market_type)
    try:
        markets = exchange.load_markets()
        market_rows = {
            symbol: data
            for symbol, data in markets.items()
            if _market_is_eligible(data, market_type)
            and _quote_asset(symbol) in _STABLE_QUOTES
            and (market_type == "futures" or ":" not in str(symbol).split("/")[-1])
        }
        if not market_rows:
            raise MarketScannerError(f"Keine {market_type}-Stablecoin-Märkte bei {exchange_id}")

        # Binance kann alle 24h-Ticker kompakt ohne ``symbols=[...]`` liefern.
        # Genau dieser Request vermeidet die URL-Längenfehler bei großen
        # Marktkatalogen. Nur Adapter, deren Signatur nachweislich ein Argument
        # verlangt, erhalten die Liste; ein interner TypeError darf keinesfalls
        # den problematischen großen Request nachträglich auslösen.
        if exchange_id == "binance":
            ticker_method = exchange.fetch_tickers
            try:
                signature = inspect.signature(ticker_method)
            except (TypeError, ValueError):
                signature = None
            if signature is not None:
                try:
                    signature.bind()
                except TypeError:
                    tickers = ticker_method(list(market_rows))
                else:
                    tickers = ticker_method()
            else:
                tickers = ticker_method()
        else:
            tickers = exchange.fetch_tickers(list(market_rows))
        normalised = []
        for symbol, ticker in tickers.items():
            if symbol not in market_rows or not isinstance(ticker, dict):
                continue
            row = _normalise_ticker(symbol, ticker, market_rows[symbol])
            if row["change_24h"] is not None:
                normalised.append(row)
        if not normalised:
            raise MarketScannerError("Die Exchange lieferte keine auswertbaren 24h-Kursdaten")

        market_caps = _coingecko_market_caps({row["base"] for row in normalised})
        for row in normalised:
            if row["market_cap"] is None:
                row["market_cap"] = market_caps.get(row["base"])

        volumes = [
            row["quote_volume_24h"]
            for row in normalised
            if row["quote_volume_24h"] is not None and row["quote_volume_24h"] > 0
        ]
        volume_reference = statistics.median(volumes) if volumes else 0
        # Orderbücher nur für eine kleine Vorauswahl laden. Das hält die
        # öffentliche API-Last und die Laufzeit der Konfigurationsseite klein.
        candidates = [row for row in normalised if abs(row["change_24h"]) > volatility_threshold]
        candidates.sort(key=lambda row: abs(row["change_24h"]), reverse=True)
        candidates = candidates[: max(20, limit * 6)]
        rows = []
        for row in candidates:
            reasons = []
            daily_volume = row["quote_volume_24h"]
            volume_ratio = (
                daily_volume / volume_reference
                if daily_volume is not None and daily_volume > 0 and volume_reference
                else None
            )
            volume_spike = bool(
                volume_ratio is not None and volume_ratio >= volume_spike_multiple
            )
            if daily_volume is None or daily_volume <= 0:
                reasons.append("24h-Quotevolumen fehlt oder ist nicht positiv")
            elif not volume_spike:
                reasons.append(
                    f"Volumen-Ausreißer unter {volume_spike_multiple:g}× Median"
                )
            fundamental_ok, fundamental_reason = _utility_check(
                row["symbol"], row["ticker"], row["market"]
            )
            if not fundamental_ok:
                reasons.append(fundamental_reason)
            market_cap = row["market_cap"]
            liquidity_ratio = (
                daily_volume / market_cap
                if daily_volume is not None
                and daily_volume > 0
                and market_cap
                and market_cap > 0
                else None
            )
            if liquidity_ratio is None or liquidity_ratio < min_volume_market_cap_ratio:
                reasons.append(
                    "Volumen/Marktkapitalisierung unter "
                    f"{min_volume_market_cap_ratio * 100:g} % oder unbekannt"
                )
            if daily_volume is not None and daily_volume > 0:
                depth, depth_error = _orderbook_depth(exchange, row["symbol"], row["ticker"])
            else:
                depth, depth_error = (
                    None,
                    "Orderbuch-Tiefe kann ohne positives 24h-Volumen nicht bewertet werden",
                )
            depth_quote = depth["quote"] if depth else None
            depth_ratio = (
                depth_quote / daily_volume
                if depth_quote and daily_volume is not None and daily_volume > 0
                else None
            )
            if depth_ratio is None or depth_ratio < min_orderbook_depth_ratio:
                reasons.append(
                    depth_error
                    or "Orderbuch-Tiefe unter "
                    f"{min_orderbook_depth_ratio * 100:g} % des Tagesvolumens"
                )
            eligible = not reasons
            rows.append(
                {
                    "symbol": row["symbol"],
                    "base": row["base"],
                    "change_24h": round(row["change_24h"], 4),
                    "last": row["last"],
                    "quote_volume_24h": row["quote_volume_24h"],
                    "volume_ratio": round(volume_ratio, 3) if volume_ratio is not None else None,
                    "volume_spike": volume_spike,
                    "market_cap": market_cap,
                    "liquidity_ratio": round(liquidity_ratio, 4)
                    if liquidity_ratio is not None
                    else None,
                    "orderbook_depth_quote": round(depth_quote, 4)
                    if depth_quote is not None
                    else None,
                    "orderbook_depth_ratio": round(depth_ratio, 5)
                    if depth_ratio is not None
                    else None,
                    "fundamental_ok": fundamental_ok,
                    "fundamental_reason": fundamental_reason,
                    "eligible": eligible,
                    "exclusion_reasons": reasons,
                    "market": market_type,
                }
            )
        gainers = [row for row in rows if row["eligible"] and row["change_24h"] > 0]
        losers = [row for row in rows if row["eligible"] and row["change_24h"] < 0]
        gainers.sort(key=lambda row: row["change_24h"], reverse=True)
        losers.sort(key=lambda row: row["change_24h"])
        return {
            "exchange": exchange_id,
            "market": market_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "minimum_change_24h_percent": volatility_threshold,
                "minimum_volume_to_market_cap": min_volume_market_cap_ratio,
                "minimum_orderbook_depth_ratio": min_orderbook_depth_ratio,
                "volume_spike_multiple": volume_spike_multiple,
            },
            "gainers": gainers[:limit],
            "losers": losers[:limit],
            "rows": rows,
            "risk_warning": (
                "Volatile Märkte können in kurzer Zeit starke Verluste verursachen. "
                "Nur etablierte, liquide Märkte auswählen und immer eine passende "
                "Stop-Loss-Order beziehungsweise ein begrenztes Risiko verwenden."
            ),
        }
    except MarketScannerError:
        raise
    except Exception as exc:
        raise MarketScannerError(f"Marktscanner für {exchange_id} fehlgeschlagen: {exc}") from exc
    finally:
        close = getattr(exchange, "close", None)
        if close:
            try:
                close()
            except Exception:
                logger.debug(
                    "Marktscanner-Client konnte nicht sauber geschlossen werden", exc_info=True
                )


def scan_market_opportunities(
    exchange_id: str,
    market: str,
    *,
    limit: int = 5,
    volatility_threshold: float = 10.0,
    volume_spike_multiple: float = 1.5,
    min_volume_market_cap_ratio: float = 0.10,
    min_orderbook_depth_ratio: float = 0.001,
    refresh: bool = False,
):
    """Ermittelt qualifizierte Gainer und Loser mit validierten Filtern."""
    exchange_id, market = exchange_id.strip().lower(), market.strip().lower()
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise MarketScannerFilterError("Ungültige Exchange")
    if market not in {"spot", "futures"}:
        raise MarketScannerFilterError("Ungültige Marktart")
    try:
        limit = max(1, min(5, int(limit)))
        values = {
            "volatility_threshold": float(volatility_threshold),
            "volume_spike_multiple": float(volume_spike_multiple),
            "min_volume_market_cap_ratio": float(min_volume_market_cap_ratio),
            "min_orderbook_depth_ratio": float(min_orderbook_depth_ratio),
        }
        bounds = {
            "volatility_threshold": (0.1, 100.0),
            "volume_spike_multiple": (1.0, 10.0),
            "min_volume_market_cap_ratio": (0.001, 2.0),
            "min_orderbook_depth_ratio": (0.0001, 0.10),
        }
        if any(
            not math.isfinite(values[name]) or not lower <= values[name] <= upper
            for name, (lower, upper) in bounds.items()
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise MarketScannerFilterError("Ungültige Scanner-Parameter") from None
    key = (
        "v2",
        exchange_id,
        market,
        limit,
        *(values[name] for name in sorted(values)),
    )
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            # LRU-Treffer nach hinten setzen, damit aktive Schluessel nicht
            # evikiert werden.
            _CACHE.move_to_end(key)
            if not refresh and now - cached[0] < _CACHE_TTL_SECONDS:
                return cached[1]
    if refresh:
        throttle_key = (exchange_id, market)
        with _FORCE_THROTTLE_LOCK:
            last_forced = _LAST_FORCED.get(throttle_key)
            allowed = last_forced is None or now - last_forced >= _FORCE_MIN_INTERVAL_SECONDS
            if allowed:
                _LAST_FORCED[throttle_key] = now
                _LAST_FORCED.move_to_end(throttle_key)
                while len(_LAST_FORCED) > _FORCE_THROTTLE_MAX_ENTRIES:
                    _LAST_FORCED.popitem(last=False)
        if not allowed and cached is not None:
            return cached[1]
    result = _scan_uncached(exchange_id, market, limit=limit, **values)
    with _CACHE_LOCK:
        _CACHE[key] = (now, result)
        _CACHE.move_to_end(key)
        # K3/PERF-24: harte Obergrenze; aelteste Eintraege fliegen raus, statt
        # dass der Cache nur TTL-geprueft weiterwuchert.
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return result


def get_top_gainers(exchange_id: str, market: str = "spot", *, refresh: bool = False):
    """Kompatibler Helfer für Integrationen, die nur Gainer benötigen."""
    return scan_market_opportunities(exchange_id, market, refresh=refresh)["gainers"]


def get_top_losers(exchange_id: str, market: str = "spot", *, refresh: bool = False):
    """Kompatibler Helfer für Integrationen, die nur Loser benötigen."""
    return scan_market_opportunities(exchange_id, market, refresh=refresh)["losers"]


def scan_all_market_opportunities(*, market="spot", refresh=False, **filters):
    """Scannt jede konfigurierte Exchange einzeln mit identischen Filtern."""
    results = {}
    errors = {}
    for exchange_id in SUPPORTED_EXCHANGES:
        try:
            results[exchange_id] = scan_market_opportunities(
                exchange_id, market, refresh=refresh, **filters
            )
        except MarketScannerFilterError:
            # The same invalid user input applies to every exchange and must
            # be reported as HTTP 400 by the API rather than five outages.
            raise
        except MarketScannerError:
            # Auch Teilergebnisse werden direkt als API-Payload ausgeliefert.
            logger.exception("Marktscanner nicht verfügbar für %s/%s", exchange_id, market)
            errors[exchange_id] = "Marktscanner vorübergehend nicht verfügbar."
    first = next(iter(results.values()), {})
    return {
        "market": market,
        "exchanges": results,
        "errors": errors,
        "filters": first.get("filters", {}),
        "generated_at": first.get("generated_at"),
        "risk_warning": first.get("risk_warning"),
    }
