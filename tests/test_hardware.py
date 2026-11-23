"""Hardware profiles and the generic AT driver, with simulated RAK3172 and Wio-E5."""

import dataclasses
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lora_text import config as C  # noqa: E402
from lora_text import hardware as HW  # noqa: E402
from lora_text.atradio import GenericATRadio, make_factory  # noqa: E402
from lora_text.la66 import LA66Radio  # noqa: E402
from lora_text.node import Node  # noqa: E402
from lora_text.protocol import PROFILE, AirProfile, apply_profile, set_hardware_limits  # noqa: E402


class FakeSerial:
    def __init__(self):
        self.out: "queue.Queue[bytes]" = queue.Queue()
        self.written: list[str] = []
        self.sent: list[bytes] = []

    def emit(self, *lines, delay=0.0):
        def go():
            for ln in lines:
                self.out.put((ln + "\r\n").encode())
        threading.Timer(delay, go).start() if delay else go()

    def write(self, data):
        cmd = data.decode().strip()
        self.written.append(cmd)
        self.handle(cmd)

    def handle(self, cmd):
        pass

    def flush(self):
        pass

    def readline(self):
        try:
            return self.out.get(timeout=0.05)
        except queue.Empty:
            return b""

    def close(self):
        pass


class FakeRAK(FakeSerial):
    """RUI3 P2P behaviour: PSEND fails with AT_BUSY_ERROR while continuous RX is on."""
    def __init__(self):
        super().__init__()
        self.rx_on = False

    def handle(self, cmd):
        if cmd == "AT+PRECV=65534":
            if self.rx_on:
                self.emit("AT_BUSY_ERROR")
            else:
                self.rx_on = True
                self.emit("OK")
        elif cmd == "AT+PRECV=0":
            self.rx_on = False
            self.emit("OK")
        elif cmd.startswith("AT+PSEND="):
            if self.rx_on:
                self.emit("AT_BUSY_ERROR")
                return
            self.sent.append(bytes.fromhex(cmd.split("=", 1)[1]))
            self.emit("OK")
            self.emit("+EVT:TXP2P DONE", delay=0.1)
        else:
            self.emit("OK")

    def inject(self, payload):
        if self.rx_on:
            self.emit(f"+EVT:RXP2P:-31:7:{payload.hex().upper()}")


class FakeWioE5(FakeSerial):
    def __init__(self):
        super().__init__()
        self.rx_on = False

    def handle(self, cmd):
        if cmd == "AT+TEST=RXLRPKT":
            self.rx_on = True
            self.emit("+TEST: RXLRPKT")
        elif cmd.startswith("AT+TEST=TXLRPKT,"):
            self.rx_on = False
            hexs = cmd.split(",", 1)[1].strip('"')
            self.sent.append(bytes.fromhex(hexs))
            self.emit(f'+TEST: TXLRPKT "{hexs}"')
            self.emit("+TEST: TX DONE", delay=0.1)
        elif cmd == "AT+MODE=TEST":
            self.emit("+MODE: TEST")
        else:
            self.emit("+TEST: RFCFG F:920800000, SF7, BW125K")

    def inject(self, payload):
        if self.rx_on:
            self.emit(f"+TEST: LEN:{len(payload)}, RSSI:-44, SNR:9",
                      f'+TEST: RX "{payload.hex().upper()}"')


def fast_profile():
    return dataclasses.replace(AirProfile(), backoff_max_s=0.0, rate_per_s=100, burst=10)


def fast(hw):
    hw = dataclasses.replace(hw, cmd_delay=0.01)
    return hw


class ProfileTests(unittest.TestCase):
    def tearDown(self):
        set_hardware_limits(HW.BUILTIN["dragino-la66-p2p"]["max_payload"], 1)
        apply_profile(AirProfile())

    def test_builtins_valid_and_rendering(self):
        for name in HW.BUILTIN:
            self.assertEqual(HW.load(name).validate(), [], name)
        rak = HW.load("rak3172-rui3")
        self.assertIn("AT+P2P=920800000:7:125:0:8:14", HW.render_all(rak.init, AirProfile()))
        wio = HW.load("seeed-wio-e5")
        self.assertIn("AT+TEST=RFCFG,920.800,SF7,125,8,8,14,ON,OFF,OFF",
                      HW.render_all(wio.init, AirProfile()))
        self.assertEqual(HW.render(wio.send, AirProfile(), b"\x01\xab"), 'AT+TEST=TXLRPKT,"01AB"')

    def test_unsupported_and_unknown(self):
        with self.assertRaises(HW.HardwareError):
            HW.load("reyax-rylr")
        with self.assertRaises(HW.HardwareError):
            HW.load("no-such-module")

    def test_custom_json(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "mine.json")
        with open(path, "w") as f:
            f.write(HW.template().to_json())
        hw = HW.load(path)
        self.assertEqual(hw.driver, "at")
        bad = json.loads(HW.template().to_json())
        bad["rx"] = [r"\+RX:(?P<data>\w+)"]               # no payload group
        bad["send"] = "AT+SEND={payload_hex}"              # unknown field
        with open(path, "w") as f:
            json.dump(bad, f)
        with self.assertRaises(HW.HardwareError) as ctx:
            HW.load(path)
        self.assertIn("payload", str(ctx.exception))
        self.assertIn("payload_hex", str(ctx.exception))

    def test_limits_follow_hardware(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "small.json")
        prof = json.loads(HW.template().to_json())
        prof["max_payload"] = {"*": 60}
        with open(path, "w") as f:
            json.dump(prof, f)
        C.activate_hardware(path)
        self.assertEqual(PROFILE.max_payload, 60)
        C.activate_hardware("rak3172-rui3")
        self.assertEqual(PROFILE.max_payload, 100)          # contract cap


class GenericDriverTests(unittest.TestCase):
    def run_radio(self, fake, hwname):
        pkts, txs = [], []
        r = GenericATRadio("x", hardware=fast(HW.load(hwname)), on_packet=pkts.append,
                           on_tx=txs.append, profile=fast_profile(), serial_obj=fake)
        r.open()
        self.assertEqual(r.reset_and_check(), [])
        self.assertTrue(fake.rx_on)
        for t in (b"\x00one", b"\x00two"):
            r.send(t)
        deadline = time.time() + 5
        while len(txs) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(fake.sent, [b"\x00one", b"\x00two"])
        self.assertTrue(all(x.ok for x in txs))
        self.assertTrue(fake.rx_on)                         # back in RX after sending
        fake.inject(b"\x00hello")
        time.sleep(0.3)
        r.close()
        return pkts

    def test_rak3172(self):
        pkts = self.run_radio(FakeRAK(), "rak3172-rui3")
        self.assertEqual([(p.payload, p.rssi, p.snr) for p in pkts], [(b"\x00hello", -31, 7)])

    def test_wio_e5_two_line_rx(self):
        pkts = self.run_radio(FakeWioE5(), "seeed-wio-e5")
        self.assertEqual([(p.payload, p.rssi, p.snr) for p in pkts], [(b"\x00hello", -44, 9)])

    def test_tx_error_reported(self):
        fake, txs = FakeRAK(), []
        hw = dataclasses.replace(fast(HW.load("rak3172-rui3")), pre_send=[], post_send=[])
        r = GenericATRadio("x", hardware=hw, on_tx=txs.append, profile=fast_profile(),
                           serial_obj=fake)
        r.open()
        r.configure()                                       # RX on -> PSEND will be busy
        r.send(b"\x00x")
        deadline = time.time() + 5
        while not txs and time.time() < deadline:
            time.sleep(0.05)
        r.close()
        self.assertFalse(txs[0].ok)

    def test_factory(self):
        self.assertIsInstance(make_factory(HW.load("dragino-la66-p2p"))("x", None,
                                                                        serial_obj=object()),
                              LA66Radio)
        r = make_factory(HW.load("rak3172-rui3"))("x", None, serial_obj=object())
        self.assertIsInstance(r, GenericATRadio)
        self.assertEqual(r.baud, 115200)


class CrossHardwareTests(unittest.TestCase):
    def test_rak_to_wio_group_message(self):
        d = tempfile.mkdtemp()
        rak, wio = FakeRAK(), FakeWioE5()
        a = Node("x", None, "Tuan", os.path.join(d, "a.json"), os.path.join(d, "a.db"),
                 serial_obj=rak, radio_factory=make_factory(fast(HW.load("rak3172-rui3"))))
        got = []
        b = Node("x", None, "Lan", os.path.join(d, "b.json"), os.path.join(d, "b.db"),
                 serial_obj=wio, radio_factory=make_factory(fast(HW.load("seeed-wio-e5"))),
                 on_msg=got.append)
        try:
            a.start()
            b.start()
            b.keys.join_group(a.key_new_group("team"))
            a.send("team", "xin chào từ RAK")
            deadline = time.time() + 6
            while not rak.sent and time.time() < deadline:
                time.sleep(0.05)
            wio.inject(rak.sent[0])
            time.sleep(0.4)
            self.assertEqual([(m.channel, m.text, m.group) for m in got],
                             [("team", "xin chào từ RAK", True)])
        finally:
            a.stop()
            b.stop()


if __name__ == "__main__":
    unittest.main()
