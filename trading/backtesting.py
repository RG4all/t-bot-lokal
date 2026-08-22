from decimal import ROUND_HALF_UP, Decimal

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
    ):
        """Simuliert eine einzelne Position inklusive beider Gebührenseiten."""
        prices = [_decimal(price) for price in prices]
        capital = _decimal(simulation_params.get("start_capital"), "1000")
        trade_amount = _decimal(simulation_params.get("trade_amount"), "100")
        take_profit = _decimal(simulation_params.get("take_profit"), "5")
        stop_loss = _decimal(simulation_params.get("stop_loss"), "100")
        fee_percentage = _decimal(simulation_params.get("fee_percentage"), "0.1")
        acc_threshold = _decimal(acc_threshold)
        nda_threshold = _decimal(nda_threshold)
        deltadelta_threshold = _decimal(deltadelta_threshold)
        if indicator_rows is None:
            indicator_rows = [None, None] + [
                Backtesting.calculate_indicators(prices, index) for index in range(2, len(prices))
            ]

        position = None
        trades = []

        for index in range(2, len(prices)):
            acceleration, deltadelta, current_nda = indicator_rows[index]
            current_price = prices[index]
            if current_price <= 0:
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
                    }
                    trades.append(
                        {
                            "type": "buy",
                            "price": current_price,
                            "index": index,
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
                        fee_percentage,
                        trades,
                        price_change,
                    )
                    position = None

        if position is not None and prices:
            final_price = prices[-1]
            price_change = (final_price - position["price"]) / position["price"] * Decimal(100)
            capital = Backtesting._close_position(
                capital,
                position,
                final_price,
                len(prices) - 1,
                fee_percentage,
                trades,
                price_change,
            )

        sells = [trade for trade in trades if trade["type"] == "sell"]
        profitable = sum(1 for trade in sells if trade["profit_nominal"] > 0)
        report = {
            "final_capital": capital,
            "trades": trades,
            "num_trades": len(trades),
            "num_buys": sum(1 for trade in trades if trade["type"] == "buy"),
            "num_sells": len(sells),
            "profitable_trades": profitable,
            "unprofitable_trades": len(sells) - profitable,
            "win_rate": (Decimal(profitable) / Decimal(len(sells)) * 100) if sells else 0,
        }
        return capital, report

    @staticmethod
    def _close_position(
        capital,
        position,
        price,
        index,
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
        trades.append(
            {
                "type": "sell",
                "price": price,
                "index": index,
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
