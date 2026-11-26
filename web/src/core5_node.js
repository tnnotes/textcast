// ---------------------------------------------------------------------------------
// Radio drivers (port of la66.py / atradio.py) and the Node controller (node.py)
//
// A "link" is the serial connection: { write(Uint8Array): Promise, close(): Promise }
// and it calls radio.feed(Uint8Array) for every chunk it reads. The browser uses
// Web Serial; the tests use a simulated LA66.
// ---------------------------------------------------------------------------------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mono = () => performance.now() / 1000;

class Radio {
  constructor(link, baud) {
    this.link = link; this.baud = baud;
    this.onPacket = () => {}; this.onTx = () => {}; this.onLog = () => {};
    this.limiter = new RateLimiter(Protocol.profile.ratePerS, Protocol.profile.burst);
    this.meter = new AirtimeMeter();
    this.banner = {};
    this.txq = []; this.ctrlq = []; this.ids = 0;
    this.running = false; this.txBusy = false; this.paused = false;
    this.lineBuf = []; this.lastData = 0;
    this.job = null; this.readyAt = 0; this.working = false;
    this.txWaiter = null; this.lineEnding = "\r\n";
  }
  get p() { return Protocol.profile; }

  open() {
    this.running = true;
    this.timer = setInterval(() => this._tick(), 50);
  }
  async close() {
    this.running = false;
    clearInterval(this.timer);
    try { await this.link.close(); } catch (e) { /* already gone */ }
  }

  send(frame) {
    if (frame.length > this.p.maxPayload) throw new Error(`frame ${frame.length} B exceeds ${this.p.maxPayload} B`);
    const job = ++this.ids;
    this.txq.push([job, frame]);
    return job;
  }
  queued() { return this.txq.length + (this.job ? 1 : 0); }
  async pause(timeoutS = 5) {
    this.paused = true;
    const end = mono() + timeoutS;
    while ((this.txBusy || this.working) && mono() < end) await sleep(50);
  }
  resume() { this.paused = false; }

  async write(cmd) { await this.link.write(Bytes.utf8(cmd + this.lineEnding)); }

  // bytes from the serial port
  feed(chunk) {
    this.lastData = mono();
    for (const b of chunk) {
      this.lineBuf.push(b);
      if (b === 10) this._emit();
    }
  }
  _emit() {
    const s = String.fromCharCode(...this.lineBuf);
    this.lineBuf = [];
    try { this.handleLine(s); } catch (e) { this.onLog(`radio: could not parse '${s.trim().slice(0, 60)}': ${e.message}`); }
  }

  // TX completion
  signalTxDone() { if (this.txWaiter) { const w = this.txWaiter; this.txWaiter = null; w(true); } }
  waitTxDone(timeoutS) {
    return new Promise((resolve) => {
      const t = setTimeout(() => { if (this.txWaiter === done) this.txWaiter = null; resolve(false); }, timeoutS * 1000);
      const done = (v) => { clearTimeout(t); resolve(v); };
      this.txWaiter = done;
    });
  }

  dutyWait(frameLen) {
    const dc = this.p.dutyCycle;
    if (!dc || dc <= 0) return 0;
    const budget = dc * this.meter.windowS * 1000;
    const need = this.meter.totalMs() + Protocol.airtimeMs(frameLen, this.p) - budget;
    if (need <= 0) return 0;
    const oldest = this.meter.oldest();
    if (oldest === null) return 1;
    return Math.max(0.5, oldest + this.meter.windowS - this.meter.clock());
  }

  _tick() {
    if (!this.running) return;
    // a partial line with no more data for 200 ms: process it anyway
    if (this.lineBuf.length && mono() - this.lastData > 0.2) this._emit();
    try { this.checkDeadline(); } catch (e) { this.onLog("radio: " + e.message); }
    if (this.working) return;
    if (this.ctrlq.length) {
      const c = this.ctrlq.shift();
      this.working = true;
      this.write(c).catch((e) => this.onLog("serial: " + e.message)).finally(() => { this.working = false; });
      return;
    }
    if (!this.job) {
      if (!this.txq.length) return;
      this.job = this.txq.shift();
      this.readyAt = mono() + this.limiter.waitTime() + Math.random() * Math.max(this.p.backoffMaxS, 1e-9);
    }
    if (mono() < this.readyAt || this.paused) return;
    const wait = this.dutyWait(this.job[1].length);
    if (wait > 0) { this.readyAt = mono() + Math.min(wait, 5); return; }
    if (!this.limiter.tryConsume()) { this.readyAt = mono() + this.limiter.waitTime(); return; }
    const [id, frame] = this.job;
    this.job = null;
    this.working = true;
    this.transmit(id, frame)
      .catch((e) => { this.onLog("send failed: " + e.message); this.onTx({ jobId: id, ok: false, airtimeMs: 0, detail: e.message }); })
      .finally(() => { this.working = false; });
  }

  checkDeadline() {}
}

class La66Radio extends Radio {
  constructor(link, baud = 9600, { fetchSnr = true, settle = 0.3, resetWait = 3.0 } = {}) {
    super(link, baud);
    Object.assign(this, { fetchSnr, settle, resetWait });
    this.pending = null; this.rxOnWaiter = null;
  }
  async configure() {
    for (const cmd of La66Parser.atCommands(this.p)) { await this.write(cmd); await sleep(this.settle * 1000); }
    return this.resetAndCheck();
  }
  async resetAndCheck() {
    this.banner = {};
    await this.write("ATZ");
    await sleep(this.resetWait * 1000);
    return La66Parser.checkBanner(this.banner, this.p);
  }
  handleLine(line) { for (const ev of La66Parser.parse(line)) this._event(ev); }
  _event(ev) {
    const now = mono();
    switch (ev[0]) {
      case "banner": this.banner[ev[1]] = ev[2]; break;
      case "rx_start": this._finalize(); this.pending = { payload: null, rssi: null, snr: null, t: Crypto.nowSeconds(), deadline: now + 0.5 }; break;
      case "rx_data": {
        if (!this.pending) this.pending = { payload: null, rssi: null, snr: null, t: Crypto.nowSeconds(), deadline: now };
        this.pending.payload = ev[1];
        if (this.fetchSnr && !this.txBusy) { this.pending.deadline = now + 1.0; this.ctrlq.push("AT+RECV=0"); }
        else this.pending.deadline = now + 0.3;
        break;
      }
      case "rssi": if (this.pending && this.pending.rssi === null) this.pending.rssi = ev[1]; break;
      case "recv": {
        const pd = this.pending;
        if (pd && pd.payload) {
          const want = pd.payload, got = ev[1];
          if (Bytes.equal(got, want) || (got.length >= want.length && Bytes.equal(got.slice(0, want.length), want))) {
            pd.payload = got; pd.rssi = ev[2]; pd.snr = ev[3];
            this._finalize();
          } else this.onLog("AT+RECV returned a different packet; ignored");
        }
        break;
      }
      case "tx_done": this.signalTxDone(); break;
      case "rx_on": if (this.rxOnWaiter) { const w = this.rxOnWaiter; this.rxOnWaiter = null; w(); } break;
    }
  }
  checkDeadline() { if (this.pending && mono() >= this.pending.deadline) this._finalize(); }
  _finalize() {
    const pd = this.pending;
    this.pending = null;
    if (pd && pd.payload) this.onPacket({ payload: pd.payload, rssi: pd.rssi, snr: pd.snr, t: pd.t });
  }
  async transmit(jobId, frame) {
    const cmd = `AT+SEND=0,${Bytes.hex(frame, true)},0,0`;
    const ms = Protocol.airtimeMs(frame.length, this.p);
    const timeout = cmd.length * 10 / this.baud + ms / 1000 + 1.5;
    let rxOn = new Promise((r) => { this.rxOnWaiter = r; });
    this.txBusy = true;
    let ok = false;
    try {
      const done = this.waitTxDone(timeout);
      await this.write(cmd);
      ok = await done;
      if (ok) await Promise.race([rxOn, sleep(1000)]);
    } finally { this.txBusy = false; this.rxOnWaiter = null; }
    if (ok) this.meter.add(ms);
    this.onTx({ jobId, ok, airtimeMs: ms, detail: ok ? "" : "no txDone" });
  }
}

class GenericAtRadio extends Radio {
  constructor(link, hw, baud = hw.baud) {
    super(link, baud);
    this.hw = hw; this.lineEnding = hw.lineEnding;
    this.rx = hw.rx.map(Hardware.regex);
    this.txDoneRe = Hardware.regex(hw.txDone);
    this.txErrRe = hw.txError ? Hardware.regex(hw.txError) : null;
    this.meta = null; this.txFailed = false;
  }
  async _run(cmds) { for (const c of cmds) { await this.write(c); await sleep(this.hw.cmdDelay * 1000); } }
  handleLine(line0) {
    const line = line0.trim();
    if (!line) return;
    if (this.txDoneRe.test(line)) { this.signalTxDone(); return; }
    if (this.txBusy && this.txErrRe && this.txErrRe.test(line)) { this.txFailed = true; this.signalTxDone(); this.onLog("radio: " + line); return; }
    for (const re of this.rx) {
      const m = re.exec(line);
      if (!m) continue;
      const g = m.groups || {};
      let rssi = g.rssi !== undefined ? parseInt(g.rssi, 10) : null;
      let snr = g.snr !== undefined ? parseInt(g.snr, 10) : null;
      if (g.payload === undefined) { this.meta = [rssi, snr, mono()]; return; }
      if (rssi === null && this.meta && mono() - this.meta[2] < 2) { rssi = this.meta[0]; snr = this.meta[1]; }
      this.meta = null;
      let payload;
      try { payload = Bytes.unhex(g.payload); } catch (e) { this.onLog("radio: could not decode received hex"); return; }
      if (payload.length) this.onPacket({ payload, rssi, snr, t: Crypto.nowSeconds() });
      return;
    }
  }
  async configure() {
    await this._run(Hardware.renderAll(this.hw.init, this.p));
    await this._run(Hardware.renderAll(this.hw.rxStart, this.p));
    this.banner = { Firmware: this.hw.name };
    return [];
  }
  resetAndCheck() { return this.configure(); }
  async transmit(jobId, frame) {
    const ms = Protocol.airtimeMs(frame.length, this.p);
    const cmd = Hardware.render(this.hw.send, this.p, frame, this.hw.hexUpper);
    const timeout = cmd.length * 10 / this.baud + ms / 1000 + 2;
    this.txFailed = false; this.txBusy = true;
    let ok = false;
    try {
      await this._run(Hardware.renderAll(this.hw.preSend, this.p));
      const done = this.waitTxDone(timeout);
      await this.write(cmd);
      ok = (await done) && !this.txFailed;
    } finally {
      await this._run(Hardware.renderAll(this.hw.postSend, this.p));
      this.txBusy = false;
    }
    if (ok) this.meter.add(ms);
    this.onTx({ jobId, ok, airtimeMs: ms, detail: ok ? "" : this.txFailed ? "radio reported an error" : "no TX confirmation" });
  }
}

const makeRadio = (hw, link) => (hw.driver === "la66" ? new La66Radio(link, hw.baud) : new GenericAtRadio(link, hw));

// ---------------------------------------------------------------------------------
// Node: radio + keyring + log, as channels and messages
// Channels: all, public, <group>, <own stream>, <followed stream>, other
// ---------------------------------------------------------------------------------

class Node {
  constructor(store, keysName, nick, passphrase, logName, { logMaxRows = 5000, logMaxDays = 30, logSaveDelayMs = 400 } = {}) {
    this.keys = new Keyring(store, keysName, nick, passphrase);
    this.protected = this.keys.encrypted;
    this.cipher = this.keys.logCipher();
    this.buffer = [];                     // private packets heard while locked
    this.log = new MessageLog(store, logName, logSaveDelayMs);
    this.logMaxRows = logMaxRows; this.logMaxDays = logMaxDays;
    this.inserts = 0; this.pending = new Map();
    this.onMsg = () => {}; this.onUpdate = () => {}; this.onLog = () => {};
    this.radio = null; this.firmware = "?"; this.publicTag = false;
    this.pruneLog();
    this.processPending();
  }
  static READ_ONLY = new Set(["follow", "other"]);
  static SENSITIVE = new Set(["sealed", "signed", "join"]);

  get nick() { return this.keys.me; }
  get handle() { return this.keys.handle; }
  get locked() { return this.keys.locked; }
  get callsign() { return this.publicTag && this.handle ? this.handle : this.nick; }

  async start(radio, { configure = false, autoApply = true } = {}) {
    this.radio = radio;
    radio.onPacket = (p) => this._onPacket(p);
    radio.onTx = (r) => this._onTx(r);
    radio.onLog = (s) => this.onLog(s);
    radio.open();
    let problems = configure ? [] : await radio.resetAndCheck();
    if (configure || (problems.length && autoApply && Object.keys(radio.banner).length)) {
      if (problems.length) this.onLog("module settings differ from the profile - applying them…");
      problems = await radio.configure();
    }
    this.firmware = radio.banner.Firmware || "?";
    return problems;
  }
  async reconfigure(p) {
    const r = this.radio;
    this.pruneLog();
    if (!r) { Protocol.applyProfile(p); return []; }
    await r.pause();
    try { Protocol.applyProfile(p); return await r.configure(); } finally { r.resume(); }
  }
  async detachRadio() { const r = this.radio; this.radio = null; if (r) await r.close(); }
  async stop() { await this.detachRadio(); this.keys.close(); this.log.flush(); }
  pruneLog() { return this.log.prune(this.logMaxRows, this.logMaxDays); }
  _logged() { if (++this.inserts % 200 === 0) this.pruneLog(); }

  // -- channels --------------------------------------------------------------------------
  channels() {
    if (this.keys.locked) return [["all", "all"], ["public", "public"], ["other", "other"]];
    return [["all", "all"], ["public", "public"], ...this.keys.groups().map((g) => [g, "group"]),
      ...this.keys.own().map((g) => [g, "own"]), ...this.keys.followed().map((g) => [g, "follow"]), ["other", "other"]];
  }
  channelType(ch) { const c = this.channels().find((x) => x[0] === ch); return c ? c[1] : "other"; }
  canSend(ch) { return !Node.READ_ONLY.has(this.channelType(ch)); }
  byteCount(ch, text) {
    if (!text.trim()) return 0;
    return ["group", "own"].includes(this.channelType(ch))
      ? Bytes.utf8(Protocol.normalize(text.trim())).length + Crypto.FRAME_OVERHEAD
      : Protocol.frameLenPlain(Protocol.compose(text, this.callsign));
  }
  get maxBytes() { return Protocol.profile.maxPayload; }

  // -- sending ---------------------------------------------------------------------------
  send(channel, text0, signed = false) {
    const r = this.radio;
    if (!r) throw new KeyException("not connected to a radio");
    const ctype = this.channelType(channel), text = text0.trim();
    if (Node.READ_ONLY.has(ctype)) throw new KeyException(`'${channel}' is read-only - switch to public, a group or your own stream`);
    if (this.locked && (ctype === "group" || ctype === "own")) throw new LockedException("identity is locked - unlock first");
    let frame, kind;
    if (ctype === "group" || ctype === "own") { frame = this.keys.sealFor(channel, text, signed); kind = signed ? "signed" : "sealed"; }
    else { frame = Protocol.encodePlain(Protocol.compose(text, this.callsign)); kind = "plain"; }
    const job = r.send(frame);
    const m = { ts: Crypto.nowSeconds(), direction: "tx", kind, channel: ctype === "all" ? "public" : channel,
                sender: this.handle || this.nick || "me", text, size: frame.length, job, status: "queued",
                airtime: Protocol.airtimeMs(frame.length), verified: true, group: ctype === "group", rssi: null, snr: null };
    const [ch, snd] = this._logMeta(m);
    m.row = this.log.add("tx", frame, kind, this._logText(m), null, null, m.airtime, m.ts, ch, snd);
    this._logged();
    this.pending.set(job, m);
    return m;
  }
  announce(group) {
    const r = this.radio;
    if (!r) throw new KeyException("not connected to a radio");
    const frame = this.keys.makeJoin(group);
    const job = r.send(frame);
    const m = { ts: Crypto.nowSeconds(), direction: "tx", kind: "join", channel: group, sender: this.handle,
                text: "announcing myself", size: frame.length, job, status: "queued", airtime: Protocol.airtimeMs(frame.length), group: true };
    this.pending.set(job, m);
    return m;
  }
  _onTx(r) {
    const m = this.pending.get(r.jobId);
    if (!m) return;
    this.pending.delete(r.jobId);
    m.status = r.ok ? "sent" : "failed";
    if (m.row) this.log.setTxOk(m.row, r.ok);
    this.onUpdate(m);
  }

  // -- receiving ---------------------------------------------------------------------------
  _onPacket(pkt) {
    if (this.locked && Protocol.decode(pkt.payload).kind === "reserved") {
      const row = this.log.add("rx", pkt.payload, "pending", null, pkt.rssi, pkt.snr, null, pkt.t);
      this._logged();
      this.buffer.push([row, pkt]);
      this.onMsg({ ts: pkt.t, direction: "rx", kind: "pending", channel: "other", sender: null,
                   text: `[${pkt.payload.length} B private - unlock to read]`, rssi: pkt.rssi, snr: pkt.snr, size: pkt.payload.length });
      return;
    }
    const m = this.classify(pkt);
    const [ch, snd] = this._logMeta(m);
    m.row = this.log.add("rx", pkt.payload, m.kind, this._logText(m), pkt.rssi, pkt.snr, null, pkt.t, ch, snd);
    this._logged();
    this.onMsg(m);
  }
  _protect(m, v) { return v !== null && v !== undefined && this.protected && Node.SENSITIVE.has(m.kind) && this.cipher ? this.cipher.encrypt(v) : v; }
  _logText(m) { return this._protect(m, m.text); }
  _logMeta(m) {
    const flag = m.group ? "g" : m.verified ? "v" : "u";
    return [this._protect(m, m.channel), this._protect(m, m.sender ? `${flag}:${m.sender}` : null)];
  }
  classify(pkt) {
    const d = Protocol.decode(pkt.payload);
    const mk = (kind, channel, sender, text, verified = false, group = false) =>
      ({ ts: pkt.t, direction: "rx", kind, channel, sender, text, rssi: pkt.rssi, snr: pkt.snr, size: pkt.payload.length, verified, group });
    if (d.kind === "plain") {
      const cs = d.callsign;
      let text = d.text || "";
      if (cs) text = text.slice(cs.length + 2);
      return mk("plain", "public", cs, text);
    }
    if (d.kind === "reserved") {
      const op = this.keys.open(pkt.payload);
      if (op) {
        if (op.group) return mk(op.kind, op.source, op.sender, Protocol.clean(op.text), false, true);
        const name = op.verified ? this.keys.display(op.sender, op.pub) : op.sender;
        return mk(op.kind, op.source, name, Protocol.clean(op.text), op.verified);
      }
      if (d.text !== null) return mk("unverified", "public", null, d.text);
      return mk("locked", "other", null, `[${pkt.payload.length} B sealed]`);
    }
    return mk("undecoded", "other", null, `[${pkt.payload.length} B undecoded]`);
  }

  // -- lock / unlock -------------------------------------------------------------------------
  lockIdentity() {
    if (!this.protected) throw new KeyException("this identity has no passphrase");
    this.keys.wipe(); this.cipher = null;
  }
  unlock(passphrase) {
    this.keys.unlock(passphrase);
    this.cipher = this.keys.logCipher();
    const buffered = this.buffer; this.buffer = [];
    for (const [row, pkt] of buffered) {
      const m = this.classify(pkt);
      const [ch, snd] = this._logMeta(m);
      this.log.update(row, m.kind, this._logText(m), ch, snd);
    }
  }
  processPending() {
    if (this.locked) return;
    for (const r of this.log.list(500, "pending")) {
      const m = this.classify({ payload: Bytes.unhex(r.payload), rssi: r.rssi, snr: r.snr, t: r.ts });
      const [ch, snd] = this._logMeta(m);
      this.log.update(r.id, m.kind, this._logText(m), ch, snd);
    }
  }
  history(n = 300) {
    const out = [], c = this.cipher;
    for (const r of this.log.list(n)) {
      let { text, channel, sender } = r;
      if ([text, channel, sender].some((x) => typeof x === "string" && x.startsWith(LogCipher.PREFIX))) {
        if (!c) continue;
        text = c.decrypt(text); channel = c.decrypt(channel); sender = c.decrypt(sender);
      }
      const payload = Bytes.unhex(r.payload);
      if (r.dir === "rx" && r.kind === "pending") text = `[${payload.length} B private - unlock to read]`;
      if (text === null || text === undefined) text = Protocol.decode(payload).display();
      let flag = "u", name = sender;
      if (sender && sender.length > 1 && sender[1] === ":" && "gvu".includes(sender[0])) { flag = sender[0]; name = sender.slice(2); }
      const status = r.dir === "tx" ? (r.tx_ok === 1 ? "sent" : r.tx_ok === 0 ? "failed" : "unknown") : null;
      out.push({ ts: r.ts, direction: r.dir, kind: r.kind || "plain", channel: channel || (r.kind === "plain" ? "public" : "other"),
                 sender: name, text, rssi: r.rssi, snr: r.snr, size: payload.length, status, airtime: r.airtime_ms,
                 verified: flag === "v", group: flag === "g", row: r.id });
    }
    return out;
  }

  // -- keys ---------------------------------------------------------------------------------
  keyNewStream(name) { return this.keys.newOwn(name); }
  keyNewGroup(label) { return this.keys.newGroup(label); }
  keyAdd(share0, name = null) {
    const share = share0.trim(), kind = share.split(":")[0];
    if (["lts1", "lts2", "lts3"].includes(kind)) return [this.keys.follow(share, name), "follow"];
    if (kind === "lid1") return [this.keys.addContact(share, name), "contact"];
    if (kind !== "ltg1" && kind !== "ltg2") throw new KeyException("paste a string starting with lts3:, ltg2: or lid1:");
    const label = this.keys.joinGroup(share, name);
    if (this.radio) { try { this.announce(label); } catch (e) { this.onLog(`joined '${label}', but ${e.message}`); } }
    return [label, "group"];
  }
  keyShare(label) { return this.keys.share(label); }
  keyDelete(label) { return this.keys.remove(label); }
  contactDel(name) { return this.keys.removeContact(name); }

  stats() {
    const r = this.radio, lim = (Protocol.profile.dutyCycle || 0) * 100;
    if (!r) return { dutyPct: 0, queue: 0, waitS: 0, airtimeS: 0, dutyLimitPct: lim };
    const wait = Math.max(r.limiter.waitTime(), r.queued() ? r.dutyWait(Protocol.profile.maxPayload) : 0);
    return { dutyPct: r.meter.dutyCycle() * 100, queue: r.queued(), waitS: wait, airtimeS: r.meter.totalMs() / 1000, dutyLimitPct: lim };
  }
}
