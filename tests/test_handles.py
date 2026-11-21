"""Identity keys, Name#TAG handles, verification and contacts."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lora_text import ed25519 as E  # noqa: E402
from lora_text.keyring import (Keyring, KeyError_, b32, fingerprint, parse_lid,  # noqa: E402
                               tag_of)


class Ed25519Tests(unittest.TestCase):
    def test_rfc8032_vector_1(self):
        seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        pub = E.public_key(seed)
        self.assertEqual(pub.hex(),
                         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
        sig = E.sign(seed, b"")
        self.assertTrue(sig.hex().startswith("e5564300c360ac72"))
        self.assertTrue(E.verify(pub, b"", sig))
        self.assertFalse(E.verify(pub, b"x", sig))

    def test_matches_cryptography(self):
        try:
            from cryptography.hazmat.primitives import serialization as S
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("cryptography not installed")
        for _ in range(3):
            seed = os.urandom(32)
            k = Ed25519PrivateKey.from_private_bytes(seed)
            pub = k.public_key().public_bytes(S.Encoding.Raw, S.PublicFormat.Raw)
            self.assertEqual(E.public_key(seed), pub)
            self.assertEqual(E.sign(seed, b"hello"), k.sign(b"hello"))


def ring(d, name, me):
    return Keyring(os.path.join(d, name), me=me)


class HandleTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_handle_and_lid(self):
        k = ring(self.d, "a.json", "Tuan")
        nick, _, tag = k.handle.partition("#")
        self.assertEqual((nick, tag), ("Tuan", tag_of(k.pub)))
        self.assertEqual(len(tag), 4)
        handle, pub = parse_lid(k.lid())
        self.assertEqual((handle, pub), (k.handle, k.pub))
        self.assertRegex(k.fingerprint(), r"^[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}$")
        # key survives restart
        self.assertEqual(ring(self.d, "a.json", "Tuan").pub, k.pub)

    def test_bad_nick_and_tampered_lid(self):
        with self.assertRaises(KeyError_):
            ring(self.d, "x.json", "bad#nick")
        k = ring(self.d, "a.json", "Tuan")
        lid = k.lid()
        with self.assertRaises(KeyError_):
            parse_lid(lid.replace("#" + tag_of(k.pub), "#ZZZZ"))

    def test_same_nick_two_people_in_one_group(self):
        a = ring(self.d, "a.json", "Lan")
        b = ring(self.d, "b.json", "Lan")             # same nick, different person
        self.assertNotEqual(a.handle, b.handle)
        c = ring(self.d, "c.json", "Tuan")
        share = c.new_group("team")
        a.join_group(share)
        b.join_group(share)
        c.open(a.make_join("team"))
        c.open(b.make_join("team"))
        self.assertEqual(len(c.data["groups"]["team"]["members"]), 3)
        oa, ob = c.open(a.seal_for("team", "from A")), c.open(b.seal_for("team", "from B"))
        self.assertEqual((oa.sender, oa.text), (a.handle, "from A"))
        self.assertEqual((ob.sender, ob.text), (b.handle, "from B"))

    def test_join_rejects_forged_handle(self):
        import hashlib
        import hmac
        from lora_text import crypto as C
        from lora_text.protocol import MsgType, make_mode
        c = ring(self.d, "c.json", "Tuan")
        share = c.new_group("team")
        a = ring(self.d, "a.json", "Lan")
        a.join_group(share)
        self.assertEqual(c.open(a.make_join("team")).kind, "join")
        # a group member announces Lan's handle with their own key -> tag mismatch
        evil = ring(self.d, "e.json", "Eve")
        evil.join_group(share)
        enc, mac = evil._ctrl_keys("team")
        nonce, mode = os.urandom(8), make_mode(MsgType.SEALED, version=1)
        pt = b"J" + C.minute_now().to_bytes(4, "big") + evil.pub + a.handle.encode()
        body = nonce + C.chacha20_xor(enc[:32], nonce + b"\0" * 4, pt)
        frame = bytes([mode]) + body + hmac.new(mac, bytes([mode]) + body,
                                                 hashlib.sha256).digest()[:5]
        self.assertIsNone(c.open(frame))

    def test_follow_verified_vs_old_string(self):
        t = ring(self.d, "t.json", "Tuan")
        share = t.new_own("Tuan")
        self.assertTrue(share.startswith("lts3:"))
        f = ring(self.d, "f.json", "Lan")
        f.follow(share)
        op = f.open(t.seal_for("Tuan", "hi"))
        self.assertEqual((op.sender, op.pub, op.verified), (t.handle, t.pub, True))
        old = "lts1:Tuan:" + t.data["own"]["Tuan"]["secret"]
        g = ring(self.d, "g.json", "Minh")
        g.follow(old)
        op = g.open(t.seal_for("Tuan", "hi again"))
        self.assertIsNone(op.pub)                      # still readable, not verified

    def test_contacts_rename(self):
        t = ring(self.d, "t.json", "Tuan")
        f = ring(self.d, "f.json", "Lan")
        f.follow(t.new_own("Tuan"))
        f.add_contact(t.lid(), "Thầy Tuấn")
        op = f.open(t.seal_for("Tuan", "hi"))
        self.assertEqual(f.display(op.sender, op.pub), "Thầy Tuấn")
        self.assertTrue(any("Thầy Tuấn" in ln for ln in f.contacts()))
        self.assertTrue(f.remove_contact("Thầy Tuấn"))
        self.assertEqual(f.display(op.sender, op.pub), t.handle)

    def test_upgrade_old_member_format(self):
        p = os.path.join(self.d, "old.json")
        with open(p, "w") as fh:
            json.dump({"me": "Tuan", "own": {}, "follow": {},
                       "groups": {"team": {"secret": b32(os.urandom(16)),
                                           "members": {"tuan": 5, "lan": 0}}}}, fh)
        k = Keyring(p)
        self.assertEqual(k.data["groups"]["team"]["members"]["lan"],
                         {"i": 0, "pub": None, "name": "lan"})
        self.assertTrue(k.data.get("id_seed"))

    def test_fingerprint_stable(self):
        self.assertEqual(fingerprint(b"\x00" * 32), fingerprint(b"\x00" * 32))


if __name__ == "__main__":
    unittest.main()
