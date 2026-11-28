// ---------------------------------------------------------------------------------
// Keyring (port of keyring.py): identity, own streams, followed streams, groups,
// contacts. The JSON content is the same as on the desktop and Android, so an
// identity file (.ltid / .lkey) moves between all three.
//
// Storage is a small synchronous key-value store: get(name) -> Uint8Array|null,
// put(name, bytes), remove(name), list(). The browser uses localStorage.
// ---------------------------------------------------------------------------------

const Ids = (() => {
  const NICK = /^[^\s:#,=]{1,16}$/u;
  function checkNick(nick) {
    if (nick !== null && nick !== undefined && !NICK.test(nick))
      throw new KeyException("nick must be 1-16 characters, no spaces or : # , =");
  }
  function fingerprint(pub) {
    const f = Bytes.b32(Bytes.sha256(pub)).toUpperCase().slice(0, 12);
    return `${f.slice(0, 4)}-${f.slice(4, 8)}-${f.slice(8)}`;
  }
  const tagOf = (pub) => fingerprint(pub).slice(0, 4);
  const makeHandle = (nick, pub) => `${nick}#${tagOf(pub)}`;
  const handleTag = (h) => (h.includes("#") ? h.slice(h.lastIndexOf("#") + 1) : h).toUpperCase();
  function parseLid(s) {
    const parts = s.trim().split(":");
    if (parts.length !== 3 || parts[0] !== "lid1") throw new KeyException("not an identity string (lid1:handle:key)");
    let pub;
    try { pub = Bytes.unb32(parts[2]); } catch (e) { throw new KeyException("identity string is damaged"); }
    if (pub.length !== 32 || handleTag(parts[1]) !== tagOf(pub))
      throw new KeyException("identity string is damaged (tag does not match key)");
    return [parts[1], pub];
  }
  return { checkNick, fingerprint, tagOf, makeHandle, handleTag, parseLid };
})();

// TextCast numbers (port of numbers.py): a 10-digit name computed from the key,
//   scrypt(pub, "textcast/number/v1", 2^14, 8, 1, 8 B) -> 9 digits + Luhn check digit.
// Lookup frames (type DIRECTORY = 3, version 1):
//   lookup [mode]['Q'][number 5 B]   answer [mode]['A'][id][part<<4|parts][chunk of pub‖nick]
class NumberException extends Error {}
const Numbers = (() => {
  const SALT = Bytes.utf8("textcast/number/v1");
  const DIRECTORY = 3, OP_LOOKUP = 0x51, OP_ANSWER = 0x41, HEAD = 4;
  const cache = new Map();
  function luhn(digits) {
    let total = 0;
    for (let k = 0; k < digits.length; k++) {
      let d = digits.charCodeAt(digits.length - 1 - k) - 48;
      if (k % 2 === 0) { d *= 2; if (d > 9) d -= 9; }
      total += d;
    }
    return (10 - (total % 10)) % 10;
  }
  function numberOf(pub) {
    const key = Bytes.hex(pub);
    if (cache.has(key)) return cache.get(key);
    const d = Scrypt.derive(pub, SALT, 1 << 14, 8, 1, 8);
    let v = 0n;
    for (const b of d) v = (v << 8n) | BigInt(b);
    const body = String(100000000n + (v % 900000000n));
    const n = body + luhn(body);
    cache.set(key, n);
    return n;
  }
  const format = (n) => (n && n.length === 10 ? `${n.slice(0, 3)} ${n.slice(3, 6)} ${n.slice(6)}` : n);
  function parse(s) {
    const n = String(s || "").replace(/[\s.\-()+]/g, "");
    if (!/^\d{10}$/.test(n)) throw new NumberException("a TextCast number has 10 digits");
    if (n[0] === "0" || luhn(n.slice(0, 9)) !== n.charCodeAt(9) - 48)
      throw new NumberException("that number has a typo (check digit does not match)");
    return n;
  }
  const mode = () => Protocol.makeMode(DIRECTORY, 0, 1);
  function makeLookup(n) {
    let v = BigInt(n);
    const out = new Uint8Array(7);
    out[0] = mode(); out[1] = OP_LOOKUP;
    for (let k = 6; k >= 2; k--) { out[k] = Number(v & 0xffn); v >>= 8n; }
    return out;
  }
  function makeAnswer(pub, nick, id, maxPayload) {
    const data = Bytes.concat(pub, Bytes.utf8(nick));
    const size = Math.max(1, maxPayload - HEAD);
    const chunks = [];
    for (let k = 0; k < data.length; k += size) chunks.push(data.slice(k, k + size));
    if (chunks.length > 15) throw new NumberException("radio profile too small for a number answer");
    return chunks.map((c, j) => Bytes.concat(Uint8Array.of(mode(), OP_ANSWER, id & 0xff, (j << 4) | chunks.length), c));
  }
  const isDirectory = (f) => f.length >= 1 && (f[0] >> 6) === 1 && ((f[0] >> 3) & 7) === DIRECTORY;
  function parseFrame(f) {
    if (f.length < 2 || !isDirectory(f)) return null;
    if (f[1] === OP_LOOKUP && f.length === 7) {
      let v = 0n;
      for (let k = 2; k < 7; k++) v = (v << 8n) | BigInt(f[k]);
      return v >= 1000000000n && v <= 9999999999n ? { op: "lookup", number: String(v) } : null;
    }
    if (f[1] === OP_ANSWER && f.length > HEAD) {
      const part = f[3] >> 4, parts = f[3] & 15;
      if (parts && part < parts) return { op: "answer", id: f[2], part, parts, chunk: f.slice(HEAD) };
    }
    return null;
  }
  return { numberOf, format, parse, makeLookup, makeAnswer, parseFrame, isDirectory, luhn };
})();

// Direct (one-to-one) messages: plaintext inside the sealed frame starts with
const DM = { TEXT: 1, RESEND: 2, RECEIPT: 3, MAX_ACKS: 4 };
const DM_INNER = { 1: "text", 2: "resend", 3: "receipt" };
const dmId = (counter) => counter % 4294967296;          // low 32 bits of the counter

class Keyring {
  constructor(store, name, me = null, passphrase = null) {
    this.store = store; this.name = name; this.me = me;
    this.encrypted = name.endsWith(".lkey");
    this.locked = false; this.vault = null; this.cache = new Map(); this.seenCtrl = new Set(); this.pairs = new Map();
    this.dirty = false; this.lastSave = 0; this.floor = Crypto.startupFloor();
    this._load(passphrase);
  }

  static empty(me) { return { me, own: {}, follow: {}, groups: {}, contacts: {} }; }

  _load(passphrase) {
    const d = Keyring.empty(this.me);
    let dirtyNow = false;
    const raw = this.store.get(this.name);
    if (raw) {
      let loaded;
      if (this.encrypted) {
        if (!passphrase) throw new LockedException("passphrase required");
        const [l, v] = Vault.open(raw, passphrase);
        loaded = l; this.vault = v;
      } else loaded = JSON.parse(Bytes.utf8Lenient(raw));
      for (const k of ["own", "follow", "groups", "contacts"]) Object.assign(d[k], loaded[k] || {});
      d.id_seed = loaded.id_seed;
      const stored = loaded.me ?? null;
      if (stored && this.me && stored.toLowerCase() !== this.me.toLowerCase())
        throw new KeyException(`${this.name} belongs to callsign '${stored}', not '${this.me}'`);
      d.me = stored ?? this.me;
      d.log_secret = loaded.log_secret;
      for (const [k, v] of Object.entries(loaded)) if (!(k in d)) d[k] = v;   // keys from newer apps
    } else {
      if (this.encrypted) {
        if (!passphrase) throw new KeyException("a passphrase is needed to create a protected identity");
        this.vault = Vault.create(passphrase);
      }
      dirtyNow = true;
    }
    if (!d.log_secret) { d.log_secret = Bytes.b32(Bytes.random(32)); dirtyNow = true; }
    if (!d.id_seed) { d.id_seed = Bytes.b32(Bytes.random(32)); dirtyNow = true; }
    for (const g of Object.values(d.groups)) {            // upgrade old member format
      g.members = g.members || {};
      for (const [m, v] of Object.entries(g.members))
        if (typeof v === "number") g.members[m] = { i: v, pub: null, name: m };
    }
    Ids.checkNick(d.me);
    this.data = d; this.me = d.me ?? null; this.locked = false;
    this.pub = Ed25519.publicKey(Bytes.unb32(d.id_seed));
    this._findable = d.findable !== false;            // kept while locked
    if (dirtyNow) this.save();
  }

  // -- persistence ---------------------------------------------------------------------
  save() {
    if (this.locked) throw new LockedException("identity is locked");
    const bytes = this.encrypted ? this.vault.seal(this.data) : Bytes.utf8(JSON.stringify(this.data, null, 2));
    this.store.put(this.name, bytes);
    this.dirty = false; this.lastSave = Date.now() / 1000;
  }
  touch() { this.dirty = true; if (Date.now() / 1000 - this.lastSave >= 30) this.save(); }
  close() { if (this.dirty && !this.locked) try { this.save(); } catch (e) { /* ignore */ } }
  wipe() {
    if (this.locked) return;
    this.save();
    this.data = Keyring.empty(this.me); this.vault = null; this.cache.clear(); this.pairs.clear(); this.locked = true;
  }
  unlock(passphrase) { this._load(passphrase); }
  changePassphrase(newPass) {
    if (!this.encrypted) throw new KeyException("this identity is not passphrase-protected");
    this._requireUnlocked();
    this.vault = Vault.create(newPass);
    this.save();
  }
  exportBlob(passphrase) { this._requireUnlocked(); return Vault.create(passphrase).seal(this.data); }
  logCipher() { return this.locked ? null : new LogCipher(Bytes.unb32(this.data.log_secret)); }
  _requireUnlocked() { if (this.locked) throw new LockedException("identity is locked - unlock first"); }
  _key(secret) {
    const h = Bytes.hex(secret);
    if (!this.cache.has(h)) this.cache.set(h, new Crypto.StreamKey(secret));
    return this.cache.get(h);
  }
  _sec(name) { return this.data[name]; }

  // -- identity --------------------------------------------------------------------------
  get handle() { return this.me ? Ids.makeHandle(this.me, this.pub) : null; }
  lid() {
    if (!this.handle) throw new KeyException("this identity has no nick");
    return `lid1:${this.handle}:${Bytes.b32(this.pub)}`;
  }
  fingerprint() { return Ids.fingerprint(this.pub); }
  get number() {
    if (!this._number) {
      const d = this.data;
      if (d.number && d.number_pub === Bytes.b32(this.pub)) this._number = d.number;
      else {
        this._number = Numbers.numberOf(this.pub);
        if (!this.locked) { d.number = this._number; d.number_pub = Bytes.b32(this.pub); this.dirty = true; }
      }
    }
    return this._number;
  }
  get findable() { return this._findable; }
  setFindable(on) { this._requireUnlocked(); this.data.findable = this._findable = !!on; this.save(); }
  _myKey() {
    if (!this.handle) throw new KeyException("set a nick to post in groups");
    return this.handle.toLowerCase();
  }

  // -- contacts ----------------------------------------------------------------------------
  // A pasted / scanned identity string holds the full key: the contact is verified.
  addContact(lid, name = null, verified = true) {
    this._requireUnlocked();
    const [h, p] = Ids.parseLid(lid);
    const n = ((name && name.trim()) || h.split("#")[0]).trim();
    const k = Bytes.b32(p);
    const old = this._sec("contacts")[k] || {};
    this._sec("contacts")[k] = { name: n, handle: h, dm_out: old.dm_out || 0, dm_in: old.dm_in || 0,
      verified: !!(verified || old.verified), number: old.number || Numbers.numberOf(p) };
    this.pairs.delete(k);
    this.save();
    return n;
  }
  // Found by number over the air: check the key really has that number, and no other
  // saved contact has it (that would mean somebody is faking it).
  addFound(pub, nick, number, name = null) {
    Ids.checkNick(nick);
    if (Numbers.numberOf(pub) !== number) throw new KeyException("the answer does not match that number");
    for (const [k, c] of Object.entries(this._sec("contacts")))
      if (k !== Bytes.b32(pub) && this._contactNumber(k, c) === number)
        throw new KeyException(`${c.name} already has number ${Numbers.format(number)} with a different key - check in person`);
    return this.addContact(`lid1:${Ids.makeHandle(nick, pub)}:${Bytes.b32(pub)}`, name, false);
  }
  _contactNumber(k, c) {
    if (!c.number) { c.number = Numbers.numberOf(Bytes.unb32(k)); this.dirty = true; }
    return c.number;
  }
  contactByNumber(number) {
    for (const [k, c] of Object.entries(this._sec("contacts"))) if (this._contactNumber(k, c) === number) return c;
    return null;
  }
  contactInfo() {
    const me = Bytes.b32(this.pub);
    return Object.entries(this._sec("contacts")).filter(([k]) => k !== me)
      .map(([k, c]) => ({ name: c.name, handle: c.handle, number: this._contactNumber(k, c), verified: c.verified !== false }))
      .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  }
  renameContact(handle, name) {
    this._requireUnlocked();
    if (!name || !name.trim()) throw new KeyException("a name is needed");
    this._contact(handle)[1].name = name.trim();
    this.save();
  }
  removeContact(name) {
    this._requireUnlocked();
    const c = this._sec("contacts");
    for (const [k, m] of Object.entries(c))
      if (m.name === name || (m.handle || "").toLowerCase() === name.toLowerCase()) { delete c[k]; this.pairs.delete(k); this.save(); return true; }
    return false;
  }
  contacts() {
    return Object.entries(this._sec("contacts")).map(([k, m]) => [m.name || "", m.handle || "", Ids.fingerprint(Bytes.unb32(k))]);
  }
  display(handle, pub) {
    if (pub) { const c = this._sec("contacts")[Bytes.b32(pub)]; if (c && c.name) return c.name; }
    return handle;
  }

  // -- direct (one-to-one) messages (see keyring.py) -----------------------------------------
  //   pair = HMAC(a_me * A_peer, "lora-text/pair/" + h1 + "|" + h2)[:16]   (handles lower, sorted)
  //   each direction: member_secret(pair, sender handle); ordinary SEALED frames
  directContacts() {
    const me = Bytes.b32(this.pub);
    return Object.entries(this._sec("contacts")).filter(([k]) => k !== me)
      .map(([, c]) => [c.handle, c.name]).sort((a, b) => a[1].toLowerCase().localeCompare(b[1].toLowerCase()));
  }
  _contact(handle) {
    for (const [k, c] of Object.entries(this._sec("contacts")))
      if (c.handle.toLowerCase() === handle.toLowerCase()) return [k, c];
    throw new KeyException(`'${handle}' is not one of your contacts`);
  }
  _dmKeys(key, c) {
    if (!this.pairs.has(key)) {
      let secrets = null;
      if (this.me && key !== Bytes.b32(this.pub)) {
        const s = Ed25519.sharedPoint(Bytes.unb32(this.data.id_seed), Bytes.unb32(key));
        if (s) {
          const me = this.handle.toLowerCase(), peer = c.handle.toLowerCase();
          const [h1, h2] = [me, peer].sort();
          const pair = Crypto.derive(s, `pair/${h1}|${h2}`).slice(0, 16);
          secrets = [Crypto.memberSecret(pair, me), Crypto.memberSecret(pair, peer)];
        }
      }
      this.pairs.set(key, secrets);
    }
    return this.pairs.get(key);
  }
  _sealDm(handle, plaintext) {
    this._requireUnlocked();
    const [key, c] = this._contact(handle);
    const keys = this._dmKeys(key, c);
    if (!keys) throw new KeyException(`cannot message ${c.name}: their identity key is not usable`);
    const i = Crypto.nextCounter(c.dm_out || 0, this.floor);
    const mode = Protocol.makeMode(MsgType.SEALED, 0, Protocol.PRIVATE_VERSION);
    const frame = Crypto.seal(this._key(keys[0]), mode, i, plaintext, true);
    c.dm_out = i;
    this.save();
    return [frame, i];
  }
  sealDirect(handle, text, resend = false) {
    const body = Bytes.utf8(Protocol.normalize(text.trim()));
    const limit = Protocol.profile.maxPayload - Crypto.FRAME_OVERHEAD - 1;
    if (body.length > limit) throw new PayloadTooLong(`${body.length} B of text > ${limit} B limit for a direct message`);
    return this._sealDm(handle, Bytes.concat(new Uint8Array([resend ? DM.RESEND : DM.TEXT]), body));
  }
  makeReceipt(handle, ids) {
    const parts = [new Uint8Array([DM.RECEIPT])];
    for (const i of ids.slice(0, DM.MAX_ACKS)) parts.push(Bytes.intBE(dmId(i)));
    return this._sealDm(handle, Bytes.concat(...parts))[0];
  }
  _openDirect(frame, t) {
    for (const [key, c] of Object.entries(this._sec("contacts"))) {
      const keys = this._dmKeys(key, c);
      if (!keys) continue;
      const k = this._key(keys[1]);
      for (const i of Crypto.matchingCounters(k, frame, c.dm_in || 0, t)) {
        const pt = Crypto.unseal(k, frame, i, true);
        if (!pt.length || !(pt[0] in DM_INNER)) continue;
        let text = "", acks = [];
        if (pt[0] === DM.RECEIPT) {
          const n = pt.length - 1;
          if (n === 0 || n % 4 || n > 4 * DM.MAX_ACKS) continue;
          for (let j = 0; j < n / 4; j++) acks.push(Bytes.readIntBE(pt, 1 + 4 * j));
        } else {
          text = Bytes.utf8Strict(pt, 1);
          if (text === null) continue;
        }
        c.dm_in = i;
        this.touch();
        return { kind: "direct", source: "@" + c.handle, sender: c.name, text, counter: i, pub: Bytes.unb32(key),
                 verified: true, group: false, inner: DM_INNER[pt[0]], acks };
      }
    }
    return null;
  }

  // -- management ----------------------------------------------------------------------------
  _checkFree(label) {
    this._requireUnlocked();
    if (["own", "follow", "groups"].some((s) => label in this._sec(s))) throw new KeyException(`label '${label}' already exists`);
    if (!label || /[:# ,=]/.test(label) || ["public", "all", "other"].includes(label))
      throw new KeyException("label must be one word without : # , = and not public/all/other");
  }
  newOwn(name) {
    this._checkFree(name);
    if (!this.me) throw new KeyException("set a nick before creating a stream");
    this._sec("own")[name] = { secret: Bytes.b32(Bytes.random(16)), last_i: 0 };
    this.save();
    return this.share(name);
  }
  static _streamMsg(handle, pubB32, label, secret) { return Bytes.utf8(`lts3:${handle}:${pubB32}:${label}:${secret}`); }
  follow(share, label = null) {
    const parts = share.trim().split(":");
    let entry, slabel, secret;
    if ((parts[0] === "lts3" && parts.length === 6) || (parts[0] === "lts2" && parts.length === 5)) {
      const [, h, pubB32] = parts; slabel = parts[3]; secret = parts[4];
      let p;
      try { p = Bytes.unb32(pubB32); } catch (e) { throw new KeyException("stream string is damaged"); }
      if (p.length !== 32 || Ids.handleTag(h) !== Ids.tagOf(p)) throw new KeyException("stream string is damaged (tag does not match key)");
      entry = { owner: h, pub: pubB32, verified: false };
      if (parts[0] === "lts3") {
        let sig;
        try { sig = Bytes.unb32(parts[5]); } catch (e) { sig = new Uint8Array(0); }
        if (!Ed25519.verify(p, Keyring._streamMsg(h, pubB32, slabel, secret), sig))
          throw new KeyException(`stream string signature is invalid - it was not made by ${h}'s key (or was altered)`);
        entry.verified = true; entry.sig = parts[5];
      }
    } else if (parts.length === 3 && parts[0] === "lts1") {
      slabel = parts[1]; secret = parts[2];
      entry = { owner: parts[1], pub: null, verified: false };
    } else throw new KeyException("not a stream string (lts3:…)");
    const l = (label && label.trim()) || slabel;
    this._checkFree(l);
    try { Bytes.unb32(secret); } catch (e) { throw new KeyException("stream secret is damaged"); }
    Object.assign(entry, { secret, last_i: 0, label: slabel });
    this._sec("follow")[l] = entry;
    this.save();
    return l;
  }
  static _member(i, pub, name) { return { i, pub, name }; }
  newGroup(label) {
    this._checkFree(label);
    const mk = this._myKey();
    this._sec("groups")[label] = { secret: Bytes.b32(Bytes.random(16)),
                                    members: { [mk]: Keyring._member(0, Bytes.b32(this.pub), this.handle) } };
    this.save();
    return this.share(label);
  }
  joinGroup(share, label = null) {
    const parts = share.trim().split(":");
    if (parts.length !== 4 || (parts[0] !== "ltg1" && parts[0] !== "ltg2")) throw new KeyException("not a group string (ltg2:… or ltg1:…)");
    const l = (label && label.trim()) || parts[1];
    this._checkFree(l);
    try { Bytes.unb32(parts[2]); } catch (e) { throw new KeyException("group secret is damaged"); }
    const members = {};
    for (const item of parts[3].split(",").map((s) => s.trim()).filter(Boolean)) {
      const eq = item.indexOf("=");
      const name = eq >= 0 ? item.slice(0, eq) : item;
      const p = eq >= 0 ? item.slice(eq + 1) : "";
      members[name.toLowerCase()] = Keyring._member(0, p || null, name);
    }
    if (this.me) { const mk = this._myKey(); if (!members[mk]) members[mk] = Keyring._member(0, Bytes.b32(this.pub), this.handle); }
    this._sec("groups")[l] = { secret: parts[2], members };
    this.save();
    return l;
  }
  addMember(group, name) {
    this._requireUnlocked();
    const g = this._sec("groups")[group];
    if (!g) throw new KeyException(`no group '${group}'`);
    const n = name.trim();
    if (!g.members[n.toLowerCase()]) g.members[n.toLowerCase()] = Keyring._member(0, null, n);
    this.save();
  }
  remove(label) {
    this._requireUnlocked();
    for (const s of ["own", "follow", "groups"]) if (label in this._sec(s)) { delete this._sec(s)[label]; this.save(); return true; }
    return false;
  }
  share(label) {
    this._requireUnlocked();
    const o = this._sec("own")[label];
    if (o) {
      const pubB32 = Bytes.b32(this.pub);
      const sig = Ed25519.sign(Bytes.unb32(this.data.id_seed), Keyring._streamMsg(this.handle, pubB32, label, o.secret));
      return `lts3:${this.handle}:${pubB32}:${label}:${o.secret}:${Bytes.b32(sig)}`;
    }
    const g = this._sec("groups")[label];
    if (g) {
      const keys = Object.keys(g.members).sort();
      const members = keys.map((k) => `${g.members[k].name}=${g.members[k].pub || ""}`).join(",");
      return `ltg2:${label}:${g.secret}:${members}`;
    }
    const f = this._sec("follow")[label];
    if (f) {
      const slabel = f.label || label;
      if (f.sig) return `lts3:${f.owner}:${f.pub}:${slabel}:${f.secret}:${f.sig}`;
      if (f.pub) return `lts2:${f.owner}:${f.pub}:${slabel}:${f.secret}`;
      return `lts1:${slabel}:${f.secret}`;
    }
    throw new KeyException(`no key '${label}'`);
  }
  listing() {
    const out = [];
    for (const n of Object.keys(this._sec("own"))) out.push([n, "own", "your stream (you post; followers read)"]);
    for (const [n, f] of Object.entries(this._sec("follow")))
      out.push([n, "follow", `by ${f.owner || n}` + (f.verified ? " ✓" : " (owner not verified)")]);
    for (const [n, g] of Object.entries(this._sec("groups")))
      out.push([n, "group", "members: " + Object.values(g.members).map((m) => m.name || "?").join(", ")]);
    return out;
  }
  groups() { return Object.keys(this._sec("groups")); }
  own() { return Object.keys(this._sec("own")); }
  followed() { return Object.keys(this._sec("follow")); }

  // -- sending ---------------------------------------------------------------------------------
  _senderState(target) {
    const o = this._sec("own")[target];
    if (o) return { secret: Bytes.unb32(o.secret), holder: o, field: "last_i" };
    const g = this._sec("groups")[target];
    if (g) {
      const mk = this._myKey();
      if (!g.members[mk]) g.members[mk] = Keyring._member(0, Bytes.b32(this.pub), this.handle);
      return { secret: Crypto.memberSecret(Bytes.unb32(g.secret), mk), holder: g.members[mk], field: "i" };
    }
    throw new KeyException(`'${target}' is not one of your streams or groups`);
  }
  sealFor(target, text, signed = false) {
    this._requireUnlocked();
    const body = Bytes.utf8(Protocol.normalize(text.trim()));
    const limit = Protocol.profile.maxPayload - Crypto.FRAME_OVERHEAD;
    if (body.length > limit) throw new PayloadTooLong(`${body.length} B of text > ${limit} B limit for ${target}`);
    const st = this._senderState(target);
    const i = Crypto.nextCounter(st.holder[st.field] || 0, this.floor);
    const mode = Protocol.makeMode(signed ? MsgType.SIGNED : MsgType.SEALED, 0, Protocol.PRIVATE_VERSION);
    const frame = Crypto.seal(this._key(st.secret), mode, i, body, !signed);
    st.holder[st.field] = i;
    this.save();
    return frame;
  }

  // -- group join announcements -------------------------------------------------------------------
  _ctrlKeys(group) {
    const gs = Bytes.unb32(this._sec("groups")[group].secret);
    return [Crypto.derive(gs, "control/enc"), Crypto.derive(gs, "control/mac")];
  }
  makeJoin(group, minute = Crypto.minuteNow(), nonce = Bytes.random(8)) {
    this._requireUnlocked();
    this._myKey();
    const [enc, mac] = this._ctrlKeys(group);
    const pt = Bytes.concat(Bytes.utf8("J"), Bytes.intBE(minute), this.pub, Bytes.utf8(this.handle));
    const ct = Crypto.chacha20Xor(enc.slice(0, 32), Bytes.concat(nonce, new Uint8Array(4)), pt);
    const mode = Protocol.makeMode(MsgType.SEALED, 0, Protocol.PRIVATE_VERSION);
    const body = Bytes.concat(nonce, ct);
    const tag = Bytes.hmacSha256(mac, Bytes.concat(new Uint8Array([mode]), body)).slice(0, Crypto.TAG_LEN);
    const frame = Bytes.concat(new Uint8Array([mode]), body, tag);
    if (frame.length > Protocol.profile.maxPayload)
      throw new KeyException(`the join announcement (${frame.length} B) does not fit this radio profile ` +
        `(${Protocol.profile.maxPayload} B max) - members must add you by hand: ${this.handle}`);
    return frame;
  }
  _openCtrl(frame, t) {
    if (frame.length < 1 + 8 + 5 + 32 + 3 + Crypto.TAG_LEN) return null;
    const body = frame.slice(1, frame.length - Crypto.TAG_LEN), tag = frame.slice(frame.length - Crypto.TAG_LEN);
    for (const label of Object.keys(this._sec("groups"))) {
      const [enc, mac] = this._ctrlKeys(label);
      const want = Bytes.hmacSha256(mac, Bytes.concat(frame.slice(0, 1), body)).slice(0, Crypto.TAG_LEN);
      if (!Bytes.equal(want, tag)) continue;
      const nonce = body.slice(0, 8), nk = Bytes.hex(nonce);
      if (this.seenCtrl.has(nk)) return null;
      const pt = Crypto.chacha20Xor(enc.slice(0, 32), Bytes.concat(nonce, new Uint8Array(4)), body.slice(8));
      if (pt[0] !== 0x4a || Math.abs(Bytes.readIntBE(pt, 1) - Crypto.minuteNow(t)) > 10) return null;
      const p = pt.slice(5, 37), h = Bytes.utf8Lenient(pt.slice(37));
      if (Ids.handleTag(h) !== Ids.tagOf(p)) return null;
      this.seenCtrl.add(nk);
      const members = this._sec("groups")[label].members;
      const k = h.toLowerCase(), old = members[k], pB32 = Bytes.b32(p);
      if (old && old.pub && old.pub !== pB32)
        return { kind: "join", source: label, sender: h, text: `rejected: ${h} announced a different key`, counter: 0, pub: null, verified: false, group: true };
      const isNew = !old || !old.pub;
      members[k] = Keyring._member(old ? old.i || 0 : 0, pB32, h);
      this.save();
      return { kind: "join", source: label, sender: h, text: isNew ? `${h} joined` : `${h} is here`, counter: 0, pub: p, verified: false, group: true };
    }
    return null;
  }

  // -- receiving -----------------------------------------------------------------------------------
  _receiveStreams() {
    const out = [];
    for (const [label, e] of Object.entries(this._sec("follow")))
      out.push({ source: label, sender: e.owner || label, pub: e.pub ? Bytes.unb32(e.pub) : null,
                 secret: Bytes.unb32(e.secret), holder: e, field: "last_i", group: false, verified: !!e.verified });
    const mine = this.handle ? this.handle.toLowerCase() : null;
    for (const [label, g] of Object.entries(this._sec("groups"))) {
      const gs = Bytes.unb32(g.secret);
      for (const [k, m] of Object.entries(g.members)) {
        if (k === mine) continue;
        out.push({ source: label, sender: m.name || k, pub: m.pub ? Bytes.unb32(m.pub) : null,
                   secret: Crypto.memberSecret(gs, k), holder: m, field: "i", group: true, verified: false });
      }
    }
    return out;
  }
  open(frame, t) {
    if (this.locked || !frame.length) return null;
    const [version, type] = Protocol.parseMode(frame[0]);
    if (version !== Protocol.PRIVATE_VERSION || (type !== MsgType.SIGNED && type !== MsgType.SEALED) ||
        frame.length < Crypto.FRAME_OVERHEAD) return null;
    const encrypted = type === MsgType.SEALED;
    if (encrypted) {
      const c = this._openCtrl(frame, t); if (c) return c;
      const d = this._openDirect(frame, t); if (d) return d;
    }
    for (const rx of this._receiveStreams()) {
      const k = this._key(rx.secret);
      for (const i of Crypto.matchingCounters(k, frame, rx.holder[rx.field] || 0, t)) {
        const text = Bytes.utf8Strict(Crypto.unseal(k, frame, i, encrypted));
        if (text === null) continue;
        rx.holder[rx.field] = i;
        this.touch();
        return { kind: encrypted ? "sealed" : "signed", source: rx.source, sender: rx.sender, text, counter: i,
                 pub: rx.pub, verified: rx.verified, group: rx.group };
      }
    }
    return null;
  }
  static maxText(priv) { return Protocol.profile.maxPayload - (priv ? Crypto.FRAME_OVERHEAD : 1); }
}

// -- message log ------------------------------------------------------------------------------
// Rows kept in memory and saved (debounced) to the store as one JSON array.
class MessageLog {
  constructor(store, name, saveDelayMs = 400) {
    this.store = store; this.name = name; this.saveDelayMs = saveDelayMs; this.rows = []; this.nextId = 1; this.timer = null;
    const raw = store.get(name);
    if (raw) {
      try { this.rows = JSON.parse(Bytes.utf8Lenient(raw)); } catch (e) { this.rows = []; }
      this.nextId = this.rows.reduce((m, r) => Math.max(m, r.id), 0) + 1;
    }
  }
  _changed() {
    if (this.saveDelayMs <= 0) return this.flush();
    if (!this.timer) this.timer = setTimeout(() => this.flush(), this.saveDelayMs);
  }
  flush() {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    try { this.store.put(this.name, Bytes.utf8(JSON.stringify(this.rows))); }
    catch (e) {                                   // storage full: drop the oldest half and retry once
      this.rows = this.rows.slice(Math.floor(this.rows.length / 2));
      try { this.store.put(this.name, Bytes.utf8(JSON.stringify(this.rows))); } catch (e2) { /* give up */ }
    }
  }
  add(dir, payload, kind, text, rssi = null, snr = null, airtimeMs = null, ts = Crypto.nowSeconds(), channel = null, sender = null) {
    const r = { id: this.nextId++, ts, dir, kind, payload: Bytes.hex(payload), text, channel, sender, rssi, snr,
                airtime_ms: airtimeMs, tx_ok: null };
    this.rows.push(r);
    this._changed();
    return r.id;
  }
  _find(id) { for (let i = this.rows.length - 1; i >= 0; i--) if (this.rows[i].id === id) return this.rows[i]; return null; }
  setTxOk(id, ok) { this.setStatus(id, ok ? 1 : 0); }
  // tx_ok: 0 not sent, 1 sent, 2 delivered, 3 not confirmed (direct messages)
  setStatus(id, code) { const r = this._find(id); if (r) { r.tx_ok = code; this._changed(); } }
  update(id, kind, text, channel, sender) {
    const r = this._find(id); if (!r) return;
    Object.assign(r, { kind, text, channel, sender }); this._changed();
  }
  list(n, kind = null) { return (kind ? this.rows.filter((r) => r.kind === kind) : this.rows).slice(-n); }
  prune(maxRows, maxDays) {
    const before = this.rows.length;
    if (maxDays > 0) { const cutoff = Crypto.nowSeconds() - maxDays * 86400; this.rows = this.rows.filter((r) => r.ts >= cutoff); }
    if (maxRows > 0 && this.rows.length > maxRows) this.rows = this.rows.slice(this.rows.length - maxRows);
    if (this.rows.length !== before) this._changed();
    return before - this.rows.length;
  }
  clear() { this.rows = []; this.flush(); }
}
