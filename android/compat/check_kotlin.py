#!/usr/bin/env python3
"""
Open, with the Python code, the files the Kotlin test wrote to android/compat/out/
(run the Kotlin CompatTest with -Dtextcast.compat.out=android/compat/out first).
The web app's test writes the same files when TEXTCAST_COMPAT_OUT is set:

    python3 android/compat/check_kotlin.py               # Kotlin files
    python3 android/compat/check_kotlin.py web/compat/out  # JavaScript files
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from lora_text import keyring as K, vault as V  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
VEC = os.path.join(ROOT, "android", "app", "src", "test", "resources", "vectors.json")


def main() -> int:
    global OUT
    if len(sys.argv) > 1:
        OUT = os.path.abspath(sys.argv[1])
    who = "JavaScript" if len(sys.argv) > 1 and "web" in sys.argv[1] else "Kotlin"
    k = json.load(open(os.path.join(OUT, "kotlin.json"), encoding="utf-8"))
    vec = json.load(open(VEC, encoding="utf-8"))
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
        fails += 0 if ok else 1

    with tempfile.TemporaryDirectory() as tmp:
        lan_path = os.path.join(tmp, "keys-Lan.json")
        shutil.copy(os.path.join(OUT, "keys-Lan.json"), lan_path)
        lan = K.Keyring(lan_path, me="Lan")
        o = lan.open(bytes.fromhex(k["tuan_group_sealed"]))
        check(f"{who} group message opens in Python", o is not None and o.text == "từ Android", o)
        o = lan.open(bytes.fromhex(k["tuan_news_signed"]))
        check(f"{who} signed stream message verifies in Python", o is not None and o.text == "signed from Kotlin"
              and o.verified, o)
        o = lan.open(bytes.fromhex(k["tuan_join"]))
        check(f"{who} join announcement opens in Python", o is not None and o.kind == "join", o)

        # a protected identity created by Kotlin opens in Python
        minh_path = os.path.join(tmp, "keys-Minh.lkey")
        shutil.copy(os.path.join(OUT, "keys-Minh.lkey"), minh_path)
        minh = K.Keyring(minh_path, me="Minh", passphrase="minh-passphrase")
        check(f"{who} .lkey identity opens in Python", minh.handle == k["minh_handle"], minh.handle)
        check("same identity string", minh.lid() == k["minh_lid"])
        check("same signed stream string", minh.share("radio") == k["minh_share_radio"])
        check("same group string", minh.share("lab") == k["minh_share_lab"])
        lan.follow(k["minh_share_radio"])                     # signature made in Kotlin verifies here
        check(f"{who} stream signature verifies in Python", lan.data["follow"]["radio"]["verified"])
        minh.close(); lan.close()

    if "direct_text" in k:
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "keys-Lan.json")
            shutil.copy(os.path.join(OUT, "keys-Lan-direct.json"), p)
            lan = K.Keyring(p, me="Lan")
            o = lan.open(bytes.fromhex(k["direct_text"]))
            check(f"{who} direct message opens in Python", o is not None and o.kind == "direct"
                  and o.inner == "text" and o.text in ("từ trình duyệt", "từ Android"), o)
            o = lan.open(bytes.fromhex(k["direct_receipt"]))
            check(f"{who} receipt opens in Python", o is not None and o.inner == "receipt"
                  and o.acks == (77, 5), o)
            lan.close()
    else:
        check(f"{who} direct messages", False, "no direct frames written")

    data = V.unpack(bytes.fromhex(k["vault_blob"]), "kotlin-pass")
    check(f"{who} vault opens in Python", data == {"me": "K", "n": 5}, data)
    lc = V.LogCipher(bytes.fromhex(vec["logcipher"]["secret"]))
    check(f"{who} log text decrypts in Python", lc.decrypt(k["logcipher"]) == "ghi log")
    print("all good" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
