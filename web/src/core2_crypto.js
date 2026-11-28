// ---------------------------------------------------------------------------------
// ChaCha20, Ed25519, scrypt and the rolling-tag frame crypto (port of crypto.py,
// ed25519.py and vault.py; byte-identical with the Python and Android apps)
// ---------------------------------------------------------------------------------

const Crypto = (() => {
  const TAG_LEN = 5, HINT_LEN = 1, FRAME_OVERHEAD = 1 + HINT_LEN + TAG_LEN;
  const SEQ = 256;                    // 2^SEQ_BITS
  const EPOCH0 = 1767225600;          // 2026-01-01T00:00:00Z
  const SKEW_MIN = 5, SEQ_MAX = 16, AHEAD = 1024;

  const le32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) | 0;
  const rotl = (v, c) => (v << c) | (v >>> (32 - c));

  function chacha20Block(key, counter, nonce) {
    const st = new Int32Array(16);
    st[0] = 0x61707865; st[1] = 0x3320646e; st[2] = 0x79622d32; st[3] = 0x6b206574;
    for (let k = 0; k < 8; k++) st[4 + k] = le32(key, 4 * k);
    st[12] = counter | 0;
    for (let k = 0; k < 3; k++) st[13 + k] = le32(nonce, 4 * k);
    const w = Int32Array.from(st);
    const qr = (a, b, c, d) => {
      w[a] = (w[a] + w[b]) | 0; w[d] = rotl(w[d] ^ w[a], 16);
      w[c] = (w[c] + w[d]) | 0; w[b] = rotl(w[b] ^ w[c], 12);
      w[a] = (w[a] + w[b]) | 0; w[d] = rotl(w[d] ^ w[a], 8);
      w[c] = (w[c] + w[d]) | 0; w[b] = rotl(w[b] ^ w[c], 7);
    };
    for (let r = 0; r < 10; r++) {
      qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15);
      qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14);
    }
    const out = new Uint8Array(64);
    for (let k = 0; k < 16; k++) {
      const v = (w[k] + st[k]) | 0;
      out[4 * k] = v; out[4 * k + 1] = v >>> 8; out[4 * k + 2] = v >>> 16; out[4 * k + 3] = v >>> 24;
    }
    return out;
  }

  function chacha20Xor(key, nonce, data, counter = 1) {
    if (key.length !== 32 || nonce.length !== 12) throw new Error("chacha20: bad key or nonce size");
    const out = new Uint8Array(data.length);
    for (let off = 0; off < data.length; off += 64) {
      const ks = chacha20Block(key, counter + off / 64, nonce);
      const n = Math.min(64, data.length - off);
      for (let k = 0; k < n; k++) out[off + k] = data[off + k] ^ ks[k];
    }
    return out;
  }

  // -- keys ----------------------------------------------------------------------------
  const derive = (secret, label) => Bytes.hmacSha256(secret, Bytes.utf8("lora-text/" + label));
  const memberSecret = (gs, callsign) => derive(gs, "member/" + callsign.trim().toLowerCase()).slice(0, 16);

  class StreamKey {
    constructor(secret) { this.enc = derive(secret, "enc"); this.mac = derive(secret, "mac"); }
  }

  // -- counters (plain numbers: i < 2^53) ------------------------------------------------
  const nowSeconds = () => Date.now() / 1000;
  const minuteNow = (t) => Math.floor(((t ?? nowSeconds()) - EPOCH0) / 60);
  const nextCounter = (lastI, floorI = 0, t) => Math.max(lastI + 1, floorI, minuteNow(t) * SEQ);
  const startupFloor = (t) => (minuteNow(t) + 1) * SEQ;

  const SKEW_ORDER = [];
  for (let d = -SKEW_MIN; d <= SKEW_MIN + 1; d++) SKEW_ORDER.push(d);
  SKEW_ORDER.sort((a, b) => Math.abs(a) - Math.abs(b));   // stable: as Python's sorted(key=abs)

  function* candidates(lastI, t, hint = null) {
    const tried = new Set();
    if (lastI > 0) {
      const start = lastI + 1;
      if (hint === null || hint === undefined) {
        for (let i = start; i < start + AHEAD; i++) { tried.add(i); yield i; }
      } else {
        let i0 = start - (start % SEQ) + hint;
        if (i0 < start) i0 += SEQ;
        for (let i = i0; i < start + AHEAD; i += SEQ) { tried.add(i); yield i; }
      }
    }
    const m = minuteNow(t);
    const seqs = hint === null || hint === undefined ? [...Array(SEQ_MAX).keys()] : [hint];
    for (const d of SKEW_ORDER) for (const s of seqs) {
      const i = (m + d) * SEQ + s;
      if (i > lastI && !tried.has(i)) yield i;
    }
  }

  const nonce = (i) => Bytes.concat(new Uint8Array(4), Bytes.longBE(i));
  const tag = (k, mode, i, body) =>
    Bytes.hmacSha256(k.mac, Bytes.concat(new Uint8Array([mode]), Bytes.longBE(i), body)).slice(0, TAG_LEN);

  function seal(k, mode, i, plaintext, encrypt) {
    const body = encrypt ? chacha20Xor(k.enc, nonce(i), plaintext) : plaintext;
    return Bytes.concat(new Uint8Array([mode, i % SEQ]), body, tag(k, mode, i, body));
  }
  const bodyOf = (frame) => frame.slice(2, frame.length - TAG_LEN);

  function verify(k, frame, i) {
    if (frame.length < FRAME_OVERHEAD || frame[1] !== i % SEQ) return false;
    return Bytes.equal(tag(k, frame[0], i, bodyOf(frame)), frame.slice(frame.length - TAG_LEN));
  }
  const unseal = (k, frame, i, encrypted) => encrypted ? chacha20Xor(k.enc, nonce(i), bodyOf(frame)) : bodyOf(frame);

  function* matchingCounters(k, frame, lastI, t) {
    if (frame.length < FRAME_OVERHEAD) return;
    for (const i of candidates(lastI, t, frame[1])) if (verify(k, frame, i)) yield i;
  }
  function findCounter(k, frame, lastI, t) {
    for (const i of matchingCounters(k, frame, lastI, t)) return i;
    return null;
  }

  return { TAG_LEN, FRAME_OVERHEAD, EPOCH0, chacha20Block, chacha20Xor, derive, memberSecret, StreamKey,
           nowSeconds, minuteNow, nextCounter, startupFloor, candidates, seal, verify, unseal,
           matchingCounters, findCounter };
})();

// Ed25519 (RFC 8032), BigInt. Not constant-time (same caveat as the Python version).
const Ed25519 = (() => {
  const P = (1n << 255n) - 19n;
  const Q = (1n << 252n) + 27742317777372353535851937790883648493n;
  const mod = (a, m = P) => { const r = a % m; return r >= 0n ? r : r + m; };
  function pow(b, e, m = P) {
    let r = 1n; b = mod(b, m);
    while (e > 0n) { if (e & 1n) r = (r * b) % m; b = (b * b) % m; e >>= 1n; }
    return r;
  }
  const inv = (x) => pow(x, P - 2n);
  const D = mod(-121665n * inv(121666n));
  const SQRT_M1 = pow(2n, (P - 1n) / 4n);

  function recoverX(y, sign) {
    if (y >= P) return null;
    const y2 = y * y;
    const x2 = mod((y2 - 1n) * inv(D * y2 + 1n));
    if (x2 === 0n) return sign ? null : 0n;
    let x = pow(x2, (P + 3n) / 8n);
    if (mod(x * x - x2) !== 0n) x = mod(x * SQRT_M1);
    if (mod(x * x - x2) !== 0n) return null;
    if (Number(x & 1n) !== sign) x = P - x;
    return x;
  }
  const gy = mod(4n * inv(5n));
  const gx = recoverX(gy, 0);
  const G = [gx, gy, 1n, mod(gx * gy)];

  function add(p, q) {
    const a = mod((p[1] - p[0]) * (q[1] - q[0]));
    const b = mod((p[1] + p[0]) * (q[1] + q[0]));
    const c = mod(2n * p[3] * q[3] * D);
    const d = mod(2n * p[2] * q[2]);
    const e = b - a, f = d - c, g = d + c, h = b + a;
    return [mod(e * f), mod(g * h), mod(f * g), mod(e * h)];
  }
  function mul(s, p) {
    let q = [0n, 1n, 1n, 0n];
    while (s > 0n) { if (s & 1n) q = add(q, p); p = add(p, p); s >>= 1n; }
    return q;
  }
  const equalPt = (p, q) => mod(p[0] * q[2] - q[0] * p[2]) === 0n && mod(p[1] * q[2] - q[1] * p[2]) === 0n;

  function leToInt(b) { let v = 0n; for (let i = b.length - 1; i >= 0; i--) v = (v << 8n) | BigInt(b[i]); return v; }
  function intToLe(v, n) { const o = new Uint8Array(n); for (let i = 0; i < n; i++) { o[i] = Number(v & 255n); v >>= 8n; } return o; }

  function compress(p) {
    const zi = inv(p[2]);
    const x = mod(p[0] * zi), y = mod(p[1] * zi);
    return intToLe(y | ((x & 1n) << 255n), 32);
  }
  function decompress(s) {
    if (s.length !== 32) return null;
    let y = leToInt(s);
    const sign = Number((y >> 255n) & 1n);
    y &= (1n << 255n) - 1n;
    const x = recoverX(y, sign);
    if (x === null) return null;
    return [x, y, 1n, mod(x * y)];
  }
  function expand(seed) {
    const h = Bytes.sha512(seed);
    let a = leToInt(h.slice(0, 32));
    a &= (1n << 254n) - 8n;
    a |= 1n << 254n;
    return [a, h.slice(32, 64)];
  }
  const hq = (...parts) => mod(leToInt(Bytes.sha512(Bytes.concat(...parts))), Q);

  const publicKey = (seed) => compress(mul(expand(seed)[0], G));
  // Diffie-Hellman with the identity keys themselves: a_me * A_peer == a_peer * A_me.
  // null for a key that is not a point or gives the neutral point (small order).
  function sharedPoint(seed, peerPub) {
    const A = decompress(peerPub);
    if (!A) return null;
    const S = mul(expand(seed)[0], A);
    if (equalPt(S, [0n, 1n, 1n, 0n])) return null;
    return compress(S);
  }
  function sign(seed, msg) {
    const [a, prefix] = expand(seed);
    const pub = compress(mul(a, G));
    const r = hq(prefix, msg);
    const R = compress(mul(r, G));
    const s = mod(r + hq(R, pub, msg) * a, Q);
    return Bytes.concat(R, intToLe(s, 32));
  }
  function verify(pub, msg, sig) {
    if (pub.length !== 32 || sig.length !== 64) return false;
    const A = decompress(pub); if (!A) return false;
    const Rb = sig.slice(0, 32);
    const R = decompress(Rb); if (!R) return false;
    const s = leToInt(sig.slice(32, 64));
    if (s >= Q) return false;
    return equalPt(mul(s, G), add(R, mul(hq(Rb, pub, msg), A)));
  }
  return { publicKey, sign, verify, sharedPoint };
})();

// scrypt (RFC 7914). n = 2^15, r = 8 needs 32 MiB and takes about a second in a browser.
const Scrypt = (() => {
  function pbkdf2(pass, salt, dkLen) {
    const out = new Uint8Array(dkLen);
    for (let block = 1, off = 0; off < dkLen; block++, off += 32) {
      const u = Bytes.hmacSha256(pass, Bytes.concat(salt, Bytes.intBE(block)));
      out.set(u.slice(0, Math.min(32, dkLen - off)), off);
    }
    return out;
  }
  function salsa8(b, x) {
    x.set(b);
    const r = (a, n) => (a << n) | (a >>> (32 - n));
    for (let i = 0; i < 4; i++) {
      x[4] ^= r(x[0] + x[12], 7); x[8] ^= r(x[4] + x[0], 9); x[12] ^= r(x[8] + x[4], 13); x[0] ^= r(x[12] + x[8], 18);
      x[9] ^= r(x[5] + x[1], 7); x[13] ^= r(x[9] + x[5], 9); x[1] ^= r(x[13] + x[9], 13); x[5] ^= r(x[1] + x[13], 18);
      x[14] ^= r(x[10] + x[6], 7); x[2] ^= r(x[14] + x[10], 9); x[6] ^= r(x[2] + x[14], 13); x[10] ^= r(x[6] + x[2], 18);
      x[3] ^= r(x[15] + x[11], 7); x[7] ^= r(x[3] + x[15], 9); x[11] ^= r(x[7] + x[3], 13); x[15] ^= r(x[11] + x[7], 18);
      x[1] ^= r(x[0] + x[3], 7); x[2] ^= r(x[1] + x[0], 9); x[3] ^= r(x[2] + x[1], 13); x[0] ^= r(x[3] + x[2], 18);
      x[6] ^= r(x[5] + x[4], 7); x[7] ^= r(x[6] + x[5], 9); x[4] ^= r(x[7] + x[6], 13); x[5] ^= r(x[4] + x[7], 18);
      x[11] ^= r(x[10] + x[9], 7); x[8] ^= r(x[11] + x[10], 9); x[9] ^= r(x[8] + x[11], 13); x[10] ^= r(x[9] + x[8], 18);
      x[12] ^= r(x[15] + x[14], 7); x[13] ^= r(x[12] + x[15], 9); x[14] ^= r(x[13] + x[12], 13); x[15] ^= r(x[14] + x[13], 18);
    }
    for (let k = 0; k < 16; k++) b[k] = (b[k] + x[k]) | 0;
  }
  function blockMix(b, y, r, x, t, s) {
    x.set(b.subarray((2 * r - 1) * 16, 2 * r * 16));
    for (let i = 0; i < 2 * r; i++) {
      for (let k = 0; k < 16; k++) t[k] = x[k] ^ b[i * 16 + k];
      salsa8(t, s);
      x.set(t);
      y.set(x, ((i % 2 === 0) ? i / 2 : r + (i - 1) / 2) * 16);
    }
  }
  function derive(pass, salt, n, r, p, dkLen) {
    if (n < 2 || (n & (n - 1)) !== 0) throw new Error("n must be a power of 2");
    const words = 32 * r;
    const bytes = pbkdf2(pass, salt, p * 128 * r);
    const v = new Int32Array(n * words);
    const xb = new Int32Array(words), yb = new Int32Array(words);
    const tx = new Int32Array(16), tt = new Int32Array(16), ts = new Int32Array(16);
    for (let blk = 0; blk < p; blk++) {
      const base = blk * 128 * r;
      for (let k = 0; k < words; k++) xb[k] = le32(bytes, base + 4 * k);
      for (let i = 0; i < n; i++) { v.set(xb, i * words); blockMix(xb, yb, r, tx, tt, ts); xb.set(yb); }
      for (let i = 0; i < n; i++) {
        const j = xb[(2 * r - 1) * 16] & (n - 1);
        for (let k = 0; k < words; k++) xb[k] ^= v[j * words + k];
        blockMix(xb, yb, r, tx, tt, ts); xb.set(yb);
      }
      for (let k = 0; k < words; k++) {
        const o = base + 4 * k, w = xb[k];
        bytes[o] = w; bytes[o + 1] = w >>> 8; bytes[o + 2] = w >>> 16; bytes[o + 3] = w >>> 24;
      }
    }
    return pbkdf2(pass, bytes, dkLen);
  }
  const le32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) | 0;
  return { derive };
})();

class WrongPassphrase extends Error {}

// Passphrase-protected container (.lkey / .ltid), same format as vault.py:
//   "LTID1" | salt(16) | nonce(12) | ChaCha20(enc, json) | HMAC-SHA256(mac, ...)[:16]
const Vault = (() => {
  const MAGIC = Bytes.utf8("LTID1");
  const MIN_PASSPHRASE = 8;
  function kdf(passphrase, salt) {
    const k = Scrypt.derive(Bytes.utf8(passphrase), salt, 1 << 15, 8, 1, 64);
    return [k.slice(0, 32), k.slice(32, 64)];
  }
  class V {
    constructor(salt, enc, mac) { this.salt = salt; this.enc = enc; this.mac = mac; }
    seal(data) {
      const n = Bytes.random(12);
      const ct = Crypto.chacha20Xor(this.enc, n, Bytes.utf8(JSON.stringify(data)));
      const tag = Bytes.hmacSha256(this.mac, Bytes.concat(MAGIC, this.salt, n, ct)).slice(0, 16);
      return Bytes.concat(MAGIC, this.salt, n, ct, tag);
    }
  }
  function create(passphrase) {
    if (passphrase.length < MIN_PASSPHRASE) throw new Error(`passphrase must be at least ${MIN_PASSPHRASE} characters`);
    const salt = Bytes.random(16);
    const [e, m] = kdf(passphrase, salt);
    return new V(salt, e, m);
  }
  const isProtected = (blob) => blob.length >= 5 && Bytes.equal(blob.slice(0, 5), MAGIC);
  function open(blob, passphrase) {
    if (!isProtected(blob) || blob.length < 5 + 16 + 12 + 16) throw new Error("not a protected identity file");
    const salt = blob.slice(5, 21), n = blob.slice(21, 33);
    const ct = blob.slice(33, blob.length - 16), tag = blob.slice(blob.length - 16);
    const [e, m] = kdf(passphrase, salt);
    const want = Bytes.hmacSha256(m, Bytes.concat(MAGIC, salt, n, ct)).slice(0, 16);
    if (!Bytes.equal(want, tag)) throw new WrongPassphrase("wrong passphrase (or damaged file)");
    return [JSON.parse(Bytes.utf8Lenient(Crypto.chacha20Xor(e, n, ct))), new V(salt, e, m)];
  }
  return { MIN_PASSPHRASE, create, open, isProtected };
})();

// Log text encryption: "enc1:" + base64(nonce(12) | ct | tag(8))
class LogCipher {
  constructor(secret) { this.enc = Crypto.derive(secret, "log/enc"); this.mac = Crypto.derive(secret, "log/mac"); }
  encrypt(text) {
    if (text === null || text === undefined) return text;
    const n = Bytes.random(12);
    const ct = Crypto.chacha20Xor(this.enc, n, Bytes.utf8(text));
    const tag = Bytes.hmacSha256(this.mac, Bytes.concat(n, ct)).slice(0, 8);
    return LogCipher.PREFIX + Bytes.b64(Bytes.concat(n, ct, tag));
  }
  decrypt(s) {
    if (s === null || s === undefined || !s.startsWith(LogCipher.PREFIX)) return s;
    let raw;
    try { raw = Bytes.unb64(s.slice(LogCipher.PREFIX.length)); } catch (e) { return null; }
    if (raw.length < 20) return null;
    const n = raw.slice(0, 12), ct = raw.slice(12, raw.length - 8), tag = raw.slice(raw.length - 8);
    if (!Bytes.equal(Bytes.hmacSha256(this.mac, Bytes.concat(n, ct)).slice(0, 8), tag)) return null;
    return Bytes.utf8Lenient(Crypto.chacha20Xor(this.enc, n, ct));
  }
}
LogCipher.PREFIX = "enc1:";
