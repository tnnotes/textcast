package io.github.textcast.core

import java.math.BigInteger

class NumberException(msg: String) : IllegalArgumentException(msg)

/**
 * TextCast numbers (port of lora_text/numbers.py): a 10-digit name computed from
 * the identity key, no server needed:
 *   scrypt(pub, "textcast/number/v1", 2^14, 8, 1, 8 B) -> 9 digits + Luhn check digit.
 * Lookup frames (type DIRECTORY = 3, version 1):
 *   lookup [mode]['Q'][number 5 B]   answer [mode]['A'][id][part<<4|parts][chunk of pub‖nick]
 */
object Numbers {
    private val SALT = "textcast/number/v1".toByteArray()
    const val DIRECTORY = 3
    private const val OP_LOOKUP = 0x51
    private const val OP_ANSWER = 0x41
    private const val HEAD = 4
    private val cache = HashMap<String, String>()

    sealed class Frame {
        data class Lookup(val number: String) : Frame()
        class Answer(val id: Int, val part: Int, val parts: Int, val chunk: ByteArray) : Frame()
    }

    fun luhn(digits: String): Int {
        var total = 0
        for (k in digits.indices) {
            var d = digits[digits.length - 1 - k] - '0'
            if (k % 2 == 0) { d *= 2; if (d > 9) d -= 9 }
            total += d
        }
        return (10 - total % 10) % 10
    }

    @Synchronized fun numberOf(pub: ByteArray): String = cache.getOrPut(Bytes.hex(pub)) {
        val d = Scrypt.derive(pub, SALT, 1 shl 14, 8, 1, 8)
        val v = BigInteger(1, d).mod(BigInteger.valueOf(900_000_000L)).toLong() + 100_000_000L
        val body = v.toString()
        body + luhn(body)
    }

    fun format(n: String): String = if (n.length == 10) "${n.substring(0, 3)} ${n.substring(3, 6)} ${n.substring(6)}" else n

    fun parse(s: String?): String {
        val n = (s ?: "").replace(Regex("[\\s.\\-()+]"), "")
        if (!Regex("^\\d{10}$").matches(n)) throw NumberException("a TextCast number has 10 digits")
        if (n[0] == '0' || luhn(n.substring(0, 9)) != n[9] - '0')
            throw NumberException("that number has a typo (check digit does not match)")
        return n
    }

    private fun mode() = Protocol.makeMode(DIRECTORY, 0, Protocol.PRIVATE_VERSION)

    fun makeLookup(number: String): ByteArray {
        var v = number.toLong()
        val out = ByteArray(7)
        out[0] = mode().toByte(); out[1] = OP_LOOKUP.toByte()
        for (k in 6 downTo 2) { out[k] = (v and 0xFF).toByte(); v = v shr 8 }
        return out
    }

    fun makeAnswer(pub: ByteArray, nick: String, id: Int, maxPayload: Int): List<ByteArray> {
        val data = Bytes.concat(pub, nick.toByteArray(Charsets.UTF_8))
        val size = maxOf(1, maxPayload - HEAD)
        val chunks = (data.indices step size).map { data.copyOfRange(it, minOf(data.size, it + size)) }
        if (chunks.size > 15) throw NumberException("radio profile too small for a number answer")
        return chunks.mapIndexed { j, c ->
            Bytes.concat(byteArrayOf(mode().toByte(), OP_ANSWER.toByte(), (id and 0xFF).toByte(), ((j shl 4) or chunks.size).toByte()), c)
        }
    }

    fun isDirectory(f: ByteArray): Boolean {
        if (f.isEmpty()) return false
        val b = f[0].toInt() and 0xFF
        return (b shr 6) == Protocol.PRIVATE_VERSION && ((b shr 3) and 7) == DIRECTORY
    }

    fun parseFrame(f: ByteArray): Frame? {
        if (f.size < 2 || !isDirectory(f)) return null
        val op = f[1].toInt() and 0xFF
        if (op == OP_LOOKUP && f.size == 7) {
            var v = 0L
            for (k in 2 until 7) v = (v shl 8) or (f[k].toLong() and 0xFF)
            return if (v in 1_000_000_000L..9_999_999_999L) Frame.Lookup(v.toString()) else null
        }
        if (op == OP_ANSWER && f.size > HEAD) {
            val pp = f[3].toInt() and 0xFF
            val part = pp shr 4; val parts = pp and 15
            if (parts > 0 && part < parts) return Frame.Answer(f[2].toInt() and 0xFF, part, parts, f.copyOfRange(HEAD, f.size))
        }
        return null
    }
}
