# User guide

How to install, start and use TextCast. See the main [README](../README.md) for an
overview, and [SPEC.md](SPEC.md) for the protocol.

## Setup

**macOS / Linux**
```bash
cd textcast
python3 -m pip install pyserial textual qrcode     # qrcode is optional (QR codes)
python3 -m unittest discover -s tests               # should end with OK
ls /dev/cu.*                                        # find the LA66 USB port
```

**Windows 10/11** (install Python 3 from python.org, tick "Add python.exe to PATH")
```bat
cd textcast
py -m pip install pyserial textual qrcode
py -m unittest discover -s tests
py -m serial.tools.list_ports          :: find the port, e.g. COM5
```
- Use Windows Terminal. Port names are `COM3`, `COM5`, … (Device Manager →
  Ports (COM & LPT)). If the LA66 is not listed, install the USB-serial driver
  for the chip Device Manager names (CP210x or CH340).
- On Windows, use `py` wherever this file says `python3`.
- `windows-curses` is only needed for the full-screen mode of the fallback
  `simple_chat.py`; without it, it uses its line mode automatically.

Close any serial terminal (CoolTerm, PuTTY, Arduino monitor) first — only one
program can hold the port. Baud rate is 9600.

## Start

```bash
python3 chat.py
```
That's all. A **login panel** opens:

1. **Identity** — pick yours from the list, or "+ new identity…" and type a nick.
2. **Passphrase** — type it (new identity: choose one, at least 8 characters, typed
   twice). **If you forget it, the identity cannot be recovered.** Old unprotected
   identities can be protected here by typing a new passphrase twice.
   "Lab mode" creates an identity without a passphrase (UNPROTECTED) for bench tests.
3. **LA66 serial port** — pick it from the list (Refresh after plugging it in) or type it.
4. **Start.** The app checks the LA66 and applies your radio profile if needed.
   "Radio settings" opens the radio panel before starting.

The panel remembers your last identity and port. Optional flags pre-fill it:
`python3 chat.py --nick Tuan --port /dev/cu.usbserial-0001`.

Each nick has its own files: `keys-<nick>.lkey` (identity, encrypted) and
`log-<nick>.db` (message log). Two instances on one laptop need different
identities and different dongles; an identity already open elsewhere is refused.

## Using the app

```
 TextCast   Tuan#K7QX                 AS923-2 · 920.800 MHz · SF7 · BW 125   TX 0.4%
╭ Channels ──────────────╮╭ ◆ team  ·  group · sealed, members only ───────────────────────╮
│ OVERVIEW               ││ ──── Saturday 21 November 2026 ────                            │
│ ◎ All messages         ││ 11:40  Lan#P2MD ◆       chào anh               -95 dBm · 7 dB  │
│ ○ Public  3            ││ 11:41  Lan#P2MD ◆       họp lúc 3h nhé         -96 dBm · 6 dB  │
│ GROUPS                 ││ 11:42  me               Ok, 3h nhé   ✓ sent · 72 ms            │
│ ◆ team                 ││                                                                │
│ MY STREAMS             ││                                                                │
│ ▲ Tuan                 ││                                                                │
│ FOLLOWING              ││                                                                │
│ ▼ Lan                  ││                                                                │
╰────────────────────────╯╰────────────────────────────────────────────────────────────────╯
╭────────────────────────────────────────────────────────────────────────────────────────╮
│ ◆ team · sealed   message to team - only key holders can read                          │
╰───────────────────────────────────────────────────────────────────────── 14 / 100 bytes ╯
 ^n Next  ^p Prev  F1 Help  F2 Keys  F3 Share  F4 Sealed/Signed  F5 Lock  F6 Me  F7 Radio
```
- **The selected channel is both what you read and where you send.** Switch with
  Ctrl+N / Ctrl+P or by clicking. Channels are grouped: Overview (◎ all, ○ public),
  ◆ Groups, ▲ My streams, ▼ Following (read-only), · Undecodable.
- The box above the messages names the channel; the input line shows where your
  message goes (green = private, yellow = public, red = read-only) and the byte
  count in its corner. A date line separates days.
- **F2 Keys:** create a stream or group (share string copied to the clipboard),
  paste a string to follow or join, delete. **F3:** share string + QR code of the
  current channel. **F4:** sealed ↔ signed. **F5:** lock.
- **F6:** your handle, fingerprint and `lid1:` string (QR + copy).
- **F7 (or `/radio`):** radio settings panel (below).
- Typed commands: `/keys /share /signed /announce /member HANDLE /lock /whoami
  /contacts /contact add|del /radio /help /quit`.
  On a Mac, F-keys may need `fn`; typed commands always work.
- ✓ = sent by the radio, … = waiting for its slot (rate limit: average 1 message
  per 10 s, bursts of 3). Numbers after a message = RSSI/SNR.
- Recent messages are reloaded from the log when you start or unlock.

## Radio settings (region presets or manual)

The LA66 only has to run the P2P firmware; the app sets frequency, SF,
bandwidth and the rest. **Every node that should talk must use the same settings.**

**In the app: F7 (or `/radio`, or "Radio settings" on the login panel).**
- *Region preset*: AS923-1/2/3/4, EU868, US915, AU915, IN865, KR920 fills in all
  values; `custom` lets you set each one.
- Edit frequency, SF, bandwidth, coding rate, power, preamble, dwell limit, duty
  cycle and the log limits. The panel shows the resulting profile and limits as
  you type (red = not allowed, with the reason).
- **Apply** saves to `config.json` and, if connected, re-applies the profile to the
  LA66 immediately (sending pauses for a few seconds).

Presets are **starting points, not legal advice** — check your local rules and
use the LA66 variant matched to your band (868 vs 915/923 MHz).

Limits follow the profile: the maximum message size shrinks at higher SF to stay
within the dwell limit and the firmware limit (default 100 B; SF9 at 125 kHz with
400 ms dwell = 65 B; SF10 = 23 B; SF11/12 need a wider bandwidth or no dwell
limit). Profiles leaving fewer than 16 B are refused. The duty-cycle cap
(e.g. 1 % = 36 s per hour) holds messages back when you have used your share.

**From the command line** (same settings file):
```bash
python3 settings.py show | regions
python3 settings.py set region EU868      # or: set sf 9 | freq 869.5 | bw 250 | power 10 …
python3 settings.py reset radio
python3 chat.py --region EU868 --sf 9     # one session only, not saved
```

## Log size

By default the log keeps **the last 20,000 messages and at most 30 days**; older
ones are deleted at start and every 200 messages, and the file is compacted.
```bash
python3 settings.py set log.max_rows 100000     # 0 = unlimited
python3 settings.py set log.max_days 0          # 0 = keep forever
# or for one session: --log-max-rows N --log-max-days N
```
The radio panel (F7) also sets these and shows how many messages and KB the log
holds; in `simple_chat.py`, `/stats`.

## Names and verification

Nobody can own a name in a network without a server, so names work like this:

| You see | Meaning |
|---|---|
| `Tuan#K7QX ✓` | verified: a post on a stream you follow whose share string was **signed** by Tuan#K7QX's identity key |
| `Thầy Tuấn ✓` | the same, shown with your **contact** name for that key |
| `Lan#P2MD ◆` | group message: sent with the group key. Any member could have sent it, so groups never get ✓ or contact names |
| `Tuan?` | public plain message — the name is only a claim (CB radio) |
| `Tuan#K7QX` (no mark) | stream from an old unsigned string (`lts2:`/`lts1:`): owner not proven |

- Two people can both choose "Tuan": they become `Tuan#K7QX` and `Tuan#P2MD`.
- **Your identity string:** F6 (or `/whoami`) shows your handle, fingerprint
  (`K7QX-M2PA-9TRE`) and `lid1:…` string with a QR code.
- **Add a contact:** paste their `lid1:…` in F2 → "Follow / Join / Contact", optionally
  followed by your name for them, or `/contact add lid1:… Thầy Tuấn`.
  `/contacts`, `/contact del NAME`.
- **Be sure it's them:** compare the fingerprint in person or by phone — the
  4-character tag alone can be imitated, the full key cannot.
- Plain public messages stay `Tuan: hello` (zero extra bytes). `--public-tag`
  writes `Tuan#K7QX: hello` instead (5 bytes more, still unverified).
- Sealed and signed messages carry up to 93 bytes of text (default profile).

## Security: passphrase, lock, unlock

| File | Protection |
|---|---|
| `keys-<nick>.lkey` | encrypted with your passphrase (scrypt + ChaCha20 + HMAC) |
| `log-<nick>.db` | for private messages (sealed / signed / joins) the text, group/stream name and sender are encrypted; public plain text and radio data (time, RSSI, SNR) readable |

- **Lock (log out):** F5 or `/lock` wipes your keys from memory. The app also locks
  itself after 15 idle minutes (`--lock-minutes N`, `0` = never).
- **While locked:** public messages keep arriving; private ones are stored
  encrypted and shown when you unlock. Sending privately is disabled.
- **Unlock:** type your passphrase in the unlock window.
- **Change passphrase:** `python3 identity.py passwd --nick Tuan` (app closed).
- **Old plain identities** (`keys-<nick>.json`): pick it on the login panel and type a
  new passphrase twice to encrypt it (the plain file is overwritten and removed), or
  run `python3 identity.py protect --nick Tuan`.
- **Lab mode** (checkbox on the login panel for a new identity) uses a plain
  `keys-<nick>.json` with no passphrase and no lock, shown as UNPROTECTED. For bench
  tests only. (`simple_chat.py --no-password` does the same.)

Share strings (`lts3:`, `ltg2:`) contain secret keys: send them only to the people
who should have them, and clear your clipboard afterwards. `config.json` holds
your last nick and port (not secret, but visible).

Protects against: someone copying your files, or using the computer while the app
is closed or locked. Does not protect against: someone using it while you are
unlocked and away (until auto-lock), malware/keyloggers, or a forgotten passphrase.

## Private messages (SIGNED / SEALED)

Nothing on air says who sent a packet or which channel it belongs to: each
packet carries a 5-byte tag that changes every time, and only key holders can
recognise it.

**Follow a person** (in chat.py use F2; in simple_chat.py the `/key` commands):
```
Tuan: /key new Tuan                  -> lts3:Tuan#K7QX:…   (signed; send to followers)
Tuan: select ▲ Tuan, type            <- sealed: only followers read it
      F4 / /signed                   <- signed: everyone reads, followers verify
Lan:  /key follow lts3:Tuan#K7QX:…   -> ▼ Tuan appears; posts show Tuan#K7QX ✓
                                        (a forged or altered string is refused)
```

**Group:**
```
Tuan: /key group new team            -> ltg2:team:…:Tuan#K7QX=…
Lan:  /key group join ltg2:team:…    <- Lan's app announces itself (encrypted, with its key)
Tuan sees: (group) team: Lan#P2MD joined   and Lan's messages show Lan#P2MD ◆
```
If a member was offline when someone joined, the newcomer sends `/announce`
(tui, in the group channel) or `/key announce team` (chat), or members add
them with `/member HANDLE` / `/key member team HANDLE` (e.g. `Lan#P2MD`).

What others see: sealed → `[N B sealed]`, signed → `[unverified] text`.

- Laptop clocks must agree within about ±5 minutes (normal with internet time).
- Limits: anyone in a group can post as another member; followers could forge
  posts on a stream they follow. Leaving a group is not possible — create a new one.
- Streams shared before signed strings existed (`lts2:`/`lts1:`) still work but show
  no ✓. Ask the owner for a new string (F3 on their stream gives an `lts3:` string).

## Your identity: back up, restore, move

Your identity = `keys-<nick>.lkey`: nick, identity key, streams, groups, follows,
contacts. Moving it keeps your handle (`Tuan#K7QX`) and fingerprint. The
LA66 holds nothing, so any dongle works with any identity.

```bash
python3 identity.py export --nick Tuan     # -> Tuan.ltid (same passphrase)
# copy Tuan.ltid to the new computer (USB, email, cloud - it is encrypted)
python3 identity.py import Tuan.ltid       # -> keys-Tuan.lkey
python3 chat.py                            # pick Tuan on the login panel
python3 identity.py show --nick Tuan       # what an identity contains
```
- Followers and group members don't need to do anything.
- **Move, don't clone.** Stop using the identity on the old computer: two
  computers posting as the same identity at once can reuse encryption nonces.
- An old backup is safe to restore: counters start from the current time.
- Keep a backup somewhere safe — losing both file and backup loses the identity.

## Simple chat (fallback without Textual)

```bash
python3 simple_chat.py --port /dev/cu.usbserial-XXXX --nick Tuan [--plain] [--region …]
```
Same identity, log and settings files as chat.py (run only one of them per identity).
It asks for the passphrase in the terminal.
Commands: `/to public|LABEL [signed]`, `/key …`, `/whoami`, `/contact add|del`, `/contacts`,
`/f #topic|@name|word`, `/f clear`, `/mute`, `/raw`, `/lock`, `/unlock`, `/stats`, `/radio`,
`/history [n]`, `/help`, `/quit`.
No auto-lock in simple_chat.py.

## Field test (range / loss / RSSI / SNR)

```bash
# laptop A (fixed)
python3 fieldtest.py beacon --port /dev/cu.usbserial-A --label A --interval 15 --size 50
# laptop B (walk away with it)
python3 fieldtest.py listen --port /dev/cu.usbserial-B --label B
# Ctrl-C on B prints received/expected, loss %, RSSI and SNR stats
```
Results go to `fieldtest.db`. Before range testing, check the bench RSSI: two
nodes a few metres apart should read well above −100 dBm. If not, check antennas
and move the dongles away from the laptops on USB extension cables.
