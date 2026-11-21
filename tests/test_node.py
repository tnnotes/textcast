"""Node controller (used by the terminal UI) with simulated radios."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_core import FakeLA66  # noqa: E402
from lora_text.node import KeyError_, Node  # noqa: E402


def make(tmp, nick):
    fake, msgs, ups = FakeLA66(), [], []
    n = Node("fake", 9600, nick, os.path.join(tmp, f"k-{nick}.json"),
             os.path.join(tmp, f"l-{nick}.db"), fetch_snr=True,
             on_msg=msgs.append, on_update=ups.append, serial_obj=fake)
    n.start()
    return n, fake, msgs, ups


def deliver(src_fake, dst_fake, start=0):
    for f in src_fake.sent[start:]:
        dst_fake.inject_rx(f)
        time.sleep(0.5)
    time.sleep(0.8)


def wait_sent(fake, n, timeout=8):
    t = time.time()
    while len(fake.sent) < n and time.time() - t < timeout:
        time.sleep(0.05)


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.a, self.fa, self.ma, self.ua = make(self.tmp, "Tuan")
        self.b, self.fb, self.mb, self.ub = make(self.tmp, "Lan")

    def tearDown(self):
        self.a.stop()
        self.b.stop()

    def test_channels_and_flow(self):
        self.assertEqual(self.a.firmware, "v1.2.4")
        share = self.a.key_new_group("team")
        label, typ = self.b.key_add(share)            # join + automatic announce
        self.assertEqual((label, typ), ("team", "group"))
        self.b.send("team", "chào anh")
        self.b.send("public", "hello all")
        wait_sent(self.fb, 3)
        deliver(self.fb, self.fa)
        kinds = [(m.channel, m.kind, m.sender, m.text) for m in self.ma]
        hb = self.b.handle
        self.assertIn(("team", "join", hb, f"{hb} joined"), kinds)
        self.assertIn(("team", "sealed", hb, "chào anh"), kinds)
        self.assertTrue(all(m.group and not m.verified for m in self.ma if m.channel == "team"))
        self.assertFalse(any(m.verified for m in self.ma if m.channel == "public"))
        self.assertIn(("public", "plain", "Lan", "hello all"), kinds)
        self.assertTrue(all(u.status == "sent" for u in self.ub))
        self.assertIn(("team", "group"), self.a.channels())

    def test_follow_readonly_and_outsider(self):
        share = self.a.key_new_stream("Tuan")
        self.b.key_add(share)
        self.assertFalse(self.b.can_send("Tuan"))
        with self.assertRaises(KeyError_):
            self.b.send("Tuan", "x")
        self.a.send("Tuan", "sealed post")
        self.a.send("Tuan", "signed post", signed=True)
        wait_sent(self.fa, 2)
        c, fc, mc, _ = make(self.tmp, "Minh")             # outsider
        try:
            deliver(self.fa, self.fb)
            deliver(self.fa, fc)
        finally:
            c.stop()
        self.assertEqual([(m.channel, m.kind) for m in self.mb],
                         [("Tuan", "sealed"), ("Tuan", "signed")])
        self.assertEqual([(m.channel, m.kind) for m in mc],
                         [("other", "locked"), ("public", "unverified")])

    def test_byte_count(self):
        self.a.key_new_group("team")
        self.assertEqual(self.a.byte_count("public", "hi"), 1 + len("Tuan: hi"))
        self.assertEqual(self.a.byte_count("team", "hi"), 2 + 7)   # mode+hint+text+tag5


if __name__ == "__main__":
    unittest.main()
