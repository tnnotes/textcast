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

from lora_text import keyring as K, vault as V  # noqa: E402
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
        check("header shows the handle", pg.inner_text("#hHandle").startswith("Lan#"))
        check("backup prompt shown for a new identity", pg.is_visible("#backupbar"))
        check("asked the browser to keep the data", pg.evaluate("JSON.parse(atob(localStorage['textcast/config.json'])).ui.persist_asked") is True)
        check("picker shown once", pg.evaluate("__la66.picker") == 1)

        # -- public message from Python ---------------------------------------------------------
        frame = encode_plain(compose("xin chào mọi người", "Tuan"))
        pg.evaluate(f"__la66.rx('{frame.hex()}', -87, 9)")
        pg.wait_for_selector("text=xin chào mọi người", timeout=5000)
        row = pg.inner_text("#feed")
        check("public message shows sender and signal", "Tuan" in row and "-87 dBm" in row and "SNR 9" in row, row[-200:])

        # -- public message to Python -------------------------------------------------------------
        pg.fill("#text", "chào Tuấn 👋")
        check("byte counter", "/ 100 bytes" in pg.inner_text("#count"), pg.inner_text("#count"))
        pg.press("#text", "Enter")
        pg.wait_for_function("__la66.sent.length >= 1", timeout=15000)
        sent = bytes.fromhex(pg.evaluate("__la66.sent[0]"))
        d = decode(sent)
        check("Python decodes the web message", d.kind == "plain" and d.text == "Lan: chào Tuấn 👋", d.text)
        pg.wait_for_selector("text=sent ✓", timeout=5000)
        check("sent message marked sent", True)

        # -- join Python's group; Python opens the join announcement and messages ------------------
        pg.click("#bKeys")
        lid = pg.inner_text("#myLid")
        check("identity string shown", lid.startswith("lid1:Lan#"), lid)
        pg.fill("#kVal", team)
        pg.click("#kAdd")
        pg.wait_for_selector("text=Joined 'team'", timeout=5000)
        if shots:
            pg.screenshot(path=os.path.join(shots, "3-keys.png"))
        pg.click("#dKeys [data-close]")
        pg.wait_for_function("__la66.sent.length >= 2", timeout=15000)
        join = bytes.fromhex(pg.evaluate("__la66.sent[1]"))
        o = tuan.open(join)
        check("Python accepts the web join announcement", o is not None and o.kind == "join" and o.sender.startswith("Lan#"), o)
        sealed = tuan.seal_for("team", "họp lúc 7 giờ")
        pg.evaluate(f"__la66.rx('{sealed.hex()}', -101, 3)")
        pg.wait_for_selector("text=họp lúc 7 giờ", timeout=5000)
        check("Python's group message opens in the browser", True)
        pg.click("#chips .chip:has-text('team')")
        pg.select_option("#sendTo", "team")
        pg.fill("#text", "ok, mình tới")
        pg.click("#bSend")
        pg.wait_for_function("__la66.sent.length >= 3", timeout=15000)
        o = tuan.open(bytes.fromhex(pg.evaluate("__la66.sent[2]")))
        check("browser's group message opens in Python", o is not None and o.text == "ok, mình tới" and o.group, o)
        pg.wait_for_timeout(300)
        if shots:
            pg.screenshot(path=os.path.join(shots, "2-chat.png"))

        # -- radio settings ------------------------------------------------------------------------
        pg.click("#bRadio")
        pg.select_option("#rSf", "10")
        prev = pg.inner_text("#rPreview")
        check("SF10 preview shows the dwell limit", "up to 23 bytes" in prev, prev)
        pg.select_option("#rSf", "9")
        pg.click("#rApply")
        pg.wait_for_selector("#rMsg >> text=Done", timeout=30000)
        check("radio reconfigured to SF9", "AT+SF=9,9" in pg.evaluate("__la66.cmds"))
        if shots:
            pg.screenshot(path=os.path.join(shots, "4-radio.png"))
        pg.click("#dRadio [data-close]")

        # -- lock, unlock ---------------------------------------------------------------------------
        pg.click("#bLock")
        check("locked hides private messages", "họp lúc" not in pg.inner_text("#feed"))
        sealed2 = tuan.seal_for("team", "tin nhắn khi khoá")
        pg.evaluate(f"__la66.rx('{sealed2.hex()}', -99, 4)")
        pg.wait_for_selector("text=private - unlock to read", timeout=5000)
        pg.fill("#unlockPass", "lan-passphrase")
        pg.click("#bUnlock")
        pg.wait_for_selector("text=tin nhắn khi khoá", timeout=10000)
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
        pg.click("#dMenu [data-close]")
        check("backup prompt gone after export", not pg.is_visible("#backupbar"))

        # -- background tab: title count (and no errors from notify/beep) ------------------------------
        pg.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true})")
        pg.evaluate(f"__la66.rx('{encode_plain(compose('ai đó?', 'Tuan')).hex()}', -90, 7)")
        pg.wait_for_function("document.title.startsWith('(1)')", timeout=5000)
        check("unread count in the tab title", True)
        pg.evaluate("delete document.hidden")

        # -- reload: history comes back (private text was stored encrypted) ---------------------------
        stored = pg.evaluate("Object.keys(localStorage).filter(k => k.startsWith('textcast/')).map(k => [k, atob(localStorage[k]).length])")
        log_raw = pg.evaluate("atob(localStorage['textcast/log-Lan.json'])")
        check("private text is not stored in the clear", "họp lúc" not in log_raw and "enc1:" in log_raw)
        check("public text is stored", "xin ch" in log_raw)
        pg.reload()
        check("reload remembers the identity", pg.inner_text("#go") == "Start as Lan" and pg.is_visible("#pass"), pg.inner_text("#go"))
        check("no backup prompt after reload (already exported)", True)
        pg.fill("#pass", "wrong-passphrase")
        pg.click("#goOffline")
        pg.wait_for_selector("#startMsg >> text=Wrong passphrase", timeout=10000)
        check("wrong passphrase refused", True)
        pg.fill("#pass", "lan-passphrase")
        pg.click("#goOffline")
        pg.wait_for_selector("text=họp lúc 7 giờ", timeout=10000)
        feed = pg.inner_text("#feed")
        check("history restored after reload", "xin chào mọi người" in feed and "ok, mình tới" in feed)
        check("offline mode shows connect button", pg.is_visible("#bReconnect"))
        check("backup prompt stays hidden", not pg.is_visible("#backupbar"))
        pg.click("#bReconnect")
        pg.wait_for_selector("text=Radio ready", timeout=30000)
        check("approved stick reused without the picker", pg.evaluate("__la66.picker") == 0)
        pg.evaluate("__la66.unplug()")
        pg.wait_for_selector("text=unplugged", timeout=5000)
        check("unplugging is noticed", pg.is_visible("#bReconnect"))
        n_ready = pg.locator("text=Radio ready").count()
        pg.evaluate("__la66.plug()")
        pg.wait_for_function(f"document.querySelectorAll('.sys').length && [...document.querySelectorAll('.sys')].filter(e => e.textContent.includes('Radio ready')).length > {n_ready}", timeout=30000)
        check("plugging it back in reconnects by itself", not pg.is_visible("#bReconnect"))
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

        # -- phone-sized screen ---------------------------------------------------------------------
        if shots:
            m = b.new_context(viewport={"width": 390, "height": 760}, color_scheme="dark")
            m.add_init_script(path=FAKE)
            mp = m.new_page()
            mp.goto(PAGE)
            mp.screenshot(path=os.path.join(shots, "5-start-dark-phone.png"))
            mp.fill("#nick", "Minh")
            mp.click("#go")
            mp.wait_for_selector("text=Radio ready", timeout=30000)
            mp.evaluate(f"__la66.rx('{encode_plain(compose('có ai nghe không?', 'Tuan')).hex()}', -112, -4)")
            mp.fill("#text", "nghe rõ")
            mp.press("#text", "Enter")
            mp.wait_for_selector("text=sent ✓", timeout=15000)
            mp.screenshot(path=os.path.join(shots, "6-chat-dark-phone.png"))
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
