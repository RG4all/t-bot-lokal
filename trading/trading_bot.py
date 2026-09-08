import asyncio
import inspect
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import wraps

import ccxt
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import (
    DataError,
    IntegrityError,
    OperationalError,
    close_old_connections,
    connection,
    transaction,
)
from django.db.utils import InterfaceError

from .market_data import (
    BinancePublicMarketData,
    BitMartPublicMarketData,
    BitunixPublicMarketData,
    MarketDataConnectionError,
    RateLimitError,
    SymbolValidationError,
    WebSocketReconnectError,
)
from .models import Configuration, DataLog, ErrorLog, TradingLog

logger = logging.getLogger("trading")
_EIGHT_PLACES = Decimal("0.00000001")
_MAX_DECIMAL = Decimal("999999999999.99999999")
# Maximale Zeilenzahl je Lösch-Charge in db_trim_datalog: Hält jede einzelne
# DELETE-Transaktion kurz, damit bei großen Tabellen keine langen
# Datenbank-Locks entstehen.
_DATALOG_TRIM_BATCH_SIZE = 1000
_DB_RECOVERY_LOCK = threading.Lock()
_DB_CIRCUIT_LOCK = threading.Lock()
_DB_CIRCUIT_OPEN_UNTIL = 0.0
_DB_CIRCUIT_LAST_ERROR = ""
# Eigener kleiner ORM-Pool: keine Konkurrenz mit Daphne und höchstens die
# konfigurierte Anzahl thread-lokaler Django-/Postgres-Verbindungen.
_BOT_DB_EXECUTOR = ThreadPoolExecutor(
    max_workers=settings.BOT_DB_WORKERS,
    thread_name_prefix="bot-db",
)


def _db_circuit_remaining():
    with _DB_CIRCUIT_LOCK:
        return max(0.0, _DB_CIRCUIT_OPEN_UNTIL - time.monotonic())


def _open_db_circuit(error):
    global _DB_CIRCUIT_OPEN_UNTIL, _DB_CIRCUIT_LAST_ERROR
    with _DB_CIRCUIT_LOCK:
        _DB_CIRCUIT_OPEN_UNTIL = time.monotonic() + settings.DB_CIRCUIT_BREAKER_SECONDS
        _DB_CIRCUIT_LAST_ERROR = f"{type(error).__name__}: {error}"


def _close_db_circuit():
    global _DB_CIRCUIT_OPEN_UNTIL, _DB_CIRCUIT_LAST_ERROR
    with _DB_CIRCUIT_LOCK:
        _DB_CIRCUIT_OPEN_UNTIL = 0.0
        _DB_CIRCUIT_LAST_ERROR = ""


def db_safe(max_retries=None, base_delay=None, max_delay=None, suppress=False):
    """Koordiniert Reconnects, damit viele Bots keinen DB-Thundering-Herd erzeugen."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            remaining = _db_circuit_remaining()
            if remaining:
                if suppress:
                    return None
                raise OperationalError(f"Datenbank-Circuit-Breaker noch {remaining:.0f}s geöffnet")
            try:
                close_old_connections()
                result = func(*args, **kwargs)
                _close_db_circuit()
                return result
            except (InterfaceError, OperationalError) as exc:
                last_error = exc

            retry_limit = max_retries or settings.DB_RECONNECT_MAX_RETRIES
            initial_delay = base_delay or settings.DB_RECONNECT_BASE_DELAY
            delay_cap = max_delay or settings.DB_RECONNECT_MAX_DELAY
            # Nur ein Executor-Thread probiert aktiv die Wiederherstellung.
            # Alle anderen warten und laufen nach seiner Erholung direkt weiter.
            with _DB_RECOVERY_LOCK:
                remaining = _db_circuit_remaining()
                if remaining:
                    if suppress:
                        return None
                    raise OperationalError(
                        f"Datenbank-Circuit-Breaker noch {remaining:.0f}s geöffnet"
                    )
                for attempt in range(1, retry_limit + 1):
                    message = str(last_error)
                    dns_failure = "could not translate host name" in message.lower()
                    try:
                        connection.close()
                    except Exception:
                        logger.debug("Defekte DB-Verbindung war bereits geschlossen")
                    try:
                        close_old_connections()
                        result = func(*args, **kwargs)
                        _close_db_circuit()
                        return result
                    except (InterfaceError, OperationalError) as exc:
                        last_error = exc
                        if attempt >= retry_limit:
                            break
                        delay = min(delay_cap, initial_delay * (2 ** (attempt - 1)))
                        if dns_failure:
                            delay = max(5, delay)
                        delay += random.uniform(0, delay * 0.25)
                        logger.warning(
                            "DB ausgefallen (DNS=%s); koordinierter Reconnect %s/%s in %.2fs: %s",
                            dns_failure,
                            attempt,
                            retry_limit,
                            delay,
                            exc,
                        )
                        time.sleep(delay)

            _open_db_circuit(last_error)
            logger.error(
                "DB-Reconnect in %s nach %s Versuchen fehlgeschlagen; Circuit %ss geöffnet (%s): %s",
                func.__name__,
                retry_limit,
                settings.DB_CIRCUIT_BREAKER_SECONDS,
                type(last_error).__name__,
                last_error,
            )
            if suppress:
                return None
            raise last_error

        return wrapper

    return decorator


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe()
def db_get_config(config_id):
    return Configuration.objects.get(id=config_id)


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_mark_bot_stopped(config_id):
    Configuration.objects.filter(id=config_id).update(is_running=False)


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_create_datalog_safe(**kwargs):
    try:
        with transaction.atomic():
            DataLog.objects.create(**kwargs)
        return True
    except (IntegrityError, DataError, InvalidOperation, ValueError) as exc:
        logger.error("DataLog konnte nicht gespeichert werden: %s", exc)
        return False


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_trim_datalog(config_id, symbol, max_rows):
    """Löscht alte DataLog-Einträge in Batches, um Datenbank-Locks zu vermeiden.

    Bei großen Tabellen (20.000+ Zeilen je Symbol) kann ein einzelner
    DELETE-Befehl die Datenbank für andere Bots und Requests lange sperren.
    Diese Funktion löscht deshalb in 1000er-Schritten: Jede Iteration liest
    höchstens _DATALOG_TRIM_BATCH_SIZE betroffene IDs und löscht genau diese
    in einer eigenen, kurzen Transaktion. So bleibt jede einzelne
    DELETE-Operation klein und die Sperrdauer begrenzt.

    Args:
        config_id: ID der Trading-Konfiguration.
        symbol: Handelspaar (z. B. "BTC/USDT").
        max_rows: Maximale Anzahl zu behaltender Zeilen.
    """
    if max_rows < 1:
        # Negatives max_rows würde beim Slicing unten eine Exception auslösen.
        # Als No-op behandeln; Aufrufer nutzen ausschließlich
        # settings.MAX_DATA_LOGS_PER_SYMBOL (Minimum 1.000).
        return
    queryset = DataLog.objects.filter(configuration_id=config_id, symbol=symbol)
    cutoff_id = (
        queryset.order_by("-id").values_list("id", flat=True)[max_rows : max_rows + 1].first()
    )
    if cutoff_id is None:
        return

    # Django erlaubt kein LIMIT direkt auf .delete() (TypeError). Stattdessen
    # wird je Durchgang nur der nächste 1000er-Block an IDs gelesen und über
    # id__in gelöscht. transaction.atomic() je Block beendet die Transaktion
    # nach jeder Charge; so entsteht keine Sperre über alle Alt-Einträge.
    while True:
        batch_ids = list(
            queryset.filter(id__lte=cutoff_id)
            .order_by("id")
            .values_list("id", flat=True)[:_DATALOG_TRIM_BATCH_SIZE]
        )
        if not batch_ids:
            break
        with transaction.atomic():
            DataLog.objects.filter(id__in=batch_ids).delete()


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_create_tradinglog_safe(**kwargs):
    try:
        with transaction.atomic():
            TradingLog.objects.create(**kwargs)
        return True
    except (IntegrityError, DataError, InvalidOperation, ValueError) as exc:
        logger.error("TradingLog konnte nicht gespeichert werden: %s", exc)
        return False


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_flush_tradinglogs_safe(payloads):
    try:
        with transaction.atomic():
            for payload in payloads:
                TradingLog.objects.create(**payload)
        return True
    except (IntegrityError, DataError, InvalidOperation, ValueError) as exc:
        logger.error("Gepufferte TradingLogs konnten nicht gespeichert werden: %s", exc)
        return False


@sync_to_async(thread_sensitive=False, executor=_BOT_DB_EXECUTOR)
@db_safe(suppress=True)
def db_log_error(
    config_id,
    source,
    message,
    severity="error",
    exception_type="",
    details=None,
):
    try:
        ErrorLog.objects.create(
            configuration_id=config_id,
            severity=severity,
            source=source[:100],
            exception_type=exception_type[:200],
            message=str(message)[:4000],
            details=details or {},
        )
    except (InterfaceError, OperationalError):
        raise
    except Exception:
        logger.exception("Fehler konnte nicht in ErrorLog gespeichert werden")


@db_safe(suppress=True)
def db_restore_state(config_id):
    return list(
        TradingLog.objects.filter(configuration_id=config_id)
        .order_by("timestamp", "id")
        .values(
            "symbol",
            "action",
            "price",
            "amount",
            "fee_amount",
            "pl_nominal",
        )
    )


def _bounded(value):
    value = Decimal(value)
    return max(-_MAX_DECIMAL, min(_MAX_DECIMAL, value)).quantize(
        _EIGHT_PLACES,
        rounding=ROUND_HALF_UP,
    )


class TradingBot(threading.Thread):
    def __init__(self, config, on_exit=None):
        super().__init__(daemon=True, name=f"trading-bot-{config.id}")
        self.config_id = config.id
        self.config = config
        self.on_exit = on_exit
        self.running = True
        self.loop = None
        self.exchange = self._setup_exchange()
        self.symbols = []
        self.price_buffer = {}
        self.positions = {}
        self.pending_trading_logs = []
        self.realized_pl = Decimal(0)
        self.last_error = None
        self.last_error_at = None
        self.last_success_at = None
        self.started_at = time.time()
        self._data_log_counts = {}
        self._last_data_log_at = {}
        self._last_config_refresh = time.monotonic()
        self._persisted_errors = {}
        self._rate_limit_delay = 0
        self._last_db_warning_at = 0
        self.liquidating = False
        self._market_data_lock = None
        self.start_time = time.time() + max(0, config.countdown) * 60
        self.start_countdown_over = config.countdown <= 0
        self._sync_symbols()
        self._restore_state()

    def _setup_exchange(self):
        exchange_id = self.config.exchange.strip().lower()
        if exchange_id == "binance":
            return BinancePublicMarketData(self.config.market)
        if exchange_id == "bitmart":
            return BitMartPublicMarketData(self.config.market)
        if exchange_id == "bitunix":
            return BitunixPublicMarketData(self.config.market)

        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unbekannte Exchange: {self.config.exchange}")
        default_type = "swap" if self.config.market == "futures" else "spot"
        params = {
            "enableRateLimit": True,
            "timeout": 15_000,
            "options": {"defaultType": default_type},
        }
        api_key = os.environ.get("EXCHANGE_API_KEY")
        secret_key = os.environ.get("EXCHANGE_SECRET_KEY")
        if api_key and secret_key:
            params.update(apiKey=api_key, secret=secret_key)
        return exchange_class(params)

    def _sync_symbols(self):
        configured = [symbol.strip() for symbol in self.config.symbols.split(",") if symbol.strip()]
        # Entfernte Symbole mit offener Position bleiben bis zum Verkauf aktiv.
        self.symbols = list(dict.fromkeys(configured + list(self.positions)))
        for symbol in self.symbols:
            self.price_buffer.setdefault(symbol, [])
        for symbol in list(self.price_buffer):
            if symbol not in self.symbols:
                del self.price_buffer[symbol]

    def _restore_state(self):
        logs = db_restore_state(self.config_id) or []
        self.realized_pl = sum(
            (log["pl_nominal"] or Decimal(0) for log in logs if log["action"] == "sell"),
            Decimal(0),
        )
        for log in logs:
            if log["action"] == "buy":
                self.positions[log["symbol"]] = {
                    "price": log["price"],
                    "amount": log["amount"],
                    "buy_fee": log["fee_amount"],
                }
            elif log["action"] == "sell":
                self.positions.pop(log["symbol"], None)
        self._sync_symbols()

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.main_loop())
        except Exception as exc:
            logger.exception("Bot %s wurde unerwartet beendet", self.config_id)
            self.last_error = str(exc)
            self.last_error_at = time.time()
        finally:
            try:
                self.loop.run_until_complete(self._close_exchange())
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:
                logger.debug("Exchange/Event-Loop-Cleanup fehlgeschlagen", exc_info=True)
            self.loop.close()
            close_old_connections()
            if self.on_exit:
                self.on_exit(self.config_id, self)

    async def _close_exchange(self):
        close_method = getattr(self.exchange, "close", None)
        if not close_method:
            return
        result = close_method()
        if inspect.isawaitable(result):
            await result

    def stop(self):
        self.running = False

    async def _persist_error(self, key, source, error, severity="error", details=None):
        now = time.time()
        text = str(error)
        previous_time, previous_text = self._persisted_errors.get(key, (0, None))
        # Identische Dauerfehler (z. B. eine regional blockierte Exchange)
        # höchstens alle 15 Minuten persistieren, damit die DB nicht vollläuft.
        if text != previous_text or now - previous_time >= 15 * 60:
            await db_log_error(
                self.config_id,
                source,
                text,
                severity=severity,
                exception_type=type(error).__name__ if isinstance(error, BaseException) else "",
                details=details,
            )
            self._persisted_errors[key] = (now, text)

    async def flush_pending_trading_logs(self):
        if not self.pending_trading_logs or _db_circuit_remaining():
            return
        batch = list(self.pending_trading_logs[:100])
        if await db_flush_tradinglogs_safe(batch):
            del self.pending_trading_logs[: len(batch)]
            logger.info(
                "Bot %s hat %s gepufferte TradingLogs nach DB-Recovery gespeichert",
                self.config_id,
                len(batch),
            )

    async def _sleep(self, seconds):
        remaining = max(0, seconds)
        while remaining > 0 and self.running:
            step = min(1, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def main_loop(self):
        while self.running:
            try:
                if (
                    time.monotonic() - self._last_config_refresh
                    >= settings.BOT_CONFIG_REFRESH_SECONDS
                ):
                    self._last_config_refresh = time.monotonic()
                    try:
                        self.config = await db_get_config(self.config_id)
                        self._sync_symbols()
                    except (OperationalError, InterfaceError):
                        # Mit der letzten validierten Konfiguration weiterlaufen.
                        # Preisstream und Strategie hängen nicht vom Frontend ab.
                        pass
                await self.flush_pending_trading_logs()
                try:
                    tickers = await self.fetch_tickers(self.symbols)
                except Exception as exc:
                    text = str(exc)
                    if isinstance(exc, SymbolValidationError):
                        self.last_error = f"Konfiguration ungültig: {text}"
                        self.last_error_at = time.time()
                        await self._persist_error(
                            "invalid_symbols",
                            "trading_bot.validate_symbols",
                            exc,
                            severity="critical",
                            details={
                                "exchange": self.config.exchange,
                                "market": self.config.market,
                                "invalid_symbols": exc.symbols,
                            },
                        )
                        await db_mark_bot_stopped(self.config_id)
                        self.running = False
                        continue
                    if isinstance(exc, RateLimitError):
                        if exc.retry_at and exc.retry_at > time.time():
                            # Den von der Börse genannten Ban vollständig abwarten.
                            self._rate_limit_delay = max(1, exc.retry_at - time.time() + 1)
                        else:
                            self._rate_limit_delay = min(
                                15 * 60,
                                max(30, self._rate_limit_delay * 2),
                            )
                    elif isinstance(exc, MarketDataConnectionError):
                        self._rate_limit_delay = min(
                            5 * 60,
                            max(5, self._rate_limit_delay * 2),
                        )
                    else:
                        self._rate_limit_delay = 2
                    self.last_error = f"Marktdaten: {text}"
                    self.last_error_at = time.time()
                    logger.error(
                        "Marktdatenfehler für Bot %s; neuer Versuch in %ss: %s",
                        self.config_id,
                        self._rate_limit_delay,
                        text,
                    )
                    await self._persist_error(
                        "market_data",
                        "trading_bot.fetch_tickers",
                        exc,
                        severity="warning" if isinstance(exc, RateLimitError) else "error",
                        details={
                            "exchange": self.config.exchange,
                            "market": self.config.market,
                            "symbols": self.symbols,
                            "retry_delay_seconds": round(self._rate_limit_delay, 2),
                            "retry_at": exc.retry_at if isinstance(exc, RateLimitError) else None,
                            "reconnect_attempts": (
                                exc.attempts if isinstance(exc, WebSocketReconnectError) else None
                            ),
                            "last_connection_error": (
                                exc.last_error if isinstance(exc, WebSocketReconnectError) else None
                            ),
                        },
                    )
                    await self._sleep(self._rate_limit_delay)
                    continue

                any_success = False
                for symbol in self.symbols:
                    ticker = tickers.get(symbol)
                    try:
                        if not ticker:
                            raise ValueError(f"Exchange lieferte keinen Ticker für {symbol}")
                        self.store_price(symbol, ticker)
                        await self.calculate_and_store(symbol)
                        any_success = True
                    except Exception as exc:
                        logger.error("%s Verarbeitungsfehler: %s", symbol, exc)
                        self.last_error = f"{symbol}: {exc}"
                        self.last_error_at = time.time()
                        await self._persist_error(
                            f"symbol:{symbol}",
                            "trading_bot.process_symbol",
                            exc,
                            details={
                                "exchange": self.config.exchange,
                                "market": self.config.market,
                                "symbol": symbol,
                            },
                        )
                if any_success:
                    self.last_success_at = time.time()
                    if not self.pending_trading_logs:
                        self.last_error = None
                        self.last_error_at = None
                    self._rate_limit_delay = 0
                await self._sleep(max(1, self.config.time_interval))
            except ObjectDoesNotExist:
                logger.info("Konfiguration %s wurde gelöscht; Bot stoppt", self.config_id)
                self.running = False
            except Exception as exc:
                self.last_error = f"main_loop: {exc}"
                self.last_error_at = time.time()
                if isinstance(exc, (OperationalError, InterfaceError)):
                    # Wenn Postgres/DNS selbst ausgefallen ist, würde ein
                    # ErrorLog-Schreibversuch nur einen zweiten Reconnect-Zyklus
                    # auslösen. Pro Bot höchstens alle fünf Minuten warnen.
                    if time.time() - self._last_db_warning_at >= 300:
                        logger.warning(
                            "Bot %s wartet auf Datenbank-Recovery: %s",
                            self.config_id,
                            exc,
                        )
                        self._last_db_warning_at = time.time()
                    await self._sleep(30)
                    continue
                logger.exception("Main-Loop-Fehler für Bot %s", self.config_id)
                await self._persist_error("main_loop", "trading_bot.main_loop", exc)
                await self._sleep(2)

    async def fetch_tickers(self, symbols):
        # Kill-Switch und Hauptzyklus dürfen dasselbe Exchange-/WebSocket-
        # Objekt niemals gleichzeitig lesen.
        if self._market_data_lock is None:
            self._market_data_lock = asyncio.Lock()
        async with self._market_data_lock:
            fetch_many_async = getattr(self.exchange, "fetch_tickers_async", None)
            if fetch_many_async:
                return await asyncio.wait_for(fetch_many_async(symbols), timeout=90)

            fetch_many = getattr(self.exchange, "fetch_tickers", None)
            if fetch_many:
                future = self.loop.run_in_executor(None, fetch_many, symbols)
                return await asyncio.wait_for(future, timeout=25)

            tickers = {}
            for symbol in symbols:
                future = self.loop.run_in_executor(None, self.exchange.fetch_ticker, symbol)
                tickers[symbol] = await asyncio.wait_for(future, timeout=20)
            return tickers

    def store_price(self, symbol, ticker):
        raw_price = ticker.get("last") or ticker.get("close")
        if raw_price is None:
            raise ValueError("Exchange lieferte keinen letzten Preis")
        price = Decimal(str(raw_price))
        if not price.is_finite() or price <= 0:
            raise ValueError(f"Ungültiger Preis: {raw_price}")
        buffer = self.price_buffer.setdefault(symbol, [])
        buffer.append(price)
        del buffer[:-10]

    async def calculate_and_store(self, symbol):
        prices = self.price_buffer[symbol]
        if len(prices) < 3:
            return
        current, previous, older = prices[-1], prices[-2], prices[-3]
        current_da = current - previous
        nda = current_da / previous * 100 if previous else Decimal(0)
        previous_da = previous - older
        previous_nda = previous_da / previous * 100 if previous else Decimal(0)
        dva = nda - previous_nda
        deltadelta = (nda + previous_nda) / 2
        acceleration = dva / previous_nda if previous_nda else Decimal(0)
        max_price = max(prices)
        min_price = min(prices)
        mvd = min_price / max_price if max_price else Decimal(0)

        now = time.monotonic()
        should_persist = (
            now - self._last_data_log_at.get(symbol, 0) >= settings.DATA_LOG_WRITE_INTERVAL_SECONDS
        )
        if should_persist:
            saved = await db_create_datalog_safe(
                configuration_id=self.config_id,
                symbol=symbol,
                price=_bounded(current),
                max_price=_bounded(max_price),
                min_price=_bounded(min_price),
                current_da=_bounded(current_da),
                nda=_bounded(nda),
                prev_da=_bounded(previous_da),
                prev_nda=_bounded(previous_nda),
                dva=_bounded(dva),
                deltadelta=_bounded(deltadelta),
                div_DVA_prev_NDA=_bounded(acceleration),
                mvd=_bounded(mvd),
            )
            if saved:
                self._last_data_log_at[symbol] = now
                count = self._data_log_counts.get(symbol, 0) + 1
                self._data_log_counts[symbol] = count
                if count % settings.DATA_LOG_CLEANUP_EVERY == 0:
                    await db_trim_datalog(
                        self.config_id,
                        symbol,
                        settings.MAX_DATA_LOGS_PER_SYMBOL,
                    )
        await self.check_trading(symbol, current, nda, deltadelta, acceleration)

    def _global_loss_limit_reached(self):
        threshold = Decimal(str(self.config.sales_stop_threshold or 0))
        if threshold <= 0:
            return False
        limit = self.config.start_capital * threshold / Decimal(100)
        return self.realized_pl <= -limit

    def _available_capital(self):
        allocated = sum(
            position["amount"] * position["price"] + position["buy_fee"]
            for position in self.positions.values()
        )
        return self.config.start_capital + self.realized_pl - allocated

    async def check_trading(self, symbol, price, nda, deltadelta, acceleration):
        if self.liquidating:
            return
        if not self.start_countdown_over:
            if time.time() >= self.start_time:
                self.start_countdown_over = True
            return

        if symbol in self.positions:
            entry_price = self.positions[symbol]["price"]
            profit_percent = (price - entry_price) / entry_price * 100
            if (
                profit_percent >= self.config.take_profit
                or profit_percent <= -self.config.stop_loss
                or self._global_loss_limit_reached()
            ):
                await self.execute_trade(symbol, "sell")
            return

        buy_fee = self.config.trade_amount * self.config.fee / Decimal(100)
        if self._global_loss_limit_reached() or self._available_capital() < (
            self.config.trade_amount + buy_fee
        ):
            return
        if (
            nda > self.config.nda_threshold_buy
            and deltadelta > self.config.deltadelta_threshold_buy
            and acceleration > self.config.div_DVA_prev_NDA_threshold_buy
        ):
            await self.execute_trade(symbol, "buy")

    async def execute_trade(self, symbol, side):
        if symbol not in self.price_buffer or not self.price_buffer[symbol]:
            raise ValueError(f"Kein aktueller Preis für {symbol}")
        price = self.price_buffer[symbol][-1]
        pl_nominal = Decimal(0)

        if side == "buy":
            if symbol in self.positions:
                raise ValueError(f"Für {symbol} ist bereits eine Position offen")
            amount = (self.config.trade_amount / price).quantize(
                _EIGHT_PLACES,
                rounding=ROUND_HALF_UP,
            )
            cost = amount * price
            fee = cost * self.config.fee / Decimal(100)
            if self._available_capital() < cost + fee:
                raise ValueError("Nicht genügend simuliertes Kapital")
            self.positions[symbol] = {
                "price": price,
                "amount": amount,
                "buy_fee": fee,
            }
        elif side == "sell":
            if symbol not in self.positions:
                raise ValueError(f"Keine offene Position für {symbol}")
            position = self.positions.pop(symbol)
            amount = position["amount"]
            proceeds = amount * price
            fee = proceeds * self.config.fee / Decimal(100)
            invested = amount * position["price"]
            pl_nominal = proceeds - invested - position["buy_fee"] - fee
            self.realized_pl += pl_nominal
        else:
            raise ValueError(f"Unbekannte Orderseite: {side}")

        return_basis = (
            amount * position["price"] + position["buy_fee"] if side == "sell" else amount * price
        )
        # `current_capital` ist der verfügbare Cash-Kontostand. Bei einem Buy
        # muss der gebundene Positionswert inklusive Kaufgebühr sofort sinken.
        current_capital = self._available_capital()
        payload = {
            "configuration_id": self.config_id,
            "symbol": symbol,
            "action": side,
            "price": _bounded(price),
            "amount": _bounded(amount),
            "fee_amount": _bounded(fee),
            "pl_nominal": _bounded(pl_nominal),
            "pl_relative": _bounded(pl_nominal / return_basis * 100 if return_basis else 0),
            "total_pl": _bounded(self.realized_pl),
            "current_capital": _bounded(current_capital),
            "tank": _bounded(self.realized_pl),
            "order_id": f"paper_{side}_{time.time_ns()}",
        }
        saved = await db_create_tradinglog_safe(**payload)
        if not saved:
            if len(self.pending_trading_logs) >= 1000:
                # Ohne dauerhaftes Journal wäre weiteres Handeln nicht mehr
                # rekonstruierbar. Erst bei vollem Puffer den letzten Trade
                # sicher zurückrollen und weitere Orders blockieren.
                if side == "sell":
                    self.realized_pl -= pl_nominal
                    self.positions[symbol] = position
                else:
                    self.positions.pop(symbol, None)
                raise RuntimeError(
                    "DB-Trade-Puffer voll; weiterer Handel sicherheitshalber blockiert"
                )
            self.pending_trading_logs.append(payload)
            self.last_error = (
                f"DB offline: {len(self.pending_trading_logs)} Trade-Logs gepuffert; "
                "Marktdaten und Paper-Handel laufen weiter"
            )
            self.last_error_at = time.time()
            if len(self.pending_trading_logs) == 1 or len(self.pending_trading_logs) % 100 == 0:
                logger.warning(
                    "Bot %s puffert %s TradingLogs im RAM",
                    self.config_id,
                    len(self.pending_trading_logs),
                )
        if side == "sell" and self.config.countdown_reset_indicators:
            self.price_buffer[symbol] = []

    async def liquidate_all_positions(self):
        """Schließt alle Paper-Positionen mit frisch abgerufenen Marktpreisen."""
        if not self.positions:
            return {"sold": [], "errors": {}}
        self.liquidating = True
        sold = []
        errors = {}
        try:
            symbols = list(self.positions)
            tickers = await self.fetch_tickers(symbols)
            for symbol in symbols:
                try:
                    ticker = tickers.get(symbol)
                    if not ticker:
                        raise ValueError(f"Kein aktueller Marktpreis für {symbol}")
                    self.store_price(symbol, ticker)
                    await self.execute_trade(symbol, "sell")
                    sold.append(symbol)
                except Exception as exc:
                    # Das errors-Dict geht an die API; Details gehören nur ins Log.
                    logger.exception("Kill-Switch-Verkauf für %s/%s fehlgeschlagen", self.config_id, symbol)
                    errors[symbol] = "Verkauf fehlgeschlagen. Siehe Fehler-Log für Details."
                    await self._persist_error(
                        f"kill-switch:{symbol}",
                        "trading_bot.kill_switch",
                        exc,
                        severity="critical",
                        details={"symbol": symbol, "exchange": self.config.exchange},
                    )
            return {"sold": sold, "errors": errors}
        finally:
            self.liquidating = False

    async def manual_sell(self, symbol):
        if symbol not in self.positions:
            raise ValueError(f"Keine offene Position für {symbol}")
        await self.execute_trade(symbol, "sell")


class TradingBotManager:
    def __init__(self):
        self.bots = {}
        self._lock = threading.RLock()

    def _forget(self, config_id, bot):
        with self._lock:
            if self.bots.get(config_id) is bot:
                self.bots.pop(config_id, None)

    def _is_running_unlocked(self, config_id):
        """Interne Prüfung ob ein Bot läuft – ohne Lock-Acquisition.

        Nur von Methoden aufrufen, die bereits ``self._lock`` halten.
        Vermeidet verschachtelte Lock-Acquisition (Deadlock bei ``Lock``,
        Code-Smell bei ``RLock``) und räumt beendete Threads atomar ab.
        """
        bot = self.bots.get(config_id)
        if bot and bot.is_alive():
            return True
        if bot:
            # Bot-Referenz entfernen wenn der Thread bereits beendet ist.
            self.bots.pop(config_id, None)
        return False

    def is_running(self, config_id):
        """Öffentliche, thread-sichere Prüfung ob ein Bot läuft."""
        with self._lock:
            return self._is_running_unlocked(config_id)

    def start_bot(self, config):
        """Startet einen TradingBot für die gegebene Konfiguration.

        Thread-sicher: Prüft unter dem Lock, ob bereits ein Bot läuft,
        und startet sonst genau einen neuen Thread. Die Prüfung nutzt
        ``_is_running_unlocked``, damit der Lock nicht erneut erworben wird.
        """
        with self._lock:
            if self._is_running_unlocked(config.id):
                return self.bots[config.id]
            bot = TradingBot(config, on_exit=self._forget)
            self.bots[config.id] = bot
            bot.start()
            return bot

    def stop_bot(self, config):
        """Stoppt den TradingBot für die gegebene Konfiguration.

        Thread-sicher: Tote Referenzen werden unter dem Lock verworfen;
        ein lebender Bot wird außerhalb des Locks gestoppt, damit Join
        den Manager nicht blockiert.
        """
        config_id = config.id if hasattr(config, "id") else int(config)
        bot = None
        with self._lock:
            if self._is_running_unlocked(config_id):
                bot = self.bots.get(config_id)
        if bot:
            bot.stop()
            threading.Thread(
                target=bot.join,
                kwargs={"timeout": 30},
                daemon=True,
                name=f"stop-bot-{config_id}",
            ).start()

    def restart_bot(self, config, before_start=None):
        """Startet nach sauberem Thread-Ende neu, optional mit atomarer Vorbereitung."""
        with self._lock:
            old_bot = self.bots.get(config.id)

        def prepare_and_start():
            close_old_connections()
            try:
                if before_start:
                    before_start()
                config.refresh_from_db()
                if config.is_running:
                    self.start_bot(config)
            except Exception as exc:
                logger.exception("Neustart für Konfiguration %s fehlgeschlagen", config.id)
                ErrorLog.objects.create(
                    configuration_id=config.id,
                    severity="critical",
                    source="trading_bot.restart",
                    exception_type=type(exc).__name__,
                    message=str(exc)[:4000],
                    details={"exchange": config.exchange, "market": config.market},
                )
            finally:
                close_old_connections()

        if not old_bot or not old_bot.is_alive():
            prepare_and_start()
            return

        old_bot.stop()

        def wait_and_restart():
            old_bot.join(timeout=30)
            if old_bot.is_alive():
                logger.error("Bot %s konnte für Neustart nicht beendet werden", config.id)
                return
            prepare_and_start()

        threading.Thread(
            target=wait_and_restart,
            daemon=True,
            name=f"restart-bot-{config.id}",
        ).start()

    def manual_sell(self, config_id, symbol):
        with self._lock:
            bot = self.bots.get(config_id)
        if not bot or not bot.is_alive() or bot.loop is None:
            raise ValueError("Bot läuft nicht; manueller Verkauf ist nicht möglich")
        future = asyncio.run_coroutine_threadsafe(bot.manual_sell(symbol), bot.loop)
        return future.result(timeout=10)

    def open_symbols(self, config_id):
        with self._lock:
            bot = self.bots.get(config_id)
            return sorted(bot.positions) if bot and bot.is_alive() else []

    def kill_switch(self, config_id):
        with self._lock:
            bot = self.bots.get(config_id)
        if not bot or not bot.is_alive() or bot.loop is None:
            raise ValueError("Bot läuft nicht; der Kill-Switch kann keine Preise abrufen")
        future = asyncio.run_coroutine_threadsafe(bot.liquidate_all_positions(), bot.loop)
        return future.result(timeout=120)

    def status(self, config_id):
        with self._lock:
            bot = self.bots.get(config_id)
        if not bot or not bot.is_alive():
            return {
                "running": False,
                "config_id": config_id,
                "started_at": None,
                "last_error": None,
                "last_error_at": None,
                "last_success_at": None,
                "pending_trading_logs": 0,
            }
        return {
            "running": True,
            "config_id": config_id,
            "started_at": bot.started_at,
            "last_error": bot.last_error,
            "last_error_at": bot.last_error_at,
            "last_success_at": bot.last_success_at,
            "pending_trading_logs": len(bot.pending_trading_logs),
        }


bot_manager = TradingBotManager()
