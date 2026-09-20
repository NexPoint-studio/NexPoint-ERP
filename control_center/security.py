"""Password hashing isolated from the client ERP application package."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os


SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
        p=SCRYPT_P, dklen=32,
    )
    return "$".join((
        "scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        parameters = (int(n), int(r), int(p))
        if algorithm != "scrypt" or parameters != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        decoded_salt = base64.urlsafe_b64decode(salt)
        if len(decoded_salt) != 16:
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=decoded_salt, n=parameters[0],
            r=parameters[1], p=parameters[2], dklen=32,
        )
        return hmac.compare_digest(
            base64.urlsafe_b64encode(digest).decode("ascii"), expected
        )
    except (AttributeError, binascii.Error, OverflowError, TypeError, ValueError):
        return False

