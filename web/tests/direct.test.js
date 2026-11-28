// Direct messages and receipts in the web core's Node (radio replaced by a stub).
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const TC = require("../textcast-core.js");
const { MemoryStore } = require("./helpers.js");
const { Node, Protocol, Settings, Hardware, Bytes } = TC;

const hw = Hardware.load(null);
Protocol.setHardwareLimits(hw.maxPayload, hw.firmwareOverhead);
Protocol.applyProfile(Settings.buildProfile({ region: "AS923-2" }));
Node.RECEIPT_DELAY = [0.02, 0.05];
Node.RECEIPT_WAIT = 0.3;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function make(nick) {
  const node = new Node(new MemoryStore(), `keys-${nick}.json`, nick, null, `log-${nick}.json`, { logSaveDelayMs: 0 });
  const air = [];
  let job = 0;
  node.radio = { send(f) { air.push(f); return ++job; }, close() {}, queued: () => 0 };
  node.msgs = []; node.ups = [];
  node.onMsg = (m) => node.msgs.push(m);
  node.onUpdate = (m) => node.ups.push(m.status);
  return { node, air, tx(ok = true) { node._onTx({ jobId: job, ok }); } };
}
const hear = (to, frame) => to.node._onPacket({ payload: frame, rssi: -80, snr: 7, t: Date.now() / 1000 });

test("message, automatic receipt, delivered", async () => {
  const a = make("Tuan"), b = make("Lan");
  a.node.keyAdd(b.node.keys.lid()); b.node.keyAdd(a.node.keys.lid());
  const ch = "@" + b.node.handle;
  assert.deepEqual(a.node.channels().find((c) => c[0] === ch), [ch, "direct"]);
  assert.equal(a.node.channelTitle(ch), "Lan");
  assert.equal(a.node.byteCount(ch, "hi"), 2 + 8);
  const m = a.node.send(ch, "xin chào");
  a.tx();
  assert.equal(m.status, "sent");
  hear(b, a.air[0]);
  assert.deepEqual(b.node.msgs.map((x) => [x.kind, x.sender, x.text, x.channel]), [["direct", "Tuan", "xin chào", "@" + a.node.handle]]);
  await sleep(120);
  assert.equal(b.air.length, 1, "Lan's app confirmed by itself");
  hear(a, b.air[0]);
  assert.equal(m.status, "delivered");
  assert.equal(a.node.msgs.length, 0, "receipts are not shown as messages");
  assert.deepEqual(a.node.history().filter((x) => x.kind === "direct").map((x) => [x.text, x.status]), [["xin chào", "delivered"]]);
  await a.node.stop(); await b.node.stop();
});

test("not confirmed, resend, shown once", async () => {
  const a = make("Tuan"), b = make("Lan");
  a.node.keyAdd(b.node.keys.lid()); b.node.keyAdd(a.node.keys.lid());
  const m = a.node.send("@" + b.node.handle, "are you there?");
  a.tx();
  hear(b, a.air[0]);                              // Lan got it, but her receipt is lost
  await sleep(400);
  assert.equal(m.status, "unconfirmed");
  a.node.resend(m);
  assert.equal(m.status, "queued");
  a.tx();
  hear(b, a.air[1]);
  assert.equal(b.node.msgs.length, 1, "the resent copy isn't shown again");
  await sleep(120);
  hear(a, b.air[b.air.length - 1]);
  assert.equal(m.status, "delivered");
  await a.node.stop(); await b.node.stop();
});

test("someone who isn't the contact can't read it", async () => {
  const a = make("Tuan"), b = make("Lan"), c = make("Minh");
  a.node.keyAdd(b.node.keys.lid()); b.node.keyAdd(a.node.keys.lid()); c.node.keyAdd(a.node.keys.lid());
  a.node.send("@" + b.node.handle, "only Lan");
  hear(c, a.air[0]);
  assert.deepEqual(c.node.msgs.map((x) => x.kind), ["locked"]);
  await sleep(120);
  assert.equal(c.air.length, 0);
  await Promise.all([a, b, c].map((x) => x.node.stop()));
});
