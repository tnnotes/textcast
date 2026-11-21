"""
Serial driver for the Dragino LA66 / LA66 USB running LA66 P2P firmware v1.2.4.

Behaviour confirmed on the bench (2026-11-21):
  * RX is printed unsolicited:  'rxDone' / 'Data: (HEX:) 48 65 ..' / 'Rssi= -101'
    - the Rssi line can be truncated, so Data is the source of truth
  * 'AT+RECV=0' replies '<hex bytes> ,<rssi>,<snr>' and clears the buffer
  * TX is strictly one at a time: a second AT+SEND before 'txDone' is dropped
    silently. After TX the radio prints 'RX on freq ...' (back in RX).
  * Payload is delivered byte-exact (firmware group byte is stripped).
  * No CAD / listen-before-talk command.
"""

from __future__ import annotations

import itertools
import queue
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .protocol import PROFILE, AirProfile, AirtimeMeter, RateLimiter, airtime_ms
from .regions import BW_CODES

# ---------------------------------------------------------------------------
# Line parser (pure, testable)
# ---------------------------------------------------------------------------

_HEXPAIRS = r"(?:[0-9a-fA-F]{2}(?:\s+|$))+"
RE_RXDONE = re.compile(r"rxDone")
RE_DATA = re.compile(r"Data:\s*\(HEX:\)\s*(" + _HEXPAIRS + r")?")
RE_RSSI = re.compile(r"Rssi=\s*(-?\d+)")
RE_RECV = re.compile(r"((?:[0-9a-fA-F]{2}\s+)+),\s*(-?\d+)\s*,\s*(-?\d+)")
RE_TXDONE = re.compile(r"txDone")
RE_RXON = re.compile(r"RX on freq")
RE_BANNER = re.compile(
    r"^(Group|Syncword|Frequency|BandWidth|Spreading Factor|Output Power|"
    r"Coding Rate|Header Type|CRC|Invert IQ|Preamble|RX Mode):\s*(.+)$")
RE_FW = re.compile(r"LA66 P2P Firmware\s+(v[\d.]+)")


def _hex_to_bytes(s: str) -> bytes:
    return bytes.fromhex("".join(s.split()))


def parse_line(line: str) -> list[tuple]:
    """Turn one serial line into events. A line may hold several events
    because truncated output gets glued to the next reply."""
    line = line.strip()
    if not line or line.startswith("AT"):
        return []
    found: list[tuple[int, tuple]] = []

    m = RE_FW.search(line)
    if m:
        found.append((m.start(), ("banner", "Firmware", m.group(1))))
    m = RE_BANNER.match(line)
    if m:
        found.append((0, ("banner", m.group(1), m.group(2).strip())))

    for m in RE_RXDONE.finditer(line):
        found.append((m.start(), ("rx_start",)))
    data_spans = []
    for m in RE_DATA.finditer(line):
        data_spans.append(m.span())
        found.append((m.start(), ("rx_data", _hex_to_bytes(m.group(1) or ""))))
    recv_spans = []
    for m in RE_RECV.finditer(line):
        if any(s <= m.start() < e for s, e in data_spans):
            continue
        recv_spans.append(m.span())
        found.append((m.start(), ("recv", _hex_to_bytes(m.group(1)),
                                  int(m.group(2)), int(m.group(3)))))
    for m in RE_RSSI.finditer(line):
        # a truncated 'Rssi= -1' glued to a RECV reply would read as '-154'
        if any(m.start() < s < m.end() for s, _ in recv_spans):
            continue
        v = int(m.group(1))
        if -150 <= v <= 0:
            found.append((m.start(), ("rssi", v)))
    for m in RE_TXDONE.finditer(line):
        found.append((m.start(), ("tx_done",)))
    for m in RE_RXON.finditer(line):
        found.append((m.start(), ("rx_on",)))

    found.sort(key=lambda x: x[0])
    return [ev for _, ev in found]


# ---------------------------------------------------------------------------
# AT configuration for the shared air profile
# ---------------------------------------------------------------------------

def at_commands(p: AirProfile = PROFILE) -> list[str]:
    f = f"{p.freq_mhz:.3f}"
    bw = BW_CODES[p.bw_hz]
    return [
        "AT+RXMOD=65535,0",                       # RX always on, NO auto-ACK
        f"AT+FRE={f},{f}",
        f"AT+SF={p.sf},{p.sf}",
        f"AT+BW={bw},{bw}",
        f"AT+CR={p.cr},{p.cr}",
        f"AT+CRC={int(p.crc)},{int(p.crc)}",
        f"AT+HEADER={0 if p.explicit_header else 1},{0 if p.explicit_header else 1}",
        "AT+IQ=0,0",
        f"AT+PREAMBLE={p.preamble},{p.preamble}",
        f"AT+SYNCWORD={0 if p.private_sync else 1}",   # 0 = 0x1424 private (bench-verified)
        f"AT+POWER={p.tx_power_dbm}",
        "AT+GROUPMOD=0,0",
    ]


def expected_banner(p: AirProfile = PROFILE) -> dict[str, str]:
    """Substrings that must appear in the reset banner."""
    return {
        "Syncword": "0x1424" if p.private_sync else "0x3444",
        "Spreading Factor": f"{p.sf} , {p.sf}",
        "Coding Rate": f"4/{p.cr + 4} , 4/{p.cr + 4}",
        "Header Type": "explicit header , explicit header",
        "CRC": "ON , ON" if p.crc else "OFF , OFF",
        "Invert IQ": "0 , 0",
        "Preamble": f"{p.preamble} , {p.preamble}",
        "Output Power": f"{p.tx_power_dbm} dBm",
        "RX Mode": "65535 , 0",
        "Group": "0 , 0",
    }


_NUM = re.compile(r"\d+(?:\.\d+)?")


def check_banner(banner: dict[str, str], p: AirProfile = PROFILE) -> list[str]:
    problems = []
    for key, want in expected_banner(p).items():
        got = banner.get(key)
        if got is None:
            problems.append(f"{key}: not reported")
        elif want not in got:
            problems.append(f"{key}: got '{got}', want '{want}'")
    # numeric checks (formatting of these varies)
    for key, want, tol in (("Frequency", p.freq_mhz, 0.0005), ("BandWidth", p.bw_hz / 1000, 0.05)):
        got = banner.get(key)
        nums = [float(x) for x in _NUM.findall(got or "")]
        if not nums:
            problems.append(f"{key}: not reported")
        elif any(abs(n - want) > tol for n in nums[:2]):
            problems.append(f"{key}: got '{got}', want {want:g}")
    return problems


def list_ports() -> list[tuple[str, str]]:
    """Serial ports that could be an LA66: [(device, description)]."""
    out: list[tuple[str, str]] = []
    try:
        from serial.tools import list_ports as lp
        for port in lp.comports():
            dev = port.device
            if dev.startswith("/dev/tty.") and dev.replace("/dev/tty.", "/dev/cu.") in \
                    [q.device for q in lp.comports()]:
                continue                                  # macOS: prefer /dev/cu.*
            if "Bluetooth" in dev or "debug-console" in dev:
                continue
            out.append((dev, port.description or ""))
    except Exception:
        import glob
        for dev in sorted(glob.glob("/dev/cu.*") + glob.glob("/dev/ttyUSB*")
                          + glob.glob("/dev/ttyACM*")):
            if "Bluetooth" not in dev and "debug-console" not in dev:
                out.append((dev, ""))
    return out


# ---------------------------------------------------------------------------
# Radio
# ---------------------------------------------------------------------------


@dataclass
class Packet:
    payload: bytes
    rssi: Optional[int] = None
    snr: Optional[int] = None
    t: float = field(default_factory=time.time)


@dataclass
class TxResult:
    job_id: int
    ok: bool
    airtime_ms: float
    detail: str = ""


class LA66Radio:
    """Threaded driver: a reader thread parses serial output, a writer thread
    serialises AT commands (TX queue with rate limit + random backoff, and
    AT+RECV follow-ups for SNR)."""

    def __init__(self, port: str, baud: int = 9600,
                 on_packet: Optional[Callable[[Packet], None]] = None,
                 on_tx: Optional[Callable[[TxResult], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 profile: AirProfile = PROFILE,
                 fetch_snr: bool = True,
                 limiter: Optional[RateLimiter] = None,
                 serial_obj=None):
        self.port, self.baud, self.p = port, baud, profile
        self.on_packet = on_packet or (lambda pkt: None)
        self.on_tx = on_tx or (lambda r: None)
        self.on_log = on_log or (lambda s: None)
        self.fetch_snr = fetch_snr
        self.limiter = limiter or RateLimiter(profile.rate_per_s, profile.burst)
        self.meter = AirtimeMeter()          # own TX airtime, rolling hour (duty cycle)
        self._ser = serial_obj
        self._wlock = threading.Lock()
        self._txq: "queue.Queue[tuple[int, bytes]]" = queue.Queue()
        self._ctrlq: "queue.Queue[str]" = queue.Queue()
        self._tx_done = threading.Event()
        self._rx_on = threading.Event()
        self._tx_busy = False
        self._paused = threading.Event()
        self._ids = itertools.count(1)
        self._running = False
        self._pending: Optional[dict] = None
        self.banner: dict[str, str] = {}

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        if self._ser is None:
            import serial  # pyserial
            self._ser = serial.Serial(self.port, self.baud, timeout=0.1)
        self._running = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._writer = threading.Thread(target=self._write_loop, daemon=True)
        self._reader.start()
        self._writer.start()

    def close(self) -> None:
        self._running = False
        time.sleep(0.2)
        try:
            self._ser.close()
        except Exception:
            pass

    # -- public API --------------------------------------------------------

    def send(self, frame: bytes) -> int:
        if len(frame) > self.p.max_payload:
            raise ValueError(f"frame {len(frame)} B exceeds {self.p.max_payload} B")
        job = next(self._ids)
        self._txq.put((job, frame))
        return job

    def queued(self) -> int:
        return self._txq.qsize()

    def pause(self, timeout: float = 5.0) -> None:
        """Stop starting new transmissions (e.g. while reconfiguring)."""
        self._paused.set()
        t = time.monotonic()
        while self._tx_busy and time.monotonic() - t < timeout:
            time.sleep(0.05)

    def resume(self) -> None:
        self._paused.clear()

    def configure(self, settle: float = 0.3, reset_wait: float = 3.0) -> list[str]:
        """Apply the air profile, reset, and verify against the banner.
        Returns a list of problems (empty = OK)."""
        for cmd in at_commands(self.p):
            self._write(cmd)
            time.sleep(settle)
        return self.reset_and_check(reset_wait)

    def reset_and_check(self, reset_wait: float = 3.0) -> list[str]:
        self.banner.clear()
        self._write("ATZ")
        time.sleep(reset_wait)
        return check_banner(self.banner, self.p)

    # -- internals ---------------------------------------------------------

    def _write(self, cmd: str) -> None:
        with self._wlock:
            self._ser.write((cmd + "\r\n").encode("ascii"))
            self._ser.flush()

    def _read_loop(self) -> None:
        buf = b""
        while self._running:
            try:
                chunk = self._ser.readline()
            except Exception as e:  # unplugged etc.
                self.on_log(f"serial error: {e}")
                time.sleep(0.5)
                continue
            if chunk:
                buf += chunk
                if buf.endswith(b"\n"):
                    self._handle_line(buf.decode("ascii", "replace"))
                    buf = b""
            elif buf:  # timeout with partial line: process it anyway
                self._handle_line(buf.decode("ascii", "replace"))
                buf = b""
            self._check_deadline()

    def _handle_line(self, line: str) -> None:
        for ev in parse_line(line):
            self._handle_event(ev)

    def _handle_event(self, ev: tuple) -> None:
        kind, now = ev[0], time.monotonic()
        if kind == "banner":
            self.banner[ev[1]] = ev[2]
        elif kind == "rx_start":
            self._finalize()
            self._pending = {"payload": None, "rssi": None, "snr": None,
                             "t": time.time(), "deadline": now + 0.5}
        elif kind == "rx_data":
            if self._pending is None:
                self._pending = {"payload": None, "rssi": None, "snr": None,
                                 "t": time.time(), "deadline": now}
            self._pending["payload"] = ev[1]
            if self.fetch_snr and not self._tx_busy:
                self._pending["deadline"] = now + 1.0
                self._ctrlq.put("AT+RECV=0")
            else:
                self._pending["deadline"] = now + 0.3
        elif kind == "rssi":
            if self._pending is not None and self._pending["rssi"] is None:
                self._pending["rssi"] = ev[1]
        elif kind == "recv":
            pend = self._pending
            if pend is not None and pend["payload"] is not None:
                data = ev[1]
                if data == pend["payload"] or data.startswith(pend["payload"]):
                    pend["payload"] = data      # RECV is authoritative if longer
                    pend["rssi"], pend["snr"] = ev[2], ev[3]
                    self._finalize()
                else:
                    self.on_log("AT+RECV returned a different packet; ignored")
        elif kind == "tx_done":
            self._tx_done.set()
        elif kind == "rx_on":
            self._rx_on.set()

    def _check_deadline(self) -> None:
        if self._pending is not None and time.monotonic() >= self._pending["deadline"]:
            self._finalize()

    def _finalize(self) -> None:
        pend, self._pending = self._pending, None
        if pend is None or pend["payload"] is None:
            return
        self.on_packet(Packet(pend["payload"], pend["rssi"], pend["snr"], pend["t"]))

    def _write_loop(self) -> None:
        job = None
        ready_at = 0.0
        while self._running:
            # control commands (AT+RECV) take priority
            try:
                self._write(self._ctrlq.get_nowait())
                time.sleep(0.05)
                continue
            except queue.Empty:
                pass
            if job is None:
                try:
                    job = self._txq.get(timeout=0.05)
                except queue.Empty:
                    continue
                ready_at = (time.monotonic() + self.limiter.wait_time()
                            + random.uniform(0, self.p.backoff_max_s))
            if time.monotonic() < ready_at or self._paused.is_set():
                time.sleep(0.05)
                continue
            wait = self.duty_wait(len(job[1]))
            if wait > 0:
                ready_at = time.monotonic() + min(wait, 5.0)
                continue
            if not self.limiter.try_consume():
                ready_at = time.monotonic() + self.limiter.wait_time()
                continue
            self._transmit(*job)
            job = None

    def duty_wait(self, frame_len: int) -> float:
        """Seconds until a frame fits the duty-cycle budget (0 = now)."""
        if not self.p.duty_cycle:
            return 0.0
        budget = self.p.duty_cycle * self.meter.window * 1000
        need = self.meter.total_ms() + airtime_ms(frame_len, self.p) - budget
        if need <= 0:
            return 0.0
        # oldest TX must age out of the window; approximate with its age
        if self.meter.events:
            oldest = self.meter.events[0][0]
            return max(0.5, oldest + self.meter.window - self.meter.clock())
        return 1.0

    def _transmit(self, job_id: int, frame: bytes) -> None:
        cmd = f"AT+SEND=0,{frame.hex().upper()},0,0"
        ms = airtime_ms(len(frame), self.p)
        timeout = len(cmd) * 10 / self.baud + ms / 1000 + 1.5
        self._tx_done.clear()
        self._rx_on.clear()
        self._tx_busy = True
        try:
            self._write(cmd)
            ok = self._tx_done.wait(timeout)
            if ok:
                self._rx_on.wait(1.0)
        finally:
            self._tx_busy = False
        if ok:
            self.meter.add(ms)
        self.on_tx(TxResult(job_id, ok, ms, "" if ok else "no txDone"))
