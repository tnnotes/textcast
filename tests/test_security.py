"""Passphrase protection, lock/unlock, encrypted log, migration."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from test_core import FakeLA66  # noqa: E402
from lora_text.keyring import Keyring, Locked, WrongPassphrase  # noqa: E402
from lora_text.node import Node  # noqa: E402
from lora_text.session import migrate, resolve, unlock_or_create  # noqa: E402
from lora_text.vault import LogCipher, is_protected_file  # noqa: E402

PW = "correct horse battery"


class KeyringProtection(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "keys-Tuan.lkey")

    def test_file_is_encrypted(self):
        k = Keyring(self.p, me="Tuan", passphrase=PW)
        share = k.new_own("Tuan")
        secret = share.split(":")[2]
        raw = open(self.p, "rb").read()
        self.assertTrue(is_protected_file(self.p))
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(b"Tuan", raw)
        with self.assertRaises(WrongPassphrase):
            Keyring(self.p, passphrase="wrong passphrase")
        with self.assertRaises(Locked):
            Keyring(self.p)
        self.assertEqual(Keyring(self.p, passphrase=PW).share("Tuan"), share)

    def test_new_identity_needs_passphrase(self):
        with self.assertRaises(Exception):
            Keyring(self.p, me="Tuan")
        with self.assertRaises(ValueError):
            Keyring(self.p, me="Tuan", passphrase="short")

    def test_wipe_and_unlock(self):
        k = Keyring(self.p, me="Tuan", passphrase=PW)
        k.new_group("team")
        k.wipe()
        self.assertTrue(k.locked)
        self.assertEqual(k.data["groups"], {})
        with self.assertRaises(Locked):
            k.seal_for("team", "x")
        with self.assertRaises(WrongPassphrase):
            k.unlock("nope nope nope")
        k.unlock(PW)
        self.assertIn("team", k.data["groups"])
        k.seal_for("team", "works again")

    def test_change_passphrase(self):
        k = Keyring(self.p, me="Tuan", passphrase=PW)
        k.new_own("Tuan")
        k.change_passphrase("another long one")
        with self.assertRaises(WrongPassphrase):
            Keyring(self.p, passphrase=PW)
        self.assertIn("Tuan", Keyring(self.p, passphrase="another long one").data["own"])

    def test_log_cipher(self):
        c = LogCipher(os.urandom(32))
        e = c.encrypt("Hà Nội mưa")
        self.assertTrue(e.startswith("enc1:"))
        self.assertEqual(c.decrypt(e), "Hà Nội mưa")
        self.assertEqual(c.decrypt("plain text"), "plain text")
        self.assertIsNone(LogCipher(os.urandom(32)).decrypt(e))


class Migration(unittest.TestCase):
    def test_json_to_lkey(self):
        d = tempfile.mkdtemp()
        j = os.path.join(d, "keys-Tuan.json")
        old = Keyring(j, me="Tuan")
        share = old.new_own("Tuan")
        _, keys, _ = resolve("Tuan", os.path.join(d, "keys-Tuan.lkey"), None)
        answers = iter([PW, PW])
        pw = unlock_or_create(keys, ask=lambda _: next(answers))
        self.assertEqual(pw, PW)
        self.assertFalse(os.path.exists(j))
        self.assertEqual(Keyring(keys, passphrase=PW).share("Tuan"), share)
        # next start: asks for the existing passphrase (one wrong try first)
        answers = iter(["wrong one!", PW])
        self.assertEqual(unlock_or_create(keys, ask=lambda _: next(answers)), PW)

    def test_resolve_defaults(self):
        self.assertEqual(resolve("Tuan", None, None)[1:], ("keys-Tuan.lkey", "log-Tuan.db"))
        self.assertEqual(resolve("Tuan", None, None, protected=False)[1],
                         "keys-Tuan.json")


def make(d, nick, protected=True):
    fake, msgs = FakeLA66(), []
    ext = "lkey" if protected else "json"
    n = Node("fake", 9600, nick, os.path.join(d, f"keys-{nick}.{ext}"),
             os.path.join(d, f"log-{nick}.db"), fetch_snr=False, on_msg=msgs.append,
             serial_obj=fake, passphrase=PW if protected else None)
    n.start()
    return n, fake, msgs


class NodeLocking(unittest.TestCase):
    def test_lock_buffer_unlock_and_encrypted_log(self):
        d = tempfile.mkdtemp()
        a, fa, ma = make(d, "Tuan")
        b, fb, mb = make(d, "Lan")
        try:
            b.key_add(a.key_new_stream("Tuan"))
            a.send("Tuan", "bí mật")
            a.send("public", "hello all")
            t = time.time()
            while len(fa.sent) < 2 and time.time() - t < 10:
                time.sleep(0.05)
            b.lock_identity()
            self.assertTrue(b.locked)
            self.assertEqual(b.channels(), [("all", "all"), ("public", "public"),
                                            ("other", "other")])
            for f in fa.sent:
                fb.inject_rx(f)
                time.sleep(0.5)
            time.sleep(0.6)
            self.assertEqual([m.kind for m in mb], ["pending", "plain"])
            self.assertTrue(all("bí" not in m.text for m in b.history()))
            b.unlock(PW)
            texts = [(m.kind, m.text) for m in b.history() if m.direction == "rx"]
            self.assertIn(("sealed", "bí mật"), texts)
            self.assertIn(("plain", "hello all"), texts)
            # on disk: private text encrypted, public text readable
            db = sqlite3.connect(os.path.join(d, "log-Lan.db"))
            stored = [r[0] for r in db.execute("SELECT text FROM packets WHERE direction='rx'")]
            self.assertTrue(any(s and s.startswith("enc1:") for s in stored))
            self.assertNotIn("bí mật", stored)
            self.assertIn("hello all", stored)
            db.close()
        finally:
            a.stop()
            b.stop()

    def test_unprotected_mode(self):
        d = tempfile.mkdtemp()
        n, _, _ = make(d, "Lab", protected=False)
        try:
            self.assertFalse(n.protected)
            with self.assertRaises(Exception):
                n.lock_identity()
        finally:
            n.stop()


class IdentityTool(unittest.TestCase):
    def run_tool(self, d, *args):
        return subprocess.run([sys.executable, os.path.join(ROOT, "identity.py"), *args],
                              cwd=d, capture_output=True, text=True)

    def test_export_import_passwd_protect(self):
        d, new = tempfile.mkdtemp(), tempfile.mkdtemp()
        k = Keyring(os.path.join(d, "keys-Tuan.lkey"), me="Tuan", passphrase=PW)
        k.new_own("Tuan")
        k.close()
        r = self.run_tool(d, "export", "--nick", "Tuan", "--passphrase", PW)
        self.assertEqual(r.returncode, 0, r.stderr)
        os.rename(os.path.join(d, "Tuan.ltid"), os.path.join(new, "Tuan.ltid"))
        r = self.run_tool(new, "import", "Tuan.ltid", "--passphrase", PW)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_tool(new, "passwd", "--nick", "Tuan", "--passphrase", PW,
                          "--new-passphrase", "brand new passphrase")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Tuan", Keyring(os.path.join(new, "keys-Tuan.lkey"),
                                      passphrase="brand new passphrase").data["own"])
        r = self.run_tool(new, "show", "--nick", "Tuan", "--passphrase", "bad passphrase")
        self.assertNotEqual(r.returncode, 0)
        # protect an old plain identity
        Keyring(os.path.join(new, "keys-Old.json"), me="Old").new_own("Old")
        r = self.run_tool(new, "protect", "--nick", "Old", "--passphrase", PW)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(new, "keys-Old.json")))
        self.assertTrue(is_protected_file(os.path.join(new, "keys-Old.lkey")))


if __name__ == "__main__":
    unittest.main()
