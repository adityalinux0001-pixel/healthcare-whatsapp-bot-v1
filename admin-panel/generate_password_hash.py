#!/usr/bin/env python3
"""Generate an scrypt password hash for ADMIN_PASSWORD_HASH."""

import argparse
import base64
import hashlib
import secrets

N = 2**14
R = 8
P = 1
SALT_BYTES = 16
KEY_BYTES = 64


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=N,
        r=R,
        p=P,
        dklen=KEY_BYTES,
    )
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(derived)}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("password")
    args = parser.parse_args()
    print(hash_password(args.password))
