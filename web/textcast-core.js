// TextCast core for the web: built from web/src by web/build.py - edit those files, not this one.
(function () {
"use strict";
// ---------------------------------------------------------------------------------
// Bytes, hashes, encodings (no dependencies; synchronous, so the core stays simple)
// ---------------------------------------------------------------------------------

const Bytes = (() => {
  const enc = new TextEncoder();
  const decStrict = new TextDecoder("utf-8", { fatal: true });
  const decLenient = new TextDecoder("utf-8");

  function random(n) {
    const b = new Uint8Array(n);
    (globalThis.crypto || require("crypto").webcrypto).getRandomValues(b);
    return b;
  }

  function concat(...parts) {
    let n = 0;
    for (const p of parts) n += p.length;
    const out = new Uint8Array(n);
    let off = 0;
    for (const p of parts) { out.set(p, off); off += p.length; }
    return out;
  }

  function equal(a, b) {
    if (a.length !== b.length) return false;
    let d = 0;
    for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
    return d === 0;
  }

  // 64-bit big-endian of a non-negative integer below 2^53
  function longBE(v) {
    const out = new Uint8Array(8);
    let hi = Math.floor(v / 4294967296), lo = v % 4294967296;
    for (let k = 7; k >= 4; k--) { out[k] = lo & 0xff; lo = Math.floor(lo / 256); }
    for (let k = 3; k >= 0; k--) { out[k] = hi & 0xff; hi = Math.floor(hi / 256); }
    return out;
  }

  function intBE(v) {
    v = v >>> 0;
    return new Uint8Array([(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]);
  }

  function readIntBE(b, off) {
    return ((b[off] << 24) >>> 0) + (b[off + 1] << 16) + (b[off + 2] << 8) + b[off + 3];
  }

  // -- SHA-256 ------------------------------------------------------------------------
  const K256 = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);

  function sha256(msg) {
    const l = msg.length;
    const n = ((l + 9 + 63) >> 6) << 6;
    const m = new Uint8Array(n);
    m.set(msg); m[l] = 0x80;
    const bits = l * 8;
    m.set(longBE(bits), n - 8);
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                               0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64);
    for (let off = 0; off < n; off += 64) {
      for (let i = 0; i < 16; i++)
        w[i] = (m[off + 4 * i] << 24) | (m[off + 4 * i + 1] << 16) | (m[off + 4 * i + 2] << 8) | m[off + 4 * i + 3];
      for (let i = 16; i < 64; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + S1 + ch + K256[i] + w[i]) | 0;
        const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const mj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + mj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    const out = new Uint8Array(32);
    for (let i = 0; i < 8; i++) out.set(intBE(h[i]), 4 * i);
    return out;
  }

  // -- SHA-512 (32-bit halves) -----------------------------------------------------------
  const K512 = [
    0x428a2f98, 0xd728ae22, 0x71374491, 0x23ef65cd, 0xb5c0fbcf, 0xec4d3b2f, 0xe9b5dba5, 0x8189dbbc,
    0x3956c25b, 0xf348b538, 0x59f111f1, 0xb605d019, 0x923f82a4, 0xaf194f9b, 0xab1c5ed5, 0xda6d8118,
    0xd807aa98, 0xa3030242, 0x12835b01, 0x45706fbe, 0x243185be, 0x4ee4b28c, 0x550c7dc3, 0xd5ffb4e2,
    0x72be5d74, 0xf27b896f, 0x80deb1fe, 0x3b1696b1, 0x9bdc06a7, 0x25c71235, 0xc19bf174, 0xcf692694,
    0xe49b69c1, 0x9ef14ad2, 0xefbe4786, 0x384f25e3, 0x0fc19dc6, 0x8b8cd5b5, 0x240ca1cc, 0x77ac9c65,
    0x2de92c6f, 0x592b0275, 0x4a7484aa, 0x6ea6e483, 0x5cb0a9dc, 0xbd41fbd4, 0x76f988da, 0x831153b5,
    0x983e5152, 0xee66dfab, 0xa831c66d, 0x2db43210, 0xb00327c8, 0x98fb213f, 0xbf597fc7, 0xbeef0ee4,
    0xc6e00bf3, 0x3da88fc2, 0xd5a79147, 0x930aa725, 0x06ca6351, 0xe003826f, 0x14292967, 0x0a0e6e70,
    0x27b70a85, 0x46d22ffc, 0x2e1b2138, 0x5c26c926, 0x4d2c6dfc, 0x5ac42aed, 0x53380d13, 0x9d95b3df,
    0x650a7354, 0x8baf63de, 0x766a0abb, 0x3c77b2a8, 0x81c2c92e, 0x47edaee6, 0x92722c85, 0x1482353b,
    0xa2bfe8a1, 0x4cf10364, 0xa81a664b, 0xbc423001, 0xc24b8b70, 0xd0f89791, 0xc76c51a3, 0x0654be30,
    0xd192e819, 0xd6ef5218, 0xd6990624, 0x5565a910, 0xf40e3585, 0x5771202a, 0x106aa070, 0x32bbd1b8,
    0x19a4c116, 0xb8d2d0c8, 0x1e376c08, 0x5141ab53, 0x2748774c, 0xdf8eeb99, 0x34b0bcb5, 0xe19b48a8,
    0x391c0cb3, 0xc5c95a63, 0x4ed8aa4a, 0xe3418acb, 0x5b9cca4f, 0x7763e373, 0x682e6ff3, 0xd6b2b8a3,
    0x748f82ee, 0x5defb2fc, 0x78a5636f, 0x43172f60, 0x84c87814, 0xa1f0ab72, 0x8cc70208, 0x1a6439ec,
    0x90befffa, 0x23631e28, 0xa4506ceb, 0xde82bde9, 0xbef9a3f7, 0xb2c67915, 0xc67178f2, 0xe372532b,
    0xca273ece, 0xea26619c, 0xd186b8c7, 0x21c0c207, 0xeada7dd6, 0xcde0eb1e, 0xf57d4f7f, 0xee6ed178,
    0x06f067aa, 0x72176fba, 0x0a637dc5, 0xa2c898a6, 0x113f9804, 0xbef90dae, 0x1b710b35, 0x131c471b,
    0x28db77f5, 0x23047d84, 0x32caab7b, 0x40c72493, 0x3c9ebe0a, 0x15c9bebc, 0x431d67c4, 0x9c100d4c,
    0x4cc5d4be, 0xcb3e42b6, 0x597f299c, 0xfc657e2a, 0x5fcb6fab, 0x3ad6faec, 0x6c44198c, 0x4a475817];

  function sha512(msg) {
    const l = msg.length;
    const n = ((l + 17 + 127) >> 7) << 7;
    const m = new Uint8Array(n);
    m.set(msg); m[l] = 0x80;
    m.set(longBE(l * 8), n - 8);
    const H = new Int32Array([0x6a09e667, 0xf3bcc908, 0xbb67ae85, 0x84caa73b, 0x3c6ef372, 0xfe94f82b,
      0xa54ff53a, 0x5f1d36f1, 0x510e527f, 0xade682d1, 0x9b05688c, 0x2b3e6c1f, 0x1f83d9ab, 0xfb41bd6b,
      0x5be0cd19, 0x137e2179]);
    const W = new Int32Array(160);
    // add 64-bit (ah,al)+(bh,bl) -> returns [h,l]
    for (let off = 0; off < n; off += 128) {
      for (let i = 0; i < 32; i++)
        W[i] = (m[off + 4 * i] << 24) | (m[off + 4 * i + 1] << 16) | (m[off + 4 * i + 2] << 8) | m[off + 4 * i + 3];
      for (let i = 16; i < 80; i++) {
        let xh = W[2 * (i - 15)], xl = W[2 * (i - 15) + 1];
        const s0h = ((xh >>> 1) | (xl << 31)) ^ ((xh >>> 8) | (xl << 24)) ^ (xh >>> 7);
        const s0l = ((xl >>> 1) | (xh << 31)) ^ ((xl >>> 8) | (xh << 24)) ^ ((xl >>> 7) | (xh << 25));
        xh = W[2 * (i - 2)]; xl = W[2 * (i - 2) + 1];
        const s1h = ((xh >>> 19) | (xl << 13)) ^ ((xl >>> 29) | (xh << 3)) ^ (xh >>> 6);
        const s1l = ((xl >>> 19) | (xh << 13)) ^ ((xh >>> 29) | (xl << 3)) ^ ((xl >>> 6) | (xh << 26));
        let lo = (s0l >>> 0) + (s1l >>> 0) + (W[2 * (i - 7) + 1] >>> 0) + (W[2 * (i - 16) + 1] >>> 0);
        let hi = s0h + s1h + W[2 * (i - 7)] + W[2 * (i - 16)] + Math.floor(lo / 4294967296);
        W[2 * i] = hi | 0; W[2 * i + 1] = lo | 0;
      }
      let ah = H[0], al = H[1], bh = H[2], bl = H[3], ch = H[4], cl = H[5], dh = H[6], dl = H[7];
      let eh = H[8], el = H[9], fh = H[10], fl = H[11], gh = H[12], gl = H[13], hh = H[14], hl = H[15];
      for (let i = 0; i < 80; i++) {
        const S1h = ((eh >>> 14) | (el << 18)) ^ ((eh >>> 18) | (el << 14)) ^ ((el >>> 9) | (eh << 23));
        const S1l = ((el >>> 14) | (eh << 18)) ^ ((el >>> 18) | (eh << 14)) ^ ((eh >>> 9) | (el << 23));
        const chh = (eh & fh) ^ (~eh & gh), chl = (el & fl) ^ (~el & gl);
        let lo = (hl >>> 0) + (S1l >>> 0) + (chl >>> 0) + (K512[2 * i + 1] >>> 0) + (W[2 * i + 1] >>> 0);
        let hi = hh + S1h + chh + K512[2 * i] + W[2 * i] + Math.floor(lo / 4294967296);
        const t1h = hi | 0, t1l = lo | 0;
        const S0h = ((ah >>> 28) | (al << 4)) ^ ((al >>> 2) | (ah << 30)) ^ ((al >>> 7) | (ah << 25));
        const S0l = ((al >>> 28) | (ah << 4)) ^ ((ah >>> 2) | (al << 30)) ^ ((ah >>> 7) | (al << 25));
        const mjh = (ah & bh) ^ (ah & ch) ^ (bh & ch), mjl = (al & bl) ^ (al & cl) ^ (bl & cl);
        lo = (S0l >>> 0) + (mjl >>> 0);
        hi = S0h + mjh + Math.floor(lo / 4294967296);
        const t2h = hi | 0, t2l = lo | 0;
        hh = gh; hl = gl; gh = fh; gl = fl; fh = eh; fl = el;
        lo = (dl >>> 0) + (t1l >>> 0); eh = (dh + t1h + Math.floor(lo / 4294967296)) | 0; el = lo | 0;
        dh = ch; dl = cl; ch = bh; cl = bl; bh = ah; bl = al;
        lo = (t1l >>> 0) + (t2l >>> 0); ah = (t1h + t2h + Math.floor(lo / 4294967296)) | 0; al = lo | 0;
      }
      const add = (k, xh, xl) => {
        const lo = (H[k + 1] >>> 0) + (xl >>> 0);
        H[k] = (H[k] + xh + Math.floor(lo / 4294967296)) | 0; H[k + 1] = lo | 0;
      };
      add(0, ah, al); add(2, bh, bl); add(4, ch, cl); add(6, dh, dl);
      add(8, eh, el); add(10, fh, fl); add(12, gh, gl); add(14, hh, hl);
    }
    const out = new Uint8Array(64);
    for (let i = 0; i < 16; i++) out.set(intBE(H[i]), 4 * i);
    return out;
  }

  function hmacSha256(key, data) {
    if (key.length > 64) key = sha256(key);
    const k = new Uint8Array(64); k.set(key);
    const ip = new Uint8Array(64), op = new Uint8Array(64);
    for (let i = 0; i < 64; i++) { ip[i] = k[i] ^ 0x36; op[i] = k[i] ^ 0x5c; }
    return sha256(concat(op, sha256(concat(ip, data))));
  }

  // -- encodings --------------------------------------------------------------------------
  const HEX = "0123456789abcdef";
  function hex(b, upper = false) {
    let s = "";
    for (const x of b) s += HEX[x >> 4] + HEX[x & 15];
    return upper ? s.toUpperCase() : s;
  }
  function unhex(s) {
    const c = s.replace(/\s+/g, "");
    if (c.length % 2) throw new Error("odd number of hex digits");
    const out = new Uint8Array(c.length / 2);
    for (let i = 0; i < out.length; i++) {
      const v = parseInt(c.substr(2 * i, 2), 16);
      if (!/^[0-9a-fA-F]{2}$/.test(c.substr(2 * i, 2))) throw new Error("not hex: " + c.substr(2 * i, 2));
      out[i] = v;
    }
    return out;
  }

  const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  function b32(b) {
    let s = "", buf = 0, bits = 0;
    for (const x of b) {
      buf = ((buf << 8) | x) & 0xffff; bits += 8;
      while (bits >= 5) { s += B32[(buf >> (bits - 5)) & 31]; bits -= 5; }
    }
    if (bits > 0) s += B32[(buf << (5 - bits)) & 31];
    return s.toLowerCase();
  }
  function unb32(s) {
    const t = s.trim().toUpperCase().replace(/=+$/, "");
    const rem = t.length % 8;
    if (rem === 1 || rem === 3 || rem === 6) throw new Error("bad base32 length");
    const out = [];
    let buf = 0, bits = 0;
    for (const ch of t) {
      const v = B32.indexOf(ch);
      if (v < 0) throw new Error("not base32: '" + ch + "'");
      buf = ((buf << 5) | v) & 0xffff; bits += 5;
      if (bits >= 8) { out.push((buf >> (bits - 8)) & 255); bits -= 8; }
    }
    return new Uint8Array(out);
  }

  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  function b64(b) {
    let s = "";
    for (let i = 0; i < b.length; i += 3) {
      const n = Math.min(3, b.length - i);
      let v = 0;
      for (let k = 0; k < 3; k++) v = (v << 8) | (k < n ? b[i + k] : 0);
      for (let k = 0; k < 4; k++) s += k <= n ? B64[(v >> (18 - 6 * k)) & 63] : "=";
    }
    return s;
  }
  function unb64(s) {
    const t = s.replace(/\s+/g, "").replace(/=+$/, "");
    const out = [];
    let buf = 0, bits = 0;
    for (const ch of t) {
      const v = B64.indexOf(ch);
      if (v < 0) throw new Error("not base64");
      buf = ((buf << 6) | v) & 0xffffff; bits += 6;
      if (bits >= 8) { out.push((buf >> (bits - 8)) & 255); bits -= 8; }
    }
    return new Uint8Array(out);
  }

  const utf8 = (s) => enc.encode(s);
  function utf8Strict(b, from = 0, to = b.length) {
    try { return decStrict.decode(b.subarray(from, to)); } catch (e) { return null; }
  }
  const utf8Lenient = (b) => decLenient.decode(b);
  const nfc = (s) => s.normalize("NFC");

  return { random, concat, equal, longBE, intBE, readIntBE, sha256, sha512, hmacSha256,
           hex, unhex, b32, unb32, b64, unb64, utf8, utf8Strict, utf8Lenient, nfc };
})();

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
    this.awaiting = new Map(); this.acks = new Map(); this.seenDm = new Map(); this.timers = new Set();
    this.onMsg = () => {}; this.onUpdate = () => {}; this.onLog = () => {};
    this.lookups = new Map(); this.parts = new Map(); this.lastAnswer = 0;
    this.onLookup = () => {};             // {number, status: found|conflict|notfound, nick, handle}
    this.radio = null; this.firmware = "?"; this.publicTag = false;
    this.pruneLog();
    this.processPending();
  }
  static READ_ONLY = new Set(["follow", "other"]);
  static SENSITIVE = new Set(["sealed", "signed", "join", "direct"]);
  static RECEIPT_WAIT = 30;            // s after sending: no receipt -> 'unconfirmed'
  static RECEIPT_DELAY = [1, 4];       // s before confirming a direct message (random)
  static RESEND_SAME = 1800;           // s: a resent text already shown is not shown again
  static STATUS_CODE = { failed: 0, sent: 1, delivered: 2, unconfirmed: 3 };
  static LOOKUP_WAIT = 10;             // s to wait for answers to "who has this number?"
  static LOOKUP_RETRY = 4;             // s: ask once more if nobody answered yet
  static ANSWER_DELAY = [0.3, 1.5];    // s before answering a lookup for my number
  static ANSWER_GAP = 3;               // s: answer at most this often
  static QUIET = new Set(["receipt", "lookup"]);

  get nick() { return this.keys.me; }
  get handle() { return this.keys.handle; }
  get locked() { return this.keys.locked; }
  get callsign() { return this.publicTag && this.handle ? this.handle : this.nick; }

  async start(radio, { configure = false, autoApply = true } = {}) {
    // the airtime count and rate limit survive unplugging: a replug must not reset the duty cycle
    if (this.meter) { radio.meter = this.meter; radio.limiter = this.limiter; }
    else { this.meter = radio.meter; this.limiter = radio.limiter; }
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
  async detachRadio() {
    const r = this.radio; this.radio = null;
    // frames still waiting fail now (sealed frames carry a time-based counter: sending
    // them much later would be refused); the user resends once the radio is back
    for (const [job, m] of this.pending) {
      this.pending.delete(job);
      if (m.status === "queued") { m.status = "failed"; if (m.row) this.log.setTxOk(m.row, false); this.onUpdate(m); }
    }
    if (r) await r.close();
  }
  async stop() { for (const t of this.timers) clearTimeout(t); await this.detachRadio(); this.keys.close(); this.log.flush(); }
  _later(s, fn) {
    const t = setTimeout(() => { this.timers.delete(t); try { fn(); } catch (e) { this.onLog(e.message); } }, s * 1000);
    this.timers.add(t);
  }
  pruneLog() { return this.log.prune(this.logMaxRows, this.logMaxDays); }
  _logged() { if (++this.inserts % 200 === 0) this.pruneLog(); }

  // -- channels --------------------------------------------------------------------------
  channels() {
    if (this.keys.locked) return [["all", "all"], ["public", "public"], ["other", "other"]];
    return [["all", "all"], ["public", "public"], ...this.keys.directContacts().map(([h]) => ["@" + h, "direct"]),
      ...this.keys.groups().map((g) => [g, "group"]),
      ...this.keys.own().map((g) => [g, "own"]), ...this.keys.followed().map((g) => [g, "follow"]), ["other", "other"]];
  }
  channelType(ch) { const c = this.channels().find((x) => x[0] === ch); return c ? c[1] : "other"; }
  canSend(ch) { return !Node.READ_ONLY.has(this.channelType(ch)); }
  channelTitle(ch) {
    if (!ch.startsWith("@")) return ch;
    const c = this.locked ? null : this.keys.directContacts().find(([h]) => "@" + h === ch);
    return c ? c[1] : ch.slice(1);
  }
  byteCount(ch, text) {
    if (!text.trim()) return 0;
    const t = this.channelType(ch);
    return ["group", "own", "direct"].includes(t)
      ? Bytes.utf8(Protocol.normalize(text.trim())).length + Crypto.FRAME_OVERHEAD + (t === "direct" ? 1 : 0)
      : Protocol.frameLenPlain(Protocol.compose(text, this.callsign));
  }
  get maxBytes() { return Protocol.profile.maxPayload; }

  // -- sending ---------------------------------------------------------------------------
  send(channel, text0, signed = false) {
    const r = this.radio;
    if (!r) throw new KeyException("not connected to a radio");
    const ctype = this.channelType(channel), text = text0.trim();
    if (Node.READ_ONLY.has(ctype)) throw new KeyException(`'${channel}' is read-only - switch to public, a group or your own stream`);
    if (this.locked && ["group", "own", "direct"].includes(ctype)) throw new LockedException("identity is locked - unlock first");
    if (ctype === "direct") return this._sendDirect(channel, text);
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
  _sendDirect(channel, text) {
    const peer = channel.slice(1);
    const [frame, i] = this.keys.sealDirect(peer, text);
    const job = this.radio.send(frame);
    const m = { ts: Crypto.nowSeconds(), direction: "tx", kind: "direct", channel, sender: this.handle || this.nick || "me",
                text, size: frame.length, job, status: "queued", airtime: Protocol.airtimeMs(frame.length), verified: true,
                group: false, rssi: null, snr: null, peer, dmId: dmId(i) };
    const [ch, snd] = this._logMeta(m);
    m.row = this.log.add("tx", frame, "direct", this._logText(m), null, null, m.airtime, m.ts, ch, snd);
    this._logged();
    this.pending.set(job, m);
    return m;
  }
  // Send a direct message again (no receipt came): the receiver doesn't show it twice
  resend(m) {
    if (m.kind !== "direct" || m.direction !== "tx" || !m.peer) throw new KeyException("only direct messages can be resent");
    if (!this.radio) throw new KeyException("not connected to a radio");
    if (m.status === "queued" || m.status === "delivered") return m;
    const [frame, i] = this.keys.sealDirect(m.peer, m.text, true);
    m.job = this.radio.send(frame); m.dmId = dmId(i); m.status = "queued";
    this.pending.set(m.job, m);
    this.onUpdate(m);
    return m;
  }
  _setStatus(m, status) {
    m.status = status;
    if (m.row) this.log.setStatus(m.row, Node.STATUS_CODE[status]);
    this.onUpdate(m);
  }
  _confirm(peer, id) {
    const key = peer.toLowerCase();
    if (!this.acks.has(key)) this.acks.set(key, { peer, ids: [] });
    const a = this.acks.get(key);
    if (!a.ids.includes(id)) a.ids.push(id);
    if (a.ids.length === 1) {
      const [lo, hi] = Node.RECEIPT_DELAY;
      this._later(lo + Math.random() * (hi - lo), () => this._sendReceipts(key));
    }
  }
  _sendReceipts(key) {
    const a = this.acks.get(key); this.acks.delete(key);
    if (!a || !a.ids.length || this.locked || !this.radio) return;
    for (let k = 0; k < a.ids.length; k += 4) this.radio.send(this.keys.makeReceipt(a.peer, a.ids.slice(k, k + 4)));
  }
  _onReceipt(m) {
    for (const id of m.acks) {
      const k = `${m.peer.toLowerCase()}|${id}`;
      const sent = this.awaiting.get(k);
      this.awaiting.delete(k);
      if (sent && sent.status !== "delivered") this._setStatus(sent, "delivered");
    }
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
    if (m.kind === "direct" && r.ok) {
      const id = m.dmId;
      this.awaiting.set(`${m.peer.toLowerCase()}|${id}`, m);
      this._later(Node.RECEIPT_WAIT, () => { if (m.status === "sent" && m.dmId === id) this._setStatus(m, "unconfirmed"); });
    }
    if (m.status === "delivered") return;            // a receipt for an earlier copy came first
    m.status = r.ok ? "sent" : "failed";
    if (m.row) this.log.setTxOk(m.row, r.ok);
    this.onUpdate(m);
  }

  // -- TextCast numbers --------------------------------------------------------------------
  get number() { return this.keys.number; }
  // Ask nearby devices "who has this number?". Returns {status: 'known'|'searching'};
  // the result arrives through onLookup. Throws NumberException.
  lookup(text) {
    const number = Numbers.parse(text);
    if (number === this.keys.number) throw new NumberException("that is your own number");
    const c = this.locked ? null : this.keys.contactByNumber(number);
    if (c) return { number, status: "known", name: c.name, handle: c.handle, channel: "@" + c.handle };
    if (!this.radio) throw new KeyException("not connected to a radio");
    this.lookups.set(number, { found: new Map() });
    this.radio.send(Numbers.makeLookup(number));
    this._later(Node.LOOKUP_RETRY, () => {
      const st = this.lookups.get(number);
      if (st && !st.found.size && this.radio) this.radio.send(Numbers.makeLookup(number));
    });
    this._later(Node.LOOKUP_WAIT, () => {
      const st = this.lookups.get(number);
      if (st && !st.found.size) { this.lookups.delete(number); this.onLookup({ number, status: "notfound" }); }
    });
    return { number, status: "searching" };
  }
  addFound(number, name = null) {
    const st = this.lookups.get(number);
    if (!st || st.found.size !== 1) throw new KeyException("nobody (or more than one key) answered for that number");
    const [pubHex, nick] = [...st.found.entries()][0];
    const pub = Bytes.unhex(pubHex);
    this.keys.addFound(pub, nick, number, name);
    this.lookups.delete(number);
    return "@" + Ids.makeHandle(nick, pub);
  }
  setFindable(on) { this.keys.setFindable(on); }
  _onDirectory(pkt) {
    const d = Numbers.parseFrame(pkt.payload);
    if (!d) return;
    const now = Crypto.nowSeconds();
    if (d.op === "lookup") {
      if (d.number === this.keys.number && this.keys.findable && this.nick && this.radio && now - this.lastAnswer > Node.ANSWER_GAP) {
        this.lastAnswer = now;
        const [lo, hi] = Node.ANSWER_DELAY;
        this._later(lo + Math.random() * (hi - lo), () => {
          if (!this.radio) return;
          for (const f of Numbers.makeAnswer(this.keys.pub, this.nick, Math.floor(Math.random() * 256), Protocol.profile.maxPayload))
            this.radio.send(f);
        });
      }
      return;
    }
    for (const [k, v] of this.parts) if (now - v.t > 30) this.parts.delete(k);
    let st = this.parts.get(d.id);
    if (!st || st.n !== d.parts) { st = { t: now, n: d.parts, chunks: new Map() }; this.parts.set(d.id, st); }
    st.chunks.set(d.part, d.chunk);
    if (st.chunks.size < d.parts) return;
    this.parts.delete(d.id);
    const data = Bytes.concat(...Array.from({ length: d.parts }, (_, j) => st.chunks.get(j)));
    if (data.length < 33 || !this.lookups.size) return;
    const pub = data.slice(0, 32), nick = Bytes.utf8Lenient(data.slice(32));
    const number = Numbers.numberOf(pub);
    const look = this.lookups.get(number), key = Bytes.hex(pub);
    if (!look || look.found.has(key)) return;
    look.found.set(key, nick);
    if (look.found.size === 1) this.onLookup({ number, status: "found", nick, handle: Ids.makeHandle(nick, pub) });
    else this.onLookup({ number, status: "conflict", nick: [...look.found.values()].join(", ") });
  }

  // -- receiving ---------------------------------------------------------------------------
  _onPacket(pkt) {
    if (Numbers.isDirectory(pkt.payload)) {
      this.log.add("rx", pkt.payload, "lookup", null, pkt.rssi, pkt.snr, null, pkt.t);
      this._logged();
      this._onDirectory(pkt);
      return;
    }
    if (this.locked && Protocol.decode(pkt.payload).kind === "reserved") {
      const row = this.log.add("rx", pkt.payload, "pending", null, pkt.rssi, pkt.snr, null, pkt.t);
      this._logged();
      this.buffer.push([row, pkt]);
      this.onMsg({ ts: pkt.t, direction: "rx", kind: "pending", channel: "other", sender: null,
                   text: `[${pkt.payload.length} B private - unlock to read]`, rssi: pkt.rssi, snr: pkt.snr, size: pkt.payload.length });
      return;
    }
    const m = this.classify(pkt);
    if (m.kind === "receipt") {
      this.log.add("rx", pkt.payload, "receipt", null, pkt.rssi, pkt.snr, null, pkt.t);
      this._logged();
      this._onReceipt(m);
      return;
    }
    if (m.kind === "direct") {
      if (this.radio) this._confirm(m.peer, m.dmId);
      const key = `${m.peer.toLowerCase()}|${m.text}`, now = Crypto.nowSeconds();
      if (m.resent && now - (this.seenDm.get(key) || 0) < Node.RESEND_SAME) return;   // shown already: just confirm
      this.seenDm.set(key, now);
    }
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
      if (op && op.kind === "direct") {
        const peer = op.source.slice(1);
        if (op.inner === "receipt") return Object.assign(mk("receipt", op.source, op.sender, "", true), { peer, acks: op.acks });
        return Object.assign(mk("direct", op.source, op.sender, Protocol.clean(op.text), true),
                             { peer, dmId: dmId(op.counter), resent: op.inner === "resend" });
      }
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
      if (Node.QUIET.has(r.kind)) continue;
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
      const status = r.dir === "tx" ? ({ 0: "failed", 1: "sent", 2: "delivered", 3: "unconfirmed" }[r.tx_ok] || "unknown") : null;
      out.push({ ts: r.ts, direction: r.dir, kind: r.kind || "plain", channel: channel || (r.kind === "plain" ? "public" : "other"),
                 sender: name, text, rssi: r.rssi, snr: r.snr, size: payload.length, status, airtime: r.airtime_ms,
                 verified: flag === "v", group: flag === "g", row: r.id,
                 peer: channel && channel.startsWith("@") ? channel.slice(1) : undefined });
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

const TextCast = { Bytes, Crypto, Ed25519, Scrypt, Vault, LogCipher, WrongPassphrase, PayloadTooLong, ConfigError, KeyException, LockedException, HardwareError, airProfile, MsgType, Protocol, RateLimiter, AirtimeMeter, Regions, Settings, Hardware, La66Parser, Ids, Numbers, NumberException, Keyring, MessageLog, Radio, La66Radio, GenericAtRadio, makeRadio, Node, sleep };
if (typeof module !== "undefined" && module.exports) module.exports = TextCast;
else globalThis.TextCast = TextCast;
})();
