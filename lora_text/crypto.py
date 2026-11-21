"""
Rolling-tag crypto for SIGNED and SEALED frames (pure Python, stdlib only).

Frame (version 1):   [mode 1B][hint 1B][body][tag 5B]
  SIGNED: body = UTF-8 text (readable by all), tag lets key holders verify it
  SEALED: body = ChaCha20(text), only key holders can read it

No sender ID is sent. Each *stream* (one sender's key) uses a counter i;
only its low byte (the hint) is sent:

    i    = max(last_i + 1, floor, minute_now << 8)      (sender side)
    hint = i & 0xFF
    tag  = Trunc5( HMAC-SHA256(mac_key, mode ‖ i(8B) ‖ body) )
    ct   = ChaCha20(enc_key, nonce = 0x00000000 ‖ i(8B), counter = 1) XOR text

Receivers try the candidate counters that match the hint (about 14 per
stream: last_i+1.. and the current minutes ± skew) for every stream they hold,
and accept only text that decodes as UTF-8. A match means: right key,
authentic, and not a replay (i > last_i). The tag looks random on air; the
hint is usually the message number within the current minute.

ChaCha20 is implemented per RFC 8439 and checked against its test vector.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import time
from typing import Iterator, Optional

TAG_LEN = 5
HINT_LEN = 1
FRAME_OVERHEAD = 1 + HINT_LEN + TAG_LEN   # mode + hint + tag
SEQ_BITS = 8
EPOCH0 = 1767225600          # 2026-01-01T00:00:00Z
SKEW_MIN = 5                 # tolerated clock difference (minutes)
SEQ_MAX = 16                 # time-window seq values when no hint is given
AHEAD = 1024                 # counters searched after the last one seen

_M = 0xFFFFFFFF


# --- ChaCha20 (RFC 8439) ----------------------------------------------------

def _rotl(v: int, c: int) -> int:
    return ((v << c) & _M) | (v >> (32 - c))


def _qr(s: list, a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & _M; s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & _M; s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & _M; s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & _M; s[b] = _rotl(s[b] ^ s[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574,
             *struct.unpack("<8I", key), counter & _M, *struct.unpack("<3I", nonce)]
    w = state[:]
    for _ in range(10):
        _qr(w, 0, 4, 8, 12); _qr(w, 1, 5, 9, 13); _qr(w, 2, 6, 10, 14); _qr(w, 3, 7, 11, 15)
        _qr(w, 0, 5, 10, 15); _qr(w, 1, 6, 11, 12); _qr(w, 2, 7, 8, 13); _qr(w, 3, 4, 9, 14)
    return struct.pack("<16I", *[(w[i] + state[i]) & _M for i in range(16)])


def chacha20_xor(key: bytes, nonce: bytes, data: bytes, counter: int = 1) -> bytes:
    out = bytearray()
    for off in range(0, len(data), 64):
        ks = chacha20_block(key, counter + off // 64, nonce)
        out += bytes(a ^ b for a, b in zip(data[off:off + 64], ks))
    return bytes(out)


# --- keys --------------------------------------------------------------------

def derive(secret: bytes, label: str) -> bytes:
    return hmac.new(secret, b"lora-text/" + label.encode(), hashlib.sha256).digest()


def member_secret(group_secret: bytes, callsign: str) -> bytes:
    """Per-member stream secret inside a group (avoids nonce reuse between
    members sharing one group key)."""
    return derive(group_secret, "member/" + callsign.strip().lower())[:16]


class StreamKey:
    def __init__(self, secret: bytes):
        self.enc = derive(secret, "enc")
        self.mac = derive(secret, "mac")


# --- counters ------------------------------------------------------------------

def minute_now(t: Optional[float] = None) -> int:
    return int(((time.time() if t is None else t) - EPOCH0) // 60)


def next_counter(last_i: int, floor_i: int = 0, t: Optional[float] = None) -> int:
    return max(last_i + 1, floor_i, minute_now(t) << SEQ_BITS)


def startup_floor(t: Optional[float] = None) -> int:
    """First counter allowed after a restart: start of the next minute, so a
    lost keystore can never reuse a counter from before the restart."""
    return (minute_now(t) + 1) << SEQ_BITS


def candidates(last_i: int, t: Optional[float] = None,
               hint: Optional[int] = None) -> Iterator[int]:
    """Counters to try, nearest first. With a hint only those whose low byte
    equals the hint (~14 instead of ~1200)."""
    tried = set()
    if last_i > 0:
        start = last_i + 1
        if hint is None:
            ahead = range(start, start + AHEAD)
        else:
            i0 = (start & ~0xFF) | hint
            if i0 < start:
                i0 += 256
            ahead = range(i0, start + AHEAD, 256)
        for i in ahead:
            tried.add(i)
            yield i
    m = minute_now(t)
    seqs = range(SEQ_MAX) if hint is None else (hint,)
    for d in sorted(range(-SKEW_MIN, SKEW_MIN + 2), key=abs):
        for s in seqs:
            i = ((m + d) << SEQ_BITS) | s
            if i > last_i and i not in tried:
                yield i


# --- seal / open ---------------------------------------------------------------

def _nonce(i: int) -> bytes:
    return b"\x00\x00\x00\x00" + i.to_bytes(8, "big")


def _tag(k: StreamKey, mode: int, i: int, body: bytes) -> bytes:
    return hmac.new(k.mac, bytes([mode]) + i.to_bytes(8, "big") + body,
                    hashlib.sha256).digest()[:TAG_LEN]


def seal(k: StreamKey, mode: int, i: int, plaintext: bytes, encrypt: bool) -> bytes:
    body = chacha20_xor(k.enc, _nonce(i), plaintext) if encrypt else plaintext
    return bytes([mode, i & 0xFF]) + body + _tag(k, mode, i, body)


def verify(k: StreamKey, frame: bytes, i: int) -> bool:
    if len(frame) < FRAME_OVERHEAD or frame[1] != (i & 0xFF):
        return False
    return hmac.compare_digest(_tag(k, frame[0], i, frame[2:-TAG_LEN]), frame[-TAG_LEN:])


def unseal(k: StreamKey, frame: bytes, i: int, encrypted: bool) -> bytes:
    body = frame[2:-TAG_LEN]
    return chacha20_xor(k.enc, _nonce(i), body) if encrypted else body


def matching_counters(k: StreamKey, frame: bytes, last_i: int,
                      t: Optional[float] = None) -> Iterator[int]:
    """Counters for which the tag verifies (normally zero or one)."""
    if len(frame) < FRAME_OVERHEAD:
        return
    for i in candidates(last_i, t, frame[1]):
        if verify(k, frame, i):
            yield i


def find_counter(k: StreamKey, frame: bytes, last_i: int,
                 t: Optional[float] = None) -> Optional[int]:
    return next(matching_counters(k, frame, last_i, t), None)
