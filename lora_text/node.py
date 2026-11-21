"""
UI-independent node controller: radio + keyring + log, exposed as channels
and structured messages. Used by the terminal UI (tui.py); testable without it.

Channels:
    all      every message (send = public)
    public   plain text and unverified signed posts (send = plain)
    <group>  sealed group messages (send = sealed / signed to the group)
    <own>    your own stream (send = sealed / signed; followers read)
    <follow> a stream you follow (read-only)
    other    packets nobody here can decode
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .crypto import FRAME_OVERHEAD
from .keyring import Keyring, KeyError_, Locked, WrongPassphrase
from .la66 import LA66Radio, Packet, TxResult
from .protocol import (PROFILE, PayloadTooLong, airtime_ms, compose,
                       decode, encode_plain, frame_len_plain, normalize)
from .storage import PacketLog

READ_ONLY = ("follow", "other")
SENSITIVE = ("sealed", "signed", "join")     # text encrypted in the log


@dataclass
class Msg:
    ts: float
    direction: str                 # 'rx' | 'tx' | 'sys'
    kind: str                      # plain | signed | sealed | join | unverified | undecoded | locked | pending
    channel: str
    sender: Optional[str]
    text: str
    rssi: Optional[int] = None
    snr: Optional[int] = None
    size: int = 0
    job: Optional[int] = None
    status: Optional[str] = None   # tx: 'queued' | 'sent' | 'failed'
    airtime: Optional[float] = None
    verified: bool = False         # owner proved by signature (followed lts3 stream) -> ✓
    group: bool = False            # group message: any member could have sent it
    row: Optional[int] = field(default=None, repr=False)


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
        # radio_factory lets another driver replace the LA66 (see docs/HARDWARE.md)
        factory = radio_factory or LA66Radio
        self.radio = factory(port, baud, on_packet=self._on_packet, on_tx=self._on_tx,
                             on_log=on_log, fetch_snr=fetch_snr, serial_obj=serial_obj)
        self.firmware = "?"

    # -- lifecycle -----------------------------------------------------------

    def start(self, configure: bool = False, auto_apply: bool = True) -> list[str]:
        """Open the radio and verify the air profile. If the LA66 runs different
        settings (or configure=True), apply the profile and check again.
        Blocks ~3-8 s. Returns remaining problems (empty = OK)."""
        self.radio.open()
        self.applied = False
        problems = [] if configure else self.radio.reset_and_check()
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

    def stop(self) -> None:
        self.radio.close()
        self.log.close()
        self.keys.close()

    # -- channels --------------------------------------------------------------

    def channels(self) -> list[tuple[str, str]]:
        with self.lock:
            d = self.keys.data
            return ([("all", "all"), ("public", "public")]
                    + [(g, "group") for g in d["groups"]]
                    + [(o, "own") for o in d["own"]]
                    + [(f, "follow") for f in d["follow"]]
                    + [("other", "other")])

    def channel_type(self, channel: str) -> str:
        return dict(self.channels()).get(channel, "other")

    def can_send(self, channel: str) -> bool:
        return self.channel_type(channel) not in READ_ONLY

    def byte_count(self, channel: str, text: str) -> int:
        if not text.strip():
            return 0
        if self.channel_type(channel) in ("group", "own"):
            return len(normalize(text.strip()).encode("utf-8")) + FRAME_OVERHEAD
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
        if self.locked and ctype in ("group", "own"):
            raise Locked("identity is locked - unlock first")
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

    def announce(self, group: str) -> Msg:
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
        m.status = "sent" if r.ok else "failed"
        if m.row is not None:
            self.log.set_tx_ok(m.row, r.ok)
        self.on_update(m)

    # -- receiving -------------------------------------------------------------------

    def _on_packet(self, pkt: Packet) -> None:
        if self.locked and decode(pkt.payload).kind == "reserved":
            row = self.log.add("rx", pkt.payload, "pending", None, pkt.rssi, pkt.snr, ts=pkt.t)
            self._logged()
            self.buffer.append((row, pkt))
            self.on_msg(Msg(pkt.t, "rx", "pending", "other", None,
                            f"[{len(pkt.payload)} B private - unlock to read]",
                            pkt.rssi, pkt.snr, len(pkt.payload)))
            return
        m = self.classify(pkt)
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
            if r["direction"] == "rx" and r["kind"] == "pending":
                text = f"[{len(r['payload'])} B private - unlock to read]"
            if text is None:
                text = decode(r["payload"]).display()
            flag, name = "u", sender
            if sender and len(sender) > 1 and sender[1] == ":" and sender[0] in "gvu":
                flag, name = sender[0], sender[2:]
            status = None
            if r["direction"] == "tx":
                status = {1: "sent", 0: "failed"}.get(r["tx_ok"], "failed")
            out.append(Msg(r["ts"], r["direction"], r["kind"] or "plain",
                           channel or ("public" if r["kind"] == "plain" else "other"),
                           name, text, r["rssi"], r["snr"], len(r["payload"]),
                           status=status, airtime=r["airtime_ms"],
                           verified=flag == "v", group=flag == "g"))
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
           "READ_ONLY"]
