#!/usr/bin/env bash

# Installs the baseline this host is expected to have:
#   - the mandatory system dependencies dev-stack's init.sh checks for;
#   - git, so the dev-stack and Porterminal checkouts can be fetched and updated;
#   - zellij, used everywhere as the terminal multiplexer;
#   - Docker Engine and the Compose plugin, plus docker-group access;
#   - python3-qrcode, for dev_stack's terminal QR rendering.
#
# Tailscale is also mandatory for dev-stack but is handled by install_tailscale.sh.
# The dev-stack service tools (code-server, File Browser, Porterminal) are
# handled by install_dev_stack_tools.sh.

set -euo pipefail

readonly MINIMUM_PYTHON_MINOR=10
readonly ZELLIJ_VERSION="${ZELLIJ_VERSION:-0.44.3}"

readonly DOCKER_KEY_URL="https://download.docker.com/linux/ubuntu/gpg"
readonly DOCKER_KEY_PATH="/etc/apt/keyrings/docker.asc"
readonly DOCKER_REPO_PATH="/etc/apt/sources.list.d/docker.list"

# Command on PATH -> Ubuntu package that provides it.
declare -rA REQUIRED_COMMANDS=(
  [git]=git
  [python3]=python3
  [jq]=jq
  [curl]=curl
  [openssl]=openssl
  [ss]=iproute2
  [ip]=iproute2
  [setsid]=util-linux
  [pgrep]=procps
  [tar]=tar
)

# Provide no command of their own, so they are checked via dpkg rather than PATH.
#   ca-certificates, gnupg  HTTPS downloads and third-party apt repositories
#   python3-qrcode          dev_stack's terminal QR rendering (imported, not run)
readonly SUPPORTING_PACKAGES=(
  ca-certificates
  gnupg
  python3-qrcode
)

usage() {
  cat <<'USAGE'
Usage: ./install_prerequisites.sh

Installs the baseline dev-stack expects on an Ubuntu 24.04 host: the mandatory
apt runtime dependencies, git, zellij, Docker Engine and the Compose plugin
with docker-group access, and python3-qrcode.

Takes no options; it installs unconditionally and is safe to re-run. Pins are
overridden through the environment instead:
  ZELLIJ_VERSION   zellij release to install

Requires sudo. Tailscale is handled by install_tailscale.sh.
USAGE
}

# This script installs as its only action, so an unrecognised argument must not
# be ignored: a typo would otherwise silently start a full install.
if (( $# > 0 )); then
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *)
      echo "Error: this script takes no options: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Error: cannot identify the operating system." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release

if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "noble" ]]; then
  echo "Error: this installer is intended for Ubuntu 24.04 (Noble)." >&2
  exit 1
fi

host_architecture="$(dpkg --print-architecture)"
if [[ "${host_architecture}" != "amd64" ]]; then
  echo "Error: the pinned zellij download is amd64 only; this host is ${host_architecture}." >&2
  exit 1
fi

temp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${temp_dir}"
}
trap cleanup EXIT

python_is_supported() {
  command -v python3 >/dev/null 2>&1 \
    && python3 -c "import sys; raise SystemExit(sys.version_info < (3, ${MINIMUM_PYTHON_MINOR}))" \
      >/dev/null 2>&1
}

missing_packages=()
missing_commands=()

for command_name in "${!REQUIRED_COMMANDS[@]}"; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    missing_commands+=("${command_name}")
    missing_packages+=("${REQUIRED_COMMANDS[${command_name}]}")
  fi
done

for package in "${SUPPORTING_PACKAGES[@]}"; do
  if ! dpkg-query --show --showformat='${db:Status-Status}\n' "${package}" 2>/dev/null \
    | grep --quiet '^installed$'; then
    missing_packages+=("${package}")
  fi
done

if ! python_is_supported && command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is installed but older than 3.${MINIMUM_PYTHON_MINOR}." >&2
  echo "Upgrade Python before continuing; this script will not replace it." >&2
  exit 1
fi

# zellij is not packaged for Noble, so it comes from the upstream release. The
# published .sha256sum covers the extracted binary, not the tarball.
install_zellij() {
  if command -v zellij >/dev/null 2>&1; then
    echo "zellij is already installed: $(zellij --version)"
    return
  fi

  local archive_name="zellij-x86_64-unknown-linux-musl.tar.gz"
  local checksum_name="zellij-x86_64-unknown-linux-musl.sha256sum"
  local release_url="https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}"

  echo "Downloading zellij ${ZELLIJ_VERSION}..."
  curl --fail --silent --show-error --location \
    "${release_url}/${archive_name}" \
    --output "${temp_dir}/${archive_name}"
  curl --fail --silent --show-error --location \
    "${release_url}/${checksum_name}" \
    --output "${temp_dir}/${checksum_name}"
  test -s "${temp_dir}/${archive_name}"
  test -s "${temp_dir}/${checksum_name}"

  tar --extract --gzip --file "${temp_dir}/${archive_name}" \
    --directory "${temp_dir}" zellij

  echo "Verifying the zellij binary checksum..."
  local expected_digest actual_digest
  expected_digest="$(awk '{print $1}' "${temp_dir}/${checksum_name}")"
  actual_digest="$(sha256sum "${temp_dir}/zellij" | awk '{print $1}')"
  if [[ -z "${expected_digest}" || "${expected_digest}" != "${actual_digest}" ]]; then
    echo "Error: zellij checksum mismatch." >&2
    echo "  expected: ${expected_digest:-<empty>}" >&2
    echo "  actual:   ${actual_digest}" >&2
    exit 1
  fi

  echo "Installing zellij..."
  sudo install --mode=0755 "${temp_dir}/zellij" /usr/local/bin/zellij
}

install_docker_packages() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    echo "Docker and the Compose plugin are already installed: $(docker --version)"
    return
  fi

  echo "Downloading Docker's official repository key..."
  curl --fail --silent --show-error --location \
    "${DOCKER_KEY_URL}" \
    --output "${temp_dir}/docker.asc"
  test -s "${temp_dir}/docker.asc"

  echo "Installing the repository and Docker packages..."
  sudo install --directory --mode=0755 /etc/apt/keyrings
  sudo install --mode=0644 "${temp_dir}/docker.asc" "${DOCKER_KEY_PATH}"
  printf 'deb [arch=%s signed-by=%s] https://download.docker.com/linux/ubuntu %s stable\n' \
    "${host_architecture}" "${DOCKER_KEY_PATH}" "${VERSION_CODENAME}" \
    > "${temp_dir}/docker.list"
  sudo install --mode=0644 "${temp_dir}/docker.list" "${DOCKER_REPO_PATH}"
  sudo apt-get update
  sudo apt-get install --yes \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
  # Guard on systemd being the init system: the packages install fine inside a
  # container, where systemctl has nothing to talk to.
  if [[ -d /run/systemd/system ]]; then
    sudo systemctl enable --now docker
  else
    echo "systemd is not the init system; skipping 'systemctl enable --now docker'."
    echo "Start the daemon yourself in this environment."
  fi
}

# Group membership is handled separately from package installation so that a
# host with Docker already present still gets its group checked.
configure_docker_group() {
  local account
  account="$(id --user --name)"

  if ! getent group docker >/dev/null 2>&1; then
    echo "Creating the docker group..."
    sudo groupadd docker
  fi

  # 'id -nG <user>' reads the group database, so it sees a previous usermod even
  # when the current session's own credentials are stale.
  if id --name --groups "${account}" | tr ' ' '\n' | grep --quiet '^docker$'; then
    echo "${account} is already in the docker group."
  else
    echo "Adding ${account} to the docker group..."
    sudo usermod --append --groups docker "${account}"
  fi

  # usermod never changes an already-running session's credentials.
  if docker info >/dev/null 2>&1; then
    echo "The docker daemon is reachable as ${account}."
  else
    echo "The docker group is set, but this session still holds its old credentials."
    echo "Log out and back in (or run 'newgrp docker') before Docker works without sudo."
  fi
}

if (( ${#missing_packages[@]} == 0 )); then
  echo "All apt-provided prerequisites are already installed."
else
  if (( ${#missing_commands[@]} > 0 )); then
    echo "Missing commands: ${missing_commands[*]}"
  fi
  # Deduplicate; iproute2 provides both ss and ip.
  mapfile -t install_packages < <(printf '%s\n' "${missing_packages[@]}" | sort --unique)
  echo "Installing packages: ${install_packages[*]}"
  sudo apt-get update
  sudo apt-get install --yes "${install_packages[@]}"
fi

echo
echo "=== zellij ==="
install_zellij

echo
echo "=== Docker ==="
install_docker_packages
configure_docker_group

echo
echo "Prerequisite verification"
exit_status=0

for command_name in $(printf '%s\n' bash zellij docker "${!REQUIRED_COMMANDS[@]}" | sort); do
  if command -v "${command_name}" >/dev/null 2>&1; then
    printf '  [ok]      %-14s %s\n' "${command_name}" "$(command -v "${command_name}")"
  else
    printf '  [missing] %-14s %s\n' "${command_name}" "still not on PATH"
    exit_status=1
  fi
done

if python_is_supported; then
  printf '  [ok]      %-14s %s\n' "python" "$(python3 --version)"
else
  printf '  [missing] %-14s %s\n' "python" "3.${MINIMUM_PYTHON_MINOR} or newer is required"
  exit_status=1
fi

# dev_stack imports this rather than invoking a command, so check importability
# with the same interpreter dev_stack uses. Read the version from package
# metadata: the Debian build exposes no qrcode.__version__ attribute.
if python3 -c 'import qrcode' >/dev/null 2>&1; then
  qrcode_version="$(
    python3 -c 'import importlib.metadata; print(importlib.metadata.version("qrcode"))' \
      2>/dev/null
  )" || qrcode_version=""
  printf '  [ok]      %-14s %s\n' "python qrcode" "${qrcode_version:-version unknown}"
else
  printf '  [missing] %-14s %s\n' "python qrcode" "import qrcode failed"
  exit_status=1
fi

if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    printf '  [ok]      %-14s %s\n' "docker compose" "$(docker compose version --short)"
  else
    printf '  [missing] %-14s %s\n' "docker compose" "Compose plugin"
    exit_status=1
  fi
  if docker info >/dev/null 2>&1; then
    printf '  [ok]      %-14s %s\n' "docker daemon" "reachable without sudo"
  else
    printf '  [pending] %-14s %s\n' "docker daemon" \
      "not reachable yet; re-login or 'newgrp docker'"
  fi
fi

if command -v tailscale >/dev/null 2>&1 && tailscale status --json >/dev/null 2>&1; then
  printf '  [ok]      %-14s %s\n' "tailscale" "daemon is reachable"
else
  printf '  [todo]    %-14s %s\n' "tailscale" "run ./install_tailscale.sh (mandatory for dev-stack)"
fi

# dev-stack writes Tailscale Serve config on every service start. Writing needs
# operator rights or root, but *reading* serve status succeeds without them, so
# init.sh's readiness check passes on hosts where every publish will fail.
# Read the recorded operator from prefs; probing serve status cannot detect this.
if command -v tailscale >/dev/null 2>&1 && tailscale status --json >/dev/null 2>&1; then
  account="$(id --user --name)"
  operator_user="$(tailscale debug prefs 2>/dev/null | jq -r '.OperatorUser // ""')"
  if [[ "${operator_user}" == "${account}" ]]; then
    printf '  [ok]      %-14s %s\n' "ts operator" "${account} can write Serve config"
  else
    printf '  [todo]    %-14s %s\n' "ts operator" \
      "run: sudo tailscale set --operator=${account}"
    printf '            %s\n' \
      "without it, every dev_stack service start fails to publish its route"
  fi
fi

if (( exit_status != 0 )); then
  echo
  echo "Error: some prerequisites are still unsatisfied." >&2
  exit "${exit_status}"
fi

echo
echo "Prerequisites are satisfied."
echo "Next: ./install_dev_stack_tools.sh for the dev-stack service tools."
