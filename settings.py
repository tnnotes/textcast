#!/usr/bin/env python3
"""
View and change settings (config.json): radio profile and log size.

    python3 settings.py show                     current settings and limits
    python3 settings.py regions                  list region presets
    python3 settings.py set region EU868         use a preset (clears manual radio values)
    python3 settings.py set sf 9                 manual radio values:
                     freq 869.5 | bw 250 | cr 2 | power 10 | preamble 12
                     dwell 400 | none | preset    duty 0.01 | none | preset
    python3 settings.py set log.max_rows 50000   log size (0 = unlimited)
    python3 settings.py set log.max_days 7       log age  (0 = keep forever)
    python3 settings.py reset [radio|log|all]

All nodes that should talk to each other need the SAME radio settings.
The apps apply the profile to the LA66 automatically at start.
Region presets are starting points, not legal advice: check your local rules.
"""

from __future__ import annotations

import argparse
import copy
import sys

from lora_text import config as C
from lora_text.regions import PRESETS

KEYS = {"region": "region", "freq": "freq_mhz", "sf": "sf", "bw": "bw_khz", "cr": "cr",
        "power": "power_dbm", "preamble": "preamble", "dwell": "max_dwell_ms",
        "duty": "duty_cycle"}


def parse_value(raw: str):
    if raw.lower() in ("none", "off"):
        return "none"
    if raw.lower() == "preset":
        return "preset"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def show(cfg: dict) -> None:
    p = C.build_profile(cfg["radio"])
    print("\n".join(C.describe(p)))
    print(C.log_limits_text(cfg["log"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=C.CONFIG_FILE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("regions")
    st = sub.add_parser("set")
    st.add_argument("key")
    st.add_argument("value")
    rs = sub.add_parser("reset")
    rs.add_argument("what", nargs="?", default="all", choices=["radio", "log", "all"])
    a = ap.parse_args()

    try:
        cfg = C.load(a.config)
        if a.cmd == "show":
            show(cfg)
        elif a.cmd == "regions":
            for name, pr in PRESETS.items():
                dwell = f"{pr['max_dwell_ms']} ms" if pr["max_dwell_ms"] else "none"
                duty = f"{pr['duty_cycle'] * 100:g} %" if pr["duty_cycle"] else "none"
                print(f"{name:<8} {pr['freq_mhz']:.3f} MHz  SF{pr['sf']}  "
                      f"BW {pr['bw_hz'] / 1000:g} kHz  {pr['tx_power_dbm']} dBm  "
                      f"dwell {dwell}  duty {duty}")
                print(f"         {pr['note']}")
            print("custom   start from the default and set every value yourself")
        elif a.cmd == "set":
            new = copy.deepcopy(cfg)
            key, value = a.key.lower(), parse_value(a.value)
            if key.startswith("log."):
                k = key[4:]
                if k not in ("max_rows", "max_days") or not isinstance(value, (int, float)):
                    sys.exit("use: set log.max_rows N  or  set log.max_days N")
                new["log"][k] = value
            elif key in KEYS:
                if key == "region":
                    new["radio"] = copy.deepcopy(C.DEFAULTS["radio"])
                    new["radio"]["region"] = value
                else:
                    new["radio"][KEYS[key]] = None if value == "preset" and key not in (
                        "dwell", "duty") else value
            else:
                sys.exit(f"unknown setting '{a.key}'. Keys: {', '.join(KEYS)}, "
                         "log.max_rows, log.max_days")
            C.build_profile(new["radio"])        # validate before saving
            C.save(new, a.config)
            print(f"saved to {a.config}\n")
            show(new)
            if not key.startswith("log."):
                print("\nRemember: every node must use the same radio settings.")
        elif a.cmd == "reset":
            if a.what in ("radio", "all"):
                cfg["radio"] = copy.deepcopy(C.DEFAULTS["radio"])
            if a.what in ("log", "all"):
                cfg["log"] = copy.deepcopy(C.DEFAULTS["log"])
            C.save(cfg, a.config)
            show(cfg)
    except C.ConfigError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
