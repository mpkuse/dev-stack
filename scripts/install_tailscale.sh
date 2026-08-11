#!/usr/bin/env bash

set -euo pipefail

readonly KEY_URL="https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg"
readonly REPO_URL="https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list"
readonly KEY_PATH="/usr/share/keyrings/tailscale-archive-keyring.gpg"
readonly REPO_PATH="/etc/apt/sources.list.d/tailscale.list"

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

if ! command -v curl >/dev/null 2>&1; then
  echo "Installing the curl prerequisite from Ubuntu..."
  sudo apt-get update
  sudo apt-get install --yes curl
fi

temp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${temp_dir}"
}
trap cleanup EXIT

echo "Downloading Tailscale's official stable repository configuration..."
curl --fail --silent --show-error --location \
  "${KEY_URL}" \
  --output "${temp_dir}/tailscale-archive-keyring.gpg"
curl --fail --silent --show-error --location \
  "${REPO_URL}" \
  --output "${temp_dir}/tailscale.list"

test -s "${temp_dir}/tailscale-archive-keyring.gpg"
test -s "${temp_dir}/tailscale.list"

echo "Installing the repository and Tailscale package..."
sudo install -d --mode=0755 /usr/share/keyrings /etc/apt/sources.list.d
sudo install --mode=0644 \
  "${temp_dir}/tailscale-archive-keyring.gpg" \
  "${KEY_PATH}"
sudo install --mode=0644 "${temp_dir}/tailscale.list" "${REPO_PATH}"
sudo apt-get update
sudo apt-get install --yes tailscale
sudo systemctl enable --now tailscaled

echo
echo "Tailscale is installed. Follow the authentication URL below:"
sudo tailscale up

echo
echo "Tailscale status:"
tailscale status
