"""
Local keyring: own streams, followed streams and groups, persisted to JSON.

Identity: an Ed25519 key pair (seed kept in the identity file). Your handle is
<nick>#<TAG>, TAG = first 4 characters of the key fingerprint. Nicks are not
unique; keys are. A ✓ in the apps means "message bound to a key we hold".

Strings exchanged out of band (chat, QR, paper):
    identity: lid1:<handle>:<base32 public key>
    stream:   lts3:<handle>:<pub>:<label>:<secret>:<Ed25519 signature by the owner>
    group:    ltg2:<label>:<base32 secret>:<handle>=<pub>,<handle>=<pub>,...
    (lts2:/lts1:/ltg1: strings are still accepted; their owner is unverified)

A ✓ means "proved by a signature from a key you hold" (signed stream strings).
Group messages are never shown with a personal ✓: every member holds the
group secret and can post as any other member.

keys-<nick>.lkey is encrypted with your passphrase; keys-<nick>.json (lab mode)
holds secrets in plain text - keep it private.
"""

from __future__ import annotations

import base64
import json
import os
import secrets as _rand
from dataclasses import dataclass
from typing import Optional

import hashlib
import re
import time

from . import crypto as C
from . import ed25519
from .protocol import (PRIVATE_VERSION, PROFILE, MsgType, PayloadTooLong, make_mode, normalize,
                       parse_mode)
from .numbers import format_number, number_of
from .vault import LogCipher, Vault, WrongPassphrase  # noqa: F401  (re-exported)


class KeyError_(ValueError):
    pass


def b32(b: bytes) -> str:
    return base64.b32encode(b).decode().rstrip("=").lower()


def unb32(s: str) -> bytes:
    s = s.strip().upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


NICK_RE = re.compile(r"^[^\s:#,=]{1,16}$")


def check_nick(nick: Optional[str]) -> None:
    if nick is not None and not NICK_RE.match(nick):
        raise KeyError_("nick must be 1-16 characters, no spaces or : # , =")


def fingerprint(pub: bytes) -> str:
    """60-bit fingerprint for comparing keys in person, e.g. K7QX-M2PA-9TRE."""
    f = b32(hashlib.sha256(pub).digest()).upper()[:12]
    return f"{f[:4]}-{f[4:8]}-{f[8:]}"


def tag_of(pub: bytes) -> str:
    return fingerprint(pub)[:4]


def make_handle(nick: str, pub: bytes) -> str:
    return f"{nick}#{tag_of(pub)}"


def parse_lid(s: str) -> tuple[str, bytes]:
    parts = s.strip().split(":")
    if len(parts) != 3 or parts[0] != "lid1":
        raise KeyError_("not an identity string (lid1:handle:key)")
    handle, pub = parts[1], unb32(parts[2])
    if len(pub) != 32 or handle.rpartition("#")[2].upper() != tag_of(pub):
        raise KeyError_("identity string is damaged (tag does not match key)")
    return handle, pub


@dataclass
class Opened:
    kind: str          # 'signed' | 'sealed' | 'join'
    source: str        # stream label, or group label for group messages
    sender: str        # display handle of the sender, e.g. 'Lan#P2MD'
    text: str
    counter: int
    pub: Optional[bytes] = None      # sender's public key, if known
    verified: bool = False           # owner proved by signature (followed lts3 stream)
    group: bool = False              # message from a group (any member could send it)
    inner: Optional[str] = None      # direct messages: 'text' | 'resend' | 'receipt'
    acks: tuple = ()                 # receipt: ids of the confirmed messages


# Direct (one-to-one) messages. Inside the sealed frame the plaintext starts with:
DM_TEXT, DM_RESEND, DM_RECEIPT = 1, 2, 3
DM_INNER = {DM_TEXT: "text", DM_RESEND: "resend", DM_RECEIPT: "receipt"}
DM_MAX_ACKS = 4


def dm_id(counter: int) -> int:
    """Id of a direct message, as confirmed by receipts: low 32 bits of its counter."""
    return counter & 0xFFFFFFFF


SAVE_EVERY = 30.0                    # s between saves of receive counters


class Locked(KeyError_):
    pass


class Keyring:
    """Identity keyring. A path ending in '.lkey' is passphrase-protected
    (see vault.py); '.json' is plain text (lab / --no-password mode)."""

    def __init__(self, path: str = "keys.json", me: Optional[str] = None,
                 lock: bool = False, passphrase: Optional[str] = None):
        self.path, self.me = path, me
        self.encrypted = path.endswith(".lkey")
        self.vault: Optional[Vault] = None
        self.locked = False
        self._lockf = None
        self._cache: dict[bytes, C.StreamKey] = {}
        self._seen_ctrl: set[bytes] = set()
        self._pair: dict[str, Optional[tuple[bytes, bytes]]] = {}   # contact key -> (send, receive) secrets
        self._dirty, self._last_save = False, 0.0
        if lock:
            self._acquire_lock()
        self.floor = C.startup_floor()
        try:
            self._load(passphrase)
        except Exception:
            self.close()
            raise

    # -- persistence -------------------------------------------------------

    @staticmethod
    def _empty(me: Optional[str]) -> dict:
        return {"me": me, "own": {}, "follow": {}, "groups": {}, "contacts": {}}

    def _load(self, passphrase: Optional[str]) -> None:
        data, dirty = self._empty(self.me), False
        if os.path.exists(self.path):
            with open(self.path, "rb") as f:
                raw = f.read()
            if self.encrypted:
                if passphrase is None:
                    raise Locked("passphrase required")
                loaded, self.vault = Vault.open(raw, passphrase)
            else:
                loaded = json.loads(raw.decode("utf-8"))
            for k in ("own", "follow", "groups", "contacts"):
                data[k].update(loaded.get(k, {}))
            data["id_seed"] = loaded.get("id_seed")
            stored = loaded.get("me")
            if stored and self.me and stored.lower() != self.me.lower():
                raise KeyError_(f"{self.path} belongs to callsign '{stored}', not '{self.me}'")
            data["me"] = stored or self.me
            data["log_secret"] = loaded.get("log_secret")
            for k, v in loaded.items():                  # keys written by newer apps
                data.setdefault(k, v)
        else:
            if self.encrypted:
                if not passphrase:
                    raise KeyError_("a passphrase is needed to create a protected identity")
                self.vault = Vault.create(passphrase)
            dirty = True
        if not data.get("log_secret"):
            data["log_secret"] = b32(_rand.token_bytes(32))
            dirty = True
        if not data.get("id_seed"):                      # identity key pair
            data["id_seed"] = b32(_rand.token_bytes(32))
            dirty = True
        for g in data["groups"].values():                # upgrade old member format
            for m, v in list(g["members"].items()):
                if isinstance(v, int):
                    g["members"][m] = {"i": v, "pub": None, "name": m}
        check_nick(data["me"])
        self.data, self.me, self.locked = data, data["me"], False
        self.pub = ed25519.public_key(unb32(data["id_seed"]))
        self._findable = data.get("findable", True) is not False    # kept while locked
        if dirty:
            self.save()

    def _acquire_lock(self) -> None:
        """Refuse to open a keyring another running instance is using."""
        self._lockf = open(self.path + ".lock", "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self._lockf.seek(0)
                msvcrt.locking(self._lockf.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockf.close()
            self._lockf = None
            raise KeyError_(f"{self.path} is already used by another running instance "
                            "- give each instance its own --nick (or --keys file)")

    def close(self) -> None:
        if getattr(self, "_dirty", False) and not self.locked and hasattr(self, "data"):
            try:
                self.save()
            except Exception:
                pass
        if self._lockf:
            self._lockf.close()
            self._lockf = None

    def touch(self) -> None:
        """Receive counters changed: save at most every SAVE_EVERY seconds
        (replay protection only; send counters are saved immediately)."""
        self._dirty = True
        if time.monotonic() - self._last_save >= SAVE_EVERY:
            self.save()

    def save(self) -> None:
        if self.locked:
            raise Locked("identity is locked")
        tmp = self.path + ".tmp"
        if self.encrypted:
            with open(tmp, "wb") as f:
                f.write(self.vault.seal(self.data))
        else:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._dirty, self._last_save = False, time.monotonic()

    # -- lock / unlock -----------------------------------------------------

    def wipe(self) -> None:
        """Save, then drop all secrets from memory (the 'log out')."""
        if self.locked:
            return
        self.save()
        self.data = self._empty(self.me)
        self.vault = None
        self._cache.clear()
        self.locked = True
        self._pair.clear()

    def unlock(self, passphrase: str) -> None:
        self._load(passphrase)

    def change_passphrase(self, new: str) -> None:
        if not self.encrypted:
            raise KeyError_("this identity is not passphrase-protected")
        self._require_unlocked()
        self.vault = Vault.create(new)
        self.save()

    def log_cipher(self) -> Optional[LogCipher]:
        if self.locked:
            return None
        return LogCipher(unb32(self.data["log_secret"]))

    def _require_unlocked(self) -> None:
        if self.locked:
            raise Locked("identity is locked - unlock first")

    def _key(self, secret: bytes) -> C.StreamKey:
        k = self._cache.get(secret)
        if k is None:
            k = self._cache[secret] = C.StreamKey(secret)
        return k

    # -- identity --------------------------------------------------------------

    @property
    def handle(self) -> Optional[str]:
        return make_handle(self.me, self.pub) if self.me else None

    def lid(self) -> str:
        if not self.me:
            raise KeyError_("this identity has no nick")
        return f"lid1:{self.handle}:{b32(self.pub)}"

    def fingerprint(self) -> str:
        return fingerprint(self.pub)

    @property
    def number(self) -> str:
        """Your 10-digit TextCast number (computed from your key, cached)."""
        n = getattr(self, "_number", None)
        if n is None:
            n = self.data.get("number")
            if not n or self.data.get("number_pub") != b32(self.pub):
                n = number_of(self.pub)
                if not self.locked:
                    self.data["number"], self.data["number_pub"] = n, b32(self.pub)
                    self._dirty = True
            self._number = n
        return n

    @property
    def findable(self) -> bool:
        """Answer 'who has my number?' lookups (default on)."""
        return self._findable

    def set_findable(self, on: bool) -> None:
        self._require_unlocked()
        self.data["findable"] = self._findable = bool(on)
        self.save()

    def set_me(self, me: Optional[str]) -> None:
        check_nick(me)
        self.me = self.data["me"] = me
        self.save()

    def _my_key(self) -> str:
        if not self.me:
            raise KeyError_("set a nick (--nick) to post in groups")
        return self.handle.lower()

    # -- contacts (your own names for keys you have verified) ------------------

    def add_contact(self, lid: str, name: Optional[str] = None, verified: bool = True) -> str:
        """Save a contact from an identity string (pasted or scanned: the full key,
        so it counts as verified)."""
        self._require_unlocked()
        handle, pub = parse_lid(lid)
        name = (name or handle.partition("#")[0]).strip()
        old = self.data["contacts"].get(b32(pub), {})
        self.data["contacts"][b32(pub)] = {
            "name": name, "handle": handle, "dm_out": old.get("dm_out", 0),
            "dm_in": old.get("dm_in", 0),
            "verified": bool(verified or old.get("verified", False)),
            "number": old.get("number") or number_of(pub)}
        self._pair.pop(b32(pub), None)
        self.save()
        return name

    def add_found(self, pub: bytes, nick: str, number: str, name: Optional[str] = None) -> str:
        """Save a contact found by number. Raises KeyError_ if the key does not
        have that number, or another saved contact already has it (possible fake)."""
        check_nick(nick)
        if number_of(pub) != number:
            raise KeyError_("the answer does not match that number")
        for k, c in self.data["contacts"].items():
            if k != b32(pub) and self._contact_number(k, c) == number:
                raise KeyError_(f"{c['name']} already has number {format_number(number)} "
                                "with a different key - check in person")
        return self.add_contact(f"lid1:{make_handle(nick, pub)}:{b32(pub)}", name, verified=False)

    def _contact_number(self, key: str, c: dict) -> str:
        if not c.get("number"):
            c["number"] = number_of(unb32(key))
            self._dirty = True
        return c["number"]

    def contact_by_number(self, number: str) -> Optional[dict]:
        for k, c in self.data["contacts"].items():
            if self._contact_number(k, c) == number:
                return c
        return None

    def contact_info(self) -> list[dict]:
        """Contacts for the UI: name, handle, number, verified (by key string / QR)."""
        me = b32(self.pub)
        out = [{"name": c["name"], "handle": c["handle"], "number": self._contact_number(k, c),
                "verified": c.get("verified", True)}
               for k, c in self.data["contacts"].items() if k != me]
        return sorted(out, key=lambda x: x["name"].lower())

    def rename_contact(self, handle: str, name: str) -> None:
        self._require_unlocked()
        name = name.strip()
        if not name:
            raise KeyError_("a name is needed")
        self._contact(handle)[1]["name"] = name
        self.save()

    def remove_contact(self, name: str) -> bool:
        self._require_unlocked()
        for k, c in list(self.data["contacts"].items()):
            if c["name"] == name or c["handle"].lower() == name.lower():
                del self.data["contacts"][k]
                self._pair.pop(k, None)
                self.save()
                return True
        return False

    def contacts(self) -> list[str]:
        return [f"{c['name']:<16} {format_number(self._contact_number(k, c)):<13} "
                f"{'✓ verified' if c.get('verified', True) else 'added by number'}"
                for k, c in self.data["contacts"].items()] or ["(no contacts)"]

    def display(self, handle: str, pub: Optional[bytes]) -> str:
        """Contact name if you saved this key, else the handle."""
        if pub is not None:
            c = self.data["contacts"].get(b32(pub))
            if c:
                return c["name"]
        return handle

    # -- direct (one-to-one) messages -------------------------------------------
    #
    # Two people who hold each other's identity string compute the same pair
    # secret, never sent: S = a_me * A_peer on the Ed25519 curve,
    #   pair = HMAC-SHA256(S, "lora-text/pair/" + handle1 + "|" + handle2)[:16]
    # (handles lower-case, sorted). Like a two-member group, each direction has
    # its own key: member_secret(pair, sender handle). Frames are ordinary
    # SEALED frames; the plaintext starts with DM_TEXT / DM_RESEND / DM_RECEIPT.

    def direct_contacts(self) -> list[tuple[str, str]]:
        """(handle, name) of the contacts you can message, sorted by name."""
        me = b32(self.pub)
        out = [(c["handle"], c["name"]) for k, c in self.data["contacts"].items() if k != me]
        return sorted(out, key=lambda x: x[1].lower())

    def _contact(self, handle: str) -> tuple[str, dict]:
        for k, c in self.data["contacts"].items():
            if c["handle"].lower() == handle.lower():
                return k, c
        raise KeyError_(f"'{handle}' is not one of your contacts")

    def _dm_keys(self, key: str, c: dict) -> Optional[tuple[bytes, bytes]]:
        if key not in self._pair:
            secrets = None
            if self.me and key != b32(self.pub):
                s = ed25519.shared_point(unb32(self.data["id_seed"]), unb32(key))
                if s is not None:
                    me, peer = self.handle.lower(), c["handle"].lower()
                    h1, h2 = sorted((me, peer))
                    pair = C.derive(s, f"pair/{h1}|{h2}")[:16]
                    secrets = (C.member_secret(pair, me), C.member_secret(pair, peer))
            self._pair[key] = secrets
        return self._pair[key]

    def _seal_dm(self, handle: str, plaintext: bytes) -> tuple[bytes, int]:
        self._require_unlocked()
        key, c = self._contact(handle)
        keys = self._dm_keys(key, c)
        if keys is None:
            raise KeyError_(f"cannot message {c['name']}: their identity key is not usable")
        i = C.next_counter(c.get("dm_out", 0), self.floor)
        mode = make_mode(MsgType.SEALED, version=PRIVATE_VERSION)
        frame = C.seal(self._key(keys[0]), mode, i, plaintext, encrypt=True)
        c["dm_out"] = i
        self.save()
        return frame, i

    def seal_direct(self, handle: str, text: str, resend: bool = False) -> tuple[bytes, int]:
        """Frame for a direct message to a contact, and its counter (see dm_id)."""
        body = normalize(text.strip()).encode("utf-8")
        limit = PROFILE.max_payload - C.FRAME_OVERHEAD - 1
        if len(body) > limit:
            raise PayloadTooLong(f"{len(body)} B of text > {limit} B limit for a direct message")
        return self._seal_dm(handle, bytes([DM_RESEND if resend else DM_TEXT]) + body)

    def make_receipt(self, handle: str, ids: list[int]) -> bytes:
        """Receipt confirming up to DM_MAX_ACKS direct messages from this contact."""
        ids = list(ids)[:DM_MAX_ACKS]
        body = bytes([DM_RECEIPT]) + b"".join(dm_id(i).to_bytes(4, "big") for i in ids)
        return self._seal_dm(handle, body)[0]

    def _open_direct(self, frame: bytes, t: Optional[float]) -> Optional[Opened]:
        for key, c in list(self.data["contacts"].items()):
            keys = self._dm_keys(key, c)
            if keys is None:
                continue
            k = self._key(keys[1])
            for i in C.matching_counters(k, frame, c.get("dm_in", 0), t):
                pt = C.unseal(k, frame, i, True)
                if not pt or pt[0] not in DM_INNER:
                    continue
                inner, text, acks = DM_INNER[pt[0]], "", ()
                if pt[0] == DM_RECEIPT:
                    n = len(pt) - 1
                    if n == 0 or n % 4 or n > 4 * DM_MAX_ACKS:
                        continue
                    acks = tuple(int.from_bytes(pt[1 + 4 * j:5 + 4 * j], "big") for j in range(n // 4))
                else:
                    try:
                        text = pt[1:].decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                c["dm_in"] = i
                self.touch()
                return Opened("direct", "@" + c["handle"], c["name"], text, i, unb32(key),
                              True, False, inner, acks)
        return None

    # -- management ----------------------------------------------------------

    def _check_free(self, label: str) -> None:
        self._require_unlocked()
        if any(label in self.data[s] for s in ("own", "follow", "groups")):
            raise KeyError_(f"label '{label}' already exists")
        if not label or any(ch in label for ch in ":# ,=") or label in ("public", "all", "other"):
            raise KeyError_("label must be one word without : # , = and not public/all/other")

    def new_own(self, name: str) -> str:
        self._check_free(name)
        if not self.me:
            raise KeyError_("set a nick (--nick) before creating a stream")
        self.data["own"][name] = {"secret": b32(_rand.token_bytes(16)), "last_i": 0}
        self.save()
        return self.share(name)

    @staticmethod
    def _stream_msg(handle: str, pub_b32: str, label: str, secret: str) -> bytes:
        return f"lts3:{handle}:{pub_b32}:{label}:{secret}".encode("utf-8")

    def follow(self, share: str, label: Optional[str] = None) -> str:
        parts = share.strip().split(":")
        if parts[0] in ("lts3", "lts2") and len(parts) == (6 if parts[0] == "lts3" else 5):
            handle, pub_b32, slabel, secret = parts[1], parts[2], parts[3], parts[4]
            pub = unb32(pub_b32)
            if len(pub) != 32 or handle.rpartition("#")[2].upper() != tag_of(pub):
                raise KeyError_("stream string is damaged (tag does not match key)")
            entry = {"owner": handle, "pub": pub_b32, "verified": False}
            if parts[0] == "lts3":
                sig = unb32(parts[5])
                if not ed25519.verify(pub, self._stream_msg(handle, pub_b32, slabel, secret), sig):
                    raise KeyError_("stream string signature is invalid - it was not made "
                                    f"by {handle}'s key (or was altered)")
                entry.update(verified=True, sig=parts[5])
        elif len(parts) == 3 and parts[0] == "lts1":
            slabel, secret = parts[1], parts[2]
            entry = {"owner": parts[1], "pub": None, "verified": False}
        else:
            raise KeyError_("not a stream string (lts3:…)")
        label = label or slabel
        self._check_free(label)
        unb32(secret)
        entry.update({"secret": secret, "last_i": 0, "label": slabel})
        self.data["follow"][label] = entry
        self.save()
        return label

    def new_group(self, label: str) -> str:
        self._check_free(label)
        me = self._my_key()
        self.data["groups"][label] = {
            "secret": b32(_rand.token_bytes(16)),
            "members": {me: {"i": 0, "pub": b32(self.pub), "name": self.handle}}}
        self.save()
        return self.share(label)

    def join_group(self, share: str, label: Optional[str] = None) -> str:
        parts = share.strip().split(":")
        if len(parts) != 4 or parts[0] not in ("ltg1", "ltg2"):
            raise KeyError_("not a group string (ltg2:… or ltg1:…)")
        label = label or parts[1]
        self._check_free(label)
        unb32(parts[2])
        members = {}
        for item in filter(None, (x.strip() for x in parts[3].split(","))):
            name, _, pub = item.partition("=")
            members[name.lower()] = {"i": 0, "pub": pub or None, "name": name}
        if self.me:
            members.setdefault(self._my_key(),
                               {"i": 0, "pub": b32(self.pub), "name": self.handle})
        self.data["groups"][label] = {"secret": parts[2], "members": members}
        self.save()
        return label

    def add_member(self, group: str, name: str) -> None:
        self._require_unlocked()
        g = self.data["groups"].get(group)
        if g is None:
            raise KeyError_(f"no group '{group}'")
        name = name.strip()
        g["members"].setdefault(name.lower(), {"i": 0, "pub": None, "name": name})
        self.save()

    def remove(self, label: str) -> bool:
        self._require_unlocked()
        for s in ("own", "follow", "groups"):
            if self.data[s].pop(label, None) is not None:
                self.save()
                return True
        return False

    def share(self, label: str) -> str:
        self._require_unlocked()
        if label in self.data["own"]:
            pub_b32, secret = b32(self.pub), self.data["own"][label]["secret"]
            sig = ed25519.sign(unb32(self.data["id_seed"]),
                               self._stream_msg(self.handle, pub_b32, label, secret))
            return f"lts3:{self.handle}:{pub_b32}:{label}:{secret}:{b32(sig)}"
        if label in self.data["groups"]:
            g = self.data["groups"][label]
            members = ",".join(f"{m['name']}={m['pub'] or ''}"
                               for _, m in sorted(g["members"].items()))
            return f"ltg2:{label}:{g['secret']}:{members}"
        if label in self.data["follow"]:
            f = self.data["follow"][label]
            slabel = f.get("label", label)
            if f.get("sig"):
                return f"lts3:{f['owner']}:{f['pub']}:{slabel}:{f['secret']}:{f['sig']}"
            if f.get("pub"):
                return f"lts2:{f['owner']}:{f['pub']}:{slabel}:{f['secret']}"
            return f"lts1:{slabel}:{f['secret']}"
        raise KeyError_(f"no key '{label}'")

    def listing(self) -> list[str]:
        out = []
        for n in self.data["own"]:
            out.append(f"own     {n}   (you post; followers read)")
        for n, f in self.data["follow"].items():
            owner = f.get("owner") or n
            out.append(f"follow  {n}   by {owner}"
                       f"{' ✓' if f.get('verified') else ' (owner not verified)'}")
        for n, g in self.data["groups"].items():
            names = ", ".join(m["name"] for m in g["members"].values())
            out.append(f"group   {n}   members: {names}")
        return out or ["(no keys)"]

    def is_target(self, label: str) -> bool:
        return label in self.data["own"] or label in self.data["groups"]

    # -- sending ---------------------------------------------------------------

    def _sender_state(self, target: str) -> tuple[bytes, dict, str]:
        """Return (stream secret, dict holding the counter, key in that dict)."""
        if target in self.data["own"]:
            ent = self.data["own"][target]
            return unb32(ent["secret"]), ent, "last_i"
        if target in self.data["groups"]:
            g = self.data["groups"][target]
            me = self._my_key()
            ent = g["members"].setdefault(me, {"i": 0, "pub": b32(self.pub), "name": self.handle})
            return C.member_secret(unb32(g["secret"]), me), ent, "i"
        raise KeyError_(f"'{target}' is not one of your streams or groups")

    def seal_for(self, target: str, text: str, signed: bool = False) -> bytes:
        self._require_unlocked()
        body = normalize(text.strip()).encode("utf-8")
        limit = PROFILE.max_payload - C.FRAME_OVERHEAD
        if len(body) > limit:
            raise PayloadTooLong(f"{len(body)} B of text > {limit} B limit for {target}")
        secret, holder, field = self._sender_state(target)
        i = C.next_counter(holder[field], self.floor)
        mode = make_mode(MsgType.SIGNED if signed else MsgType.SEALED, version=PRIVATE_VERSION)
        frame = C.seal(self._key(secret), mode, i, body, encrypt=not signed)
        holder[field] = i
        self.save()
        return frame

    # -- group join announcements ------------------------------------------------
    #
    # A SEALED frame under the group's control key, so members learn a new
    # member's handle and public key automatically:
    #   [mode][nonce 8][ChaCha20(ctrl_enc, nonce‖0000, "J" ‖ minute(4) ‖ pub(32) ‖ handle)][tag 4]
    #   tag = HMAC-SHA256(ctrl_mac, mode ‖ nonce ‖ ct)[:4]
    # Accepted if fresh (±10 min), nonce unseen, and handle tag matches the key.

    def _ctrl_keys(self, group: str) -> tuple[bytes, bytes]:
        gs = unb32(self.data["groups"][group]["secret"])
        return C.derive(gs, "control/enc"), C.derive(gs, "control/mac")

    def make_join(self, group: str) -> bytes:
        self._require_unlocked()
        self._my_key()
        enc, mac = self._ctrl_keys(group)
        nonce = _rand.token_bytes(8)
        pt = (b"J" + C.minute_now().to_bytes(4, "big") + self.pub
              + self.handle.encode("utf-8"))
        ct = C.chacha20_xor(enc[:32], nonce + b"\x00" * 4, pt)
        mode = make_mode(MsgType.SEALED, version=PRIVATE_VERSION)
        body = nonce + ct
        tag = C.hmac.new(mac, bytes([mode]) + body, C.hashlib.sha256).digest()[:C.TAG_LEN]
        frame = bytes([mode]) + body + tag
        if len(frame) > PROFILE.max_payload:
            raise KeyError_(f"the join announcement ({len(frame)} B) does not fit this radio "
                            f"profile ({PROFILE.max_payload} B max) - members must add you "
                            f"by hand: /member {self.handle}")
        return frame

    def _open_ctrl(self, frame: bytes) -> Optional[Opened]:
        if len(frame) < 1 + 8 + 5 + 32 + 3 + C.TAG_LEN:
            return None
        body, tag = frame[1:-C.TAG_LEN], frame[-C.TAG_LEN:]
        for label in self.data["groups"]:
            enc, mac = self._ctrl_keys(label)
            want = C.hmac.new(mac, frame[:1] + body, C.hashlib.sha256).digest()[:C.TAG_LEN]
            if not C.hmac.compare_digest(want, tag):
                continue
            nonce = body[:8]
            if nonce in self._seen_ctrl:
                return None
            pt = C.chacha20_xor(enc[:32], nonce + b"\x00" * 4, body[8:])
            if pt[:1] != b"J" or abs(int.from_bytes(pt[1:5], "big") - C.minute_now()) > 10:
                return None
            pub, handle = pt[5:37], pt[37:].decode("utf-8", "replace")
            if handle.rpartition("#")[2].upper() != tag_of(pub):
                return None
            self._seen_ctrl.add(nonce)
            members = self.data["groups"][label]["members"]
            key = handle.lower()
            old = members.get(key)
            if old and old.get("pub") and old["pub"] != b32(pub):
                return Opened("join", label, handle,
                              f"rejected: {handle} announced a different key", 0, None,
                              False, True)
            new = old is None or not old.get("pub")
            members[key] = {"i": old["i"] if old else 0, "pub": b32(pub), "name": handle}
            self.save()
            return Opened("join", label, handle,
                          f"{handle} joined" if new else f"{handle} is here", 0, pub,
                          False, True)
        return None

    # -- receiving ---------------------------------------------------------------

    def _receive_streams(self):
        for label, ent in self.data["follow"].items():
            pub = unb32(ent["pub"]) if ent.get("pub") else None
            yield (label, ent.get("owner") or label, pub, unb32(ent["secret"]), ent, "last_i",
                   False, bool(ent.get("verified")))
        mine = self.handle.lower() if self.me else None
        for label, g in self.data["groups"].items():
            gs = unb32(g["secret"])
            for key, m in g["members"].items():
                if key == mine:
                    continue
                pub = unb32(m["pub"]) if m.get("pub") else None
                yield label, m["name"], pub, C.member_secret(gs, key), m, "i", True, False

    def open(self, frame: bytes, t: Optional[float] = None) -> Optional[Opened]:
        if self.locked or not frame:
            return None
        version, mtype, _ = parse_mode(frame[0])
        if (version != PRIVATE_VERSION or mtype not in (MsgType.SIGNED, MsgType.SEALED)
                or len(frame) < C.FRAME_OVERHEAD):
            return None
        encrypted = mtype == MsgType.SEALED
        if encrypted:
            ctrl = self._open_ctrl(frame)
            if ctrl:
                return ctrl
            direct = self._open_direct(frame, t)
            if direct:
                return direct
        for source, sender, pub, secret, holder, field, group, verified in self._receive_streams():
            k = self._key(secret)
            for i in C.matching_counters(k, frame, holder[field], t):
                try:     # a false tag match almost never decodes as text
                    text = C.unseal(k, frame, i, encrypted).decode("utf-8")
                except UnicodeDecodeError:
                    continue
                holder[field] = i
                self.touch()
                return Opened("sealed" if encrypted else "signed", source, sender, text, i,
                              pub, verified, group)
        return None


def max_text(private: bool) -> int:
    return PROFILE.max_payload - (C.FRAME_OVERHEAD if private else 1)
