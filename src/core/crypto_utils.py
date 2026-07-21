"""Encryption utilities using Windows DPAPI (Data Protection API).

DPAPI encrypts data tied to the current user's login credentials,
so the encrypted blob can only be decrypted by the same user on the
same machine.  No extra dependencies required — uses ctypes to call
crypt32.dll directly.

The encrypted data is returned as a base64 string for safe storage
in JSON config files.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes

# ── Windows DPAPI constants ──────────────────────────────────

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CRYPTPROTECT_LOCAL_MACHINE = 0x4  # unused — we want per-user encryption


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


# ── ctypes wrappers ──────────────────────────────────────────

_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32


def _protect(plaintext: bytes) -> bytes:
    """Encrypt plaintext bytes using DPAPI (per-user, no UI prompt)."""
    data_in = _DATA_BLOB()
    data_in.cbData = len(plaintext)
    data_in.pbData = ctypes.cast(
        ctypes.create_string_buffer(plaintext, len(plaintext)),
        ctypes.POINTER(ctypes.c_char),
    )

    data_out = _DATA_BLOB()

    ok = _crypt32.CryptProtectData(
        ctypes.byref(data_in),
        None,  # description (optional)
        None,  # entropy (optional)
        None,  # reserved
        None,  # prompt struct
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptProtectData failed")

    try:
        result = ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        _kernel32.LocalFree(data_out.pbData)

    return result


def _unprotect(ciphertext: bytes) -> bytes:
    """Decrypt ciphertext bytes previously encrypted by _protect()."""
    data_in = _DATA_BLOB()
    data_in.cbData = len(ciphertext)
    data_in.pbData = ctypes.cast(
        ctypes.create_string_buffer(ciphertext, len(ciphertext)),
        ctypes.POINTER(ctypes.c_char),
    )

    data_out = _DATA_BLOB()

    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None,  # description out (optional)
        None,  # entropy (optional)
        None,  # reserved
        None,  # prompt struct
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed — wrong user or machine?")

    try:
        result = ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        _kernel32.LocalFree(data_out.pbData)

    return result


# ── Public API ───────────────────────────────────────────────

def encrypt(plaintext: str) -> str:
    """Encrypt a string and return a base64-encoded encrypted blob."""
    if not plaintext:
        return ""
    encrypted = _protect(plaintext.encode("utf-8"))
    return base64.b64encode(encrypted).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded blob back to the original string."""
    if not ciphertext:
        return ""
    raw = base64.b64decode(ciphertext.encode("ascii"))
    decrypted = _unprotect(raw)
    return decrypted.decode("utf-8")
