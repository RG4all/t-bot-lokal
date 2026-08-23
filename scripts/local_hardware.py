"""Portable hardware detection and deterministic local resource recommendations."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class HardwareInfo:
    architecture: str
    cpu_count: int
    memory_mb: int
    disk_free_mb: int
    cpu_score: float
    disk_write_mbps: float


@dataclass(frozen=True)
class LocalProfile:
    web_cpus: float
    worker_cpus: float
    postgres_cpus: float
    redis_cpus: float
    scheduler_cpus: float
    web_memory: str
    worker_memory: str
    postgres_memory: str
    redis_memory: str
    scheduler_memory: str
    redis_maxmemory: str
    postgres_shared_buffers: str
    postgres_effective_cache_size: str
    backtest_default_price_points: int
    data_log_write_interval_seconds: int
    simulate_render_free: bool

    def env(self):
        values = asdict(self)
        return {
            "WEB_CPUS": f"{values['web_cpus']:.2f}",
            "WORKER_CPUS": f"{values['worker_cpus']:.2f}",
            "POSTGRES_CPUS": f"{values['postgres_cpus']:.2f}",
            "REDIS_CPUS": f"{values['redis_cpus']:.2f}",
            "SCHEDULER_CPUS": f"{values['scheduler_cpus']:.2f}",
            "WEB_MEMORY": values["web_memory"],
            "WORKER_MEMORY": values["worker_memory"],
            "POSTGRES_MEMORY": values["postgres_memory"],
            "REDIS_MEMORY": values["redis_memory"],
            "SCHEDULER_MEMORY": values["scheduler_memory"],
            "REDIS_MAXMEMORY": values["redis_maxmemory"],
            "POSTGRES_SHARED_BUFFERS": values["postgres_shared_buffers"],
            "POSTGRES_EFFECTIVE_CACHE_SIZE": values["postgres_effective_cache_size"],
            "BACKTEST_MAX_COMBINATIONS": str(
                20_000
                if values["worker_memory"].rstrip("m") not in {"512", "768"}
                else 12_000
                if values["worker_memory"].rstrip("m") == "768"
                else 5_000
            ),
            "BACKTEST_MAX_PRICE_POINTS": str(values["backtest_default_price_points"]),
            "BACKTEST_DEFAULT_PRICE_POINTS": str(values["backtest_default_price_points"]),
            "DATA_LOG_WRITE_INTERVAL_SECONDS": str(values["data_log_write_interval_seconds"]),
            "CELERY_WORKER_MAX_MEMORY_PER_CHILD": "384000",
            "SIMULATE_RENDER_FREE": "True" if values["simulate_render_free"] else "False",
        }


def detect_memory_mb():
    if os.path.exists("/proc/meminfo"):
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    if platform.system() == "Darwin":
        try:
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)) // (
                1024 * 1024
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size // (1024 * 1024))
    except (AttributeError, OSError, ValueError):
        return 4096


def benchmark_cpu(iterations=150_000):
    started = time.perf_counter()
    value = b"t-bot-local-hardware-probe"
    for index in range(iterations):
        value = hashlib.sha256(value + index.to_bytes(4, "little")).digest()
    duration = max(0.001, time.perf_counter() - started)
    return round(iterations / duration, 1)


def benchmark_disk(size_mb=32):
    block = b"t-bot-probe" * 8192
    target_bytes = size_mb * 1024 * 1024
    started = time.perf_counter()
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="tbot-hw-", delete=False) as handle:
            path = handle.name
            written = 0
            while written < target_bytes:
                handle.write(block)
                written += len(block)
            handle.flush()
            os.fsync(handle.fileno())
        duration = max(0.001, time.perf_counter() - started)
        return round(target_bytes / (1024 * 1024) / duration, 1)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def detect_hardware(run_benchmarks=True):
    cpu_count = max(1, os.cpu_count() or 1)
    memory_mb = detect_memory_mb()
    disk = shutil.disk_usage(os.getcwd())
    return HardwareInfo(
        architecture=platform.machine().lower(),
        cpu_count=cpu_count,
        memory_mb=memory_mb,
        disk_free_mb=int(disk.free // (1024 * 1024)),
        cpu_score=benchmark_cpu() if run_benchmarks else 0.0,
        disk_write_mbps=benchmark_disk() if run_benchmarks else 0.0,
    )


def recommend_profile(hardware: HardwareInfo, simulate_render_free=False):
    if hardware.memory_mb < 1800:
        raise ValueError(
            "Mindestens 2 GB RAM werden für Web, Worker, Redis und PostgreSQL benötigt"
        )

    cores = hardware.cpu_count
    if simulate_render_free:
        web_cpu = 0.10
        worker_cpu = min(0.75, max(0.25, cores - 0.25))
    elif cores <= 2:
        web_cpu, worker_cpu = 0.65, 0.65
    elif cores <= 4:
        web_cpu, worker_cpu = 1.25, 1.50
    elif cores <= 8:
        web_cpu, worker_cpu = 2.00, 3.00
    else:
        web_cpu, worker_cpu = 3.00, 4.00

    postgres_cpu = 0.35 if cores <= 2 else min(1.5, cores * 0.15)
    redis_cpu = 0.10 if cores <= 2 else 0.25
    scheduler_cpu = 0.10 if cores <= 2 else 0.20

    if hardware.memory_mb < 6_000:
        memory = ("512m", "512m", "256m", "64m", "128m", "48mb", "64MB", "192MB")
    elif hardware.memory_mb < 12_000:
        memory = ("1024m", "768m", "512m", "128m", "192m", "96mb", "128MB", "384MB")
    else:
        memory = ("1536m", "1024m", "1024m", "256m", "256m", "192mb", "256MB", "768MB")

    if (hardware.cpu_score and hardware.cpu_score < 120_000) or hardware.memory_mb < 4_000:
        default_price_points = 2_500
    else:
        default_price_points = 5_000
    data_log_interval = 5 if hardware.disk_write_mbps >= 150 else 10

    return LocalProfile(
        web_cpus=web_cpu,
        worker_cpus=worker_cpu,
        postgres_cpus=postgres_cpu,
        redis_cpus=redis_cpu,
        scheduler_cpus=scheduler_cpu,
        web_memory=memory[0],
        worker_memory=memory[1],
        postgres_memory=memory[2],
        redis_memory=memory[3],
        scheduler_memory=memory[4],
        redis_maxmemory=memory[5],
        postgres_shared_buffers=memory[6],
        postgres_effective_cache_size=memory[7],
        backtest_default_price_points=default_price_points,
        data_log_write_interval_seconds=data_log_interval,
        simulate_render_free=simulate_render_free,
    )
