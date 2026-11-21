"""Tests for SIGNED / SEALED frames and the keyring (no hardware)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lora_text import crypto as C  # noqa: E402
from lora_text.keyring import Keyring, KeyError_  # noqa: E402
from lora_text.protocol import PayloadTooLong, decode  # noqa: E402

T0 = C.EPOCH0 + 86400 * 300 + 37  # a fixed "now"


class ChaChaTests(unittest.TestCase):
    def test_rfc8439_vector(self):
        key = bytes(range(32))
        nonce = bytes.fromhex("000000000000004a00000000")
        pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you "
              b"only one tip for the future, sunscreen would be it.")
        ct = C.chacha20_xor(key, nonce, pt, counter=1)
        self.assertEqual(ct[:16].hex(), "6e2e359a2568f98041ba0728dd0d6981")
        self.assertEqual(C.chacha20_xor(key, nonce, ct, counter=1), pt)

    def test_matches_cryptography_lib(self):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
        except ImportError:
            self.skipTest("cryptography not installed")
        key, nonce = os.urandom(32), os.urandom(12)
        data = os.urandom(150)
        enc = Cipher(algorithms.ChaCha20(key, (1).to_bytes(4, "little") + nonce),
                     mode=None).encryptor()
        self.assertEqual(C.chacha20_xor(key, nonce, data, counter=1), enc.update(data))


def ring(tmp, name, me):
    return Keyring(os.path.join(tmp, name), me=me)


class KeyringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_follow_sealed_and_signed(self):
        tuan, lan, eve = ring(self.tmp, "t.json", "Tuan"), ring(self.tmp, "l.json", "Lan"), \
            ring(self.tmp, "e.json", "Eve")
        share = tuan.new_own("Tuan")
        lan.follow(share)
        f = tuan.seal_for("Tuan", "Xin chào #hanoi")
        self.assertEqual(decode(f).display(), f"[{len(f)} B sealed]")
        self.assertNotIn("Xin".encode(), f)
        op = lan.open(f)
        self.assertEqual((op.kind, op.source, op.text), ("sealed", "Tuan", "Xin chào #hanoi"))
        self.assertEqual(op.sender, tuan.handle)
        self.assertIsNone(eve.open(f))                     # no key
        self.assertIsNone(lan.open(f))                     # replay rejected
        g = tuan.seal_for("Tuan", "public but verified", signed=True)
        self.assertEqual(decode(g).display(), "[unverified] public but verified")
        self.assertEqual(lan.open(g).kind, "signed")

    def test_tamper_and_forgery(self):
        tuan, lan = ring(self.tmp, "t.json", "Tuan"), ring(self.tmp, "l.json", "Lan")
        lan.follow(tuan.new_own("Tuan"))
        g = bytearray(tuan.seal_for("Tuan", "pay 10", signed=True))
        g[5] ^= 0x01                                       # change the text
        self.assertIsNone(lan.open(bytes(g)))

    def test_group_members(self):
        tuan = ring(self.tmp, "t.json", "Tuan")
        share = tuan.new_group("team")
        lan = ring(self.tmp, "l.json", "Lan")
        lan.join_group(share)                  # share lists only tuan; lan adds herself
        tuan.add_member("team", lan.handle)
        f1 = tuan.seal_for("team", "hello team")
        f2 = lan.seal_for("team", "hi from Lan")
        op = lan.open(f1)
        self.assertEqual((op.source, op.sender, op.text), ("team", tuan.handle, "hello team"))
        self.assertIsNotNone(op.pub)                       # creator key came in the string
        self.assertEqual(tuan.open(f2).sender, lan.handle)
        self.assertIsNone(tuan.open(f1))      # own messages are not opened

    def test_join_announcement(self):
        tuan = ring(self.tmp, "t.json", "Tuan")
        share = tuan.new_group("team")
        lan = ring(self.tmp, "l.json", "Lan")
        lan.join_group(share)
        f = lan.seal_for("team", "before announce")
        self.assertIsNone(tuan.open(f))                     # Tuan doesn't know Lan yet
        j = lan.make_join("team")
        op = tuan.open(j)
        self.assertEqual((op.kind, op.source, op.sender), ("join", "team", lan.handle))
        self.assertIn(lan.handle.lower(), tuan.data["groups"]["team"]["members"])
        self.assertIsNone(tuan.open(j))                     # replayed join ignored
        self.assertIsNone(ring(self.tmp, "e.json", "Eve").open(j))   # outsider
        self.assertEqual(tuan.open(lan.seal_for("team", "after")).text, "after")

    def test_late_joiner_and_skew(self):
        tuan = ring(self.tmp, "t.json", "Tuan")
        share = tuan.new_own("Tuan")
        frames = [tuan.seal_for("Tuan", f"m{i}") for i in range(5)]
        late = ring(self.tmp, "late.json", "X")
        late.follow(share)
        self.assertEqual(late.open(frames[-1]).text, "m4")      # joins mid-stream
        self.assertIsNone(late.open(frames[2]))                  # older = replay

    def test_counter_monotonic_after_restart(self):
        path = os.path.join(self.tmp, "t.json")
        a = Keyring(path, me="Tuan")
        a.new_own("Tuan")
        a.seal_for("Tuan", "one")
        i1 = a.data["own"]["Tuan"]["last_i"]
        b = Keyring(path, me="Tuan")              # restart
        b.seal_for("Tuan", "two")
        self.assertGreater(b.data["own"]["Tuan"]["last_i"], i1)
        # keystore lost and group re-joined a minute later: floor is past old counters
        import time
        self.assertGreater(C.startup_floor(time.time() + 60), i1)

    def test_limits_and_errors(self):
        tuan = ring(self.tmp, "t.json", "Tuan")
        tuan.new_own("Tuan")
        tuan.seal_for("Tuan", "x" * 93)
        with self.assertRaises(PayloadTooLong):
            tuan.seal_for("Tuan", "x" * 94)
        with self.assertRaises(KeyError_):
            tuan.new_own("Tuan")
        with self.assertRaises(KeyError_):
            tuan.follow("garbage")
        with self.assertRaises(KeyError_):
            tuan.seal_for("nobody", "hi")

    def test_trial_cost(self):
        import time
        r = ring(self.tmp, "r.json", "R")
        for n in range(10):
            src = ring(self.tmp, f"s{n}.json", f"S{n}")  # noqa
            r.follow(src.new_own(f"S{n}"))
        junk = bytes([0x10]) + os.urandom(40)
        t = time.perf_counter()
        self.assertIsNone(r.open(junk))
        self.assertLess(time.perf_counter() - t, 0.5)   # 10 streams, no match


if __name__ == "__main__":
    unittest.main()
