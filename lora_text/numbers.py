"""
TextCast numbers: a 10-digit, phone-style name for an identity key.

No server hands numbers out: the number is computed from the public key,

    d      = scrypt(pub, salt="textcast/number/v1", n=2^14, r=8, p=1, 8 bytes)
    body   = 100000000 + int(d, big-endian) mod 900000000      (9 digits, no leading 0)
    number = body * 10 + luhn_check(body)                      (10 digits)

shown as "512 384 9076". The slow scrypt makes it expensive to search for a
key that has somebody else's number; trust-on-first-use and the QR code
(full key) cover the rest.

Finding a person by number over the air (frame type DIRECTORY, version 1,
no encryption - the answer is a public key, which is public):

    lookup:  [mode][ 'Q' ][number, 5 bytes big-endian]                     7 B
    answer:  [mode][ 'A' ][answer id][part<<4 | parts][chunk]
             chunks joined = pub(32) || nick (UTF-8)

The answer is split into parts when it does not fit one frame (SF9+).
The asker checks number_of(pub) == number before saving the contact.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from .protocol import PRIVATE_VERSION, make_mode, parse_mode

SALT = b"textcast/number/v1"
DIRECTORY = 3                      # frame type (mode byte bits 3-5)
OP_LOOKUP, OP_ANSWER = 0x51, 0x41  # 'Q', 'A'
ANSWER_HEAD = 4                    # mode, op, answer id, part/parts


class NumberError(ValueError):
    pass


def _luhn(digits: str) -> int:
    total = 0
    for k, ch in enumerate(reversed(digits)):
        d = int(ch)
        if k % 2 == 0:             # the digit next to the check digit is doubled
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def number_of(pub: bytes) -> str:
    """The 10-digit TextCast number of a public key, as a digit string."""
    d = hashlib.scrypt(pub, salt=SALT, n=2 ** 14, r=8, p=1, dklen=8)
    body = str(100_000_000 + int.from_bytes(d, "big") % 900_000_000)
    return body + str(_luhn(body))


def format_number(n: str) -> str:
    """'5123849076' -> '512 384 9076'"""
    return f"{n[:3]} {n[3:6]} {n[6:]}" if len(n) == 10 else n


def parse_number(s: str) -> str:
    """Accept '512 384 9076', '512-384-9076', … Raises NumberError."""
    n = re.sub(r"[\s.\-()+]", "", s or "")
    if not n.isdigit() or len(n) != 10:
        raise NumberError("a TextCast number has 10 digits")
    if n[0] == "0" or _luhn(n[:9]) != int(n[9]):
        raise NumberError("that number has a typo (check digit does not match)")
    return n


def _mode() -> int:
    return make_mode(DIRECTORY, version=PRIVATE_VERSION)


def make_lookup(number: str) -> bytes:
    return bytes([_mode(), OP_LOOKUP]) + int(number).to_bytes(5, "big")


def make_answer(pub: bytes, nick: str, answer_id: int, max_payload: int) -> list[bytes]:
    data = pub + nick.encode("utf-8")
    size = max(1, max_payload - ANSWER_HEAD)
    chunks = [data[k:k + size] for k in range(0, len(data), size)]
    if len(chunks) > 15:
        raise NumberError("radio profile too small for a number answer")
    return [bytes([_mode(), OP_ANSWER, answer_id & 0xFF, (j << 4) | len(chunks)]) + c
            for j, c in enumerate(chunks)]


def parse_directory(frame: bytes) -> Optional[tuple]:
    """('lookup', number) | ('answer', answer_id, part, parts, chunk) | None"""
    if len(frame) < 2:
        return None
    version, mtype, _ = parse_mode(frame[0])
    if version != PRIVATE_VERSION or mtype != DIRECTORY:
        return None
    if frame[1] == OP_LOOKUP and len(frame) == 7:
        n = int.from_bytes(frame[2:7], "big")
        return ("lookup", str(n)) if 1_000_000_000 <= n <= 9_999_999_999 else None
    if frame[1] == OP_ANSWER and len(frame) > ANSWER_HEAD:
        part, parts = frame[3] >> 4, frame[3] & 0x0F
        if parts and part < parts:
            return ("answer", frame[2], part, parts, frame[ANSWER_HEAD:])
    return None


def is_directory(frame: bytes) -> bool:
    return len(frame) >= 1 and parse_mode(frame[0])[:2] == (PRIVATE_VERSION, DIRECTORY)
