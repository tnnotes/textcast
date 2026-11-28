#!/usr/bin/env python3
"""
End-to-end test of web/index.html in headless Chrome with a simulated LA66,
against the Python TextCast code on the other end of the "radio".

    python3 web/build.py && python3 web/tests/e2e_browser.py [--shots DIR]

Needs: playwright (Python) and a Chrome/Chromium.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from playwright.sync_api import sync_playwright  # noqa: E402

from lora_text import keyring as K, numbers as NB, vault as V  # noqa: E402
from lora_text.protocol import AirProfile, apply_profile, compose, decode, encode_plain  # noqa: E402

PAGE = "file://" + os.path.join(ROOT, "web", "index.html")
FAKE = os.path.join(ROOT, "web", "tests", "fake_la66.js")
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")

fails = 0


def check(name: str, ok: bool, detail="") -> None:
    global fails
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"   {detail}"))
    fails += 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", help="save screenshots here")
    a = ap.parse_args()
    shots = a.shots
    if shots:
        os.makedirs(shots, exist_ok=True)
    apply_profile(AirProfile())
    tmp = tempfile.mkdtemp()
    tuan = K.Keyring(os.path.join(tmp, "keys-Tuan.json"), me="Tuan")
    team = tuan.new_group("team")

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"]) if CHROME else p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1100, "height": 760}, accept_downloads=True,
                            timezone_id="Asia/Ho_Chi_Minh")
        ctx.add_init_script(path=FAKE)
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(PAGE)
        check("Web Serial available (simulated)", pg.evaluate("'serial' in navigator"))
        check("first run asks only for a name", pg.is_visible("#nick") and not pg.is_visible("#pass")
              and not pg.is_visible("#idPick") and not pg.is_visible("#options"))
        summary = pg.inner_text("#radioSummary")
        check("region picked from the time zone", "AS923-2 (from your time zone)" in summary, summary)
        if shots:
            pg.screenshot(path=os.path.join(shots, "1-start.png"))

        # -- new identity with a passphrase, connect ------------------------------------------
        pg.fill("#nick", "Lan")
        pg.check("#protect")
        pg.fill("#pass", "lan-passphrase")
        pg.fill("#pass2", "lan-passphrase")
        pg.click("#go")
        pg.wait_for_selector("text=Radio ready", timeout=30000)
        cmds = pg.evaluate("__la66.cmds")
        check("factory SF12 module was reconfigured", "AT+SF=7,7" in cmds and cmds.count("ATZ") == 2, cmds)
        check("chat list starts with Public, opened", pg.inner_text("#chatList .ci >> nth=0 >> .l1 b") == "Public"
              and pg.inner_text("#cTitle") == "Public")
        check("radio status shown", "Radio on" in pg.inner_text("#radio"), pg.inner_text("#radio"))
        check("own number shown on the list", "Your number" in pg.inner_text("#chatList"))
        check("backup prompt shown for a new identity", pg.is_visible("#backupbar"))
        check("asked the browser to keep the data", pg.evaluate("JSON.parse(atob(localStorage['textcast/config.json'])).ui.persist_asked") is True)
        check("picker shown once", pg.evaluate("__la66.picker") == 1)

        # -- public message from Python ---------------------------------------------------------
        frame = encode_plain(compose("xin chào mọi người", "Tuan"))
        pg.evaluate(f"__la66.rx('{frame.hex()}', -87, 9)")
        pg.wait_for_selector("#feed >> text=xin chào mọi người", timeout=5000)
        bub = pg.locator(".b:has-text('xin chào mọi người')")
        check("public message shows the sender", "Tuan" in bub.inner_text())
        bub.click()
        check("tap shows signal details", "-87 dBm · SNR 9" in bub.inner_text(), bub.inner_text())
        check("chat list preview", "Tuan: xin chào mọi người" in pg.inner_text("#chatList"))

        # -- public message to Python -------------------------------------------------------------
        pg.fill("#text", "x" * 80)
        check("byte counter near the limit", "/ 100 bytes" in pg.inner_text("#count"), pg.inner_text("#count"))
        pg.fill("#text", "x" * 120)
        check("too long: send disabled", pg.is_disabled("#bSend") and "Too long" in pg.inner_text("#barMsg"))
        pg.fill("#text", "chào Tuấn 👋")
        check("short message: no counter", pg.inner_text("#count") == "")
        pg.press("#text", "Enter")
        pg.wait_for_function("__la66.sent.length >= 1", timeout=15000)
        sent = bytes.fromhex(pg.evaluate("__la66.sent[0]"))
        d = decode(sent)
        check("Python decodes the web message", d.kind == "plain" and d.text == "Lan: chào Tuấn 👋", d.text)
        pg.wait_for_function("[...document.querySelectorAll('.b.tx .ft')].some(e => e.textContent.includes('✓'))", timeout=5000)
        check("sent message gets ✓", True)

        # -- join Python's group from an invite; Python opens the join announcement ------------------
        pg.click("#bNew")
        check("new chat asks for a number", pg.is_visible("#addNum"))
        pg.click("#pasteBox summary")
        pg.fill("#kVal", team)
        pg.click("#kAdd")
        pg.wait_for_function("document.querySelector('#cTitle').textContent === 'team'", timeout=5000)
        check("joining a group opens it", "Group" in pg.inner_text("#cSub"))
        pg.wait_for_function("__la66.sent.length >= 2", timeout=15000)
        join = bytes.fromhex(pg.evaluate("__la66.sent[1]"))
        o = tuan.open(join)
        check("Python accepts the web join announcement", o is not None and o.kind == "join" and o.sender.startswith("Lan#"), o)
        sealed = tuan.seal_for("team", "họp lúc 7 giờ")
        pg.evaluate(f"__la66.rx('{sealed.hex()}', -101, 3)")
        pg.wait_for_selector("#feed >> text=họp lúc 7 giờ", timeout=5000)
        check("Python's group message opens in the browser", True)
        pg.fill("#text", "ok, mình tới")
        pg.click("#bSend")
        pg.wait_for_function("__la66.sent.length >= 3", timeout=15000)
        o = tuan.open(bytes.fromhex(pg.evaluate("__la66.sent[2]")))
        check("browser's group message opens in Python", o is not None and o.text == "ok, mình tới" and o.group, o)
        pg.wait_for_timeout(300)
        if shots:
            pg.screenshot(path=os.path.join(shots, "2-group.png"))

        # -- find Tuan by his number; Tuan finds Lan by hers ---------------------------------------------
        pg.evaluate("TextCast.Node.RECEIPT_WAIT = 2; TextCast.Node.RECEIPT_DELAY = [0.1, 0.3]")
        pg.click("#bSettings")
        lan_number = pg.inner_text("#myNumber").replace(" ", "")
        check("settings show a 10-digit number", len(lan_number) == 10 and NB.number_of(bytes(32)) != lan_number, lan_number)
        pg.click("#dSettings summary")
        lan_lid = pg.inner_text("#myLid")
        check("identity string still available", lan_lid.startswith("lid1:Lan#"), lan_lid)
        check("number belongs to the key", NB.number_of(K.parse_lid(lan_lid)[1]) == lan_number)
        if shots:
            pg.screenshot(path=os.path.join(shots, "3-settings.png"))
        pg.click("#dSettings [data-close]")
        pg.click("#bNew")
        pg.fill("#addNum", tuan.number[:4] + str((int(tuan.number[4]) + 1) % 10) + tuan.number[5:])
        pg.click("#addFind")
        check("typo in the number is caught", "typo" in pg.inner_text("#addStatus"), pg.inner_text("#addStatus"))
        pg.fill("#addNum", tuan.number)
        check("number formatted while typing", pg.input_value("#addNum") == NB.format_number(tuan.number))
        n0 = pg.evaluate("__la66.sent.length")
        pg.click("#addFind")
        check("searching shown", "Looking for" in pg.inner_text("#addStatus"))
        pg.wait_for_function(f"__la66.sent.length > {n0}", timeout=15000)
        q = NB.parse_directory(bytes.fromhex(pg.evaluate(f"__la66.sent[{n0}]")))
        check("browser asks 'who has this number?'", q == ("lookup", tuan.number), q)
        for f in NB.make_answer(tuan.pub, "Tuan", 9, 100):
            pg.evaluate(f"__la66.rx('{f.hex()}', -90, 6)")
        pg.wait_for_selector("#addFound >> text=Found", timeout=5000)
        check("Tuan found", pg.inner_text("#addNick") == "Tuan")
        if shots:
            pg.screenshot(path=os.path.join(shots, "4-found.png"))
        pg.fill("#addName", "Anh Tuấn")
        pg.click("#addGo")
        pg.wait_for_function("document.querySelector('#cTitle').textContent === 'Anh Tuấn'", timeout=5000)
        check("private chat opens, with the number", NB.format_number(tuan.number) in pg.inner_text("#cSub"))
        check("empty private chat explains ✓✓", "✓✓" in pg.inner_text("#feed"))
        n1 = pg.evaluate("__la66.sent.length")
        pg.evaluate(f"__la66.rx('{NB.make_lookup(lan_number).hex()}', -90, 6)")       # Tuan looks Lan up
        pg.wait_for_function(f"__la66.sent.slice({n1}).filter(h => h.startsWith('5841')).length >= 1", timeout=45000)
        ans = [NB.parse_directory(bytes.fromhex(h)) for h in pg.evaluate(f"__la66.sent.slice({n1})")]
        ans = sorted((a for a in ans if a and a[0] == "answer"), key=lambda a: a[2])
        data = b"".join(a[4] for a in ans)
        check("browser answers for its own number", NB.number_of(data[:32]) == lan_number and data[32:] == b"Lan", data)
        tuan.add_found(data[:32], data[32:].decode(), lan_number)
        lan_handle = K.make_handle("Lan", data[:32])

        # -- private messages: receipt, not delivered, resend ---------------------------------------------
        n0 = pg.evaluate("__la66.sent.length")
        pg.fill("#text", "riêng cho anh Tuấn")
        pg.press("#text", "Enter")
        pg.wait_for_function(f"__la66.sent.length > {n0}", timeout=40000)
        o = tuan.open(bytes.fromhex(pg.evaluate(f"__la66.sent[{n0}]")))
        check("Python opens the browser's private message", o is not None and o.kind == "direct" and o.text == "riêng cho anh Tuấn", o)
        pg.evaluate(f"__la66.rx('{tuan.make_receipt(lan_handle, [o.counter]).hex()}', -95, 5)")
        pg.wait_for_selector(".b.tx .ok:has-text('✓✓')", timeout=5000)
        check("receipt shows ✓✓", True)
        dm_in, _ = tuan.seal_direct(lan_handle, "anh nhận được rồi")
        n1 = pg.evaluate("__la66.sent.length")
        pg.evaluate(f"__la66.rx('{dm_in.hex()}', -92, 6)")
        pg.wait_for_selector("#feed >> text=anh nhận được rồi", timeout=5000)
        pg.wait_for_function(f"__la66.sent.length > {n1}", timeout=40000)     # the rate limit may hold it ~10 s
        r = tuan.open(bytes.fromhex(pg.evaluate(f"__la66.sent[{n1}]")))
        check("browser confirms Python's message by itself", r is not None and r.inner == "receipt", r)
        n2 = pg.evaluate("__la66.sent.length")
        pg.fill("#text", "còn đó không?")
        pg.press("#text", "Enter")
        pg.wait_for_selector("text=not delivered", timeout=45000)
        check("no receipt: not delivered + Resend", pg.is_visible("a.resend"))
        if shots:
            pg.screenshot(path=os.path.join(shots, "5-private.png"))
        pg.click("a.resend")
        pg.wait_for_function(f"__la66.sent.length > {n2 + 1}", timeout=40000)
        o = tuan.open(bytes.fromhex(pg.evaluate(f"__la66.sent[{n2 + 1}]")))
        check("Resend sends it again (marked as resent)", o is not None and o.inner == "resend" and o.text == "còn đó không?", o)
        pg.click("#cWho")
        check("contact info: added by number", "Added by number" in pg.inner_text("#iBody"))
        pg.click("#dInfo [data-close]")

        # -- unread badge ---------------------------------------------------------------------------
        pg.evaluate(f"__la66.rx('{tuan.seal_for('team', 'nhớ mang nước').hex()}', -99, 4)")
        pg.wait_for_selector("#chatList .ci:has-text('team') .badge", timeout=5000)
        check("unread badge on another chat", pg.inner_text("#chatList .ci:has-text('team') .badge") == "1")
        pg.click("#chatList .ci:has-text('team')")
        check("opening the chat clears the badge", pg.locator("#chatList .ci:has-text('team') .badge").count() == 0)

        # -- radio settings (Settings > Advanced > Radio) ------------------------------------------------
        pg.click("#bSettings")
        pg.click("#bAdvanced")
        pg.click("#bRadio")
        pg.select_option("#rSf", "10")
        prev = pg.inner_text("#rPreview")
        check("SF10 preview shows the dwell limit", "up to 23 bytes" in prev, prev)
        pg.select_option("#rSf", "9")
        pg.click("#rApply")
        pg.wait_for_selector("#rMsg >> text=Done", timeout=30000)
        check("radio reconfigured to SF9", "AT+SF=9,9" in pg.evaluate("__la66.cmds"))
        pg.click("#dRadio [data-close]")
        pg.click("#bSettings"); pg.click("#bAdvanced"); pg.click("#bLog")
        check("radio log lists packets with signal", "-101 dBm" in pg.inner_text("#logBody"))
        pg.click("#dLog [data-close]")

        # -- lock, unlock ---------------------------------------------------------------------------
        pg.click("#bSettings")
        pg.click("#bLock")
        check("locked hides private chats", "team" not in pg.inner_text("#chatList") and "Anh Tuấn" not in pg.inner_text("#chatList"))
        sealed2 = tuan.seal_for("team", "tin nhắn khi khoá")
        pg.evaluate(f"__la66.rx('{sealed2.hex()}', -99, 4)")
        pg.wait_for_selector("#lockbar >> text=1 private message waiting", timeout=5000)
        pg.fill("#unlockPass", "lan-passphrase")
        pg.click("#bUnlock")
        pg.wait_for_selector("#chatList >> text=tin nhắn khi khoá", timeout=10000)
        check("message heard while locked opens after unlock", True)

        # -- export identity; Python opens the file -------------------------------------------------
        pg.click("#bBackup")
        pg.fill("#ePass", "export-pass-1")
        pg.fill("#ePass2", "export-pass-1")
        with pg.expect_download() as dl:
            pg.click("#eGo")
        path = os.path.join(tmp, "Lan.ltid")
        dl.value.save_as(path)
        data = V.unpack(open(path, "rb").read(), "export-pass-1")
        check("Python opens the exported identity", data.get("me") == "Lan" and "team" in data.get("groups", {}), list(data))
        pg.click("#dSettings [data-close]")
        check("backup prompt gone after export", not pg.is_visible("#backupbar"))

        # -- background tab: title count (and no errors from notify/beep) ------------------------------
        pg.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})")
        pg.evaluate(f"__la66.rx('{encode_plain(compose('ai đó?', 'Tuan')).hex()}', -90, 7)")
        pg.wait_for_function("document.title.startsWith('(1)')", timeout=5000)
        check("unread count in the tab title", True)
        pg.evaluate("delete document.hidden")

        # -- reload: history comes back (private text was stored encrypted) ---------------------------
        log_raw = pg.evaluate("atob(localStorage['textcast/log-Lan.json'])")
        check("private text is not stored in the clear", "họp lúc" not in log_raw and "enc1:" in log_raw)
        check("public text is stored", "xin ch" in log_raw)
        pg.reload()
        check("reload remembers the identity", pg.inner_text("#go") == "Start as Lan" and pg.is_visible("#pass"), pg.inner_text("#go"))
        pg.fill("#pass", "wrong-passphrase")
        pg.click("#goOffline")
        pg.wait_for_selector("#startMsg >> text=Wrong passphrase", timeout=10000)
        check("wrong passphrase refused", True)
        pg.fill("#pass", "lan-passphrase")
        pg.click("#goOffline")
        pg.wait_for_selector("#feed >> text=xin chào mọi người", timeout=10000)
        lst = pg.inner_text("#chatList")
        check("history restored after reload", "Anh Tuấn" in lst and "team" in lst, lst)
        pg.click("#chatList .ci:has-text('team')")
        check("group history restored", "ok, mình tới" in pg.inner_text("#feed"))
        check("offline mode shows a Connect button", pg.is_visible("#bReconnect"))
        check("backup prompt stays hidden", not pg.is_visible("#backupbar"))
        pg.click("#bReconnect")
        pg.wait_for_selector("text=Radio ready", timeout=30000)
        check("approved stick reused without the picker", pg.evaluate("__la66.picker") == 0,
              pg.evaluate("[__la66.picker, sessionStorage.getItem('fakeGranted'), __la66.cmds.length]"))
        for k in (1, 2):
            pg.evaluate("__la66.unplug()")
            pg.wait_for_selector("#radio.off >> text=unplugged", timeout=5000)
            check(f"unplug {k} is noticed", not pg.is_visible("#bReconnect"))
            pg.wait_for_timeout(1500 * k)
            pg.evaluate("__la66.plug()")
            pg.wait_for_selector("#radio.on", timeout=30000)
            check(f"plugging it back in reconnects by itself ({k})", "Radio on" in pg.inner_text("#radio"))
        pg.evaluate("__la66.plug(false)")                    # plugged, but 'connect' event missed
        pg.evaluate("__la66.unplug(); setTimeout(() => __la66.plug(true), 1000)")
        pg.wait_for_selector("#radio.on", timeout=30000)
        check("reconnects even without a 'connect' event", True)
        if shots:
            pg.set_viewport_size({"width": 1100, "height": 760})
            pg.screenshot(path=os.path.join(shots, "6-wide.png"))
        check("no page errors", not errors, errors)

        eu = b.new_context(timezone_id="Europe/Berlin")
        eu.add_init_script(path=FAKE)
        ep = eu.new_page(); ep.goto(PAGE)
        check("Europe gets EU868", "EU868" in ep.inner_text("#radioSummary"), ep.inner_text("#radioSummary"))
        eu.close()

        # -- served over http: offline copy and installable -------------------------------------------
        import functools, http.server, threading
        h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=os.path.join(ROOT, "web"))
        http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        profile = tempfile.mkdtemp()                       # a normal profile: incognito never offers "Install"
        kw = {"executable_path": CHROME, "args": ["--no-sandbox"]} if CHROME else {}
        sc = p.chromium.launch_persistent_context(profile, timezone_id="Asia/Ho_Chi_Minh", **kw)
        sc.add_init_script(path=FAKE)
        sp = sc.pages[0] if sc.pages else sc.new_page()
        sp.goto(url)
        sp.evaluate("navigator.serviceWorker.ready")
        sp.reload()
        sp.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10000)
        cdp = sc.new_cdp_session(sp)
        inst = cdp.send("Page.getInstallabilityErrors")
        check("installable as an app", inst.get("installabilityErrors") == [], inst)
        manifest = cdp.send("Page.getAppManifest")
        check("manifest found", "TextCast" in (manifest.get("data") or ""), manifest.get("errors"))
        srv.shutdown()                                     # the web server is gone...
        sc.set_offline(True)                               # ...and so is the internet
        sp.reload()
        sp.wait_for_selector("#nick", timeout=10000)
        check("opens offline from the saved copy", sp.title() == "TextCast")
        sp.fill("#nick", "Hoa")
        sp.click("#go")
        sp.wait_for_selector("text=Radio ready", timeout=30000)
        check("works offline end to end (identity + radio)", True)
        sc.close()
        shutil.rmtree(profile, ignore_errors=True)

        # -- start with no stick, plug it in later ---------------------------------------------------
        nc = b.new_context(viewport={"width": 1000, "height": 700})
        nc.add_init_script(path=FAKE)
        np_ = nc.new_page()
        np_.goto(PAGE)
        np_.evaluate("__la66.denyPicker = true")
        np_.fill("#nick", "Hoa")
        np_.click("#go")
        np_.wait_for_selector("#home:not(.hide)", timeout=10000)
        check("no stick: the app opens anyway", "No radio" in np_.inner_text("#radio") and np_.is_visible("#bReconnect"),
              np_.inner_text("#radio"))
        np_.fill("#text", "xin chào")
        np_.press("#text", "Enter")
        check("no stick: sending asks to plug it in, keeps the draft",
              "Plug the radio in" in np_.inner_text("#barMsg") and np_.input_value("#text") == "xin chào")
        np_.evaluate("__la66.denyPicker = false")
        np_.click("#bReconnect")
        np_.wait_for_selector("#radio.on", timeout=30000)
        np_.press("#text", "Enter")
        np_.wait_for_function("__la66.sent.length >= 1", timeout=15000)
        check("plugged in later: connected and sent", True)
        nc.close()

        # -- phone-sized screen ---------------------------------------------------------------------
        m = b.new_context(viewport={"width": 390, "height": 760}, color_scheme="dark")
        m.add_init_script(path=FAKE)
        mp = m.new_page()
        mp.goto(PAGE)
        if shots:
            mp.screenshot(path=os.path.join(shots, "7-start-dark-phone.png"))
        mp.fill("#nick", "Minh")
        mp.click("#go")
        mp.wait_for_selector("#radio.on", timeout=30000)
        check("phone: starts on the chat list", mp.is_visible("#chatList") and not mp.is_visible("#feed"))
        mp.evaluate(f"__la66.rx('{encode_plain(compose('có ai nghe không?', 'Tuan')).hex()}', -112, -4)")
        mp.wait_for_selector("#chatList .badge", timeout=5000)
        if shots:
            mp.screenshot(path=os.path.join(shots, "8-list-dark-phone.png"))
        mp.click("#chatList .ci >> nth=0")
        check("phone: tap opens the chat full screen", mp.is_visible("#feed") and not mp.is_visible("#chatList"))
        mp.fill("#text", "nghe rõ")
        mp.press("#text", "Enter")
        mp.wait_for_function("__la66.sent.length >= 1", timeout=15000)
        mp.wait_for_timeout(400)
        if shots:
            mp.screenshot(path=os.path.join(shots, "9-chat-dark-phone.png"))
        mp.go_back()
        check("phone: Back returns to the list", mp.is_visible("#chatList"))
        m.close()
        b.close()
    tuan.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("all good" if not fails else f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    t0 = time.time()
    rc = main()
    print(f"({time.time() - t0:.0f} s)")
    sys.exit(rc)
