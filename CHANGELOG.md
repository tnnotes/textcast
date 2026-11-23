# Changelog

## 0.5.0

- **Android app** (Android 5.0+, USB OTG): login, channels, groups, streams,
  contacts, lock/unlock, radio settings, background listening, identity
  import/export (`.ltid`). The protocol core is ported to Kotlin and checked
  byte for byte against the Python code in both directions (`android/compat/`).
- **Hardware profiles:** support for different LoRa modules. Built-in profiles
  for the Dragino LA66 (tested), RAK3172 (RUI3) and Seeed Wio-E5 (untested);
  custom modules via a JSON profile with your own AT commands, receive patterns
  and payload limits (`settings.py hardware …`, `--hardware`). Modules with
  different profiles share one network.
- **Desktop login panel simplified:** identity, passphrase, serial port. The
  radio module moved to Radio settings (F7 → Module); the panel scrolls on small
  screens.
- 84 Python tests; Kotlin tests for the core and simulated LA66/RAK modules.

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
