from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INIT = PROJECT_ROOT / "init.sh"


class InitTests(unittest.TestCase):
    def _environment(self, root: Path) -> dict[str, str]:
        test_bin = root / "test-bin"
        test_bin.mkdir()
        tailscale = test_bin / "tailscale"
        tailscale.write_text(
            "#!/bin/sh\n"
            "case \"$1 $2\" in\n"
            "  \"status --json\"|\"serve status\") printf '{}\\n'; exit 0 ;;\n"
            "esac\n"
            "exit 1\n",
            encoding="utf-8",
        )
        tailscale.chmod(0o755)
        return {
            **os.environ,
            "HOME": str(root / "home"),
            "NO_COLOR": "1",
            "PATH": f"{test_bin}:{os.environ.get('PATH', '')}",
        }

    def _run(
        self,
        prefix: Path,
        environment: dict[str, str],
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(INIT), "--prefix", str(prefix), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )

    def test_fresh_install_is_idempotent_and_services_can_be_toggled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = self._environment(root)
            prefix = root / "prefix"

            first = self._run(
                prefix,
                environment,
                "--no-code-server",
                "--no-filebrowser",
                "--no-porterminal",
                "--no-go2rtc",
                "--no-www",
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            installed_root = prefix / "src" / "dev-stack"
            runtime_home = prefix / ".dev-stack"
            command_path = prefix / "dev_stack"
            self.assertEqual(command_path.resolve(), installed_root / "dev_stack")
            self.assertTrue((installed_root / "TODO.md").is_file())
            self.assertFalse(any(installed_root.rglob("__pycache__")))
            self.assertEqual(
                stat.S_IMODE((runtime_home / "config" / "settings.json").stat().st_mode),
                0o600,
            )
            for name in ("config", "state", "data", "secrets", "logs"):
                self.assertEqual(
                    stat.S_IMODE((runtime_home / name).stat().st_mode),
                    0o700,
                )

            disabled = subprocess.run(
                [str(command_path), "code-server", "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env=environment,
            )
            self.assertEqual(disabled.returncode, 4)
            self.assertIn("Service code-server is disabled", disabled.stderr)

            status = subprocess.run(
                [str(command_path), "status", "--json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                env=environment,
            )
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["services"], [])

            enabled = self._run(
                prefix,
                environment,
                "--code-server",
                "--filebrowser",
                "--go2rtc",
                "--www",
            )
            self.assertEqual(enabled.returncode, 0, enabled.stderr)
            self.assertNotIn("SyntaxError", enabled.stdout + enabled.stderr)
            self.assertIn(
                str(Path(environment["HOME"]) / "workspace" / "iap_rosws" / ".env"),
                enabled.stdout,
            )
            self.assertIn("The dashboard master password is not configured", enabled.stdout)
            self.assertIn(
                f"Run: {prefix / 'dev_stack'} admin password reset",
                enabled.stdout,
            )
            self.assertIn(
                "File Browser has no database or saved admin password yet",
                enabled.stdout,
            )
            self.assertIn(
                f"Run: {prefix / 'dev_stack'} filebrowser start",
                enabled.stdout,
            )
            self.assertIn(
                f"To rotate it afterward, run: {prefix / 'dev_stack'} filebrowser password reset",
                enabled.stdout,
            )
            settings = json.loads(
                (runtime_home / "config" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertTrue(settings["services"]["code-server"]["enabled"])
            self.assertTrue(settings["services"]["filebrowser"]["enabled"])
            self.assertTrue(settings["services"]["go2rtc"]["enabled"])
            self.assertTrue(settings["services"]["www"]["enabled"])

            (runtime_home / "data" / "filebrowser.db").write_bytes(b"database fixture")
            checked = self._run(prefix, environment, "--check")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn("Installation layout", checked.stdout)
            self.assertIn("The File Browser admin password is not saved", checked.stdout)
            self.assertIn(
                f"Run: {prefix / 'dev_stack'} filebrowser password reset",
                checked.stdout,
            )

    def test_changed_installation_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = self._environment(root)
            prefix = root / "prefix"
            first = self._run(
                prefix,
                environment,
                "--no-code-server",
                "--no-filebrowser",
                "--no-porterminal",
                "--no-go2rtc",
                "--no-www",
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            readme = prefix / "src" / "dev-stack" / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\nlocal edit\n",
                encoding="utf-8",
            )
            settings = prefix / ".dev-stack" / "config" / "settings.json"
            settings_before = settings.read_bytes()

            repeated = self._run(prefix, environment)
            self.assertEqual(repeated.returncode, 1)
            self.assertIn("installed code has local changes", repeated.stderr)
            self.assertIn(str(prefix / "src" / "dev-stack"), repeated.stderr)
            self.assertEqual(settings.read_bytes(), settings_before)

    def test_enabled_porterminal_is_discovered_from_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = self._environment(root)
            prefix = root / "prefix"
            test_bin = Path(environment["PATH"].split(os.pathsep, 1)[0])
            porterminal = test_bin / "porterminal"
            porterminal.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            porterminal.chmod(0o755)

            completed = self._run(
                prefix,
                environment,
                "--no-code-server",
                "--no-filebrowser",
                "--porterminal",
                "--no-go2rtc",
                "--no-www",
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "[ok]      porterminal",
                completed.stdout,
            )
            self.assertNotIn(
                str(Path(environment["HOME"]) / ".bin" / "src" / "porterminal"),
                completed.stdout + completed.stderr,
            )

    def test_conflicting_command_path_stops_before_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = self._environment(root)
            prefix = root / "prefix"
            prefix.mkdir()
            command_path = prefix / "dev_stack"
            command_path.write_text("legacy\n", encoding="utf-8")

            completed = self._run(prefix, environment)

            self.assertEqual(completed.returncode, 1)
            self.assertIn("command path exists", completed.stderr)
            self.assertEqual(command_path.read_text(encoding="utf-8"), "legacy\n")
            self.assertFalse((prefix / "src" / "dev-stack").exists())
            self.assertFalse((prefix / ".dev-stack").exists())
