#!/usr/bin/env python3
"""
Manage your identity file (callsign + streams + groups + follows).

    python3 identity.py export --nick Tuan          -> Tuan.ltid  (backup / move)
    python3 identity.py import Tuan.ltid            -> keys-Tuan.lkey on this computer
    python3 identity.py passwd --nick Tuan          change the passphrase
    python3 identity.py protect --nick Tuan         encrypt an old keys-Tuan.json
    python3 identity.py show --nick Tuan            list what an identity contains

keys-<nick>.lkey and <nick>.ltid use the same encrypted format, protected by
your passphrase: a backup is a verified copy of the identity file. Without the
passphrase it is useless, so it can travel by USB, email or cloud.

MOVE, DON'T CLONE: stop using the identity on the old computer. Two computers
sending with the same identity at the same time can reuse encryption nonces.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from lora_text import ed25519
from lora_text.keyring import Keyring, KeyError_, fingerprint, make_handle, unb32
from lora_text.session import ask_new_passphrase, migrate
from lora_text.vault import WrongPassphrase, Vault, pack, unpack

__all__ = ["pack", "unpack"]          # kept for scripts that imported them from here


def summary(data: dict) -> list[str]:
    me = data.get("me") or "(none)"
    out = [f"nick: {me}"]
    if data.get("id_seed"):
        pub = ed25519.public_key(unb32(data["id_seed"]))
        out = [f"handle: {make_handle(me, pub)}   fingerprint: {fingerprint(pub)}"]
    out += [f"  own stream : {n}" for n in data.get("own", {})]
    out += [f"  following  : {n}" for n in data.get("follow", {})]
    out += [f"  group      : {n} (members: "
            f"{', '.join(m['name'] if isinstance(m, dict) else k for k, m in g['members'].items())})"
            for n, g in data.get("groups", {}).items()]
    out += [f"  contact    : {c['name']} ({c['handle']})" for c in data.get("contacts", {}).values()]
    return out


def find_identity(a) -> str:
    if a.keys:
        return a.keys
    if not a.nick:
        sys.exit("give --nick (or --keys FILE)")
    for ext in ("lkey", "json"):
        p = f"keys-{a.nick}.{ext}"
        if os.path.exists(p):
            return p
    sys.exit(f"no identity found for '{a.nick}' (keys-{a.nick}.lkey / .json)")


def ask(a, prompt: str) -> str:
    return a.passphrase if a.passphrase is not None else getpass.getpass(prompt)


def load(path: str, a) -> tuple[dict, bytes | None]:
    """Return (data, raw encrypted blob or None for plain json)."""
    with open(path, "rb") as f:
        raw = f.read()
    if path.endswith(".json"):
        return json.loads(raw.decode("utf-8")), None
    try:
        data, _ = Vault.open(raw, ask(a, f"Passphrase for {path}: "))
    except WrongPassphrase as e:
        sys.exit(str(e))
    return data, raw


def cmd_export(a) -> None:
    path = find_identity(a)
    data, raw = load(path, a)
    if not data.get("me"):
        sys.exit("identity has no callsign")
    out = a.out or f"{data['me']}.ltid"
    if raw is None:                            # plain json: protect the backup
        pw = a.passphrase if a.passphrase is not None else ask_new_passphrase()
        raw = pack(data, pw)
    with open(out, "wb") as f:
        f.write(raw)
    print(f"wrote {out} (same passphrase as the identity)")
    print("\n".join(summary(data)))


def cmd_import(a) -> None:
    with open(a.file, "rb") as f:
        blob = f.read()
    pw = ask(a, "Passphrase of the backup: ")
    try:
        data = unpack(blob, pw)
    except (WrongPassphrase, ValueError) as e:
        sys.exit(str(e))
    me = data.get("me")
    out = a.keys or f"keys-{me}.{'json' if a.no_password else 'lkey'}"
    for existing in (out, f"keys-{me}.lkey", f"keys-{me}.json"):
        if os.path.exists(existing) and not a.force:
            sys.exit(f"{existing} already exists (use --force to replace it)")
    if out.endswith(".json"):
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    else:
        with open(out, "wb") as f:
            f.write(blob)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    print(f"restored identity to {out}")
    print("\n".join(summary(data)))
    print("\nrun:  python3 chat.py   (pick this identity in the login panel)")
    print("Stop using this identity on the old computer (move, don't clone).")


def cmd_passwd(a) -> None:
    path = find_identity(a)
    if not path.endswith(".lkey"):
        sys.exit(f"{path} has no passphrase yet - use: identity.py protect --nick {a.nick}")
    try:
        k = Keyring(path, lock=True, passphrase=ask(a, "Current passphrase: "))
    except WrongPassphrase as e:
        sys.exit(str(e))
    except KeyError_ as e:
        sys.exit(f"{e} (close the app first)")
    new = a.new_passphrase if a.new_passphrase is not None else ask_new_passphrase()
    k.change_passphrase(new)
    k.close()
    print(f"passphrase changed for {path}. Old backups keep their old passphrase.")


def cmd_protect(a) -> None:
    src = a.keys or f"keys-{a.nick}.json"
    if not os.path.exists(src):
        sys.exit(f"{src} not found")
    dst = src[:-5] + ".lkey"
    if os.path.exists(dst):
        sys.exit(f"{dst} already exists")
    pw = a.passphrase if a.passphrase is not None else ask_new_passphrase()
    migrate(src, dst, pw)
    print(f"encrypted {src} -> {dst} (plain file removed)")


def cmd_show(a) -> None:
    data, _ = load(find_identity(a), a)
    print("\n".join(summary(data)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("export", "passwd", "protect", "show"):
        p = sub.add_parser(name)
        p.add_argument("--nick")
        p.add_argument("--keys")
        p.add_argument("--passphrase", help=argparse.SUPPRESS)      # scripts / tests
        if name == "export":
            p.add_argument("--out")
        if name == "passwd":
            p.add_argument("--new-passphrase", help=argparse.SUPPRESS)
    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--keys")
    i.add_argument("--force", action="store_true")
    i.add_argument("--no-password", action="store_true",
                   help="restore as a plain keys-<nick>.json (lab mode)")
    i.add_argument("--passphrase", help=argparse.SUPPRESS)
    a = ap.parse_args()
    {"export": cmd_export, "import": cmd_import, "passwd": cmd_passwd,
     "protect": cmd_protect, "show": cmd_show}[a.cmd](a)


if __name__ == "__main__":
    main()
