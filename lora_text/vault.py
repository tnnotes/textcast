"""
Passphrase protection for identity files (.lkey / .ltid) and the message log.

File format (identical for keys-<nick>.lkey and <nick>.ltid backups):
    b"LTID1" | salt(16) | nonce(12) | ciphertext | tag(16)
    enc ‖ mac = scrypt(passphrase, salt, n=2^15, r=8, p=1, 64 bytes)
    ct  = ChaCha20(enc, nonce) XOR json
    tag = HMAC-SHA256(mac, "LTID1" ‖ salt ‖ nonce ‖ ct)[:16]

The slow scrypt step runs once per unlock; later saves reuse the derived keys
with a fresh nonce.

Log text is encrypted with keys derived from a random `log_secret` stored
inside the identity, so changing the passphrase never breaks old logs:
    "enc1:" + base64( nonce(12) ‖ ChaCha20(log_enc, nonce) XOR text ‖ tag(8) )
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Optional

from .crypto import chacha20_xor, derive

MAGIC = b"LTID1"
MIN_PASSPHRASE = 8


class WrongPassphrase(ValueError):
    pass


def _kdf(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    k = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=2 ** 15, r=8, p=1,
                       maxmem=64 * 1024 * 1024, dklen=64)
    return k[:32], k[32:]


class Vault:
    def __init__(self, salt: bytes, enc: bytes, mac: bytes):
        self.salt, self._enc, self._mac = salt, enc, mac

    @classmethod
    def create(cls, passphrase: str) -> "Vault":
        if len(passphrase) < MIN_PASSPHRASE:
            raise ValueError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
        salt = os.urandom(16)
        return cls(salt, *_kdf(passphrase, salt))

    @classmethod
    def open(cls, blob: bytes, passphrase: str) -> tuple[dict, "Vault"]:
        if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 16 + 12 + 16:
            raise ValueError("not a protected identity file")
        salt, nonce, ct, tag = blob[5:21], blob[21:33], blob[33:-16], blob[-16:]
        enc, mac = _kdf(passphrase, salt)
        want = hmac.new(mac, MAGIC + salt + nonce + ct, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(want, tag):
            raise WrongPassphrase("wrong passphrase (or damaged file)")
        data = json.loads(chacha20_xor(enc, nonce, ct).decode("utf-8"))
        return data, cls(salt, enc, mac)

    def seal(self, data: dict) -> bytes:
        nonce = os.urandom(12)
        ct = chacha20_xor(self._enc, nonce,
                          json.dumps(data, ensure_ascii=False).encode("utf-8"))
        tag = hmac.new(self._mac, MAGIC + self.salt + nonce + ct, hashlib.sha256).digest()[:16]
        return MAGIC + self.salt + nonce + ct + tag


def pack(data: dict, passphrase: str) -> bytes:
    return Vault.create(passphrase).seal(data)


def unpack(blob: bytes, passphrase: str) -> dict:
    return Vault.open(blob, passphrase)[0]


def is_protected_file(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5) == MAGIC
    except OSError:
        return False


class LogCipher:
    PREFIX = "enc1:"

    def __init__(self, secret: bytes):
        self._enc = derive(secret, "log/enc")
        self._mac = derive(secret, "log/mac")

    def encrypt(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        nonce = os.urandom(12)
        ct = chacha20_xor(self._enc, nonce, text.encode("utf-8"))
        tag = hmac.new(self._mac, nonce + ct, hashlib.sha256).digest()[:8]
        return self.PREFIX + base64.b64encode(nonce + ct + tag).decode()

    def decrypt(self, s: Optional[str]) -> Optional[str]:
        if s is None or not s.startswith(self.PREFIX):
            return s
        raw = base64.b64decode(s[len(self.PREFIX):])
        nonce, ct, tag = raw[:12], raw[12:-8], raw[-8:]
        if not hmac.compare_digest(hmac.new(self._mac, nonce + ct, hashlib.sha256).digest()[:8], tag):
            return None
        return chacha20_xor(self._enc, nonce, ct).decode("utf-8", "replace")
