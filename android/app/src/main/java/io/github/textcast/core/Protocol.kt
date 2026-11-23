package io.github.textcast.core

/**
 * Protocol core: air profile, airtime maths, the mode byte, plain framing and
 * rate limiting. Port of lora_text/protocol.py; frames are byte-identical.
 */
data class AirProfile(
    var name: String = "AS923-2",
    var freqMhz: Double = 920.800,
    var sf: Int = 7,
    var bwHz: Int = 125_000,
    var cr: Int = 1,                 // 1 = 4/5 ... 4 = 4/8
    var preamble: Int = 8,
    var crc: Boolean = true,
    var explicitHeader: Boolean = true,
    var privateSync: Boolean = true, // 0x1424
    var txPowerDbm: Int = 14,
    var contractMax: Int = 100,
    var maxPayload: Int = 100,
    var ratePerS: Double = 0.1,
    var burst: Int = 3,
    var backoffMaxS: Double = 2.0,
    var maxDwellMs: Double? = 400.0,
    var dutyCycle: Double? = 0.01,
)

class PayloadTooLong(msg: String) : IllegalArgumentException(msg)

object MsgType { const val PLAIN = 0; const val SIGNED = 1; const val SEALED = 2 }

object Protocol {
    const val PROTOCOL_VERSION = 0
    const val PRIVATE_VERSION = 1
    const val PRIVATE_TAG = 5

    /** The active profile (every node in a network must match). */
    @Volatile var profile = AirProfile()

    // firmware limits (Dragino LA66 P2P v1.2.4 by default)
    @Volatile var firmwareOverhead = 1
    private var limits: Map<Int, Int> = mapOf(5 to 230, 6 to 230, 7 to 230, 8 to 230, 9 to 123, 10 to 59, 11 to 59, 12 to 59)
    private var limitDefault = 59

    fun firmwareLimit(sf: Int): Int = limits[sf] ?: limitDefault

    /** limits: {"7": 230, ...} or {"*": 255} (from a hardware profile). */
    fun setHardwareLimits(lim: Map<String, Int>, overhead: Int) {
        firmwareOverhead = overhead
        limits = lim.filterKeys { it != "*" }.mapKeys { it.key.toInt() }
        limitDefault = lim["*"] ?: (limits.values.minOrNull() ?: 255)
    }

    /** Semtech time-on-air formula for one LoRa packet. */
    fun airtimeMs(payloadLen: Int, p: AirProfile = profile, overhead: Int = firmwareOverhead): Double {
        val n = payloadLen + overhead
        val tsym = Math.pow(2.0, p.sf.toDouble()) / p.bwHz * 1000.0
        val de = if (tsym > 16.0) 1 else 0
        val h = if (p.explicitHeader) 0 else 1
        val num = 8 * n - 4 * p.sf + 28 + (if (p.crc) 16 else 0) - 20 * h
        val nPayload = 8 + maxOf(Math.ceil(num.toDouble() / (4 * (p.sf - 2 * de))).toInt() * (p.cr + 4), 0)
        return (p.preamble + 4.25) * tsym + nPayload * tsym
    }

    fun effectivePayload(p: AirProfile): Int {
        var lim = minOf(p.contractMax, firmwareLimit(p.sf) - firmwareOverhead)
        val dwell = p.maxDwellMs
        if (dwell != null && dwell > 0) while (lim > 1 && airtimeMs(lim, p) > dwell) lim--
        return lim
    }

    fun applyProfile(p: AirProfile): AirProfile {
        p.maxPayload = effectivePayload(p)
        profile = p
        return p
    }

    // -- mode byte: [version:2][type:3][hop:2][reserved:1] ---------------------------

    fun makeMode(type: Int, hop: Int = 0, version: Int = PROTOCOL_VERSION): Int =
        ((version and 3) shl 6) or ((type and 7) shl 3) or ((hop and 3) shl 1)

    /** (version, type, hop) */
    fun parseMode(b: Int): Triple<Int, Int, Int> = Triple((b shr 6) and 3, (b shr 3) and 7, (b shr 1) and 3)

    // -- plain text --------------------------------------------------------------------

    fun normalize(text: String): String = Bytes.nfc(text)

    fun compose(text: String, callsign: String?): String {
        val t = normalize(text.trim())
        return if (callsign != null) "$callsign: $t" else t
    }

    fun encodePlain(text: String, p: AirProfile = profile): ByteArray {
        val body = Bytes.utf8(normalize(text))
        val frame = Bytes.concat(byteArrayOf(makeMode(MsgType.PLAIN).toByte()), body)
        if (frame.size > p.maxPayload)
            throw PayloadTooLong("${frame.size} B > ${p.maxPayload} B limit (${body.size} B of text, max ${p.maxPayload - 1})")
        return frame
    }

    fun frameLenPlain(text: String): Int = 1 + Bytes.utf8(normalize(text)).size

    private val CTRL = Regex("[\\x00-\\x08\\x0b-\\x1f\\x7f]")
    private val CALLSIGN = Regex("^([^\\s:]{1,16}): ")
    private val TOKENS = Regex("[#@][\\p{L}\\p{N}\\p{M}_\\-]+")

    fun clean(s: String): String = CTRL.replace(s, "�")

    class Decoded(val kind: String, val raw: ByteArray, val text: String? = null,
                  val msgType: Int? = null, val hop: Int = 0) {
        val callsign: String? get() = text?.let { CALLSIGN.find(it)?.groupValues?.get(1) }
        val tokens: List<String> get() = TOKENS.findAll(text ?: "").map { it.value.lowercase() }.toList()

        fun display(): String = when (kind) {
            "plain" -> text ?: ""
            "reserved" -> if (msgType == MsgType.SIGNED && text != null) "[unverified] $text"
                          else "[${raw.size} B ${if (msgType == MsgType.SIGNED) "signed" else "sealed"}]"
            else -> "[${raw.size} B undecoded]"
        }
    }

    fun decode(frame: ByteArray): Decoded {
        if (frame.isEmpty()) return Decoded("undecoded", frame)
        val (version, type, hop) = parseMode(frame[0].toInt() and 0xFF)
        if (version == PROTOCOL_VERSION && type == MsgType.PLAIN) {
            val t = Bytes.utf8Strict(frame, 1) ?: return Decoded("undecoded", frame)
            return Decoded("plain", frame, clean(t), type, hop)
        }
        if (version == PRIVATE_VERSION && type == MsgType.SIGNED && frame.size > 2 + PRIVATE_TAG) {
            val t = Bytes.utf8Strict(frame, 2, frame.size - PRIVATE_TAG)?.let { clean(it) }
            return Decoded("reserved", frame, t, type, hop)
        }
        if (version == PRIVATE_VERSION && (type == MsgType.SIGNED || type == MsgType.SEALED))
            return Decoded("reserved", frame, null, type, hop)
        return Decoded("undecoded", frame)
    }

    /** Local subscription filter (see protocol.matches). */
    fun matches(d: Decoded, filters: Set<String>): Boolean {
        if (filters.isEmpty()) return true
        if (d.kind != "plain") return false
        val textL = (d.text ?: "").lowercase()
        val toks = d.tokens
        val cs = (d.callsign ?: "").lowercase()
        for (f in filters) {
            val fl = f.lowercase()
            if (fl.startsWith("#") && fl in toks) return true
            if (fl.startsWith("@") && (cs == fl.substring(1) || fl in toks)) return true
            if (!fl.startsWith("#") && !fl.startsWith("@") && fl in textL) return true
        }
        return false
    }
}

/** Monotonic clock in seconds (overridable in tests). */
fun interface Clock { fun now(): Double }

val MONOTONIC = Clock { System.nanoTime() / 1e9 }

/** Token bucket: average `rate` messages/s, bursts up to `burst`. */
class RateLimiter(val rate: Double = Protocol.profile.ratePerS, burst: Int = Protocol.profile.burst,
                  private val clock: Clock = MONOTONIC) {
    private val capacity = burst.toDouble()
    private var tokens = capacity
    private var t = clock.now()

    @Synchronized private fun refill() {
        val now = clock.now()
        tokens = minOf(capacity, tokens + (now - t) * rate)
        t = now
    }

    @Synchronized fun waitTime(): Double { refill(); return if (tokens >= 1) 0.0 else (1 - tokens) / rate }

    @Synchronized fun tryConsume(): Boolean {
        refill()
        if (tokens >= 1) { tokens -= 1; return true }
        return false
    }
}

/** Rolling sum of own TX airtime (default window: 1 hour). */
class AirtimeMeter(val windowS: Double = 3600.0, val clock: Clock = MONOTONIC) {
    private val events = ArrayDeque<Pair<Double, Double>>()

    @Synchronized fun add(ms: Double) { events.addLast(clock.now() to ms) }

    @Synchronized fun totalMs(): Double {
        val cutoff = clock.now() - windowS
        while (events.isNotEmpty() && events.first().first < cutoff) events.removeFirst()
        return events.sumOf { it.second }
    }

    @Synchronized fun oldest(): Double? = events.firstOrNull()?.first

    fun dutyCycle(): Double = totalMs() / (windowS * 1000.0)
}
