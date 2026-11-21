# TextCast

**Decentralized text broadcast over LoRa P2P.**

A CB-radio-style **text** network over LoRa. Everyone shares one
radio channel and everyone can receive everything. What you read is decided on
your own device: you follow people, join encrypted groups, or just read the
public channel. There is **no server, no account and no internet connection**.

<img width="1352" height="838" alt="Main_application" src="https://github.com/user-attachments/assets/92178b80-c130-4342-9166-1544839bb806" />


> **Built with AI.** This project was designed and written with substantial help
> from an AI assistant (Claude, by Anthropic). A human directed the work and
> tested it on real hardware. The code, including the cryptography, has **not**
> been independently reviewed or audited. Read it before you rely on it.

**Status:** working prototype (v0.4). Tested in a lab with two or more Dragino
LA66 USB adapters. Not field-tested at scale.

---

## What it does

| Feature | How |
|---|---|
| Public channel | Plain text that anyone in range can read, like CB radio |
| Follow a person | They create a *stream*; followers get a signed share string |
| Private groups | Messages encrypted with a shared group key |
| Signed posts | Readable by everyone, verifiable by people who hold the key |
| No sender ID on air | Each private packet carries a changing 5-byte tag that only key holders can recognise |
| Identities | Ed25519 key pair, handle `nick#TAG`, a contact book for verified keys |
| Local security | Identity file encrypted with a passphrase; lock/unlock; private parts of the log encrypted |
| Radio settings | Region presets (AS923, EU868, US915, …) or every parameter set by hand |
| Terminal app | Runs on macOS, Windows and Linux: login panel, channel list, key dialogs, radio panel |

## Purpose and scale

**Purpose:** local, infrastructure-free text communication. Useful at events,
campuses, rural areas and in emergencies, and as an open platform for
experimenting with decentralized messaging over radio.

**Scale, honestly:**
- **One radio hop.** There are no repeaters or routing. Range is what the radios
  reach (SF7: roughly 1–3 km in a city, more in open terrain).
- **Shared capacity:** one channel carries about **1 message per second for
  everyone in range**, roughly 86,000 messages per day at a realistic load.
- **Message size:** up to 100 bytes (93 bytes of text for private messages) at
  the default profile. Vietnamese and other non-Latin text uses 2–3 bytes per
  character.
- It is not an internet replacement. See [docs/SCALING.md](docs/SCALING.md) for
  what happens at larger scale and what would have to change.

## Hardware

**Tested:** Dragino **LA66 USB LoRaWAN Adapter** with Dragino's **LA66 P2P
firmware v1.2.4** (`LA66_P2P_v1.2.4_application_withbootloder.bin`).
The stock LoRaWAN firmware does **not** work: nodes must run P2P firmware.

- One LA66 per user, connected to a laptop (macOS, Windows or Linux).
- The app configures the radio itself (frequency, SF, bandwidth, …). It only
  needs the P2P firmware.
- Use the LA66 variant made for your band (868 MHz or 915/923 MHz).
- An iPhone cannot connect to a USB-serial adapter. Phones would need a
  Bluetooth bridge (not built yet).

Details, firmware quirks and how to add other radios:
[docs/HARDWARE.md](docs/HARDWARE.md).

## Install

Requires **Python 3.10+**.

```bash
git clone https://github.com/tnnotes/textcast.git
cd textcast
python3 -m pip install -r requirements.txt     # pyserial, textual, qrcode (optional)
python3 -m unittest discover -s tests          # should end with OK
```

On Windows, use `py` instead of `python3`, and Windows Terminal. Close any other
serial terminal before starting: only one program can use the port.

## Quick start

```bash
python3 chat.py
```

1. **Login panel:** choose or create an identity, type your passphrase, and pick
   the LA66 port.
2. **Start.** The app checks the LA66 and applies the radio profile.
3. Pick a channel on the left and type. **F1** opens help.

| Key | Action |
|---|---|
| Ctrl+N / Ctrl+P | next / previous channel |
| F2 | keys & contacts: create streams and groups, follow, join, add contacts |
| F3 | share string and QR code of the current channel |
| F4 | sealed ↔ signed |
| F5 | lock |
| F6 | your identity (handle, fingerprint, `lid1:` string) |
| F7 | radio settings |

The full guide is in [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

**All nodes must use the same radio settings** (default: AS923-2, 920.8 MHz,
SF7, 125 kHz). Region presets are starting points, **not legal advice**: check
your local rules for frequency, power, duty cycle and listen-before-talk.

## Project layout

```
chat.py              main app (Textual terminal UI)
simple_chat.py       fallback app without Textual (line or curses UI)
identity.py          back up, restore and move identities, change passphrase
settings.py          radio profile and log limits (config.json)
fieldtest.py         range and packet-loss test tool
lora_text/           core library
  protocol.py        frames, mode byte, airtime, rate limiter
  crypto.py          rolling tags, ChaCha20, counters
  keyring.py         identities, streams, groups, contacts
  vault.py           passphrase encryption (scrypt + ChaCha20 + HMAC)
  ed25519.py         identity keys (pure-Python RFC 8032)
  la66.py            LA66 P2P serial driver
  node.py            radio + keys + log, used by both apps
  config.py, regions.py, session.py, storage.py
tests/               75 unit tests with a simulated LA66 (no hardware needed)
docs/                specification, user guide, hardware, security, scaling
```

## Documentation

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md): using the app
- [docs/SPEC.md](docs/SPEC.md): air protocol and file formats
- [docs/HARDWARE.md](docs/HARDWARE.md): LA66 setup, firmware notes, adding new radios
- [docs/SECURITY.md](docs/SECURITY.md): what is protected and what is not
- [docs/SCALING.md](docs/SCALING.md): capacity limits and larger-scale design
- [CONTRIBUTING.md](CONTRIBUTING.md) · [CHANGELOG.md](CHANGELOG.md)

## Known limitations

- Single hop; no repeaters, routing or message history across devices.
- No acknowledgements: a message lost to a collision is gone.
- The rate limit is enforced by this software only. A modified client can flood
  the channel, and jamming is always possible.
- Anyone holding a group or stream key can post with it. Removing someone means
  creating a new key.
- Clocks must agree within about ±5 minutes (normal with internet time sync).
- The Textual UI is not covered by the automated tests.
- No listen-before-talk (the LA66 P2P firmware lacks it). Some regions, such as
  Japan and Korea, require it.

## License

MIT, see [LICENSE](LICENSE). Use of radio spectrum is your responsibility under
your local regulations.
# Pictures
<img width="1352" height="838" alt="Screenshot 2026-11-21 at 15 50 13" src="https://github.com/user-attachments/assets/a29d71cf-11e5-436a-a338-c0a7dda32d1b" />
<img width="1352" height="838" alt="Screenshot 2026-11-21 at 15 49 35" src="https://github.com/user-attachments/assets/7e01515b-2e12-4f45-957f-58b845a1e929" />
<img width="1352" height="838" alt="Screenshot 2026-11-21 at 15 49 06" src="https://github.com/user-attachments/assets/f87b1d1e-09cc-4ed4-b1ba-b595e9fef9c0" />
<img width="1352" height="838" alt="Login" src="https://github.com/user-attachments/assets/0553521e-6cb8-469d-8515-5522cebb89a2" />

