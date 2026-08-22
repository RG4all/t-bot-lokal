#!/usr/bin/env bash
set -Eeuo pipefail

DRY_RUN=0
WITH_REDIS_SERVER=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --with-host-redis) WITH_REDIS_SERVER=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/install_system_dependencies.sh [--dry-run] [--with-host-redis]

Installs Docker/Compose, Python tooling and native PDF/build libraries.
The normal local stack runs Redis in Docker; --with-host-redis additionally
installs the distro's Redis server package for non-Compose development.

Testing overrides: TBOT_PACKAGE_FAMILY=apt|pacman|dnf|yum|zypper|apk
EOF
      exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1 || [[ $DRY_RUN -eq 1 ]]; then
  SUDO=(sudo)
else
  echo "Root privileges or sudo are required." >&2
  exit 1
fi

run() {
  printf '+ '
  printf '%q ' "$@"
  printf '\n'
  if [[ $DRY_RUN -eq 0 ]]; then "$@"; fi
}

source_os_release() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
  else
    ID=unknown
    # shellcheck disable=SC2034
    ID_LIKE=""
  fi
}

source_os_release
ID=${TBOT_DISTRO_ID:-${ID:-unknown}}
ARCH_RAW=${TBOT_ARCH:-$(uname -m)}
case "$ARCH_RAW" in
  x86_64|amd64) OCI_ARCH=amd64 ;;
  aarch64|arm64) OCI_ARCH=arm64 ;;
  armv7l|armv7) OCI_ARCH=arm/v7 ;;
  ppc64le) OCI_ARCH=ppc64le ;;
  s390x) OCI_ARCH=s390x ;;
  *) echo "Unsupported CPU architecture: $ARCH_RAW" >&2; exit 1 ;;
esac

if [[ -n ${TBOT_PACKAGE_FAMILY:-} ]]; then
  FAMILY=$TBOT_PACKAGE_FAMILY
elif command -v apt-get >/dev/null 2>&1; then FAMILY=apt
elif command -v pacman >/dev/null 2>&1; then FAMILY=pacman
elif command -v dnf >/dev/null 2>&1; then FAMILY=dnf
elif command -v yum >/dev/null 2>&1; then FAMILY=yum
elif command -v zypper >/dev/null 2>&1; then FAMILY=zypper
elif command -v apk >/dev/null 2>&1; then FAMILY=apk
else
  echo "No supported package manager found (apt, pacman, dnf/yum, zypper, apk)." >&2
  exit 1
fi

echo "Detected distro=${ID:-unknown} family=$FAMILY architecture=$ARCH_RAW docker_platform=linux/$OCI_ARCH"

case "$FAMILY" in
  apt)
    compose_package=docker-compose-v2
    [[ ${ID:-} == "debian" ]] && compose_package=docker-compose
    packages=(ca-certificates curl git python3 python3-venv python3-pip build-essential
      docker.io "$compose_package" pango1.0-tools libpango-1.0-0 libpangoft2-1.0-0
      libharfbuzz-subset0 libjpeg62-turbo postgresql-client)
    [[ $WITH_REDIS_SERVER -eq 1 ]] && packages+=(redis-tools redis-server)
    run "${SUDO[@]}" apt-get update
    run "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
    ;;
  pacman)
    packages=(ca-certificates curl git python python-pip base-devel docker docker-compose
      pango harfbuzz libjpeg-turbo postgresql-libs)
    [[ $WITH_REDIS_SERVER -eq 1 ]] && packages+=(redis)
    run "${SUDO[@]}" pacman -Syu --needed --noconfirm "${packages[@]}"
    ;;
  dnf|yum)
    pm=$FAMILY
    base_packages=(ca-certificates curl git python3 python3-pip gcc gcc-c++ make
      pango harfbuzz libjpeg-turbo postgresql)
    [[ $WITH_REDIS_SERVER -eq 1 ]] && base_packages+=(redis)
    case "${ID:-}" in
      rhel|centos|rocky|almalinux|ol)
        run "${SUDO[@]}" "$pm" install -y dnf-plugins-core "${base_packages[@]}"
        run "${SUDO[@]}" "$pm" config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        run "${SUDO[@]}" "$pm" install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        ;;
      *)
        run "${SUDO[@]}" "$pm" install -y "${base_packages[@]}" moby-engine docker-compose
        ;;
    esac
    ;;
  zypper)
    packages=(ca-certificates curl git python3 python3-pip gcc gcc-c++ make
      docker docker-compose pango-tools harfbuzz libjpeg8 postgresql)
    [[ $WITH_REDIS_SERVER -eq 1 ]] && packages+=(redis)
    run "${SUDO[@]}" zypper --non-interactive refresh
    run "${SUDO[@]}" zypper --non-interactive install --no-recommends "${packages[@]}"
    ;;
  apk)
    packages=(ca-certificates curl git python3 py3-pip py3-virtualenv build-base
      docker docker-cli-compose pango harfbuzz libjpeg-turbo postgresql-client)
    [[ $WITH_REDIS_SERVER -eq 1 ]] && packages+=(redis)
    run "${SUDO[@]}" apk add --no-cache "${packages[@]}"
    ;;
esac

if command -v systemctl >/dev/null 2>&1 || [[ $DRY_RUN -eq 1 ]]; then
  run "${SUDO[@]}" systemctl enable --now docker
elif command -v rc-update >/dev/null 2>&1; then
  run "${SUDO[@]}" rc-update add docker default
  run "${SUDO[@]}" rc-service docker start
fi

if [[ $DRY_RUN -eq 0 ]] && command -v docker >/dev/null 2>&1; then
  if [[ ${EUID:-$(id -u)} -ne 0 ]] && getent group docker >/dev/null 2>&1; then
    run "${SUDO[@]}" usermod -aG docker "$USER"
    echo "Docker group updated. Log out/in once before running Docker without sudo."
  fi
  docker --version
  if docker compose version >/dev/null 2>&1; then docker compose version; fi
fi

echo "System dependency installation completed for $FAMILY/$ARCH_RAW."
