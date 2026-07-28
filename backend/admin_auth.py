#!/usr/bin/env python3
"""Master-password verifier storage for the dev_stack dashboard."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = 1
ALGORITHM = "scrypt"
DEFAULT_N = 1 << 17
DEFAULT_R = 8
DEFAULT_P = 1
DEFAULT_DKLEN = 32
MAXMEM = 256 * 1024 * 1024
MINIMUM_PASSWORD_LENGTH = 8
MAXIMUM_PASSWORD_BYTES = 1024


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"admin verifier {label} is invalid")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ValueError(f"admin verifier {label} is invalid") from error


def validate_password(password: str) -> None:
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise ValueError(
            f"master password must contain at least {MINIMUM_PASSWORD_LENGTH} characters"
        )
    if not any(character.isdigit() for character in password):
        raise ValueError("master password must contain at least one number")
    if not any(
        not character.isalnum() and not character.isspace() for character in password
    ):
        raise ValueError("master password must contain at least one special character")
    if len(password.encode("utf-8")) > MAXIMUM_PASSWORD_BYTES:
        raise ValueError(f"master password must be at most {MAXIMUM_PASSWORD_BYTES} UTF-8 bytes")


def create_state(
    password: str,
    *,
    n: int = DEFAULT_N,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
    salt: bytes | None = None,
) -> dict[str, Any]:
    validate_password(password)
    salt_value = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_value,
        n=n,
        r=r,
        p=p,
        maxmem=MAXMEM,
        dklen=DEFAULT_DKLEN,
    )
    return {
        "version": SCHEMA_VERSION,
        "verifier": {
            "algorithm": ALGORITHM,
            "salt_b64": _encode(salt_value),
            "digest_b64": _encode(digest),
            "n": n,
            "r": r,
            "p": p,
            "dklen": DEFAULT_DKLEN,
        },
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _validated_verifier(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict) or state.get("version") != SCHEMA_VERSION:
        raise ValueError("admin state has an unsupported version")
    verifier = state.get("verifier")
    if not isinstance(verifier, dict) or verifier.get("algorithm") != ALGORITHM:
        raise ValueError("admin state has an unsupported verifier")
    for name in ("n", "r", "p", "dklen"):
        if not isinstance(verifier.get(name), int) or verifier[name] <= 0:
            raise ValueError(f"admin verifier parameter {name} is invalid")
    if verifier["n"] & (verifier["n"] - 1):
        raise ValueError("admin verifier parameter n must be a power of two")
    _decode(verifier.get("salt_b64"), "salt")
    digest = _decode(verifier.get("digest_b64"), "digest")
    if len(digest) != verifier["dklen"]:
        raise ValueError("admin verifier digest length is invalid")
    return verifier


def read_state(path: Path, *, require_private: bool = True) -> dict[str, Any]:
    try:
        if require_private and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError(f"admin state must be mode 0600: {path}")
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError("master password is not configured") from error
    except json.JSONDecodeError as error:
        raise ValueError("admin state is not valid JSON") from error
    _validated_verifier(state)
    return state


def verify_password(password: str, state: dict[str, Any]) -> bool:
    verifier = _validated_verifier(state)
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(verifier["salt_b64"], "salt"),
            n=verifier["n"],
            r=verifier["r"],
            p=verifier["p"],
            maxmem=MAXMEM,
            dklen=verifier["dklen"],
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, _decode(verifier["digest_b64"], "digest"))


def fingerprint(state: dict[str, Any]) -> str:
    verifier = _validated_verifier(state)
    canonical = json.dumps(verifier, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def write_state(path: Path, state: dict[str, Any]) -> None:
    _validated_verifier(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="state-admin.tmp.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def reset_interactive(path: Path) -> None:
    first = getpass.getpass("New dev_stack master password: ")
    second = getpass.getpass("Confirm dev_stack master password: ")
    if first != second:
        raise ValueError("master password confirmation does not match")
    write_state(path, create_state(first))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the dev_stack master-password verifier")
    parser.add_argument("command", choices=["reset", "status"])
    parser.add_argument("--state-file", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_args(argv)
    if arguments.command == "reset":
        try:
            reset_interactive(arguments.state_file)
        except ValueError as error:
            print(f"Error: {error}", file=os.sys.stderr)
            return 1
        print("dev_stack master password updated. Existing dashboard sessions are invalidated.")
        return 0
    try:
        state = read_state(arguments.state_file)
    except ValueError as error:
        print(f"not configured: {error}")
        return 1
    print(f"configured: yes\nupdated: {state.get('updated_at', 'unknown')}\nstate: {arguments.state_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
