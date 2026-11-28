#!/usr/bin/env python3
"""
Build the TextCast web app (run from anywhere):

    python3 web/build.py

  web/src/core*.js   -> web/textcast-core.js   (the protocol core; also used by the tests)
  web/src/app.html   -> web/index.html          (one self-contained page: open it, plug in, talk)
  web/src/sw.js, manifest.webmanifest -> web/   (offline use and "Install" when served over https)

To publish, serve these from web/: index.html, sw.js, manifest.webmanifest, icons/
"""

from __future__ import annotations

import glob
import hashlib
import os
import shutil

VERSION = "0.7.0"

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")

EXPORTS = ("Bytes Crypto Ed25519 Scrypt Vault LogCipher WrongPassphrase PayloadTooLong ConfigError KeyException "
           "LockedException HardwareError airProfile MsgType Protocol RateLimiter AirtimeMeter Regions Settings "
           "Hardware La66Parser Ids Numbers NumberException Keyring MessageLog Radio La66Radio GenericAtRadio makeRadio Node sleep").split()


def core() -> str:
    parts = []
    for path in sorted(glob.glob(os.path.join(SRC, "core*.js"))):
        with open(path, encoding="utf-8") as f:
            parts.append(f.read())
    body = "\n".join(parts)
    exports = ", ".join(EXPORTS)
    return ("// TextCast core for the web: built from web/src by web/build.py - edit those files, not this one.\n"
            "(function () {\n\"use strict\";\n" + body +
            f"\nconst TextCast = {{ {exports} }};\n"
            "if (typeof module !== \"undefined\" && module.exports) module.exports = TextCast;\n"
            "else globalThis.TextCast = TextCast;\n})();\n")


def main() -> None:
    js = core()
    with open(os.path.join(HERE, "textcast-core.js"), "w", encoding="utf-8") as f:
        f.write(js)
    app = os.path.join(SRC, "app.html")
    if os.path.exists(app):
        with open(app, encoding="utf-8") as f:
            html = f.read()
        marker = "<script src=\"../textcast-core.js\"></script>"
        if marker not in html:
            raise SystemExit("app.html must load ../textcast-core.js (it is inlined here)")
        html = html.replace(marker, "<script>\n" + js.replace("</script", "<\\/script") + "</script>")
        stamp = hashlib.sha256(html.encode()).hexdigest()[:10]
        html = html.replace("__VERSION__", f"{VERSION} ({stamp})")
        with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        with open(os.path.join(SRC, "sw.js"), encoding="utf-8") as f:
            sw = f.read().replace("__VERSION__", f"{VERSION}-{stamp}")
        with open(os.path.join(HERE, "sw.js"), "w", encoding="utf-8") as f:
            f.write(sw)
        shutil.copy(os.path.join(SRC, "manifest.webmanifest"), os.path.join(HERE, "manifest.webmanifest"))
    print("built web/textcast-core.js" + (" and web/index.html" if os.path.exists(app) else ""))


if __name__ == "__main__":
    main()
