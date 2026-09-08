#!/usr/bin/env python3
"""Reproduzierbarer Mikrobenchmark; kein End-to-End-Backtest-Versprechen."""

import json
import statistics
import sys
from decimal import Decimal
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.backtesting import Backtesting


def main():
    price_points = 5_000
    repetitions = 20
    samples = 5
    prices = [Decimal(100) + Decimal(index % 97) / 100 for index in range(price_points)]

    def measure(calculate):
        started = perf_counter()
        for _ in range(repetitions):
            for index in range(2, len(prices)):
                calculate(prices, index)
        return perf_counter() - started

    try:
        Backtesting.clear_indicator_cache()
        expected = [Backtesting._calculate_indicators(prices, i) for i in range(2, len(prices))]
        started = perf_counter()
        actual = [Backtesting.calculate_indicators(prices, i) for i in range(2, len(prices))]
        cold_seconds = perf_counter() - started
        if actual != expected:
            raise AssertionError("Cache-Ergebnisse weichen von der Decimal-Referenz ab")
        uncached_samples = []
        cached_samples = []
        for sample in range(samples):
            # Reihenfolge wechseln, um Warm-up-/Last-Effekte zu reduzieren.
            order = (False, True) if sample % 2 else (True, False)
            for cached in order:
                calculate = (
                    Backtesting.calculate_indicators
                    if cached
                    else Backtesting._calculate_indicators
                )
                (cached_samples if cached else uncached_samples).append(measure(calculate))
        uncached = statistics.median(uncached_samples)
        cached = statistics.median(cached_samples)
        print(
            json.dumps(
                {
                    "price_points": price_points,
                    "repetitions": repetitions,
                    "samples": samples,
                    "calls_per_sample": repetitions * (price_points - 2),
                    "cold_fill_seconds": round(cold_seconds, 6),
                    "uncached_median_seconds": round(uncached, 6),
                    "cached_median_seconds": round(cached, 6),
                    "speedup": round(uncached / cached, 2),
                },
                indent=2,
            )
        )
    finally:
        Backtesting.clear_indicator_cache()


if __name__ == "__main__":
    main()
