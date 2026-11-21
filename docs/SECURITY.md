# Security

> The code was written with AI assistance and has **not** been independently
> audited. The cryptography uses standard building blocks (ChaCha20, HMAC-SHA256,
> scrypt, Ed25519), but their combination and the pure-Python implementations
> have not been reviewed by an expert. Do not rely on this for safety-critical
> or high-risk communication.

## What is protected

| Asset | Protection |
|---|---|
| Sealed messages (groups, your stream) | ChaCha20 encryption + 5-byte HMAC-SHA256 tag (encrypt-then-MAC); only key holders can read |
| Signed messages | readable by all; key holders can verify origin and integrity |
| Replays | per-stream counter; old packets rejected |
| Who sent a private message (on air) | no sender ID; the tag changes on every packet |
| Stream ownership | `lts3:` share strings are signed with the owner's Ed25519 key; forged or altered strings are refused |
| Identity file (`keys-<nick>.lkey`) | encrypted with your passphrase (scrypt n=2¹⁵ + ChaCha20 + HMAC) |
| Message log (`log-<nick>.db`) | for private messages, the text, channel and sender are encrypted |
| Unattended computer | lock (F5) and auto-lock after 15 idle minutes wipe keys from memory |

## What is NOT protected

- **Public (plain) messages:** anyone can read them, and anyone can claim any
  name. The app marks such names with `?`.
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
- Verify a contact's **full fingerprint** (F6) in person or by phone. The
  4-character tag in a handle can be imitated.
- Move an identity to another computer; don't run it on two computers at once
  (that risks nonce reuse).
- `config.json` (radio settings, last nick and port) is not secret but visible.

## Design references

See [SPEC.md](SPEC.md) for exact formats: frame v1, counters, key derivation,
the vault format and the log encryption.

## Reporting issues

Please open a GitHub issue. For a sensitive vulnerability, contact the
maintainer privately before publishing details.
