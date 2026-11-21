# Scaling

What limits this design as it grows, and what would have to change.

## Capacity of one channel

| Quantity (SF7, 125 kHz, ~50-byte message) | Value |
|---|---|
| Airtime per message | ~100 ms |
| Usable load without listen-before-talk | ~10% |
| **Capacity per channel, per area in radio range** | **~1 msg/s, ~86,000 msgs/day** |
| Default rate limit per device | average 1 message per 10 s, bursts of 3 |
| Default duty-cycle cap (AS923) | 1% = 36 s of airtime per hour |

An SF7 cell in a city covers roughly 1–3 km. With 5,000 active users in one
cell, that is about 17 messages per user per day, combined.

**Range vs capacity:** higher SF or more power reaches further, but each message
takes longer on air and more people share the channel. SF12 has about 1/30 of
SF7's capacity.

## Limits by layer

| Layer | Limit | Possible direction |
|---|---|---|
| Physics | one shared channel per area | small cells, frequency reuse, multi-channel community receivers, listen-before-talk in firmware |
| Reach | single hop | interest-scoped bridges between cells (radio, Wi-Fi or IP); repeaters cost airtime |
| Abuse | free transmission; flooding and jamming | receiver-side filtering and web of trust; firmware limits only bind honest hardware |
| Keys | shared group secrets; no removal; receivers try every member key | group re-keying, signed posts for popular streams |
| Naming | no global namespace (by design) | full-key trust, contact books, social introduction |
| Privacy | type, size and timing visible; RF location | partly inherent to radio |
| Reliability | no ACKs, no history | ACKs cost airtime; history needs storage somewhere |
| Governance | the spec must evolve compatibly | versioned spec plus test suite; the mode byte has version bits |

## Measured costs (current implementation)

- Checking a packet against every key you hold: about 25 µs per stream (6 ms
  for 250 streams).
- A false tag match: about 2×10⁻⁹ per packet with 250 streams, before the UTF-8
  check.

## Summary

The current system is a solid single **cell**. An "internet for text" would be
a federation of such cells with interest-scoped bridging, multi-channel
receivers, firmware-level airtime control and better group cryptography. These
are architecture questions to settle before scaling up.
