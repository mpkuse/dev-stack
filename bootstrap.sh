#!/usr/bin/env bash

# One-command setup for a fresh Ubuntu 24.04 (Noble) box:
#
#   1. install fresh Ubuntu
#   2. git clone this repository
#   3. ./bootstrap.sh
#
# Order matters. Prerequisites come first because the later steps need curl,
# git and jq. Tailscale must be up and must record an operator before init.sh
# can report Serve readiness, and before any service can publish a route.
#
# init.sh is deliberately NOT modified by this: it still never installs
# packages, clones repositories, or starts services. This script is the only
# place that does, so './init.sh' and './init.sh --check' keep behaving exactly
# as documented.
#
# Every step is idempotent; re-running on a configured box changes nothing.

set -euo pipefail

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
REPOSITORY_ROOT="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
readonly SCRIPTS_DIR="${REPOSITORY_ROOT}/scripts"

PREFIX="${HOME}/.bin"
RUN_PREREQUISITES="true"
RUN_TAILSCALE="true"
RUN_TOOLS="true"
RUN_INIT="true"
RUN_SHELL_ENV="true"
RUN_ADMIN_PASSWORD="true"

usage() {
  cat <<EOF
Usage: ./bootstrap.sh [options]

Sets up a fresh Ubuntu 24.04 host for dev-stack, then installs the checkout.

Options:
  --prefix PATH          Installation prefix passed to init.sh. Default: ${PREFIX}
  --skip-prerequisites   Do not run scripts/install_prerequisites.sh
  --skip-tailscale       Do not install or configure Tailscale
  --skip-tools           Do not run scripts/install_dev_stack_tools.sh
  --no-init              Stop before running ./init.sh
  --skip-shell-env       Do not touch ~/.bashrc or ~/.profile
  --skip-admin-password  Do not prompt for the dashboard master password
  -h, --help             Show this help

Steps:
  1. scripts/install_prerequisites.sh   apt deps, git, zellij, Docker, qrcode
  2. scripts/install_tailscale.sh       only when Tailscale is not yet up
  3. tailscale set --operator           only when no operator is recorded
  4. scripts/install_dev_stack_tools.sh code-server, filebrowser, porterminal
  5. shell environment                  PATH and DEV_STACK_PORTERMINAL_COMMAND in
                                        ~/.bashrc and ~/.profile
  6. ./init.sh --prefix PATH            install the checkout and report readiness
  7. dev_stack admin password reset     only when no master password is set

Requires sudo for steps 1-4. Two steps are interactive: Tailscale prints a URL
to authenticate the machine, and the master password is typed without echo.
Step 6 is skipped automatically when stdin is not a terminal.

No service is started; nothing is configured to start at boot.
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      [[ $# -ge 2 ]] || fail "--prefix requires a path"
      PREFIX="$2"
      shift 2
      ;;
    --skip-prerequisites) RUN_PREREQUISITES="false"; shift ;;
    --skip-tailscale) RUN_TAILSCALE="false"; shift ;;
    --skip-tools) RUN_TOOLS="false"; shift ;;
    --no-init) RUN_INIT="false"; shift ;;
    --skip-shell-env) RUN_SHELL_ENV="false"; shift ;;
    --skip-admin-password) RUN_ADMIN_PASSWORD="false"; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

for required_script in install_prerequisites.sh install_tailscale.sh install_dev_stack_tools.sh; do
  [[ -x "${SCRIPTS_DIR}/${required_script}" ]] \
    || fail "missing or non-executable: ${SCRIPTS_DIR}/${required_script}"
done
[[ -x "${REPOSITORY_ROOT}/init.sh" ]] \
  || fail "missing or non-executable: ${REPOSITORY_ROOT}/init.sh"

step() {
  printf '\n===============================================================\n'
  printf '  %s\n' "$*"
  printf '===============================================================\n'
}

tailscale_is_up() {
  command -v tailscale >/dev/null 2>&1 && tailscale status --json >/dev/null 2>&1
}

# dev_stack resolves Porterminal as a bare command on PATH, and the www dashboard
# inherits the PATH that started it. ~/.local/bin is added only by ~/.profile, so
# an interactive non-login shell never sees it and 'porterminal start' fails with
# "Missing Porterminal command". Warning about it is not enough: a fresh box would
# hit that on the first start, so write the fix into both rc files.
#   ~/.bashrc  interactive shells, login or not
#   ~/.profile login shells, including 'bash -lc'
configure_shell_environment() {
  local rc_file
  local wrote_any="false"

  for rc_file in "${HOME}/.bashrc" "${HOME}/.profile"; do
    [[ -f "${rc_file}" ]] || continue
    if grep --quiet 'DEV_STACK_PORTERMINAL_COMMAND' "${rc_file}"; then
      echo "already configured: ${rc_file}"
      continue
    fi
    cat >> "${rc_file}" <<EOF

# dev-stack: resolve dev_stack and porterminal regardless of shell type.
export DEV_STACK_PORTERMINAL_COMMAND="\${HOME}/.local/bin/porterminal"
case ":\${PATH}:" in
    *":${PREFIX}:"*) ;;
    *) PATH="${PREFIX}:\${PATH}" ;;
esac
case ":\${PATH}:" in
    *":\${HOME}/.local/bin:"*) ;;
    *) PATH="\${HOME}/.local/bin:\${PATH}" ;;
esac
EOF
    echo "updated: ${rc_file}"
    wrote_any="true"
  done

  if [[ "${wrote_any}" == "true" ]]; then
    echo
    echo "New shells pick this up automatically. This bootstrap run exports it"
    echo "itself so the remaining steps and any service it starts inherit it."
  fi

  # Apply to this run too, so step 6's init.sh sees porterminal on PATH.
  export DEV_STACK_PORTERMINAL_COMMAND="${HOME}/.local/bin/porterminal"
  case ":${PATH}:" in
    *":${HOME}/.local/bin:"*) ;;
    *) PATH="${HOME}/.local/bin:${PATH}" ;;
  esac
  export PATH
}

if [[ "${RUN_PREREQUISITES}" == "true" ]]; then
  step "1/7  Prerequisites"
  # A missing Tailscale is reported as [todo] rather than a failure, so this
  # succeeds on a fresh box and any non-zero exit is a genuine problem.
  "${SCRIPTS_DIR}/install_prerequisites.sh"
else
  step "1/7  Prerequisites (skipped)"
fi

if [[ "${RUN_TAILSCALE}" == "true" ]]; then
  if tailscale_is_up; then
    step "2/7  Tailscale (already up, skipping install)"
    tailscale status | head -3 || true
  else
    step "2/7  Tailscale (interactive: authenticate at the printed URL)"
    "${SCRIPTS_DIR}/install_tailscale.sh"
  fi

  # dev-stack writes Serve config on every service start, which needs operator
  # rights. Reading serve status succeeds without them, so this is checked
  # against prefs rather than by probing.
  step "3/7  Tailscale operator"
  if tailscale_is_up; then
    account="$(id --user --name)"
    operator_user="$(tailscale debug prefs 2>/dev/null | jq -r '.OperatorUser // ""')"
    if [[ "${operator_user}" == "${account}" ]]; then
      echo "${account} is already the Tailscale operator."
    else
      echo "Granting Serve config access to ${account}..."
      sudo tailscale set --operator="${account}"
    fi
  else
    echo "Tailscale is not up; skipping the operator grant." >&2
  fi
else
  step "2/7  Tailscale (skipped)"
  step "3/7  Tailscale operator (skipped)"
fi

if [[ "${RUN_TOOLS}" == "true" ]]; then
  step "4/7  dev-stack service tools"
  "${SCRIPTS_DIR}/install_dev_stack_tools.sh"
else
  step "4/7  dev-stack service tools (skipped)"
fi

if [[ "${RUN_SHELL_ENV}" == "true" ]]; then
  step "5/7  Shell environment"
  configure_shell_environment
else
  step "5/7  Shell environment (skipped)"
fi

if [[ "${RUN_INIT}" == "false" ]]; then
  step "6/7  init.sh (skipped)"
  echo
  echo "Bootstrap stopped before install. Run it yourself with:"
  echo "  ${REPOSITORY_ROOT}/init.sh --prefix ${PREFIX}"
  exit 0
fi

step "6/7  Installing the checkout"
# init.sh exit 2 means the files installed but a mandatory check failed, most
# often Tailscale not being up. The install itself is sound, so carry on to the
# password step rather than aborting and silently skipping it, and report the
# unready state at the end.
init_status=0
"${REPOSITORY_ROOT}/init.sh" --prefix "${PREFIX}" || init_status=$?
if (( init_status == 2 )); then
  echo
  echo "init.sh reports the install is not ready yet (exit 2)." >&2
  echo "Continuing; the summary below repeats what is outstanding." >&2
elif (( init_status != 0 )); then
  exit "${init_status}"
fi

# The dashboard refuses to start until a master password exists. The prompt
# reads from a terminal with echo off, so it can only run interactively; a
# piped or cron-driven bootstrap gets the instruction instead of a hang.
step "7/7  Dashboard master password"
if [[ "${RUN_ADMIN_PASSWORD}" != "true" ]]; then
  echo "Skipped. Set it before starting the dashboard:"
  echo "  ${PREFIX}/dev_stack admin password reset"
elif "${PREFIX}/dev_stack" admin status >/dev/null 2>&1; then
  echo "The master password is already configured."
elif [[ -t 0 ]]; then
  echo "dev_stack www refuses to start until this is set."
  "${PREFIX}/dev_stack" admin password reset
else
  echo "Not running on a terminal, so the password prompt was skipped." >&2
  echo "Set it before starting the dashboard:" >&2
  echo "  ${PREFIX}/dev_stack admin password reset" >&2
fi

cat <<EOF

===============================================================
  Bootstrap complete
===============================================================

dev_stack lives at ${PREFIX}/dev_stack

Start the services (none of them start at boot):

  ${PREFIX}/dev_stack filebrowser start
  ${PREFIX}/dev_stack porterminal start
  ${PREFIX}/dev_stack code-server start --instance-name main
  ${PREFIX}/dev_stack www start

Then check everything with:

  ${PREFIX}/dev_stack status

Two things worth knowing:

  - Porterminal is found as a bare 'porterminal' command on PATH, and the www
    dashboard inherits the PATH that started it. ~/.local/bin is added only by
    ~/.profile, so a non-login shell cannot see it. Export the absolute path in
    both ~/.bashrc and ~/.profile:
      export DEV_STACK_PORTERMINAL_COMMAND="\$HOME/.local/bin/porterminal"

  - If you were just added to the docker group, log out and back in (or run
    'newgrp docker') before Docker works without sudo.
EOF

if (( init_status != 0 )); then
  echo
  echo "Note: init.sh exited ${init_status}. dev-stack is installed, but at least one" >&2
  echo "mandatory check is still failing. Re-run to recheck:" >&2
  echo "  ${REPOSITORY_ROOT}/init.sh --check --prefix ${PREFIX}" >&2
  exit "${init_status}"
fi
