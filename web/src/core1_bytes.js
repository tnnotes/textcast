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
