#!/usr/bin/env python3
"""
TextCast - terminal app (Textual).

    pip install pyserial textual qrcode      (once; qrcode is optional)
    python3 chat.py

Like a phone messenger: chats on the left (Public, people, groups), the open
chat on the right, type at the bottom.

Keys:  Ctrl+N new chat (add a person by their TextCast number)
       F2 settings (your number, radio, lock)   F3 chat info   F1 help   Ctrl+Q quit
       Esc goes to the chat list (arrows + Enter); Alt+Up / Alt+Down switch chats
Typed commands also work: /add NUMBER  /number  /resend  /lock  /radio  /help  /quit

If the LA66 is unplugged, plug it back in: the app reconnects by itself.

The identity (keys-<nick>.lkey) is protected by a passphrase asked at start.
It locks itself after --lock-minutes of inactivity (default 15, 0 = never).
--no-password uses a plain keys-<nick>.json (lab testing only).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time

try:
    from rich.markup import escape
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen, Screen
    from textual.widgets import (Button, Checkbox, Footer, Input, Label, ListItem, ListView,
                                 RichLog, Select, Static)
except ImportError:
    sys.exit("This app needs Textual:  python3 -m pip install textual\n"
             "(or use TextCast in the browser: see web/README.md)")

from lora_text.node import SENSITIVE, KeyError_, Msg, Node, PayloadTooLong, WrongPassphrase
from lora_text.numbers import NumberError, format_number
from lora_text import config as CFG
from lora_text import hardware as HW
from lora_text.atradio import make_factory
from lora_text.la66 import list_ports
from lora_text.protocol import PROFILE, apply_profile, set_hardware_limits
from lora_text.regions import BW_KHZ, PRESETS
from lora_text.session import LoginError, list_identities, login, new_identity_path, resolve

KIND_STYLE = {
    "plain": ("", "white"),
    "signed": ("signed", "green"),
    "sealed": ("", "cyan"),
    "join": ("group", "magenta"),
    "direct": ("", "medium_purple1"),
    "unverified": ("unverified", "yellow"),
    "locked": ("", "grey50"),
    "undecoded": ("", "grey50"),
    "pending": ("", "grey50"),
}
TYPE_ICON = {"all": "◎", "public": "◎", "direct": "●", "group": "◆", "own": "▲", "follow": "▼",
             "other": "·"}
CHAT_TYPES = ("public", "direct", "group", "own", "follow")      # shown in the chat list
TYPE_COLOR = {"all": "white", "public": "yellow", "direct": "medium_purple1", "group": "magenta",
              "own": "dodger_blue2", "follow": "green", "other": "grey50"}
CHANNEL_DESC = {"all": "everything you receive", "public": "everyone nearby can read",
                "direct": "🔒 private", "group": "🔒 group · members only",
                "own": "your channel · followers read", "follow": "channel · read-only",
                "other": "packets you cannot decode"}
NAME_W = 18                      # width of the sender column


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def copy_to_clipboard(app: App, text: str) -> bool:
    try:
        app.copy_to_clipboard(text)      # OSC 52 (iTerm2, Windows Terminal, ...)
    except Exception:
        pass
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=False, timeout=3)
                return True
            except Exception:
                pass
    return False


def qr_text(data: str) -> Text | None:
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2)
    qr.add_data(data)
    qr.make(fit=True)
    m = qr.get_matrix()
    if len(m) % 2:
        m.append([False] * len(m[0]))
    out = Text(style="black on white")
    for y in range(0, len(m), 2):
        for top, bot in zip(m[y], m[y + 1]):
            out.append("█" if top and bot else "▀" if top else "▄" if bot else " ")
        out.append("\n")
    return out


def day_rule(ts: float) -> Text:
    return Text(f"──── {time.strftime('%A %d %B %Y', time.localtime(ts))} ────", style="grey46")


def _name(t: Text, name: str, style: str, mark: str = "", mark_style: str = "") -> None:
    room = NAME_W - (len(mark) + 1 if mark else 0)
    name = name if len(name) <= room else name[:room - 1] + "…"
    t.append(name, style=style)
    used = len(name)
    if mark:
        t.append(" " + mark, style=mark_style)
        used += len(mark) + 1
    t.append(" " * max(1, NAME_W + 1 - used))


STATUS_TEXT = {"queued": ("🕓", "yellow"), "sent": ("✓", "green"), "delivered": ("✓✓", "bold green"),
               "unconfirmed": ("✓ not delivered · F8 resends", "bold yellow"),
               "failed": ("! not sent", "bold red")}


def render(m: Msg, ctype: str) -> Text:
    """One message as a chat line: theirs on the left (with the sender's name in
    Public and groups), mine on the right with ✓ / ✓✓."""
    hhmm = time.strftime("%H:%M", time.localtime(m.ts))
    if m.direction == "sys":
        return Text(f"· {m.text} ·", style="italic grey62", justify="center")
    if m.direction == "tx":
        t = Text(justify="right")
        t.append(m.text, style="bold")
        t.append(f"  {hhmm} ", style="grey50")
        mark, style = STATUS_TEXT.get(m.status or "queued", ("", ""))
        if m.status == "failed" and m.kind == "direct":
            mark += " · F8 resends"
        t.append(mark, style=style)
        return t
    t = Text()
    tag, color = KIND_STYLE.get(m.kind, ("", "white"))
    dim = m.kind in ("locked", "undecoded", "pending", "unverified")
    if ctype in ("public", "group", "all", "other"):
        name = m.sender or "?"
        t.append(name, style="grey62" if dim else f"bold {color}")
        if m.verified and not m.group and m.kind != "direct":
            t.append(" ✓", style="bold green")
        t.append(": ")
    elif m.verified and m.kind in ("signed", "sealed"):
        t.append("✓ ", style="bold green")
    t.append(m.text, style="grey50 italic" if dim else "")
    t.append(f"  {hhmm}", style="grey50")
    return t


def render_log(m: Msg, title=lambda c: c) -> Text:
    """A line of the radio log: every packet, with where it went and the signal."""
    t = Text()
    t.append(time.strftime("%d %b %H:%M  ", time.localtime(m.ts)), style="grey50")
    t.append("sent  " if m.direction == "tx" else "heard ", style="dodger_blue2" if m.direction == "tx" else "green")
    where = {"other": "not for you", "public": "public"}.get(m.channel, title(m.channel) if m.channel else "?")
    t.append(f"{m.kind:<10} {where[:14]:<14} ", style="grey62")
    if m.direction == "rx" and m.rssi is not None:
        t.append(f"{m.rssi:>4} dBm" + (f" {m.snr:>3} dB " if m.snr is not None else "        "), style="grey62")
    t.append(f"{m.size:>3} B  ", style="grey50")
    if m.sender and m.direction == "rx":
        t.append(f"{m.sender}: ", style="bold")
    t.append(m.text)
    return t


# ---------------------------------------------------------------------------
# modal screens
# ---------------------------------------------------------------------------


class InfoScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, title: str, body, copy: str | None = None, wide: bool = False):
        super().__init__()
        self.title_text, self.body, self.copy, self.wide = title, body, copy, wide

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide" if self.wide else "dialog"):
            body = self.body if isinstance(self.body, Text) else Text(str(self.body))
            yield Static(body, id="dialog-body")
            with Horizontal(classes="buttons"):
                if self.copy:
                    yield Button("Copy", id="copy")
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = escape(self.title_text)
        self.query_one("#close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy" and self.copy:
            ok = copy_to_clipboard(self.app, self.copy)
            self.app._notify("Copied. Share strings contain a secret - give them only to the "
                             "right people." if ok else "Copy failed - select the text instead.")
        else:
            self.dismiss(None)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class KeysScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, node: Node):
        super().__init__()
        self.node = node

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide"):
            yield Static(self._listing(), id="key-list")
            yield Label("Create or add", classes="section-title")
            yield Input(placeholder="a name for a new stream / group   -or-   paste lts3: / ltg2: "
                                    "/ lid1: (optionally followed by your name for them)",
                        id="key-input")
            yield Static(Text("New stream: you post, followers read.  New group: everyone posts.  "
                              "Paste a string you received to follow, join or save a contact.",
                              style="grey58"), classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Delete", id="delete", variant="error")
                yield Button("New stream", id="new-stream")
                yield Button("New group", id="new-group")
                yield Button("Follow / Join / Contact", id="add", variant="success")
                yield Button("Close", id="close", variant="primary")
            yield Static("", id="key-result")

    def _listing(self) -> Text:
        k = self.node.keys
        t = Text()

        def head(title: str) -> None:
            t.append(("\n" if t.plain else "") + title + "\n", style="bold dodger_blue2")

        head("You")
        try:
            handle, _, fp = self.node.whoami()
            t.append(f"  {handle}", style="bold")
            t.append(f"   fingerprint {fp}\n", style="grey58")
        except KeyError_:
            t.append("  (no nick - create a new identity with a nick to post in groups)\n",
                     style="grey58")
        head("My streams")
        for name in k.data["own"]:
            t.append(f"  ▲ {name}", style="dodger_blue2")
            t.append("   you post, followers read\n", style="grey58")
        if not k.data["own"]:
            t.append("  none\n", style="grey50")
        head("Groups")
        for name, g in k.data["groups"].items():
            t.append(f"  ◆ {name}", style="magenta")
            t.append(f"   {', '.join(m['name'] for m in g['members'].values())}\n", style="grey58")
        if not k.data["groups"]:
            t.append("  none\n", style="grey50")
        head("Following")
        for name, f in k.data["follow"].items():
            t.append(f"  ▼ {name}", style="green")
            t.append(f"   by {f.get('owner') or name}", style="grey58")
            t.append(" ✓\n" if f.get("verified") else "  (owner not verified)\n",
                     style="bold green" if f.get("verified") else "yellow")
        if not k.data["follow"]:
            t.append("  none\n", style="grey50")
        head("People (private messages)")
        people = k.direct_contacts() if not k.locked else []
        for handle, name in people:
            t.append(f"  ● {name}", style="medium_purple1 bold")
            t.append(f"   {handle}\n", style="grey58")
        if people:
            t.append("  They are in the chat list. Ctrl+N adds more people.\n", style="grey58")
        else:
            t.append("  none - Ctrl+N adds a person by their TextCast number\n", style="grey50")
        return t

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "Groups & channels"
        self.query_one("#key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip().split(":", 1)[0] in ("lts1", "lts2", "lts3", "ltg1", "ltg2", "lid1"):
            self.query_one("#add", Button).press()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "close":
            self.dismiss(True)
            return
        value = self.query_one("#key-input", Input).value.strip()
        result = self.query_one("#key-result", Static)
        try:
            if not value:
                raise KeyError_("type a name or paste a string first")
            if bid in ("new-stream", "new-group"):
                share = (self.node.key_new_stream(value) if bid == "new-stream"
                         else self.node.key_new_group(value))
                copied = copy_to_clipboard(self.app, share)
                result.update(Text.assemble(
                    (f"Created '{value}'. Send this string to "
                     f"{'your followers' if bid == 'new-stream' else 'the members'}"
                     + (" (copied):" if copied else ":") + "\n", "green"), (share, "bold")))
            elif bid == "add":
                share, _, name = value.partition(" ")
                label, typ = self.node.key_add(share, name.strip() or None)
                msg = {"follow": f"Following '{label}'.",
                       "group": f"Joined '{label}' and announced you to the group.",
                       "contact": f"Added {label}. Close this window: {label} is in the chat list."}[typ]
                result.update(Text(msg, style="green"))
            elif bid == "delete":
                ok = self.node.key_delete(value) or self.node.contact_del(value)
                result.update(Text(f"Deleted '{value}'." if ok else f"Nothing named '{value}'.",
                                   style="green" if ok else "red"))
            self.query_one("#key-input", Input).value = ""
            self.query_one("#key-list", Static).update(self._listing())
        except (KeyError_, ValueError) as e:
            result.update(Text(str(e), style="red"))

    def action_dismiss_screen(self) -> None:
        self.dismiss(True)


class UnlockScreen(ModalScreen):
    """Shown when the identity is locked. Cannot be dismissed without the passphrase."""

    def __init__(self, node: Node):
        super().__init__()
        self.node = node

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog narrow"):
            yield Static(Text(self.node.handle or self.node.nick or "identity", style="bold"))
            yield Static(Text("Public messages keep arriving. Private messages are stored "
                              "encrypted and appear when you unlock.", style="grey58"),
                         classes="hint")
            yield Input(placeholder="passphrase", password=True, id="pw")
            yield Static("", id="pw-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Quit", id="quit", variant="error")
                yield Button("Unlock", id="unlock", variant="primary")

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "🔒 Locked"
        self.query_one("#pw", Input).focus()

    def _try(self) -> None:
        inp = self.query_one("#pw", Input)
        try:
            self.node.unlock(inp.value)
        except (WrongPassphrase, KeyError_) as e:
            inp.value = ""
            self.query_one("#pw-error", Static).update(Text(str(e), style="red"))
            return
        self.dismiss(True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._try()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
        else:
            self._try()


class NewChatScreen(ModalScreen):
    """Ctrl+N: add a person by their TextCast number (asked over the radio), paste an
    identity string or a group invite, or start a group. Returns the chat to open."""
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, node: Node):
        super().__init__()
        self.node, self.number = node, None

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog"):
            yield Label("Their TextCast number", classes="field-label first")
            with Horizontal(classes="row"):
                yield Input(placeholder="e.g. 512 384 9076", id="num")
                yield Button("Find", id="find", variant="primary")
            yield Static("", id="status", classes="error")
            with Vertical(id="found"):
                yield Label("Save as", classes="field-label")
                yield Input(id="name")
                with Horizontal(classes="buttons"):
                    yield Button("Add and chat", id="add", variant="success")
            yield Label("Or paste an identity string or a group invite", classes="section-title")
            with Horizontal(classes="row"):
                yield Input(placeholder="lid1:…  ltg2:…  lts3:…", id="paste")
                yield Button("Add", id="paste-add")
            yield Label("Or start a group", classes="section-title")
            with Horizontal(classes="row"):
                yield Input(placeholder="group name", id="group")
                yield Button("Create", id="group-new")
            yield Static("", id="result")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "New chat"
        self.query_one("#found").display = False
        self.query_one("#num", Input).focus()

    def _status(self, text: str, style: str = "grey70") -> None:
        self.query_one("#status", Static).update(Text(text, style=style))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        target = {"num": "#find", "name": "#add", "paste": "#paste-add", "group": "#group-new"}.get(event.input.id)
        if target:
            self.query_one(target, Button).press()

    def lookup_result(self, r: dict) -> None:
        if r.get("number") != self.number:
            return
        if r["status"] == "found":
            self._status(f"Found {r['nick']} nearby.", "bold green")
            self.query_one("#found").display = True
            name = self.query_one("#name", Input)
            name.value = r["nick"]
            name.focus()
        elif r["status"] == "conflict":
            self.number = None
            self._status(f"⚠ Two different devices answered for this number ({r['nick']}). Someone may "
                         "be pretending: add the person with their identity string instead.", "bold red")
        else:
            self.number = None
            self._status(f"Nobody answered for {format_number(r['number'])}. Check the number; they need "
                         "TextCast running with the radio plugged in, within range.", "yellow")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "close":
            self.dismiss(None)
            return
        try:
            if bid == "find":
                self.query_one("#found").display = False
                r = self.node.lookup(self.query_one("#num", Input).value)
                if r["status"] == "known":
                    self.dismiss(r["channel"])
                    return
                self.number = r["number"]
                self._status(f"Looking for {format_number(r['number'])} nearby… (up to 10 s)")
            elif bid == "add":
                ch = self.node.add_found(self.number, self.query_one("#name", Input).value.strip() or None)
                self.dismiss(ch)
            elif bid == "paste-add":
                v = self.query_one("#paste", Input).value.strip()
                label, typ = self.node.key_add(v)
                self.dismiss("@" + v.split(":")[1] if typ == "contact" else label)
            elif bid == "group-new":
                name = self.query_one("#group", Input).value.strip()
                share = self.node.key_new_group(name)
                copied = copy_to_clipboard(self.app, share)
                self.query_one("#result", Static).update(Text.assemble(
                    (f"Group '{name}' created. Send this invite to the members"
                     + (" (copied)" if copied else "") + ":\n", "green"), (share, "bold")))
                self.app.refresh_chats(name)
        except (KeyError_, NumberError, ValueError) as e:
            self._status(str(e), "red")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class ChatInfoScreen(ModalScreen):
    """F3: about the open chat - a person's number (rename, delete), a group's invite."""
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, node: Node, channel: str):
        super().__init__()
        self.node, self.channel = node, channel
        self.ctype = node.channel_type(channel)

    def compose(self) -> ComposeResult:
        t, body, share = self.ctype, Text(), None
        if t == "direct":
            c = next((x for x in self.node.keys.contact_info() if "@" + x["handle"] == self.channel), None)
            body.append(f"{self.node.channel_title(self.channel)}\n", style="bold")
            if c:
                body.append(f"TextCast number  {format_number(c['number'])}\n", style="bold")
                body.append("✓ added with their identity string\n" if c["verified"] else
                            "Added by number. To be sure it's really them, check the number with them.\n",
                            style="green" if c["verified"] else "yellow")
            body.append(f"Name on the radio: {self.channel[1:]}\n", style="grey58")
        elif t == "public":
            body.append("Everyone within radio range reads the Public chat. Don't write anything private "
                        "here: use a private chat (Ctrl+N and their number).\n")
            body.append(f"You appear as {self.node.callsign}.", style="grey58")
        else:
            body.append(f"{self.channel}\n", style="bold")
            if t != "follow":
                share = self.node.key_share(self.channel)
                body.append("Invite people with this string (it holds the key - share it only with them):\n\n"
                            if t == "group" else "Followers need this string:\n\n", style="grey58")
                body.append(share + "\n\n", style="bold")
                q = qr_text(share)
                if q:
                    body.append_text(q)
        self.share = share
        with VerticalScroll(classes="dialog"):
            yield Static(body)
            if t == "direct":
                yield Label("Name", classes="field-label")
                yield Input(value=self.node.channel_title(self.channel), id="rename")
            yield Static("", id="result")
            with Horizontal(classes="buttons"):
                if share:
                    yield Button("Copy", id="copy")
                if t != "public":
                    yield Button({"direct": "Delete contact", "group": "Leave group", "own": "Delete channel",
                                  "follow": "Unfollow"}[t], id="delete", variant="error")
                if t == "direct":
                    yield Button("Save name", id="save")
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = {"direct": "Contact", "public": "Public", "group": "Group"}.get(
            self.ctype, "Channel")
        self.armed = False

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#save", Button).press()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        try:
            if bid == "copy":
                ok = copy_to_clipboard(self.app, self.share)
                self.query_one("#result", Static).update(Text("Copied." if ok else "Copy failed - select the text.",
                                                              style="green" if ok else "red"))
            elif bid == "save":
                self.node.keys.rename_contact(self.channel[1:], self.query_one("#rename", Input).value)
                self.dismiss("changed")
            elif bid == "delete":
                if not self.armed:
                    self.armed = True
                    event.button.label = "Sure? Press again"
                    return
                if self.ctype == "direct":
                    self.node.contact_del(self.channel[1:])
                else:
                    self.node.key_delete(self.channel)
                self.dismiss("deleted")
            else:
                self.dismiss(None)
        except KeyError_ as e:
            self.query_one("#result", Static).update(Text(str(e), style="red"))

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen):
    """F2: your number and identity, privacy, radio, lock. Returns an action for the app."""
    BINDINGS = [Binding("escape", "dismiss_screen", "Close")]

    def __init__(self, node: Node):
        super().__init__()
        self.node = node

    def compose(self) -> ComposeResult:
        n = self.node
        body = Text()
        body.append(f"{n.nick}\n", style="bold")
        if not n.locked:
            body.append("Your TextCast number  ", style="grey58")
            body.append(format_number(n.number) + "\n", style="bold green")
            body.append("People nearby can add you with it (Ctrl+N on their side).", style="grey58")
        with VerticalScroll(classes="dialog"):
            yield Static(body)
            yield Checkbox("Let people find me by my number", value=n.keys.findable, id="findable",
                           disabled=n.locked)
            yield Static("", id="result")
            with Horizontal(classes="buttons"):
                yield Button("Copy number", id="copy")
                yield Button("My identity string", id="whoami")
            with Horizontal(classes="buttons"):
                yield Button("Radio settings", id="radio")
                yield Button("Radio log", id="log")
                yield Button("Groups & channels", id="keys")
            with Horizontal(classes="buttons"):
                if n.protected:
                    yield Button("Lock", id="lock", variant="warning")
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "Settings"

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        try:
            self.node.set_findable(event.value)
        except KeyError_ as e:
            self.query_one("#result", Static).update(Text(str(e), style="red"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy":
            ok = copy_to_clipboard(self.app, format_number(self.node.number))
            self.query_one("#result", Static).update(Text("Copied." if ok else "Copy failed.",
                                                          style="green" if ok else "red"))
            return
        self.dismiss(None if event.button.id == "close" else event.button.id)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# login panel
# ---------------------------------------------------------------------------

NEW_IDENTITY = "__new__"
OTHER_PORT = "__other__"
AUTO_PORT = "auto"


class LoginScreen(Screen):
    """First screen: identity, passphrase, serial port. Nothing else."""

    def __init__(self, args, error: str = ""):
        super().__init__()
        self.args, self.error = args, error
        self.idents = list_identities()

    @staticmethod
    def _port_options() -> list[tuple[str, str]]:
        opts = [(f"{dev}   {desc}".rstrip(), dev) for dev, desc in list_ports()]
        return ([("Automatic - use the radio when it is plugged in", AUTO_PORT)] + opts
                + [("Other port… (type it)", OTHER_PORT)])

    def compose(self) -> ComposeResult:
        ui = self.args.cfg.get("ui", {}) if getattr(self.args, "cfg", None) else {}
        opts = [(f"{d['nick'] or '(no nick)'}" + ("" if d["protected"] else "   (no passphrase)"),
                 d["path"]) for d in self.idents]
        opts.append(("+ New identity", NEW_IDENTITY))
        want = (self.args.nick or ui.get("nick") or "").lower()
        default = next((d["path"] for d in self.idents if (d["nick"] or "").lower() == want),
                       self.idents[0]["path"] if self.idents and not self.args.nick
                       else NEW_IDENTITY)
        ports = self._port_options()
        devs = [dev for _, dev in ports]
        want_port = self.args.port or ui.get("port")
        if want_port and want_port.startswith("/dev/tty.") and \
                want_port.replace("/dev/tty.", "/dev/cu.") in devs:
            want_port = want_port.replace("/dev/tty.", "/dev/cu.")   # same device on macOS
        if want_port in devs:
            port_kw, manual = {"value": want_port}, ""
        elif want_port and want_port.startswith(("COM", "/dev/")) and self.args.port:
            port_kw, manual = {"value": OTHER_PORT}, want_port
        else:                                   # nothing chosen, or not plugged in now: automatic
            port_kw, manual = {"value": AUTO_PORT}, ""
        with VerticalScroll(classes="dialog narrow", id="login"):
            yield Label("Identity", classes="field-label first")
            yield Select(opts, value=default, allow_blank=False, id="ident")
            yield Input(placeholder="nick (1-16 characters, no spaces)",
                        value=self.args.nick or "", id="nick")
            yield Label("Passphrase", classes="field-label", id="pw-label")
            yield Input(placeholder="passphrase", password=True, id="pw")
            yield Input(placeholder="repeat the passphrase", password=True, id="pw2")
            yield Checkbox("No passphrase (lab only)", id="lab")
            yield Label("Radio (you can plug it in later)", classes="field-label")
            with Horizontal(id="port-row"):
                yield Select(ports, prompt="select the port", id="port", **port_kw)
                yield Button("↻", id="refresh")
            yield Input(placeholder="e.g. /dev/cu.usbserial-0001 or COM5", value=manual,
                        id="port-manual")
            yield Static(Text(self.error, style="red"), id="login-error", classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Settings", id="radio")
                yield Button("Quit", id="quit", variant="error")
                yield Button("Start", id="start", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#login").border_title = "TextCast"
        self._update_port()
        self._update_fields()

    def _ident(self) -> str:
        v = self.query_one("#ident", Select).value
        return v if isinstance(v, str) else NEW_IDENTITY

    def _update_fields(self) -> None:
        ident = self._ident()
        new = ident == NEW_IDENTITY
        old_plain = ident.endswith(".json") and not new
        lab = self.query_one("#lab", Checkbox).value if new else False
        nick, pw, pw2 = (self.query_one(f"#{i}", Input) for i in ("nick", "pw", "pw2"))
        self.query_one("#lab", Checkbox).display = new
        nick.display = new
        pw.display = not lab
        self.query_one("#pw-label", Label).display = not lab
        pw2.display = (new and not lab) or old_plain
        if old_plain:
            pw.placeholder = "set a passphrase to protect it (optional)"
            pw2.placeholder = "repeat it"
        elif new:
            pw.placeholder = "choose a passphrase (8+ characters, cannot be recovered)"
            pw2.placeholder = "repeat it"
        else:
            pw.placeholder = "passphrase"
        (nick if new and not nick.value else pw).focus()

    def _update_port(self) -> None:
        v = self.query_one("#port", Select).value
        self.query_one("#port-manual", Input).display = v == OTHER_PORT

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "ident":
            self._update_fields()
        elif event.select.id == "port":
            self._update_port()
            if event.value == OTHER_PORT:
                self.query_one("#port-manual", Input).focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._update_fields()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._start()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "start":
            self._start()
        elif bid == "quit":
            self.app.exit()
        elif bid == "refresh":
            self.query_one("#port", Select).set_options(self._port_options())
            self._update_port()
        elif bid == "radio":
            self.app.action_radio_settings()

    def _fail(self, msg: str) -> None:
        err = self.query_one("#login-error", Static)
        err.update(Text(msg, style="red"))
        err.scroll_visible()                  # keep the message in view on small screens

    def _start(self) -> None:
        ident = self._ident()
        lab = ident == NEW_IDENTITY and self.query_one("#lab", Checkbox).value
        pw = self.query_one("#pw", Input).value
        pw2 = self.query_one("#pw2", Input).value
        try:
            if ident == NEW_IDENTITY:
                nick = self.query_one("#nick", Input).value.strip()
                path = new_identity_path(nick, lab)
            else:
                path = ident
                nick = next((d["nick"] for d in self.idents if d["path"] == ident), None)
            self._fail("checking…")
            path, passphrase = login(path, "" if lab else pw, pw2, lab)
        except LoginError as e:
            self.query_one("#pw", Input).value = ""
            self.query_one("#pw2", Input).value = ""
            self._fail(str(e))
            return
        sel = self.query_one("#port", Select).value
        port = (self.query_one("#port-manual", Input).value.strip() if sel == OTHER_PORT
                else sel if isinstance(sel, str) else "")
        if not port:
            port = AUTO_PORT
        self.dismiss({"keys": path, "nick": nick, "passphrase": passphrase, "port": port})


# ---------------------------------------------------------------------------
# radio settings panel
# ---------------------------------------------------------------------------


class RadioScreen(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, fields: dict, log_cfg: dict, status: str = "",
                 hw_id: str = HW.DEFAULT_HARDWARE, connected: bool = False):
        super().__init__()
        self.fields, self.log_cfg, self.status = fields, log_cfg, status
        self.hw_id, self.connected = hw_id, connected
        try:
            self._orig_hw = HW.load(hw_id)
        except HW.HardwareError:
            self._orig_hw = HW.load(None)

    @staticmethod
    def _row(label: str, widget, unit: str = ""):
        parts = [Label(label, classes="r-label"), widget]
        if unit:
            parts.append(Label(unit, classes="r-unit"))
        return Horizontal(*parts, classes="r-row")

    def compose(self) -> ComposeResult:
        f = self.fields
        regions = [(f"{name}   {pr['note'].split('.')[0][:52]}", name)
                   for name, pr in PRESETS.items()]
        regions.append(("custom   set every value yourself", "custom"))
        bws = [(f"{k:g} kHz", k) for k in sorted(BW_KHZ, reverse=True)]
        dwell = "" if f["max_dwell_ms"] == "none" else f"{f['max_dwell_ms']:g}"
        duty = "" if f["duty_cycle"] == "none" else f"{f['duty_cycle'] * 100:g}"
        with VerticalScroll(classes="dialog wide", id="radio-dialog"):
            if self.status:
                yield Static(Text(self.status, style="grey58"), classes="hint")
            yield Label("Module", classes="section-title")
            hw_opts = [(f"{name}   {desc}", name) for name, desc in HW.available()
                       if not desc.startswith("invalid")]
            hw_names = [n for _, n in hw_opts]
            yield self._row("Radio module", Select(
                hw_opts, value=self.hw_id if self.hw_id in hw_names else HW.DEFAULT_HARDWARE,
                allow_blank=False, id="r-hw"))
            yield Static("", id="r-hw-hint", classes="hint")
            yield Label("Preset", classes="section-title")
            yield self._row("Region", Select(regions, value=f["region"], allow_blank=False,
                                             id="r-region"))
            yield Label("Radio", classes="section-title")
            yield self._row("Frequency", Input(f"{f['freq_mhz']:.3f}", id="r-freq"), "MHz")
            yield self._row("Spreading factor", Select([(f"SF{n}", n) for n in range(5, 13)],
                                                       value=f["sf"], allow_blank=False, id="r-sf"))
            yield self._row("Bandwidth", Select(bws, value=round(f["bw_khz"], 2),
                                                allow_blank=False, id="r-bw"))
            yield self._row("Coding rate", Select([(f"4/{n + 4}", n) for n in range(1, 5)],
                                                  value=f["cr"], allow_blank=False, id="r-cr"))
            yield self._row("TX power", Input(str(f["power_dbm"]), id="r-power"), "dBm")
            yield self._row("Preamble", Input(str(f["preamble"]), id="r-preamble"), "symbols")
            yield Label("Limits", classes="section-title")
            yield self._row("Dwell limit", Input(dwell, placeholder="empty = no limit",
                                                 id="r-dwell"), "ms")
            yield self._row("Duty cycle", Input(duty, placeholder="empty = no limit",
                                                id="r-duty"), "%")
            yield Label("Message log", classes="section-title")
            yield self._row("Keep messages", Input(str(self.log_cfg.get("max_rows", 0)),
                                                   placeholder="0 = all", id="r-rows"), "newest")
            yield self._row("Keep days", Input(f"{self.log_cfg.get('max_days', 0):g}",
                                               placeholder="0 = forever", id="r-days"), "days")
            yield Static("", id="radio-preview")
            yield Static(Text("Every node must use the same radio settings. Presets are starting "
                              "points, not legal advice: check your local rules and use the LA66 "
                              "variant for your band.", style="grey50"), classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Reset to preset", id="preset")
                yield Button("Cancel", id="cancel")
                yield Button("Apply", id="apply", variant="primary")

    def _selected_hw(self):
        v = self.query_one("#r-hw", Select).value
        try:
            return HW.load(v if isinstance(v, str) else None)
        except HW.HardwareError:
            return self._orig_hw

    def _update_hw(self) -> None:
        hw = self._selected_hw()
        set_hardware_limits(hw.max_payload or {"*": 255}, hw.firmware_overhead)  # preview
        text = hw.description + (". " if hw.description else "")
        text += ("Tested with TextCast." if hw.verified else "Untested with TextCast. " + hw.notes)
        if self.connected and hw.name != self._orig_hw.name:
            text += "  A module change takes effect at the next start."
        self.query_one("#r-hw-hint", Static).update(
            Text(text, style="grey58" if hw.verified else "yellow"))

    def _restore_limits(self) -> None:
        set_hardware_limits(self._orig_hw.max_payload or {"*": 255},
                            self._orig_hw.firmware_overhead)

    def on_mount(self) -> None:
        self._update_hw()
        self.query_one("#radio-dialog").border_title = "Radio settings"
        self.query_one("#radio-preview").border_title = "Result"
        self._preview()

    def _collect(self) -> tuple[dict, dict]:
        q = self.query_one
        val = lambda i: q(i, Input).value.strip()  # noqa: E731
        try:
            dwell, duty = val("#r-dwell"), val("#r-duty")
            radio = {"region": q("#r-region", Select).value,
                     "freq_mhz": float(val("#r-freq")),
                     "sf": q("#r-sf", Select).value,
                     "bw_khz": q("#r-bw", Select).value,
                     "cr": q("#r-cr", Select).value,
                     "power_dbm": int(val("#r-power")),
                     "preamble": int(val("#r-preamble")),
                     "max_dwell_ms": float(dwell) if dwell else "none",
                     "duty_cycle": float(duty) / 100 if duty else "none"}
            log = {"max_rows": int(val("#r-rows") or 0), "max_days": float(val("#r-days") or 0)}
        except ValueError:
            raise CFG.ConfigError("numbers only in frequency, power, preamble, dwell, duty "
                                  "and log fields")
        return radio, log

    def _preview(self) -> bool:
        preview = self.query_one("#radio-preview", Static)
        try:
            radio, log = self._collect()
            p = CFG.build_profile(radio)
            lines = CFG.describe(p)
            preview.update(Text.assemble(
                (lines[0].split("(")[0].strip() + "\n", "bold green"),
                ("\n".join(x.strip() for x in lines[1:]) + "\n", "green"),
                (CFG.log_limits_text(log), "grey58")))
            ok = True
        except CFG.ConfigError as e:
            preview.update(Text(str(e), style="red"))
            ok = False
        self.query_one("#apply", Button).disabled = not ok
        return ok

    def _fill(self, region: str) -> None:
        f = CFG.preset_fields(region)
        self.query_one("#r-freq", Input).value = f"{f['freq_mhz']:.3f}"
        self.query_one("#r-sf", Select).value = f["sf"]
        self.query_one("#r-bw", Select).value = round(f["bw_khz"], 2)
        self.query_one("#r-cr", Select).value = f["cr"]
        self.query_one("#r-power", Input).value = str(f["power_dbm"])
        self.query_one("#r-preamble", Input).value = str(f["preamble"])
        self.query_one("#r-dwell", Input).value = (
            "" if f["max_dwell_ms"] == "none" else f"{f['max_dwell_ms']:g}")
        self.query_one("#r-duty", Input).value = (
            "" if f["duty_cycle"] == "none" else f"{f['duty_cycle'] * 100:g}")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "r-hw":
            self._update_hw()
        elif event.select.id == "r-region" and isinstance(event.value, str) \
                and event.value != "custom":
            self._fill(event.value)
        self._preview()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id and event.input.id.startswith("r-"):
            self._preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply" and self._preview():
            radio, log = self._collect()
            hw = self.query_one("#r-hw", Select).value
            self._restore_limits()              # the app activates the new module itself
            self.dismiss({"radio": radio, "log": log,
                          "hardware": hw if isinstance(hw, str) else self.hw_id})
        elif event.button.id == "preset":
            region = self.query_one("#r-region", Select).value
            self._fill(region if region in PRESETS else "AS923-2")
            self._preview()
        elif event.button.id == "cancel":
            self._restore_limits()
            self.dismiss(None)

    def action_cancel(self) -> None:
        self._restore_limits()
        self.dismiss(None)


# ---------------------------------------------------------------------------
# main app
# ---------------------------------------------------------------------------


class ChatItem(ListItem):
    """A chat in the list: name and time, then the last message (like a phone messenger)."""

    def __init__(self, channel: str, ctype: str, title: str):
        self.channel, self.ctype, self.chan_title = channel, ctype, title
        self.label = Label(Text(title))
        super().__init__(self.label)

    def show(self, last: Msg | None, unread: int) -> None:
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f"{TYPE_ICON.get(self.ctype, '·')} ", style=TYPE_COLOR.get(self.ctype, "white"))
        t.append(self.chan_title[:20], style="bold")
        if last is not None:
            when = time.strftime("%H:%M" if time.localtime(last.ts)[:3] == time.localtime()[:3] else "%d %b",
                                 time.localtime(last.ts))
            t.append(f"  {when}", style="bold yellow" if unread else "grey50")
        if unread:
            t.append(f" {unread} ", style="bold black on yellow")
        t.append("\n  ")
        if last is None:
            prev = {"public": "everyone nearby", "direct": "private chat", "group": "group",
                    "own": "your channel", "follow": "channel"}.get(self.ctype, "")
        elif last.direction == "tx":
            prev = ("✓✓ " if last.status == "delivered" else "You: ") + last.text
        elif self.ctype in ("public", "group") and last.sender:
            prev = f"{last.sender}: {last.text}"
        else:
            prev = last.text
        t.append(prev[:26], style="grey62")
        self.label.update(t)


HELP = """[b]Chats[/b]
  The list on the left is your chats; the open one is on the right. Type at the bottom
  and press Enter. Click a chat, or press Esc then the arrows and Enter.
  ◎ Public        everyone nearby can read it
  ● a person      private: only the two of you; ✓✓ when their device confirms
  ◆ a group       only members can read
  ▲ / ▼           a broadcast channel you post to / follow

[b]Adding people[/b]
  Press Ctrl+N and type their TextCast number (they find theirs in F2). Your app
  asks nearby devices who has it; when they answer, save them and start writing.
  Both of you add each other. You can also paste an identity string (lid1:…),
  which proves it's really them.

[b]Ticks[/b]
  🕓 waiting to send    ✓ sent by your radio    ✓✓ delivered (private chats)
  "not delivered": no confirmation within 30 s (out of range, or they haven't
  added you) - F8 sends it again.

[b]The radio[/b]
  The top bar shows it. If you unplug the LA66, plug it back in: TextCast
  reconnects by itself. Radio settings and the radio log are in F2.

[b]Keys[/b]
  Ctrl+N new chat   F2 settings   F3 chat info   F8 resend   F1 help   Ctrl+Q quit
  Esc chat list     Alt+Up / Alt+Down previous / next chat   F5 lock

[b]Typed commands[/b]
  /add NUMBER   /number   /resend   /lock   /radio   /log   /whoami   /help   /quit"""


class RadioApp(App):
    TITLE = "TextCast"
    CSS = """
    Screen { layout: vertical; }

    /* top bar */
    #topbar { height: 1; background: $primary-darken-2; padding: 0 1; }
    #top-left { width: 1fr; }
    #top-right { width: auto; }

    /* main area */
    #body { height: 1fr; }
    #sidebar { width: 34; border: round $primary-darken-1; border-title-color: $text;
               border-title-style: bold; padding: 0; }
    #sidebar > ListItem { padding: 0 1; height: 2; }
    .row { height: auto; }
    .row Input { width: 1fr; }
    .row Button { margin-left: 1; }
    #log { border: round $primary-darken-1; padding: 0 1; border-title-color: $text;
           border-title-style: bold; }

    /* input */
    #inputbar { height: 3; border: round $accent; border-subtitle-color: $text-muted; }
    #target { width: auto; padding: 0 1; height: 1; text-style: bold; }
    #target.public { color: $warning; }
    #target.private { color: $success; }
    #target.readonly { color: $error; }
    #message { border: none; height: 1; width: 1fr; padding: 0; }

    /* dialogs */
    InfoScreen, KeysScreen, UnlockScreen, RadioScreen, LoginScreen, NewChatScreen, ChatInfoScreen,
    SettingsScreen { align: center middle; }
    .dialog { width: 80; max-width: 96%; height: auto; max-height: 92%;
              border: round $primary; border-title-color: $text; border-title-style: bold;
              background: $surface; padding: 1 2; }
    .dialog.wide { width: 100; }
    .dialog.narrow { width: 64; }
    .section-title { text-style: bold; color: $accent; margin-top: 1; }
    .field-label { text-style: bold; margin-top: 1; }
    .hint { color: $text-muted; margin-bottom: 1; }
    .error { height: auto; margin-top: 1; }
    .buttons { height: auto; margin-top: 1; align-horizontal: right; }
    .buttons Button { margin-left: 1; min-width: 10; }
    #key-input { margin-top: 1; }
    #key-result { margin-top: 1; }
    #pw { margin-top: 1; }

    /* login */
    #login { max-height: 98%; scrollbar-size-vertical: 1; }
    #login .first { margin-top: 0; }
    #port-row { height: auto; }
    #port-row Select { width: 1fr; }
    #port-row Button { margin-left: 1; min-width: 5; }

    /* radio settings */
    .r-row { height: auto; }
    .r-label { width: 20; padding: 1 1 0 0; }
    .r-unit { width: 9; padding: 1 0 0 1; color: $text-muted; }
    .r-row Input, .r-row Select { width: 1fr; }
    #radio-preview { margin-top: 1; border: round $success; padding: 0 1; height: auto;
                     border-title-color: $success; }
    """
    BINDINGS = [
        Binding("ctrl+n", "new_chat", "New chat", priority=True),
        Binding("f2", "settings", "Settings", priority=True),
        Binding("f3", "info", "Chat info", priority=True),
        Binding("f1", "help", "Help", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("escape", "focus_list", "Chats", show=False),
        Binding("alt+down", "next_channel", "Next chat", show=False, priority=True),
        Binding("alt+up", "prev_channel", "Previous chat", show=False, priority=True),
        Binding("ctrl+p", "prev_channel", "Previous chat", show=False, priority=True),
        Binding("f4", "toggle_signed", "Sealed/Signed", show=False, priority=True),
        Binding("f5", "lock", "Lock", show=False, priority=True),
        Binding("f6", "whoami", "Me", show=False, priority=True),
        Binding("f7", "radio_settings", "Radio", show=False, priority=True),
        Binding("f8", "resend", "Resend", show=False, priority=True),
        Binding("f9", "radio_log", "Radio log", show=False, priority=True),
    ]

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.node: Node | None = None
        self.channel = "public"
        self.signed = False
        self.new_chat: NewChatScreen | None = None
        self.had_radio = False
        self.history: list[Msg] = []
        self.unread: dict[str, int] = {}
        self.items: list[ChatItem] = []
        self.rows: list[ListItem] = []
        self._last_index = 0
        self.last_activity = time.monotonic()

    # -- layout --------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(Text("TextCast", style="bold"), id="top-left")
            yield Static("", id="top-right")
        with Horizontal(id="body"):
            yield ListView(id="sidebar")
            yield RichLog(id="log", wrap=True, markup=False, highlight=False, max_lines=3000)
        with Horizontal(id="inputbar"):
            yield Label("", id="target")
            yield Input(placeholder="connecting…", id="message")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#message", Input).disabled = True
        self.query_one("#sidebar").border_title = "Chats"
        self.query_one("#log").border_title = "Public"
        self.set_interval(1.0, self.update_topbar)
        self.push_screen(LoginScreen(self.args), self._login_done)

    # -- login ---------------------------------------------------------------

    def _login_done(self, result) -> None:
        if not result:
            self.exit()
            return
        a = self.args
        a.keys, a.nick, a.passphrase, a.port = (result["keys"], result["nick"],
                                                result["passphrase"], result["port"])
        a.db = a.db_arg or resolve(a.nick, a.keys, None)[2]
        try:
            a.hw = CFG.activate_hardware(a.hw_id)
        except CFG.ConfigError as e:
            self.push_screen(LoginScreen(a, error=f"{e} (open Settings to choose another "
                                                  "module)"), self._login_done)
            return
        try:                                       # remember port + identity for next time
            cfg = CFG.load(a.config)
            cfg["ui"] = {"port": a.port, "nick": a.nick}
            CFG.save(cfg, a.config)
        except (CFG.ConfigError, OSError):
            pass
        try:
            self._open_node()
        except Exception as e:
            self.node = None
            self.push_screen(LoginScreen(a, error=str(e)), self._login_done)

    def _open_node(self) -> None:
        self.sys("Starting…" if self.args.port == AUTO_PORT else f"Opening {self.args.port} ({self.args.hw.name})…")
        self.node = Node(self.args.port, self.args.baud, self.args.nick, self.args.keys,
                         self.args.db, fetch_snr=not self.args.no_snr,
                         passphrase=self.args.passphrase,
                         public_tag=self.args.public_tag,
                         log_max_rows=self.args.log_cfg.get("max_rows", 0),
                         log_max_days=self.args.log_cfg.get("max_days", 0),
                         radio_factory=make_factory(self.args.hw),
                         on_msg=lambda m: self.call_from_thread(self.add_msg, m),
                         on_update=lambda m: self.call_from_thread(self._tx_updated, m),
                         on_log=lambda s: self.call_from_thread(self.sys, s))
        self.node.on_lookup = lambda r: self.call_from_thread(self._lookup_result, r)
        self.node.on_radio = lambda st: self.call_from_thread(self._radio_state, st)
        self.args.passphrase = None                 # don't keep it around
        self.history = self.node.history(300) + self.history
        self.redraw()
        self.sys(f"identity: {self.args.keys}   log: {self.args.db}"
                 + ("" if self.node.protected else "   (UNPROTECTED: no passphrase)"))
        threading.Thread(target=self._start_radio, daemon=True).start()

    def _radio_failed(self, error: str) -> None:
        if self.node:
            self.node.stop()
            self.node = None
        self.push_screen(LoginScreen(self.args, error=f"could not use {self.args.port}: {error}"),
                         self._login_done)

    def _start_radio(self) -> None:
        try:
            problems = self.node.start(self.args.configure)
        except Exception as e:
            self.call_from_thread(self._radio_failed, str(e))
            return
        self.call_from_thread(self._radio_ready, problems)

    def _radio_ready(self, problems: list[str]) -> None:
        self.had_radio = self.node.connected
        if not self.node.connected:
            self.sys(f"no radio yet: plug in the LA66 to send - TextCast connects by itself. "
                     f"Your number is {format_number(self.node.number)}.")
        elif problems:
            self.sys("WARNING - LA66 configuration mismatch:")
            for p in problems:
                self.sys("   " + p)
            self.sys("check that this is the LA66 port and it runs the P2P firmware; "
                     "F7 opens the radio settings")
        else:
            self.sys(f"radio ready ({PROFILE.freq_mhz:.3f} MHz · SF{PROFILE.sf}). "
                     f"Your number is {format_number(self.node.number)}. Ctrl+N adds a person, F1 helps.")
        self.run_worker(self.rebuild_sidebar(), exclusive=True)
        inp = self.query_one("#message", Input)
        inp.disabled = False
        inp.focus()
        self.update_target()

    # -- sidebar -------------------------------------------------------------

    async def rebuild_sidebar(self) -> None:
        lv = self.query_one("#sidebar", ListView)
        await lv.clear()
        chats = [(c, t) for c, t in self.node.channels() if t in CHAT_TYPES]
        last = {c: self._last(c) for c, _ in chats}
        chats.sort(key=lambda x: (x[1] != "public", -(last[x[0]].ts if last[x[0]] else 0),
                                  self.node.channel_title(x[0]).lower()))
        self.items = [ChatItem(c, t, "Public" if t == "public" else self.node.channel_title(c)) for c, t in chats]
        self.rows = list(self.items)
        for it in self.items:
            await lv.append(it)
            it.show(last[it.channel], self.unread.get(it.channel, 0))
        if self.channel not in [it.channel for it in self.items]:
            self.channel = "public"
        idx = next(i for i, r in enumerate(self.items) if r.channel == self.channel)
        self._last_index = idx
        lv.index = idx
        self.update_target()

    def refresh_chats(self, channel: str | None = None) -> None:
        if channel:
            self.switch_channel(channel)
        self.run_worker(self.rebuild_sidebar(), exclusive=True)

    def _last(self, channel: str) -> Msg | None:
        for m in reversed(self.history):
            if m.channel == channel and m.direction != "sys":
                return m
        return None

    def _show_item(self, channel: str) -> None:
        for it in self.items:
            if it.channel == channel:
                it.show(self._last(channel), self.unread.get(channel, 0))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, ChatItem) and item.channel != self.channel:
            self._last_index = self.query_one("#sidebar", ListView).index or 0
            self.switch_channel(item.channel)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.query_one("#message", Input).focus()

    def switch_channel(self, channel: str) -> None:
        self.channel = channel
        self.unread[channel] = 0
        self._show_item(channel)
        self.redraw()
        self.update_target()

    def _move(self, step: int) -> None:
        if not self.items:
            return
        lv = self.query_one("#sidebar", ListView)
        lv.index = ((lv.index or 0) + step) % len(self.items)

    def action_next_channel(self) -> None:
        self._move(1)

    def action_prev_channel(self) -> None:
        self._move(-1)

    def action_focus_list(self) -> None:
        self.query_one("#sidebar", ListView).focus()

    # -- messages ------------------------------------------------------------

    def visible(self, m: Msg) -> bool:
        return m.direction == "sys" or m.channel == self.channel

    def add_msg(self, m: Msg) -> None:
        self.history.append(m)
        if len(self.history) > 3000:
            self.history = self.history[-3000:]
        if self.visible(m):
            self._write(m)
        elif m.direction == "rx" and m.kind not in ("undecoded", "locked"):
            self.unread[m.channel] = self.unread.get(m.channel, 0) + 1
        if m.direction == "sys":
            return
        if m.kind == "join" and m.direction == "rx":
            self.run_worker(self.rebuild_sidebar(), exclusive=True)
        elif self.items and self.items[0].channel != m.channel and m.channel in [i.channel for i in self.items]:
            self.run_worker(self.rebuild_sidebar(), exclusive=True)      # newest chat moves up
        else:
            self._show_item(m.channel)

    def _write(self, m: Msg) -> None:
        log = self.query_one("#log", RichLog)
        day = time.localtime(m.ts)[:3]
        if day != getattr(self, "_last_day", None):
            log.write(day_rule(m.ts))
            self._last_day = day
        log.write(render(m, self.node.channel_type(self.channel) if self.node else "public"))

    def sys(self, text: str) -> None:
        self.add_msg(Msg(time.time(), "sys", "plain", "all", None, text))

    REDRAW_LIMIT = 500        # lines re-rendered when the view changes

    def redraw(self) -> None:
        self.query_one("#log", RichLog).clear()
        self._last_day = None
        for m in [m for m in self.history if self.visible(m)][-self.REDRAW_LIMIT:]:
            self._write(m)

    def _tx_updated(self, m: Msg) -> None:
        """A sent message got its ✓: re-render only if it is near the bottom."""
        recent = [x for x in self.history[-50:] if self.visible(x)]
        if any(x is m for x in recent):
            self.redraw()
        self._show_item(m.channel)

    # -- input ---------------------------------------------------------------

    def update_target(self) -> None:
        if not self.node:
            return
        ctype = self.node.channel_type(self.channel)
        label = self.query_one("#target", Label)
        inp = self.query_one("#message", Input)
        name = "Public" if ctype in ("all", "public") else self.node.channel_title(self.channel)
        desc = CHANNEL_DESC.get(ctype, "")
        if ctype == "direct":
            c = next((x for x in self.node.keys.contact_info() if "@" + x["handle"] == self.channel), None) \
                if not self.node.locked else None
            if c:
                desc += " · " + format_number(c["number"])
            cls, inp.placeholder = "private", f"private message to {name}"
        elif ctype in ("follow", "other"):
            cls, inp.placeholder = "readonly", "read-only: only the owner posts here"
        elif ctype in ("group", "own"):
            cls = "private"
            inp.placeholder = f"message to {name}" + (" (signed: readable by everyone)" if self.signed else "")
        else:
            cls, inp.placeholder = "public", "message to everyone nearby"
        label.update(Text(f"{TYPE_ICON.get(ctype, '·')} {name[:16]}"))
        for c in ("public", "private", "readonly"):
            label.set_class(c == cls, c)
        self.query_one("#log").border_title = escape(f"{TYPE_ICON.get(ctype, '·')} {name}  ·  {desc}")
        self.update_count(inp.value)

    def update_count(self, text: str) -> None:
        if not self.node:
            return
        n, mx = self.node.byte_count(self.channel, text), PROFILE.max_payload
        bar = self.query_one("#inputbar")
        bar.border_subtitle = (f"[b red]{n} / {mx} bytes - too long for one radio message[/]" if n > mx
                               else f"{n} / {mx} bytes" if n > mx * 0.7 else "")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "message":
            self.update_count(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "message" or not self.node:
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self.command(text)
            return
        try:
            m = self.node.send(self.channel, text, self.signed)
        except (KeyError_, PayloadTooLong, ValueError) as e:
            self._notify(str(e), severity="error")
            event.input.value = text
            return
        self.add_msg(m)

    def command(self, line: str) -> None:
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        if cmd in ("/quit", "/q"):
            self.exit()
        elif cmd == "/help":
            self.action_help()
        elif cmd == "/keys":
            self.action_keys()
        elif cmd == "/share":
            self.action_share()
        elif cmd == "/signed":
            self.action_toggle_signed()
        elif cmd == "/lock":
            self.action_lock()
        elif cmd == "/whoami":
            self.action_whoami()
        elif cmd == "/radio":
            self.action_radio_settings()
        elif cmd == "/resend":
            self.action_resend()
        elif cmd == "/add":
            self.action_new_chat(arg)
        elif cmd == "/number":
            self.sys(f"your TextCast number: {format_number(self.node.number)}")
        elif cmd == "/log":
            self.action_radio_log()
        elif cmd == "/contacts":
            self.push_screen(InfoScreen("Contacts", Text("\n".join(self.node.contacts()))))
        elif cmd == "/contact":
            sub, _, rest = arg.partition(" ")
            try:
                if sub == "add" and rest:
                    lid, _, name = rest.strip().partition(" ")
                    label, _ = self.node.key_add(lid, name.strip() or None)
                    self.sys(f"saved contact {label} ✓")
                elif sub == "del" and rest:
                    self.sys("deleted" if self.node.contact_del(rest.strip())
                             else f"no contact '{rest.strip()}'")
                else:
                    self._notify("/contact add lid1:… [name]  |  /contact del NAME",
                                severity="warning")
            except KeyError_ as e:
                self._notify(str(e), severity="error")
        elif cmd == "/announce":
            if self.node.channel_type(self.channel) == "group":
                self.add_msg(self.node.announce(self.channel))
            else:
                self._notify("switch to a group channel first", severity="warning")
        elif cmd == "/member" and arg:
            try:
                self.node.key_member(self.channel, arg)
                self.sys(f"added {arg} to {self.channel}")
            except KeyError_ as e:
                self._notify(str(e), severity="error")
        else:
            self._notify(f"unknown command {cmd} (F1 for help)", severity="warning")

    # -- actions -------------------------------------------------------------

    def action_new_chat(self, number: str = "") -> None:
        if not self.node or self.node.locked:
            return
        self.new_chat = NewChatScreen(self.node)

        def done(channel) -> None:
            self.new_chat = None
            if channel:
                self.refresh_chats(channel)
        self.push_screen(self.new_chat, done)
        if number:
            def go() -> None:
                self.new_chat.query_one("#num", Input).value = number
                self.new_chat.query_one("#find", Button).press()
            self.call_after_refresh(go)

    def _lookup_result(self, r: dict) -> None:
        if self.new_chat is not None:
            self.new_chat.lookup_result(r)

    def _radio_state(self, state: str) -> None:
        if state == "lost":
            self.sys("radio unplugged - plug it back in, TextCast reconnects by itself")
        elif state == "back":
            first, self.had_radio = not self.had_radio, True
            self.sys(f"radio {'connected' if first else 'back'} ({PROFILE.freq_mhz:.3f} MHz · SF{PROFILE.sf})")
        else:
            self.sys("radio back, but " + state.partition(": ")[2])
        self.update_topbar()

    def action_info(self) -> None:
        if not self.node:
            return

        def done(result) -> None:
            if result == "deleted":
                self.channel = "public"
            if result:
                self.refresh_chats()
        self.push_screen(ChatInfoScreen(self.node, self.channel), done)

    def action_settings(self) -> None:
        if not self.node:
            return

        def done(choice) -> None:
            {"whoami": self.action_whoami, "radio": self.action_radio_settings, "log": self.action_radio_log,
             "keys": self.action_keys, "lock": self.action_lock}.get(choice, lambda: None)()
        self.push_screen(SettingsScreen(self.node), done)

    def action_radio_log(self) -> None:
        if not self.node:
            return
        lines = [render_log(m, self.node.channel_title) for m in self.history[-300:] if m.direction != "sys"]
        body = Text("\n").join(reversed(lines)) if lines else Text("Nothing heard yet.", style="grey58")
        self.push_screen(InfoScreen("Radio log (newest first)", body, wide=True))

    def action_help(self) -> None:
        self.push_screen(InfoScreen("Help", Text.from_markup(HELP), wide=True))

    def action_keys(self) -> None:
        if self.node:
            self.push_screen(KeysScreen(self.node),
                             lambda _: self.run_worker(self.rebuild_sidebar(), exclusive=True))

    def action_share(self) -> None:
        if not self.node:
            return
        try:
            share = self.node.key_share(self.channel)
        except KeyError_:
            self._notify("select a group, your stream or a followed stream first",
                        severity="warning")
            return
        ctype = self.node.channel_type(self.channel)
        who = {"own": "your followers", "group": "new members",
               "follow": "others who should follow it"}.get(ctype, "others")
        body = Text.assemble(
            (f"Send this to {who}. It contains the secret key - share it only with them.\n\n",
             "grey58"), (share + "\n\n", "bold"))
        q = qr_text(share)
        body.append_text(q if q else Text("Install qrcode (pip install qrcode) to show a QR code.",
                                          style="grey50"))
        self.push_screen(InfoScreen(f"Share · {self.channel}", body, copy=share))

    def _notify(self, message: str, **kw) -> None:
        self.notify(escape(str(message)), **kw)

    def on_key(self, event) -> None:
        self.last_activity = time.monotonic()

    def action_lock(self) -> None:
        if not self.node:
            return
        if not self.node.protected:
            self._notify("no passphrase on this identity (--no-password)", severity="warning")
            return
        if self.node.locked:
            return
        self.node.lock_identity()
        self.history = [m for m in self.history
                        if m.kind not in SENSITIVE and not (m.direction == "tx" and m.kind != "plain")]
        self.redraw()
        self.run_worker(self.rebuild_sidebar(), exclusive=True)
        self.update_target()
        self.push_screen(UnlockScreen(self.node), self._after_unlock)

    def _after_unlock(self, _result) -> None:
        self.last_activity = time.monotonic()
        self.history = self.node.history(300)
        self.sys("unlocked")
        self.redraw()
        self.run_worker(self.rebuild_sidebar(), exclusive=True)
        self.query_one("#message", Input).focus()

    def _radio_status(self) -> str:
        if not self.node:
            return ""
        ls = self.node.log_stats()
        oldest = (time.strftime("%Y-%m-%d %H:%M", time.localtime(ls["oldest"]))
                  if ls["oldest"] else "-")
        st = self.node.stats()
        return (f"Now: {PROFILE.name} on {self.args.hw.name}, "
                f"{st['airtime_s']:.1f} s on air in the last hour "
                f"({st['duty']:.2f} %"
                + (f" of {st['duty_limit']:g} % allowed)" if st["duty_limit"] else ")")
                + f"\nLog: {ls['rows']:,} messages, {ls['bytes'] / 1024:.0f} KB, oldest {oldest}")

    def action_radio_settings(self, after=None) -> None:
        def done(result) -> None:
            self._radio_saved(result)
            if after:
                after()
        self.push_screen(RadioScreen(CFG.profile_fields(PROFILE), self.args.log_cfg,
                                     self._radio_status(), self.args.hw_id,
                                     connected=self.node is not None), done)

    def _radio_saved(self, result) -> None:
        if not result:
            return
        new_hw = result.get("hardware", self.args.hw_id)
        hw_changed = new_hw != self.args.hw_id
        try:
            if hw_changed and not self.node:          # not connected yet: switch now
                self.args.hw = CFG.activate_hardware(new_hw)
                self.args.hw_id = new_hw
            profile = CFG.build_profile(result["radio"])
            cfg = CFG.load(self.args.config)
            cfg["radio"], cfg["log"] = result["radio"], result["log"]
            cfg["hardware"] = {"type": new_hw}
            CFG.save(cfg, self.args.config)
        except (CFG.ConfigError, OSError) as e:
            self._notify(str(e), severity="error")
            return
        if hw_changed and self.node:
            self.sys(f"radio module set to {new_hw} - it takes effect at the next start")
        self.args.log_cfg = result["log"]
        if not self.node:
            apply_profile(profile)
            self._notify(f"saved: {profile.name} {profile.freq_mhz:.3f} MHz SF{profile.sf}")
            return
        self.sys(f"applying {profile.name} {profile.freq_mhz:.3f} MHz SF{profile.sf} "
                 f"BW{profile.bw_hz / 1000:g} to the LA66…")
        node = self.node

        def work():
            try:
                problems = node.reconfigure(profile, result["log"]["max_rows"],
                                            result["log"]["max_days"])
            except Exception as e:
                problems = [str(e)]
            self.call_from_thread(self._reconfigured, problems)
        threading.Thread(target=work, daemon=True).start()

    def _reconfigured(self, problems: list[str]) -> None:
        if problems:
            self.sys("WARNING - the LA66 did not take all settings:")
            for p in problems:
                self.sys("   " + p)
        else:
            self.sys(f"radio profile applied: {PROFILE.name}. "
                     "Make sure every other node uses the same settings.")
        self.update_target()

    def action_whoami(self) -> None:
        if not self.node:
            return
        try:
            handle, lid, fp = self.node.whoami()
        except KeyError_ as e:
            self._notify(str(e), severity="error")
            return
        body = Text.assemble(("Handle        ", "grey58"), (f"{handle}\n", "bold"),
                             ("Fingerprint   ", "grey58"), (f"{fp}\n\n", "bold"),
                             ("Your identity string - people add it as a contact (F2):\n",
                              "grey58"), (lid + "\n\n", "bold"))
        q = qr_text(lid)
        if q:
            body.append_text(q)
            body.append("\n")
        body.append("To be sure it is really you, compare the fingerprint in person or by phone.",
                    style="grey58")
        self.push_screen(InfoScreen("Who am I", body, copy=lid))

    def action_resend(self) -> None:
        """Resend the latest private message that was not confirmed (this channel first)."""
        if not self.node:
            return
        todo = [m for m in self.history if m.kind == "direct" and m.direction == "tx"
                and m.status in ("unconfirmed", "failed")]
        here = [m for m in todo if m.channel == self.channel]
        m = (here or todo or [None])[-1]
        if m is None:
            self._notify("nothing to resend: every private message was confirmed", severity="information")
            return
        try:
            self.node.resend(m)
            self.redraw()
        except KeyError_ as e:
            self._notify(str(e), severity="error")

    def action_toggle_signed(self) -> None:
        self.signed = not self.signed
        self.update_target()
        self._notify("signed: readable by everyone, verifiable by key holders" if self.signed
                    else "sealed: only key holders can read")

    def update_topbar(self) -> None:
        if not self.node:
            return
        lock_after = self.args.lock_minutes * 60
        if (self.node.protected and not self.node.locked and lock_after > 0
                and time.monotonic() - self.last_activity > lock_after):
            self.action_lock()
        s = self.node.stats()
        left = Text()
        left.append("TextCast", style="bold")
        left.append("   ")
        left.append(self.node.nick or "(no nick)", style="bold")
        if not self.node.locked:
            left.append(f"  {format_number(self.node.number)}", style="grey74")
        if self.node.locked:
            left.append("  LOCKED ", style="bold white on red")
        elif not self.node.protected:
            left.append("  no passphrase ", style="black on yellow")
        right = Text()
        if not self.node.connected:
            right.append("○ Radio unplugged - plug it back in" if self.node.applied or self.had_radio
                         else "○ No radio - plug it in to send", style="bold black on yellow")
        else:
            right.append("● ", style="bold green")
            right.append(f"Radio on · {PROFILE.freq_mhz:.1f} MHz · SF{PROFILE.sf}", style="grey74")
            if s["queue"]:
                right.append(f"   {s['queue']} waiting", style="yellow")
            if s["wait"] > 0.5 and s["queue"]:
                right.append(f" (about {s['wait']:.0f} s)", style="yellow")
        self.query_one("#top-left", Static).update(left)
        self.query_one("#top-right", Static).update(right)


def main() -> None:
    ap = argparse.ArgumentParser(description="TextCast - just run: python3 chat.py")
    ap.add_argument("--port", help="pre-select the LA66 port in the login panel")
    ap.add_argument("--baud", type=int, help="serial speed (default: from the hardware profile)")
    ap.add_argument("--nick", help="pre-select an identity in the login panel")
    ap.add_argument("--db", help="log file (default log-<nick>.db)")
    ap.add_argument("--configure", action="store_true", help="always re-apply the radio profile")
    ap.add_argument("--no-snr", action="store_true")
    ap.add_argument("--lock-minutes", type=float, default=15,
                    help="auto-lock after this many idle minutes (0 = never)")
    ap.add_argument("--public-tag", action="store_true",
                    help="write Nick#TAG instead of Nick in plain public messages")
    CFG.add_cli_args(ap)
    a = ap.parse_args()
    try:
        _, a.log_cfg = CFG.from_args(a)
        a.cfg = CFG.load(a.config)
    except CFG.ConfigError as e:
        sys.exit(f"settings error: {e}")
    a.db_arg, a.keys, a.passphrase = a.db, None, None
    a.hw_id = (a.hardware or a.cfg.get("hardware", {}).get("type")
               or HW.DEFAULT_HARDWARE)
    app = RadioApp(a)
    try:
        app.run()
    finally:
        if app.node:
            app.node.stop()


if __name__ == "__main__":
    main()
