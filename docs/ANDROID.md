# TextCast for Android

The Android app turns a phone or tablet into a TextCast node. It speaks the same
protocol as the web and terminal apps, so phones and laptops share one network,
and an identity (with its TextCast number) can move between them. It looks and
works like the web app: a chat list, and one chat at a time.

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

1. **Identity**: choose "+ New identity", type a nick (1–16 characters, no
   spaces) and a passphrase (at least 8 characters, typed twice). **A forgotten
   passphrase cannot be recovered.**
2. **Radio module**: pick the USB device if it is plugged in, or
   **Connect later (plug it in anytime)**.
3. **Settings** (optional): radio module type, region preset and radio values.
   They must match the other nodes. The default is AS923-2, 920.8 MHz, SF7.
4. **Start**. With a module, allow USB access when Android asks; the app checks
   the module and applies the radio settings (a few seconds).

**Plugging the module in later:** connect it with the OTG adapter whenever you
want to send. TextCast connects by itself (tap the bar at the top if it
doesn't). Android may ask to open TextCast for this USB device: tick *Always*.
If you unplug it, the bar says *Radio unplugged*; plug it back in and it
reconnects, no need to log out. Nothing is received while it is unplugged.

The login screen remembers your identity and module for next time.

## Chats

- **Chat list** (home screen): 📢 **Public** first, then people and groups,
  newest first, with the last message and an unread count. The bar on top
  shows the radio (*Radio on · 920.8 MHz · SF7*, *No radio*, *Radio unplugged*)
  and how many messages wait to be sent. The subtitle shows **your number**.
- **A chat:** tap one. Their messages on the left (with the sender's name in
  Public and groups), yours on the right with 🕓 → ✓ sent → **✓✓ delivered**
  (private chats). Long-press a message to copy it or see its details (signal
  strength, size). The byte count appears near the limit (100 bytes by
  default; Vietnamese letters use 2–3 bytes each). In groups, the **Signed**
  chip sends a readable but signed message instead of a sealed one.
- **Info** (top right in a chat): the person's number, *verified* or *added by
  number*, rename or delete; for a group, its invite string and *Leave*.

Messages leave at most about once every 10 seconds on average (short bursts are
allowed), and wait for the duty-cycle budget. That keeps the shared channel
usable for everyone.

## Add a person by their number

1. Ask for their **TextCast number** (⋮ → *My number* on their phone, ⚙ in the
   web app, F2 in the terminal app).
2. Tap the round **+**, type the number, tap **Find**. Your phone asks nearby
   devices who has it (they need TextCast running with the radio plugged in,
   within range).
3. *Found Lan nearby* → choose the name to save → **Add and chat**.
4. They add you the same way. Private messages now get ✓✓ when they arrive;
   *not delivered · tap to resend* after 30 s without a confirmation.

A wrong digit is caught before anything is sent. If two devices answer for one
number, TextCast warns and adds no one. **+ → More…** has the other ways:
paste an identity string (`lid1:`, counts as verified) or a group invite
(`ltg2:`), start a new group, and the list of groups and channels.

## Menu (⋮ on the chat list)

- **My number**: your TextCast number (copy it) and your identity string.
- **Let people find me by number**: on by default.
- **Groups and channels**: create a group or a broadcast channel, share its
  string (**Share…** to any app, or in person), delete.
- **Radio settings**, **Radio log** (every packet heard, with signal strength).
- **Lock / Unlock**, **Change passphrase…**, **Back up identity (.ltid)**,
  **Log out**.

## Security

- **Lock** removes your keys from memory; private messages that arrive while
  locked wait and open when you unlock.
- **Change passphrase…** re-encrypts the identity file.
- The identity and the message log stay in the app's private storage. Android
  backup is turned off for the app, so they don't leave the phone unless you
  export them.
- Notifications for private messages say who wrote, not what.

## Moving an identity between computer and phone

- **Computer → phone**: `python3 identity.py export --nick Tuan` (or ⚙ → Back
  up in the web app) makes `Tuan.ltid`. Copy it to the phone, then on the login
  screen choose **Identity → Import from file…** and type the file's passphrase.
- **Phone → computer**: ⋮ → **Back up identity (.ltid)**, save the file, copy it
  to the computer and run `python3 identity.py import Tuan.ltid` (or import it
  in the web app under *more options*).

Your TextCast number moves with the identity. **Move, don't clone.** Use an
identity on one device at a time: two devices sending with the same identity
can reuse encryption counters.

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
| "USB permission was refused" | Unplug and replug the module, tap *Allow* (tick *Always*) |
| *No radio* after plugging in | Tap the bar at the top; unplug and replug |
| "radio check" problems after Start | The module reported different settings: check its firmware (LA66 needs the P2P firmware) |
| Nothing received | All nodes need the same region/frequency/SF/bandwidth; check antennas; keep sticks a few metres apart (side by side they can overload each other) |
| "Nobody answered" when adding by number | Check the number; the other device needs TextCast running, the radio plugged in, within range |
| Private messages stay *not delivered* | They must add you too; clocks must agree within ±5 minutes (turn on automatic date and time) |
| The phone's only USB port is used | Charge before use, or use an OTG adapter with power pass-through |
