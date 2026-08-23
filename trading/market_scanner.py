"""Öffentlicher Markt-Scanner für die Konfigurationsvorlagen.

Die Börse liefert die Marktdaten; t-bot trifft keine Kaufentscheidung. Die
Filter sind absichtlich konservativ: Ein Markt muss eine Kursbewegung von
mehr als zehn Prozent, einen Volumen-Ausreißer, ausreichende Orderbuch-Tiefe,
ein plausibles Volumen/Marktkapitalisierungs-Verhältnis und eine bekannte
Utility-/Fundamental-Einstufung haben. Fehlende Daten führen zum Ausschluss
statt zu einer optimistischen Annahme.
"""

from __future__ import annotations

import logging
import math
import statistics
import threading
import time
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
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 60


class MarketScannerError(MarketDataError):
    """Markt-Scanner konnte keinen belastbaren Snapshot erstellen."""


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
    """Read-only Adapter für die öffentlichen Bitunix-Ticker."""

    def __init__(self, market_type):
        self.market_type = market_type
        self.provider = PublicHTTPMarketData()
        self.base = (
            "https://fapi.bitunix.com/api/v1/futures/market"
            if market_type == "futures"
            else "https://openapi.bitunix.com/api/spot/v1"
        )

    def _payload_rows(self, endpoint):
        payload = self.provider._json(endpoint)
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = data.get("list") or data.get("records") or data.get("data") or data
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
                compact, active = item, True
            else:
                compact = item.get("symbol") or item.get("symbolName") or item.get("id")
                active = str(item.get("symbolStatus", item.get("isOpen", "1"))).upper() in {
                    "1",
                    "OPEN",
                    "TRUE",
                }
            canonical = _canonical_compact(compact) if compact else None
            if canonical and active:
                result[canonical] = {
                    "active": True,
                    "swap": self.market_type == "futures",
                    "spot": self.market_type == "spot",
                }
        return result

    def fetch_tickers(self, symbols):
        # Die Futures-API bietet einen Batch-Ticker. Spot-Installationen
        # verwenden denselben öffentlichen Pfad, sofern vom Account aktiviert.
        endpoint = f"{self.base}/tickers"
        rows = self._payload_rows(endpoint)
        result = {}
        for item in rows:
            if not isinstance(item, dict):
                continue
            canonical = _canonical_compact(item.get("symbol"))
            if canonical not in symbols:
                continue
            change = _number(item.get("percentage", item.get("price24hPcnt")))
            if change is not None and abs(change) <= 1:
                change *= 100
            result[canonical] = {
                "symbol": canonical,
                "last": item.get("lastPrice", item.get("last")),
                "percentage": change,
                "baseVolume": item.get("volume24h", item.get("volume")),
                "quoteVolume": item.get("turnover24h", item.get("quoteVolume")),
                "info": item,
            }
        return result

    def fetch_order_book(self, symbol, limit=20):
        payload = self.provider._json(
            f"{self.base}/depth",
            params={"symbol": symbol.replace("/", ""), "limit": limit},
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
_MARKET_CAP_CACHE = {}
_MARKET_CAP_CACHE_LOCK = threading.Lock()
_MARKET_CAP_TTL_SECONDS = 300


def _coingecko_market_caps(bases):
    ids = sorted({_COINGECKO_IDS[base] for base in bases if base in _COINGECKO_IDS})
    if not ids:
        return {}
    key = tuple(ids)
    now = time.monotonic()
    with _MARKET_CAP_CACHE_LOCK:
        cached = _MARKET_CAP_CACHE.get(key)
        if cached and now - cached[0] < _MARKET_CAP_TTL_SECONDS:
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
    last = _nested_number(ticker, keys=("last", "close"))
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    depth = 0.0
    for side in (bids, asks):
        for level in side[:20]:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price, amount = _number(level[0]), _number(level[1])
            elif isinstance(level, dict):
                price = _number(level.get("price", level.get("px")))
                amount = _number(level.get("amount", level.get("volume", level.get("qty"))))
            else:
                continue
            if price is not None and amount is not None and price > 0 and amount > 0:
                depth += price * amount
    if depth <= 0:
        return None, "Orderbuch ist leer"
    # Der Wert dient zusätzlich der Diagnose; der eigentliche Mindesttest
    # erfolgt relativ zum 24h-Quotevolumen.
    return {"quote": depth, "mid": last}, None


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


def _scan_uncached(exchange_id, market_type, limit=5, volatility_threshold=10.0):
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
        tickers = exchange.fetch_tickers(list(market_rows))
        normalised = []
        for symbol, ticker in tickers.items():
            if symbol not in market_rows or not isinstance(ticker, dict):
                continue
            row = _normalise_ticker(symbol, ticker, market_rows[symbol])
            if row["change_24h"] is not None and row["quote_volume_24h"] is not None:
                normalised.append(row)
        if not normalised:
            raise MarketScannerError("Die Exchange lieferte keine vollständigen 24h-Ticker")

        market_caps = _coingecko_market_caps({row["base"] for row in normalised})
        for row in normalised:
            if row["market_cap"] is None:
                row["market_cap"] = market_caps.get(row["base"])

        volumes = [row["quote_volume_24h"] for row in normalised if row["quote_volume_24h"] > 0]
        volume_reference = statistics.median(volumes) if volumes else 0
        # Orderbücher nur für eine kleine Vorauswahl laden. Das hält die
        # öffentliche API-Last und die Laufzeit der Konfigurationsseite klein.
        candidates = [row for row in normalised if abs(row["change_24h"]) > volatility_threshold]
        candidates.sort(key=lambda row: abs(row["change_24h"]), reverse=True)
        candidates = candidates[: max(20, limit * 6)]
        rows = []
        for row in candidates:
            reasons = []
            volume_ratio = row["quote_volume_24h"] / volume_reference if volume_reference else None
            volume_spike = bool(volume_ratio is not None and volume_ratio >= 1.5)
            if not volume_spike:
                reasons.append("kein signifikanter Volumen-Ausreißer")
            fundamental_ok, fundamental_reason = _utility_check(
                row["symbol"], row["ticker"], row["market"]
            )
            if not fundamental_ok:
                reasons.append(fundamental_reason)
            market_cap = row["market_cap"]
            liquidity_ratio = (
                row["quote_volume_24h"] / market_cap if market_cap and market_cap > 0 else None
            )
            if liquidity_ratio is None or liquidity_ratio < 0.10:
                reasons.append("Volumen/Marktkapitalisierung unter 10 % oder unbekannt")
            depth, depth_error = _orderbook_depth(exchange, row["symbol"], row["ticker"])
            depth_quote = depth["quote"] if depth else None
            depth_ratio = (
                depth_quote / row["quote_volume_24h"]
                if depth_quote and row["quote_volume_24h"] > 0
                else None
            )
            # 0,1 % des Tagesvolumens im sichtbaren Orderbuch ist eine
            # Mindesttiefe. Dünne Bücher werden damit nicht als liquide verkauft.
            if depth_ratio is None or depth_ratio < 0.001:
                reasons.append(depth_error or "Orderbuch-Tiefe unter 0,1 % des Tagesvolumens")
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
                "minimum_volume_to_market_cap": 0.10,
                "minimum_orderbook_depth_ratio": 0.001,
                "volume_spike_multiple": 1.5,
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
    refresh: bool = False,
):
    """Ermittelt bis zu fünf qualifizierte Gainer und Loser mit TTL-Cache."""
    exchange_id, market = exchange_id.strip().lower(), market.strip().lower()
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise MarketScannerError("Ungültige Exchange")
    if market not in {"spot", "futures"}:
        raise MarketScannerError("Ungültige Marktart")
    try:
        limit = max(1, min(5, int(limit)))
        volatility_threshold = float(volatility_threshold)
        if not math.isfinite(volatility_threshold) or volatility_threshold < 10:
            raise ValueError
    except (TypeError, ValueError):
        raise MarketScannerError("Ungültige Scanner-Parameter") from None
    key = (exchange_id, market, limit, volatility_threshold)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and not refresh and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
    result = _scan_uncached(exchange_id, market, limit, float(volatility_threshold))
    with _CACHE_LOCK:
        _CACHE[key] = (now, result)
    return result


def get_top_gainers(exchange_id: str, market: str = "spot", *, refresh: bool = False):
    """Kompatibler Helfer für Integrationen, die nur Gainer benötigen."""
    return scan_market_opportunities(exchange_id, market, refresh=refresh)["gainers"]


def get_top_losers(exchange_id: str, market: str = "spot", *, refresh: bool = False):
    """Kompatibler Helfer für Integrationen, die nur Loser benötigen."""
    return scan_market_opportunities(exchange_id, market, refresh=refresh)["losers"]


def scan_all_market_opportunities(*, market="spot", refresh=False):
    """Scannt jede konfigurierte Exchange einzeln und isoliert Fehler."""
    results = {}
    errors = {}
    for exchange_id in SUPPORTED_EXCHANGES:
        try:
            results[exchange_id] = scan_market_opportunities(exchange_id, market, refresh=refresh)
        except MarketScannerError as exc:
            errors[exchange_id] = str(exc)
    return {"market": market, "exchanges": results, "errors": errors}
