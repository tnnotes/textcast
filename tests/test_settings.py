"""Radio profiles, region presets, settings file, log limits, duty cycle."""

import dataclasses
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from lora_text import config as C  # noqa: E402
from lora_text.la66 import LA66Radio, at_commands, check_banner  # noqa: E402
from lora_text.protocol import (PROFILE, AirProfile, PayloadTooLong, airtime_ms,  # noqa: E402
                                apply_profile, effective_payload, encode_plain)
from lora_text.regions import PRESETS  # noqa: E402
from lora_text.storage import PacketLog  # noqa: E402


def args(**kw):
    base = dict(config=os.path.join(tempfile.mkdtemp(), "none.json"), region=None, freq=None,
                sf=None, bw=None, cr=None, power=None, preamble=None, log_max_rows=None,
                log_max_days=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class ProfileTests(unittest.TestCase):
    def tearDown(self):
        apply_profile(AirProfile())                    # restore the default for other tests

    def test_default_is_as923_2(self):
        p, log = C.from_args(args())
        self.assertEqual((p.name, p.freq_mhz, p.sf, p.bw_hz, p.max_payload),
                         ("AS923-2", 920.8, 7, 125000, 100))
        self.assertEqual(log, {"max_rows": 20000, "max_days": 30})

    def test_every_preset_is_valid(self):
        for name in PRESETS:
            p = C.build_profile({"region": name})
            self.assertGreaterEqual(p.max_payload, 50, name)

    def test_overrides_and_limits(self):
        p, _ = C.from_args(args(sf=9))
        self.assertEqual(p.name, "AS923-2*")
        self.assertEqual(p.max_payload, 65)                     # 400 ms dwell at SF9
        self.assertLessEqual(airtime_ms(p.max_payload, p), 400)
        with self.assertRaises(PayloadTooLong):                 # limits follow the profile
            encode_plain("x" * 70)
        p, _ = C.from_args(args(region="EU868", sf=12))
        self.assertEqual(p.max_payload, 58)                     # firmware limit, no dwell
        with self.assertRaises(C.ConfigError):
            C.from_args(args(sf=11))                            # AS923 dwell makes SF11 unusable
        with self.assertRaises(C.ConfigError):
            C.from_args(args(bw=33))
        with self.assertRaises(C.ConfigError):
            C.from_args(args(power=30))

    def test_apply_profile_is_in_place(self):
        ref = PROFILE
        apply_profile(dataclasses.replace(AirProfile(), sf=8, name="x"))
        self.assertIs(ref, PROFILE)
        self.assertEqual(PROFILE.sf, 8)

    def test_at_commands_and_banner(self):
        p = C.build_profile({"region": "custom", "freq_mhz": 868.1, "bw_khz": 62.5, "sf": 9,
                             "max_dwell_ms": "none"})
        cmds = at_commands(p)
        self.assertIn("AT+FRE=868.100,868.100", cmds)
        self.assertIn("AT+BW=3,3", cmds)
        self.assertIn("AT+SF=9,9", cmds)
        self.assertEqual(cmds[0], "AT+RXMOD=65535,0")
        banner = {"Group": "0 , 0", "Syncword": "0x1424 for Private Network",
                  "Frequency": "868.100 MHZ , 868.100 MHZ", "BandWidth": "62.5 kHZ , 62.5 kHZ",
                  "Spreading Factor": "9 , 9", "Output Power": "14 dBm",
                  "Coding Rate": "4/5 , 4/5", "Header Type": "explicit header , explicit header",
                  "CRC": "ON , ON", "Invert IQ": "0 , 0", "Preamble": "8 , 8",
                  "RX Mode": "65535 , 0"}
        self.assertEqual(check_banner(banner, p), [])
        banner["Frequency"] = "920.800 MHZ , 920.800 MHZ"
        self.assertTrue(any("Frequency" in x for x in check_banner(banner, p)))


class ConfigFileTests(unittest.TestCase):
    def tearDown(self):
        apply_profile(AirProfile())

    def run_tool(self, cfg, *a):
        return subprocess.run([sys.executable, os.path.join(ROOT, "settings.py"),
                               "--config", cfg, *a], capture_output=True, text=True)

    def test_settings_tool_and_precedence(self):
        cfg = os.path.join(tempfile.mkdtemp(), "config.json")
        self.assertEqual(self.run_tool(cfg, "set", "region", "EU868").returncode, 0)
        self.assertEqual(self.run_tool(cfg, "set", "sf", "9").returncode, 0)
        self.assertEqual(self.run_tool(cfg, "set", "log.max_rows", "500").returncode, 0)
        self.assertNotEqual(self.run_tool(cfg, "set", "sf", "42").returncode, 0)
        p, log = C.from_args(args(config=cfg))
        self.assertEqual((p.name, p.sf, p.freq_mhz), ("EU868*", 9, 869.525))
        self.assertEqual(log["max_rows"], 500)
        p, _ = C.from_args(args(config=cfg, sf=7))              # flag beats file
        self.assertEqual(p.sf, 7)
        p, _ = C.from_args(args(config=cfg, region="AS923-2"))  # new region resets file values
        self.assertEqual((p.name, p.sf), ("AS923-2", 7))
        self.assertEqual(self.run_tool(cfg, "reset").returncode, 0)
        self.assertEqual(C.load(cfg), C.DEFAULTS)


class LogLimitTests(unittest.TestCase):
    def test_prune_rows_and_age(self):
        log = PacketLog(os.path.join(tempfile.mkdtemp(), "l.db"))
        old = time.time() - 40 * 86400
        for i in range(30):
            log.add("rx", b"\x00x", "plain", f"old {i}", ts=old)
        for i in range(100):
            log.add("rx", b"\x00x", "plain", f"new {i}")
        self.assertEqual(log.stats()["rows"], 130)
        self.assertEqual(log.prune(max_rows=0, max_days=30), 30)
        self.assertEqual(log.prune(max_rows=50, max_days=0), 50)
        rows = log.rows(1000)
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[-1]["text"], "new 99")             # newest kept
        self.assertEqual(log.prune(0, 0), 0)                     # unlimited
        log.close()


class DutyCycleTests(unittest.TestCase):
    def test_duty_wait(self):
        p = dataclasses.replace(AirProfile(), duty_cycle=0.001)   # 3.6 s per hour
        r = LA66Radio("x", profile=p, serial_obj=object())
        self.assertEqual(r.duty_wait(50), 0.0)
        for _ in range(40):
            r.meter.add(100.0)                                   # 4 s used
        self.assertGreater(r.duty_wait(50), 0)
        r.p = dataclasses.replace(p, duty_cycle=None)
        self.assertEqual(r.duty_wait(50), 0.0)


class EffectivePayload(unittest.TestCase):
    def test_bounds(self):
        for sf in (7, 8, 9, 10):
            p = dataclasses.replace(AirProfile(), sf=sf)
            n = effective_payload(p)
            self.assertLessEqual(airtime_ms(n, p), 400)
            if n < 100:
                self.assertGreater(airtime_ms(n + 1, p), 400)


if __name__ == "__main__":
    unittest.main()
