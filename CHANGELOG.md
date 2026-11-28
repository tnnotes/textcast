# Changelog

## 0.7.0

- **Looks like a phone messenger** in all three apps: a chat list (Public
  first, then people and groups with the last message and an unread count)
  and the open chat with your messages on the right. On narrow screens one at
  a time with ← back. Radio settings, the radio log and channels moved to
  Settings → Advanced. The Android app has a new home screen (chat list with a
  round + button).
- **Private chats** with one person, end-to-end encrypted with a key both
  sides compute from their Ed25519 identity keys. **Delivery receipts:**
  ✓ sent → ✓✓ delivered; *not delivered · Resend* after 30 s. A resend is never
  shown twice. On air a private message looks like any other sealed packet.
- **TextCast numbers:** every identity has a 10-digit number (`512 384 9076`)
  computed from its key: a slow scrypt hash plus a check digit, no server.
  **＋ New chat** asks over the radio "who has this number?"; the owner's device
  answers with its key, which is checked against the number. Typos are caught
  before sending; two keys for one number are refused with a warning.
  "Let people find me by my number" can be switched off. Contacts show
  *✓ verified* (identity string) or *added by number*.
- **Plug the radio in any time:** log in without it (read chats, set things
  up); the app finds the stick when it is plugged in and reconnects by itself
  after unplugging, in all three apps. Frames waiting to be sent when the stick
  is unplugged fail (and can be resent) instead of going out later with a stale
  counter. The duty-cycle count is kept across a replug.
- The terminal app is `chat.py` only (`simple_chat.py` and `tui.py` are gone).
  Keys: Ctrl+N new chat, F2 settings, F3 chat info, F8 resend.
- Protocol: new direct-message inner types (text, resend, receipt) and the
  DIRECTORY frame type for number lookups ([docs/SPEC.md](docs/SPEC.md)).
  Older apps show them as sealed packets they can't open.

## 0.6.0

- **Web app** (`web/`, published at https://tnnotes.github.io/textcast/): the
  whole app in one page that talks to the LoRa stick through the browser (Web
  Serial). Chrome, Edge and Firefox 151+ on Windows, macOS, Linux and
  ChromeOS; nothing to install. Same protocol and `.ltid` identity files as the
  desktop and Android apps: the JavaScript core is checked byte for byte
  against the Python code in both directions.
  - Start with no questions: type your name and press Start; the region comes
    from the time zone; the browser remembers the stick and reconnects when it
    is plugged back in.
  - Works offline after the first visit, and installs as an app in Chrome and
    Edge. `web/index.html` also runs straight from a file.
  - Groups, streams, contacts, radio settings with a size/airtime preview,
    passphrase lock, identity export/import, a backup reminder, notifications.
- Docs: [docs/WEB.md](docs/WEB.md); the README starts with the three ways in
  (browser, Android, repository).

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
