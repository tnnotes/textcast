"""Identity backup / restore across computers."""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import identity  # noqa: E402
from lora_text.keyring import Keyring, KeyError_  # noqa: E402


class IdentityTests(unittest.TestCase):
    def test_pack_unpack(self):
        blob = identity.pack({"me": "Tuan", "own": {}}, "correct horse")
        self.assertEqual(identity.unpack(blob, "correct horse")["me"], "Tuan")
        with self.assertRaises(ValueError):
            identity.unpack(blob, "wrong pass")
        bad = bytearray(blob)
        bad[40] ^= 1
        with self.assertRaises(ValueError):
            identity.unpack(bytes(bad), "correct horse")

    def test_move_to_new_computer(self):
        old, new, peer = (tempfile.mkdtemp() for _ in range(3))
        tuan = Keyring(os.path.join(old, "keys-Tuan.json"), me="Tuan")
        stream_share = tuan.new_own("Tuan")
        group_share = tuan.new_group("team")
        lan = Keyring(os.path.join(peer, "keys-Lan.json"), me="Lan")
        lan.follow(stream_share)
        lan.join_group(group_share)
        tuan.add_member("team", "Lan")
        self.assertIsNotNone(lan.open(tuan.seal_for("Tuan", "before move")))
        self.assertIsNotNone(lan.open(tuan.seal_for("team", "before move")))

        run = lambda *args, cwd: subprocess.run(  # noqa: E731
            [sys.executable, os.path.join(ROOT, "identity.py"), *args],
            cwd=cwd, capture_output=True, text=True, check=True)
        run("export", "--nick", "Tuan", "--passphrase", "pass phrase 1", cwd=old)
        os.rename(os.path.join(old, "Tuan.ltid"), os.path.join(new, "Tuan.ltid"))
        out = run("import", "Tuan.ltid", "--passphrase", "pass phrase 1", cwd=new).stdout

        self.assertIn("keys-Tuan.lkey", out)
        moved = Keyring(os.path.join(new, "keys-Tuan.lkey"), passphrase="pass phrase 1")
        self.assertEqual(moved.me, "Tuan")
        for target in ("Tuan", "team"):
            op = lan.open(moved.seal_for(target, "after move"))
            self.assertIsNotNone(op, target)
            self.assertEqual(op.text, "after move")

    def test_callsign_mismatch_refused(self):
        d = tempfile.mkdtemp()
        k = Keyring(os.path.join(d, "k.json"), me="Tuan")
        k.new_own("Tuan")
        with self.assertRaises(KeyError_):
            Keyring(os.path.join(d, "k.json"), me="Lan")


if __name__ == "__main__":
    unittest.main()
