package io.github.textcast.core

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.security.MessageDigest
import java.security.SecureRandom
import java.text.Normalizer
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** Byte helpers shared by the core (no Android dependencies). */
object Bytes {
    private val rng = SecureRandom()

    fun random(n: Int): ByteArray = ByteArray(n).also { rng.nextBytes(it) }

    fun sha256(b: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(b)
    fun sha512(b: ByteArray): ByteArray = MessageDigest.getInstance("SHA-512").digest(b)

    fun hmacSha256(key: ByteArray, data: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        // an empty HMAC key equals 64 zero bytes; SecretKeySpec refuses empty keys
        mac.init(SecretKeySpec(if (key.isEmpty()) ByteArray(64) else key, "HmacSHA256"))
        return mac.doFinal(data)
    }

    /** Constant-time comparison. */
    fun equal(a: ByteArray, b: ByteArray): Boolean = MessageDigest.isEqual(a, b)

    fun concat(vararg parts: ByteArray): ByteArray {
        val out = ByteArray(parts.sumOf { it.size })
        var off = 0
        for (p in parts) { System.arraycopy(p, 0, out, off, p.size); off += p.size }
        return out
    }

    fun longBE(v: Long): ByteArray = ByteBuffer.allocate(8).putLong(v).array()
    fun intBE(v: Int): ByteArray = ByteBuffer.allocate(4).putInt(v).array()
    fun readIntBE(b: ByteArray, off: Int): Long =
        ((b[off].toLong() and 0xFF) shl 24) or ((b[off + 1].toLong() and 0xFF) shl 16) or
            ((b[off + 2].toLong() and 0xFF) shl 8) or (b[off + 3].toLong() and 0xFF)

    // -- hex -------------------------------------------------------------------
    private const val HEX = "0123456789abcdef"

    fun hex(b: ByteArray, upper: Boolean = false): String {
        val sb = StringBuilder(b.size * 2)
        for (x in b) {
            sb.append(HEX[(x.toInt() shr 4) and 0xF]); sb.append(HEX[x.toInt() and 0xF])
        }
        return if (upper) sb.toString().uppercase() else sb.toString()
    }

    /** Hex string to bytes; whitespace ignored. Throws IllegalArgumentException. */
    fun unhex(s: String): ByteArray {
        val c = s.filter { !it.isWhitespace() }
        require(c.length % 2 == 0) { "odd number of hex digits" }
        return ByteArray(c.length / 2) { i ->
            val hi = Character.digit(c[2 * i], 16); val lo = Character.digit(c[2 * i + 1], 16)
            require(hi >= 0 && lo >= 0) { "not hex: ${c.substring(2 * i, 2 * i + 2)}" }
            ((hi shl 4) or lo).toByte()
        }
    }

    // -- base32 (RFC 4648, lower case, no padding: same as keyring.b32) ---------
    private const val B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

    fun b32(b: ByteArray): String {
        val sb = StringBuilder()
        var buf = 0; var bits = 0
        for (x in b) {
            buf = (buf shl 8) or (x.toInt() and 0xFF); bits += 8
            while (bits >= 5) { sb.append(B32[(buf shr (bits - 5)) and 31]); bits -= 5 }
        }
        if (bits > 0) sb.append(B32[(buf shl (5 - bits)) and 31])
        return sb.toString().lowercase()
    }

    /** Like Python's b32decode after re-padding: strict alphabet, case-insensitive. */
    fun unb32(s: String): ByteArray {
        val t = s.trim().uppercase().trimEnd('=')
        val rem = t.length % 8
        if (rem == 1 || rem == 3 || rem == 6) throw IllegalArgumentException("bad base32 length")
        val out = java.io.ByteArrayOutputStream()
        var buf = 0L; var bits = 0
        for (ch in t) {
            val v = B32.indexOf(ch)
            if (v < 0) throw IllegalArgumentException("not base32: '$ch'")
            buf = (buf shl 5) or v.toLong(); bits += 5
            if (bits >= 8) { out.write(((buf shr (bits - 8)) and 0xFF).toInt()); bits -= 8 }
        }
        return out.toByteArray()
    }

    // -- base64 (standard, padded; java.util.Base64 needs Android 8) -----------
    private const val B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

    fun b64(b: ByteArray): String {
        val sb = StringBuilder((b.size + 2) / 3 * 4)
        var i = 0
        while (i < b.size) {
            val n = minOf(3, b.size - i)
            var v = 0
            for (k in 0 until 3) v = (v shl 8) or (if (k < n) b[i + k].toInt() and 0xFF else 0)
            for (k in 0 until 4) sb.append(if (k <= n) B64[(v shr (18 - 6 * k)) and 63] else '=')
            i += 3
        }
        return sb.toString()
    }

    fun unb64(s: String): ByteArray {
        val t = s.filter { !it.isWhitespace() }.trimEnd('=')
        val out = java.io.ByteArrayOutputStream()
        var buf = 0; var bits = 0
        for (ch in t) {
            val v = B64.indexOf(ch)
            if (v < 0) throw IllegalArgumentException("not base64")
            buf = (buf shl 6) or v; bits += 6
            if (bits >= 8) { out.write((buf shr (bits - 8)) and 0xFF); bits -= 8 }
        }
        return out.toByteArray()
    }

    // -- text --------------------------------------------------------------------
    fun utf8(s: String): ByteArray = s.toByteArray(Charsets.UTF_8)

    /** Strict UTF-8 decoding (like Python's bytes.decode): null if invalid. */
    fun utf8Strict(b: ByteArray, from: Int = 0, to: Int = b.size): String? = try {
        Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(b, from, to - from)).toString()
    } catch (e: CharacterCodingException) {
        null
    }

    fun utf8Lenient(b: ByteArray): String = String(b, Charsets.UTF_8)

    /** NFC keeps Vietnamese diacritics precomposed (fewer bytes than NFD). */
    fun nfc(s: String): String = Normalizer.normalize(s, Normalizer.Form.NFC)
}
