#!/usr/bin/env bash

# Installs the per-service tools that dev-stack manages but never installs itself.
#
#   code-server   the Code Server service   -> /usr/bin/code-server
#   filebrowser   the File Browser service  -> /usr/local/bin/filebrowser
#   porterminal   the Porterminal service   -> ~/.local/bin/porterminal
#
# Run ./install_prerequisites.sh first; it provides git, Docker, zellij and the
# mandatory runtime dependencies. With no flags every tool here is installed;
# any flag narrows the run to the tools named.
#
# go2rtc needs no installer of its own: dev_stack runs it as a Docker container,
# and Docker comes from install_prerequisites.sh.

set -euo pipefail

readonly CODE_SERVER_VERSION="${CODE_SERVER_VERSION:-4.115.0}"
readonly FILEBROWSER_VERSION="${FILEBROWSER_VERSION:-2.63.23}"
readonly PORTERMINAL_REPOSITORY="${PORTERMINAL_REPOSITORY:-https://github.com/mpkuse/porterminal.git}"
readonly PORTERMINAL_BRANCH="${PORTERMINAL_BRANCH:-master}"
readonly PORTERMINAL_CHECKOUT="${PORTERMINAL_CHECKOUT:-${HOME}/workspace/porterminal}"
readonly LOCAL_BIN_DIR="${HOME}/.local/bin"
readonly UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

INSTALL_CODE_SERVER="false"
INSTALL_FILEBROWSER="false"
INSTALL_PORTERMINAL="false"
ANY_FLAG_GIVEN="false"

usage() {
  cat <<EOF
Usage: ./install_dev_stack_tools.sh [tool flags]

Tool flags (default: all of them):
  --code-server    Install code-server ${CODE_SERVER_VERSION} (the version dev_stack is tested against)
  --filebrowser    Install File Browser ${FILEBROWSER_VERSION}
  --porterminal    Clone Porterminal, install its launcher, and warm its uv environment
  --all            Explicitly select every tool
  -h, --help       Show this help

Environment overrides:
  CODE_SERVER_VERSION     Default: ${CODE_SERVER_VERSION}
  FILEBROWSER_VERSION     Default: ${FILEBROWSER_VERSION}
  PORTERMINAL_REPOSITORY  Default: ${PORTERMINAL_REPOSITORY}
  PORTERMINAL_BRANCH      Default: ${PORTERMINAL_BRANCH}
  PORTERMINAL_CHECKOUT    Default: ${PORTERMINAL_CHECKOUT}

Already-installed tools are left untouched. Nothing is started; use dev_stack
for that.
EOF
}

select_all() {
  INSTALL_CODE_SERVER="true"
  INSTALL_FILEBROWSER="true"
  INSTALL_PORTERMINAL="true"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --code-server) INSTALL_CODE_SERVER="true"; ANY_FLAG_GIVEN="true"; shift ;;
    --filebrowser) INSTALL_FILEBROWSER="true"; ANY_FLAG_GIVEN="true"; shift ;;
    --porterminal) INSTALL_PORTERMINAL="true"; ANY_FLAG_GIVEN="true"; shift ;;
    --all) select_all; ANY_FLAG_GIVEN="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${ANY_FLAG_GIVEN}" == "false" ]]; then
  select_all
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
  echo "Error: the pinned downloads are amd64 only; this host is ${host_architecture}." >&2
  exit 1
fi

for required_command in curl tar; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "Error: ${required_command} is missing. Run ./install_prerequisites.sh first." >&2
    exit 1
  fi
done

temp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${temp_dir}"
}
trap cleanup EXIT

install_code_server() {
  if command -v code-server >/dev/null 2>&1; then
    echo "code-server is already installed: $(code-server --version | head -1)"
    return
  fi

  local package_name="code-server_${CODE_SERVER_VERSION}_amd64.deb"
  local package_url="https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/${package_name}"

  echo "Downloading code-server ${CODE_SERVER_VERSION}..."
  curl --fail --silent --show-error --location \
    "${package_url}" \
    --output "${temp_dir}/${package_name}"
  test -s "${temp_dir}/${package_name}"

  echo "Installing code-server..."
  sudo apt-get install --yes "${temp_dir}/${package_name}"
}

install_filebrowser() {
  if command -v filebrowser >/dev/null 2>&1; then
    echo "filebrowser is already installed: $(filebrowser version)"
    return
  fi

  local archive_name="linux-amd64-filebrowser.tar.gz"
  local checksums_name="filebrowser_${FILEBROWSER_VERSION}_checksums.txt"
  local release_url="https://github.com/filebrowser/filebrowser/releases/download/v${FILEBROWSER_VERSION}"

  echo "Downloading File Browser ${FILEBROWSER_VERSION}..."
  curl --fail --silent --show-error --location \
    "${release_url}/${archive_name}" \
    --output "${temp_dir}/${archive_name}"
  curl --fail --silent --show-error --location \
    "${release_url}/${checksums_name}" \
    --output "${temp_dir}/${checksums_name}"
  test -s "${temp_dir}/${archive_name}"
  test -s "${temp_dir}/${checksums_name}"

  echo "Verifying the download checksum..."
  ( cd "${temp_dir}" \
    && grep " ${archive_name}\$" "${checksums_name}" > "${archive_name}.sha256" \
    && test -s "${archive_name}.sha256" \
    && sha256sum --check --strict "${archive_name}.sha256" )

  echo "Installing filebrowser..."
  tar --extract --gzip --file "${temp_dir}/${archive_name}" \
    --directory "${temp_dir}" filebrowser
  sudo install --mode=0755 "${temp_dir}/filebrowser" /usr/local/bin/filebrowser
}

install_uv() {
  if [[ -x "${LOCAL_BIN_DIR}/uv" ]]; then
    echo "uv is already installed: $("${LOCAL_BIN_DIR}/uv" --version)"
    return
  fi

  echo "Downloading the uv installer (Porterminal runs through uv)..."
  curl --fail --silent --show-error --location \
    "${UV_INSTALLER_URL}" \
    --output "${temp_dir}/uv-install.sh"
  test -s "${temp_dir}/uv-install.sh"

  echo "Installing uv into ${LOCAL_BIN_DIR}..."
  env UV_INSTALL_DIR="${LOCAL_BIN_DIR}" INSTALLER_NO_MODIFY_PATH=1 \
    sh "${temp_dir}/uv-install.sh"

  if [[ ! -x "${LOCAL_BIN_DIR}/uv" ]]; then
    echo "Error: uv was not installed at ${LOCAL_BIN_DIR}/uv." >&2
    echo "Porterminal's launcher looks for it there." >&2
    exit 1
  fi
}

install_porterminal() {
  if ! command -v git >/dev/null 2>&1; then
    echo "Error: git is missing. Run ./install_prerequisites.sh first." >&2
    exit 1
  fi

  install_uv

  if [[ -e "${PORTERMINAL_CHECKOUT}" ]]; then
    if [[ -d "${PORTERMINAL_CHECKOUT}/.git" ]]; then
      echo "Reusing the existing Porterminal checkout: ${PORTERMINAL_CHECKOUT}"
      echo "It was not updated; run 'git pull' there yourself when you want to."
    else
      echo "Error: ${PORTERMINAL_CHECKOUT} exists but is not a git checkout." >&2
      echo "Inspect and move it, or set PORTERMINAL_CHECKOUT to another path." >&2
      exit 1
    fi
  else
    echo "Cloning Porterminal into ${PORTERMINAL_CHECKOUT}..."
    mkdir --parents "$(dirname "${PORTERMINAL_CHECKOUT}")"
    git clone --branch "${PORTERMINAL_BRANCH}" \
      "${PORTERMINAL_REPOSITORY}" "${PORTERMINAL_CHECKOUT}"
  fi

  echo "Installing the porterminal command..."
  "${PORTERMINAL_CHECKOUT}/install_on_localhost.sh" --bin-dir "${LOCAL_BIN_DIR}"

  # A cold uv cache makes the first launch resolve, download and build ~40
  # packages. That takes long enough that 'dev_stack porterminal start' times out
  # waiting for the port and leaves an unmanaged process behind, so warm it here.
  echo "Warming the Porterminal uv environment (first run downloads dependencies)..."
  ( cd "${PORTERMINAL_CHECKOUT}" \
    && UV_CACHE_DIR="${PORTERMINAL_CHECKOUT}/.uv-cache" \
       UV_PYTHON_INSTALL_DIR="${PORTERMINAL_CHECKOUT}/.uv-python" \
       "${LOCAL_BIN_DIR}/uv" sync )
}

if [[ "${INSTALL_CODE_SERVER}" == "true" ]]; then
  echo
  echo "=== Code Server ==="
  install_code_server
fi

if [[ "${INSTALL_FILEBROWSER}" == "true" ]]; then
  echo
  echo "=== File Browser ==="
  install_filebrowser
fi

if [[ "${INSTALL_PORTERMINAL}" == "true" ]]; then
  echo
  echo "=== Porterminal ==="
  install_porterminal
fi

echo
echo "Tool verification"
exit_status=0

report_tool() {
  local label="$1"
  local command_name="$2"
  local selected="$3"
  if [[ "${selected}" != "true" ]]; then
    printf '  [skipped] %-13s not selected for this run\n' "${label}"
    return
  fi
  if PATH="${LOCAL_BIN_DIR}:${PATH}" command -v "${command_name}" >/dev/null 2>&1; then
    printf '  [ok]      %-13s %s\n' "${label}" \
      "$(PATH="${LOCAL_BIN_DIR}:${PATH}" command -v "${command_name}")"
  else
    printf '  [missing] %-13s %s\n' "${label}" "not on PATH"
    exit_status=1
  fi
}

report_tool "code-server" code-server "${INSTALL_CODE_SERVER}"
report_tool "filebrowser" filebrowser "${INSTALL_FILEBROWSER}"
report_tool "porterminal" porterminal "${INSTALL_PORTERMINAL}"

# dev_stack resolves Porterminal as a bare command on PATH, and the www dashboard
# inherits whatever PATH started it. ~/.local/bin is only added by ~/.profile, so
# a non-login shell (or cron/systemd) cannot see it without the absolute override.
if [[ "${INSTALL_PORTERMINAL}" == "true" ]]; then
  case ":${PATH:-}:" in
    *":${LOCAL_BIN_DIR}:"*)
      printf '  [ok]      %-13s %s\n' "path" "${LOCAL_BIN_DIR} is on PATH"
      ;;
    *)
      printf '  [todo]    %-13s %s\n' "path" "${LOCAL_BIN_DIR} is not on PATH"
      ;;
  esac
  if [[ -n "${DEV_STACK_PORTERMINAL_COMMAND:-}" ]]; then
    printf '  [ok]      %-13s %s\n' "pt override" "${DEV_STACK_PORTERMINAL_COMMAND}"
  else
    printf '  [todo]    %-13s %s\n' "pt override" \
      "add to ~/.bashrc and ~/.profile, then restart dev_stack www:"
    printf '            %s\n' \
      "export DEV_STACK_PORTERMINAL_COMMAND=\"\$HOME/.local/bin/porterminal\""
  fi
fi

if (( exit_status != 0 )); then
  echo
  echo "Error: some tools are still unavailable." >&2
  exit "${exit_status}"
fi

echo
echo "Selected tools are installed."
echo "Recheck dev-stack from the checkout with: ./init.sh --check [--prefix PATH]"
