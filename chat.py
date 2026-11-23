#!/usr/bin/env python3
"""
TextCast - main app (terminal UI, Textual).

    pip install pyserial textual qrcode      (once; qrcode is optional)
    python3 chat.py

A login panel asks for your identity, passphrase and the LA66 port.
F7 (or /radio) opens the radio settings panel. Optional flags (--port, --nick,
--region, …) pre-fill the panels.

Keys:  Ctrl+N / Ctrl+P  next / previous channel   (or click the sidebar)
       F1 help  F2 keys  F3 share  F4 sealed/signed  F5 lock  F6 me  Ctrl+Q quit
Typed commands also work: /help /keys /share /signed /announce /lock /whoami
/contacts /contact add <lid1:…> [name] /contact del <name> /quit

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
             "(or use the simple UI:  python3 simple_chat.py --port … --nick …)")

from lora_text.node import SENSITIVE, KeyError_, Msg, Node, PayloadTooLong, WrongPassphrase
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
    "unverified": ("unverified", "yellow"),
    "locked": ("", "grey50"),
    "undecoded": ("", "grey50"),
    "pending": ("", "grey50"),
}
TYPE_ICON = {"all": "◎", "public": "○", "group": "◆", "own": "▲", "follow": "▼", "other": "·"}
TYPE_COLOR = {"all": "white", "public": "yellow", "group": "magenta", "own": "dodger_blue2",
              "follow": "green", "other": "grey50"}
CHANNEL_DESC = {"all": "everything you receive", "public": "plain text · anyone can read",
                "group": "group · sealed, members only", "own": "your stream · your followers read",
                "follow": "following · read-only", "other": "packets you cannot decode"}
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


def render(m: Msg, show_channel: bool) -> Text:
    tag, color = KIND_STYLE.get(m.kind, ("", "white"))
    t = Text()
    t.append(time.strftime("%H:%M  ", time.localtime(m.ts)), style="grey50")
    if m.direction == "sys":
        t.append("• " + m.text, style="italic grey62")
        return t
    if m.direction == "tx":
        _name(t, "me", "bold dodger_blue2")
    else:
        name = m.sender or "unknown"
        if m.kind in ("plain", "unverified"):          # names in public are claims
            _name(t, name, "yellow", "?", "grey50")
        elif m.group:                                  # any member could have sent it
            _name(t, name, color, "◆", "magenta")
        elif m.verified:
            _name(t, name, f"bold {color}", "✓", "bold green")
        else:
            _name(t, name, "grey62" if m.kind in ("locked", "undecoded", "pending") else color)
    if show_channel and m.channel not in ("public", "all", "other"):
        t.append(f"{m.channel} › ", style="dodger_blue2" if m.direction == "tx" else color)
    if tag:
        t.append(f"{tag} · ", style=f"{color} italic")
    t.append(m.text, style="grey50 italic" if m.kind in ("locked", "undecoded", "pending") else "")
    if m.direction == "rx" and m.rssi is not None:
        t.append(f"   {m.rssi} dBm" + (f" · {m.snr} dB" if m.snr is not None else ""),
                 style="grey35")
    if m.direction == "tx":
        if m.status == "sent":
            t.append(f"   ✓ sent · {m.airtime:.0f} ms", style="green")
        elif m.status == "failed":
            t.append("   ✗ not sent", style="bold red")
        else:
            t.append("   … waiting for a slot", style="yellow")
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
        head("Contacts")
        contacts = k.data.get("contacts", {})
        for c in contacts.values():
            t.append(f"  {c['name']}", style="bold")
            t.append(f"   {c['handle']}\n", style="grey58")
        if not contacts:
            t.append("  none - paste someone's lid1: string (their F6) to add one\n",
                     style="grey50")
        return t

    def on_mount(self) -> None:
        self.query_one(".dialog").border_title = "Keys & contacts"
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
                       "contact": f"Saved contact '{label}' ✓"}[typ]
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


# ---------------------------------------------------------------------------
# login panel
# ---------------------------------------------------------------------------

NEW_IDENTITY = "__new__"
OTHER_PORT = "__other__"


class LoginScreen(Screen):
    """First screen: identity, passphrase, serial port. Nothing else."""

    def __init__(self, args, error: str = ""):
        super().__init__()
        self.args, self.error = args, error
        self.idents = list_identities()

    @staticmethod
    def _port_options() -> list[tuple[str, str]]:
        opts = [(f"{dev}   {desc}".rstrip(), dev) for dev, desc in list_ports()]
        return opts + [("Other port… (type it)", OTHER_PORT)]

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
        elif want_port:
            port_kw, manual = {"value": OTHER_PORT}, want_port
        else:
            port_kw, manual = {}, ""
        with VerticalScroll(classes="dialog narrow", id="login"):
            yield Label("Identity", classes="field-label first")
            yield Select(opts, value=default, allow_blank=False, id="ident")
            yield Input(placeholder="nick (1-16 characters, no spaces)",
                        value=self.args.nick or "", id="nick")
            yield Label("Passphrase", classes="field-label", id="pw-label")
            yield Input(placeholder="passphrase", password=True, id="pw")
            yield Input(placeholder="repeat the passphrase", password=True, id="pw2")
            yield Checkbox("No passphrase (lab only)", id="lab")
            yield Label("Serial port", classes="field-label")
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
            self._fail("choose the serial port (plug the module in, then ↻)")
            return
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


class ChannelItem(ListItem):
    def __init__(self, channel: str, ctype: str):
        self.channel, self.ctype = channel, ctype
        self.label = Label(self._text(0))
        super().__init__(self.label)

    def _text(self, unread: int) -> Text:
        t = Text()
        t.append(f"{TYPE_ICON.get(self.ctype, '·')} ", style=TYPE_COLOR.get(self.ctype, "white"))
        name = {"all": "All messages", "public": "Public", "other": "Undecodable"}.get(
            self.channel, self.channel) if self.ctype in ("all", "public", "other") else self.channel
        t.append(name[:18])
        if unread:
            t.append(f"  {unread} ", style="bold black on yellow")
        return t

    def set_unread(self, n: int) -> None:
        self.label.update(self._text(n))


class SectionItem(ListItem):
    """Heading in the channel list (skipped when moving through channels)."""

    def __init__(self, title: str):
        super().__init__(Label(Text(title.upper(), style="bold grey58")), classes="section")


HELP = """[b]Getting around[/b]
  The channel list on the left chooses what you read [i]and[/i] where you send.
  Ctrl+N / Ctrl+P (or click) to switch. Type a message and press Enter.

[b]Channels[/b]
  ◎ All messages   everything you receive; sending here = public plain text
  ○ Public         plain text, anyone can read
  ◆ Group          sealed, only members can read
  ▲ My stream      sealed (or signed with F4) for your followers
  ▼ Following      a stream you follow (read-only)
  · Undecodable    packets you have no key for

[b]Who sent it[/b]
  Tuan#K7QX ✓    followed stream, signed by Tuan#K7QX's key
  Thầy Tuấn ✓    the same, shown with your contact name for that key
  Lan#P2MD ◆     group message: sent with the group key, any member could send it
  Tuan ?         public plain message: the name is only a claim

[b]Keys[/b]
  F1 help   F2 keys & contacts   F3 share / QR   F4 sealed ↔ signed
  F5 lock   F6 who am I          F7 radio settings   Ctrl+Q quit

[b]Typed commands[/b]
  /keys  /share  /signed  /announce  /member HANDLE  /lock  /whoami  /radio
  /contacts  /contact add lid1:… \\[name]  /contact del NAME  /help  /quit

[b]Status[/b]
  ✓ sent · 72 ms       the radio transmitted it (time on air)
  … waiting for a slot rate limit: average 1 message per 10 s, bursts of 3
  -95 dBm · 7 dB       signal strength and quality of a received message
  The app locks itself after a period of inactivity (F5 locks now)."""


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
    #sidebar { width: 28; border: round $primary-darken-1; border-title-color: $text;
               border-title-style: bold; padding: 0; }
    #sidebar > ListItem { padding: 0 1; }
    #sidebar > .section { padding: 1 1 0 1; }
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
    InfoScreen, KeysScreen, UnlockScreen, RadioScreen, LoginScreen { align: center middle; }
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
        Binding("ctrl+n", "next_channel", "Next", priority=True),
        Binding("ctrl+p", "prev_channel", "Prev", priority=True),
        Binding("f1", "help", "Help", priority=True),
        Binding("f2", "keys", "Keys", priority=True),
        Binding("f3", "share", "Share", priority=True),
        Binding("f4", "toggle_signed", "Sealed/Signed", priority=True),
        Binding("f5", "lock", "Lock", priority=True),
        Binding("f6", "whoami", "Me", priority=True),
        Binding("f7", "radio_settings", "Radio", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.node: Node | None = None
        self.channel = "all"
        self.signed = False
        self.history: list[Msg] = []
        self.unread: dict[str, int] = {}
        self.items: list[ChannelItem] = []
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
        self.query_one("#sidebar").border_title = "Channels"
        self.query_one("#log").border_title = "Messages"
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
        self.sys(f"Opening {self.args.port} ({self.args.hw.name})…")
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
        if problems:
            self.sys("WARNING - LA66 configuration mismatch:")
            for p in problems:
                self.sys("   " + p)
            self.sys("check that this is the LA66 port and it runs the P2P firmware; "
                     "F7 opens the radio settings")
        else:
            self.sys(f"connected - LA66 firmware {self.node.firmware}, radio profile "
                     + ("applied" if self.node.applied else "OK")
                     + f" ({PROFILE.name} · {PROFILE.freq_mhz:.3f} MHz · SF{PROFILE.sf}). "
                       "Press F1 for help.")
        self.run_worker(self.rebuild_sidebar(), exclusive=True)
        inp = self.query_one("#message", Input)
        inp.disabled = False
        inp.focus()
        self.update_target()

    # -- sidebar -------------------------------------------------------------

    async def rebuild_sidebar(self) -> None:
        lv = self.query_one("#sidebar", ListView)
        await lv.clear()
        chans = self.node.channels()
        by_type = {t: [c for c, ct in chans if ct == t] for t in TYPE_ICON}
        self.rows, self.items = [], []
        for title, types in (("Overview", ("all", "public")), ("Groups", ("group",)),
                             ("My streams", ("own",)), ("Following", ("follow",)),
                             ("Other", ("other",))):
            names = [(c, t) for t in types for c in by_type.get(t, [])]
            if not names:
                continue
            self.rows.append(SectionItem(title))
            for c, t in names:
                item = ChannelItem(c, t)
                self.rows.append(item)
                self.items.append(item)
        for row in self.rows:
            await lv.append(row)
        for it in self.items:
            it.set_unread(self.unread.get(it.channel, 0))
        if self.channel not in [it.channel for it in self.items]:
            self.channel = "all"
        idx = next(i for i, r in enumerate(self.rows)
                   if isinstance(r, ChannelItem) and r.channel == self.channel)
        self._last_index = idx
        lv.index = idx
        self.update_target()

    def _next_channel_row(self, idx: int, step: int):
        n = len(self.rows)
        for k in range(1, n + 1):
            j = (idx + step * k) % n
            if isinstance(self.rows[j], ChannelItem):
                return j
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        lv = self.query_one("#sidebar", ListView)
        if isinstance(item, SectionItem):                # skip headings
            idx = lv.index or 0
            j = self._next_channel_row(idx, 1 if idx >= self._last_index else -1)
            if j is not None and j != idx:
                lv.index = j
            return
        if isinstance(item, ChannelItem):
            self._last_index = lv.index or 0
            if item.channel != self.channel:
                self.switch_channel(item.channel)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.query_one("#message", Input).focus()

    def switch_channel(self, channel: str) -> None:
        self.channel = channel
        self.unread[channel] = 0
        for it in self.items:
            if it.channel == channel:
                it.set_unread(0)
        self.redraw()
        self.update_target()

    def _move(self, step: int) -> None:
        if not self.rows:
            return
        lv = self.query_one("#sidebar", ListView)
        j = self._next_channel_row(lv.index or 0, step)
        if j is not None:
            lv.index = j

    def action_next_channel(self) -> None:
        self._move(1)

    def action_prev_channel(self) -> None:
        self._move(-1)

    # -- messages ------------------------------------------------------------

    def visible(self, m: Msg) -> bool:
        return (self.channel == "all" or m.direction == "sys" or m.channel == self.channel)

    def add_msg(self, m: Msg) -> None:
        self.history.append(m)
        if len(self.history) > 3000:
            self.history = self.history[-3000:]
        if self.visible(m):
            self._write(m)
        elif m.direction == "rx":
            self.unread[m.channel] = self.unread.get(m.channel, 0) + 1
            for it in self.items:
                if it.channel == m.channel:
                    it.set_unread(self.unread[m.channel])
        if m.kind == "join" and m.direction == "rx":
            self.run_worker(self.rebuild_sidebar(), exclusive=True)

    def _write(self, m: Msg) -> None:
        log = self.query_one("#log", RichLog)
        day = time.localtime(m.ts)[:3]
        if day != getattr(self, "_last_day", None):
            log.write(day_rule(m.ts))
            self._last_day = day
        log.write(render(m, self.channel == "all"))

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

    # -- input ---------------------------------------------------------------

    def update_target(self) -> None:
        if not self.node:
            return
        ctype = self.node.channel_type(self.channel)
        icon = TYPE_ICON.get(ctype, "·")
        label = self.query_one("#target", Label)
        inp = self.query_one("#message", Input)
        name = "Public" if ctype in ("all", "public") else self.channel
        if ctype in ("follow", "other"):
            desc, cls = f"{icon} {name} · read-only", "readonly"
            inp.placeholder = "read-only channel - switch with Ctrl+N / Ctrl+P to write"
        elif ctype in ("group", "own"):
            mode = "signed" if self.signed else "sealed"
            desc, cls = f"{icon} {name} · {mode}", "private"
            inp.placeholder = (f"message to {name} - readable by everyone, verifiable by key holders"
                               if self.signed else f"message to {name} - only key holders can read")
        else:
            desc, cls = "○ Public · plain", "public"
            inp.placeholder = "message to everyone - plain text, anyone can read"
        label.update(Text(desc))
        for c in ("public", "private", "readonly"):
            label.set_class(c == cls, c)
        title_name = {"all": "All messages", "public": "Public", "other": "Undecodable"}.get(
            self.channel, self.channel) if ctype in ("all", "public", "other") else self.channel
        self.query_one("#log").border_title = escape(
            f"{icon} {title_name}  ·  {CHANNEL_DESC.get(ctype, '')}")
        self.update_count(inp.value)

    def update_count(self, text: str) -> None:
        if not self.node:
            return
        n, mx = self.node.byte_count(self.channel, text), PROFILE.max_payload
        bar = self.query_one("#inputbar")
        bar.border_subtitle = (f"[b red]{n} / {mx} bytes - too long[/]" if n > mx
                               else f"{n} / {mx} bytes")

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
        left.append(self.node.handle or "(no nick)", style="bold")
        if self.node.locked:
            left.append("  LOCKED ", style="bold white on red")
        elif not self.node.protected:
            left.append("  UNPROTECTED ", style="bold black on yellow")
        right = Text()
        right.append(f"{PROFILE.name} · {PROFILE.freq_mhz:.3f} MHz · SF{PROFILE.sf} · "
                     f"BW {PROFILE.bw_hz / 1000:g}", style="grey74")
        right.append(f"   TX {s['duty']:.1f}%", style="grey74")
        if s["queue"]:
            right.append(f"   queue {s['queue']}", style="yellow")
        if s["wait"] > 0.5:
            right.append(f"   next slot {s['wait']:.0f}s", style="yellow")
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
