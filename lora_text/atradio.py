"""
Generic AT-command radio driver, driven by a HardwareProfile (hardware.py).

It reuses the LA66 driver's machinery (TX queue, rate limit, random backoff,
duty-cycle cap, pause/resume, reader/writer threads) and replaces only the
module-specific parts: configuration commands, the send sequence, and the
parsing of received packets and TX confirmations.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Optional

from .hardware import HardwareProfile, render, render_all
from .la66 import LA66Radio, Packet, TxResult
from .protocol import PROFILE, AirProfile, RateLimiter, airtime_ms


class GenericATRadio(LA66Radio):
    def __init__(self, port: str, baud: Optional[int] = None,
                 on_packet: Optional[Callable] = None, on_tx: Optional[Callable] = None,
                 on_log: Optional[Callable] = None, profile: AirProfile = PROFILE,
                 fetch_snr: bool = True, limiter: Optional[RateLimiter] = None,
                 serial_obj=None, hardware: Optional[HardwareProfile] = None):
        if hardware is None:
            raise ValueError("GenericATRadio needs a hardware profile")
        super().__init__(port, baud or hardware.baud, on_packet=on_packet, on_tx=on_tx,
                         on_log=on_log, profile=profile, fetch_snr=False, limiter=limiter,
                         serial_obj=serial_obj)
        self.hw = hardware
        self._rx = [re.compile(r) for r in hardware.rx]
        self._tx_done_re = re.compile(hardware.tx_done)
        self._tx_err_re = re.compile(hardware.tx_error) if hardware.tx_error else None
        self._meta: Optional[tuple] = None          # (rssi, snr, time) from a meta line
        self._tx_failed = False

    # -- serial --------------------------------------------------------------------

    def _write(self, cmd: str) -> None:
        with self._wlock:
            self._ser.write((cmd + self.hw.line_ending).encode("ascii"))
            self._ser.flush()

    def _run(self, commands: list[str]) -> None:
        for cmd in commands:
            self._write(cmd)
            time.sleep(self.hw.cmd_delay)

    # -- receiving -----------------------------------------------------------------

    def _handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        if self._tx_done_re.search(line):
            self._tx_done.set()
            return
        if self._tx_busy and self._tx_err_re and self._tx_err_re.search(line):
            self._tx_failed = True
            self._tx_done.set()
            self.on_log(f"radio: {line}")
            return
        for rx in self._rx:
            m = rx.search(line)
            if not m:
                continue
            g = m.groupdict()
            rssi = int(g["rssi"]) if g.get("rssi") else None
            snr = int(g["snr"]) if g.get("snr") else None
            if g.get("payload") is None:               # meta line (e.g. Wio-E5 RSSI/SNR)
                self._meta = (rssi, snr, time.monotonic())
                return
            if rssi is None and self._meta and time.monotonic() - self._meta[2] < 2.0:
                rssi, snr = self._meta[0], self._meta[1]
            self._meta = None
            try:
                payload = bytes.fromhex(g["payload"])
            except ValueError:
                self.on_log(f"radio: could not decode received hex: {g['payload'][:40]}")
                return
            if payload:
                self.on_packet(Packet(payload, rssi, snr))
            return

    def _check_deadline(self) -> None:
        pass

    # -- configuration -------------------------------------------------------------

    def configure(self, settle: float = 0.0, reset_wait: float = 0.0) -> list[str]:
        """Send the profile's init commands and enter receive mode. Generic modules
        cannot be verified, so this always applies the settings."""
        self._run(render_all(self.hw.init, self.p))
        self._run(render_all(self.hw.rx_start, self.p))
        self.banner = {"Firmware": self.hw.name}
        return []

    def reset_and_check(self, reset_wait: float = 0.0) -> list[str]:
        return self.configure()

    # -- sending -------------------------------------------------------------------

    def _transmit(self, job_id: int, frame: bytes) -> None:
        ms = airtime_ms(len(frame), self.p)
        cmd = render(self.hw.send, self.p, frame, self.hw.hex_upper)
        timeout = len(cmd) * 10 / self.baud + ms / 1000 + 2.0
        self._tx_done.clear()
        self._tx_failed = False
        self._tx_busy = True
        try:
            self._run(render_all(self.hw.pre_send, self.p))
            self._write(cmd)
            ok = self._tx_done.wait(timeout) and not self._tx_failed
        finally:
            self._run(render_all(self.hw.post_send, self.p))
            self._tx_busy = False
        if ok:
            self.meter.add(ms)
        detail = "" if ok else ("radio reported an error" if self._tx_failed
                                else "no TX confirmation")
        self.on_tx(TxResult(job_id, ok, ms, detail))


def make_factory(hw: HardwareProfile):
    """Radio constructor for Node(radio_factory=...) for this hardware."""
    def factory(port, baud=None, **kw):
        if hw.driver == "la66":
            return LA66Radio(port, baud or hw.baud, **kw)
        return GenericATRadio(port, baud or hw.baud, hardware=hw, **kw)
    return factory
