# Contributing

This project was built with substantial AI assistance (Claude, by Anthropic).
Contributions from humans, and careful review especially, are very welcome.

## Run the tests

```bash
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests
```

The tests use a simulated LA66 (`FakeLA66` in `tests/test_core.py`), so no
hardware is needed. The Textual UI (`chat.py`) is not covered by tests. Please
describe how you tested UI changes manually.

## Where things live

- `lora_text/` is the core: protocol, crypto, keys, driver, node. It has no UI
  code and no third-party dependencies apart from `pyserial`.
- `chat.py` and `simple_chat.py` are user interfaces on top of `lora_text.node.Node`.
- `docs/SPEC.md` is the protocol. **Any change to bytes on air or file formats
  must update SPEC.md**, and should bump the version bits in the mode byte if
  old and new nodes can no longer interoperate.

## Good first areas

- Drivers for other radios (see [docs/HARDWARE.md](docs/HARDWARE.md))
- UI tests (Textual's `pilot` testing API)
- A review of `crypto.py`, `vault.py` and `ed25519.py`
- Packaging (a pip package, or a single executable)

## Guidelines

- Keep the airtime contract (payload limit, rate limit, duty cycle) intact. The
  channel is shared by everyone.
- Never add automatic acknowledgements or repeating without discussing the
  airtime cost.
- Region presets must stay conservative. They are not legal advice.
