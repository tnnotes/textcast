# Contributing

This project was built with substantial AI assistance (Claude, by Anthropic).
Contributions from humans, and careful review especially, are very welcome.

## Run the tests

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests
```

The tests use simulated radios (`FakeLA66` in `tests/test_core.py`, fake RAK and
Wio-E5 modules in `tests/test_hardware.py`), so no hardware is needed. The user
interfaces are not covered by tests. Please describe how you tested UI changes
manually.

**Android** (Kotlin core and simulated modules):

```bash
cd android && ./gradlew testDebugUnitTest
```

The Kotlin core must stay byte-compatible with the Python core. After changing
anything on air or in a file format, regenerate the vectors and check both
directions (see [docs/RELEASING.md](docs/RELEASING.md), step 1).

## Where things live

- `lora_text/` is the core: protocol, crypto, keys, driver, node. It has no UI
  code and no third-party dependencies apart from `pyserial`.
- `chat.py` and `simple_chat.py` are user interfaces on top of `lora_text.node.Node`.
- `android/app/src/main/java/io/github/textcast/core/` is the Kotlin port of
  `lora_text/` (same names: `Protocol`, `Crypto`, `Keyring`, `Node`, …);
  `…/app/` holds the Android screens.
- `docs/SPEC.md` is the protocol. **Any change to bytes on air or file formats
  must update SPEC.md and both implementations**, and should bump the version bits in the mode byte if
  old and new nodes can no longer interoperate.

## Good first areas

- Profiles for other modules, tested on real hardware (see [docs/HARDWARE.md](docs/HARDWARE.md))
- QR codes for key strings in the Android app
- UI tests (Textual's `pilot` testing API)
- A review of `crypto.py`, `vault.py` and `ed25519.py`
- Packaging (a pip package, or a single executable)

## Guidelines

- Keep the airtime contract (payload limit, rate limit, duty cycle) intact. The
  channel is shared by everyone.
- Never add automatic acknowledgements or repeating without discussing the
  airtime cost.
- Region presets must stay conservative. They are not legal advice.
