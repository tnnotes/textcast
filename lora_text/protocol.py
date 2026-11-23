"""
Portable protocol core for the TextCast.

Pure logic, no I/O: framing, the mode byte, the air profile, airtime maths and
the rate limiter. Keep it dependency-free so it can be ported to Kotlin/Swift/C.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# ---------------------------------------------------------------------------
# Air profile and airtime contract (network-wide: every node must match)
# ---------------------------------------------------------------------------


@dataclass
class AirProfile:
    name: str = "AS923-2"
    freq_mhz: float = 920.800
    sf: int = 7
    bw_hz: int = 125_000
    cr: int = 1                 # 1 = 4/5 ... 4 = 4/8
    preamble: int = 8
    crc: bool = True
    explicit_header: bool = True
    private_sync: bool = True   # 0x1424 (not LoRaWAN public 0x3444)
    tx_power_dbm: int = 14      # confirm against your local regulation

    # airtime contract
    contract_max: int = 100     # app payload cap (bytes, incl. mode byte)
    max_payload: int = 100      # effective cap for this profile (see effective_payload)
    rate_per_s: float = 0.1     # average 1 message per 10 s
    burst: int = 3              # short bursts allowed
    backoff_max_s: float = 2.0  # random delay before every TX
    max_dwell_ms: Optional[float] = 400.0   # None = no dwell-time limit
    duty_cycle: Optional[float] = 0.01      # per device, rolling hour; None = no cap


# Bytes the radio firmware adds on air, and its payload limit per SF. Defaults
# are the Dragino LA66 P2P firmware v1.2.4 (1 group byte; 230/123/59 B);
# set_hardware_limits() changes them for other hardware.
FIRMWARE_OVERHEAD = 1
_LIMIT = {5: 230, 6: 230, 7: 230, 8: 230, 9: 123, 10: 59, 11: 59, 12: 59}
_LIMIT_DEFAULT = 59


def firmware_limit(sf: int) -> int:
    return _LIMIT.get(sf, _LIMIT_DEFAULT)


def set_hardware_limits(limits: dict, overhead: int) -> None:
    """limits: {"7": 230, ...} or {"*": 255} (from a hardware profile)."""
    global FIRMWARE_OVERHEAD, _LIMIT, _LIMIT_DEFAULT
    FIRMWARE_OVERHEAD = int(overhead)
    _LIMIT = {int(k): int(v) for k, v in limits.items() if k != "*"}
    _LIMIT_DEFAULT = int(limits.get("*", min(_LIMIT.values()) if _LIMIT else 255))


def airtime_ms(payload_len: int, p: Optional[AirProfile] = None,
               firmware_overhead: Optional[int] = None) -> float:
    """Semtech time-on-air formula for one LoRa packet."""
    p = p or PROFILE
    n = payload_len + (FIRMWARE_OVERHEAD if firmware_overhead is None else firmware_overhead)
    tsym = (2 ** p.sf) / p.bw_hz * 1000.0
    de = 1 if tsym > 16.0 else 0
    h = 0 if p.explicit_header else 1
    num = 8 * n - 4 * p.sf + 28 + (16 if p.crc else 0) - 20 * h
    n_payload = 8 + max(math.ceil(num / (4 * (p.sf - 2 * de))) * (p.cr + 4), 0)
    return (p.preamble + 4.25) * tsym + n_payload * tsym


def effective_payload(p: AirProfile) -> int:
    """Largest application payload allowed by the contract, the firmware and
    the dwell-time limit for this profile."""
    lim = min(p.contract_max, firmware_limit(p.sf) - FIRMWARE_OVERHEAD)
    if p.max_dwell_ms:
        while lim > 1 and airtime_ms(lim, p) > p.max_dwell_ms:
            lim -= 1
    return lim


PROFILE = AirProfile()


def apply_profile(new: AirProfile) -> AirProfile:
    """Make `new` the active profile. Updates the shared PROFILE object in place,
    so every module that imported PROFILE sees the change."""
    new.max_payload = effective_payload(new)
    PROFILE.__dict__.update(new.__dict__)
    return PROFILE


# ---------------------------------------------------------------------------
# Mode byte:  [version:2][type:3][hop:2][reserved:1]
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = 0          # PLAIN frames
PRIVATE_VERSION = 1           # SIGNED / SEALED frames: [mode][hint][body][tag 5]
PRIVATE_TAG = 5


class MsgType(IntEnum):
    PLAIN = 0    # UTF-8 text, readable by everyone
    SIGNED = 1   # reserved: readable text + rolling MAC tag (phase 1b)
    SEALED = 2   # reserved: ciphertext + rolling MAC tag (phase 1b)


def make_mode(msg_type: int, hop: int = 0, version: int = PROTOCOL_VERSION) -> int:
    return ((version & 0x3) << 6) | ((int(msg_type) & 0x7) << 3) | ((hop & 0x3) << 1)


def parse_mode(b: int) -> tuple[int, int, int]:
    """Return (version, type, hop)."""
    return (b >> 6) & 0x3, (b >> 3) & 0x7, (b >> 1) & 0x3


# ---------------------------------------------------------------------------
# Plain-text framing and display conventions
# ---------------------------------------------------------------------------


class PayloadTooLong(ValueError):
    pass


def normalize(text: str) -> str:
    # NFC keeps Vietnamese diacritics precomposed (fewer bytes than NFD).
    return unicodedata.normalize("NFC", text)


def compose(text: str, callsign: Optional[str] = None) -> str:
    text = normalize(text.strip())
    return f"{callsign}: {text}" if callsign else text


def encode_plain(text: str, p: Optional[AirProfile] = None) -> bytes:
    p = p or PROFILE
    body = normalize(text).encode("utf-8")
    frame = bytes([make_mode(MsgType.PLAIN)]) + body
    if len(frame) > p.max_payload:
        raise PayloadTooLong(
            f"{len(frame)} B > {p.max_payload} B limit "
            f"({len(body)} B of text, max {p.max_payload - 1})")
    return frame


def frame_len_plain(text: str) -> int:
    return 1 + len(normalize(text).encode("utf-8"))


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_CALLSIGN = re.compile(r"^([^\s:]{1,16}): ")
_TOKENS = re.compile(r"[#@][\w\-]+")


@dataclass
class Decoded:
    kind: str                    # 'plain' | 'reserved' | 'undecoded'
    raw: bytes
    text: Optional[str] = None
    msg_type: Optional[int] = None
    hop: int = 0

    @property
    def callsign(self) -> Optional[str]:
        if self.text is None:
            return None
        m = _CALLSIGN.match(self.text)
        return m.group(1) if m else None

    @property
    def tokens(self) -> list[str]:
        return [t.lower() for t in _TOKENS.findall(self.text or "")]

    def display(self) -> str:
        if self.kind == "plain":
            return self.text or ""
        if self.kind == "reserved":
            if self.msg_type == MsgType.SIGNED and self.text is not None:
                return f"[unverified] {self.text}"
            name = MsgType(self.msg_type).name.lower()
            return f"[{len(self.raw)} B {name}]"
        return f"[{len(self.raw)} B undecoded]"


def decode(frame: bytes) -> Decoded:
    if not frame:
        return Decoded("undecoded", frame)
    version, mtype, hop = parse_mode(frame[0])
    if version == PROTOCOL_VERSION and mtype == MsgType.PLAIN:
        try:
            text = frame[1:].decode("utf-8")
        except UnicodeDecodeError:
            return Decoded("undecoded", frame)
        return Decoded("plain", frame, _CTRL.sub("\ufffd", text), mtype, hop)
    if version == PRIVATE_VERSION and mtype == MsgType.SIGNED and len(frame) > 2 + PRIVATE_TAG:
        try:  # readable by everyone, verifiable only with the key
            text = _CTRL.sub("\ufffd", frame[2:-PRIVATE_TAG].decode("utf-8"))
        except UnicodeDecodeError:
            text = None
        return Decoded("reserved", frame, text, mtype, hop)
    if version == PRIVATE_VERSION and mtype in (MsgType.SIGNED, MsgType.SEALED):
        return Decoded("reserved", frame, None, mtype, hop)
    return Decoded("undecoded", frame)


def matches(d: Decoded, filters: set[str]) -> bool:
    """Local subscription filter. Empty set = show everything.

    '#topic'  -> message contains the #topic token
    '@name'   -> callsign is 'name' or message mentions @name
    other     -> case-insensitive substring
    """
    if not filters:
        return True
    if d.kind != "plain":
        return False
    text_l = (d.text or "").lower()
    toks = d.tokens
    cs = (d.callsign or "").lower()
    for f in filters:
        fl = f.lower()
        if fl.startswith("#") and fl in toks:
            return True
        if fl.startswith("@") and (cs == fl[1:] or fl in toks):
            return True
        if not fl.startswith(("#", "@")) and fl in text_l:
            return True
    return False


# ---------------------------------------------------------------------------
# Rate limiting and airtime accounting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token bucket: average `rate` messages/s, bursts up to `burst`."""

    def __init__(self, rate: Optional[float] = None, burst: Optional[int] = None,
                 clock=time.monotonic):
        rate = PROFILE.rate_per_s if rate is None else rate
        burst = PROFILE.burst if burst is None else burst
        self.rate, self.capacity, self.clock = rate, burst, clock
        self.tokens = float(burst)
        self.t = clock()

    def _refill(self) -> None:
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
        self.t = now

    def wait_time(self) -> float:
        self._refill()
        return 0.0 if self.tokens >= 1 else (1 - self.tokens) / self.rate

    def try_consume(self) -> bool:
        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class AirtimeMeter:
    """Rolling sum of own TX airtime (default window: 1 hour)."""

    def __init__(self, window_s: float = 3600.0, clock=time.monotonic):
        self.window, self.clock, self.events = window_s, clock, deque()

    def add(self, ms: float) -> None:
        self.events.append((self.clock(), ms))

    def total_ms(self) -> float:
        cutoff = self.clock() - self.window
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()
        return sum(ms for _, ms in self.events)

    def duty_cycle(self) -> float:
        return self.total_ms() / (self.window * 1000.0)
