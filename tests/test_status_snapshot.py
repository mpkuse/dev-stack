from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(MODULE_DIR))

from status_snapshot import CommandResult, build_snapshot  # noqa: E402


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: list[str] | tuple[str, ...]) -> CommandResult:
        key = tuple(argv)
        self.commands.append(key)
        return self.responses.get(key, CommandResult(127, "", "not configured"))


class StatusSnapshotTests(unittest.TestCase):
    def test_disabled_services_are_omitted_from_the_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_root = root / "state"
            sys_class_net = root / "sys-class-net"
            settings_file = root / "settings.json"
            state_root.mkdir()
            sys_class_net.mkdir()
            settings_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "services": {
                            "code-server": {"enabled": False},
                            "filebrowser": {"enabled": False},
                            "porterminal": {"enabled": False},
                            "go2rtc": {"enabled": False},
                        },
                    }
                ),
                encoding="utf-8",
            )
            runner = FakeRunner({})

            snapshot = build_snapshot(
                state_root=state_root,
                settings_file=settings_file,
                sys_class_net=sys_class_net,
                runner=runner,
            )

            self.assertEqual(snapshot["services"], [])
            self.assertFalse(
                any(command[1:3] == ("go2rtc", "status") for command in runner.commands)
            )

    def test_snapshot_is_whitelisted_and_filters_virtual_interfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_root = root / "state"
            sys_class_net = root / "sys-class-net"
            dev_stack_path = root / "dev_stack"
            state_root.mkdir()
            (sys_class_net / "enp1s0" / "device").mkdir(parents=True)
            (sys_class_net / "docker0").mkdir(parents=True)

            (state_root / "state-code-server.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {
                            "work": {
                                "workspace_dir": "/workspace",
                                "bind_host": "127.0.0.1",
                                "port": 9341,
                                "pid": None,
                                "password": "must-never-leak",
                                "tailscale_enable": "true",
                                "tailscale_path": "/code-server-manager/work",
                                "started_at": "2026-07-16T09:00:00+00:00",
                            },
                            "second-work": {
                                "workspace_dir": "/workspace/second",
                                "bind_host": "127.0.0.1",
                                "port": 9342,
                                "pid": None,
                                "tailscale_enable": "false",
                                "tailscale_path": "/code-server-manager/second-work",
                            }
                        },
                        "profiles": {
                            "saved-work": {
                                "name": "saved-work",
                                "workspace_dir": "/workspace/saved",
                                "password": "profile-secret-must-not-leak",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (state_root / "state-filebrowser.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {
                            "filebrowser": {
                                "password": "filebrowser-must-never-leak",
                                "bind_host": "127.0.0.1",
                                "port": 9900,
                                "tailscale_enable": "true",
                                "tailscale_path": "/filebrowser",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (state_root / "state-porterminal.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {
                            "porterminal": {
                                "password_hash": "also-secret",
                                "password": "porterminal-must-never-leak",
                                "bind_host": "127.0.0.1",
                                "port": 9444,
                                "tailscale_enable": "true",
                                "tailscale_path": "/",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            tailscale_status = {
                "BackendState": "Running",
                "Self": {
                    "DNSName": "host.example.ts.net.",
                    "TailscaleIPs": ["100.64.0.11", "fd7a::1"],
                },
            }
            serve_status = {
                "Web": {
                    "host.example.ts.net:443": {
                        "Handlers": {
                            "/code-server-manager/work": {
                                "Proxy": "http://127.0.0.1:9341"
                            }
                        }
                    }
                }
            }
            ip_status = [
                {
                    "ifname": "enp1s0",
                    "operstate": "DOWN",
                    "flags": ["UP"],
                    "addr_info": [],
                },
                {
                    "ifname": "docker0",
                    "operstate": "UP",
                    "flags": ["UP", "LOWER_UP"],
                    "addr_info": [{"family": "inet", "local": "172.17.0.1"}],
                },
            ]
            go2rtc_status = {
                "id": "go2rtc",
                "name": "go2rtc",
                "description": "Local camera and RTSP/WebRTC gateway",
                "status": "running",
                "summary": "The go2rtc container and local WebUI/API are healthy.",
                "bind_host": "127.0.0.1",
                "port": 1984,
                "uptime_seconds": 300,
                "url": "https://host.example.ts.net/go2rtc/",
                "route": {"enabled": True, "present": True, "path": "/go2rtc"},
                "credential": {"available": True, "username": "admin"},
                "health": {
                    "state": "healthy",
                    "message": "Container state and local HTTP health check agree.",
                },
                "password": "go2rtc-cli-secret-must-never-leak",
            }
            runner = FakeRunner(
                {
                    ("tailscale", "status", "--json"): CommandResult(
                        0, json.dumps(tailscale_status), ""
                    ),
                    ("tailscale", "serve", "status", "--json"): CommandResult(
                        0, json.dumps(serve_status), ""
                    ),
                    ("ss", "-H", "-ltn"): CommandResult(0, "", ""),
                    ("ip", "-json", "address", "show"): CommandResult(
                        0, json.dumps(ip_status), ""
                    ),
                    (
                        "nmcli",
                        "-t",
                        "-f",
                        "NAME,TYPE,DEVICE",
                        "connection",
                        "show",
                    ): CommandResult(0, "wired:802-3-ethernet:\n", ""),
                    (
                        "nmcli",
                        "-g",
                        "connection.interface-name,ipv4.addresses",
                        "connection",
                        "show",
                        "wired",
                    ): CommandResult(0, "enp1s0\n192.168.50.10/24\n", ""),
                    (
                        str(dev_stack_path),
                        "go2rtc",
                        "status",
                        "--json",
                    ): CommandResult(0, json.dumps(go2rtc_status), ""),
                }
            )

            with patch("status_snapshot.getpass.getuser", return_value="test-user"):
                snapshot = build_snapshot(
                    state_root=state_root,
                    sys_class_net=sys_class_net,
                    dev_stack_path=dev_stack_path,
                    runner=runner,
                    now=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
                )

            serialized = json.dumps(snapshot)
            self.assertNotIn("must-never-leak", serialized)
            self.assertNotIn("also-secret", serialized)
            self.assertNotIn("filebrowser-must-never-leak", serialized)
            self.assertNotIn("porterminal-must-never-leak", serialized)
            self.assertNotIn("go2rtc-cli-secret-must-never-leak", serialized)
            self.assertNotIn('"password"', serialized)
            self.assertEqual(snapshot["schema_version"], 1)
            self.assertEqual(snapshot["host"]["username"], "test-user")
            self.assertEqual(snapshot["tailnet"]["ipv4"], "100.64.0.11")
            self.assertEqual(
                snapshot["tailnet"]["dashboard_url"],
                "https://host.example.ts.net/dev-stack/",
            )
            self.assertEqual([item["name"] for item in snapshot["host"]["interfaces"]], ["enp1s0"])
            self.assertEqual(
                snapshot["host"]["interfaces"][0]["configured_addresses"],
                ["192.168.50.10/24"],
            )
            self.assertEqual(
                [service["id"] for service in snapshot["services"]],
                ["code-server", "filebrowser", "porterminal", "go2rtc"],
            )
            self.assertEqual(
                [instance["name"] for instance in snapshot["services"][0]["instances"]],
                ["second-work", "work"],
            )
            work_instance = next(
                instance
                for instance in snapshot["services"][0]["instances"]
                if instance["name"] == "work"
            )
            self.assertTrue(work_instance["credential"]["available"])
            self.assertEqual(
                snapshot["services"][0]["profiles"],
                [{"name": "saved-work", "workspace": "/workspace/saved"}],
            )
            self.assertTrue(snapshot["services"][1]["credential"]["available"])
            self.assertTrue(snapshot["services"][2]["credential"]["available"])
            self.assertEqual(snapshot["services"][3]["status"], "running")
            self.assertEqual(snapshot["services"][3]["credential"]["username"], "admin")

            (state_root / "state-filebrowser.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {},
                        "credentials": {
                            "filebrowser": {
                                "username": "admin",
                                "password": "preserved-filebrowser-password",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            cleaned_snapshot = build_snapshot(
                state_root=state_root,
                sys_class_net=sys_class_net,
                dev_stack_path=dev_stack_path,
                runner=runner,
                now=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            )
            cleaned_filebrowser = next(
                service for service in cleaned_snapshot["services"] if service["id"] == "filebrowser"
            )
            self.assertEqual(cleaned_filebrowser["status"], "not_found")
            self.assertTrue(cleaned_filebrowser["credential"]["available"])
            self.assertNotIn("preserved-filebrowser-password", json.dumps(cleaned_snapshot))

            (state_root / "state-porterminal.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {},
                        "credentials": {
                            "porterminal": {"password": "preserved-porterminal-password"}
                        },
                    }
                ),
                encoding="utf-8",
            )
            cleaned_snapshot = build_snapshot(
                state_root=state_root,
                sys_class_net=sys_class_net,
                dev_stack_path=dev_stack_path,
                runner=runner,
                now=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            )
            cleaned_porterminal = next(
                service for service in cleaned_snapshot["services"] if service["id"] == "porterminal"
            )
            self.assertEqual(cleaned_porterminal["status"], "not_found")
            self.assertTrue(cleaned_porterminal["credential"]["available"])
            self.assertNotIn("preserved-porterminal-password", json.dumps(cleaned_snapshot))

    def test_go2rtc_cli_contract_states_and_failure_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_root = root / "state"
            sys_class_net = root / "sys-class-net"
            dev_stack_path = root / "dev_stack"
            state_root.mkdir()
            sys_class_net.mkdir()
            command = (str(dev_stack_path), "go2rtc", "status", "--json")

            for status, health_state in (
                ("running", "healthy"),
                ("stopped", "stopped"),
                ("drifted", "degraded"),
                ("not_found", "not_found"),
                ("unknown", "unknown"),
            ):
                with self.subTest(status=status):
                    payload = {
                        "id": "go2rtc",
                        "name": "go2rtc",
                        "description": "Local camera and RTSP/WebRTC gateway",
                        "status": status,
                        "summary": f"go2rtc is {status}.",
                        "bind_host": "127.0.0.1",
                        "port": 1984,
                        "uptime_seconds": 42 if status in {"running", "drifted"} else None,
                        "url": (
                            "https://host.example.ts.net/go2rtc/"
                            if status == "running"
                            else None
                        ),
                        "route": {
                            "enabled": True,
                            "present": status == "running",
                            "path": "/go2rtc",
                        },
                        "credential": {"available": True, "username": "admin"},
                        "health": {
                            "state": health_state,
                            "message": f"go2rtc health is {health_state}.",
                        },
                    }
                    runner = FakeRunner({command: CommandResult(0, json.dumps(payload), "")})
                    snapshot = build_snapshot(
                        state_root=state_root,
                        sys_class_net=sys_class_net,
                        dev_stack_path=dev_stack_path,
                        runner=runner,
                    )
                    service = snapshot["services"][-1]
                    self.assertEqual(service["id"], "go2rtc")
                    self.assertEqual(service["status"], status)
                    self.assertEqual(service["health"]["state"], health_state)

            failed_runner = FakeRunner({command: CommandResult(1, "", "docker unavailable")})
            failed_snapshot = build_snapshot(
                state_root=state_root,
                sys_class_net=sys_class_net,
                dev_stack_path=dev_stack_path,
                runner=failed_runner,
            )
            failed_service = failed_snapshot["services"][-1]
            self.assertEqual(failed_service["status"], "unknown")
            self.assertIn("go2rtc status unavailable", failed_snapshot["warnings"])

            unsafe_payload = {
                "id": "go2rtc",
                "status": "running",
                "summary": "Unsafe URL",
                "bind_host": "127.0.0.1",
                "port": 1984,
                "uptime_seconds": 1,
                "url": "javascript:alert(1)",
                "route": {"enabled": True, "present": True, "path": "/go2rtc"},
                "credential": {"available": True, "username": "admin"},
                "health": {"state": "healthy", "message": "Unsafe URL"},
            }
            unsafe_runner = FakeRunner(
                {command: CommandResult(0, json.dumps(unsafe_payload), "")}
            )
            unsafe_snapshot = build_snapshot(
                state_root=state_root,
                sys_class_net=sys_class_net,
                dev_stack_path=dev_stack_path,
                runner=unsafe_runner,
            )
            self.assertEqual(unsafe_snapshot["services"][-1]["status"], "unknown")
            self.assertIsNone(unsafe_snapshot["services"][-1]["url"])
            self.assertIn("go2rtc status failed validation", unsafe_snapshot["warnings"])


if __name__ == "__main__":
    unittest.main()
