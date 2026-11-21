"""Login panel logic, identity discovery, port listing, live reconfigure."""

import dataclasses
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_core import FakeLA66  # noqa: E402
from lora_text.keyring import Keyring  # noqa: E402
from lora_text.la66 import list_ports  # noqa: E402
from lora_text.node import Node  # noqa: E402
from lora_text.protocol import PROFILE, AirProfile, apply_profile  # noqa: E402
from lora_text.session import (LoginError, list_identities, login,  # noqa: E402
                               new_identity_path)

PW = "correct horse battery"


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)

    def test_discovery(self):
        Keyring("keys-Tuan.lkey", me="Tuan", passphrase=PW)
        Keyring("keys-Lab.json", me="Lab")
        Keyring("keys-Old.json", me="Old")
        Keyring("keys-Old.lkey", me="Old", passphrase=PW)      # protected copy wins
        open("keys-x.lkey.lock", "w").close()
        ids = list_identities()
        self.assertEqual([(d["nick"], d["protected"]) for d in ids],
                         [("Old", True), ("Tuan", True), ("Lab", False)])

    def test_new_identity(self):
        path = new_identity_path("Lan")
        self.assertEqual(path, "keys-Lan.lkey")
        with self.assertRaises(LoginError):
            login(path, "short", "short")
        with self.assertRaises(LoginError):
            login(path, PW, PW + "x")
        self.assertEqual(login(path, PW, PW), (path, PW))
        self.assertEqual(new_identity_path("Lab", lab=True), "keys-Lab.json")
        self.assertEqual(login("keys-Lab.json", "", None, lab=True), ("keys-Lab.json", None))
        with self.assertRaises(LoginError):
            new_identity_path("bad nick")

    def test_existing_and_migration(self):
        Keyring("keys-Tuan.lkey", me="Tuan", passphrase=PW)
        with self.assertRaises(LoginError):
            login("keys-Tuan.lkey", "wrong passphrase")
        self.assertEqual(login("keys-Tuan.lkey", PW), ("keys-Tuan.lkey", PW))
        with self.assertRaises(LoginError):
            new_identity_path("Tuan")                           # already exists
        Keyring("keys-Old.json", me="Old").new_own("Old")
        self.assertEqual(login("keys-Old.json", ""), ("keys-Old.json", None))   # as is
        self.assertEqual(login("keys-Old.json", PW, PW), ("keys-Old.lkey", PW))  # protect
        self.assertFalse(os.path.exists("keys-Old.json"))
        self.assertIn("Old", Keyring("keys-Old.lkey", passphrase=PW).data["own"])

    def test_list_ports_does_not_crash(self):
        self.assertIsInstance(list_ports(), list)


class ReconfigureTests(unittest.TestCase):
    def tearDown(self):
        apply_profile(AirProfile())

    def test_live_reconfigure(self):
        d = tempfile.mkdtemp()
        fake = FakeLA66()
        written = []
        orig = fake.write
        fake.write = lambda data: (written.append(data.decode().strip()), orig(data))[1]
        n = Node("fake", 9600, "Tuan", os.path.join(d, "k.json"), os.path.join(d, "l.db"),
                 fetch_snr=False, serial_obj=fake)
        try:
            self.assertEqual(n.start(), [])                     # default profile matches
            new = dataclasses.replace(AirProfile(), sf=8, name="AS923-2*")
            problems = n.reconfigure(new, log_max_rows=10, log_max_days=0)
            self.assertIn("AT+SF=8,8", written)
            self.assertEqual(PROFILE.sf, 8)
            self.assertEqual(n.log_max_rows, 10)
            # the fake LA66 still reports SF7, so the check must notice
            self.assertTrue(any("Spreading Factor" in p for p in problems))
            self.assertFalse(n.radio._paused.is_set())          # TX resumed
        finally:
            n.stop()


if __name__ == "__main__":
    unittest.main()
