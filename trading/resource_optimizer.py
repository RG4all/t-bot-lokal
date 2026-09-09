"""Ermittlung von Serverressourcen und sichere Backtest-Empfehlungen.

Dieses Modul ist bewusst unabhängig von Django-Modellen und von externen
Diensten. Es darf daher sowohl im Webprozess als auch in einem Celery-Worker
verwendet werden. In Containern werden, sofern vorhanden, die cgroup-Limits
statt der meist deutlich größeren Host-Werte berücksichtigt.
"""

from __future__ import annotations

import math
import os
import shutil
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

_MIN_PRICE_POINTS = 100
_MAX_PRICE_POINTS = 50_000
_MIN_COMBINATIONS = 100
_DEFAULT_COMBINATIONS = 20_000
_DEFAULT_PRICE_POINTS = 5_000


def _positive_int(value, fallback):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _read_text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _read_cgroup_memory_mb() -> int | None:
    """Liest cgroup v2 und v1, ohne auf Linux angewiesen zu sein."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read_text(path)
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 verwendet bei fehlendem Limit einen sehr großen Sentinel.
        if value <= 0 or value >= 1 << 60:
            continue
        return max(1, value // (1024 * 1024))
    return None


def _read_cgroup_cpu_count() -> float | None:
    raw = _read_text("/sys/fs/cgroup/cpu.max")
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return max(0.1, quota / period)
            except ValueError:
                pass

    quota = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if quota and period and quota != "-1":
        try:
            if int(quota) > 0 and int(period) > 0:
                return max(0.1, int(quota) / int(period))
        except ValueError:
            pass
    return None


@dataclass(frozen=True)
class ServerResources:
    """Ein konsistenter, serialisierbarer Ressourcen-Snapshot."""

    cpu_count: int
    memory_mb: int
    storage_total_mb: int
    storage_free_mb: int
    cpu_quota: float | None = None
    memory_limit_mb: int | None = None
    source: str = "host"

    @property
    def effective_cpu_count(self) -> float:
        return self.cpu_quota or float(self.cpu_count)

    def as_dict(self):
        result = asdict(self)
        result["effective_cpu_count"] = round(self.effective_cpu_count, 2)
        return result


@lru_cache(maxsize=4)
def _detect_server_resources(path_string: str) -> ServerResources:
    path = Path(path_string)
    host_cpu_count = max(1, os.cpu_count() or 1)
    cpu_quota = _read_cgroup_cpu_count()
    # os.cpu_count() kann in älteren Docker-Versionen den Host melden. Die
    # cgroup-CPU-Menge ist für die Backtest-Prognose die sichere Untergrenze.
    effective_cpu = max(1, math.floor(cpu_quota)) if cpu_quota else host_cpu_count

    memory_mb = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    memory_mb = int(line.split()[1]) // 1024
                    break
    except (OSError, ValueError, IndexError):
        pass
    if not memory_mb:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            memory_mb = int(pages * page_size // (1024 * 1024))
        except (AttributeError, OSError, ValueError):
            memory_mb = 4096

    cgroup_memory = _read_cgroup_memory_mb()
    if cgroup_memory:
        memory_mb = min(memory_mb, cgroup_memory)

    try:
        disk = shutil.disk_usage(path)
        total_mb = int(disk.total // (1024 * 1024))
        free_mb = int(disk.free // (1024 * 1024))
    except OSError:
        total_mb = free_mb = 0

    source = "container" if cpu_quota or cgroup_memory else "host"
    return ServerResources(
        cpu_count=effective_cpu,
        memory_mb=max(1, memory_mb),
        storage_total_mb=max(0, total_mb),
        storage_free_mb=max(0, free_mb),
        cpu_quota=cpu_quota,
        memory_limit_mb=cgroup_memory,
        source=source,
    )


def get_server_resources(path: str | Path | None = None, refresh: bool = False) -> ServerResources:
    """Ermittelt CPU, RAM und freien/gesamten Speicher des laufenden Servers.

    Der Snapshot wird pro Prozess gecacht, weil er in der UI bei jeder Anfrage
    benötigt werden kann. ``refresh=True`` ist für Diagnose-Endpunkte und
    Tests vorgesehen; es führt keine I/O-Benchmarks aus.
    """
    path = Path(path or os.environ.get("TBOT_RESOURCE_PATH", os.getcwd())).resolve()
    if refresh:
        _detect_server_resources.cache_clear()
    return _detect_server_resources(str(path))


# Verständlicher Alias für Integrationen, die die englische Bezeichnung nutzen.
detect_server_resources = get_server_resources


@dataclass(frozen=True)
class BacktestResourceProfile:
    """Sicheres Backtest-Budget für genau einen Web-/Worker-Prozess."""

    resources: ServerResources
    max_price_points: int
    max_combinations: int
    max_grid_points: int
    estimated_seconds_per_combination_at_1000_points: float

    def as_dict(self):
        result = {
            "max_price_points": self.max_price_points,
            "max_combinations": self.max_combinations,
            "max_grid_points": self.max_grid_points,
            "estimated_seconds_per_combination_at_1000_points": (
                self.estimated_seconds_per_combination_at_1000_points
            ),
            "resources": self.resources.as_dict(),
        }
        return result


def _env_int(name: str, default: int) -> int:
    return _positive_int(os.environ.get(name), default)


def optimize_backtest_limits(
    resources: ServerResources | None = None,
    *,
    configured_max_combinations: int | None = None,
    configured_max_price_points: int | None = None,
) -> BacktestResourceProfile:
    """Berechnet ein Hardware-Budget ohne die Maschine zu übercommitten.

    Der Administrator kann ``BACKTEST_MAX_COMBINATIONS`` und
    ``BACKTEST_MAX_PRICE_POINTS`` als absolute Obergrenzen setzen. Die
    Hardware-Heuristik darf diese Werte nur verkleinern, nie überschreiten.
    ``max_combinations`` zählt bereits über alle Symbole hinweg.
    """
    resources = resources or get_server_resources()
    configured_combinations = (
        configured_max_combinations
        if configured_max_combinations is not None
        else _env_int("BACKTEST_MAX_COMBINATIONS", _DEFAULT_COMBINATIONS)
    )
    configured_price_points = (
        configured_max_price_points
        if configured_max_price_points is not None
        else _env_int("BACKTEST_MAX_PRICE_POINTS", _DEFAULT_PRICE_POINTS)
    )
    configured_combinations = max(_MIN_COMBINATIONS, configured_combinations)
    configured_price_points = max(_MIN_PRICE_POINTS, configured_price_points)

    # Ein Backtest darf nur einen Teil des RAMs beanspruchen; Datenpunkte sind
    # pro Symbol im Speicher und jede Kombination läuft über alle Punkte.
    memory_factor = max(0.35, min(8.0, resources.memory_mb / 2048))
    cpu_factor = max(0.5, min(16.0, resources.effective_cpu_count))
    storage_factor = 0.65 if resources.storage_free_mb and resources.storage_free_mb < 1024 else 1.0
    hardware_combinations = int(
        2_000 * math.sqrt(memory_factor) * math.sqrt(cpu_factor) * storage_factor
    )
    hardware_combinations = max(
        _MIN_COMBINATIONS, min(configured_combinations, hardware_combinations)
    )

    if resources.memory_mb < 2_048:
        hardware_points = 1_000
    elif resources.memory_mb < 4_096:
        hardware_points = 2_500
    else:
        hardware_points = 5_000
    if resources.storage_free_mb and resources.storage_free_mb < 512:
        hardware_points = min(hardware_points, 1_000)
    max_price_points = max(
        _MIN_PRICE_POINTS, min(configured_price_points, hardware_points, _MAX_PRICE_POINTS)
    )

    # Erfahrungswert mit Decimal-Indikatoren plus einer kleinen I/O-Reserve;
    # absichtlich konservativ, damit die Anzeige nicht zu optimistisch wirkt.
    seconds_per_combination = round(0.0000025 * 1000 / max(0.75, cpu_factor**0.65), 6)
    max_grid_points = max(2, int(hardware_combinations ** (1 / 3)))
    return BacktestResourceProfile(
        resources=resources,
        max_price_points=max_price_points,
        max_combinations=hardware_combinations,
        max_grid_points=max_grid_points,
        estimated_seconds_per_combination_at_1000_points=seconds_per_combination,
    )


# Alias für Code außerhalb des Projekts.
get_backtest_resource_profile = optimize_backtest_limits


def estimate_backtest_runtime(
    combinations: int,
    price_points: int,
    symbols: int = 1,
    resources: ServerResources | None = None,
) -> dict:
    """Gibt eine grobe, klar als Schätzung markierte Laufzeit zurück.

    ``combinations`` ist die Gesamtzahl über alle Symbole. Für alte Aufrufer,
    die Kombinationen pro Symbol übergeben, kann ``symbols`` zusätzlich
    gesetzt werden; die Anzeige rechnet dann konservativ mit allen Symbolen.
    """
    combinations = max(0, int(combinations))
    price_points = max(0, int(price_points))
    symbols = max(1, int(symbols))
    profile = optimize_backtest_limits(resources)
    total_combinations = combinations * symbols
    seconds = (
        total_combinations
        * (price_points / 1000)
        * profile.estimated_seconds_per_combination_at_1000_points
    )
    # Nie eine irreführende Null anzeigen; eine leere Historie wird separat
    # behandelt, reale Arbeit braucht mindestens eine kleine Zeitspanne.
    seconds = round(seconds, 1) if seconds else 0.0
    return {
        "seconds": seconds,
        "minutes": round(seconds / 60, 1),
        "hours": round(seconds / 3600, 2),
        "formatted": format_duration(seconds),
        "combinations": combinations,
        "symbols": symbols,
        "price_points": price_points,
        "is_estimate": True,
        "resources": profile.resources.as_dict(),
    }


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"ca. {seconds:.1f} s"
    whole_seconds = round(seconds)
    minutes, remainder = divmod(whole_seconds, 60)
    if minutes < 60:
        return f"ca. {minutes} min {remainder:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"ca. {hours} h {minutes:02d} min"
