"""
Start-up helpers shared by chat.py and identity.py: file names for an
identity, the passphrase prompt, and migration of old plain-text keyrings.
"""

from __future__ import annotations

import getpass
import json
import os
import re
from typing import Callable, Optional

from .vault import MIN_PASSPHRASE, WrongPassphrase, Vault, pack, unpack


class LoginError(ValueError):
    pass


_IDENT = re.compile(r"^keys(?:-(.+))?\.(lkey|json)$")


def list_identities(folder: str = ".") -> list[dict]:
    """Identity files in a folder: [{"nick", "path", "protected"}], protected first."""
    found: dict[str, dict] = {}
    for name in sorted(os.listdir(folder)):
        m = _IDENT.match(name)
        if not m:
            continue
        nick, ext = m.group(1), m.group(2)
        path = os.path.join(folder, name) if folder != "." else name
        if ext == "json":
            try:
                with open(path, encoding="utf-8") as f:
                    nick = json.load(f).get("me") or nick
            except (OSError, ValueError):
                continue
        key = (nick or "").lower()
        if key in found and found[key]["protected"]:
            continue                                   # .lkey wins over an old .json
        found[key] = {"nick": nick, "path": path, "protected": ext == "lkey"}
    return sorted(found.values(), key=lambda d: (not d["protected"], (d["nick"] or "").lower()))


def new_identity_path(nick: str, lab: bool = False, folder: str = ".") -> str:
    from .keyring import KeyError_, check_nick
    try:
        check_nick(nick)
    except KeyError_ as e:
        raise LoginError(str(e)) from e
    for ext in ("lkey", "json"):
        if os.path.exists(os.path.join(folder, f"keys-{nick}.{ext}")):
            raise LoginError(f"an identity '{nick}' already exists - pick it from the list")
    name = f"keys-{nick}.{'json' if lab else 'lkey'}"
    return name if folder == "." else os.path.join(folder, name)


def login(path: str, passphrase: str = "", confirm: Optional[str] = None,
          lab: bool = False) -> tuple[str, Optional[str]]:
    """Check what the login panel collected. Returns (identity path, passphrase or None).
    - existing .lkey: verify the passphrase
    - new .lkey: passphrase (min length) typed twice
    - existing .json (old, unprotected): empty passphrase = open as is (lab);
      a passphrase typed twice = encrypt it now
    - new .json: lab mode, no passphrase"""
    if path.endswith(".lkey"):
        if os.path.exists(path):
            with open(path, "rb") as f:
                blob = f.read()
            try:
                Vault.open(blob, passphrase)
            except WrongPassphrase as e:
                raise LoginError("wrong passphrase") from e
            return path, passphrase
        if len(passphrase) < MIN_PASSPHRASE:
            raise LoginError(f"choose a passphrase of at least {MIN_PASSPHRASE} characters")
        if passphrase != confirm:
            raise LoginError("the two passphrases differ")
        return path, passphrase
    if path.endswith(".json"):
        if lab or not passphrase or not os.path.exists(path):
            return path, None
        if len(passphrase) < MIN_PASSPHRASE:
            raise LoginError(f"choose a passphrase of at least {MIN_PASSPHRASE} characters")
        if passphrase != confirm:
            raise LoginError("repeat the passphrase to protect this identity")
        lkey = path[:-5] + ".lkey"
        migrate(path, lkey, passphrase)
        return lkey, passphrase
    raise LoginError(f"not an identity file: {path}")


def resolve(nick: Optional[str], keys: Optional[str], db: Optional[str],
            protected: bool = True) -> tuple[Optional[str], str, str]:
    """Return (nick, keys_path, db_path).
    Default files: keys-<nick>.lkey (protected) or keys-<nick>.json (--no-password)."""
    if not nick and keys and keys.endswith(".json") and os.path.exists(keys):
        with open(keys, encoding="utf-8") as f:
            nick = json.load(f).get("me")
    suffix = f"-{nick}" if nick else ""
    keys_path = keys or f"keys{suffix}.{'lkey' if protected else 'json'}"
    return nick, keys_path, db or f"log{suffix}.db"


def ask_new_passphrase(ask: Callable[[str], str] = getpass.getpass) -> str:
    while True:
        pw = ask(f"Choose a passphrase (min {MIN_PASSPHRASE} characters): ")
        if len(pw) < MIN_PASSPHRASE:
            print(f"  too short - at least {MIN_PASSPHRASE} characters")
            continue
        if pw != ask("Repeat the passphrase: "):
            print("  the two entries differ - try again")
            continue
        return pw


def migrate(json_path: str, lkey_path: str, passphrase: str) -> None:
    """Encrypt an old plain-text keyring and remove the plain copy."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    blob = pack(data, passphrase)
    unpack(blob, passphrase)                       # verify before deleting anything
    tmp = lkey_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, lkey_path)
    try:
        os.chmod(lkey_path, 0o600)
    except OSError:
        pass
    size = os.path.getsize(json_path)
    with open(json_path, "r+b") as f:              # overwrite before deleting
        f.write(b"\0" * size)
    os.remove(json_path)


def unlock_or_create(keys_path: str, ask: Callable[[str], str] = getpass.getpass,
                     tries: int = 3) -> Optional[str]:
    """Interactive passphrase step before the app starts.
    Returns the passphrase (None for a plain .json identity)."""
    if not keys_path.endswith(".lkey"):
        return None
    if os.path.exists(keys_path):
        with open(keys_path, "rb") as f:
            blob = f.read()
        for _ in range(tries):
            pw = ask(f"Passphrase for {os.path.basename(keys_path)}: ")
            try:
                Vault.open(blob, pw)
                return pw
            except WrongPassphrase:
                print("  wrong passphrase")
        raise SystemExit("too many wrong attempts")
    json_path = keys_path[:-5] + ".json"
    if os.path.exists(json_path):
        print(f"Found an unprotected identity: {json_path}")
        print("It will be encrypted with a passphrase and the plain file removed.")
        pw = ask_new_passphrase(ask)
        migrate(json_path, keys_path, pw)
        print(f"Encrypted -> {keys_path}")
        return pw
    print(f"New identity: {keys_path}")
    print("If you forget the passphrase, this identity cannot be recovered.")
    return ask_new_passphrase(ask)
