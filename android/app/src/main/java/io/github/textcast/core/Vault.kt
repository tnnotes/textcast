package io.github.textcast.core

/**
 * Passphrase protection for identity files (.lkey / .ltid) and the message log.
 * Same format as lora_text/vault.py, so identity files move between the
 * desktop app and the phone:
 *
 *   b"LTID1" | salt(16) | nonce(12) | ciphertext | tag(16)
 *   enc ‖ mac = scrypt(passphrase, salt, n=2^15, r=8, p=1, 64 bytes)
 *   ct  = ChaCha20(enc, nonce) XOR json
 *   tag = HMAC-SHA256(mac, "LTID1" ‖ salt ‖ nonce ‖ ct)[:16]
 */
class WrongPassphrase(msg: String) : IllegalArgumentException(msg)

object Scrypt {
    private fun pbkdf2(pass: ByteArray, salt: ByteArray, dkLen: Int): ByteArray {
        // PBKDF2-HMAC-SHA256 with one iteration (all scrypt needs)
        val out = ByteArray(dkLen)
        var block = 1; var off = 0
        while (off < dkLen) {
            val u = Bytes.hmacSha256(pass, Bytes.concat(salt, Bytes.intBE(block)))
            val n = minOf(32, dkLen - off)
            System.arraycopy(u, 0, out, off, n)
            off += n; block++
        }
        return out
    }

    private fun salsa8(b: IntArray, x: IntArray) {
        System.arraycopy(b, 0, x, 0, 16)
        fun r(a: Int, n: Int) = (a shl n) or (a ushr (32 - n))
        repeat(4) {
            x[4] = x[4] xor r(x[0] + x[12], 7); x[8] = x[8] xor r(x[4] + x[0], 9)
            x[12] = x[12] xor r(x[8] + x[4], 13); x[0] = x[0] xor r(x[12] + x[8], 18)
            x[9] = x[9] xor r(x[5] + x[1], 7); x[13] = x[13] xor r(x[9] + x[5], 9)
            x[1] = x[1] xor r(x[13] + x[9], 13); x[5] = x[5] xor r(x[1] + x[13], 18)
            x[14] = x[14] xor r(x[10] + x[6], 7); x[2] = x[2] xor r(x[14] + x[10], 9)
            x[6] = x[6] xor r(x[2] + x[14], 13); x[10] = x[10] xor r(x[6] + x[2], 18)
            x[3] = x[3] xor r(x[15] + x[11], 7); x[7] = x[7] xor r(x[3] + x[15], 9)
            x[11] = x[11] xor r(x[7] + x[3], 13); x[15] = x[15] xor r(x[11] + x[7], 18)
            x[1] = x[1] xor r(x[0] + x[3], 7); x[2] = x[2] xor r(x[1] + x[0], 9)
            x[3] = x[3] xor r(x[2] + x[1], 13); x[0] = x[0] xor r(x[3] + x[2], 18)
            x[6] = x[6] xor r(x[5] + x[4], 7); x[7] = x[7] xor r(x[6] + x[5], 9)
            x[4] = x[4] xor r(x[7] + x[6], 13); x[5] = x[5] xor r(x[4] + x[7], 18)
            x[11] = x[11] xor r(x[10] + x[9], 7); x[8] = x[8] xor r(x[11] + x[10], 9)
            x[9] = x[9] xor r(x[8] + x[11], 13); x[10] = x[10] xor r(x[9] + x[8], 18)
            x[12] = x[12] xor r(x[15] + x[14], 7); x[13] = x[13] xor r(x[12] + x[15], 9)
            x[14] = x[14] xor r(x[13] + x[12], 13); x[15] = x[15] xor r(x[14] + x[13], 18)
        }
        for (k in 0 until 16) b[k] += x[k]
    }

    private fun blockMix(b: IntArray, y: IntArray, r: Int, x: IntArray, t: IntArray, s: IntArray) {
        System.arraycopy(b, (2 * r - 1) * 16, x, 0, 16)
        for (i in 0 until 2 * r) {
            for (k in 0 until 16) t[k] = x[k] xor b[i * 16 + k]
            salsa8(t, s)
            System.arraycopy(t, 0, x, 0, 16)
            // even blocks to the first half, odd blocks to the second half
            val dst = (if (i % 2 == 0) i / 2 else r + i / 2) * 16
            System.arraycopy(x, 0, y, dst, 16)
        }
    }

    /** scrypt (RFC 7914). n = 2^15, r = 8 needs 32 MiB. */
    fun derive(pass: ByteArray, salt: ByteArray, n: Int, r: Int, p: Int, dkLen: Int): ByteArray {
        require(n > 1 && n and (n - 1) == 0) { "n must be a power of 2" }
        val words = 32 * r
        val bytes = pbkdf2(pass, salt, p * 128 * r)
        val v = IntArray(n * words)
        val xb = IntArray(words); val yb = IntArray(words)
        val tmpX = IntArray(16); val tmpT = IntArray(16); val tmpS = IntArray(16)
        for (blk in 0 until p) {
            val base = blk * 128 * r
            for (k in 0 until words) {
                val o = base + 4 * k
                xb[k] = (bytes[o].toInt() and 0xFF) or ((bytes[o + 1].toInt() and 0xFF) shl 8) or
                    ((bytes[o + 2].toInt() and 0xFF) shl 16) or ((bytes[o + 3].toInt() and 0xFF) shl 24)
            }
            for (i in 0 until n) {
                System.arraycopy(xb, 0, v, i * words, words)
                blockMix(xb, yb, r, tmpX, tmpT, tmpS)
                System.arraycopy(yb, 0, xb, 0, words)
            }
            for (i in 0 until n) {
                val j = xb[(2 * r - 1) * 16] and (n - 1)
                for (k in 0 until words) xb[k] = xb[k] xor v[j * words + k]
                blockMix(xb, yb, r, tmpX, tmpT, tmpS)
                System.arraycopy(yb, 0, xb, 0, words)
            }
            for (k in 0 until words) {
                val o = base + 4 * k; val w = xb[k]
                bytes[o] = w.toByte(); bytes[o + 1] = (w shr 8).toByte()
                bytes[o + 2] = (w shr 16).toByte(); bytes[o + 3] = (w shr 24).toByte()
            }
        }
        return pbkdf2(pass, bytes, dkLen)
    }
}

class Vault private constructor(val salt: ByteArray, private val enc: ByteArray, private val mac: ByteArray) {
    companion object {
        val MAGIC = Bytes.utf8("LTID1")
        const val MIN_PASSPHRASE = 8

        fun kdf(passphrase: String, salt: ByteArray): Pair<ByteArray, ByteArray> {
            val k = Scrypt.derive(Bytes.utf8(passphrase), salt, 1 shl 15, 8, 1, 64)
            return k.copyOfRange(0, 32) to k.copyOfRange(32, 64)
        }

        fun create(passphrase: String): Vault {
            if (passphrase.length < MIN_PASSPHRASE)
                throw IllegalArgumentException("passphrase must be at least $MIN_PASSPHRASE characters")
            val salt = Bytes.random(16)
            val (e, m) = kdf(passphrase, salt)
            return Vault(salt, e, m)
        }

        fun isProtected(blob: ByteArray): Boolean =
            blob.size >= 5 && blob.copyOfRange(0, 5).contentEquals(MAGIC)

        /** Returns (decoded JSON object, vault for later saves). */
        fun open(blob: ByteArray, passphrase: String): Pair<MutableMap<String, Any?>, Vault> {
            if (!isProtected(blob) || blob.size < 5 + 16 + 12 + 16)
                throw IllegalArgumentException("not a protected identity file")
            val salt = blob.copyOfRange(5, 21)
            val nonce = blob.copyOfRange(21, 33)
            val ct = blob.copyOfRange(33, blob.size - 16)
            val tag = blob.copyOfRange(blob.size - 16, blob.size)
            val (e, m) = kdf(passphrase, salt)
            val want = Bytes.hmacSha256(m, Bytes.concat(MAGIC, salt, nonce, ct)).copyOf(16)
            if (!Bytes.equal(want, tag)) throw WrongPassphrase("wrong passphrase (or damaged file)")
            val json = Bytes.utf8Lenient(Crypto.chacha20Xor(e, nonce, ct))
            return Json.parseObject(json) to Vault(salt, e, m)
        }
    }

    fun seal(data: Map<String, Any?>): ByteArray {
        val nonce = Bytes.random(12)
        val ct = Crypto.chacha20Xor(enc, nonce, Bytes.utf8(Json.stringify(data)))
        val tag = Bytes.hmacSha256(mac, Bytes.concat(MAGIC, salt, nonce, ct)).copyOf(16)
        return Bytes.concat(MAGIC, salt, nonce, ct, tag)
    }
}

/** Log text encryption: "enc1:" + base64(nonce(12) ‖ ct ‖ tag(8)). */
class LogCipher(secret: ByteArray) {
    private val enc = Crypto.derive(secret, "log/enc")
    private val mac = Crypto.derive(secret, "log/mac")

    fun encrypt(text: String?): String? {
        if (text == null) return null
        val nonce = Bytes.random(12)
        val ct = Crypto.chacha20Xor(enc, nonce, Bytes.utf8(text))
        val tag = Bytes.hmacSha256(mac, Bytes.concat(nonce, ct)).copyOf(8)
        return PREFIX + Bytes.b64(Bytes.concat(nonce, ct, tag))
    }

    fun decrypt(s: String?): String? {
        if (s == null || !s.startsWith(PREFIX)) return s
        val raw = try { Bytes.unb64(s.substring(PREFIX.length)) } catch (e: IllegalArgumentException) { return null }
        if (raw.size < 20) return null
        val nonce = raw.copyOfRange(0, 12)
        val ct = raw.copyOfRange(12, raw.size - 8)
        val tag = raw.copyOfRange(raw.size - 8, raw.size)
        if (!Bytes.equal(Bytes.hmacSha256(mac, Bytes.concat(nonce, ct)).copyOf(8), tag)) return null
        return Bytes.utf8Lenient(Crypto.chacha20Xor(enc, nonce, ct))
    }

    companion object { const val PREFIX = "enc1:" }
}
