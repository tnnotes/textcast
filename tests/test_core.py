"""Unit tests (no hardware). Run:  python3 -m unittest discover -s tests"""

import os
import queue
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lora_text import protocol as P  # noqa: E402
from lora_text.la66 import LA66Radio, check_banner, parse_line  # noqa: E402

BANNER = """Dragino OTA bootloader EU868 v1.3
LA66 P2P Firmware v1.2.4
Group: 0 , 0
Syncword: 0x1424 for Private Network
Frequency: 920.800 MHZ , 920.800 MHZ
BandWidth: 125 kHZ , 125 kHZ
Spreading Factor: 7 , 7
Output Power: 14 dBm
Coding Rate: 4/5 , 4/5
Header Type: explicit header , explicit header
CRC: ON , ON
Invert IQ: 0 , 0
Preamble: 8 , 8
RX Mode: 65535 , 0
**************************
RX on freq 920.800 Hz at SF 7"""


class ProtocolTests(unittest.TestCase):
    def test_mode_byte(self):
        self.assertEqual(P.make_mode(P.MsgType.PLAIN), 0x00)
        self.assertEqual(P.parse_mode(P.make_mode(P.MsgType.SEALED, hop=2)), (0, 2, 2))

    def test_plain_roundtrip_vietnamese(self):
        f = P.encode_plain("Tuan: Xin chào Hà Nội #thoitiet")
        d = P.decode(f)
        self.assertEqual(d.kind, "plain")
        self.assertEqual(d.callsign, "Tuan")
        self.assertIn("#thoitiet", d.tokens)

    def test_nfc_saves_bytes(self):
        import unicodedata
        nfd = unicodedata.normalize("NFD", "Hà Nội")
        self.assertLess(len(P.encode_plain(nfd)), 1 + len(nfd.encode()))

    def test_too_long(self):
        with self.assertRaises(P.PayloadTooLong):
            P.encode_plain("x" * 100)
        P.encode_plain("x" * 99)

    def test_undecoded_and_reserved(self):
        self.assertEqual(P.decode(b"\x00\xff\xfe").kind, "undecoded")
        self.assertEqual(P.decode(bytes([0x50]) + b"abcdefgh").kind, "reserved")  # v1 sealed
        self.assertEqual(P.decode(bytes([0x10]) + b"abc").kind, "undecoded")      # old v0
        self.assertEqual(P.decode(b"\xc0hi").kind, "undecoded")
        self.assertEqual(P.decode(b"\x00\xff\xfe").display(), "[3 B undecoded]")

    def test_filters(self):
        d = P.decode(P.encode_plain("Lan: mưa to quá #hanoi @Tuan"))
        self.assertTrue(P.matches(d, set()))
        self.assertTrue(P.matches(d, {"#HaNoi"}))
        self.assertTrue(P.matches(d, {"@lan"}))
        self.assertTrue(P.matches(d, {"@tuan"}))
        self.assertTrue(P.matches(d, {"mưa"}))
        self.assertFalse(P.matches(d, {"#saigon"}))

    def test_airtime(self):
        # 40 B text + mode byte + firmware group byte = 42 B on air
        self.assertAlmostEqual(P.airtime_ms(41), 87.3, delta=1.0)
        self.assertLess(P.airtime_ms(100), P.PROFILE.max_dwell_ms)

    def test_rate_limiter(self):
        t = [0.0]
        rl = P.RateLimiter(rate=0.1, burst=3, clock=lambda: t[0])
        self.assertTrue(all(rl.try_consume() for _ in range(3)))
        self.assertFalse(rl.try_consume())
        self.assertAlmostEqual(rl.wait_time(), 10.0)
        t[0] = 10.0
        self.assertTrue(rl.try_consume())


class ParserTests(unittest.TestCase):
    def test_bench_rx(self):
        evs = []
        for ln in ["rxDone", "Data: (HEX:) 48 65 6c 6c 6f ", "Rssi= -101"]:
            evs += parse_line(ln)
        self.assertEqual(evs, [("rx_start",), ("rx_data", b"Hello"), ("rssi", -101)])

    def test_bench_garbled_recv(self):
        # truncated 'Rssi= -1' glued to the AT+RECV reply (seen on the bench)
        evs = parse_line("Rssi= -154 57 4f ,-101,5")
        self.assertEqual(evs, [("recv", b"TWO", -101, 5)])

    def test_tx_lines(self):
        self.assertEqual(parse_line("txDone"), [("tx_done",)])
        self.assertEqual(parse_line("RX on freq 920.800 Hz at SF 7"), [("rx_on",)])
        self.assertEqual(parse_line("AT+SEND=0,4F4E45,0,0"), [])

    def test_banner(self):
        banner = {}
        for ln in BANNER.splitlines():
            for ev in parse_line(ln):
                if ev[0] == "banner":
                    banner[ev[1]] = ev[2]
        self.assertEqual(banner["Firmware"], "v1.2.4")
        self.assertEqual(check_banner(banner), [])
        banner["RX Mode"] = "65535 , 2"
        self.assertTrue(any("RX Mode" in p for p in check_banner(banner)))


class FakeLA66:
    """Mimics the bench-observed firmware on a fake serial port."""

    def __init__(self):
        self.out: "queue.Queue[bytes]" = queue.Queue()
        self.sent: list[bytes] = []
        self.busy_until = 0.0
        self.last_rx = None

    def write(self, data: bytes):
        cmd = data.decode().strip()
        if cmd.startswith("AT+SEND="):
            if time.monotonic() < self.busy_until:
                return                           # firmware drops it silently
            hexs = cmd.split(",")[1]
            self.sent.append(bytes.fromhex(hexs))
            self.busy_until = time.monotonic() + 0.1
            threading.Timer(0.1, lambda: self._emit(
                "TX on freq 920.800 Hz at SF 7", "txDone",
                "RX on freq 920.800 Hz at SF 7")).start()
        elif cmd.startswith("AT+RECV=0") and self.last_rx:
            p, self.last_rx = self.last_rx, None
            self._emit(" ".join(f"{b:02x}" for b in p) + " ,-101,5")
        elif cmd == "ATZ":
            self._emit(*BANNER.splitlines())

    def flush(self):
        pass

    def inject_rx(self, payload: bytes, truncate_rssi=False):
        self.last_rx = payload
        self._emit("rxDone", "Data: (HEX:) " + " ".join(f"{b:02x}" for b in payload) + " ")
        self.out.put(b"Rssi= -1" if truncate_rssi else b"Rssi= -101\r\n")

    def _emit(self, *lines):
        for ln in lines:
            self.out.put((ln + "\r\n").encode())

    def readline(self):
        try:
            return self.out.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self):
        pass


class RadioTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLA66()
        self.pkts, self.txs = [], []
        prof = P.AirProfile(backoff_max_s=0.0, rate_per_s=100, burst=10)
        self.radio = LA66Radio("fake", on_packet=self.pkts.append,
                               on_tx=self.txs.append, profile=prof,
                               serial_obj=self.fake)
        self.radio.open()

    def tearDown(self):
        self.radio.close()

    def test_config_check(self):
        self.assertEqual(self.radio.reset_and_check(reset_wait=0.5), [])

    def test_tx_is_serialised(self):
        for t in ("ONE", "TWO", "THREE"):
            self.radio.send(P.encode_plain(t))
        deadline = time.time() + 5
        while len(self.txs) < 3 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual([P.decode(f).text for f in self.fake.sent], ["ONE", "TWO", "THREE"])
        self.assertTrue(all(r.ok for r in self.txs))

    def test_rx_with_snr(self):
        self.fake.inject_rx(P.encode_plain("hi"), truncate_rssi=True)
        deadline = time.time() + 3
        while not self.pkts and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(self.pkts), 1)
        p = self.pkts[0]
        self.assertEqual(P.decode(p.payload).text, "hi")
        self.assertEqual((p.rssi, p.snr), (-101, 5))


if __name__ == "__main__":
    unittest.main()
