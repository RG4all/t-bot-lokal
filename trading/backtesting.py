from decimal import ROUND_HALF_UP, Decimal

from .strategy import (
    LONG,
    SHORT,
    entry_signal,
    gross_pnl,
    is_liquidated,
    normalize_direction,
    position_size,
    price_change_percent,
)

_EIGHT_PLACES = Decimal("0.00000001")


def _decimal(value, default="0"):
    if value is None:
        return Decimal(default)
    return value if isinstance(value, Decimal) else Decimal(str(value))


class Backtesting:
    @staticmethod
    def calculate_indicators(prices, idx):
        """Berechnet die drei Strategieindikatoren am angegebenen Index."""
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
        # Hebel und Handelsrichtung. Ohne Angabe verhält sich die Simulation
        # exakt wie bisher: Long ohne Hebel.
        leverage = _decimal(simulation_params.get("leverage"), "1")
        if leverage <= 0:
            leverage = Decimal(1)
        direction = normalize_direction(simulation_params.get("direction") or LONG)
        acc_threshold = _decimal(acc_threshold)
        nda_threshold = _decimal(nda_threshold)
        deltadelta_threshold = _decimal(deltadelta_threshold)
        if indicator_rows is None:
            indicator_rows = [None, None] + [
                Backtesting.calculate_indicators(prices, index) for index in range(2, len(prices))
            ]

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
                signal = entry_signal(
                    acceleration,
                    deltadelta,
                    current_nda,
                    (acc_threshold, nda_threshold, deltadelta_threshold),
                    direction,
                )
                # Gebühren fallen auf das Nominalvolumen (Margin × Hebel) an,
                # gebunden wird nur die Margin.
                buy_fee = trade_amount * leverage * fee_percentage / Decimal(100)
                if signal and capital >= trade_amount + buy_fee:
                    amount = position_size(trade_amount, current_price, leverage)
                    capital_before = capital
                    capital -= trade_amount + buy_fee
                    position = {
                        "price": current_price,
                        "amount": amount,
                        "buy_fee": buy_fee,
                        "index": index,
                        "timestamp": timestamps[index],
                        "direction": signal,
                        "leverage": leverage,
                        "margin": trade_amount,
                    }
                    if trades is not None:
                        trades.append(
                            {
                                "type": "buy",
                                "direction": signal,
                                "leverage": leverage,
                                "margin": trade_amount,
                                "price": current_price,
                                "index": index,
                                "timestamp": Backtesting._timestamp_text(timestamps[index]),
                                "capital_before": capital_before,
                                "capital_after": capital,
                                "fee": buy_fee,
                            }
                        )

            if position is not None:
                # Kursbewegung aus Sicht der Position: Für Shorts zählt ein
                # fallender Kurs als Gewinn.
                price_change = price_change_percent(
                    position["price"],
                    current_price,
                    position["direction"],
                )
                if (
                    price_change >= take_profit
                    or price_change <= -stop_loss
                    or is_liquidated(price_change, position["leverage"])
                ):
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
                # Mark-to-Market: freies Kapital plus Margin plus schwebendes
                # Ergebnis der offenen Position (richtungs- und hebelrichtig).
                open_value = Decimal(0)
                if position:
                    open_value = position["margin"] + gross_pnl(
                        position["price"],
                        current_price,
                        position["amount"],
                        position["direction"],
                    )
                equity_values.append(capital + open_value)

        if position is not None and prices:
            # Falls die Reihe mit ungültigen Ticks endet, wird am letzten
            # tatsächlich beobachteten positiven Preis geschlossen.
            final_index = next(
                index for index in range(len(prices) - 1, -1, -1) if prices[index] > 0
            )
            final_price = prices[final_index]
            price_change = price_change_percent(
                position["price"],
                final_price,
                position["direction"],
            )
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
        sell_fee = position["amount"] * price * fee_percentage / Decimal(100)
        # Rückfluss auf das Konto: Margin + Rohergebnis − Schließungsgebühr.
        # Die Eröffnungsgebühr wurde bereits beim Einstieg abgezogen.
        raw_profit = gross_pnl(position["price"], price, position["amount"], position["direction"])
        profit = raw_profit - position["buy_fee"] - sell_fee
        capital_before = capital
        capital += position["margin"] + raw_profit - sell_fee
        if trades is not None:
            trades.append(
                {
                    "type": "sell",
                    "direction": position["direction"],
                    "leverage": position["leverage"],
                    "margin": position["margin"],
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
                    # Rendite auf das eingesetzte Eigenkapital (Margin + Gebühr).
                    "roi_percentage": (
                        profit / (position["margin"] + position["buy_fee"]) * Decimal(100)
                        if (position["margin"] + position["buy_fee"])
                        else Decimal(0)
                    ),
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

        # Ein- und Ausstiege werden nach Handelsrichtung getrennt dargestellt,
        # damit Long- und Short-Trades im Chart unterscheidbar bleiben.
        marker_styles = (
            ("buy", LONG, "green", "triangle-up", "Long-Einstiege"),
            ("sell", LONG, "darkgreen", "triangle-down", "Long-Ausstiege"),
            ("buy", SHORT, "red", "triangle-down", "Short-Einstiege"),
            ("sell", SHORT, "darkred", "triangle-up", "Short-Ausstiege"),
        )
        for trade_type, trade_direction, color, marker, label in marker_styles:
            trades = [
                trade
                for trade in report.get("trades", [])
                if trade["type"] == trade_type
                and (trade.get("direction") or LONG) == trade_direction
            ]
            if not trades:
                continue
            figure.add_trace(
                go.Scatter(
                    x=[trade["index"] for trade in trades],
                    y=[float(trade["price"]) for trade in trades],
                    mode="markers",
                    marker={"symbol": marker, "size": 12, "color": color},
                    name=label,
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
