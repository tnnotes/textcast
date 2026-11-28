// TextCast numbers: same values as Python (vectors.json), and finding a person by number.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const TC = require("../textcast-core.js");
const { MemoryStore } = require("./helpers.js");
const { Node, Protocol, Settings, Hardware, Bytes, Numbers, NumberException } = TC;

const v = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "android/app/src/test/resources/vectors.json"), "utf8")).numbers;
const hw = Hardware.load(null);
Protocol.setHardwareLimits(hw.maxPayload, hw.firmwareOverhead);
Protocol.applyProfile(Settings.buildProfile({ region: "AS923-2" }));
Node.ANSWER_DELAY = [0.01, 0.02]; Node.LOOKUP_RETRY = 0.1; Node.LOOKUP_WAIT = 0.3; Node.ANSWER_GAP = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("numbers and frames match Python", () => {
  for (const k of v.keys) {
    assert.equal(Numbers.numberOf(Bytes.unhex(k.pub)), k.number);
    assert.equal(Numbers.format(k.number), k.shown);
    assert.equal(Numbers.parse(k.shown), k.number);
  }
  assert.equal(Bytes.hex(Numbers.makeLookup(v.lookup.number)), v.lookup.frame);
  assert.deepEqual(Numbers.parseFrame(Bytes.unhex(v.lookup.frame)), { op: "lookup", number: v.lookup.number });
  for (const a of v.answers)
    assert.deepEqual(Numbers.makeAnswer(Bytes.unhex(a.pub), a.nick, a.id, a.max).map((f) => Bytes.hex(f)), a.frames);
  for (const g of v.good) Numbers.parse(g);
  for (const b of v.bad) assert.throws(() => Numbers.parse(b), NumberException);
});

function make(nick) {
  const node = new Node(new MemoryStore(), `keys-${nick}.json`, nick, null, `log-${nick}.json`, { logSaveDelayMs: 0 });
  const air = [];
  let job = 0;
  node.radio = { send(f) { air.push(f); return ++job; }, close() {}, queued: () => 0 };
  node.msgs = []; node.found = [];
  node.onMsg = (m) => node.msgs.push(m);
  node.onLookup = (r) => node.found.push(r);
  return { node, air };
}
const hear = (to, frame) => to.node._onPacket({ payload: frame, rssi: -80, snr: 7, t: Date.now() / 1000 });

test("find by number, add, message", async () => {
  const a = make("Tuan"), b = make("Lan");
  assert.equal(a.node.lookup(Numbers.format(b.node.number)).status, "searching");
  hear(b, a.air[0]);
  await sleep(60);
  assert.ok(b.air.length >= 1, "Lan's device answered");
  for (const f of b.air) hear(a, f);
  assert.deepEqual(a.node.found.map((r) => [r.status, r.nick]), [["found", "Lan"]]);
  const ch = a.node.addFound(b.node.number);
  assert.equal(ch, "@" + b.node.handle);
  assert.equal(a.node.keys.contactInfo()[0].verified, false);
  assert.equal(a.node.lookup(b.node.number).status, "known");
  assert.equal(b.node.msgs.length + a.node.msgs.length, 0, "lookups are not messages");
  await sleep(400);
  assert.equal(a.node.found.length, 1, "no 'not found' after an answer");
  await a.node.stop(); await b.node.stop();
});

test("split answer at SF10, not findable, not found", async () => {
  const saved = Protocol.profile.maxPayload;
  const a = make("Tuan"), b = make("Lan");
  Protocol.profile.maxPayload = 23;
  try {
    a.node.lookup(b.node.number);
    hear(b, a.air[0]);
    await sleep(60);
    assert.ok(b.air.length >= 2 && b.air.every((f) => f.length <= 23));
    for (const f of b.air) hear(a, f);
    assert.equal(a.node.found[0].status, "found");
  } finally { Protocol.profile.maxPayload = saved; }
  const c = make("Minh");
  c.node.setFindable(false);
  a.node.lookup(c.node.number);
  hear(c, a.air[a.air.length - 1]);
  await sleep(60);
  assert.equal(c.air.length, 0);
  await sleep(400);
  assert.equal(a.node.found[a.node.found.length - 1].status, "notfound");
  assert.throws(() => a.node.lookup(a.node.number), NumberException);
  await Promise.all([a, b, c].map((x) => x.node.stop()));
});

test("unplug: waiting frames fail, the airtime count survives a replug", async () => {
  const { AirtimeMeter } = TC;
  const a = make("Tuan");
  a.node.radio = null;
  const fakeRadio = () => ({ open() {}, async resetAndCheck() { return []; }, async close() {}, banner: { Firmware: "x" },
    meter: new AirtimeMeter(), limiter: { n: Math.random() }, sent: [], send(f) { this.sent.push(f); return this.sent.length; } });
  const r1 = fakeRadio();
  await a.node.start(r1);
  r1.meter.add(1234);
  const m = a.node.send("public", "hello");
  assert.equal(m.status, "queued");
  await a.node.detachRadio();
  assert.equal(m.status, "failed", "not sent later with a stale counter");
  assert.throws(() => a.node.send("public", "again"));
  const r2 = fakeRadio();
  await a.node.start(r2);
  assert.equal(r2.meter.totalMs(), 1234, "duty cycle carried over");
  assert.equal(r2.limiter, r1.limiter);
  await a.node.stop();
});
