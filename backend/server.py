#!/usr/bin/env python3
"""Local HTTP bridge for the dev_stack status frontend."""

from __future__ import annotations

import argparse
import http.cookies
import json
import os
import posixpath
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence

from admin_auth import fingerprint, read_state, verify_password


SUPPORTED_SCHEMA_VERSION = 1
READ_ONLY_METHODS = {"GET", "HEAD"}
INSTANCE_NAME = re.compile(r"^[a-z0-9-]+$")
SESSION_COOKIE = "dev_stack_admin_session"
PUBLIC_FILES = {"/login", "/login.html", "/login.css", "/login.js"}
FORBIDDEN_KEYS = {
    "access_token",
    "api_key",
    "auth_key",
    "password",
    "password_hash",
    "private_key",
    "secret",
    "token",
}


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_KEYS or normalized.endswith("_password"):
                return True
            if _contains_forbidden_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def validate_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("status output is not a JSON object")
    if value.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError("status output has an unsupported schema version")
    if not isinstance(value.get("host"), dict):
        raise ValueError("status output is missing host details")
    if not isinstance(value.get("tailnet"), dict):
        raise ValueError("status output is missing tailnet details")
    if not isinstance(value.get("services"), list):
        raise ValueError("status output is missing the services list")
    if _contains_forbidden_key(value):
        raise ValueError("status output contains a forbidden secret field")
    return value


class StatusBridge:
    """Run exactly one approved CLI command and briefly cache its JSON output."""

    def __init__(
        self,
        dev_stack_path: Path,
        *,
        timeout_seconds: float = 8.0,
        cache_seconds: float = 3.0,
    ) -> None:
        self.dev_stack_path = dev_stack_path
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached_value: dict[str, Any] | None = None

    @property
    def command(self) -> list[str]:
        return [str(self.dev_stack_path), "status", "--json"]

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if self._cached_value is not None and now - self._cached_at < self.cache_seconds:
                return self._cached_value

            completed = subprocess.run(
                self.command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.dev_stack_path.parent,
                env={**os.environ, "NO_COLOR": "1"},
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or f"exit status {completed.returncode}"
                raise RuntimeError(f"dev_stack status failed: {detail}")
            try:
                value = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"dev_stack status returned invalid JSON: {error}") from error
            snapshot = validate_snapshot(value)
            self._cached_value = snapshot
            self._cached_at = time.monotonic()
            return snapshot

    def invalidate(self) -> None:
        with self._lock:
            self._cached_at = 0.0
            self._cached_value = None


class OperationInProgressError(RuntimeError):
    """Raised when a lifecycle operation is already running."""


class FileBrowserOperationBridge:
    """Run only the allowlisted File Browser lifecycle commands."""

    ACTIONS = {"start", "stop", "restart", "clean"}

    def __init__(self, dev_stack_path: Path, *, timeout_seconds: float = 15.0) -> None:
        self.dev_stack_path = dev_stack_path
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def command(self, action: str) -> list[str]:
        if action not in self.ACTIONS:
            raise ValueError("unsupported filebrowser action")
        return [str(self.dev_stack_path), "filebrowser", action]

    def run(self, action: str) -> dict[str, str]:
        command = self.command(action)
        if not self._lock.acquire(blocking=False):
            raise OperationInProgressError("another File Browser operation is already running")
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.dev_stack_path.parent,
                env={**os.environ, "NO_COLOR": "1"},
            )
        finally:
            self._lock.release()
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit status {completed.returncode}"
            raise RuntimeError(f"File Browser {action} failed: {detail[:1000]}")
        return {"service": "filebrowser", "action": action, "status": "completed"}


class PorterminalOperationBridge:
    """Run only the allowlisted Porterminal lifecycle commands."""

    ACTIONS = {"start", "stop", "restart", "clean"}

    def __init__(self, dev_stack_path: Path, *, timeout_seconds: float = 20.0) -> None:
        self.dev_stack_path = dev_stack_path
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def command(self, action: str) -> list[str]:
        if action not in self.ACTIONS:
            raise ValueError("unsupported porterminal action")
        command = [str(self.dev_stack_path), "porterminal", action]
        if action in {"start", "restart"}:
            command.append("--tailscale-serve")
        return command

    def run(self, action: str) -> dict[str, str]:
        command = self.command(action)
        if not self._lock.acquire(blocking=False):
            raise OperationInProgressError("another Porterminal operation is already running")
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.dev_stack_path.parent,
                env={**os.environ, "NO_COLOR": "1"},
            )
        finally:
            self._lock.release()
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit status {completed.returncode}"
            raise RuntimeError(f"Porterminal {action} failed: {detail[:1000]}")
        return {"service": "porterminal", "action": action, "status": "completed"}


class Go2RTCOperationBridge:
    """Run only the allowlisted go2rtc lifecycle commands."""

    ACTIONS = {"start", "stop", "restart", "clean", "publish", "unpublish"}

    def __init__(self, dev_stack_path: Path, *, timeout_seconds: float = 90.0) -> None:
        self.dev_stack_path = dev_stack_path
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def command(self, action: str) -> list[str]:
        if action not in self.ACTIONS:
            raise ValueError("unsupported go2rtc action")
        return [str(self.dev_stack_path), "go2rtc", action]

    def run(self, action: str) -> dict[str, str]:
        command = self.command(action)
        if not self._lock.acquire(blocking=False):
            raise OperationInProgressError("another go2rtc operation is already running")
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.dev_stack_path.parent,
                env={**os.environ, "NO_COLOR": "1"},
            )
        finally:
            self._lock.release()
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit status {completed.returncode}"
            raise RuntimeError(f"go2rtc {action} failed: {detail[:1000]}")
        return {"service": "go2rtc", "action": action, "status": "completed"}


class CodeServerOperationBridge:
    """Run only allowlisted lifecycle commands for one named Code Server instance."""

    ACTIONS = {"start", "stop", "restart"}
    GLOBAL_ACTIONS = {"clean"}
    PROFILE_ACTIONS = {"profile-save", "profile-start", "profile-delete"}

    def __init__(self, dev_stack_path: Path, *, timeout_seconds: float = 20.0) -> None:
        self.dev_stack_path = dev_stack_path
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()

    def command(
        self,
        action: str,
        instance: str | None = None,
        workspace: str | None = None,
    ) -> list[str]:
        if action in self.GLOBAL_ACTIONS:
            if instance is not None or workspace is not None:
                raise ValueError(f"code-server {action} does not accept instance or workspace values")
            return [str(self.dev_stack_path), "code-server", action]
        if action in self.PROFILE_ACTIONS:
            if instance is None or not INSTANCE_NAME.fullmatch(instance) or workspace is not None:
                raise ValueError("invalid Code Server profile action")
            profile_action = action.removeprefix("profile-")
            return [str(self.dev_stack_path), "code-server", "profile", profile_action, instance]
        if action == "start-workspace":
            if instance is None or not INSTANCE_NAME.fullmatch(instance):
                raise ValueError("invalid code-server instance name")
            if (
                not isinstance(workspace, str)
                or not Path(workspace).is_absolute()
                or not workspace.strip()
                or "\x00" in workspace
                or len(workspace) > 4096
            ):
                raise ValueError("workspace must be an absolute path")
            return [
                str(self.dev_stack_path),
                "code-server",
                "start",
                "--instance-name",
                instance,
                "--workspace-dir",
                workspace,
            ]
        if action not in self.ACTIONS:
            raise ValueError("unsupported code-server action")
        if workspace is not None:
            raise ValueError(f"code-server {action} does not accept a workspace")
        if instance is None or not INSTANCE_NAME.fullmatch(instance):
            raise ValueError("invalid code-server instance name")
        return [
            str(self.dev_stack_path),
            "code-server",
            action,
            "--instance-name",
            instance,
        ]

    def run(
        self,
        action: str,
        instance: str | None = None,
        workspace: str | None = None,
    ) -> dict[str, str]:
        command = self.command(action, instance, workspace)
        if not self._lock.acquire(blocking=False):
            raise OperationInProgressError("another Code Server operation is already running")
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=self.dev_stack_path.parent,
                env={**os.environ, "NO_COLOR": "1"},
            )
        finally:
            self._lock.release()
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit status {completed.returncode}"
            raise RuntimeError(f"Code Server {action} failed: {detail[:1000]}")
        result = {
            "service": "code-server",
            "action": action,
            "status": "completed",
        }
        if instance is not None:
            result["instance"] = instance
        return result


class CredentialBridge:
    """Run only the credential-read CLI surface for an allowlisted target."""

    SERVICES = {"code-server", "filebrowser", "porterminal", "go2rtc"}

    def __init__(self, dev_stack_path: Path, *, timeout_seconds: float = 5.0) -> None:
        self.dev_stack_path = dev_stack_path
        self.timeout_seconds = timeout_seconds

    def command(self, service: str, instance: str | None) -> list[str]:
        if service not in self.SERVICES:
            raise ValueError("unsupported credential service")
        if service == "code-server":
            if not instance or not INSTANCE_NAME.fullmatch(instance):
                raise ValueError("code-server credential requires a valid instance name")
        elif instance is not None:
            raise ValueError(f"{service} does not accept an instance name")

        argv = [str(self.dev_stack_path), "credentials", "show", "--service", service]
        if instance is not None:
            argv.extend(["--instance-name", instance])
        argv.append("--json")
        return argv

    def reveal(self, service: str, instance: str | None) -> dict[str, Any]:
        completed = subprocess.run(
            self.command(service, instance),
            check=False,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            cwd=self.dev_stack_path.parent,
            env={**os.environ, "NO_COLOR": "1"},
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or f"exit status {completed.returncode}"
            raise RuntimeError(f"credential lookup failed: {detail}")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"credential lookup returned invalid JSON: {error}") from error
        if not isinstance(value, dict) or not isinstance(value.get("password"), str):
            raise RuntimeError("credential lookup returned an invalid response")
        if value.get("service") != service or value.get("instance") != instance:
            raise RuntimeError("credential lookup response target mismatch")
        username = value.get("username")
        if username is not None and not isinstance(username, str):
            raise RuntimeError("credential lookup returned an invalid username")
        return {
            "service": service,
            "instance": instance,
            "username": username,
            "password": value["password"],
            "clear_after_seconds": 30,
        }


class AdminSessionManager:
    """Validate the master password and hold short-lived server-side sessions."""

    def __init__(
        self,
        *,
        admin_state: Path,
        session_seconds: int = 1800,
    ) -> None:
        self.admin_state = admin_state
        self.session_seconds = session_seconds
        self._lock = threading.Lock()
        self._login_verification_gate = threading.BoundedSemaphore(value=1)
        self._sessions: dict[str, tuple[float, str]] = {}
        self._failed_logins: list[float] = []

    def _current_state(self) -> tuple[dict[str, Any], str]:
        state = read_state(self.admin_state)
        return state, fingerprint(state)

    def session_authorized(self, cookie_header: str | None) -> bool:
        if not cookie_header:
            return False
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(cookie_header)
        except http.cookies.CookieError:
            return False
        morsel = cookie.get(SESSION_COOKIE)
        if morsel is None:
            return False
        now = time.time()
        with self._lock:
            self._sessions = {
                token: value for token, value in self._sessions.items() if value[0] > now
            }
            session = self._sessions.get(morsel.value)
        if session is None:
            return False
        try:
            _, current_fingerprint = self._current_state()
        except ValueError:
            return False
        return session[1] == current_fingerprint

    def login(self, password: str) -> str:
        if not self._login_verification_gate.acquire(timeout=5):
            raise PermissionError("another login verification is in progress; try again")
        try:
            now = time.time()
            with self._lock:
                self._failed_logins = [value for value in self._failed_logins if now - value < 60]
                if len(self._failed_logins) >= 5:
                    raise PermissionError("too many login attempts; wait one minute")
            state, state_fingerprint = self._current_state()
            if not verify_password(password, state):
                with self._lock:
                    self._failed_logins.append(now)
                raise ValueError("master password is incorrect")
            token = secrets.token_urlsafe(32)
            with self._lock:
                self._sessions[token] = (time.time() + self.session_seconds, state_fingerprint)
                self._failed_logins.clear()
            return token
        finally:
            self._login_verification_gate.release()

    def logout(self, cookie_header: str | None) -> None:
        token = self._cookie_token(cookie_header)
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    @staticmethod
    def _cookie_token(cookie_header: str | None) -> str | None:
        if not cookie_header:
            return None
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(cookie_header)
        except http.cookies.CookieError:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel is not None else None


class DevStackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[SimpleHTTPRequestHandler],
        *,
        bridge: StatusBridge,
        credential_bridge: CredentialBridge | None = None,
        filebrowser_operations: FileBrowserOperationBridge | None = None,
        porterminal_operations: PorterminalOperationBridge | None = None,
        go2rtc_operations: Go2RTCOperationBridge | None = None,
        code_server_operations: CodeServerOperationBridge | None = None,
        admin_sessions: AdminSessionManager | None = None,
        public_path: str = "/dev-stack",
        static_dir: Path,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.bridge = bridge
        self.credential_bridge = credential_bridge
        self.filebrowser_operations = filebrowser_operations
        self.porterminal_operations = porterminal_operations
        self.go2rtc_operations = go2rtc_operations
        self.code_server_operations = code_server_operations
        self.admin_sessions = admin_sessions
        self.public_path = public_path.rstrip("/") or "/"
        self.static_dir = static_dir


class DevStackRequestHandler(SimpleHTTPRequestHandler):
    server_version = "dev-stack-www/1"

    @property
    def app_server(self) -> DevStackHTTPServer:
        return self.server  # type: ignore[return-value]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        directory = kwargs.pop("directory", None)
        if directory is None and len(args) >= 3:
            server = args[2]
            directory = getattr(server, "static_dir", None)
        super().__init__(*args, directory=str(directory) if directory else None, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; connect-src 'self'; frame-ancestors 'none'; "
            "form-action 'none'; img-src 'self' data:; script-src 'self'; style-src 'self'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        cache_control: str = "no-store",
        head_only: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _normalized_path(self) -> str:
        parsed = urllib.parse.urlsplit(self.path)
        decoded = urllib.parse.unquote(parsed.path)
        return posixpath.normpath(decoded)

    def _request_host(self) -> str:
        host = self.headers.get("Host", "")
        if host.startswith("["):
            return host.partition("]")[0].lstrip("[").casefold()
        return host.split(":", 1)[0].casefold()

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return False
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError:
            return False
        return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == self.headers.get(
            "Host", ""
        ).casefold()

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("invalid content length") from error
        if length <= 0 or length > 4096:
            raise ValueError("request body must be between 1 and 4096 bytes")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("content type must be application/json")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            raise ValueError("request body is not valid JSON") from error
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _authenticated(self) -> bool:
        sessions = self.app_server.admin_sessions
        return bool(sessions and sessions.session_authorized(self.headers.get("Cookie")))

    def _session_cookie(self, token: str, *, clear: bool = False) -> str:
        origin = urllib.parse.urlsplit(self.headers.get("Origin", ""))
        local = self._request_host() in {"127.0.0.1", "localhost", "::1"}
        path = "/" if local else f"{self.app_server.public_path}/"
        max_age = 0 if clear else self.app_server.admin_sessions.session_seconds  # type: ignore[union-attr]
        attributes = [
            f"{SESSION_COOKIE}={token}",
            f"Path={path}",
            f"Max-Age={max_age}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if origin.scheme == "https" and not local:
            attributes.append("Secure")
        return "; ".join(attributes)

    def _redirect_to_login(self) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "./login")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _serve_static(self, *, head_only: bool, alias: str | None = None) -> None:
        original_path = self.path
        if alias:
            self.path = alias
        try:
            if head_only:
                super().do_HEAD()
            else:
                super().do_GET()
        finally:
            self.path = original_path

    def _serve_read(self, *, head_only: bool) -> None:
        path = self._normalized_path()
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "healthy", "mode": "limited-operations", "schema_version": 1},
                head_only=head_only,
            )
            return
        if path in PUBLIC_FILES:
            if path in {"/login", "/login.html"} and self._authenticated():
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "./")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            alias = "/login.html" if path == "/login" else None
            self._serve_static(head_only=head_only, alias=alias)
            return
        if not self._authenticated():
            if path.startswith("/api/"):
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "authentication_required", "message": "Master password login required"},
                    head_only=head_only,
                )
            else:
                self._redirect_to_login()
            return
        if path == "/api/status":
            try:
                snapshot = self.app_server.bridge.snapshot()
            except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "status_unavailable", "message": str(error)},
                    head_only=head_only,
                )
                return
            self._send_json(HTTPStatus.OK, snapshot, head_only=head_only)
            return

        if path.startswith("/api/"):
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error": "not_found", "message": "Unknown API endpoint"},
                head_only=head_only,
            )
            return

        self._serve_static(head_only=head_only)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._serve_read(head_only=False)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
        self._serve_read(head_only=True)

    def _reject_write(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", ", ".join(sorted(READ_ONLY_METHODS)))
        body = b'{"error":"method_not_allowed","message":"Method not allowed for this endpoint"}'
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = self._normalized_path()
        if path != "/api/session/login" and not self._authenticated():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "authentication_required", "message": "Master password login required"},
            )
            return
        action_targets = {
            "/api/services/code-server/actions": (
                self.app_server.code_server_operations,
                "Code Server",
            ),
            "/api/services/filebrowser/actions": (
                self.app_server.filebrowser_operations,
                "File Browser",
            ),
            "/api/services/porterminal/actions": (
                self.app_server.porterminal_operations,
                "Porterminal",
            ),
            "/api/services/go2rtc/actions": (
                self.app_server.go2rtc_operations,
                "go2rtc",
            ),
        }
        action_target = action_targets.get(path)
        if path not in {
            "/api/session/login",
            "/api/session/logout",
            "/api/credentials/reveal",
            *action_targets,
        }:
            self._reject_write()
            return
        expected_request = "operation" if action_target else "credentials"
        if self.headers.get("X-Dev-Stack-Request") != expected_request or not self._same_origin():
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"error": "request_rejected", "message": "Same-origin dashboard request required"},
            )
            return
        try:
            body = self._read_json_body()
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request", "message": str(error)})
            return

        sessions = self.app_server.admin_sessions
        if path == "/api/session/login":
            password = body.get("password")
            if sessions is None or not isinstance(password, str) or not 1 <= len(password) <= 1024:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_request", "message": "A master password is required"},
                )
                return
            try:
                token = sessions.login(password)
            except PermissionError as error:
                self._send_json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "rate_limited", "message": str(error)})
                return
            except ValueError as error:
                error_code = "admin_not_configured" if "not configured" in str(error) else "login_failed"
                status = HTTPStatus.SERVICE_UNAVAILABLE if error_code == "admin_not_configured" else HTTPStatus.UNAUTHORIZED
                self._send_json(status, {"error": error_code, "message": str(error)})
                return
            self._send_json(
                HTTPStatus.OK,
                {"status": "authenticated", "expires_in_seconds": sessions.session_seconds},
                extra_headers={"Set-Cookie": self._session_cookie(token)},
            )
            return

        if not self._authenticated():
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"error": "authentication_required", "message": "Master password login required"},
            )
            return

        if action_target:
            operation_bridge, service_label = action_target
            code_server_action = path == "/api/services/code-server/actions"
            code_server_clean = code_server_action and body.get("action") == "clean"
            code_server_workspace_start = code_server_action and body.get("action") == "start-workspace"
            expected_keys = (
                {"action"}
                if code_server_clean or not code_server_action
                else {"action", "instance", "workspace"}
                if code_server_workspace_start
                else {"action", "instance"}
            )
            if (
                set(body) != expected_keys
                or not isinstance(body.get("action"), str)
                or (
                    code_server_action
                    and not code_server_clean
                    and not isinstance(body.get("instance"), str)
                )
                or (
                    code_server_workspace_start
                    and not isinstance(body.get("workspace"), str)
                )
            ):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_request", "message": f"A single {service_label} action is required"},
                )
                return
            if operation_bridge is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "operations_unavailable", "message": f"{service_label} operations are disabled"},
                )
                return
            try:
                result = (
                    operation_bridge.run(body["action"], body.get("instance"), body.get("workspace"))
                    if code_server_action
                    else operation_bridge.run(body["action"])
                )
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_action", "message": str(error)})
                return
            except OperationInProgressError as error:
                self._send_json(HTTPStatus.CONFLICT, {"error": "operation_in_progress", "message": str(error)})
                return
            except subprocess.TimeoutExpired:
                self._send_json(
                    HTTPStatus.GATEWAY_TIMEOUT,
                    {"error": "operation_timed_out", "message": f"{service_label} operation timed out"},
                )
                return
            except (OSError, RuntimeError) as error:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "operation_failed", "message": str(error)},
                )
                return
            finally:
                self.app_server.bridge.invalidate()
            self._send_json(HTTPStatus.OK, result)
            return

        if path == "/api/session/logout":
            if sessions is not None:
                sessions.logout(self.headers.get("Cookie"))
            self._send_json(
                HTTPStatus.OK,
                {"status": "logged_out"},
                extra_headers={"Set-Cookie": self._session_cookie("", clear=True)},
            )
            return

        service = body.get("service")
        instance = body.get("instance")
        if not isinstance(service, str) or (instance is not None and not isinstance(instance, str)):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": "Invalid credential target"},
            )
            return
        bridge = self.app_server.credential_bridge
        if bridge is None:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "credentials_unavailable", "message": "Credential bridge is disabled"},
            )
            return
        try:
            credential = bridge.reveal(service, instance)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_target", "message": str(error)})
            return
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "credentials_unavailable", "message": str(error)},
            )
            return
        self._send_json(HTTPStatus.OK, credential)

    do_PUT = _reject_write
    do_PATCH = _reject_write
    do_DELETE = _reject_write

    def log_message(self, format_string: str, *args: Any) -> None:
        print(
            f"{self.log_date_time_string()} {self.client_address[0]} {format_string % args}",
            file=sys.stderr,
        )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    state_root = Path(
        os.environ.get("DEV_STACK_STATE_ROOT", Path.home() / ".bin" / "state" / "dev-stack")
    )
    parser = argparse.ArgumentParser(description="Serve the dev_stack status and operations frontend")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1080)
    parser.add_argument("--dev-stack", type=Path, default=project_root / "dev_stack")
    parser.add_argument("--static-dir", type=Path, default=project_root / "frontend")
    parser.add_argument("--status-timeout", type=float, default=8.0)
    parser.add_argument("--cache-seconds", type=float, default=3.0)
    parser.add_argument(
        "--admin-state",
        type=Path,
        default=state_root / "state-admin.json",
    )
    parser.add_argument("--public-path", default="/dev-stack")
    parser.add_argument("--session-seconds", type=int, default=1800)
    arguments = parser.parse_args(argv)
    if arguments.bind_host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("phase 1 only permits loopback bind hosts")
    if not 0 < arguments.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not arguments.dev_stack.is_file() or not os.access(arguments.dev_stack, os.X_OK):
        parser.error(f"dev_stack executable not found: {arguments.dev_stack}")
    if not arguments.static_dir.is_dir():
        parser.error(f"static directory not found: {arguments.static_dir}")
    if not arguments.public_path.startswith("/") or ".." in arguments.public_path:
        parser.error("public path must be an absolute URL path")
    if not 60 <= arguments.session_seconds <= 86400:
        parser.error("session seconds must be between 60 and 86400")
    try:
        read_state(arguments.admin_state)
    except ValueError as error:
        parser.error(str(error))
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    bridge = StatusBridge(
        arguments.dev_stack,
        timeout_seconds=arguments.status_timeout,
        cache_seconds=arguments.cache_seconds,
    )
    credential_bridge = CredentialBridge(
        arguments.dev_stack,
        timeout_seconds=arguments.status_timeout,
    )
    filebrowser_operations = FileBrowserOperationBridge(
        arguments.dev_stack,
        timeout_seconds=max(arguments.status_timeout, 15.0),
    )
    porterminal_operations = PorterminalOperationBridge(
        arguments.dev_stack,
        timeout_seconds=max(arguments.status_timeout, 20.0),
    )
    go2rtc_operations = Go2RTCOperationBridge(
        arguments.dev_stack,
        timeout_seconds=max(arguments.status_timeout, 90.0),
    )
    code_server_operations = CodeServerOperationBridge(
        arguments.dev_stack,
        timeout_seconds=max(arguments.status_timeout, 30.0),
    )
    admin_sessions = AdminSessionManager(
        admin_state=arguments.admin_state,
        session_seconds=arguments.session_seconds,
    )
    server = DevStackHTTPServer(
        (arguments.bind_host, arguments.port),
        DevStackRequestHandler,
        bridge=bridge,
        credential_bridge=credential_bridge,
        filebrowser_operations=filebrowser_operations,
        porterminal_operations=porterminal_operations,
        go2rtc_operations=go2rtc_operations,
        code_server_operations=code_server_operations,
        admin_sessions=admin_sessions,
        public_path=arguments.public_path,
        static_dir=arguments.static_dir,
    )
    print(
        f"dev_stack www listening on http://{arguments.bind_host}:{arguments.port} (limited operations)",
        file=sys.stderr,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
