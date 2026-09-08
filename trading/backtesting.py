import threading
from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal, getcontext, localcontext

_EIGHT_PLACES = Decimal("0.00000001")
# Deckt die üblichen 5.000 Preispunkte ab, ohne unbegrenzt zu wachsen.
_INDICATOR_CACHE_MAXSIZE = 8192
_indicator_cache = OrderedDict()
_indicator_cache_lock = threading.Lock()


def _decimal(value, default="0"):
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


class Backtesting:
    @staticmethod
    def calculate_indicators(prices, idx):
        """Berechnet und memoisiert (acceleration, deltadelta, current_nda).

        Preislisten bleiben während eines Backtests unverändert. Direkte Aufrufer
        müssen den Cache nach ihrem Lauf mit ``clear_indicator_cache()`` leeren.
        Der prozesslokale LRU hält höchstens 8.192 Einträge; andere Prozesse
        teilen ihn nicht. Decimal-Kontext und ersetzte Preispunkte werden geprüft.
        """
        cache_key = (id(prices), idx)
        context = getcontext()
        context_key = (
            context.prec,
            context.rounding,
            context.Emin,
            context.Emax,
            context.clamp,
            tuple(context.traps.values()),
        )
        # Auch Berechnung und Einfügen sind geschützt: Ein gleichzeitiges clear
        # darf nicht von einer zuvor gestarteten Berechnung rückgängig werden.
        with _indicator_cache_lock:
            values = (prices[idx], prices[idx - 1], prices[idx - 2])
            entry = _indicator_cache.get(cache_key)
            if entry is not None:
                source, cached_values, cached_context, result, signals = entry
                if (
                    source is prices
                    and cached_context == context_key
                    and cached_values[0] is values[0]
                    and cached_values[1] is values[1]
                    and cached_values[2] is values[2]
                ):
                    _indicator_cache.move_to_end(cache_key)
                    for signal in signals:
                        context.flags[signal] = True
                    return result

            # Flags gehören zur Decimal-API. Nur die von dieser Berechnung
            # gesetzten Flags wiedergeben, nicht fremde, zuvor gesetzte Flags.
            with localcontext(context) as calculation_context:
                calculation_context.clear_flags()
                try:
                    result = Backtesting._calculate_indicators(prices, idx)
                finally:
                    signals = tuple(
                        signal for signal, raised in calculation_context.flags.items() if raised
                    )
                    for signal in signals:
                        context.flags[signal] = True

            # Starke Referenz verhindert id-Wiederverwendung für andere Listen.
            # LRU-Eviction und clear geben Liste und Ergebnis gemeinsam frei.
            _indicator_cache[cache_key] = (prices, values, context_key, result, signals)
            _indicator_cache.move_to_end(cache_key)
            if len(_indicator_cache) > _INDICATOR_CACHE_MAXSIZE:
                _indicator_cache.popitem(last=False)
            return result

    @classmethod
    def clear_indicator_cache(cls):
        """Gibt alle Cache-Einträge frei; auch bei Backtest-Abbruch aufrufen."""
        with _indicator_cache_lock:
            _indicator_cache.clear()

    @staticmethod
    def _calculate_indicators(prices, idx):
        """Unveränderte Decimal-Formeln für einen Cache-Miss."""
        current_price = _decimal(prices[idx])
        previous_price = _decimal(prices[idx - 1])
        older_price = _decimal(prices[idx - 2])

        current_da = current_price - previous_price
        current_nda = (
            (current_da / previous_price * Decimal(100)).quantize(
                _EIGHT_PLACES,
                rounding=ROUND_HALF_UP,
            )
            if previous_price
            else Decimal(0)
        )
        previous_da = previous_price - older_price
        previous_nda = (
            (previous_da / previous_price * Decimal(100)).quantize(
                _EIGHT_PLACES,
                rounding=ROUND_HALF_UP,
            )
            if previous_price
            else Decimal(0)
        )
        dva = (current_nda - previous_nda).quantize(
            _EIGHT_PLACES,
            rounding=ROUND_HALF_UP,
        )
        acceleration = (
            (dva / previous_nda).quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP)
            if previous_nda
            else Decimal(0)
        )
        deltadelta = ((current_nda + previous_nda) / Decimal(2)).quantize(
            _EIGHT_PLACES,
            rounding=ROUND_HALF_UP,
        )
        return acceleration, deltadelta, current_nda

    @staticmethod
    def compute_indicator_series(prices):
        indices = []
        acceleration_series = []
        deltadelta_series = []
        nda_series = []
        for index in range(2, len(prices)):
            acceleration, deltadelta, nda = Backtesting.calculate_indicators(
                prices,
                index,
            )
            indices.append(index)
            acceleration_series.append(acceleration)
            deltadelta_series.append(deltadelta)
            nda_series.append(nda)
        return indices, acceleration_series, deltadelta_series, nda_series

    @staticmethod
    def simulate_trading_detailed(
        prices,
        acc_threshold,
        nda_threshold,
        deltadelta_threshold,
        simulation_params,
        indicator_rows=None,
        timestamps=None,
        include_details=True,
    ):
        """Simuliert eine Position und optional einen vollständigen Report.

        Die Equity-Kurve bewertet eine offene Position zu jedem Messpunkt zum
        aktuellen Marktpreis (Cash + Position), statt nur realisierte Trades zu
        verbinden. Rasterkandidaten können mit ``include_details=False`` ohne
        große Trade-/Kurvenlisten bewertet werden.
        """
        # Indikatoren vor der Decimal-Listenkopie berechnen, damit wiederholte
        # Simulationen derselben Eingabereihe denselben Cache-Key verwenden.
        if not isinstance(prices, (list, tuple)):
            prices = list(prices)
        if indicator_rows is None:
            indicator_rows = [None, None] + [
                Backtesting.calculate_indicators(prices, index) for index in range(2, len(prices))
            ]
        prices = [_decimal(price) for price in prices]
        timestamps = list(timestamps or [])
        if len(timestamps) != len(prices):
            timestamps = [None] * len(prices)
        capital = _decimal(simulation_params.get("start_capital"), "1000")
        start_capital = capital
        trade_amount = _decimal(simulation_params.get("trade_amount"), "100")
        take_profit = _decimal(simulation_params.get("take_profit"), "5")
        stop_loss = _decimal(simulation_params.get("stop_loss"), "100")
        fee_percentage = _decimal(simulation_params.get("fee_percentage"), "0.1")
        acc_threshold = _decimal(acc_threshold)
        nda_threshold = _decimal(nda_threshold)
        deltadelta_threshold = _decimal(deltadelta_threshold)

        position = None
        trades = [] if include_details else None
        equity_values = [capital for _ in prices[:2]] if include_details else []

        for index in range(2, len(prices)):
            acceleration, deltadelta, current_nda = indicator_rows[index]
            current_price = prices[index]
            if current_price <= 0:
                if include_details:
                    # Ein ungültiger Tick darf eine offene Position in der
                    # Equity-Kurve nicht fälschlich auf null bewerten.
                    equity_values.append(equity_values[-1] if equity_values else capital)
                continue

            if position is None:
                should_buy = (
                    acceleration > acc_threshold
                    and current_nda > nda_threshold
                    and deltadelta > deltadelta_threshold
                )
                buy_fee = trade_amount * fee_percentage / Decimal(100)
                if should_buy and capital >= trade_amount + buy_fee:
                    amount = (trade_amount / current_price).quantize(
                        _EIGHT_PLACES,
                        rounding=ROUND_HALF_UP,
                    )
                    capital_before = capital
                    capital -= trade_amount + buy_fee
                    position = {
                        "price": current_price,
                        "amount": amount,
                        "buy_fee": buy_fee,
                        "index": index,
                        "timestamp": timestamps[index],
                    }
                    if trades is not None:
                        trades.append(
                            {
                                "type": "buy",
                                "price": current_price,
                                "index": index,
                                "timestamp": Backtesting._timestamp_text(timestamps[index]),
                                "capital_before": capital_before,
                                "capital_after": capital,
                                "fee": buy_fee,
                            }
                        )

            if position is not None:
                price_change = (
                    (current_price - position["price"]) / position["price"] * Decimal(100)
                )
                if price_change >= take_profit or price_change <= -stop_loss:
                    capital = Backtesting._close_position(
                        capital,
                        position,
                        current_price,
                        index,
                        timestamps[index],
                        fee_percentage,
                        trades,
                        price_change,
                    )
                    position = None
            if include_details:
                equity_values.append(
                    capital + (position["amount"] * current_price if position else Decimal(0))
                )

        if position is not None and prices:
            # Falls die Reihe mit ungültigen Ticks endet, wird am letzten
            # tatsächlich beobachteten positiven Preis geschlossen.
            final_index = next(
                index for index in range(len(prices) - 1, -1, -1) if prices[index] > 0
            )
            final_price = prices[final_index]
            price_change = (final_price - position["price"]) / position["price"] * Decimal(100)
            capital = Backtesting._close_position(
                capital,
                position,
                final_price,
                final_index,
                timestamps[final_index],
                fee_percentage,
                trades,
                price_change,
            )
            if include_details and equity_values:
                equity_values[final_index:] = [capital] * (len(equity_values) - final_index)

        if not include_details:
            return capital, {"final_capital": capital}

        sells = [trade for trade in trades if trade["type"] == "sell"]
        profitable = sum(1 for trade in sells if trade["profit_nominal"] > 0)
        gross_profit = sum(
            (trade["profit_nominal"] for trade in sells if trade["profit_nominal"] > 0),
            Decimal(0),
        )
        gross_loss = abs(
            sum(
                (trade["profit_nominal"] for trade in sells if trade["profit_nominal"] < 0),
                Decimal(0),
            )
        )
        total_fees = sum((trade["fee"] for trade in trades), Decimal(0))
        duration_points = [trade["duration_points"] for trade in sells]
        duration_seconds = [
            trade["duration_seconds"]
            for trade in sells
            if trade.get("duration_seconds") is not None
        ]
        equity_curve = Backtesting._equity_curve(equity_values, timestamps)
        peak = None
        max_drawdown = Decimal(0)
        for equity in equity_values:
            peak = equity if peak is None else max(peak, equity)
            if peak:
                drawdown = (peak - equity) / peak * Decimal(100)
                max_drawdown = max(max_drawdown, drawdown)
        report = {
            "final_capital": capital,
            "net_profit": capital - start_capital,
            "return_percentage": (
                (capital - start_capital) / start_capital * Decimal(100)
                if start_capital
                else Decimal(0)
            ),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "total_fees": total_fees,
            "average_profit": (
                sum((trade["profit_nominal"] for trade in sells), Decimal(0)) / len(sells)
                if sells
                else Decimal(0)
            ),
            "profit_factor": gross_profit / gross_loss if gross_loss else None,
            "max_drawdown_percentage": max_drawdown,
            "average_trade_duration_points": (
                sum(duration_points) / len(duration_points) if duration_points else 0
            ),
            "average_trade_duration_seconds": (
                sum(duration_seconds) / len(duration_seconds) if duration_seconds else None
            ),
            "trades": trades,
            "equity_curve": equity_curve,
            "num_trades": len(trades),
            "num_buys": sum(1 for trade in trades if trade["type"] == "buy"),
            "num_sells": len(sells),
            "profitable_trades": profitable,
            "unprofitable_trades": len(sells) - profitable,
            "win_rate": (Decimal(profitable) / Decimal(len(sells)) * 100) if sells else 0,
        }
        return capital, report

    @staticmethod
    def _timestamp_text(value):
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @staticmethod
    def _duration_seconds(start, end):
        if start is None or end is None:
            return None
        try:
            if isinstance(start, str):
                from datetime import datetime

                start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if isinstance(end, str):
                from datetime import datetime

                end = datetime.fromisoformat(end.replace("Z", "+00:00"))
            return max(0.0, (end - start).total_seconds())
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _equity_curve(values, timestamps, maximum_points=1000):
        if not values:
            return []
        if len(values) <= maximum_points:
            indices = range(len(values))
        else:
            step = (len(values) - 1) / (maximum_points - 1)
            indices = sorted({round(index * step) for index in range(maximum_points)})
        return [
            {
                "index": index,
                "timestamp": Backtesting._timestamp_text(timestamps[index]),
                "equity": values[index],
            }
            for index in indices
        ]

    @staticmethod
    def _close_position(
        capital,
        position,
        price,
        index,
        timestamp,
        fee_percentage,
        trades,
        price_change,
    ):
        gross_proceeds = position["amount"] * price
        sell_fee = gross_proceeds * fee_percentage / Decimal(100)
        net_proceeds = gross_proceeds - sell_fee
        profit = net_proceeds - (position["amount"] * position["price"]) - position["buy_fee"]
        capital_before = capital
        capital += net_proceeds
        if trades is not None:
            trades.append(
                {
                    "type": "sell",
                    "price": price,
                    "index": index,
                    "timestamp": Backtesting._timestamp_text(timestamp),
                    "entry_index": position["index"],
                    "entry_timestamp": Backtesting._timestamp_text(position["timestamp"]),
                    "duration_points": index - position["index"],
                    "duration_seconds": Backtesting._duration_seconds(
                        position["timestamp"], timestamp
                    ),
                    "capital_before": capital_before,
                    "capital_after": capital,
                    "fee": sell_fee,
                    "profit_percentage": price_change,
                    "profit_nominal": profit,
                }
            )
        return capital

    @staticmethod
    def create_plot_for_symbol(historical_prices, report):
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        prices = [_decimal(price) for price in historical_prices]
        prices_float = [float(price) for price in prices]
        indices, acceleration, deltadelta, nda = Backtesting.compute_indicator_series(prices)

        figure = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            row_heights=[0.6, 0.4],
        )
        figure.add_trace(
            go.Scatter(x=list(range(len(prices))), y=prices_float, name="Preis"),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=indices,
                y=[float(value) for value in acceleration],
                name="Beschleunigung (DVA/prev NDA)",
            ),
            row=2,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=indices,
                y=[float(value) for value in deltadelta],
                name="DeltaDelta (Momentum)",
            ),
            row=2,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=indices,
                y=[float(value) for value in nda],
                name="NDA (% Preisänderung)",
            ),
            row=2,
            col=1,
        )

        for trade_type, color, symbol in (
            ("buy", "green", "triangle-up"),
            ("sell", "red", "triangle-down"),
        ):
            trades = [trade for trade in report.get("trades", []) if trade["type"] == trade_type]
            figure.add_trace(
                go.Scatter(
                    x=[trade["index"] for trade in trades],
                    y=[float(trade["price"]) for trade in trades],
                    mode="markers",
                    marker={"symbol": symbol, "size": 12, "color": color},
                    name=f"{trade_type.title()}-Signale",
                ),
                row=1,
                col=1,
            )

        figure.update_layout(
            title="Preis- und Indikatorendiagramm mit Trades",
            height=600,
            autosize=True,
        )
        return figure
