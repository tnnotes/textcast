"""Direct (one-to-one) messages and delivery receipts, with simulated radios."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_core import FakeLA66  # noqa: E402
from test_node import deliver, make, wait_sent  # noqa: E402
import lora_text.node as N  # noqa: E402
from lora_text import ed25519  # noqa: E402
from lora_text.keyring import DM_RESEND, Keyring, KeyError_, unb32  # noqa: E402
from lora_text import crypto as C  # noqa: E402


def wait_for(cond, timeout=8.0):
    t = time.time()
    while not cond() and time.time() - t < timeout:
        time.sleep(0.05)
    return cond()


class PairKeyTests(unittest.TestCase):
    def test_both_sides_compute_the_same_secret(self):
        s1, s2 = os.urandom(32), os.urandom(32)
        p1, p2 = ed25519.public_key(s1), ed25519.public_key(s2)
        self.assertEqual(ed25519.shared_point(s1, p2), ed25519.shared_point(s2, p1))
        self.assertNotEqual(ed25519.shared_point(s1, p2), ed25519.shared_point(s1, ed25519.public_key(os.urandom(32))))

    def test_bad_keys_are_refused(self):
        seed = os.urandom(32)
        identity = (1).to_bytes(32, "little")            # the neutral point (small order)
        self.assertIsNone(ed25519.shared_point(seed, identity))
        self.assertIsNone(ed25519.shared_point(seed, b"\xff" * 32))     # not a point
        self.assertIsNone(ed25519.shared_point(seed, b"\x01" * 31))     # wrong length

    def test_keyring_frames_only_open_for_the_pair(self):
        tmp = tempfile.mkdtemp()
        a = Keyring(os.path.join(tmp, "a.json"), me="Tuan")
        b = Keyring(os.path.join(tmp, "b.json"), me="Lan")
        c = Keyring(os.path.join(tmp, "c.json"), me="Minh")
        a.add_contact(b.lid()); b.add_contact(a.lid())
        c.add_contact(a.lid())                           # Minh knows Tuan, but is not Lan
        frame, i = a.seal_direct(b.handle, "chào Lan")
        self.assertEqual(len(frame), 1 + 1 + 1 + len("chào Lan".encode()) + C.TAG_LEN)
        o = b.open(frame)
        self.assertEqual((o.kind, o.source, o.sender, o.text, o.inner, o.verified),
                         ("direct", "@" + a.handle, "Tuan", "chào Lan", "text", True))
        self.assertIsNone(b.open(frame), "replay must be refused")
        self.assertIsNone(c.open(frame))
        rec = b.make_receipt(a.handle, [i])
        o = a.open(rec)
        self.assertEqual((o.inner, o.acks), ("receipt", (i & 0xFFFFFFFF,)))
        with self.assertRaises(KeyError_):
            a.seal_direct("Nobody#XXXX", "hi")
        self.assertEqual(a.direct_contacts(), [(b.handle, "Lan")])
        for k in (a, b, c):
            k.close()


class DirectNodeTests(unittest.TestCase):
    def setUp(self):
        self.saved = (N.RECEIPT_DELAY, N.RECEIPT_WAIT)
        N.RECEIPT_DELAY, N.RECEIPT_WAIT = (0.1, 0.2), 3.0
        self.tmp = tempfile.mkdtemp()
        self.a, self.fa, self.ma, self.ua = make(self.tmp, "Tuan")
        self.b, self.fb, self.mb, self.ub = make(self.tmp, "Lan")
        self.a.key_add(self.b.whoami()[1])
        self.b.key_add(self.a.whoami()[1])
        self.ch_ab = "@" + self.b.handle             # Tuan's channel to Lan

    def tearDown(self):
        N.RECEIPT_DELAY, N.RECEIPT_WAIT = self.saved
        self.a.stop()
        self.b.stop()

    def test_channels_and_titles(self):
        self.assertIn((self.ch_ab, "direct"), self.a.channels())
        self.assertEqual(self.a.channel_title(self.ch_ab), "Lan")
        self.assertTrue(self.a.can_send(self.ch_ab))
        self.assertEqual(self.a.byte_count(self.ch_ab, "hi"), 2 + 8)

    def test_message_and_receipt(self):
        m = self.a.send(self.ch_ab, "tối nay họp nhé")
        wait_sent(self.fa, 1)
        self.assertTrue(wait_for(lambda: m.status == "sent"))
        deliver(self.fa, self.fb)
        got = [x for x in self.mb if x.kind == "direct"]
        self.assertEqual([(x.channel, x.sender, x.text, x.verified) for x in got],
                         [("@" + self.a.handle, "Tuan", "tối nay họp nhé", True)])
        self.assertTrue(wait_for(lambda: len(self.fb.sent) >= 1))      # Lan's app confirms by itself
        deliver(self.fb, self.fa)
        self.assertTrue(wait_for(lambda: m.status == "delivered"), m.status)
        self.assertFalse(any(x.kind == "receipt" for x in self.ma), "receipts are not messages")
        hist = [x for x in self.a.history() if x.kind == "direct"]
        self.assertEqual([(x.text, x.status) for x in hist], [("tối nay họp nhé", "delivered")])
        self.assertEqual([x.text for x in self.b.history() if x.kind == "direct"], ["tối nay họp nhé"])

    def test_no_receipt_then_resend(self):
        m = self.a.send(self.ch_ab, "are you there?")
        wait_sent(self.fa, 1)
        deliver(self.fa, self.fb)                      # Lan gets it, but her receipt is lost
        self.assertTrue(wait_for(lambda: len(self.fb.sent) >= 1))
        self.assertTrue(wait_for(lambda: m.status == "unconfirmed", 6), m.status)
        self.a.resend(m)
        self.assertEqual(m.status, "queued")
        wait_sent(self.fa, 2)
        self.assertNotEqual(self.fa.sent[0], self.fa.sent[1])
        deliver(self.fa, self.fb, start=1)
        self.assertEqual(len([x for x in self.mb if x.kind == "direct"]), 1, "shown once, not twice")
        self.assertTrue(wait_for(lambda: len(self.fb.sent) >= 2))     # but confirmed again
        deliver(self.fb, self.fa, start=1)
        self.assertTrue(wait_for(lambda: m.status == "delivered"), m.status)

    def test_one_sided_contact_cannot_read(self):
        c, fc, mc, _ = make(self.tmp, "Minh")
        try:
            c.key_add(self.a.whoami()[1])              # Minh knows Tuan; Tuan doesn't know Minh
            self.a.send(self.ch_ab, "only for Lan")
            wait_sent(self.fa, 1)
            deliver(self.fa, fc)
            self.assertEqual([(x.kind, x.channel) for x in mc], [("locked", "other")])
            self.assertEqual(fc.sent, [], "no receipt for a message it can't read")
        finally:
            c.stop()



if __name__ == "__main__":
    unittest.main()
