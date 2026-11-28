# TextCast — Air Specification v0.7 (draft)

A decentralized, CB-style text broadcast network. Everyone shares one LoRa
channel, everyone receives everything, and all meaning (filtering, identity,
encryption) is decided by the end applications. The network defines only
**how to share the air**.

Three implementations follow this specification: the Python core
(`lora_text/`), the Kotlin core of the Android app (`android/…/core/`) and the
JavaScript core of the web app (`web/src/`). Cross-language test vectors in
`android/compat/` keep them byte-compatible.

## 1. Air profile (mandatory, identical on every node)

The profile is configurable (region presets + manual values, see README and
`lora_text/regions.py`); the table shows the default. A network *is* a profile:
nodes with different frequency/SF/BW/CR/preamble/sync word cannot hear each other.

| Parameter | Value |
|---|---|
| Frequency | 920.800 MHz (outside the lab's AS923-2 LoRaWAN channels; confirm vs. regulation) |
| Modulation | LoRa SF7, BW 125 kHz, CR 4/5 |
| Header / CRC | explicit header, CRC on |
| Preamble | 8 symbols |
| Sync word | 0x1424 (private; not LoRaWAN) |
| IQ | standard (not inverted) |
| TX power | 14 dBm (pending confirmation of the Vietnamese limit) |
| LoRaWAN framing | not used |

## 2. Airtime contract (mandatory)

| Rule | Value |
|---|---|
| Max application payload | 100 bytes per packet at the default profile (≈174 ms on air); for other profiles min(100, LA66 firmware limit for the SF − 1, largest payload within the dwell limit) |
| Duty cycle | per device, rolling hour, from the region (default 1 %) |
| Rate | average ≤ 1 packet / 10 s per node, bursts ≤ 3 (token bucket) |
| Random backoff | 0–2 s uniform delay before every transmission |
| Listen-before-talk | when the firmware supports it (not in LA66 P2P v1.2.4) |
| ACKs / retransmissions | none (broadcast) |
| Forwarding | none in phase 1 |

Channel capacity guide: at ~100 ms per message, ~10 % channel load is about
**one message per second for everyone within radio range**.

## 3. Frame

```
+-----------+---------------------------+
| mode (1B) | body (0..99 B)            |
+-----------+---------------------------+
mode = [version:2][type:3][hop:2][reserved:1]
```

| type | name | body | status |
|---|---|---|---|
| 0 | PLAIN | UTF-8 text | v0.1 |
| 1 | SIGNED | hint + text + rolling MAC tag (5 B), `version` = 1 | see §5 |
| 2 | SEALED | hint + ciphertext + rolling MAC tag (5 B), `version` = 1 | see §5 |
| 3–7 | — | — | reserved |

`version` = 0. `hop` = 0 (reserved for phase-2 repeaters). Receivers must
treat unknown versions/types as opaque bytes. Applications are free to put
their own formats inside a body; the network never inspects it.

## 4. Plain-text conventions (recommended, not enforced)

- Text is UTF-8, **NFC-normalised** (Vietnamese diacritics precomposed = fewer bytes).
- Optional sender prefix: `callsign: message` (a claim, not verified).
- Topics and mentions are tokens in the text: `#topic`, `@name`.
- Display rule: valid PLAIN → show text; anything else → `[N B undecoded]`
  (or `[N B sealed]` / `[N B signed]` for reserved types).
- Filtering is local: by `#topic`, `@name` (callsign or mention), or substring.

## 5. SIGNED and SEALED frames (v0.2)

```
+-----------+-----------+-------------------------+---------+
| mode (1B) | hint (1B) | body                    | tag (5B)|
+-----------+-----------+-------------------------+---------+
mode version = 1 (PLAIN frames stay version 0; version-0 SIGNED/SEALED from
v0.2/v0.3 are no longer accepted)
SIGNED: body = UTF-8 text (anyone can read; key holders can verify)
SEALED: body = ChaCha20(text) (only key holders can read)
```

No sender ID on air; `hint` = low byte of the counter. Max text: 93 bytes
(at the default profile).

**Identity and handles (off air).** Each identity has an Ed25519 key pair
(RFC 8032). `fingerprint = base32(SHA-256(pub))[:12]` shown as `K7QX-M2PA-9TRE`;
`TAG` = its first 4 characters; **handle** = `nick#TAG`. Nicks are not unique;
keys are. The 4-character tag separates honest users with the same nick but can
be ground by an attacker in seconds, so trust comes from holding the full key.
Identity string: `lid1:<handle>:<base32 pub>`.

**Streams.** Every key is a *stream* owned by one sender.
- *Own stream*: random 128-bit secret; followers receive it as
  `lts3:<owner handle>:<owner pub>:<label>:<secret>:<sig>` with
  `sig = Ed25519_sign(owner, "lts3:<handle>:<pub>:<label>:<secret>")`. Receivers
  verify the signature; a string that fails is refused. Old `lts2:` (no
  signature) and `lts1:` strings are accepted but the owner is shown unverified.
- *Group*: random 128-bit group secret shared as
  `ltg2:<label>:<secret>:<handle>=<pub>,…` (old `ltg1:` accepted). Each member
  sends on a member stream
  `secret_m = HMAC-SHA256(group_secret, "lora-text/member/" + lower(handle))[:16]`,
  so members never share a nonce. Handles make collisions practically impossible.

**Join announcement.** When a member joins, it broadcasts one SEALED (version 1)
frame under the group control keys `ctrl_enc/ctrl_mac = HMAC-SHA256(group_secret, "lora-text/control/enc|mac")`:
`[mode][nonce 8][ChaCha20(ctrl_enc, nonce‖0000, "J" ‖ minute(4) ‖ pub(32) ‖ handle)][tag 5]`
(≈ 50–70 B, no hint byte), `tag = HMAC-SHA256(ctrl_mac, mode ‖ nonce ‖ ct)[:5]`. Receivers
accept it if fresh (±10 min), the nonce is unseen and the handle's TAG matches
`pub`; a known handle announcing a different key is rejected.

**Direct (one-to-one) messages (v0.7).** Two people who hold each other's
identity string (`lid1:`) derive a pair secret with Diffie–Hellman on their
Ed25519 identity keys, on the Edwards curve itself (no separate key type):
```
S      = compress(a_me · A_peer)            a = clamped secret scalar of the identity seed
                                            refused: A_peer not a point, or S the neutral point
pair   = HMAC-SHA256(S, "lora-text/pair/" + h1 + "|" + h2)[:16]
                                            h1, h2 = both handles, lower case, sorted
secret = HMAC-SHA256(pair, "lora-text/member/" + lower(sender handle))[:16]
```
One `secret` per direction, used exactly like a group member's stream: an
ordinary SEALED frame (version 1, hint byte, 5-byte tag), so on air a direct
message looks like any other sealed packet. Counters are kept per contact
(`dm_out`, `dm_in`). The decrypted body starts with a type byte:

| byte | meaning | rest |
|---|---|---|
| `0x01` | message | UTF-8 text (max payload − 8 bytes) |
| `0x02` | message sent again | the same text; a receiver that showed it in the last 30 min doesn't show it twice |
| `0x03` | receipt | 1–4 ids, 4 bytes each (big-endian): `id = i & 0xFFFFFFFF` of the confirmed messages |

**Receipts.** A receiver that decodes a message (`0x01`/`0x02`) sends a receipt
1–4 s later (random; ids that arrive meanwhile share one receipt). Receipts are
never confirmed. The sender shows *sent* → *delivered* when a receipt names the
message's id, or *not confirmed* after 30 s; resending (`0x02`) uses a new
counter and either copy's receipt counts. Receipts reveal to the other person
(only) that the receiving device was in range and unlocked.

**Verification shown to users.** ✓ only for posts on a followed stream whose
`lts3` string carried a valid owner signature; such posts may show the
user's contact name for that key. Group messages are shown as `handle ◆`
(group badge), never with ✓ or a contact name: every member holds the group
secret and can send as any member, and anyone in the group can announce any
handle/key.
PLAIN messages are never verified: `nick: text` is a claim (optionally
`nick#TAG: text` as an app setting; still a claim).

**Keys per stream.** `enc = HMAC-SHA256(secret, "lora-text/enc")`,
`mac = HMAC-SHA256(secret, "lora-text/mac")`.

**Counter `i` (never transmitted).**
- Sender: `i = max(last_i + 1, floor, minute << 8)` where
  `minute = (unix_time − 1767225600) // 60` and `floor = (minute_at_start + 1) << 8`.
  `last_i` is persisted after every send.
- Receiver: tries only counters whose low byte equals `hint`: those in
  `last_i+1 … last_i+1024`, then `(minute+d) << 8 | hint` for `d ∈ [−5, +6]`
  (≈ 7–16 candidates per stream), accepting only `i > last_i` (replay
  protection) and only if the body decodes as strict UTF-8. Clocks must agree
  within about ±5 minutes.

**Tag and cipher.**
```
hint  = i & 0xFF
tag   = HMAC-SHA256(mac, mode ‖ i (8 B big-endian) ‖ body)[:5]
nonce = 00 00 00 00 ‖ i (8 B big-endian)
body  = ChaCha20(enc, nonce, block counter 1) XOR text      (SEALED)
```
Encrypt-then-MAC. A random or forged packet matches one stream with
probability ≈ candidates/2⁴⁰ (≈ 10⁻¹¹), i.e. ≈ 2·10⁻⁹ per packet for a receiver
holding 250 streams, before the UTF-8 check; the channel carries about one
packet per second. Trial decryption costs ≈ 25 µs per stream per packet.

Direct messages are authenticated: only the two key holders can compute the
pair secret, so a direct message is shown with the contact's own name.

**Known limits.** Group members can impersonate each other (shared group
secret; hence the ◆ badge instead of ✓). Followers of a stream could forge that stream's posts. Fixing this
needs asymmetric signatures or TESLA-style delayed keys (future). Radio
direction-finding can still locate transmitters.

## 5a. TextCast numbers and number lookup (v0.7)

A **TextCast number** is a 10-digit name for an identity key, computed (no server):

    d      = scrypt(pub, salt = "textcast/number/v1", N = 2^14, r = 8, p = 1, 8 bytes)
    body   = 100000000 + (d as big-endian integer) mod 900000000     (9 digits)
    number = body ‖ Luhn check digit of body                         (10 digits)

Shown as `512 384 9076`. Typing accepts spaces, `-`, `.`, `()`; a number with
a leading 0 or a wrong check digit is refused before anything is sent.

**Frames** (mode byte: version 1, type DIRECTORY = 3, i.e. `0x58`; no
encryption, no MAC - the answer is a public key, which is public):

| frame | bytes | content |
|---|---|---|
| lookup | 7 | `0x58` `'Q'` number (5 bytes, big-endian integer) |
| answer | 4 + chunk | `0x58` `'A'` answer-id (random byte) `part<<4 ‖ parts` chunk |

The answer's chunks, in part order, are `pub (32) ‖ nick (UTF-8)`. Each answer
frame is at most the profile's payload limit, so the answer takes one frame at
SF7/SF8 and two or three at SF10 (23 bytes). Rules:

- Answer a lookup only for your own number, only if "find me by number" is on
  (default), after a random 0.3-1.5 s, at most once per 3 s.
- The asker sends the lookup, again after 4 s if nobody answered, and gives up
  after 10 s ("not found"). It accepts an answer only if `number(pub)` equals
  the number it asked for. Two different keys for one number = **conflict**:
  nothing is saved and the user is warned.
- A contact saved from an answer is marked "added by number"; one saved from an
  identity string (`lid1:`, QR) is "verified". Saving a key whose number another
  saved contact already has is refused.
- Lookup and answer frames are logged as kind `lookup` and never shown as messages.

Old apps see these frames as `[N B sealed]` in *Other*.

## 5b. Local storage (not on air)

Identity file `keys-<nick>.lkey` and backups `<nick>.ltid` share one format:
`"LTID1" | salt 16 | nonce 12 | ChaCha20(enc, nonce) XOR json | HMAC-SHA256(mac, …)[:16]`
with `enc ‖ mac = scrypt(passphrase, salt, n=2^15, r=8, p=1)`. Message text of
sealed/signed/join messages in the log is encrypted with keys derived from a
random `log_secret` kept inside the identity (`"enc1:" + base64(...)`); for
those messages the channel and sender columns are encrypted the same way.
Receive counters are saved at most every 30 s and on lock/exit; send counters
are saved before every transmission.

## 6. Reference airtime (SF7/BW125/CR4/5, incl. firmware group byte)

| Frame bytes | 11 | 21 | 41 | 61 | 81 | 100 |
|---|---|---|---|---|---|---|
| Airtime (ms) | 41 | 57 | 87 | 118 | 144 | 174 |

## 7. LA66 P2P firmware v1.2.4 notes (bench-verified 2026-11-21)

- `AT+SYNCWORD=0` → 0x1424 private; `AT+HEADER=0` → explicit (manual is inconsistent).
- Default `AT+RXMOD=65535,2` auto-ACKs every packet — **must** be `65535,0`.
- RX is printed unsolicited (`rxDone` / `Data: (HEX:) …` / `Rssi= …`); the Rssi
  line may be truncated. `AT+RECV=0` returns `<hex> ,<rssi>,<snr>` and clears.
- One TX at a time: a second `AT+SEND` before `txDone` is silently dropped.
- Firmware adds 1 group byte on air (stripped on RX). No CAD/LBT command.
