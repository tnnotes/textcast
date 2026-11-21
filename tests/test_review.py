"""Fixes from the security / performance review (A-F)."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_core import FakeLA66  # noqa: E402
from lora_text import crypto as C  # noqa: E402
from lora_text import ed25519  # noqa: E402
from lora_text.keyring import Keyring, KeyError_, b32, unb32  # noqa: E402
from lora_text.la66 import Packet  # noqa: E402
from lora_text.node import Node  # noqa: E402
from lora_text.protocol import MsgType, make_mode  # noqa: E402

PW = "correct horse battery"


def ring(d, name, me, **kw):
    return Keyring(os.path.join(d, name), me=me, **kw)


class SignedStreams(unittest.TestCase):           # A
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.tuan = ring(self.d, "t.json", "Tuan")
        self.lan = ring(self.d, "l.json", "Lan")
        self.lan.add_contact(self.tuan.lid(), "Thầy Tuấn")
        self.eve = ring(self.d, "e.json", "Eve")

    def test_real_stream_is_verified(self):
        self.lan.follow(self.tuan.new_own("Tuan"))
        op = self.lan.open(self.tuan.seal_for("Tuan", "hello"))
        self.assertTrue(op.verified)
        self.assertEqual(self.lan.display(op.sender, op.pub), "Thầy Tuấn")

    def test_forged_lts3_rejected(self):
        # Eve builds a stream string claiming Tuan's identity, signed with her own key
        secret = b32(os.urandom(16))
        handle, pub = self.tuan.handle, b32(self.tuan.pub)
        msg = f"lts3:{handle}:{pub}:Tuan:{secret}".encode()
        sig = ed25519.sign(unb32(self.eve.data["id_seed"]), msg)
        with self.assertRaises(KeyError_):
            self.lan.follow(f"lts3:{handle}:{pub}:Tuan:{secret}:{b32(sig)}")
        # altered secret in a genuine string
        real = self.tuan.new_own("Tuan").split(":")
        real[4] = secret
        with self.assertRaises(KeyError_):
            self.lan.follow(":".join(real))

    def test_unsigned_lts2_not_verified(self):
        secret = b32(os.urandom(16))
        self.lan.follow(f"lts2:{self.tuan.handle}:{b32(self.tuan.pub)}:Fake:{secret}")
        k = C.StreamKey(unb32(secret))
        frame = C.seal(k, make_mode(MsgType.SEALED, version=1), C.next_counter(0),
                       "trust me".encode(), True)
        op = self.lan.open(frame)
        self.assertFalse(op.verified)
        d = tempfile.mkdtemp()
        n = Node("x", 9600, "Lan", os.path.join(d, "k.json"), os.path.join(d, "l.db"),
                 serial_obj=FakeLA66())
        try:
            n.keys.add_contact(self.tuan.lid(), "Thầy Tuấn")
            n.keys.follow(f"lts2:{self.tuan.handle}:{b32(self.tuan.pub)}:Fake:{secret}")
            m = n.classify(Packet(C.seal(k, make_mode(MsgType.SEALED, version=1),
                                         C.next_counter(0) + 1, b"again", True)))
            self.assertEqual((m.sender, m.verified), (self.tuan.handle, False))  # no contact name
        finally:
            n.stop()


class GroupLabels(unittest.TestCase):             # B
    def test_group_never_uses_contact_names(self):
        d = tempfile.mkdtemp()
        tuan = ring(d, "t.json", "Tuan")
        n = Node("x", 9600, "Lan", os.path.join(d, "k.json"), os.path.join(d, "l.db"),
                 serial_obj=FakeLA66())
        try:
            n.keys.add_contact(tuan.lid(), "Thầy Tuấn")
            n.keys.join_group(tuan.new_group("team"))
            m = n.classify(Packet(tuan.seal_for("team", "hi team")))
            self.assertEqual((m.sender, m.group, m.verified), (tuan.handle, True, False))
        finally:
            n.stop()


class FrameV1(unittest.TestCase):                 # C
    def test_hint_cuts_candidates(self):
        last = C.next_counter(0) - 3
        full = len(list(C.candidates(last)))
        hinted = len(list(C.candidates(last, hint=7)))
        self.assertGreater(full, 1000)
        self.assertLessEqual(hinted, 20)

    def test_no_false_accepts_on_junk(self):
        d = tempfile.mkdtemp()
        r = ring(d, "r.json", "R")
        for n in range(30):
            r.follow(ring(d, f"s{n}.json", f"S{n}").new_own(f"S{n}"))
        mode = make_mode(MsgType.SEALED, version=1)
        t = time.perf_counter()
        hits = sum(r.open(bytes([mode]) + os.urandom(40)) is not None for _ in range(2000))
        per_packet = (time.perf_counter() - t) / 2000
        self.assertEqual(hits, 0)
        self.assertLess(per_packet, 0.02)

    def test_invalid_text_not_accepted(self):
        d = tempfile.mkdtemp()
        t = ring(d, "t.json", "Tuan")
        f = ring(d, "f.json", "Lan")
        f.follow(t.new_own("Tuan"))
        k = C.StreamKey(unb32(t.data["own"]["Tuan"]["secret"]))
        i = C.next_counter(0)
        bad = C.seal(k, make_mode(MsgType.SEALED, version=1), i, b"\xff\xfe\xfd", True)
        self.assertIsNone(f.open(bad))
        self.assertEqual(f.data["follow"]["Tuan"]["last_i"], 0)   # counter not moved
        self.assertIsNotNone(f.open(t.seal_for("Tuan", "real")))

    def test_frame_layout(self):
        d = tempfile.mkdtemp()
        t = ring(d, "t.json", "Tuan")
        t.new_own("Tuan")
        f = t.seal_for("Tuan", "abc")
        self.assertEqual(len(f), 3 + C.FRAME_OVERHEAD)
        self.assertEqual(f[0] >> 6, 1)                              # version 1
        self.assertEqual(f[1], t.data["own"]["Tuan"]["last_i"] & 0xFF)


class LogAndSaves(unittest.TestCase):             # D + E
    def test_metadata_encrypted_and_restored(self):
        d = tempfile.mkdtemp()
        tuan = ring(d, "t.json", "Tuan")
        fake = FakeLA66()
        n = Node("x", 9600, "Lan", os.path.join(d, "keys-Lan.lkey"), os.path.join(d, "l.db"),
                 serial_obj=fake, passphrase=PW, fetch_snr=False)
        try:
            n.start()
            n.keys.join_group(tuan.new_group("secretgroup"))
            fake.inject_rx(tuan.seal_for("secretgroup", "xin chào"))
            time.sleep(0.8)
            db = sqlite3.connect(os.path.join(d, "l.db"))
            row = db.execute("SELECT channel, sender, text FROM packets WHERE direction='rx'"
                             ).fetchone()
            db.close()
            self.assertTrue(all(v.startswith("enc1:") for v in row))
            h = [m for m in n.history() if m.direction == "rx"][0]
            self.assertEqual((h.channel, h.sender, h.text, h.group),
                             ("secretgroup", tuan.handle, "xin chào", True))
        finally:
            n.stop()

    def test_receive_saves_are_debounced(self):
        d = tempfile.mkdtemp()
        t = ring(d, "t.json", "Tuan")
        path = os.path.join(d, "f.json")
        f = Keyring(path, me="Lan")
        f.follow(t.new_own("Tuan"))
        before = open(path).read()
        f._last_save = time.monotonic()                  # just saved
        self.assertIsNotNone(f.open(t.seal_for("Tuan", "one")))
        self.assertEqual(open(path).read(), before)      # not written yet
        f.close()                                        # flushed on close
        self.assertNotEqual(open(path).read(), before)
        # send counters are saved immediately
        sp = os.path.join(d, "t.json")
        t.seal_for("Tuan", "two")
        self.assertEqual(Keyring(sp).data["own"]["Tuan"]["last_i"],
                         t.data["own"]["Tuan"]["last_i"])


if __name__ == "__main__":
    unittest.main()
