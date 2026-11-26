// ---------------------------------------------------------------------------------
// Protocol, regions, settings, hardware profiles, LA66 parser
// (ports of protocol.py, regions.py, config.py, hardware.py, la66.py)
// ---------------------------------------------------------------------------------

class PayloadTooLong extends Error {}
class ConfigError extends Error {}
class KeyException extends Error {}
class LockedException extends KeyException {}
class HardwareError extends Error {}

function airProfile(o = {}) {
  return Object.assign({
    name: "AS923-2", freqMhz: 920.8, sf: 7, bwHz: 125000, cr: 1, preamble: 8, crc: true,
    explicitHeader: true, privateSync: true, txPowerDbm: 14, contractMax: 100, maxPayload: 100,
    ratePerS: 0.1, burst: 3, backoffMaxS: 2.0, maxDwellMs: 400, dutyCycle: 0.01,
  }, o);
}

// Python's "%g"-style number text for the values used here (125 -> "125", 62.5 -> "62.5")
const fmtG = (v) => (Number.isInteger(v) ? String(v) : String(v));

const MsgType = { PLAIN: 0, SIGNED: 1, SEALED: 2 };

const Protocol = (() => {
  const PROTOCOL_VERSION = 0, PRIVATE_VERSION = 1, PRIVATE_TAG = 5;
  const P = { profile: airProfile(), firmwareOverhead: 1 };
  let limits = { 5: 230, 6: 230, 7: 230, 8: 230, 9: 123, 10: 59, 11: 59, 12: 59 };
  let limitDefault = 59;

  const firmwareLimit = (sf) => limits[sf] ?? limitDefault;

  function setHardwareLimits(lim, overhead) {
    P.firmwareOverhead = overhead;
    limits = {};
    for (const [k, v] of Object.entries(lim)) if (k !== "*") limits[Number(k)] = v;
    const vals = Object.values(limits);
    limitDefault = lim["*"] ?? (vals.length ? Math.min(...vals) : 255);
  }

  function airtimeMs(len, p = P.profile, overhead = P.firmwareOverhead) {
    const n = len + overhead;
    const tsym = Math.pow(2, p.sf) / p.bwHz * 1000;
    const de = tsym > 16 ? 1 : 0;
    const h = p.explicitHeader ? 0 : 1;
    const num = 8 * n - 4 * p.sf + 28 + (p.crc ? 16 : 0) - 20 * h;
    const nPayload = 8 + Math.max(Math.ceil(num / (4 * (p.sf - 2 * de))) * (p.cr + 4), 0);
    return (p.preamble + 4.25) * tsym + nPayload * tsym;
  }

  function effectivePayload(p) {
    let lim = Math.min(p.contractMax, firmwareLimit(p.sf) - P.firmwareOverhead);
    if (p.maxDwellMs) while (lim > 1 && airtimeMs(lim, p) > p.maxDwellMs) lim--;
    return lim;
  }

  function applyProfile(p) { p.maxPayload = effectivePayload(p); P.profile = p; return p; }

  const makeMode = (type, hop = 0, version = PROTOCOL_VERSION) =>
    ((version & 3) << 6) | ((type & 7) << 3) | ((hop & 3) << 1);
  const parseMode = (b) => [(b >> 6) & 3, (b >> 3) & 7, (b >> 1) & 3];

  const normalize = (t) => Bytes.nfc(t);
  function compose(text, callsign) {
    const t = normalize(text.trim());
    return callsign ? `${callsign}: ${t}` : t;
  }
  function encodePlain(text, p = P.profile) {
    const body = Bytes.utf8(normalize(text));
    const frame = Bytes.concat(new Uint8Array([makeMode(MsgType.PLAIN)]), body);
    if (frame.length > p.maxPayload)
      throw new PayloadTooLong(`${frame.length} B > ${p.maxPayload} B limit (${body.length} B of text, max ${p.maxPayload - 1})`);
    return frame;
  }
  const frameLenPlain = (text) => 1 + Bytes.utf8(normalize(text)).length;

  const CTRL = /[\x00-\x08\x0b-\x1f\x7f]/g;
  const CALLSIGN = /^([^\s:]{1,16}): /u;
  const TOKENS = /[#@][\p{L}\p{N}\p{M}_\-]+/gu;
  const clean = (s) => s.replace(CTRL, "�");

  class Decoded {
    constructor(kind, raw, text = null, msgType = null, hop = 0) {
      Object.assign(this, { kind, raw, text, msgType, hop });
    }
    get callsign() { const m = this.text === null ? null : CALLSIGN.exec(this.text); return m ? m[1] : null; }
    get tokens() { return [...(this.text || "").matchAll(TOKENS)].map((m) => m[0].toLowerCase()); }
    display() {
      if (this.kind === "plain") return this.text || "";
      if (this.kind === "reserved")
        return this.msgType === MsgType.SIGNED && this.text !== null ? `[unverified] ${this.text}`
          : `[${this.raw.length} B ${this.msgType === MsgType.SIGNED ? "signed" : "sealed"}]`;
      return `[${this.raw.length} B undecoded]`;
    }
  }

  function decode(frame) {
    if (!frame.length) return new Decoded("undecoded", frame);
    const [version, type, hop] = parseMode(frame[0]);
    if (version === PROTOCOL_VERSION && type === MsgType.PLAIN) {
      const t = Bytes.utf8Strict(frame, 1);
      return t === null ? new Decoded("undecoded", frame) : new Decoded("plain", frame, clean(t), type, hop);
    }
    if (version === PRIVATE_VERSION && type === MsgType.SIGNED && frame.length > 2 + PRIVATE_TAG) {
      const t = Bytes.utf8Strict(frame, 2, frame.length - PRIVATE_TAG);
      return new Decoded("reserved", frame, t === null ? null : clean(t), type, hop);
    }
    if (version === PRIVATE_VERSION && (type === MsgType.SIGNED || type === MsgType.SEALED))
      return new Decoded("reserved", frame, null, type, hop);
    return new Decoded("undecoded", frame);
  }

  return { P, PROTOCOL_VERSION, PRIVATE_VERSION, firmwareLimit, setHardwareLimits, airtimeMs, effectivePayload,
           applyProfile, makeMode, parseMode, normalize, compose, encodePlain, frameLenPlain, clean, decode,
           get profile() { return P.profile; } };
})();

// Token bucket: average `rate` messages/s, bursts up to `burst`
class RateLimiter {
  constructor(rate, burst, clock = () => performance.now() / 1000) {
    this.rate = rate; this.capacity = burst; this.tokens = burst; this.clock = clock; this.t = clock();
  }
  refill() { const now = this.clock(); this.tokens = Math.min(this.capacity, this.tokens + (now - this.t) * this.rate); this.t = now; }
  waitTime() { this.refill(); return this.tokens >= 1 ? 0 : (1 - this.tokens) / this.rate; }
  tryConsume() { this.refill(); if (this.tokens >= 1) { this.tokens -= 1; return true; } return false; }
}

// Rolling sum of own TX airtime over one hour
class AirtimeMeter {
  constructor(windowS = 3600, clock = () => performance.now() / 1000) { this.windowS = windowS; this.clock = clock; this.events = []; }
  add(ms) { this.events.push([this.clock(), ms]); }
  totalMs() {
    const cutoff = this.clock() - this.windowS;
    while (this.events.length && this.events[0][0] < cutoff) this.events.shift();
    return this.events.reduce((s, e) => s + e[1], 0);
  }
  oldest() { return this.events.length ? this.events[0][0] : null; }
  dutyCycle() { return this.totalMs() / (this.windowS * 1000); }
}

// -- regions and settings ---------------------------------------------------------------

const Regions = (() => {
  const pr = (freqMhz, sf, bwHz, powerDbm, maxDwellMs, dutyCycle, note) => ({ freqMhz, sf, bwHz, powerDbm, maxDwellMs, dutyCycle, note });
  const PRESETS = {
    "AS923-2": pr(920.8, 7, 125000, 14, 400, 0.01,
      "Vietnam/Indonesia (AS923-2 band). 920.8 MHz sits outside the usual AS923-2 LoRaWAN gateway channels (921.4-922.8). Default."),
    "AS923-1": pr(922.0, 7, 125000, 14, 400, 0.01,
      "Japan, Malaysia, Singapore, ... Japan requires listen-before-talk, which LA66 P2P firmware v1.2.4 does not do."),
    "AS923-3": pr(917.0, 7, 125000, 14, 400, 0.01, "AS923-3 countries (e.g. Philippines)."),
    "AS923-4": pr(917.7, 7, 125000, 14, 400, 0.01, "AS923-4 (Israel)."),
    "EU868": pr(869.525, 7, 125000, 14, null, 0.10,
      "Europe, sub-band 869.4-869.65 MHz (10 % duty cycle). Needs the 868 MHz LA66 variant."),
    "US915": pr(915.0, 7, 500000, 20, 400, null,
      "USA. 500 kHz bandwidth (wideband digital modulation). Check FCC Part 15.247 for your use."),
    "AU915": pr(917.0, 7, 125000, 20, 400, null, "Australia / New Zealand (915-928 MHz)."),
    "IN865": pr(866.0, 7, 125000, 20, null, null, "India (865-867 MHz). Needs the 868 MHz LA66 variant."),
    "KR920": pr(923.0, 7, 125000, 14, null, 0.01,
      "South Korea. Listen-before-talk is required, which LA66 P2P firmware v1.2.4 does not do."),
  };
  const DEFAULT_REGION = "AS923-2";
  const BW_CODES = new Map([[125000, 0], [250000, 1], [500000, 2], [62500, 3], [41670, 4], [31250, 5],
                            [20830, 6], [15630, 7], [10420, 8], [7810, 9]]);
  function bwFromKhz(khz) {
    const r = Math.round(khz * 100) / 100;
    for (const k of BW_CODES.keys()) if (Math.round(k / 10) / 100 === r) return k;
    throw new ConfigError(`bandwidth must be one of ${[...BW_CODES.keys()].map((k) => k / 1000).sort((a, b) => a - b).join(", ")} kHz`);
  }
  function apply(p, region) {
    const x = PRESETS[region] || PRESETS[DEFAULT_REGION];
    return Object.assign(p, { name: region, freqMhz: x.freqMhz, sf: x.sf, bwHz: x.bwHz, txPowerDbm: x.powerDbm,
                              maxDwellMs: x.maxDwellMs, dutyCycle: x.dutyCycle });
  }
  return { PRESETS, DEFAULT_REGION, BW_CODES, bwFromKhz, apply };
})();

const Settings = (() => {
  const MIN_PAYLOAD = 16;
  const defaults = () => ({
    radio: { region: Regions.DEFAULT_REGION, freq_mhz: null, sf: null, bw_khz: null, cr: null, power_dbm: null,
             preamble: null, max_dwell_ms: "preset", duty_cycle: "preset" },
    log: { max_rows: 5000, max_days: 30 },
    hardware: { type: Hardware.DEFAULT },
    ui: { nick: null },
  });
  const given = (radio, k) => radio[k] !== null && radio[k] !== undefined && radio[k] !== "preset";
  function num(v) {
    if (typeof v === "number") return v;
    if (typeof v === "string" && v.trim() !== "" && !isNaN(Number(v))) return Number(v);
    throw new ConfigError(`bad radio setting: '${v}'`);
  }
  const offOrNum = (v) => (v === 0 || v === "none" || v === "off" || v === "0") ? null : num(v);

  function buildProfile(radio) {
    const region = radio.region || Regions.DEFAULT_REGION;
    if (!(region in Regions.PRESETS) && region !== "custom")
      throw new ConfigError(`unknown region '${region}'. Choose one of: ${Object.keys(Regions.PRESETS).join(", ")} or custom`);
    const p = Regions.apply(airProfile(), region === "custom" ? Regions.DEFAULT_REGION : region);
    p.name = region;
    if (given(radio, "freq_mhz")) p.freqMhz = num(radio.freq_mhz);
    if (given(radio, "sf")) p.sf = Math.trunc(num(radio.sf));
    if (given(radio, "bw_khz")) p.bwHz = Regions.bwFromKhz(num(radio.bw_khz));
    if (given(radio, "cr")) p.cr = Math.trunc(num(radio.cr));
    if (given(radio, "power_dbm")) p.txPowerDbm = Math.trunc(num(radio.power_dbm));
    if (given(radio, "preamble")) p.preamble = Math.trunc(num(radio.preamble));
    if (given(radio, "max_dwell_ms")) p.maxDwellMs = offOrNum(radio.max_dwell_ms);
    if (given(radio, "duty_cycle")) p.dutyCycle = offOrNum(radio.duty_cycle);
    if (region !== "custom") {
      const ref = Regions.apply(airProfile(), region);
      for (const k of ["freqMhz", "sf", "bwHz", "cr", "txPowerDbm", "preamble", "maxDwellMs", "dutyCycle"])
        if (p[k] !== ref[k]) { p.name = region + "*"; break; }
    }
    validate(p);
    p.maxPayload = Protocol.effectivePayload(p);
    if (p.maxPayload < MIN_PAYLOAD)
      throw new ConfigError(`SF${p.sf} at ${p.bwHz / 1000} kHz leaves only ${p.maxPayload} B per message ` +
        `under the ${p.maxDwellMs || 0} ms dwell limit. Use a lower SF or a wider bandwidth, ` +
        `or remove the dwell limit if your region allows it.`);
    return p;
  }
  function validate(p) {
    if (!(p.freqMhz >= 137 && p.freqMhz <= 1020)) throw new ConfigError("frequency must be 137-1020 MHz (SX1262 range)");
    if (!(p.sf >= 5 && p.sf <= 12)) throw new ConfigError("spreading factor must be 5-12");
    if (!Regions.BW_CODES.has(p.bwHz)) throw new ConfigError("bandwidth not supported");
    if (!(p.cr >= 1 && p.cr <= 4)) throw new ConfigError("coding rate must be 1-4 (4/5 .. 4/8)");
    if (!(p.txPowerDbm >= 0 && p.txPowerDbm <= 22)) throw new ConfigError("TX power must be 0-22 dBm (LA66 range)");
    if (!(p.preamble >= 6 && p.preamble <= 65535)) throw new ConfigError("preamble must be 6-65535 symbols");
    if (p.dutyCycle !== null && !(p.dutyCycle > 0 && p.dutyCycle <= 1))
      throw new ConfigError("duty cycle must be between 0 and 1 (e.g. 0.01 = 1 %)");
  }
  const profileFields = (p) => ({
    region: p.name.replace(/\*+$/, ""), freq_mhz: p.freqMhz, sf: p.sf, bw_khz: p.bwHz / 1000, cr: p.cr,
    power_dbm: p.txPowerDbm, preamble: p.preamble,
    max_dwell_ms: p.maxDwellMs > 0 ? p.maxDwellMs : "none", duty_cycle: p.dutyCycle > 0 ? p.dutyCycle : "none" });
  function activateHardware(hw) {
    Protocol.setHardwareLimits(Object.keys(hw.maxPayload).length ? hw.maxPayload : { "*": 255 }, hw.firmwareOverhead);
    const p = { ...Protocol.profile };
    p.maxPayload = Protocol.effectivePayload(p);
    if (p.maxPayload < MIN_PAYLOAD) throw new ConfigError(`${hw.name} allows only ${p.maxPayload} B per message with the current radio settings`);
    Protocol.applyProfile(p);
    return hw;
  }
  return { MIN_PAYLOAD, defaults, buildProfile, validate, profileFields, activateHardware };
})();

// -- hardware profiles -----------------------------------------------------------------

const Hardware = (() => {
  const DEFAULT = "dragino-la66-p2p";
  const hp = (o) => Object.assign({ description: "", driver: "at", baud: 9600, lineEnding: "\r\n", cmdDelay: 0.3,
    init: [], rxStart: [], send: "", preSend: [], postSend: [], txDone: "", txError: "", rx: [], hexUpper: true,
    maxPayload: {}, firmwareOverhead: 0, syncWord: true, verified: false, notes: "" }, o);
  const BUILTIN = {
    [DEFAULT]: hp({ name: DEFAULT, description: "Dragino LA66 / LA66 USB with LA66 P2P firmware v1.2.4",
      driver: "la66", baud: 9600, firmwareOverhead: 1, verified: true,
      maxPayload: { 5: 230, 6: 230, 7: 230, 8: 230, 9: 123, 10: 59, 11: 59, 12: 59 },
      notes: "Tested with TextCast. Needs the P2P firmware, not the LoRaWAN firmware." }),
    "rak3172-rui3": hp({ name: "rak3172-rui3",
      description: "RAKwireless RAK3172 / RAK3272 / RAK3372 with RUI3 firmware (LoRa P2P)",
      baud: 115200, cmdDelay: 0.4,
      init: ["AT+NWM=0", "AT+P2P={freq_hz}:{sf}:{bw_khz}:{cr_index}:{preamble}:{power}",
             "AT+SYNCWORD={sync16}", "AT+IQINVER=0", "AT+ENCRY=0"],
      rxStart: ["AT+PRECV=0", "AT+PRECV=65534"], preSend: ["AT+PRECV=0"], send: "AT+PSEND={hex}",
      postSend: ["AT+PRECV=65534"], txDone: "\\+EVT:TXP2P DONE", txError: "AT_BUSY_ERROR|AT_PARAM_ERROR|AT_ERROR",
      rx: ["\\+EVT:RXP2P:(?P<rssi>-?\\d+):(?P<snr>-?\\d+):(?P<payload>[0-9A-Fa-f]+)"], maxPayload: { "*": 255 },
      notes: "From RAK's RUI3 documentation; not yet tested with TextCast. Some RUI3 versions expect " +
             "the bandwidth as a code (0=125) in AT+P2P, and the sync-word syntax may differ by version." }),
    "seeed-wio-e5": hp({ name: "seeed-wio-e5",
      description: "Seeed Wio-E5 / LoRa-E5 (STM32WLE5) with the factory AT firmware, TEST mode",
      baud: 9600, cmdDelay: 0.4,
      init: ["AT+MODE=TEST", "AT+TEST=RFCFG,{freq_mhz},SF{sf},{bw_khz},{preamble},{preamble},{power},ON,OFF,{public}"],
      rxStart: ["AT+TEST=RXLRPKT"], send: 'AT+TEST=TXLRPKT,"{hex}"', postSend: ["AT+TEST=RXLRPKT"],
      txDone: "\\+TEST: TX DONE", txError: "\\+TEST: ERROR|ERROR\\(",
      rx: ["\\+TEST: LEN:\\s*\\d+,\\s*RSSI:\\s*(?P<rssi>-?\\d+),\\s*SNR:\\s*(?P<snr>-?\\d+)",
           '\\+TEST: RX "(?P<payload>[0-9A-Fa-f]*)"'], maxPayload: { "*": 255 },
      notes: "From Seeed's LoRa-E5 AT specification; not yet tested with TextCast. Bandwidth 125/250/500 kHz only." }),
  };
  // Python-style named groups (?P<name>...) -> JavaScript (?<name>...)
  const regex = (r) => new RegExp(r.replace(/\(\?P</g, "(?<"));

  function validate(h) {
    const problems = [];
    if (h.driver !== "at" && h.driver !== "la66") problems.push("driver must be 'at' or 'la66'");
    if (h.driver === "at") {
      if (!h.send.includes("{hex}")) problems.push("'send' must contain {hex}");
      if (!h.txDone) problems.push("'tx_done' regex is required");
      if (!h.rx.length) problems.push("'rx' needs at least one regex");
      if (!h.rx.some((r) => r.includes("(?P<payload>") || r.includes("(?<payload>")))
        problems.push("one 'rx' regex must capture (?P<payload>...)");
      for (const r of [...h.rx, h.txDone, h.txError].filter(Boolean)) {
        try { regex(r); } catch (e) { problems.push(`bad regex ${r}: ${e.message}`); }
      }
    }
    return problems;
  }

  function fields(p, payload = null, hexUpper = true) {
    const d = {
      freq_mhz: p.freqMhz.toFixed(3), freq_hz: String(Math.round(p.freqMhz * 1e6)), sf: String(p.sf),
      bw_khz: fmtG(p.bwHz / 1000), bw_hz: String(p.bwHz), bw_code: String(Regions.BW_CODES.get(p.bwHz) ?? 0),
      cr: String(p.cr), cr_index: String(p.cr - 1), cr_denom: String(p.cr + 4), preamble: String(p.preamble),
      power: String(p.txPowerDbm), sync16: p.privateSync ? "1424" : "3444", sync8: p.privateSync ? "12" : "34",
      public: p.privateSync ? "OFF" : "ON", public01: p.privateSync ? "0" : "1" };
    if (payload) { d.hex = Bytes.hex(payload, hexUpper); d.len = String(payload.length); }
    return d;
  }
  function render(template, p, payload = null, hexUpper = true) {
    const f = fields(p, payload, hexUpper);
    return template.replace(/\{\{/g, "\u0001").replace(/\}\}/g, "\u0002")
      .replace(/\{([a-z0-9_]*)\}/g, (_, k) => { if (!(k in f)) throw new HardwareError(`unknown template field '${k}'`); return f[k]; })
      .replace(/\u0001/g, "{").replace(/\u0002/g, "}");
  }
  const renderAll = (ts, p) => ts.map((t) => render(t, p));
  function load(name) {
    const n = name || DEFAULT;
    if (BUILTIN[n]) return BUILTIN[n];
    throw new HardwareError(`unknown hardware '${n}'. Built-in: ${Object.keys(BUILTIN).join(", ")}`);
  }
  return { DEFAULT, BUILTIN, regex, validate, fields, render, renderAll, load };
})();

// -- Dragino LA66 P2P firmware v1.2.4: line parser and AT commands -----------------------

const La66Parser = (() => {
  const HEXPAIRS = "(?:[0-9a-fA-F]{2}(?:\\s+|$))+";
  const RXDONE = /rxDone/g;
  const DATA = new RegExp(`Data:\\s*\\(HEX:\\)\\s*(${HEXPAIRS})?`, "g");
  const RSSI = /Rssi=\s*(-?\d+)/g;
  const RECV = /((?:[0-9a-fA-F]{2}\s+)+),\s*(-?\d+)\s*,\s*(-?\d+)/g;
  const TXDONE = /txDone/g;
  const RXON = /RX on freq/g;
  const BANNER = /^(Group|Syncword|Frequency|BandWidth|Spreading Factor|Output Power|Coding Rate|Header Type|CRC|Invert IQ|Preamble|RX Mode):\s*(.+)$/;
  const FW = /LA66 P2P Firmware\s+(v[\d.]+)/;

  function parse(line0) {
    const line = line0.trim();
    if (!line || line.startsWith("AT")) return [];
    const found = [];
    let m;
    if ((m = FW.exec(line))) found.push({ pos: m.index, ev: ["banner", "Firmware", m[1]] });
    if ((m = BANNER.exec(line))) found.push({ pos: 0, ev: ["banner", m[1], m[2].trim()] });
    for (const x of line.matchAll(RXDONE)) found.push({ pos: x.index, ev: ["rx_start"] });
    const dataSpans = [];
    for (const x of line.matchAll(DATA)) {
      dataSpans.push([x.index, x.index + x[0].length]);
      found.push({ pos: x.index, ev: ["rx_data", Bytes.unhex(x[1] || "")] });
    }
    const recvSpans = [];
    for (const x of line.matchAll(RECV)) {
      if (dataSpans.some(([a, b]) => x.index >= a && x.index < b)) continue;
      recvSpans.push([x.index, x.index + x[0].length]);
      found.push({ pos: x.index, ev: ["recv", Bytes.unhex(x[1]), parseInt(x[2], 10), parseInt(x[3], 10)] });
    }
    for (const x of line.matchAll(RSSI)) {
      // a truncated 'Rssi= -1' glued to a RECV reply would read as '-154'
      if (recvSpans.some(([a]) => x.index < a && a < x.index + x[0].length)) continue;
      const v = parseInt(x[1], 10);
      if (v >= -150 && v <= 0) found.push({ pos: x.index, ev: ["rssi", v] });
    }
    for (const x of line.matchAll(TXDONE)) found.push({ pos: x.index, ev: ["tx_done"] });
    for (const x of line.matchAll(RXON)) found.push({ pos: x.index, ev: ["rx_on"] });
    found.sort((a, b) => a.pos - b.pos);          // stable, like Python's sort
    return found.map((f) => f.ev);
  }

  function atCommands(p) {
    const f = p.freqMhz.toFixed(3);
    const bw = Regions.BW_CODES.get(p.bwHz) ?? 0;
    const hdr = p.explicitHeader ? 0 : 1, crc = p.crc ? 1 : 0;
    return ["AT+RXMOD=65535,0", `AT+FRE=${f},${f}`, `AT+SF=${p.sf},${p.sf}`, `AT+BW=${bw},${bw}`,
      `AT+CR=${p.cr},${p.cr}`, `AT+CRC=${crc},${crc}`, `AT+HEADER=${hdr},${hdr}`, "AT+IQ=0,0",
      `AT+PREAMBLE=${p.preamble},${p.preamble}`, `AT+SYNCWORD=${p.privateSync ? 0 : 1}`,
      `AT+POWER=${p.txPowerDbm}`, "AT+GROUPMOD=0,0"];
  }

  const expectedBanner = (p) => ({
    "Syncword": p.privateSync ? "0x1424" : "0x3444", "Spreading Factor": `${p.sf} , ${p.sf}`,
    "Coding Rate": `4/${p.cr + 4} , 4/${p.cr + 4}`, "Header Type": "explicit header , explicit header",
    "CRC": p.crc ? "ON , ON" : "OFF , OFF", "Invert IQ": "0 , 0", "Preamble": `${p.preamble} , ${p.preamble}`,
    "Output Power": `${p.txPowerDbm} dBm`, "RX Mode": "65535 , 0", "Group": "0 , 0" });

  function checkBanner(banner, p) {
    const problems = [];
    for (const [k, want] of Object.entries(expectedBanner(p))) {
      const got = banner[k];
      if (got === undefined) problems.push(`${k}: not reported`);
      else if (!got.includes(want)) problems.push(`${k}: got '${got}', want '${want}'`);
    }
    for (const [k, want, tol] of [["Frequency", p.freqMhz, 0.0005], ["BandWidth", p.bwHz / 1000, 0.05]]) {
      const got = banner[k];
      const nums = [...(got || "").matchAll(/\d+(?:\.\d+)?/g)].map((x) => Number(x[0]));
      if (!nums.length) problems.push(`${k}: not reported`);
      else if (nums.slice(0, 2).some((x) => Math.abs(x - want) > tol)) problems.push(`${k}: got '${got}', want ${fmtG(want)}`);
    }
    return problems;
  }
  return { parse, atCommands, expectedBanner, checkBanner };
})();
