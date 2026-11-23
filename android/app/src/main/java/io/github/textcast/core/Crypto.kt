package io.github.textcast.core

/**
 * Rolling-tag crypto for SIGNED and SEALED frames. Byte-exact port of
 * lora_text/crypto.py (see that file for the design notes).
 *
 *   Frame (version 1):   [mode 1B][hint 1B][body][tag 5B]
 *   i    = max(last_i + 1, floor, minute_now << 8)
 *   tag  = Trunc5(HMAC-SHA256(mac_key, mode ‖ i(8B) ‖ body))
 *   ct   = ChaCha20(enc_key, nonce = 0x00000000 ‖ i(8B), counter = 1) XOR text
 */
object Crypto {
    const val TAG_LEN = 5
    const val HINT_LEN = 1
    const val FRAME_OVERHEAD = 1 + HINT_LEN + TAG_LEN
    const val SEQ_BITS = 8
    const val EPOCH0 = 1767225600L          // 2026-01-01T00:00:00Z
    const val SKEW_MIN = 5
    const val SEQ_MAX = 16
    const val AHEAD = 1024

    // --- ChaCha20 (RFC 8439) ---------------------------------------------------

    private fun rotl(v: Int, c: Int) = (v shl c) or (v ushr (32 - c))

    private fun le32(b: ByteArray, off: Int) =
        (b[off].toInt() and 0xFF) or ((b[off + 1].toInt() and 0xFF) shl 8) or
            ((b[off + 2].toInt() and 0xFF) shl 16) or ((b[off + 3].toInt() and 0xFF) shl 24)

    fun chacha20Block(key: ByteArray, counter: Int, nonce: ByteArray): ByteArray {
        require(key.size == 32 && nonce.size == 12)
        val st = IntArray(16)
        st[0] = 0x61707865; st[1] = 0x3320646E; st[2] = 0x79622D32; st[3] = 0x6B206574
        for (k in 0 until 8) st[4 + k] = le32(key, 4 * k)
        st[12] = counter
        for (k in 0 until 3) st[13 + k] = le32(nonce, 4 * k)
        val w = st.copyOf()
        fun qr(a: Int, b: Int, c: Int, d: Int) {
            w[a] += w[b]; w[d] = rotl(w[d] xor w[a], 16)
            w[c] += w[d]; w[b] = rotl(w[b] xor w[c], 12)
            w[a] += w[b]; w[d] = rotl(w[d] xor w[a], 8)
            w[c] += w[d]; w[b] = rotl(w[b] xor w[c], 7)
        }
        repeat(10) {
            qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15)
            qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14)
        }
        val out = ByteArray(64)
        for (k in 0 until 16) {
            val v = w[k] + st[k]
            out[4 * k] = v.toByte(); out[4 * k + 1] = (v shr 8).toByte()
            out[4 * k + 2] = (v shr 16).toByte(); out[4 * k + 3] = (v shr 24).toByte()
        }
        return out
    }

    fun chacha20Xor(key: ByteArray, nonce: ByteArray, data: ByteArray, counter: Int = 1): ByteArray {
        val out = ByteArray(data.size)
        var off = 0
        while (off < data.size) {
            val ks = chacha20Block(key, counter + off / 64, nonce)
            val n = minOf(64, data.size - off)
            for (k in 0 until n) out[off + k] = (data[off + k].toInt() xor ks[k].toInt()).toByte()
            off += 64
        }
        return out
    }

    // --- keys -------------------------------------------------------------------

    fun derive(secret: ByteArray, label: String): ByteArray =
        Bytes.hmacSha256(secret, Bytes.utf8("lora-text/$label"))

    /** Per-member stream secret inside a group. */
    fun memberSecret(groupSecret: ByteArray, callsign: String): ByteArray =
        derive(groupSecret, "member/" + callsign.trim().lowercase()).copyOf(16)

    class StreamKey(secret: ByteArray) {
        val enc: ByteArray = derive(secret, "enc")
        val mac: ByteArray = derive(secret, "mac")
    }

    // --- counters ---------------------------------------------------------------

    fun nowSeconds(): Double = System.currentTimeMillis() / 1000.0

    fun minuteNow(t: Double? = null): Long =
        Math.floor(((t ?: nowSeconds()) - EPOCH0) / 60.0).toLong()

    fun nextCounter(lastI: Long, floorI: Long = 0, t: Double? = null): Long =
        maxOf(lastI + 1, floorI, minuteNow(t) shl SEQ_BITS)

    fun startupFloor(t: Double? = null): Long = (minuteNow(t) + 1) shl SEQ_BITS

    /** Minute offsets nearest first, as Python's sorted(range(-5, 7), key=abs). */
    private val SKEW_ORDER: List<Int> = (-SKEW_MIN..SKEW_MIN + 1).sortedBy { Math.abs(it) }

    /** Counters to try, nearest first (see crypto.candidates). */
    fun candidates(lastI: Long, t: Double? = null, hint: Int? = null): Sequence<Long> = sequence {
        val tried = HashSet<Long>()
        if (lastI > 0) {
            val start = lastI + 1
            if (hint == null) {
                for (i in start until start + AHEAD) { tried.add(i); yield(i) }
            } else {
                var i0 = (start and 0xFF.toLong().inv()) or hint.toLong()
                if (i0 < start) i0 += 256
                var i = i0
                while (i < start + AHEAD) { tried.add(i); yield(i); i += 256 }
            }
        }
        val m = minuteNow(t)
        val seqs = if (hint == null) (0 until SEQ_MAX).toList() else listOf(hint)
        for (d in SKEW_ORDER) for (s in seqs) {
            val i = ((m + d) shl SEQ_BITS) or s.toLong()
            if (i > lastI && i !in tried) yield(i)
        }
    }

    // --- seal / open --------------------------------------------------------------

    private fun nonce(i: Long): ByteArray = Bytes.concat(ByteArray(4), Bytes.longBE(i))

    private fun tag(k: StreamKey, mode: Int, i: Long, body: ByteArray): ByteArray =
        Bytes.hmacSha256(k.mac, Bytes.concat(byteArrayOf(mode.toByte()), Bytes.longBE(i), body))
            .copyOf(TAG_LEN)

    fun seal(k: StreamKey, mode: Int, i: Long, plaintext: ByteArray, encrypt: Boolean): ByteArray {
        val body = if (encrypt) chacha20Xor(k.enc, nonce(i), plaintext) else plaintext
        return Bytes.concat(byteArrayOf(mode.toByte(), (i and 0xFF).toByte()), body, tag(k, mode, i, body))
    }

    private fun body(frame: ByteArray) = frame.copyOfRange(2, frame.size - TAG_LEN)

    fun verify(k: StreamKey, frame: ByteArray, i: Long): Boolean {
        if (frame.size < FRAME_OVERHEAD || (frame[1].toInt() and 0xFF) != (i and 0xFF).toInt()) return false
        val want = tag(k, frame[0].toInt() and 0xFF, i, body(frame))
        return Bytes.equal(want, frame.copyOfRange(frame.size - TAG_LEN, frame.size))
    }

    fun unseal(k: StreamKey, frame: ByteArray, i: Long, encrypted: Boolean): ByteArray {
        val b = body(frame)
        return if (encrypted) chacha20Xor(k.enc, nonce(i), b) else b
    }

    fun matchingCounters(k: StreamKey, frame: ByteArray, lastI: Long, t: Double? = null): Sequence<Long> {
        if (frame.size < FRAME_OVERHEAD) return emptySequence()
        return candidates(lastI, t, frame[1].toInt() and 0xFF).filter { verify(k, frame, it) }
    }

    fun findCounter(k: StreamKey, frame: ByteArray, lastI: Long, t: Double? = null): Long? =
        matchingCounters(k, frame, lastI, t).firstOrNull()
}
