from __future__ import annotations

import json
import http.cookiejar
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


MODULE_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(MODULE_DIR))

from server import (  # noqa: E402
    AdminSessionManager,
    CodeServerOperationBridge,
    CredentialBridge,
    DevStackHTTPServer,
    DevStackRequestHandler,
    FileBrowserOperationBridge,
    Go2RTCOperationBridge,
    INSTANCE_NAME,
    OperationInProgressError,
    PorterminalOperationBridge,
    StatusBridge,
    validate_snapshot,
)
from admin_auth import create_state, write_state  # noqa: E402


VALID_SNAPSHOT = {
    "schema_version": 1,
    "generated_at": "2026-07-16T12:00:00+02:00",
    "host": {"hostname": "test-host", "interfaces": []},
    "tailnet": {"connected": True, "dns_name": "test.example.ts.net", "ipv4": "100.64.0.1"},
    "services": [],
}


class StaticBridge:
    def __init__(self) -> None:
        self.invalidations = 0

    def snapshot(self) -> dict[str, object]:
        return VALID_SNAPSHOT

    def invalidate(self) -> None:
        self.invalidations += 1


class StaticCredentialBridge:
    def reveal(self, service: str, instance: str | None) -> dict[str, object]:
        return {
            "service": service,
            "instance": instance,
            "username": "admin" if service in {"filebrowser", "go2rtc"} else None,
            "password": "test-only-password",
            "clear_after_seconds": 30,
        }


class StaticFileBrowserOperationBridge:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def run(self, action: str) -> dict[str, str]:
        if action not in {"start", "stop", "restart", "clean"}:
            raise ValueError("unsupported filebrowser action")
        self.actions.append(action)
        return {"service": "filebrowser", "action": action, "status": "completed"}


class StaticPorterminalOperationBridge:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def run(self, action: str) -> dict[str, str]:
        if action not in {"start", "stop", "restart", "clean"}:
            raise ValueError("unsupported porterminal action")
        self.actions.append(action)
        return {"service": "porterminal", "action": action, "status": "completed"}


class StaticGo2RTCOperationBridge:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def run(self, action: str) -> dict[str, str]:
        if action not in {"start", "stop", "restart", "clean", "publish", "unpublish"}:
            raise ValueError("unsupported go2rtc action")
        self.actions.append(action)
        return {"service": "go2rtc", "action": action, "status": "completed"}


class StaticCodeServerOperationBridge:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str | None, str | None]] = []

    def run(
        self,
        action: str,
        instance: str | None = None,
        workspace: str | None = None,
    ) -> dict[str, str]:
        if action == "clean":
            if instance is not None or workspace is not None:
                raise ValueError("clean does not accept an instance")
        elif action == "start-workspace":
            if instance is None or not INSTANCE_NAME.fullmatch(instance) or not workspace or not workspace.startswith("/"):
                raise ValueError("invalid workspace start")
        elif action in {"profile-save", "profile-start", "profile-delete"}:
            if instance is None or not INSTANCE_NAME.fullmatch(instance) or workspace is not None:
                raise ValueError("invalid profile action")
        elif action not in {"start", "stop", "restart"} or instance is None or not INSTANCE_NAME.fullmatch(instance) or workspace is not None:
            raise ValueError("unsupported code-server action")
        self.actions.append((action, instance, workspace))
        result = {
            "service": "code-server",
            "action": action,
            "status": "completed",
        }
        if instance is not None:
            result["instance"] = instance
        return result


class StatusBridgeTests(unittest.TestCase):
    def test_bridge_executes_only_status_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argument_log = root / "arguments.txt"
            executable = root / "dev_stack"
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGUMENT_LOG\"\nprintf '%s\\n' \"$SNAPSHOT_JSON\"\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            old_log = os.environ.get("ARGUMENT_LOG")
            old_snapshot = os.environ.get("SNAPSHOT_JSON")
            os.environ["ARGUMENT_LOG"] = str(argument_log)
            os.environ["SNAPSHOT_JSON"] = json.dumps(VALID_SNAPSHOT)
            try:
                snapshot = StatusBridge(executable, cache_seconds=0).snapshot()
            finally:
                if old_log is None:
                    os.environ.pop("ARGUMENT_LOG", None)
                else:
                    os.environ["ARGUMENT_LOG"] = old_log
                if old_snapshot is None:
                    os.environ.pop("SNAPSHOT_JSON", None)
                else:
                    os.environ["SNAPSHOT_JSON"] = old_snapshot
            self.assertEqual(snapshot["schema_version"], 1)
            self.assertEqual(argument_log.read_text(encoding="utf-8"), "status\n--json\n")

    def test_credential_bridge_builds_an_allowlisted_command(self) -> None:
        bridge = CredentialBridge(Path("/tmp/dev_stack"))
        self.assertEqual(
            bridge.command("code-server", "iap-rosws"),
            [
                "/tmp/dev_stack",
                "credentials",
                "show",
                "--service",
                "code-server",
                "--instance-name",
                "iap-rosws",
                "--json",
            ],
        )
        with self.assertRaises(ValueError):
            bridge.command("code-server", "../unsafe")
        with self.assertRaises(ValueError):
            bridge.command("unknown", None)
        self.assertEqual(
            bridge.command("go2rtc", None),
            [
                "/tmp/dev_stack",
                "credentials",
                "show",
                "--service",
                "go2rtc",
                "--json",
            ],
        )
        with self.assertRaises(ValueError):
            bridge.command("go2rtc", "unexpected-instance")

    def test_filebrowser_operation_bridge_executes_only_allowlisted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argument_log = root / "arguments.txt"
            executable = root / "dev_stack"
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGUMENT_LOG\"\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            old_log = os.environ.get("ARGUMENT_LOG")
            os.environ["ARGUMENT_LOG"] = str(argument_log)
            try:
                result = FileBrowserOperationBridge(executable).run("restart")
            finally:
                if old_log is None:
                    os.environ.pop("ARGUMENT_LOG", None)
                else:
                    os.environ["ARGUMENT_LOG"] = old_log
            self.assertEqual(result["status"], "completed")
            self.assertEqual(argument_log.read_text(encoding="utf-8"), "filebrowser\nrestart\n")

            bridge = FileBrowserOperationBridge(executable)
            self.assertEqual(
                bridge.command("clean"),
                [str(executable), "filebrowser", "clean"],
            )
            with self.assertRaises(ValueError):
                bridge.command("password-reset")
            bridge._lock.acquire()
            try:
                with self.assertRaises(OperationInProgressError):
                    bridge.run("start")
            finally:
                bridge._lock.release()

    def test_porterminal_operation_bridge_is_allowlisted(self) -> None:
        bridge = PorterminalOperationBridge(Path("/tmp/dev_stack"))
        self.assertEqual(
            bridge.command("restart"),
            ["/tmp/dev_stack", "porterminal", "restart", "--tailscale-serve"],
        )
        self.assertEqual(
            bridge.command("start"),
            ["/tmp/dev_stack", "porterminal", "start", "--tailscale-serve"],
        )
        self.assertEqual(
            bridge.command("clean"),
            ["/tmp/dev_stack", "porterminal", "clean"],
        )
        with self.assertRaises(ValueError):
            bridge.command("password-reset")
        with self.assertRaises(ValueError):
            bridge.command("publish")

    def test_porterminal_operation_bridge_preserves_path_for_dashboard_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            test_bin = root / "bin"
            test_bin.mkdir()
            porterminal = test_bin / "porterminal"
            porterminal.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            porterminal.chmod(0o755)
            command_log = root / "command.txt"
            executable = root / "dev_stack"
            executable.write_text(
                "#!/bin/sh\n"
                'command -v porterminal > "$PORTERMINAL_COMMAND_LOG"\n',
                encoding="utf-8",
            )
            executable.chmod(0o755)

            with patch.dict(
                os.environ,
                {
                    "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
                    "PORTERMINAL_COMMAND_LOG": str(command_log),
                },
            ):
                result = PorterminalOperationBridge(executable).run("start")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(command_log.read_text(encoding="utf-8").strip(), str(porterminal))

    def test_go2rtc_operation_bridge_executes_only_allowlisted_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            argument_log = root / "arguments.txt"
            executable = root / "dev_stack"
            executable.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGUMENT_LOG\"\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            old_log = os.environ.get("ARGUMENT_LOG")
            os.environ["ARGUMENT_LOG"] = str(argument_log)
            try:
                bridge = Go2RTCOperationBridge(executable)
                result = bridge.run("restart")
            finally:
                if old_log is None:
                    os.environ.pop("ARGUMENT_LOG", None)
                else:
                    os.environ["ARGUMENT_LOG"] = old_log
            self.assertEqual(result["status"], "completed")
            self.assertEqual(argument_log.read_text(encoding="utf-8"), "go2rtc\nrestart\n")
            self.assertEqual(
                bridge.command("clean"),
                [str(executable), "go2rtc", "clean"],
            )
            self.assertEqual(
                bridge.command("publish"),
                [str(executable), "go2rtc", "publish"],
            )
            with self.assertRaises(ValueError):
                bridge.command("password-reset")
            bridge._lock.acquire()
            try:
                with self.assertRaises(OperationInProgressError):
                    bridge.run("start")
            finally:
                bridge._lock.release()

            with patch("server.subprocess.run", side_effect=subprocess.TimeoutExpired([], 1)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    bridge.run("start")
            self.assertTrue(bridge._lock.acquire(blocking=False))
            bridge._lock.release()

    def test_code_server_operation_bridge_requires_a_valid_instance(self) -> None:
        bridge = CodeServerOperationBridge(Path("/tmp/dev_stack"))
        self.assertEqual(
            bridge.command("restart", "iap-rosws"),
            [
                "/tmp/dev_stack",
                "code-server",
                "restart",
                "--instance-name",
                "iap-rosws",
            ],
        )
        self.assertEqual(
            bridge.command("clean"),
            ["/tmp/dev_stack", "code-server", "clean"],
        )
        self.assertEqual(
            bridge.command("profile-save", "iap-rosws"),
            ["/tmp/dev_stack", "code-server", "profile", "save", "iap-rosws"],
        )
        self.assertEqual(
            bridge.command("profile-start", "iap-rosws"),
            ["/tmp/dev_stack", "code-server", "profile", "start", "iap-rosws"],
        )
        self.assertEqual(
            bridge.command("start-workspace", "new-work", "/home/test/work space"),
            [
                "/tmp/dev_stack",
                "code-server",
                "start",
                "--instance-name",
                "new-work",
                "--workspace-dir",
                "/home/test/work space",
            ],
        )
        with self.assertRaises(ValueError):
            bridge.command("clean", "iap-rosws")
        with self.assertRaises(ValueError):
            bridge.command("start")
        with self.assertRaises(ValueError):
            bridge.command("start", "../unsafe")
        with self.assertRaises(ValueError):
            bridge.command("start-workspace", "work", "relative/path")

    def test_validation_rejects_secret_fields(self) -> None:
        unsafe = {**VALID_SNAPSHOT, "password": "secret"}
        with self.assertRaisesRegex(ValueError, "forbidden secret field"):
            validate_snapshot(unsafe)


class HTTPBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        static_dir = Path(self.temporary_directory.name)
        (static_dir / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
        (static_dir / "login.html").write_text("<!doctype html><title>login</title>", encoding="utf-8")
        (static_dir / "login.css").write_text("body{}", encoding="utf-8")
        (static_dir / "login.js").write_text("", encoding="utf-8")
        self.password = "test-master-password1"
        self.admin_state = static_dir / "state-admin.json"
        write_state(self.admin_state, create_state(self.password, n=1 << 10))
        self.status_bridge = StaticBridge()
        self.operation_bridge = StaticFileBrowserOperationBridge()
        self.porterminal_operation_bridge = StaticPorterminalOperationBridge()
        self.go2rtc_operation_bridge = StaticGo2RTCOperationBridge()
        self.code_server_operation_bridge = StaticCodeServerOperationBridge()
        self.server = DevStackHTTPServer(
            ("127.0.0.1", 0),
            DevStackRequestHandler,
            bridge=self.status_bridge,  # type: ignore[arg-type]
            credential_bridge=StaticCredentialBridge(),  # type: ignore[arg-type]
            filebrowser_operations=self.operation_bridge,  # type: ignore[arg-type]
            porterminal_operations=self.porterminal_operation_bridge,  # type: ignore[arg-type]
            go2rtc_operations=self.go2rtc_operation_bridge,  # type: ignore[arg-type]
            code_server_operations=self.code_server_operation_bridge,  # type: ignore[arg-type]
            admin_sessions=AdminSessionManager(
                admin_state=self.admin_state,
                session_seconds=60,
            ),
            static_dir=static_dir,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def test_health_is_public_and_status_requires_login(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
            health = json.loads(response.read())
            self.assertEqual(
                health,
                {"status": "healthy", "mode": "limited-operations", "schema_version": 1},
            )

        with urllib.request.urlopen(f"{self.base_url}/login", timeout=2) as response:
            self.assertIn(b"login", response.read())
        with urllib.request.urlopen(f"{self.base_url}/", timeout=2) as response:
            self.assertTrue(response.geturl().endswith("/login"))

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base_url}/api/status", timeout=2)
        self.assertEqual(raised.exception.code, 401)

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        with self._post(opener, "/api/session/login", {"password": self.password}) as response:
            self.assertEqual(json.loads(response.read())["status"], "authenticated")

        with opener.open(f"{self.base_url}/api/status", timeout=2) as response:
            snapshot = json.loads(response.read())
            self.assertEqual(snapshot["host"]["hostname"], "test-host")
            self.assertEqual(response.headers["Cache-Control"], "no-store")

        request = urllib.request.Request(
            f"{self.base_url}/api/status",
            method="POST",
            data=b"{}",
        )
        # Build the authenticated request through the opener so its cookie is attached.
        with self.assertRaises(urllib.error.HTTPError) as raised:
            opener.open(request, timeout=2)
        self.assertEqual(raised.exception.code, 405)
        self.assertEqual(raised.exception.headers["Allow"], "GET, HEAD")

    def _post(
        self,
        opener: urllib.request.OpenerDirector,
        path: str,
        payload: dict[str, object],
        *,
        request_type: str = "credentials",
        origin: str | None = None,
    ):
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Origin": origin or self.base_url,
                "X-Dev-Stack-Request": request_type,
            },
        )
        return opener.open(request, timeout=2)

    def test_filebrowser_operations_require_auth_and_same_origin(self) -> None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                "/api/services/filebrowser/actions",
                {"action": "start"},
                request_type="operation",
            )
        self.assertEqual(raised.exception.code, 401)

        with self._post(opener, "/api/session/login", {"password": self.password}):
            pass

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                "/api/services/filebrowser/actions",
                {"action": "start"},
                request_type="operation",
                origin="https://attacker.example",
            )
        self.assertEqual(raised.exception.code, 403)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                "/api/services/filebrowser/actions",
                {"action": "password-reset"},
                request_type="operation",
            )
        self.assertEqual(raised.exception.code, 400)

        with self._post(
            opener,
            "/api/services/filebrowser/actions",
            {"action": "clean"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result, {"service": "filebrowser", "action": "clean", "status": "completed"})

        with self._post(
            opener,
            "/api/services/filebrowser/actions",
            {"action": "restart"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result, {"service": "filebrowser", "action": "restart", "status": "completed"})
        self.assertEqual(self.operation_bridge.actions, ["clean", "restart"])
        self.assertEqual(self.status_bridge.invalidations, 3)

        with self._post(
            opener,
            "/api/services/porterminal/actions",
            {"action": "start"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result, {"service": "porterminal", "action": "start", "status": "completed"})
        self.assertEqual(self.porterminal_operation_bridge.actions, ["start"])
        self.assertEqual(self.status_bridge.invalidations, 4)

        with self._post(
            opener,
            "/api/services/porterminal/actions",
            {"action": "clean"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result, {"service": "porterminal", "action": "clean", "status": "completed"})
        self.assertEqual(self.porterminal_operation_bridge.actions, ["start", "clean"])
        self.assertEqual(self.status_bridge.invalidations, 5)

        with self._post(
            opener,
            "/api/services/code-server/actions",
            {"action": "restart", "instance": "iap-rosws"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(
            result,
            {
                "service": "code-server",
                "instance": "iap-rosws",
                "action": "restart",
                "status": "completed",
            },
        )
        self.assertEqual(self.code_server_operation_bridge.actions, [("restart", "iap-rosws", None)])
        self.assertEqual(self.status_bridge.invalidations, 6)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                "/api/services/code-server/actions",
                {"action": "start", "instance": "../unsafe"},
                request_type="operation",
            )
        self.assertEqual(raised.exception.code, 400)

        with self._post(
            opener,
            "/api/services/code-server/actions",
            {"action": "clean"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result, {"service": "code-server", "action": "clean", "status": "completed"})
        self.assertEqual(
            self.code_server_operation_bridge.actions,
            [("restart", "iap-rosws", None), ("clean", None, None)],
        )
        self.assertEqual(self.status_bridge.invalidations, 8)

        with self._post(
            opener,
            "/api/services/code-server/actions",
            {"action": "profile-save", "instance": "iap-rosws"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result["action"], "profile-save")

        with self._post(
            opener,
            "/api/services/code-server/actions",
            {"action": "start-workspace", "instance": "new-work", "workspace": "/home/test/work"},
            request_type="operation",
        ) as response:
            result = json.loads(response.read())
        self.assertEqual(result["instance"], "new-work")
        self.assertEqual(
            self.code_server_operation_bridge.actions[-2:],
            [("profile-save", "iap-rosws", None), ("start-workspace", "new-work", "/home/test/work")],
        )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                "/api/services/code-server/actions",
                {"action": "start-workspace", "instance": "new-work", "workspace": "relative"},
                request_type="operation",
            )
        self.assertEqual(raised.exception.code, 400)

    def test_master_session_reveals_credentials_and_logout_revokes_it(self) -> None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(opener, "/api/credentials/reveal", {"service": "filebrowser", "instance": None})
        self.assertEqual(raised.exception.code, 401)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(opener, "/api/session/login", {"password": "incorrect-password"})
        self.assertEqual(raised.exception.code, 401)

        with self._post(opener, "/api/session/login", {"password": self.password}) as response:
            self.assertEqual(json.loads(response.read())["status"], "authenticated")

        with self._post(
            opener,
            "/api/credentials/reveal",
            {"service": "filebrowser", "instance": None},
        ) as response:
            credential = json.loads(response.read())
        self.assertEqual(credential["username"], "admin")
        self.assertEqual(credential["password"], "test-only-password")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        with self._post(
            opener,
            "/api/credentials/reveal",
            {"service": "go2rtc", "instance": None},
        ) as response:
            go2rtc_credential = json.loads(response.read())
        self.assertEqual(go2rtc_credential["service"], "go2rtc")
        self.assertEqual(go2rtc_credential["username"], "admin")
        self.assertEqual(go2rtc_credential["password"], "test-only-password")

        with self._post(opener, "/api/session/logout", {}) as response:
            self.assertEqual(json.loads(response.read())["status"], "logged_out")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            opener.open(f"{self.base_url}/api/status", timeout=2)
        self.assertEqual(raised.exception.code, 401)

    def test_go2rtc_operations_require_auth_and_reject_password_reset(self) -> None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        endpoint = "/api/services/go2rtc/actions"
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                endpoint,
                {"action": "start"},
                request_type="operation",
            )
        self.assertEqual(raised.exception.code, 401)

        with self._post(opener, "/api/session/login", {"password": self.password}):
            pass

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                endpoint,
                {"action": "start"},
                request_type="operation",
                origin="https://attacker.example",
            )
        self.assertEqual(raised.exception.code, 403)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._post(
                opener,
                endpoint,
                {"action": "password-reset"},
                request_type="operation",
            )
        self.assertEqual(raised.exception.code, 400)

        for action in ("start", "stop", "restart", "clean", "publish", "unpublish"):
            with self._post(
                opener,
                endpoint,
                {"action": action},
                request_type="operation",
            ) as response:
                result = json.loads(response.read())
            self.assertEqual(
                result,
                {"service": "go2rtc", "action": action, "status": "completed"},
            )

        self.assertEqual(
            self.go2rtc_operation_bridge.actions,
            ["start", "stop", "restart", "clean", "publish", "unpublish"],
        )
        # Rejected bridge actions and successful actions both invalidate cached status.
        self.assertEqual(self.status_bridge.invalidations, 7)

    def test_resetting_master_password_invalidates_existing_session(self) -> None:
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        with self._post(opener, "/api/session/login", {"password": self.password}):
            pass
        write_state(
            self.admin_state,
            create_state("replacement-master-password1", n=1 << 10),
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            opener.open(f"{self.base_url}/api/status", timeout=2)
        self.assertEqual(raised.exception.code, 401)

    def test_session_expiry_and_login_rate_limit(self) -> None:
        manager = AdminSessionManager(admin_state=self.admin_state, session_seconds=60)
        token = manager.login(self.password)
        cookie = f"dev_stack_admin_session={token}"
        self.assertTrue(manager.session_authorized(cookie))
        with manager._lock:
            _, state_fingerprint = manager._sessions[token]
            manager._sessions[token] = (time.time() - 1, state_fingerprint)
        self.assertFalse(manager.session_authorized(cookie))

        limited = AdminSessionManager(admin_state=self.admin_state, session_seconds=60)
        for _ in range(5):
            with self.assertRaises(ValueError):
                limited.login("incorrect-test-password")
        with self.assertRaises(PermissionError):
            limited.login("incorrect-test-password")

    def test_password_verification_is_serialized(self) -> None:
        manager = AdminSessionManager(admin_state=self.admin_state, session_seconds=60)
        first_verification_started = threading.Event()
        allow_first_verification_to_finish = threading.Event()
        counters_lock = threading.Lock()
        active_verifications = 0
        peak_verifications = 0
        tokens: list[str] = []
        errors: list[BaseException] = []

        def controlled_verify(_password: str, _state: dict[str, object]) -> bool:
            nonlocal active_verifications, peak_verifications
            with counters_lock:
                active_verifications += 1
                peak_verifications = max(peak_verifications, active_verifications)
                invocation = len(tokens) + active_verifications
            if invocation == 1:
                first_verification_started.set()
                self.assertTrue(allow_first_verification_to_finish.wait(timeout=2))
            with counters_lock:
                active_verifications -= 1
            return True

        def login() -> None:
            try:
                tokens.append(manager.login(self.password))
            except BaseException as error:
                errors.append(error)

        with patch("server.verify_password", side_effect=controlled_verify):
            first = threading.Thread(target=login)
            second = threading.Thread(target=login)
            first.start()
            self.assertTrue(first_verification_started.wait(timeout=2))
            second.start()
            time.sleep(0.05)
            with counters_lock:
                self.assertEqual(active_verifications, 1)
            allow_first_verification_to_finish.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(tokens), 2)
        self.assertEqual(peak_verifications, 1)


if __name__ == "__main__":
    unittest.main()
