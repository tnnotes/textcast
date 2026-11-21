# Hardware

## Tested setup

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

The app talks to the radio through one driver, `lora_text/la66.py`
(`LA66Radio`). Everything else (protocol, encryption, identities, UI) is
independent of the hardware. To support another radio, write a driver class
with the same interface and pass it to `Node(radio_factory=YourRadio)`.

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
| Payload | delivered byte-exact. If your firmware adds bytes on air, set `FIRMWARE_OVERHEAD` accordingly |

Other requirements:
- **Half-duplex:** send one packet at a time and report completion.
- **Never auto-acknowledge** received packets.

### Examples of what a new driver involves

- **Other single-channel modules** (RAK, Heltec, SX127x boards with custom
  firmware): a serial or USB protocol of your own, or AT commands. Same
  interface as above.
- **Multi-channel gateways** (SX1302, e.g. Dragino LPS8v2):
  - The driver talks to the Semtech UDP packet forwarder: it receives `rxpk`
    and sends `txpk` with `"ipol": false`.
  - The gateway must use the private sync word (`lorawan_public: false`) and
    include the profile's frequency and SF in its channel plan.
  - The gateway then stops serving LoRaWAN, and cannot receive while
    transmitting.
- **Phones:** a Bluetooth LE bridge (e.g. ESP32 + LA66 over UART) with a
  driver or app speaking BLE. iOS cannot use USB-serial adapters.
- **Custom LA66 firmware** (ASR6601 SDK): could add listen-before-talk, a rate
  limit enforced in firmware, and standalone operation. The driver interface
  stays the same.
