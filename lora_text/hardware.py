"""
Hardware profiles: how to talk to a particular LoRa module.

All modules offer the same functions (set frequency/SF/BW/CR/power, send bytes,
report received packets) but with different AT commands and output formats.
A profile describes those commands; the generic AT driver (atradio.py) runs
them. The Dragino LA66 keeps its dedicated, bench-tested driver (la66.py).

Custom profiles are JSON files (e.g. hardware/my-module.json), see
`template()` and the README. Command templates may use:

    {freq_mhz} 920.800   {freq_hz} 920800000   {sf} 7
    {bw_khz} 125         {bw_hz} 125000         {bw_code} LA66 code (0=125, 1=250, 2=500, ...)
    {cr} 1..4 (4/5..4/8) {cr_index} 0..3        {cr_denom} 5..8
    {preamble} 8         {power} 14
    {sync16} 1424        {sync8} 12             {public} OFF   {public01} 0
    {hex} payload in hex {len} payload length in bytes

Receive patterns are regular expressions with named groups: `payload` (hex),
`rssi`, `snr`. A pattern may capture only rssi/snr (a "meta" line that comes
before the payload line, as on the Wio-E5).
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .protocol import AirProfile
from .regions import BW_CODES

DEFAULT_HARDWARE = "dragino-la66-p2p"
HARDWARE_DIR = "hardware"           # custom profiles: hardware/*.json


@dataclass
class HardwareProfile:
    name: str
    description: str = ""
    driver: str = "at"               # "at" (generic) or "la66" (built-in driver)
    baud: int = 9600
    line_ending: str = "\r\n"
    cmd_delay: float = 0.3           # pause after each configuration command (s)
    init: list[str] = field(default_factory=list)       # applied by "configure"
    rx_start: list[str] = field(default_factory=list)   # (re)enter continuous receive
    send: str = ""                   # e.g. "AT+PSEND={hex}"
    pre_send: list[str] = field(default_factory=list)   # e.g. stop RX before TX
    post_send: list[str] = field(default_factory=list)  # e.g. restart RX after TX
    tx_done: str = ""                # regex seen when the transmission finished
    tx_error: str = ""               # regex for a failed transmission (optional)
    rx: list[str] = field(default_factory=list)         # receive regexes (see above)
    hex_upper: bool = True
    max_payload: dict = field(default_factory=dict)     # {"7": 230, ...} or {"*": 255}
    firmware_overhead: int = 0       # bytes the firmware adds on air
    sync_word: bool = True           # can be set to the private LoRa sync word
    verified: bool = False           # tested on real hardware with TextCast
    notes: str = ""

    def payload_limit(self, sf: int) -> int:
        m = self.max_payload
        return int(m.get(str(sf), m.get("*", 255)))

    def validate(self) -> list[str]:
        problems = []
        if self.driver not in ("at", "la66"):
            problems.append("driver must be 'at' or 'la66'")
        if self.driver == "at":
            if "{hex}" not in self.send:
                problems.append("'send' must contain {hex}")
            if not self.tx_done:
                problems.append("'tx_done' regex is required")
            if not any("?P<payload>" in r for r in self.rx):
                problems.append("one 'rx' regex must have a (?P<payload>...) group")
            for r in self.rx + [self.tx_done] + ([self.tx_error] if self.tx_error else []):
                try:
                    re.compile(r)
                except re.error as e:
                    problems.append(f"bad regex {r!r}: {e}")
            try:
                render_all(self.init + self.rx_start + self.pre_send + self.post_send,
                           AirProfile())
                render(self.send, AirProfile(), b"\x00")
            except (KeyError, IndexError, ValueError) as e:
                problems.append(f"unknown template field {e}")
        return problems

    def to_json(self) -> str:
        d = copy.deepcopy(self.__dict__)
        return json.dumps(d, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


def fields(p: AirProfile, payload: Optional[bytes] = None, hex_upper: bool = True) -> dict:
    d = {
        "freq_mhz": f"{p.freq_mhz:.3f}", "freq_hz": str(round(p.freq_mhz * 1_000_000)),
        "sf": p.sf, "bw_khz": f"{p.bw_hz / 1000:g}", "bw_hz": p.bw_hz,
        "bw_code": BW_CODES.get(p.bw_hz, 0), "cr": p.cr, "cr_index": p.cr - 1,
        "cr_denom": p.cr + 4, "preamble": p.preamble, "power": p.tx_power_dbm,
        "sync16": "1424" if p.private_sync else "3444",
        "sync8": "12" if p.private_sync else "34",
        "public": "OFF" if p.private_sync else "ON",
        "public01": 0 if p.private_sync else 1,
    }
    if payload is not None:
        h = payload.hex()
        d.update(hex=h.upper() if hex_upper else h, len=len(payload))
    return d


def render(template: str, p: AirProfile, payload: Optional[bytes] = None,
           hex_upper: bool = True) -> str:
    return template.format(**fields(p, payload, hex_upper))


def render_all(templates: list[str], p: AirProfile) -> list[str]:
    return [render(t, p) for t in templates]


# ---------------------------------------------------------------------------
# built-in profiles
# ---------------------------------------------------------------------------

BUILTIN: dict[str, dict] = {
    "dragino-la66-p2p": dict(
        description="Dragino LA66 / LA66 USB with LA66 P2P firmware v1.2.4",
        driver="la66", baud=9600, firmware_overhead=1,
        max_payload={"5": 230, "6": 230, "7": 230, "8": 230, "9": 123,
                     "10": 59, "11": 59, "12": 59},
        verified=True,
        notes="Tested with TextCast. Needs the P2P firmware, not the LoRaWAN firmware."),
    "rak3172-rui3": dict(
        description="RAKwireless RAK3172 / RAK3272 / RAK3372 with RUI3 firmware (LoRa P2P)",
        driver="at", baud=115200, cmd_delay=0.4,
        init=["AT+NWM=0",
              "AT+P2P={freq_hz}:{sf}:{bw_khz}:{cr_index}:{preamble}:{power}",
              "AT+SYNCWORD={sync16}",
              "AT+IQINVER=0",
              "AT+ENCRY=0"],
        rx_start=["AT+PRECV=0", "AT+PRECV=65534"],
        pre_send=["AT+PRECV=0"],
        send="AT+PSEND={hex}",
        post_send=["AT+PRECV=65534"],
        tx_done=r"\+EVT:TXP2P DONE",
        tx_error=r"AT_BUSY_ERROR|AT_PARAM_ERROR|AT_ERROR",
        rx=[r"\+EVT:RXP2P:(?P<rssi>-?\d+):(?P<snr>-?\d+):(?P<payload>[0-9A-Fa-f]+)"],
        max_payload={"*": 255},
        verified=False,
        notes="From RAK's RUI3 documentation; not yet tested with TextCast. RUI3 needs "
              "AT+PRECV=0 before AT+PSEND (else AT_BUSY_ERROR). Some RUI3 versions expect "
              "the bandwidth as a code (0=125) instead of kHz in AT+P2P, and the sync-word "
              "syntax may differ by version - check AT+P2P=? / AT+SYNCWORD=? and adjust a "
              "custom copy if needed."),
    "seeed-wio-e5": dict(
        description="Seeed Wio-E5 / LoRa-E5 (STM32WLE5) with the factory AT firmware, TEST mode",
        driver="at", baud=9600, cmd_delay=0.4,
        init=["AT+MODE=TEST",
              "AT+TEST=RFCFG,{freq_mhz},SF{sf},{bw_khz},{preamble},{preamble},{power},ON,OFF,{public}"],
        rx_start=["AT+TEST=RXLRPKT"],
        send='AT+TEST=TXLRPKT,"{hex}"',
        post_send=["AT+TEST=RXLRPKT"],
        tx_done=r"\+TEST: TX DONE",
        tx_error=r"\+TEST: ERROR|ERROR\(",
        rx=[r"\+TEST: LEN:\s*\d+,\s*RSSI:\s*(?P<rssi>-?\d+),\s*SNR:\s*(?P<snr>-?\d+)",
            r'\+TEST: RX "(?P<payload>[0-9A-Fa-f]*)"'],
        max_payload={"*": 255},
        verified=False,
        notes="From Seeed's LoRa-E5 AT specification; not yet tested with TextCast. NET=OFF "
              "selects the private sync word. Bandwidth 125/250/500 kHz only. Sending stops "
              "receive, so RXLRPKT is re-sent after every transmission."),
}

UNSUPPORTED = {
    "reyax-rylr": "Reyax RYLR896/RYLR998: the firmware adds its own address header and "
                  "sends ASCII; it cannot exchange raw LoRa packets with other modules.",
    "ebyte-e22/e32": "Ebyte E22/E32: transparent UART modes with the vendor's own packet "
                     "format; no raw LoRa access over AT.",
}


def template() -> HardwareProfile:
    """Starting point for a custom profile (copy of the RAK3172 profile)."""
    hp = from_dict(dict(BUILTIN["rak3172-rui3"], name="my-module"))
    hp.description = "My LoRa module (edit the commands below)"
    hp.verified = False
    hp.notes = "Custom profile."
    return hp


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


class HardwareError(ValueError):
    pass


def from_dict(d: dict) -> HardwareProfile:
    known = set(HardwareProfile.__dataclass_fields__)
    unknown = set(d) - known
    if unknown:
        raise HardwareError(f"unknown profile keys: {', '.join(sorted(unknown))}")
    if "name" not in d:
        raise HardwareError("profile needs a 'name'")
    return HardwareProfile(**d)


def custom_files(folder: str = HARDWARE_DIR) -> list[str]:
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json"))


def available(folder: str = HARDWARE_DIR) -> list[tuple[str, str]]:
    """[(id, description)] - built-ins first, then custom JSON files."""
    out = [(k, v["description"] + ("" if v.get("verified") else "  (untested)"))
           for k, v in BUILTIN.items()]
    for path in custom_files(folder):
        try:
            out.append((path, load(path).description + "  (custom)"))
        except HardwareError as e:
            out.append((path, f"invalid: {e}"))
    return out


def load(name_or_path: Optional[str]) -> HardwareProfile:
    """Built-in name, or path to a JSON profile."""
    name_or_path = name_or_path or DEFAULT_HARDWARE
    if name_or_path in BUILTIN:
        return from_dict(dict(BUILTIN[name_or_path], name=name_or_path))
    if name_or_path in UNSUPPORTED:
        raise HardwareError(UNSUPPORTED[name_or_path])
    if not os.path.exists(name_or_path):
        raise HardwareError(f"unknown hardware '{name_or_path}'. Built-in: "
                            f"{', '.join(BUILTIN)}; or give a .json profile file")
    try:
        with open(name_or_path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise HardwareError(f"{name_or_path}: {e}") from e
    hp = from_dict(d)
    problems = hp.validate()
    if problems:
        raise HardwareError(f"{name_or_path}: " + "; ".join(problems))
    return hp
