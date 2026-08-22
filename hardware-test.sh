#!/usr/bin/env bash
# ============================================================================
# hardware-test.sh - Hardware-Analyse und automatische Optimierung fuer t-bot
#
# Vermisst:
#   * CPU-Anzahl und -Architektur
#   * RAM-Kapazitaet und verfuegbaren Speicher
#   * Disk-I/O-Performance (Schreib-/Lesedurchsatz)
#
# Leitet daraus empfohlene Werte fuer:
#   * Redis: maxmemory, maxmemory-policy, io-threads
#   * t-bot: BOT_DB_WORKERS, DBP_POOL_SIZE, CELERY_WORKER_MAX_MEMORY_PER_CHILD,
#            Web-/Worker-CPU-Limits
#
# ab und schreibt eine fuer Docker-Compose sourcbare env-Datei sowie einen
# menschlichen Bericht.
#
# Nutzung:
#   ./hardware-test.sh [--env-out=DATEI] [--report-out=DATEI]
#                      [--disk-test-dir=DIR] [--skip-disk-test]
#                      [--format=auto|env|json|text]
# ============================================================================

set -euo pipefail

if [[ "${__HARDWARE_TEST_SOURCED:-0}" == "1" ]]; then
  :
fi

SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_NAME SCRIPT_DIR

# Defaults fuer Ueberschreibungen (insbesondere im Test/Container nuetzlich).
: "${HW_NPROC_CMD:=nproc}"
: "${HW_UNAME_CMD:=uname}"
: "${HW_MEMINFO:=/proc/meminfo}"
: "${HW_CPUINFO:=/proc/cpuinfo}"
: "${HW_FREE_CMD:=free}"

ENV_OUT="${SCRIPT_DIR}/config/hardware.env"
REPORT_OUT="${SCRIPT_DIR}/config/hardware-report.txt"
DISK_TEST_DIR=""
SKIP_DISK_TEST=0
FORMAT="auto"

# Ergebnis-Variablen
CPU_COUNT=0
CPU_MHZ=0
CPU_ARCH=""
CPU_ARCH_NORMALIZED=""
CPU_MODEL=""
CPU_HASHRATE_MBPS=0
RAM_TOTAL_KB=0
RAM_AVAILABLE_KB=0
DISK_FREE_KB=0
DISK_WRITE_MBPS=0
DISK_READ_MBPS=0
FSYNC_LATENCY_MS=0

# Empfehlungen
REDIS_MAXMEMORY_MB=0
REDIS_MAXMEMORY_POLICY="noeviction"
REDIS_IO_THREADS=1
BOT_DB_WORKERS=1
DB_POOL_SIZE=4
WEB_CPUS=0.50
WORKER_CPUS=0.50
REDIS_CPUS=0.20
POSTGRES_CPUS=0.50
SCHEDULER_CPUS=0.20
CELERY_WORKER_MAX_MEMORY_PER_CHILD=384000
WEB_CONCURRENCY=1
POSTGRES_MAX_CONNECTIONS=40
POSTGRES_SHARED_BUFFERS_MB=32
POSTGRES_EFFECTIVE_CACHE_MB=128
POSTGRES_WORK_MEM_MB=4

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_info()  { printf '\033[0;32m[HW]\033[0m  %s\n' "$*" >&2; }
log_warn()  { printf '\033[0;33m[HW]\033[0m  %s\n' "$*" >&2; }
log_error() { printf '\033[0;31m[HW]\033[0m  %s\n' "$*" >&2; }
die()       { log_error "$*"; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'USAGE'
Nutzung: hardware-test.sh [OPTIONEN]

  --env-out=DATEI        Pfad fuer die generierte env-Datei (sourcbar)
  --report-out=DATEI     Pfad fuer den menschlichen Bericht
  --disk-test-dir=DIR    Verzeichnis fuer den Disk-I/O-Test (Temp-Standard)
  --skip-disk-test       Keinen Disk-I/O-Test ausfuehren
  --format=FMT           auto|env|json|text (Default: auto)
  -h, --help             Hilfe anzeigen
USAGE
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "${arg}" in
      --env-out=*)     ENV_OUT="${arg#*=}" ;;
      --report-out=*)  REPORT_OUT="${arg#*=}" ;;
      --disk-test-dir=*) DISK_TEST_DIR="${arg#*=}" ;;
      --skip-disk-test) SKIP_DISK_TEST=1 ;;
      --format=*)      FORMAT="${arg#*=}" ;;
      -h|--help)       usage; exit 0 ;;
      *)               die "Unbekannte Option: ${arg}" ;;
    esac
  done
  case "${FORMAT}" in
    auto|env|json|text) ;;
    *) die "Ungueltiges Format: ${FORMAT}" ;;
  esac
}

# ---------------------------------------------------------------------------
# Erkennungsfunktionen
# ---------------------------------------------------------------------------
detect_cpu() {
  CPU_COUNT="$("${HW_NPROC_CMD}" 2>/dev/null || echo 1)"
  [[ "${CPU_COUNT}" =~ ^[0-9]+$ ]] || CPU_COUNT=1

  CPU_ARCH="$("${HW_UNAME_CMD}" -m 2>/dev/null || echo "unknown")"
  # Normalisierung auf Docker-Platform-kompatible Bezeichner.
  case "${CPU_ARCH}" in
    x86_64|amd64)        CPU_ARCH_NORMALIZED="amd64" ;;
    aarch64|arm64)       CPU_ARCH_NORMALIZED="arm64" ;;
    armv7*|armhf)        CPU_ARCH_NORMALIZED="arm/v7" ;;
    ppc64le)             CPU_ARCH_NORMALIZED="ppc64le" ;;
    s390x)               CPU_ARCH_NORMALIZED="s390x" ;;
    riscv64)             CPU_ARCH_NORMALIZED="riscv64" ;;
    *)                   CPU_ARCH_NORMALIZED="${CPU_ARCH}" ;;
  esac

  if [[ -r "${HW_CPUINFO}" ]]; then
    CPU_MODEL="$(grep -m1 -E '^model name' "${HW_CPUINFO}" | cut -d: -f2- | sed 's/^[[:space:]]*//' || true)"
    # ARM nutzt "Hardware" / "model name" je nach Kernel; leer bleiben ist ok.
    [[ -z "${CPU_MODEL}" ]] && CPU_MODEL="$(grep -m1 -E '^(Hardware|Processor)' "${HW_CPUINFO}" | cut -d: -f2- | sed 's/^[[:space:]]*//' || true)"
    CPU_MHZ="$(grep -m1 -E '^cpu MHz' "${HW_CPUINFO}" | cut -d: -f2- | awk '{printf "%d", $1}' || echo 0)"
  fi
  [[ -n "${CPU_MODEL}" ]] || CPU_MODEL="unknown"
}

# ---------------------------------------------------------------------------
# CPU-Hashrate: misst, wie viele MB SHA-256 pro Sekunde verarbeitet werden
# koennen. Gibt einen ungefaehren Anhaltspunkt fuer Single-Core-Durchsatz.
# Sicher: nur /dev/zero lesen, keine Dateien schreiben.
# ---------------------------------------------------------------------------
measure_cpu_hashrate() {
  local tool=""
  if have openssl; then tool="openssl"
  elif have sha256sum; then tool="sha256sum"
  else log_warn "Weder openssl noch sha256sum verfuegbar - CPU-Hashrate uebersprungen."; return 0
  fi

  local start end bytes
  bytes=$(( 64 * 1024 * 1024 ))  # 64 MB
  start="$(date +%s.%N 2>/dev/null || date +%s)"
  if [[ "${tool}" == "openssl" ]]; then
    # openssl speed -bytes 16384 sha256 misst viele kleine Bloecke; wir
    # nehmen stattdessen einen 64-MB-Block ueber Pipe.
    if head -c "${bytes}" /dev/zero 2>/dev/null | openssl dgst -sha256 >/dev/null 2>&1; then
      :
    fi
  else
    head -c "${bytes}" /dev/zero 2>/dev/null | sha256sum >/dev/null 2>&1 || true
  fi
  end="$(date +%s.%N 2>/dev/null || date +%s)"

  local dur
  dur="$(awk -v s="${start}" -v e="${end}" 'BEGIN { d=e-s; if (d<=0) d=1; printf "%.3f", d }')"
  CPU_HASHRATE_MBPS="$(awk -v b="${bytes}" -v d="${dur}" 'BEGIN { printf "%.0f", (b/1048576)/d }')"
  : "${CPU_HASHRATE_MBPS:=0}"
}

detect_memory() {
  if [[ -r "${HW_MEMINFO}" ]]; then
    RAM_TOTAL_KB="$(awk '/^MemTotal:/ {print $2}' "${HW_MEMINFO}")"
    RAM_AVAILABLE_KB="$(awk '/^MemAvailable:/ {print $2}' "${HW_MEMINFO}")"
  fi
  # Fallback: free
  if [[ -z "${RAM_TOTAL_KB}" || "${RAM_TOTAL_KB}" == "0" ]] && have "${HW_FREE_CMD%% *}"; then
    RAM_TOTAL_KB="$(${HW_FREE_CMD} | awk '/^Mem:/ {print $2}')"
    RAM_AVAILABLE_KB="$(${HW_FREE_CMD} | awk '/^Mem:/ {print $7}')"
  fi
  : "${RAM_TOTAL_KB:=0}"
  : "${RAM_AVAILABLE_KB:=0}"
}

# ---------------------------------------------------------------------------
# Freien Plattenspeicher im Test-Verzeichnis messen (in KB).
# ---------------------------------------------------------------------------
detect_disk_free() {
  local dir="${DISK_TEST_DIR:-${TMPDIR:-/tmp}}"
  mkdir -p "${dir}" 2>/dev/null || true
  # POSIX-portabel: df -P (eine Zeile pro Dateisystem).
  DISK_FREE_KB="$(df -Pk "${dir}" 2>/dev/null | awk 'NR==2 {print $4}')"
  : "${DISK_FREE_KB:=0}"
}

# ---------------------------------------------------------------------------
# fsync-Latenz: 32-mal eine kleine Datei erstellen, mit conv=fdatasync
# schreiben und die durchschnittliche Dauer in Millisekunden berichten.
# ---------------------------------------------------------------------------
measure_fsync_latency() {
  [[ "${SKIP_DISK_TEST}" -eq 1 ]] && return 0
  have dd || return 0
  local dir="${DISK_TEST_DIR:-${TMPDIR:-/tmp}}"
  mkdir -p "${dir}"
  local f; f="$(mktemp "${dir}/tbot-fsync.XXXXXXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -f '${f}'" RETURN

  local runs=32 i start end dur total=0
  for ((i=0; i<runs; i++)); do
    start="$(date +%s%N 2>/dev/null || echo 0)"
    dd if=/dev/zero of="${f}" bs=4k count=1 conv=fdatasync status=none 2>/dev/null || true
    end="$(date +%s%N 2>/dev/null || echo 0)"
    if [[ "${start}" != "0" && "${end}" != "0" ]]; then
      dur=$(( (end - start) / 1000000 ))
      total=$(( total + dur ))
    fi
  done
  if (( runs > 0 )); then
    FSYNC_LATENCY_MS=$(( total / runs ))
  else
    FSYNC_LATENCY_MS=0
  fi
}

# ---------------------------------------------------------------------------
# Parst die Durchsatzrate aus der letzten Zeile eines dd-Laufs.
# GNU-dd (Englisch): "64 MB (64.0 MB, 64.0 MiB) copied, 0.123 s, 520 MB/s"
# GNU-dd (Lokalisiert): "64 Bytes kopiert, 0.123 s, 520 MB/s"
# Altes dd: "64+0 records in ... 52428800 bytes (64 MB) copied, 0.123 s, 520 MB/s"
# Gibt die Rate in MB/s auf stdout aus (GB/s werden umgerechnet).
# ---------------------------------------------------------------------------
parse_dd_throughput() {
  local output="$1"
  local last_line num unit
  last_line="$(printf '%s\n' "${output}" | tail -1)"

  # Suche nach einer Zahl direkt gefolgt von GB/s oder MB/s.
  if [[ "${last_line}" =~ ([0-9]+([.,][0-9]+)?)\ *(GB/s|MB/s) ]]; then
    num="${BASH_REMATCH[1]//,/.}"
    unit="${BASH_REMATCH[3]}"
    if [[ "${unit}" == "GB/s" ]]; then
      awk -v n="${num}" 'BEGIN { printf "%.0f", n * 1024 }'
    else
      awk -v n="${num}" 'BEGIN { printf "%.0f", n }'
    fi
  else
    printf '0'
  fi
}

# ---------------------------------------------------------------------------
# Disk-I/O-Benchmark (sicher: nur in eigenem Temp-File)
# ---------------------------------------------------------------------------
disk_test() {
  [[ "${SKIP_DISK_TEST}" -eq 1 ]] && { log_info "Disk-Test uebersprungen."; return 0; }
  have dd || { log_warn "dd nicht verfuegbar - Disk-Test uebersprungen."; return 0; }

  local test_dir="${DISK_TEST_DIR:-${TMPDIR:-/tmp}}"
  mkdir -p "${test_dir}"
  local f; f="$(mktemp "${test_dir}/tbot-hw.XXXXXXXXXX")"
  # Sicherstellen, dass die Datei wieder entfernt wird.
  # shellcheck disable=SC2064
  trap "rm -f '${f}'" RETURN

  # 1M ist portabel (GNU + BSD dd); 64 Bloecke = 64 MB Testdatei.
  local bs="1M" count="64"

  # Schreibtest (64 MB)
  local write_out
  write_out="$(dd if=/dev/zero of="${f}" bs="${bs}" count="${count}" conv=fdatasync 2>&1)" || {
    log_warn "Schreibtest fehlgeschlagen: ${write_out}"; return 0;
  }
  DISK_WRITE_MBPS="$(parse_dd_throughput "${write_out}")"
  DISK_WRITE_MBPS="${DISK_WRITE_MBPS:-0}"

  # Cache fallen lassen und Lesetest
  sync
  if [[ -w /proc/sys/vm/drop_caches ]]; then
    echo 3 >/proc/sys/vm/drop_caches 2>/dev/null || true
  fi
  local read_out
  read_out="$(dd if="${f}" of=/dev/null bs="${bs}" 2>&1)" || true
  DISK_READ_MBPS="$(parse_dd_throughput "${read_out}")"
  DISK_READ_MBPS="${DISK_READ_MBPS:-0}"
}

# ---------------------------------------------------------------------------
# Heuristik zur Empfehlungsberechnung
# ---------------------------------------------------------------------------
compute_recommendations() {
  local total_mb=$((RAM_TOTAL_KB / 1024))
  local avail_mb=$((RAM_AVAILABLE_KB / 1024))

  # --- Redis ---
  # Maxmemory: 10% des gesamten RAMs, mindestens 32 MB, max 1024 MB.
  local redis_mem=$(( total_mb / 10 ))
  (( redis_mem < 32 ))    && redis_mem=32
  (( redis_mem > 1024 ))  && redis_mem=1024
  # Bei sehr knappem verfuegbarem Speicher auf 32 MB deckeln.
  (( avail_mb > 0 && redis_mem > avail_mb / 2 )) && redis_mem=$(( avail_mb / 2 ))
  (( redis_mem < 32 )) && redis_mem=32
  REDIS_MAXMEMORY_MB="${redis_mem}"

  # noeviction ist der sichere Standard fuer einen Task-Broker (keine
  # stillschweigend verlorenen Jobs). allkeys-lrU nur waehlen, wenn Redis
  # ohnehin fast nur als Cache genutzt wird.
  REDIS_MAXMEMORY_POLICY="noeviction"

  # io-threads: lohnen sich erst ab >= 4 Kernen; max 4.
  if (( CPU_COUNT >= 8 )); then
    REDIS_IO_THREADS=4
  elif (( CPU_COUNT >= 4 )); then
    REDIS_IO_THREADS=2
  else
    REDIS_IO_THREADS=1
  fi

  # --- t-bot / App ---
  # Ein Worker isoliert einen Backtest (Sicherheit/Garantien im Projekt).
  # BOT_DB_WORKERS skaliert die DB-Thread-Pool-Groesse, aber >= 1.
  BOT_DB_WORKERS=$(( CPU_COUNT / 2 ))
  (( BOT_DB_WORKERS < 1 )) && BOT_DB_WORKERS=1
  (( BOT_DB_WORKERS > 8 )) && BOT_DB_WORKERS=8

  DB_POOL_SIZE=$(( BOT_DB_WORKERS * 4 ))
  (( DB_POOL_SIZE < 4 ))  && DB_POOL_SIZE=4
  (( DB_POOL_SIZE > 32 )) && DB_POOL_SIZE=32

  WEB_CONCURRENCY=$(( CPU_COUNT / 2 ))
  (( WEB_CONCURRENCY < 1 )) && WEB_CONCURRENCY=1
  (( WEB_CONCURRENCY > 4 )) && WEB_CONCURRENCY=4

  # Maximaler Speicher pro Celery-Child in KB (Sicherheits-Polster).
  local celery_mem=384000
  if   (( total_mb >= 16384 )); then celery_mem=768000
  elif (( total_mb >=  8192 )); then celery_mem=512000
  elif (( total_mb <   2048 )); then celery_mem=192000
  fi
  CELERY_WORKER_MAX_MEMORY_PER_CHILD="${celery_mem}"

  # CPU-Limits fuer Docker Compose (in Anteilen eines Kernels).
  if   (( CPU_COUNT >= 8 )); then
    WEB_CPUS=1.00; WORKER_CPUS=1.00; POSTGRES_CPUS=1.00
    REDIS_CPUS=0.50; SCHEDULER_CPUS=0.50
  elif (( CPU_COUNT >= 4 )); then
    WEB_CPUS=0.75; WORKER_CPUS=0.75; POSTGRES_CPUS=0.75
    REDIS_CPUS=0.30; SCHEDULER_CPUS=0.30
  elif (( CPU_COUNT >= 2 )); then
    WEB_CPUS=0.50; WORKER_CPUS=0.50; POSTGRES_CPUS=0.50
    REDIS_CPUS=0.20; SCHEDULER_CPUS=0.20
  else
    WEB_CPUS=0.35; WORKER_CPUS=0.35; POSTGRES_CPUS=0.35
    REDIS_CPUS=0.15; SCHEDULER_CPUS=0.15
  fi

  # --- PostgreSQL (lokal) ---
  # Max. 40 Verbindungen (Sicherheitsabstand zu den Free-Tier-Slots),
  # skaliert mit RAM. shared_buffers ~25% RAM (max 256 MB lokal),
  # effective_cache_size ~50% RAM (max 512 MB), work_mem ~4 MB.
  local shared=$(( total_mb / 4 ))
  local cache=$(( total_mb / 2 ))
  local wmem=4
  (( shared < 32 ))  && shared=32
  (( shared > 256 )) && shared=256
  (( cache  < 64 ))  && cache=64
  (( cache  > 512 )) && cache=512
  if   (( total_mb >= 8192 )); then wmem=16
  elif (( total_mb >= 4096 )); then wmem=8
  fi
  POSTGRES_SHARED_BUFFERS_MB="${shared}"
  POSTGRES_EFFECTIVE_CACHE_MB="${cache}"
  POSTGRES_WORK_MEM_MB="${wmem}"
  POSTGRES_MAX_CONNECTIONS=40
}

# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------
render_env() {
  cat <<EOF
# Auto-generated by ${SCRIPT_NAME} - $(date -u +%Y-%m-%dT%H:%M:%SZ)
# DO NOT EDIT MANUALLY. Re-run hardware-test.sh to regenerate.
CPU_ARCH=${CPU_ARCH_NORMALIZED}
CPU_COUNT=${CPU_COUNT}
CPU_HASHRATE_MBPS=${CPU_HASHRATE_MBPS}
REDIS_MAXMEMORY_MB=${REDIS_MAXMEMORY_MB}
REDIS_MAXMEMORY_POLICY=${REDIS_MAXMEMORY_POLICY}
REDIS_IO_THREADS=${REDIS_IO_THREADS}
BOT_DB_WORKERS=${BOT_DB_WORKERS}
DB_POOL_SIZE=${DB_POOL_SIZE}
WEB_CONCURRENCY=${WEB_CONCURRENCY}
WEB_CPUS=${WEB_CPUS}
WORKER_CPUS=${WORKER_CPUS}
POSTGRES_CPUS=${POSTGRES_CPUS}
POSTGRES_MAX_CONNECTIONS=${POSTGRES_MAX_CONNECTIONS}
POSTGRES_SHARED_BUFFERS_MB=${POSTGRES_SHARED_BUFFERS_MB}
POSTGRES_EFFECTIVE_CACHE_MB=${POSTGRES_EFFECTIVE_CACHE_MB}
POSTGRES_WORK_MEM_MB=${POSTGRES_WORK_MEM_MB}
REDIS_CPUS=${REDIS_CPUS}
SCHEDULER_CPUS=${SCHEDULER_CPUS}
CELERY_WORKER_MAX_MEMORY_PER_CHILD=${CELERY_WORKER_MAX_MEMORY_PER_CHILD}
EOF
}

render_text_report() {
  local total_mb=$((RAM_TOTAL_KB / 1024))
  local avail_mb=$((RAM_AVAILABLE_KB / 1024))
  cat <<EOF
t-bot-lokal - Hardware-Analyse
==============================
Datum/Zeit (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)
Host: $(uname -n 2>/dev/null || echo unknown)

CPU
---
  Anzahl logischer Kerne : ${CPU_COUNT}
  Architektur            : ${CPU_ARCH} (${CPU_ARCH_NORMALIZED})
  Modell                 : ${CPU_MODEL}
  Takt                   : ${CPU_MHZ} MHz
  SHA-256 Durchsatz      : ${CPU_HASHRATE_MBPS} MB/s

Speicher
--------
  Gesamter RAM           : ${total_mb} MB
  Verfuegbarer RAM       : ${avail_mb} MB

Disk
----
  Freier Speicher        : $((DISK_FREE_KB / 1024)) MB
  Schreibdurchsatz       : ${DISK_WRITE_MBPS} MB/s
  Lesedurchsatz          : ${DISK_READ_MBPS} MB/s
  fsync-Latenz (Mittel)  : ${FSYNC_LATENCY_MS} ms

Empfohlene Konfiguration
------------------------
  Redis
    maxmemory            : ${REDIS_MAXMEMORY_MB} MB
    maxmemory-policy     : ${REDIS_MAXMEMORY_POLICY}
    io-threads           : ${REDIS_IO_THREADS}

  t-bot / App
    BOT_DB_WORKERS       : ${BOT_DB_WORKERS}
    DB_POOL_SIZE         : ${DB_POOL_SIZE}
    WEB_CONCURRENCY      : ${WEB_CONCURRENCY}
    CELERY_WORKER_...    : ${CELERY_WORKER_MAX_MEMORY_PER_CHILD} KB

  PostgreSQL
    max_connections      : ${POSTGRES_MAX_CONNECTIONS}
    shared_buffers       : ${POSTGRES_SHARED_BUFFERS_MB} MB
    effective_cache_size : ${POSTGRES_EFFECTIVE_CACHE_MB} MB
    work_mem             : ${POSTGRES_WORK_MEM_MB} MB

  Docker-Compose CPU-Limits
    WEB_CPUS             : ${WEB_CPUS}
    WORKER_CPUS          : ${WORKER_CPUS}
    POSTGRES_CPUS        : ${POSTGRES_CPUS}
    REDIS_CPUS           : ${REDIS_CPUS}
    SCHEDULER_CPUS       : ${SCHEDULER_CPUS}
EOF
}

render_json() {
  cat <<EOF
{
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "cpu": {
    "count": ${CPU_COUNT},
    "arch": "${CPU_ARCH}",
    "arch_normalized": "${CPU_ARCH_NORMALIZED}",
    "model": "${CPU_MODEL//\"/\\\"}",
    "mhz": ${CPU_MHZ},
    "sha256_mbps": ${CPU_HASHRATE_MBPS}
  },
  "memory_mb": {
    "total": $((RAM_TOTAL_KB / 1024)),
    "available": $((RAM_AVAILABLE_KB / 1024))
  },
  "disk": {
    "free_mb": $((DISK_FREE_KB / 1024)),
    "write_mbps": ${DISK_WRITE_MBPS},
    "read_mbps": ${DISK_READ_MBPS},
    "fsync_latency_ms": ${FSYNC_LATENCY_MS}
  },
  "recommendations": {
    "redis": {
      "maxmemory_mb": ${REDIS_MAXMEMORY_MB},
      "maxmemory_policy": "${REDIS_MAXMEMORY_POLICY}",
      "io_threads": ${REDIS_IO_THREADS}
    },
    "app": {
      "bot_db_workers": ${BOT_DB_WORKERS},
      "db_pool_size": ${DB_POOL_SIZE},
      "web_concurrency": ${WEB_CONCURRENCY},
      "celery_worker_max_memory_per_child_kb": ${CELERY_WORKER_MAX_MEMORY_PER_CHILD}
    },
    "postgres": {
      "max_connections": ${POSTGRES_MAX_CONNECTIONS},
      "shared_buffers_mb": ${POSTGRES_SHARED_BUFFERS_MB},
      "effective_cache_mb": ${POSTGRES_EFFECTIVE_CACHE_MB},
      "work_mem_mb": ${POSTGRES_WORK_MEM_MB}
    },
    "compose_cpus": {
      "web": ${WEB_CPUS},
      "worker": ${WORKER_CPUS},
      "postgres": ${POSTGRES_CPUS},
      "redis": ${REDIS_CPUS},
      "scheduler": ${SCHEDULER_CPUS}
    }
  }
}
EOF
}

write_outputs() {
  mkdir -p "$(dirname "${ENV_OUT}")" "$(dirname "${REPORT_OUT}")"
  render_env > "${ENV_OUT}"
  chmod 644 "${ENV_OUT}"
  render_text_report > "${REPORT_OUT}"
  chmod 644 "${REPORT_OUT}"
  log_info "Env-Datei geschrieben : ${ENV_OUT}"
  log_info "Bericht geschrieben   : ${REPORT_OUT}"
}

# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"
  detect_cpu
  measure_cpu_hashrate
  detect_memory
  detect_disk_free
  disk_test
  measure_fsync_latency
  compute_recommendations
  write_outputs

  case "${FORMAT}" in
    env)  render_env ;;
    json) render_json ;;
    text) render_text_report ;;
    auto) render_text_report ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
