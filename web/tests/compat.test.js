// Byte-for-byte compatibility with the Python apps (same vectors as the Android CompatTest).
//   node --test web/tests/            (after python3 web/build.py)
// With TEXTCAST_COMPAT_OUT=DIR it also writes JS-made files that
//   python3 android/compat/check_kotlin.py DIR
// opens with the Python code.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const TC = require("../textcast-core.js");
const { Bytes, Crypto, Ed25519, Scrypt, Vault, LogCipher, WrongPassphrase, Protocol, Regions, Settings,
        Hardware, La66Parser, Ids, Keyring, airProfile } = TC;

const ROOT = path.join(__dirname, "..", "..");
const v = JSON.parse(fs.readFileSync(path.join(ROOT, "android/app/src/test/resources/vectors.json"), "utf8"));
const t = v.t;
const hx = (o, k) => Bytes.unhex(o[k]);
const H = (b) => Bytes.hex(b);

class MemoryStore {
  constructor() { this.m = new Map(); }
  get(n) { return this.m.has(n) ? this.m.get(n) : null; }
  put(n, b) { this.m.set(n, Uint8Array.from(b)); }
  remove(n) { this.m.delete(n); }
  list() { return [...this.m.keys()]; }
}
module.exports.MemoryStore = MemoryStore;

const la66 = () => { const hw = Hardware.load(null); Protocol.setHardwareLimits(hw.maxPayload, hw.firmwareOverhead); };

test("chacha20 RFC 8439", () => {
  const key = Uint8Array.from({ length: 32 }, (_, i) => i);
  const nonce = Bytes.unhex("000000000000004a00000000");
  const pt = Bytes.utf8("Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.");
  assert.equal(H(Crypto.chacha20Xor(key, nonce, pt, 1)).slice(0, 64), "6e2e359a2568f98041ba0728dd0d6981e97e7aec1d4360c20a27afccfd9fae0b");
});

test("hashes", () => {
  assert.equal(H(Bytes.sha256(Bytes.utf8("abc"))), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.equal(H(Bytes.sha512(Bytes.utf8("abc"))),
    "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f");
  const long = Bytes.utf8("a".repeat(1000));
  assert.equal(H(Bytes.sha512(long)).slice(0, 16), require("node:crypto").createHash("sha512").update(long).digest("hex").slice(0, 16));
});

test("ed25519", () => {
  for (const x of v.ed25519) {
    const seed = hx(x, "seed");
    assert.equal(H(Ed25519.publicKey(seed)), x.pub);
    assert.equal(H(Ed25519.sign(seed, hx(x, "msg"))), x.sig);
    assert.ok(Ed25519.verify(hx(x, "pub"), hx(x, "msg"), hx(x, "sig")));
    assert.ok(!Ed25519.verify(hx(x, "pub"), Bytes.concat(hx(x, "msg"), new Uint8Array([1])), hx(x, "sig")));
  }
});

test("scrypt", () => {
  for (const x of v.scrypt) assert.equal(H(Scrypt.derive(hx(x, "pass"), hx(x, "salt"), x.n, x.r, x.p, x.dklen)), x.out);
});

test("encodings", () => {
  for (const x of v.b32) { assert.equal(Bytes.b32(hx(x, "bytes")), x.b32); assert.equal(H(Bytes.unb32(x.b32)), x.bytes); }
  for (const x of v.fingerprint) {
    assert.equal(Ids.fingerprint(hx(x, "pub")), x.fp);
    assert.equal(Ids.makeHandle("Tuan", hx(x, "pub")), x.handle);
  }
  for (const x of v.derive) assert.equal(H(Crypto.derive(hx(x, "secret"), x.label)), x.out);
  for (const x of v.member_secret) assert.equal(H(Crypto.memberSecret(hx(x, "gs"), x.cs)), x.out);
  const s = Bytes.random(37);
  assert.equal(H(Bytes.unb64(Bytes.b64(s))), H(s));
  assert.equal(Bytes.b64(Bytes.utf8("hello!?")), Buffer.from("hello!?").toString("base64"));
});

test("frames and counters", () => {
  for (const x of v.frames) {
    const k = new Crypto.StreamKey(hx(x, "secret"));
    const frame = Crypto.seal(k, x.mode, x.i, hx(x, "text"), x.encrypt);
    assert.equal(H(frame), x.frame);
    assert.equal(Crypto.findCounter(k, frame, 0, t), x.i);
    assert.equal(H(Crypto.unseal(k, frame, x.i, x.encrypt)), x.text);
  }
  for (const x of v.candidates) {
    const got = [];
    for (const i of Crypto.candidates(x.last_i, t, x.hint)) { got.push(i); if (got.length === 40) break; }
    assert.deepEqual(got, x.first);
  }
});

test("profile maths", () => {
  for (const x of v.airtime) {
    const p = airProfile({ sf: x.sf, bwHz: x.bw, cr: x.cr });
    assert.ok(Math.abs(Protocol.airtimeMs(x.len, p, 1) - x.ms) < 1e-9, JSON.stringify(x));
  }
  for (const x of v.effective) {
    const hw = Hardware.load(x.hw);
    Protocol.setHardwareLimits(hw.maxPayload, hw.firmwareOverhead);
    const pr = Regions.PRESETS[x.region];
    const p = airProfile({ sf: x.sf, bwHz: pr.bwHz, maxDwellMs: pr.maxDwellMs });
    assert.equal(Protocol.effectivePayload(p), x.eff, `${x.hw} ${x.region} SF${x.sf}`);
    if (x.build !== null) assert.equal(Settings.buildProfile({ region: x.region }).maxPayload, x.build);
  }
  la66();
  for (const x of v.builds) {
    const p = Settings.buildProfile(x.radio);
    assert.equal(p.name, x.name);
    assert.equal(p.freqMhz, x.freq); assert.equal(p.sf, x.sf); assert.equal(p.bwHz, x.bw);
    assert.equal(p.txPowerDbm, x.power); assert.equal(p.maxDwellMs, x.dwell); assert.equal(p.dutyCycle, x.duty);
    assert.equal(p.maxPayload, x.max);
  }
});

test("plain frames and decode", () => {
  la66();
  Protocol.applyProfile(airProfile());
  for (const x of v.plain) assert.equal(H(Protocol.encodePlain(Protocol.compose(x.text, x.cs))), x.frame);
  for (const x of v.decode) {
    const d = Protocol.decode(hx(x, "frame"));
    assert.equal(d.kind, x.kind, x.frame);
    assert.equal(d.text, x.text);
    assert.equal(d.callsign, x.callsign);
    assert.deepEqual(d.tokens, x.tokens);
    assert.equal(d.display(), x.display);
  }
});

test("hardware and LA66 parser", () => {
  la66();
  const p = Settings.buildProfile({ region: "AS923-2" });
  assert.deepEqual(La66Parser.atCommands(p), v.la66_at);
  for (const [name, r] of Object.entries(v.render)) {
    const hw = Hardware.load(name);
    assert.deepEqual(Hardware.renderAll(hw.init, p), r.init, name);
    assert.deepEqual(Hardware.renderAll(hw.rxStart, p), r.rx_start, name);
    if (hw.send) assert.equal(Hardware.render(hw.send, p, Bytes.unhex("01ab"), hw.hexUpper), r.send, name);
    assert.deepEqual(Hardware.validate(hw), [], name);
  }
  for (const x of v.la66_parse) {
    const got = La66Parser.parse(x.line).map((e) => e.map((y) => (y instanceof Uint8Array ? H(y) : y)));
    assert.deepEqual(got, x.events, x.line);
  }
  for (const key of ["banner_ok", "banner_bad"]) assert.deepEqual(La66Parser.checkBanner(v[key].banner, p), v[key].problems, key);
  const rx = Hardware.regex("\\+EVT:RXP2P:(?P<rssi>-?\\d+):(?:x)?(?<snr>-?\\d+):[(](?P<payload>[0-9A-F]+)");
  const m = rx.exec("+EVT:RXP2P:-80:5:(ABCD");
  assert.deepEqual([m.groups.rssi, m.groups.snr, m.groups.payload], ["-80", "5", "ABCD"]);
});

test("keyring interop with Python", () => {
  la66();
  Protocol.applyProfile(Settings.buildProfile({ region: "AS923-2" }));
  const kr = v.keyring;
  const st = new MemoryStore();
  st.put("keys-Tuan.json", Bytes.utf8(kr.tuan_json));
  st.put("keys-Lan.json", Bytes.utf8(kr.lan_json));
  const tuan = new Keyring(st, "keys-Tuan.json", "Tuan");
  assert.equal(tuan.handle, kr.tuan_handle);
  assert.equal(tuan.lid(), kr.tuan_lid);
  assert.equal(tuan.fingerprint(), kr.tuan_fp);
  assert.equal(tuan.share("news"), kr.share_news);           // Ed25519 signatures are deterministic
  assert.equal(tuan.share("team"), kr.share_team);
  for (const k of ["lan_group_sealed", "lan_group_signed", "lan_join"]) {
    const o = tuan.open(Bytes.unhex(kr.frames[k]), kr.t);
    assert.ok(o, k);
    const e = kr.expected[k];
    assert.equal(o.kind, e.kind, k); assert.equal(o.source, e.source); assert.equal(o.sender, e.sender);
    assert.equal(o.text, e.text); assert.equal(o.counter, e.counter); assert.equal(o.group, e.group);
  }
  assert.equal(tuan.open(Bytes.unhex(kr.frames.lan_group_sealed), kr.t), null, "replay must be refused");
  const lan = new Keyring(st, "keys-Lan.json", "Lan");
  assert.equal(lan.lid(), kr.lan_lid);
  assert.equal(lan.share("news"), kr.lan_share_news);
  const o = lan.open(Bytes.unhex(kr.frames.tuan_news), kr.t);
  assert.equal(o.text, kr.expected.tuan_news.text);
  assert.ok(o.verified);
  tuan.save();
  assert.equal(new Keyring(st, "keys-Tuan.json", "Tuan").share("team"), kr.share_team);

  // files for Python to check (check_kotlin.py DIR)
  const out = process.env.TEXTCAST_COMPAT_OUT;
  if (!out) return;
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  const t2 = new Keyring(st, "keys-Tuan.json", "Tuan");
  const st2 = new MemoryStore();
  const minh = new Keyring(st2, "keys-Minh.lkey", "Minh", "minh-passphrase");
  const res = {
    tuan_group_sealed: H(t2.sealFor("team", "từ Android")),
    tuan_news_signed: H(t2.sealFor("news", "signed from Kotlin", true)),
    tuan_join: H(t2.makeJoin("team")),
    minh_lid: minh.lid(), minh_share_radio: minh.newOwn("radio"), minh_share_lab: minh.newGroup("lab"),
    minh_handle: minh.handle,
    vault_blob: H(Vault.create("kotlin-pass").seal({ me: "K", n: 5 })),
    logcipher: new LogCipher(Bytes.unhex(v.logcipher.secret)).encrypt("ghi log"),
  };
  fs.writeFileSync(path.join(out, "kotlin.json"), JSON.stringify(res, null, 1));
  fs.writeFileSync(path.join(out, "keys-Minh.lkey"), st2.get("keys-Minh.lkey"));
  fs.writeFileSync(path.join(out, "keys-Lan.json"), kr.lan_json);
});

test("vault and log cipher", () => {
  const x = v.vault;
  const [data, vault] = Vault.open(hx(x, "blob"), x.pass);
  assert.deepEqual(data, x.json);
  assert.deepEqual(Vault.open(vault.seal(data), x.pass)[0], x.json);
  assert.throws(() => Vault.open(hx(x, "blob"), "wrong-pass"), WrongPassphrase);
  const lc = new LogCipher(hx(v.logcipher, "secret"));
  assert.equal(lc.decrypt(v.logcipher.enc), v.logcipher.text);
  assert.equal(lc.decrypt(lc.encrypt("abc")), "abc");
});

test("keyring lifecycle", () => {
  const st = new MemoryStore();
  const a = new Keyring(st, "keys-An.lkey", "An", "an-passphrase");
  const share = a.newGroup("g1");
  a.wipe();
  assert.ok(a.locked);
  assert.throws(() => new Keyring(st, "keys-An.lkey", "An", "bad-passphrase"), WrongPassphrase);
  const b = new Keyring(st, "keys-An.lkey", "An", "an-passphrase");
  assert.equal(b.share("g1"), share);
  st.put("keys-An2.lkey", b.exportBlob("export-pass"));
  assert.equal(new Keyring(st, "keys-An2.lkey", null, "export-pass").share("g1"), share);
});
