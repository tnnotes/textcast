"""
Settings: radio profile (region preset + manual overrides) and log limits.

Precedence (lowest -> highest):
    region preset  <  config.json  <  command-line flags

config.json (next to the app, not secret):
    {
      "radio": {"region": "AS923-2", "freq_mhz": null, "sf": null, "bw_khz": null,
                "cr": null, "power_dbm": null, "preamble": null,
                "max_dwell_ms": "preset", "duty_cycle": "preset"},
      "log":   {"max_rows": 20000, "max_days": 30}
    }
null / "preset" = use the region's value.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
from typing import Any

from . import hardware as HW
from .protocol import (PROFILE, AirProfile, airtime_ms, apply_profile, effective_payload,
                       set_hardware_limits)
from .regions import BW_CODES, DEFAULT_REGION, PRESETS, bw_from_khz

CONFIG_FILE = "config.json"
MIN_PAYLOAD = 16          # below this a profile is not usable

DEFAULTS: dict[str, Any] = {
    "radio": {"region": DEFAULT_REGION, "freq_mhz": None, "sf": None, "bw_khz": None,
              "cr": None, "power_dbm": None, "preamble": None,
              "max_dwell_ms": "preset", "duty_cycle": "preset"},
    "log": {"max_rows": 20000, "max_days": 30},
    "hardware": {"type": HW.DEFAULT_HARDWARE},
    "ui": {"port": None, "nick": None},
}


class ConfigError(ValueError):
    pass


# -- file ----------------------------------------------------------------------

def load(path: str = CONFIG_FILE) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ConfigError(f"{path} is not valid JSON: {e}") from e
        for section in cfg:
            cfg[section].update(data.get(section, {}))
    return cfg


def save(cfg: dict, path: str = CONFIG_FILE) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


# -- building the profile ----------------------------------------------------------

def build_profile(radio: dict) -> AirProfile:
    region = radio.get("region") or DEFAULT_REGION
    if region not in PRESETS and region != "custom":
        raise ConfigError(f"unknown region '{region}'. Choose one of: "
                          f"{', '.join(PRESETS)} or custom")
    base = dict(PRESETS.get(region, PRESETS[DEFAULT_REGION]))
    base.pop("note", None)
    p = dataclasses.replace(AirProfile(), name=region, **base)

    def given(key):
        v = radio.get(key)
        return v is not None and v != "preset"

    try:
        if given("freq_mhz"):
            p.freq_mhz = float(radio["freq_mhz"])
        if given("sf"):
            p.sf = int(radio["sf"])
        if given("bw_khz"):
            p.bw_hz = bw_from_khz(radio["bw_khz"])
        if given("cr"):
            p.cr = int(radio["cr"])
        if given("power_dbm"):
            p.tx_power_dbm = int(radio["power_dbm"])
        if given("preamble"):
            p.preamble = int(radio["preamble"])
        if given("max_dwell_ms"):
            v = radio["max_dwell_ms"]
            p.max_dwell_ms = None if v in (0, "none", "off") else float(v)
        if given("duty_cycle"):
            v = radio["duty_cycle"]
            p.duty_cycle = None if v in (0, "none", "off") else float(v)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"bad radio setting: {e}") from e

    if region != "custom":
        ref = dataclasses.replace(AirProfile(), name=region,
                                  **{k: v for k, v in PRESETS[region].items() if k != "note"})
        fields = ("freq_mhz", "sf", "bw_hz", "cr", "tx_power_dbm", "preamble",
                  "max_dwell_ms", "duty_cycle")
        if any(getattr(p, f) != getattr(ref, f) for f in fields):
            p.name = f"{region}*"        # * = preset with manual changes
    validate(p)
    p.max_payload = effective_payload(p)
    if p.max_payload < MIN_PAYLOAD:
        raise ConfigError(
            f"SF{p.sf} at {p.bw_hz / 1000:g} kHz leaves only {p.max_payload} B per message "
            f"under the {p.max_dwell_ms:g} ms dwell limit. Use a lower SF or a wider "
            f"bandwidth, or remove the dwell limit if your region allows it.")
    return p


def validate(p: AirProfile) -> None:
    if not 137.0 <= p.freq_mhz <= 1020.0:
        raise ConfigError("frequency must be 137-1020 MHz (SX1262 range)")
    if not 5 <= p.sf <= 12:
        raise ConfigError("spreading factor must be 5-12")
    if p.bw_hz not in BW_CODES:
        raise ConfigError(f"bandwidth must be one of {sorted(b / 1000 for b in BW_CODES)} kHz")
    if not 1 <= p.cr <= 4:
        raise ConfigError("coding rate must be 1-4 (4/5 .. 4/8)")
    if not 0 <= p.tx_power_dbm <= 22:
        raise ConfigError("TX power must be 0-22 dBm (LA66 range)")
    if not 6 <= p.preamble <= 65535:
        raise ConfigError("preamble must be 6-65535 symbols")
    if p.duty_cycle is not None and not 0 < p.duty_cycle <= 1:
        raise ConfigError("duty cycle must be between 0 and 1 (e.g. 0.01 = 1 %)")


def profile_fields(p: AirProfile) -> dict:
    """Profile as the editable values shown in the settings panel."""
    return {"region": p.name.rstrip("*"), "freq_mhz": p.freq_mhz, "sf": p.sf,
            "bw_khz": p.bw_hz / 1000, "cr": p.cr, "power_dbm": p.tx_power_dbm,
            "preamble": p.preamble,
            "max_dwell_ms": p.max_dwell_ms if p.max_dwell_ms else "none",
            "duty_cycle": p.duty_cycle if p.duty_cycle else "none"}


def preset_fields(region: str) -> dict:
    return profile_fields(build_profile({"region": region}))


def describe(p: AirProfile) -> list[str]:
    note = PRESETS.get(p.name.rstrip("*"), {}).get("note", "manual settings")
    return [
        f"profile   {p.name}  ({note})",
        f"radio     {p.freq_mhz:.3f} MHz  SF{p.sf}  BW {p.bw_hz / 1000:g} kHz  CR 4/{p.cr + 4}"
        f"  preamble {p.preamble}  {p.tx_power_dbm} dBm  sync {'private' if p.private_sync else 'public'}",
        f"limits    max {p.max_payload} B per message "
        f"({airtime_ms(p.max_payload, p):.0f} ms on air)"
        f" | dwell {'none' if not p.max_dwell_ms else f'{p.max_dwell_ms:g} ms'}"
        f" | duty cycle {'none' if not p.duty_cycle else f'{p.duty_cycle * 100:g} %'}"
        f" | rate {p.rate_per_s * 60:g}/min, burst {p.burst}",
    ]


# -- command line -------------------------------------------------------------------

def activate_hardware(name_or_path: str | None) -> HW.HardwareProfile:
    """Load a hardware profile and apply its payload limits to the active radio
    profile. Raises ConfigError if unknown/invalid or if the profile becomes unusable."""
    try:
        hw = HW.load(name_or_path)
    except HW.HardwareError as e:
        raise ConfigError(str(e)) from e
    set_hardware_limits(hw.max_payload or {"*": 255}, hw.firmware_overhead)
    p = dataclasses.replace(PROFILE)
    p.max_payload = effective_payload(p)
    if p.max_payload < MIN_PAYLOAD:
        raise ConfigError(f"{hw.name} allows only {p.max_payload} B per message with the "
                          "current radio settings")
    apply_profile(p)
    return hw


def add_cli_args(ap: argparse.ArgumentParser) -> None:
    hg = ap.add_argument_group("hardware")
    hg.add_argument("--hardware", help=f"module profile: {', '.join(HW.BUILTIN)} "
                                       "or a .json profile file")
    g = ap.add_argument_group("radio profile (overrides config.json for this run)")
    g.add_argument("--region", help=f"preset: {', '.join(PRESETS)} or custom")
    g.add_argument("--freq", type=float, help="frequency in MHz, e.g. 920.8")
    g.add_argument("--sf", type=int, help="spreading factor 5-12")
    g.add_argument("--bw", type=float, help="bandwidth in kHz: 125, 250, 500, 62.5, ...")
    g.add_argument("--cr", type=int, help="coding rate 1-4 (4/5 .. 4/8)")
    g.add_argument("--power", type=int, help="TX power in dBm (0-22)")
    g.add_argument("--preamble", type=int, help="preamble length in symbols")
    g.add_argument("--config", default=CONFIG_FILE, help="settings file (default config.json)")
    lg = ap.add_argument_group("log size")
    lg.add_argument("--log-max-rows", type=int, help="keep at most N messages (0 = unlimited)")
    lg.add_argument("--log-max-days", type=float, help="delete messages older than N days (0 = never)")


def from_args(args) -> tuple[AirProfile, dict]:
    """Merge config.json and command-line flags, activate the profile.
    Returns (profile, log settings)."""
    cfg = load(getattr(args, "config", CONFIG_FILE))
    r = cfg["radio"]
    if getattr(args, "region", None) and args.region != r.get("region"):
        r.update({k: None for k in ("freq_mhz", "sf", "bw_khz", "cr", "power_dbm", "preamble")})
        r.update(max_dwell_ms="preset", duty_cycle="preset", region=args.region)
    for flag, key in (("freq", "freq_mhz"), ("sf", "sf"), ("bw", "bw_khz"), ("cr", "cr"),
                      ("power", "power_dbm"), ("preamble", "preamble")):
        v = getattr(args, flag, None)
        if v is not None:
            r[key] = v
    hw_name = getattr(args, "hardware", None) or cfg["hardware"].get("type")
    try:
        hw = HW.load(hw_name)
    except HW.HardwareError as e:
        raise ConfigError(str(e)) from e
    set_hardware_limits(hw.max_payload or {"*": 255}, hw.firmware_overhead)
    try:
        args.hw = hw
    except AttributeError:
        pass
    log = cfg["log"]
    if getattr(args, "log_max_rows", None) is not None:
        log["max_rows"] = args.log_max_rows
    if getattr(args, "log_max_days", None) is not None:
        log["max_days"] = args.log_max_days
    p = build_profile(r)
    apply_profile(p)
    return PROFILE, log


def log_limits_text(log: dict) -> str:
    rows = log.get("max_rows") or 0
    days = log.get("max_days") or 0
    return (f"log keeps {'all' if not rows else f'the last {rows:,}'} messages"
            f"{'' if not days else f', up to {days:g} days'}")

