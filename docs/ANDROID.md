# TextCast for Android

The Android app turns a phone or tablet into a TextCast node. It speaks the same
protocol as the desktop app, so phones and laptops share one network, and an
identity can move between them.

## What you need

- A phone or tablet with **Android 5.0 or newer** and **USB host (OTG)**
  support. Most phones have it; some low-cost models don't.
- A **USB OTG adapter or cable** (USB-C or micro-USB to USB-A) for the LoRa stick.
- A supported LoRa module (see [HARDWARE.md](HARDWARE.md)). The Dragino LA66 USB
  stick with the P2P firmware is tested.

## Install

1. Download `TextCast-<version>.apk` from the
   [Releases page](https://github.com/tnnotes/textcast/releases) (on the phone,
   or copy it there).
2. Open the file. Android asks to allow installing apps from this source: allow
   it for your browser or file manager.
3. TextCast appears in the app list.

To build the APK yourself, see [RELEASING.md](RELEASING.md).

## First start

1. **Plug in the module** with the OTG adapter. Android asks whether to open
   TextCast for this USB device. Tick *Always* so it doesn't ask again.
2. **Identity**: choose "+ New identity", type a nick (1–16 characters, no
   spaces) and a passphrase (at least 8 characters, typed twice). **A forgotten
   passphrase cannot be recovered.**
3. **Radio module**: pick the USB device from the list (↻ refreshes it).
   "Offline" opens the app without a radio, to read history and manage keys.
4. **Settings** (optional): radio module type, region preset and radio values.
   They must match the other nodes. The default is AS923-2, 920.8 MHz, SF7.
5. **Start**, and allow USB access when Android asks. The app checks the module
   and applies the radio settings (a few seconds).

The login screen remembers your identity and module for next time.

## The chat screen

- **Title bar**: the channel you read and write in. Tap it to switch:
  *All* (everything), *Public* (plain text), your groups 👥, your streams 📣,
  streams you follow 👁, and *Other* (packets nobody here can decode).
- **Status strip** under it: your handle, region, frequency and how much of the
  duty-cycle budget you have used. It turns amber when you are offline, locked,
  or messages are waiting to be sent.
- **Messages**: sender (each person has their own colour), 🔒 sealed,
  👥🔒 group, ✍ signed, ✓ verified owner, then time and signal strength
  (dBm, SNR). Your own messages have a coloured stripe and show *sending…*,
  *✓ sent* or *✗ not sent*. Long-press a message to copy it or reply.
- **Input bar**: type and tap ➤. The byte count appears when you get close to
  the limit (100 bytes by default; Vietnamese letters use 2–3 bytes each).
  On group and stream channels, the **Signed** chip sends a readable but signed
  message instead of a sealed one.

Messages leave at most about once every 10 seconds on average (short bursts are
allowed), and wait for the duty-cycle budget. That keeps the shared channel
usable for everyone.

## Groups, streams and contacts (⋮ menu)

- **Keys and groups → + New group**: creates a group and shows its key string
  (`ltg2:…`). Send it to the members with the **Share…** button (any messaging
  app, email, or in person). Anyone with the string can read and post.
- **+ New stream**: a channel only you post to. Followers need its signed string
  (`lts3:…`).
- **Add a key string…**: paste a `ltg2:` (join a group), `lts3:` (follow a
  stream) or `lid1:` (save a contact) string. If you copied one, it is filled
  in for you. Joining a group announces you to its members automatically.
- **My identity**: your `lid1:` string and fingerprint. Compare fingerprints in
  person before trusting a key.
- **Contacts**: names you gave to verified keys.

## Security

- **Lock** (⋮ menu) removes your keys from memory; private messages that arrive
  while locked wait and open when you unlock.
- **Change passphrase…** re-encrypts the identity file.
- The identity and the message log stay in the app's private storage. Android
  backup is turned off for the app, so they don't leave the phone unless you
  export them.

## Moving an identity between computer and phone

- **Computer → phone**: `python3 identity.py export --nick Tuan` makes
  `Tuan.ltid`. Copy it to the phone, then on the login screen choose
  **Identity → Import from file…** and type the file's passphrase.
- **Phone → computer**: ⋮ → **Export identity (.ltid)…**, save the file, copy it
  to the computer and run `python3 identity.py import Tuan.ltid`.

**Move, don't clone.** Use an identity on one device at a time. Two devices
sending with the same identity can reuse encryption counters.

## Running in the background

While connected, TextCast keeps listening with the screen off or another app
open. A notification shows it is running and counts new messages. This uses
some battery (the radio and USB stay on). **Log out** (⋮ menu) to stop.

If the phone kills the app in the background, turn off battery optimisation for
TextCast (Settings → Apps → TextCast → Battery → Unrestricted; wording varies by
phone).

## Troubleshooting

| Problem | What to try |
|---|---|
| The module is not in the list | Check the OTG adapter (try it with a USB memory stick); tap ↻; unplug and replug |
| "USB permission was refused" | Start again and tap *Allow* (tick *Always*) |
| "radio check" problems after Start | The app applied the settings but the module reported different ones: check the module firmware (LA66 needs the P2P firmware) |
| Nothing received | All nodes need the same region/frequency/SF/bandwidth; check antennas; keep sticks a few metres apart (side by side they can overload each other) |
| Private messages don't open | Clocks must agree within ±5 minutes: turn on automatic date and time |
| The phone's only USB port is used | Charge before use, or use an OTG adapter with power pass-through |
