#!/usr/bin/env python3
"""Build the read-only JSON snapshot exposed by ``dev_stack status --json``."""

from __future__ import annotations

import json
import getpass
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
MANAGED_SERVICE_IDS = ("code-server", "filebrowser", "porterminal", "go2rtc")
DEFAULT_TIMEOUT_SECONDS = 3.0
VIRTUAL_INTERFACE_NAME = re.compile(
    r"^(?:br-|bridge|cni|docker|dummy|lo$|podman|tailscale|tap|tun|veth|virbr|vmnet|wg)",
    re.IGNORECASE,
)
PHYSICAL_INTERFACE_NAME = re.compile(r"^(?:en|eth|wl)", re.IGNORECASE)
SERVICE_STATES = {"running", "stopped", "drifted", "not_found", "unknown"}
HEALTH_STATES = SERVICE_STATES | {"healthy", "degraded"}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Execute fixed argument arrays with a short timeout."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def run(self, argv: Sequence[str]) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={**os.environ, "NO_COLOR": "1"},
            )
        except (FileNotFoundError, PermissionError) as error:
            return CommandResult(127, "", str(error))
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            return CommandResult(124, stdout, stderr or f"timed out after {self.timeout:g}s")
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "instances": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "instances": {}}
    if not isinstance(value, dict) or not isinstance(value.get("instances"), dict):
        return {"version": 1, "instances": {}}
    return value


def _as_string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_port(value: Any, default: int | None = None) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return default
    return port if 0 < port <= 65535 else default


def _as_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return default


def _process_alive(pid: int | None, expected_marker: str = "") -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        pass

    if not expected_marker:
        return True
    try:
        command_line = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return True
    return expected_marker in command_line


def _uptime_seconds(started_at: Any, now: datetime) -> int | None:
    if not isinstance(started_at, str) or not started_at:
        return None
    try:
        parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((now - parsed.astimezone(now.tzinfo)).total_seconds()))


def _tailnet_url(dns_name: str, path: str) -> str | None:
    host = dns_name.rstrip(".")
    if not host:
        return None
    normalized_path = path if path.startswith("/") else f"/{path}"
    if normalized_path == "/":
        return f"https://{host}/"
    return f"https://{host}{normalized_path.rstrip('/')}/"


def _normalize_proxy(value: Any) -> str:
    return _as_string(value).rstrip("/")


def _route_map(serve_status: Mapping[str, Any]) -> dict[str, str]:
    routes: dict[str, str] = {}
    web = serve_status.get("Web")
    if not isinstance(web, dict):
        return routes
    for host_config in web.values():
        if not isinstance(host_config, dict):
            continue
        handlers = host_config.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        for path, handler in handlers.items():
            if isinstance(path, str) and isinstance(handler, dict):
                proxy = handler.get("Proxy")
                if isinstance(proxy, str):
                    routes[path.rstrip("/") or "/"] = proxy
    return routes


def _split_nmcli_terse(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line.rstrip("\n"):
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _address_values(value: str) -> list[str]:
    addresses: list[str] = []
    for part in value.replace(";", ",").replace("\n", ",").split(","):
        address = part.strip()
        if address and not address.startswith("127.") and ":" not in address:
            addresses.append(address)
    return addresses


def _read_enabled_services(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set(MANAGED_SERVICE_IDS)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid dev-stack settings file: {path}") from error
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError(f"invalid dev-stack settings file: {path}")
    services = value.get("services")
    if not isinstance(services, dict):
        raise ValueError(f"invalid dev-stack settings file: {path}")

    enabled: set[str] = set()
    for service_id in MANAGED_SERVICE_IDS:
        record = services.get(service_id, {})
        if not isinstance(record, dict):
            raise ValueError(f"invalid settings for service {service_id}")
        service_enabled = record.get("enabled", True)
        if not isinstance(service_enabled, bool):
            raise ValueError(f"invalid enabled setting for service {service_id}")
        if service_enabled:
            enabled.add(service_id)
    return enabled


class SnapshotBuilder:
    def __init__(
        self,
        *,
        state_root: Path,
        sys_class_net: Path = Path("/sys/class/net"),
        dev_stack_path: Path | None = None,
        enabled_services: set[str] | None = None,
        runner: CommandRunner | None = None,
        now: datetime | None = None,
    ) -> None:
        self.state_root = state_root
        self.sys_class_net = sys_class_net
        self.dev_stack_path = dev_stack_path or Path(__file__).resolve().parents[1] / "dev_stack"
        self.enabled_services = (
            set(MANAGED_SERVICE_IDS) if enabled_services is None else set(enabled_services)
        )
        self.runner = runner or CommandRunner()
        self.now = now or datetime.now().astimezone()
        self.warnings: list[str] = []
        self.routes: dict[str, str] = {}
        self.dns_name = ""
        self.tailnet_ipv4 = ""
        self.tailnet_connected = False
        self.listening_ports: set[int] = set()

    def _run_json(self, argv: Sequence[str], label: str) -> dict[str, Any]:
        result = self.runner.run(argv)
        if result.returncode != 0:
            self.warnings.append(f"{label} unavailable")
            return {}
        value = _json_object(result.stdout)
        if value is None:
            self.warnings.append(f"{label} returned invalid JSON")
            return {}
        return value

    def _collect_tailnet(self) -> dict[str, Any]:
        status = self._run_json(["tailscale", "status", "--json"], "Tailscale status")
        self.tailnet_connected = status.get("BackendState") == "Running"
        self_info = status.get("Self") if isinstance(status.get("Self"), dict) else {}
        self.dns_name = _as_string(self_info.get("DNSName")).rstrip(".")
        ips = self_info.get("TailscaleIPs")
        if not isinstance(ips, list):
            ips = status.get("TailscaleIPs") if isinstance(status.get("TailscaleIPs"), list) else []
        self.tailnet_ipv4 = next(
            (value for value in ips if isinstance(value, str) and ":" not in value), ""
        )

        serve_status = self._run_json(
            ["tailscale", "serve", "status", "--json"], "Tailscale Serve status"
        )
        self.routes = _route_map(serve_status)
        dashboard_url = _tailnet_url(self.dns_name, "/dev-stack")
        return {
            "connected": self.tailnet_connected,
            "dns_name": self.dns_name or None,
            "ipv4": self.tailnet_ipv4 or None,
            "dashboard_url": dashboard_url,
        }

    def _collect_listeners(self) -> None:
        result = self.runner.run(["ss", "-H", "-ltn"])
        if result.returncode != 0:
            self.warnings.append("TCP listener status unavailable")
            return
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue
            local_address = fields[3]
            match = re.search(r":(\d+)$", local_address)
            if match:
                self.listening_ports.add(int(match.group(1)))

    def _configured_addresses(self) -> dict[str, list[str]]:
        configured: dict[str, list[str]] = {}
        result = self.runner.run(
            ["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"]
        )
        if result.returncode != 0:
            return configured

        for line in result.stdout.splitlines():
            fields = _split_nmcli_terse(line)
            if len(fields) < 2 or fields[1] not in {
                "802-3-ethernet",
                "802-11-wireless",
                "ethernet",
                "wifi",
            }:
                continue
            profile_name = fields[0]
            profile = self.runner.run(
                [
                    "nmcli",
                    "-g",
                    "connection.interface-name,ipv4.addresses",
                    "connection",
                    "show",
                    profile_name,
                ]
            )
            if profile.returncode != 0:
                continue
            lines = profile.stdout.splitlines()
            interface_name = lines[0].strip() if lines else ""
            addresses = _address_values("\n".join(lines[1:]))
            if interface_name and addresses:
                configured.setdefault(interface_name, []).extend(addresses)
        return configured

    def _is_physical(self, name: str) -> bool:
        if not name or VIRTUAL_INTERFACE_NAME.search(name):
            return False
        device_path = self.sys_class_net / name / "device"
        return device_path.exists() or PHYSICAL_INTERFACE_NAME.search(name) is not None

    def _collect_interfaces(self) -> list[dict[str, Any]]:
        result = self.runner.run(["ip", "-json", "address", "show"])
        if result.returncode != 0:
            self.warnings.append("Network interface status unavailable")
            return []
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.warnings.append("Network interface status returned invalid JSON")
            return []
        if not isinstance(records, list):
            return []

        configured = self._configured_addresses()
        interfaces: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            name = _as_string(record.get("ifname"))
            if not self._is_physical(name):
                continue
            addresses: list[str] = []
            for address in record.get("addr_info", []):
                if not isinstance(address, dict) or address.get("family") != "inet":
                    continue
                local = _as_string(address.get("local"))
                if local and not local.startswith("127."):
                    addresses.append(local)
            flags = record.get("flags") if isinstance(record.get("flags"), list) else []
            state = _as_string(record.get("operstate"), "unknown").lower()
            if state == "unknown" and "LOWER_UP" in flags:
                state = "up"
            interface_type = (
                "wireless"
                if name.lower().startswith("wl") or (self.sys_class_net / name / "wireless").exists()
                else "ethernet"
            )
            configured_values = [
                value
                for value in dict.fromkeys(configured.get(name, []))
                if value.split("/", 1)[0] not in {address.split("/", 1)[0] for address in addresses}
            ]
            interfaces.append(
                {
                    "name": name,
                    "type": interface_type,
                    "physical": True,
                    "state": state,
                    "addresses": addresses,
                    "configured_addresses": configured_values,
                }
            )
        return sorted(interfaces, key=lambda item: item["name"])

    def _listener_alive(self, port: int | None) -> bool:
        return port is not None and port in self.listening_ports

    def _route_details(self, path: str, bind_host: str, port: int | None) -> tuple[bool, bool]:
        normalized_path = path.rstrip("/") or "/"
        route_target = self.routes.get(normalized_path)
        present = route_target is not None
        expected = f"http://{bind_host}:{port}" if port else ""
        return present, bool(route_target and expected and _normalize_proxy(route_target) == expected)

    def _observe(
        self,
        instance: Mapping[str, Any],
        *,
        marker: str,
        default_port: int | None = None,
        require_health: bool = False,
    ) -> dict[str, Any]:
        pid = _as_pid(instance.get("pid"))
        port = _as_port(instance.get("port"), default_port)
        bind_host = _as_string(instance.get("bind_host"), "127.0.0.1")
        path = _as_string(instance.get("tailscale_path"), "/")
        tailscale_enabled = _as_bool(instance.get("tailscale_enable"), True)
        pid_alive = _process_alive(pid, marker)
        listener_alive = self._listener_alive(port)
        health_ok = True
        if require_health:
            health_ok = listener_alive and self._porterminal_health(bind_host, port)
        route_present, route_matches = self._route_details(path, bind_host, port)

        if pid_alive and listener_alive and health_ok:
            status = "running"
        elif not pid_alive and not listener_alive:
            status = "stopped"
        else:
            status = "drifted"
        if status == "running" and tailscale_enabled and not route_matches:
            status = "drifted"

        return {
            "status": status,
            "pid_alive": pid_alive,
            "listener_alive": listener_alive,
            "health_ok": health_ok,
            "route_present": route_present,
            "route_matches": route_matches,
            "bind_host": bind_host,
            "port": port,
            "tailscale_enabled": tailscale_enabled,
            "tailscale_path": path,
        }

    def _porterminal_health(self, bind_host: str, port: int | None) -> bool:
        if port is None:
            return False
        try:
            with urllib.request.urlopen(
                f"http://{bind_host}:{port}/health", timeout=1.0
            ) as response:
                value = _json_object(response.read().decode("utf-8", errors="replace"))
        except (OSError, urllib.error.URLError, ValueError):
            return False
        return bool(value and value.get("status") == "healthy" and "sessions" in value)

    def _code_server_service(self) -> dict[str, Any]:
        state = _read_state(self.state_root / "state-code-server.json")
        instances: list[dict[str, Any]] = []
        for name, raw_instance in sorted(state["instances"].items()):
            if not isinstance(raw_instance, dict):
                continue
            observed = self._observe(raw_instance, marker="code-server")
            url = (
                _tailnet_url(self.dns_name, observed["tailscale_path"])
                if observed["route_matches"]
                else None
            )
            instances.append(
                {
                    "name": str(name),
                    "status": observed["status"],
                    "workspace": _as_string(raw_instance.get("workspace_dir")) or None,
                    "port": observed["port"],
                    "uptime_seconds": (
                        _uptime_seconds(raw_instance.get("started_at"), self.now)
                        if observed["status"] in {"running", "drifted"}
                        else None
                    ),
                    "url": url,
                    "credential": {
                        "available": bool(_as_string(raw_instance.get("password"))),
                        "username": None,
                    },
                }
            )

        profiles: list[dict[str, str]] = []
        raw_profiles = state.get("profiles")
        if isinstance(raw_profiles, dict):
            for name, raw_profile in sorted(raw_profiles.items()):
                if not isinstance(raw_profile, dict):
                    continue
                workspace = _as_string(raw_profile.get("workspace_dir"))
                if workspace:
                    profiles.append({"name": str(name), "workspace": workspace})

        statuses = [instance["status"] for instance in instances]
        running = statuses.count("running")
        if any(status in {"drifted", "unknown"} for status in statuses):
            service_status = "drifted"
        elif running > 0:
            service_status = "running"
        else:
            service_status = "stopped"
        summary = (
            "No managed workspaces."
            if not instances
            else f"{running} of {len(instances)} managed workspaces running."
        )
        return {
            "id": "code-server",
            "name": "Code Server",
            "description": "Browser-based development workspaces",
            "status": service_status,
            "summary": summary,
            "health": {
                "state": "healthy" if service_status == "running" else service_status,
                "message": summary,
            },
            "instances": instances,
            "profiles": profiles,
        }

    def _singleton_service(
        self,
        *,
        state_file: str,
        instance_name: str,
        service_id: str,
        name: str,
        description: str,
        default_port: int,
        default_path: str,
        marker: str,
        require_health: bool = False,
    ) -> dict[str, Any]:
        state = _read_state(self.state_root / state_file)
        credentials = state.get("credentials")
        persistent_credential = credentials.get(service_id, {}) if isinstance(credentials, dict) else {}
        persistent_password = (
            _as_string(persistent_credential.get("password"))
            if isinstance(persistent_credential, dict)
            else ""
        )
        raw_instance = state["instances"].get(instance_name)
        if not isinstance(raw_instance, dict):
            return {
                "id": service_id,
                "name": name,
                "description": description,
                "status": "not_found",
                "summary": "The service is not currently managed.",
                "bind_host": "127.0.0.1",
                "port": default_port,
                "uptime_seconds": None,
                "url": None,
                "route": {"enabled": True, "present": False, "path": default_path},
                "credential": {
                    "available": bool(persistent_password),
                    "username": "admin" if service_id == "filebrowser" else None,
                },
                "health": {"state": "not_found", "message": "No manager state is recorded"},
            }

        observed = self._observe(
            raw_instance,
            marker=marker,
            default_port=default_port,
            require_health=require_health,
        )
        url = (
            _tailnet_url(self.dns_name, observed["tailscale_path"])
            if observed["route_matches"]
            else None
        )
        if observed["status"] == "running":
            summary = "The service is running and its private route is healthy."
            health_message = "Process, listener, health check, and route agree"
        elif observed["status"] == "stopped":
            summary = "The service is configured but not currently running."
            health_message = "No process or listener detected"
        else:
            summary = "The recorded process, listener, health check, or route needs attention."
            health_message = "Runtime state does not fully match the manager state"
        return {
            "id": service_id,
            "name": name,
            "description": description,
            "status": observed["status"],
            "summary": summary,
            "bind_host": observed["bind_host"],
            "port": observed["port"],
            "uptime_seconds": (
                _uptime_seconds(raw_instance.get("started_at"), self.now)
                if observed["status"] in {"running", "drifted"}
                else None
            ),
            "url": url,
            "route": {
                "enabled": observed["tailscale_enabled"],
                "present": observed["route_matches"],
                "path": observed["tailscale_path"],
            },
            "credential": {
                "available": bool(_as_string(raw_instance.get("password")) or persistent_password),
                "username": "admin" if service_id == "filebrowser" else None,
            },
            "health": {"state": observed["status"], "message": health_message},
        }

    @staticmethod
    def _unknown_go2rtc(message: str) -> dict[str, Any]:
        return {
            "id": "go2rtc",
            "name": "go2rtc",
            "description": "Local camera and RTSP/WebRTC gateway",
            "status": "unknown",
            "summary": message,
            "bind_host": "127.0.0.1",
            "port": 1984,
            "uptime_seconds": None,
            "url": None,
            "route": {"enabled": True, "present": False, "path": "/go2rtc"},
            "credential": {"available": False, "username": "admin"},
            "health": {"state": "unknown", "message": message},
        }

    def _go2rtc_service(self) -> dict[str, Any]:
        """Consume the Phase 1 CLI contract without duplicating Docker observation."""
        result = self.runner.run(
            [str(self.dev_stack_path), "go2rtc", "status", "--json"]
        )
        if result.returncode != 0:
            self.warnings.append("go2rtc status unavailable")
            return self._unknown_go2rtc("go2rtc status is temporarily unavailable.")

        value = _json_object(result.stdout)
        if value is None:
            self.warnings.append("go2rtc status returned invalid JSON")
            return self._unknown_go2rtc("go2rtc status returned an invalid response.")

        route = value.get("route")
        credential = value.get("credential")
        health = value.get("health")
        configuration = value.get("configuration")
        status = value.get("status")
        port = _as_port(value.get("port"))
        uptime_seconds = value.get("uptime_seconds")
        url = value.get("url")
        if (
            value.get("id") != "go2rtc"
            or not isinstance(status, str)
            or status not in SERVICE_STATES
            or not isinstance(value.get("summary"), str)
            or value.get("bind_host") != "127.0.0.1"
            or port is None
            or (
                uptime_seconds is not None
                and (
                    not isinstance(uptime_seconds, int)
                    or isinstance(uptime_seconds, bool)
                    or uptime_seconds < 0
                )
            )
            or (url is not None and not isinstance(url, str))
            or not isinstance(route, dict)
            or not isinstance(route.get("enabled"), bool)
            or not isinstance(route.get("present"), bool)
            or not isinstance(route.get("path"), str)
            or not route["path"].startswith("/")
            or not isinstance(credential, dict)
            or not isinstance(credential.get("available"), bool)
            or (
                credential.get("username") is not None
                and not isinstance(credential.get("username"), str)
            )
            or not isinstance(health, dict)
            or not isinstance(health.get("state"), str)
            or health.get("state") not in HEALTH_STATES
            or not isinstance(health.get("message"), str)
        ):
            self.warnings.append("go2rtc status failed validation")
            return self._unknown_go2rtc("go2rtc status returned an invalid response.")

        safe_configuration: dict[str, Any] | None = None
        if configuration is not None:
            stream_env_file = configuration.get("stream_env_file") if isinstance(configuration, dict) else None
            webcam = configuration.get("webcam") if isinstance(configuration, dict) else None
            path_values = (
                configuration.get("compose_file") if isinstance(configuration, dict) else None,
                configuration.get("config_file") if isinstance(configuration, dict) else None,
                stream_env_file.get("path") if isinstance(stream_env_file, dict) else None,
                webcam.get("video_device") if isinstance(webcam, dict) else None,
                webcam.get("sound_device") if isinstance(webcam, dict) else None,
            )
            if (
                not isinstance(configuration, dict)
                or any(not isinstance(path, str) or not path.startswith("/") for path in path_values)
                or not isinstance(stream_env_file, dict)
                or not isinstance(stream_env_file.get("available"), bool)
                or not isinstance(webcam, dict)
                or webcam.get("optional") is not True
                or not isinstance(webcam.get("enabled"), bool)
                or not isinstance(webcam.get("video_available"), bool)
                or not isinstance(webcam.get("sound_available"), bool)
                or not isinstance(webcam.get("message"), str)
            ):
                self.warnings.append("go2rtc status failed validation")
                return self._unknown_go2rtc("go2rtc status returned an invalid response.")
            safe_configuration = {
                "compose_file": configuration["compose_file"],
                "config_file": configuration["config_file"],
                "stream_env_file": {
                    "path": stream_env_file["path"],
                    "available": stream_env_file["available"],
                },
                "webcam": {
                    "optional": True,
                    "enabled": webcam["enabled"],
                    "video_device": webcam["video_device"],
                    "video_available": webcam["video_available"],
                    "sound_device": webcam["sound_device"],
                    "sound_available": webcam["sound_available"],
                    "message": webcam["message"],
                },
            }

        if isinstance(url, str):
            try:
                parsed_url = urllib.parse.urlsplit(url)
            except ValueError:
                parsed_url = None
            if (
                parsed_url is None
                or parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username is not None
                or parsed_url.password is not None
            ):
                self.warnings.append("go2rtc status failed validation")
                return self._unknown_go2rtc("go2rtc status returned an invalid response.")

        # Rebuild the service from allowlisted fields so unknown or secret-bearing
        # CLI fields can never flow into the dashboard snapshot.
        service = {
            "id": "go2rtc",
            "name": "go2rtc",
            "description": "Local camera and RTSP/WebRTC gateway",
            "status": status,
            "summary": value["summary"],
            "bind_host": "127.0.0.1",
            "port": port,
            "uptime_seconds": uptime_seconds,
            "url": url,
            "route": {
                "enabled": route["enabled"],
                "present": route["present"],
                "path": route["path"],
            },
            "credential": {
                "available": credential["available"],
                "username": credential.get("username"),
            },
            "health": {
                "state": health["state"],
                "message": health["message"],
            },
        }
        if safe_configuration is not None:
            service["configuration"] = safe_configuration
        return service

    def build(self) -> dict[str, Any]:
        tailnet = self._collect_tailnet()
        self._collect_listeners()
        hostname = socket.gethostname()
        try:
            username = getpass.getuser()
        except OSError:
            username = ""
        services: list[dict[str, Any]] = []
        if "code-server" in self.enabled_services:
            services.append(self._code_server_service())
        if "filebrowser" in self.enabled_services:
            services.append(self._singleton_service(
                state_file="state-filebrowser.json",
                instance_name="filebrowser",
                service_id="filebrowser",
                name="File Browser",
                description="Private file access for this machine",
                default_port=9900,
                default_path="/filebrowser",
                marker="filebrowser",
            ))
        if "porterminal" in self.enabled_services:
            services.append(self._singleton_service(
                state_file="state-porterminal.json",
                instance_name="porterminal",
                service_id="porterminal",
                name="Porterminal",
                description="Persistent terminal sessions in the browser",
                default_port=9444,
                default_path="/",
                marker="porterminal",
                require_health=True,
            ))
        if "go2rtc" in self.enabled_services:
            services.append(self._go2rtc_service())
        snapshot: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.now.isoformat(timespec="seconds"),
            "host": {
                "hostname": hostname,
                "display_name": hostname,
                "username": username,
                "interfaces": self._collect_interfaces(),
            },
            "tailnet": tailnet,
            "services": services,
        }
        if self.warnings:
            snapshot["warnings"] = list(dict.fromkeys(self.warnings))
        return snapshot


def build_snapshot(
    *,
    state_root: Path | None = None,
    settings_file: Path | None = None,
    sys_class_net: Path | None = None,
    dev_stack_path: Path | None = None,
    runner: CommandRunner | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parents[1]
    resolved_state_root = state_root or Path(
        os.environ.get(
            "DEV_STACK_STATE_ROOT",
            Path.home() / ".bin" / "state" / "dev-stack",
        )
    )
    resolved_sys_class_net = sys_class_net or Path(
        os.environ.get("DEV_STACK_SYS_CLASS_NET", "/sys/class/net")
    )
    resolved_settings_file = settings_file
    if resolved_settings_file is None:
        configured_settings_file = os.environ.get("DEV_STACK_SETTINGS_FILE")
        resolved_settings_file = Path(configured_settings_file) if configured_settings_file else None
    return SnapshotBuilder(
        state_root=resolved_state_root,
        sys_class_net=resolved_sys_class_net,
        dev_stack_path=dev_stack_path or project_root / "dev_stack",
        enabled_services=_read_enabled_services(resolved_settings_file),
        runner=runner,
        now=now,
    ).build()


def main(argv: Iterable[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments:
        print("status_snapshot.py does not accept arguments", file=sys.stderr)
        return 2
    try:
        snapshot = build_snapshot()
    except Exception as error:  # Keep stdout clean when snapshot construction fails.
        print(f"Could not build dev_stack status snapshot: {error}", file=sys.stderr)
        return 1
    json.dump(snapshot, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
