# Hardware

TextCast works with LoRa modules that can send and receive **raw LoRa packets**
over a serial (USB) connection. Modules differ in their AT commands, so each
kind is described by a **hardware profile**. Everything above the radio
(protocol, encryption, identities, apps) is the same for all modules, and nodes
with different modules talk to each other when their radio settings match.

## Supported modules

LoRa modules all offer the same functions, but their AT commands differ. A
**hardware profile** tells the app which commands to use. Modules running
different profiles can talk to each other as long as the radio settings match.

| Profile | Module | Status |
|---|---|---|
| `dragino-la66-p2p` | Dragino LA66 / LA66 USB, P2P firmware v1.2.4 | tested (default) |
| `rak3172-rui3` | RAKwireless RAK3172 / RAK3272 / RAK3372, RUI3 firmware (115200 baud) | from vendor docs, untested |
| `seeed-wio-e5` | Seeed Wio-E5 / LoRa-E5, factory AT firmware, TEST mode | from vendor docs, untested |
| *custom* | any module with raw LoRa P2P over AT commands | your JSON file |

**Not supported:** Reyax RYLR896/998 (the firmware adds its own address header,
so no raw LoRa packets) and Ebyte E22/E32 (transparent UART with the vendor's
own packet format).

Choose the module in **Settings** (desktop login panel, F7 → Module, or Settings in the Android app), or on the desktop:
```bash
python3 settings.py hardware list                 # profiles, incl. custom ones
python3 settings.py set hardware rak3172-rui3     # saved in config.json
python3 chat.py --hardware seeed-wio-e5           # one session only
```

## Your own module: a custom profile

```bash
python3 settings.py hardware new hardware/my-module.json   # starts from the RAK profile
# edit the file, then:
python3 settings.py set hardware hardware/my-module.json
```
Profiles in the `hardware/` folder also appear in the Module list. On Android, use
Settings → *Import a module profile (.json)*. A profile is JSON:

| Key | Meaning |
|---|---|
| `baud`, `line_ending`, `cmd_delay` | serial speed, `"\r\n"`, pause after each setup command (s) |
| `init` | commands that set mode, frequency, SF, bandwidth, CR, power, sync word |
| `rx_start` | commands that enter continuous receive |
| `pre_send` / `send` / `post_send` | sequence for one transmission; `send` must contain `{hex}` |
| `tx_done` / `tx_error` | regex for "transmission finished" / "failed" |
| `rx` | regexes for received packets: named groups `payload` (hex), `rssi`, `snr`. A regex with only rssi/snr is a line that comes before the payload line |
| `max_payload` | firmware payload limit, e.g. `{"*": 255}` or per SF `{"7": 230, "9": 123}` |
| `firmware_overhead` | bytes the firmware adds on air (LA66: 1, most: 0) |

Fields usable in commands: `{freq_mhz}` `{freq_hz}` `{sf}` `{bw_khz}` `{bw_hz}`
`{bw_code}` `{cr}` (1–4) `{cr_index}` (0–3) `{cr_denom}` (5–8) `{preamble}`
`{power}` `{sync16}` (1424) `{sync8}` (12) `{public}` (OFF) `{public01}` (0)
`{hex}` `{len}`.

Any module must send **raw LoRa** with explicit header, CRC on, standard IQ,
the **private sync word** (0x1424 / 0x12) and no automatic acknowledgements,
otherwise it cannot hear or be heard by the other nodes. If a module works,
please share its profile.

## Using a module with a phone

The Android app talks to the module through **USB OTG**. It supports the common
USB-serial chips (WCH CH340/CH9102, Silicon Labs CP210x, FTDI, Prolific PL2303)
and standard USB serial (CDC ACM, e.g. STM32 boards) through
[usb-serial-for-android](https://github.com/mik3y/usb-serial-for-android).

- The phone powers the module. A LoRa stick draws little current, but long
  sessions use battery; an OTG adapter with power pass-through lets you charge
  at the same time.
- Keep the stick and its antenna away from the phone body for better range: a
  short USB extension cable helps.
- iPhones cannot use USB-serial modules. They would need a Bluetooth bridge
  (not built yet).

## Dragino LA66 (tested setup)

| Item | Detail |
|---|---|
| Radio | Dragino **LA66 USB LoRaWAN Adapter** (ASR6601 chip: SX1262-class LoRa radio plus MCU) |
| Firmware | Dragino **LA66 P2P firmware v1.2.4**: `LA66_P2P_v1.2.4_application_withbootloder.bin` |
| Host | laptop running macOS, Windows or Linux, Python 3.10+ |
| Connection | USB serial, 9600 baud |
| Band tested | AS923-2 (Vietnam), 920.8 MHz |

The LA66 ships with **LoRaWAN** firmware. That does not work here: LoRaWAN
nodes cannot hear each other. Flash the **P2P** firmware first.

## Flashing the P2P firmware (LA66 USB)

1. Download the P2P firmware from Dragino's LA66 "Peer to Peer firmware"
   instructions page.
2. Use the `…_withbootloder.bin` file (the full image, written from address
   0x08000000) with Dragino's LA66 USB upgrade method/tool. The
   `…_application.bin` file is only for devices that already have Dragino's
   bootloader.
3. Reset the device. The banner should show `LA66 P2P Firmware v1.2.4`.

You can always flash the LoRaWAN firmware back.

## What the app does with the LA66

It sends these commands at start whenever the LA66 settings differ from the
active radio profile, then checks the reset banner:

```
AT+RXMOD=65535,0        receive always on, NO auto-ACK  (the factory default auto-ACKs!)
AT+FRE=920.800,920.800  frequency (from the profile)
AT+SF=7,7  AT+BW=0,0  AT+CR=1,1  AT+CRC=1,1  AT+HEADER=0,0  AT+IQ=0,0
AT+PREAMBLE=8,8  AT+SYNCWORD=0  AT+POWER=14  AT+GROUPMOD=0,0
ATZ                     reset, then the banner is parsed and verified
```

## Firmware behaviour (verified on the bench)

- **Defaults to change:** frequency 868.1 (EU), SF12, CRC off, and
  `RXMOD=65535,2`, which auto-ACKs every packet and would flood a broadcast
  network.
- **Manual errors:** `AT+SYNCWORD=0` is **0x1424 (private)**, and
  `AT+HEADER=0` is **explicit** header. The manual contradicts itself on both.
- **Receive output is unsolicited:** `rxDone` / `Data: (HEX:) 48 65 …` /
  `Rssi= -101`. The RSSI line can be truncated, so the driver treats the data
  line as authoritative.
- `AT+RECV=0` returns `<hex> ,<rssi>,<snr>` and clears the buffer. The driver
  uses it to get SNR.
- **One transmission at a time.** A second `AT+SEND` sent before `txDone` is
  **silently dropped**. The driver queues sends and waits for `txDone` and
  `RX on freq`.
- The payload is delivered byte-for-byte. The firmware adds and strips one
  group byte on air.
- Maximum payload: 230 B at SF7/8, 123 B at SF9, 59 B at SF10–12.
- **No channel-activity detection**, so no listen-before-talk.

## Practical notes

- The tested setup measured weak RSSI (about −100 dBm) with dongles close
  together. Check antennas, and keep dongles away from laptops (USB extension
  cable). Two dongles side by side can also overload each other's receivers.
- On macOS each port appears as both `/dev/tty.*` and `/dev/cu.*`. The app uses
  `cu`.
- On Windows the port is `COMx`. Install the USB-serial driver named in Device
  Manager (CP210x or CH340) if it is missing.
- Several dongles on one computer work: run one app per dongle, each with its
  own identity.

## Connecting other hardware

Most modules with AT commands need only a **JSON profile** (above): the generic
driver (`lora_text/atradio.py`, and `GenericAtRadio` in the Android app's
`core/Radio.kt`) runs its commands. Write a driver of your own only when a
module needs logic that a profile cannot express (like the LA66, whose driver
also reads back SNR and verifies the settings from its reset banner).

A driver class has the interface below; pass it to
`Node(radio_factory=YourRadio)`. The Android app has the same structure in
Kotlin (`Radio` base class, `makeRadio()` in `core/Radio.kt`).

### Driver interface

```python
class YourRadio:
    def __init__(self, port, baud, on_packet, on_tx, on_log, fetch_snr, serial_obj=None): ...
    # callbacks (call them from your own threads):
    #   on_packet(Packet(payload: bytes, rssi: int|None, snr: int|None, t: float))
    #   on_tx(TxResult(job_id: int, ok: bool, airtime_ms: float, detail: str))
    #   on_log(text: str)
    banner: dict          # at least {"Firmware": "..."} after reset_and_check()
    limiter: RateLimiter  # from lora_text.protocol (rate limit of the contract)
    meter: AirtimeMeter   # own airtime, for the duty-cycle cap
    def open(self) -> None: ...
    def close(self) -> None: ...
    def send(self, frame: bytes) -> int: ...          # queue; returns a job id
    def queued(self) -> int: ...
    def configure(self) -> list[str]: ...             # apply PROFILE; return problems ([] = ok)
    def reset_and_check(self) -> list[str]: ...       # verify settings without changing them
    def duty_wait(self, frame_len: int) -> float: ... # seconds until the duty budget allows a frame
    def pause(self) -> None: ...                      # stop starting new TX (reconfiguration)
    def resume(self) -> None: ...
```

`LA66Radio` is the reference implementation. Its unit tests use a simulated
device (`tests/test_core.py`, `FakeLA66`). Write a similar fake for your radio.

### Requirements for any radio

Every node must match on all of these, or they will not hear each other:

| Setting | Value |
|---|---|
| Modulation | LoRa, explicit header, CRC on |
| Frequency / SF / bandwidth / coding rate / preamble | from the active profile (`lora_text/protocol.py`, `PROFILE`) |
| Sync word | private **0x1424** (SX126x) / **0x12** (SX127x) |
| IQ | standard (not inverted), for both TX and RX |
| Payload | delivered byte-exact. If your firmware adds bytes on air, set `firmware_overhead` in its profile |

Other requirements:
- **Half-duplex:** send one packet at a time and report completion.
- **Never auto-acknowledge** received packets.

### Examples of what a new driver involves

- **Other single-channel modules** with AT commands: a JSON profile is usually
  enough. Boards with custom firmware (Heltec, SX127x + microcontroller) can
  implement a simple AT protocol and use a profile too.
- **iPhones:** a Bluetooth LE bridge (e.g. ESP32 + LA66 over UART) with an app
  speaking BLE. Android already works over USB OTG.
- **Custom LA66 firmware** (ASR6601 SDK): could add listen-before-talk, a rate
  limit enforced in firmware, and standalone operation. The driver interface
  stays the same.
