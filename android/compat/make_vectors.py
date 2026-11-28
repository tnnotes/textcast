#!/usr/bin/env python3
"""
Generate test vectors from the Python TextCast code for the Android (Kotlin)
port. Run from the LoRa_Network folder:

    python3 android/compat/make_vectors.py

Writes android/app/src/test/resources/vectors.json. The Kotlin test
(CompatTest) must reproduce every value byte for byte. It also writes files
made by Kotlin to android/compat/out/, which check_kotlin.py opens with Python.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import tempfile
import time
import unicodedata

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from lora_text import crypto as C, ed25519, hardware as HW, keyring as K, vault as V  # noqa: E402
from lora_text import config as CFG  # noqa: E402
from lora_text.la66 import at_commands, check_banner, parse_line  # noqa: E402
from lora_text.protocol import (AirProfile, apply_profile, airtime_ms, decode, effective_payload,  # noqa: E402
                                encode_plain, compose, set_hardware_limits, PROFILE)
from lora_text.regions import PRESETS  # noqa: E402

OUT = os.path.join(ROOT, "android", "app", "src", "test", "resources", "vectors.json")


def h(b: bytes) -> str:
    return b.hex()


def ev_repr(ev: tuple) -> list:
    return [x.hex() if isinstance(x, bytes) else x for x in ev]


def main() -> None:
    v: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S")}
    t_now = time.time()
    v["t"] = t_now

    # -- primitives ------------------------------------------------------------------
    v["ed25519"] = []
    for k in range(4):
        seed = hashlib.sha256(b"seed%d" % k).digest()
        msg = ("hello %d " % k).encode() * k
        v["ed25519"].append({"seed": h(seed), "pub": h(ed25519.public_key(seed)), "msg": h(msg),
                             "sig": h(ed25519.sign(seed, msg))})
    v["scrypt"] = []
    for (pw, salt, n, r, p, dk) in ((b"password", b"NaCl", 1024, 8, 16, 64),
                                    (b"correct horse", b"0123456789abcdef", 2 ** 15, 8, 1, 64),
                                    (b"", b"", 16, 1, 1, 64)):
        v["scrypt"].append({"pass": h(pw), "salt": h(salt), "n": n, "r": r, "p": p, "dklen": dk,
                            "out": h(hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=dk,
                                                    maxmem=128 * 1024 * 1024))})
    v["b32"] = [{"bytes": h(b), "b32": K.b32(b)} for b in (b"", b"a", b"ab", b"abc", os.urandom(32), os.urandom(16))]
    v["fingerprint"] = [{"pub": x["pub"], "fp": K.fingerprint(bytes.fromhex(x["pub"])),
                         "handle": K.make_handle("Tuan", bytes.fromhex(x["pub"]))} for x in v["ed25519"]]
    v["derive"] = [{"secret": h(s), "label": l, "out": h(C.derive(s, l))}
                   for s, l in ((b"\x01" * 16, "enc"), (b"\x02" * 16, "mac"), (b"x", "member/lan#abcd"))]
    v["member_secret"] = [{"gs": h(b"\x07" * 16), "cs": "Lan#P2MD", "out": h(C.member_secret(b"\x07" * 16, "Lan#P2MD"))}]

    # -- frames -------------------------------------------------------------------------
    frames = []
    for k, (txt, enc, mode) in enumerate((("xin chào", True, 0x50), ("signed text", False, 0x48),
                                         ("a" * 80, True, 0x50), ("", True, 0x50))):
        secret = hashlib.sha256(b"stream%d" % k).digest()[:16]
        i = (C.minute_now(t_now) << 8) + k
        frames.append({"secret": h(secret), "mode": mode, "i": i, "text": h(txt.encode()), "encrypt": enc,
                       "frame": h(C.seal(C.StreamKey(secret), mode, i, txt.encode(), enc))})
    v["frames"] = frames
    v["candidates"] = []
    for last_i, hint in ((0, None), (0, 5), ((C.minute_now(t_now) << 8) + 3, 7), ((C.minute_now(t_now) << 8) + 3, None)):
        v["candidates"].append({"last_i": last_i, "hint": hint,
                                "first": list(__import__("itertools").islice(C.candidates(last_i, t_now, hint), 40))})

    # -- profile maths --------------------------------------------------------------------
    air = []
    for sf in range(5, 13):
        for bw in (125_000, 250_000, 500_000, 62_500):
            for cr in (1, 4):
                p = AirProfile(sf=sf, bw_hz=bw, cr=cr)
                for n in (1, 50, 100):
                    air.append({"sf": sf, "bw": bw, "cr": cr, "len": n, "ovh": 1,
                                "ms": airtime_ms(n, p, firmware_overhead=1)})
    v["airtime"] = air
    eff = []
    for hw in ("dragino-la66-p2p", "rak3172-rui3"):
        hp = HW.load(hw)
        set_hardware_limits(hp.max_payload, hp.firmware_overhead)
        for region in PRESETS:
            for sf in (7, 9, 10, 12):
                p = CFG.build_profile({"region": region}) if sf == 7 else None
                pp = dataclasses.replace(AirProfile(), sf=sf, bw_hz=PRESETS[region]["bw_hz"],
                                         max_dwell_ms=PRESETS[region]["max_dwell_ms"])
                eff.append({"hw": hw, "region": region, "sf": sf, "eff": effective_payload(pp),
                            "build": None if p is None else p.max_payload})
    v["effective"] = eff
    set_hardware_limits(HW.load(None).max_payload, 1)
    builds = []
    for radio in ({"region": "AS923-2"}, {"region": "EU868", "sf": 9}, {"region": "custom", "freq_mhz": 433.175, "bw_khz": 62.5},
                  {"region": "US915", "duty_cycle": 0.05, "max_dwell_ms": "none"}, {"region": "AS923-2", "power_dbm": 10}):
        p = CFG.build_profile(radio)
        builds.append({"radio": radio, "name": p.name, "freq": p.freq_mhz, "sf": p.sf, "bw": p.bw_hz,
                       "power": p.tx_power_dbm, "dwell": p.max_dwell_ms, "duty": p.duty_cycle, "max": p.max_payload})
    v["builds"] = builds

    # -- plain frames ------------------------------------------------------------------------
    apply_profile(AirProfile())
    plain = []
    for text, cs in (("hello", "Tuan"), (unicodedata.normalize("NFD", "Xin chào các bạn"), "Lan#P2MD"), ("#news @lan hi", None)):
        plain.append({"text": text, "cs": cs, "frame": h(encode_plain(compose(text, cs)))})
    v["plain"] = plain
    v["decode"] = []
    for fr in (b"\x00Tuan: hi #News @Lan", b"\x00bad \xff", b"\x48\x01hello\x00\x00\x00\x00\x00", b"\x50\x01abcdefgh",
               b"\xc0xyz", b"", b"\x00ctl\x07x"):
        d = decode(fr)
        v["decode"].append({"frame": h(fr), "kind": d.kind, "text": d.text, "callsign": d.callsign,
                            "tokens": d.tokens, "display": d.display()})

    # -- hardware and LA66 parsing ------------------------------------------------------------
    p = CFG.build_profile({"region": "AS923-2"})
    v["la66_at"] = at_commands(p)
    v["render"] = {name: {"init": HW.render_all(hp.init, p), "rx_start": HW.render_all(hp.rx_start, p),
                          "send": HW.render(hp.send, p, b"\x01\xab", hp.hex_upper) if hp.send else ""}
                   for name in HW.BUILTIN for hp in [HW.load(name)]}
    lines = ["rxDone", "Data: (HEX:) 48 65 6C 6C 6F ", "Rssi= -101", "48 65 6C 6C 6F ,-98,7",
             "txDone", "RX on freq 920800000", "LA66 P2P Firmware v1.2.4", "Spreading Factor: 7 , 7",
             "Data: (HEX:) 00 41 Rssi= -1", "48 65 ,-9Rssi= -154", "AT+SEND=0,00,0,0", "Frequency: 920.800 , 920.800"]
    v["la66_parse"] = [{"line": l, "events": [ev_repr(e) for e in parse_line(l)]} for l in lines]
    banner = {"Syncword": "0x1424", "Spreading Factor": "7 , 7", "Coding Rate": "4/5 , 4/5",
              "Header Type": "explicit header , explicit header", "CRC": "ON , ON", "Invert IQ": "0 , 0",
              "Preamble": "8 , 8", "Output Power": "14 dBm", "RX Mode": "65535 , 0", "Group": "0 , 0",
              "Frequency": "920.800 , 920.800", "BandWidth": "125 kHz , 125 kHz"}
    v["banner_ok"] = {"banner": banner, "problems": check_banner(banner, p)}
    bad = dict(banner, **{"Spreading Factor": "9 , 9", "Frequency": "921.400 , 921.400"})
    bad.pop("CRC")
    v["banner_bad"] = {"banner": bad, "problems": check_banner(bad, p)}

    # -- keyrings -------------------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        tuan = K.Keyring(os.path.join(tmp, "keys-Tuan.json"), me="Tuan")
        lan = K.Keyring(os.path.join(tmp, "keys-Lan.json"), me="Lan")
        news = tuan.new_own("news")
        team = tuan.new_group("team")
        lan.follow(news)
        lan.join_group(team)
        tuan.add_member("team", lan.handle)
        tuan.data["groups"]["team"]["members"][lan.handle.lower()]["pub"] = K.b32(lan.pub)
        tuan.save()
        tuan.add_contact(lan.lid(), "Lan (school)")
        msgs = {
            "lan_group_sealed": lan.seal_for("team", "chào nhóm"),
            "lan_group_signed": lan.seal_for("team", "signed to team", signed=True),
            "tuan_news": tuan.seal_for("news", "tin tức hôm nay"),
            "lan_join": lan.make_join("team"),
        }
        # Lan's keyring after sending (counters advanced) and Tuan's
        with open(tuan.path, encoding="utf-8") as f:
            tuan_json = f.read()
        with open(lan.path, encoding="utf-8") as f:
            lan_json = f.read()
        v["keyring"] = {
            "tuan_json": tuan_json, "lan_json": lan_json,
            "tuan_handle": tuan.handle, "tuan_lid": tuan.lid(), "tuan_fp": tuan.fingerprint(),
            "lan_handle": lan.handle, "lan_lid": lan.lid(),
            "share_news": tuan.share("news"), "share_team": tuan.share("team"),
            "lan_share_news": lan.share("news"),
            "frames": {k: h(x) for k, x in msgs.items()},
            "t": time.time(),
        }
        # what Tuan's Python keyring opens (expected results for the Kotlin port)
        t2 = K.Keyring(os.path.join(tmp, "keys-Tuan.json"), me="Tuan")
        exp = {}
        for k in ("lan_group_sealed", "lan_group_signed", "lan_join"):
            o = t2.open(msgs[k])
            exp[k] = None if o is None else {"kind": o.kind, "source": o.source, "sender": o.sender, "text": o.text,
                                             "counter": o.counter, "group": o.group, "verified": o.verified}
        l2 = K.Keyring(os.path.join(tmp, "keys-Lan.json"), me="Lan")
        o = l2.open(msgs["tuan_news"])
        exp["tuan_news"] = {"kind": o.kind, "source": o.source, "sender": o.sender, "text": o.text,
                            "counter": o.counter, "group": o.group, "verified": o.verified}
        v["keyring"]["expected"] = exp

        # direct messages: Tuan and Lan hold each other's identity strings
        lan.add_contact(tuan.lid(), "Anh Tuấn")
        tuan2 = K.Keyring(os.path.join(tmp, "keys-Tuan.json"), me="Tuan")
        dm_text, i_text = lan.seal_direct(tuan.handle, "gặp nhau lúc 7 giờ")
        dm_resend, i_resend = lan.seal_direct(tuan.handle, "gặp nhau lúc 7 giờ", resend=True)
        dm_receipt = lan.make_receipt(tuan.handle, [123456789012, 42])
        with open(lan.path, encoding="utf-8") as f:
            lan_json2 = f.read()
        with open(tuan.path, encoding="utf-8") as f:
            tuan_json2 = f.read()                    # before Tuan opens anything (counters)
        exp_dm = {}
        for name, fr in (("text", dm_text), ("resend", dm_resend), ("receipt", dm_receipt)):
            o = tuan2.open(fr)
            exp_dm[name] = {"kind": o.kind, "source": o.source, "sender": o.sender, "text": o.text,
                            "inner": o.inner, "acks": list(o.acks), "counter": o.counter}
        seed_t = K.unb32(json.loads(open(tuan.path, encoding="utf-8").read())["id_seed"])
        v["direct"] = {
            "lan_json": lan_json2, "tuan_json": tuan_json2,
            "shared": h(ed25519.shared_point(seed_t, lan.pub)),
            "frames": {"text": h(dm_text), "resend": h(dm_resend), "receipt": h(dm_receipt)},
            "expected": exp_dm, "t": time.time(),
            "bad_keys": [h((1).to_bytes(32, "little")), "ff" * 32],
        }
        tuan2.close()

        # TextCast numbers and number lookup frames
        from lora_text import numbers as NB
        pubs = [bytes(32), bytes(range(32)), tuan.pub, lan.pub]
        v["numbers"] = {
            "keys": [{"pub": h(p), "number": NB.number_of(p), "shown": NB.format_number(NB.number_of(p))}
                     for p in pubs],
            "lookup": {"number": NB.number_of(lan.pub), "frame": h(NB.make_lookup(NB.number_of(lan.pub)))},
            "answers": [{"pub": h(lan.pub), "nick": nick, "id": aid, "max": mx,
                         "frames": [h(f) for f in NB.make_answer(lan.pub, nick, aid, mx)]}
                        for nick, aid, mx in (("Lan", 7, 100), ("Trần Thị Lan", 200, 23))],
            "good": ["512 384 907" + str(NB._luhn("512384907")), "(" + NB.number_of(tuan.pub)[:3] + ") "
                     + NB.number_of(tuan.pub)[3:6] + "-" + NB.number_of(tuan.pub)[6:]],
            "bad": ["5123849070" if NB._luhn("512384907") else "5123849071", "0123456789", "12345", "51238490 7x"],
        }

        # vault + log cipher
        blob = V.pack({"me": "Vault", "x": [1, 2, "ă"]}, "passphrase-123")
        v["vault"] = {"blob": h(blob), "pass": "passphrase-123", "json": {"me": "Vault", "x": [1, 2, "ă"]}}
        lc = V.LogCipher(b"\x09" * 32)
        v["logcipher"] = {"secret": h(b"\x09" * 32), "enc": lc.encrypt("nhật ký"), "text": "nhật ký"}
        tuan.close(); lan.close(); t2.close(); l2.close()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(v, f, indent=1, ensure_ascii=False)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
