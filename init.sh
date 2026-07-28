#!/usr/bin/env bash
set -euo pipefail

umask 077

SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
SOURCE_ROOT="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
PREFIX="${HOME}/.bin"
CHECK_ONLY="false"

CODE_SERVER_CHOICE=""
FILEBROWSER_CHOICE=""
PORTERMINAL_CHOICE=""
GO2RTC_CHOICE=""
WWW_CHOICE=""

usage() {
  cat <<'EOF'
Install or check dev-stack without downloading or starting anything.

Usage:
  ./init.sh [--prefix PATH] [service flags]
  ./init.sh --check [--prefix PATH]

Options:
  --prefix PATH          Installation prefix. Default: $HOME/.bin
  --check                Check an existing installation and its dependencies only.
  --code-server          Enable Code Server.
  --no-code-server       Disable Code Server.
  --filebrowser          Enable File Browser.
  --no-filebrowser       Disable File Browser.
  --porterminal          Enable Porterminal.
  --no-porterminal       Disable Porterminal.
  --go2rtc               Enable go2rtc.
  --no-go2rtc            Disable go2rtc.
  --www                  Enable the dev-stack web dashboard.
  --no-www               Disable the dev-stack web dashboard.
  -h, --help             Show this help.

Fresh installations enable every optional service. On an existing installation,
omitted service flags preserve the current settings.

This script never installs packages, clones repositories, starts services,
stops services, removes an existing installation/runtime tree, or upgrades a
changed code copy.
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

set_choice() {
  local variable_name="$1"
  local value="$2"
  printf -v "$variable_name" '%s' "$value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix)
      [[ $# -ge 2 ]] || fail "--prefix requires a path"
      PREFIX="$2"
      shift 2
      ;;
    --check)
      CHECK_ONLY="true"
      shift
      ;;
    --code-server) set_choice CODE_SERVER_CHOICE true; shift ;;
    --no-code-server) set_choice CODE_SERVER_CHOICE false; shift ;;
    --filebrowser) set_choice FILEBROWSER_CHOICE true; shift ;;
    --no-filebrowser) set_choice FILEBROWSER_CHOICE false; shift ;;
    --porterminal) set_choice PORTERMINAL_CHOICE true; shift ;;
    --no-porterminal) set_choice PORTERMINAL_CHOICE false; shift ;;
    --go2rtc) set_choice GO2RTC_CHOICE true; shift ;;
    --no-go2rtc) set_choice GO2RTC_CHOICE false; shift ;;
    --www) set_choice WWW_CHOICE true; shift ;;
    --no-www) set_choice WWW_CHOICE false; shift ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

if [[ "$CHECK_ONLY" == "true" ]] \
  && [[ -n "${CODE_SERVER_CHOICE}${FILEBROWSER_CHOICE}${PORTERMINAL_CHOICE}${GO2RTC_CHOICE}${WWW_CHOICE}" ]]; then
  fail "--check is read-only and cannot be combined with service enable/disable flags"
fi

if [[ "$PREFIX" != /* ]]; then
  PREFIX="$(pwd -P)/${PREFIX}"
fi
PREFIX="$(readlink -m -- "$PREFIX")"

INSTALL_ROOT="${PREFIX}/src/dev-stack"
COMMAND_PATH="${PREFIX}/dev_stack"
RUNTIME_HOME="${PREFIX}/.dev-stack"
SETTINGS_FILE="${RUNTIME_HOME}/config/settings.json"
MANIFEST_FILE="${INSTALL_ROOT}/.dev-stack-manifest"

REQUIRED_REPOSITORY_PATHS=(
  dev_stack
  backend
  frontend
  config
  tests
  README.md
  TODO.md
  init.sh
)

validate_source() {
  local relative_path
  for relative_path in "${REQUIRED_REPOSITORY_PATHS[@]}"; do
    [[ -e "${SOURCE_ROOT}/${relative_path}" ]] \
      || fail "source repository is incomplete; missing ${SOURCE_ROOT}/${relative_path}"
  done
  [[ -x "${SOURCE_ROOT}/dev_stack" ]] \
    || fail "source CLI is not executable: ${SOURCE_ROOT}/dev_stack"
}

python_is_supported() {
  command -v python3 >/dev/null 2>&1 \
    && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

content_fingerprint() {
  local root="$1"
  python3 - "$root" <<'PY'
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
excluded_names = {
    ".dev-stack-manifest",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}

paths = []
for path in root.rglob("*"):
    relative = path.relative_to(root)
    if any(part in excluded_names for part in relative.parts):
        continue
    if path.is_file() and path.suffix != ".pyc":
        paths.append(relative)

digest = hashlib.sha256()
for relative in sorted(paths, key=lambda item: item.as_posix()):
    path = root / relative
    digest.update(relative.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
    digest.update(b"x" if os.access(path, os.X_OK) else b"-")
    digest.update(b"\0")
print(digest.hexdigest())
PY
}

read_manifest_fingerprint() {
  local manifest="$1"
  python3 - "$manifest" <<'PY'
import json
import sys
from pathlib import Path

try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    fingerprint = value["content_sha256"]
    if value.get("version") != 1 or not isinstance(fingerprint, str):
        raise ValueError
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
print(fingerprint)
PY
}

write_manifest() {
  local path="$1"
  local fingerprint="$2"
  python3 - "$path" "$fingerprint" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "version": 1,
    "content_sha256": sys.argv[2],
}
path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
PY
  chmod 600 "$path"
}

resolved_link_target() {
  [[ -L "$COMMAND_PATH" ]] || return 1
  readlink -f -- "$COMMAND_PATH"
}

print_conflict_help() {
  cat >&2 <<EOF

An installation or unrelated files already occupy the target paths.
Nothing was removed. To reinstall, manually move or delete only the paths you
have inspected:
  code:    ${INSTALL_ROOT}
  command: ${COMMAND_PATH}

Runtime data is separate and was not changed:
  runtime: ${RUNTIME_HOME}
EOF
}

validate_existing_installation() {
  local source_fingerprint="$1"
  local installed_fingerprint
  local recorded_fingerprint

  if [[ ! -f "$MANIFEST_FILE" ]] \
    || [[ ! -x "${INSTALL_ROOT}/dev_stack" ]] \
    || [[ ! -x "${INSTALL_ROOT}/init.sh" ]] \
    || [[ ! -d "${INSTALL_ROOT}/backend" ]] \
    || [[ ! -d "${INSTALL_ROOT}/frontend" ]] \
    || [[ ! -d "${INSTALL_ROOT}/config" ]]; then
    print_conflict_help
    fail "the target code directory is not a valid managed dev-stack installation"
  fi

  recorded_fingerprint="$(read_manifest_fingerprint "$MANIFEST_FILE" || true)"
  [[ -n "$recorded_fingerprint" ]] || {
    print_conflict_help
    fail "the installation manifest is invalid: $MANIFEST_FILE"
  }
  installed_fingerprint="$(content_fingerprint "$INSTALL_ROOT")"
  if [[ "$installed_fingerprint" != "$recorded_fingerprint" ]]; then
    print_conflict_help
    fail "the installed code has local changes; this installer will not overwrite them"
  fi
  if [[ "$source_fingerprint" != "$recorded_fingerprint" ]]; then
    print_conflict_help
    fail "the source and installed code differ; this installer does not perform upgrades"
  fi
}

install_code_copy() {
  local source_fingerprint="$1"
  local stage_root

  mkdir -p -- "${PREFIX}/src"
  stage_root="$(mktemp -d "${PREFIX}/src/.dev-stack-stage.XXXXXXXX")"
  trap 'if [[ -n "${stage_root:-}" && -d "$stage_root" ]]; then rm -rf -- "$stage_root"; fi' EXIT
  mkdir -p -- "${stage_root}/dev-stack"

  cp -a -- "${SOURCE_ROOT}/." "${stage_root}/dev-stack/"

  find "${stage_root}/dev-stack" -type d \
    \( -name .git -o -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name .venv -o -name node_modules \) \
    -prune -exec rm -rf -- {} +
  find "${stage_root}/dev-stack" -type f -name '*.pyc' -delete
  rm -f -- "${stage_root}/dev-stack/.dev-stack-manifest"
  write_manifest "${stage_root}/dev-stack/.dev-stack-manifest" "$source_fingerprint"
  mv -- "${stage_root}/dev-stack" "$INSTALL_ROOT"
  rmdir -- "$stage_root"
  stage_root=""
  trap - EXIT
}

create_runtime_layout() {
  local directory
  for directory in config state data secrets logs; do
    mkdir -p -- "${RUNTIME_HOME}/${directory}"
    chmod 700 "${RUNTIME_HOME}/${directory}"
  done
  chmod 700 "$RUNTIME_HOME"
}

settings_are_valid() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  python3 - "$path" <<'PY'
import json
import sys
from pathlib import Path

try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if value.get("version") != 1 or not isinstance(value.get("services"), dict):
        raise ValueError
    for service in ("code-server", "filebrowser", "porterminal", "go2rtc", "www"):
        record = value["services"].get(service)
        if not isinstance(record, dict) or not isinstance(record.get("enabled"), bool):
            raise ValueError
    go2rtc = value["host"]["go2rtc"]
    for key in ("stream_env_file", "video_device", "sound_device"):
        if not isinstance(go2rtc.get(key), str) or not go2rtc[key]:
            raise ValueError
except (AttributeError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}

write_settings() {
  local fresh_install="$1"
  python3 - \
    "$SETTINGS_FILE" \
    "$fresh_install" \
    "$CODE_SERVER_CHOICE" \
    "$FILEBROWSER_CHOICE" \
    "$PORTERMINAL_CHOICE" \
    "$GO2RTC_CHOICE" \
    "$WWW_CHOICE" \
    "${HOME}/workspace/iap_rosws/.env" \
    "/dev/v4l/by-id/usb-046d_Logitech_BRIO_67EAF98D-video-index0" \
    "/dev/snd" <<'PY'
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

(
    settings_name,
    fresh_text,
    code_server,
    filebrowser,
    porterminal,
    go2rtc,
    www,
    stream_env_file,
    video_device,
    sound_device,
) = sys.argv[1:]
settings_path = Path(settings_name)
fresh = fresh_text == "true"

if settings_path.exists():
    try:
        value = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Invalid existing settings file {settings_path}: {error}")
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SystemExit(f"Invalid existing settings file {settings_path}")
else:
    if not fresh:
        raise SystemExit(f"Missing settings file for existing installation: {settings_path}")
    value = {
        "version": 1,
        "services": {},
        "host": {
            "go2rtc": {
                "stream_env_file": stream_env_file,
                "video_device": video_device,
                "sound_device": sound_device,
            }
        },
    }

services = value.setdefault("services", {})
if not isinstance(services, dict):
    raise SystemExit(f"Invalid services object in {settings_path}")
host = value.setdefault("host", {})
if not isinstance(host, dict):
    raise SystemExit(f"Invalid host settings in {settings_path}")
go2rtc_host = host.setdefault("go2rtc", {})
if not isinstance(go2rtc_host, dict):
    raise SystemExit(f"Invalid go2rtc host settings in {settings_path}")
host_defaults = {
    "stream_env_file": stream_env_file,
    "video_device": video_device,
    "sound_device": sound_device,
}
for key, default in host_defaults.items():
    if key not in go2rtc_host:
        go2rtc_host[key] = default
    elif not isinstance(go2rtc_host[key], str) or not go2rtc_host[key]:
        raise SystemExit(f"Invalid go2rtc host setting {key}")

choices = {
    "code-server": code_server,
    "filebrowser": filebrowser,
    "porterminal": porterminal,
    "go2rtc": go2rtc,
    "www": www,
}
for service, choice in choices.items():
    existing = services.get(service)
    if existing is None:
        existing = {}
        services[service] = existing
    if not isinstance(existing, dict):
        raise SystemExit(f"Invalid settings for service {service}")
    if choice:
        existing["enabled"] = choice == "true"
    elif "enabled" not in existing:
        existing["enabled"] = True
    elif not isinstance(existing["enabled"], bool):
        raise SystemExit(f"Invalid enabled setting for service {service}")

settings_path.parent.mkdir(parents=True, exist_ok=True)
descriptor, temporary_name = tempfile.mkstemp(
    dir=settings_path.parent,
    prefix=f".{settings_path.name}.",
    text=True,
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, settings_path)
finally:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
PY
}

service_enabled() {
  python3 - "$SETTINGS_FILE" "$1" <<'PY'
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
enabled = value["services"][sys.argv[2]]["enabled"]
raise SystemExit(0 if enabled is True else 1)
PY
}

MANDATORY_MISSING=()
OPTIONAL_MISSING=()
READINESS_PROBLEMS=()

record_command() {
  local category="$1"
  local command_name="$2"
  local description="$3"
  if command -v "$command_name" >/dev/null 2>&1; then
    printf '  [ok]      %-18s %s\n' "$command_name" "$description"
  else
    printf '  [missing] %-18s %s\n' "$command_name" "$description"
    if [[ "$category" == "mandatory" ]]; then
      MANDATORY_MISSING+=("$command_name")
    else
      OPTIONAL_MISSING+=("$command_name")
    fi
  fi
}

record_path() {
  local label="$1"
  local path="$2"
  local required_type="$3"
  local ok="false"
  case "$required_type" in
    executable) [[ -x "$path" ]] && ok="true" ;;
    directory) [[ -d "$path" ]] && ok="true" ;;
    file) [[ -f "$path" ]] && ok="true" ;;
    path) [[ -e "$path" ]] && ok="true" ;;
  esac
  if [[ "$ok" == "true" ]]; then
    printf '  [ok]      %-18s %s\n' "$label" "$path"
  else
    printf '  [missing] %-18s %s\n' "$label" "$path"
    OPTIONAL_MISSING+=("${label}: ${path}")
  fi
}

run_dependency_checks() {
  printf '\nMandatory runtime dependencies\n'
  record_command mandatory bash "shell runtime"
  if python_is_supported; then
    printf '  [ok]      %-18s %s\n' "python3" "Python 3.10+; dev-stack uses only the standard library"
  else
    printf '  [missing] %-18s %s\n' "python3" "Python 3.10 or newer is required"
    MANDATORY_MISSING+=("python3 >= 3.10")
  fi
  record_command mandatory jq "JSON state handling"
  record_command mandatory curl "local health checks"
  record_command mandatory openssl "credential generation"
  record_command mandatory ss "listener inspection (iproute2)"
  record_command mandatory ip "network interface inspection (iproute2)"
  record_command mandatory setsid "background process launch (util-linux)"
  record_command mandatory pgrep "process inspection (procps)"
  record_command mandatory tailscale "private network and Serve routing"

  printf '\nMandatory Tailscale readiness\n'
  if command -v tailscale >/dev/null 2>&1; then
    if tailscale status --json >/dev/null 2>&1; then
      printf '  [ok]      tailscale status   local daemon is reachable\n'
    else
      printf '  [not ready] tailscale status local daemon is not reachable or not authenticated\n'
      READINESS_PROBLEMS+=("Tailscale is not reachable or authenticated")
    fi
    if tailscale serve status --json >/dev/null 2>&1; then
      printf '  [ok]      tailscale serve    Serve status is readable\n'
    else
      printf '  [not ready] tailscale serve  Serve status is not readable\n'
      READINESS_PROBLEMS+=("Tailscale Serve status is not readable")
    fi
  else
    printf '  [blocked] tailscale checks   command is missing\n'
  fi

  printf '\nEnabled service dependencies\n'
  if service_enabled code-server; then
    record_command optional code-server "enabled Code Server service"
  else
    printf '  [disabled] code-server\n'
  fi
  if service_enabled filebrowser; then
    record_command optional filebrowser "enabled File Browser service"
  else
    printf '  [disabled] filebrowser\n'
  fi
  if service_enabled porterminal; then
    record_command optional porterminal "enabled Porterminal service"
  else
    printf '  [disabled] porterminal\n'
  fi
  if service_enabled go2rtc; then
    record_command optional docker "enabled go2rtc service"
    if command -v docker >/dev/null 2>&1; then
      if docker compose version >/dev/null 2>&1; then
        printf '  [ok]      %-18s %s\n' "docker compose" "Compose plugin"
      else
        printf '  [missing] %-18s %s\n' "docker compose" "Compose plugin"
        OPTIONAL_MISSING+=("docker compose")
      fi
    fi
    record_path "go2rtc env" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["host"]["go2rtc"]["stream_env_file"])' "$SETTINGS_FILE")" file
    record_path "go2rtc video" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["host"]["go2rtc"]["video_device"])' "$SETTINGS_FILE")" path
    record_path "go2rtc sound" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["host"]["go2rtc"]["sound_device"])' "$SETTINGS_FILE")" path
  else
    printf '  [disabled] go2rtc\n'
  fi
  if service_enabled www; then
    printf '  [ok]      %-18s %s\n' "www" "uses the mandatory Python runtime"
  else
    printf '  [disabled] www\n'
  fi

  printf '\nOptional diagnostics\n'
  record_command optional nmcli "inactive NetworkManager connection details"
  printf '  [optional] %-18s %s\n' "python qrcode" "terminal-only QR rendering; browser QR has no Python dependency"
}

print_next_steps() {
  local admin_helper="${INSTALL_ROOT}/backend/admin_auth.py"
  local admin_state="${RUNTIME_HOME}/state/state-admin.json"
  local filebrowser_db="${RUNTIME_HOME}/data/filebrowser.db"
  local admin_missing="false"
  local filebrowser_credential_missing="false"

  if service_enabled www \
    && [[ -f "$admin_helper" ]] \
    && ! python3 "$admin_helper" status --state-file "$admin_state" >/dev/null 2>&1; then
    admin_missing="true"
  fi
  if service_enabled filebrowser \
    && ! "$COMMAND_PATH" credentials show --service filebrowser --json >/dev/null 2>&1; then
    filebrowser_credential_missing="true"
  fi

  if [[ "$admin_missing" == "false" && "$filebrowser_credential_missing" == "false" ]]; then
    return
  fi

  printf '\nNext steps\n'
  if [[ "$admin_missing" == "true" ]]; then
    printf '  The dashboard master password is not configured.\n'
    printf '  Run: %s admin password reset\n' "$COMMAND_PATH"
  fi
  if [[ "$filebrowser_credential_missing" == "true" ]]; then
    if [[ -s "$filebrowser_db" ]]; then
      printf '  The File Browser admin password is not saved.\n'
      printf '  Run: %s filebrowser password reset\n' "$COMMAND_PATH"
    else
      printf '  File Browser has no database or saved admin password yet.\n'
      printf '  Run: %s filebrowser start\n' "$COMMAND_PATH"
      printf '  The first start creates the database and saves its generated password.\n'
      printf '  To rotate it afterward, run: %s filebrowser password reset\n' "$COMMAND_PATH"
    fi
  fi
}

validate_source

if ! python_is_supported; then
  printf 'Mandatory runtime dependency missing: Python 3.10 or newer.\n' >&2
  printf 'No files were changed.\n' >&2
  exit 1
fi

SOURCE_FINGERPRINT="$(content_fingerprint "$SOURCE_ROOT")"
SOURCE_IS_INSTALL="false"
if [[ "$(readlink -f -- "$SOURCE_ROOT")" == "$(readlink -m -- "$INSTALL_ROOT")" ]]; then
  SOURCE_IS_INSTALL="true"
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
  [[ -d "$INSTALL_ROOT" ]] || fail "no installation exists at $INSTALL_ROOT"
  [[ -f "$SETTINGS_FILE" ]] || fail "missing runtime settings: $SETTINGS_FILE"
  validate_existing_installation "$SOURCE_FINGERPRINT"
  [[ "$(resolved_link_target || true)" == "${INSTALL_ROOT}/dev_stack" ]] \
    || fail "the command symlink is missing or points elsewhere: $COMMAND_PATH"
  printf 'Installation layout\n'
  printf '  [ok]      code               %s\n' "$INSTALL_ROOT"
  printf '  [ok]      command            %s -> %s\n' "$COMMAND_PATH" "${INSTALL_ROOT}/dev_stack"
  printf '  [ok]      runtime            %s\n' "$RUNTIME_HOME"
  run_dependency_checks
elif [[ "$SOURCE_IS_INSTALL" == "true" ]]; then
  if [[ -e "$COMMAND_PATH" || -L "$COMMAND_PATH" ]]; then
    existing_target="$(resolved_link_target || true)"
    if [[ "$existing_target" != "${INSTALL_ROOT}/dev_stack" ]]; then
      print_conflict_help
      fail "the command path exists and is not this installation's symlink"
    fi
  fi
  if [[ -f "$MANIFEST_FILE" ]]; then
    validate_existing_installation "$SOURCE_FINGERPRINT"
    FRESH_INSTALL="false"
  else
    if [[ -e "$RUNTIME_HOME" ]]; then
      settings_are_valid "$SETTINGS_FILE" || {
        print_conflict_help
        fail "runtime data exists without valid managed settings"
      }
      FRESH_INSTALL="false"
    else
      FRESH_INSTALL="true"
    fi
    write_manifest "$MANIFEST_FILE" "$SOURCE_FINGERPRINT"
  fi
  if [[ -e "$RUNTIME_HOME" && ! -d "$RUNTIME_HOME" ]]; then
    print_conflict_help
    fail "the runtime path exists but is not a directory"
  fi
  if [[ "$FRESH_INSTALL" == "false" ]] && ! settings_are_valid "$SETTINGS_FILE"; then
    print_conflict_help
    fail "the existing installation has no valid runtime settings"
  fi
  create_runtime_layout
  write_settings "$FRESH_INSTALL"
  if [[ ! -L "$COMMAND_PATH" ]]; then
    ln -s -- "${INSTALL_ROOT}/dev_stack" "$COMMAND_PATH"
  fi
  printf 'Installation ready\n'
  printf '  code:    %s\n' "$INSTALL_ROOT"
  printf '  command: %s -> %s\n' "$COMMAND_PATH" "${INSTALL_ROOT}/dev_stack"
  printf '  runtime: %s\n' "$RUNTIME_HOME"
  run_dependency_checks
else
  if [[ -e "$COMMAND_PATH" || -L "$COMMAND_PATH" ]]; then
    existing_target="$(resolved_link_target || true)"
    if [[ "$existing_target" != "${INSTALL_ROOT}/dev_stack" ]]; then
      print_conflict_help
      fail "the command path exists and is not this installation's symlink"
    fi
  fi

  if [[ -e "$INSTALL_ROOT" ]]; then
    validate_existing_installation "$SOURCE_FINGERPRINT"
    FRESH_INSTALL="false"
  else
    if [[ -e "$RUNTIME_HOME" ]]; then
      settings_are_valid "$SETTINGS_FILE" || {
        print_conflict_help
        fail "runtime data exists without valid managed settings"
      }
      FRESH_INSTALL="false"
    else
      FRESH_INSTALL="true"
    fi
    install_code_copy "$SOURCE_FINGERPRINT"
  fi

  if [[ "$FRESH_INSTALL" == "false" ]] && ! settings_are_valid "$SETTINGS_FILE"; then
    print_conflict_help
    fail "the existing installation has no valid runtime settings"
  fi
  create_runtime_layout
  write_settings "$FRESH_INSTALL"
  if [[ ! -L "$COMMAND_PATH" ]]; then
    ln -s -- "${INSTALL_ROOT}/dev_stack" "$COMMAND_PATH"
  fi
  printf 'Installation ready\n'
  printf '  code:    %s\n' "$INSTALL_ROOT"
  printf '  command: %s -> %s\n' "$COMMAND_PATH" "${INSTALL_ROOT}/dev_stack"
  printf '  runtime: %s\n' "$RUNTIME_HOME"
  run_dependency_checks
fi

print_next_steps

if (( ${#MANDATORY_MISSING[@]} > 0 || ${#READINESS_PROBLEMS[@]} > 0 )); then
  printf '\ndev-stack is installed but not ready: mandatory checks failed.\n' >&2
  exit 2
fi
if (( ${#OPTIONAL_MISSING[@]} > 0 )); then
  printf '\ndev-stack core is ready; one or more optional features are unavailable.\n'
  exit 0
fi
printf '\ndev-stack is installed and all enabled checks passed.\n'
