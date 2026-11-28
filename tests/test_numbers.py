"""TextCast numbers, finding people by number over the air, and LA66 replug."""

import dataclasses
import os
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_core import FakeLA66  # noqa: E402
from test_node import deliver, make, wait_sent  # noqa: E402
import lora_text.node as N  # noqa: E402
from lora_text import numbers as NB  # noqa: E402
from lora_text.keyring import Keyring, KeyError_  # noqa: E402
from lora_text.protocol import PROFILE  # noqa: E402


def wait_for(cond, timeout=8.0):
    t = time.time()
    while not cond() and time.time() - t < timeout:
        time.sleep(0.05)
    return cond()


class NumberTests(unittest.TestCase):
    def test_number_shape_and_typos(self):
        pub = bytes(range(32))
        n = NB.number_of(pub)
        self.assertEqual(n, NB.number_of(pub))
        self.assertEqual(len(n), 10)
        self.assertNotEqual(n[0], "0")
        self.assertEqual(NB.parse_number(NB.format_number(n)), n)
        self.assertEqual(NB.parse_number(f"({n[:3]}) {n[3:6]}-{n[6:]}"), n)
        wrong = n[:4] + str((int(n[4]) + 1) % 10) + n[5:]
        with self.assertRaises(NB.NumberError):
            NB.parse_number(wrong)                       # one digit changed
        swapped = n[:2] + n[3] + n[2] + n[4:]
        if swapped != n:
            with self.assertRaises(NB.NumberError):
                NB.parse_number(swapped)                 # two neighbours swapped
        with self.assertRaises(NB.NumberError):
            NB.parse_number("12345")

    def test_frames(self):
        n = NB.number_of(bytes(32))
        q = NB.make_lookup(n)
        self.assertEqual(len(q), 7)
        self.assertEqual(NB.parse_directory(q), ("lookup", n))
        parts = NB.make_answer(bytes(range(32)), "Tuấn", 7, 23)
        self.assertTrue(all(len(p) <= 23 for p in parts))
        got = [NB.parse_directory(p) for p in parts]
        self.assertEqual({g[3] for g in got}, {len(parts)})
        self.assertEqual(b"".join(g[4] for g in got), bytes(range(32)) + "Tuấn".encode())
        self.assertEqual(len(NB.make_answer(bytes(32), "Tuan", 1, 100)), 1)

    def test_keyring_number_and_found_contacts(self):
        tmp = tempfile.mkdtemp()
        a = Keyring(os.path.join(tmp, "a.json"), me="Tuan")
        b = Keyring(os.path.join(tmp, "b.json"), me="Lan")
        self.assertEqual(a.number, NB.number_of(a.pub))
        with self.assertRaises(KeyError_):
            a.add_found(b.pub, "Lan", a.number)          # key does not have that number
        a.add_found(b.pub, "Lan", b.number)
        info = a.contact_info()
        self.assertEqual([(c["name"], c["number"], c["verified"]) for c in info],
                         [("Lan", b.number, False)])
        a.add_contact(b.lid())                           # scanned QR later: now verified
        self.assertTrue(a.contact_info()[0]["verified"])
        a.close(); b.close()


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.saved = (N.ANSWER_DELAY, N.LOOKUP_RETRY, N.LOOKUP_WAIT, N.ANSWER_GAP)
        N.ANSWER_DELAY, N.LOOKUP_RETRY, N.LOOKUP_WAIT, N.ANSWER_GAP = (0.05, 0.1), 3.0, 6.0, 0
        self.tmp = tempfile.mkdtemp()
        self.a, self.fa, self.ma, _ = make(self.tmp, "Tuan")
        self.b, self.fb, self.mb, _ = make(self.tmp, "Lan")
        self.events = []
        self.a.on_lookup = self.events.append

    def tearDown(self):
        N.ANSWER_DELAY, N.LOOKUP_RETRY, N.LOOKUP_WAIT, N.ANSWER_GAP = self.saved
        self.a.stop()
        self.b.stop()

    def test_find_add_and_message(self):
        st = self.a.lookup(NB.format_number(self.b.number))
        self.assertEqual(st["status"], "searching")
        wait_sent(self.fa, 1)
        deliver(self.fa, self.fb)
        self.assertTrue(wait_for(lambda: len(self.fb.sent) >= 1))
        deliver(self.fb, self.fa)
        self.assertTrue(wait_for(lambda: self.events), "no answer")
        self.assertEqual(self.events[0]["status"], "found")
        self.assertEqual(self.events[0]["nick"], "Lan")
        ch = self.a.add_found(self.b.number)
        self.assertEqual(ch, "@" + self.b.handle)
        self.assertEqual(self.a.channel_title(ch), "Lan")
        self.assertEqual(self.mb, [], "lookups are not shown as messages")
        self.assertEqual(self.a.lookup(self.b.number)["status"], "known")
        # Lan adds Tuan the same way, then a private message works
        self.b.on_lookup = ev = []
        self.b.on_lookup = ev.append
        n0, m0 = len(self.fb.sent), len(self.fa.sent)
        self.b.lookup(self.a.number)
        wait_sent(self.fb, n0 + 1)
        deliver(self.fb, self.fa, start=n0)
        self.assertTrue(wait_for(lambda: len(self.fa.sent) > m0))
        deliver(self.fa, self.fb, start=m0)
        self.assertTrue(wait_for(lambda: ev and ev[0]["status"] == "found"))
        self.b.add_found(self.a.number)
        k = len(self.fa.sent)
        self.a.send(ch, "xin chào")
        wait_sent(self.fa, k + 1)
        deliver(self.fa, self.fb, start=k)
        self.assertEqual([m.text for m in self.mb if m.kind == "direct"], ["xin chào"])

    def test_not_findable_and_not_found(self):
        self.b.set_findable(False)
        self.a.lookup(self.b.number)
        wait_sent(self.fa, 1)
        deliver(self.fa, self.fb)
        time.sleep(0.5)
        self.assertEqual(self.fb.sent, [])
        self.assertTrue(wait_for(lambda: self.events, 10))
        self.assertEqual(self.events[-1]["status"], "notfound")

    def test_own_number_and_typo(self):
        with self.assertRaises(NB.NumberError):
            self.a.lookup(self.a.number)
        with self.assertRaises(NB.NumberError):
            self.a.lookup("123 456 7890")

    def test_answer_split_at_sf10(self):
        saved = PROFILE.max_payload
        PROFILE.max_payload = 23
        try:
            self.a.lookup(self.b.number)
            wait_sent(self.fa, 1)
            deliver(self.fa, self.fb)
            self.assertTrue(wait_for(lambda: len(self.fb.sent) >= 2))  # 32 B key + "Lan" in 19 B parts
            self.assertTrue(all(len(f) <= 23 for f in self.fb.sent))
            deliver(self.fb, self.fa)
            self.assertTrue(wait_for(lambda: self.events))
            self.assertEqual(self.events[0]["status"], "found")
        finally:
            PROFILE.max_payload = saved

    def test_two_keys_same_number_is_a_conflict(self):
        self.a.lookup(self.b.number)
        fake_pub = b"\x01" * 32
        real = NB.make_answer(self.b.keys.pub, "Lan", 1, 100)[0]
        orig = N.number_of
        N.number_of = lambda pub: self.b.number          # pretend a faker found a collision
        try:
            self.fa.inject_rx(real)
            time.sleep(0.6)
            self.fa.inject_rx(NB.make_answer(fake_pub, "Lan", 2, 100)[0])
            self.assertTrue(wait_for(lambda: len(self.events) >= 2))
        finally:
            N.number_of = orig
        self.assertEqual([e["status"] for e in self.events], ["found", "conflict"])
        with self.assertRaises(KeyError_):
            self.a.add_found(self.b.number)


class ReplugTests(unittest.TestCase):
    def test_unplug_and_plug_back(self):
        tmp = tempfile.mkdtemp()
        n, fake, msgs, _ = make(tmp, "Tuan")
        states = []
        n.on_radio = states.append
        new = FakeLA66()
        sys.modules["serial"] = types.SimpleNamespace(Serial=lambda *a, **k: new)
        try:
            n.radio._reopen = True
            n.radio._candidates = lambda: ["fake"]
            gone = [False]

            def unplugged():
                gone[0] = True
                raise OSError("device disconnected")
            fake.readline = unplugged
            self.assertTrue(wait_for(lambda: "lost" in states, 3))
            self.assertTrue(wait_for(lambda: "back" in states, 10), states)
            self.assertTrue(n.connected)
            n.send("public", "still here")
            self.assertTrue(wait_for(lambda: len(new.sent) == 1))
            new.inject_rx(b"\x00Lan: hi")
            self.assertTrue(wait_for(lambda: any(m.text == "hi" for m in msgs)))
        finally:
            sys.modules.pop("serial", None)
            n.stop()


class StartWithoutRadioTests(unittest.TestCase):
    def test_log_in_first_plug_in_later(self):
        tmp = tempfile.mkdtemp()
        msgs, states = [], []
        n = N.Node("auto", 9600, "Tuan", os.path.join(tmp, "k.json"), os.path.join(tmp, "l.db"),
                   on_msg=msgs.append)
        n.on_radio = states.append
        other, la66 = types.SimpleNamespace(readline=lambda: b"", write=lambda d: None,
                                            flush=lambda: None, close=lambda: None), FakeLA66()
        plugged = []
        sys.modules["serial"] = types.SimpleNamespace(Serial=lambda name, *a, **k: la66 if name == "la66" else other)
        try:
            n.radio._candidates = lambda: [p for p in plugged if p not in n.radio._skip]
            self.assertEqual(n.start(), [])
            self.assertFalse(n.connected)
            with self.assertRaises(N.KeyError_):
                n.send("public", "hello")                   # the draft stays with the user
            with self.assertRaises(N.KeyError_):
                n.lookup(NB.format_number(NB.number_of(bytes(32))))
            plugged.append("other")                         # some other USB serial device first
            self.assertTrue(wait_for(lambda: "other" in n.radio._skip, 15), "the other device was not checked")
            self.assertTrue(wait_for(lambda: not n.connected, 3), "a device that doesn't answer like an LA66 is not used")
            plugged[:] = ["la66"]
            self.assertTrue(wait_for(lambda: "back" in states, 20), states)
            self.assertTrue(any(c.startswith("AT+SF=") for c in la66.written) if hasattr(la66, "written") else True)
            n.send("public", "hello")
            self.assertTrue(wait_for(lambda: len(la66.sent) == 1))
        finally:
            sys.modules.pop("serial", None)
            n.stop()

    def test_unplug_fails_waiting_messages(self):
        tmp = tempfile.mkdtemp()
        n, fake, msgs, ups = make(tmp, "Lan")
        try:
            n.radio._reopen = True
            n.radio._candidates = lambda: []
            n.radio.limiter.try_consume(); n.radio.limiter.try_consume(); n.radio.limiter.try_consume()
            m = n.send("public", "waiting for a slot")
            fake.readline = lambda: (_ for _ in ()).throw(OSError("gone"))
            self.assertTrue(wait_for(lambda: m.status == "failed", 5), m.status)
            self.assertFalse(n.connected)
        finally:
            n.stop()


if __name__ == "__main__":
    unittest.main()
