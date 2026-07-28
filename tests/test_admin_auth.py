from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(MODULE_DIR))

from admin_auth import (  # noqa: E402
    create_state,
    fingerprint,
    read_state,
    validate_password,
    verify_password,
    write_state,
)


class AdminAuthTests(unittest.TestCase):
    def test_scrypt_verifier_is_private_and_never_stores_plaintext(self) -> None:
        password = "correct-test-master-password1"
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state-admin.json"
            state = create_state(password, n=1 << 10)
            write_state(path, state)

            serialized = path.read_text(encoding="utf-8")
            self.assertNotIn(password, serialized)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = read_state(path)
            self.assertTrue(verify_password(password, loaded))
            self.assertFalse(verify_password("incorrect-test-password", loaded))
            self.assertEqual(fingerprint(loaded), fingerprint(json.loads(serialized)))

    def test_master_password_policy(self) -> None:
        validate_password("secure1!")

        invalid_passwords = (
            ("short1!", "at least 8"),
            ("secure!!", "at least one number"),
            ("secure12", "at least one special character"),
            ("secure1 ", "at least one special character"),
        )
        for password, message in invalid_passwords:
            with self.subTest(password=password):
                with self.assertRaisesRegex(ValueError, message):
                    validate_password(password)

    def test_non_private_state_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state-admin.json"
            path.write_text(json.dumps(create_state("correct-test-password1", n=1 << 10)))
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "mode 0600"):
                read_state(path)


if __name__ == "__main__":
    unittest.main()
