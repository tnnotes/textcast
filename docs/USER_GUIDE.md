# User guide: the terminal app

How to install, start and use `chat.py`, the terminal app (macOS, Windows,
Linux, Raspberry Pi). See the main [README](../README.md) for an overview,
[WEB.md](WEB.md) for the browser app, [ANDROID.md](ANDROID.md) for the Android
app and [SPEC.md](SPEC.md) for the protocol. All three apps work the same way
and talk to each other.

## Setup

**macOS / Linux**
```bash
cd textcast
python3 -m pip install -r requirements.txt     # pyserial, textual; qrcode is optional (QR codes)
python3 -m unittest discover -s tests          # should end with OK
```

**Windows 10/11** (install Python 3 from python.org, tick "Add python.exe to PATH")
```bat
cd textcast
py -m pip install -r requirements.txt
py -m unittest discover -s tests
```
- Use Windows Terminal, and `py` wherever this guide says `python3`.
- Port names are `COM3`, `COM5`, … (Device Manager → Ports (COM & LPT)). If the
  LA66 is not listed, install the USB-serial driver for the chip Device Manager
  names (CP210x or CH340).

Close any serial terminal (CoolTerm, PuTTY, Arduino monitor) first: only one
program can hold the port. The baud rate comes from the module's profile
(9600 for the LA66).

## Start

```bash
python3 chat.py
```
A **login panel** opens with three things:

1. **Identity**: pick yours, or "+ New identity" and type a nick (1–16
   characters, no spaces).
2. **Passphrase**: new identity: choose one, at least 8 characters, typed twice;
   **it cannot be recovered**. "No passphrase (lab only)" creates an unprotected
   identity for bench tests.
3. **Radio**: leave it on **Automatic**. TextCast uses the LA66 whenever it is
   plugged in, now or later. Or pick a port from the list (↻ after plugging it
   in), or "Other port…" to type it.

Press **Start** (or Enter in any field). **Settings** opens the radio settings,
including the **radio module** (Dragino LA66 by default), before you start.

You can log in without the radio: read your chats, add groups, see your number.
Sending (and finding people by number) waits until you plug it in; your typed
message stays in the box. Nothing is received while the radio is unplugged.
When you plug it in, the top bar changes to **● Radio on** within a few seconds.

The panel remembers your last identity and port. Optional flags pre-fill it:
`python3 chat.py --nick Tuan --port /dev/cu.usbserial-0001`.

Each nick has its own files: `keys-<nick>.lkey` (identity, encrypted) and
`log-<nick>.db` (message log). Two instances on one computer need different
identities and different sticks; an identity already open elsewhere is refused.

## Using the app

It works like a phone messenger:

```
 TextCast   Tuan  512 384 9076                     ● Radio on · 920.8 MHz · SF7
╭ Chats ─────────────────────────╮╭ ● Lan  ·  🔒 private · 847 001 8055 ─────────────╮
│ ◎ Public  11:40                ││                                                   │
│   Minh: có ai nghe không?      ││ chào anh  11:40                                   │
│ ● Lan  11:42 1                 ││                            Ok, 3h nhé  11:41 ✓✓   │
│   họp lúc 3h nhé               ││ họp lúc 3h nhé  11:42                             │
│ ◆ team  09:15                  ││                                                   │
│   You: đã tới                  ││                                                   │
╰────────────────────────────────╯╰───────────────────────────────────────────────────╯
╭────────────────────────────────────────────────────────────────────────────────────╮
│ ● Lan   private message to Lan                                                     │
╰────────────────────────────────────────────────────────────────────────────────────╯
 ^n New chat  F2 Settings  F3 Chat info  F1 Help  ^q Quit
```
- **Chats on the left, the open chat on the right.** ◎ Public is always first
  (everyone nearby reads it); people (●) and groups (◆) follow, newest first,
  with the last message and an unread count. Click a chat, or press **Esc** and
  use the arrows and Enter. **Alt+Up / Alt+Down** switch chats from the message
  line.
- **Their messages on the left, yours on the right** with 🕓 waiting, ✓ sent,
  ✓✓ delivered (private chats). The byte count appears in the corner of the
  input box when a message gets close to the limit.
- **Ctrl+N, new chat:** type someone's TextCast number (below), or paste an
  identity string or a group invite, or start a group.
- **F2, settings:** your number, "let people find me by my number", your
  identity string (QR + copy), radio settings, the radio log (every packet with
  its signal strength), groups & channels, lock.
- **F3, chat info:** the person's number (rename, delete) or the group's invite
  string with a QR code (leave the group).
- **F8** (or `/resend`) sends a private message again if it was not delivered.
- Typed commands: `/add NUMBER /number /resend /lock /radio /log /whoami
  /announce /member HANDLE /contacts /contact add|del /help /quit`.
  On a Mac, F-keys may need `fn`; typed commands always work.
- Recent messages are reloaded from the log when you start or unlock.

### The radio: unplug and plug back in

The top bar shows the radio: **● Radio on · 920.8 MHz · SF7**, **○ No radio**
or **○ Radio unplugged**. Unplug the LA66 and plug it back in (into any USB
socket): TextCast reconnects by itself and re-applies the radio settings if the
stick lost them. Messages that were still waiting to be sent show "not sent":
send them again (a delayed private message would be refused, because its
counter is time-based). The 1 %/hour airtime count carries over a replug.

In Automatic mode TextCast only uses a serial device that answers like an
LA66 after a reset; anything else is left alone.

## TextCast numbers

Every identity has a 10-digit number, like a phone number: `512 384 9076`
(F2 shows yours, or `/number`). There is no server: the number is computed from
your identity key (a slow hash plus a check digit), so nobody hands numbers out
and you can't pick one. Tell people your number; they press **Ctrl+N** (＋ in
the web and Android apps) and type it:

1. Their app asks over the radio "who has 512 384 9076?" (7 bytes).
2. Your app answers with your name and public key (one or a few short packets).
3. Their app checks that the key really has that number and saves you.
   Add each other both ways; then private messages get ✓✓.

- A typo is caught by the check digit before anything is sent.
- Only people within radio range, with TextCast running, can answer.
- Switch "Let people find me by my number" off (F2) to be added only with your
  identity string.
- **Added by number** vs **✓ verified:** a contact added by number shows that in
  its info (F3); one added with the identity string (`lid1:…`, the QR code)
  counts as verified. If two different devices answer for one number, the app
  warns you and adds no one: use the identity string then.
- 10 digits are short, so a determined person with a lot of computing power
  could make a key with your number. The slow hash makes that cost years of
  computer time; for high stakes, compare the number in person or use the
  identity string.

## Private messages to one person

1. Add each other once: by TextCast number or by identity string. **Both** of
   you add each other.
2. The person appears in the chat list (● Lan). Pick them and type.
3. Your message shows `✓` when sent, then **`✓✓`** when their device confirms it
   (automatically, within a few seconds). After 30 s without a confirmation it
   shows **`not delivered`**: they may be out of range, off, or haven't added
   you. **F8** (or `/resend`) sends it again; they won't see it twice.

On air a private message looks like any other sealed packet: nobody else can
tell who is talking to whom. A private message carries up to about 92 bytes of
text at the default profile.

## Groups and broadcast channels

Nothing on air says who sent a packet or which chat it belongs to: each packet
carries a 5-byte tag that changes every time, and only key holders can
recognise it.

**Group:** Ctrl+N → "start a group" → send the invite string (`ltg2:…`, also F3
with a QR code) to the members. They paste it in Ctrl+N; their app announces
itself to the group (encrypted, with its key), and their messages show
`Lan#P2MD ◆`. If a member was offline when someone joined, the newcomer types
`/announce` in the group, or members add them with `/member HANDLE`
(e.g. `Lan#P2MD`).

**Broadcast channel** (you post, followers read): F2 → Groups & channels →
name → "New stream"; give followers its `lts3:…` string (signed by your key, so
their app shows your posts as `Tuan#K7QX ✓`; a forged or altered string is
refused). F4 (`/signed`) posts readable-by-all text that followers can verify.

What others see: sealed → `[N B sealed]`, signed → `[unverified] text`.

- Clocks must agree within about ±5 minutes (normal with internet time).
- Limits: anyone in a group can post as another member; followers could forge
  posts on a stream they follow. Leaving a group means creating a new one.

## Radio settings (region presets or manual)

The module only needs suitable firmware (the LA66: P2P firmware); the app sets
frequency, SF, bandwidth and the rest. **Every node that should talk must use
the same settings.**

**In the app: F2 → Radio settings (or `/radio`, or "Settings" on the login
panel).**
- *Module*: which radio module you use (see "Radio modules" below).
- *Region preset*: AS923-1/2/3/4, EU868, US915, AU915, IN865, KR920 fills in all
  values; `custom` lets you set each one.
- Edit frequency, SF, bandwidth, coding rate, power, preamble, dwell limit, duty
  cycle and the log limits. The panel shows the resulting profile and limits as
  you type (red = not allowed, with the reason).
- **Apply** saves to `config.json` and, if connected, re-applies the profile to
  the module immediately (sending pauses for a few seconds).

Presets are **starting points, not legal advice**: check your local rules and
use the module variant matched to your band (868 vs 915/923 MHz).

Limits follow the profile: the maximum message size shrinks at higher SF to
stay within the dwell limit and the firmware limit (default 100 B; SF9 at
125 kHz with 400 ms dwell = 65 B; SF10 = 23 B; SF11/12 need a wider bandwidth
or no dwell limit). Profiles leaving fewer than 16 B are refused. The
duty-cycle cap (e.g. 1 % = 36 s per hour) holds messages back when you have
used your share.

**From the command line** (same settings file):
```bash
python3 settings.py show | regions
python3 settings.py set region EU868      # or: set sf 9 | freq 869.5 | bw 250 | power 10 …
python3 settings.py reset radio
python3 chat.py --region EU868 --sf 9     # one session only, not saved
```

## Radio modules

The Dragino LA66 is the default. RAK3172 and Seeed Wio-E5 profiles are
included, and other modules can be added with a JSON profile. Choose the module
in **Settings → Module**, or `python3 settings.py set hardware NAME`. Details and
the profile format: [HARDWARE.md](HARDWARE.md).

## Log size

By default the log keeps **the last 20,000 messages and at most 30 days**; older
ones are deleted at start and every 200 messages, and the file is compacted.
```bash
python3 settings.py set log.max_rows 100000     # 0 = unlimited
python3 settings.py set log.max_days 0          # 0 = keep forever
# or for one session: --log-max-rows N --log-max-days N
```
The radio settings panel also sets these and shows how many messages and KB
the log holds.

## Names and verification

Nobody can own a name in a network without a server, so names work like this:

| You see | Meaning |
|---|---|
| `Lan` in your chat list | a contact: added by number, or ✓ verified with their identity string (F3 says which) |
| `Tuan#K7QX ✓` | verified: a post on a channel you follow whose share string was **signed** by Tuan#K7QX's identity key |
| `Lan#P2MD ◆` | group message: sent with the group key. Any member could have sent it, so groups never get ✓ |
| `Tuan` in Public | a plain public message: the name is only a claim (CB radio) |

- Two people can both choose "Tuan": they become `Tuan#K7QX` and `Tuan#P2MD`,
  with different TextCast numbers.
- **Your identity string:** F2 → "My identity string" (or `/whoami`) shows your
  handle, fingerprint (`K7QX-M2PA-9TRE`) and `lid1:…` string with a QR code.
- **Be sure it's them:** compare the number or the fingerprint in person or by
  phone.
- Plain public messages stay `Tuan: hello` (zero extra bytes). `--public-tag`
  writes `Tuan#K7QX: hello` instead (5 bytes more, still unverified).

## Security: passphrase, lock, unlock

| File | Protection |
|---|---|
| `keys-<nick>.lkey` | encrypted with your passphrase (scrypt + ChaCha20 + HMAC) |
| `log-<nick>.db` | for private messages the text, chat name and sender are encrypted; public plain text and radio data (time, RSSI, SNR) readable |

- **Lock:** F2 → Lock (or F5, `/lock`) wipes your keys from memory. The app
  also locks itself after 15 idle minutes (`--lock-minutes N`, `0` = never).
- **While locked:** public messages keep arriving; private ones are stored
  encrypted and shown when you unlock. Your device still answers lookups for
  your number if that is switched on.
- **Unlock:** type your passphrase in the unlock window.
- **Change passphrase:** `python3 identity.py passwd --nick Tuan` (app closed).
- **Old plain identities** (`keys-<nick>.json`): pick it on the login panel and
  type a new passphrase twice to encrypt it, or run
  `python3 identity.py protect --nick Tuan`.

Share strings (`lts3:`, `ltg2:`) contain secret keys: send them only to the
people who should have them, and clear your clipboard afterwards.
`config.json` holds your last nick and port (not secret, but visible).

## Your identity: back up, restore, move

Your identity = `keys-<nick>.lkey`: nick, identity key (and so your TextCast
number), channels, groups, contacts. Moving it keeps your handle
(`Tuan#K7QX`), number and fingerprint. The LoRa stick holds nothing, so any
stick works with any identity.

```bash
python3 identity.py export --nick Tuan     # -> Tuan.ltid (same passphrase)
# copy Tuan.ltid to the new computer (USB, email, cloud - it is encrypted)
python3 identity.py import Tuan.ltid       # -> keys-Tuan.lkey
python3 chat.py                            # pick Tuan on the login panel
python3 identity.py show --nick Tuan       # what an identity contains
```
- Contacts and group members don't need to do anything.
- **Move, don't clone.** Stop using the identity on the old computer: two
  devices posting as the same identity at once can reuse encryption nonces.
- An old backup is safe to restore: counters start from the current time.
- The same `.ltid` file moves an identity to or from the web app and the
  Android app.

## Field test (range / loss / RSSI / SNR)

```bash
# laptop A (fixed)
python3 fieldtest.py beacon --port /dev/cu.usbserial-A --label A --interval 15 --size 50
# laptop B (walk away with it)
python3 fieldtest.py listen --port /dev/cu.usbserial-B --label B
# Ctrl-C on B prints received/expected, loss %, RSSI and SNR stats
```
Results go to `fieldtest.db`. Before range testing, check the bench RSSI: two
nodes a few metres apart should read well above −100 dBm. If not, check
antennas and move the sticks away from the laptops on USB extension cables.
