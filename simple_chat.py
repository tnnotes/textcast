#!/usr/bin/env python3
"""
TextCast - simple chat (fallback UI without Textual; the main app is chat.py).

    python3 simple_chat.py --port /dev/cu.usbserial-XXXX --nick Tuan [--plain]

Commands (type in the input line):
    /f #topic | @name | word   add a display filter (OR'ed); /f alone lists them
    /f clear                   remove all filters
    /mute name                 hide a callsign;  /unmute name
    /raw                       toggle showing undecoded/private packets
    /to public                 send plain text (default)
    /to LABEL [signed]         send sealed (or signed) on your stream / group
    /key new NAME              create your own stream -> share string for followers
    /key follow STRING         follow someone's stream (lts1:...)
    /key group new LABEL       create a group -> share string for members
    /key group join STRING     join a group (ltg1:...) and announce yourself
    /key member GROUP NAME     add a member callsign to a group by hand
    /key announce GROUP        re-announce yourself (if members missed your join)
    /key list | share LABEL | del LABEL
    /whoami                    your handle, fingerprint and lid1: string
    /contact add LID [NAME]    save a verified contact (lid1:...)
    /contact del NAME | /contacts
    /lock                      wipe keys from memory; unlock with /unlock
    /unlock                    asks for the passphrase
    /stats                     airtime used, queue, rate-limit state, log size
    /radio                     radio profile and limits
    /history [n]               show the last n messages
    /help, /quit
"""

from __future__ import annotations

import argparse
import getpass
import queue
import sys
import threading
import time

from lora_text import config as CFG
from lora_text.node import KeyError_, Msg, Node, PayloadTooLong, WrongPassphrase
from lora_text.protocol import PROFILE, Decoded, matches
from lora_text.session import resolve, unlock_or_create


def hhmmss(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


def fmt(m: Msg) -> str:
    if m.direction == "tx":
        mark = {"sent": f"sent, {m.airtime or 0:.0f} ms on air",
                "failed": "FAILED"}.get(m.status, "queued")
        tag = "" if m.kind == "plain" else f"({m.kind}) "
        return f"[{hhmmss(m.ts)}] {'me -> ' + m.channel:>14}  {tag}{m.text}   (#{m.job} {mark})"
    sig = f"{m.rssi if m.rssi is not None else '?'}dBm"
    if m.snr is not None:
        sig += f"/{m.snr}dB"
    who = (m.sender or "?") + (" ◆" if m.group else " ✓" if m.verified else "")
    if m.kind == "plain":
        body = f"{m.sender}?: {m.text}" if m.sender else m.text
    elif m.kind == "join":
        body = f"(group) {m.channel}: {m.text}"
    elif m.kind in ("sealed", "signed"):
        body = f"({m.kind}) {m.channel} | {who}: {m.text}"
    elif m.kind == "unverified":
        body = f"[unverified] {m.text}"
    else:
        body = m.text
    return f"[{hhmmss(m.ts)}] {sig:>12}  {body}"


class ChatApp:
    def __init__(self, args):
        self.out: "queue.Queue[str]" = queue.Queue()
        self.filters: set[str] = set()
        self.muted: set[str] = set()
        self.show_raw = True
        self.target = "public"
        self.signed = False
        self.node = Node(args.port, args.baud, args.nick, args.keys, args.db,
                         fetch_snr=not args.no_snr, passphrase=args.passphrase,
                         public_tag=args.public_tag,
                         log_max_rows=args.log_cfg.get("max_rows", 0),
                         log_max_days=args.log_cfg.get("max_days", 0),
                         on_msg=self.on_msg, on_update=self.on_update, on_log=self.say)
        self.nick = self.node.handle or self.node.nick
        self.log_cfg = args.log_cfg

    def say(self, line: str) -> None:
        self.out.put(line)

    # -- radio events ----------------------------------------------------------

    def on_msg(self, m: Msg) -> None:
        if m.kind in ("locked", "undecoded", "pending") and not self.show_raw:
            return
        view = Decoded("plain", b"", f"{m.sender}: {m.text}" if m.sender else (m.text or ""))
        if view.callsign and view.callsign.lower() in self.muted:
            return
        if not (matches(view, self.filters) or m.channel in self.filters):
            return
        self.say(fmt(m))

    def on_update(self, m: Msg) -> None:
        mark = "sent" if m.status == "sent" else "FAILED (no txDone)"
        self.say(f"           #{m.job} {mark}, {m.airtime or 0:.0f} ms on air")

    # -- input -------------------------------------------------------------------

    def byte_count(self, text: str) -> int:
        return self.node.byte_count(self.target, text)

    def target_desc(self) -> str:
        if self.target == "public":
            return "public"
        return f"{self.target} ({'signed' if self.signed else 'sealed'})"

    def handle(self, line: str) -> bool:
        """Process one input line. Returns False to quit."""
        line = line.strip()
        if not line:
            return True
        if line.startswith("/"):
            try:
                return self.command(line)
            except KeyError_ as e:
                self.say(f"!! {e}")
                return True
        try:
            m = self.node.send(self.target, line, self.signed)
        except (PayloadTooLong, KeyError_) as e:
            self.say(f"!! {e}")
            return True
        q = self.node.radio.queued()
        self.say(fmt(m) + (f"  [queue {q}]" if q > 1 else ""))
        return True

    def command(self, line: str) -> bool:
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        n = self.node
        if cmd in ("/quit", "/q", "/exit"):
            return False
        if cmd == "/help":
            for ln in (__doc__ or "").strip().splitlines()[4:]:
                self.say(ln)
        elif cmd == "/f":
            if arg == "clear":
                self.filters.clear()
                self.say("filters cleared (showing everything)")
            elif arg:
                self.filters.add(arg)
                self.say(f"filters: {', '.join(sorted(self.filters))}")
            else:
                self.say(f"filters: {', '.join(sorted(self.filters)) or '(none)'}")
        elif cmd == "/mute" and arg:
            self.muted.add(arg.lower().lstrip("@"))
            self.say(f"muted: {', '.join(sorted(self.muted))}")
        elif cmd == "/unmute" and arg:
            self.muted.discard(arg.lower().lstrip("@"))
            self.say(f"muted: {', '.join(sorted(self.muted)) or '(none)'}")
        elif cmd == "/raw":
            self.show_raw = not self.show_raw
            self.say(f"show undecoded/private: {self.show_raw}")
        elif cmd == "/to":
            a = arg.split()
            if not a or a[0] == "public":
                self.target, self.signed = "public", False
            elif n.channel_type(a[0]) in ("group", "own"):
                self.target, self.signed = a[0], len(a) > 1 and a[1] == "signed"
            else:
                self.say(f"'{a[0]}' is not your stream or group (/key list)")
                return True
            self.say(f"sending to: {self.target_desc()}")
        elif cmd == "/key":
            self.key_command(arg.split())
        elif cmd == "/whoami":
            handle, lid, fp = n.whoami()
            self.say(f"you: {handle}   fingerprint {fp}")
            self.say("  " + lid)
        elif cmd == "/contacts":
            for ln in n.contacts():
                self.say("  " + ln)
        elif cmd == "/contact":
            sub, _, rest = arg.partition(" ")
            if sub == "add" and rest:
                lid, _, name = rest.strip().partition(" ")
                label, _ = n.key_add(lid, name.strip() or None)
                self.say(f"saved contact {label} ✓")
            elif sub == "del" and rest:
                self.say("deleted" if n.contact_del(rest.strip()) else "no such contact")
            else:
                self.say("usage: /contact add lid1:... [name] | /contact del NAME")
        elif cmd == "/lock":
            n.lock_identity()
            self.target, self.signed = "public", False
            self.say("locked - private messages are stored and shown after /unlock")
        elif cmd == "/unlock":
            if not n.locked:
                self.say("not locked")
            else:
                pw = self.ask_secret("Passphrase: ")
                try:
                    n.unlock(pw)
                    self.say("unlocked - /history shows what arrived while locked")
                except WrongPassphrase as e:
                    self.say(f"!! {e}")
        elif cmd == "/stats":
            s = n.stats()
            self.say(f"TX airtime last hour: {s['airtime_s']:.1f} s ({s['duty']:.3f} %)")
            self.say(f"queue: {s['queue']}  |  next free slot in {s['wait']:.1f} s  |  "
                     f"limit {PROFILE.rate_per_s * 60:.0f} msg/min avg, burst {PROFILE.burst}")
            ls = n.log_stats()
            self.say(f"log: {ls['rows']:,} messages, {ls['bytes'] / 1024:.0f} KB  "
                     f"({CFG.log_limits_text(self.log_cfg)})")
        elif cmd == "/radio":
            for ln in CFG.describe(PROFILE):
                self.say(ln)
        elif cmd == "/history":
            for m in n.history(int(arg) if arg.isdigit() else 20):
                self.say("  " + fmt(m))
        else:
            self.say(f"unknown command {cmd} - /help")
        return True

    def ask_secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)

    def key_command(self, a: list[str]) -> None:
        n = self.node
        if not a or a[0] == "list":
            for ln in n.keys.listing():
                self.say("  " + ln)
        elif a[0] == "new" and len(a) >= 2:
            self.say(f"created stream '{a[1]}'. Give followers this string:")
            self.say("  " + n.key_new_stream(a[1]))
            self.say(f"post with: /to {a[1]}   (or /to {a[1]} signed)")
        elif a[0] == "follow" and len(a) >= 2:
            label, _ = n.key_add(a[1])
            self.say(f"following '{label}'")
        elif a[0] == "group" and len(a) >= 3 and a[1] == "new":
            self.say(f"created group '{a[2]}'. Give members this string:")
            self.say("  " + n.key_new_group(a[2]))
        elif a[0] == "group" and len(a) >= 3 and a[1] == "join":
            label, _ = n.key_add(a[2])
            self.say(f"joined group '{label}' and announced yourself. Post with: /to {label}")
        elif a[0] == "member" and len(a) >= 3:
            n.key_member(a[1], a[2])
            self.say(f"added {a[2]} to {a[1]}")
        elif a[0] == "announce" and len(a) >= 2:
            self.say(fmt(n.announce(a[1])))
        elif a[0] == "share" and len(a) >= 2:
            self.say("  " + n.key_share(a[1]))
        elif a[0] == "del" and len(a) >= 2:
            self.say("deleted" if n.key_delete(a[1]) else f"no key '{a[1]}'")
            if self.target == a[1]:
                self.target = "public"
        else:
            self.say("usage: /key list | new NAME | follow STRING | group new LABEL | "
                     "group join STRING | member GROUP NAME | announce GROUP | "
                     "share LABEL | del LABEL")

    # -- startup -----------------------------------------------------------------

    def start(self, configure: bool) -> None:
        print("Configuring LA66 (air profile + reset)..." if configure
              else "Checking LA66 configuration (reset)...")
        problems = self.node.start(configure)
        if self.node.applied and not problems:
            print("Applied the radio profile to the LA66.")
        if problems:
            print(f"Firmware {self.node.firmware}. WARNING - configuration mismatch:")
            for p in problems:
                print("   -", p)
            print("Run with --configure to apply the air profile.")
        else:
            print(f"Firmware {self.node.firmware}, profile OK "
                  f"({PROFILE.name} {PROFILE.freq_mhz:.3f} MHz SF{PROFILE.sf} "
                  f"BW{PROFILE.bw_hz / 1000:g}).")

    def stop(self) -> None:
        self.node.stop()


# ---------------------------------------------------------------------------
# UIs
# ---------------------------------------------------------------------------


def run_plain(app: ChatApp) -> None:
    def printer():
        while True:
            print("\r" + app.out.get())
    threading.Thread(target=printer, daemon=True).start()
    print("Type a message and press Enter. /help for commands.")
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if not app.handle(line):
            break


def run_curses(app: ChatApp) -> None:
    import curses

    def main(scr):
        curses.use_default_colors()
        scr.keypad(True)
        scr.timeout(100)
        history: list[str] = []
        buf: list[str] = []

        def ask_secret(prompt: str) -> str:            # passphrase inside curses
            h, w = scr.getmaxyx()
            scr.addstr(h - 1, 0, prompt.ljust(w - 1))
            scr.refresh()
            secret: list[str] = []
            scr.timeout(-1)
            while True:
                ch = scr.get_wch()
                if ch in ("\n", "\r") or ch == curses.KEY_ENTER:
                    break
                if ch in ("\x7f", "\b") or ch == curses.KEY_BACKSPACE:
                    if secret:
                        secret.pop()
                elif isinstance(ch, str) and ch.isprintable():
                    secret.append(ch)
            scr.timeout(100)
            return "".join(secret)

        app.ask_secret = ask_secret

        def redraw():
            h, w = scr.getmaxyx()
            scr.erase()
            rows = max(h - 2, 1)
            lines = []
            for ln in history[-500:]:
                while len(ln) > w - 1:
                    lines.append(ln[:w - 1])
                    ln = "  " + ln[w - 1:]
                lines.append(ln)
            for i, ln in enumerate(lines[-rows:]):
                try:
                    scr.addstr(i, 0, ln)
                except curses.error:
                    pass
            text = "".join(buf)
            n = app.byte_count(text)
            over = n > PROFILE.max_payload
            state = "LOCKED | " if app.node.locked else (
                "" if app.node.protected else "UNPROTECTED | ")
            status = (f" {state}{app.nick or '(no callsign)'} | to: {app.target_desc()} | "
                      f"{n}/{PROFILE.max_payload} B{' TOO LONG' if over else ''} | filters: "
                      f"{', '.join(sorted(app.filters)) or 'none'} | /help ")
            try:
                scr.addstr(h - 2, 0, status[:w - 1].ljust(w - 1), curses.A_REVERSE)
                scr.addstr(h - 1, 0, ("> " + text)[-(w - 1):])
            except curses.error:
                pass
            scr.refresh()

        app.say("Type a message and press Enter. /help for commands.")
        dirty = True
        while True:
            try:
                while True:
                    history.append(app.out.get_nowait())
                    dirty = True
            except queue.Empty:
                pass
            if dirty:
                redraw()
                dirty = False
            try:
                ch = scr.get_wch()
            except curses.error:
                continue
            except KeyboardInterrupt:
                return
            dirty = True
            if ch in ("\n", "\r") or ch == curses.KEY_ENTER:
                line, buf[:] = "".join(buf), []
                if not app.handle(line):
                    return
            elif ch in ("\x7f", "\b") or ch == curses.KEY_BACKSPACE:
                if buf:
                    buf.pop()
            elif ch == curses.KEY_RESIZE:
                pass
            elif isinstance(ch, str) and ch.isprintable():
                buf.append(ch)

    curses.wrapper(main)


def main() -> None:
    ap = argparse.ArgumentParser(description="TextCast chat (LA66 P2P)")
    ap.add_argument("--port", required=True, help="e.g. /dev/cu.usbserial-0001 or COM5")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--nick", help="your callsign")
    ap.add_argument("--configure", action="store_true",
                    help="apply the air profile to the LA66 before starting")
    ap.add_argument("--db", help="SQLite log file (default: log-<nick>.db)")
    ap.add_argument("--keys", help="identity file (default: keys-<nick>.lkey)")
    ap.add_argument("--no-password", action="store_true",
                    help="lab mode: plain keys-<nick>.json, no passphrase")
    ap.add_argument("--no-snr", action="store_true",
                    help="skip the AT+RECV follow-up (no SNR)")
    ap.add_argument("--plain", action="store_true",
                    help="simple line UI instead of curses")
    ap.add_argument("--public-tag", action="store_true",
                    help="write Nick#TAG instead of Nick in plain public messages")
    CFG.add_cli_args(ap)
    a = ap.parse_args()
    try:
        _, a.log_cfg = CFG.from_args(a)
    except CFG.ConfigError as e:
        sys.exit(f"settings error: {e}")
    print("\n".join(CFG.describe(PROFILE)))
    a.nick, a.keys, a.db = resolve(a.nick, a.keys, a.db, protected=not a.no_password)
    a.passphrase = unlock_or_create(a.keys)
    try:
        app = ChatApp(a)
    except KeyError_ as e:
        sys.exit(f"error: {e}")
    a.passphrase = None
    print(f"identity: {a.keys}   log: {a.db}"
          + ("" if app.node.protected else "   (UNPROTECTED: --no-password)"))
    app.start(a.configure)
    try:
        if a.plain:
            run_plain(app)
        else:
            try:
                run_curses(app)
            except Exception as e:  # curses unavailable / no wide-char support
                print(f"curses UI failed ({e}); falling back to --plain")
                run_plain(app)
    finally:
        app.stop()


if __name__ == "__main__":
    sys.exit(main())
