from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


DEV_STACK = Path(__file__).resolve().parents[1] / "dev_stack"


class Go2RTCCLITests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict[str, str], Path, Path]:
        home = root / "home"
        home.mkdir()
        state_root = home / ".bin" / "state" / "dev-stack"
        state_root.mkdir(parents=True)
        state_file = state_root / "state-go2rtc.json"
        state_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "instances": {},
                    "credentials": {
                        "go2rtc": {
                            "username": "admin",
                            "password": "fixture-go2rtc-password",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        state_file.chmod(0o600)
        auth_env = state_root / "go2rtc-auth.env"
        auth_env.write_text(
            "GO2RTC_API_USERNAME=admin\n"
            "GO2RTC_API_PASSWORD=fixture-go2rtc-password\n",
            encoding="utf-8",
        )
        auth_env.chmod(0o600)
        deployment = root / "go2rtc"
        deployment.mkdir(parents=True)
        (deployment / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (deployment / "compose.webcam.yaml").write_text("services: {}\n", encoding="utf-8")
        (deployment / "go2rtc.yaml").write_text("streams: {}\n", encoding="utf-8")
        env_file = root / "iap.env"
        env_file.write_text(
            "REOLINK_MAIN_RTSP_URL=rtsp://secret-main\n"
            "REOLINK_SUB_RTSP_URL=rtsp://secret-sub\n",
            encoding="utf-8",
        )
        brio_device = root / "video-brio"
        brio_device.touch()
        sound_device = root / "snd"
        sound_device.mkdir()

        runtime_state = root / "container-state"
        runtime_state.write_text("not_found\n", encoding="utf-8")
        health_state = root / "health-state"
        health_state.write_text("healthy\n", encoding="utf-8")
        route_state = root / "route-state"
        route_state.write_text("absent\n", encoding="utf-8")
        argument_log = root / "docker-arguments.log"
        tailscale_log = root / "tailscale-arguments.log"

        test_bin = root / "test-bin"
        test_bin.mkdir()
        docker = test_bin / "docker"
        docker.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu

                first="true"
                for argument in "$@"; do
                  if [ "$first" = "true" ]; then
                    first="false"
                  else
                    printf '|' >> "$GO2RTC_TEST_ARGUMENT_LOG"
                  fi
                  printf '%s' "$argument" >> "$GO2RTC_TEST_ARGUMENT_LOG"
                done
                printf '\\n' >> "$GO2RTC_TEST_ARGUMENT_LOG"

                if [ "${1:-}" = "compose" ] && [ "${2:-}" = "version" ]; then
                  exit 0
                fi
                if [ "${1:-}" = "inspect" ]; then
                  printf '%s\\n' '2026-07-24T14:00:00Z'
                  exit 0
                fi
                [ "${1:-}" = "compose" ]
                shift
                [ "${1:-}" = "--project-directory" ]
                shift 2
                [ "${1:-}" = "-f" ]
                shift 2
                while [ "${1:-}" = "-f" ]; do
                  shift 2
                done
                command="${1:-}"
                shift || true

                case "$command" in
                  config)
                    exit 0
                    ;;
                  ps)
                    state="$(cat "$GO2RTC_TEST_RUNTIME_STATE")"
                    case "$state" in
                      not_found)
                        exit 0
                        ;;
                      stopped)
                        printf '%s\\n' '{"ID":"abc123","Service":"go2rtc","State":"exited","Status":"Exited (0)"}'
                        ;;
                      running)
                        printf '%s\\n' '{"ID":"abc123","Service":"go2rtc","State":"running","Status":"Up"}'
                        ;;
                      invalid)
                        printf '%s\\n' 'not-json'
                        ;;
                      error)
                        exit 1
                        ;;
                    esac
                    ;;
                  up)
                    printf '%s\\n' running > "$GO2RTC_TEST_RUNTIME_STATE"
                    ;;
                  stop)
                    printf '%s\\n' stopped > "$GO2RTC_TEST_RUNTIME_STATE"
                    ;;
                  restart)
                    printf '%s\\n' running > "$GO2RTC_TEST_RUNTIME_STATE"
                    ;;
                  down)
                    printf '%s\\n' not_found > "$GO2RTC_TEST_RUNTIME_STATE"
                    ;;
                  logs)
                    printf '%s\\n' 'go2rtc-test-log'
                    ;;
                  *)
                    exit 2
                    ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        docker.chmod(0o755)

        curl = test_bin / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            '[ "$(cat "$GO2RTC_TEST_HEALTH_STATE")" = "healthy" ]\n',
            encoding="utf-8",
        )
        curl.chmod(0o755)

        ss = test_bin / "ss"
        ss.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        ss.chmod(0o755)

        tailscale = test_bin / "tailscale"
        tailscale.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu
                printf '%s\\n' "$*" >> "$GO2RTC_TEST_TAILSCALE_LOG"
                if [ "${1:-}" = "status" ] && [ "${2:-}" = "--json" ]; then
                  printf '%s\\n' '{"Self":{"DNSName":"test-host.example.ts.net."}}'
                  exit 0
                fi
                if [ "${1:-}" = "serve" ] && [ "${2:-}" = "status" ] && [ "${3:-}" = "--json" ]; then
                  case "$(cat "$GO2RTC_TEST_ROUTE_STATE")" in
                    present)
                      printf '%s\\n' '{"Web":{"test-host.example.ts.net:443":{"Handlers":{"/go2rtc":{"Proxy":"http://127.0.0.1:1984"}}}}}'
                      ;;
                    foreign)
                      printf '%s\\n' '{"Web":{"test-host.example.ts.net:443":{"Handlers":{"/go2rtc":{"Proxy":"http://127.0.0.1:9999"}}}}}'
                      ;;
                    *)
                      printf '%s\\n' '{"Web":{"test-host.example.ts.net:443":{"Handlers":{}}}}'
                      ;;
                  esac
                  exit 0
                fi
                if [ "$*" = "serve --bg --https=443 --set-path /go2rtc http://127.0.0.1:1984" ]; then
                  printf '%s\\n' present > "$GO2RTC_TEST_ROUTE_STATE"
                  exit 0
                fi
                if [ "$*" = "serve --https=443 --set-path /go2rtc off" ]; then
                  printf '%s\\n' absent > "$GO2RTC_TEST_ROUTE_STATE"
                  exit 0
                fi
                exit 1
                """
            ),
            encoding="utf-8",
        )
        tailscale.chmod(0o755)

        environment = {
            **os.environ,
            "HOME": str(home),
            "NO_COLOR": "1",
            "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
            "DEV_STACK_HOME": str(home / ".dev-stack-test"),
            "DEV_STACK_STATE_ROOT": str(state_root),
            "DEV_STACK_GO2RTC_DIR": str(deployment),
            "DEV_STACK_GO2RTC_ENV_FILE": str(env_file),
            "DEV_STACK_GO2RTC_BRIO_DEVICE": str(brio_device),
            "DEV_STACK_GO2RTC_SOUND_DEVICE": str(sound_device),
            "DEV_STACK_GO2RTC_AUTH_ENV_FILE": str(auth_env),
            "GO2RTC_TEST_ARGUMENT_LOG": str(argument_log),
            "GO2RTC_TEST_RUNTIME_STATE": str(runtime_state),
            "GO2RTC_TEST_HEALTH_STATE": str(health_state),
            "GO2RTC_TEST_ROUTE_STATE": str(route_state),
            "GO2RTC_TEST_TAILSCALE_LOG": str(tailscale_log),
        }
        return environment, runtime_state, argument_log

    def _run(
        self,
        environment: dict[str, str],
        *arguments: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(DEV_STACK), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
            cwd=cwd,
        )

    def test_status_json_maps_compose_and_http_states_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, _argument_log = self._fixture(root)
            health_state = Path(environment["GO2RTC_TEST_HEALTH_STATE"])
            route_state = Path(environment["GO2RTC_TEST_ROUTE_STATE"])
            cases = [
                ("not_found", "healthy", "absent", "not_found"),
                ("stopped", "healthy", "absent", "stopped"),
                ("running", "healthy", "present", "running"),
                ("running", "healthy", "absent", "drifted"),
                ("running", "unhealthy", "present", "drifted"),
                ("invalid", "healthy", "absent", "unknown"),
                ("error", "healthy", "absent", "unknown"),
            ]

            for compose_state, http_state, route, expected_status in cases:
                with self.subTest(
                    compose_state=compose_state,
                    http_state=http_state,
                    route=route,
                ):
                    runtime_state.write_text(f"{compose_state}\n", encoding="utf-8")
                    health_state.write_text(f"{http_state}\n", encoding="utf-8")
                    route_state.write_text(f"{route}\n", encoding="utf-8")
                    completed = self._run(
                        environment,
                        "go2rtc",
                        "status",
                        "--json",
                        cwd=root,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    payload = json.loads(completed.stdout)
                    self.assertEqual(payload["id"], "go2rtc")
                    self.assertEqual(payload["status"], expected_status)
                    self.assertEqual(payload["bind_host"], "127.0.0.1")
                    self.assertEqual(payload["port"], 1984)
                    self.assertEqual(
                        payload["credential"],
                        {"available": True, "username": "admin"},
                    )
                    self.assertTrue(payload["route"]["enabled"])
                    self.assertEqual(payload["route"]["path"], "/go2rtc")
                    self.assertEqual(payload["route"]["present"], route == "present")
                    if route == "present":
                        self.assertEqual(
                            payload["url"],
                            "https://test-host.example.ts.net/go2rtc/",
                        )
                    else:
                        self.assertIsNone(payload["url"])
                    serialized = json.dumps(payload)
                    self.assertNotIn("secret-main", serialized)
                    self.assertNotIn("secret-sub", serialized)
                    self.assertNotIn("fixture-go2rtc-password", serialized)
                    self.assertNotIn("REOLINK_", serialized)
                    if expected_status in {"running", "drifted"}:
                        self.assertIsInstance(payload["uptime_seconds"], int)
                    else:
                        self.assertIsNone(payload["uptime_seconds"])

    def test_lifecycle_uses_fixed_compose_project_from_any_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, argument_log = self._fixture(root)
            route_state = Path(environment["GO2RTC_TEST_ROUTE_STATE"])
            outside = root / "outside"
            outside.mkdir()

            started = self._run(environment, "go2rtc", "start", cwd=outside)
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "running")
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "present")

            stopped = self._run(environment, "go2rtc", "stop", cwd=outside)
            self.assertEqual(stopped.returncode, 0, stopped.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "stopped")
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "absent")

            restarted = self._run(environment, "go2rtc", "restart", cwd=outside)
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "running")
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "present")

            stopped_again = self._run(environment, "go2rtc", "stop", cwd=outside)
            self.assertEqual(stopped_again.returncode, 0, stopped_again.stderr)
            cleaned = self._run(environment, "go2rtc", "clean", cwd=outside)
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "not_found")
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "absent")

            calls = argument_log.read_text(encoding="utf-8").splitlines()
            prefix = (
                f"compose|--project-directory|{environment['DEV_STACK_GO2RTC_DIR']}"
                f"|-f|{environment['DEV_STACK_GO2RTC_DIR']}/compose.yaml"
                f"|-f|{environment['DEV_STACK_GO2RTC_DIR']}/compose.webcam.yaml"
            )
            self.assertIn(f"{prefix}|config|-q", calls)
            self.assertIn(f"{prefix}|up|-d", calls)
            self.assertIn(f"{prefix}|stop", calls)
            self.assertIn(f"{prefix}|restart", calls)
            self.assertIn(f"{prefix}|down", calls)
            tailscale_calls = Path(
                environment["GO2RTC_TEST_TAILSCALE_LOG"]
            ).read_text(encoding="utf-8")
            self.assertIn(
                "serve --bg --https=443 --set-path /go2rtc http://127.0.0.1:1984",
                tailscale_calls,
            )
            self.assertIn(
                "serve --https=443 --set-path /go2rtc off",
                tailscale_calls,
            )

    def test_clean_refuses_a_running_container(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, argument_log = self._fixture(root)
            runtime_state.write_text("running\n", encoding="utf-8")

            completed = self._run(environment, "go2rtc", "clean")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Refusing to clean", completed.stderr)
            self.assertNotIn("|down", argument_log.read_text(encoding="utf-8"))
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "running")

    def test_tailscale_can_be_disabled_and_the_preference_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, _argument_log = self._fixture(root)
            route_state = Path(environment["GO2RTC_TEST_ROUTE_STATE"])

            started = self._run(
                environment,
                "go2rtc",
                "start",
                "--tailscale-no-serve",
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "running")
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "absent")

            status = self._run(environment, "go2rtc", "status", "--json")
            self.assertEqual(status.returncode, 0, status.stderr)
            payload = json.loads(status.stdout)
            self.assertEqual(payload["status"], "running")
            self.assertFalse(payload["route"]["enabled"])
            self.assertFalse(payload["route"]["present"])
            self.assertIsNone(payload["url"])

            started_again = self._run(environment, "go2rtc", "start")
            self.assertEqual(started_again.returncode, 0, started_again.stderr)
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "absent")

            published = self._run(environment, "go2rtc", "publish")
            self.assertEqual(published.returncode, 0, published.stderr)
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "present")
            published_status = json.loads(
                self._run(environment, "go2rtc", "status", "--json").stdout
            )
            self.assertTrue(published_status["route"]["enabled"])
            self.assertTrue(published_status["route"]["present"])
            self.assertEqual(
                published_status["url"],
                "https://test-host.example.ts.net/go2rtc/",
            )

            unpublished = self._run(environment, "go2rtc", "unpublish")
            self.assertEqual(unpublished.returncode, 0, unpublished.stderr)
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "absent")
            unpublished_status = json.loads(
                self._run(environment, "go2rtc", "status", "--json").stdout
            )
            self.assertFalse(unpublished_status["route"]["enabled"])
            self.assertIsNone(unpublished_status["url"])

    def test_start_bootstraps_an_owner_only_authentication_credential(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, argument_log = self._fixture(root)
            state_file = (
                Path(environment["HOME"])
                / ".bin"
                / "state"
                / "dev-stack"
                / "state-go2rtc.json"
            )
            state_file.write_text(
                json.dumps({"version": 1, "instances": {}}),
                encoding="utf-8",
            )
            auth_env = Path(environment["DEV_STACK_GO2RTC_AUTH_ENV_FILE"])
            auth_env.unlink()

            started = self._run(environment, "go2rtc", "start")
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "running")

            state = json.loads(state_file.read_text(encoding="utf-8"))
            credential = state["credentials"]["go2rtc"]
            self.assertEqual(credential["username"], "admin")
            self.assertRegex(credential["password"], r"^[0-9a-f]{32}$")
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(auth_env.stat().st_mode & 0o777, 0o600)
            self.assertIn(
                f"GO2RTC_API_PASSWORD={credential['password']}",
                auth_env.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "|up|-d|--force-recreate",
                argument_log.read_text(encoding="utf-8"),
            )

    def test_running_service_without_managed_authentication_is_drifted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, _argument_log = self._fixture(root)
            state_file = (
                Path(environment["HOME"])
                / ".bin"
                / "state"
                / "dev-stack"
                / "state-go2rtc.json"
            )
            state_file.write_text(
                json.dumps({"version": 1, "instances": {}}),
                encoding="utf-8",
            )
            Path(environment["DEV_STACK_GO2RTC_AUTH_ENV_FILE"]).unlink()
            runtime_state.write_text("running\n", encoding="utf-8")
            Path(environment["GO2RTC_TEST_ROUTE_STATE"]).write_text(
                "present\n",
                encoding="utf-8",
            )

            status = self._run(environment, "go2rtc", "status", "--json")
            self.assertEqual(status.returncode, 0, status.stderr)
            payload = json.loads(status.stdout)
            self.assertEqual(payload["status"], "drifted")
            self.assertFalse(payload["credential"]["available"])
            self.assertIn("credential", payload["health"]["message"])

    def test_password_reset_recreates_running_container_and_is_revealable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, argument_log = self._fixture(root)
            runtime_state.write_text("running\n", encoding="utf-8")
            Path(environment["GO2RTC_TEST_ROUTE_STATE"]).write_text(
                "present\n",
                encoding="utf-8",
            )

            reset = self._run(environment, "go2rtc", "password", "reset")
            self.assertEqual(reset.returncode, 0, reset.stderr)
            password_line = next(
                line for line in reset.stdout.splitlines() if line.strip().startswith("password:")
            )
            new_password = password_line.split(":", 1)[1].strip()
            self.assertRegex(new_password, r"^[0-9a-f]{32}$")
            self.assertNotEqual(new_password, "fixture-go2rtc-password")
            self.assertIn(
                "|up|-d|--force-recreate",
                argument_log.read_text(encoding="utf-8"),
            )

            revealed = self._run(
                environment,
                "credentials",
                "show",
                "--service",
                "go2rtc",
                "--json",
            )
            self.assertEqual(revealed.returncode, 0, revealed.stderr)
            credential = json.loads(revealed.stdout)
            self.assertEqual(credential["username"], "admin")
            self.assertEqual(credential["password"], new_password)
            auth_env = Path(environment["DEV_STACK_GO2RTC_AUTH_ENV_FILE"])
            self.assertIn(
                f"GO2RTC_API_PASSWORD={new_password}",
                auth_env.read_text(encoding="utf-8"),
            )
            self.assertEqual(auth_env.stat().st_mode & 0o777, 0o600)

    def test_start_refuses_to_replace_a_foreign_tailscale_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, _argument_log = self._fixture(root)
            route_state = Path(environment["GO2RTC_TEST_ROUTE_STATE"])
            route_state.write_text("foreign\n", encoding="utf-8")

            completed = self._run(environment, "go2rtc", "start")
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Route collision", completed.stderr)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "running")
            self.assertEqual(route_state.read_text(encoding="utf-8").strip(), "foreign")

    def test_logs_and_argument_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, _runtime_state, argument_log = self._fixture(root)

            logs = self._run(
                environment,
                "go2rtc",
                "logs",
                "--follow",
                "--tail",
                "25",
            )
            self.assertEqual(logs.returncode, 0, logs.stderr)
            self.assertEqual(logs.stdout.strip(), "go2rtc-test-log")
            expected = (
                f"compose|--project-directory|{environment['DEV_STACK_GO2RTC_DIR']}"
                f"|-f|{environment['DEV_STACK_GO2RTC_DIR']}/compose.yaml"
                f"|-f|{environment['DEV_STACK_GO2RTC_DIR']}/compose.webcam.yaml"
                "|logs|--follow|--tail|25|go2rtc"
            )
            self.assertIn(expected, argument_log.read_text(encoding="utf-8").splitlines())

            invalid_tail = self._run(environment, "go2rtc", "logs", "--tail", "all")
            self.assertNotEqual(invalid_tail.returncode, 0)
            self.assertIn("non-negative integer", invalid_tail.stderr)
            invalid_json = self._run(environment, "go2rtc", "start", "--json")
            self.assertNotEqual(invalid_json.returncode, 0)
            self.assertIn("only supported", invalid_json.stderr)

    def test_aggregate_status_and_clean_include_go2rtc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, runtime_state, argument_log = self._fixture(root)
            runtime_state.write_text("stopped\n", encoding="utf-8")

            status = self._run(environment, "status", cwd=root)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("--go2rtc:", status.stdout)
            self.assertIn("dev_stack go2rtc status", status.stdout)

            cleaned = self._run(environment, "clean", cwd=root)
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            self.assertIn("--go2rtc:", cleaned.stdout)
            self.assertEqual(runtime_state.read_text(encoding="utf-8").strip(), "not_found")
            self.assertIn("|down", argument_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
