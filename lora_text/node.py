"""
UI-independent node controller: radio + keyring + log, exposed as channels
and structured messages. Used by chat.py and other front ends; testable without them.

Channels:
    all      every message (send = public)
    public   plain text and unverified signed posts (send = plain)
    <group>  sealed group messages (send = sealed / signed to the group)
    <own>    your own stream (send = sealed / signed; followers read)
    <follow> a stream you follow (read-only)
    @<handle> direct (one-to-one) messages with a contact, with delivery receipts
    other    packets nobody here can decode
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .crypto import FRAME_OVERHEAD
from .keyring import DM_MAX_ACKS, Keyring, KeyError_, Locked, WrongPassphrase, dm_id, make_handle
from .la66 import LA66Radio, Packet, TxResult
from .numbers import (NumberError, format_number, is_directory, make_answer, make_lookup,
                      number_of, parse_directory, parse_number)
from .protocol import (PROFILE, PayloadTooLong, airtime_ms, compose,
                       decode, encode_plain, frame_len_plain, normalize)
from .storage import PacketLog

READ_ONLY = ("follow", "other")
SENSITIVE = ("sealed", "signed", "join", "direct")     # text encrypted in the log
RECEIPT_WAIT = 30.0                # s after sending: no receipt -> 'unconfirmed'
RECEIPT_DELAY = (1.0, 4.0)         # s before confirming a direct message (random)
RESEND_SAME = 1800.0               # s: a resent text already shown is not shown again
STATUS_CODE = {"failed": 0, "sent": 1, "delivered": 2, "unconfirmed": 3}
STATUS_NAME = {v: k for k, v in STATUS_CODE.items()}
LOOKUP_WAIT = 10.0                 # s to wait for answers to "who has this number?"
LOOKUP_RETRY = 4.0                 # s: ask once more if nobody answered yet
ANSWER_DELAY = (0.3, 1.5)          # s before answering a lookup for my number (random)
ANSWER_GAP = 3.0                   # s: answer at most this often
QUIET_KINDS = ("receipt", "lookup")   # handled without showing a message


@dataclass
class Msg:
    ts: float
    direction: str                 # 'rx' | 'tx' | 'sys'
    kind: str                      # plain | signed | sealed | join | direct | unverified | undecoded | locked | pending | receipt
    channel: str
    sender: Optional[str]
    text: str
    rssi: Optional[int] = None
    snr: Optional[int] = None
    size: int = 0
    job: Optional[int] = None
    status: Optional[str] = None   # tx: 'queued' | 'sent' | 'failed'; direct also 'delivered' | 'unconfirmed'
    airtime: Optional[float] = None
    verified: bool = False         # owner proved by signature (followed lts3 stream) -> ✓
    group: bool = False            # group message: any member could have sent it
    row: Optional[int] = field(default=None, repr=False)
    peer: Optional[str] = None     # direct: the contact's handle
    dm_id: Optional[int] = None    # direct: id the receipt refers to
    acks: tuple = ()               # receipt: confirmed ids
    resent: bool = False           # direct rx: the sender sent this again


class Node:
    def __init__(self, port: str, baud: int, nick: Optional[str], keys_path: str,
                 db_path: str, fetch_snr: bool = True,
                 on_msg: Callable[[Msg], None] = lambda m: None,
                 on_update: Callable[[Msg], None] = lambda m: None,
                 on_log: Callable[[str], None] = lambda s: None,
                 serial_obj=None, passphrase: Optional[str] = None,
                 public_tag: bool = False, log_max_rows: int = 20000,
                 log_max_days: float = 30, radio_factory=None):
        self.lock = threading.RLock()
        self.keys = Keyring(keys_path, me=nick, lock=True, passphrase=passphrase)
        self.nick = self.keys.me
        self.public_tag = public_tag   # add #TAG to the callsign in plain messages
        self.protected = self.keys.encrypted
        self.cipher = self.keys.log_cipher()
        self.buffer: list[tuple[int, Packet]] = []     # private packets heard while locked
        self.log = PacketLog(db_path, node=self.nick)
        self.log_max_rows, self.log_max_days = log_max_rows or 0, log_max_days or 0
        self._inserts = 0
        self.prune_log()
        self.process_pending()
        self.on_msg, self.on_update, self.on_log = on_msg, on_update, on_log
        self.pending: dict[int, Msg] = {}
        self.awaiting: dict[tuple[str, int], Msg] = {}     # (peer, id) -> sent direct message
        self._acks: dict[str, list[int]] = {}               # peer -> ids to confirm
        self._seen_dm: dict[tuple[str, str], float] = {}    # (peer, text) -> when shown
        self._timers: set[threading.Timer] = set()
        self._lookups: dict[str, dict] = {}                 # number -> lookup state
        self._parts: dict[int, dict] = {}                   # answer id -> received parts
        self._last_answer = 0.0
        self.on_lookup: Callable[[dict], None] = lambda r: None   # lookup progress / result
        self.on_radio: Callable[[str], None] = lambda state: None # 'lost' | 'back' | 'problem: …'
        # optional hooks for unattended front ends:
        self.on_quiet: Callable[[Msg], None] = lambda m: None     # receipts, repeated resends
        self.reply_route: Callable[[str], dict] = lambda peer: {}  # extra radio.send() options for receipts
        # radio_factory lets another driver replace the LA66 (see docs/HARDWARE.md)
        factory = radio_factory or LA66Radio
        self.radio = factory(port, baud, on_packet=self._on_packet, on_tx=self._on_tx,
                             on_log=on_log, fetch_snr=fetch_snr, serial_obj=serial_obj)
        self.firmware = "?"
        if hasattr(self.radio, "on_state"):
            self.radio.on_state = self._radio_state

    # -- radio unplugged / plugged back -------------------------------------------

    @property
    def connected(self) -> bool:
        return getattr(self.radio, "plugged", True)

    def _radio_state(self, state: str) -> None:
        if state == "lost":
            self.on_log("LA66 unplugged - waiting for it to come back…")
            self.on_radio("lost")
        elif state == "back":
            threading.Thread(target=self._recheck, daemon=True).start()

    def _recheck(self) -> None:
        """The LA66 came back (maybe power-cycled): check its settings, re-apply if needed."""
        try:
            problems = self.radio.reset_and_check()
            if (not self.radio.banner and getattr(self.radio, "auto", False)
                    and type(self.radio).__name__ == "LA66Radio"):
                self.radio.reject("no answer like an LA66")     # some other USB device
                return
            if problems and self.radio.banner:
                problems = self.radio.configure()
            self.applied = True
        except Exception as e:
            problems = [str(e)]
        self.on_log("LA66 is back" + (f" - {'; '.join(problems)}" if problems else ""))
        self.on_radio("back" if not problems else "problem: " + "; ".join(problems))

    # -- lifecycle -----------------------------------------------------------

    def start(self, configure: bool = False, auto_apply: bool = True) -> list[str]:
        """Open the radio and verify the air profile. If the LA66 runs different
        settings (or configure=True), apply the profile and check again.
        Blocks ~3-8 s. Returns remaining problems (empty = OK)."""
        self.radio.open()
        self.applied = False
        if not self.connected:              # not plugged in yet: checked when it appears
            self.on_log("no radio yet - plug it in to send")
            return []
        problems = [] if configure else self.radio.reset_and_check()
        if (not self.radio.banner and getattr(self.radio, "auto", False)
                and type(self.radio).__name__ == "LA66Radio"):
            self.radio.reject("no answer like an LA66")
            return []
        if configure or (problems and auto_apply and self.radio.banner):
            if problems:
                self.on_log("LA66 settings differ from the profile - applying it…")
            problems = self.radio.configure()
            self.applied = True
        self.firmware = self.radio.banner.get("Firmware", "?")
        return problems

    def reconfigure(self, profile, log_max_rows: Optional[int] = None,
                    log_max_days: Optional[float] = None) -> list[str]:
        """Switch to a new radio profile while running: pause TX, apply the
        profile to the LA66, resume. Blocks ~5 s. Returns problems."""
        from .protocol import apply_profile
        if log_max_rows is not None:
            self.log_max_rows = log_max_rows or 0
        if log_max_days is not None:
            self.log_max_days = log_max_days or 0
        self.prune_log()
        self.radio.pause()
        try:
            apply_profile(profile)
            problems = self.radio.configure()
            self.applied = True
            return problems
        finally:
            self.radio.resume()

    # -- log size ----------------------------------------------------------------

    def prune_log(self) -> int:
        return self.log.prune(self.log_max_rows, self.log_max_days)

    def _logged(self) -> None:
        self._inserts += 1
        if self._inserts % 200 == 0:
            self.prune_log()

    def _later(self, delay: float, fn, *args) -> None:
        t = threading.Timer(delay, self._fire, (fn, args))
        t.daemon = True
        self._timers.add(t)
        t.start()

    def _fire(self, fn, args) -> None:
        self._timers = {t for t in self._timers if t.is_alive() and t is not threading.current_thread()}
        try:
            fn(*args)
        except Exception as e:                      # never kill the timer thread
            self.on_log(f"{getattr(fn, '__name__', 'timer')}: {e}")

    def stop(self) -> None:
        for t in list(self._timers):
            t.cancel()
        self.radio.close()
        self.log.close()
        self.keys.close()

    # -- channels --------------------------------------------------------------

    def channels(self) -> list[tuple[str, str]]:
        with self.lock:
            d = self.keys.data
            people = [] if self.locked else [("@" + h, "direct") for h, _ in self.keys.direct_contacts()]
            return ([("all", "all"), ("public", "public")] + people
                    + [(g, "group") for g in d["groups"]]
                    + [(o, "own") for o in d["own"]]
                    + [(f, "follow") for f in d["follow"]]
                    + [("other", "other")])

    def channel_type(self, channel: str) -> str:
        return dict(self.channels()).get(channel, "other")

    def channel_title(self, channel: str) -> str:
        """How to show a channel: the contact's name for '@handle', else the label."""
        if channel.startswith("@"):
            with self.lock:
                for h, name in self.keys.direct_contacts():
                    if "@" + h == channel:
                        return name
            return channel[1:]
        return channel

    def can_send(self, channel: str) -> bool:
        return self.channel_type(channel) not in READ_ONLY

    def byte_count(self, channel: str, text: str) -> int:
        if not text.strip():
            return 0
        ctype = self.channel_type(channel)
        if ctype in ("group", "own", "direct"):
            return len(normalize(text.strip()).encode("utf-8")) + FRAME_OVERHEAD + (ctype == "direct")
        return frame_len_plain(compose(text, self.callsign))

    @property
    def handle(self) -> Optional[str]:
        return self.keys.handle

    @property
    def callsign(self) -> Optional[str]:
        """Name written in plain (public) messages: nick, or handle with --public-tag."""
        return self.handle if (self.public_tag and self.handle) else self.nick

    def max_bytes(self, channel: str) -> int:
        return PROFILE.max_payload

    # -- sending -------------------------------------------------------------------

    def send(self, channel: str, text: str, signed: bool = False) -> Msg:
        ctype = self.channel_type(channel)
        text = text.strip()
        if ctype in READ_ONLY:
            raise KeyError_(f"'{channel}' is read-only - switch to public, a group or your own stream")
        if self.locked and ctype in ("group", "own", "direct"):
            raise Locked("identity is locked - unlock first")
        if not self.connected:
            raise KeyError_("plug in the radio to send")
        if ctype == "direct":
            return self._send_direct(channel, text)
        with self.lock:
            if ctype in ("group", "own"):
                frame = self.keys.seal_for(channel, text, signed)
                kind = "signed" if signed else "sealed"
            else:
                frame, kind = encode_plain(compose(text, self.callsign)), "plain"
        job = self.radio.send(frame)
        m = Msg(time.time(), "tx", kind, channel if ctype != "all" else "public",
                self.handle or self.nick or "me", text, size=len(frame), job=job,
                status="queued", airtime=airtime_ms(len(frame)), verified=True)
        if ctype == "group":
            m.group = True
        ch, snd = self._log_meta(m)
        m.row = self.log.add("tx", frame, kind, self._log_text(m), airtime_ms=m.airtime,
                             channel=ch, sender=snd)
        self._logged()
        self.pending[job] = m
        return m

    def _send_direct(self, channel: str, text: str) -> Msg:
        peer = channel[1:]
        with self.lock:
            frame, i = self.keys.seal_direct(peer, text)
        opts = self.reply_route(peer)
        job = self.radio.send(frame, **opts) if opts else self.radio.send(frame)
        m = Msg(time.time(), "tx", "direct", channel, self.handle or self.nick or "me", text,
                size=len(frame), job=job, status="queued", airtime=airtime_ms(len(frame)),
                verified=True, peer=peer, dm_id=dm_id(i))
        ch, snd = self._log_meta(m)
        m.row = self.log.add("tx", frame, "direct", self._log_text(m), airtime_ms=m.airtime,
                             channel=ch, sender=snd)
        self._logged()
        self.pending[job] = m
        return m

    def resend(self, m: Msg) -> Msg:
        """Send a direct message again (no receipt came). The receiver does not show
        it twice; a receipt for either copy marks it delivered."""
        if m.kind != "direct" or m.direction != "tx" or not m.peer:
            raise KeyError_("only direct messages can be resent")
        if m.status in ("queued", "delivered"):
            return m
        if not self.connected:
            raise KeyError_("plug in the radio to send")
        with self.lock:
            frame, i = self.keys.seal_direct(m.peer, m.text, resend=True)
        m.job, m.dm_id, m.status = self.radio.send(frame), dm_id(i), "queued"
        self.pending[m.job] = m
        self.on_update(m)
        return m

    def _set_status(self, m: Msg, status: str) -> None:
        m.status = status
        if m.row is not None:
            self.log.set_status(m.row, STATUS_CODE[status])
        self.on_update(m)

    def _receipt_timeout(self, m: Msg, ident: int) -> None:
        if m.status == "sent" and m.dm_id == ident:
            self._set_status(m, "unconfirmed")

    def _confirm(self, peer: str, ident: int) -> None:
        """Queue a receipt for a direct message; one frame confirms up to 4."""
        ids = self._acks.setdefault(peer, [])
        if ident not in ids:
            ids.append(ident)
        if len(ids) == 1:
            self._later(random.uniform(*RECEIPT_DELAY), self._send_receipts, peer)

    def _send_receipts(self, peer: str) -> None:
        ids = self._acks.pop(peer, [])
        if not ids or self.locked:
            return
        for k in range(0, len(ids), DM_MAX_ACKS):
            with self.lock:
                frame = self.keys.make_receipt(peer, ids[k:k + DM_MAX_ACKS])
            opts = self.reply_route(peer)
            self._quiet_send(frame, **opts)

    def _on_receipt(self, m: Msg) -> None:
        for ident in m.acks:
            sent = self.awaiting.pop((m.peer.lower(), ident), None)
            if sent is not None and sent.status != "delivered":
                self._set_status(sent, "delivered")

    def _quiet_send(self, frame: bytes, **opts) -> None:
        """Background frames (receipts, lookups, answers): skipped while unplugged."""
        if self.connected:
            try:
                self.radio.send(frame, **opts) if opts else self.radio.send(frame)
            except OSError:
                pass

    def announce(self, group: str) -> Msg:
        if not self.connected:
            raise KeyError_("the radio is not plugged in - announce yourself later with /announce")
        with self.lock:
            frame = self.keys.make_join(group)
        job = self.radio.send(frame)
        m = Msg(time.time(), "tx", "join", group, self.handle, "announcing myself",
                size=len(frame), job=job, status="queued", airtime=airtime_ms(len(frame)))
        self.pending[job] = m
        return m

    def _on_tx(self, r: TxResult) -> None:
        m = self.pending.pop(r.job_id, None)
        if m is None:
            return
        if m.kind == "direct" and r.ok:
            self.awaiting[(m.peer.lower(), m.dm_id)] = m
            self._later(RECEIPT_WAIT, self._receipt_timeout, m, m.dm_id)
        if m.status == "delivered":            # receipt for an earlier copy came first
            return
        m.status = "sent" if r.ok else "failed"
        if m.row is not None:
            self.log.set_tx_ok(m.row, r.ok)
        self.on_update(m)

    # -- TextCast numbers -----------------------------------------------------------

    @property
    def number(self) -> str:
        return self.keys.number

    def lookup(self, text: str) -> dict:
        """Ask nearby devices "who has this number?". Returns the first state
        ('known' | 'searching'); progress arrives through on_lookup with status
        'found' (nick, handle) | 'conflict' | 'notfound'. Raises NumberError."""
        number = parse_number(text)
        if number == self.keys.number:
            raise NumberError("that is your own number")
        with self.lock:
            c = self.keys.contact_by_number(number)
        if c is not None:
            return {"number": number, "status": "known", "name": c["name"],
                    "handle": c["handle"], "channel": "@" + c["handle"]}
        if not self.connected:
            raise KeyError_("plug in the radio: the search goes over the radio")
        self._lookups[number] = {"found": {}, "t": time.time()}
        self.radio.send(make_lookup(number))
        self._later(LOOKUP_RETRY, self._lookup_retry, number)
        self._later(LOOKUP_WAIT, self._lookup_done, number)
        return {"number": number, "status": "searching"}

    def _lookup_retry(self, number: str) -> None:
        st = self._lookups.get(number)
        if st is not None and not st["found"]:
            self._quiet_send(make_lookup(number))

    def _lookup_done(self, number: str) -> None:
        st = self._lookups.get(number)
        if st is not None and not st["found"]:
            self._lookups.pop(number, None)
            self.on_lookup({"number": number, "status": "notfound"})

    def add_found(self, number: str, name: Optional[str] = None) -> str:
        """Save the person found by lookup(); returns their chat channel '@handle'."""
        st = self._lookups.get(number)
        if not st or len(st["found"]) != 1:
            raise KeyError_("nobody (or more than one key) answered for that number")
        pub, nick = next(iter(st["found"].items()))
        with self.lock:
            self.keys.add_found(pub, nick, number, name)
            self._lookups.pop(number, None)
        return "@" + make_handle(nick, pub)

    def set_findable(self, on: bool) -> None:
        with self.lock:
            self.keys.set_findable(on)

    def _on_directory(self, pkt: Packet) -> None:
        d = parse_directory(pkt.payload)
        if d is None:
            return
        if d[0] == "lookup":
            if (d[1] == self.keys.number and self.keys.findable and self.nick
                    and time.time() - self._last_answer > ANSWER_GAP):
                self._last_answer = time.time()
                self._later(random.uniform(*ANSWER_DELAY), self._answer)
            return
        _, aid, part, parts, chunk = d
        now = time.time()
        self._parts = {k: v for k, v in self._parts.items() if now - v["t"] < 30}
        st = self._parts.setdefault(aid, {"t": now, "n": parts, "chunks": {}})
        if st["n"] != parts:
            st.update(n=parts, chunks={})
        st["chunks"][part] = chunk
        if len(st["chunks"]) < parts:
            return
        data = b"".join(st["chunks"][j] for j in range(parts))
        del self._parts[aid]
        if len(data) < 33 or not self._lookups:
            return
        pub, nick = data[:32], data[32:].decode("utf-8", "replace")
        number = number_of(pub)
        look = self._lookups.get(number)
        if look is None or pub in look["found"]:
            return
        look["found"][pub] = nick
        if len(look["found"]) == 1:
            self.on_lookup({"number": number, "status": "found", "nick": nick,
                            "handle": make_handle(nick, pub)})
        else:
            self.on_lookup({"number": number, "status": "conflict",
                            "nick": ", ".join(look["found"].values())})

    def _answer(self) -> None:
        for frame in make_answer(self.keys.pub, self.nick, random.getrandbits(8),
                                 PROFILE.max_payload):
            self._quiet_send(frame)

    # -- receiving -------------------------------------------------------------------

    def _on_packet(self, pkt: Packet) -> None:
        if is_directory(pkt.payload):
            self.log.add("rx", pkt.payload, "lookup", None, pkt.rssi, pkt.snr, ts=pkt.t)
            self._logged()
            try:
                self._on_directory(pkt)
            finally:
                self.on_quiet(Msg(pkt.t, "rx", "lookup", "other", None, "", pkt.rssi, pkt.snr,
                                  len(pkt.payload)))
            return
        if self.locked and decode(pkt.payload).kind == "reserved":
            row = self.log.add("rx", pkt.payload, "pending", None, pkt.rssi, pkt.snr, ts=pkt.t)
            self._logged()
            self.buffer.append((row, pkt))
            self.on_msg(Msg(pkt.t, "rx", "pending", "other", None,
                            f"[{len(pkt.payload)} B private - unlock to read]",
                            pkt.rssi, pkt.snr, len(pkt.payload)))
            return
        m = self.classify(pkt)
        if m.kind == "receipt":
            self.log.add("rx", pkt.payload, "receipt", None, pkt.rssi, pkt.snr, ts=pkt.t)
            self._logged()
            self._on_receipt(m)
            self.on_quiet(m)
            return
        if m.kind == "direct":
            self._confirm(m.peer, m.dm_id)
            key, now = (m.peer.lower(), m.text), time.time()
            if m.resent and now - self._seen_dm.get(key, 0) < RESEND_SAME:
                self.on_quiet(m)
                return                              # already shown: just confirm it again
            self._seen_dm[key] = now
        ch, snd = self._log_meta(m)
        m.row = self.log.add("rx", pkt.payload, m.kind, self._log_text(m), pkt.rssi, pkt.snr,
                             ts=pkt.t, channel=ch, sender=snd)
        self._logged()
        self.on_msg(m)

    def _protect(self, m: Msg, value: Optional[str]) -> Optional[str]:
        if value is not None and self.protected and m.kind in SENSITIVE and self.cipher:
            return self.cipher.encrypt(value)
        return value

    def _log_text(self, m: Msg) -> Optional[str]:
        return self._protect(m, m.text)

    def _log_meta(self, m: Msg) -> tuple[Optional[str], Optional[str]]:
        """(channel, sender) for the log; encrypted for private messages so the
        file does not reveal your groups and contacts."""
        flag = "g" if m.group else "v" if m.verified else "u"
        sender = None if m.sender is None else f"{flag}:{m.sender}"
        return self._protect(m, m.channel), self._protect(m, sender)

    # -- lock / unlock -------------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self.keys.locked

    def lock_identity(self) -> None:
        if not self.protected:
            raise KeyError_("this identity has no passphrase (started with --no-password)")
        with self.lock:
            self.keys.wipe()
            self.cipher = None

    def unlock(self, passphrase: str) -> None:
        """Raises WrongPassphrase. Opens private packets heard while locked."""
        with self.lock:
            self.keys.unlock(passphrase)
            self.cipher = self.keys.log_cipher()
            buffered, self.buffer = self.buffer, []
            for row, pkt in buffered:
                m = self.classify(pkt)
                self.log.update(row, m.kind, self._log_text(m), *self._log_meta(m))

    def process_pending(self) -> None:
        """Open private packets logged while locked in an earlier session."""
        if self.locked:
            return
        for r in self.log.rows(500, kind="pending"):
            m = self.classify(Packet(r["payload"], r["rssi"], r["snr"], r["ts"]))
            self.log.update(r["id"], m.kind, self._log_text(m), *self._log_meta(m))

    def history(self, n: int = 300) -> list[Msg]:
        out = []
        for r in self.log.rows(n):
            text, channel, sender = r["text"], r["channel"], r["sender"]
            if any(v is not None and v.startswith("enc1:") for v in (text, channel, sender)):
                if not self.cipher:
                    continue                               # locked: hide private rows
                text, channel, sender = (self.cipher.decrypt(v) for v in (text, channel, sender))
            if r["kind"] in QUIET_KINDS:
                continue
            if r["direction"] == "rx" and r["kind"] == "pending":
                text = f"[{len(r['payload'])} B private - unlock to read]"
            if text is None:
                text = decode(r["payload"]).display()
            flag, name = "u", sender
            if sender and len(sender) > 1 and sender[1] == ":" and sender[0] in "gvu":
                flag, name = sender[0], sender[2:]
            status = None
            if r["direction"] == "tx":
                status = STATUS_NAME.get(r["tx_ok"], "failed")
            out.append(Msg(r["ts"], r["direction"], r["kind"] or "plain",
                           channel or ("public" if r["kind"] == "plain" else "other"),
                           name, text, r["rssi"], r["snr"], len(r["payload"]),
                           status=status, airtime=r["airtime_ms"],
                           verified=flag == "v", group=flag == "g", row=r["id"],
                           peer=channel[1:] if channel and channel.startswith("@") else None))
        return out

    def classify(self, pkt: Packet) -> Msg:
        d = decode(pkt.payload)
        base = dict(ts=pkt.t, direction="rx", rssi=pkt.rssi, snr=pkt.snr,
                    size=len(pkt.payload))
        if d.kind == "plain":
            cs, text = d.callsign, d.text or ""
            if cs:
                text = text[len(cs) + 2:]
            return Msg(kind="plain", channel="public", sender=cs, text=text, **base)
        if d.kind == "reserved":
            with self.lock:
                op = self.keys.open(pkt.payload)
            if op:
                if op.kind == "direct":
                    peer = op.source[1:]
                    if op.inner == "receipt":
                        return Msg(kind="receipt", channel=op.source, sender=op.sender, text="",
                                   verified=True, peer=peer, acks=op.acks, **base)
                    return Msg(kind="direct", channel=op.source, sender=op.sender, text=op.text,
                               verified=True, peer=peer, dm_id=dm_id(op.counter),
                               resent=op.inner == "resend", **base)
                if op.group:          # any group member could have sent it: no contact name
                    return Msg(kind=op.kind, channel=op.source, sender=op.sender, text=op.text,
                               group=True, **base)
                with self.lock:
                    name = self.keys.display(op.sender, op.pub) if op.verified else op.sender
                return Msg(kind=op.kind, channel=op.source, sender=name, text=op.text,
                           verified=op.verified, **base)
            if d.text is not None:  # signed, but we don't hold the key
                return Msg(kind="unverified", channel="public", sender=None, text=d.text, **base)
            return Msg(kind="locked", channel="other", sender=None,
                       text=f"[{len(pkt.payload)} B sealed]", **base)
        return Msg(kind="undecoded", channel="other", sender=None,
                   text=f"[{len(pkt.payload)} B undecoded]", **base)

    # -- keys ------------------------------------------------------------------------

    def key_new_stream(self, name: str) -> str:
        with self.lock:
            return self.keys.new_own(name)

    def key_new_group(self, label: str) -> str:
        with self.lock:
            return self.keys.new_group(label)

    def key_add(self, share: str, name: Optional[str] = None) -> tuple[str, str]:
        """Follow a stream (lts3:/lts2:/lts1:), join a group (ltg2:/ltg1:) or save a
        contact (lid1:). Returns (label, type)."""
        share = share.strip()
        kind = share.split(":", 1)[0]
        with self.lock:
            if kind in ("lts1", "lts2", "lts3"):
                return self.keys.follow(share, name), "follow"
            if kind == "lid1":
                return self.keys.add_contact(share, name), "contact"
            if kind in ("ltg1", "ltg2"):
                label = self.keys.join_group(share, name)
            else:
                raise KeyError_("paste a string starting with lts3:, ltg2: or lid1:")
        try:
            self.announce(label)
        except KeyError_ as e:
            self.on_log(f"joined '{label}', but {e}")
        return label, "group"

    def whoami(self) -> tuple[Optional[str], str, str]:
        """(handle, lid string, fingerprint)"""
        with self.lock:
            return self.keys.handle, self.keys.lid(), self.keys.fingerprint()

    def contact_del(self, name: str) -> bool:
        with self.lock:
            return self.keys.remove_contact(name)

    def contacts(self) -> list[str]:
        with self.lock:
            return self.keys.contacts()

    def key_share(self, label: str) -> str:
        with self.lock:
            return self.keys.share(label)

    def key_delete(self, label: str) -> bool:
        with self.lock:
            return self.keys.remove(label)

    def key_member(self, group: str, name: str) -> None:
        with self.lock:
            self.keys.add_member(group, name)

    # -- status ----------------------------------------------------------------------

    def stats(self) -> dict:
        meter = self.radio.meter
        return {"duty": meter.duty_cycle() * 100, "queue": self.radio.queued(),
                "wait": max(self.radio.limiter.wait_time(), self.radio.duty_wait(PROFILE.max_payload)
                            if self.radio.queued() else 0.0),
                "airtime_s": meter.total_ms() / 1000,
                "duty_limit": (PROFILE.duty_cycle or 0) * 100}

    def log_stats(self) -> dict:
        return self.log.stats()


__all__ = ["Node", "Msg", "KeyError_", "Locked", "WrongPassphrase", "PayloadTooLong",
           "READ_ONLY", "NumberError", "format_number"]
