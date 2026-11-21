"""
Region presets: starting points for a single P2P channel per region.

These are NOT legal advice. Frequencies, power, duty cycle and listen-before-
talk rules differ by country and change over time: check your regulator. The
LA66 also comes in hardware variants matched to a band (e.g. 868 vs 915/923
MHz): use a preset that fits your module.

Every node in a network must use the SAME profile (frequency, SF, bandwidth,
coding rate, preamble, sync word), or they cannot hear each other.
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "AS923-2": dict(
        freq_mhz=920.800, sf=7, bw_hz=125_000, tx_power_dbm=14,
        max_dwell_ms=400, duty_cycle=0.01,
        note="Vietnam/Indonesia (AS923-2 band). 920.8 MHz sits outside the usual "
             "AS923-2 LoRaWAN gateway channels (921.4-922.8). Default."),
    "AS923-1": dict(
        freq_mhz=922.000, sf=7, bw_hz=125_000, tx_power_dbm=14,
        max_dwell_ms=400, duty_cycle=0.01,
        note="Japan, Malaysia, Singapore, ... Japan requires listen-before-talk, "
             "which LA66 P2P firmware v1.2.4 does not do."),
    "AS923-3": dict(
        freq_mhz=917.000, sf=7, bw_hz=125_000, tx_power_dbm=14,
        max_dwell_ms=400, duty_cycle=0.01,
        note="AS923-3 countries (e.g. Philippines)."),
    "AS923-4": dict(
        freq_mhz=917.700, sf=7, bw_hz=125_000, tx_power_dbm=14,
        max_dwell_ms=400, duty_cycle=0.01,
        note="AS923-4 (Israel)."),
    "EU868": dict(
        freq_mhz=869.525, sf=7, bw_hz=125_000, tx_power_dbm=14,
        max_dwell_ms=None, duty_cycle=0.10,
        note="Europe, sub-band 869.4-869.65 MHz (10 % duty cycle). Needs the "
             "868 MHz LA66 variant."),
    "US915": dict(
        freq_mhz=915.000, sf=7, bw_hz=500_000, tx_power_dbm=20,
        max_dwell_ms=400, duty_cycle=None,
        note="USA. 500 kHz bandwidth (wideband digital modulation). Check FCC "
             "Part 15.247 for your use."),
    "AU915": dict(
        freq_mhz=917.000, sf=7, bw_hz=125_000, tx_power_dbm=20,
        max_dwell_ms=400, duty_cycle=None,
        note="Australia / New Zealand (915-928 MHz)."),
    "IN865": dict(
        freq_mhz=866.000, sf=7, bw_hz=125_000, tx_power_dbm=20,
        max_dwell_ms=None, duty_cycle=None,
        note="India (865-867 MHz). Needs the 868 MHz LA66 variant."),
    "KR920": dict(
        freq_mhz=923.000, sf=7, bw_hz=125_000, tx_power_dbm=14,
        max_dwell_ms=None, duty_cycle=0.01,
        note="South Korea. Listen-before-talk is required, which LA66 P2P "
             "firmware v1.2.4 does not do."),
}

DEFAULT_REGION = "AS923-2"

# LA66 P2P bandwidth codes (AT+BW)
BW_CODES = {125_000: 0, 250_000: 1, 500_000: 2, 62_500: 3, 41_670: 4, 31_250: 5,
            20_830: 6, 15_630: 7, 10_420: 8, 7_810: 9}
BW_KHZ = {round(hz / 1000, 2): hz for hz in BW_CODES}     # 125.0 -> 125000, 62.5 -> 62500


def bw_from_khz(khz: float) -> int:
    hz = BW_KHZ.get(round(float(khz), 2))
    if hz is None:
        raise ValueError(f"bandwidth must be one of {sorted(BW_KHZ)} kHz")
    return hz
