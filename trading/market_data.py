import asyncio
import json
import random
import re
import time
from dataclasses import dataclass

import aiohttp
import ccxt
import requests


class MarketDataError(RuntimeError):
    """Basisklasse für verständliche Fehler des Marktdaten-Layers."""


class MarketDataConnectionError(MarketDataError):
    """Die Börse war technisch nicht erreichbar oder lieferte ungültige Daten."""


class WebSocketReconnectError(MarketDataConnectionError):
    def __init__(self, exchange, attempts, last_error):
        self.exchange = exchange
        self.attempts = attempts
        self.last_error = str(last_error)
        super().__init__(
            f"{exchange}-WebSocket konnte nach {attempts} Versuchen nicht "
            f"wiederhergestellt werden: {last_error}"
        )


class SymbolValidationError(MarketDataError):
    def __init__(self, exchange, symbols):
        self.exchange = exchange
        self.symbols = sorted(set(symbols))
        joined = ", ".join(self.symbols)
        super().__init__(f"Ungültige/nicht gelistete Symbole bei {exchange}: {joined}")


@dataclass
class RateLimitError(MarketDataError):
    message: str
    status_code: int | None = None
    retry_at: float | None = None

    def __str__(self):
        if self.retry_at:
            retry_text = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.retry_at))
            return f"{self.message} (gesperrt bis {retry_text})"
        return self.message


def _base_symbol(symbol):
    return symbol.split(":", 1)[0]


def _compact_symbol(symbol):
    return _base_symbol(symbol).replace("/", "").upper()


def _bitmart_symbol(symbol):
    return _base_symbol(symbol).replace("/", "_").upper()


def _ban_timestamp(text):
    match = re.search(r"banned\s+until\s+(\d{10,13})", text, flags=re.IGNORECASE)
    if not match:
        return None
    timestamp = int(match.group(1))
    return timestamp / 1000 if timestamp > 10_000_000_000 else float(timestamp)


class PublicHTTPMarketData:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "t-bot-paper-trading/1.0"})

    def _json(self, url, **kwargs):
        try:
            response = self.session.get(url, timeout=15, **kwargs)
        except requests.RequestException as exc:
            raise MarketDataConnectionError(f"Verbindung zu {url} fehlgeschlagen: {exc}") from exc

        if response.status_code in {418, 429}:
            retry_at = _ban_timestamp(response.text)
            retry_after = response.headers.get("Retry-After")
            if retry_at is None and retry_after:
                try:
                    retry_at = time.time() + float(retry_after)
                except ValueError:
                    retry_at = None
            raise RateLimitError(
                f"Rate-Limit {response.status_code} von {response.url}: {response.text[:300]}",
                status_code=response.status_code,
                retry_at=retry_at,
            )
        try:
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketDataConnectionError(f"Ungültige Antwort von {response.url}: {exc}") from exc

    def close(self):
        self.session.close()


class BinancePublicSymbolCatalog(PublicHTTPMarketData):
    """Maßgeblicher Binance-Spot-/Futures-Katalog für Vorschläge und Validierung."""

    endpoints = {
        "spot": "https://api.binance.com/api/v3/exchangeInfo",
        "futures": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    }

    def __init__(self, market):
        if market not in self.endpoints:
            raise ValueError(f"Nicht unterstützter Binance-Markt: {market}")
        super().__init__()
        self.market = market
        self._available_symbols = None

    def available_symbols(self):
        if self._available_symbols is not None:
            return self._available_symbols
        payload = self._json(self.endpoints[self.market])
        rows = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise MarketDataConnectionError(
                "Binance lieferte keinen gültigen ExchangeInfo-Symbolkatalog"
            )
        available = set()
        for item in rows:
            if not isinstance(item, dict) or item.get("status") != "TRADING":
                continue
            if self.market == "futures" and item.get("contractType") != "PERPETUAL":
                continue
            if self.market == "spot" and item.get("isSpotTradingAllowed") is False:
                continue
            base, quote = item.get("baseAsset"), item.get("quoteAsset")
            if base and quote:
                available.add(f"{str(base).upper()}/{str(quote).upper()}")
        if not available:
            raise MarketDataConnectionError(f"Binance lieferte keine aktiven {self.market}-Symbole")
        self._available_symbols = available
        return available

    def validate_symbols(self, symbols):
        available = self.available_symbols()
        invalid = [symbol for symbol in symbols if _base_symbol(symbol).upper() not in available]
        if invalid:
            raise SymbolValidationError("Binance", invalid)
        return []


class BinancePublicMarketData:
    """Persistenter Binance-WebSocket-Stream ohne REST-Request-Weight.

    `miniTicker` sendet höchstens einmal pro Sekunde Updates. Eine Verbindung
    enthält alle konfigurierten Symbole, sodass weder Einzel- noch Batch-REST-
    Polling stattfindet und ein bestehender REST-IP-Ban nicht verlängert wird.
    """

    def __init__(self, market):
        self.market = market
        self._session = None
        self._websocket = None
        self._stream_key = None
        self._latest = {}
        self._symbol_by_compact = {}
        self._connected_at = 0
        self._endpoint_index = 0

    @property
    def websocket_base_urls(self):
        if self.market == "futures":
            # Zweiter dokumentierter USDT-M-Futures-Endpunkt als Ausweichpfad:
            # Der Reconnect rotiert die Endpunkte und übersteht damit auch den
            # Ausfall eines einzelnen Binance-Stream-Frontends.
            return (
                "wss://fstream.binance.com/stream",
                "wss://fstream.binance.com:9443/stream",
            )
        return (
            "wss://stream.binance.com:443/stream",
            "wss://stream.binance.com:9443/stream",
            "wss://data-stream.binance.vision/stream",
        )

    async def _disconnect(self):
        if self._websocket is not None:
            await self._websocket.close()
            self._websocket = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._connected_at = 0

    async def _connect(self, symbols):
        compact_symbols = tuple(sorted(_compact_symbol(symbol) for symbol in symbols))
        connection_is_fresh = (
            self._connected_at and time.monotonic() - self._connected_at < 23 * 3600
        )
        if (
            self._websocket is not None
            and not self._websocket.closed
            and compact_symbols == self._stream_key
            and connection_is_fresh
        ):
            return
        await self._disconnect()
        self._stream_key = compact_symbols
        self._symbol_by_compact = {_compact_symbol(symbol): symbol for symbol in symbols}
        streams = "/".join(f"{symbol.lower()}@miniTicker" for symbol in compact_symbols)
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=30)
        self._session = aiohttp.ClientSession(timeout=timeout)
        endpoint = self.websocket_base_urls[self._endpoint_index % len(self.websocket_base_urls)]
        try:
            self._websocket = await self._session.ws_connect(
                endpoint,
                params={"streams": streams},
                heartbeat=20,
                autoping=True,
                max_msg_size=1_000_000,
            )
            self._connected_at = time.monotonic()
        except Exception as exc:
            await self._disconnect()
            raise MarketDataConnectionError(
                f"Binance-WebSocket-Verbindung zu {endpoint} fehlgeschlagen: {exc}"
            ) from exc

    def _consume_message(self, message):
        if message.type != aiohttp.WSMsgType.TEXT:
            if message.type in {
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.ERROR,
            }:
                raise MarketDataConnectionError("Binance-WebSocket wurde geschlossen")
            return False
        try:
            payload = json.loads(message.data)
            data = payload.get("data", payload)
            compact = str(data["s"]).upper()
            price = data.get("c")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MarketDataConnectionError(
                f"Ungültige Binance-WebSocket-Nachricht: {exc}"
            ) from exc
        original = self._symbol_by_compact.get(compact)
        if original and price is not None:
            self._latest[original] = {"last": price}
            return True
        return False

    async def _fetch_once(self, symbols, timeout_seconds):
        await self._connect(symbols)
        required = set(symbols)
        received_update = False
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            timeout = min(2, max(0.05, deadline - asyncio.get_running_loop().time()))
            try:
                message = await asyncio.wait_for(self._websocket.receive(), timeout=timeout)
            except TimeoutError:
                if required.issubset(self._latest) and received_update:
                    break
                continue
            received_update = self._consume_message(message) or received_update
            if required.issubset(self._latest) and received_update:
                break

        missing = required.difference(self._latest)
        if missing:
            raise SymbolValidationError("Binance", missing)
        if not received_update:
            raise MarketDataConnectionError("Keine aktuellen Daten vom Binance-WebSocket empfangen")

        # Während des konfigurierten Abfrageintervalls läuft der Stream weiter.
        # Alle bereits gepufferten Nachrichten konsumieren, damit sich bei
        # mehreren Symbolen kein stetig wachsender Rückstau bildet.
        for _ in range(10_000):
            try:
                message = await asyncio.wait_for(self._websocket.receive(), timeout=0.01)
            except TimeoutError:
                break
            self._consume_message(message)
        return {symbol: self._latest[symbol].copy() for symbol in symbols}

    async def fetch_tickers_async(self, symbols, timeout_seconds=12, max_reconnects=5):
        last_error = None
        for attempt in range(1, max_reconnects + 1):
            try:
                return await self._fetch_once(symbols, timeout_seconds)
            except SymbolValidationError:
                raise
            except MarketDataConnectionError as exc:
                last_error = exc
                await self._disconnect()
                self._endpoint_index = (self._endpoint_index + 1) % len(self.websocket_base_urls)
                if attempt >= max_reconnects:
                    break
                delay = min(30, 2 ** (attempt - 1)) + random.uniform(0, 0.5)
                await asyncio.sleep(delay)
        raise WebSocketReconnectError("Binance", max_reconnects, last_error)

    async def validate_symbols_async(self, symbols):
        try:
            await self.fetch_tickers_async(symbols, timeout_seconds=10, max_reconnects=2)
        finally:
            await self.close()
        return []

    async def close(self):
        await self._disconnect()


class BybitPublicSymbolCatalog(PublicHTTPMarketData):
    """Maßgeblicher Bybit-Katalog für Spot und Linear-Futures (USDT-Perpetuals)."""

    base_url = "https://api.bybit.com"
    category_map = {"spot": "spot", "futures": "linear"}

    def __init__(self, market):
        if market not in self.category_map:
            raise ValueError(f"Nicht unterstützter Bybit-Markt: {market}")
        super().__init__()
        self.market = market
        self.category = self.category_map[market]
        self._available_symbols = None

    def _fetch_instruments(self):
        symbols = set()
        cursor = ""
        for _ in range(10):
            params = {"category": self.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = self._json(f"{self.base_url}/v5/market/instruments-info", params=params)
            if str(payload.get("retCode")) != "0":
                raise MarketDataConnectionError(
                    f"Bybit-Instruments fehlgeschlagen ({payload.get('retCode')}): "
                    f"{payload.get('retMsg', payload)}"
                )
            result = payload.get("result") or {}
            rows = result.get("list") or []
            if not isinstance(rows, list):
                raise MarketDataConnectionError("Bybit lieferte keinen gültigen Instrumenten-Katalog")
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if str(item.get("status", "")).lower() != "trading":
                    continue
                raw_symbol = item.get("symbol") or ""
                compact = str(raw_symbol).replace("_", "").replace("/", "").upper()
                if compact.endswith("USDT") and len(compact) > 4:
                    canonical = f"{compact[:-4]}/USDT"
                else:
                    canonical = None
                    for quote in ("USDT", "USDC", "BTC", "ETH"):
                        if compact.endswith(quote) and len(compact) > len(quote):
                            canonical = f"{compact[: -len(quote)]}/{quote}"
                            break
                if canonical:
                    symbols.add(canonical)
            cursor = (result.get("nextPageCursor") or "").strip()
            if not cursor:
                break
        if not symbols:
            raise MarketDataConnectionError(f"Bybit lieferte keine aktiven {self.market}-Symbole")
        return symbols

    def available_symbols(self):
        if self._available_symbols is not None:
            return self._available_symbols
        self._available_symbols = self._fetch_instruments()
        return self._available_symbols

    def validate_symbols(self, symbols):
        available = self.available_symbols()
        invalid = [symbol for symbol in symbols if _base_symbol(symbol).upper() not in available]
        if invalid:
            raise SymbolValidationError("Bybit", invalid)
        return []


class BybitPublicMarketData(PublicHTTPMarketData):
    """Öffentliche Bybit-Marktdaten für Paper-Trading (ohne API-Schlüssel)."""

    base_url = "https://api.bybit.com"
    category_map = {"spot": "spot", "futures": "linear"}

    def __init__(self, market):
        if market not in self.category_map:
            raise ValueError(f"Nicht unterstützter Bybit-Markt: {market}")
        super().__init__()
        self.market = market
        self.category = self.category_map[market]
        self._available_symbols = None

    def _fetch_tickers_payload(self):
        payload = self._json(
            f"{self.base_url}/v5/market/tickers", params={"category": self.category}
        )
        if str(payload.get("retCode")) != "0":
            raise MarketDataConnectionError(
                f"Bybit-Ticker fehlgeschlagen ({payload.get('retCode')}): "
                f"{payload.get('retMsg', payload)}"
            )
        result = payload.get("result") or {}
        rows = result.get("list") or []
        if not isinstance(rows, list):
            raise MarketDataConnectionError("Bybit lieferte keinen gültigen Ticker-Katalog")
        return rows

    def available_symbols(self):
        if self._available_symbols is not None:
            return self._available_symbols
        rows = self._fetch_tickers_payload()
        symbols = set()
        for item in rows:
            if not isinstance(item, dict):
                continue
            raw = item.get("symbol") or ""
            compact = str(raw).upper()
            if compact.endswith("USDT") and len(compact) > 4:
                symbols.add(f"{compact[:-4]}/USDT")
        if not symbols:
            raise MarketDataConnectionError(f"Bybit lieferte keine {self.market}-Symbole")
        self._available_symbols = symbols
        return symbols

    def validate_symbols(self, symbols):
        available = self.available_symbols()
        invalid = [symbol for symbol in symbols if _base_symbol(symbol).upper() not in available]
        if invalid:
            raise SymbolValidationError("Bybit", invalid)
        return []

    def fetch_tickers(self, symbols):
        self.validate_symbols(symbols)
        compact_to_original = {_compact_symbol(symbol): symbol for symbol in symbols}
        rows = self._fetch_tickers_payload()
        prices = {}
        for item in rows:
            compact = str(item.get("symbol", "")).upper()
            original = compact_to_original.get(compact)
            if not original:
                continue
            last = item.get("lastPrice") or item.get("markPrice")
            if last is not None:
                prices[original] = {"last": last}
        missing = set(symbols).difference(prices)
        if missing:
            raise MarketDataConnectionError(
                f"Bybit lieferte keine Preise für: {', '.join(sorted(missing))}"
            )
        return prices


class BitunixPublicMarketData(PublicHTTPMarketData):
    """Öffentliche Bitunix-Marktdaten für Paper-Trading (ohne API-Schlüssel)."""

    futures_base = "https://fapi.bitunix.com/api/v1/futures/market"
    spot_base = "https://openapi.bitunix.com/api/spot/v1"

    def __init__(self, market):
        if market not in {"spot", "futures"}:
            raise ValueError(f"Nicht unterstützter Bitunix-Markt: {market}")
        super().__init__()
        self.market = market
        self._available_symbols = None

    @staticmethod
    def _successful(payload):
        return str(payload.get("code")) == "0"

    def available_symbols(self):
        if self._available_symbols is not None:
            return self._available_symbols
        if self.market == "futures":
            payload = self._json(f"{self.futures_base}/trading_pairs")
        else:
            payload = self._json(f"{self.spot_base}/common/coin_pair/list")
        if not self._successful(payload):
            raise MarketDataConnectionError(
                f"Bitunix-Symbolliste fehlgeschlagen ({payload.get('code')}): "
                f"{payload.get('msg', payload.get('message', payload))}"
            )
        data = payload.get("data") or []
        if isinstance(data, dict):
            data = data.get("list") or data.get("records") or data.get("data") or []
        symbols = set()
        for item in data:
            if isinstance(item, str):
                compact = item
                active = True
            else:
                compact = (
                    item.get("symbol")
                    or item.get("symbolName")
                    or (f"{item.get('base', '')}{item.get('quote', '')}")
                    or item.get("id")
                )
                status = str(item.get("symbolStatus", item.get("isOpen", "OPEN"))).upper()
                active = status in {"OPEN", "1", "TRUE"}
            if compact and active:
                symbols.add(str(compact).replace("_", "").replace("/", "").upper())
        if not symbols:
            raise MarketDataConnectionError(
                "Bitunix lieferte keine nutzbare Symbolliste; die Konfiguration wird "
                "vorsorglich nicht aktiviert."
            )
        self._available_symbols = symbols
        return symbols

    def validate_symbols(self, symbols):
        available = self.available_symbols()
        invalid = [symbol for symbol in symbols if _compact_symbol(symbol) not in available]
        if invalid:
            raise SymbolValidationError("Bitunix", invalid)
        return []

    def fetch_tickers(self, symbols):
        self.validate_symbols(symbols)
        compact_to_original = {_compact_symbol(symbol): symbol for symbol in symbols}
        prices = {}
        if self.market == "futures":
            payload = self._json(
                f"{self.futures_base}/tickers",
                params={"symbols": ",".join(compact_to_original)},
            )
            if not self._successful(payload):
                raise MarketDataError(
                    f"Bitunix-Ticker fehlgeschlagen ({payload.get('code')}): {payload.get('msg')}"
                )
            rows = payload.get("data") or []
            for row in rows:
                compact = str(row.get("symbol", "")).upper()
                original = compact_to_original.get(compact)
                last = row.get("lastPrice") or row.get("last") or row.get("markPrice")
                if original and last is not None:
                    prices[original] = {"last": last}
        else:
            for compact, original in compact_to_original.items():
                payload = self._json(
                    f"{self.spot_base}/market/last_price",
                    params={"symbol": compact},
                )
                if not self._successful(payload):
                    raise MarketDataError(
                        f"Bitunix-Spot-Ticker für {original} fehlgeschlagen "
                        f"({payload.get('code')}): {payload.get('msg')}"
                    )
                data = payload.get("data")
                last = data.get("lastPrice") if isinstance(data, dict) else data
                if last is None:
                    raise MarketDataConnectionError(
                        f"Bitunix lieferte keinen Spot-Preis für {original}"
                    )
                prices[original] = {"last": last}
        missing = set(symbols).difference(prices)
        if missing:
            raise MarketDataConnectionError(
                f"Bitunix lieferte keine Preise für: {', '.join(sorted(missing))}"
            )
        return prices


class BitMartPublicMarketData(PublicHTTPMarketData):
    """Ersatz für den in CCXT 4.5 entfernten BitMart-Adapter (Spot-Marktdaten)."""

    ticker_url = "https://api-cloud.bitmart.com/spot/quotation/v3/ticker"
    symbols_url = "https://api-cloud.bitmart.com/spot/v1/symbols"

    def __init__(self, market):
        if market != "spot":
            raise ValueError(
                "BitMart Futures wird nicht unterstützt; bitte Spot oder eine andere Exchange wählen."
            )
        super().__init__()
        self._available_symbols = None

    def available_symbols(self):
        if self._available_symbols is not None:
            return self._available_symbols
        payload = self._json(self.symbols_url)
        if payload.get("code") != 1000:
            raise MarketDataConnectionError(
                f"BitMart-Symbolliste fehlgeschlagen ({payload.get('code')}): "
                f"{payload.get('message', payload)}"
            )
        raw_symbols = (payload.get("data") or {}).get("symbols") or []
        symbols = set()
        for item in raw_symbols:
            raw = item.get("symbol") if isinstance(item, dict) else item
            if raw:
                symbols.add(str(raw).upper())
        if not symbols:
            raise MarketDataConnectionError("BitMart lieferte eine leere Symbolliste")
        self._available_symbols = symbols
        return symbols

    def validate_symbols(self, symbols):
        available = self.available_symbols()
        invalid = [symbol for symbol in symbols if _bitmart_symbol(symbol) not in available]
        if invalid:
            raise SymbolValidationError("BitMart", invalid)
        return []

    def fetch_tickers(self, symbols):
        self.validate_symbols(symbols)
        prices = {}
        for symbol in symbols:
            payload = self._json(self.ticker_url, params={"symbol": _bitmart_symbol(symbol)})
            if payload.get("code") != 1000:
                raise MarketDataError(
                    f"BitMart-Fehler für {symbol} ({_bitmart_symbol(symbol)}), "
                    f"Code {payload.get('code')}: {payload.get('message', payload)}"
                )
            data = payload.get("data") or {}
            last = data.get("last") or data.get("last_price")
            if last is None:
                raise MarketDataConnectionError(f"BitMart lieferte keinen Preis für {symbol}")
            prices[symbol] = {"last": last}
        return prices


def validate_exchange_symbols(exchange_id, market, symbols):
    """Validiert normalisierte CCXT-Symbole gegen die aktuell gelisteten Märkte."""
    exchange_id = exchange_id.strip().lower()
    if exchange_id == "binance":
        provider = BinancePublicSymbolCatalog(market)
        try:
            provider.validate_symbols(symbols)
        finally:
            provider.close()
        return
    if exchange_id == "bybit":
        provider = BybitPublicSymbolCatalog(market)
        try:
            provider.validate_symbols(symbols)
        finally:
            provider.close()
        return
    if exchange_id in {"bitmart", "bitunix"}:
        provider_class = (
            BitMartPublicMarketData if exchange_id == "bitmart" else BitunixPublicMarketData
        )
        provider = provider_class(market)
        try:
            provider.validate_symbols(symbols)
        finally:
            provider.close()
        return

    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise MarketDataError(f"Exchange-Adapter nicht verfügbar: {exchange_id}")
    default_type = "swap" if market == "futures" else "spot"
    exchange = exchange_class(
        {
            "enableRateLimit": True,
            "timeout": 15_000,
            "options": {"defaultType": default_type},
        }
    )
    try:
        markets = exchange.load_markets()
        invalid = []
        for symbol in symbols:
            market_info = markets.get(symbol)
            expected_type = market_info and (
                market_info.get("spot") if market == "spot" else market_info.get("swap")
            )
            if not market_info or not expected_type or market_info.get("active") is False:
                invalid.append(symbol)
        if invalid:
            raise SymbolValidationError(exchange.name, invalid)
    except SymbolValidationError:
        raise
    except (ccxt.BaseError, OSError, ValueError) as exc:
        raise MarketDataConnectionError(
            f"Symbole konnten bei {exchange.name} nicht geprüft werden: {exc}"
        ) from exc
    finally:
        close_method = getattr(exchange, "close", None)
        if close_method:
            close_method()
