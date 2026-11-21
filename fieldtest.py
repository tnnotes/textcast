#!/usr/bin/env python3
"""
Field test: range, packet loss, RSSI/SNR.

Node 1 (fixed):   python3 fieldtest.py beacon --port /dev/cu.usbserial-A --interval 15 --size 50
Node 2 (moving):  python3 fieldtest.py listen --port /dev/cu.usbserial-B

The beacon sends plain frames 'FT <seq> <label>' padded to --size bytes.
The listener prints each one and, on Ctrl-C, a loss / signal summary.
Both log everything to the SQLite file (--db).
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time

from lora_text import config as CFG
from lora_text.la66 import LA66Radio, Packet, TxResult
from lora_text.protocol import PROFILE, airtime_ms, decode, encode_plain
from lora_text.storage import PacketLog


def ensure_profile(radio: LA66Radio, force: bool) -> None:
    probs = [] if force else radio.reset_and_check()
    if force or (probs and radio.banner):
        print("applying radio profile to the LA66…")
        probs = radio.configure()
    print("radio:", "OK" if not probs else probs)


def beacon(a) -> None:
    log = PacketLog(a.db, node=a.label)
    done = threading.Event()
    results: dict[int, bool] = {}

    def on_tx(r: TxResult):
        results[r.job_id] = r.ok
        print(f"  tx #{r.job_id} {'ok' if r.ok else 'FAILED'} ({r.airtime_ms:.0f} ms)")
        done.set()

    # interval is enforced here; the limiter still protects the contract
    radio = LA66Radio(a.port, a.baud, on_tx=on_tx, on_log=print, fetch_snr=False)
    radio.open()
    ensure_profile(radio, a.configure)
    seq = 0
    try:
        while a.count == 0 or seq < a.count:
            text = f"FT {seq} {a.label}"
            text = text + "." * max(0, a.size - 1 - len(text.encode()))
            frame = encode_plain(text)
            done.clear()
            radio.send(frame)
            log.add("tx", frame, "plain", text, airtime_ms=airtime_ms(len(frame)))
            print(f"[{time.strftime('%H:%M:%S')}] beacon seq={seq} ({len(frame)} B)")
            done.wait(a.interval + 15)
            seq += 1
            time.sleep(max(0.0, a.interval - PROFILE.backoff_max_s / 2))
    except KeyboardInterrupt:
        pass
    finally:
        ok = sum(results.values())
        print(f"\nsent {ok}/{len(results)} confirmed by txDone")
        radio.close()
        log.close()


def listen(a) -> None:
    log = PacketLog(a.db, node=a.label)
    seqs: list[int] = []
    rssi: list[int] = []
    snr: list[int] = []

    def on_packet(p: Packet):
        d = decode(p.payload)
        log.add("rx", p.payload, d.kind, d.text, p.rssi, p.snr, ts=p.t)
        if d.kind == "plain" and d.text and d.text.startswith("FT "):
            try:
                s = int(d.text.split()[1])
            except (IndexError, ValueError):
                return
            seqs.append(s)
            if p.rssi is not None:
                rssi.append(p.rssi)
            if p.snr is not None:
                snr.append(p.snr)
            print(f"[{time.strftime('%H:%M:%S')}] seq={s:<5} rssi={p.rssi} snr={p.snr} "
                  f"len={len(p.payload)}")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] other: {d.display()}")

    radio = LA66Radio(a.port, a.baud, on_packet=on_packet, on_log=print,
                      fetch_snr=not a.no_snr)
    radio.open()
    ensure_profile(radio, a.configure)
    print("listening... Ctrl-C for summary")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    radio.close()
    log.close()
    if not seqs:
        print("\nno beacon packets received")
        return
    expected = max(seqs) - min(seqs) + 1
    got = len(set(seqs))
    print(f"\nreceived {got}/{expected} (loss {100 * (1 - got / expected):.1f} %)")
    if rssi:
        print(f"RSSI  mean {statistics.mean(rssi):.1f}  min {min(rssi)}  max {max(rssi)} dBm")
    if snr:
        print(f"SNR   mean {statistics.mean(snr):.1f}  min {min(snr)}  max {max(snr)} dB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["beacon", "listen"])
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--label", default="node")
    ap.add_argument("--db", default="fieldtest.db")
    ap.add_argument("--configure", action="store_true")
    ap.add_argument("--interval", type=float, default=15.0,
                    help="beacon period in s (>= 10 to respect the contract)")
    ap.add_argument("--size", type=int, default=50, help="beacon frame size in bytes")
    ap.add_argument("--count", type=int, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--no-snr", action="store_true")
    CFG.add_cli_args(ap)
    a = ap.parse_args()
    try:
        CFG.from_args(a)
    except CFG.ConfigError as e:
        raise SystemExit(f"settings error: {e}")
    print("\n".join(CFG.describe(PROFILE)))
    if a.size > PROFILE.max_payload:
        raise SystemExit(f"--size {a.size} > {PROFILE.max_payload} B allowed by this profile")
    if a.mode == "beacon":
        if a.interval < 1 / PROFILE.rate_per_s:
            print(f"note: interval below {1 / PROFILE.rate_per_s:.0f} s; "
                  "the rate limiter will slow the beacon down")
        beacon(a)
    else:
        listen(a)


if __name__ == "__main__":
    main()
