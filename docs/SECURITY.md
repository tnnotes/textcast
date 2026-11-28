# Security

> The code was written with AI assistance and has **not** been independently
> audited. The cryptography uses standard building blocks (ChaCha20, HMAC-SHA256,
> scrypt, Ed25519), but their combination and the pure-Python implementations
> have not been reviewed by an expert. Do not rely on this for safety-critical
> or high-risk communication.

## What is protected

| Asset | Protection |
|---|---|
| Private messages (one person) | a pair key both sides compute from their Ed25519 keys (Diffie–Hellman on the Ed25519 curve), then ChaCha20 + 5-byte HMAC tag; only the two of you can read; a separate key per direction |
| Delivery receipts | sent inside the same encryption; outsiders can't tell a receipt from a message |
| Sealed messages (groups, your stream) | ChaCha20 encryption + 5-byte HMAC-SHA256 tag (encrypt-then-MAC); only key holders can read |
| Signed messages | readable by all; key holders can verify origin and integrity |
| Replays | per-stream counter; old packets rejected |
| Who sent a private message (on air) | no sender ID; the tag changes on every packet |
| TextCast numbers | computed from the public key (scrypt n=2¹⁴ + Luhn check digit); an answer is accepted only if its key really has the number; two keys for one number are refused with a warning |
| Stream ownership | `lts3:` share strings are signed with the owner's Ed25519 key; forged or altered strings are refused |
| Identity file (`keys-<nick>.lkey`) | encrypted with your passphrase (scrypt n=2¹⁵ + ChaCha20 + HMAC) |
| Message log (`log-<nick>.db`) | for private messages, the text, channel and sender are encrypted |
| Unattended computer | lock (F2 → Lock, F5) and auto-lock after 15 idle minutes wipe keys from memory |
| Android phone | identity and log in the app's private storage, Android backup off for the app; Lock (⋮ menu) wipes keys from memory |

## What is NOT protected

- **Public (plain) messages:** anyone can read them, and anyone can claim any
  name.
- **A faked TextCast number:** 10 digits are about 33 bits. Someone with a lot
  of computing power could search for a key that has your number and answer
  lookups for it. The slow hash makes each try cost about 0.05–0.5 s (years of
  one computer's time for one number); trust on first use and the conflict
  warning help. Contacts added by number are marked as such; for high stakes,
  add people with their identity string (`lid1:`) or compare the number in
  person.
- **Number lookups are public:** anyone in range sees that someone asked for a
  number, and the answer (name and public key). Switch "find me by number" off
  if you'd rather not answer.
- **Group members impersonating each other:** every member holds the group
  secret. Group senders are shown with ◆, never ✓.
- **Followers forging posts** on a stream they follow (shared secret). A real
  fix needs per-post signatures (64 bytes each).
- **Member removal / forward secrecy:** a key cannot be revoked. Create a new
  group or stream. A leaked key exposes past and future messages under it.
- **Metadata on air:** message type (plain/signed/sealed), size and timing are
  visible. The transmitter can be located by direction-finding.
- **Flooding and jamming:** the rate limit is enforced only in this software.
- **Malware / keyloggers** on your computer, or someone using it while it is
  unlocked.
- **A forgotten passphrase:** the identity cannot be recovered. Keep a backup
  (`identity.py export`).

## Practical advice

- Share strings (`lts3:`, `ltg2:`) contain **secret keys**. Send them only to
  the intended people over a channel you trust. Clear your clipboard afterwards.
- Verify a contact's **number or full fingerprint** in person or by phone. The
  4-character tag in a handle can be imitated.
- Move an identity to another computer; don't run it on two computers at once
  (that risks nonce reuse).
- `config.json` (radio settings, last nick and port) is not secret but visible.
- On Android, anyone who can unlock the phone and open the app sees the chat
  while TextCast is unlocked. Lock it (⋮ → Lock) when you hand the phone over.
  A rooted or compromised phone can read the app's memory and files.
- Share strings sent through other messaging apps are only as private as those
  apps. For sensitive groups, share them in person.

## Three implementations

The Python core (terminal app), the Kotlin core (Android) and the JavaScript
core (web app) implement the same cryptography independently. Test vectors
generated from the Python code are reproduced byte for byte by the Kotlin and
JavaScript code, and files and messages made by them are opened by the Python
code (`android/compat/`, `web/tests/`). That catches format mistakes; it is not
a security review of any implementation.

The web app keeps the identity and messages in the browser's storage
(encrypted with the passphrase if you set one). Anyone who can use your browser
profile while TextCast is unlocked can read your chats; clearing site data
deletes the identity, so keep a `.ltid` backup.

## Design references

See [SPEC.md](SPEC.md) for exact formats: frame v1, counters, key derivation,
the vault format and the log encryption.

## Reporting issues

Please open a GitHub issue. For a sensitive vulnerability, contact the
maintainer privately before publishing details.
