# Changelog

## 0.4.0 (first public release as TextCast)

- **Radio:** Dragino LA66 P2P driver (paced TX queue, robust RX parser, SNR),
  region presets (AS923-1/2/3/4, EU868, US915, AU915, IN865, KR920) and manual
  settings. Limits follow the profile (dwell time, firmware limits, duty cycle).
  The profile is applied to the LA66 automatically.
- **Protocol:** plain, signed and sealed messages. Private frame v1 (hint byte
  + 5-byte rolling tag, no sender ID on air). Group join announcements.
- **Identity:** Ed25519 key pair, `nick#TAG` handles, signed stream strings
  (`lts3:`), contact book.
- **Local security:** passphrase-protected identity file, lock and auto-lock,
  encrypted private log data. Backup, restore and passphrase change
  (`identity.py`).
- **Apps:** `chat.py` (Textual UI with login panel, channel list, key, share,
  identity and radio panels) and `simple_chat.py` (fallback).
- **Tools:** `settings.py`, `fieldtest.py`.
- 75 unit tests with a simulated LA66.
