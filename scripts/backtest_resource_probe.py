#!/usr/bin/env python3
"""Isolierter Ressourcen-Probe für die Backtesting-Kernsimulation."""

import json
import multiprocessing as mp
import os
import resource
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def worker(queue):
    # Harte Obergrenze entsprechend Celery max-memory-per-child.
    memory_limit = 384 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    try:
        os.nice(10)
    except OSError:
        pass

    from trading.backtesting import Backtesting
    from trading.indicators import build_indicator_rows

    prices = [Decimal(100) + Decimal(index % 97) / Decimal(100) for index in range(5_000)]
    indicators = build_indicator_rows(prices)
    started = time.perf_counter()
    best = Decimal("-Infinity")
    for index in range(100):
        final, _ = Backtesting.simulate_trading_detailed(
            prices,
            Decimal(index - 50) / Decimal(10),
            Decimal("-0.2"),
            Decimal("-0.1"),
            {
                "start_capital": Decimal(1000),
                "trade_amount": Decimal(100),
                "take_profit": Decimal(1),
                "stop_loss": Decimal(1),
                "fee_percentage": Decimal("0.1"),
            },
            indicator_rows=indicators,
        )
        best = max(best, final)
    usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    queue.put(
        {
            "candidates": 100,
            "price_points": len(prices),
            "duration_seconds": round(time.perf_counter() - started, 3),
            "peak_rss_mb": round(usage_kb / 1024, 2),
            "best_capital": str(best),
        }
    )


def main():
    heartbeat_gaps = []
    stop = threading.Event()

    def heartbeat():
        previous = time.perf_counter()
        while not stop.wait(0.02):
            now = time.perf_counter()
            heartbeat_gaps.append(now - previous)
            previous = now

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=worker, args=(queue,), name="isolated-backtest-probe")
    process.start()
    process.join(timeout=120)
    stop.set()
    thread.join(timeout=1)
    if process.is_alive():
        process.kill()
        raise SystemExit("Backtest-Probe überschritt 120 Sekunden")
    if process.exitcode != 0:
        raise SystemExit(f"Backtest-Probe fehlgeschlagen (exit={process.exitcode})")
    result = queue.get(timeout=5)
    result["worker_exit_code"] = process.exitcode
    result["parent_heartbeat_max_gap_ms"] = round(max(heartbeat_gaps, default=0) * 1000, 2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
