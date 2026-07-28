from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path


DEV_STACK = Path(__file__).resolve().parents[1] / "dev_stack"


class SymlinkEntrypointTests(unittest.TestCase):
    def test_symlink_resolves_helpers_from_the_copied_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            home.mkdir()
            alias = root / "dev_stack_v2"
            alias.symlink_to(DEV_STACK)
            environment = {
                **os.environ,
                "HOME": str(home),
                "NO_COLOR": "1",
                "DEV_STACK_HOME": str(home / ".dev-stack-test"),
                "DEV_STACK_STATE_ROOT": str(home / ".bin" / "state" / "dev-stack"),
            }

            completed = subprocess.run(
                [str(alias), "admin", "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("not configured:", completed.stdout)
            self.assertNotIn("Missing admin authentication helper", completed.stderr)

    def test_symlink_status_uses_the_cli_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            state_root = home / ".bin" / "state" / "dev-stack"
            state_root.mkdir(parents=True)
            state_file = state_root / "state-code-server.json"
            state_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {},
                        "profiles": {
                            "shadow": {
                                "name": "shadow",
                                "workspace_dir": str(root),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            state_file.chmod(0o600)
            alias = root / "dev_stack_v2"
            alias.symlink_to(DEV_STACK)
            environment = {
                **os.environ,
                "HOME": str(home),
                "NO_COLOR": "1",
                "DEV_STACK_HOME": str(home / ".dev-stack-test"),
                "DEV_STACK_STATE_ROOT": str(state_root),
            }

            completed = subprocess.run(
                [str(alias), "status", "--json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            services = {
                service["id"]: service for service in json.loads(completed.stdout)["services"]
            }
            self.assertEqual(
                services["code-server"]["profiles"],
                [{"name": "shadow", "workspace": str(root)}],
            )


class PorterminalPathCommandTests(unittest.TestCase):
    def test_start_uses_path_installed_porterminal_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            home = root / "home"
            state_root = root / "state"
            log_root = root / "logs"
            test_bin = root / "test-bin"
            for directory in (home, state_root, log_root, test_bin):
                directory.mkdir()

            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]

            capture_path = root / "porterminal-launch.json"
            porterminal = test_bin / "porterminal"
            porterminal.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import os\n"
                "import sys\n"
                "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                "from pathlib import Path\n"
                "\n"
                "arguments = sys.argv[1:]\n"
                "def option(name):\n"
                "    return arguments[arguments.index(name) + 1]\n"
                "password = sys.stdin.readline().rstrip('\\n')\n"
                "Path(os.environ['PORTERMINAL_TEST_CAPTURE']).write_text(\n"
                "    json.dumps({\n"
                "        'argv0': sys.argv[0],\n"
                "        'arguments': arguments,\n"
                "        'config': os.environ.get('PORTERMINAL_CONFIG_PATH'),\n"
                "        'password': password,\n"
                "    }),\n"
                "    encoding='utf-8',\n"
                ")\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                "    def do_GET(self):\n"
                "        if self.path != '/health':\n"
                "            self.send_error(404)\n"
                "            return\n"
                "        body = b'{\"status\":\"healthy\",\"sessions\":0}'\n"
                "        self.send_response(200)\n"
                "        self.send_header('Content-Type', 'application/json')\n"
                "        self.send_header('Content-Length', str(len(body)))\n"
                "        self.end_headers()\n"
                "        self.wfile.write(body)\n"
                "    def log_message(self, format, *args):\n"
                "        pass\n"
                "HTTPServer((option('--host'), int(option('--port'))), Handler).serve_forever()\n",
                encoding="utf-8",
            )
            porterminal.chmod(0o755)
            route_state = root / "porterminal-route-state"
            route_state.write_text("absent\n", encoding="utf-8")
            tailscale = test_bin / "tailscale"
            tailscale.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                'if [ "${1:-}" = "status" ] && [ "${2:-}" = "--json" ]; then\n'
                "  printf '%s\\n' '{\"Self\":{\"DNSName\":\"test-host.example.ts.net.\"}}'\n"
                "  exit 0\n"
                "fi\n"
                'if [ "${1:-}" = "serve" ] && [ "${2:-}" = "status" ] '
                '&& [ "${3:-}" = "--json" ]; then\n'
                '  if [ "$(cat "$PORTERMINAL_TEST_ROUTE_STATE")" = "present" ]; then\n'
                "    printf '%s\\n' "
                "'{\"Web\":{\"test-host.example.ts.net:443\":{\"Handlers\":"
                "{\"/\":{\"Proxy\":\"http://127.0.0.1:"
                + str(port)
                + "\"}}}}}'\n"
                "  else\n"
                "    printf '%s\\n' "
                "'{\"Web\":{\"test-host.example.ts.net:443\":{\"Handlers\":{}}}}'\n"
                "  fi\n"
                "  exit 0\n"
                "fi\n"
                f'if [ "$*" = "serve --bg --https=443 --set-path / '
                f'http://127.0.0.1:{port}" ]; then\n'
                '  printf "%s\\n" present > "$PORTERMINAL_TEST_ROUTE_STATE"\n'
                "  exit 0\n"
                "fi\n"
                'if [ "$*" = "serve --https=443 --set-path / off" ]; then\n'
                '  printf "%s\\n" absent > "$PORTERMINAL_TEST_ROUTE_STATE"\n'
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            tailscale.chmod(0o755)

            dev_stack_home = root / "dev-stack-home"
            environment = {
                **os.environ,
                "HOME": str(home),
                "NO_COLOR": "1",
                "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
                "DEV_STACK_HOME": str(dev_stack_home),
                "DEV_STACK_STATE_ROOT": str(state_root),
                "DEV_STACK_LOG_ROOT": str(log_root),
                "PORTERMINAL_TEST_CAPTURE": str(capture_path),
                "PORTERMINAL_TEST_ROUTE_STATE": str(route_state),
            }
            started = subprocess.run(
                [
                    str(DEV_STACK),
                    "porterminal",
                    "start",
                    "--port",
                    str(port),
                    "--password",
                    "dashboard-secret",
                    "--tailscale-no-serve",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )
            try:
                self.assertEqual(started.returncode, 0, started.stderr)
                capture = json.loads(capture_path.read_text(encoding="utf-8"))
                self.assertEqual(capture["argv0"], str(porterminal))
                self.assertEqual(capture["password"], "dashboard-secret")
                self.assertEqual(
                    capture["config"],
                    str(dev_stack_home / "config" / "porterminal.yaml"),
                )
                self.assertEqual(
                    capture["arguments"],
                    [
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--snippets",
                        str(dev_stack_home / "data" / "porterminal-snippets.json"),
                        "--password-stdin",
                    ],
                )

                published = subprocess.run(
                    [str(DEV_STACK), "porterminal", "publish"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=environment,
                )
                self.assertEqual(published.returncode, 0, published.stderr)
                self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "present")
                state = json.loads(
                    (state_root / "state-porterminal.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    state["instances"]["porterminal"]["tailscale_enable"],
                    "true",
                )
                self.assertEqual(
                    state["instances"]["porterminal"]["tailscale_url"],
                    "https://test-host.example.ts.net/",
                )

                unpublished = subprocess.run(
                    [str(DEV_STACK), "porterminal", "unpublish"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=environment,
                )
                self.assertEqual(unpublished.returncode, 0, unpublished.stderr)
                self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "absent")
                state = json.loads(
                    (state_root / "state-porterminal.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    state["instances"]["porterminal"]["tailscale_enable"],
                    "false",
                )
                self.assertIsNone(state["instances"]["porterminal"]["tailscale_url"])

                restarted = subprocess.run(
                    [
                        str(DEV_STACK),
                        "porterminal",
                        "restart",
                        "--tailscale-serve",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    env=environment,
                )
                self.assertEqual(restarted.returncode, 0, restarted.stderr)
                self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "present")
                state = json.loads(
                    (state_root / "state-porterminal.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    state["instances"]["porterminal"]["tailscale_enable"],
                    "true",
                )
                self.assertEqual(
                    state["instances"]["porterminal"]["tailscale_url"],
                    "https://test-host.example.ts.net/",
                )
            finally:
                if (state_root / "state-porterminal.json").exists():
                    subprocess.run(
                        [str(DEV_STACK), "porterminal", "stop"],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        env=environment,
                    )


class SingletonCleanTests(unittest.TestCase):
    def test_clean_preserves_saved_password_outside_runtime_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            state_root = home / ".bin" / "state" / "dev-stack"
            state_root.mkdir(parents=True)
            state_file = state_root / "state-filebrowser.json"
            state_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {
                            "filebrowser": {
                                "status": "stopped",
                                "pid": None,
                                "password": "preserve-this-password",
                                "bind_host": "127.0.0.1",
                                "port": 9900,
                                "tailscale_enable": "false",
                                "tailscale_path": "/filebrowser",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            state_file.chmod(0o600)
            test_bin = home / "test-bin"
            test_bin.mkdir()
            for command in ("ss", "tailscale"):
                stub = test_bin / command
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(0o755)
            environment = {
                **os.environ,
                "HOME": str(home),
                "NO_COLOR": "1",
                "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
                "DEV_STACK_HOME": str(home / ".dev-stack-test"),
                "DEV_STACK_STATE_ROOT": str(state_root),
            }

            cleaned = subprocess.run(
                [str(DEV_STACK), "filebrowser", "clean"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("filebrowser", state["instances"])
            self.assertEqual(
                state["credentials"]["filebrowser"],
                {"username": "admin", "password": "preserve-this-password"},
            )

            revealed = subprocess.run(
                [
                    str(DEV_STACK),
                    "credentials",
                    "show",
                    "--service",
                    "filebrowser",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )
            self.assertEqual(revealed.returncode, 0, revealed.stderr)
            credential = json.loads(revealed.stdout)
            self.assertEqual(credential["username"], "admin")
            self.assertEqual(credential["password"], "preserve-this-password")

    def test_porterminal_clean_preserves_password_config_and_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory)
            state_root = home / ".bin" / "state" / "dev-stack"
            state_root.mkdir(parents=True)
            config_file = home / ".bin" / "src" / "porterminal" / ".ptn" / "run-on-localhost.yaml"
            config_file.parent.mkdir(parents=True)
            config_file.write_text("security:\n  password_hash: test-hash\n", encoding="utf-8")
            snippets_file = state_root / "porterminal-snippets.json"
            snippets_file.write_text('{"snippets":[{"name":"kept"}]}\n', encoding="utf-8")
            state_file = state_root / "state-porterminal.json"
            state_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "instances": {
                            "porterminal": {
                                "status": "stopped",
                                "pid": None,
                                "password": "preserve-porterminal-password",
                                "bind_host": "127.0.0.1",
                                "port": 9444,
                                "tailscale_enable": "false",
                                "tailscale_path": "/",
                                "config_path": str(config_file),
                                "snippets_path": str(snippets_file),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            state_file.chmod(0o600)
            test_bin = home / "test-bin"
            test_bin.mkdir()
            for command in ("ss", "tailscale"):
                stub = test_bin / command
                stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                stub.chmod(0o755)
            environment = {
                **os.environ,
                "HOME": str(home),
                "NO_COLOR": "1",
                "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
                "DEV_STACK_HOME": str(home / ".dev-stack-test"),
                "DEV_STACK_STATE_ROOT": str(state_root),
            }

            cleaned = subprocess.run(
                [str(DEV_STACK), "porterminal", "clean"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("porterminal", state["instances"])
            self.assertEqual(
                state["credentials"]["porterminal"],
                {"password": "preserve-porterminal-password"},
            )
            self.assertEqual(
                config_file.read_text(encoding="utf-8"),
                "security:\n  password_hash: test-hash\n",
            )
            self.assertEqual(
                snippets_file.read_text(encoding="utf-8"),
                '{"snippets":[{"name":"kept"}]}\n',
            )

            revealed = subprocess.run(
                [
                    str(DEV_STACK),
                    "credentials",
                    "show",
                    "--service",
                    "porterminal",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                env=environment,
            )
            self.assertEqual(revealed.returncode, 0, revealed.stderr)
            credential = json.loads(revealed.stdout)
            self.assertIsNone(credential["username"])
            self.assertEqual(credential["password"], "preserve-porterminal-password")


class CodeServerProfileTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, dict[str, str]]:
        home = root / "home"
        state_root = home / ".bin" / "state" / "dev-stack"
        state_root.mkdir(parents=True)
        workspace = root / "workspace"
        workspace.mkdir()
        state_file = state_root / "state-code-server.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "instances": {
                        "work": {
                            "instance_name": "work",
                            "workspace_dir": str(workspace / ".." / "workspace"),
                            "status": "running",
                            "pid": None,
                            "bind_host": "127.0.0.1",
                            "port": 9000,
                            "tailscale_enable": "false",
                            "tailscale_path": "/code-server-manager/work",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        state_file.chmod(0o600)
        test_bin = root / "test-bin"
        test_bin.mkdir()
        for command in ("ss", "tailscale"):
            stub = test_bin / command
            stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            stub.chmod(0o755)
        environment = {
            **os.environ,
            "HOME": str(home),
            "NO_COLOR": "1",
            "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
            "DEV_STACK_HOME": str(home / ".dev-stack-test"),
            "DEV_STACK_STATE_ROOT": str(state_root),
            "DEV_STACK_LOG_ROOT": str(root),
        }
        return state_file, workspace, environment

    def _run(self, environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DEV_STACK), "code-server", "profile", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
            env=environment,
        )

    def test_profile_save_list_clean_and_delete_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file, workspace, environment = self._fixture(Path(temporary_directory))

            saved = self._run(environment, "save", "work")
            self.assertEqual(saved.returncode, 0, saved.stderr)
            saved_again = self._run(environment, "save", "work")
            self.assertEqual(saved_again.returncode, 0, saved_again.stderr)

            listed = self._run(environment, "list", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(
                json.loads(listed.stdout),
                {"profiles": [{"name": "work", "workspace_dir": str(workspace.resolve())}]},
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(list(state["profiles"]), ["work"])
            self.assertEqual(set(state["profiles"]["work"]), {"name", "workspace_dir"})

            state["instances"]["work"]["status"] = "stopped"
            state_file.write_text(json.dumps(state), encoding="utf-8")
            cleaned = subprocess.run(
                [str(DEV_STACK), "code-server", "clean"],
                check=False,
                capture_output=True,
                text=True,
                timeout=12,
                env=environment,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            cleaned_state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertNotIn("work", cleaned_state["instances"])
            self.assertEqual(cleaned_state["profiles"]["work"]["workspace_dir"], str(workspace.resolve()))

            deleted = self._run(environment, "delete", "work")
            self.assertEqual(deleted.returncode, 0, deleted.stderr)
            self.assertEqual(json.loads(state_file.read_text(encoding="utf-8"))["profiles"], {})

    def test_profile_validation_errors_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_file, _workspace, environment = self._fixture(Path(temporary_directory))
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["instances"]["work"]["status"] = "stopped"
            state_file.write_text(json.dumps(state), encoding="utf-8")

            stopped = self._run(environment, "save", "work")
            self.assertNotEqual(stopped.returncode, 0)
            self.assertIn("is not running", stopped.stderr)
            missing_start = self._run(environment, "start", "missing")
            self.assertNotEqual(missing_start.returncode, 0)
            self.assertIn("profile not found", missing_start.stderr.lower())
            missing_delete = self._run(environment, "delete", "missing")
            self.assertNotEqual(missing_delete.returncode, 0)
            self.assertIn("profile not found", missing_delete.stderr.lower())
            unsafe = self._run(environment, "delete", "../unsafe")
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("Invalid instance name", unsafe.stderr)

    def test_profile_start_delegates_to_normal_named_workspace_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            state_file, workspace, environment = self._fixture(root)
            saved = self._run(environment, "save", "work")
            self.assertEqual(saved.returncode, 0, saved.stderr)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["instances"] = {}
            state_file.write_text(json.dumps(state), encoding="utf-8")

            argument_log = root / "code-server-arguments.txt"
            code_server = root / "test-bin" / "code-server"
            code_server.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CODE_SERVER_ARGUMENT_LOG\"\nexec sleep 30\n",
                encoding="utf-8",
            )
            code_server.chmod(0o755)
            tailscale = root / "test-bin" / "tailscale"
            tailscale.write_text(
                "#!/bin/sh\n"
                "if [ \"$1 $2\" = \"status --json\" ]; then\n"
                "  printf '%s\\n' '{\"Self\":{\"DNSName\":\"test.example.ts.net.\"}}'\n"
                "elif [ \"$1 $2 $3\" = \"serve status --json\" ]; then\n"
                "  printf '%s\\n' '{\"Web\":{}}'\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            tailscale.chmod(0o755)
            environment["CODE_SERVER_ARGUMENT_LOG"] = str(argument_log)

            started = self._run(environment, "start", "work")
            self.assertEqual(started.returncode, 0, started.stderr)
            arguments = argument_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(arguments[-1], str(workspace.resolve()))
            self.assertIn("--bind-addr", arguments)
            runtime_state = json.loads(state_file.read_text(encoding="utf-8"))["instances"]["work"]
            self.assertEqual(runtime_state["workspace_dir"], str(workspace.resolve()))
            self.assertIsInstance(runtime_state["pid"], int)
            try:
                os.killpg(runtime_state["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    unittest.main()
